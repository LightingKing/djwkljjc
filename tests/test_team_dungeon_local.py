from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from accounts import LocalAccounts
from game import LocalGame
import test_systems


class TeamDungeonLocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts, initial_level=30)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        profile = self.game._profile(self.account)
        state = self.game.systems._state(profile)
        state.setdefault("items", {})["80005"] = {"count": 1, "gotTime": 0}
        self.game.systems._save(profile, state)

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

    def test_canonical_dungeon_balance_alias_and_ticket_gate(self):
        status, entered = self.call("POST", "/fci/teamdungeon/enter", {"dungeonId": 7011})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](dungeonType=10, stageID=7011, gameRound=1, winTeam=1)
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{uid}/active", active.SerializeToString(), "application/wireformat")[0], 200)
        self.assertEqual(self.call("POST", f"/fci/dungeon/{uid}/balance", {})[0], 200)
        status, exhausted = self.call("POST", "/fci/teamdungeon/enter", {"dungeonId": 7011})
        self.assertEqual((status, exhausted["error"]), (409, "team_dungeon_ticket_not_enough"))


if __name__ == "__main__":
    unittest.main()
