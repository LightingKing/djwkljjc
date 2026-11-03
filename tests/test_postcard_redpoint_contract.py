"""Exercise login postcard data with the unmodified client red-point function."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from lupa.lua53 import LuaRuntime

from accounts import LocalAccounts
from external_assets import external_path, require_file
from game import LocalGame
from grants import grant_rewards
from test_welfare_maze_local import guest


class PostcardRedpointContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / "accounts")
        self.now = int(datetime(2026, 11, 4, 4, tzinfo=timezone.utc).timestamp())
        self.restart()
        self.account, self.auth = guest(self.accounts)
        lua_root = external_path("LJJC_DECOMPILED_LUA", "analysis/lua-decompiled")
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute('''
            ProtobufI = function(_, row) return row end
            table.find = function(t, predicate)
                for k,v in pairs(t) do if predicate(k,v) then return k,v end end
            end
            DB = {GetData = function(_, key) return Items[key] end}
            PB = {get = function(name, card, cell)
                assert(name == "PostcardCell")
                return PostcardCell[1][PostcardCell[2][card][cell]]
            end}
            require = function(name)
                if name == "Common/WindowUtil" then return function() return {},DB end end
                if name == "Common/PbHelper" then return PB end
                return {}
            end
        ''')
        table_path = require_file(lua_root / "xlsx__postcardcell.lua", "LJJC_DECOMPILED_LUA")
        source = require_file(lua_root / "common__activityutil.lua", "LJJC_DECOMPILED_LUA")
        self.lua.globals().PostcardCell = self.lua.execute(table_path.read_text(encoding="utf8"))
        self.activity_util = self.lua.execute(source.read_text(encoding="utf8"))

    def restart(self):
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now, initial_level=60)

    def request(self, method, path, payload=None):
        status, result = self.game.handle_http(method, path, json.dumps(payload or {}).encode(),
                                             "application/json", self.auth)
        self.assertEqual(status, 200, result)
        return result

    def login_postcard(self):
        return self.request("GET", "/fci/redpoint/data")["postcardResult"]

    def redpoint(self, data):
        items = self.game.systems._state(self.game._profile(self.account)).get("items", {})
        self.lua.globals().Items = self.lua.table_from(
            {"fci/item/" + key: value for key, value in items.items()}, recursive=True)
        return self.activity_util.DosePostCardHasRedPoint(self.lua.table_from(data, recursive=True))

    def assert_snapshot(self, expected_redpoint):
        data = self.login_postcard()
        self.assertEqual(data, self.request("GET", "/fci/Postcard/"))
        self.assertEqual([card["postcardId"] for card in data["postcardInfo"]], [1, 2, 3, 4])
        self.assertEqual(len(data["prizeInfo"]), 5)
        for card in data["postcardInfo"]:
            self.assertEqual([cell["cellId"] for cell in card["postcardCell"]], list(range(1, 19)))
            self.assertTrue(all(type(cell["isLighten"]) is bool for cell in card["postcardCell"]))
        self.assertTrue(all(type(row["prizeReceiveStatus"]) is bool for row in data["prizeInfo"]))
        self.assertEqual(self.redpoint(data), expected_redpoint)
        return data

    def test_visited_login_runs_original_lua_without_empty_prize_crash(self):
        self.request("POST", "/fci/Postcard/")
        # Previously raises the same LuaError seen in ActivityUtil.bytes:317
        # while City binds the real /fci/redpoint/data response.
        self.assertFalse(self.redpoint(self.login_postcard()))

    def test_native_progress_and_claims_share_login_snapshot_after_restart(self):
        self.assert_snapshot(True)
        self.request("POST", "/fci/Postcard/")
        self.assert_snapshot(False)
        grant_rewards(self.game, self.game._profile(self.account), [{"type": 11, "id": 2001, "count": 72}])
        self.assert_snapshot(True)
        for card in range(1, 5):
            for cell in range(1, 19):
                self.request("POST", f"/fci/Postcard/Lighten/{card}", {"cellId": cell})
            completed = self.assert_snapshot(True)
            self.assertTrue(all(c["isLighten"] for c in completed["postcardInfo"][card - 1]["postcardCell"]))
            self.request("POST", "/fci/Postcard/ReceivePrize/", {"postcardId": card})
        self.assert_snapshot(True)  # All four individual prizes claimed; total remains.
        self.request("POST", "/fci/Postcard/ReceivePrize/", {"postcardId": 0})
        before_restart = self.assert_snapshot(False)
        self.restart()
        self.assertEqual(self.assert_snapshot(False), before_restart)

    def test_login_reads_do_not_visit_or_change_postcard_progress_or_assets(self):
        self.assert_snapshot(True)
        profile = self.game._profile(self.account)
        before = self.game.welfare._state(profile)["welfare"]["postcard"]
        assets = self.game.systems._state(profile)
        resources = profile["resources"]
        self.assert_snapshot(True)
        self.assertEqual(self.game.welfare._state(profile)["welfare"]["postcard"], before)
        self.assertEqual(self.game.systems._state(profile), assets)
        self.assertEqual(self.game._profile(self.account)["resources"], resources)


if __name__ == "__main__":
    unittest.main()
