"""A level change sends BaseInfoChange so the city level refreshes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
from server.core.push_service import encode_base_push  # noqa: E402
import test_systems  # noqa: E402


class BasePushTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.pushes = []
        self.game.base_push_notifier = lambda profile, push_type: self.pushes.append((profile["level"], push_type))

    def tearDown(self):
        self._temp.cleanup()

    def call(self, method, path, payload=None, content_type="application/json"):
        body = payload if isinstance(payload, (bytes, type(None))) else json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def win(self, dungeon_id):
        status, entered = self.call("POST", "/fci/dungeon/", {"dungeonId": dungeon_id})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](dungeonType=1, stageID=dungeon_id, gameRound=1, winTeam=1)
        self.call("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(), "application/wireformat")
        status, _ = self.call("POST", f"/fci/dungeon/{uid}/balance", {})
        self.assertEqual(status, 200)
        self.call("DELETE", f"/fci/dungeon/{uid}")

    def test_level_up_pushes_base_info_change_once(self):
        level = lambda: int(self.game._profile(self.account)["level"])
        start = level()
        for _ in range(40):
            self.win(1)
            if level() != start:
                break
        self.assertGreater(level(), start)
        self.assertIn((level(), 2), self.pushes)
        self.assertEqual(len([p for p in self.pushes if p[0] == level()]), 1)

    def test_no_push_without_a_change_and_encoding(self):
        self.call("POST", "/fci/task/target-done-add/", {"targetId": 1})
        self.assertEqual(self.pushes, [])
        self.assertEqual(encode_base_push(2), b"\x08\x02")


if __name__ == "__main__":
    unittest.main()
