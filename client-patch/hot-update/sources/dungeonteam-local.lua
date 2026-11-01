local BASE = inherit("Window/DungeonPrepareBase/DungeonPrepareBase", _ENV)
local PB = require("Common/PbHelper")
local WU, DB, REF = require("Common/WindowUtil")(this)
local HU = require("Common/HtmlUtil")
local ACU = require("Common/ActivityUtil")
local LU = require("Common/ListUtil")
local RU = require("Common/RedMarkUtil")
local DU = require("Common/DungeonUtil")
local S = require("Common/Singleton")
local AM = S:Get("ActivityManager")
local m_buffBgheight = {
  310,
  445,
  580
}
m_buffBgheight[0] = 177
local MaxDropDisplay = 5.5
local DungeonTeamTicket = PB.all("Misc"):first().teamDungeonTicket
local m_activityId2GroupId, m_currentActivity, m_dropDisplay, m_dungeonReward, m_displayLevels, m_displayHelp, m_displayMonsterInfo, m_selectedLevel, m_hasSetUrls, m_difficultLevelLowTable, m_dungeonIdTable, m_dungeonId

function SetupWindow()
  local function CloseCheck()
    if not ACU.IsOpenForDoing(m_currentActivity) then
      WU.ShowHintText(WU.GetString("DungeonTeam_ActivityClosed"))
      
      return true
    end
    return false
  end
  
  local function OnPayFailed()
    WU.ShowMessageYesNo(WU.GetString("ActorGroup_PowerNotEnough"), function(result)
      if result == "YES" then
        WU.Exchange(PB.enum.ResourceType.ResPlayerPower)
      end
    end)
  end
  
  local function OnTicketFailed()
    WU.ShowHintText(WU.GetString("DungeonTeam_TicketNotEnough"))
  end
  
  WU.TraverseChildren(REF.WrapContent, function(go)
    WU.BindButtonEvent(go, function(go)
      local ref = _ENV["$"](go)
      m_selectedLevel = ref["@level"]
      REF.LabelLv.UILabel.text = WU.GetString("DungeonTeam_DifficultLevel", ref["@level"])
      local rememberedLevel = this:GetData("DungeonTeam/RememberedLevel") or {}
      rememberedLevel[m_currentActivity.activityId] = m_selectedLevel
      this:SetData("DungeonTeam/RememberedLevel", rememberedLevel)
      SwitchLevelSelection(false)
      RefreshDungeonInfo(ref["@dungeonId"])
    end)
  end)
  WU.BindButtonEvent(REF.ButtonRoomCreate, function()
    if m_dungeonReward then
      local function Create()
        if m_hasSetUrls and m_activityId2GroupId and m_currentActivity and m_selectedLevel and not CloseCheck() then
          DU.IsInDungeonTeamPrepare(function()
            local groupId = m_activityId2GroupId[tostring(m_currentActivity.activityId)]
            
            WU.AcquireWindowAsync("DungeonTeamPrepare", function(ui)
              _ENV["$"](ui)["$$CreateRoom"](groupId, m_selectedLevel, m_currentActivity.activityId, m_dungeonId)
              _ENV["$"](ui)["$$InitBackAndHomeCallback"]()
            end)
          end)
        end
      end
      
      if not DU.IsWaitingForDungeonTeam(function()
        WU.TryToPay(DungeonTeamTicket.type, DungeonTeamTicket.id, DungeonTeamTicket.count, Create, OnTicketFailed)
      end) then
        WU.TryToPay(DungeonTeamTicket.type, DungeonTeamTicket.id, DungeonTeamTicket.count, Create, OnTicketFailed)
      end
    end
  end)
  WU.BindButtonEvent(REF.ButtonFastEnter, function()
    if m_dungeonReward then
      local function FastJoin()
        if m_hasSetUrls and m_activityId2GroupId and m_currentActivity and m_selectedLevel and not CloseCheck() then
          local groupId = m_activityId2GroupId[tostring(m_currentActivity.activityId)]
          
          WU.AcquireWindowAsync("DungeonTeamFastJoin", function(ui)
            _ENV["$"](ui)["$$FastJoin"](groupId, m_selectedLevel, m_currentActivity.activityId, m_dungeonId)
          end)
        end
      end
      
      WU.TryToPay(DungeonTeamTicket.type, DungeonTeamTicket.id, DungeonTeamTicket.count, FastJoin, OnTicketFailed)
    end
  end)
  WU.BindButtonEvent(REF.TouchInfo, function()
    SwitchMonsterInfo()
  end)
  WU.BindButtonEvent(REF.SpriteHelp, function()
    SwitchHelp()
  end)
  WU.BindButtonEvent(REF.ButtonLevel, function()
    SwitchLevelSelection()
  end)
  WU.BindButtonEvent(REF.Mask, function()
    SwitchCheck()
  end)
  LU.Bind(REF.DropContent, {
    updateRow = UpdateDropSlot
  })
  LU.Bind(REF.WrapContent, {
    updateRow = UpdateLevel
  })
end

function InitWindow()
  m_hasSetUrls = nil
  m_selectedLevel = nil
  REF.LeftBottom.gameObject:SetActive(false)
  REF.RIGHT.gameObject:SetActive(false)
  this:GameRequest("fci/teamdungeonurl/"):SyncInput(false):Get(function(result)
    -- The local single-player route deliberately does not open the retired
    -- DungeonTeamService websocket.  Keep the response probe so the native
    -- menu still follows its normal readiness handshake.
    this:SetData("DungeonTeam/LocalAi", result.localAi == true)
    if result.localAi ~= true then
      DU.SetDungeonTeamUrl(result.url)
    end
    m_hasSetUrls = true
  end)
  this:SetData("Activity/CurrentActivity", ACU.GetCurrentActivityList(PB.enum.ActivityType.DungeonTeam))
  this:Bind("Activity/CurrentActivity", OnCurrentActivityChange)
  this:BindRemote(DB:GameRequest("fci/teamdungeongroups/"), RefreshActivityInfo)
  this:RegisterGameEvent("ActivitySelect", OnActivitySelect)
  SwitchLevelSelection(false)
  SwitchHelp(false)
  SwitchMonsterInfo(false)
end

function OnActivitySelect(activity)
  m_currentActivity = activity
  REF.DungeonTeamTimeLabel.UILabel.text = WU.GetString("DungeonTeam_DayOpenTime", WU.RenderTimeOfDay(activity.dayOpenTime[1].timestampStart), WU.RenderTimeOfDay(activity.dayOpenTime[1].timestampEnd))
  RefreshActivityInfo(m_activityId2GroupId)
end

function SwitchMonsterInfo(flag)
  if flag == nil and SwitchCheck(3) then
    return
  end
  m_displayMonsterInfo = fif(flag == nil, not m_displayMonsterInfo, flag)
  WU.ToggleRendering(REF.PanelBuff, m_displayMonsterInfo)
  REF.Mask.gameObject:SetActive(m_displayMonsterInfo)
end

function SwitchHelp(flag)
  if flag == nil and SwitchCheck(2) then
    return
  end
  m_displayHelp = fif(flag == nil, not m_displayHelp, flag)
  WU.ToggleRendering(REF.HelpNode, m_displayHelp)
  REF.Mask.gameObject:SetActive(m_displayHelp)
end

function SwitchLevelSelection(flag)
  if flag == nil and SwitchCheck(1) then
    return
  end
  m_displayLevels = fif(flag == nil, not m_displayLevels, flag)
  WU.ToggleRendering(REF.LevelSelection, m_displayLevels)
  REF.Mask.gameObject:SetActive(m_displayLevels)
  if m_displayLevels then
    LU.Set(REF.WrapContent, #m_difficultLevelLowTable, false, true)
  end
end

function SwitchCheck(type)
  local nothingDisplay = not m_displayLevels and not m_displayHelp and not m_displayMonsterInfo
  if nothingDisplay then
    return false
  elseif type == 1 and m_displayLevels or type == 2 and m_displayHelp or type == 3 and m_displayMonsterInfo then
    return false
  end
  SwitchLevelSelection(false)
  SwitchHelp(false)
  SwitchMonsterInfo(false)
  REF.Mask.gameObject:SetActive(false)
  return true
end

function UpdateDropSlot(ref, wrapIndex, realIndex)
  if m_dropDisplay ~= nil and realIndex < #m_dropDisplay then
    local widgetIconSlot = ref.root
    local dropDisplay = m_dropDisplay[realIndex + 1]
    if dropDisplay.type == PB.enum.ResourceType.ResPlayerGold or dropDisplay.type == PB.enum.ResourceType.ResPlayerExp then
      widgetIconSlot["$SetData"](dropDisplay.type, dropDisplay.id, dropDisplay.count)
    else
      widgetIconSlot["$SetData"](dropDisplay.type, dropDisplay.id)
    end
    widgetIconSlot["$SetClickCallback"](function()
      WU.ShowResourceDetail(dropDisplay.type, dropDisplay.id)
    end)
  end
end

function OnCurrentActivityChange(currentActivity)
  if currentActivity == nil then
    return
  end
  local activities = currentActivity
  for i = 0, #REF.Activities - 1 do
    REF.Activities[i].WidgetDungeonTeamSlot["$ModifyContent"](activities[i + 1])
  end
  local toIndex, activeActivity = _ENV["!"](activities):find(function(k, v)
    return ACU.IsOpenForDoing(v, false, true)
  end)
  m_currentActivity = activeActivity
  REF.Activities[toIndex - 1].WidgetDungeonTeamSlot.UIToggle.value = true
  REF.DungeonTeamTimeLabel.UILabel.text = WU.GetString("DungeonTeam_DayOpenTime", WU.RenderTimeOfDay(activeActivity.dayOpenTime[1].timestampStart), WU.RenderTimeOfDay(activeActivity.dayOpenTime[1].timestampEnd))
end

function RefreshActivityInfo(activityId2GroupId)
  if activityId2GroupId == nil then
    return
  end
  m_activityId2GroupId = activityId2GroupId
  local groupId = m_activityId2GroupId[tostring(m_currentActivity.activityId)]
  for i = 0, #REF.Activities - 1 do
    local ref = _ENV["$"](REF.Activities[i].WidgetDungeonTeamSlot)
    if ref["@activityId"] == nil or m_activityId2GroupId[tostring(ref["@activityId"])] == nil then
      warning("Activity", "ActivityList missing activityId  or  DungeonTeam activity is missing")
      return
    end
    local name = WU.GetString("DungeonTeamName_" .. m_activityId2GroupId[tostring(ref["@activityId"])])
    ref.LabelMaterialTitle.UILabel.text = name
    if ref["$UIToggle"].value then
      this:SetData("DungeonTeam/ActivityName", name)
    end
  end
  local teamDungeonGroup = PB.get("TeamDungeonGroup", groupId)
  if teamDungeonGroup == nil then
    warning("DungeonTeam", "TeamDungeonGroup.xlsx not found : " .. groupId)
    WU.ShowHintText(WU.GetString("DungeonTeam_XlsxError"))
    WU.RecycleWindow(this)
    return
  end
  REF.TextureStage.UITexture.mainTexturePath = "Texture/Background/" .. (teamDungeonGroup.bgPicture or "")
  REF.LeftBottom.gameObject:SetActive(true)
  REF.RIGHT.gameObject:SetActive(true)
  local dungeonId = InitDungeonsLevel(teamDungeonGroup)
  if dungeonId == nil or dungeonId == 0 or m_selectedLevel == nil then
    warning("DungeonTeam", "DungeonId is nil or 0 in groupId : " .. tostring(groupId))
    return
  end
  RefreshDungeonInfo(dungeonId)
end

function RefreshDungeonInfo(dungeonId)
  if dungeonId ~= nil then
    m_dungeonId = dungeonId
    InitDropList(dungeonId)
    REF.WidgetTicketLabel["$UpdateTickets"](DungeonTeamTicket)
    InitDungeonInfo(dungeonId)
    BASE.InitHelp(dungeonId)
    local index = BASE.InitMonsterInfo(dungeonId)
    SetBuffVisible(index)
    BASE.InitEnemyAttr(dungeonId)
    local dungeon = PB.get("DungeonMonster", dungeonId)
    if dungeon == nil then
      warning("DungeonTeam", "DungeonMonster.xlsx error no dungeonId : " .. dungeonId)
      return
    end
    REF.LabelWinTitle.UIHtmlLabel.text = WU.GetString("WinType_Title") .. WU.GetDungeonWinTypeText(dungeon.winParam)
    REF.SpriteHelp.gameObject:SetActive(dungeon.isShowHelp)
  else
    warning("RefreshDungeonInfo", "dungeonId nil?")
  end
end

function InitDropList(dungeonId)
  m_dungeonReward = PB.get("DungeonReward", dungeonId)
  if m_dungeonReward == nil then
    warning("DungeonTeam", "no reward view dungeonId : " .. dungeonId)
    return
  end
  m_dropDisplay = {}
  for k, v in pairs(m_dungeonReward.dropDisplay) do
    m_dropDisplay[k] = v
  end
  local widgetIconSlotWidth = REF.DropContent[0].Bounds.UIWidget.width
  local scaleX = REF.DropContent.transform.localScale.x
  local addWidth = (widgetIconSlotWidth / 2 - REF.DropContent.UIWrapContent.itemSize / 2) * scaleX
  local itemSize = REF.DropContent.UIWrapContent.itemSize * scaleX
  local extraRewardParams = {
    type = PB.enum.ActivityExtraRewardRuleType.Dungeon_Team
  }
  AM.GetTimeLimitExtraReward(extraRewardParams, function(extra)
    if #extra ~= 0 then
      for i = 1, #extra do
        table.insert(m_dropDisplay, extra[i])
      end
    end
    if #m_dropDisplay < MaxDropDisplay then
      REF.DropList.UIWidget.width = math.floor(itemSize * #m_dropDisplay + addWidth)
    else
      REF.DropList.UIWidget.width = math.floor(itemSize * MaxDropDisplay + addWidth)
    end
    LU.Set(REF.DropContent, #m_dropDisplay)
  end)
end

function InitDungeonInfo(dungeonId)
  local monsterList = {}
  local showTypeList = {}
  local showDescList = {}
  local dungeons = PB.get("DungeonMonster", dungeonId)
  for j = 1, #dungeons.monsterId do
    monsterList[j] = dungeons.monsterId[j]
  end
  for j = 1, #dungeons.showType do
    showTypeList[j] = dungeons.showType[j]
  end
  BASE.ShowStoryDungeonActorList(monsterList, showTypeList)
end

function SetBuffVisible(index)
  REF.SpriteBuffBg.UISprite.height = m_buffBgheight[0]
  for i = 1, 3 do
    local ref = REF[string.format("NodeBuff%d", i)]
    if i <= index then
      ref.gameObject:SetActive(true)
      if i == index then
        REF.SpriteBuffBg.UISprite.height = m_buffBgheight[index]
      end
    else
      ref.gameObject:SetActive(false)
    end
  end
end

function InitDungeonsLevel(teamDungeonGroup)
  m_difficultLevelLowTable = {}
  m_dungeonIdTable = {}
  for i = 1, #teamDungeonGroup.dungeonIds do
    local dungeonId = teamDungeonGroup.dungeonIds[i]
    local dungeonReward = PB.get("DungeonReward", dungeonId)
    if dungeonReward == nil then
      warning("DungeonTeam", "Can't find dungeonId " .. dungeonId .. " in DungeonReward.xlsx.")
    else
      table.insert(m_difficultLevelLowTable, dungeonReward.difficultLevelLow)
      table.insert(m_dungeonIdTable, dungeonId)
    end
  end
  table.sort(m_difficultLevelLowTable, function(v1, v2)
    return v1 < v2
  end)
  local playerLv = this:GetData("fci/baseinfo/").level
  local rtnDungeonId, index
  local rememberedLevel = this:GetData("DungeonTeam/RememberedLevel") or {}
  m_selectedLevel = rememberedLevel[m_currentActivity.activityId]
  local autoSelect = m_selectedLevel == nil
  for i = 1, #m_difficultLevelLowTable do
    local level = m_difficultLevelLowTable[i]
    
    local function Assign(i, level)
      rtnDungeonId = m_dungeonIdTable[i]
      index = i
      REF.LabelLv.UILabel.text = WU.GetString("DungeonTeam_DifficultLevel", level)
    end
    
    if autoSelect and playerLv >= level then
      m_selectedLevel = level
      Assign(i, level)
    elseif m_selectedLevel == level then
      Assign(i, level)
    end
  end
  LU.Set(REF.WrapContent, #m_difficultLevelLowTable)
  local displayCount = #REF.WrapContent - 1
  if index > displayCount then
    local height = REF.WrapContent.UIWrapContent.itemSize
    REF.LevelScrollView.UIScrollView:MoveRelative(CS.UnityEngine.Vector3(0, (index - displayCount) * height, 0))
  end
  return rtnDungeonId
end

function UpdateLevel(ref, wrapIndex, realIndex)
  if m_difficultLevelLowTable ~= nil and realIndex < #m_difficultLevelLowTable then
    local level = m_difficultLevelLowTable[realIndex + 1]
    local dungeonId = m_dungeonIdTable[realIndex + 1]
    ref["@level"] = level
    ref["@dungeonId"] = dungeonId
    local playerLv = this:GetData("fci/baseinfo/").level
    ref["$BoxCollider2D"].enabled = level <= playerLv
    ref.Label.UILabel.text = WU.GetString("DungeonTeam_DifficultLevel", level)
    ref.GameObject.gameObject:SetActive(m_selectedLevel == level)
    if m_selectedLevel == level then
      ref.Label.UILabel.color = CS.NGUIMath.HexToColor(911905023)
    else
      ref.Label.UILabel.color = CS.NGUIMath.HexToColor(fif(level > playerLv, 4294967159, 4294967295))
    end
  end
end
