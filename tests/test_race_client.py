"""Real RacePVP routes, room contract and atomic battle/reward lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.protobuf import json_format, message_factory
from accounts import LocalAccounts
from game import LocalGame
from profile_features import actor_role, role_head_photo, table_rows
from server.core.reset_scheduler import ResetScheduler
import test_systems


class RaceClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [int(datetime(2026, 10, 30, 4, tzinfo=timezone.utc).timestamp())]
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0], initial_level=26,
                              initial_actor_ids=[1001, 1002], initial_resources=[{"type": 7, "id": 0, "param": 0, "count": 100}])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.race = self.game.realtime_pvp.race
        self.pushes = []
        self.game.base_push_notifier = lambda p, k, params=(): self.pushes.append((k, tuple(params)))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, body=None, game=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        return (game or self.game).handle_http(method, path, raw, "application/json", self.auth)

    def info(self):
        status, data = self.call("GET", "/fci/RacePVPInfo")
        self.assertEqual(status, 200)
        return data

    def item(self, rid):
        return self.game.systems._state(self.profile).get("items", {}).get(str(rid), {}).get("count", 0)

    def enter(self):
        stage = self.info()["dungeonId"]
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiRacePVPDungeon"))
        request = cls(dungeon_id=stage, select_actor_group_id=1)
        status, created = self.call("POST", "/fci/racepvp-dungeon/", request.SerializeToString())
        self.assertEqual(status, 200, created)
        return created

    def snapshot(self, created, damage=25000, own_damage=0, round_no=8, win=1):
        active = self.game._types["FightActiveData"](dungeonType=7, stageID=created["baseInfo"]["dungeonId"], gameRound=round_no, winTeam=win)
        active.teamList.add(team=1).statistic.totalDamage = own_damage
        active.teamList.add(team=2).statistic.totalDamage = damage
        return active

    def fight(self, created, damage=25000):
        status, result = self.call("PATCH", f"/fci/dungeon/{created['dungeonUid']}/active/", self.snapshot(created, damage).SerializeToString())
        self.assertEqual(status, 200, result)
        status, result = self.call("POST", f"/fci/racepvp-dungeon/{created['dungeonUid']}/balance/", {})
        self.assertEqual(status, 200, result)
        return result

    def assert_message(self, name, data):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        msg = json_format.ParseDict(data, cls())
        self.assertEqual(cls.FromString(msg.SerializeToString()), msg)

    def test_weekly_room_has_twenty_players_and_native_contracts(self):
        info = self.info()
        self.assert_message("ApiRacePVPInfo.result", info)
        self.assertEqual(len(info["players"]), 20)
        self.assertEqual(sum(p["playerId"] == self.profile["player_id"] for p in info["players"]), 1)
        self.assertTrue(all(p["battleActors"] for p in info["players"]))
        photos = {row["id"]: row for row in table_rows("PlayerHeadPhoto")}
        for player in info["players"]:
            self.assertIn(player["headPhoto"], photos)
            self.assertTrue(photos[player["headPhoto"]]["sprite"])
            if player["playerId"] != self.profile["player_id"]:
                self.assertEqual(player["headPhoto"], role_head_photo(actor_role(player["battleActors"][0]["id"])))
        self.assertEqual((info["state"], info["round"], info["score"]), (2, 1, 200))
        self.assertIn(info["dungeonId"], [3001, 3002, 3003, 3004])
        self.assertEqual(self.item(80004), 6)
        boundary = self.game.reset_policy.local_datetime(info["endTime"])
        self.assertEqual((boundary.weekday(), boundary.hour), (0, 6))
        self.assert_message("ApiRacePVPSummary.result", self.call("GET", "/fci/RacePVPSummary/")[1])
        self.assert_message("ApiRacePVPBattleScoreReward.get", self.call("GET", "/fci/RacePVPBattleScoreReward/")[1])
        self.assertEqual(self.call("POST", "/fci/racepvp/match", {})[0], 410)

    def test_existing_room_repairs_actor_ids_as_photos_without_resetting_progress(self):
        run = self.enter()
        self.fight(run)
        self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})
        state = self.race._load(self.profile)
        for player in state["players"]:
            player["headPhoto"] = 1001
        self.race._save(self.profile, state)
        before = self.race._copy(state)
        ticket_count = self.item(80004)
        profile_state = self.game.profile_features._state(self.profile)
        profile_state.update(headPhoto=2, socialPicture="")
        self.game.profile_features._save(self.profile, profile_state)

        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        status, data = self.call("GET", "/fci/RacePVPInfo", game=reboot)
        self.assertEqual(status, 200)
        self.assert_message("ApiRacePVPInfo.result", data)
        own = next(p for p in data["players"] if p["playerId"] == self.profile["player_id"])
        self.assertEqual(own["headPhoto"], 2)
        self.assertTrue(all(p["headPhoto"] in self.race.HEAD_PHOTOS for p in data["players"]))
        repaired = reboot.realtime_pvp.race._load(self.profile)
        for player in repaired["players"]:
            player["headPhoto"] = 1001
        self.assertEqual(repaired, before)
        self.assertEqual(self.item(80004), ticket_count)
        self.assertEqual((self.item(40002), self.item(10001)), (20, 20))
        self.assertEqual(reboot.realtime_pvp.race._run(self.profile, run["dungeonUid"]), self.race._run(self.profile, run["dungeonUid"]))

        # A user may choose a platform picture instead of a table avatar.
        profile_state.update(headPhoto=0, socialPicture="local://player-picture")
        self.game.profile_features._save(self.profile, profile_state)
        own = next(p for p in self.info()["players"] if p["playerId"] == self.profile["player_id"])
        self.assertEqual((own["headPhoto"], own["socialPicture"]), (0, "local://player-picture"))

    def test_native_create_retry_and_score_from_enemy_received_damage(self):
        run = self.enter()
        self.assert_message("ApiRacePVPDungeon.result", run)
        self.assertEqual(run["baseInfo"]["dungeonType"], 7)
        self.assertEqual(run["baseInfo"]["activityType"], 20)
        self.assertEqual(self.item(80004), 5)
        duplicate = self.enter()
        self.assertEqual(duplicate["dungeonUid"], run["dungeonUid"])
        self.assertEqual(self.item(80004), 5)
        self.assertEqual(duplicate["resChange"][0]["baseRes"]["countDelta"], 0)
        uid = run["dungeonUid"]
        self.assertEqual(self.call("POST", f"/fci/racepvp-dungeon/{uid}/balance/", {"win": True, "battleScore": 999999})[1]["error"], "race_fight_snapshot_required")
        active = self.snapshot(run, damage=25009, own_damage=900000)
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString())[0], 200)
        result = self.call("POST", f"/fci/racepvp-dungeon/{uid}/balance/", {})[1]
        self.assertEqual(result, {"curBattleScore": 2500, "highBattleScore": 2500})
        self.assertEqual(self.call("POST", f"/fci/racepvp-dungeon/{uid}/balance/", {})[1], result)
        self.assertEqual(self.item(80004), 5)
        self.assertEqual(self.call("DELETE", f"/fci/racepvp-dungeon/{uid}")[0], 200)
        self.assertEqual(self.call("DELETE", f"/fci/racepvp-dungeon/{uid}")[0], 200)
        weaker = self.enter()
        self.assertEqual(self.fight(weaker, 10000), {"curBattleScore": 1000, "highBattleScore": 2500})
        self.assertEqual(self.item(80004), 4)

    def test_restart_preserves_active_snapshot_balance_and_exact_claim(self):
        run = self.enter()
        first_reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        status, retry = self.call("POST", "/fci/racepvp-dungeon/", {"dungeonId": run["baseInfo"]["dungeonId"]}, game=first_reboot)
        self.assertEqual(status, 200)
        self.assertEqual(retry["dungeonUid"], run["dungeonUid"])
        self.assertEqual(self.item(80004), 5)
        self.fight(run)
        before_money = next(r["count"] for r in self.game._profile(self.account)["resources"] if r["type"] == 7)
        status, result = self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})
        self.assertEqual(status, 200)
        self.assert_message("ApiRacePVPBattleScoreReward.post", result)
        self.assertEqual((self.item(40002), self.item(10001)), (20, 20))
        self.assertEqual(next(r["count"] for r in self.game._profile(self.account)["resources"] if r["type"] == 7), before_money)
        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        status, restored = self.call("GET", "/fci/RacePVPInfo", game=reboot)
        self.assertEqual(status, 200)
        own = next(p for p in restored["players"] if p["playerId"] == self.profile["player_id"])
        self.assertEqual(own["battleScore"], 2500)
        self.assertEqual(self.call("POST", f"/fci/racepvp-dungeon/{run['dungeonUid']}/balance/", {}, game=reboot)[1]["highBattleScore"], 2500)
        self.assertEqual(self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000}, game=reboot)[1], {"resChange": []})
        self.assertEqual((self.item(40002), self.item(10001)), (20, 20))
        self.assertEqual(self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 5000})[1]["error"], "race_reward_not_earned")

    def test_creation_and_reward_faults_rollback_bag_records_and_pushes(self):
        stage = self.info()["dungeonId"]
        self.pushes.clear()
        with patch.object(self.race, "_save_run", side_effect=RuntimeError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                self.call("POST", "/fci/racepvp-dungeon/", {"dungeonId": stage})
        self.assertEqual(self.item(80004), 6)
        self.assertIsNone(self.race._load(self.profile)["activeUid"])
        self.assertEqual(self.pushes, [])
        run = self.enter()
        self.fight(run)
        self.pushes.clear()
        original_save = self.race._save
        def fail_claim(profile, state):
            if state["rewardRecved"]:
                raise RuntimeError("claim failed")
            return original_save(profile, state)
        with patch.object(self.race, "_save", side_effect=fail_claim):
            with self.assertRaisesRegex(RuntimeError, "claim failed"):
                self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})
        self.assertEqual((self.item(40002), self.item(10001)), (0, 0))
        self.assertEqual(self.race._load(self.profile)["rewardRecved"], [])
        self.assertEqual(self.pushes, [])

    def test_simultaneous_creates_spend_one_ticket(self):
        stage = self.info()["dungeonId"]
        other = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        def create(game):
            return self.call("POST", "/fci/racepvp-dungeon/", {"dungeonId": stage}, game=game)
        with ThreadPoolExecutor(max_workers=2) as executor:
            rows = list(executor.map(create, (self.game, other)))
        self.assertTrue(all(row[0] == 200 for row in rows))
        self.assertEqual(rows[0][1]["dungeonUid"], rows[1][1]["dungeonUid"])
        self.assertEqual(self.item(80004), 5)

    def test_weekly_rank_settlement_and_clock_rollback_do_not_repeat(self):
        initial = self.info()
        self.fight(self.enter(), damage=400000)
        self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})
        self.now[0] = initial["endTime"]
        next_week = self.info()
        self.assertEqual((next_week["score"], next_week["oldScore"], next_week["round"]), (240, 200, 2))
        self.assertEqual(next_week["state"], 2)
        self.assertEqual(self.call("GET", "/fci/RacePVPBattleScoreReward/")[1]["rewardRecved"], [])
        own = next(p for p in next_week["players"] if p["playerId"] == self.profile["player_id"])
        self.assertEqual(own["battleScore"], 0)
        self.assertEqual((self.item(40002), self.item(10001)), (20, 20))
        self.now[0] -= 86400
        self.assertEqual(self.info()["round"], 2)
        self.now[0] += 86400
        self.assertEqual(self.info()["score"], 240)
        self.assertEqual(len(self.race._load(self.profile)["settlements"]), 1)

    def test_daily_tickets_and_cancelled_or_mismatched_battles(self):
        run = self.enter()
        uid = run["dungeonUid"]
        wrong = self.snapshot(run)
        wrong.stageID += 1
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{uid}/active/", wrong.SerializeToString())[0], 400)
        self.assertEqual(self.call("DELETE", f"/fci/racepvp-dungeon/{uid}")[0], 200)
        self.assertEqual(self.call("POST", f"/fci/racepvp-dungeon/{uid}/balance/", {})[1]["error"], "race_dungeon_closed")
        self.assertEqual(self.item(80004), 5)
        self.now[0] = self.game.reset_policy.next_boundary(self.now[0])
        self.info()
        self.assertEqual(self.item(80004), 6)
        self.now[0] -= 1
        self.info()
        self.now[0] += 1
        self.info()
        self.assertEqual(self.item(80004), 6)

    def test_daily_hook_returns_changes_and_scheduler_notifies_ticket_once(self):
        changes = self.race.refresh_daily_items(self.profile)
        self.assertEqual(changes, [{"baseRes": {"type": 11, "id": 80004, "count": 6, "param": 0, "countDelta": 6}}])
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.race.refresh_daily_items(self.profile), [])
        run = self.enter()
        self.call("DELETE", f"/fci/racepvp-dungeon/{run['dungeonUid']}")
        owner = self.profile["account_id"]
        class Service:
            def account_ids(self):
                return [owner]
            def tasks_change_for_account_id(self, account_id, payload):
                pass
        scheduler = ResetScheduler(self.game, [Service()])
        self.assertEqual(scheduler.tick(), 1)
        self.pushes.clear()
        self.now[0] = self.game.reset_policy.next_boundary(self.now[0])
        self.assertEqual(scheduler.tick(), 1)
        self.assertEqual(self.item(80004), 6)
        self.assertEqual([push for push in self.pushes if push == (3, (80004,))], [(3, (80004,))])
        self.assertEqual(scheduler.tick(), 0)
        self.assertEqual([push for push in self.pushes if push == (3, (80004,))], [(3, (80004,))])

    def test_material_and_race_runs_use_disjoint_uids_and_dispatch(self):
        self.now[0] = int(datetime(2026, 10, 26, 12, tzinfo=timezone.utc).timestamp())
        self.game.economy._change_resources(self.profile, [{"type": 6, "id": 0, "param": 0, "count": 100}])
        status, material = self.call("POST", "/fci/activity-dungeon/", {"dungeonId": 52101,
            "selectActorGroupId": 1, "activityId": 2001, "activityType": 2, "multiPower": 1})
        self.assertEqual(status, 200, material)
        race = self.enter()
        self.assertNotEqual(race["dungeonUid"], material["dungeonUid"])
        self.assertEqual(self.fight(race), {"curBattleScore": 2500, "highBattleScore": 2500})
        material_run = self.game.activity_modes._run(self.profile, material["dungeonUid"])
        self.assertIsNone(material_run["active_data"])
        self.assertEqual(material_run["status"], "active")


if __name__ == "__main__":
    unittest.main()
