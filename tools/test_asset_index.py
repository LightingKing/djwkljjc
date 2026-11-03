from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from asset_index import build_index


class AssetIndexTests(unittest.TestCase):
    def test_indexes_known_character_skin_scene_and_rejects_unknown_id(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "assets"
            config = root / "config"
            for relative in (
                "animation/role/mikoto",
                "audio/voice/mikoto/voice_01",
                "texture/actorskinhead/actorskinhead_1001",
                "texture/skin/100101",
                "scene/stage_bg_1",
            ):
                path = assets / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
            config.mkdir()
            (config / "ActorConfig.json").write_text(json.dumps({"rows": [{"id": 1001, "role": 1, "animRes": "mikoto", "prefabRes": "mikoto", "logicRes": "mikoto", "voiceRes": "mikoto"}]}), encoding="utf-8")
            (config / "ActorSkin.json").write_text(json.dumps({"rows": [
                {"id": 100101, "actorIds": [1001], "paletteIndexs": [21], "sources": [21]},
                {"id": 999999, "actorIds": [9999], "paletteIndexs": [21], "sources": [0]},
            ]}), encoding="utf-8")
            (config / "CollectionsScenes.json").write_text(json.dumps({"rows": [{"id": 31001, "textureRes": "stage_bg_1"}]}), encoding="utf-8")
            report = build_index(assets, config)
            actor = report["characters"][0]
            skin, missing_skin = report["skins"]
            scene = report["scenes"][0]
            self.assertEqual(actor["status"], "complete")
            self.assertTrue(actor["resources"]["mikoto"])
            self.assertEqual(skin["status"], "skin_id_evidence")
            self.assertTrue(skin["skinIdMatches"])
            self.assertEqual(scene["status"], "complete")
            self.assertEqual(missing_skin["id"], 999999)
            self.assertEqual(missing_skin["status"], "missing")


if __name__ == "__main__":
    unittest.main()
