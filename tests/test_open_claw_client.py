"""Execute the local claw branch with the recovered durable cache contract."""
from pathlib import Path
import unittest

from lupa.lua53 import LuaRuntime

SOURCE = Path(__file__).parents[1] / "client-patch/hot-update/sources/gachaeventautumn-open-all.lua"


class ClawClientReceiptTests(unittest.TestCase):
    def lua(self, pending=None, counter=0, local=True):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute('''
            Cache = {}; Calls = {}; LocalServer = true; PlayerId = "123"
            WU = {
                GetGameDataCache = function(k) return Cache[k] end,
                SetGameDataCache = function(k,v) Cache[k]=v end,
                AcquireWindowAsync = function() end
            }
            DB = {}; REF = {}; CS = {GameTime={serverUtc=1793016000}}
            this = {
                GetData = function(_,key) if key=="playerId" then return PlayerId end end,
                SetData = function() end,
                GameRequest = function(_,path)
                    local req={path=path}
                    function req:Query(key,value) self.key=key; self.value=value; return self end
                    function req:Post(body,callback) self.callback=callback; table.insert(Calls,self) end
                    return req
                end
            }
            require = function(name)
                if name == "Common/WindowUtil" then return function() return WU,DB,REF end end
                if name == "Common/Singleton" then return {Get=function() return {IsLocalAccountServer=function() return LocalServer end} end} end
                if name == "Manager/DataBindingHandler" then return {ResChange=function() end} end
                return {}
            end
        ''')
        lua.globals().LocalServer = local
        if pending is not None or counter:
            lua.globals().Cache["LJJCLocalClaw_123"] = lua.table_from({"pending": pending, "counter": counter})
        lua.execute(SOURCE.read_text(encoding="utf8"))
        return lua

    def test_pending_key_survives_window_and_client_restart_until_success(self):
        lua = self.lua()
        lua.globals().DoGacha()
        key = lua.globals().Calls[1].value
        self.assertTrue(key)
        self.assertEqual(lua.globals().Calls[1].key, "localRequestId")
        saved = lua.globals().Cache["LJJCLocalClaw_123"]
        self.assertEqual(saved.pending, key)
        restored = self.lua(saved.pending, saved.counter)
        restored.globals().CS.GameTime.serverUtc += 86400
        # A pending paid operation can recover even when its ticket was spent
        # before the reply was lost; this precedes the old balance guard.
        restored.globals().OnButtonGacha()
        self.assertEqual(restored.globals().Calls[1].value, key)
        restored.globals().Calls[1].callback(restored.table_from({"resChange": restored.table()}))
        self.assertIsNone(restored.globals().Cache["LJJCLocalClaw_123"].pending)
        restored.globals().DoGacha()
        self.assertNotEqual(restored.globals().Calls[2].value, key)

    def test_remote_branch_keeps_original_post_without_query(self):
        lua = self.lua(local=False)
        lua.globals().DoGacha()
        self.assertEqual(lua.globals().Calls[1].path, "fci/ClawCraneBingo/")
        self.assertIsNone(lua.globals().Calls[1].key)
