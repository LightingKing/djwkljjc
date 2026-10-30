from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.audit_server_values import audit  # noqa: E402


class ServerValueAuditTests(unittest.TestCase):
    def test_inherits_provenance_and_marks_local_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "values.yaml").write_text(
                "provenance:\n"
                "  source: local fixture\n"
                "  confidence: D\n"
                "  generated: 2026-10-28\n"
                "resources:\n"
                "  gold:\n"
                "    initial: 100\n",
                encoding="utf-8",
            )
            result = audit(root)
            self.assertEqual(result["summary"]["entries"], 1)
            self.assertEqual(result["summary"]["localPolicy"], 1)
            entry = result["entries"][0]
            self.assertEqual(entry["source"], "local fixture")
            self.assertEqual(entry["confidence"], "D")
            self.assertEqual(entry["date"], "2026-10-28")

    def test_missing_metadata_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "values.yaml").write_text("resources:\n  gold: 100\n", encoding="utf-8")
            result = audit(root)
            self.assertEqual(result["summary"]["needsSource"], 1)
            self.assertEqual(result["files"][0]["status"], "needs-source")


if __name__ == "__main__":
    unittest.main()
