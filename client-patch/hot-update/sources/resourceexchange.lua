local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local DBH = require("Manager/DataBindingHandler")
local m_exchangeType, m_resourceType, m_param, m_autoClose
local m_goldChangeType = {
  change60 = 1000201,
  change500 = 1000202,
  change1000 = 1000203
}
local m_exchangeList

function SetupWindow()
  WU.BindButtonEvent(_ENV["$"](REF.OtherExchange).ButtonExchange, OnExchangeClick)
  WU.TraverseChildren(_ENV["$"](REF.MoneyExchange).ExchangeList, function(go)
    WU.BindButtonEvent(_ENV["$"](go).ButtonExchange, OnExchangeClick)
  end)
end

function InitWindow()
  WU.ToggleRendering(REF.Content, false)
  local resourceType = this:GetData("ExchangeResourceType")
  if resourceType then
    ShowExchange(resourceType)
  end
end

function UninitWindow()
  this:SetData("ExchangeResourceType", nil)
end

function ShowExchange(resourceType, param, autoClose)
  m_resourceType = resourceType
  m_param = param
  m_autoClose = autoClose
  local all = PB.all("RefreshPrice")
  if resourceType == PB.enum.ResourceType.ResPlayerGold then
    local info = {}
    local _, t = table.find(all, function(k, v)
      return v.buy and v.type == m_goldChangeType.change60
    end)
    if t ~= nil then
      table.insert(info, t)
    end
    _, t = table.find(all, function(k, v)
      return v.buy and v.type == m_goldChangeType.change500
    end)
    if t ~= nil then
      table.insert(info, t)
    end
    _, t = table.find(all, function(k, v)
      return v.buy and v.type == m_goldChangeType.change1000
    end)
    if t ~= nil then
      table.insert(info, t)
    end
    SetView(info, REF.MoneyExchange)
    REF.MoneyExchange.gameObject:SetActive(true)
    REF.OtherExchange.gameObject:SetActive(false)
  else
    local _, t = table.find(all, function(k, v)
      return v.buy and v.buy.type == resourceType
    end)
    if t ~= nil then
      m_exchangeType = t.type
    end
    if param then
      this:GameRequest("fci/resbuyprice/" .. m_exchangeType .. "/" .. param):Get(function(data)
        data.type = m_exchangeType
        SetView({data}, REF.OtherExchange)
      end)
    else
      this:GameRequest("fci/resbuyprice/" .. m_exchangeType):Get(function(data)
        data.type = m_exchangeType
        SetView({data}, REF.OtherExchange)
      end)
    end
    REF.MoneyExchange.gameObject:SetActive(false)
    REF.OtherExchange.gameObject:SetActive(true)
  end
end

function SetView(result, go)
  local ref = _ENV["$"](go)
  m_exchangeList = result
  WU.ToggleRendering(REF.Content, true)
  REF.LabelTitle.UIHtmlLabel.text = WU.GetString("Window_Exchange") .. WU.GetString("ResourceTypeName_" .. m_resourceType)
  for i = 1, #result do
    local refNode = ref.ExchangeList[i - 1]
    local noTimes = false
    if result[i].remainBuyTimes then
      refNode.LabelRemainTimes.UILabel.text = tostring(result[i].remainBuyTimes)
      noTimes = result[i].remainBuyTimes <= 0
      refNode.LabelNoTimes.gameObject:SetActive(noTimes)
    end
    refNode.ButtonExchange.UIButton.isEnabled = not noTimes
    refNode.LabelCost.gameObject:SetActive(not noTimes)
    if not noTimes then
      refNode.LabelCost.ResourcePrinter:SetResource(result[i].cost.type, result[i].cost.id, result[i].cost.count)
    end
    refNode.WidgetIconSlot["$SetData"](m_resourceType, result[i].buy.id, result[i].buy.count)
    _ENV["$"](refNode.ButtonExchange)["@realResourceType"] = result[i].type
    _ENV["$"](refNode.ButtonExchange)["@index"] = i
  end
end

function OnExchangeClick(go)
  local realResourceType = _ENV["$"](go)["@realResourceType"]
  local index = _ENV["$"](go)["@index"]
  if m_resourceType == PB.enum.ResourceType.ResPlayerPower then
    local own = this:GetData("fci/resource/ResPlayerPower_0") or 0
    local baseInfo = this:GetData("fci/baseinfo/")
    local levelInfo = baseInfo and PB.get("PlayerLevelInitConfig", baseInfo.level)
    local max
    for _, increase in ipairs(levelInfo and levelInfo.incInfo or {}) do
      if increase.type == PB.enum.ResourceType.ResPlayerPower then
        max = increase.max
        break
      end
    end
    if max ~= nil and own >= max then
      WU.ShowHintText(WU.GetString("Window_ReachPowerLimit"))
      return
    end
  end
  if m_exchangeList[index].remainBuyTimes and m_exchangeList[index].remainBuyTimes <= 0 then
    WU.ShowHintText(WU.GetString("Window_ExchangesNoTimes"))
    return
  end
  WU.TryToPayResource(m_exchangeList[index].cost, function()
    this:GameRequest("fci/resbuyprice/resbuy/"):Post({resbuypriceType = realResourceType, params = m_param}, function(result)
      DBH.ResChange(result.resChange)
      if m_resourceType ~= PB.enum.ResourceType.ResPlayerGold then
        result.type = realResourceType
        SetView({result}, REF.OtherExchange)
      end
      WU.ShowRewards(result.resChange)
      this:BroadcastGameEvent("ResourceExchangeSucceed", result.buy)
      if m_autoClose then
        WU.RecycleWindow("ResourceExchange")
      end
    end)
  end)
end
