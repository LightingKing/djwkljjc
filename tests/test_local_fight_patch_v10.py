"""Behaviour contracts for the v10 local fight Lua patches.

These tests execute the patched Lua with lupa and record the request paths the
real functions issue.  They deliberately exercise both the local REST seam and
the original remote FightService branch.
"""
from __future__ import annotations

from pathlib import Path
import unittest

try:
    from lupa.lua53 import LuaRuntime
except ImportError:  # pragma: no cover - optional hot-update dependency
    LuaRuntime = None


PATCH = Path(__file__).resolve().parents[1] / "client-patch" / "hot-update"


BASE = r'''
request_log = {}
listeners = {}
data = {}
function record(value) table.insert(request_log, value) end
function noop(...) end
function fif(condition, yes, no) if condition then return yes else return no end end
function info(...) end
function warning(...) end
function error(...) end
function table.dump(value) return "table" end

PB = {enum={AttrType={Hp=1, Max=2}, Operation={LessThan=1, LessThanOrEqualTo=2,
  GreaterThan=3, GreaterThanOrEqualTo=4, EqualTo=5, NotEqualTo=6},
  WinType={KillAll=1, KillSingle=2, KillSingleInRound=3, NoDeadSingle=4},
  DungeonType={None=0, Story=1, Normal=2, AsyncPvp=3, Tower=4,
  Gold=5, TeamPve=6, RacePVP=7, BurstLinkPvp=8, WeekActivity=9,
  Training=10, Maze=12, Triggered=13, SemiFixed=14, AssistGuild=15,
  ChallengeGuild=16, Challenge=17}, ActivityType={}}}
PB.get = function() return nil end
PB.all = function() return {} end
DEFINE = {}
TLog = {CollectEndInfo=noop, CollectBalanceData=noop, Report=noop,
  RecordLeaveDungeon=noop}
ATU = {UsedForAutumn=function() return false end}
PU = {IsRecordVideoEnabled=function() return false end}
DU = {SendAutoFightSetting=noop, Restart=noop}
U = {}
REF = {}

local preferences = {presetLevel=0, audioSetting={bgmVolume=1, sfxVolume=1}}
local singleton = {
  ConfigDataManager={GetPreferences=function() return preferences end,
                     SetPreferences=function(value) preferences=value end}
}
S = {Get=function(_, key) return singleton[key] or {} end}

local function request(path)
  local req = {path=path}
  function req:Patch(payload, callback)
    record("PATCH " .. self.path)
    if callback then callback({}) end
  end
  function req:Post(payload, callback, failed)
    record("POST " .. self.path)
    if callback then callback({}) end
  end
  function req:Delete(callback)
    record("DELETE " .. self.path)
    if callback then callback({}) end
  end
  return req
end

DB = {data=data}
function DB:GetData(key) return self.data[key] end
function DB:SetData(key, value) self.data[key]=value end
function DB:GameRequest(path) return request(path) end
function DB:SyncRemote(...) end
function DB:SetLockFlag(...) end
WU = {
  BindButtonEvent=noop, BindKeyEvent=noop, IsPreCommit=function() return false end,
  GetString=function(key, ...) return key end,
  ShowHintText=noop, RecycleWindow=noop, ReturnCity=noop,
  SaveCitySceneContext=noop, SetLockFlag=noop,
  ShowMessageYesNo=function(text, callback) callback("YES") end,
}
this = {data=data}
function this:GetData(key) return self.data[key] end
function this:SetData(key, value) self.data[key]=value end
function this:RegisterGameEvent(...) end
function this:RegisterWebSocketLuaListener(tag, name, callback)
  table.insert(listeners, tag .. ":" .. name)
end
function this:GameRequest(path) return request(path) end
CS = {
  Restifizer={WebSocketTag={FightService="fight", PushService="push"},
    RestifizerManager={Instance={
      RegisterWebSocketKickCallback=noop,
      SetFightWebSocketUrl=noop}}},
  FightBase={Instance={
    TimeOutLeaveGameLua=function(_, scene, timeout) record("TIMEOUT " .. scene) end,
    LeaveGameLua=function(_, scene) record("LEAVE " .. scene) end}},
  FightLuaLogic={Instance={FightLuaTable={gameRound=5, winTeam=1}}},
  SoundManager={Instance={StopCurrentMusic=noop}},
  RecordVideoManager={Instance={}},
  SceneFight={Instance=nil},
  EventDelegate={Add=noop},
}
function ProtobufT(_, value) return value end
function require(name)
  if name == "Common/WindowUtil" then return function() return WU, DB, REF end end
  if name == "Common/PbHelper" then return PB end
  if name == "Common/DungeonUtil" then return DU end
  if name == "Common/TlogUtil" then return TLog end
  if name == "Common/Singleton" then return S end
  if name == "Common/PlatformUtil" then return PU end
  if name == "Common/AutumnUtil" then return ATU end
  if name == "Common/Util" then return U end
  if name == "Logic/Define" then return DEFINE end
  if name == "Internal/random" then
    return {new=function() return {setSeed=noop, getSeed=function() return 0 end} end}
  end
  return {}
end
'''


def _lua(filename: str) -> LuaRuntime:
    if LuaRuntime is None:
        raise unittest.SkipTest("offline Lua tests require hot-update/requirements.txt")
    lua = LuaRuntime()
    lua.execute(BASE)
    module = lua.execute((PATCH / "sources" / filename).read_text(encoding="utf-8"))
    if module is not None:
        lua.globals().module = module
        if filename == "helplogic-v10.lua":
            lua.globals().FightHelp = module
    if filename == "dungeonutil-v10.lua":
        lua.globals().module = module
    return lua


def _requests(lua: LuaRuntime) -> list[str]:
    values = lua.eval("request_log")
    return [str(values[index]) for index in range(1, len(values) + 1)]


def _values(lua: LuaRuntime, name: str) -> list[str]:
    values = lua.eval(name)
    return [str(values[index]) for index in range(1, len(values) + 1)]


class SceneFightPatchTests(unittest.TestCase):
    def _run_end(self, dungeon_type: int, local: bool) -> tuple[list[str], list[str]]:
        lua = _lua("scenefight-v10.lua")
        lua.execute(
            f"this.data={{['FightDungeonType']={dungeon_type}, ['Dungeon/DungeonUid']='u1', "
            f"['LocalFight']={'true' if local else 'false'}}}; Start(); InitWebSocket(); ProcessGameEnd({{gameRound=5}})"
        )
        return _requests(lua), _values(lua, "listeners")

    def test_local_burst_and_team_use_active_then_balance_http(self):
        burst, burst_listeners = self._run_end(8, True)
        team, team_listeners = self._run_end(6, True)
        self.assertEqual(burst_listeners, [])
        self.assertEqual(team_listeners, [])
        self.assertEqual(burst, [
            "TIMEOUT SceneCity",
            "PATCH fci/dungeon/{Dungeon/DungeonUid}/active/",
            "POST fci/BurstLinkDunBalance/",
        ])
        self.assertEqual(team, [
            "TIMEOUT SceneCity",
            "PATCH fci/dungeon/{Dungeon/DungeonUid}/active/",
            "POST fci/teamdungeon/{Dungeon/DungeonUid}/balance/",
        ])

    def test_remote_burst_and_team_keep_fightservice_balance_flow(self):
        burst, burst_listeners = self._run_end(8, False)
        team, team_listeners = self._run_end(6, False)
        self.assertEqual(burst, ["TIMEOUT SceneCity", "POST fci/BurstLinkDunBalance/"])
        self.assertEqual(team, ["TIMEOUT SceneCity", "POST fci/teamdungeon/{Dungeon/DungeonUid}/balance/"])
        self.assertEqual(burst_listeners, ["fight:ScInteractionRsp", "fight:ScLeaveDungeonNtf"])
        self.assertEqual(team_listeners, ["fight:ScInteractionRsp", "fight:ScLeaveDungeonNtf"])


class DungeonUtilPatchTests(unittest.TestCase):
    def _leave(self, dungeon_type: int, local: bool) -> list[str]:
        lua = _lua("dungeonutil-v10.lua")
        lua.execute(
            f"data={{['FightDungeonType']={dungeon_type}, ['Dungeon/DungeonUid']='u1', "
            f"['LocalFight']={'true' if local else 'false'}, ['fci/autoFightSetting/']={{}}}}; "
            "DB.data=data; CS.FightLuaLogic.Instance.FightLuaTable={gameRound=5,winTeam=1}; "
            "module.LeaveDungeon(3)"
        )
        return _requests(lua)

    def test_local_leave_uploads_active_before_burst_and_team_balance(self):
        burst = self._leave(8, True)
        team = self._leave(6, True)
        burst_index = burst.index("PATCH fci/dungeon/{Dungeon/DungeonUid}/active/")
        team_index = team.index("PATCH fci/dungeon/{Dungeon/DungeonUid}/active/")
        self.assertEqual(burst[burst_index:burst_index + 2], [
            "PATCH fci/dungeon/{Dungeon/DungeonUid}/active/", "POST fci/BurstLinkDunBalance/",
        ])
        self.assertEqual(team[team_index:team_index + 2], [
            "PATCH fci/dungeon/{Dungeon/DungeonUid}/active/", "POST fci/teamdungeon/{Dungeon/DungeonUid}/balance/",
        ])

    def test_remote_leave_does_not_use_shared_active_endpoint(self):
        burst = self._leave(8, False)
        team = self._leave(6, False)
        self.assertNotIn("PATCH fci/dungeon/{Dungeon/DungeonUid}/active/", burst)
        self.assertNotIn("PATCH fci/dungeon/{Dungeon/DungeonUid}/active/", team)
        self.assertIn("POST fci/BurstLinkDunBalance/", burst)
        self.assertNotIn("POST fci/teamdungeon/{Dungeon/DungeonUid}/balance/", team)


class HelpLogicPatchTests(unittest.TestCase):
    def test_remote_mode_is_preserved_and_local_markers_disable_net_mode(self):
        lua = _lua("helplogic-v10.lua")
        lua.execute("CS={DataBindingManager={Instance={data={}}}}; "
                    "function CS.DataBindingManager.Instance:GetData(k) return self.data[k] end")
        is_net = lua.eval("FightHelp.isNetMode")
        lua.execute("CS.DataBindingManager.Instance.data={}")
        self.assertTrue(is_net(8))
        self.assertTrue(is_net(6))
        lua.execute("CS.DataBindingManager.Instance.data={LocalFight=true}")
        self.assertFalse(is_net(8))
        lua.execute("CS.DataBindingManager.Instance.data={['TeamPve/LocalFight']=true}")
        self.assertFalse(is_net(6))


class FightPausePatchTests(unittest.TestCase):
    def _exit(self, dungeon_type: int, local: bool) -> list[str]:
        lua = _lua("fightpausewithmorecontent-v10.lua")
        script = '''
          local function widget()
            return {gameObject={SetActive=noop}, UISlider={onChange={}},
                    UIPlayTween={Play=noop}, transform={}}
          end
          for _, key in ipairs({'ButtonExit','ButtonRestart','ButtonSetting','ButtonSettingClose',
              'HighMask','UltimateMask','CustomMask','InfoContent','SettingContent','LabelTitle',
              'LabelGoal','GoalSlot','Preset'}) do REF[key]=widget() end
          REF.Preset.UIToggleGroup={value=0}
          REF.ButtonContinue={['$SetCallback']=function(_, callback) end}
          REF.SfxSlider=widget(); REF.BgmSlider=widget()
          DU.LeaveDungeon=function() record('LOCAL_LEAVE') end
          DU.LeavePVPNetDungeon=function() record('REMOTE_LEAVE') end
          this.data={['FightDungeonType']=__TYPE__, ['LocalFight']=__LOCAL__}
          SetupWindow(); OnUIExit()
        '''.replace('__TYPE__', str(dungeon_type)).replace('__LOCAL__', 'true' if local else 'false')
        lua.execute(script)
        return _requests(lua)

    def test_local_exit_uses_local_leave_and_remote_exit_uses_fightservice(self):
        local_requests = self._exit(8, True)
        remote_requests = self._exit(8, False)
        self.assertEqual(local_requests, ["LOCAL_LEAVE"])
        self.assertEqual(remote_requests, ["REMOTE_LEAVE", "TIMEOUT SceneCity"])


if __name__ == "__main__":
    unittest.main()
