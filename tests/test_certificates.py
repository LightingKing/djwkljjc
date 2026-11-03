from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from server.core.certificates import inspect_certificate_expiry


class CertificateExpiryTests(unittest.TestCase):
    def test_existing_server_certificate_reports_expiry(self) -> None:
        path = Path(__file__).resolve().parents[3] / "analysis" / "ljjc-local" / "certs" / "server.pem"
        if not path.is_file():
            self.skipTest(f"certificate fixture not available: {path}")
        result = inspect_certificate_expiry(path, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["notAfter"], "2027-01-25T05:22:20+00:00")
        self.assertFalse(result["warning"])

    def test_expiry_warning_is_reported_before_thirty_days(self) -> None:
        path = Path(__file__).resolve().parents[3] / "analysis" / "ljjc-local" / "certs" / "server.pem"
        if not path.is_file():
            self.skipTest(f"certificate fixture not available: {path}")
        result = inspect_certificate_expiry(path, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["warning"])
        self.assertLess(result["daysRemaining"], 30)

    def test_malformed_certificate_is_an_error_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.pem"
            path.write_text("not a certificate", encoding="ascii")
            result = inspect_certificate_expiry(path, now=datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "SSLError")


if __name__ == "__main__":
    unittest.main()
