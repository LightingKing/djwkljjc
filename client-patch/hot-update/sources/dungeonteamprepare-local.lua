local SortFocusBase = inherit("Window/SortBase/SortFocusBase", _ENV)
local PB = require("Common/PbHelper")
local WU, DB, REF = require("Common/WindowUtil")(this)
local HU = require("Common/HtmlUtil")
local AU = require("Common/ActorUtil")
local LU = require("Common/ListUtil")
local CU = require("Common/ChatUtil")
local U = require("Common/Util")
local ACU = require("Common/ActivityUtil")
local DBH = require("Manager/DataBindingHandler")
local SU = require("Common/SortUtil")
local SpeechTemplateCount = 7
local CloseCheckInterval = 10
local ChatInterval = PB.get("ChatChannel", PB.enum.EChatChannel.Group).interval
local m_closeCheck = 0
local m_applyHasProcessed, m_roomInfo, m_mainUid, m_subUid, m_resetActorSelect, m_displayMode, m_actorType, m_displayActorTable, m_displayAttrs, m_displayNumbers, m_advancedFilters, m_currentActorUid, m_isReady, m_showFastSpeech, m_showButton, m_allowFastJoin, m_cooldown, m_localAi, m_localBaseInfo
local m_stateChange = true
local m_backHome, m_fight
local m_chatInterval = ChatInterval
local m_linksActorId, m_activityList, m_activityId, m_iAmLeader, m_dungeonId, m_blockMemberChange, m_onLeaving

function SendTeamSocket(name, payload)
  if m_localAi then
    return
  end
  this:WebSocketSendLua(CS.Restifizer.WebSocketTag.DungeonTeamService, name, payload)
end

function SetupWindow()
  LU.Bind(REF.SpeechWrapContent, {
    updateRow = function(rowRef, wrapIndex, rowIndex)
      WU.ToggleRendering(rowRef["$"], rowIndex ~= nil)
      if rowIndex ~= nil and rowIndex < SpeechTemplateCount then
        local text = WU.GetString("DTChatTemplate_" .. rowIndex + 1)
        rowRef.SpeechTemplate.UILabel.text = text
      end
    end
  })
  WU.TraverseChildren(REF.SpeechWrapContent, function(go)
    WU.BindButtonEvent(go, function()
      if m_chatInterval < ChatInterval then
        WU.ShowHintText(WU.GetString("Error_ChatSpeakTooFast"))
        return
      end
      m_chatInterval = 0
      local text = _ENV["$"](go).SpeechTemplate.UILabel.text
      OnGroupChatMsg(text, true)
    end)
  end)
  WU.BindButtonEvent(REF.FastEnter, function()
    if m_stateChange then
      m_stateChange = false
      SendTeamSocket("CsTeamDungeonRoomAllowFastJoin", {
        isAllow = not m_allowFastJoin
      })
    end
  end)
  WU.BindButtonEvent(REF.Fight, function()
    if m_isReady then
      StartFightWebSocket()
    else
      m_fight = true
      OnRequestReady(true)
    end
  end)
  WU.BindButtonEvent(REF.Remove, function()
    m_subUid = fif(m_actorType == PB.enum.ActorType.Sub, 0, m_subUid)
    m_mainUid = fif(m_actorType == PB.enum.ActorType.Sub, m_mainUid, 0)
    m_currentActorUid = 0
    RefreshWrapContent()
    RefreshActors()
  end)
  WU.BindButtonEvent(REF.TeamDungeonDetail, function()
    if m_roomInfo then
      WU.AcquireWindowAsync("DungeonDetail", function(ui)
        _ENV["$"](ui)["$$SetData"](m_dungeonId, m_activityId, m_roomInfo.roomLevelLimit)
      end)
    end
  end)
  WU.BindButtonEvent(REF.TeamSpeech, function()
    RefreshSpeechDisplay(not m_showFastSpeech)
  end)
  WU.BindButtonEvent(REF.SpeechMask, function()
    RefreshSpeechDisplay(false)
  end)
  CS.EventDelegate.Add(REF.ToggleAttrs.UIToggle.onChange, function()
    m_displayAttrs = CS.UIToggle.current.value
    RefreshAttrDisplay()
  end)
  CS.EventDelegate.Add(REF.ActorSelect.UIToggle.onChange, function()
    if REF.ActorSelect.gameObject.activeSelf and CS.UIToggle.current.value then
      local sortLocalContext = WU.GetGameDataCache("SortLocalContext")
      if sortLocalContext ~= nil then
        local contextName = REF.ActorSelect.SortMate.contextName
        local context = sortLocalContext[contextName] or {}
        context.contextName = contextName
        context.kind = 0
        context.tags1 = 0
        context.tags2 = 0
        sortLocalContext[contextName] = context
        this:BroadcastGameEvent("ApplySortAlternative", 0, true, context)
      end
    end
  end)
  WU.BindButtonEvent(REF.InviteFriend, function()
    if m_roomInfo then
      WU.AcquireWindowAsync("DungeonTeamInvite", function(ui)
        _ENV["$"](ui)["$$InitData"](PB.enum.TeamDungeonInviteFrom.Friend, m_roomInfo.roomLevelLimit)
      end)
    end
  end)
  WU.BindButtonEvent(REF.InviteGuild, function()
    if m_roomInfo then
      local guildId = this:GetData("guildId")
      if guildId == 0 then
        WU.ShowHintText(WU.GetString("DungeonTeam_NoGuild"))
        return
      end
      WU.AcquireWindowAsync("DungeonTeamInvite", function(ui)
        _ENV["$"](ui)["$$InitData"](PB.enum.TeamDungeonInviteFrom.Guild, m_roomInfo.roomLevelLimit)
      end)
    end
  end)
  WU.BindButtonEvent(REF.Confirm, function()
    SendTeamSocket("CsTeamDungeonSelectActor", {
      actorUids = {
        m_mainUid or 0,
        m_subUid or 0
      }
    })
    REF.RoomState.UIToggle.value = true
    m_showButton = true
    RefreshShowCustom()
    ClearSlotSelection()
    if m_mainUid == 0 or m_subUid == 0 then
      WU.ShowHintText(WU.GetString("DungeonTeam_ActorGroupNotFull"))
    end
  end)
  LU.Bind(REF.ActorSelectWidgetGrid, {
    updateRow = UpdateRow,
    delta = 0.01
  })
end

function InitWindow()
  SortFocusBase.InitWindowActor()
  this:SetData("ChatRoom/ChatContentGroup", nil)
  this:SetData("ChatRoom/ChatContentGroupUpdate", nil)
  this:SetData("DungeonTeam/PrepareCache", nil)
  this:SetData("DungeonTeamInviteHint/Hide", true)
  m_cooldown = {}
  this:SetData("DungeonTeamInvite/Cooldown", m_cooldown)
  m_fight = false
  m_backHome = false
  m_localAi = this:GetData("DungeonTeam/LocalAi") == true
  m_localBaseInfo = nil
  m_displayAttrs = false
  m_mainUid = nil
  m_subUid = nil
  m_currentActorUid = nil
  m_isReady = false
  m_dungeonId = nil
  m_activityId = nil
  m_roomInfo = nil
  m_displayMode = nil
  m_actorType = nil
  m_onLeaving = false
  RefreshAttrDisplay()
  RefreshSpeechDisplay(false)
  OnFastEnterChanged({isAllow = false})
  REF.Content.gameObject:SetActive(false)
  REF.Center.gameObject:SetActive(false)
  CS.Restifizer.RestifizerManager.Instance:RegisterWebSocketKickCallback(CS.Restifizer.WebSocketTag.DungeonTeamService, OnWebSocketKick)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScClientError", onErrorCode)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonAllActors", OnTeamAllActors)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScLeaveTeamDungeonRoom", function()
    OnLeaveTeamDungeonRoom()
  end)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonMemberChange", OnMemberChange)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonPosChgApply", OnPositionChangeApply)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonActorChange", OnTeamActorChange)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonKick", function()
    OnLeaveTeamDungeonRoom(WU.GetString("DungeonTeam_YouAreKicked"))
  end)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonChat", OnGroupChatMsgReceive)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonStart", OnTeamDungeonStart)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScTeamDungeonRoomAllowFastJoin", OnFastEnterChanged)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScCreateTeamdungeonRoom", OnTeamDungeonRoom)
  this:RegisterWebSocketLuaListener(CS.Restifizer.WebSocketTag.DungeonTeamService, "ScJoinTeamDungeonRoom", OnTeamDungeonRoom)
  this:RegisterGameEvent("DungeonTeamInvite", Invite)
  this:RegisterGameEvent("DungeonTeamPositionChange", OnRequestPositionChange)
  this:RegisterGameEvent("DungeonTeamKickOut", OnRequestKickOut)
  this:RegisterGameEvent("DungeonTeamReady", OnRequestReady)
  this:RegisterGameEvent("DungeonTeamGroupChatMsg", OnGroupChatMsg)
  this:Bind("SortFinished", OnSortFinished)
  this:Bind("ActivityList", function(list)
    if list ~= nil then
      m_activityList = list
    end
  end)
end

function InitBackAndHomeCallback()
  local nav = WU.FindWindow("Navigation")
  
  local function ForceLeave()
    this:DelayInvokeInSeconds(2, function()
      if m_roomInfo then
        SendTeamSocket("CsLeaveTeamDungeonRoom", {
          roomId = m_roomInfo.roomId
        })
      end
      OnLeaveTeamDungeonRoom()
    end)
  end
  
  _ENV["$"](nav)["$$Save"]()
  _ENV["$"](nav)["$$SetBackCallback"](function()
    local top = WU.TopWindow()
    if top and top.name == "DungeonTeamPrepare" then
      if m_roomInfo then
        m_backHome = false
        SendTeamSocket("CsLeaveTeamDungeonRoom", {
          roomId = m_roomInfo.roomId
        })
      end
      ForceLeave()
    else
      this:BroadcastGameEvent("OnNavigation", "Back")
    end
  end)
  _ENV["$"](nav)["$$SetHomeCallback"](function()
    if m_roomInfo then
      m_backHome = true
      SendTeamSocket("CsLeaveTeamDungeonRoom", {
        roomId = m_roomInfo.roomId
      })
      ForceLeave()
    end
  end)
end

function OnTeamDungeonStart(message)
  m_blockMemberChange = true
  info("DungeonTeam", table.dump(message))
  if m_localAi then
    local balanceInfo = _ENV["!"]({})
    for index, playerId in ipairs(m_roomInfo.memberList or {}) do
      local info = (m_roomInfo.actorInfo or {})[playerId] or {}
      balanceInfo[playerId] = {
        playerInfo = info.playerInfo or {playerId = playerId, name = playerId, level = 1},
        actors = info.actors or {},
        position = index - 1
      }
    end
    this:SetData("DungeonTeam/BalanceInfo", balanceInfo)
    this:SetData("DungeonTeam/LocalAi", true)
    -- SceneFight/HelpLogic use these markers to stay on the local Lua
    -- combat path and avoid the retired FightService websocket.
    this:SetData("LocalFight", true)
    this:SetData("TeamPveLocal", true)
    WU.EnterGameLevel(message.dungeonUid, message.baseInfo, m_dungeonId, PB.enum.DungeonType.TeamPve, 1, false)
    return
  end
  if not table.empty(message.costNotEnoughPlayers or {}) then
    local playerNames = ""
    for i = 1, #message.costNotEnoughPlayers do
      playerNames = playerNames .. message.costNotEnoughPlayers[i]
      if i < #message.costNotEnoughPlayers then
        playerNames = playerNames .. ","
      end
    end
    WU.ShowHintText(WU.GetString("DungeonTeam_CostNotEnough", playerNames))
    return
  end
  local balanceInfo = _ENV["!"]({})
  local cachedPlayerInfo = this:GetData("DungeonTeam/CachePlayerInfo")
  for index, playerId in pairs(m_roomInfo.memberList) do
    if U.trim(playerId) ~= "" then
      local playerInfo = cachedPlayerInfo[playerId]
      balanceInfo[playerId] = {
        playerInfo = playerInfo,
        actors = m_roomInfo.actorInfo[playerId].actors,
        position = index - 1
      }
    end
  end
  this:SetData("DungeonTeam/BalanceInfo", balanceInfo)
  info("DungeonTeam", table.dump(balanceInfo))
  CS.Restifizer.RestifizerManager.Instance:StopWebSocket(CS.Restifizer.WebSocketTag.DungeonTeamService)
  CS.Restifizer.RestifizerManager.Instance:SetFightWebSocketUrl(message.fightUrl)
  WU.EnterGameLevel(message.dungeonUid, nil, message.dungeonId, PB.enum.DungeonType.TeamPve, 1, false)
end

function CreateRoom(groupId, level, activityId, dungeonId)
  m_dungeonId = tonumber(dungeonId)
  m_activityId = tonumber(activityId)
  REF.HintCreateOrJoinSprite.gameObject:SetActive(true)
  if m_localAi then
    this:GameRequest("fci/teamdungeon/enter/"):Post({
      level = level,
      dungeonGroup = groupId,
      activityId = m_activityId,
      dungeonId = m_dungeonId
    }, function(result)
      m_localBaseInfo = result.baseInfo
      m_roomInfo = result.roomInfo
      OnTeamDungeonRoom(result)
    end)
    return
  end
  SendTeamSocket("CsCreateTeamdungeonRoom", {level = level, dungeonGroup = groupId})
  CommonRoomFunc()
end

function JoinRoom(roomId, activityId, dungeonId)
  m_dungeonId = tonumber(dungeonId)
  m_activityId = tonumber(activityId)
  REF.HintCreateOrJoinSprite.gameObject:SetActive(true)
  SendTeamSocket("CsJoinTeamDungeonRoom", {roomId = roomId})
  CommonRoomFunc(roomId)
end

function FastJoinRoom(activityId, dungeonId)
  m_dungeonId = tonumber(dungeonId)
  m_activityId = tonumber(activityId)
  REF.HintCreateOrJoinSprite.gameObject:SetActive(true)
  SendTeamSocket("CsFastJoinTeamDungeonRoom", {})
  CommonRoomFunc()
end

function CommonRoomFunc(roomId)
  SendTeamSocket("CsTeamDungeonAllActors", {roomId = roomId})
  WU.AcquireWindowAsync("DungeonTeamMsg")
end

function UninitWindow()
  WU.RecycleWindow("DungeonTeamMsg")
  this:BroadcastGameEvent("DungeonTeamMsgClose")
  this:SetData("DungeonTeamInviteHint/Hide", nil)
  this:SetData("DungeonTeamInvite/Cooldown", nil)
  this:SetData("ChatRoom/ShowChatGroup", false)
  local nav = WU.FindWindow("Navigation")
  if nav ~= nil then
    _ENV["$"](nav)["$$Load"]()
  end
  if CS.Restifizer.RestifizerManager.Instance ~= nil then
    CS.Restifizer.RestifizerManager.Instance:StopWebSocket(CS.Restifizer.WebSocketTag.DungeonTeamService)
    CS.Restifizer.RestifizerManager.Instance:StopWebSocket(CS.Restifizer.WebSocketTag.DungeonTeamService)
  end
end

function UpdateWindow(delta)
  for k, v in pairs(m_cooldown) do
    m_cooldown[k] = v - delta
  end
  m_chatInterval = m_chatInterval + delta
  m_closeCheck = m_closeCheck + delta
  if m_closeCheck > CloseCheckInterval and m_activityList ~= nil and m_activityId ~= nil then
    local _, target = table.find(m_activityList, function(_, v)
      return v.activityId == m_activityId
    end)
    if not ACU.IsOpenForDoing(target) and m_roomInfo ~= nil then
      WU.ShowHintText(WU.GetString("DungeonTeam_ActivityEnded"))
      SendTeamSocket("CsLeaveTeamDungeonRoom", {
        roomId = m_roomInfo.roomId
      })
    end
  end
end

function onErrorCode(error)
  if error.isBox then
    WU.ShowMessageOK(WU.GetString(error.code))
  else
    WU.ShowHintText(WU.GetString(error.code))
  end
  if error.isReset then
    OnLeaveTeamDungeonRoom()
  end
end

function OnWebSocketKick(webSocket)
  local message = ""
  if webSocket.BanReason ~= nil and webSocket.BanReason ~= "" then
    message = webSocket.BanReason .. "\n" .. WU.GetString("Error_UnBanTime", WU.RenderTime2(webSocket.BanTime, false, false, true))
  else
    message = WU.GetString(PB.enum.ErrorMessageBox.__keys[webSocket.DisconnectReason])
  end
  WU.ShowMessageOK(message, function()
    OnLeaveTeamDungeonRoom()
  end)
end

function OnLeaveTeamDungeonRoom(hint)
  if m_onLeaving then
    return
  end
  m_onLeaving = true
  if CS.Restifizer.RestifizerManager.Instance ~= nil then
    CS.Restifizer.RestifizerManager.Instance:StopWebSocket(CS.Restifizer.WebSocketTag.DungeonTeamService)
  end
  info("DungeonTeam", "OnLeaveTeamDungeonRoom with hint : " .. tostring(hint))
  m_roomInfo = nil
  if m_backHome then
    this:BroadcastGameEvent("OnNavigation", "City")
  else
    this:SetData("DungeonTeam/RoomInfo", nil)
    local windowName = this:GetData("NavigationContext")
    while windowName ~= "DungeonTeamPrepare" do
      WU.RecycleWindow(windowName)
      windowName = this:GetData("NavigationContext")
    end
    WU.RecycleWindow(this)
  end
  if hint ~= nil then
    WU.ShowHintText(hint)
  end
end

function OnTeamDungeonRoom(message)
  WU.RecycleWindow("DungeonTeamFastJoin")
  m_roomInfo = message.roomInfo
  info("DungeonTeam", table.dump(m_roomInfo))
  OnFastEnterChanged({
    isAllow = m_roomInfo.allowFastJoin
  })
  this:SetData("DungeonTeam/RoomInfo", m_roomInfo)
  REF.Content.gameObject:SetActive(true)
  this:SetData("ChatRoom/ShowChatGroup", true)
  REF.RoomState.UIToggle.value = true
  REF["$Animator"]:Play("DelayOpen", -1, 0)
  m_showButton = true
  SetDungeonTeamMember()
end

function Invite(targetIds, type)
  SendTeamSocket("CsTeamDungeonInvitePlayer", {targetIdList = targetIds, inviteFromType = type})
end

function IsPositionChanged(memberList)
  local changedPosition = {}
  for k, v in pairs(memberList) do
    if v ~= m_roomInfo.memberList[k] then
      table.insert(changedPosition, k)
    end
  end
  if not table.empty(changedPosition) then
    this:BroadcastGameEvent("PositionChanged", changedPosition)
  end
end

function OnMemberChange(message)
  if m_blockMemberChange then
    return
  end
  m_roomInfo.masterId = message.masterId
  if m_iAmLeader == false and this:GetData("playerId") == m_roomInfo.masterId then
    WU.ShowHintText(WU.GetString("DungeonTeam_NewLeader"))
  end
  IsPositionChanged(message.memberList)
  m_roomInfo.memberList = message.memberList
  this:BroadcastGameEvent("InGroupCheck")
  m_roomInfo.memberReadyState = message.memberReadyState
  if m_roomInfo.actorInfo ~= nil then
    for playerId, _ in pairs(m_roomInfo.actorInfo) do
      local find = table.find(m_roomInfo.memberList, function(_, v)
        return v == playerId
      end)
      if not find then
        m_roomInfo.actorInfo[playerId] = {}
      end
    end
    info("DungeonTeam", table.dump(message))
    SetDungeonTeamMember()
  end
  if m_fight and CheckAllowFight() then
    StartFightWebSocket()
  end
  m_fight = false
end

function SetDungeonTeamMember()
  m_iAmLeader = this:GetData("playerId") == m_roomInfo.masterId
  m_isReady = m_roomInfo.memberReadyState[this:GetData("playerId")]
  this:SetData("DungeonTeam/IAmReady", m_isReady)
  for i = 0, 2 do
    local playerId = m_roomInfo.memberList[i + 1]
    local isReady = m_roomInfo.memberReadyState[playerId] or false
    local ref = _ENV["$"](REF.MemberGrid[i]["$transform"]:GetChild(0))
    ref["@playerId"] = playerId
    ref["@position"] = i
    ref["$$SetPlayer"](playerId, m_iAmLeader, playerId == m_roomInfo.masterId, isReady)
  end
  if m_roomInfo.actorInfo ~= nil then
    local meRef
    for i = 0, 2 do
      local playerId = m_roomInfo.memberList[i + 1]
      local ref = _ENV["$"](REF.MemberGrid[i]["$transform"]:GetChild(0))
      local playerActorInfo = m_roomInfo.actorInfo[playerId] or {}
      local actors = playerActorInfo.actors or {}
      local isMe = this:GetData("playerId") == playerId
      local callback = fif(isMe, OnSlotSelected, nil)
      for i = 1, #actors do
        DBH.AppendActorInfo(actors[i], not isMe)
      end
      ref["$$SetActors"](actors, callback, not isMe)
      if ref["@playerId"] == this:GetData("playerId") then
        meRef = ref
      end
    end
    RefreshShowCustom()
    if REF.ActorSelect.gameObject.activeSelf then
      if m_displayMode == 1 then
        OnSlotSelected(meRef.DragDropNodeMain.gameObject)
      else
        OnSlotSelected(meRef.DragDropNodeSub.gameObject)
      end
    end
  end
  REF.TeamLeaderState.gameObject:SetActive(m_iAmLeader)
  REF.TeamMemberState.gameObject:SetActive(not m_iAmLeader)
end

function OnRequestPositionChange(position)
  SendTeamSocket("CsTeamDungeonPosChgApply", {targetPos = position})
end

function OnRequestKickOut(targetId)
  SendTeamSocket("CsTeamDungeonKickMember", {targetId = targetId})
end

function OnPositionChangeApply(message)
  if message.applyerPlayerId ~= this:GetData("playerId") then
    m_applyHasProcessed = false
    
    local function Process(flag)
      m_applyHasProcessed = true
      SendTeamSocket("CsTeamDungeonPosChgProcess", {
        applyerPlayerId = message.applyerPlayerId,
        isAgree = flag
      })
    end
    
    local position = 0
    for i = 0, 2 do
      local ref = _ENV["$"](REF.MemberGrid[i]["$transform"]:GetChild(0))
      if ref["@playerId"] == message.applyerPlayerId then
        position = i
        break
      end
    end
    WU.ShowMessageYesNo(WU.GetString("DungeonTeam_PositionChangeApply", WU.GetString("DungeonTeam_Position" .. position)), function(result)
      Process(result == "YES")
    end)
    this:DelayInvokeInSeconds(tonumber(message.lockInterval), function()
      if m_applyHasProcessed then
        return
      end
      Process(false)
      WU.RecycleWindow("MessageBox")
    end)
  else
    WU.ShowHintText(WU.GetString("DungeonTeam_PositionChangeApplySend"))
  end
end

function OnSlotSelected(go)
  if m_isReady then
    return
  end
  REF.ActorSelect.UIToggle.value = true
  m_showButton = false
  ClearSlotSelection()
  local ref = _ENV["$"](go)
  ref.WidgetActorCard["$SetHighlighted"](true)
  m_currentActorUid = ref["@uid"]
  
  local function GetLinksActorId(actor)
    if actor ~= nil then
      local rtn = {}
      local activeLinks = AU.GetActorLinks(actor, DB:GetData("fci/actor/"))
      for i = 1, #activeLinks do
        local row = activeLinks[i]
        rtn[row.link[1].actorID] = true
        rtn[row.link[2].actorID] = true
      end
      return rtn
    end
  end
  
  WU.ToggleRendering(REF.Remove, ref["@uid"] ~= nil and ref["@uid"] > 0)
  local mode = 2
  if ref["$transform"].name == "DragDropNodeMain" then
    mode = 1
  end
  if mode == 1 then
    m_linksActorId = GetLinksActorId(this:GetData("fci/actor/" .. (m_subUid or "")))
    m_mainUid = ref["@uid"]
  else
    m_linksActorId = GetLinksActorId(this:GetData("fci/actor/" .. (m_mainUid or "")))
    m_subUid = ref["@uid"]
  end
  RefreshWrapContent(mode)
end

function RefreshWrapContent(displayMode)
  if displayMode ~= nil then
    m_resetActorSelect = m_displayMode ~= displayMode
    m_displayMode = displayMode
  end
  if m_displayMode == 1 then
    if m_actorType ~= PB.enum.ActorType.Main then
      m_actorType = PB.enum.ActorType.Main
      this:Unbind("DungeonTeamPrepare/Actor/Sub/", InitSubList)
      this:Bind("DungeonTeamPrepare/Actor/Main/", InitMainList)
    else
      InitMainList(this:GetData("DungeonTeamPrepare/Actor/Main/"))
    end
  elseif m_displayMode == 2 then
    if m_actorType ~= PB.enum.ActorType.Sub then
      m_actorType = PB.enum.ActorType.Sub
      this:Unbind("DungeonTeamPrepare/Actor/Main/", InitMainList)
      this:Bind("DungeonTeamPrepare/Actor/Sub/", InitSubList)
    else
      InitSubList(this:GetData("DungeonTeamPrepare/Actor/Sub/"))
    end
  end
end

function InitMainList(result)
  if result then
    m_displayActorTable = _ENV["!"]({})
    _ENV["!"](result):copy(m_displayActorTable)
    RemoveActorsInGroup(m_displayActorTable)
    local resetPosition = true
    if m_resetActorSelect == false then
      resetPosition = false
    end
    LU.Set(REF.ActorSelectWidgetGrid, #m_displayActorTable, resetPosition, not resetPosition)
    RefreshShowCustom()
  end
end

function InitSubList(result)
  if result then
    m_displayActorTable = _ENV["!"]({})
    _ENV["!"](result):copy(m_displayActorTable)
    RemoveActorsInGroup(m_displayActorTable)
    local resetPosition = true
    if m_resetActorSelect == false then
      resetPosition = false
    end
    LU.Set(REF.ActorSelectWidgetGrid, #m_displayActorTable, resetPosition, not resetPosition)
    RefreshShowCustom()
  end
end

function RemoveActorsInGroup(actors)
  local uids = {
    [m_mainUid or 0] = true,
    [m_subUid or 0] = true
  }
  for i = #actors, 1, -1 do
    local actor = actors[i]
    if uids[actor.uid] == true then
      table.remove(actors, i)
    end
  end
end

function ActorSwitch(actorUid, anotherUid)
  local actor = this:GetData("fci/actor/" .. actorUid)
  local anotherActor = this:GetData("fci/actor/" .. (anotherUid or 0))
  if anotherActor then
    local actorConfig = PB.get("ActorConfig", anotherActor.id)
    local role = actorConfig.role
    actorConfig = PB.get("ActorConfig", actor.id)
    if role == actorConfig.role then
      WU.ShowMessageYesNo(WU.GetString("DungeonTeam_ActorSwitchHint"), function(result)
        if result == "YES" then
          if actorConfig.kind == PB.enum.ActorType.Main then
            m_mainUid = actorUid
            m_subUid = 0
          else
            m_mainUid = 0
            m_subUid = actorUid
          end
          m_currentActorUid = actorUid
          RefreshActors()
        end
      end)
      return false
    end
  end
  return true
end

function OnActorHeadClick(go)
  local ref = _ENV["$"](go)
  local actorUid = ref["@uid"]
  if actorUid == nil then
    info("Actor", "actorUid nil?")
    return
  end
  local top = WU.TopWindow()
  if top and top.name == "ActorPeek" then
    WU.ShowActorPeek(this:GetData("fci/actor/" .. actorUid))
  else
    if m_currentActorUid == actorUid then
      return
    end
    local assign = false
    if m_actorType == PB.enum.ActorType.Main then
      assign = ActorSwitch(actorUid, m_subUid)
      if assign then
        m_mainUid = actorUid
        m_currentActorUid = actorUid
      end
    else
      assign = ActorSwitch(actorUid, m_mainUid)
      if assign then
        m_subUid = actorUid
        m_currentActorUid = actorUid
      end
    end
    if assign then
      RefreshActors()
    end
  end
end

function OnActorHeadHold(go)
  local ref = _ENV["$"](go)
  local actorUid = ref["@uid"]
  if actorUid then
    WU.ShowActorPeek(this:GetData("fci/actor/" .. actorUid))
  end
end

function RefreshActors()
  for i = 0, 2 do
    local ref = _ENV["$"](REF.MemberGrid[i]["$transform"]:GetChild(0))
    if ref["@playerId"] == this:GetData("playerId") then
      local actors = {
        this:GetData("fci/actor/" .. (m_mainUid or 0)),
        this:GetData("fci/actor/" .. (m_subUid or 0))
      }
      m_roomInfo.actorInfo[this:GetData("playerId")] = {actors = actors}
      ref["$$SetActors"](actors, OnSlotSelected, false)
      RefreshShowCustom()
      local go = fif(m_actorType == PB.enum.ActorType.Main, ref.DragDropNodeMain.gameObject, ref.DragDropNodeSub.gameObject)
      OnSlotSelected(go)
    end
  end
end

function RefreshAttrDisplay()
  for i = 0, 2 do
    REF.MemberGrid[i].WidgetGroupMemberLink["$DisplayAttrs"](m_displayAttrs)
  end
  if REF.ToggleAttrs.UIToggle.value ~= m_displayAttrs then
    REF.ToggleAttrs.UIToggle.value = m_displayAttrs
  end
end

function UpdateRow(rowRef, wrapIndex, rowIndex)
  WU.ToggleRendering(rowRef["$"], rowIndex ~= nil)
  if rowIndex ~= nil and m_displayActorTable ~= nil and rowIndex < #m_displayActorTable then
    local actor = m_displayActorTable[rowIndex + 1]
    rowRef["$$BindActor"](actor)
    rowRef["@uid"] = actor.uid
    rowRef["$$SetClickCallback"](OnActorHeadClick)
    rowRef["$$SetHoldCallback"](OnActorHeadHold)
    local currentActor
    if m_currentActorUid ~= nil then
      currentActor = this:GetData("fci/actor/" .. m_currentActorUid)
    end
    if currentActor ~= nil and currentActor.id == actor.id then
      rowRef["$$SetMemberLink"](false)
    elseif m_linksActorId ~= nil then
      rowRef["$$SetMemberLink"](m_linksActorId[actor.id])
    end
    rowRef["$$ShowCustom"]({
      showCultivationTotal = m_displayNumbers == SU.DisplayNumbers.Cultivation,
      showSpeed = m_displayNumbers == SU.DisplayNumbers.Speed,
      showStar = m_displayNumbers == SU.DisplayNumbers.Star
    })
  end
end

function OnTeamActorChange(message)
  if m_roomInfo == nil then
    warning("DungeonTeam", "ActorChange received before roomInfo")
    return
  end
  info("DungeonTeam", table.dump(message))
  local playerId = message.playerId
  if m_roomInfo.actorInfo == nil then
    m_roomInfo.actorInfo = {}
  end
  for i = 0, 2 do
    local ref = _ENV["$"](REF.MemberGrid[i]["$transform"]:GetChild(0))
    if ref["@playerId"] == playerId then
      local isMe = this:GetData("playerId") == playerId
      local callback = fif(isMe, OnSlotSelected, nil)
      for i = 1, #message.actors do
        DBH.AppendActorInfo(message.actors[i], not isMe)
      end
      ref["$$SetActors"](message.actors, callback, not isMe)
      m_roomInfo.actorInfo[playerId] = {
        actors = message.actors
      }
    end
  end
  RefreshShowCustom()
end

function ClearSlotSelection()
  for i = 0, 2 do
    local ref = REF.MemberGrid[i]
    ref.WidgetGroupMemberLink[0].WidgetActorCard["$SetHighlighted"](false)
    ref.WidgetGroupMemberLink[1].WidgetActorCard["$SetHighlighted"](false)
  end
end

function RefreshShowCustom()
  for i = 0, 2 do
    local ref = _ENV["$"](REF.MemberGrid[i]["$transform"]:GetChild(0))
    ref["$$ShowCustom"]({
      showNew = false,
      showName = true,
      showCultivationTotal = fif(ref["@playerId"] == this:GetData("playerId"), m_displayNumbers == SU.DisplayNumbers.Cultivation, false),
      showSpeed = m_displayNumbers == SU.DisplayNumbers.Speed,
      showStar = m_displayNumbers == SU.DisplayNumbers.Star,
      showButton = m_showButton
    })
  end
end

function OnRequestReady(flag)
  SendTeamSocket("CsTeamDungeonReady", {ready = flag})
end

function RefreshSpeechDisplay(flag)
  m_showFastSpeech = flag
  REF.SpeechList.gameObject:SetActive(m_showFastSpeech)
  REF.SpeechMask.gameObject:SetActive(m_showFastSpeech)
  if m_showFastSpeech then
    LU.Set(REF.SpeechWrapContent, SpeechTemplateCount)
  end
end

function OnGroupChatMsg(content, isFast)
  SendTeamSocket("CsTeamDungeonChat", {
    content = content,
    isFast = isFast or false
  })
end

function OnGroupChatMsgReceive(message)
  local msg = {
    content = message.content,
    senderId = message.senderId,
    sender = message.sender,
    time = message.sendTime
  }
  local position = 1
  for i = 0, 2 do
    local ref = _ENV["$"](REF.MemberGrid[i]["$transform"]:GetChild(0))
    if message.senderId == ref["@playerId"] then
      position = i + 1
    end
  end
  _ENV["$"](WU.FindWindow("DungeonTeamMsg"))["$$SetContent"](position, message.content)
  local groupCache = this:GetData("ChatRoom/ChatContentGroup") or _ENV["!"]({})
  groupCache = CU.MergeChatContent(groupCache, {msg})
  this:SetData("ChatRoom/ChatContentGroup", groupCache)
end

function OnFastEnterChanged(message)
  local spriteName = fif(message.isAllow, "Dungeon_switch_on", "Dungeon_switch_off")
  m_allowFastJoin = message.isAllow
  REF.FastEnter.UISprite.spriteName = spriteName
  m_stateChange = true
end

function StartFightWebSocket()
  if m_localAi then
    if m_roomInfo == nil or m_localBaseInfo == nil then
      WU.ShowHintText(WU.GetString("DungeonTeam_XlsxError"))
      return
    end
    OnTeamDungeonStart({
      dungeonUid = m_roomInfo.dungeonUid or m_roomInfo.roomId,
      dungeonId = m_dungeonId,
      roomInfo = m_roomInfo,
      baseInfo = m_localBaseInfo,
      localAi = true
    })
    return
  end
  local stateFalse = table.find(m_roomInfo.memberReadyState, function(k, v)
    return U.trim(k) ~= "" and v == false
  end)
  if stateFalse then
    WU.ShowHintText(WU.GetString("DungeonTeam_MemberNotReady"))
    return
  end
  if MemberCount() > 1 then
    if table.find(m_roomInfo.memberList, function(_, v)
      return U.trim(v) == ""
    end) then
      WU.ShowMessageYesNo(WU.GetString("DungeonTeam_MemberNotFull"), function(result)
        if result == "YES" then
          SendTeamSocket("CsTeamDungeonStart", {})
        end
      end)
    else
      SendTeamSocket("CsTeamDungeonStart", {})
    end
  else
    WU.ShowHintText(WU.GetString("DungeonTeam_YouAreAlone"))
  end
end

function OnTeamAllActors(message)
  if m_roomInfo == nil then
    warning("DungeonTeam", "AllActors received before roomInfo")
    return
  end
  info("DungeonTeam", table.dump(message))
  m_roomInfo.actorInfo = message.actorInfo
  REF.Center.gameObject:SetActive(true)
  REF.HintCreateOrJoinSprite.gameObject:SetActive(false)
  SetDungeonTeamMember()
end

function CheckAllowFight()
  local isFull = true
  for _, v in pairs(m_roomInfo.actorInfo) do
    if table.count(v.actors) < 2 then
      isFull = false
    end
  end
  local memberCount = MemberCount()
  if memberCount == 1 then
    WU.ShowHintText(WU.GetString("DungeonTeam_YouAreAlone"))
  end
  return isFull and 1 < memberCount
end

function MemberCount()
  local memberCount = 0
  for _, v in pairs(m_roomInfo.memberList) do
    if U.trim(v) ~= "" then
      memberCount = memberCount + 1
    end
  end
  return memberCount
end

function OnSortFinished(data)
  if data ~= nil and REF.ActorSelect.SortMate == data.sortMate then
    m_advancedFilters = fif(data.advancedFilters == nil, m_advancedFilters, data.advancedFilters)
    local filteredList = SU.ApplyFilters(data.list, m_advancedFilters)
    local id = data.sortMate.ids[data.sortMate.tabIndex]
    for _, ids in pairs(SU.DisplayNumbers) do
      if table.find(ids, function(_, v)
        return v == id
      end) then
        m_displayNumbers = ids
      end
    end
    AU.OnActorClassify(filteredList, "DungeonTeamPrepare")
  end
end

function Focus(on)
  SortFocusBase.FocusActor(on, function()
    m_resetActorSelect = true
    return m_advancedFilters
  end)
end
