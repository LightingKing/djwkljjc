from __future__ import annotations

from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
HOT_UPDATE = ROOT / "client-patch" / "hot-update"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HOT_UPDATE))

from server.core.patch_versions import PatchVersionError, PatchVersionMismatch, parse_version, select_patch_manifest

try:
    from lupa.lua53 import LuaRuntime
except ImportError:
    LuaRuntime = None


def manifest():
    return {"version": "2.0.4.59162.0", "history": [
        {"version": version, "length": 100 + version, "checksum": 123 + version,
         "urls": [f"http://127.0.0.1:19080/patch-v{version}.zip"]}
        for version in (2, 4, 5)
    ]}


def lua_function(filename: str, name: str) -> str:
    source = (HOT_UPDATE / "sources" / filename).read_text(encoding="utf-8")
    match = re.search(rf"^function {re.escape(name)}\(.*?^end$", source, re.M | re.S)
    if match is None:
        raise AssertionError(f"missing Lua function {name}")
    return match.group()


class PatchVersionTests(unittest.TestCase):
    def test_old_current_and_newer_clients(self):
        data = manifest()
        self.assertEqual([e["version"] for e in select_patch_manifest(data, "2.0.4.59162.3")["history"]], [4, 5])
        for version in (5, 6):
            self.assertEqual(select_patch_manifest(data, f"2.0.4.59162.{version}")["history"], [])
        # Numeric comparison, including the two-digit patch version.
        self.assertEqual(select_patch_manifest(data, "2.0.4.59162.10")["history"], [])

    def test_selection_is_detached_and_missing_version_preserves_legacy_request(self):
        data = manifest()
        before = deepcopy(data)
        result = select_patch_manifest(data, None)
        self.assertEqual(result, data)
        result["history"][0]["urls"].append("changed")
        self.assertEqual(data, before)

    def test_different_apk_never_receives_incremental_lua(self):
        data = manifest()
        for version in ("2.0.3.59162.0", "2.0.5.59162.0"):
            with self.assertRaises(PatchVersionMismatch):
                select_patch_manifest(data, version)
        data["history"].insert(0, {"version": 0, "length": 200, "checksum": 300,
                                  "urls": ["http://127.0.0.1:19080/client.apk"]})
        self.assertEqual([e["version"] for e in select_patch_manifest(data, "2.0.3.59162.0")["history"]], [0])
        self.assertEqual([e["version"] for e in select_patch_manifest(data, "2.0.4.59162.4")["history"]], [5])

    def test_malformed_input_cannot_silently_skip_update(self):
        for version in ("2.0.4", "2.0.4.59162.-1", "2.0.4.59162.5x", " 2.0.4.59162.5", "2.0.4.59162.999999999999"):
            with self.subTest(version=version), self.assertRaises(PatchVersionError):
                parse_version(version)
        for key, value in (("version", True), ("length", -1), ("checksum", 0xFFFFFFFF), ("urls", [])):
            data = manifest()
            data["history"][0][key] = value
            with self.subTest(key=key), self.assertRaises(PatchVersionError):
                select_patch_manifest(data, "2.0.4.59162.5")
        data = manifest()
        data["history"].append(deepcopy(data["history"][0]))
        with self.assertRaises(PatchVersionError):
            select_patch_manifest(data, None)


@unittest.skipIf(LuaRuntime is None, "offline Lua tests require client-patch/hot-update/requirements.txt")
class HotUpdateLuaTests(unittest.TestCase):
    def test_iap_loads_first_purchase_and_used_tiers_and_always_refreshes(self):
        lua = LuaRuntime()
        lua.execute("""
            events = 0; delayed = nil; failRequest = false
            this = {}
            function this:BroadcastGameEvent(name) assert(name == 'OnGetPurchaseActivityInfo'); events = events + 1 end
            function this:DelayInvokeInSeconds(seconds, callback) assert(seconds == 0.1); delayed = callback end
            function this:GameRequest(path)
              assert(path == 'fci/purchase-activity-info/')
              return {SyncInput=function(self, value) assert(value == false); return self end,
                      Get=function(self, ok, failed) if failRequest then failed() else ok(response) end end}
            end
            function JsonD(value) if value == '{}' then return {} end; return value end
            function JsonE(value) return value end
            response = {mp_info={utp_mpinfo_detail={
                rank_info={{num='60',send_ext='60'},{num='1280',send_ext=''}},
                range_info={{num='60',coin={{send_num='0'}}},{num='1280',coin={{send_num='120'}}}}
            }}}
        """)
        functions = ("OnGetActivityInfo", "IsDoubleSendByFirst", "GetSendNum", "GetActivityByPrice", "GetActivityInfo")
        lua.execute("local m_activityInfo = {}\n" + "\n".join(lua_function("iaphandler.lua", name) for name in functions))
        lua.execute("GetActivityInfo(); assert(events == 0); delayed(); assert(events == 1)")
        lua.execute("assert(IsDoubleSendByFirst(GetActivityByPrice(60))); assert(not IsDoubleSendByFirst(GetActivityByPrice(1280))); assert(GetSendNum(GetActivityByPrice(1280)) == 120)")
        lua.execute("failRequest = true; GetActivityInfo(); delayed(); assert(events == 2); assert(GetActivityByPrice(60) == nil)")

    def test_collection_filter_uses_role_mapping_and_keeps_open_playable_actors(self):
        lua = LuaRuntime()
        actors = json.loads((ROOT / "data/client-tables/ActorConfig.json").read_text(encoding="utf-8"))["rows"]
        roles = json.loads((ROOT / "data/client-tables/Role.json").read_text(encoding="utf-8"))["rows"]
        actor_table = lua.table_from({row["id"]: lua.table_from(row) for row in actors})
        role_table = lua.table_from({row["id"]: lua.table_from(row) for row in roles})
        lua.globals().actorRows, lua.globals().roleRows = actor_table, role_table
        lua.execute("AU={}; PB={get=function(name,id) if name == 'ActorConfig' then return actorRows[id] else return roleRows[id] end end}; CS={GameTime={serverUtc=100,ServerTimeStrToUtc=function(value) return tonumber(value) end}}")
        lua.execute(lua_function("actorutil.lua", "AU.IsActorOpen"))
        is_open = lua.globals().AU.IsActorOpen
        for actor_id in (3006, 3007, 7004, 7008, 7009, 7010, 7011, 7012, 7013, 8008, 8009, 8011, 8012, 8013, 999999):
            self.assertFalse(is_open(actor_id, ""), actor_id)
        self.assertTrue(is_open(1001, ""))
        self.assertTrue(is_open(1001, "99"))
        self.assertFalse(is_open(1001, "101"))
        # Inspect every original row: valid entries outside the three explicit
        # enemy exclusions retain their original empty-open-time behavior.
        role_ids = {row["id"] for row in roles}
        for row in actors:
            expected = row["role"] in role_ids and row["id"] not in {3006, 3007, 7004}
            self.assertEqual(is_open(row["id"], ""), expected, row["id"])

    def test_power_limit_uses_resource_type_and_blocks_request_without_spending(self):
        lua = LuaRuntime()
        lua.execute("""
          own=60; paid=0; posted=0; hints={}; this={}; REF={}
          _ENV['$']=function(value) return value end
          PB={enum={ResourceType={ResPlayerPower=6,ResPlayerGold=7}},
              get=function(name,level) assert(level==1); return {incInfo={{type=10,max=999},{type=6,max=60}}} end}
          function this:GetData(path)
            if path=='fci/resource/ResPlayerPower_0' then return own end
            if path=='fci/baseinfo/' then return {level=1} end
            error('wrong data binding path: '..path)
          end
          function this:GameRequest(path) posted=posted+1; return {Post=function() end} end
          WU={ShowHintText=function(value) table.insert(hints,value) end,
              GetString=function(value) return value end,
              TryToPayResource=function(cost,callback) paid=paid+1; callback() end}
          go={['@realResourceType']=1000101,['@index']=1}
        """)
        lua.execute("local m_resourceType=6; local m_param; local m_exchangeList={{remainBuyTimes=5,cost={}}}\n" + lua_function("resourceexchange.lua", "OnExchangeClick"))
        lua.execute("OnExchangeClick(go); assert(hints[1]=='Window_ReachPowerLimit'); assert(paid==0 and posted==0)")
        lua.execute("own=90; OnExchangeClick(go); assert(paid==0 and posted==0)")
        lua.execute("own=59; OnExchangeClick(go); assert(paid==1 and posted==1)")

    def test_login_success_normalizes_social_entry_without_overwriting_existing_value(self):
        for filename in ("auth.lua", "login.lua"):
            source = (HOT_UPDATE / "sources" / filename).read_text(encoding="utf-8")
            self.assertEqual(source.count('accountCache.socialEntry = accountCache.socialEntry or ""'), 1)
        lua = LuaRuntime()
        lua.execute('accountCache={}; accountCache.socialEntry=accountCache.socialEntry or ""; assert(accountCache.socialEntry==""); accountCache.socialEntry="existing"; accountCache.socialEntry=accountCache.socialEntry or ""; assert(accountCache.socialEntry=="existing")')

    def test_every_candidate_source_compiles_and_fci_round_trips(self):
        import fci_lua_codec as codec
        spec = importlib.util.spec_from_file_location("hot_update_build_test", HOT_UPDATE / "build.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        config = json.loads((HOT_UPDATE / "modules.json").read_text(encoding="utf-8"))
        for module in config["modules"]:
            with self.subTest(module=module["source"]):
                source = (HOT_UPDATE / module["source"]).read_bytes()
                standard = builder.compile_source(source, b"@candidate")
                encrypted = codec.encode_fci(codec.standard_to_fci(standard), [1, 2, 3, 4])
                plain, _ = codec.decode_fci(encrypted)
                self.assertEqual(codec.fci_to_standard(plain), standard)
                builder.verify_lua(standard)


class PatchZipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("hot_update_zip_test", HOT_UPDATE / "build.py")
        cls.builder = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(cls.builder)
        except ImportError as exc:
            raise unittest.SkipTest(f"offline builder dependency unavailable: {exc}")

    def test_zip_is_deterministic_and_matches_manifest_checksum_convention(self):
        content = {"assetbundle/data/lua/manager/test": b"real candidate bytes", "assetbundle/data/lua/window/test": b"another module"}
        raw, checksum = self.builder.build_zip(content)
        self.assertEqual((raw, checksum), self.builder.build_zip(dict(reversed(list(content.items())))))
        self.assertEqual(checksum, zlib.crc32(raw) & 0xFFFFFFFF)
        self.assertLess(checksum, 0x80000000)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual({name: archive.read(name) for name in archive.namelist()}, content)

    def test_paths_cannot_escape_candidate_or_patch_root(self):
        for name in ("../bad", "/absolute", "C:/bad", "bad\\name", "a/../b", "a//b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.builder.build_zip({name: b"payload"})

    def test_candidate_rejects_wrong_original_bundle_before_compiling_or_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root, bundle_root, output = root / "source", root / "original", root / "candidate"
            source_root.mkdir()
            target = "assetbundle/data/lua/manager/test"
            original = bundle_root / target
            original.parent.mkdir(parents=True)
            original.write_bytes(b"different APK bundle")
            (source_root / "test.lua").write_text("return true", encoding="utf-8")
            config = {"appVersion": "2.0.4.59162.0", "patchVersion": 5,
                      "modules": [{"source": "test.lua", "target": target, "originalSha256": "0" * 64}]}
            config_path = source_root / "modules.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                self.builder.build_candidate(config_path, bundle_root, output, "http://127.0.0.1:19080")
            self.assertFalse(output.exists())
            self.assertEqual(original.read_bytes(), b"different APK bundle")


if __name__ == "__main__":
    unittest.main()
