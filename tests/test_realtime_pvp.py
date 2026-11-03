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
        self.now = [1793332800]
        self.game = LocalGame(root / "game", self.accounts, clock=lambda: self.now[0], initial_level=28)
        self.account, self.auth = self.guest(self.accounts)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method: str, path: str, payload: dict | None = None):
        if isinstance(payload, (bytes, bytearray, str)):
            body = payload
        else:
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
        self.assertEqual((info["mode"], info["tickets"], info["maxTickets"], info["dungeonId"]), ("burst", 2, 10, 4001))
        status, joined = self.call("POST", "/fci/BurstLinkPvpInfo/", {})
        self.assertEqual(status, 200)
        self.assertEqual(joined["resChange"][0]["baseRes"]["id"], 80003)
        status, created = self.call("POST", "/match/BurstLinkPVPMatch", b"")
        self.assertEqual(status, 200)
        self.assertTrue(created["localFight"] and created["dungeonInfo"]["dungeonType"] == 8)
        uid = created["dungeonInfo"]["dungeonUid"]
        active = self.game._types["FightActiveData"](dungeonType=8, stageID=4001, gameRound=5, winTeam=2)
        status, _ = self.call("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString())
        self.assertEqual(status, 200)
        status, settled = self.call("POST", "/fci/BurstLinkDunBalance/", b"{}")
        self.assertEqual((status, settled["isWin"]), (200, False))

    def test_weekly_burst_tickets_are_two_with_a_ten_ticket_cap(self):
        status, info = self.call("GET", "/fci/BurstLinkPvpInfo/")
        self.assertEqual(status, 200)
        self.assertEqual((info["tickets"], info["maxTickets"]), (2, 10))
        self.now = [1793931600]  # 2026-11-06 02:20 UTC, Friday 10:20 Beijing (next Beijing week)
        status, next_info = self.call("GET", "/fci/BurstLinkPvpInfo/")
        self.assertEqual(status, 200)
        self.assertEqual(next_info["tickets"], 4)

    def test_race_closed_period_returns_activity_notify_instead_of_keyerror(self):
        self.now = [1793584800]  # 2026-11-02 02:00 UTC, Monday 10:00 Beijing (open)
        self.assertEqual(self.call("GET", "/fci/RacePVPInfo")[0], 200)
        self.now = [1793656800]  # 2026-11-02 22:00 UTC, Tuesday 06:00 Beijing (still open)
        self.now = [1793822400]  # 2026-11-04 20:00 UTC, Thursday 04:00 Beijing (closed gap)
        status, info = self.call("GET", "/fci/RacePVPInfo")
        self.assertEqual(status, 200)
        self.assertEqual(info["state"], 1)


if __name__ == "__main__":
    unittest.main()
