from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
from http.client import HTTPConnection

from accounts import LocalAccounts
from server import FixtureServer, JsonlLogger
from server.core.activity_schedule import ActivitySchedule
from server.core.clock import ServerClock


class AdminSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        fixtures = self.root / "fixtures"
        fixtures.mkdir()
        state = self.root / "state"
        self.accounts = LocalAccounts(state)
        self.clock = ServerClock(source=lambda: 1_700_000_000)
        schedule_source = self.root / "schedule.yaml"
        schedule_source.write_text(
            "cycle:\n  periodDays: 2\n  anchor: '2023-11-14T00:00:00Z'\n  phases:\n    - id: local-shop\n      dayStart: 0\n      dayEnd: 2\n      activityType: 12\n",
            encoding="utf-8",
        )
        self.activity_schedule = ActivitySchedule(schedule_source, state_path=state / "activity-schedule.json", clock=self.clock.now)
        self.server = FixtureServer(
            ("127.0.0.1", 0), fixtures, JsonlLogger(self.root / "requests.jsonl"),
            accounts=self.accounts, clock=self.clock, state_dir=state,
            backup_dir=self.root / "backups",
            activity_schedule=self.activity_schedule,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method: str, path: str, payload: dict | None = None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, json.loads(data) if data else None

    def test_status_clock_and_backup_are_loopback_admin_operations(self):
        status, payload = self.request("GET", "/__local__/admin/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["clockOffsetSeconds"], 0)
        status, payload = self.request("POST", "/__local__/admin/clock", {"adjustSeconds": 3600})
        self.assertEqual(status, 200)
        self.assertEqual(payload["offsetSeconds"], 3600)
        status, payload = self.request("POST", "/__local__/admin/backup", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        status, payload = self.request("GET", "/__local__/admin/backups")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["backups"]), 1)
        backup_name = payload["backups"][0]
        with closing(self.accounts._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO accounts(account_id, entry, entry_type, open_id, created_at, expire_seconds) VALUES(9, 'temp', 'guest', 'open_temp', 1, 60)"
            )
        status, restore = self.request("POST", "/__local__/admin/restore", {"backup": backup_name})
        self.assertEqual(status, 200)
        self.assertIn("accounts.sqlite3", restore["restored"])
        with closing(self.accounts._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 0)

    def test_admin_password_reset_revokes_sessions_and_redacts_secrets(self):
        password_hash, salt = self.accounts._password_hash("old-password")
        with self.accounts._lock, closing(self.accounts._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO accounts(account_id, entry, entry_type, open_id,
                   created_at, expire_seconds, login_type, username,
                   password_hash, password_salt)
                   VALUES(1, 'account_admin', 'guest', 'open_admin', 1, 3600,
                   'password', 'admin', ?, ?)""",
                (password_hash, salt),
            )
        status, payload = self.request("GET", "/__local__/admin/accounts")
        self.assertEqual(status, 200)
        self.assertEqual(payload["accounts"][0]["username"], "admin")
        self.assertNotIn("passwordHash", payload["accounts"][0])
        status, payload = self.request(
            "POST", "/__local__/admin/accounts/1/reset-password", {"newPassword": "new-password"}
        )
        self.assertEqual(status, 200)
        with closing(self.accounts._connect()) as connection:
            row = connection.execute("SELECT password_hash, password_salt FROM accounts WHERE account_id=1").fetchone()
        self.assertTrue(self.accounts._verify_password("new-password", row[0], row[1]))

    def test_sandbox_billing_is_read_only_and_requires_game_economy(self):
        status, payload = self.request("GET", "/__local__/admin/payments")
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "economy_unavailable")
        status, payload = self.request("POST", "/__local__/admin/payments", {})
        self.assertEqual(status, 405)

    def test_crash_log_is_local_and_redacted(self):
        status, payload = self.request(
            "POST",
            "/__local__/crash-log",
            {"message": "client crash", "accessToken": "do-not-store"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        status, payload = self.request("GET", "/__local__/admin/crash-logs")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["reports"]), 1)
        self.assertNotIn("payload", payload["reports"][0])
        saved = next((self.root / "state" / "crash-logs").glob("*/*.json"))
        self.assertNotIn("do-not-store", saved.read_text(encoding="utf-8"))

    def test_activity_schedule_admin_crud(self):
        status, payload = self.request("GET", "/__local__/admin/activities")
        self.assertEqual(status, 200)
        self.assertEqual(payload["activities"][0]["id"], "local-shop")
        status, payload = self.request("POST", "/__local__/admin/activities", {"id": "local-event", "dayStart": 1, "dayEnd": 2, "activityType": 46})
        self.assertEqual(status, 200)
        status, payload = self.request("PATCH", "/__local__/admin/activities/local-event", {"enabled": False})
        self.assertEqual(status, 200)
        self.assertFalse(payload["activity"]["enabled"])
        status, payload = self.request("DELETE", "/__local__/admin/activities/local-event")
        self.assertEqual(status, 200)
        status, payload = self.request("POST", "/__local__/admin/activity-schedule", {"anchor": "2024-01-01T00:00:00Z"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["anchor"], "2024-01-01T00:00:00+00:00")

    def test_local_activity_web_dashboard_and_json(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request("GET", "/__local__/activity")
        response = connection.getresponse()
        html = response.read().decode("utf-8")
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertIn("本地活动控制台", html)
        self.assertIn("/__local__/activity.json", html)

        status, payload = self.request("GET", "/__local__/activity.json")
        self.assertEqual(status, 200)
        self.assertEqual(payload["active"][0]["id"], "local-shop")
        self.assertEqual(payload["cycleDay"], 0)


if __name__ == "__main__":
    unittest.main()
