"""Exact-path regressions plus read-only checks against recovered real bundles.

Set LJJC_ASSET_ROOT (and optionally LJJC_LUA_DIR) outside this workspace.
Real-bundle tests skip with a reason when those external inputs are absent.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from asset_index import build_index

ROOT = Path(__file__).resolve().parents[1]


class AssetIndexRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.config = self.root / "config"
        self.assets.mkdir()
        self.config.mkdir()

    def table(self, name, rows):
        (self.config / (name + ".json")).write_text(json.dumps({"rows": rows}), encoding="utf-8")

    def asset(self, relative):
        path = self.assets / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")

    def test_stage_bg_1_does_not_match_stage_bg_10(self):
        self.table("CollectionsScenes", [{"id": 31001, "textureRes": "stage_bg_1"}])
        self.asset("texture/background/stage_bg_10")
        scene = build_index(self.assets, self.config)["scenes"][0]
        self.assertEqual(scene["status"], "missing")

    def test_skin_id_cannot_match_unrelated_skill_texture(self):
        self.table("ActorConfig", [{"id": 1001, "kind": 1, "animRes": "1001a", "prefabRes": "mikoto", "skinUIRes": 1001}])
        self.table("ActorSkin", [{"id": 100101, "actorIds": [1001], "paletteIndexs": [21]}])
        self.asset("texture/actorskill/actor_skill_100101")
        skin = build_index(self.assets, self.config)["skins"][0]
        self.assertEqual(skin["status"], "missing")

    def test_nonexistent_actor_cannot_resolve_skin(self):
        self.table("ActorSkin", [{"id": 999999, "actorIds": [9999], "paletteIndexs": [1]}])
        skin = build_index(self.assets, self.config)["skins"][0]
        self.assertEqual(skin["status"], "missing")
        self.assertEqual(skin["variants"][0]["status"], "missing_actor_config")

    def test_skill_slots_require_commands_and_exclude_passive_and_link(self):
        self.table("ActorConfig", [{"id": 1503, "kind": 1}, {"id": 1511, "kind": 1}, {"id": 2015, "kind": 2}])
        self.table("ActorSkill", [
            {"actorId": 1503, "skillId": 1503900, "displayIndex": 1, "isPassive": True, "cmdStr": "passive"},
            {"actorId": 1503, "skillId": 1503201, "displayIndex": 2, "isLink": True, "cmdStr": "link"},
            {"actorId": 1503, "skillId": 1503003, "displayIndex": 3},
            {"actorId": 1511, "skillId": 1511015, "displayIndex": 3, "cmdStr": "InputCardCmd10020"},
            {"actorId": 2015, "skillId": 2015001, "displayIndex": 1, "cmdStr": "InputCardCmd10021"},
        ])
        a, b, c = build_index(self.assets, self.config)["characters"]
        self.assertEqual(a["skills"]["status"], "cannot_battle")
        self.assertEqual(b["skills"]["missingActiveSlots"], [1, 2])
        self.assertEqual(c["skills"]["status"], "complete")


class RealAssetIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidate = ROOT.parents[1] / "analysis/ljjc_manifest_out/assets/Patch/assetbundle"
        cls.asset_root = Path(os.environ.get("LJJC_ASSET_ROOT", candidate))
        cls.lua_dir = Path(os.environ.get("LJJC_LUA_DIR", ROOT.parents[1] / "analysis/lua-decompiled"))
        if not cls.asset_root.is_dir():
            raise unittest.SkipTest(f"Recovered asset bundles unavailable: {cls.asset_root}; set LJJC_ASSET_ROOT")
        if importlib.util.find_spec("UnityPy") is None:
            raise unittest.SkipTest("UnityPy is required to read actual palette rows")
        cls.report = build_index(cls.asset_root, ROOT / "data/client-tables", cls.lua_dir)
        cls.actors = {r["id"]: r for r in cls.report["characters"]}
        cls.skins = {r["id"]: r for r in cls.report["skins"]}

    def test_live_characters_have_all_eleven_ui_assets(self):
        for actor_id in (1502, 1514):
            with self.subTest(actor=actor_id):
                actor = self.actors[actor_id]
                self.assertEqual(actor["uiPresentCount"], 11)
                self.assertEqual(actor["uiStatus"], "complete")
                self.assertEqual(actor["skills"]["status"], "complete")
                self.assertTrue(actor["resources"]["voice"]["present"])

    def test_unavailable_characters_have_none_of_eleven_ui_assets(self):
        for actor_id in (2016, 2017):
            with self.subTest(actor=actor_id):
                actor = self.actors[actor_id]
                self.assertEqual(actor["uiPresentCount"], 0)
                self.assertEqual(actor["uiStatus"], "missing")
                self.assertTrue(actor["resources"]["animationRole"]["present"])

    def test_live_skins_have_real_palette_rows_and_exact_heads(self):
        for skin_id in (100104, 100204, 100306):
            with self.subTest(skin=skin_id):
                skin = self.skins[skin_id]
                self.assertEqual(skin["status"], "complete", skin)
                for variant in skin["variants"]:
                    self.assertEqual(variant["palette"]["status"], "ok")
                    self.assertTrue(variant["resources"]["skinHeadBig"]["present"])

    def test_future_skin_palette_and_large_head_coverage(self):
        future = [s for s in self.report["skins"] if str(s.get("openTime", "")).startswith("2029")]
        self.assertEqual(len(future), 199)
        self.assertEqual(sum(s["status"] == "complete" for s in future), 183)

    def test_known_incomplete_active_skills_are_not_reported_playable(self):
        for actor_id in (1503, 1505, 1506, 1515, 1516, 1601, 1607):
            with self.subTest(actor=actor_id):
                self.assertEqual(self.actors[actor_id]["skills"]["status"], "cannot_battle")
        self.assertEqual(self.actors[1511]["skills"]["missingActiveSlots"], [1, 2])


if __name__ == "__main__":
    unittest.main()
