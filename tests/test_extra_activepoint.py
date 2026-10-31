from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
from game import LocalGame
import test_systems


class ExtraActivePointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        accounts = LocalAccounts(Path(self.temp.name) / "accounts")
        self.game = LocalGame(Path(self.temp.name) / "game", accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, "application/json", self.auth)

    def test_native_snapshot_and_claim_are_persistent_and_idempotent(self):
        status, initial = self.call("GET", "/fci/task/extra-activepoint/1")
        self.assertEqual(status, 200)
        self.assertEqual(initial["extraActivePointType"], 1)
        self.assertEqual(initial["extraActivePoint"], 0)
        self.assertEqual([item["targetActivePoint"] for item in initial["progress"]], [120, 240, 360, 480, 600])

        self.game.tasks.record_extra_active_point(self.profile, 120, point_type=1)
        status, result = self.call("POST", "/fci/task/extra-activepoint/reward/",
                                   {"extraActivePointType": 1, "extraActivePoint": 120})
        self.assertEqual(status, 200)
        self.assertEqual(result["extraActivePointType"], 1)
        self.assertTrue(next(row for row in result["progress"] if row["targetActivePoint"] == 120)["rewardGot"])
        self.assertEqual({(change["baseRes"]["id"], change["baseRes"]["countDelta"])
                          for change in result["resChange"]}, {(60001, 1), (61001, 1)})

        status, second = self.call("POST", "/fci/task/extra-activepoint/reward/",
                                   {"extra_active_point_type": 1, "extra_active_point": 120})
        self.assertEqual(status, 409)
        self.assertEqual(self.game.systems._state(self.profile)["items"].get("60001", {}).get("count"), 1)

    def test_insufficient_extra_point_does_not_grant(self):
        status, result = self.call("POST", "/fci/task/extra-activepoint/reward/",
                                   {"extraActivePointType": 1, "extraActivePoint": 120})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "extra_active_point_insufficient")


if __name__ == "__main__":
    unittest.main()
