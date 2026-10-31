local U = require("Common/Util")
local WU, DB = require("Common/WindowUtil")()
local PB = require("Common/PbHelper")
local DBH = require("Manager/DataBindingHandler")
local CU = require("Common/ChatUtil")
local TU = require("Common/TaskUtil")
local FU = require("Common/FriendUtil")
local AU = require("Common/ActorUtil")
local RU = require("Common/RedMarkUtil")
local DU = require("Common/DungeonUtil")
local MU = require("Common/MailUtil")
local NU = require("Common/NotepadUtil")
local S = require("Common/Singleton"):Register(_ENV, "RestifizerManager")
local Misc = PB.all("Misc"):first()
local m_accessTokenOld
local m_longpollingPushSeq = 0
local m_timeSyncContext = {
  minInterval = 4,
  maxInterval = 32,
  interval = 8
}
local m_boardFavourInterval, m_boardActorRoleId, m_levelBefore

function Start()
  CS.Restifizer.RestifizerManager.Instance.RequestExecuteInterval = 0.01
  LoadUnlockWindowXlsx()
  this:RegisterGameEvent("OnPreUpdateAccessToken", OnPreUpdateAccessToken)
  this:RegisterGameEvent("HandleRestifizerError", OnHandleRestifizerError)
end

function Update(deltaTime)
  if m_boardFavourInterval ~= nil then
    m_boardFavourInterval = m_boardFavourInterval + deltaTime
    if m_boardFavourInterval > Misc.signboardFavourCycle + 60 then
      m_boardFavourInterval = 0
      DB:HandleRemoteChanged("fci/RoleNotePad/" .. m_boardActorRoleId)
    end
  end
end

function DoEnteringRequests()
  print("Resitifizer", "DoEnteringRequests")
  DB:SyncRemote(DB:GameRequest("fci/ActorGroup/"))
  DB:SyncRemote(DB:GameRequest("fci/friendlist/"):Query("fields", "playerId,name,lastLogoutTime,level,headPhoto,socialPicture,sex,qqvip,socialPlatform,gamecenterLoginTime"):SetPageSize(20):SyncInput(false))
  DB:SyncRemote(DB:GameRequest("fci/blacklist/"):Query("fields", "playerId"):SetPageSize(50):SyncInput(false))
  DB:SyncRemote(DB:GameRequest("fci/asyncpvprecord/"):SyncInput(false))
  DB:SyncRemote(DB:GameRequest("fci/RoleNotePad/"):Query("fields", "roleId,favourLevel,favour,unlockTime,readTag"):SetPageSize(20):SyncInput(false))
  DB:SyncRemote(DB:GameRequest("fci/activity/"))
  DB:SyncRemote(DB:GameRequest("fci/name/"))
  this:GameRequest("fci/auto-fight-conf/"):Get(function(result)
    this:SetData("fci/autoFightSetting/", result.banedSkills)
  end)
  this:GameRequest("fci/mail/"):Get(MU.OnMails)
  this:GameRequest("fci/recentcontact/"):SyncInput(false):Get(OnGetRecentContact)
  this:GameRequest("fci/Signboard/"):Get(function(result)
    this:SetData("BoardActor/RoleId", result.signboard)
    this:SetData("BoardActor/AllUnlocked", result.unlockedSignboardId)
    this:BroadcastGameEvent("BoardActorChanged", result.signboard, result.checkSignboardTime)
  end)
  this:RegisterGameEvent("BoardActorChanged", function(signBoardID, lastChanged)
    local allEntity = PB.all("Signboard")
    for _, v in pairs(allEntity) do
      if v.signboardId == signBoardID then
        m_boardActorRoleId = v.roleId
        break
      end
    end
    m_boardFavourInterval = 0
    if lastChanged then
      m_boardFavourInterval = CS.GameTime.serverUtc - lastChanged
    end
  end)
  this:Bind("fci/mail/", CheckMailRedPoint)
  this:GameRequest("fci/task/"):SyncInput(false):Get(TU.InitData)
  DB:SyncRemote(DB:GameRequest("fci/deploy/mission/"):SyncInput(false))
  this:GameRequest("fci/redpoint/data"):Get(function(data)
    this:SetData("fci/redpoint/data", data)
    this:SetData("fci/medal", data.medalRedpoint and data.medalRedpoint.medal)
    this:SetData("fci/tower/summary", data.towerSummaryResult)
    this:SetData("fci/event-dungeon/star-reward", data.eventDungeonStarRewardGet)
    this:SetData("fci/RacePVPSummary", data.racePvpSummaryResult)
    this:SetData("fci/signin", data.signinResult)
    this:SetData("fci/gachainfo", data.gachaInfoGet and data.gachaInfoGet.gachaInfo)
    this:SetData("fci/Shop", data.shopRedpoint and data.shopRedpoint.shopInfo)
    this:SetData("fci/mall-info", data.mallInfoResult)
    this:SetData("fci/RolePresent", data.rolePresentRedpoint.presents)
    this:SetData("fci/seal", data.sealResult)
    local activityViewedData = {}
    if data.redpoint and data.redpoint.redpointType then
      for type, v1 in pairs(data.redpoint.redpointType) do
        for id, value in pairs(v1.redpointId) do
          local activityType, activityId = tonumber(type), tonumber(id)
          if activityType ~= nil and activityId ~= nil then
            activityViewedData[activityType] = activityViewedData[activityType] or {}
            activityViewedData[activityType][activityId] = value
          end
        end
      end
    end
    this:SetData("activityViewed", activityViewedData)
    RU.SetRedMark("Journey/Achievement", data.achievementSummaryGet.canReward)
    local achievementPoint = this:GetData("fci/resource/ResAchievementPoint_0") or 0
    local levelName, curTrophy = AU.GetAchievementInfo(math.max(0, achievementPoint))
    this:SetData("AchievementLevelName", levelName)
    this:SetData("AchievementIconName", "cup_icon_" .. curTrophy)
    RU.SetRedMark("Relation/Friend/RelationPoint", data.relationPointRedpoint)
    this:SetData("DungeonGuild/Summary", data.guildDungeonSummary)
    this:SetData("fci/guild-dungeon/reward", data.guildDungeonScoreReward)
    this:SetData("fci/guild-dungeon/progress-reward", data.guildDungeonProgressReward)
  end)
  this:Bind("fci/friendlist/", function(friends)
    if friends ~= nil then
      local chatFriends = this:GetData("ChatRoom/Friends") or {}
      local newCountTable = {}
      for i = 1, #chatFriends do
        local c = chatFriends[i]
        if c.newCount ~= nil and c.newCount > 0 then
          newCountTable[c.senderId] = c.newCount
        end
      end
      local chatFriends = {}
      for i = 1, #friends do
        local playerId = friends[i].playerId
        table.insert(chatFriends, {
          senderId = playerId,
          sender = friends[i].name,
          newCount = newCountTable[playerId] or 0
        })
      end
      this:SetData("ChatRoom/Friends", chatFriends)
    end
  end)
  this:Bind("fci/baseinfo/", function(result)
    if result ~= nil then
      this:SetData("PlayerFightMedal", result.fightMedal)
      this:SetData("playerSex", result.sex)
      this:SetData("PlayerHeadPhoto", result.headPhoto)
      if m_levelBefore == nil then
        m_levelBefore = result.level
      elseif m_levelBefore < result.level then
        TU.CheckTaskReward(this:GetData("fci/task/"))
        this:BroadcastGameEvent("OnPlayerLevelup", m_levelBefore, result.level)
        m_levelBefore = result.level
      end
    end
  end)
  this:Bind("fci/actor/", function(actors)
    if actors ~= nil then
      local groupactorUids = this:GetData("GroupActorUids")
      if groupactorUids == nil then
        groupactorUids = {}
        for i = 1, #actors do
          for key, val in pairs(actors[i].groupTag) do
            local rec = groupactorUids[actors[i].uid]
            groupactorUids[actors[i].uid] = (rec or 0) | 1 << tonumber(key) - 1
          end
        end
        groupactorUids[0] = 0
        this:SetData("GroupActorUids", groupactorUids)
      end
    end
  end)
  this:GameRequest("fci/temp-entrance-switch"):Get(OnTempEntranceSwitch)
  this:GameRequest("fci/equip/custom-match"):Get(OnEquipCustomMatchChanged)
end

function OnEquipCustomMatchChanged(result)
  for index, match in pairs(result.equipCustomMatch) do
    this:SetData("Equip/Match/" .. index, match)
  end
end

function OnTempEntranceSwitch(resp)
  local tempEntranceData = {}
  local accountSwitch = this:GetData("AccountSwitch")
  local switchInfo = resp.switchInfo
  local timeNow = CS.GameTime.serverUtc
  for i = 1, #switchInfo do
    local switch = switchInfo[i]
    if switch.isWindowEntrance then
      switch.switch = switch.switch ~= 0 and IsTimeInOpenSwitch(switch, timeNow)
      tempEntranceData[switch.type] = switch
    elseif switch.type == PB.enum.TempEntranceSwitchType.TssOpen then
      this:SetData("TssReportInterval", switch.switch)
    else
      local key = PB.enum.TempEntranceSwitchType.__keys[switch.type]
      if key then
        accountSwitch[key] = switch.switch ~= 0 and IsTimeInOpenSwitch(switch, timeNow)
      end
    end
  end
  this:SetData("fci/tempEntranceSwitchInfo", tempEntranceData)
  this:SetData("AccountSwitch", accountSwitch)
end

function IsTimeInOpenSwitch(switch, timeNow)
  local startTime, stopTime
  if switch.stopTime ~= "" then
    stopTime = CS.GameTime.ServerTimeStrToUtc(switch.stopTime)
  else
    stopTime = 9999999999
  end
  if switch.startTime ~= "" then
    startTime = CS.GameTime.ServerTimeStrToUtc(switch.startTime)
  else
    startTime = 0
  end
  if timeNow <= stopTime and timeNow > startTime then
    return true
  else
    return false
  end
end

function OnPreUpdateAccessToken()
  m_accessTokenOld = this:GetAccessToken()
end

function OnGetRecentContact(resp)
  local targets = {}
  for i = 1, #resp do
    resp[i].newCount = 0
    table.insert(targets, resp[i])
  end
  this:SetData("LatestChatTargets", targets)
end

function LoadUnlockWindowXlsx()
  local resPlayerLevelInitConfig = PB.all("PlayerLevelInitConfig")
  local data = {}
  for i = 1, #resPlayerLevelInitConfig do
    local row = resPlayerLevelInitConfig[i]
    for j = 1, #row.unlockWindow do
      if row.unlockWindow[j] ~= nil and row.unlockWindow[j] ~= 0 then
        data[row.unlockWindow[j]] = row.level
      end
    end
  end
  this:SetData("UnlockWindowData", data)
end

function CheckMailRedPoint(result)
  if result then
    for i = 1, #result do
      if not (not (#result[i].attachments > 0) or result[i].attachmentsGot) or result[i].read == false and #result[i].attachments == 0 then
        RU.SetRedMark("Mail", true)
        return
      end
    end
    RU.SetRedMark("Mail", false)
  end
end

function OnHandleRestifizerError(status, errorRaw)
  warning("Debug", "OnHandleRestifizerError. status: " .. status .. ", errorRaw: " .. JsonE(errorRaw))
  if status == 401 then
    -- An expired/revoked access token must return to the login window without
    -- ResetGame.  Keep the session cache clear so cold-start auto-login cannot
    -- immediately replay the invalid token.
    this:SetData("accountCache", {})
    this:SetData("accountAuth", nil)
    this:BroadcastGameEvent("NetworkErrorNotResetGame")
    return true
  end
  if errorRaw.error then
    if errorRaw.error.code == "Error_ActivityNotStart" then
      return
    end
    WU.ShowMessageOK(WU.GetString(errorRaw.error.code), function()
      CS.GameGlobal.Instance:ResetGame()
    end)
  elseif errorRaw.notify then
    local message = errorRaw.notify.banReason
    if message == "Error_ActivityNotStart" then
      return
    end
    if message ~= nil and message ~= "" then
      message = message .. "\n" .. WU.GetString("Error_UnBanTime", WU.RenderTime2(errorRaw.notify.banTime, false, false, true))
    else
      message = WU.GetString(errorRaw.notify.code)
    end
    if status == 507 then
      WU.ShowMessageOK(message)
    elseif status == 509 then
      WU.ShowHintText(message, 2)
    end
  elseif errorRaw.kick then
    if errorRaw.kick.code == "Error_DbError" then
      CS.Restifizer.RestifizerManager.Instance.StopRequestTime = 20
      WU.ShowMessageOK(WU.GetString(errorRaw.kick.code))
    else
      WU.ShowMessageOK(WU.GetString(errorRaw.kick.code), function()
        CS.GameGlobal.Instance:ResetGame()
      end)
    end
  else
    WU.ShowMessageOK(WU.GetString("Window_FatalError"), function()
      CS.GameGlobal.Instance:ResetGame()
    end)
  end
end
