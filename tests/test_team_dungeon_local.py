from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from accounts import LocalAccounts
from game import LocalGame
from grants import grant_rewards
import test_systems


class TeamDungeonLocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.now = [int(datetime.fromisoformat("2026-11-03T12:00:00+08:00").timestamp())]
        self.game = LocalGame(root / "game", self.accounts, initial_level=30,
                              initial_actor_ids=[1001, 1002, 1501, 1502], clock=lambda: self.now[0])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        profile = self.game._profile(self.account)
        grant_rewards(self.game, profile, [{"type": 11, "id": 80005, "count": 1}])

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method: str, path: str, payload=None, content_type="application/json"):
        if content_type == "application/wireformat":
            body = payload
        elif payload is None:
            body = None
        else:
            body = json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def item(self, rid: int) -> int:
        profile = self.game._profile(self.account)
        return self.game.systems._state(profile).get("items", {}).get(str(rid), {}).get("count", 0)

    def test_groups_entry_ai_ticket_and_idempotent_balance(self):
        status, groups = self.call("GET", "/fci/teamdungeongroups/")
        self.assertEqual(status, 200)
        self.assertEqual(groups, {"23001": 1})
        summary = self.call("GET", "/fci/teamdungeon/summary/")[1]
        self.assertEqual(len(summary["groups"]), 7)
        self.assertEqual(summary["groups"][0]["dungeonIds"][0], 7011)
        status, entered = self.call("POST", "/fci/teamdungeon/enter", {"dungeonGroup": 1, "level": 1})
        self.assertEqual(status, 200)
        self.assertEqual((entered["dungeonId"], entered["mode"]), (7011, "team"))
        self.assertEqual(len(entered["roomInfo"]["aiTeammates"]), 2)
        room = entered["roomInfo"]
        self.assertEqual(room["roomLevelLimit"], 30)
        self.assertFalse(room["allowFastJoin"])
        self.assertEqual(room["memberList"], list(room["actorInfo"]))
        self.assertTrue(all("playerInfo" in room["actorInfo"][player] for player in room["memberList"]))
        self.assertEqual(self.game.activity_modes._item_count(self.game._profile(self.account), 80005), 0)
        uid = entered["dungeonUid"]
        # The battle result uses the real FightActiveData protobuf bytes.
        active = self.game._types["FightActiveData"](dungeonType=10, stageID=7011, gameRound=1, winTeam=1)
        status, _ = self.call("PATCH", f"/fci/teamdungeon/{uid}/active", active.SerializeToString(), "application/wireformat")
        self.assertEqual(status, 200)
        status, settled = self.call("POST", f"/fci/teamdungeon/{uid}/balance", {})
        self.assertEqual(status, 200)
        self.assertTrue(settled["win"])
        self.assertTrue(settled["localAi"])
        self.assertEqual(settled["profits"], [])
        self.assertEqual(settled["balanceInfo"]["profits"], [])
        self.assertTrue(any(change["baseRes"]["id"] == 71501 for change in settled["resChange"]))
        self.assertEqual(self.item(1016), 8)
        status, repeated = self.call("POST", f"/fci/teamdungeon/{uid}/balance", {})
        self.assertEqual(status, 200)
        self.assertEqual(repeated, settled)
        self.assertEqual(self.item(1016), 8)

    def test_active_null_and_malformed_payloads_do_not_crash_or_close_room(self):
        status, entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        for body in (b"null", b"{broken", b"\xff\xff"):
            with self.subTest(body=body):
                result = self.call("PATCH", f"/fci/teamdungeon/{uid}/active/", body,
                                   "application/wireformat")
                self.assertEqual(result, (400, {"error": "invalid_fight_active_data"}))
        self.assertEqual(self.call("GET", f"/fci/teamdungeon/{uid}/")[1]["status"], "active")

    def test_new_room_refunds_only_an_unstarted_room(self):
        first = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})[1]
        status, second = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7021})
        self.assertEqual(status, 200)
        self.assertNotEqual(second["dungeonUid"], first["dungeonUid"])
        self.assertEqual(self.item(80005), 0)
        self.assertEqual(self.call("GET", f"/fci/teamdungeon/{first['dungeonUid']}/")[1]["status"], "failed")
        active = self.game._types["FightActiveData"](dungeonType=10, stageID=7021, winTeam=1)
        self.assertEqual(self.call("PATCH", f"/fci/teamdungeon/{second['dungeonUid']}/active/",
                                   active.SerializeToString(), "application/wireformat")[0], 200)
        status, refused = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7031})
        self.assertEqual((status, refused["error"]), (409, "team_dungeon_ticket_not_enough"))
        self.assertEqual(self.call("GET", f"/fci/teamdungeon/{second['dungeonUid']}/")[1]["status"], "active")

    def test_start_uses_selected_owned_actors_and_is_immutable_on_retry(self):
        entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})[1]
        uid = entered["dungeonUid"]
        status, started = self.call("POST", f"/fci/teamdungeon/{uid}/start/", {"actorUids": [2, 4]})
        self.assertEqual(status, 200)
        self.assertEqual(started["baseInfo"]["actorUid"], [2, 0, 0, 4, 0, 0])
        self.assertEqual(started["baseInfo"]["actorId"], [1002, 0, 0, 1502, 0, 0])
        player_id = self.game._profile(self.account)["player_id"]
        self.assertEqual([row["uid"] for row in started["roomInfo"]["actorInfo"][player_id]["actors"]], [2, 4])
        replay = self.call("POST", f"/fci/teamdungeon/{uid}/start/", {"actorUids": [1, 3]})
        self.assertEqual(replay, (200, started))
        self.assertEqual(self.item(80005), 0)
        self.assertEqual(self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7021})[0], 409)

    def test_start_rejects_invalid_actor_selection_without_consuming_room(self):
        entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})[1]
        uid = entered["dungeonUid"]
        for actor_uids in ([999, 4], [2, 2], [2, 4, 1], []):
            with self.subTest(actor_uids=actor_uids):
                self.assertEqual(self.call("POST", f"/fci/teamdungeon/{uid}/start/",
                                           {"actorUids": actor_uids})[0], 400)
        # Invalid selections did not mark the room started, so it is refundable.
        self.assertEqual(self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7021})[0], 200)
        self.assertEqual(self.item(80005), 0)

    def test_team_open_hours_are_enforced_at_create_and_start(self):
        self.now[0] = int(datetime.fromisoformat("2026-11-03T05:59:59+08:00").timestamp())
        self.assertEqual(self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011}),
                         (409, {"error": "activity_closed_today"}))
        self.assertEqual(self.item(80005), 1)
        self.assertEqual(self.team_runs(), 0)
        self.now[0] += 1
        status, entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        self.now[0] = int(datetime.fromisoformat("2026-11-04T00:00:00+08:00").timestamp())
        self.assertEqual(self.call("POST", f"/fci/teamdungeon/{entered['dungeonUid']}/start/", {"actorUids": [2, 4]}),
                         (409, {"error": "activity_closed_today"}))
    def test_team_dungeon_candy_respects_season_cap(self):
        profile = self.game._profile(self.account)
        state = self.game.welfare._state(profile)
        state["welfare"]["earnedMaterials"]["1016"] = 746
        self.game.welfare._save(profile, state)
        status, entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](
            dungeonType=10, stageID=7011, gameRound=1, winTeam=1,
        )
        self.assertEqual(self.call(
            "PATCH", f"/fci/teamdungeon/{uid}/active/",
            active.SerializeToString(), "application/wireformat",
        )[0], 200)
        status, settled = self.call("POST", f"/fci/teamdungeon/{uid}/balance/", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.item(1016), 4)
        self.assertEqual(self.game.welfare._state(profile)["welfare"]["earnedMaterials"]["1016"], 750)
        self.assertTrue(any(
            change["baseRes"]["id"] == 1016 and change["baseRes"]["countDelta"] == 4
            for change in settled["resChange"]
        ))

    def test_balance_empty_body_requires_real_fight_active_data(self):
        status, entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        status, rejected = self.call("POST", f"/fci/teamdungeon/{uid}/balance/", {})
        self.assertEqual((status, rejected["error"]), (409, "battle_not_finished"))
        active = self.game._types["FightActiveData"](
            dungeonType=10, stageID=7011, gameRound=1, winTeam=1,
        )
        status, ack = self.call("PATCH", f"/fci/teamdungeon/{uid}/active/", active.SerializeToString(), "application/wireformat")
        self.assertEqual((status, ack), (200, {}))
        status, settled = self.call("POST", f"/fci/teamdungeon/{uid}/balance/", {})
        self.assertEqual(status, 200)
        self.assertTrue(settled["balanceInfo"]["win"])

    def test_damage_report_uses_client_player_attribution_and_is_cached(self):
        status, entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        player_id = self.game._profile(self.account)["player_id"]
        bot_id = entered["roomInfo"]["aiTeammates"][0]["playerId"]
        active = self.game._types["FightActiveData"](
            dungeonType=10, stageID=7011, gameRound=4, winTeam=1,
        )
        # The client records outgoing damageList on the attacking team, but
        # totalDamage on the receiving team. Team number, not list order,
        # identifies our side. Reflected/DOT damage uses the same attribution.
        enemy = active.teamList.add(team=2)
        enemy.statistic.totalDamage = 99999
        enemy.statistic.damageList.add(playerId="enemy", damage=750)
        own = active.teamList.add(team=1)
        own.statistic.totalDamage = 750
        own.statistic.damageList.add(playerId=player_id, damage=4321)
        own.statistic.damageList.add(playerId=bot_id, damage=987)
        self.assertEqual(self.call(
            "PATCH", f"/fci/dungeon/{uid}/active/",
            active.SerializeToString(), "application/wireformat",
        )[0], 200)
        status, settled = self.call("POST", f"/fci/teamdungeon/{uid}/balance/", {})
        self.assertEqual(status, 200)
        # DungeonBalanceTeam indexes result.playerDamages by playerId.
        self.assertEqual(settled["playerDamages"], {player_id: 4321, bot_id: 987})
        self.assertEqual(self.call("POST", f"/fci/teamdungeon/{uid}/balance/", {}), (200, settled))

    def test_defeat_damage_report_does_not_substitute_a_fixed_amount(self):
        status, entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        player_id = self.game._profile(self.account)["player_id"]
        active = self.game._types["FightActiveData"](
            dungeonType=10, stageID=7011, gameRound=3, winTeam=2,
        )
        own = active.teamList.add(team=1)
        own.statistic.damageList.add(playerId=player_id, damage=12345)
        self.assertEqual(self.call(
            "PATCH", f"/fci/dungeon/{uid}/active/",
            active.SerializeToString(), "application/wireformat",
        )[0], 200)
        status, settled = self.call("POST", f"/fci/teamdungeon/{uid}/balance/", {})
        self.assertEqual(status, 200)
        self.assertFalse(settled["win"])
        self.assertEqual(settled["playerDamages"], {player_id: 12345})
        self.assertEqual(self.item(1016), 0)

    def test_damage_report_does_not_invent_missing_player_statistics(self):
        status, entered = self.call("POST", "/fci/teamdungeon/enter/", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](
            dungeonType=10, stageID=7011, gameRound=1, winTeam=1,
        )
        # A team total alone cannot establish which player dealt the damage.
        enemy = active.teamList.add(team=2)
        enemy.statistic.totalDamage = 5000
        enemy.statistic.damageList.add(playerId="enemy", damage=2000)
        self.assertEqual(self.call(
            "PATCH", f"/fci/dungeon/{uid}/active/",
            active.SerializeToString(), "application/wireformat",
        )[0], 200)
        status, settled = self.call("POST", f"/fci/teamdungeon/{uid}/balance/", {})
        self.assertEqual(status, 200)
        self.assertEqual(settled["playerDamages"], {})

    def test_canonical_dungeon_balance_alias_and_ticket_gate(self):
        status, entered = self.call("POST", "/fci/teamdungeon/enter", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](dungeonType=10, stageID=7011, gameRound=1, winTeam=1)
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{uid}/active", active.SerializeToString(), "application/wireformat")[0], 200)
        self.assertEqual(self.call("POST", f"/fci/dungeon/{uid}/balance", {})[0], 200)
        status, exhausted = self.call("POST", "/fci/teamdungeon/enter", {"dungeonId": 7011})
        self.assertEqual((status, exhausted["error"]), (409, "team_dungeon_ticket_not_enough"))

    def team_runs(self):
        with closing(self.game._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM local_dungeons WHERE mode='team'").fetchone()[0]

    def test_local_entry_failures_roll_back_ticket_and_run_then_retry_once(self):
        # This tests the existing local helper's transaction only. The native
        # DungeonTeamService room protocol is a separate implementation task.
        profile = self.game._profile(self.account)
        for failing_step in ('_new_uid', '_team_room'):
            with self.subTest(failing_step=failing_step):
                with patch.object(self.game.dungeons, failing_step, side_effect=RuntimeError('create failed')):
                    with self.assertRaisesRegex(RuntimeError, 'create failed'):
                        self.game.dungeons._team_enter(profile, {'dungeonId': 7011})
                self.assertEqual(self.game.activity_modes._item_count(profile, 80005), 1)
                self.assertEqual(self.team_runs(), 0)
        status, first = self.game.dungeons._team_enter(profile, {'dungeonId': 7011})
        self.assertEqual(status, 200)
        status, replay = self.game.dungeons._team_enter(profile, {'dungeonId': 7011})
        self.assertEqual((status, replay['dungeonUid']), (200, first['dungeonUid']))
        self.assertEqual(self.game.activity_modes._item_count(profile, 80005), 0)
        self.assertEqual(self.team_runs(), 1)

    def test_ticket_helper_swallowed_failure_does_not_commit_a_partial_debit(self):
        profile = self.game._profile(self.account)
        original = self.game.activity_modes._change_item
        def failing_debit(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('failure after ticket save')
        with patch.object(self.game.activity_modes, '_change_item', side_effect=failing_debit):
            result = self.game.dungeons._team_enter(profile, {'dungeonId': 7011})
        self.assertEqual(result[0], 409)
        self.assertEqual(self.game.activity_modes._item_count(profile, 80005), 1)
        self.assertEqual(self.team_runs(), 0)

    def test_successful_team_entry_is_undone_when_the_outer_transaction_fails(self):
        profile = self.game._profile(self.account)
        with self.assertRaisesRegex(ValueError, 'outer failed'):
            with self.game.atomic_mutation():
                self.assertEqual(self.game.dungeons._team_enter(profile, {'dungeonId': 7011})[0], 200)
                raise ValueError('outer failed')
        self.assertEqual(self.game.activity_modes._item_count(profile, 80005), 1)
        self.assertEqual(self.team_runs(), 0)


if __name__ == "__main__":
    unittest.main()
