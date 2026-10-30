"""MySpace page, likes, message board and small acknowledgement routes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
import test_systems  # noqa: E402


class PlayerPageTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        _, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)

    def tearDown(self):
        self._temp.cleanup()

    def call(self, method, path, payload=None, content_type="application/json"):
        body = payload if isinstance(payload, (bytes, type(None))) else json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def test_own_page_is_an_unwrapped_summary_with_name_avatar_and_team(self):
        status, page = self.call("GET", "/fci/playerpageinfo/")
        self.assertEqual(status, 200)
        self.assertTrue(page["playerId"].startswith("local-"))
        self.assertTrue(page["name"])          # never blank
        self.assertEqual(page["headPhoto"], 1)  # default avatar
        member = page["showGroup"]["members"][0]
        self.assertEqual((member["actorUid"], member["index"], member["actor"]["id"]), (1, 0, 1001))
        status, again = self.call("GET", f"/fci/playerpageinfo/{page['playerId']}")
        self.assertEqual(again["playerId"], page["playerId"])
        status, edited = self.call("PATCH", "/fci/playerpageinfo/", {"sign": "hi"})
        self.assertEqual((status, edited["sign"]), (200, "hi"))

    def test_bot_page_likes_and_message_board(self):
        status, bot = self.call("GET", "/fci/playerpageinfo/local-bot-003")
        self.assertEqual(status, 200)
        self.assertEqual(bot["playerId"], "local-bot-003")
        self.assertEqual(len(bot["showGroup"]["members"]), 3)
        self.assertGreater(bot["auraActorId"], 0)
        self.assertEqual(self.call("GET", "/fci/playerpageinfo/nobody")[0], 404)
        status, liked = self.call("POST", "/fci/PageClickLikeView/local-bot-003")
        self.assertEqual((status, liked["isClicked"]), (200, True))
        status, board = self.call("GET", "/fci/player-message/local-bot-003")
        self.assertEqual((status, board), (200, []))
        status, posted = self.call("POST", "/fci/player-message/local-bot-003", {"content": "hello"})
        self.assertEqual((status, posted["content"]), (200, "hello"))
        self.assertEqual(len(self.call("GET", "/fci/player-message/local-bot-003")[1]), 1)

    def test_acknowledgement_routes(self):
        # Danmu poll (wireformat ApiDanmu) answers with a list, not 400.
        self.assertEqual(self.call("POST", "/fci/danmu/", b"\x0a\x00\x10\x00", "application/wireformat"), (200, []))
        self.assertEqual(self.call("PATCH", "/fci/new-flag/", b"\x10\x00", "application/wireformat"), (200, {}))
        self.assertEqual(self.call("GET", "/fci/profit/"), (200, []))
        self.assertEqual(self.call("GET", "/fci/equip/custom-match/"), (200, {"equipCustomMatch": {}}))


if __name__ == "__main__":
    unittest.main()
