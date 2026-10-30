import sys
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from accounts import LocalAccounts  # noqa: E402


def wire_string(number, value):
    raw = value.encode("utf-8")
    result = bytearray()
    tag = number << 3 | 2
    while tag >= 0x80:
        result.append((tag & 0x7F) | 0x80)
        tag >>= 7
    result.append(tag)
    length = len(raw)
    while length >= 0x80:
        result.append((length & 0x7F) | 0x80)
        length >>= 7
    result.append(length)
    result.extend(raw)
    return bytes(result)


def wire_varint(number, value):
    tag = number << 3
    result = bytearray()
    while tag >= 0x80:
        result.append((tag & 0x7F) | 0x80)
        tag >>= 7
    result.append(tag)
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def account_session_body(entry_type="guest", device_id=None):
    # ApiAccountSession.entry_type is descriptor field 3.  Empty is also
    # accepted by the compatibility module because the original client can
    # omit protobuf defaults before the guest prompt is selected.
    result = wire_string(3, entry_type) if entry_type else b""
    if device_id is not None:
        result += wire_string(5, device_id)
    return result


def auth_body(entry, entry_type="guest", social_token=None):
    result = wire_string(1, entry) + wire_string(2, entry_type)
    if social_token is not None:
        result += wire_string(5, social_token)
    return result


class FakeClock:
    def __init__(self, value=1_700_000_000):
        self.value = value

    def __call__(self):
        return self.value


class LocalAccountsTests(unittest.TestCase):
    def test_password_registration_login_reset_and_existence(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state, clock=clock)
            status, session = accounts.handle(
                "POST", "/AccountSession/", account_session_body("password"), "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz = "AccountToken " + session["accountToken"]
            body = json.dumps({"entryType": "password", "username": "Alice_01", "password": "secret-1"})
            status, entry = accounts.handle("POST", "/EntryReg/", body, "application/json", authz)
            self.assertEqual(status, 200)
            self.assertEqual(entry["entry"]["entryType"], "password")
            status, account = accounts.handle("POST", "/Account/", body, "application/json", authz)
            self.assertEqual(status, 200)
            self.assertEqual(account["entry"]["entry"], entry["entry"]["entry"])

            status, exists = accounts.handle(
                "POST", "/ExistEntry/", json.dumps({"username": "alice_01"}), "application/json"
            )
            self.assertEqual(status, 200)
            self.assertTrue(exists["exists"])
            status, missing = accounts.handle(
                "POST", "/ExistEntry/", json.dumps({"username": "missing_user"}), "application/json"
            )
            self.assertEqual(status, 200)
            self.assertFalse(missing["exists"])
            status, alias_exists = accounts.handle(
                "POST", "/exist-entry/", json.dumps({"username": "alice_01"}), "application/json"
            )
            self.assertEqual(status, 200)
            self.assertTrue(alias_exists["exists"])

            status, auth = accounts.handle(
                "POST", "/Password/", json.dumps({"username": "ALICE_01", "password": "secret-1"}), "application/json", authz
            )
            self.assertEqual(status, 200)
            self.assertTrue(auth["tokenAccess"].startswith("local_access_"))
            self.assertEqual(accounts.verify_access_token(auth["tokenAccess"])["entryType"], "password")

            status, changed = accounts.handle(
                "POST", "/PasswordReset/",
                json.dumps({"oldPassword": "secret-1", "newPassword": "secret-2"}),
                "application/json", authz,
            )
            self.assertEqual(status, 200)
            self.assertTrue(changed["ok"])
            status, changed_alias = accounts.handle(
                "POST", "/Password/reset/",
                json.dumps({"oldPassword": "secret-2", "newPassword": "secret-3"}),
                "application/json", authz,
            )
            self.assertEqual(status, 200)
            self.assertTrue(changed_alias["ok"])
            status, _ = accounts.handle(
                "POST", "/Password/", json.dumps({"username": "alice_01", "password": "secret-1"}), "application/json"
            )
            self.assertEqual(status, 401)
            status, _ = accounts.handle(
                "POST", "/Password/", json.dumps({"username": "alice_01", "password": "secret-3"}), "application/json"
            )
            self.assertEqual(status, 200)
            with open(Path(state, "accounts.sqlite3"), "rb") as stream:
                raw_db = stream.read()
            self.assertNotIn(b"secret-2", raw_db)

    def test_guest_can_bind_sandbox_password_without_changing_guest_entry(self):
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state)
            status, session = accounts.handle("POST", "/AccountSession/", account_session_body(), "application/wireformat")
            self.assertEqual(status, 200)
            authz = "AccountToken " + session["accountToken"]
            status, guest = accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz)
            self.assertEqual(status, 200)
            guest_entry = guest["entry"]["entry"]
            status, bound = accounts.handle(
                "POST", "/AccountGuestBind/",
                json.dumps({"username": "bound_user", "password": "bound-pass"}),
                "application/json", authz,
            )
            self.assertEqual(status, 200)
            self.assertEqual(bound["entry"]["entry"], guest_entry)
            status, alias_bound = accounts.handle(
                "POST", "/AccountEntry/bind/",
                json.dumps({"username": "bound_user", "password": "bound-pass"}),
                "application/json", authz,
            )
            self.assertEqual(status, 200)
            self.assertEqual(alias_bound["entry"]["entry"], guest_entry)
            status, login = accounts.handle(
                "POST", "/Password/", json.dumps({"username": "bound_user", "password": "bound-pass"}), "application/json"
            )
            self.assertEqual(status, 200)
            self.assertEqual(login["entry"], guest_entry)
    def test_guest_registration_and_auth(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state, clock=clock)
            status, session_result = accounts.handle(
                "POST",
                "/AccountSession/",
                account_session_body(),
                "application/wireformat",
            )
            self.assertEqual(status, 200)
            session = session_result["accountToken"]
            authz = "AccountToken " + session

            status, _ = accounts.handle(
                "POST",
                "/EntryReg/",
                b'{"entryType":"guest"}',
                "application/json",
                authz,
            )
            self.assertEqual(status, 200)
            status, account_result = accounts.handle(
                "POST",
                "/Account/",
                b'{"entryType":"guest"}',
                "application/json; charset=utf-8",
                authz,
            )
            self.assertEqual(status, 200)
            entry = account_result["entry"]["entry"]
            self.assertEqual(account_result["entry"]["entryType"], "guest")

            status, result = accounts.handle(
                "POST",
                "/Auth/",
                auth_body(entry),
                "application/wireformat",
                authz,
            )
            self.assertEqual(status, 200)
            self.assertIsInstance(result["accountId"], int)
            self.assertGreater(result["accountId"], 0)
            self.assertLess(result["accountId"], 2**31)
            self.assertTrue(result["openId"].startswith("local_guest_"))
            self.assertTrue(result["tokenAccess"].startswith("local_access_"))
            self.assertEqual(result["createTime"], clock.value)
            self.assertGreater(result["expireSeconds"], 0)
            self.assertIsNotNone(accounts.verify_access_token(result["tokenAccess"]))

    def test_bad_or_expired_session_rejected(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state, session_ttl_seconds=5, clock=clock)
            status, _ = accounts.handle(
                "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", "AccountToken bad"
            )
            self.assertEqual(status, 401)
            status, session_result = accounts.handle(
                "POST", "/AccountSession/", account_session_body(), "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz = "AccountToken " + session_result["accountToken"]
            clock.value += 5
            status, result = accounts.handle(
                "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", authz
            )
            self.assertEqual(status, 401)
            self.assertEqual(result["error"], "expired_account_token")

    def test_social_login_is_rejected(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state, clock=clock)
            status, _ = accounts.handle(
                "POST", "/AccountSession/", account_session_body("qq"), "application/wireformat"
            )
            self.assertEqual(status, 403)
            status, session_result = accounts.handle(
                "POST", "/AccountSession/", account_session_body(), "application/wireformat"
            )
            authz = "AccountToken " + session_result["accountToken"]
            status, _ = accounts.handle(
                "POST",
                "/EntryReg/",
                b'{"entryType":"guest","socialToken":"third-party"}',
                "application/json",
                authz,
            )
            self.assertEqual(status, 403)
            status, _ = accounts.handle(
                "POST",
                "/Account/",
                b'{"entryType":"guest"}',
                "application/json",
                authz,
            )
            self.assertEqual(status, 200)
            # A social ApiAuthInfo field is rejected before account lookup.
            status, _ = accounts.handle(
                "POST", "/Auth/", auth_body("not-used", social_token="third-party"),
                "application/wireformat", authz
            )
            self.assertEqual(status, 403)

    def test_persistence_after_reopen(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as state:
            first = LocalAccounts(state, clock=clock)
            status, session_result = first.handle(
                "POST", "/AccountSession/", account_session_body(), "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz = "AccountToken " + session_result["accountToken"]
            status, account_result = first.handle(
                "POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz
            )
            self.assertEqual(status, 200)
            entry = account_result["entry"]["entry"]
            status, old_auth = first.handle(
                "POST", "/Auth/", auth_body(entry), "application/wireformat", authz
            )
            self.assertEqual(status, 200)

            second = LocalAccounts(state, clock=clock)
            status, again = second.handle(
                "POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz
            )
            self.assertEqual(status, 200)
            self.assertEqual(again["entry"]["entry"], entry)
            status, new_auth = second.handle(
                "POST", "/Auth/", auth_body(entry), "application/wireformat", authz
            )
            self.assertEqual(status, 200)
            self.assertEqual(new_auth["accountId"], old_auth["accountId"])
            self.assertNotEqual(new_auth["tokenAccess"], old_auth["tokenAccess"])

    def test_guest_auth_uses_fresh_session_and_allows_relogin(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state, clock=clock)
            status, session_a = accounts.handle(
                "POST", "/AccountSession/", account_session_body(), "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz_a = "AccountToken " + session_a["accountToken"]
            status, account_result = accounts.handle(
                "POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz_a
            )
            self.assertEqual(status, 200)
            entry = account_result["entry"]["entry"]

            # This mirrors Login.lua: RequestAccountAuth first refreshes the
            # AccountSession, so Auth must not require session A.
            clock.value += 1
            status, session_b = accounts.handle(
                "POST", "/AccountSession/", account_session_body(), "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz_b = "AccountToken " + session_b["accountToken"]
            status, first_auth = accounts.handle(
                "POST", "/Auth/", auth_body(entry), "application/wireformat", authz_b
            )
            self.assertEqual(status, 200)
            self.assertEqual(first_auth["createTime"], clock.value)

            # A later fresh session can log in with the registered local guest
            # entry, but a made-up entry remains rejected.
            clock.value += 1
            status, session_c = accounts.handle(
                "POST", "/AccountSession/", account_session_body(), "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz_c = "AccountToken " + session_c["accountToken"]
            status, second_auth = accounts.handle(
                "POST", "/Auth/", auth_body(entry), "application/wireformat", authz_c
            )
            self.assertEqual(status, 200)
            self.assertEqual(second_auth["accountId"], first_auth["accountId"])
            self.assertEqual(second_auth["createTime"], clock.value)
            self.assertNotEqual(second_auth["tokenAccess"], first_auth["tokenAccess"])
            status, _ = accounts.handle(
                "POST", "/Auth/", auth_body("guest_" + "f" * 32),
                "application/wireformat", authz_c
            )
            self.assertEqual(status, 404)

    def test_same_device_reuses_guest_across_two_complete_restarts(self):
        clock = FakeClock()
        device_id = "local-avd-device-123"
        with tempfile.TemporaryDirectory() as state:
            first = LocalAccounts(state, clock=clock)

            # Restart 1: GuestSignup uses session A for EntryReg/Account, then
            # Login.lua refreshes it to session B for Auth.
            status, session_a = first.handle(
                "POST", "/AccountSession/", account_session_body(device_id=device_id),
                "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz_a = "AccountToken " + session_a["accountToken"]
            status, entry_result = first.handle(
                "POST", "/EntryReg/", b'{"entryType":"guest"}',
                "application/json", authz_a
            )
            self.assertEqual(status, 200)
            status, account_result = first.handle(
                "POST", "/Account/", b'{"entryType":"guest"}',
                "application/json", authz_a
            )
            self.assertEqual(status, 200)
            entry = account_result["entry"]["entry"]
            self.assertEqual(entry_result["entry"]["entry"], entry)

            status, session_b = first.handle(
                "POST", "/AccountSession/", account_session_body(device_id=device_id),
                "application/wireformat"
            )
            self.assertEqual(status, 200)
            status, first_auth = first.handle(
                "POST", "/Auth/", auth_body(entry), "application/wireformat",
                "AccountToken " + session_b["accountToken"]
            )
            self.assertEqual(status, 200)

            # Restart 2: a new LocalAccounts instance sees only the hashed
            # device binding.  The client may repeat EntryReg/Account, but it
            # receives the original entry and account id.
            second = LocalAccounts(state, clock=clock)
            status, session_c = second.handle(
                "POST", "/AccountSession/", account_session_body(device_id=device_id),
                "application/wireformat"
            )
            self.assertEqual(status, 200)
            authz_c = "AccountToken " + session_c["accountToken"]
            status, entry_again = second.handle(
                "POST", "/EntryReg/", b'{"entryType":"guest"}',
                "application/json", authz_c
            )
            self.assertEqual(status, 200)
            status, account_again = second.handle(
                "POST", "/Account/", b'{"entryType":"guest"}',
                "application/json", authz_c
            )
            self.assertEqual(status, 200)
            self.assertEqual(entry_again["entry"]["entry"], entry)
            self.assertEqual(account_again["entry"]["entry"], entry)

            status, session_d = second.handle(
                "POST", "/AccountSession/", account_session_body(device_id=device_id),
                "application/wireformat"
            )
            self.assertEqual(status, 200)
            status, second_auth = second.handle(
                "POST", "/Auth/", auth_body(entry), "application/wireformat",
                "AccountToken " + session_d["accountToken"]
            )
            self.assertEqual(status, 200)
            self.assertEqual(second_auth["accountId"], first_auth["accountId"])
            self.assertEqual(second_auth["openId"], first_auth["openId"])
            self.assertNotEqual(second_auth["tokenAccess"], first_auth["tokenAccess"])

            # The persistent state contains a one-way binding key, never the
            # original device identifier.
            raw_db = Path(state, "accounts.sqlite3").read_bytes()
            self.assertNotIn(device_id.encode("utf-8"), raw_db)
            db = sqlite3.connect(Path(state, "accounts.sqlite3"))
            try:
                row = db.execute("SELECT device_hash FROM device_bindings").fetchone()
            finally:
                db.close()
            self.assertIsNotNone(row)
            self.assertEqual(len(row[0]), 64)

    def test_unknown_route_is_left_for_fixture_router(self):
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state)
            self.assertIsNone(accounts.handle("POST", "/app_version/", b"", "application/json"))

    def test_local_account_compatibility_aliases_and_verification_seams(self):
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state)
            status, session = accounts.handle("POST", "/AccountSession/", account_session_body("password"), "application/wireformat")
            self.assertEqual(status, 200)
            account_token = "AccountToken " + session["accountToken"]
            body = json.dumps({"entryType": "password", "username": "compat_user", "password": "compat-pass"})
            status, created = accounts.handle("POST", "/NewEntry/", body, "application/json", account_token)
            self.assertEqual(status, 200)
            self.assertEqual(created["entry"]["entryType"], "password")
            entry = created["entry"]["entry"]

            status, detail = accounts.handle("GET", f"/AccountEntry/{entry}/", authorization=account_token)
            self.assertEqual(status, 200)
            self.assertEqual(detail["username"], "compat_user")
            status, exists = accounts.handle("GET", f"/AccountEntry/{entry}/exist/", authorization=account_token)
            self.assertEqual((status, exists["exists"]), (200, True))

            status, captcha = accounts.handle("POST", "/Captcha/", b'{"code":"LOCAL"}', "application/json")
            self.assertEqual(status, 200)
            self.assertTrue(captcha["verifyCodePassed"])

            # Password login returns an access token that ExtraInfoByAuth can
            # inspect without exposing password material.
            status, login = accounts.handle("POST", "/Password/", body, "application/json")
            self.assertEqual(status, 200)
            status, extra = accounts.handle("GET", "/ExtraInfoByAuth/", authorization="Token " + login["tokenAccess"])
            self.assertEqual(status, 200)
            self.assertEqual(extra["account"]["username"], "compat_user")


if __name__ == "__main__":
    unittest.main()
