"""Exact assets, original table preservation and executable local Lua branches."""
import json
from pathlib import Path
import unittest

from lupa.lua53 import LuaRuntime
from external_assets import external_path, require_file

ROOT = Path(__file__).parents[1]
SOURCES = ROOT / "client-patch/hot-update/sources"
POLICY = json.loads((ROOT / "data/server-only/content-open-20261104.json").read_text(encoding="utf8"))
GENERIC = "Texture/ItemIcon/ResUnknownAllThings_0"
MISSING = {
    "Texture/ActorPortraitFull/actor_portrait_full_1017a": GENERIC,
    "Texture/ActorSkinHead/ActorSkinHead_1017": GENERIC,
    "Texture/ActorPortraitFull/actor_portrait_full_1509a": "Texture/ActorPortraitFull/actor_portrait_full_1009a",
    "Texture/ActorSkinHead/ActorSkinHead_2009": "Texture/ActorSkinHead/ActorSkinHead_1009",
    "Texture/ActorSkinHeadBig/ActorSkinHead_big_2009": "Texture/ActorSkinHeadBig/ActorSkinHead_big_1009",
    "Texture/ActorSkinHead/ActorSkinHead_1127": GENERIC,
    "Texture/ActorSkinHeadBig/ActorSkinHead_big_1127": GENERIC,
    "Texture/ActorSkinHeadBig/ActorSkinHead_big_1133": GENERIC,
}


class OpenContentClientTests(unittest.TestCase):
    def setUp(self):
        self.assets = external_path("LJJC_ASSET_BUNDLES", "analysis/ljjc_manifest_out/assets/Patch/assetbundle")
        self.lua_root = external_path("LJJC_DECOMPILED_LUA", "analysis/lua-decompiled")
        require_file(self.assets / GENERIC.lower(), "LJJC_ASSET_BUNDLES")

    def source(self, name, original):
        path = SOURCES / name
        return (path if path.exists() else original).read_text(encoding="utf8")

    def runtime(self, local=True):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
            Assets={}; Calls={}; Released={}; LocalServer=true
            CS={UIWindowHelp={},DataBindingManager={Instance={}},ResourceManager={Instance={}},
                GameTime={serverUtc=2026,ServerTimeStrToUtc=function(s) return tonumber(s:sub(1,4)) end}}
            CS.ResourceManager.Instance.AcquireAssetObject=function(_,path,reference)
                table.insert(Calls,path); return Assets[string.lower(path)]
            end
            CS.ResourceManager.Instance.ReleaseAssetObject=function(_,asset,reference)
                table.insert(Released,asset)
            end
            PB={enum={LockInputFlag={},AttrType={HpRate=1,Hp=2,AttackRate=3,Attack=4,
                PhysicalDefenseRate=5,PhysicalDefense=6,PenetrationRate=7,Penetration=8,
                MagicalDefenseRate=9,MagicalDefense=10}}}
            PB.get=function(name,id) return Tables[name] and Tables[name][id] end
            PB.all=function(name) return Tables[name] or {} end
            S={Get=function() return {IsLocalAccountServer=function() return LocalServer end} end}
            _ENV["$"]=function(target) return target or {} end
            function require(name)
                if name=="Common/PbHelper" then return PB end
                if name=="Common/Singleton" then return S end
                if name=="Common/WindowUtil" then return WindowFactory end
                if name=="Common/ActorUtil" then return AU end
                if name=="Common/SoundUtil" then return {PlayActorWiki=function() end} end
                return {}
            end
            table.find=function(t,p) for k,v in pairs(t) do if p(k,v) then return k,v end end end
        ''')
        lua.globals().LocalServer = local
        tables = {}
        for name in ("ActorConfig", "ActorSkin", "Role"):
            rows = json.loads((ROOT / f"data/client-tables/{name}.json").read_text(encoding="utf8"))["rows"]
            tables[name] = {r["id"]: r for r in rows}
        lua.globals().Tables = lua.table_from(tables, recursive=True)
        factory = lua.execute(self.source("windowutil-content-open.lua", SOURCES / "windowutil.lua"))
        lua.globals().WindowFactory = factory
        wu = factory()[0]
        lua.globals().WU = wu
        au = lua.execute(self.source("actorutil-content-open.lua", SOURCES / "actorutil.lua"))
        lua.globals().AU = au
        return lua, wu, au

    def populate_real_assets(self, lua):
        for path in set(MISSING.values()) | {GENERIC}:
            self.assertTrue((self.assets / path.lower()).is_file())
            lua.globals().Assets[path.lower()] = path

    def test_actual_skin_preview_preserves_recolor_or_clears_it_for_generic(self):
        lua, wu, au = self.runtime()
        self.populate_real_assets(lua)
        wu.ToggleRendering = lua.eval("function() end")
        cases = (
            (1509, 100901, "Texture/ActorSkinHeadBig/ActorSkinHead_big_1009"),
            (2127, 112701, GENERIC),
            (2133, 113301, GENERIC),
        )
        for actor_id, skin_id, expected in cases:
            with self.subTest(actor=actor_id, skin=skin_id):
                gasket = lua.table_from({"UITexture": {}}, recursive=True)
                au.SetActorSkinPreview(gasket, lua.table(), actor_id, skin_id)
                self.assertEqual(gasket.UITexture.mainTexturePath, expected)
                if expected == GENERIC:
                    self.assertEqual(gasket.UITexture.maskTexturePath, "")
                    self.assertEqual(gasket.UITexture.additionalParam, 0)
                else:
                    self.assertIn("_palette", gasket.UITexture.maskTexturePath)
                    self.assertEqual(gasket.UITexture.additionalParam, au.GetActorPaletteIndex(actor_id, skin_id))

    def test_actual_skin_icon_draw_selects_allowed_actor_and_default_fallback(self):
        lua, wu, _ = self.runtime()
        self.populate_real_assets(lua)
        wu.ToggleRendering = lua.eval("function() end")
        lua.execute('''
            inherit=function() end; MarkAsDirty=function() end
            this={SpriteIconBG={UISprite={}},TextureHead={UITexture={}},SpritePaletteBG={UISprite={}}}
        ''')
        lua.execute(self.source("widgeticonskin-content-open.lua", self.lua_root / "window__widget__widgeticonslot__widgeticonskin.lua"))
        lua.globals().Tables.ActorSkin[101701].ignoreActorOpenTime = True
        lua.globals().SetData(101701)
        lua.globals().Draw()
        self.assertEqual(lua.globals().this.TextureHead.UITexture.mainTexturePath, GENERIC)
        lua.globals().SetDefault(1509)
        lua.globals().Draw()
        self.assertEqual(lua.globals().this.TextureHead.UITexture.mainTexturePath, "Texture/ActorSkinHead/ActorSkinHead_1009")

    def test_actual_full_portrait_window_keeps_background_and_uses_same_role_or_generic(self):
        lua, _, _ = self.runtime()
        self.populate_real_assets(lua)
        lua.execute('''
            this={TextureActor={UITexture={},transform={}}, BG={UITexture={}},Slider={UISlider={}}}
            Tables.ActorPortrait={[1017]={bg="original_bg_1017"},[1509]={bg="original_bg_1509"}}
            CS.UnityEngine={Vector3=function(x,y,z) return {x=x,y=y,z=z} end}
        ''')
        lua.execute(self.source("collectionsactorpicture-content-open.lua", self.lua_root / "window__collectionsactorpicture__collectionsactorpicture.lua"))
        for actor_id, expected in ((1017, GENERIC), (1509, "Texture/ActorPortraitFull/actor_portrait_full_1009a")):
            with self.subTest(actor=actor_id):
                lua.globals().SetActor(lua.globals().Tables.ActorConfig[actor_id])
                self.assertEqual(lua.globals().this.TextureActor.UITexture.mainTexturePath, expected)
                self.assertEqual(lua.globals().this.BG.UITexture.mainTexturePath, f"Texture/ActorPortraitFullBG/original_bg_{actor_id}")

    def test_skin_stays_closed_when_all_texture_fallbacks_fail(self):
        lua, _, au = self.runtime()
        lua.globals().Tables.ActorSkin[112701].openTime = "2018-1-1 00:00:00"
        self.assertFalse(au.IsActorSkinOpen(112701))
        self.assertEqual(list(au.GetSkinActors(112701).values()), [])
        lua.globals().Assets[GENERIC.lower()] = GENERIC
        self.assertTrue(au.IsActorSkinOpen(112701))
        self.assertEqual(list(au.GetSkinActors(112701).values()), [2127])

    def test_real_missing_resources_receive_exact_fallback_only_after_nil(self):
        lua, wu, _ = self.runtime()
        for missing, expected in MISSING.items():
            with self.subTest(path=missing):
                self.assertFalse((self.assets / missing.lower()).exists())
                self.assertTrue((self.assets / expected.lower()).is_file())
                lua.globals().Assets[expected.lower()] = expected
                ui = lua.table_from({"mainTexturePath": missing})
                helper = wu.ApplyLocalMissingActorTexture
                if helper is not None:
                    helper(ui, missing, lua.table())
                self.assertEqual(ui.mainTexturePath, expected)
                # A later client asset with this exact path must take priority.
                lua.globals().Assets[missing.lower()] = "original"
                unchanged = lua.table_from({"mainTexturePath": missing})
                helper(unchanged, missing, lua.table())
                self.assertEqual(unchanged.mainTexturePath, missing)
                lua.globals().Assets[missing.lower()] = None

    def test_same_role_fallback_then_generic_and_no_foreign_or_unlisted_changes(self):
        lua, wu, _ = self.runtime()
        lua.globals().Assets[GENERIC.lower()] = GENERIC
        missing = "Texture/ActorSkinHeadBig/ActorSkinHead_big_2009"
        ui = lua.table_from({"mainTexturePath": missing, "maskTexturePath": "palette", "additionalParam": 21})
        helper = wu.ApplyLocalMissingActorTexture
        self.assertIsNotNone(helper)
        helper(ui, missing, lua.table())
        self.assertEqual(ui.mainTexturePath, GENERIC)
        self.assertEqual(ui.maskTexturePath, "")
        self.assertEqual(ui.additionalParam, 0)
        lua.globals().LocalServer = False
        remote = lua.table_from({"mainTexturePath": missing})
        helper(remote, missing, lua.table())
        self.assertEqual(remote.mainTexturePath, missing)
        lua.globals().LocalServer = True
        unrelated = lua.table_from({"mainTexturePath": "Texture/ActorPortrait/actor_portrait_2016a"})
        helper(unrelated, unrelated.mainTexturePath, lua.table())
        self.assertEqual(unrelated.mainTexturePath, "Texture/ActorPortrait/actor_portrait_2016a")

    def test_all_existing_six_character_resources_remain_original(self):
        lua, wu, _ = self.runtime()
        rows = json.loads((ROOT / "data/client-tables/ActorConfig.json").read_text(encoding="utf8"))["rows"]
        from tools.asset_index import ACTOR_UI_PATHS
        helper = wu.ApplyLocalMissingActorTexture
        self.assertIsNotNone(helper)
        for row in rows:
            if row["id"] not in POLICY["openCharacterIds"]:
                continue
            for pattern in ACTOR_UI_PATHS.values():
                path = pattern.format(**row)
                if (self.assets / path).is_file():
                    lua.globals().Assets[path] = path
                    ui = lua.table_from({"mainTexturePath": path})
                    helper(ui, path, lua.table())
                    self.assertEqual(ui.mainTexturePath, path)

    def test_local_character_and_skin_filters_keep_blocked_variants_closed(self):
        lua, _, au = self.runtime()
        self.populate_real_assets(lua)
        for actor_id in POLICY["openCharacterIds"]:
            self.assertTrue(au.IsActorOpen(actor_id, "2029-1-1 00:00:00"))
        for actor_id in POLICY["excludedCharacterIds"]:
            self.assertFalse(au.IsActorOpen(actor_id, "2018-1-1 00:00:00"))
        # ignoreActorOpenTime must not enable unusable 2017 in a mixed row.
        lua.globals().Tables.ActorSkin[101701].ignoreActorOpenTime = True
        self.assertEqual(list(au.GetSkinActors(101701).values()), [1017])
        self.assertFalse(au.IsActorSkinOpen(113404))
        lua.globals().LocalServer = False
        self.assertTrue(au.IsActorOpen(1511, "2018-1-1 00:00:00"))
        self.assertTrue(au.IsActorSkinOpen(113404))

    def test_skin_table_changes_exactly_195_open_times_and_no_other_values(self):
        from tools.export_client_tables import _to_py
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute("function ProtobufI(_,value) return value end")
        original = self.lua_root / "xlsx__actorskin.lua"
        raw = _to_py(lua.execute(self.source("actorskin-content-open.lua", original))[1])
        source_rows = json.loads((ROOT / "data/client-tables/ActorSkin.json").read_text(encoding="utf8"))["rows"]
        self.assertEqual(len(raw), 442)
        changed = set()
        for old, new in zip(source_rows, raw):
            expected = dict(old)
            if old["id"] in POLICY["futureSkinIds"]:
                self.assertTrue(old["openTime"].startswith("2029"))
                expected["openTime"] = "2018-1-1 00:00:00"
                changed.add(old["id"])
            self.assertEqual(new, expected)
        self.assertEqual(len(changed), 195)
        self.assertEqual(changed, set(POLICY["futureSkinIds"]))
