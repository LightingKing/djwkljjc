local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local ACU = require("Common/ActivityUtil")
local DBH = require("Manager/DataBindingHandler")
local S = require("Common/Singleton")
local ATU = require("Common/AutumnUtil")
local m_gachaClaw, m_cost, m_cnt

-- Local-only durable operation key. WindowUtil's GameDataCache binding is
-- saved by ConfigDataManager.OnGameDataCache before the request is sent.
-- Original remote POST and animation paths remain below unchanged.
local function LocalClawCache()
  local iap = S:Get("IAPHandler")
  if not (iap and iap.IsLocalAccountServer and iap.IsLocalAccountServer()) then
    return nil
  end
  local playerId = this:GetData("playerId")
  if playerId == nil then
    return nil
  end
  local name = "LJJCLocalClaw_" .. tostring(playerId)
  return name, WU.GetGameDataCache(name) or {counter = 0}
end

function SetupWindow()
  WU.BindButtonEvent(REF.ButtonRewardPreview, OnButtonRewardPreview)
  WU.BindButtonEvent(REF.ButtonRecord, OnButtonRecord)
  WU.BindButtonEvent(REF.ButtonGacha, OnButtonGacha)
  WU.BindButtonEvent(REF.ButtonStock, OnButtonStock)
end

function InitWindow()
  m_cost = PB.index("Misc", 1).clawCraneCost
  m_cnt = DB:GetData("fci/item/" .. m_cost.id).count
  REF.LabelStock.ResourcePrinter:SetResource(m_cost.type, m_cost.id)
  REF.LabelOneCost.ResourcePrinter:SetResource(m_cost.type, m_cost.id, m_cost.count)
  this:GameRequest("fci/ClawCranePrize/"):Get(function(resp)
  end)
  this:Bind("NavigationContext", function(name)
    if name == REF.root.name then
      REF.NodeNormal.UIPlayTween:ResetToBeginning()
      REF.NodeNormal.UIPlayTween:Play(true)
    end
  end)
end

function OnButtonGacha()
  local cacheName, cache = LocalClawCache()
  if cacheName and cache.pending then
    DoGacha()
    return
  end
  if m_cnt < m_cost.count then
    WU.ShowHintText(WU.GetString("Error_CostResourceNotEnough"))
    return
  end
  ATU.HasGoods(DoGacha, function()
    if not IsDisableTip() then
      WU.ShowMessageYesNo(WU.GetString("Error_ClawCraneSellOut"), function(result, toggleVal)
        if result == "YES" then
          DoGacha()
        end
        if toggleVal then
          DisableTip()
        end
      end, nil, nil, nil, nil, true, WU.GetString("Window_Disable_For_Today"))
    else
      DoGacha()
    end
  end)
end

function DoGacha()
  local cacheName, cache = LocalClawCache()
  if cacheName then
    if not cache.pending then
      cache.counter = (cache.counter or 0) + 1
      cache.pending = table.concat({"claw", tostring(this:GetData("playerId")),
        tostring(math.floor(CS.GameTime.serverUtc)), tostring(cache.counter)}, "-")
      WU.SetGameDataCache(cacheName, cache)
    end
    local requestId = cache.pending
    this:GameRequest("fci/ClawCraneBingo/"):Query("localRequestId", requestId):Post({}, function(result)
      if result ~= nil then
        this:SetData("fci/ClawCraneBingo/", result)
        DBH.ResChange(result.resChange)
        local latest = WU.GetGameDataCache(cacheName) or cache
        if latest.pending == requestId then
          latest.pending = nil
          WU.SetGameDataCache(cacheName, latest)
        end
        WU.AcquireWindowAsync("GachaClaw")
      end
    end)
    return
  end
  this:GameRequest("fci/ClawCraneBingo/"):Post({}, function(result)
    if result ~= nil then
      this:SetData("fci/ClawCraneBingo/", result)
      DBH.ResChange(result.resChange)
      WU.AcquireWindowAsync("GachaClaw")
    end
  end)
end

function OnButtonRewardPreview()
  WU.AcquireWindowAsync("EventAutumnRewardPreview", function(window)
    _ENV["$"](window)["$$SetMode"]("Reward")
  end)
end

function OnButtonRecord()
  WU.AcquireWindowAsync("EventAutumnRewardPreview", function(window)
    _ENV["$"](window)["$$SetMode"]("Record")
  end)
end

function OnButtonStock()
  local cost = PB.index("Misc", 1).clawCraneCost
  WU.ShowResourceDetail(cost.type, cost.id)
end

function IsDisableTip()
  local isDisableByUser = false
  local tipInfo = WU.GetGameDataCache("GachaClawTipInfo")
  if tipInfo and tipInfo.isDisable then
    local disableDate = CS.GameTime.UtcToLocal(tipInfo.time)
    if WU.IsToday(disableDate) then
      isDisableByUser = true
    end
  end
  return isDisableByUser
end

function DisableTip()
  local disableInfo
  disableInfo = {
    time = CS.GameTime.serverUtc,
    isDisable = true
  }
  WU.SetGameDataCache("GachaClawTipInfo", disableInfo)
end
