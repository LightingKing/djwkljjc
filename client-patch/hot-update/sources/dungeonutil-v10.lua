local DU = {}
local WU, DB = require("Common/WindowUtil")()
local PB = require("Common/PbHelper")
local IU = require("Common/ItemUtil")
local AU = require("Common/ActorUtil")
local DBH = require("Manager/DataBindingHandler")
local TLog = require("Common/TlogUtil")
local S = require("Common/Singleton")
local m_configDataManager = S:Get("ConfigDataManager")
local PU = require("Common/PlatformUtil")
local m_activityManager = S:Get("ActivityManager")
local ACU = require("Common/ActivityUtil")

-- BurstLink and TeamPve can be routed through the local REST fight seam by
-- the v10 client patch.  Keep this check local to the module instead of
-- relying on the original global helper (which is not present in every
-- window's Lua environment).
local function IsLocalFight()
  return DB:GetData("LocalFight") == true
      or DB:GetData("BurstLink/LocalFight") == true
      or DB:GetData("LocalBurstLinkFight") == true
      or DB:GetData("TeamPve/LocalFight") == true
end

function DU.IsChapterUnlocked(chapterSummary, chapterId)
  local chapterInfo = PB.get("ChapterInfo", chapterId)
  if chapterInfo == nil then
    return false
  end
  for _, unlockedId in pairs(chapterSummary.unlockedChapterId) do
    if unlockedId == chapterId or unlockedId == chapterInfo.storyChapterId or unlockedId == chapterInfo.trainingId then
      return true
    end
  end
  return false
end

function DU.IsDungeonUnlocked(chapterSummary, dungeonId, callback)
  local chapterId = DU.GetChapterId(dungeonId)
  if DU.IsChapterUnlocked(chapterSummary, chapterId) then
    local info = DB:GetData("fci/dungeon/chapter/" .. chapterId)
    callback(info.recordDungeons[dungeonId] ~= nil)
  else
    callback(false)
  end
end

function DU.IsDungeonFinished(chapterSummary, dungeonId, callback)
  local chapterId = DU.GetChapterId(dungeonId)
  if DU.IsChapterUnlocked(chapterSummary, chapterId) then
    local info = DB:GetData("fci/dungeon/chapter/" .. chapterId)
    local dungeon = info.recordDungeons[dungeonId]
    local finished = false
    if dungeon then
      finished = dungeon.finished
    end
    callback(finished)
  else
    callback(false)
  end
end

function DU.IsChapterFinished(chapterSummary, chapterId)
  for _, finishedId in pairs(chapterSummary.finishedChapterId) do
    if finishedId == chapterId then
      return true
    end
  end
  return false
end

function DU.GetChapterRewards(chapterId)
  local rewards = PB.all("ChapterReward"):where(function(k, v)
    return v.chapterId == chapterId
  end):toarray()
  table.sort(rewards, function(a, b)
    return a.star < b.star
  end)
  return rewards
end

function DU.GetChapterId(dungeonId)
  local all = PB.all("ChapterInfo")
  for _, chapter in pairs(all) do
    if _ENV["!"](chapter.dungeonId):find(function(k, v)
      return v == dungeonId
    end) ~= nil then
      return chapter.chapterId
    end
  end
end

function DU.IsShowStar(dungeonId)
  local dungeonReward = PB.get("DungeonReward", dungeonId)
  if dungeonReward then
    return #dungeonReward.target > 0
  end
  return false
end

function DU.DoesChapterHasStarReward(chapterRecord)
  local rewards = DU.GetChapterRewards(chapterRecord.chapterId)
  for _, reward in pairs(rewards) do
    local hasGot = table.has(chapterRecord.starRewardGot, reward.star)
    if not hasGot and (reward.star == 0 and chapterRecord.finished or reward.star > 0 and chapterRecord.totalStar >= reward.star) then
      return true
    end
  end
  return false
end

function DU.Restart()
  local dungeonId = DB:GetData("WindowDungeon/DungeonId")
  local fightDungeonType = DB:GetData("FightDungeonType")
  
  local function EnterDungeon(result)
    if CS.FightBase.Instance ~= nil then
      DB:SetData("Dungeon/DungeonUid", result.dungeonUid)
      DBH.ResChange(result.resChange)
      WU.SimpleCheckCheat(fightDungeonType)
      WU.SyncActorAttr(fightDungeonType, result.baseInfo)
      CS.FightBase.Instance:LeaveGameLua(tostring(dungeonId))
    end
  end
  
  DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local param = DB:GetData("WindowDungeon/EnterParam")
    if fightDungeonType == PB.enum.DungeonType.Training or fightDungeonType == PB.enum.DungeonType.Challenge or fightDungeonType == PB.enum.DungeonType.Story then
      DB:GameRequest("fci/dungeon/"):Post({dungeonId = dungeonId}, EnterDungeon)
    elseif fightDungeonType == PB.enum.DungeonType.Normal then
      local wireFormatTable = ProtobufT("ApiDungeon", param)
      DB:GameRequest("fci/dungeon/"):Post(wireFormatTable, EnterDungeon)
    elseif fightDungeonType == PB.enum.DungeonType.WeekActivity then
      local wireFormatTable = ProtobufT("ApiActivityDungeon", param)
      DB:GameRequest("fci/activity-dungeon/"):Post(wireFormatTable, EnterDungeon)
    elseif fightDungeonType == PB.enum.DungeonType.Gold then
      local wireFormatTable = ProtobufT("ApiGoldDungeon", param)
      DB:GameRequest("fci/gold-dungeon/"):Post(wireFormatTable, EnterDungeon)
    elseif fightDungeonType == PB.enum.DungeonType.AssistGuild or fightDungeonType == PB.enum.DungeonType.ChallengeGuild then
      local wireFormatTable = ProtobufT("ApiGuildDungeon", param)
      DB:GameRequest("fci/guild-dungeon/"):Post(wireFormatTable, EnterDungeon)
    end
  end)
end

function DU.SendAutoFightSetting()
  local param = {
    configureSkills = DB:GetData("fci/autoFightSetting/")
  }
  DB:GameRequest("fci/auto-fight-conf/"):Post(param, function(result)
    DB:SetData("fci/autoFightSetting/", result.banedSkills)
  end)
end

function EndRecordVideo()
  if PU.IsRecordVideoEnabled() then
    local isRecording = CS.RecordVideoManager.Instance:isRecordingMoments()
    if isRecording then
      CS.RecordVideoManager.Instance:EndMomentsRecording(function(status)
        if status == CS.GameJoyAPI.RecordingStartStatus.Fail then
          warning("Debug", "record end error")
        end
      end)
    end
  end
end

function DU.CreateResetList()
  DU.AddToResetListExitGame("Assistant/Aura", nil)
  DU.AddToResetListExitGame("IsCultivationPVPMode", false)
  DU.AddToResetListExitGame("CultivationPVPFromWelfare", false)
  DU.AddToResetListExitGame("FightActorInfoSynced", false)
  DU.AddToResetListExitGame("Event/ActivityType", nil)
  DU.AddToResetListExitGame("BalanceInfo/Groups", nil)
end

function DU.LeaveDungeon(leaveFrom)
  DU.CreateResetList()
  if leaveFrom == 3 then
    CS.SoundManager.Instance:StopCurrentMusic()
    DU.SendAutoFightSetting()
  end
  if PU.IsRecordVideoEnabled() then
    EndRecordVideo()
    CS.RecordVideoManager.Instance:CancelGenerateMomentsVideo()
    CS.RecordVideoManager.Instance:CloseGenerateMomentsVideoDialog()
  end
  TLog.RecordLeaveDungeon(leaveFrom)
  local eventActivityType = DB:GetData("Event/ActivityType")
  if eventActivityType then
    DB:SetData("Event/ActivityType", nil)
    DB:SetData("Event/ActivityId", nil)
    LeaveEvent(leaveFrom, eventActivityType)
    return
  end
  local dungeonType = DB:GetData("FightDungeonType")
  if dungeonType == PB.enum.DungeonType.BurstLinkPvp then
    LeaveBurstLinkPvp(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.RacePVP then
    LeaveRacePvp(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.TeamPve then
    LeaveTeamPve(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.AsyncPvp then
    LeaveAsyncPvp(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.Tower then
    DU.LeaveTower(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.WeekActivity then
    LeaveWeekActivity(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.Gold then
    LeaveGold(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.Normal then
    local isCultivationPVPModeThisTime = DB:GetData("IsCultivationPVPMode")
    local cultivationPVPFromWelfare = DB:GetData("CultivationPVPFromWelfare")
    LeaveNormal(leaveFrom, isCultivationPVPModeThisTime, cultivationPVPFromWelfare)
  elseif dungeonType == PB.enum.DungeonType.Story then
    LeaveStory(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.Training then
    LeaveTraining(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.Challenge then
    LeaveChallenge(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.Maze then
    LeaveMaze(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.Triggered then
    LeaveTriggered(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.AssistGuild or dungeonType == PB.enum.DungeonType.ChallengeGuild then
    LeaveGuild(leaveFrom)
  elseif dungeonType == PB.enum.DungeonType.SemiFixed then
    LeaveSemiFixed(leaveFrom)
  else
    error("GamePlay", "no dungeonType = " .. dungeonType)
  end
end

function LeaveGame(windowParam, leaveFrom)
  DU.ResetFlagExitGame()
  if leaveFrom == 3 then
    WU.SaveCitySceneContext(windowParam)
    CS.FightBase.Instance:LeaveGameLua("SceneCity")
  else
    WU.ReturnCity(windowParam)
  end
end

function LeaveSemiFixed(leaveFrom)
  DB:GameRequest("fci/semifixed-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    LeaveGame("Welfare, EventCarnival91Stage", leaveFrom)
  end)
end

function LeaveBurstLinkPvp(leaveFrom)
  if leaveFrom == 3 then
    local function balance()
      DB:GameRequest("fci/BurstLinkDunBalance/"):Post({}, function(response)
        DB:SetData("DungeonBalance/result", response)
        CS.FightBase.Instance:LeaveGameLua("SceneBalance")
      end)
    end
    if IsLocalFight() then
      local fightLuaTable = ProtobufT("FightActiveData", CS.FightLuaLogic.Instance.FightLuaTable)
      DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, balance)
    else
      balance()
    end
  else
    LeaveGame("Activity, BurstLinkActorGroup", leaveFrom)
  end
end

function DU.LeavePVPNetDungeon()
  WU.SetLockFlag(WU.LockInputFlag.SceneLoading, true)
  local postData = {
    dungeonId = DB:GetData("Dungeon/DungeonUid")
  }
  DB:WebSocketSendLua(CS.Restifizer.WebSocketTag.FightService, "CsLeaveDungeonReq", postData)
end

function LeaveRacePvp(leaveFrom)
  if leaveFrom == 3 then
    local fightLuaTable = ProtobufT("FightActiveData", CS.FightLuaLogic.Instance.FightLuaTable)
    DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function(response)
      DB:GameRequest("fci/racepvp-dungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response2)
        DB:SetData("DungeonBalance/result", response2)
        CS.FightBase.Instance:LeaveGameLua("SceneBalance")
      end)
    end)
  else
    DB:GameRequest("fci/racepvp-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
      LeaveGame("Activity, RacePVP", leaveFrom)
    end)
  end
end

function LeaveEvent(leaveFrom, activityType)
  DB:GameRequest("fci/event-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local newTriggeredEvent = DB:GetData("TriggeredEvent/New")
    local windowParam = ""
    if activityType == PB.enum.ActivityType.EventDungeon_Autumn2018 then
      windowParam = "Activity, EventAutumnStage"
    elseif activityType == PB.enum.ActivityType.EventDungeon_Autumn2018Hard then
      windowParam = "Activity, EventAutumnStageHard"
    elseif activityType == PB.enum.ActivityType.Carnival91 then
      windowParam = "Welfare, EventCarnival91Stage"
    elseif activityType == PB.enum.ActivityType.Christmas then
      windowParam = "Welfare,EventChristmasStage,EventChristmasStageChapter"
      DB:SetData("WelfareTab", "Christmas")
    else
      windowParam = "Activity, EventActivityStage"
    end
    if newTriggeredEvent ~= nil and leaveFrom == 1 then
      windowParam = "Activity, TriggeredEvent"
    elseif leaveFrom == 2 then
      local leaveInfo = DB:GetData("DungeonBalanceRecommendInfo")
      if leaveInfo and leaveInfo.gotoAble then
        DB:SetData("Actor/SelectedUid", leaveInfo.actor.uid)
        windowParam = windowParam .. ", ActorInfo"
      end
    end
    DB:SetData("EventDungeon/RewardUpdateAble", true)
    LeaveGame(windowParam, leaveFrom)
  end)
end

function LeaveTeamPve(leaveFrom)
  local newTriggeredEvent = DB:GetData("TriggeredEvent/New")
  local windowParam = "Activity, DungeonTeam"
  if newTriggeredEvent ~= nil and leaveFrom == 1 then
    windowParam = "Activity, TriggeredEvent"
  end
  if leaveFrom == 3 and IsLocalFight() then
    local fightLuaTable = ProtobufT("FightActiveData", CS.FightLuaLogic.Instance.FightLuaTable)
    DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function()
      DB:GameRequest("fci/teamdungeon/{Dungeon/DungeonUid}/balance/"):Post({}, function(response)
        DB:SetData("DungeonBalance/result", response)
        CS.FightBase.Instance:LeaveGameLua("SceneBalance")
      end)
    end)
  else
    LeaveGame(windowParam, leaveFrom)
  end
end

function LeaveAsyncPvp(leaveFrom)
  local activityType = DB:GetData("fci/tournament/curActivityType")
  
  local function quit()
    DB:GameRequest("fci/tournament/dungeonquit/"):Post({activityType = activityType}, function(response)
      if response then
        LeaveGame("Activity, AsyncPvp", leaveFrom)
      end
    end)
  end
  
  if leaveFrom == 3 then
    local fightLuaTable = ProtobufT("FightActiveData", CS.FightLuaLogic.Instance.FightLuaTable)
    DB:GameRequest("fci/tournamentdungeon/{Dungeon/DungeonUid}/active/"):Patch(fightLuaTable, function()
      DB:GameRequest("fci/tournament/dungeonbalance/"):Post({activityType = activityType}, quit)
    end)
  else
    quit()
  end
end

function DU.LeaveTower(leaveFrom, windowParam)
  DB:GameRequest("fci/tower-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    if windowParam == nil then
      windowParam = "Activity,Tower"
      if leaveFrom == 3 then
        windowParam = "Activity,Tower,TowerActorGroup"
      end
    end
    LeaveGame(windowParam, leaveFrom)
  end)
end

function LeaveWeekActivity(leaveFrom)
  DB:GameRequest("fci/activity-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local newTriggeredEvent = DB:GetData("TriggeredEvent/New")
    local windowParam = "Activity, ActivityStageChoose" .. DB:GetData("Activity/CurrentType")
    if newTriggeredEvent ~= nil and leaveFrom == 1 then
      windowParam = "Activity, TriggeredEvent"
    end
    if leaveFrom == 3 then
      windowParam = windowParam .. ", WeekActivityPrepare, ActivityActorGroup"
    elseif leaveFrom == 2 then
      local leaveInfo = DB:GetData("DungeonBalanceRecommendInfo")
      if leaveInfo and leaveInfo.gotoAble then
        DB:SetData("Actor/SelectedUid", leaveInfo.actor.uid)
        windowParam = windowParam .. ", ActorInfo"
      end
    end
    LeaveGame(windowParam, leaveFrom)
  end)
end

function LeaveNormal(leaveFrom, isCultivationPVPMode, cultivationPVPFromWelfare)
  DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local newTriggeredEvent = DB:GetData("TriggeredEvent/New")
    local windowParam = "ChapterNormal"
    if leaveFrom == 1 then
      if newTriggeredEvent ~= nil then
        windowParam = "Activity, TriggeredEvent"
      elseif isCultivationPVPMode then
        DB:SetData("WelfareTab", "CultivationPvP")
        windowParam = fif(cultivationPVPFromWelfare, "Welfare", "ChapterNormal, DungeonStory")
      end
    elseif leaveFrom == 2 then
      local leaveInfo = DB:GetData("DungeonBalanceRecommendInfo")
      if leaveInfo and leaveInfo.gotoAble then
        DB:SetData("Actor/SelectedUid", leaveInfo.actor.uid)
        windowParam = "ChapterNormal, ActorInfo"
      end
    elseif leaveFrom == 3 then
      DB:SetData("NoNewChapterUnlocked", true)
      windowParam = "ChapterNormal, DungeonStory, " .. fif(isCultivationPVPMode, "ActorGroupCultivationPVP", "ActorGroup")
    end
    LeaveGame(windowParam, leaveFrom)
  end)
end

function LeaveStory(leaveFrom)
  DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local windows = "ChapterNormal"
    if DB:GetData("Tutorial/ToCity") then
      windows = ""
      DB:SetData("Tutorial/ToCity", false)
    end
    LeaveGame(windows, leaveFrom)
  end)
end

function LeaveTraining(leaveFrom)
  DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local windowParam = "ChapterNormal"
    if leaveFrom == 3 then
      windowParam = "ChapterNormal, DungeonTraining"
    end
    LeaveGame(windowParam, leaveFrom)
  end)
end

function LeaveChallenge(leaveFrom)
  DB:GameRequest("fci/dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local windowParam = "Activity, ChapterChallenge, ChallengeDungeon"
    if leaveFrom == 3 then
      windowParam = "Activity, ChapterChallenge, ChallengeDungeon, DungeonTraining"
    end
    LeaveGame(windowParam, leaveFrom)
  end)
end

function LeaveMaze(leaveFrom)
  DB:GameRequest("fci/maze-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    LeaveGame("Activity, MazeMain", leaveFrom)
  end)
end

function LeaveTriggered(leaveFrom)
  DB:GameRequest("fci/triggered-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    LeaveGame("Activity, TriggeredEvent", leaveFrom)
  end)
end

function LeaveGold(leaveFrom)
  DB:GameRequest("fci/gold-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local windowParam = "Activity, DungeonGold"
    if leaveFrom == 3 then
      windowParam = "Activity, DungeonGold, DungeonGoldActorGroup"
    end
    LeaveGame(windowParam, leaveFrom)
  end)
end

function LeaveGuild(leaveFrom)
  DB:GameRequest("fci/guild-dungeon/{Dungeon/DungeonUid}"):Delete(function(response)
    local windowParam = "Guild, DungeonGuildStage"
    if leaveFrom == 3 then
      windowParam = "Guild, DungeonGuildStage, DungeonGuildPrepare, DungeonGuildActorGroup"
    end
    LeaveGame(windowParam, leaveFrom)
  end)
end

function DU.IsTowerAwardCanGet(towerSummary)
  local floorRewardInfo = PB.all("TowerFloorReward")
  for _, info in pairs(floorRewardInfo) do
    if info.towerFloor <= towerSummary.towerFloorRecord then
      local _, floor = table.find(towerSummary.rewardGot, function(_, v)
        return info.towerFloor == v
      end)
      if floor == nil then
        return true
      end
    end
  end
  return false
end

function DU.GetDungeonBalanceRewards(balance)
  local result = {}
  local expOverFlow = {}
  if not balance or not balance.resChange and not balance.expReturn then
    return _ENV["!"]({})
  else
    if balance and balance.resChange then
      result = IU.FlatInboxItems(balance.resChange):where(function(_, change)
        local target = change.baseRes or change.equip or change.actor
        return target and target.countDelta > 0 and (not target.baseRes or target.baseRes.type ~= PB.enum.ResourceType.ResPlayerExp)
      end):toarray()
    end
    if balance and balance.expReturn then
      expOverFlow = IU.FlatInboxItems(balance.expReturn):where(function(_, change)
        local target = change.baseRes
        return target and target.countDelta > 0 and (not target.baseRes or target.baseRes.type ~= PB.enum.ResourceType.ResPlayerExp)
      end):toarray()
      for i, v in pairs(expOverFlow) do
        v.isExpReturn = 1
        table.insert(result, v)
      end
    end
    return result
  end
end

function DU.GetDungeonBalanceExpOverFlow(balance)
  if balance and balance.expReturn then
    return IU.FlatInboxItems(balance.expReturn):where(function(_, change)
      local target = change.baseRes
      return target and target.countDelta > 0 and (not target.baseRes or target.baseRes.type ~= PB.enum.ResourceType.ResPlayerExp)
    end):toarray()
  else
    return _ENV["!"]({})
  end
end

function DU.CultivationReachExpection(members, m_dungeonId)
  local result = {}
  result.isReach = true
  local groupCul = 0
  local lowestCul, lowestMember
  for i = 1, #members do
    local member = members[i]
    local uid, actor
    if member.actorUid and member.actorUid ~= 0 then
      uid = member.actorUid
    end
    if uid == nil and member.uid ~= nil and member.uid ~= 0 then
      uid = member.uid
    end
    if member.isNpc then
      actor = PB.get("DungeonNPC", member.npcId)
      AU.RenameDungeonNPCFields(actor)
    elseif uid then
      actor = DB:GetData("fci/actor/" .. uid)
    end
    if actor then
      local actorCul = AU.GetActorCultivationDegree(actor)
      groupCul = groupCul + actorCul
      if (lowestCul == nil or lowestCul > actorCul) and actor.uid then
        lowestCul = actorCul
        lowestMember = actor
      end
    end
  end
  local expection = PB.get("DungeonReward", m_dungeonId).expectCulDegree
  if expection ~= nil and groupCul < expection then
    result.isReach = false
    result.suggest = lowestMember
  end
  result.suggest = lowestMember
  return result
end

function DU.ShowChallengeChapterUnlockHint(chapterId)
  local chapterInfo = PB.get("ChapterInfo", chapterId)
  if chapterInfo then
    local conditions = {preChapterFinished = false, challengeStarNumReached = false}
    local chapterSummary = DB:GetData("fci/dungeon/chapter-summary")
    conditions.preChapterFinished = DU.IsChapterFinished(chapterSummary, chapterInfo.preChapterId)
    conditions.challengeStarNumReached = chapterSummary.totalChallengeStar >= chapterInfo.dungeonTypeStar
    local text = ""
    if not conditions.preChapterFinished then
      text = WU.GetString("Training_dungeon_locked_by_chapter", chapterInfo.preChapterId)
    end
    if not conditions.challengeStarNumReached then
      if text ~= "" then
        text = text .. WU.GetString("Challenge_Lock_Conjunction")
      end
      text = text .. WU.GetString("Challenge_Star_Requirement", chapterInfo.dungeonTypeStar)
    end
    if text ~= nil and text ~= "" then
      WU.ShowHintText(text)
    end
  end
end

function DU.IsWaitingForDungeonTeam(callback)
  local state = DB:GetData("DungeonTeam/OnFastJoin")
  if state then
    WU.ShowMessageYesNo(WU.GetString("DungeonTeam_CancelWaitingHint"), function(result)
      if result == "YES" then
        local ui = WU.FindWindow("DungeonTeamFastJoin")
        if ui.gameObject.activeSelf then
          _ENV["$"](ui)["$$CancelWaiting"](callback)
        end
      end
    end)
  end
  return state
end

function DU.CanEnterGoldDungeon()
  local goldDungeonLevelInfo = PB.get("GoldDungeonLevel", DB:GetData("fci/baseinfo").level)
  if goldDungeonLevelInfo then
    local dungeonRewardInfo = PB.get("DungeonReward", goldDungeonLevelInfo.dungeonId)
    if dungeonRewardInfo then
      local cost = dungeonRewardInfo.costWin[1]
      if cost and DB:GetData("fci/item/" .. cost.id) and DB:GetData("fci/item/" .. cost.id).count >= cost.count then
        return true
      end
    end
  end
  return false
end

function DU.StoryChapterToNormal(storyId)
  local chapterInfos = PB.all("ChapterInfo")
  for i, v in ipairs(chapterInfos) do
    if v.storyChapterId == storyId then
      return v.chapterId
    end
  end
end

function DU.DoesEventActivityHasStarReward(rewardSummary, activityId)
  if rewardSummary == nil then
    return
  end
  local summary = rewardSummary[activityId]
  if summary then
    local starCnt = summary.totalStar
    local rewardGotCnt = #summary.rewardGot
    return rewardGotCnt < #PB.all("EventReward"):where(function(k, v)
      return v.activityId == activityId and v.star <= starCnt
    end):toarray()
  end
  return false
end

function DU.GetDungeonGoldLevel()
  local baseInfo = DB:GetData("fci/baseinfo")
  return PB.get("GoldDungeonLevel", baseInfo.level)
end

function DU.GetDungeonGoldCost()
  local goldLevel = DU.GetDungeonGoldLevel()
  local reward = PB.get("DungeonReward", goldLevel.dungeonId)
  if reward and #reward.costWin > 0 then
    return reward.costWin[1]
  end
end

function DU.SetDungeonTeamUrl(url)
  local lastTeamDungeonUrl = DB:GetData("fci/teamdungeonurl/")
  if lastTeamDungeonUrl ~= nil and lastTeamDungeonUrl ~= url then
    CS.Restifizer.RestifizerManager.Instance:StopWebSocket(CS.Restifizer.WebSocketTag.DungeonTeamService)
  end
  DB:SetData("fci/teamdungeonurl/", url)
  CS.Restifizer.RestifizerManager.Instance:SetDungeonTeamWebSocketUrl(url)
end

function DU.IsInDungeonTeamPrepare(callback)
  local dungeonTeamPrepare = WU.FindWindow("DungeonTeamPrepare")
  if dungeonTeamPrepare ~= nil then
    info("DungeonTeam", "dungeonTeamPrepare.gameObject.activeSelf : " .. tostring(dungeonTeamPrepare.gameObject.activeSelf))
  else
    info("DungeonTeam", "dungeonTeamPrepare is nil")
  end
  if (dungeonTeamPrepare == nil or not dungeonTeamPrepare.gameObject.activeSelf) and callback then
    callback()
  else
    WU.ShowHintText(WU.GetString("DungeonTeam_OnJoinDungeonTeam"))
  end
end

function DU.CultivationPVPRatioConvert(members, ratios)
  local reduceInfo = {}
  for i = 1, 6 do
    if members[i].actorUid ~= 0 then
      local ratio = 1000
      if ratios then
        ratio = ratios[members[i].index] * 1000
      end
      reduceInfo[tostring(members[i].actorUid)] = ratio
    end
  end
  return reduceInfo
end

function DU.GetActorChipList()
  local dungeonList = {}
  local allRewards = PB.all("DungeonReward")
  for k, v in pairs(allRewards) do
    if v.chipExchangePlayerPos ~= nil and v.chipExchangePlayerPos > 0 then
      table.insert(dungeonList, {
        dungeonId = v.dungeonId,
        posIndex = v.chipExchangePlayerPos
      })
    end
  end
  return dungeonList
end

function DU.ReplaceActorChip(dungeonId, dropDisplay)
  local chipId, tmpList, index
  local chipList = DU.GetActorChipList()
  local _, dungeon = table.find(chipList, function(key, val)
    return val.dungeonId == dungeonId
  end)
  if dungeon == nil then
    return
  end
  local chipInfo = DB:GetData("fci/chipexchange/")
  local chipId = chipInfo.exchangePosInfo[dungeon.posIndex]
  if chipId and chipId ~= 0 then
    tmpList = _ENV["!"]({})
    _ENV["!"](dropDisplay):copy(tmpList, true)
  else
    tmpList = dropDisplay
  end
  for k, v in pairs(tmpList) do
    local info = PB.get("ItemInfo", v.id)
    if info and info.funcType == PB.enum.ItemType.ActorChip then
      index = k
      if chipId and chipId ~= 0 then
        tmpList[k].id = chipId
      end
    end
  end
  return chipId, tmpList, index
end

function DU.GetCultivationDungeonId(dungeonId)
  if PB.get("DungeonReward", dungeonId).enterTimes ~= 0 then
    return PB.get("ChipDungeonToCultivationDungeon", dungeonId) and PB.get("ChipDungeonToCultivationDungeon", dungeonId).cultivationPVPDungeonId
  else
    return dungeonId
  end
end

function DU.ResetFlagExitGame()
  local flagPar = DB:GetData("ExitGame/ResetFlag")
  if flagPar then
    for k, v in pairs(flagPar) do
      DB:SetData(k, v.resetState)
    end
  end
end

function DU.AddToResetListExitGame(databinding, resetState)
  local parent = fif(DB:GetData("ExitGame/ResetFlag") ~= nil, DB:GetData("ExitGame/ResetFlag"), {})
  if parent[databinding] == nil then
    parent[databinding] = {resetState = resetState}
    DB:SetData("ExitGame/ResetFlag", parent)
  end
end

return DU
