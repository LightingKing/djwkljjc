local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local DU = require("Common/DungeonUtil")
local TLog = require("Common/TlogUtil")
local S = require("Common/Singleton")
local m_configDataManager = S:Get("ConfigDataManager")
local PU = require("Common/PlatformUtil")
local ATU = require("Common/AutumnUtil")
local fightDungeonType
local bBalance = false
local resultVal
local enableChangeScene = false

local function IsLocalFight()
  return this:GetData("LocalFight") == true or this:GetData("BurstLink/LocalFight") == true
      or this:GetData("LocalBurstLinkFight") == true or this:GetData("TeamPve/LocalFight") == true
end

function Start()
  fightDungeonType = this:GetData("FightDungeonType")
  this:RegisterGameEvent("Fight_GameEnd", ProcessGameEnd)
  this:RegisterGameEvent("Fight_EnableChange", OnChangeScene)
  this:RegisterGameEvent("FightWebSocket_Init", InitWebSocket)
  StartRecordVideo()
end

function OnDestroy()
  if PU.IsRecordVideoEnabled() then
    CS.RecordVideoManager.Instance:ClearStartMomentsRecordingCallbacks()
    CS.RecordVideoManager.Instance:ClearEndMomentsRecordingCallbacks()
  end
end

function StartRecordVideo()
  if PU.IsRecordVideoEnabled() then
    this:SetData("RecordVideoList", nil)
    CS.RecordVideoManager.Instance:StartMomentsRecording(function(status)
      if status == CS.GameJoyAPI.RecordingStartStatus.Fail then
        warning("RecordVideo", "record start error")
      else
        this:SetData("RecordVideoStartTime", CS.GameJoy.getSystemCurrentTimeMillis)
      end
      this:DelayInvokeInSeconds(1, function()
        local dungeonId = this:GetData("WindowDungeon/DungeonId")
        local dungeonInfo = PB.get("DungeonMonster", dungeonId)
        if dungeonInfo ~= nil then
          CS.SoundManager.Instance:StopCurrentMusic()
          CS.SoundManager.Instance:PlayMusic(dungeonInfo.musicID)
        end
      end)
    end)
  end
end

function EndRecordVideo()
  if PU.IsRecordVideoEnabled() then
    local isRecording = CS.RecordVideoManager.Instance:isRecordingMoments()
    if isRecording then
      CS.RecordVideoManager.Instance:EndMomentsRecording(function(status)
        if status == CS.GameJoyAPI.RecordingStartStatus.Fail then
          warning("RecordVideo", "record end error")
        end
      end)
    end
  end
end

function InitWebSocket()
  if (fightDungeonType == PB.enum.DungeonType.BurstLinkPvp or fightDungeonType == PB.enum.DungeonType.TeamPve)
      and not IsLocalFight() then
    this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.FightService, "ScInteractionRsp", OnFightEmoteNtf)
    this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.FightService, "ScLeaveDungeonNtf", OnLeaveDungeonNtf)
    CS.Restifizer.RestifizerManager.Instance:RegisterWebSocketKickCallback(CS.Restifizer.WebSocketTag.FightService, function(webSocket)
      info("GameLogic", "fight kick")
      local message = WU.GetString(PB.enum.ErrorMessageBox.__keys[webSocket.DisconnectReason])
      WU.ShowMessageOK(message, function()
        if CS.FightBase.Instance ~= nil then
          EndGame("SceneCity")
        end
      end)
    end)
  end
end

function OnFightEmoteNtf(data)
  warning("OnFightEmoteNtf", table.dump(data))
  if data.dungeonId == this:GetData("Dungeon/DungeonUid") then
    this:BroadcastGameEvent("FightUI_EmoteNtf", data)
  else
    warning("GamePlay", "error data = " .. table.dump(data))
  end
end

function OnLeaveDungeonNtf(data)
  if bBalance == true then
    return
  end
  bBalance = true
  DU.LeaveDungeon(3)
end

function EndGame(scene)
  info("GamePlay", "SceneFight.EndGame( " .. scene .. " )")
  ReportTLog()
  CS.FightBase.Instance:LeaveGameLua(scene)
end

function OnChangeScene()
  enableChangeScene = true
end

function Update()
  if enableChangeScene and resultVal ~= nil then
    ProcessChangeScene()
    enableChangeScene = false
  end
end

function ProcessChangeScene()
  info("GamePlay", "ProcessChangeScene")
  local uid = this:GetData("Dungeon/DungeonUid")
  if uid == nil or fightDungeonType == PB.enum.DungeonType.None then
    CS.FightBase.Instance:TimeOutLeaveGameLua("", 1)
    return
  end
  if fightDungeonType == PB.enum.DungeonType.BurstLinkPvp then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.RacePVP then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.TeamPve then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.AsyncPvp then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.Tower then
    if resultVal == 1 then
      EndGame("SceneBalance")
    elseif resultVal == 2 then
      WU.ShowMessageOK(WU.GetString("WindowTower_DataReset"), function(result)
        if result == "OK" then
          this:SetData("SceneContext/SceneCity", "Tower")
          EndGame("SceneCity")
        end
      end)
    end
  elseif fightDungeonType == PB.enum.DungeonType.WeekActivity then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.Story or fightDungeonType == PB.enum.DungeonType.Normal or fightDungeonType == PB.enum.DungeonType.Training or fightDungeonType == PB.enum.DungeonType.Challenge then
    if resultVal.balanceInfo and resultVal.balanceInfo.win then
      local chapterId = this:GetData("WindowDungeon/ChapterId")
      local dungeonId = this:GetData("WindowDungeon/DungeonId")
      local cartoon = WU.GetCartoonInfo(chapterId, dungeonId, 1)
      if cartoon then
        WU.EnterCartoonLevel(cartoon.id, nil, "balance")
      else
        EndGame("SceneBalance")
      end
    else
      EndGame("SceneBalance")
    end
  elseif fightDungeonType == PB.enum.DungeonType.Gold then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.Maze then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.Triggered then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.SemiFixed then
    EndGame("SceneBalance")
  elseif fightDungeonType == PB.enum.DungeonType.AssistGuild or fightDungeonType == PB.enum.DungeonType.ChallengeGuild then
    EndGame("SceneBalance")
  else
    error("GamePlay", "no fightDungeonType = " .. fightDungeonType)
    EndGame("SceneCity")
  end
end

function ProcessGameEnd(fightLuaTable)
  fightLuaTable = ProtobufT("FightActiveData", fightLuaTable)
  TLog.CollectEndInfo(fightLuaTable)
  EndRecordVideo()
  local uid = this:GetData("Dungeon/DungeonUid")
  if uid ~= nil then
    info("GamePlay", "request game end " .. uid)
    CS.FightBase.Instance:TimeOutLeaveGameLua("SceneCity", 70)
  end
  if uid == nil or fightDungeonType == PB.enum.DungeonType.None then
    DB:SetData("FightDataRecord", fightLuaTable)
    resultVal = 1
    return
  end
  DU.SendAutoFightSetting()
  local eventActivityType = this:GetData("Event/ActivityType")
  if eventActivityType then
    this:GameRequest("fci/event-dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/event-dungeon/{Dungeon/DungeonUid}/balance"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        if ATU.UsedForAutumn(eventActivityType) then
          DB:SyncRemote(DB:GameRequest("fci/event-dungeon/record/"))
        else
          DB:SyncRemote(DB:GameRequest(string.format("fci/activity/record/%s/%s", eventActivityType, this:GetData("Event/ActivityId"))))
        end
        resultVal = 1
      end, function(error)
        resultVal = 2
        return true
      end)
    end)
    return
  end
  if fightDungeonType == PB.enum.DungeonType.BurstLinkPvp then
    if bBalance == true then
      return
    end
    bBalance = true
    local function balance()
      this:GameRequest("fci/BurstLinkDunBalance/"):Post({}, function(response)
        this:SetData("DungeonBalance/result", response)
        resultVal = 1
      end)
    end
    if IsLocalFight() then
      this:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, balance)
    else
      balance()
    end
  elseif fightDungeonType == PB.enum.DungeonType.TeamPve then
    if bBalance == true then
      return
    end
    bBalance = true
    local function balance()
      this:GameRequest("fci/teamdungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end
    if IsLocalFight() then
      this:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, balance)
    else
      balance()
    end
  elseif fightDungeonType == PB.enum.DungeonType.RacePVP then
    this:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/racepvp-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.AsyncPvp then
    this:GameRequest("fci/tournamentdungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/tournament/dungeonbalance/"):Post({
        activityType = this:GetData("fci/tournament/curActivityType")
      }, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.Tower then
    this:GameRequest("fci/tower-dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/tower-dungeon/{Dungeon/DungeonUid}/balance"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end, function(error)
        resultVal = 2
        return true
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.WeekActivity then
    this:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/activity-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.Story or fightDungeonType == PB.enum.DungeonType.Normal or fightDungeonType == PB.enum.DungeonType.Training or fightDungeonType == PB.enum.DungeonType.Challenge then
    this:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/balance"):Post({}, function(response2)
        if response2.chapterSummaryInfo ~= nil and this:GetData("fci/dungeon/chapter-summary") then
          table.merge(this:GetData("fci/dungeon/chapter-summary"), response2.chapterSummaryInfo)
        end
        this:SetData("NoNewChapterUnlocked", #response2.chapterInfo <= 1)
        for i = 1, #response2.chapterInfo do
          local chapterInfo = response2.chapterInfo[i]
          this:SetData("fci/dungeon/chapter/" .. chapterInfo.recordChapter.chapterId, chapterInfo)
        end
        if response2.chapterSummaryInfo ~= nil then
          this:SetData("fci/dungeon/chapter-summary", response2.chapterSummaryInfo)
        end
        this:SetData("DungeonBalance/result", response2.balanceInfo)
        resultVal = response2
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.Gold then
    this:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/gold-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        local deadCount = #fightLuaTable.teamList[2].statistic.deadInfo
        DB:SetData("FightBeatEnemyCount", deadCount)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.Maze then
    this:GameRequest("fci/maze-dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/maze-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.Triggered then
    this:GameRequest("fci/triggered-dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/triggered-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.SemiFixed then
    this:GameRequest("fci/semifixed-dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/semifixed-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  elseif fightDungeonType == PB.enum.DungeonType.AssistGuild or fightDungeonType == PB.enum.DungeonType.ChallengeGuild then
    this:GameRequest("fci/guild-dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      this:GameRequest("fci/guild-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        this:SetData("DungeonBalance/result", response2)
        resultVal = 1
      end)
    end)
  else
    resultVal = 1
  end
end

function ReportTLog()
  TLog.CollectBalanceData()
  TLog.Report()
end
