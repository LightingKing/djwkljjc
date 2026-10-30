"""Player level and stage unlocking follow the original client tables."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dungeon import (  # noqa: E402
    MAX_PLAYER_LEVEL,
    PLAYER_LEVELS,
    STAGE_ENERGY_COST,
    STAGE_PREREQUISITE,
    LocalDungeons,
    _roll_player_level,
)


class PlayerLevelTests(unittest.TestCase):
    def test_full_client_level_table_is_loaded(self):
        self.assertEqual(MAX_PLAYER_LEVEL, 99)
        self.assertEqual(len(PLAYER_LEVELS), 99)
        # Per-level thresholds from xlsx/PlayerLevelInitConfig.
        self.assertEqual([PLAYER_LEVELS[lv].get("exp", 0) for lv in range(2, 10)], [1, 6, 6, 6, 6, 6, 6, 12])

    def test_exp_rolls_over_level_thresholds(self):
        self.assertEqual(_roll_player_level(1, 0), (1, 0))
        self.assertEqual(_roll_player_level(1, 1), (2, 0))
        self.assertEqual(_roll_player_level(2, 5), (2, 5))
        self.assertEqual(_roll_player_level(2, 6), (3, 0))
        # 1 + 6 + 6 = 13 EXP from level 1 reaches level 4 exactly.
        self.assertEqual(_roll_player_level(1, 13), (4, 0))
        self.assertEqual(_roll_player_level(1, 14), (4, 1))

    def test_level_is_capped_at_table_maximum(self):
        level, _ = _roll_player_level(MAX_PLAYER_LEVEL, 10**9)
        self.assertEqual(level, MAX_PLAYER_LEVEL)

    def test_player_exp_equals_energy_spent(self):
        # Normal stage: 1 to enter + 5 on win; first story battle: 1.
        self.assertEqual(STAGE_ENERGY_COST[1], 6)
        self.assertEqual(STAGE_ENERGY_COST[2012], 1)
        self.assertEqual(LocalDungeons._battle_exp_reward(1), 6)
        self.assertEqual(LocalDungeons._battle_exp_reward(2012), 1)
        # Non-fight story stages cost no energy.
        self.assertEqual(LocalDungeons._nonfight_exp_reward(2011), 0)


class StageUnlockTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.dungeons = LocalDungeons(SimpleNamespace(db_path=Path(self._temp.name) / "game.sqlite3"))

    def tearDown(self):
        self._temp.cleanup()

    def _unlocked(self, finished):
        snapshot = self.dungeons.chapter_snapshot(None, set(finished), compact=False)
        records = snapshot["record"]["dungeonRecord"]
        return {int(key) for key, record in records.items() if record["unlocked"]}, snapshot["summary"]

    def test_prerequisite_chain_matches_client_table(self):
        self.assertEqual(STAGE_PREREQUISITE[2012], 2011)
        self.assertEqual(STAGE_PREREQUISITE[2013], 2012)
        self.assertEqual(STAGE_PREREQUISITE[1], 2013)
        self.assertEqual(STAGE_PREREQUISITE[2], 1)

    def test_new_account_sees_only_the_first_story_stage(self):
        unlocked, summary = self._unlocked([])
        self.assertIn(2011, unlocked)
        self.assertNotIn(2012, unlocked)
        self.assertNotIn(1, unlocked)
        self.assertNotIn(8, unlocked)
        self.assertEqual(summary["newDungeonId"], [2011])

    def test_stages_open_one_by_one(self):
        unlocked, _ = self._unlocked([2011])
        self.assertIn(2012, unlocked)
        self.assertNotIn(2013, unlocked)
        unlocked, summary = self._unlocked([2011, 2012, 2013])
        self.assertIn(1, unlocked)
        self.assertNotIn(2, unlocked)
        self.assertEqual(summary["newDungeonId"], [1])
        unlocked, _ = self._unlocked([2011, 2012, 2013, 1, 2, 3])
        self.assertIn(4, unlocked)
        self.assertNotIn(5, unlocked)

    def test_chapter_gates_follow_pre_chapter_and_challenge_stars(self):
        state = LocalDungeons._chapter_state({"finished": set(), "targets": {}})
        unlocked = {cid for cid, ok in state["chapterUnlocked"].items() if ok}
        self.assertEqual(unlocked, {1, 1001, 2001})
        # Normal chapter 1 finishes with its keyDungeonId (8).
        finished = {2011, 2012, 2013, *range(1, 9)}
        state = LocalDungeons._chapter_state({"finished": finished, "targets": {}})
        self.assertIn(1, state["finishedChapters"])
        self.assertTrue(state["chapterUnlocked"][2] and state["chapterUnlocked"][1002])
        self.assertTrue(state["dungeonUnlocked"][2021])
        self.assertFalse(state["dungeonUnlocked"][9])  # waits for story 2023
        self.assertTrue(state["dungeonUnlocked"][5001] and state["dungeonUnlocked"][5002])
        self.assertTrue(state["chapterUnlocked"][3001])
        # Challenge chapter 3002: normal chapter 4 finished AND 4 challenge stars.
        finished |= {43}
        finished |= {17, 29}
        state = LocalDungeons._chapter_state({"finished": finished, "targets": {}})
        self.assertFalse(state["chapterUnlocked"][3002])
        targets = {11011: {1, 2, 3}, 11012: {1}}
        state = LocalDungeons._chapter_state({"finished": finished | {11011, 11012}, "targets": targets})
        self.assertEqual(state["totals"]["challenge"], 4)
        self.assertTrue(state["chapterUnlocked"][3002])

    def test_cleared_stage_stays_open(self):
        # Progress recorded under the old "everything unlocked" rule.
        unlocked, _ = self._unlocked([6])
        self.assertIn(6, unlocked)
        self.assertNotIn(5, unlocked)


if __name__ == "__main__":
    unittest.main()
