from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.verify_config_extraction import verify


class ConfigExtractionVerifierTests(unittest.TestCase):
    def test_complete_equal_tables_are_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            expected = root / "expected"; actual = root / "actual"
            expected.mkdir(); actual.mkdir()
            payload = {"table": "Example", "completeness": "complete_observed", "rowCount": 1, "observedCount": 1, "schema": {"types": ["object"]}, "rows": [{"id": 1}]}
            import json
            for directory in (expected, actual):
                (directory / "Example.json").write_text(json.dumps(payload), encoding="utf-8")
            result = verify(expected, actual)
            self.assertTrue(result["verified"])
            self.assertEqual(result["counts"]["match"], 1)

    def test_partial_table_is_inconclusive_and_never_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); expected = root / "expected"; actual = root / "actual"
            expected.mkdir(); actual.mkdir()
            import json
            for directory, count in ((expected, 10), (actual, 2)):
                (directory / "Example.json").write_text(json.dumps({"table": "Example", "completeness": "partial", "rowCount": 2, "observedCount": count, "schema": {}, "rows": []}), encoding="utf-8")
            result = verify(expected, actual)
            self.assertFalse(result["verified"])
            self.assertEqual(result["counts"]["inconclusive"], 1)


if __name__ == "__main__":
    unittest.main()
