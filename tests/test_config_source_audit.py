from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.audit_config_sources import audit_config_sources


class ConfigSourceAuditTests(unittest.TestCase):
    def test_distinguishes_asset_only_from_extracted_tables(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / "inventory.json"
            inventory.write_text(json.dumps({
                "entries": [
                    {"path": "assets/Patch/assetbundle/data/lua/xlsx/actorinfo"},
                    {"path": "assets/Patch/assetbundle/data/lua/xlsx/actorskin"},
                    {"path": "assets/Patch/assetbundle/data/configproto/gameconstconfig"},
                ]
            }), encoding="utf-8")
            config = root / "config"
            config.mkdir()
            (config / "ActorInfo.json").write_text(json.dumps({
                "completeness": "partial", "rowCount": 1, "observedCount": 4,
                "rowsSha256": "abc", "rows": [{}],
            }), encoding="utf-8")
            report = audit_config_sources(inventory, config)
            rows = {row["normalizedName"]: row for row in report["tables"]}
            self.assertEqual(rows["actorinfo"]["status"], "asset_and_extracted")
            self.assertEqual(len(rows["actorinfo"]["extracted"]["fileSha256"]), 64)
            self.assertEqual(rows["actorskin"]["status"], "asset_only")
            self.assertFalse(rows["actorskin"]["decoded"])
            self.assertEqual(report["summary"]["assetOnly"], 1)
            self.assertEqual(report["summary"]["inventoryPathCount"], 3)
            self.assertEqual(report["summary"]["xlsxAssetPathCount"], 2)
            self.assertEqual(report["summary"]["xlsxDistinctTableNames"], 2)
            self.assertEqual(report["summary"]["nonXlsxConfigPathCount"], 1)

    def test_inventory_configproto_is_not_mistaken_for_a_table(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / "inventory.json"
            inventory.write_text(json.dumps({"entries": [
                {"path": "assets/Patch/assetbundle/data/configproto/gameconstconfig"},
            ]}), encoding="utf-8")
            config = root / "config"
            config.mkdir()
            report = audit_config_sources(inventory, config)
            self.assertEqual(report["summary"]["tables"], 0)


if __name__ == "__main__":
    unittest.main()
