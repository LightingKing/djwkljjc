from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from accounts import LocalAccounts
from game import LocalGame
from social import LocalBotPool


class LocalSocialTests(unittest.TestCase):
    @staticmethod
    def guest(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        auth = accounts.handle("POST", "/Auth/", b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest", "application/wireformat", "AccountToken " + second[1]["accountToken"])
        return accounts.verify_access_token(auth[1]["tokenAccess"]), "Token " + auth[1]["tokenAccess"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, self.auth = self.guest(self.accounts)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method: str, path: str, payload: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else None
        return self.game.handle_http(method, path, body, "application/json", self.auth)

    def test_bots_friend_chat_and_assistant_state_persist(self):
        status, friends = self.call("GET", "/fci/friend/list")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(friends["friends"]), 2)
        status, initial_chat = self.call("GET", "/fci/chat")
        self.assertEqual(status, 200)
        self.assertTrue(initial_chat["messages"])
        self.assertTrue(initial_chat["messages"][0]["localBot"])
        target = friends["friends"][0]["playerId"]
        status, applied = self.call("POST", "/fci/friend/apply", {"playerId": "local-bot-003"})
        self.assertEqual(status, 200); self.assertTrue(applied["isFriend"])
        status, sent = self.call("POST", "/fci/chat", {"text": "hello local"})
        self.assertEqual(status, 200); self.assertEqual(sent["message"]["text"], "hello local")
        status, chat = self.call("GET", "/fci/chat")
        self.assertEqual(status, 200); self.assertEqual(chat["messages"][-1]["text"], "hello local")
        # The formation posts {token}; the reply is a plain list (the client
        # reads #assistants / assistants[1].token): friends first, then fixed
        # bot strangers.
        status, rows = self.call("POST", "/fci/dungeon-assistants", {"token": 9})
        self.assertEqual(status, 200)
        self.assertIsInstance(rows, list)
        self.assertTrue(rows)
        self.assertTrue(all(row["token"] == 9 for row in rows))
        friend_rows = [row for row in rows if not row["stranger"]]
        self.assertIn("local-bot-003", [row["playerId"] for row in friend_rows])
        self.assertTrue(any(row["stranger"] for row in rows))
        restarted = LocalGame(Path(self.temp.name) / "game", self.accounts)
        status, chat_again = restarted.handle_http("GET", "/fci/chat", authorization=self.auth)
        self.assertEqual(status, 200); self.assertEqual(chat_again["messages"][-1]["text"], "hello local")

    def test_async_pvp_and_ranking_are_deterministic_and_persistent(self):
        status, match = self.call("POST", "/fci/asyncpvp/match", {})
        self.assertEqual(status, 200)
        opponent = match["match"]["opponent"]["playerId"]
        status, result = self.call("POST", "/fci/asyncpvp/settle", {"win": True})
        self.assertEqual(status, 200); self.assertEqual(result["result"]["opponent"], opponent)
        status, ranking = self.call("GET", "/fci/leaderboard")
        self.assertEqual(status, 200)
        self.assertEqual(ranking["self"]["score"], 1025)
        self.assertEqual(ranking["self"]["rank"], min(row["rank"] for row in ranking["ranking"] if row["isSelf"]))

    def test_async_pvp_match_and_settle_retries_are_idempotent(self):
        status, first = self.call("POST", "/fci/asyncpvp/match", {})
        self.assertEqual(status, 200)
        self.assertFalse(first["duplicate"])
        match_id = first["match"]["matchId"]

        status, repeated_match = self.call("POST", "/fci/asyncpvp/match", {})
        self.assertEqual(status, 200)
        self.assertTrue(repeated_match["duplicate"])
        self.assertEqual(repeated_match["match"]["matchId"], match_id)

        status, settled = self.call("POST", "/fci/asyncpvp/settle", {"matchId": match_id, "win": True})
        self.assertEqual(status, 200)
        self.assertFalse(settled["duplicate"])
        rating = settled["rating"]

        # A retry after a new LocalGame instance must return the stored result
        # without adding another rating delta or balance mutation.
        restarted = LocalGame(Path(self.temp.name) / "game", self.accounts)
        status, repeated_settle = restarted.handle_http(
            "POST", "/fci/asyncpvp/settle", json.dumps({"matchId": match_id, "win": True}).encode(), "application/json", self.auth
        )
        self.assertEqual(status, 200)
        self.assertTrue(repeated_settle["duplicate"])
        self.assertEqual(repeated_settle["rating"], rating)
        self.assertEqual(repeated_settle["result"]["matchId"], match_id)

    def test_chat_ids_remain_monotonic_after_retention_trim(self):
        for index in range(105):
            status, sent = self.call("POST", "/fci/chat", {"text": f"message-{index}"})
            self.assertEqual(status, 200)
            if index:
                self.assertGreater(sent["message"]["id"], previous_id)
            previous_id = sent["message"]["id"]
        status, listing = self.call("GET", "/fci/chat")
        self.assertEqual(status, 200)
        ids = [item["id"] for item in listing["messages"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), 50)

    def test_bot_pool_progresses_slowly_with_server_clock(self):
        now = [LocalBotPool.BOT_EPOCH]
        pool = LocalBotPool(clock=lambda: now[0])
        initial = pool.get("local-bot-001")
        self.assertIsNotNone(initial)
        assert initial is not None
        self.assertEqual(initial["progressWeeks"], 0)
        now[0] += 14 * 86400
        later = pool.get("local-bot-001")
        self.assertIsNotNone(later)
        assert later is not None
        self.assertEqual(later["progressWeeks"], 2)
        self.assertEqual(later["level"], min(60, initial["level"] + 2))
        self.assertEqual(later["score"], initial["score"] + 50)

    def test_blocked_bot_is_not_recommended_or_readded(self):
        status, blocked = self.call("POST", "/fci/friend/block", {"playerId": "local-bot-003"})
        self.assertEqual(status, 200)
        self.assertTrue(blocked["blocked"])
        status, recommended = self.call("GET", "/fci/friend/recommend")
        self.assertEqual(status, 200)
        self.assertNotIn("local-bot-003", {row["playerId"] for row in recommended["friends"]})
        status, applied = self.call("POST", "/fci/friend/apply", {"playerId": "local-bot-003"})
        self.assertEqual(status, 409)
        self.assertEqual(applied["error"], "local_bot_blocked")


if __name__ == "__main__":
    unittest.main()
