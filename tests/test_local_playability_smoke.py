from __future__ import annotations

import unittest

from dev.local_playability_smoke import run_smoke


class LocalPlayabilitySmokeTests(unittest.TestCase):
    def test_m4_m6_local_compatibility_subset(self):
        report = run_smoke()
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["checkCount"], 9)
        self.assertEqual(report["passedChecks"], 9)
        self.assertEqual(report["scope"], "local_m4_m6_compatibility_subset")


if __name__ == "__main__":
    unittest.main()
