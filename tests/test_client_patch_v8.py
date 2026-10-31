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


PATCH = Path(__file__).resolve().parents[1] / "client-patch/hot-update"


@unittest.skipIf(LuaRuntime is None, "offline Lua tests require hot-update/requirements.txt")
class MazePrizeLuaTests(unittest.TestCase):
    def test_native_reward_icons_group_repeated_prizes_by_quality_and_clear(self):
        source = (PATCH / "sources/mazemain.lua").read_text(encoding="utf-8")
        function = re.search(r"^function UpdatePrize\(.*?^end$", source, re.M | re.S)
        self.assertIsNotNone(function)
        lua = LuaRuntime()
        lua.execute("""
          REF = {}
          for i=1,4 do
            local box = {UISprite={}, LabelNum={UILabel={}}, active=false}
            box.gameObject = {SetActive=function(_, active) box.active=active end}
            REF['SpriteBox'..i] = box
          end
          _ENV['$'] = function(ref) return ref end
          local rows = {
            [101]={treasureBox={{quality=1,count=1,dropId='MazePrize_101'}}},
            [102]={treasureBox={{quality=2,count=1,dropId='MazePrize_102'}}},
            [103]={treasureBox={{quality=3,count=1,dropId='MazePrize_103'}}}
          }
          PB={get=function(name,id) assert(name=='MazePrize'); return assert(rows[id]) end}
        """)
        lua.execute(function.group())
        lua.execute("""
          UpdatePrize({prizeId={101,101,102,103,102}})
          local counts, visible = {}, 0
          for i=1,4 do
            local box = REF['SpriteBox'..i]
            if box.active then
              visible=visible+1
              assert(counts[box.UISprite.spriteName] == nil, 'duplicate quality icon')
              counts[box.UISprite.spriteName] = tonumber(box.LabelNum.UILabel.text)
            end
          end
          assert(visible==3)
          assert(counts.ResItem_mazebox_1==2, 'quality 1 rewards must merge')
          assert(counts.ResItem_mazebox_2==2, 'quality 2 rewards must merge')
          assert(counts.ResItem_mazebox_3==1, 'quality 3 icon must exist')
          UpdatePrize({prizeId={}})
          for i=1,4 do assert(not REF['SpriteBox'..i].active) end
        """)


class MazePatchVersionTests(unittest.TestCase):
    def test_v8_keeps_v7_modules_and_adds_maze_reward_and_arena_title_modules(self):
        old = json.loads((PATCH / "modules.json").read_text(encoding="utf-8"))
        new = json.loads((PATCH / "modules-v8.json").read_text(encoding="utf-8"))
        self.assertEqual(old["patchVersion"], 7)
        self.assertEqual(new["patchVersion"], 8)
        self.assertEqual(new["appVersion"], old["appVersion"])
        self.assertEqual(new["modules"][:-2], old["modules"])
        self.assertEqual([(m["source"], m["target"]) for m in new["modules"][-2:]], [
            ("sources/mazemain.lua", "assetbundle/data/lua/window/mazemain/mazemain"),
            ("sources/dungeonbalancefailed.lua", "assetbundle/data/lua/window/dungeonbalancefailed/dungeonbalancefailed")])
        manifest = {"version": new["appVersion"], "history": [
            {"version": 8, "length": 100, "checksum": 100,
             "urls": ["http://127.0.0.1:19080/ljjc-hot-update-v8.zip"]}]}
        for version in range(8):
            self.assertEqual(select_patch_manifest(manifest, f"2.0.4.59162.{version}")["history"], manifest["history"])
        for version in (8, 9, 10):
            self.assertEqual(select_patch_manifest(manifest, f"2.0.4.59162.{version}")["history"], [])


@unittest.skipIf(LuaRuntime is None, "offline Lua tests require hot-update/requirements.txt")
class ArenaTitleLuaTests(unittest.TestCase):
    def test_async_pvp_failure_uses_existing_activity_title_without_affecting_other_modes(self):
        source = (PATCH / "sources/dungeonbalancefailed.lua").read_text(encoding="utf-8")
        function = re.search(r"^function InitDungeonName\(.*?^end$", source, re.M | re.S)
        self.assertIsNotNone(function)
        lua = LuaRuntime()
        lua.execute("""
          PB={enum={DungeonType={AsyncPvp=3,Tower=4,BurstLinkPvp=8,Normal=2},
                    ActivityType={Tournament_AsyncPvp=5}}}
          REF={DungeonName={UIHtmlLabel={text='existing'}}}
          floor=2; dungeonId=4001; requested={}
          this={GetData=function(_,key)
            if key=='WindowDungeon/DungeonId' then return dungeonId end
            if key=='fci/tower/summary/' then return {towerFloor=floor} end
            error('unexpected data key '..key)
          end}
          WU={GetString=function(key,value)
            table.insert(requested,key)
            if key=='WeekActivitiesType_Name_5' then return '\u865a\u50cf\u4e4b\u5883' end
            if key=='WindowTower_CurrentFloor' then return 'floor '..value end
            return key
          end}
        """)
        lua.execute(function.group())
        lua.execute("""
          m_fightDungeonType=PB.enum.DungeonType.AsyncPvp
          InitDungeonName()
          assert(REF.DungeonName.UIHtmlLabel.text=='\u865a\u50cf\u4e4b\u5883')
          assert(#requested==1 and requested[1]=='WeekActivitiesType_Name_5')
          m_fightDungeonType=PB.enum.DungeonType.Normal; dungeonId=3
          InitDungeonName(); assert(REF.DungeonName.UIHtmlLabel.text=='DungeonName_3')
          m_fightDungeonType=PB.enum.DungeonType.Tower
          InitDungeonName(); assert(REF.DungeonName.UIHtmlLabel.text=='floor 2')
          floor=0; InitDungeonName(); assert(REF.DungeonName.UIHtmlLabel.text=='floor 1')
          m_fightDungeonType=PB.enum.DungeonType.BurstLinkPvp
          local before=#requested
          InitDungeonName(); assert(#requested==before and REF.DungeonName.UIHtmlLabel.text=='floor 1')
        """)


if __name__ == "__main__":
    unittest.main()
