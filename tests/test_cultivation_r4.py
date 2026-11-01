from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import actors
from accounts import LocalAccounts
from cultivation import LocalCultivation, TASKS
from game import LocalGame
import test_systems


class CultivationRound4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = [1793318400]
        self.accounts = LocalAccounts(Path(self.temp.name) / "accounts", clock=lambda: self.now[0])
        self.game = LocalGame(Path(self.temp.name) / "game", self.accounts, clock=lambda: self.now[0], initial_level=30)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        state = self.game.systems._state(self.profile)
        actor = actors.add(self.profile, state, 1502)
        actor.update(quality=3, level=30)
        self.game.systems._save(self.profile, state)
        self.module: LocalCultivation = self.game.cultivation

    def tearDown(self):
        self.temp.cleanup()

    def test_daily_draw_is_stable_and_level_filtered(self):
        self.module.snapshot(self.profile)
        first = self.module._state(self.profile)["dailySelection"]["53001"]
        self.assertLessEqual(len(first), 8)
        self.assertTrue(first)
        for tid in first:
            task = TASKS[tid]
            if task["goalType"] == 58:
                self.assertLessEqual(task["maxProgress"], 30)
        restarted = LocalGame(Path(self.temp.name) / "game", self.accounts, clock=lambda: self.now[0], initial_level=30)
        profile = restarted._profile(self.account)
        self.assertEqual(first, restarted.cultivation._state(profile)["dailySelection"]["53001"])
        self.now[0] += 86400
        restarted.cultivation.snapshot(profile)
        second = restarted.cultivation._state(profile)["dailySelection"]["53001"]
        self.assertTrue(second)
        self.assertNotEqual(first, second)

    def test_non_vip_reaches_100_from_normal_daily_draws(self):
        """Use the persisted draw and trusted wins; never seed task progress."""
        def call(method, path, body=None):
            status, result = self.game.handle_http(method, path, body, "application/json", self.auth)
            self.assertEqual(status, 200, result)
            return result

        claimed_ids = []
        total = 0
        for day in range(17):
            daily = [row for row in call("GET", "/fci/task/")["tasks"]
                     if row.get("actorCultivationActivityId") == 53001 and row.get("dailyRefresh")]
            self.assertEqual(len(daily), 8)
            ordinary = [row for row in daily if not row["actorCultivationVipOnly"]]
            self.assertEqual(len(ordinary), 6)
            self.assertTrue(all(row["actorCultivationRewardProgress"] == 1 for row in ordinary))
            # Feed the same trusted settlement hook used by dungeon balance.
            # Combat statistics satisfy the selected skill/kill objectives;
            # all task claims still use the client's native HTTP request.
            for dungeon_type, count in ((2, 5), (5, 5), (3, 1), (9, 2), (13, 2)):
                for _ in range(count):
                    self.module.on_battle_win(
                        self.profile, 8, actor_ids=[1502], dungeon_type=dungeon_type,
                        activity_type=51, activity_id=51002,
                        stats={"skillCounts": {1: 1, 2: 1, 3: 1},
                               "skillEnemyCounts": {(1502, 2): 3},
                               "enemyDeadInfo": [{"id": 15020001, "seat": 1}],
                               "lastKillSkill": 1})
            complete = {row["id"]: row for row in call("GET", "/fci/task/")["tasks"]}
            self.assertTrue(all(complete[row["id"]]["isDone"] for row in ordinary))
            for row in ordinary:
                result = call("POST", f'/fci/task/{row["id"]}/reward-actor-cultivation/', b"null")
                total = result["progress"]
                claimed_ids.append(row["id"])
                self.assertFalse(result["vip"])
                self.assertLessEqual(result["progressDaily"], 6)
                if total == 100:
                    break
            if total == 100:
                break
            self.now[0] += 86400
        self.assertEqual(total, 100)
        self.assertEqual(len(claimed_ids), 100)
        self.assertEqual(day, 16)
        result = call("POST", "/fci/actor-cultivation/progress-reward/",
                      json.dumps({"activityId": 53001, "progress": 100, "vip": False}).encode())
        self.assertEqual(result["info"]["progressReward"], 100)
        self.assertFalse(result["info"]["vip"])

    def test_battle_telemetry_advances_only_matching_objectives(self):
        self.module.snapshot(self.profile)
        state = self.module._state(self.profile)
        selected = [53012, 53013, 53014, 53017, 53018, 53021, 53025, 53029, 53030]
        state["dailySelection"]["53001"] = selected
        self.module._save(self.profile, state)
        self.module.snapshot(self.profile)
        self.module.on_battle_win(
            self.profile, 8, actor_ids=[1502], dungeon_type=2,
            stats={
                "recordHpList": [1000], "actorSeats": {1502: 1},
                "enemyDeadInfo": [{"id": 15020001, "seat": 1, "kind": 1}],
                "enemyBuffKindList": [123], "ownBuffKindList": [],
                "skillCounts": {1: 1, 2: 1}, "skillEnemyCounts": {(1502, 2): 1},
                "lastKillSkill": 1,
            },
        )
        state = self.module._state(self.profile)
        self.assertEqual(state["tasks"]["53012"]["progress"], 1)
        self.assertEqual(state["tasks"]["53013"]["progress"], 1)
        self.assertEqual(state["tasks"]["53014"]["progress"], 0)
        self.assertEqual(state["tasks"]["53017"]["progress"], 1)
        self.assertEqual(state["tasks"]["53018"]["progress"], 1)
        self.assertEqual(state["tasks"]["53021"]["progress"], 1)
        self.assertEqual(state["tasks"]["53030"]["progress"], 1)


if __name__ == "__main__":
    unittest.main()
