"""Run the actual LAN Lua adapters against local and native request contracts."""
from __future__ import annotations

import hashlib
import json
import unittest

try:
    import tests.test_client_patch_v11 as v11
except ModuleNotFoundError:  # unittest discover adds tests/ directly to sys.path
    import test_client_patch_v11 as v11

from server.core.patch_versions import select_patch_manifest


PATCH = v11.PATCH
LAN_ENDPOINTS = ("http://192.168.1.22:19080", "http://192.168.1.22:19082")


class ClientPatchLanTests(unittest.TestCase):
    def runtime(self, source, endpoint):
        lua = v11.ClientPatchV11Tests().runtime(source)
        lua.globals().CS.Restifizer.RestifizerManager.Instance.AccountServerUrl = endpoint
        return lua

    def test_allowlist_accepts_only_explicit_lan_and_existing_loopback_services(self):
        accepted = LAN_ENDPOINTS + (
            "http://127.0.0.1:19080", "http://127.0.0.1:19081",
            "http://localhost:19080", "http://localhost:19081",
            "https://127.0.0.1:19102", "https://127.0.0.1:19103",
            "https://localhost:19102", "https://localhost:19103",
        )
        rejected = (
            "http://192.168.1.23:19080", "http://192.168.1.22:19081",
            "https://192.168.1.22:19080", "http://192.168.1.22:190800",
            "http://192.168.1.22:19080.evil", "http://192.168.1.22:19080/path",
            "http://192.168.1.22:19080?x=1", "http://192.168.1.22:19080#fragment",
            "http://192.168.1.22:19080@accounts.example.invalid",
            "http://accounts.example.invalid@192.168.1.22:19080",
            "https://accounts.example.invalid", "", None, 19080,
        )
        for source in ("auth-lan.lua", "iaphandler-lan.lua"):
            for endpoint in accepted:
                for normalized in (endpoint, endpoint.upper() + "/"):
                    with self.subTest(source=source, endpoint=normalized):
                        lua = self.runtime(source, normalized)
                        self.assertTrue(lua.globals().IsLocalAccountServer())
            for endpoint in rejected:
                with self.subTest(source=source, endpoint=endpoint):
                    lua = self.runtime(source, endpoint)
                    self.assertFalse(lua.globals().IsLocalAccountServer())
            lua.execute("CS.Restifizer=nil; assert(not IsLocalAccountServer())")

    def test_lan_login_uses_password_contract_and_account_then_auth_requests(self):
        for endpoint in LAN_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                lua = self.runtime("auth-lan.lua", endpoint)
                lua.execute("""
                  UpdateAccountSession=function(callback) callback() end
                  store['account/loginAccountToken']='test-session'
                  assert(GetEntryType('lan_user')=='password')
                  assert(GetEntryType('13800138000')=='password')
                  assert(GetEntryType('name@example.com')=='password')
                  RequestAccountAuth('lan_user','password','a-password-longer-than-12')
                  assert(#calls==2)
                  assert(calls[1].kind=='/Account/' and calls[2].kind=='/Auth/')
                  assert(calls[2].value.entryType=='password')
                  assert(calls[2].value.username=='lan_user')
                  assert(calls[2].value.password=='a-password-longer-than-12')
                  assert(calls[2].value.wire==nil)
                """)

    def test_lan_purchase_delivers_once_and_never_calls_native_platform_sdk(self):
        for endpoint in LAN_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                lua = self.runtime("iaphandler-lan.lua", endpoint)
                lua.execute("""
                  configure()
                  sdk.Refresh=function() error('LAN used SDK refresh') end
                  sdk.RequestRemoteProduct=function() error('LAN used SDK price') end
                  sdk.GetActivityInfo=function() error('LAN used SDK activity') end
                  sdk.Purchase=function() error('LAN used SDK purchase') end
                  SetIAPInterface(sdk)
                  OnGetActivityInfo=function() record('activity',true) end
                  OnLocalDelivered=function(product,result) record('delivered',result.buyCount) end
                  assert(not UseRemoteProduct())
                  Refresh()
                  assert(GetLocalizedPrice('test-product')=='Purchase_LocalPrice')
                  GetActivityInfo()
                  Purchase({productId='test-product',price=10,purchaseRes={}},1,1)
                  assert(count('fci/purchase-activity-info/')==1)
                  assert(count('fci/purchasetouch/')==1)
                  assert(count('fci/purchasedelevery/')==1 and count('delivered')==1)
                  SyncMidas(function() record('sync-callback',true) end)
                  assert(count('/fci/syncmidas')==0 and count('sync-callback')==1)
                """)

    def test_unlisted_lan_and_remote_servers_keep_native_auth_and_checkout(self):
        for endpoint in ("http://192.168.1.23:19080", "https://accounts.example.invalid"):
            with self.subTest(endpoint=endpoint):
                lua = self.runtime("auth-lan.lua", endpoint)
                lua.execute("""
                  UpdateAccountSession=function(callback) callback() end
                  store['account/loginAccountToken']='test-session'
                  assert(GetEntryType('lan_user')=='unknow')
                  assert(GetEntryType('13800138000')=='phone')
                  assert(GetEntryType('name@example.com')=='email')
                  RequestAccountAuth('name@example.com','email','test-pass')
                  assert(#calls==1 and calls[1].kind=='/Auth/')
                  assert(calls[1].value.wire=='ApiAuthInfo')
                  assert(calls[1].value.body.entry=='name@example.com')
                """)
                lua = self.runtime("iaphandler-lan.lua", endpoint)
                lua.execute("""
                  configure()
                  sdk.Refresh=function() record('sdk-refresh',true) end
                  sdk.RequestRemoteProduct=function() record('sdk-price',true) end
                  sdk.GetActivityInfo=function() record('sdk-activity',true) end
                  sdk.Purchase=function(self,body) record('sdk-purchase',body) end
                  sdk.SetUserInfo=function() record('sdk-user',true) end
                  SetIAPInterface(sdk)
                  assert(UseRemoteProduct())
                  Refresh()
                  assert(count('sdk-refresh')==1 and count('sdk-price')==1)
                  assert(GetLocalizedPrice('test-product')=='native-price')
                  GetActivityInfo()
                  UpdateMidasUserInfo=function(info,callback) record('midas-user',true); callback(true) end
                  Purchase({productId='test-product',price=10,purchaseRes={}},1,1)
                  assert(count('sdk-activity')==1 and count('midas-user')==1)
                  assert(count('/fci/syncmidas')==1 and count('bearer')==1)
                  assert(count('fci/purchasetouch/')==1 and count('sdk-purchase')==1)
                  assert(count('fci/purchasedelevery/')==0)
                """)

    def test_cumulative_v12_retains_v11_targets_and_reproducible_sources(self):
        old_bytes = (PATCH / "modules-v11.json").read_bytes()
        old = json.loads(old_bytes)
        new = json.loads((PATCH / "modules-lan.json").read_text(encoding="utf-8"))
        origins = json.loads((PATCH / "source-origins-lan.json").read_text(encoding="utf-8"))
        self.assertEqual(new["patchVersion"], 12)
        self.assertEqual(origins["patchVersion"], 12)
        self.assertEqual(new["appVersion"], old["appVersion"])
        self.assertEqual(origins["baseModules"], "modules-v11.json")
        self.assertEqual(origins["baseModulesSha256"], hashlib.sha256(old_bytes).hexdigest())
        self.assertEqual(origins["localLanEndpoints"], list(LAN_ENDPOINTS))
        self.assertEqual(len(new["modules"]), len(old["modules"]))
        self.assertEqual(len(origins["modules"]), len(new["modules"]))
        changed = []
        for previous, current, origin in zip(old["modules"], new["modules"], origins["modules"]):
            self.assertEqual(previous["target"], current["target"])
            self.assertEqual(previous["originalSha256"], current["originalSha256"])
            self.assertEqual(origin["source"], previous["source"])
            self.assertEqual(origin["destination"], current["source"])
            self.assertEqual(origin["target"], current["target"])
            source = (PATCH / origin["source"]).read_bytes()
            output = (PATCH / origin["destination"]).read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), origin["sha256"])
            self.assertEqual(hashlib.sha256(output).hexdigest(), origin["patchedSha256"])
            if origin["replacements"]:
                text = source.decode("utf-8").replace("\r\n", "\n")
                for replacement in origin["replacements"]:
                    self.assertEqual(text.count(replacement["old"]), 1)
                    text = text.replace(replacement["old"], replacement["new"], 1)
                self.assertEqual(text.encode("utf-8"), output)
                changed.append(current["source"])
            else:
                self.assertEqual(source, output)
        self.assertEqual(changed, ["sources/auth-lan.lua", "sources/iaphandler-lan.lua"])

    def test_native_version_filter_offers_v12_to_fresh_or_v11_clients_only(self):
        for endpoint in LAN_ENDPOINTS:
            manifest = {"version": "2.0.4.59162.0", "history": [{
                "version": 12, "length": 100, "checksum": 100,
                "urls": [endpoint + "/ljjc-hot-update-lan-v12.zip"],
            }]}
            for version in (0, 11):
                result = select_patch_manifest(manifest, f"2.0.4.59162.{version}")
                self.assertEqual(result["history"], manifest["history"])
            self.assertEqual(select_patch_manifest(manifest, "2.0.4.59162.12")["history"], [])

    def test_lan_iap_source_includes_large_local_recharge_tiers(self):
        source = (PATCH / "sources" / "iaphandler-v13.lua").read_text(encoding="utf-8")
        self.assertIn('com.tencent.fcicv.Gem64800.android', source)
        self.assertIn('com.tencent.fcicv.Gem648000.android', source)
        self.assertIn('AppendLocalRechargeProducts', source)

    def test_v13_recharge_candidate_is_cumulative_from_lan_v12(self):
        base = json.loads((PATCH / "modules-lan.json").read_text(encoding="utf-8"))
        candidate = json.loads((PATCH / "modules-v13.json").read_text(encoding="utf-8"))
        self.assertEqual(candidate["patchVersion"], 13)
        self.assertEqual(candidate["appVersion"], base["appVersion"])
        self.assertEqual(len(candidate["modules"]), len(base["modules"]))
        by_target = {item["target"]: item for item in candidate["modules"]}
        iap = by_target["assetbundle/data/lua/manager/iaphandler"]
        self.assertEqual(iap["source"], "sources/iaphandler-v13.lua")
        self.assertEqual(iap["originalSha256"], next(item["originalSha256"] for item in base["modules"]
                                                        if item["target"] == iap["target"]))
        origins = json.loads((PATCH / "source-origins-v13.json").read_text(encoding="utf-8"))
        self.assertEqual(origins["baseModules"], "modules-lan.json")
        self.assertEqual(origins["patchVersion"], 13)
