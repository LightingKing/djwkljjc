from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
from game import LocalGame
from tasks import LocalTasks


def _guest(accounts: LocalAccounts):
    """Same guest bootstrap as tests/test_systems.py LocalSystemsTests._guest."""

    first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    token = "AccountToken " + first[1]["accountToken"]
    entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    second_token = "AccountToken " + second[1]["accountToken"]
    auth_body = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
    auth = accounts.handle("POST", "/Auth/", auth_body, "application/wireformat", second_token)
    return accounts.verify_access_token(auth[1]["tokenAccess"]), "Token " + auth[1]["tokenAccess"]


def _ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


class LocalTasksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", accounts)
        self.account, _ = _guest(accounts)
        self.profile = dict(self.game._profile(self.account))
        self.profile["level"] = 1
        # 08:00 UTC: inside the 06:00 daily bucket, outside both online windows.
        self.now = [_ts(2026, 10, 29, 8, 0)]
        self.tasks = LocalTasks(self.game, clock=lambda: self.now[0])

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _get(self) -> dict:
        status, payload = self.tasks.handle_http("GET", "/fci/task/", None, self.profile)
        self.assertEqual(status, 200)
        json.dumps(payload)  # plain JSON
        return payload

    def _post(self, path: str, body: dict | None = None):
        raw = None if body is None else json.dumps(body).encode()
        return self.tasks.handle_http("POST", path, raw, self.profile)

    @staticmethod
    def _task(payload: dict, task_id: int) -> dict | None:
        return next((task for task in payload["tasks"] if task["id"] == task_id), None)

    @staticmethod
    def _deltas(changes: list) -> dict:
        return {(c["baseRes"]["type"], c["baseRes"]["id"]): c["baseRes"]["countDelta"] for c in changes if "baseRes" in c}

    def test_task_list_shape_matches_taskutil_initdata(self) -> None:
        payload = self._get()
        self.assertFalse(payload["clearAll"])
        self.assertEqual(payload["disableCategory"], [3, 4, 5, 6, 7])
        self.assertEqual(payload["activePoint"], 0)
        self.assertEqual(payload["activePointRewardGot"], [])
        self.assertIsInstance(payload["taskTargetDone"], list)
        ids = {task["id"] for task in payload["tasks"]}
        self.assertTrue({1, 2, 3, 15}.issubset(ids))
        self.assertNotIn(16, ids)  # closed-beta recharge rebate
        self.assertIn(1101, ids)
        self.assertNotIn(1102, ids)
        self.assertIn(1102, payload["delTasks"])
        for task in payload["tasks"]:
            self.assertEqual(set(task), {"id", "progress", "isDone", "rewardGot", "endTime"})
            self.assertEqual(task["endTime"], 0)
        # The task list request is the session-start login: task 9 is done.
        self.assertTrue(self._task(payload, 9)["isDone"])
        self.assertFalse(self._task(payload, 1)["isDone"])  # 11-16h window not reached
        self.now[0] = _ts(2026, 10, 29, 12, 30)
        self.assertTrue(self._task(self._get(), 1)["isDone"])
        status, result = self._post("/fci/task/1/reward/")
        self.assertEqual(status, 200)
        self.assertEqual(self._deltas(result["resChange"])[(6, 0)], 60)  # lunch energy
        self.now[0] = _ts(2026, 10, 29, 17, 0)
        self.assertTrue(self._task(self._get(), 1)["rewardGot"])
        self.assertFalse(self._task(self._get(), 2)["isDone"])  # 18-23h window

    def test_win_five_battles_completes_and_pays(self) -> None:
        self._get()
        for _ in range(4):
            self.tasks.on_battle_win(self.profile, 1, 6)
        task = self._task(self._get(), 3)
        self.assertEqual((task["progress"], task["isDone"]), (4, False))
        status, _ = self._post("/fci/task/3/reward/")
        self.assertEqual(status, 409)
        self.assertIn(3, self.tasks.on_battle_win(self.profile, 2, 6))
        task = self._task(self._get(), 3)
        self.assertEqual((task["progress"], task["isDone"], task["rewardGot"]), (5, True, False))

        status, result = self._post("/fci/task/3/reward/")
        self.assertEqual(status, 200)
        self.assertEqual(result["activePoint"], 20)
        deltas = self._deltas(result["resChange"])
        self.assertEqual(deltas[(7, 0)], 1000)
        self.assertEqual(deltas[(11, 10001)], 5)
        self.assertEqual(deltas[(11, 40002)], 5)
        items = self.game.systems._state(self.profile)["items"]
        self.assertGreaterEqual(items["10001"]["count"], 5)
        self.assertTrue(self._task(self._get(), 3)["rewardGot"])
        self.assertEqual(self._post("/fci/task/3/reward/")[0], 409)
        # 5 wins x 6 energy also feeds "spend 120 energy" (task 11).
        self.assertEqual(self._task(self._get(), 11)["progress"], 30)

    def test_active_point_milestone_and_weekly_points(self) -> None:
        self._get()
        self.assertEqual(self._post("/fci/task/activepoint/reward/", {"targetActivePoint": 20})[0], 409)
        for _ in range(5):
            self.tasks.on_battle_win(self.profile, 1, 6)
        self.assertEqual(self._post("/fci/task/3/reward/")[0], 200)  # +20 active points

        status, result = self._post("/fci/task/activepoint/reward/", {"targetActivePoint": 20})
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"resChange"})
        deltas = self._deltas(result["resChange"])
        self.assertEqual(deltas[(11, 10001)], 5)
        self.assertEqual(deltas[(11, 40002)], 5)
        self.assertEqual(self._post("/fci/task/activepoint/reward/", {"targetActivePoint": 20})[0], 409)
        self.assertEqual(self._post("/fci/task/activepoint/reward/", {"targetActivePoint": 40})[0], 409)
        payload = self._get()
        self.assertEqual(payload["activePoint"], 20)
        self.assertEqual(payload["activePointRewardGot"], [20])
        self.assertEqual(self._task(payload, 15)["progress"], 20)  # "reach 80 active points"
        status, week = self.tasks.handle_http("GET", "/fci/week-activepoint/", None, self.profile)
        self.assertEqual((status, week), (200, {"weekActivePoint": 20, "rewardGot": []}))

    def test_daily_rollover_at_0600_utc(self) -> None:
        self._get()
        for _ in range(5):
            self.tasks.on_battle_win(self.profile, 1, 6)
        self._post("/fci/task/3/reward/")
        self._post("/fci/task/activepoint/reward/", {"targetActivePoint": 20})
        self.tasks.on_level({**self.profile, "level": 5})
        self._post("/fci/task/1101/reward/", None)

        self.now[0] = _ts(2026, 10, 30, 5, 59)  # still the same daily bucket
        payload = self._get()
        self.assertTrue(self._task(payload, 3)["rewardGot"])
        self.assertEqual(payload["activePoint"], 20)

        self.now[0] = _ts(2026, 10, 30, 6, 0)
        payload = self._get()
        task = self._task(payload, 3)
        self.assertEqual((task["progress"], task["isDone"], task["rewardGot"]), (0, False, False))
        self.assertEqual(payload["activePoint"], 0)
        self.assertEqual(payload["activePointRewardGot"], [])
        self.assertTrue(self._task(payload, 9)["isDone"])  # new day's login
        self.assertIsNone(self._task(payload, 1101))  # main chain does not reset
        self.assertIn(1101, payload["delTasks"])
        self.assertIsNotNone(self._task(payload, 1102))
        # Weekly points survive the daily reset (Monday 06:00 is the weekly one).
        self.assertEqual(self.tasks.handle_http("GET", "/fci/week-activepoint/", None, self.profile)[1]
                         ["weekActivePoint"], 20)

    def test_level_five_completes_1101_and_unlocks_successor(self) -> None:
        payload = self._get()
        self.assertFalse(self._task(payload, 1101)["isDone"])
        self.assertEqual(self._post("/fci/task/1102/reward/")[0], 409)  # locked

        self.profile["level"] = 5
        self.assertIn(1101, self.tasks.on_level(self.profile))
        task = self._task(self._get(), 1101)
        self.assertEqual((task["progress"], task["isDone"]), (5, True))

        status, result = self._post("/fci/task/1101/reward/")
        self.assertEqual(status, 200)
        self.assertEqual(self._deltas(result["resChange"])[(1, 0)], 6)  # TaskInfo 1101: 6 player EXP
        self.assertEqual(result["activePoint"], 0)
        payload = self._get()
        self.assertIsNone(self._task(payload, 1101))
        successor = self._task(payload, 1102)
        self.assertEqual((successor["progress"], successor["isDone"]), (5, False))  # reach level 10
        self.assertEqual(self._post("/fci/task/1101/reward/")[0], 409)

    def test_record_before_got_and_unowned_routes(self) -> None:
        self._get()
        # Stage 12 cleared before 1104 (clear stage 12, recordBeforeGot) unlocks.
        self.tasks.on_battle_win(self.profile, 12, 6)
        self.tasks.on_battle_win(self.profile, 8, 6)
        self.assertEqual(self._post("/fci/task/1103/reward/")[0], 200)
        self.assertTrue(self._task(self._get(), 1104)["isDone"])
        # Other categories and telemetry stay with their existing handlers.
        self.assertIsNone(self.tasks.handle_http("POST", "/fci/task/target-done-add/", b"{}", self.profile))
        self.assertIsNone(self.tasks.handle_http("POST", "/fci/task/3001/reward/", None, self.profile))
        self.assertEqual(self.tasks.record(self.profile, "DeployDoneM", 3), [8, 1205])  # daily + main


if __name__ == "__main__":
    unittest.main()
