from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from accounts import LocalAccounts
from game import LocalGame


class LocalRealtimePvpTests(unittest.TestCase):
    @staticmethod
    def guest(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        auth = accounts.handle(
            "POST", "/Auth/", b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest",
            "application/wireformat", "AccountToken " + second[1]["accountToken"],
        )
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

    def test_race_uses_native_room_and_rejects_retired_simulation(self):
        status, info = self.call("GET", "/fci/RacePVPInfo")
        self.assertEqual(status, 200)
        self.assertEqual(len(info["players"]), 20)
        self.assertIn(info["dungeonId"], (3001, 3002, 3003, 3004))
        self.assertEqual(info["state"], 2)
        status, retired = self.call("POST", "/fci/racepvp/match", {})
        self.assertEqual((status, retired["error"]), (410, "race_requires_client_dungeon_flow"))
        # The complete protobuf create/active/balance/DELETE flow, resource
        # transactions, and week boundaries are covered in test_race_client.

    def test_burstlink_bootstrap_and_auto_balance(self):
        status, info = self.call("GET", "/fci/BurstLinkPvpInfo/")
        self.assertEqual(status, 200)
        self.assertEqual((info["mode"], info["tickets"]), ("burst", 1))
        status, created = self.call("POST", "/fci/burstlink/join", {})
        self.assertEqual(status, 200)
        match = created["match"]
        self.assertEqual((match["mode"], match["rounds"], len(match["participants"])), ("burst", 5, 3))
        status, settled = self.call("POST", f"/fci/burstlink/match/{match['matchId']}/balance", {})
        self.assertEqual(status, 200)
        self.assertIn("reward", settled["settlement"])
        status, exhausted = self.call("POST", "/fci/burstlink/join", {})
        self.assertEqual((status, exhausted["error"]), (409, "ticket_exhausted"))


if __name__ == "__main__":
    unittest.main()
