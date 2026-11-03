local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local ACU = require("Common/ActivityUtil")
local S = require("Common/Singleton")
local U = require("Common/Util")
local NU = require("Common/NotepadUtil")
local DU = require("Common/DungeonUtil")
local m_gachaId = 1
local m_gachaDetail, m_gachaInfo, m_activityId
local GM_activityId = 14001

function InitWindow()
  this:BindRemote(DB:GameRequest("fci/gachainfo/"), OnGachaInfoChanged)
  this:RegisterGameEvent("ActivityStatusChanged", OnActivityStatusChanged)
  REF.GM_AdChangeButton.gameObject:SetActive(false)
  REF.WidgetGachaInsertCoin["$SetGachaId"](m_gachaId)
  REF.WidgetGachaInsertCoin["$SetGachaClickCallback"](function(times)
    if not DU.IsWaitingForDungeonTeam(function()
      DoGacha(times)
    end) then
      DoGacha(times)
    end
  end)
  REF.GachaActivitySelection["$SetClickCallback"](function(activityInfo)
    SetStatus(activityInfo)
  end)
end

function SetupWindow()
  WU.BindButtonEvent(REF.ButtonActorCollections, function()
    if not WU.WindowIsLocked(PB.enum.UnlockWindow.Journey, PB.enum.UnlockWindow.Journey) then
      NU.GetCollectionsActor(function()
        WU.AcquireWindowAsync("CollectionsActor")
      end)
    end
  end)
end

function OnGachaInfoChanged(gachaInfo)
  if gachaInfo then
    m_gachaInfo = gachaInfo
    local _, gacha = _ENV["!"](gachaInfo):find(function(k, v)
      return v.id == m_gachaId
    end)
    m_gachaDetail = gacha
    OnActivityChanged()
  end
end

function OnActivityChanged()
  local activities = _ENV["!"](this:GetData("ActivityList")):where(function(k, v)
    if v.type == PB.enum.ActivityType.GachaProbabilityUp then
      return ACU.IsOpenForDoing(v)
    end
  end)
  activities = activities or {}
  local activityInfo = _ENV["!"](m_gachaDetail.activityInfo)
  local tempList = {}
  for _, a in pairs(activities) do
    local _, temp = activityInfo:find(function(k, v)
      return v.activityId == a.activityId
    end)
    if temp == nil then
      table.insert(tempList, {
        activityId = a.activityId,
        curDropActivityActor = 0,
        preDropActivityActor = 0,
        getActivityActorCount = 0,
        nextGetPurpleCount = PB.get("GachaType", m_gachaDetail.id).actorDropCount
      })
    else
      table.insert(tempList, temp)
    end
  end
  m_gachaDetail.activityInfo = tempList
  activityInfo = _ENV["!"](m_gachaDetail.activityInfo)
  local _, temp = activityInfo:find(function(k, v)
    return v.activityId == 0
  end)
  if temp == nil then
    table.insert(activityInfo, {
      activityId = 0,
      curDropActivityActor = 0,
      preDropActivityActor = 0,
      getActivityActorCount = 0,
      nextGetPurpleCount = m_gachaDetail.nextGetPurpleCount
    })
  else
    temp.nextGetPurpleCount = m_gachaDetail.nextGetPurpleCount
    temp.nextWillFreeTime = m_gachaDetail.nextFreeGachaTime
  end
  SetActivity()
end

function OnActivityStatusChanged(category, activityId, status, type, activity)
  if type == PB.enum.ActivityType.GachaProbabilityUp then
    local activityInfo = _ENV["!"](m_gachaDetail.activityInfo)
    local opened = ACU.IsOpenForDoing(activity)
    local i, a = activityInfo:find(function(k, v)
      return v.activityId == activityId
    end)
    if opened then
      if a == nil then
        table.insert(activityInfo, {
          activityId = activityId,
          curDropActivityActor = 0,
          preDropActivityActor = 0,
          nextGetPurpleCount = PB.get("GachaType", m_gachaDetail.id).actorDropCount
        })
      end
    elseif i ~= nil then
      table.remove(activityInfo, i)
    end
    this:SetData("gacha/" .. m_gachaId .. "/SelectedActivityId", 1)
    SetActivity()
  end
end

function SetActivity()
  local activities = m_gachaDetail.activityInfo
  table.sort(activities, function(a, b)
    if a.activityId == 0 then
      return false
    end
    if b.activityId == 0 then
      return true
    end
    return a.activityId < b.activityId
  end)
  local selectedActivityId = this:GetData("gacha/" .. m_gachaId .. "/SelectedActivityId") or 1
  local jumpActivityId = this:GetData("GachaJumpId")
  if jumpActivityId and jumpActivityId ~= 0 then
    local index, _ = table.find(activities, function(k, v)
      return v.activityId == jumpActivityId
    end)
    this:SetData("GachaJumpId", nil)
    if index then
      selectedActivityId = index
      this:SetData("gacha/" .. m_gachaId .. "/SelectedActivityId", index)
    end
  end
  if this:GetData("CurrentTutorial") == 5 then
    selectedActivityId = #activities
  end
  SetStatus(activities[selectedActivityId])
  REF.GachaActivitySelection["$SetView"](activities, m_gachaDetail)
end

function SetStatus(activity)
  if activity then
    REF.WidgetGachaInsertCoin["$SetActivity"](activity)
    REF.NodeNormal.gameObject:SetActive(activity.activityId == 0)
    if activity.activityId ~= 0 then
      if m_activityId ~= activity.activityId then
        m_activityId = activity.activityId
        WU.RemoveAllChild(REF.NodeEffect)
        local ad = PB.get("GachaAd", activity.activityId)
        if ad == nil and activity.activityId == 14019 then
          -- Local opening: ActivityGachaNormalUp has 14019 but GachaAd has no
          -- row. Keep the existing ordinary background; retain UP selection
          -- and all original non-14019 missing-config/error behavior.
          REF.NodeNormal.gameObject:SetActive(true)
        elseif ad == nil then
          error("Gacha", "no bg effect prefab for gacha activityId " .. activity.activityId .. " in GachaAd.xlsx")
        else
          GM_activityId = activity.activityId
          CreateEffect(ad)
        end
      end
    else
      m_activityId = nil
      WU.RemoveAllChild(REF.NodeEffect)
    end
  end
end

function DoGacha(times)
  if m_gachaDetail then
    S:Get("Gacha").DoGacha(m_gachaDetail, {
      activityId = m_activityId,
      times = times,
      cinematic = "ATM",
      showContinue = false
    })
  end
end

function OnClickGM_AdChange()
  WU.RemoveAllChild(REF.NodeEffect)
  GM_activityId = GM_activityId + 1
  local ad = PB.get("GachaAd", GM_activityId)
  if ad == nil then
    GM_activityId = 14001
    ad = PB.get("GachaAd", GM_activityId)
  end
  CreateEffect(ad)
end

function CreateEffect(ad)
  local obj = WU.InstantiateWindowPrefab(REF.NodeEffect, ad.displayName)
  if _ENV["$"](obj)["$Animator"] ~= nil then
    _ENV["$"](obj)["$Animator"]:Play("Open", -1, 0)
  end
  WU.HandleGameObjectAttached(obj)
  SetActorButtons()
end

function SetActorButtons()
  local ref = reRef(REF.NodeEffect)
  WU.TraverseChildren(ref.RoleButtons, function(go)
    local name = go.name
    if string.sub(name, 1, 4) == "Role" then
      local id = U.ToInt(string.sub(name, string.len("Role") + 2, string.len(name)))
      WU.BindButtonEvent(go, function()
        if not WU.WindowIsLocked(PB.enum.UnlockWindow.Journey, PB.enum.UnlockWindow.Journey) then
          NU.GetCollectionsActor(function(actors)
            local actor = {}
            for k, v in pairs(actors) do
              if v.id == id then
                actor = v
                break
              end
            end
            WU.AcquireWindowAsync("CollectionsActorDetail", function(w)
              _ENV["$"](w)["$$SetActor"](actor)
            end)
          end)
        end
      end)
    end
  end)
end
