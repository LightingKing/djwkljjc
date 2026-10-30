from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

from server.core.crash_logs import CrashLogStore


class CrashLogStoreTests(unittest.TestCase):
    def test_redacts_credentials_and_lists_metadata(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = CrashLogStore(root, clock=lambda: datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp())
            report = store.write(
                json.dumps({"message": "boom", "stack": "line1", "accessToken": "secret", "nested": {"password": "pw"}}).encode(),
                content_type="application/json",
                path="/fci/crash-log",
                account_id=7,
            )
            self.assertEqual(report["accountId"], 7)
            self.assertEqual(store.count(), 1)
            listed = store.list_recent()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["sha256"], report["sha256"])
            path = next(root.glob("*/*.json"))
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["payload"]["accessToken"], "<redacted>")
            self.assertEqual(saved["payload"]["nested"]["password"], "<redacted>")
            self.assertNotIn("secret", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
