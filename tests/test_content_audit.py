from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.audit_content_coverage import audit_content


class ContentAuditTests(unittest.TestCase):
    def test_reports_resource_evidence_without_promoting_partial_config(self):
        with tempfile.TemporaryDirectory() as temp:
            config_dir = Path(temp)
            (config_dir / "ActorConfig.json").write_text(
                '{"completeness":"partial","rows":[{"id":1001,"prefabRes":"mikoto"}]}',
                encoding="utf-8",
            )
            (config_dir / "ChapterInfo.json").write_text(
                '{"completeness":"complete","rows":[{"chapterIndex":1}]}',
                encoding="utf-8",
            )
            (config_dir / "StoryDungeon.json").write_text(
                '{"completeness":"complete","rows":[{"dungeonId":1001}]}',
                encoding="utf-8",
            )
            report = audit_content(
                {"entries": [
                    {"path": "assets/Patch/assetbundle/animation/role/mikoto"},
                    {"path": "assets/Patch/particles/material/story_chapter1"},
                ]},
                config_dir,
            )
            by_table = {table["table"]: table for table in report["tables"]}
            self.assertEqual(by_table["ActorConfig"]["status"], "partial")
            self.assertEqual(by_table["ActorConfig"]["evidenceRowCount"], 1)
            self.assertEqual(by_table["ChapterInfo"]["status"], "complete")
            self.assertEqual(by_table["StoryDungeon"]["status"], "complete")
            self.assertEqual(report["summary"]["partial"], 1)


if __name__ == "__main__":
    unittest.main()

