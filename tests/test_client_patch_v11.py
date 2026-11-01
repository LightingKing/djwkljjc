"""Exercise local adapters and retained native account/IAP paths without a network."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from lupa.lua53 import LuaRuntime


PATCH = Path(__file__).resolve().parents[1] / "client-patch/hot-update"


class ClientPatchV11Tests(unittest.TestCase):
    def runtime(self, name):
        lua = LuaRuntime()
        lua.execute("""
          calls={}; store={accountCache={entryType='password'}}; sdk={}
          function record(kind,value) table.insert(calls,{kind=kind,value=value}) end
          function JsonE(value) return value end
          function JsonD(value) return value end
          function ProtobufT(name,value) return {wire=name,body=value} end
          _ENV['!']=function(value)
            value.merge=function(self,other)
              for k,v in pairs(other) do self[k]=v end
              return self
            end
            return value
          end
          WU={GetString=function(key) return key end,LockInputFlag={Purchase=1},
              SetLockFlag=function(_,value) record('lock',value) end,
              ShowHintText=function(value) record('hint',value) end}
          PB={enum={PurchasePlatform={ios_cn=1,android_global=2,ios_midas=3,android_midas=4},
                    PurchaseChannel={__keys={[1]='midas'}},ResourceType={ResNone=0}}}
          CS={Msdk={},Restifizer={RestifizerManager={Instance={AccountServerUrl='http://127.0.0.1:19080',
               ConfigBearerAuth=function(self,value) record('bearer',value) end}}}}
          this={GetData=function(self,key) return store[key] end,
                SetData=function(self,key,value) store[key]=value end,
                DelayInvokeInSeconds=function(self,delay,callback)
                  if delay<1 then callback() end
                  return 'timer'
                end,
                StopCoroutine=function() end}
          function request(path)
            local req={}
            function req:AddHeader() return self end
            function req:SyncInput() return self end
            function req:Post(body,ok,fail)
              record(path,body)
              if path=='/Account/' then ok({})
              elseif path=='fci/purchasetouch/' then ok({tradeData='local-sandbox:issued'})
              elseif path=='fci/purchasedelevery/' then ok({buyCount=1,resChange={}}) end
            end
            function req:Get(ok) record(path,{}); ok({}) end
            function req:Patch(body,ok) record(path,body); if ok then ok() end end
            return req
          end
          this.AccountRequest=function(self,path) return request(path) end
          this.GameRequest=function(self,path) return request(path) end
          function require(name)
            if name=='Common/WindowUtil' then return function() return WU,{},{} end end
            if name=='Common/PbHelper' then return PB end
            if name=='Common/Singleton' then return {Register=function() return {} end} end
            return {}
          end
          function count(kind)
            local n=0
            for _,v in ipairs(calls) do if v.kind==kind then n=n+1 end end
            return n
          end
        """)
        source = (PATCH / "sources" / name).read_text(encoding="utf-8")
        if name.startswith("iaphandler"):
            source += """
              function configure()
                m_platform=1
                m_products={{productId='test-product',price=10}}
                m_remotePrice={['test-product']='native-price'}
                m_midasInfoCache={accessToken='native-token'}
              end
            """
        lua.execute(source)
        return lua

    def test_local_endpoint_match_rejects_remote_and_lookalike_urls(self):
        for source in ("auth-v11.lua", "iaphandler-v11.lua"):
            with self.subTest(source=source):
                lua = self.runtime(source)
                lua.execute("""
                  local manager=CS.Restifizer.RestifizerManager.Instance
                  for _,url in ipairs({'http://127.0.0.1:19080','http://127.0.0.1:19081/',
                    'https://localhost:19103','https://127.0.0.1:19102'}) do
                    manager.AccountServerUrl=url
                    assert(IsLocalAccountServer())
                  end
                  for _,url in ipairs({'https://accounts.example.invalid','http://127.0.0.1:19080.evil',
                    'http://127.0.0.1:190800','http://127.0.0.1:19080/path','http://localhost:8080'}) do
                    manager.AccountServerUrl=url
                    assert(not IsLocalAccountServer())
                  end
                  CS.Restifizer=nil
                  assert(not IsLocalAccountServer())
                """)

    def test_native_phone_email_and_local_username_auth_keep_their_own_contract(self):
        lua = self.runtime("auth-v11.lua")
        lua.execute("""
          UpdateAccountSession=function(callback) callback() end
          store['account/loginAccountToken']='test-session'
          assert(GetEntryType('local_user')=='password')
          assert(GetEntryType(string.rep('a',64))=='password')
          assert(GetEntryType(string.rep('a',65))=='unknow')
          RequestAccountAuth('local_user','password','test-pass')
          assert(calls[1].kind=='/Account/' and calls[2].kind=='/Auth/')
          assert(calls[2].value.entryType=='password' and calls[2].value.username=='local_user')
          calls={}
          CS.Restifizer.RestifizerManager.Instance.AccountServerUrl='https://accounts.example.invalid'
          assert(GetEntryType('13800138000')=='phone')
          assert(GetEntryType('name+tag@example.com')=='email')
          assert(GetEntryType('local_user')=='unknow')
          RequestAccountAuth('name+tag@example.com','email','test-pass')
          assert(#calls==1 and calls[1].kind=='/Auth/')
          assert(calls[1].value.wire=='ApiAuthInfo')
          assert(calls[1].value.body.entryType=='email')
          assert(calls[1].value.body.entry=='name+tag@example.com')
        """)

    def test_local_purchase_delivers_once_without_calling_platform_sdk(self):
        lua = self.runtime("iaphandler-v11.lua")
        lua.execute("""
          configure()
          sdk.Refresh=function() error('local branch called SDK refresh') end
          sdk.Purchase=function() error('local branch called SDK purchase') end
          sdk.GetActivityInfo=function() error('local branch called SDK activity info') end
          SetIAPInterface(sdk)
          OnGetActivityInfo=function() record('activity',true) end
          OnLocalDelivered=function(product,result) record('delivered',result.buyCount) end
          assert(not UseRemoteProduct())
          Refresh()
          assert(GetLocalizedPrice('test-product')=='Purchase_LocalPrice')
          GetActivityInfo()
          Purchase({productId='test-product',price=10,purchaseRes={}},1,1)
          assert(count('fci/purchase-activity-info/')==1)
          assert(count('fci/purchasetouch/')==1 and count('fci/purchasedelevery/')==1)
          assert(count('delivered')==1)
          SyncMidas(function() record('sync-callback',true) end)
          assert(count('/fci/syncmidas')==0 and count('sync-callback')==1)
        """)

    def test_remote_iap_refresh_price_checkout_and_midas_path_are_retained(self):
        lua = self.runtime("iaphandler-v11.lua")
        lua.execute("""
          configure()
          CS.Restifizer.RestifizerManager.Instance.AccountServerUrl='https://accounts.example.invalid'
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

    def test_cumulative_v11_retains_every_v10_module_and_adds_three_welfare_fixes(self):
        before = json.loads((PATCH / "modules-v10.json").read_text(encoding="utf-8"))
        after = json.loads((PATCH / "modules-v11.json").read_text(encoding="utf-8"))
        self.assertEqual(after["patchVersion"], 11)
        self.assertEqual(after["appVersion"], before["appVersion"])
        self.assertEqual(len(after["modules"]), len(before["modules"]) + 3)
        changed = []
        for old, new in zip(before["modules"], after["modules"]):
            self.assertEqual(old["target"], new["target"])
            self.assertEqual(old["originalSha256"], new["originalSha256"])
            if old["source"] != new["source"]:
                changed.append(new["source"])
        self.assertEqual(changed, ["sources/auth-v11.lua", "sources/iaphandler-v11.lua"])
        self.assertEqual(
            [entry["source"] for entry in after["modules"][len(before["modules"]):]],
            ["sources/wlfpostcard-v11.lua", "sources/wlfdecrepittransport-v11.lua",
             "sources/wlfconditionreward-v11.lua"],
        )
