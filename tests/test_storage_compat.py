from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from accounts import LocalAccounts
from game import LocalGame
from storage.compat import resolve_unified_store_path


class StorageCompatibilityTests(unittest.TestCase):
    def test_explicit_store_is_shared_by_legacy_modules(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shared = root / "state" / "local.sqlite3"
            accounts = LocalAccounts(root / "accounts", unified_store_path=shared)
            game = LocalGame(root / "game", accounts, unified_store_path=shared)

            self.assertEqual(accounts.db_path, shared.resolve())
            self.assertEqual(game.db_path, shared.resolve())
            self.assertEqual(game.economy.db_path, shared.resolve())
            self.assertEqual(resolve_unified_store_path(root / "state", "local.sqlite3"), shared.resolve())

            connection = sqlite3.connect(shared)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            finally:
                connection.close()
            self.assertTrue({"accounts", "sessions", "game_profiles", "economy_profiles"} <= tables)

    def test_unified_guest_and_profile_rows_share_one_database(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shared = root / "local.sqlite3"
            accounts = LocalAccounts(root / "accounts", unified_store_path=shared)
            game = LocalGame(root / "game", accounts, unified_store_path=shared)

            status, session = accounts.handle(
                "POST", "/AccountSession/", b"", "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz = "AccountToken " + session["accountToken"]
            status, entry = accounts.handle(
                "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", authz
            )
            self.assertEqual(status, 200)
            guest_entry = entry["entry"]["entry"].encode("utf-8")
            accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz)
            status, second = accounts.handle(
                "POST", "/AccountSession/", b"", "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz = "AccountToken " + second["accountToken"]
            auth_body = b"\x0a" + bytes((len(guest_entry),)) + guest_entry + b"\x12\x05guest"
            status, authenticated = accounts.handle(
                "POST", "/Auth/", auth_body, "application/wireformat", authz
            )
            self.assertEqual(status, 200)
            account = accounts.verify_access_token(authenticated["tokenAccess"])
            game._profile(account)
            game.economy._state(account)

            connection = sqlite3.connect(shared)
            try:
                for table in ("accounts", "sessions", "game_profiles", "economy_profiles"):
                    self.assertEqual(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= 1,
                        True,
                        table,
                    )
            finally:
                connection.close()

    def test_legacy_mode_keeps_three_database_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            self.assertEqual(accounts.db_path.name, "accounts.sqlite3")
            self.assertEqual(game.db_path.name, "game.sqlite3")
            self.assertEqual(game.economy.db_path.name, "economy.sqlite3")
            self.assertTrue((root / "accounts" / "accounts.sqlite3").is_file())
            self.assertTrue((root / "game" / "game.sqlite3").is_file())
            self.assertTrue((root / "game" / "economy.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
