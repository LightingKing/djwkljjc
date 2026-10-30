from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_lua_decompilation import (
    ASSET_MAGIC,
    RUNTIME_MAGIC,
    audit_tree,
    classify_prefix,
    load_inventory,
)


class LuaDecompilationAuditTests(unittest.TestCase):
    def test_classification_is_conservative(self):
        self.assertEqual(classify_prefix(RUNTIME_MAGIC + b"x"), "runtime_fci")
        self.assertEqual(classify_prefix(ASSET_MAGIC + b"x"), "protected_asset_fci")
        self.assertEqual(classify_prefix(b"lua\x1b"), "unknown")

    def test_protected_payload_is_reported_without_decompiling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input"
            output = root / "output"
            source.mkdir()
            (source / "module.txt").write_bytes(ASSET_MAGIC + b"encrypted")
            report = audit_tree(source, output)
            self.assertEqual(report["candidateCount"], 1)
            self.assertEqual(report["counts"], {"blocked_protected_payload": 1})
            self.assertFalse(output.exists() and any(output.rglob("*.lua")))

    def test_inventory_filters_lua_paths_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            inventory = Path(temp) / "inventory.json"
            inventory.write_text(json.dumps({"entries": [
                {"path": "assets/Patch/assetbundle/data/lua/common/a"},
                {"path": "assets/Patch/assetbundle/data/lua/common/a"},
                {"path": "assets/Patch/assetbundle/window/a"},
            ]}), encoding="utf-8")
            self.assertEqual(load_inventory(inventory), [
                "assets/Patch/assetbundle/data/lua/common/a"
            ])


if __name__ == "__main__":
    unittest.main()
