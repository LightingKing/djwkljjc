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

    def test_race_rounds_are_deterministic_idempotent_and_persistent(self):
        status, info = self.call("GET", "/fci/racepvp/info")
        self.assertEqual(status, 200)
        self.assertEqual(info["tickets"], 6)
        status, created = self.call("POST", "/fci/racepvp/match", {})
        self.assertEqual(status, 200)
        match = created["match"]
        self.assertEqual((match["mode"], match["rounds"], len(match["participants"])), ("race", 8, 5))
        match_id = match["matchId"]

        status, duplicate_match = self.call("POST", "/fci/racepvp/match", {})
        self.assertEqual(status, 200)
        self.assertTrue(duplicate_match["retry"])
        self.assertEqual(duplicate_match["match"]["matchId"], match_id)

        status, out_of_order = self.call("POST", f"/fci/racepvp/match/{match_id}/round", {"round": 2, "action": "attack"})
        self.assertEqual((status, out_of_order["error"]), (409, "round_out_of_order"))
        for round_no in range(1, 9):
            status, result = self.call("POST", f"/fci/racepvp/match/{match_id}/round", {"round": round_no, "action": "skill"})
            self.assertEqual(status, 200)
            self.assertEqual(result["round"], round_no)
        status, repeat_round = self.call("POST", f"/fci/racepvp/match/{match_id}/round", {"round": 8, "action": "wait"})
        self.assertEqual(status, 200)
        self.assertTrue(repeat_round["duplicate"])

        status, settled = self.call("POST", f"/fci/racepvp/match/{match_id}/balance", {})
        self.assertEqual(status, 200)
        self.assertFalse(settled["duplicate"])
        status, repeat_settled = self.call("POST", f"/fci/racepvp/match/{match_id}/balance", {})
        self.assertEqual(status, 200)
        self.assertTrue(repeat_settled["duplicate"])
        status, after = self.call("GET", "/fci/racepvp/info")
        self.assertEqual(status, 200)
        self.assertEqual((after["tickets"], len(after["matches"])), (5, 1))

        restarted = LocalGame(Path(self.temp.name) / "game", self.accounts)
        status, persisted = restarted.handle_http("GET", "/fci/racepvp/info", authorization=self.auth)
        self.assertEqual(status, 200)
        self.assertEqual((persisted["tickets"], len(persisted["matches"])), (5, 1))

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
