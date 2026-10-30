"""A new account name + password on the login screen registers and plays."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
from test_accounts import account_session_body  # noqa: E402


class RegisterOnLoginTests(unittest.TestCase):
    def test_client_login_sequence_registers_a_new_name_then_rejects_a_wrong_password(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            # Auth.lua: AccountSession -> POST /Account/ -> POST /Auth/ with the same credentials.
            status, session = accounts.handle("POST", "/AccountSession/", account_session_body("password"), "application/wireformat")
            self.assertEqual(status, 200)
            authz = "AccountToken " + session["accountToken"]
            credentials = json.dumps({"entryType": "password", "username": "newplayer9", "password": "pw-123456"})
            status, account = accounts.handle("POST", "/Account/", credentials, "application/json", authz)
            self.assertEqual(status, 200)
            status, auth = accounts.handle("POST", "/Auth/", credentials, "application/json", authz)
            self.assertEqual(status, 200)
            self.assertTrue(auth.get("tokenAccess"))
            # The new account plays: profile with name and starter character.
            status, base = game.handle_http("GET", "/fci/baseinfo/", None, "application/json", "Token " + auth["tokenAccess"])
            self.assertEqual(status, 200)
            self.assertEqual(base["name"], "newplayer9")
            # Same name, wrong password: refused instead of hijacking the account.
            status, _ = accounts.handle("POST", "/AccountSession/", account_session_body("password"), "application/wireformat")
            status2, session2 = accounts.handle("POST", "/AccountSession/", account_session_body("password"), "application/wireformat")
            wrong = json.dumps({"entryType": "password", "username": "newplayer9", "password": "nope-0000"})
            status, _ = accounts.handle("POST", "/Account/", wrong, "application/json", "AccountToken " + session2["accountToken"])
            self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
