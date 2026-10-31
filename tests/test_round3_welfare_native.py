"""Round-3 welfare regressions using requests issued by the native Lua UI."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

from accounts import LocalAccounts
from game import LocalGame
from server import FixtureHandler, FixtureServer, JsonlLogger
from test_welfare_maze_local import guest


class WelfareNativeHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / "accounts")
        self.now = [int(datetime(2026, 10, 30, 4, tzinfo=timezone.utc).timestamp())]
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.account, self.auth = guest(self.accounts)
        self.server = FixtureServer(
            ("127.0.0.1", 0), self.root, JsonlLogger(self.root / "requests.jsonl"),
            accounts=self.accounts, game=self.game,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, body=None, content_type="application/json"):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body, {
            "Authorization": self.auth, "Content-Type": content_type,
        })
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        payload = FixtureHandler._wire_json_payload(raw) if response.status == 200 else json.loads(raw)
        return response.status, payload

    def assets(self):
        profile = self.game._profile(self.account)
        return profile["resources"], self.game.systems._state(profile).get("items", {})

    def test_story_chip_read_has_top_level_lists_even_before_selection(self):
        # DungeonStory.ReplaceChip iterates info.settlePosInfo directly, even
        # before the player has ever opened the chip-exchange welfare tab.
        status, info = self.request("GET", "/fci/chipexchange/")
        self.assertEqual(status, 200)
        self.assertEqual(info, {
            "lastActivitySeason": 20261026, "modifyCount": 0,
            "settlePosInfo": [], "exchangePosInfo": [],
        })

    def test_native_chip_selection_keeps_shape_and_limits_after_restart(self):
        # ActorChipExchangeCustom posts {chips = requestArray}, then stores
        # the response directly at fci/chipexchange/ for DungeonStory to read.
        order = [20002, 20116, 20014, 20128, 20107, 20108, 20012, 20006]
        status, first = self.request("POST", "/fci/chipexchange/", json.dumps({"chips": order}).encode())
        self.assertEqual(status, 200)
        self.assertEqual(first.get("settlePosInfo"), order)
        native = json.dumps({"chips": order[::-1]}).encode()
        status, changed = self.request("POST", "/fci/chipexchange/", native)
        self.assertEqual(status, 200)
        self.assertEqual(changed, {
            "lastActivitySeason": 20261026, "modifyCount": 2,
            "settlePosInfo": order[::-1], "exchangePosInfo": [],
        })
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.server.game = self.game
        self.assertEqual(self.request("GET", "/fci/chipexchange/"), (200, changed))
        self.assertEqual(self.request("POST", "/fci/chipexchange/", native), (200, changed))
        status, rejected = self.request("POST", "/fci/chipexchange/", json.dumps({"chips": order}).encode())
        self.assertEqual(status, 509)
        self.assertIn("code", rejected["notify"])

    def test_unknown_reward_paths_and_forged_body_cannot_mint_resources_or_points(self):
        self.request("GET", "/fci/task/")
        before_assets = self.assets()
        before_points = self.request("GET", "/fci/task/extra-activepoint/1")[1]
        for task_id in (999000, 999001, 999002):
            for body in (b"null", json.dumps({"taskId": task_id}).encode(), b'{"taskId":60001}'):
                with self.subTest(task_id=task_id, body=body):
                    status, rejected = self.request("POST", f"/fci/task/{task_id}/reward/", body)
                    self.assertEqual(status, 509)
                    self.assertIn("code", rejected["notify"])
        self.assertEqual(self.assets(), before_assets)
        self.assertEqual(self.request("GET", "/fci/task/extra-activepoint/1")[1], before_points)

    def test_theme_alias_has_only_real_offered_tasks_and_cannot_claim_rewards(self):
        status, snapshot = self.request("GET", "/fci/task/")
        self.assertEqual(status, 200)
        expected = [row for row in snapshot["tasks"] if row.get("category") == 102]
        self.assertEqual(len(expected), 19)
        status, theme = self.request("GET", "/fci/themeActivityTasks")
        self.assertEqual((status, theme), (200, expected))
        before_assets = self.assets()
        before_points = self.request("GET", "/fci/task/extra-activepoint/1")[1]
        for route in ("/fci/themeActivityTasks/77/", "/fci/theme-activity-tasks/78/", "/fci/themeActivityTasks"):
            status, rejected = self.request("POST", route, b'{"taskId":60001}')
            self.assertEqual(status, 509)
            self.assertIn("code", rejected["notify"])
        self.assertEqual(self.assets(), before_assets)
        self.assertEqual(self.request("GET", "/fci/task/extra-activepoint/1")[1], before_points)

    def test_native_login_theme_reward_returns_extra_points_once(self):
        # GET /fci/task is the real login trigger. No task flags or balances
        # are injected: it completes native login task 60001 naturally.
        status, snapshot = self.request("GET", "/fci/task/")
        self.assertEqual(status, 200)
        login = next(row for row in snapshot["tasks"] if row["id"] == 60001)
        self.assertTrue(login["isDone"])
        status, claimed = self.request("POST", "/fci/task/60001/reward/", b"null")
        self.assertEqual(status, 200)
        self.assertEqual(claimed.get("extraActivePoint"), 20)
        self.assertIsInstance(claimed["extraActivePoint"], int)
        self.assertTrue(claimed["resChange"])
        after_assets = self.assets()
        status, rejected = self.request("POST", "/fci/task/60001/reward/", b"null")
        self.assertEqual(status, 509)
        self.assertIn("code", rejected["notify"])
        # A valid but unfinished task must remain guarded by LocalTasks too.
        self.assertEqual(self.request("POST", "/fci/task/60002/reward/", b"null")[0], 509)
        self.assertEqual(self.assets(), after_assets)
        self.assertEqual(self.request("GET", "/fci/task/extra-activepoint/1")[1]["extraActivePoint"], 20)


if __name__ == "__main__":
    unittest.main()
