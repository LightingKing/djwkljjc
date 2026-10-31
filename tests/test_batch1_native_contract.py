"""Regressions at the actual HTTP/raw-JSON boundary used by the Lua client."""
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

from accounts import LocalAccounts
from game import LocalGame
from grants import grant_rewards
from server import FixtureHandler, FixtureServer, JsonlLogger
import test_systems
from test_accounts import FakeClock, account_session_body, auth_body


class NativeHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / "state")
        self.game = LocalGame(self.root / "game", self.accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.server = FixtureServer(("127.0.0.1", 0), self.root, JsonlLogger(self.root / "requests.jsonl"))
        self.server.accounts, self.server.game = self.accounts, self.game
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, body=None, content_type="application/json"):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body, {"Authorization": self.auth, "Content-Type": content_type})
        response = connection.getresponse()
        raw = response.read()
        payload = FixtureHandler._wire_json_payload(raw) if response.status == 200 else json.loads(raw)
        connection.close()
        return response.status, payload

    def test_item_bindings_follow_requested_order_duplicates_and_zero_stacks(self):
        grant_rewards(self.game, self.profile, [{"type": 11, "id": 80002, "count": 2}, {"type": 11, "id": 80006, "count": 3}])
        for query in ("uids=80006,80002,80006", "uids=80006&uids=80002%2C80006"):
            status, rows = self.request("GET", "/fci/item/?perPage=3&" + query)
            self.assertEqual(status, 200)
            self.assertEqual([row["id"] for row in rows], [80006, 80002, 80006])
            self.assertEqual([row["count"] for row in rows], [3, 2, 3])
        status, missing = self.request("GET", "/fci/item/80009")
        self.assertEqual(status, 200)
        self.assertEqual(missing["count"], 0)
        status, invalid = self.request("GET", "/fci/item/?uids=80002,bad")
        self.assertEqual(status, 509)
        self.assertIn("code", invalid["notify"])
        self.assertNotIn("error", invalid)

    def test_expected_rejection_and_internal_failure_have_native_notify_shape(self):
        status, error = self.request("POST", "/fci/item/use", b'{"itemId":80009,"count":1}')
        self.assertEqual((status, error), (509, {"notify": {"code": "Window_ItemNotEnough"}}))
        with patch.object(self.game, "handle_http", side_effect=RuntimeError("private failure detail")):
            status, error = self.request("GET", "/fci/baseinfo/")
        self.assertEqual((status, error), (507, {"notify": {"code": "Error_DbError"}}))

    def test_committed_action_survives_followup_cultivation_failure(self):
        with patch.object(self.game.cultivation, "sync", side_effect=RuntimeError("derived refresh")):
            with self.assertLogs("game", level="ERROR"):
                status, _ = self.request("POST", "/fci/item", b'{"itemId":80009,"count":1}')
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/fci/item/80009")[1]["count"], 1)


class CachedPasswordAuthTests(unittest.TestCase):
    def test_guest_device_then_password_login_can_resume_after_cold_start(self):
        with tempfile.TemporaryDirectory() as directory:
            accounts = LocalAccounts(directory)
            def session(device="same-emulator"):
                return "AccountToken " + accounts.handle("POST", "/AccountSession/", account_session_body("", device), "application/wireformat")[1]["accountToken"]
            guest_session = session()
            guest = accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", guest_session)[1]["entry"]["entry"]
            self.assertEqual(accounts.handle("POST", "/Auth/", auth_body(guest), "application/wireformat", guest_session)[0], 200)
            credentials = {"entryType": "password", "username": "cached_switch", "password": "long-password-over-twelve"}
            accounts.handle("POST", "/Account/", json.dumps(credentials), "application/json", session(None))
            first = accounts.handle("POST", "/Auth/", json.dumps(credentials), "application/json", session())[1]
            cached = {"entryType": "password", "username": "cached_switch", "accessToken": first["tokenAccess"]}
            status, result = accounts.handle("POST", "/Auth/", json.dumps(cached), "application/json", session())
            self.assertEqual(status, 200, result)
            self.assertEqual(result["accountId"], first["accountId"])
            # A session explicitly authenticated to a different account without
            # device seeding cannot use the cached-login shortcut to change it.
            other = session(None)
            accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", other)
            cached["accessToken"] = result["tokenAccess"]
            self.assertEqual(accounts.handle("POST", "/Auth/", json.dumps(cached), "application/json", other)[0], 403)

    def test_cached_token_refresh_is_bound_to_identity_and_expires(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as directory:
            accounts = LocalAccounts(directory, clock=clock)
            def session():
                return "AccountToken " + accounts.handle("POST", "/AccountSession/", account_session_body("password"), "application/wireformat")[1]["accountToken"]
            def auth(data, token):
                return accounts.handle("POST", "/Auth/", json.dumps(data), "application/json", token)
            credentials = {"entryType": "password", "username": "cached_user", "password": "long-password-over-twelve"}
            account_token = session()
            self.assertEqual(accounts.handle("POST", "/Account/", json.dumps(credentials), "application/json", account_token)[0], 200)
            status, first = auth(credentials, account_token)
            self.assertEqual(status, 200)
            self.assertEqual(first["expireSeconds"], 30 * 86400)
            clock.value += 2 * 86400
            cached = {"entryType": "password", "username": "cached_user", "accessToken": first["tokenAccess"]}
            self.assertEqual(auth(dict(cached, username="someone_else"), session())[0], 401)
            status, refreshed = auth(cached, session())
            self.assertEqual(status, 200)
            self.assertEqual(refreshed["accountId"], first["accountId"])
            self.assertIsNone(accounts.verify_access_token(first["tokenAccess"]))
            self.assertEqual(auth(cached, session())[0], 401)
            clock.value += 30 * 86400
            cached["accessToken"] = refreshed["tokenAccess"]
            self.assertEqual(auth(cached, session())[0], 401)
