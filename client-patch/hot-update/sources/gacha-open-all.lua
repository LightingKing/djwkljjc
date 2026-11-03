local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local ACU = require("Common/ActivityUtil")
local DBH = require("Manager/DataBindingHandler")
local PU = require("Common/PlatformUtil")
local S = require("Common/Singleton")
local m_activityManager = S:Get("ActivityManager")
local m_gachaId, m_gachaInfo, m_callback, m_onOk, m_cenimatic, m_lastTickSec, m_willFreeTime, m_limitAct, m_hasOrangeActor, m_hasReviewed
local m_periodicOn = false

function SetupWindow()
  WU.BindButtonEvent(REF.ButtonGachaPeriodic, function(go)
    if m_periodicOn then
      WU.AcquireWindowAsync("GachaPeriodic")
    else
      WU.ShowHintText(WU.GetString("WindowGacha_PeriodicGachaOff"))
    end
  end)
  WU.BindButtonEvent(REF.ButtonGachaNormal, function(go)
    WU.AcquireWindowAsync("GachaNormal")
  end)
  WU.BindButtonEvent(REF.ButtonGachaChips, function(go)
    WU.AcquireWindowAsync("GachaChips")
  end)
  WU.BindButtonEvent(REF.ButtonGachaFriend, function(go)
    WU.AcquireWindowAsync("GachaClawEnter")
  end)
  WU.BindButtonEvent(REF.ButtonShop, function(go)
    this:GameRequest("fci/gacha-shop/"):Get(function()
      WU.AcquireWindowAsync("GachaScoreShop")
    end)
  end)
  WU.BindButtonEvent(_ENV["$"](REF.ButtonGachaChips).ButtonUnlockShelter, function()
    local lock, unlockLevel = WU.WindowIsLocked(PB.enum.UnlockWindow.GachaChips)
    WU.ShowHintText(WU.GetString("WindowUnlock_Hint", WU.GetString("UnlockType_" .. PB.enum.UnlockWindow.GachaChips), unlockLevel))
  end)
  m_hasReviewed = WU.GetGameDataCache("ReviewApp")
end

function IsFreeGacha()
  if m_willFreeTime and 0 < m_willFreeTime then
    return 0 >= m_willFreeTime - CS.GameTime.serverUtc
  else
    return false
  end
end

function UpdateFreeTime()
  UpdateNormalRedPoint()
  if m_willFreeTime and 0 < m_willFreeTime then
    local leftTime = m_willFreeTime - CS.GameTime.serverUtc
    if 0 < leftTime then
      local freeTime = WU.RenderCountDownTime(leftTime, 19)
      REF.HtmlLabelFreeTime.UIHtmlLabel.text = freeTime .. WU.GetString("WindowGacha_FreeTips2")
      if REF.GameObjectNoFree.gameObject.activeSelf == false then
        REF.GameObjectNoFree.gameObject:SetActive(true)
      end
      if REF.GameObjectFree.gameObject.activeSelf == true then
        REF.GameObjectFree.gameObject:SetActive(false)
      end
    else
      if REF.GameObjectNoFree.gameObject.activeSelf == true then
        REF.GameObjectNoFree.gameObject:SetActive(false)
      end
      if REF.GameObjectFree.gameObject.activeSelf == false then
        REF.GameObjectFree.gameObject:SetActive(true)
      end
    end
  else
    if REF.GameObjectNoFree.gameObject.activeSelf == true then
      REF.GameObjectNoFree.gameObject:SetActive(false)
    end
    if REF.GameObjectFree.gameObject.activeSelf == true then
      REF.GameObjectFree.gameObject:SetActive(false)
    end
  end
end

function ShowEmoteView(res)
  if S:Get("TutorialManager").IsTutorialDoing() then
    return
  end
  if res and res.isFirstPlotEmote then
    this:DelayInvokeInSeconds(0.1, function()
      WU.AcquireWindowAsync("GachaEmote", function(ui)
        res.isFirstPlotEmote = false
        this:SetData("fci/gacha-plot-emote/", res)
        this:GameRequest("fci/gacha-plot-emote/"):Post({}, function()
        end)
      end)
    end)
  end
end

function InitWindow()
  S:Register(_ENV, "Gacha")
  REF.TextureActor.gameObject:SetActive(false)
  if WU.IsPreCommit() then
    REF.TextureActor.UITexture.mainTexturePath = "Texture/ActorPortrait/actor_portrait_1001a"
  end
  this:Bind("fci/baseinfo/", function(baseInfo)
    local lock, unlockLevel = WU.WindowIsLocked(PB.enum.UnlockWindow.GachaChips)
    _ENV["$"](REF.ButtonGachaChips).ButtonUnlockShelter.gameObject:SetActive(lock)
  end)
  this:Bind("fci/gachaResult/", function(res)
    UpdateRedPoint()
  end)
  m_activityManager.GetActivityByType(PB.enum.ActivityType.GachaPlotEmote, function(activity)
    if activity then
      local ac = activity[1]
      if ACU.IsOpenForDoing(ac) then
        this:Unbind("fci/gacha-plot-emote/", ShowEmoteView)
        this:BindRemote(DB:GameRequest("fci/gacha-plot-emote/"), ShowEmoteView)
      end
    end
  end)
  REF.SpriteProbabilityUp.gameObject:SetActive(false)
  REF.ButtonShop.gameObject:SetActive(false)
  SetPeriodicButton(false)
  this:BindRemote(DB:GameRequest("fci/gachainfo/"), OnGachaInfoChanged)
  this:Bind("ActivityList", OnActivityChanged)
  this:RegisterGameEvent("ActivityStatusChanged", OnActivityStatusChanged)
  this:RepeatInvokeInSeconds(1.0, function()
    UpdateFreeTime()
  end)
  UpdateRedPoint()
  REF["$UILuaWindow"]:PlayUITweener()
  this:RegisterGameEvent("UI/WindowUninited", OnWindowUninited)
end

function UninitWindow()
  S:Unregister("Gacha")
  if PU.IsRecordVideoEnabled() then
    CS.RecordVideoManager.Instance:ClearStartMomentsRecordingCallbacks()
  end
end

function OnWindowUninited(window)
  if window.name == "GachaNormal" and this:GetData("CurrentTutorial") == 5 then
    S:Get("TutorialManager").EndTutorial()
  end
  if window.name == "GachaResult" then
    ShowReviewApp()
  end
end

function Focus(on)
  REF.SpriteActorRoot.gameObject:SetActive(on)
end

function ShowReviewApp()
end

function UpdateLimitActor()
  REF.TextureActor.gameObject:SetActive(true)
  REF.SpriteActor.gameObject:SetActive(false)
  if m_limitAct then
    local actorId = {}
    for i, v in ipairs(m_limitAct) do
      local info = PB.get("ActivityGachaLimited", v.activityId)
      if info then
        for i2, v2 in ipairs(info.dropResInfo) do
          if v2.dropRes.type == PB.enum.ResourceType.ResActor then
            actorId[#actorId + 1] = v2.dropRes.id
          end
        end
      end
      local info = PB.get("ActivityGachaNormalUp", v.activityId)
      if info then
        for i2, v2 in ipairs(info.dropResInfo) do
          actorId[#actorId + 1] = v2.dropActorId
        end
      end
    end
    if 0 < #actorId then
      local index = math.random(1, #actorId)
      local currentActorId = actorId[index]
      local boardInfo = PB.get("BoardActorConfig", currentActorId)
      local hasBoardInfo = false
      if boardInfo then
        REF.TextureActor.gameObject:SetActive(false)
        REF.SpriteActor.gameObject:SetActive(true)
        REF.WidgetBoardActor["$PlayByActorID"](currentActorId, "Gacha")
      else
        for i = 1, #actorId do
          local tempId = actorId[i]
          boardInfo = PB.get("BoardActorConfig", tempId)
          if boardInfo then
            REF.TextureActor.gameObject:SetActive(false)
            REF.SpriteActor.gameObject:SetActive(true)
            REF.WidgetBoardActor["$PlayByActorID"](tempId, "Gacha")
            hasBoardInfo = true
            break
          end
        end
        if hasBoardInfo == false then
          REF.TextureActor.gameObject:SetActive(true)
          REF.SpriteActor.gameObject:SetActive(false)
          local config = PB.get("ActorConfig", currentActorId)
          if config then
            REF.TextureActor.UITexture.mainTexturePath = "Texture/ActorPortrait/actor_portrait_" .. config.animRes
          end
        end
      end
    end
  end
end

function OnGachaInfoChanged(gachaInfo)
  m_gachaInfo = gachaInfo
  if m_gachaInfo then
    for i = 1, #m_gachaInfo do
      local info = m_gachaInfo[i]
      local gachaType = PB.get("GachaType", info.id)
      if gachaType == nil then
        error("Gacha", "GachaType.xlsx error , id " .. tostring(info.id))
      else
        if info.id == 1 then
          m_willFreeTime = info.nextFreeGachaTime
        end
        local button = REF["Button" .. gachaType.windowName]
        if button then
          button.gameObject:SetActive(not gachaType.hide)
        end
      end
    end
  end
end

function OnActivityChanged(activityList)
  if activityList then
    local upAct = table.find(activityList, function(k, v)
      return v.type == PB.enum.ActivityType.GachaProbabilityUp and ACU.GetStatus(v) == PB.enum.ActivityStatus.Started
    end)
    local periodAct, act = table.find(activityList, function(k, v)
      return v.type == PB.enum.ActivityType.Gacha and ACU.GetStatus(v) == PB.enum.ActivityStatus.Started
    end)
    m_limitAct = {}
    local shopActivityId
    for k, v in pairs(activityList) do
      if (v.type == PB.enum.ActivityType.Gacha or v.type == PB.enum.ActivityType.GachaProbabilityUp) and ACU.GetStatus(v) == PB.enum.ActivityStatus.Started then
        m_limitAct[#m_limitAct + 1] = v
      end
      if v.type == PB.enum.ActivityType.ActivityType_GachaShop and ACU.GetStatus(v) == PB.enum.ActivityStatus.Started then
        shopActivityId = v.activityId
      end
    end
    SetActivityGachaStatus(PB.enum.ActivityType.GachaProbabilityUp, fif(upAct ~= nil, PB.enum.ActivityStatus.Started, PB.enum.ActivityStatus.Close))
    SetActivityGachaStatus(PB.enum.ActivityType.Gacha, fif(periodAct ~= nil, PB.enum.ActivityStatus.Started, PB.enum.ActivityStatus.Close))
    UpdateGachaShopBg(shopActivityId)
  end
  UpdateLimitActor()
end

function UpdateGachaShopBg(activityId)
  if activityId == nil then
    REF.ButtonShop.gameObject:SetActive(false)
    return
  end
  local info = PB.get("GachaScoreBg", activityId)
  if info then
    REF.ButtonShop.gameObject:SetActive(true)
    REF.SpriteGachaIcon.UITexture.mainTexturePath = info.bgName
  end
end

function OnActivityStatusChanged(category, activityId, status, type)
  SetActivityGachaStatus(type, status)
  if type == PB.enum.ActivityType.ActivityType_GachaShop and status == PB.enum.ActivityStatus.Started then
    UpdateGachaShopBg(activityId)
  end
  if type == PB.enum.ActivityType.GachaProbabilityUp then
    OnActivityChanged(this:GetData("ActivityList"))
  end
  if type == PB.enum.ActivityType.Gacha and status == PB.enum.ActivityStatus.Close then
    OnActivityChanged(this:GetData("ActivityList"))
  end
end

function SetActivityGachaStatus(type, status)
  local started = status == PB.enum.ActivityStatus.Started
  if type == PB.enum.ActivityType.GachaProbabilityUp then
    REF.SpriteProbabilityUp.gameObject:SetActive(started)
  elseif type == PB.enum.ActivityType.Gacha then
    SetPeriodicButton(started)
  end
end

function Goto(gachaId)
  m_gachaId = math.floor(tonumber(gachaId))
  SwitchGacha()
end

function SwitchGacha()
  if m_gachaId and m_gachaInfo then
    local gachaType = PB.get("GachaType", m_gachaId)
    if gachaType and gachaType.windowName then
      WU.AcquireWindowAsync(gachaType.windowName)
    end
  end
end

function DoGachaImpl(gachaId, costArray, onSucceedProxy)
  local function onCancelProxy()
    this:BroadcastGameEvent("ResetGachaStatus")
  end
  
  local flag = WU.GetGameDataCache("GachaPurchaseTips" .. CS.DataBindingManager.Instance:GetData("playerId"))
  if flag == nil then
    flag = false
  end
  local isTutorialOn = S:Get("TutorialManager").IsTutorialDoing()
  if not flag and not IsFreeGacha() and not isTutorialOn and gachaId ~= 5 then
    WU.AcquireWindowAsync("GachaPurchaseDlg", function(ui)
      _ENV["$"](ui)["$$SetData"](gachaId, costArray, onSucceedProxy, onCancelProxy)
    end)
  else
    onSucceedProxy()
  end
end

function DoGacha(gachaDetail, gachaData)
  local actors = this:GetData("fci/actor/")
  if #actors >= PB.index("Misc", 1).maxActorWarningCount then
    WU.ShowMessageYesNo(WU.GetString("ActorOverflowWarning"), function(result)
      if result == "YES" then
        WU.AcquireWindowAsync("ActorDecompose")
      end
    end, WU.GetString("Window_Decompose"))
    return
  end
  DB:SetData("fci/gachaShareInfo", {
    id = gachaDetail.id,
    times = gachaData.times
  })
  m_callback = gachaData.callback
  m_onOk = gachaData.onOk
  local gachaType = PB.get("GachaType", gachaDetail.id)
  local times = gachaData.times or 1
  if times > gachaType.maxCountPerDay - gachaDetail.gachaCountToday then
    WU.ShowHintText(WU.GetString("WindowGacha_NoTimes"))
    return
  end
  if gachaData.cost ~= nil then
    local function onSucceedProxy()
      this:SetData("fci/gachaDetail/", nil)
      
      this:SetData("fci/gachaData/", nil)
      RequestGacha({
        id = gachaDetail.id,
        cost = gachaData.cost,
        cinematic = gachaData.cinematic,
        gachaFree = IsFreeGacha()
      })
    end
    
    local costArray = {}
    table.insert(costArray, gachaData.cost)
    DoGachaImpl(gachaDetail.id, costArray, onSucceedProxy)
  else
    local costStock = 0
    if gachaType.cost.type == PB.enum.ResourceType.ResItem then
      costStock = this:GetData("fci/item/" .. gachaType.cost.id).count
    else
      costStock = this:GetData("fci/resource/" .. PB.enum.ResourceType[gachaType.cost.type] .. "_" .. gachaType.cost.id)
    end
    local baseAffordTimes = math.floor(math.max(0, costStock) / gachaType.cost.count)
    local extraAffordCount = gachaType.costSub.count * (times - baseAffordTimes)
    this:SetData("fci/gachaDetail/", gachaDetail)
    this:SetData("fci/gachaData/", gachaData)
    local costArray = {}
    if 0 < baseAffordTimes then
      if times > baseAffordTimes then
        costArray[#costArray + 1] = {
          id = gachaType.cost.id,
          type = gachaType.cost.type,
          count = baseAffordTimes
        }
      else
        costArray[#costArray + 1] = {
          id = gachaType.cost.id,
          type = gachaType.cost.type,
          count = times
        }
      end
    end
    if 0 < extraAffordCount then
      costArray[#costArray + 1] = {
        id = gachaType.costSub.id,
        type = gachaType.costSub.type,
        count = extraAffordCount
      }
    end
    
    local function onSucceedProxy()
      local param = {
        id = gachaDetail.id,
        activityId = gachaData.activityId,
        count = times,
        cinematic = gachaData.cinematic,
        gachaFree = IsFreeGacha()
      }
      if not IsFreeGacha() then
        WU.TryToPay(gachaType.costSub.type, gachaType.costSub.id, extraAffordCount, function()
          if baseAffordTimes ~= 0 and 0 < extraAffordCount then
            RequestGacha(param)
          else
            RequestGacha(param)
            if m_onOk then
              m_onOk()
            end
          end
        end)
      else
        RequestGacha(param)
      end
    end
    
    DoGachaImpl(gachaDetail.id, costArray, onSucceedProxy)
  end
end

function TryToShowResult()
  local result = this:GetData("fci/gachaResult/")
  local topWindow = WU.TopWindow()
  if result ~= nil and m_cenimatic == nil and topWindow and topWindow.name ~= "GachaResult" then
    WU.AcquireWindowAsync("GachaResult", nil, this:GetData("NavigationContext") == "GachaCinematic")
    if m_callback then
      m_callback(result)
      m_callback = nil
    end
  else
    this:BroadcastGameEvent("ResetGachaTimes")
  end
end

function RequestGacha(param)
  -- Local-only replay key. ApiGachaTrade has no nonce field, so carry this
  -- through the existing GameRequest.Query API without changing protobuf.
  -- A lost reply retains the same day/pool/count; a completed draw advances
  -- gachaCountToday before the next click. The server persists the receipt.
  local localRequestId
  local iap = S:Get("IAPHandler")
  if iap and iap.IsLocalAccountServer and iap.IsLocalAccountServer() then
    local pools = this:GetData("fci/gachainfo/") or {}
    for _, pool in pairs(pools) do
      if pool.id == param.id then
        local day = math.floor((CS.GameTime.serverUtc + 7200) / 86400)
        localRequestId = table.concat({tostring(this:GetData("playerId") or "local"),
          tostring(day), tostring(param.id), tostring(pool.gachaCountToday or 0)}, ":")
        break
      end
    end
  end
  local function RequestGachaImpl()
    this:SetData("fci/gachaResult/", nil)
    
    m_cenimatic = param.cinematic
    if localRequestId then
      this:GameRequest("fci/gachatrade/"):Query("localRequestId", localRequestId):Post(param, OnGachaResponse, function(result)
        this:BroadcastGameEvent("ResetGachaStatus")
      end)
    else
      this:GameRequest("fci/gachatrade/"):Post(param, OnGachaResponse, function(result)
        this:BroadcastGameEvent("ResetGachaStatus")
      end)
    end
  end
  
  local willRecordGacha = false
  if PU.IsRecordGachaEnabled() then
    local times = param.count or 1
    if 2 <= times then
      willRecordGacha = true
      this:SetData("RecordVideoList", nil)
      WU.SetLockFlag(PB.enum.LockInputFlag.NormalBlock, true)
      CS.RecordVideoManager.Instance:StartMomentsRecording(function(status)
        WU.SetLockFlag(PB.enum.LockInputFlag.NormalBlock, false)
        if status == CS.GameJoyAPI.RecordingStartStatus.Fail then
          warning("RecordVideo", "record start fail")
        else
          this:SetData("RecordVideoStartTime", CS.GameJoy.getSystemCurrentTimeMillis)
          local recordVideoStartTime = CS.GameJoy.getSystemCurrentTimeMillis
          this:SetData("RecordVideoStartTime", recordVideoStartTime)
          local recordVideoData = {
            startTime = recordVideoStartTime,
            showActorGet = false,
            isOrangeActor = false,
            isIntro = true
          }
          this:SetData("RecordVideoData", recordVideoData)
        end
        RequestGachaImpl()
      end)
    end
  end
  if not willRecordGacha then
    RequestGachaImpl()
  end
end

function OnGachaResponse(result)
  DBH.ResChange(result.info.resChange)
  if this:GetData("CurrentTutorial") == 5 then
    SetDefaultGroup()
  end
  local gachaInfo = this:GetData("fci/gachainfo/")
  this:BroadcastGameEvent("OnGachaResponse")
  for _, v in pairs(gachaInfo) do
    if v.id == result.info.id then
      v.gachaCountToday = result.gachaCountToday
      if result.nextFreeGachaTime and result.nextFreeGachaTime > 0 then
        v.nextFreeGachaTime = result.nextFreeGachaTime
      end
      v.nextGetPurpleCount = result.nextGetPurpleCount
      if v.activityInfo and result.activityInfo then
        for k2, v2 in pairs(v.activityInfo) do
          if v2.activityId == result.activityInfo.activityId then
            v2.curDropActivityActor = result.activityInfo.curDropActivityActor
            v2.nextGetPurpleCount = result.activityInfo.nextGetPurpleCount
            v2.preDropActivityActor = result.activityInfo.preDropActivityActor
            v2.getActivityActorCount = result.activityInfo.getActivityActorCount
          end
        end
      end
      this:SetData("fci/gachainfo/", gachaInfo)
      break
    end
  end
  this:SetData("fci/gachaResult/", result)
  m_hasOrangeActor = false
  for i = 1, #result.info.resChange do
    local change = result.info.resChange[i]
    if change.actor and 0 < change.actor.countDelta and change.actor.quality >= 4 then
      m_hasOrangeActor = true
      break
    end
  end
  if m_cenimatic ~= nil then
    WU.AcquireWindowAsync("GachaCinematic", function(window)
      _ENV["$"](window)["$$PlayCinematic"](m_cenimatic, function()
        m_cenimatic = nil
        TryToShowResult()
      end)
    end, this:GetData("NavigationContext") == "GachaResult")
  else
    TryToShowResult()
  end
end

function UpdateNormalRedPoint()
  if m_willFreeTime and 0 < m_willFreeTime then
    local b = 0 >= m_willFreeTime - CS.GameTime.serverUtc
    REF.SpriteNormalRed.gameObject:SetActive(b)
  else
    REF.SpriteNormalRed.gameObject:SetActive(false)
  end
end

function UpdateFriendRedPoint()
  local gachaType = PB.get("GachaType", 6)
  local stockAmount = 0
  if gachaType.cost.type == PB.enum.ResourceType.ResItem then
    stockAmount = this:GetData("fci/item/" .. gachaType.cost.id).count
  else
    stockAmount = this:GetData("fci/resource/" .. PB.enum.ResourceType.__keys[gachaType.cost.type] .. "_" .. gachaType.cost.id)
  end
  if stockAmount >= gachaType.cost.count then
    REF.SpriteFriendRed.gameObject:SetActive(true)
  else
    REF.SpriteFriendRed.gameObject:SetActive(false)
  end
end

function UpdateRedPoint()
  UpdateNormalRedPoint()
  UpdateFriendRedPoint()
end

function SetPeriodicButton(started)
  m_periodicOn = started
  _ENV["$"](REF.ButtonGachaPeriodic).SpriteIconOn.gameObject:SetActive(started)
  _ENV["$"](REF.ButtonGachaPeriodic).SpriteIconOff.gameObject:SetActive(not started)
end

function SetDefaultGroup()
  local actors = this:GetData("fci/actor")
  local info = _ENV["!"]({})
  for _, actor in pairs(actors) do
    local config = PB.get("ActorConfig", actor.id)
    if config == nil then
      error("TutorialManager", "no gacha actor id " .. actor.id .. " in ActorConfig!")
      return
    end
    table.insert(info, {
      kind = config.kind,
      uid = actor.uid
    })
  end
  table.sort(info, function(a, b)
    if a.kind ~= b.kind then
      return a.kind < b.kind
    else
      return a.uid < b.uid
    end
  end)
  info[#info], info[#info - 1] = info[#info - 1], info[#info]
  local members = {}
  for i = 1, 6 do
    local member = {
      index = i - 1,
      actorUid = 0
    }
    local index, actor
    if i <= 3 then
      index, actor = info:find(function(k, v)
        return v.kind == PB.enum.ActorType.Main
      end)
    else
      index, actor = info:find(function(k, v)
        return v.kind == PB.enum.ActorType.Sub
      end)
    end
    if actor ~= nil then
      member.actorUid = actor.uid
      table.remove(info, index)
    end
    table.insert(members, member)
  end
  local luaTable = {members = members}
  local wireFormatTable = ProtobufT("ApiActorGroup", luaTable)
  this:GameRequest("fci/ActorGroup/1"):Put(wireFormatTable, function()
    local groups = this:GetData("fci/ActorGroup/")
    if groups ~= nil then
      groups["1"].members = members
      this:SetData("fci/ActorGroup/", groups)
    end
  end)
end
