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
