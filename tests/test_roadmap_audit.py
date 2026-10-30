from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.audit_roadmap import build_report


class RoadmapAuditTests(unittest.TestCase):
    def test_report_keeps_client_blockers_and_verified_artifacts_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path in (
                "docs/设备报告.md",
                "docs/工具链.md",
                "tools/verify-toolchain.ps1",
                "client-patch/build.py",
                "tests",
            ):
                target = root / path
                if "." not in target.name:
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("x", encoding="utf-8")
            report = build_report(root)
            by_code = {item["code"]: item for item in report["items"]}
            self.assertEqual(by_code["M0.3"]["status"], "verified")
            self.assertEqual(by_code["M1.3"]["status"], "blocked")
            self.assertEqual(by_code["M8.2"]["status"], "partial")
            self.assertGreaterEqual(report["counts"]["blocked"], 1)


if __name__ == "__main__":
    unittest.main()
