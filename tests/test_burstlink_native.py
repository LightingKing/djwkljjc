from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from google.protobuf import message_factory

from accounts import LocalAccounts
from game import LocalGame


class NativeBurstLinkTests(unittest.TestCase):
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
        self.game = LocalGame(root / "game", self.accounts, clock=lambda: 1793332800, initial_level=30)
        self.account, self.auth = self.guest(self.accounts)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None, content_type="application/json"):
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def test_native_info_actor_selection_accepts_wire_request(self):
        status, info = self.call("GET", "/fci/BurstLinkPvpInfo/")
        self.assertEqual(status, 200)
        self.assertEqual(info["dungeonId"], 18001)
        status, actors = self.call("GET", "/fci/BurstLinkPVPRandActorInfo/")
        self.assertEqual(status, 200)
        self.assertTrue(actors["randActor"])
        uid = int(actors["randActor"][0]["uid"])
        request_type = self.game._pool.FindMessageTypeByName("ApiBurstLinkPVPRandActor")
        request = message_factory.GetMessageClass(request_type)(actor_uid=uid).SerializeToString()
        status, selected = self.call("POST", "/fci/BurstLinkPVPRandActorInfo/1/", request, "application/wireformat")
        self.assertEqual(status, 200)
        self.assertIn(uid, [int(value) for value in selected["curMainActor"]])

    def test_native_match_push_shape_and_idempotent_balance(self):
        self.assertEqual(self.call("POST", "/fci/BurstLinkPvpInfo", {})[0], 200)
        status, matching = self.call("POST", "/match/BurstLinkPVPMatch", b"{}")
        self.assertEqual((status, matching["maxWaitTime"]), (200, 1))
        profile = self.game._profile(self.account)
        state = self.game.realtime_pvp._state(profile)
        dungeon = state["burst"]["native"]["active"]["dungeon"]
        self.assertEqual(dungeon["dungeonType"], 6)
        status, result = self.call("POST", "/fci/BurstLinkDunBalance/", b'{"win":true}')
        self.assertEqual((status, result["isWin"]), (200, True))
        status, duplicate = self.call("POST", "/fci/BurstLinkDunBalance/", b'{"win":false}')
        self.assertEqual((status, duplicate["isWin"]), (200, True))


if __name__ == "__main__":
    unittest.main()
