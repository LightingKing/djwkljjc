from __future__ import annotations

import json
import gc
import sqlite3
import tempfile
import unittest
from pathlib import Path

from accounts import LocalAccounts
from dungeon import SPECIAL_DUNGEONS, load_special_dungeons
from game import LocalGame


class SpecialDungeonTests(unittest.TestCase):
    def test_special_policy_is_loaded_from_reviewed_yaml(self):
        source = Path(__file__).resolve().parents[1] / "data" / "server-only" / "special-dungeons.yaml"
        loaded = load_special_dungeons(source)
        self.assertEqual(set(loaded), {"gold", "tower", "maze", "tournament"})
        self.assertEqual(loaded["gold"]["id"], 900001)
        self.assertEqual(loaded["tower"]["max_floor"], 10)
        self.assertEqual(loaded["maze"]["reward"][1]["id"], 910001)
        self.assertEqual(SPECIAL_DUNGEONS["tournament"]["source"], str(source))

    def test_special_policy_invalid_file_uses_reviewed_fallback(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "broken.yaml"
            source.write_text("stages: [broken]", encoding="utf-8")
            loaded = load_special_dungeons(source)
        self.assertEqual(loaded["gold"]["id"], 900001)
        self.assertEqual(loaded["tower"]["reward"][0]["count"], 1000)

    @staticmethod
    def guest(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        account_token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle(
            "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", account_token
        )[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", account_token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        auth = accounts.handle(
            "POST",
            "/Auth/",
            b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest",
            "application/wireformat",
            "AccountToken " + second[1]["accountToken"],
        )
        return accounts.verify_access_token(auth[1]["tokenAccess"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        account = self.guest(self.accounts)
        with sqlite3.connect(root / "accounts" / "accounts.sqlite3") as connection:
            token = connection.execute(
                "SELECT access_token FROM accounts WHERE account_id = ?", (account["accountId"],)
            ).fetchone()[0]
        self.account = account
        self.auth = "Token " + token

    def tearDown(self):
        self.game = None
        self.accounts = None
        gc.collect()
        self.temp.cleanup()

    def run_mode(self, payload):
        status, entered = self.game.handle_http(
            "POST", "/fci/dungeon/", json.dumps(payload).encode(), "application/json", self.auth
        )
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        status, result = self.game.handle_http(
            "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", self.auth
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["balanceInfo"]["win"])
        status, repeat = self.game.handle_http(
            "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", self.auth
        )
        self.assertEqual(status, 200)
        self.assertEqual(repeat, result)
        return entered, result

    def test_gold_dungeon_spends_power_and_grants_gold(self):
        before = next(item["count"] for item in self.game._profile(self.account)["resources"] if item["type"] == 6)
        entered, result = self.run_mode({"mode": "gold"})
        self.assertEqual(entered["mode"], "gold")
        self.assertEqual(next(item["count"] for item in entered["resChange"] if item["type"] == 6), before - 1)
        self.assertEqual(result["profits"][0]["countDelta"], 5000)

    def test_tower_unlocks_next_floor_after_restart(self):
        self.run_mode({"mode": "tower", "floor": 1})
        status, entered = self.game.handle_http(
            "POST", "/fci/dungeon/", b'{"mode":"tower","floor":2}', "application/json", self.auth
        )
        self.assertEqual(status, 200)
        self.assertEqual(entered["floor"], 2)
        self.game = None
        restarted = LocalGame(Path(self.temp.name) / "game", self.accounts)
        status, info = restarted.handle_http("GET", "/fci/tower/", b"", "application/json", self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(info["currentFloor"], 1)
        self.game = restarted

    def test_maze_and_tournament_persist_rewards(self):
        self.run_mode({"mode": "maze", "node": 2})
        self.run_mode({"mode": "tournament", "stage": 1})
        profile = self.game._profile(self.account)
        items = {(item["type"], item["id"]): item["count"] for item in profile["resources"]}
        self.assertEqual(items[(11, 910001)], 1)
        self.assertEqual(items[(11, 920001)], 1)


if __name__ == "__main__":
    unittest.main()
