from __future__ import annotations

import json
from contextlib import closing
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
        self.game = LocalGame(root / "game", self.accounts, initial_level=30)
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

    def test_groups_entry_ai_ticket_and_idempotent_balance(self):
        status, groups = self.call("GET", "/fci/teamdungeongroups/")
        self.assertEqual(status, 200)
        self.assertEqual(groups["result"], {"23001": 1})
        self.assertEqual(len(groups["groups"]), 7)
        self.assertEqual(groups["groups"][0]["dungeonIds"][0], 7011)
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
        self.assertTrue(any(change["baseRes"]["id"] == 71501 for change in settled["resChange"]))
        status, repeated = self.call("POST", f"/fci/teamdungeon/{uid}/balance", {})
        self.assertEqual(status, 200)
        self.assertEqual(repeated, settled)

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
