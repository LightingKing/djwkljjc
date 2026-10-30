from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dungeon import DUNGEON_CONFIG, NORMAL_DUNGEON_IDS, STORY_DUNGEON_IDS, _LOCAL_DUNGEON_POLICY
from server.core.content import load_dungeon_config


class DungeonConfigTests(unittest.TestCase):
    def test_checked_in_tables_drive_verified_local_ids(self):
        self.assertEqual(NORMAL_DUNGEON_IDS, tuple(range(1, 9)))
        self.assertTrue({2011, 2012, 2013}.issubset(STORY_DUNGEON_IDS))
        self.assertIn(81, DUNGEON_CONFIG.all_normal_ids)
        self.assertEqual(DUNGEON_CONFIG.reward_row(2012)["preDungeonId"], 2011)

    def test_checked_in_tables_are_the_complete_client_tables(self):
        # Regenerated from data/client-tables (xlsx__chapterinfo/dungeonreward).
        self.assertTrue(DUNGEON_CONFIG.chapter_complete)
        self.assertTrue(DUNGEON_CONFIG.reward_complete)
        self.assertEqual(len(DUNGEON_CONFIG.chapter_rows), 35)
        self.assertEqual(len(DUNGEON_CONFIG.reward_rows), 683)
        self.assertEqual(DUNGEON_CONFIG.all_normal_ids[-1], 5912)
        self.assertIn(261, DUNGEON_CONFIG.all_normal_ids)
        self.assertEqual(DUNGEON_CONFIG.training_ids, (5001, 5002, 5003, 5004, 5005, 5006))
        self.assertIn(11011, DUNGEON_CONFIG.challenge_ids)
        self.assertIn(2123, DUNGEON_CONFIG.story_ids)
        self.assertEqual(DUNGEON_CONFIG.reward_row(9)["preDungeonId"], 2023)
        chapter2 = next(row for row in DUNGEON_CONFIG.chapter_rows if row["chapterId"] == 2)
        self.assertEqual(chapter2["preChapterId"], 1)
        self.assertEqual(chapter2["keyDungeonId"], [17])

    def test_missing_tables_use_explicit_compatibility_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            config = load_dungeon_config(Path(raw))
            self.assertEqual(config.supported_normal_ids, tuple(range(1, 9)))
            self.assertEqual(config.story_ids, (2011, 2012, 2013))
            self.assertTrue(config.fallback_used)
            self.assertEqual(config.all_normal_ids, ())

    def test_player_exp_policy_is_externalized_and_provenanced(self):
        self.assertIn("2011", _LOCAL_DUNGEON_POLICY["tutorial_player_exp"])
        self.assertIn("2012", _LOCAL_DUNGEON_POLICY["story_player_exp"])
        self.assertGreaterEqual(int(_LOCAL_DUNGEON_POLICY["normal_player_exp"]), 0)
        self.assertIn("local", str(_LOCAL_DUNGEON_POLICY["provenance"]).lower())


if __name__ == "__main__":
    unittest.main()
