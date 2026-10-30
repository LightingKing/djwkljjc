from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
import sys
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.extract_runtime_configs import extract, infer_schema


class RuntimeConfigExtractionTests(unittest.TestCase):
    def test_inferred_schema_is_recursive_and_deterministic(self):
        schema = infer_schema([
            {"id": 1, "tags": ["a"], "nested": {"enabled": True}},
            {"id": 2, "tags": [], "nested": {"enabled": False}},
        ])
        self.assertEqual(schema["types"], ["object"])
        self.assertEqual(schema["fields"]["id"]["types"], ["integer"])
        self.assertEqual(schema["fields"]["tags"]["items"]["types"], ["string"])
        self.assertEqual(schema["fields"]["nested"]["fields"]["enabled"]["types"], ["boolean"])
        self.assertEqual(list(schema["fields"]), ["id", "nested", "tags"])

    def test_partial_and_error_tables_are_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "runtime.jsonl"
            source.write_text(
                '{"payload":{"values":["{\\"Partial\\":{\\"count\\":3,\\"rows\\":[{\\"id\\":1}]},\\"Broken\\":{\\"count\\":0,\\"rows\\":[],\\"error\\":\\"nil\\"}}"]}}\n',
                encoding="utf-8",
            )
            index, tables = extract([source])
            self.assertEqual(index["tableCount"], 2)
            self.assertEqual(tables["Partial"]["completeness"], "partial")
            self.assertEqual(tables["Broken"]["completeness"], "error")

    def test_plain_json_array_and_jsonl_root_are_kept_with_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            array_source = root / "array.json"
            array_source.write_text(
                '[{"TableA":{"count":1,"rows":[{"id":1}]}}, {"TableB":{"rows":[{"id":2}]}}]',
                encoding="utf-8",
            )
            root_source = root / "root.jsonl"
            root_source.write_text('{"table":"RootTable","rows":[{"id":3}]}\n', encoding="utf-8")

            index, tables = extract([array_source, root_source])
            self.assertEqual(index["tableCount"], 3)
            self.assertEqual(set(tables), {"TableA", "TableB", "RootTable"})
            digest = hashlib.sha256(array_source.read_bytes()).hexdigest()
            self.assertEqual(index["sourceRecords"][0]["sha256"], digest)
            self.assertEqual(tables["TableA"]["rowsSha256"], hashlib.sha256(b'[{"id":1}]').hexdigest())
            self.assertEqual(tables["RootTable"]["sourceRecords"][0]["name"], "root.jsonl")


if __name__ == "__main__":
    unittest.main()
