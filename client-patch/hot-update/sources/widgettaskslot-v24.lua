local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local HU = require("Common/HtmlUtil")
local U = require("Common/Util")
local GotoUtil = require("Common/GotoUtil")
local DBH = require("Manager/DataBindingHandler")
local IU = require("Common/ItemUtil")
local NU = require("Common/NotepadUtil")
local TU = require("Common/TaskUtil")
local m_task, m_styleType, m_rewardClickCall, m_showRemainTime, m_curCultivation

function Awake()
end

function Start()
  WU.BindButtonEvent(REF.BtnGo, function()
    local g = PB.get("Goto", m_task.res.gotoId)
    if g == nil then
      error("Task", "Goto id " .. m_task.res.gotoId .. " doesn't exist")
      return
    end
    if g.id == 208 then
      local actorId = this:GetData("CollectionsActorSelected")
      if actorId then
        NU.GetCollectionsActor(function(actors)
          local actor = {}
          local index, actor = table.find(actors, function(k, v)
            return v.id == actorId
          end)
          if actor then
            WU.AcquireWindowAsync("CollectionsActorDetail", function(w)
              _ENV["$"](w)["$$SetActor"](actor)
            end)
          end
        end)
      end
    elseif g.id == 209 then
      local selectedUid = this:GetData("Actor/SelectedUid")
      if selectedUid then
        this:SetData("Actor/SelectedUid", nil)
        WU.AcquireWindowAsync("ActorMain", function()
          this:SetData("Actor/SelectedUid", selectedUid)
        end)
      end
    elseif g.id == 300 then
      local isLocked, unlockLevel = WU.WindowIsLocked(PB.enum.UnlockWindow.Journey, PB.enum.UnlockWindow.Journey)
      if not isLocked then
        WU.AcquireWindowAsync("Portal", function(win)
          _ENV["$"](win)["$$OnBookClick"]()
        end)
      end
    elseif m_task.res.category == PB.enum.TaskCategory.Repeat then
      GotoUtil.Goto(g.windowName, m_task.res.taskParam)
    else
      GotoUtil.Goto(g.windowName, g.params)
    end
  end)
  WU.BindButtonEvent(REF.BtnGetReward, OnGetRewardClick)
  if REF.BtnActivate then
    WU.BindButtonEvent(REF.BtnActivate, OnClickActivate)
  end
  if REF.ButtonPlatform then
    WU.BindButtonEvent(REF.ButtonPlatform, function()
      WU.AcquireWindowAsync("PlatformStartPage", function(ui)
        _ENV["$"](ui)["$$SetData"]()
      end)
    end)
  end
end

function Update()
  if m_task == nil or m_task.endTime == 0 then
    return
  end
  if REF.NodeRemainTime and m_showRemainTime ~= false then
    local remain = m_task.endTime - CS.GameTime.serverUtc
    if 0 <= remain then
      if not REF.NodeRemainTime.gameObject.activeSelf then
        REF.NodeRemainTime.gameObject:SetActive(true)
      end
      REF.LabelRemainTime.UIHtmlLabel.text = WU.RenderCountDownTime(remain, REF.LabelRemainTime.UIHtmlLabel.fontSize)
    else
      REF.NodeRemainTime.gameObject:SetActive(false)
    end
  end
end

function SetView(taskInfo, viewType)
  local play = this:GetData("ActorCultivation/PlayTween")
  m_styleType = viewType or 1
  m_task = taskInfo
  _ENV["$"](REF.BtnGetReward)["@taskInfo"] = taskInfo
  if taskInfo and REF.Daily then
    WU.SetActive(REF.Daily, taskInfo.dailyRefresh)
  end
  local isCustom = taskInfo.res.refreshType == 1
  local hasActivePoint = taskInfo.res.activePoint ~= 0 or taskInfo.res.extraActivePoint ~= 0
  if REF.NodeRemainTime then
    REF.NodeRemainTime.gameObject:SetActive(false)
  end
  if REF.BtnActivate then
    WU.ToggleRendering(REF.BtnActivate, false)
  end
  if REF.TaskVip then
    WU.ToggleRendering(REF.TaskVip, false)
  end
  if REF.ActivePoint then
    REF.ActivePoint.gameObject:SetActive(hasActivePoint)
  end
  if REF.SpriteActivePoint_BG then
    REF.SpriteActivePoint_BG.gameObject:SetActive(hasActivePoint)
  end
  if REF.LabelActivePoint then
    REF.LabelActivePoint.UIHtmlLabel.text = fif(taskInfo.res.activePoint ~= 0, taskInfo.res.activePoint, taskInfo.res.extraActivePoint)
  end
  local rewardList
  local rewardCount = #taskInfo.res.reward
  if m_styleType == 1 then
    for i = 0, #REF.Rewards - 1 do
      REF.Rewards[i].root.gameObject:SetActive(i == rewardCount - 1)
      if i == rewardCount - 1 then
        rewardList = REF.Rewards[i]
      end
    end
  elseif m_styleType == 2 then
    rewardList = _ENV["$"](REF.RewardList)
  end
  if rewardList ~= nil then
    for i = 0, #rewardList.root - 1 do
      if rewardCount < i + 1 then
        rewardList.root[i].WidgetIconSlotTask.gameObject:SetActive(false)
      else
        local data = taskInfo.res.reward[i + 1]
        rewardList.root[i].WidgetIconSlotTask["$SetData"](data.type, data.id, data.count)
        rewardList.root[i].WidgetIconSlotTask.gameObject:SetActive(true)
      end
    end
  end
  if m_styleType == 3 then
    REF.RewardProgressPoint.UISprite.spriteName = "ActorCultivation/ActorCultivation_bar_icon_" .. taskInfo.actorCultivationRewardProgress
  end
  if m_styleType == 1 then
    REF.ProgressBar.UIProgressBar.value = taskInfo.progress / taskInfo.res.maxProgress
    if taskInfo.progress >= taskInfo.res.maxProgress then
      REF.SpriteProgress.UISprite.spriteName = "levelup_bar_02"
    else
      REF.SpriteProgress.UISprite.spriteName = "levelup_bar_01"
    end
  end
  REF.LabelProgress.UIHtmlLabel.text = taskInfo.progress .. "/" .. taskInfo.res.maxProgress
  local text = WU.GetString("TaskName_" .. taskInfo.id)
  REF.LabelTaskName.UIHtmlLabel.text = text
  REF.LabelTaskDesc.UIHtmlLabel.text = WU.GetString("TaskDesc_" .. taskInfo.id, HU.ApplyFontColor(taskInfo.res.maxProgress, "#0199E9"), GetParam())
  if m_styleType == 1 then
    REF.ButtonPlatform.gameObject:SetActive(false)
    if taskInfo.res.goalType == PB.enum.TaskType.GameCenterLoginMTimes then
      REF.SpriteDoing.gameObject:SetActive(false)
      REF.ButtonPlatform.gameObject:SetActive(taskInfo.res.gotoId == 0 and not taskInfo.isDone and not taskInfo.timeout)
    else
      REF.SpriteDoing.gameObject:SetActive(taskInfo.res.gotoId == 0 and not taskInfo.isDone and not taskInfo.timeout)
      REF.ButtonPlatform.gameObject:SetActive(false)
    end
  elseif m_styleType == 2 then
    REF.SpriteDoing.gameObject:SetActive(taskInfo.res.gotoId == 0 and not taskInfo.isDone and not taskInfo.timeout)
  elseif m_styleType == 3 then
    local isVipTask = taskInfo.actorCultivationVipOnly
    if isVipTask then
      WU.ToggleRendering(REF.TaskVip, true)
    end
    local infos = this:GetData("ActorCultivation")
    local vip = false
    if infos then
      for _, cultivation in pairs(infos) do
        if taskInfo.actorCultivationActivityId == cultivation.activityId then
          m_curCultivation = cultivation
          break
        end
      end
      if m_curCultivation ~= nil then
        WU.ToggleRendering(REF.BtnActivate, not m_curCultivation.vip and isVipTask)
        WU.ToggleRendering(REF.StateNode, m_curCultivation.vip or not isVipTask)
      end
    end
    REF.SpriteDoing.gameObject:SetActive(taskInfo.res.gotoId == 0 and not taskInfo.isDone and not taskInfo.timeout)
  end
  REF.BtnGo.gameObject:SetActive(taskInfo.res.gotoId ~= 0 and not taskInfo.isDone)
  REF.SpriteCompleted.gameObject:SetActive(taskInfo.rewardGot)
  if REF.SpriteTimeout then
    REF.SpriteTimeout.gameObject:SetActive(not taskInfo.rewardGot and taskInfo.timeout)
  end
  REF.BtnGetReward.gameObject:SetActive(taskInfo.isDone and not taskInfo.rewardGot and not taskInfo.timeout)
  if taskInfo.rewardGot or taskInfo.timeout then
    REF.Container.UIWidget.alpha = 0.6
  else
    REF.Container.UIWidget.alpha = 1
  end
  if REF.ActorCultivationVisual and play then
    REF.ActorCultivationVisual.UIPlayTween:Play(true)
  end
end

function OnGetRewardClick(go)
  if m_rewardClickCall then
    m_rewardClickCall(go)
    return
  end
  this:GameRequest("fci/task/" .. m_task.id .. "/reward/"):Post(nil, function(result)
    DBH.ResChange(result.resChange)
    WU.ShowRewards(result.resChange)
    -- ScTasksChange and this HTTP callback can complete in either order.  Do
    -- not remove/reinsert from the cached category list: that local write can
    -- overwrite a concurrent push and make another pending task disappear.
    local all = this:GetData("fci/task/")
    for category, taskList in pairs(all) do
      local _, task = _ENV["!"](taskList):find(function(k, v)
        return v.id == m_task.id
      end)
      if task ~= nil then
        task.rewardGot = true
        this:SetData("fci/task/", all)
        break
      end
    end
    local points = this:GetData("fci/task/activepoint/")
    points.activePoint = result.activePoint
    this:SetData("fci/task/activepoint/", points)
    -- Reconcile against the server after the immediate local button update.
    -- TU.InitData preserves special categories and applies the complete
    -- delTasks projection, so pending rows cannot be lost to callback order.
    this:GameRequest("fci/task/"):SyncInput(false):Get(TU.InitData)
  end)
end

function GetParam()
  local param = m_task.res.taskParam
  if param[1] ~= nil and (m_task.res.goalType == PB.enum.TaskType.BalanceP0DungeonMTimes or m_task.res.goalType == PB.enum.TaskType.BalanceP0DungeonMTimesP1Star or m_task.res.goalType == PB.enum.TaskType.BalanceP0DungeonMTimesActorPN or m_task.res.goalType == PB.enum.TaskType.BalanceP0GoldMTimes) then
    if param[1] == 0 then
      return WU.GetString("Task_AnyDugeon")
    else
      return WU.GetString("DungeonName_" .. param[1])
    end
  end
  return ColorParam(param)
end

function ColorParam(param)
  local p = {}
  for i = 1, #param do
    table.insert(p, HU.ApplyFontColor(param[i], "#0199E9"))
  end
  return table.unpack(p)
end

function SetRewardClick(call)
  m_rewardClickCall = call
end

function SetActivePointIcon(spriteName)
  REF.ActivePoint.UISprite.spriteName = spriteName
end

function ToggleRemainTime(isShow)
  REF.NodeRemainTime.gameObject:SetActive(isShow)
  m_showRemainTime = isShow
end

function OnClickActivate()
  if m_curCultivation then
    WU.AcquireWindowAsync("ActorCultivationBuy", function(win)
      local costs = PB.get("ActorCultivation", m_curCultivation.activityId).resActiveVip
      _ENV["$"](win)["$$SetData"]({
        isVip = m_curCultivation.vip,
        cost = costs,
        activityId = m_curCultivation.activityId
      })
    end)
  end
end
