"""Offline Lua contract tests for the cumulative v9 authentication patch."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

try:
    from lupa.lua53 import LuaRuntime
except ImportError:  # pragma: no cover - exercised in environments without Lua
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
class ClientPatchV9Tests(unittest.TestCase):
    def auth(self):
        lua = LuaRuntime()
        lua.execute(
            """
            REF={InputPassword={UIInput={value=''}}}
            hints={}
            WU={GetString=function(key) return key end,
                ShowHintText=function(text) table.insert(hints,text) end,
                RecycleWindow=function() recycled=true end}
            this={RegisterGameEvent=function() end,
                  BroadcastGameEvent=function(self,name) event=name end}
            function require(name)
              if name=='Common/WindowUtil' then return function() return WU,{},REF end end
              return function() return {} end
            end
            U={trim=function(value) return value end}
            CS={EventDelegate={Add=function() end}}
            """
        )
        lua.execute(function("auth-v9.lua", "OnAccountAuthResponseFailed"))
        return lua

    def restifizer(self):
        lua = LuaRuntime()
        lua.execute(
            """
            store={accountCache={accessToken='expired'},accountAuth={entry='local_user'}}
            events={}; resets=0; hints=0; dialogs=0
            this={SetData=function(self,path,value) store[path]=value end,
                  BroadcastGameEvent=function(self,name) table.insert(events,name) end}
            WU={GetString=function(key) return key end,
                ShowMessageOK=function(message,callback) dialogs=dialogs+1; if callback then callback() end end,
                ShowHintText=function() hints=hints+1 end,
                RenderTime2=function() return 'later' end}
            CS={GameGlobal={Instance={ResetGame=function() resets=resets+1 end}}}
            function warning() end
            function JsonE() return '' end
            function require(name)
              if name=='Common/WindowUtil' then return function() return WU,{},{} end end
              if name=='Common/PbHelper' then return {all=function() return {first=function() return {} end} end} end
              return function() return {} end
            end
            """
        )
        lua.execute(function("restifizermanager-v9.lua", "OnHandleRestifizerError"))
        return lua

    def test_auth_uses_exact_localized_codes_and_consumes_failure(self):
        lua = self.auth()
        lua.execute(
            """
            for _,result in ipairs({
              {error={code='Error_PasswordDoNotMatch'}},
              {notify={code='Error_AccountNotExist'}},
              {error={code='unrecognised_server_code'}},
              {}
            }) do
              REF.InputPassword.UIInput.value='bad-password'
              assert(OnAccountAuthResponseFailed(result)==true)
              assert(REF.InputPassword.UIInput.value=='')
            end
            assert(hints[1]=='Error_PasswordDoNotMatch')
            assert(hints[2]=='Error_AccountNotExist')
            assert(hints[3]=='WindowLogin_LoginFailed')
            assert(hints[4]=='WindowLogin_LoginFailed')
            assert(recycled==nil and event==nil)
            """
        )

    def test_activation_code_still_uses_both_native_error_shapes(self):
        lua = self.auth()
        lua.execute(
            """
            for _,kind in ipairs({'error','notify'}) do
              local result={[kind]={code='Error_CDKEYNotActivation'}}
              assert(OnAccountAuthResponseFailed(result)==true)
              assert(recycled==true and event=='ShowActivationCode')
              recycled=nil; event=nil
            end
            assert(#hints==0)
            """
        )

    def test_http_401_clears_session_and_returns_to_login_without_reset(self):
        lua = self.restifizer()
        lua.execute(
            """
            assert(OnHandleRestifizerError(401,
              {notify={code='Error_KickReason_NeedLoginAgain'}})==true)
            assert(store.accountCache ~= nil and store.accountCache.accessToken == nil)
            assert(store.accountAuth == nil)
            assert(events[1]=='NetworkErrorNotResetGame')
            assert(resets==0 and dialogs==0 and hints==0)
            """
        )

    def test_native_notify_errors_remain_displayable_without_reset(self):
        lua = self.restifizer()
        lua.execute(
            """
            OnHandleRestifizerError(509,{notify={code='Error_PasswordDoNotMatch'}})
            OnHandleRestifizerError(507,{notify={code='Error_AccountNotExist'}})
            assert(hints==1 and dialogs==1 and resets==0)
            """
        )


class CandidatePatchV9Tests(unittest.TestCase):
    def test_manifest_is_cumulative_v9_and_replaces_only_auth_error_modules(self):
        config = json.loads((PATCH / "modules-v9.json").read_text(encoding="utf-8"))
        previous = json.loads((PATCH / "modules-v8.json").read_text(encoding="utf-8"))
        self.assertEqual(config["patchVersion"], 9)
        self.assertEqual(
            {row["target"] for row in config["modules"]},
            {row["target"] for row in previous["modules"]},
        )
        rows = {row["target"]: row for row in config["modules"]}
        self.assertEqual(rows["assetbundle/data/lua/window/auth/auth"]["source"], "sources/auth-v9.lua")
        self.assertEqual(
            rows["assetbundle/data/lua/manager/restifizermanager"]["source"],
            "sources/restifizermanager-v9.lua",
        )
        for target, row in rows.items():
            old = next(item for item in previous["modules"] if item["target"] == target)
            self.assertEqual(row["originalSha256"], old["originalSha256"])


if __name__ == "__main__":
    unittest.main()
