local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local S = require("Common/Singleton")
local U = require("Common/Util")
local ACU = require("Common/ActivityUtil")
local m_iaphandler = S:Get("IAPHandler")
local m_data
local m_remainTime = 0
local m_slotRef, m_giftInfos, m_mapMallRecord
local m_viewedList = {}

function UpdateWidget(go, wrapIndex, realIndex)
  local slotRef = _ENV["$"](go)
  if m_giftInfos then
    local giftInfo = m_giftInfos[realIndex + 1]
    if giftInfo then
      m_slotRef[giftInfo.id] = slotRef
      slotRef["$$SetData"](giftInfo, _ENV["$"](this))
    end
  end
end

function Awake()
  this:RegisterGameEvent("UpdateMallData", UpdateData)
  this:RegisterGameEvent("UpdateSale", function(id, buyCount)
    if m_slotRef == nil then
      return
    end
    local ref = m_slotRef[id]
    if ref then
      ref["$$SetBuyCount"](tonumber(buyCount))
    end
    SetDataBuyCount(id, tonumber(buyCount))
    UpdateData()
  end)
  this:RegisterGameEvent("PushTypePurchaseBuyRecord", function(params)
    SetProductBuyCount(params[1], tonumber(params[2]))
  end)
  this:Bind("ActivityList", function(activityResp)
    if activityResp then
      UpdateData()
    end
  end)
end

function SetDataBuyCount(id, buyCount)
  local mallData = m_mapMallRecord[id]
  if mallData then
    mallData.buyCount = buyCount
  end
end

-- Kept for callers that need the first matching row; product updates use the
-- all-row helper below because seasonal rows can share one product ID.
function GetMallInfoByProductID(proID)
  if m_data == nil then
    return nil
  end
  for _, v in ipairs(m_data) do
    if v.productId == proID then
      return v
    end
  end
  return nil
end

function UpdateBuyCount()
  this:GameRequest("fci/mall-info/"):Get(function(response)
    for k, v in pairs(response.mallRecord.summary) do
      local key = tonumber(k)
      local buyCount = tonumber(v.buyCount)
      SetDataBuyCount(key, buyCount)
    end
    for k, v in pairs(response.purchaseRecord.buyRecord) do
      SetProductBuyCount(k, tonumber(v.buyCount))
    end
    UpdateData()
  end)
end

function SetProductBuyCount(proID, buyCount)
  if m_data == nil or buyCount == nil then
    return
  end
  -- Seasonal Mall rows reuse a product ID; the ledger belongs to the product.
  for i, v in ipairs(m_data) do
    if v.productId == proID then
      SetDataBuyCount(v.id, buyCount)
      local ref = m_slotRef and m_slotRef[v.id]
      if ref then
        ref["$$SetBuyCount"](buyCount)
      end
    end
  end
end

function IsSaleFinish(data)
  if data.buyCount and data.buyCount >= data.maxBuyCount and data.maxBuyCount > 0 then
    return true
  end
  return false
end

function Start()
  this:RegisterGameEvent("UpdateActivityViewed", function()
    WU.SetGameDataCache("ActivityId_Viewed" .. DB:GetData("playerId"), m_viewedList)
  end)
end

function UpdateData()
  if m_data == nil then
    return
  end
  local activityInfoList = DB:GetData("ActivityList")
  m_slotRef = {}
  local activityIDs = {}
  m_viewedList = {}
  if activityInfoList then
    local localTime = CS.GameTime.serverNow
    local dayOfWeek = U.ToInt(localTime.DayOfWeek)
    if dayOfWeek == 0 then
      dayOfWeek = 7
    end
    for i, v in pairs(activityInfoList) do
      local preheartTime
      if v.type == PB.enum.ActivityType.Mall then
        local shouldAdd = false
        local isPreheat = false
        local maxDay = 0
        local minDay = 999
        if v.weekdays then
          for i, day in ipairs(v.weekdays) do
            if day < minDay then
              minDay = day
            end
            if day > maxDay then
              maxDay = day
            end
          end
        end
        local preheatOrder = v.timestampPreheat < CS.GameTime.serverUtc and CS.GameTime.serverUtc < v.timestampStart
        if v.timestampPreheat and 0 < v.timestampPreheat then
          shouldAdd = v.timestampPreheat < CS.GameTime.serverUtc and CS.GameTime.serverUtc < v.timestampStart
          isPreheat = true
        end
        if v.weekdayPreheart and 0 < v.weekdayPreheart and CS.GameTime.serverUtc > v.timestampStart and CS.GameTime.serverUtc < v.timestampEnd then
          if dayOfWeek < minDay then
            local willTime = CS.System.DateTime(localTime.Year, localTime.Month, localTime.Day)
            willTime = willTime:AddDays(minDay - dayOfWeek)
            isPreheat = true
            shouldAdd = true
            preheartTime = CS.GameTime.ToSecondsForTimeZone(willTime)
          elseif dayOfWeek > maxDay then
            local willTime = CS.System.DateTime(localTime.Year, localTime.Month, localTime.Day)
            willTime = willTime:AddDays(minDay + 7 - dayOfWeek)
            isPreheat = true
            shouldAdd = true
            preheartTime = CS.GameTime.ToSecondsForTimeZone(willTime)
          elseif dayOfWeek >= minDay and dayOfWeek <= maxDay then
            shouldAdd = true
            local willTime = CS.System.DateTime(localTime.Year, localTime.Month, localTime.Day)
            willTime = willTime:AddDays(maxDay - dayOfWeek + 1)
            v.timestampEnd = CS.GameTime.ToSecondsForTimeZone(willTime)
          end
        end
        if not shouldAdd and ACU.IsOpenForDoing(v) then
          shouldAdd = true
        end
        if shouldAdd then
          local activity = {}
          activity.id = v.activityId
          activity.startTime = v.timestampStart
          activity.endTime = v.timestampEnd
          activity.isPreheat = isPreheat
          activity.preheatOrder = preheatOrder
          activity.isNew = ACU.IsOpenForDoing(v) and not table.has(WU.GetGameDataCache("ActivityId_Viewed" .. DB:GetData("playerId")), v.activityId)
          local refreshTime = CS.System.DateTime(localTime.Year, localTime.Month, localTime.Day)
          if 0 < maxDay then
            local weekRefreshTime = refreshTime:AddDays(maxDay - dayOfWeek + 1)
            weekRefreshTime = CS.GameTime.ToSecondsForTimeZone(weekRefreshTime)
            if weekRefreshTime > activity.endTime then
              weekRefreshTime = nil
            end
            activity.weekRefreshTime = weekRefreshTime
          end
          local dayRefreshTime = v.timestampEnd
          activity.dayRefreshTime = dayRefreshTime
          if ACU.IsOpenForDoing(v) then
            table.insert(m_viewedList, v.activityId)
          end
          if preheartTime then
            activity.preheartTime = preheartTime
          end
          activityIDs[#activityIDs + 1] = activity
        end
      end
    end
  end
  local curDirctPurchaseGiftIds = WU.GetGameDataCache("DirctPurchaseGiftIds" .. DB:GetData("playerId")) or {}
  local ids = {}
  local giftInfos = {}
  for j, giftInfo in ipairs(m_data) do
    if giftInfo.activityId == 0 then
      giftInfos[#giftInfos + 1] = giftInfo
    else
      for i, activity in ipairs(activityIDs) do
        if giftInfo.activityId == activity.id then
          local product
          local isProduct = giftInfo.productId and giftInfo.productId ~= ""
          if isProduct then
            product = m_iaphandler.GetProductsByID(giftInfo.productId)
          end
          if not isProduct or product ~= nil then
            giftInfo.startTime = activity.startTime
            giftInfo.endTime = activity.endTime
            giftInfo.isPreheat = activity.isPreheat
            giftInfo.preheartTime = activity.preheartTime
            giftInfo.preheatOrder = activity.preheatOrder
            giftInfo.isNew = activity.isNew
            giftInfos[#giftInfos + 1] = giftInfo
          end
          if giftInfo.maxCountClearInterval == PB.enum.MallBuyIntervalType.Day then
            giftInfo.refreshTime = activity.dayRefreshTime
          elseif giftInfo.maxCountClearInterval == PB.enum.MallBuyIntervalType.Week then
            giftInfo.refreshTime = activity.weekRefreshTime
          else
            giftInfo.refreshTime = nil
          end
          if giftInfo.giftPage == 3 then
            table.insert(ids, giftInfo.activityId)
            giftInfo.isNew = table.find(curDirctPurchaseGiftIds, function(k, v)
              return v == giftInfo.activityId
            end) == nil
          end
          break
        end
      end
    end
  end
  if 0 < #giftInfos and giftInfos[1].giftPage == 3 then
    WU.SetGameDataCache("DirctPurchaseGiftIds" .. DB:GetData("playerId"), ids)
  end
  table.sort(giftInfos, function(a, b)
    if IsSaleFinish(a) == true and IsSaleFinish(b) == false then
      return false
    elseif IsSaleFinish(a) == false and IsSaleFinish(b) == true then
      return true
    elseif IsSaleFinish(a) == false and IsSaleFinish(b) == false then
      if a.preheatOrder == true and b.preheatOrder == false then
        return false
      elseif a.preheatOrder == false and b.preheatOrder == true then
        return true
      else
        return a.id < b.id
      end
    else
      return a.id < b.id
    end
  end)
  if #giftInfos == 0 then
    REF.SpriteTips.gameObject:SetActive(true)
    REF.Drag.gameObject:SetActive(false)
  else
    REF.SpriteTips.gameObject:SetActive(false)
    REF.Drag.gameObject:SetActive(true)
  end
  local slotIndex = 0
  local slotCount = #REF.ItemList
  for i, v in ipairs(giftInfos) do
    slotIndex, slotCount = SetAttrImpl(v, REF.ItemList, slotIndex, slotCount)
  end
  for i = slotIndex, slotCount - 1 do
    REF.ItemList[i]["$gameObject"]:SetActive(false)
  end
  REF.ItemList.UITable:Reposition()
  m_giftInfos = giftInfos
end

function SetGiftPage(giftPage)
  m_data = PB.all("Mall"):where(function(_, p)
    return p.isMonth == false and p.giftPage == giftPage
  end):toarray()
  m_mapMallRecord = {}
  for i, v in ipairs(m_data) do
    m_mapMallRecord[v.id] = v
    v.buyCount = 0
  end
  WU.TraverseChildren(REF.ItemList, function(g)
    g:SetActive(false)
  end)
  UpdateBuyCount()
  REF.ScrollView.UIScrollView:ResetPosition()
end

function SetAttrImpl(attr, listGasket, slotIndex, slotCount)
  local slotRef
  if slotIndex < slotCount then
    slotRef = listGasket[slotIndex]
  else
    local source = listGasket[0]["$gameObject"]
    local clone = CS.UnityEngine.GameObject.Instantiate(source, listGasket.transform)
    clone.name = source.name
    slotRef = _ENV["$"](clone)
    slotCount = slotCount + 1
  end
  slotRef["$gameObject"]:SetActive(true)
  slotRef["$$SetData"](attr)
  slotIndex = slotIndex + 1
  m_slotRef[attr.id] = slotRef
  return slotIndex, slotCount
end
