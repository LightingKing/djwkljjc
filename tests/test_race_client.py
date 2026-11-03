"""Real RacePVP routes, room contract and atomic battle/reward lifecycle."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
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
        return (game or self.game).handle_http(method, path, raw,
            "application/wireformat" if isinstance(body, bytes) else "application/json", self.auth)

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

    def test_cycle_room_has_twenty_players_and_native_contracts(self):
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
        self.assertEqual((boundary.weekday(), boundary.hour), (6, 0))
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
        # Exercise ordinary race explicitly; the default local schedule now
        # also offers type 42, covered separately by test_open_gacha_race.
        with patch.object(self.game.events, "_activity_list", return_value=(200, [])):
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

    def test_cycle_rank_settlement_and_clock_rollback_do_not_repeat(self):
        initial = self.info()
        self.fight(self.enter(), damage=400000)
        self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})
        self.now[0] = initial["endTime"]
        settled = self.info()
        self.assertEqual((settled["score"], settled["oldScore"], settled["round"]), (240, 200, 1))
        self.assertEqual(settled["state"], 1)
        self.assertEqual(self.item(80004), 0)
        self.now[0] = settled["endTime"]
        next_cycle = self.info()
        self.assertEqual((next_cycle["score"], next_cycle["round"], next_cycle["state"]), (240, 2, 2))
        self.assertEqual(self.call("GET", "/fci/RacePVPBattleScoreReward/")[1]["rewardRecved"], [])
        own = next(p for p in next_cycle["players"] if p["playerId"] == self.profile["player_id"])
        self.assertEqual(own["battleScore"], 0)
        self.assertEqual((self.item(40002), self.item(10001)), (20, 20))
        self.now[0] -= 86400
        self.assertEqual(self.info()["round"], 2)
        self.now[0] += 86400
        self.assertEqual(self.info()["score"], 240)
        self.assertEqual(len(self.race._load(self.profile)["settlements"]), 1)

    def test_cycle_tickets_and_cancelled_or_mismatched_battles(self):
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
        self.assertEqual(self.item(80004), 5)
        self.now[0] -= 1
        self.info()
        self.now[0] += 1
        self.info()
        self.assertEqual(self.item(80004), 5)

    def test_cycle_hook_returns_changes_and_scheduler_notifies_ticket_once(self):
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
        self.assertEqual(self.item(80004), 5)
        self.assertEqual([push for push in self.pushes if push == (3, (80004,))], [])
        self.now[0] = self.race._load(self.profile)["nextStart"]
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

    def race_mails(self):
        return [mail for mail in self.game.systems._state(self.profile).get("mails", [])
                if mail.get("senderName") == "残梦之境"]

    def test_abandoned_run_is_closed_uploaded_run_is_balanced_and_fresh_create_is_replayed(self):
        first = self.enter()
        self.now[0] += self.race.CREATE_RETRY_SECONDS + 1
        second = self.enter()
        self.assertNotEqual(first["dungeonUid"], second["dungeonUid"])
        self.assertEqual(self.race._run(self.profile, first["dungeonUid"])["status"], "closed")
        self.assertEqual(self.item(80004), 4)
        self.call("PATCH", f"/fci/dungeon/{second['dungeonUid']}/active/", self.snapshot(second).SerializeToString())
        third = self.enter()
        self.assertNotEqual(second["dungeonUid"], third["dungeonUid"])
        restored = self.race._run(self.profile, second["dungeonUid"])
        self.assertEqual(restored["status"], "balanced")
        self.assertEqual(restored["balance"], {"curBattleScore": 2500, "highBattleScore": 2500})
        self.assertEqual(self.enter()["dungeonUid"], third["dungeonUid"])
        self.assertEqual(self.item(80004), 3)
        self.assertEqual(self.fight(third), {"curBattleScore": 2500, "highBattleScore": 2500})

    def test_short_create_retry_does_not_reuse_a_different_native_formation(self):
        first = self.enter()
        groups = self.call("GET", "/fci/ActorGroup/")[1]
        uid = groups["1"]["members"][0]["actorUid"]
        for member in groups["2"]["members"]:
            member["actorUid"] = 0
        groups["2"]["members"][1]["actorUid"] = uid
        self.assertEqual(self.call("PUT", "/fci/ActorGroup/", {"actorGroups": groups})[0], 200)
        request_type = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiRacePVPDungeon"))
        body = request_type(dungeon_id=first["baseInfo"]["dungeonId"], select_actor_group_id=2).SerializeToString()
        status, second = self.call("POST", "/fci/racepvp-dungeon/", body)
        self.assertEqual(status, 200)
        self.assertNotEqual(second["dungeonUid"], first["dungeonUid"])
        self.assertEqual(second["baseInfo"]["actorUid"], [0, uid, 0, 0, 0, 0])
        self.assertEqual(self.call("POST", "/fci/racepvp-dungeon/", body)[1]["dungeonUid"], second["dungeonUid"])
        self.assertEqual(self.item(80004), 4)

    def test_cycle_boundary_refunds_late_battle_once_and_keeps_new_room_clean(self):
        first = self.enter()
        self.now[0] = self.race._load(self.profile)["nextStart"]
        result = self.fight(first, damage=1883)
        self.assertEqual(result, {"curBattleScore": 188, "highBattleScore": 188})
        self.assertEqual(self.item(80004), 6)
        self.assertEqual(self.race._own(self.profile, self.race._load(self.profile))["battleScore"], 0)
        mails = self.race_mails()
        compensation = [mail for mail in mails if "补偿" in mail["title"]]
        self.assertEqual(len(compensation), 1)
        self.assertEqual(len(mails), 1)  # No ranking reward for an unfinished run.
        self.assertFalse(self.race._load(self.profile)["enrolled"])
        self.assertIsNone(self.race._load(self.profile)["lastParticipatedCycle"])
        self.assertEqual(compensation[0]["attachments"], [{"type": 11, "id": 80004, "count": 1, "param": 0}])
        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.assertEqual(self.call("POST", f"/fci/racepvp-dungeon/{first['dungeonUid']}/balance/", {}, game=reboot)[1], result)
        self.assertEqual(self.race_mails(), mails)
        path = f"/fci/mail/{compensation[0]['id']}"
        self.assertEqual(self.call("PATCH", path, {"attachmentsGot": True})[0], 200)
        self.assertEqual(self.item(80004), 7)
        self.assertEqual(self.call("PATCH", path, {"attachmentsGot": True})[0], 200)
        self.assertEqual(self.item(80004), 7)
        self.call("DELETE", f"/fci/racepvp-dungeon/{first['dungeonUid']}")
        self.assertIsNone(self.race._load(self.profile)["activeUid"])

    def test_refund_claimed_during_later_closure_survives_mail_auto_delete(self):
        self.enter()
        self.now[0] = self.info()["endTime"]
        closed = self.info()
        mail = next(m for m in self.race_mails() if "补偿" in m["title"])
        path = f"/fci/mail/{mail['id']}"
        early = self.call("PATCH", path, {"attachmentsGot": True})[1]
        self.assertEqual((early["attachmentsGot"], early["resChange"], self.item(80004)), ([], [], 0))
        self.now[0] = closed["endTime"]
        opened = self.info()
        self.now[0] = opened["endTime"]
        later_closure = self.info()
        self.call("POST", "/fci/mail-auto-delete/", {"autoDelete": True})
        claimed = self.call("PATCH", path, {"attachmentsGot": True})[1]
        self.assertEqual(claimed["attachmentsGot"], [mail["id"]])
        self.assertIn(mail["id"], claimed["delMails"])
        self.info()
        self.assertEqual(self.item(80004), 0)
        self.assertEqual(self.race._load(self.profile)["pendingTicketRefunds"], 1)
        self.now[0] = later_closure["endTime"]
        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.call("GET", "/fci/RacePVPInfo", game=reboot)
        self.assertEqual(self.item(80004), 7)
        self.info()
        self.assertEqual(self.item(80004), 7)
        self.assertEqual(self.race._load(self.profile)["pendingTicketRefunds"], 0)

    def test_uploaded_battle_is_scored_in_old_cycle_and_unclaimed_rewards_are_mailed(self):
        run = self.enter()
        self.call("PATCH", f"/fci/dungeon/{run['dungeonUid']}/active/", self.snapshot(run, damage=60000).SerializeToString())
        self.now[0] = self.info()["endTime"]
        settled = self.info()
        self.assertEqual(settled["state"], self.race.MATCH)
        self.assertEqual(self.race._run(self.profile, run["dungeonUid"])["balance"], {"curBattleScore": 6000, "highBattleScore": 6000})
        self.assertEqual(len(self.race_mails()), 1)
        mail = self.race_mails()[0]
        expected = list(self.race._segment(settled["score"])["rewards"])
        expected += [r for score in sorted(self.race.SCORE_REWARDS) if score <= 6000 for r in self.race.SCORE_REWARDS[score]]
        self.assertEqual(mail["attachments"], [dict(type=r["type"], id=r.get("id", 0), param=r.get("param", 0), count=r["count"]) for r in expected])
        self.assertEqual(self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})[1], {"resChange": []})
        self.assertEqual(self.item(40002), 0)
        self.call("PATCH", f"/fci/mail/{mail['id']}", {"attachmentsGot": True})
        received = self.item(40002)
        self.assertGreater(received, 0)
        self.info()
        self.call("PATCH", f"/fci/mail/{mail['id']}", {"attachmentsGot": True})
        self.assertEqual(self.item(40002), received)

    def test_opening_and_closing_boundaries_and_non_accumulating_tickets(self):
        policy = self.game.reset_policy
        def at(value):
            self.now[0] = policy.parse_config_datetime(value)
            return self.info()
        self.assertEqual(at("2026-10-26 05:59:59")["state"], self.race.MATCH)
        self.assertEqual((self.item(80004), len(self.race_mails())), (0, 0))
        self.assertEqual(at("2026-10-26 06:00:00")["state"], self.race.FIGHT)
        run = self.enter()
        self.call("DELETE", f"/fci/racepvp-dungeon/{run['dungeonUid']}")
        self.assertEqual(at("2026-10-28 23:59:59")["state"], self.race.FIGHT)
        self.assertEqual(self.item(80004), 5)
        closed = at("2026-10-29 00:00:00")
        self.assertEqual((closed["state"], self.item(80004)), (self.race.MATCH, 0))
        self.assertEqual(closed["endTime"], policy.parse_config_datetime("2026-10-29 06:00:00"))
        self.assertEqual(self.call("POST", "/fci/racepvp-dungeon/", {"dungeonId": closed["dungeonId"]})[1]["error"], "race_cycle_closed")
        self.assertEqual(at("2026-10-29 05:59:59")["state"], self.race.MATCH)
        self.assertEqual(at("2026-10-29 06:00:00")["state"], self.race.FIGHT)
        self.assertEqual(self.item(80004), 6)
        with self.game.atomic_mutation():
            self.race._ticket(self.profile, set_count=17)
        self.assertEqual(at("2026-10-31 23:59:59")["state"], self.race.FIGHT)
        self.assertEqual(at("2026-11-01 00:00:00")["state"], self.race.MATCH)
        self.assertEqual(self.item(80004), 0)
        self.assertEqual(at("2026-11-02 06:00:00")["state"], self.race.FIGHT)
        self.assertEqual(self.item(80004), 6)

    def test_level_26_unlock_grants_once_and_locked_or_closed_login_has_no_rewards(self):
        with closing(self.game._connect()) as conn, conn:
            conn.execute("UPDATE game_profiles SET level=25 WHERE account_id=?", (self.profile["account_id"],))
        self.profile = self.game._profile(self.account)
        self.assertEqual(self.info()["state"], self.race.MATCH)
        self.assertFalse(self.call("GET", "/fci/RacePVPSummary/")[1]["rewardTag"])
        self.assertEqual((self.item(80004), len(self.race_mails())), (0, 0))
        self.assertIsNone(self.race._load(self.profile)["seasonOrigin"])
        with closing(self.game._connect()) as conn, conn:
            conn.execute("UPDATE game_profiles SET level=26 WHERE account_id=?", (self.profile["account_id"],))
        self.profile = self.game._profile(self.account)
        self.assertEqual(self.info()["state"], self.race.FIGHT)
        self.assertEqual(self.item(80004), 6)
        self.assertEqual(self.race.refresh_daily_items(self.profile), [])

    def test_eight_cycles_pay_table_rewards_then_reset_season_once(self):
        for cycle_no in range(1, 9):
            self.assertEqual(self.info()["round"], cycle_no)
            self.fight(self.enter(), damage=400000)
            self.now[0] = self.info()["endTime"]
            closed = self.info()
            self.assertEqual(closed["state"], self.race.MATCH)
            if cycle_no < 8:
                self.now[0] = closed["endTime"]
        state = self.race._load(self.profile)
        self.assertEqual(state["score"], 200)
        self.assertEqual(len(state["settlements"]), 8)
        self.assertEqual(len(state["seasonSettlements"]), 1)
        season_mail = next(m for m in self.race_mails() if "赛季" in m["title"])
        self.assertEqual(season_mail["attachments"], [{"type": 8, "id": 1004, "count": 1, "param": 0}])
        self.assertEqual(self.call("PATCH", f"/fci/mail/{season_mail['id']}", {"attachmentsGot": True})[0], 200)
        self.assertIn(1004, [medal["id"] for medal in self.call("GET", "/fci/medal/")[1]])
        self.assertEqual(len(self.race_mails()), 9)
        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.call("GET", "/fci/RacePVPInfo", game=reboot)
        self.assertEqual(len(self.race_mails()), 9)
        self.now[0] = closed["endTime"]
        self.assertEqual((self.info()["round"], self.info()["score"]), (1, 200))
        self.assertEqual(self.item(80004), 6)

    def test_offline_jump_settles_participated_cycle_and_season_without_phantom_rewards(self):
        self.fight(self.enter(), damage=400000)
        origin = self.race._load(self.profile)["seasonOrigin"]
        self.now[0] = self.race._cycle_by_index(origin + 9)["start"]
        info = self.info()
        self.assertEqual((info["round"], info["score"], info["state"]), (2, 200, self.race.FIGHT))
        self.assertEqual(len(self.race_mails()), 2)
        self.assertEqual(len(self.race._load(self.profile)["settlements"]), 1)
        self.info()
        self.assertEqual(len(self.race_mails()), 2)

    def test_closed_period_login_does_not_forget_previous_season_participation(self):
        self.fight(self.enter(), damage=400000)
        origin = self.race._load(self.profile)["seasonOrigin"]
        self.now[0] = self.race._cycle_by_index(origin + 2)["end"]
        self.info()
        self.assertFalse(self.race._load(self.profile)["enrolled"])
        self.now[0] = self.race._cycle_by_index(origin + 8)["start"]
        self.assertEqual((self.info()["score"], self.info()["round"]), (200, 1))
        self.assertEqual(len([mail for mail in self.race_mails() if "赛季" in mail["title"]]), 1)
        self.assertEqual(len(self.race._load(self.profile)["settlements"]), 1)

    def test_legacy_weekly_room_migration_preserves_score_claims_and_spent_tickets(self):
        created = self.enter()
        self.fight(created)
        self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})
        state = self.race._load(self.profile)
        legacy = {key: state[key] for key in ("week", "round", "score", "oldScore", "players", "rewardRecved",
                                            "activeUid", "isNew", "settlements", "status", "endTime", "dungeonId")}
        legacy["week"] = self.game.reset_policy.period_start(self.now[0], "week")
        legacy["day"] = self.game.reset_policy.day_key(self.now[0])
        self.race._save(self.profile, legacy)
        run = self.race._run(self.profile, created["dungeonUid"])
        run.pop("cycle")
        run.pop("cycleEnd")
        run["week"] = legacy["week"]
        self.race._save_run(self.profile, run)
        self.info()
        migrated = self.race._load(self.profile)
        self.assertEqual(migrated["players"], state["players"])
        self.assertEqual(migrated["rewardRecved"], [2000])
        self.assertEqual(self.item(80004), 5)
        self.assertEqual(self.race_mails(), [])
        self.assertEqual(self.race._run(self.profile, created["dungeonUid"])["cycle"], migrated["cycle"])
        self.assertEqual((self.item(40002), self.item(10001)), (20, 20))

    def test_cycle_mail_and_settlement_failure_roll_back_together(self):
        self.fight(self.enter(), damage=60000)
        state = self.race._load(self.profile)
        self.now[0] = state["cycleEnd"]
        published = []
        self.game.mailbox.notifier = lambda p, payload: published.append(payload)
        original_save = self.race._save
        def fail_settlement(profile, data):
            if data.get("cycleSettled"):
                raise RuntimeError("settlement failed")
            return original_save(profile, data)
        with patch.object(self.race, "_save", side_effect=fail_settlement):
            with self.assertRaisesRegex(RuntimeError, "settlement failed"):
                self.info()
        self.assertEqual(self.race_mails(), [])
        self.assertEqual(published, [])
        self.assertEqual(self.item(80004), 5)
        self.assertEqual(self.race._load(self.profile), state)
        self.info()
        self.assertEqual(len(self.race_mails()), 1)
        self.assertEqual(len(published), 1)

    def test_login_and_browsing_nine_cycles_never_enroll_or_grant_cycle_or_season_rewards(self):
        origin = self.race._cycle_at(self.now[0])["index"]
        original_resources = self.game._profile(self.account)["resources"]
        for step in range(9):
            with self.subTest(cycle=step):
                cycle = self.race._cycle_by_index(origin + step)
                self.now[0] = cycle["start"]
                list(self.game.login_messages(self.account))
                self.assertEqual(self.info()["score"], 200)
                self.assertFalse(self.call("GET", "/fci/RacePVPSummary/")[1]["rewardTag"])
                self.assertEqual(self.item(80004), 6)
                self.now[0] = cycle["end"]
                list(self.game.login_messages(self.account))
                self.assertEqual(self.info()["score"], 200)
                state = self.race._load(self.profile)
                self.assertFalse(state["enrolled"])
                self.assertIsNone(state["seasonOrigin"])
                self.assertIsNone(state["lastParticipatedCycle"])
                self.assertEqual(state["settlements"], [])
                self.assertEqual(state.get("seasonSettlements", []), [])
                self.assertEqual(self.race_mails(), [])
                self.assertEqual(self.item(80004), 0)
        self.assertEqual(self.game._profile(self.account)["resources"], original_resources)

    def test_empty_cancel_in_next_cycle_does_not_take_rank_points_or_earn_rewards(self):
        self.fight(self.enter(), damage=400000)
        self.now[0] = self.info()["endTime"]
        closed = self.info()
        self.assertEqual(closed["score"], 240)
        self.assertEqual(len(self.race_mails()), 1)
        self.now[0] = closed["endTime"]
        empty = self.enter()
        self.assertFalse(self.race._load(self.profile)["enrolled"])
        self.assertEqual(self.call("DELETE", f"/fci/racepvp-dungeon/{empty['dungeonUid']}")[0], 200)
        self.now[0] = self.info()["endTime"]
        self.assertEqual(self.info()["score"], 240)
        self.assertEqual(len(self.race_mails()), 1)
        self.assertEqual(len(self.race._load(self.profile)["settlements"]), 1)

    def test_native_zero_damage_surrender_counts_and_first_real_battle_anchors_season(self):
        origin = self.race._cycle_at(self.now[0])["index"]
        self.info()
        self.now[0] = self.race._cycle_by_index(origin + 3)["start"]
        run = self.enter()
        self.assertIsNone(self.race._load(self.profile)["seasonOrigin"])
        # Common/DungeonUtil.LeaveRacePvp(3) uploads the current bare snapshot;
        # it does not replace winTeam=0 with a fabricated loss or require damage.
        snapshot = self.snapshot(run, damage=0, own_damage=0, round_no=0, win=0)
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{run['dungeonUid']}/active/", snapshot.SerializeToString())[0], 200)
        self.assertFalse(self.race._load(self.profile)["enrolled"])
        result = self.call("POST", f"/fci/racepvp-dungeon/{run['dungeonUid']}/balance/", b"")[1]
        self.assertEqual(result, {"curBattleScore": 0, "highBattleScore": 0})
        state = self.race._load(self.profile)
        self.assertTrue(state["enrolled"])
        self.assertEqual((state["seasonOrigin"], state["lastParticipatedCycle"], state["round"]), (origin + 3, origin + 3, 1))
        self.now[0] = state["cycleEnd"]
        self.info()
        self.assertEqual(len(self.race_mails()), 1)

    def test_twenty_four_native_reentries_recover_uploaded_surrender_once_and_spend_one_new_ticket(self):
        run = self.enter()
        active = self.snapshot(run, damage=60000, round_no=2, win=0)
        self.call("PATCH", f"/fci/dungeon/{run['dungeonUid']}/active/", active.SerializeToString())
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiRacePVPDungeon"))
        body = cls(dungeon_id=run["baseInfo"]["dungeonId"], select_actor_group_id=1).SerializeToString()
        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        with ThreadPoolExecutor(max_workers=24) as executor:
            replies = list(executor.map(lambda i: self.call("POST", "/fci/racepvp-dungeon/", body,
                                                           game=self.game if i % 2 else reboot), range(24)))
        self.assertTrue(all(code == 200 for code, _ in replies), replies)
        self.assertEqual(len({data["dungeonUid"] for _, data in replies}), 1)
        self.assertNotEqual(replies[0][1]["dungeonUid"], run["dungeonUid"])
        self.assertEqual(self.item(80004), 4)
        recovered = self.race._run(self.profile, run["dungeonUid"])
        self.assertEqual(recovered["status"], "balanced")
        self.assertEqual(recovered["balance"], {"curBattleScore": 6000, "highBattleScore": 6000})
        self.assertEqual(self.race._own(self.profile, self.race._load(self.profile))["battleScore"], 6000)
        self.assertEqual(self.call("POST", f"/fci/racepvp-dungeon/{run['dungeonUid']}/balance/", b"")[1], recovered["balance"])
        reward_cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiRacePVPBattleScoreReward"))
        reward_body = reward_cls(battle_score=2000).SerializeToString()
        self.assertEqual(self.call("POST", "/fci/RacePVPBattleScoreReward/", reward_body)[0], 200)
        self.assertEqual(self.call("POST", "/fci/RacePVPBattleScoreReward/", reward_body)[1], {"resChange": []})
        self.assertEqual((self.item(40002), self.item(10001)), (20, 20))

    def test_last_ticket_completed_run_is_recovered_even_when_new_entry_cannot_be_paid(self):
        for _ in range(5):
            self.fight(self.enter(), damage=10000)
        run = self.enter()
        self.call("PATCH", f"/fci/dungeon/{run['dungeonUid']}/active/", self.snapshot(run, damage=90000).SerializeToString())
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiRacePVPDungeon"))
        body = cls(dungeon_id=run["baseInfo"]["dungeonId"], select_actor_group_id=1).SerializeToString()
        before_bag = self.game.systems._state(self.profile)
        before_resources = self.game._profile(self.account)["resources"]
        status, error = self.call("POST", "/fci/racepvp-dungeon/", body)
        self.assertEqual((status, error["error"]), (409, "race_ticket_exhausted"))
        self.assertEqual(self.item(80004), 0)
        self.assertEqual(self.game.systems._state(self.profile), before_bag)
        self.assertEqual(self.game._profile(self.account)["resources"], before_resources)
        self.assertEqual(self.race._run(self.profile, run["dungeonUid"])["balance"], {"curBattleScore": 9000, "highBattleScore": 9000})
        self.assertEqual(self.race._own(self.profile, self.race._load(self.profile))["battleScore"], 9000)

    def test_reentry_write_failure_rolls_back_recovery_new_ticket_and_notifications(self):
        run = self.enter()
        self.call("PATCH", f"/fci/dungeon/{run['dungeonUid']}/active/", self.snapshot(run, damage=60000).SerializeToString())
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiRacePVPDungeon"))
        body = cls(dungeon_id=run["baseInfo"]["dungeonId"], select_actor_group_id=1).SerializeToString()
        before = (self.race._load(self.profile), self.race._run(self.profile, run["dungeonUid"]),
                  self.game.systems._state(self.profile))
        original = self.race._save_run
        def fail_new_run(profile, data):
            if data["dungeonUid"] != run["dungeonUid"]:
                raise RuntimeError("new run failed")
            return original(profile, data)
        self.pushes.clear()
        with patch.object(self.race, "_save_run", side_effect=fail_new_run):
            with self.assertRaisesRegex(RuntimeError, "new run failed"):
                self.call("POST", "/fci/racepvp-dungeon/", body)
        self.assertEqual((self.race._load(self.profile), self.race._run(self.profile, run["dungeonUid"]),
                          self.game.systems._state(self.profile)), before)
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.call("POST", "/fci/racepvp-dungeon/", body)[0], 200)
        self.assertEqual(self.item(80004), 4)
        self.assertTrue(self.race._load(self.profile)["enrolled"])

    def test_old_phantom_participation_migration_preserves_assets_without_future_rewards(self):
        self.info()
        legacy = self.race._load(self.profile)
        legacy.pop("participationVersion")
        legacy.update(enrolled=True, lastParticipatedCycle=legacy["cycle"], seasonOrigin=legacy["cycle"], score=480)
        self.race._save(self.profile, legacy)  # Explicit version-2 migration fixture.
        before_bag = self.game.systems._state(self.profile)
        before_resources = self.game._profile(self.account)["resources"]
        self.assertEqual(self.info()["score"], 480)
        repaired = self.race._load(self.profile)
        self.assertFalse(repaired["enrolled"])
        self.assertIsNone(repaired["lastParticipatedCycle"])
        self.assertIsNone(repaired["seasonOrigin"])
        self.assertEqual(self.game.systems._state(self.profile), before_bag)
        self.assertEqual(self.game._profile(self.account)["resources"], before_resources)
        self.now[0] = self.race._cycle_by_index(legacy["cycle"] + 8)["start"]
        self.assertEqual(self.info()["score"], 480)
        self.assertEqual(self.race_mails(), [])

    def test_old_participation_migration_uses_last_balanced_cycle_instead_of_last_login(self):
        self.fight(self.enter(), damage=400000)
        previous = self.race._load(self.profile)
        self.now[0] = previous["nextStart"]
        self.info()
        state = self.race._load(self.profile)
        state.pop("participationVersion")
        state.update(enrolled=True, lastParticipatedCycle=state["cycle"])
        self.race._save(self.profile, state)  # Old version marked every login.
        mails = self.race_mails()
        self.info()
        migrated = self.race._load(self.profile)
        self.assertFalse(migrated["enrolled"])
        self.assertEqual(migrated["lastParticipatedCycle"], previous["cycle"])
        self.assertEqual(migrated["seasonOrigin"], previous["seasonOrigin"])
        self.assertEqual(self.race_mails(), mails)

    def test_legacy_weekly_migration_keeps_a_previous_week_real_battle_when_current_room_is_unused(self):
        created = self.enter()
        self.fight(created, damage=400000)
        previous = self.race._load(self.profile)
        self.now[0] = self.race._cycle_by_index(previous["cycle"] + 2)["start"]
        self.info()
        current = self.race._load(self.profile)
        # Explicit old weekly-schema fixture: this week's room was only viewed,
        # while the last real finished run belongs to the prior weekly room.
        legacy = {key: current[key] for key in ("week", "round", "score", "oldScore", "players", "rewardRecved",
                                              "activeUid", "isNew", "settlements", "status", "endTime", "dungeonId")}
        legacy["week"] = self.game.reset_policy.period_start(self.now[0], "week")
        legacy["round"] = 2
        self.race._save(self.profile, legacy)
        old_run = self.race._run(self.profile, created["dungeonUid"])
        old_run.pop("cycle")
        old_run.pop("cycleEnd")
        old_run["week"] = self.game.reset_policy.period_start(old_run["createdAt"], "week")
        self.race._save_run(self.profile, old_run)
        before_bag = self.game.systems._state(self.profile)
        self.info()
        migrated = self.race._load(self.profile)
        self.assertFalse(migrated["enrolled"])
        self.assertEqual(migrated["lastParticipatedCycle"], previous["cycle"])
        self.assertLessEqual(migrated["seasonOrigin"], previous["cycle"])
        self.assertEqual(self.race._run(self.profile, created["dungeonUid"])["cycle"], previous["cycle"])
        self.assertEqual(self.game.systems._state(self.profile), before_bag)
        self.now[0] = self.race._cycle_by_index(migrated["seasonOrigin"] + 8)["start"]
        self.info()
        self.assertEqual(len([m for m in self.race_mails() if "赛季" in m["title"]]), 1)


if __name__ == "__main__":
    unittest.main()
