"""Execute the v6 Lua account, redpoint and native notify paths offline."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from server.core.patch_versions import select_patch_manifest

try:
    from lupa.lua53 import LuaRuntime
except ImportError:
    LuaRuntime = None


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "client-patch/hot-update"


def function(filename: str, name: str) -> str:
    source = (PATCH / "sources" / filename).read_text(encoding="utf-8")
    found = re.search(rf"^function {re.escape(name)}\(.*?^end$", source, re.M | re.S)
    if found is None:
        raise AssertionError(f"missing Lua function: {name}")
    return found.group()


@unittest.skipIf(LuaRuntime is None, "offline Lua tests require hot-update/requirements.txt")
class ClientPatchV6Tests(unittest.TestCase):
    def test_purchase_product_count_updates_every_matching_mall_row(self):
        """A product ledger is shared by seasonal Mall rows (834/860)."""
        lua = LuaRuntime()
        lua.execute("""
          WU={}; DB={}; REF={}; this={}
          function require(name)
            if name == 'Common/WindowUtil' then
              return function() return WU,DB,REF end
            end
            if name == 'Common/Singleton' then
              return {Get=function() return {} end}
            end
            return function() return {} end
          end
          function makeRef(row) return {['$$SetBuyCount']=function(count) row.seen=count end} end
        """)
        source = (PATCH / "sources" / "purchaseactivitygift.lua").read_text(encoding="utf-8")
        lua.execute(source + "\nfunction __setPurchaseRows(data, refs, map) m_data=data; m_slotRef=refs; m_mapMallRecord=map end")
        lua.execute("""
          rows={{id=834,productId='com.tencent.fcicv.re8.android',buyCount=0},
                {id=860,productId='com.tencent.fcicv.re8.android',buyCount=0},
                {id=926,productId='com.tencent.fcicv.box1.android',buyCount=0}}
          refs={[834]=makeRef(rows[1]),[860]=makeRef(rows[2]),[926]=makeRef(rows[3])}
          map={[834]=rows[1],[860]=rows[2],[926]=rows[3]}
          __setPurchaseRows(rows,refs,map)
          SetProductBuyCount('com.tencent.fcicv.re8.android',1)
          assert(rows[1].buyCount==1 and rows[2].buyCount==1 and rows[3].buyCount==0)
          assert(rows[1].seen==1 and rows[2].seen==1 and rows[3].seen==nil)
        """)

    def test_purchase_text_fallback_uses_original_legacy_row_or_item(self):
        """Missing 926/927 keys must not render the raw localization key."""
        lua = LuaRuntime()
        lua.execute("""
          strings={MALL_GIFT_NAME918='灵子礼盒（初中生）',MALL_GIFT_DESC918='旧商品描述'}
          WU={GetString=function(key) return strings[key] or key end,
              GetItemName=function() return '灵子礼盒（初中生）' end,
              GetItemDesc=function() return '道具描述' end}
          PB={enum={ResourceType={ResItem=11}},get=function(name,id)
             if name=='Mall' and id==918 then return {id=918,productId='com.tencent.fcicv.box1.android',reward={{id=123003,type=11}}} end
             return nil
          end}
        """)
        source = (PATCH / "sources" / "iaphandler.lua").read_text(encoding="utf-8")
        lines = source.splitlines()
        start = next(i for i,line in enumerate(lines) if line.startswith('function GetMallGiftText'))
        end = next(i for i in range(start+1,len(lines)) if lines[i].startswith('function GetProducts'))
        lua.execute('\n'.join(lines[start:end]))
        lua.execute("""
          local row={id=926,productId='com.tencent.fcicv.box1.android',reward={{id=123003,type=11}}}
          assert(GetMallGiftText(row,'NAME')=='灵子礼盒（初中生）')
          assert(GetMallGiftText(row,'DESC')=='旧商品描述')
          strings.MALL_GIFT_DESC918=nil
          assert(GetMallGiftText(row,'DESC')=='道具描述')
        """)

    def login(self):
        lua = LuaRuntime()
        lua.execute("""
          now=1000; os.time=function() return now end
          store={accountCache={accountEntry='local_user',entryType='password',
                   accessToken='cached-token',expireTime=now+86400,localeId='local'},
                 AccountSwitch={customService=false}}
          requests={}; hints={}; windows={}; msdkCalls=0; dirCalls=0; resets=0
          this={}; REF={}
          local function widget()
            return {gameObject={activeSelf=false,SetActive=function(self,value) self.activeSelf=value end},
                    UIPlayTween={Play=function() end},UIHtmlLabel={},
                    UIGrid={Reposition=function() end}}
          end
          for _,name in ipairs({'BottomBG','Start','LastAccount','SwitchAccount','ButtonSwitchAccount',
              'ButtonReturn','ButtonCustomSrv','NormalRightButtons','LabelLastAccount','Agreement'}) do
            REF[name]=widget()
          end
          CS={ConfigDataManager={Instance={CurrentDynamicConfig={Id='local'}}},
              PlatformManager={IsNeedWXAutoLogin=false,IsNeedQQAutoLogin=false}}
          PB={enum={MSDKLoginPlatform={MSDK_QQ=2,MSDK_Wechat=1}}}
          Msdk={ePlatform={ePlatform_None=0}}
          S={Get=function() return {ClientBasicInfo=function() return {deviceId='test-device'} end} end}
          WU={GetString=function(key,...) return key end,
              ShowHintText=function(text) table.insert(hints,text) end,
              AcquireWindowAsync=function(name) table.insert(windows,name) end}
          function this:GetData(path) return store[path] end
          function this:SetData(path,value) store[path]=value end
          function this:BroadcastGameEvent() error('cached login must not start a game') end
          function this:DelayInvokeInScaledSeconds(_,callback) callback() end
          function this:AccountRequest(path)
            local request={path=path}
            function request:AddHeader(key,value) self.header={key,value}; return self end
            function request:Post(body,ok,failed)
              self.body=body; table.insert(requests,self)
              if failedPath==self.path then
                handled=failed({notify={code='WindowLogin_LoginFailed'}})
              elseif self.path=='/AccountSession/' then
                ok({accountToken='session-token'})
              elseif self.path=='/Auth/' then
                ok({accountId=1,openId='open-local',tokenAccess='renewed-token',
                    createTime=now,expireSeconds=30*86400})
              else error('unexpected account endpoint '..self.path) end
            end
            return request
          end
          function ProtobufT(name,value) return {wireType=name,fields=value} end
          function RequestDir(callback) dirCalls=dirCalls+1; callback({}) end
          function ShowAroundWidget() end
          function MsdkLogin(platform,callback) msdkCalls=msdkCalls+1 end
          function OnButtonAuth2() error('unexpected WeChat login') end
          function OnButtonAuth3() error('unexpected QQ login') end
          function ShowLicense() end
          function ShowNotice() end
          function SetTimeoutCheck() error('unexpected unhandled session failure') end
        """)
        names = ("IsNeedAutoLogin", "SetInitUI", "ProcessLogin", "RequestCachedAccountAuth",
                 "OnCachedAccountAuthResponseFailed", "UpdateAccountSession", "OnAccountSessionResponse",
                 "SaveAccountAuthResponse", "ShowAccountAuthWindow", "clearAccountCache")
        lua.execute("local m_cachedAuthFailed=false; local m_accountSessionUpdatedCallback; "
                    "local m_noticeDisplayed=true; local m_isAcceptLicense=true; local m_currWolrdData;\n" +
                    "\n".join(function("login.lua", name) for name in names))
        return lua

    def test_password_cold_login_renews_token_without_msdk_or_password_storage(self):
        lua = self.login()
        lua.execute("""
          -- A stale social-platform field must never route a local account into MSDK.
          store.accountCache.socialPlatform=2
          ProcessLogin()
          assert(msdkCalls==0 and #requests==2)
          assert(requests[1].path=='/AccountSession/' and requests[1].body.wireType=='ApiAccountSession')
          assert(requests[2].path=='/Auth/' and requests[2].header[2]=='AccountToken session-token')
          local body=requests[2].body
          assert(body.entryType=='password' and body.username=='local_user')
          assert(body.accessToken=='cached-token' and body.password==nil and body.wireType==nil)
          assert(store.accountCache.accessToken=='renewed-token' and store.accountCache.accountEntry=='local_user')
          assert(store.accountCache.expireTime==now+30*86400 and store.accountCache.password==nil)
          assert(REF.Start.gameObject.activeSelf and #windows==0)
        """)

    def test_guest_cold_login_uses_original_wireformat_auth_even_when_token_expired(self):
        lua = self.login()
        lua.execute("""
          store.accountCache={accountEntry='guest_saved',entryType='guest',localeId='local',expireTime=0}
          ProcessLogin()
          assert(msdkCalls==0 and #requests==2)
          assert(requests[2].body.wireType=='ApiAuthInfo')
          assert(requests[2].body.fields.entry=='guest_saved' and requests[2].body.fields.entryType=='guest')
          assert(store.accountCache.guestEntry=='guest_saved' and store.accountCache.socialEntry=='')
          assert(REF.Start.gameObject.activeSelf)
        """)

    def test_only_qq_and_wechat_caches_enter_msdk(self):
        for platform in (0, 1, 2, 3):
            with self.subTest(platform=platform):
                lua = self.login()
                lua.globals().platform = platform
                lua.execute("""
                  store.accountCache.entryType='msdk'; store.accountCache.socialPlatform=platform
                  ProcessLogin()
                  assert(msdkCalls==((platform==1 or platform==2) and 1 or 0))
                  assert(#requests==0)
                """)

    def test_expired_password_token_prompts_with_saved_username(self):
        lua = self.login()
        lua.execute("""
          store.accountCache.expireTime=now-1
          ProcessLogin()
          assert(msdkCalls==0 and #requests==0 and windows[1]=='Auth')
          assert(store.accountAuth.entry=='local_user' and store.accountAuth.accessToken==nil)
          assert(store.accountCache.accessToken=='cached-token')
        """)

    def test_local_auth_and_session_failures_keep_cache_and_open_form_without_directory_retry(self):
        for endpoint in ("/AccountSession/", "/Auth/"):
            with self.subTest(endpoint=endpoint):
                lua = self.login()
                lua.globals().failedPath = endpoint
                lua.execute("""
                  CS.PlatformManager.IsNeedQQAutoLogin=true
                  ProcessLogin()
                  assert(handled==true and msdkCalls==0 and dirCalls==0)
                  assert(store.accountCache.accountEntry=='local_user' and store.accountCache.accessToken=='cached-token')
                  assert(hints[1]=='WindowLogin_LoginFailed' and windows[1]=='Auth')
                  assert(store.accountAuth.entry=='local_user' and store.accountAuth.accessToken==nil)
                  assert(not REF.Start.gameObject.activeSelf and REF.SwitchAccount.gameObject.activeSelf)
                """)

    def test_foreign_locale_does_not_send_cached_credentials(self):
        lua = self.login()
        lua.execute("""
          store.accountCache.localeId='another-server'
          ProcessLogin()
          assert(#requests==0 and msdkCalls==0 and store.accountCache.accountEntry==nil)
          assert(windows[1]=='Auth')
        """)

    def auth(self):
        lua = LuaRuntime()
        lua.execute("""
          REF={InputName={UIInput={value='',onChange={}}},InputPassword={UIInput={value='',onChange={}}},
               ButtonCancel={},ButtonConfirm={}}
          WU={BindButtonEvent=function() end,GetString=function(key) return key end,
              ShowHintText=function(text) hint=text end,RecycleWindow=function() recycled=true end}
          CS={EventDelegate={Add=function() end}}
          this={RegisterGameEvent=function() end,SetData=function() end,
                BroadcastGameEvent=function(self,name) event=name end}
          U={trim=function(value) return (value:gsub('^%s+',''):gsub('%s+$','')) end}
          function RequestAccountAuth(entry,kind,password) sent={entry=entry,kind=kind,password=password} end
        """)
        names = ("SetupWindow", "OnConfirmClick", "OnAccountAuthResponseFailed", "GetEntryType")
        lua.execute("local authWithBearer=false;\n" + "\n".join(function("auth.lua", name) for name in names))
        return lua

    def test_64_character_username_and_password_reach_auth(self):
        lua = self.auth()
        lua.execute("""
          SetupWindow()
          assert(REF.InputName.UIInput.characterLimit==64 and REF.InputPassword.UIInput.characterLimit==64)
          REF.InputName.UIInput.value=string.rep('a',64)
          REF.InputPassword.UIInput.value=string.rep('P',64)
          OnConfirmClick()
          assert(#sent.entry==64 and #sent.password==64 and sent.kind=='password')
          assert(GetEntryType(string.rep('a',65))=='unknow')
        """)

    def test_wrong_credentials_failure_is_consumed_and_uses_existing_localized_hint(self):
        lua = self.auth()
        lua.execute("""
          for _,result in ipairs({{error='invalid_credentials'},
              {error={code='Error_LoginFaild'}},{notify={code='WindowLogin_LoginFailed'}},{}}) do
            REF.InputPassword.UIInput.value='bad-password'
            assert(OnAccountAuthResponseFailed(result)==true)
            assert(hint=='WindowLogin_LoginFailed' and REF.InputPassword.UIInput.value=='')
          end
          assert(OnAccountAuthResponseFailed(nil)==true)
          assert(event==nil and recycled==nil)
        """)

    def test_activation_code_flow_still_intercepts_both_native_shapes(self):
        lua = self.auth()
        lua.execute("""
          for _,kind in ipairs({'error','notify'}) do
            local result={[kind]={code='Error_CDKEYNotActivation'}}
            assert(OnAccountAuthResponseFailed(result)==true)
            assert(recycled==true and event=='ShowActivationCode')
            assert(hint==nil)
          end
        """)

    def test_entering_normalizes_json_redpoint_keys_for_city_and_keeps_false_values(self):
        lua = LuaRuntime()
        lua.execute("""
          store={}; this={}; DB=this; redpointCallback=nil
          function this:SetData(path,value) store[path]=value end
          function this:GetData(path) return store[path] end
          function this:SyncRemote() end
          function this:Bind() end
          function this:RegisterGameEvent() end
          function this:GameRequest(path)
            local request={}
            function request:Query() return self end
            function request:SetPageSize() return self end
            function request:SyncInput() return self end
            function request:Get(callback) if path=='fci/redpoint/data' then redpointCallback=callback end end
            return request
          end
          MU={}; TU={}; RU={SetRedMark=function() end}
          AU={GetAchievementInfo=function() return 'level',1 end}
          data={rolePresentRedpoint={presents={}},achievementSummaryGet={canReward=false},
                redpoint={redpointType={['7']={redpointId={['1']={redpointValue=true}}},
                  ['3']={redpointId={['1001']={redpointValue=false}}},
                  [8]={redpointId={[1]={redpointValue=true}}},
                  malformed={redpointId={['1']={redpointValue=true}}}}}}
        """)
        lua.execute(function("restifizermanager.lua", "DoEnteringRequests"))
        lua.execute("""
          DoEnteringRequests(); redpointCallback(data)
          local viewed=store.activityViewed
          assert(viewed[7][1].redpointValue==true and viewed['7']==nil)
          assert(viewed[3][1001].redpointValue==false and viewed[8][1].redpointValue==true)
          assert(viewed.malformed==nil)
          -- A later login reloads the map, so the acknowledgement persists.
          store.activityViewed=nil; DoEnteringRequests(); redpointCallback(data)
          assert(store.activityViewed[7][1].redpointValue==true)
        """)

    def test_native_507_509_notify_paths_never_reset_the_game(self):
        lua = LuaRuntime()
        lua.execute("""
          resets=0; okCalls=0; hints=0
          CS={GameGlobal={Instance={ResetGame=function() resets=resets+1 end}}}
          WU={GetString=function(key) assert(type(key)=='string'); return key end,
              ShowMessageOK=function(message,callback) okCalls=okCalls+1; text=message; if callback then callback() end end,
              ShowHintText=function(message,seconds) hints=hints+1; text=message; duration=seconds end}
          function warning() end
          function JsonE() return '' end
        """)
        lua.execute(function("restifizermanager.lua", "OnHandleRestifizerError"))
        lua.execute("""
          OnHandleRestifizerError(509,{notify={code='WindowLogin_LoginFailed'}})
          assert(hints==1 and duration==2 and okCalls==0 and resets==0)
          OnHandleRestifizerError(507,{notify={code='Window_FatalError'}})
          assert(okCalls==1 and hints==1 and resets==0)
          OnHandleRestifizerError(509,{notify={code='Error_ActivityNotStart'}})
          assert(hints==2 and resets==0)
          -- Structured error alone still resets in the original client.
          OnHandleRestifizerError(400,{error={code='Error_LoginFaild'}})
          assert(resets==1)
        """)


class CandidateV6Tests(unittest.TestCase):
    def test_v6_is_cumulative_and_same_version_gets_no_patch(self):
        config = json.loads((PATCH / "modules.json").read_text(encoding="utf-8"))
        self.assertEqual(config["patchVersion"], 6)
        self.assertEqual({row["source"] for row in config["modules"]}, {
            "sources/login.lua", "sources/auth.lua", "sources/iaphandler.lua", "sources/actorutil.lua",
            "sources/resourceexchange.lua", "sources/restifizermanager.lua",
            "sources/purchaseactivitygift.lua", "sources/purchasegiftitem.lua", "sources/purchasebuy.lua"})
        manifest = {"version": config["appVersion"], "history": [
            {"version": 6, "length": 100, "checksum": 100,
             "urls": ["http://127.0.0.1:19080/ljjc-hot-update-v6.zip"]}]}
        for version in range(6):
            self.assertEqual(select_patch_manifest(manifest, f"2.0.4.59162.{version}")["history"], manifest["history"])
        for version in (6, 7, 10):
            self.assertEqual(select_patch_manifest(manifest, f"2.0.4.59162.{version}")["history"], [])


if __name__ == "__main__":
    unittest.main()
