from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from accounts import LocalAccounts
from dungeon import ALL_NORMAL_DUNGEON_IDS, CHAPTER_DUNGEON_IDS, DUNGEON_CONFIG, _dungeon_type
from game import LocalGame
try:
    from tests.test_dungeon import DungeonTests
except ModuleNotFoundError:  # unittest discover adds tests/ directly to sys.path
    from test_dungeon import DungeonTests


class ExpandedDungeonTests(unittest.TestCase):
    def _setup(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        accounts = LocalAccounts(root / "accounts")
        game = LocalGame(root / "game", accounts)
        account = DungeonTests._guest_account(accounts)
        with closing(accounts._connect()) as connection:
            token = connection.execute(
                "SELECT access_token FROM accounts WHERE account_id = ?",
                (account["accountId"],),
            ).fetchone()[0]
        return temp, game, "Token " + token

    def test_configured_later_chapter_stage_is_playable(self):
        temp, game, auth = self._setup()
        try:
            request = game._types["ApiDungeon"]()
            request.dungeon_id = 10  # ChapterInfo chapter 2, a stage with 3 star targets.
            status, entered = game.handle_http(
                "POST", "/fci/dungeon/", request.SerializeToString(), "application/wireformat", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(entered["baseInfo"]["dungeonId"], 10)
            self.assertEqual(entered["baseInfo"]["dungeonType"], 2)
            active = game._types["FightActiveData"]()
            active.dungeonType = 2
            active.stageID = 10
            active.gameRound = 2
            active.winTeam = 1
            self.assertEqual(
                game.handle_http(
                    "PATCH", f"/fci/dungeon/{entered['dungeonUid']}/active/",
                    active.SerializeToString(), "application/wireformat", auth,
                ),
                (200, {}),
            )
            status, result = game.handle_http(
                "POST", f"/fci/dungeon/{entered['dungeonUid']}/balance",
                json.dumps({"stars": 2, "targetsDone": [1, 3]}).encode(),
                "application/json", auth,
            )
            self.assertEqual(status, 200)
            # Local tools may still name the targets; the client posts {}.
            self.assertEqual(result["dungeonId"], 10)
            self.assertEqual(result["stars"], 2)
            self.assertEqual(result["balanceInfo"]["targetsDone"], [1, 3])
            self.assertEqual(result["balanceInfo"]["difficulty"], "normal")
        finally:
            temp.cleanup()

    def test_hard_mode_persists_and_full_chapter_graph_is_typed(self):
        temp, game, auth = self._setup()
        try:
            status, entered = game.handle_http(
                "POST", "/fci/dungeon/", json.dumps({"dungeonId": 10, "difficulty": "hard"}).encode(),
                "application/json", auth,
            )
            self.assertEqual(status, 200)
            self.assertTrue(entered["baseInfo"]["isChallengeMode"])
            self.assertEqual(entered["baseInfo"]["difficulty"], "hard")
            status, chapter = game.handle_http("GET", "/fci/dungeon/chapter/2", b"", "application/wireformat", auth)
            self.assertEqual(status, 200)
            self.assertEqual(chapter.record_chapter.chapter_id, 2)
            # Chapter 2 is still locked: its record says so and none of its
            # stages has a record yet (record presence == opened stage).
            self.assertFalse(chapter.record_chapter.unlocked)
            self.assertNotIn(9, chapter.record_dungeons)
        finally:
            temp.cleanup()

    def test_every_chapter_stage_is_enterable(self):
        """Every stage of the 35 client ChapterInfo rows can be entered.

        Story/plot (1001+), normal (1-12), training (2001+) and challenge
        (3001+) chapters; the entry reports the stage's DungeonType.
        """

        temp, game, auth = self._setup()
        try:
            self.assertEqual(len(DUNGEON_CONFIG.chapter_rows), 35)
            self.assertEqual(len(CHAPTER_DUNGEON_IDS), 376)
            self.assertGreaterEqual(len(ALL_NORMAL_DUNGEON_IDS), 280)
            self.assertEqual(game.handle_http("GET", "/fci/dungeon/chapter", b"", "application/wireformat", auth)[0], 200)
            with closing(game._connect()) as connection:
                account_id = connection.execute("SELECT account_id FROM game_profiles").fetchone()[0]
            for dungeon_id in CHAPTER_DUNGEON_IDS:
                # Entry costs energy (DungeonReward.cost); refill each time.
                with closing(game._connect()) as connection, connection:
                    connection.execute(
                        "UPDATE game_profiles SET resources_json = ? WHERE account_id = ?",
                        (json.dumps([{"type": 6, "id": 0, "param": 0, "count": 120}]), account_id),
                    )
                status, payload = game.handle_http(
                    "POST",
                    "/fci/dungeon/",
                    json.dumps({"dungeonId": int(dungeon_id)}).encode(),
                    "application/json",
                    auth,
                )
                self.assertEqual(status, 200, dungeon_id)
                self.assertEqual(payload["baseInfo"]["dungeonId"], int(dungeon_id))
                self.assertEqual(payload["baseInfo"]["dungeonType"], _dungeon_type(dungeon_id))
            self.assertEqual({_dungeon_type(did) for did in CHAPTER_DUNGEON_IDS}, {1, 2, 6, 11})
        finally:
            temp.cleanup()

    def test_stage_metadata_exposes_only_captured_enemy_rows(self):
        """The partial DungeonMonster export must remain explicit at runtime."""

        temp, game, auth = self._setup()
        try:
            status, stage = game.handle_http(
                "GET", "/fci/dungeon/stage/1", b"", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(stage["status"], "configured")
            self.assertEqual(stage["dungeonId"], 1)
            self.assertEqual(stage["monsterId"], [3004001, 3003001, 1008005, 4001098, 0, 0])
            self.assertEqual(stage["completeness"], "partial")

            status, missing = game.handle_http(
                "GET", "/fci/dungeon/stage/9", b"", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(missing["status"], "unavailable")
            self.assertEqual(missing["dungeonId"], 9)

            status, entered = game.handle_http(
                "POST", "/fci/dungeon/", json.dumps({"dungeonId": 1}).encode(), "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(entered["baseInfo"]["stageMetadata"]["status"], "configured")
            self.assertEqual(entered["baseInfo"]["stageMetadata"]["source"], "data/config/DungeonMonster.json")
        finally:
            temp.cleanup()

    def test_story_metadata_exposes_captured_rows_without_claiming_full_story(self):
        """StoryDungeon evidence is queryable but stays separate from rewards."""

        temp, game, auth = self._setup()
        try:
            status, story = game.handle_http(
                "GET", "/fci/dungeon/story/1001", b"", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(story["status"], "configured")
            self.assertEqual(story["dungeonId"], 1001)
            self.assertEqual(story["monsterId"], [1002309, 0, 0, 2012098, 2014098, 2108098])
            self.assertEqual(story["level"], [1, 1, 1, 1, 1, 1])
            self.assertEqual(story["source"], "data/config/StoryDungeon.json")
            self.assertEqual(story["completeness"], "partial")

            status, missing = game.handle_http(
                "GET", "/fci/dungeon/story/1005", b"", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(missing["status"], "unavailable")
            self.assertEqual(missing["dungeonId"], 1005)

            status, method_error = game.handle_http(
                "POST", "/fci/dungeon/story/1001", b"{}", "application/json", auth
            )
            self.assertEqual(status, 405)
            self.assertEqual(method_error["error"], "method_not_allowed")
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
