require("Common/RefInject")
local PB = require("Common/PbHelper")
local S = require("Common/Singleton")
local profiler = require("Internal/profiler")
local m_funcInfo = {}
local WU = {
  SetResource = CS.UIWindowHelp.SetResource
}
WU.LockInputFlag = PB.enum.LockInputFlag

function EnableUIInstantUnload(enable)
  CS.UIWindowManager.Instance:EnableUIInstantUnload(enable)
end

function TryToPayImpl(type, id, count, onSucceed, onFailed)
  local DB = CS.DataBindingManager.Instance
  if type ~= PB.enum.ResourceType.ResNone then
    local stock = 0
    if type == PB.enum.ResourceType.ResItem then
      local item = DB:GetData("fci/item/" .. id)
      if item then
        stock = item.count
      end
    else
      stock = DB:GetData("fci/resource/" .. PB.enum.ResourceType.__keys[type] .. "_" .. tostring(id or 0))
    end
    if count <= stock then
      if onSucceed then
        onSucceed()
      end
    elseif onFailed then
      onFailed()
    else
      local tip = WU.GetString("Window_ResourceTypeNotEnough", WU.GetString("ResourceTypeName_" .. type))
      if type == PB.enum.ResourceType.ResPlayerMoney then
        DB:BroadcastGameEvent("ShowMessageYesNo", tip, function(result)
          if result == "YES" then
            WU.AcquireWindowAsync("Purchase")
          end
        end, WU.GetString("Window_Recharge"))
      else
        local canExchange = false
        local refreshPriceList = PB.all("RefreshPrice")
        for _, refreshPrice in pairs(refreshPriceList) do
          if refreshPrice.buy and refreshPrice.buy.type == type and refreshPrice.buy.id == id then
            canExchange = true
            break
          end
        end
        if canExchange then
          DB:BroadcastGameEvent("ShowMessageYesNo", tip, function(result)
            if result == "YES" then
              DB:SetData("ExchangeResourceType", type)
              WU.AcquireWindowAsync("ResourceExchange")
            end
          end, WU.GetString("Window_Exchange"))
        else
          DB:BroadcastGameEvent("ShowHintText", tip)
        end
      end
    end
    return count <= stock
  else
    return false
  end
end

function WU.TryToPay(type, id, count, onSucceed, onFailed)
  if type == PB.enum.ResourceType.ResPlayerMoney then
    local onSucceedProxy = onSucceed

    function onSucceedProxy()
      S:Get("IAPHandler").MidasProxy(onSucceed)
    end

    local flag = WU.GetGameDataCache("purchaseTips" .. CS.DataBindingManager.Instance:GetData("playerId"))
    if flag == nil then
      flag = false
    end
    local isEnough, bindCost, noBindCost = WU.IsEnoughMoney(count, type)
    if not flag and isEnough and 0 < noBindCost then
      WU.AcquireWindowAsync("PurchaseDlg", function(ui)
        _ENV["$"](ui)["$$SetData"](type, count, onSucceedProxy, onFailed)
      end)
      return false
    else
      return TryToPayImpl(type, id, count, onSucceedProxy, onFailed)
    end
  else
    return TryToPayImpl(type, id, count, onSucceed, onFailed)
  end
end

function WU.TryToPayResource(cost, onSucceed, onFailed)
  return WU.TryToPay(cost.type, cost.id, cost.count, onSucceed, onFailed)
end

function WU.TickBegin(func_layer)
  if func_layer == nil then
    func_layer = 2
  end
  profiler.start(func_layer)
end

function WU.TickEnd(tag, path)
  local log = profiler.report()
  profiler.stop()
  if tag then
    log = "tag:" .. tag .. "\n" .. log
  end
  LuaDebuger:Instance():ReHook()
  warning("Debug", log)
end

function WU.TickFile(path)
  profiler.flush(path)
  LuaDebuger:Instance():ReHook()
end

function WU.Preload(resName, callback)
  local instance = CS.ResourceManager.Instance
  if instance ~= nil then
    instance:Preload(resName, callback)
  end
end

function WU.FindWindow(name)
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:FindWindow(name)
  end
end

function WU.GetWindowStackString()
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:GetWindowStackString()
  end
end

function WU.TopWindow()
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:WindowStackOnTop()
  end
end

function WU.PreloadWindow(resName, callback)
  WU.Preload("Window/" .. resName .. "/" .. resName, callback)
end

function WU.AcquireWindowAsync(name, callback, replaceTop)
  if not WU.CheckWindowTempSwitch(name) then
    return
  end
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    if replaceTop == nil then
      replaceTop = false
    end
    return instance:AcquireWindowAsync(name, callback, replaceTop)
  end
end

function WU.AcquireWindow(name)
  if not WU.CheckWindowTempSwitch(name) then
    return
  end
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:AcquireWindow(name)
  end
end

function WU.RecycleWindow(windowOrName)
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:RecycleWindow(windowOrName)
  end
end

function WU.DestroyWindow(window)
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:DestroyWindow(window)
  end
end

function WU.BringToFront(window, depth)
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:AdjustWindowDepthForLua(window, depth)
  end
end

function WU.AdjustWidgetDepth(widget)
  local instance = CS.UIWindowManager.Instance
  if instance ~= nil then
    return instance:AdjustWidgetDepth(widget)
  end
end

function WU.InstantiateWindowPrefab(parent, path, name)
  local newObj = CS.ResourceManager.Instance:InstantiateWindowPrefab(path)
  CS.UIWindowManager.Instance:UpdateLocalization(newObj)
  newObj.transform.parent = parent.transform
  newObj.transform.localScale = CS.UnityEngine.Vector3.one
  newObj.transform.localPosition = CS.UnityEngine.Vector3.zero
  newObj.transform.localRotation = CS.UnityEngine.Quaternion.identity
  if name ~= nil then
    newObj.transform.name = name
  end
  return newObj
end

function WU.InstantiateWindowPrefabAttached(parent, path, name)
  local newObj = CS.ResourceManager.Instance:InstantiateWindowPrefabAttached(path, parent.transform)
  newObj.transform.localScale = CS.UnityEngine.Vector3.one
  newObj.transform.localPosition = CS.UnityEngine.Vector3.zero
  newObj.transform.localRotation = CS.UnityEngine.Quaternion.identity
  if name ~= nil then
    newObj.transform.name = name
  end
  return newObj
end

function WU.RecycleCacheObject(obj)
  CS.ResourceManager.Instance:RecycleCacheObject(obj)
end

function WU.RemoveAllChild(node)
  if node ~= nil then
    local childCnt = #node
    if 0 < childCnt then
      for i = childCnt - 1, 0, -1 do
        local obj = node[i]["$gameObject"]
        obj.transform.parent = nil
        CS.UnityEngine.GameObject.Destroy(obj)
      end
    end
  end
end

function WU.TraverseChildren(rootRef, delegate)
  for i = 0, #rootRef - 1 do
    delegate(rootRef[i]["$gameObject"], i)
  end
end

function WU.SetActive(gasket, active)
  if gasket ~= nil and gasket.gameObject.activeSelf ~= active then
    gasket.gameObject:SetActive(active)
  end
end

function WU.IsActive(gasket, visible)
  if gasket ~= nil then
    return gasket.gameObject.activeInHierarchy
  end
end

function WU.ToggleRendering(gasket, visible)
  if gasket ~= nil then
    CS.UIWindowManager.Instance:ToggleRendering(gasket.gameObject, visible)
  end
end

function WU.IsRendering(gasket)
  if gasket ~= nil then
    return CS.UIWindowManager.Instance:IsRendering(gasket.gameObject)
  end
end

function WU.IsRenderingWithName(windowName)
  local isRendering = false
  local window = WU.FindWindow(windowName)
  if window ~= nil then
    local windowRef = _ENV["$"](window)
    isRendering = CS.UIWindowManager.Instance:IsRendering(windowRef["$gameObject"])
  end
  return isRendering
end

function WU.BindButtonEvent(gasket, callback, soundName)
  CS.UIWindowHelp.BindButtonEvent(gasket.gameObject, callback, soundName)
end

function WU.BindDragStartEvent(gasket, callback)
  CS.UIWindowHelp.BindDragStartEvent(gasket.gameObject, callback)
end

function WU.BindDragEndEvent(gasket, callback)
  CS.UIWindowHelp.BindDragEndEvent(gasket.gameObject, callback)
end

function WU.BindDragEvent(gasket, callback)
  CS.UIWindowHelp.BindDragEvent(gasket.gameObject, callback)
end

function WU.BindDragOverEvent(gasket, callback)
  CS.UIWindowHelp.BindDragOverEvent(gasket.gameObject, callback)
end

function WU.BindScrollEvent(gasket, callback)
  CS.UIWindowHelp.BindScrollEvent(gasket.gameObject, callback)
end

function WU.BindPressEvent(gasket, callback, soundName)
  CS.UIWindowHelp.BindPressEvent(gasket.gameObject, callback, soundName)
end

function WU.BindKeyEvent(gasket, callback)
  CS.UIWindowHelp.BindKeyEvent(gasket.gameObject, callback)
end

function WU.ClearButtonEvent(gasket)
  CS.UIWindowHelp.ClearButtonEvent(gasket.gameObject)
end

function WU.ClearDragEvent(gasket)
  CS.UIWindowHelp.ClearDragEvent(gasket.gameObject)
end

function WU.ClearPressEvent(gasket)
  CS.UIWindowHelp.ClearPressEvent(gasket.gameObject)
end

function WU.HandleGameObjectAttached(go)
  CS.UIWindowHelp.HandleGameObjectAttached(go)
end

function WU.RestartTween(tween, callback)
  tween.enabled = true
  tween:ResetToBeginning()
  tween:SetOnFinished(callback)
end

function WU.SetLockFlag(flag, lock)
  CS.UIBaseProcess.Instance:SetLockFlag(flag, lock)
end

function WU.ShowNetworkUnavailable()
  WU.AcquireWindow("NetworkUnavailable")
end

function WU.HideNetworkUnavailable()
  WU.RecycleWindow("NetworkUnavailable")
end

function WU.AcquireAsset(path, reference)
  if CS.ResourceManager.Instance ~= nil then
    return CS.ResourceManager.Instance:AcquireAssetObject(path, reference)
  end
end

-- Local content policy: these eight exact paths are absent in the original
-- asset index. Probe the requested asset first; never replace a whole actor.
-- 1509 -> original same-role variants 1009, then 2009. Role 17/127/133
-- have no other usable original variant; use the original generic item icon.
local LocalMissingActorTextures = {
  ["texture/actorportraitfull/actor_portrait_full_1017a"] = {},
  ["texture/actorskinhead/actorskinhead_1017"] = {},
  ["texture/actorportraitfull/actor_portrait_full_1509a"] = {
    "Texture/ActorPortraitFull/actor_portrait_full_1009a",
    "Texture/ActorPortraitFull/actor_portrait_full_2009a"},
  ["texture/actorskinhead/actorskinhead_2009"] = {"Texture/ActorSkinHead/ActorSkinHead_1009"},
  ["texture/actorskinheadbig/actorskinhead_big_2009"] = {"Texture/ActorSkinHeadBig/ActorSkinHead_big_1009"},
  ["texture/actorskinhead/actorskinhead_1127"] = {},
  ["texture/actorskinheadbig/actorskinhead_big_1127"] = {},
  ["texture/actorskinheadbig/actorskinhead_big_1133"] = {}
}
local LocalGenericActorTexture = "Texture/ItemIcon/ResUnknownAllThings_0"

function WU.IsLocalContentServer()
  local iap = S:Get("IAPHandler")
  return iap and iap.IsLocalAccountServer and iap.IsLocalAccountServer()
end

function WU.CanUseLocalActorTexture(path)
  local alternatives = LocalMissingActorTextures[string.lower(path)]
  if alternatives == nil then
    return true
  end
  local function CanLoad(candidate)
    local asset = WU.AcquireAsset(candidate)
    if asset == nil then
      return false
    end
    WU.ReleaseAsset(asset)
    return true
  end
  if CanLoad(path) then
    return true
  end
  for _, candidate in ipairs(alternatives) do
    if CanLoad(candidate) then
      return true
    end
  end
  return CanLoad(LocalGenericActorTexture)
end

function WU.ApplyLocalMissingActorTexture(uiTexture, path, reference)
  if not WU.IsLocalContentServer() or type(path) ~= "string" then
    return false
  end
  local alternatives = LocalMissingActorTextures[string.lower(path)]
  if alternatives == nil then
    return false
  end
  local asset = WU.AcquireAsset(path, reference)
  if asset ~= nil then
    WU.ReleaseAsset(asset, reference)
    return false
  end
  local function TryPath(candidate, generic)
    local replacement = WU.AcquireAsset(candidate, reference)
    if replacement == nil then
      return false
    end
    WU.ReleaseAsset(replacement, reference)
    uiTexture.mainTexturePath = candidate
    if generic then
      -- A generic icon is not a recolorable actor atlas.
      uiTexture.maskTexturePath = ""
      uiTexture.additionalParam = 0
    end
    return true
  end
  for _, candidate in ipairs(alternatives) do
    if TryPath(candidate, false) then
      return true
    end
  end
  return TryPath(LocalGenericActorTexture, true)
end

function WU.ReleaseAsset(asset, reference)
  if CS.ResourceManager.Instance ~= nil then
    CS.ResourceManager.Instance:ReleaseAssetObject(asset, reference)
  end
end

function WU.ShowMessageYesNo(message, callback, yesText, noText, alignLeft, disableButtonClose, justClose, toggleText)
  WU.AcquireWindowAsync("MessageBox", function(ui)
    _ENV["$"](ui)["$$ShowMessageYesNo"](message, callback, yesText, noText, alignLeft, disableButtonClose, justClose, toggleText)
  end)
end

function WU.ShowMessageOK(message, callback, okText, alignLeft)
  WU.AcquireWindowAsync("MessageBox", function(ui)
    _ENV["$"](ui)["$$ShowMessageOK"](message, callback, okText, alignLeft)
  end)
end

function WU.ShowMessageWithNoButton(message)
  WU.AcquireWindowAsync("MessageBox", function(ui)
    _ENV["$"](ui)["$$ShowMessageWithNoButton"](message)
  end)
end

function WU.TryToRefresh(moneyType, count, RefreshFunction, OnYes, skip)
  if skip then
    RefreshFunction()
  else
    local text = WU.GetString("Window_ConfirmCostMoneyToRefreshGoodPage", "<img src='ResIcon_s." .. PB.enum.ResourceType.__keys[moneyType] .. "_0'/>", count)
    WU.ShowMessageYesNo(text, function(message, selectToggle)
      if message == "YES" then
        RefreshFunction()
        OnYes(selectToggle)
      end
    end, WU.GetString("Window_SortFullScreenConfirm"), WU.GetString("WindowSystem_Cancel"), nil, nil, nil, WU.GetString("Purchase_Tips5"))
  end
end

function WU.ShowLoadingDetail()
  _ENV["$"](WU.AcquireWindow("SceneLoading"))["$$ShowLoadingDetail"]()
end

function WU.UpdateLoadingProgress(progress)
  local loadingDetail = WU.FindWindow("SceneLoading")
  if loadingDetail then
    _ENV["$"](loadingDetail)["$$UpdateLoadingProgress"](progress)
  end
end

function WU.HideLoadingDetail()
  local loadingDetail = WU.FindWindow("SceneLoading")
  if loadingDetail then
    _ENV["$"](loadingDetail)["$$HideLoadingDetail"]()
  end
end

function WU.ShowHintText(text, duration)
  _ENV["$"](WU.AcquireWindow("HintText"))["$$ShowHintText"](text, duration)
end

function WU.ShowHintTextRolling(text)
  _ENV["$"](WU.AcquireWindow("HintRolling"))["$$ShowHintText"](text)
end

function WU.ShowLevelLimitMsg(lv, uid)
  WU.ShowMessageYesNo(WU.GetString("Window_StarLevelupError_LevelLimit", lv), function(msg)
    if msg == "YES" then
      local topWindow = WU.TopWindow()
      if topWindow.gameObject.name == "ActorInfo" then
        _ENV["$"](topWindow)["$$SetSelectedTab"]("TabCultivation")
      else
        WU.AcquireWindowAsync("ActorInfo", function(window)
          _ENV["$"](window)["$$SetSelectedTab"]("TabCultivation")
        end)
      end
    end
  end, WU.GetString("Window_GoTo"), WU.GetString("WindowSystem_Confirm"))
end

function WU.ShowHintIcon(items, callback, bExchangeSkin, forActorCultivation)
  if items == nil or #items == 0 then
    info("WindowUtil", "Hint: no items to show")
    if callback then
      callback()
    end
  elseif #items <= 7 then
    WU.AcquireWindowAsync("HintFewResources", function(ui)
      _ENV["$"](ui)["$$ShowHintIcon"](items, callback, 2, bExchangeSkin, forActorCultivation)
    end)
  else
    WU.AcquireWindowAsync("HintResources", function(ui)
      _ENV["$"](ui)["$$ShowHintIcon"](items, callback, bExchangeSkin, forActorCultivation)
    end)
  end
end

function WU.ShowRewards(rewards, callback, forActorCultivation)
  local bExchangeSkin = false
  for _, reward in pairs(rewards) do
    if reward.baseRes and reward.baseRes.type == PB.enum.ResourceType.ResActorSkin and #reward.inBox > 0 then
      bExchangeSkin = true
    end
  end
  local IU = require("Common/ItemUtil")
  rewards = IU.FlatInboxItems(rewards)
  local items = _ENV["!"]({})
  local actors = _ENV["!"]({})
  local manualBoxes = _ENV["!"]({})
  for _, reward in pairs(rewards) do
    if reward.baseRes then
      if 0 < reward.baseRes.countDelta and reward.baseRes.param == PB.enum.ResourceParamType.None then
        table.insert(items, {
          type = reward.baseRes.type,
          id = reward.baseRes.id,
          count = reward.baseRes.count,
          countDelta = reward.baseRes.countDelta,
          ownerBoxId = reward.ownerBoxId
        })
        if reward.baseRes.type == PB.enum.ResourceType.ResItem then
          local itemInfo = PB.get("ItemInfo", reward.baseRes.id)
          if itemInfo.funcType == PB.enum.ItemFuncType.ManualBox then
            table.insert(manualBoxes, {
              type = reward.baseRes.type,
              id = reward.baseRes.id,
              countDelta = reward.baseRes.countDelta
            })
          end
        end
      end
    elseif reward.equip then
      if 0 < reward.equip.countDelta then
        table.insert(items, {
          type = PB.enum.ResourceType.ResEquip,
          id = reward.equip.id,
          count = 1,
          ownerBoxId = reward.ownerBoxId
        })
      end
    elseif reward.actor and 0 < reward.actor.countDelta then
      table.insert(actors, reward.actor)
      table.insert(items, {
        type = PB.enum.ResourceType.ResActor,
        id = reward.actor.id,
        count = 1,
        ownerBoxId = reward.ownerBoxId,
        quality = reward.actor.quality
      })
    end
  end
  if 0 < #actors then
    local actorList = _ENV["!"](CS.DataBindingManager.Instance:GetData("fci/actor/")):duplicate()
    for _, actor in pairs(actors) do
      local index = table.find(actorList, function(k, v)
        return v.uid == actor.uid
      end)
      if index then
        table.remove(actorList, index)
      end
    end
    actors = actors:where(function(k, v)
      return not actorList:find(function(_k, _v)
        return _v.id == v.id and _v.quality >= v.quality
      end)
    end):toarray()
    WU.ShowNewActorGet(actors, function()
      WU.ShowHintIcon(items, function()
        WU.ShowManualBox(manualBoxes, callback)
      end, bExchangeSkin)
    end)
  else
    WU.ShowHintIcon(items, function()
      WU.ShowManualBox(manualBoxes, callback)
    end, bExchangeSkin, forActorCultivation)
  end
end

function WU.ShowManualBox(manualBoxes, callback)
  if 0 < #manualBoxes then
    local manualBox = manualBoxes[1]
    if 1 < manualBox.countDelta then
      manualBox.countDelta = manualBox.countDelta - 1
    else
      table.remove(manualBoxes, 1)
    end
    WU.ShowItemDetail(manualBox.id, false, function(opened)
      if not opened then
        WU.ShowHintText(WU.GetString("Window_ManualBoxInBag"))
      end
      WU.ShowManualBox(manualBoxes, callback)
    end)
  elseif callback then
    callback()
  end
end

function WU.ShowNewActorGet(actors, callback)
  if actors and not _ENV["!"](actors):empty() then
    WU.AcquireWindowAsync("NewActorGet", function(window)
      _ENV["$"](window)["$$NewActorGet"](actors, callback)
    end)
  elseif callback then
    callback()
  end
end

function WU.UnlockAchievement(id)
  local ui = WU.FindWindow("AchievementUnlock")
  if ui == nil then
    ui = WU.AcquireWindow("AchievementUnlock")
  end
  _ENV["$"](ui)["$$AddUnlock"](id)
end

function WU.IsResource(type)
  if type == PB.enum.ResourceType.ResPlayerExp or type == PB.enum.ResourceType.ResPlayerTitle or type == PB.enum.ResourceType.ResPlayerMoney or type == PB.enum.ResourceType.ResPlayerPower or type == PB.enum.ResourceType.ResPlayerGold or type == PB.enum.ResourceType.ResPlayerHonourPoint or type == PB.enum.ResourceType.ResPlayerRelationPoint or type == PB.enum.ResourceType.ResGuildScore or type == PB.enum.ResourceType.ResTowerScore or type == PB.enum.ResourceType.ResAchievementPoint or type == PB.enum.ResourceType.ResMonthCardDays or type == PB.enum.ResourceType.ResPlayerPayMoney or type == PB.enum.ResourceType.ResGachaPoint or type == PB.enum.ResourceType.ResDungeonPlayTimesLimit then
    return true
  else
    return false
  end
end

function WU.ShowResourceDetail(type, id, isPreview)
  if WU.IsResource(type) then
    WU.AcquireWindowAsync("ItemDetail", function(ui)
      _ENV["$"](ui)["$$SetResourceType"](type)
    end)
  elseif type == PB.enum.ResourceType.ResItem then
    WU.ShowItemDetail(id, isPreview)
  elseif type == PB.enum.ResourceType.ResSignboard then
    WU.ShowSignboardDetail(id, isPreview)
  elseif type == PB.enum.ResourceType.ResEquip then
    WU.ShowEquipDetailById(id)
  elseif type == PB.enum.ResourceType.ResMedal then
    WU.ShowMedalDetail(id)
  elseif type == PB.enum.ResourceType.ResActorSkin then
    WU.ShowActorSkinDetail(id)
  end
end

function WU.ShowItemDetail(id, isPreview, callback)
  if isPreview == nil then
    isPreview = true
  end
  local itemInfo = PB.get("ItemInfo", id)
  if itemInfo.funcType == PB.enum.ItemFuncType.ManualBox then
    if itemInfo.param[1] == PB.enum.ManualBoxType.Actor then
      WU.AcquireWindowAsync("ManualBoxActor", function(ui)
        _ENV["$"](ui)["$$SetBoxId"](id, isPreview, callback)
      end)
    elseif itemInfo.param[1] == PB.enum.ManualBoxType.Equip then
      WU.AcquireWindowAsync("ManualBoxEquip", function(ui)
        _ENV["$"](ui)["$$SetBoxId"](id, isPreview, callback)
      end)
    elseif itemInfo.param[1] == PB.enum.ManualBoxType.Mix then
      WU.AcquireWindowAsync("ManualBoxMix", function(ui)
        _ENV["$"](ui)["$$SetBoxId"](id, isPreview, callback)
      end)
    elseif itemInfo.param[1] == PB.enum.ManualBoxType.ActorSkin then
      WU.AcquireWindowAsync("ManualBoxSkin", function(ui)
        _ENV["$"](ui)["$$SetBoxId"](id, isPreview, callback)
      end)
    else
      warning("Unknow ManualBoxType: " .. itemInfo.param[1])
    end
  else
    WU.AcquireWindowAsync("ItemDetail", function(ui)
      _ENV["$"](ui)["$$SetItemId"](id, isPreview)
    end)
  end
end

function WU.ShowEquipDetailById(id, level)
  WU.AcquireWindowAsync("EquipDetail", function(ui)
    _ENV["$"](ui)["$$SetEquipId"](id, level)
  end)
end

function WU.ShowEquipDetailByUid(uid, showButtons, callback)
  WU.AcquireWindowAsync("EquipDetail", function(ui)
    _ENV["$"](ui)["$$SetEquipUid"](uid, showButtons, nil, callback)
  end)
end

function WU.ShowMedalDetail(id, showButtonEquip, expireTime, windowName)
  WU.AcquireWindowAsync("ItemDetail", function(ui)
    _ENV["$"](ui)["$$SetMedalId"](id, showButtonEquip, expireTime, windowName)
  end)
end

function WU.ShowActorSkinDetail(id)
  WU.AcquireWindowAsync("ItemDetail", function(ui)
    _ENV["$"](ui)["$$SetSkinId"](id)
  end)
end

function WU.ShowSignboardDetail(id)
  WU.AcquireWindowAsync("ItemDetail", function(ui)
    _ENV["$"](ui)["$$SetSignboardId"](id)
  end)
end

function WU.ShowHoverTips(title, description)
  _ENV["$"](WU.AcquireWindow("HoverTips"))["$$SetTitleDescription"](title, description)
end

function WU.ShowHoverTips2(windowName, title, desc1, desc2, desc3, desc4, desc5, desc6)
  _ENV["$"](WU.AcquireWindow(windowName))["$$SetTitleDescription2"](title, desc1, desc2, desc3, desc4, desc5, desc6)
end

function WU.ShowHoverTips3(content)
  _ENV["$"](WU.AcquireWindow("HoverTips"))["$$SetContent"](content)
end

function WU.HideHoverTips()
  local hoverTips = WU.FindWindow("HoverTips")
  if hoverTips then
    _ENV["$"](hoverTips)["$$Hide"]()
  end
end

function WU.HideHoverTipsSpecial(windowName)
  local hoverTips = WU.FindWindow(windowName)
  if hoverTips then
    _ENV["$"](hoverTips)["$$Hide"]()
  end
end

function WU.ShowEditBox(context, confirmCallback, cancelCallback, lenLimit)
  WU.AcquireWindowAsync("EditBox", function(ui)
    _ENV["$"](ui)["$$ShowEditBox"](context, confirmCallback, cancelCallback, lenLimit)
  end)
end

function WU.ShowSliderBox(context, confirmCallback, cancelCallback)
  WU.AcquireWindowAsync("SliderBox", function(ui)
    _ENV["$"](ui)["$$ShowSliderBox"](context, confirmCallback, cancelCallback)
  end)
end

function WU.ShowPlayerOption(data, cancelCallback)
  WU.AcquireWindowAsync("PlayerOption", function(window)
    _ENV["$"](window)["$$SetContext"](data)
    _ENV["$"](window)["$$SetCancelCallback"](cancelCallback)
  end)
end

function WU.HidePlyaerOption()
  local playerOption = WU.FindWindow("PlayerOption")
  if playerOption.gameObject.activeSelf then
    _ENV["$"](playerOption)["$$Hide"]()
  end
end

function WU.ShowPlayerDetailByIdOrName(data, type)
  WU.AcquireWindowAsync("PlayerDetail", function(window)
    _ENV["$"](window)["$$SetPlayerDetail"](data, type)
  end)
end

function WU.ShowPlayerDetail(playerInfo)
  WU.AcquireWindowAsync("PlayerDetail", function(window)
    _ENV["$"](window)["$$SetByResult"](playerInfo)
  end)
end

function WU.ShowPlayerSpace(playerId, resetToggle, selectToggle)
  WU.AcquireWindowAsync("MySpace", function(window)
    _ENV["$"](window)["$$SetPlayerDetail"](playerId, resetToggle, selectToggle)
  end)
end

function WU.ShowPlayerLevelupResult(lvFrom, lvTo)
  WU.SetLockFlag(PB.enum.LockInputFlag.UITransition, true)
  _ENV["$"](WU.AcquireWindow("PlayerLevelupResult"))["$$SetData"](lvFrom, lvTo)
end

function WU.Exchange(resourceType, param, autoClose)
  WU.AcquireWindowAsync("ResourceExchange", function(ui)
    _ENV["$"](ui)["$$ShowExchange"](resourceType, param, autoClose)
  end)
end

function WU.ShowActorPeek(actor)
  local actorPeek = WU.FindWindow("ActorPeek")
  if actorPeek and _ENV["$"](actorPeek)["$UILuaWindow"].InUse then
    _ENV["$"](actorPeek)["$$SetData"](actor)
  else
    WU.AcquireWindowAsync("ActorPeek", function(window)
      _ENV["$"](window)["$$SetData"](actor)
    end)
  end
end


-- D/local: explicit missing role-name keys and same-Role banner fallback.
-- Original localization_chs and all present locale/sprite values are preserved.
local completionNames = {[1501]="御坂美琴",[1503]="司波深雪",[1505]="夏娜",[1506]="玉置亚子",[1511]="亚丝娜",[1515]="逢坂大河",[1516]="湊智花",[1517]="司波达也",[1601]="黑猫",[1607]="真奥贞夫（魔王形态）",[2016]="湊智花",[2017]="司波达也",[2018]="库温瑟",[2106]="晓古城",[2115]="折原临也",[2134]="伊里野加奈"}
local completionDonors = {[1501]=1001,[1503]=1003,[1505]=1005,[1506]=1006,[1511]=1011,[1515]=1015,[1516]=1016,[1517]=1017,[1601]=1101,[1607]=2107,[2016]=1016,[2017]=1017,[2018]=1018,[2106]=1106,[2115]=1115}
local completionSprites = {["1501a"]="1001a",["1503a"]="1003a",["1505a"]="1005a",["1506a"]="1006a",["1511a"]="1011a",["1515a"]="1015a",["1516a"]="1016a",["1517a"]="1017a",["1601a"]="1101a",["1607a"]="2107a",["2016a"]="1016a",["2017a"]="1017a",["2018a"]="1018a",["2106a"]="1106a",["2115a"]="1115a"}
local completionSkills = {[1503001]=1003001,[1503002]=1003002,[1503015]=1003015,[1503301]=1003301,[1505001]=1005001,[1505002]=1005002,[1505003]=1005003,[1505004]=1005004,[1505015]=1005015,[1505301]=1005301,[1506003]=1006003,[1506004]=1006004,[1506015]=1006015,[1506301]=1006301,[1515001]=1015001,[1515002]=1015002,[1515003]=1015003,[1515004]=1015004,[1515015]=1015015,[1515301]=1015301,[1516001]=1016001,[1516002]=1016002,[1516015]=1016015,[1516301]=1016301,[1601001]=1101001,[1601002]=1101002,[1601015]=1101015,[1607001]=2107001,[1607002]=2107002,[1607003]=2107002,[1511001]=1011001,[1511002]=1011002}
local function IsRoleCompletionLocal()
  local service = S:Get("IAPHandler")
  return service and service.IsLocalAccountServer and service.IsLocalAccountServer()
end
function WU.CompleteRoleNameSprite(sprite)
  if not IsRoleCompletionLocal() or sprite:GetAtlasSprite() ~= nil then return end
  local anim = string.match(sprite.spriteName or "", "^actor_name_(.+)$")
  local donor = completionSprites[anim]
  if donor then sprite.spriteName = "actor_name_" .. donor end
end
local function CompleteRoleLocale(key, original)
  if original ~= nil and original ~= "" and original ~= key then return original end
  if not IsRoleCompletionLocal() then return original end
  if key == "tag990001" then return "复刻" end
  for _, prefix in ipairs({"SkillName_", "SkillDescribe_", "NextSkillDescribe_"}) do
    local skill = tonumber(string.match(key, "^" .. prefix .. "(%d+)$"))
    local donorSkill = skill and completionSkills[skill]
    if donorSkill then
      return CS.LocaleManager.Instance:GetString(prefix .. donorSkill) or original
    end
  end
  local id = tonumber(string.match(key, "^ActorName_(%d+)$"))
  if id and completionNames[id] then return completionNames[id] end
  id = tonumber(string.match(key, "^ActorCV_(%d+)$"))
  local donor = id and completionDonors[id]
  if donor then return CS.LocaleManager.Instance:GetString("ActorCV_" .. donor) or original end
  return original
end

function WU.GetString(key, ...)
  if CS.LocaleManager.Instance == nil then
    return ""
  end
  if #{
    ...
  } == 0 then
    return CompleteRoleLocale(key, CS.LocaleManager.Instance:GetString(key))
  else
    return CS.LocaleManager.Instance:GetStringFormat(key, ...)
  end
end

function WU.IsExist(key)
  return CS.LocaleManager.Instance:IsExist(key)
end

function WU.GetStringEmpty(key, ...)
  local result = WU.GetString(key, ...)
  if result == key then
    return ""
  end
  return result
end

function WU.FormatString(text, ...)
  return CS.LocaleManager.Instance:FormatString(text, ...)
end

function WU.CurrentScene()
  local scene = CS.SceneBaseImpl.CurrentScene
  if scene ~= nil then
    return scene:GetType().Name
  end
end

function WU.ContextWindow(sceneName)
  return CS.DataBindingManager.Instance:GetData("SceneContext/" .. sceneName)
end

function WU.WorldToUIScreen(pos)
  local camera = CS.UIBaseProcess.Instance:GetCamera()
  return camera:WorldToScreenPoint(pos)
end

function WU.MousePosition()
  return CS.GameUtility.DeviceToUIPoint(CS.UnityEngine.Input.mousePosition)
end

function WU.ScreenToWorld(pos)
  local camera = CS.UIBaseProcess.Instance:GetOriginalCamera()
  return camera:ScreenToWorldPoint(pos)
end

function WU.DivideAlign(widgets, range)
  local segments = #widgets
  local segmentRange = range / segments
  for i = 1, segments do
    local button = widgets[i]
    local position = button.transform.localPosition
    position.x = (i - 0.5) * segmentRange - range * 0.5
    button.transform.localPosition = position
  end
end

function WU.GetDungeonTargetText(target)
  local zeroStr = ""
  local compareVal = target.param[1]
  if compareVal == 0 then
    zeroStr = "_0"
  end
  local param3 = target.param[3]
  if param3 == nil then
    param3 = 0
  end
  local findStr = "TargetType_" .. target.type .. "_" .. target.param[2]
  if target.type == PB.enum.DungeonTarget.KillWinType then
    findStr = findStr .. "_" .. compareVal
  elseif target.type == PB.enum.DungeonTarget.KillBySkill then
    findStr = findStr .. zeroStr .. "_" .. param3
  elseif target.type == PB.enum.DungeonTarget.CardNum then
    findStr = findStr .. zeroStr .. "_" .. param3
  elseif target.type == PB.enum.DungeonTarget.KillBySequence then
    findStr = findStr .. "_" .. compareVal .. "_" .. param3
  elseif target.type == PB.enum.DungeonTarget.ClearBuff then
    findStr = findStr .. zeroStr .. "_" .. param3
  else
    findStr = findStr .. zeroStr
  end
  return WU.GetString(findStr, compareVal, param3)
end

function WU.GetDungeonWinTypeText(winParam)
  local paramType = winParam[1]
  if paramType == nil then
    paramType = 1
  end
  local findStr = "WinType_" .. paramType
  if paramType == PB.enum.WinType.KillSingle or paramType == PB.enum.WinType.KillSingleInRound or paramType == PB.enum.WinType.NoDeadSingle or paramType == PB.enum.WinType.NoDeadSingleInRound then
    findStr = findStr .. "_" .. winParam[2]
  end
  return WU.GetString(findStr, winParam[2], winParam[3])
end

function WU.IsToday(dateTime)
  local isToday = false
  if dateTime.Date == CS.GameTime.serverNow.Date then
    isToday = true
  end
  return isToday
end

function WU.RenderCountdownTimeNotUrgent(remainTime, highlightColor)
  local seconds = math.max(remainTime, 0)
  local timeSpan = CS.System.TimeSpan(0, 0, seconds)
  local pattern = "<font color = %s>%s</font>"
  local res = ""
  if 0 < timeSpan.Days then
    res = res .. string.format(pattern, highlightColor, timeSpan.Days) .. WU.GetString("Window_DayUnit")
  end
  res = res .. string.format(pattern, highlightColor, timeSpan.Hours) .. WU.GetString("Window_HourUnit")
  res = res .. string.format(pattern, highlightColor, timeSpan.Minutes) .. WU.GetString("Window_MinUnit")
  return res
end

function WU.RenderTime(time, linefeed)
  local result = ""
  local dateTime = CS.GameTime.UtcToLocal(time)
  if WU.IsToday(dateTime) then
    result = dateTime:ToString("HH:mm")
  else
    local diffTime = math.abs(CS.GameTime.serverUtc - time)
    if diffTime < 604800 then
      local dayOfWeek = CS.System.Convert.ToInt32(dateTime.DayOfWeek)
      if linefeed then
        result = dateTime:ToString("HH:mm") .. "<br/>" .. WU.GetString("Window_DayOfWeek" .. dayOfWeek)
      else
        result = WU.GetString("Window_DayOfWeek" .. dayOfWeek) .. " " .. dateTime:ToString("HH:mm")
      end
    elseif linefeed then
      result = dateTime:ToString("HH:mm<br/>yyyy/MM/dd")
    else
      result = dateTime:ToString("yyyy/MM/dd HH:mm")
    end
  end
  return result
end

function WU.RenderTime24(startTimeStamp, endTimeStamp)
  local startTime = CS.GameTime.UtcToLocal(startTimeStamp)
  local endTime = CS.GameTime.UtcToLocal(endTimeStamp)
  return WU.GetString("Window_Month_Day_Hour", startTime.Month, startTime.Day, startTime.Hour) .. " ~ " .. WU.GetString("Window_Month_Day_Hour", endTime.Month, endTime.Day, endTime.Hour + 1)
end

function WU.RenderTime2(time, showHours, showDay, showYear)
  local result = ""
  local dateTime = CS.GameTime.UtcToLocal(time)
  if showHours then
    result = WU.GetString("Window_Month_Day_Hour", dateTime.Month, dateTime.Day, dateTime.Hour)
  elseif showDay then
    result = WU.GetString("Window_Day_Hour_Min", dateTime.Day, dateTime.Hour, dateTime.Minute)
  elseif showYear then
    result = WU.GetString("Window_Year_Month_Day_Hour_Min", dateTime.Year, dateTime.Month, dateTime.Day, dateTime.Hour, dateTime.Minute)
  else
    result = WU.GetString("Window_Month_Day", dateTime.Month, dateTime.Day)
  end
  return result
end

function WU.RenderCountDownTime(seconds, fontSize, showAllUnit, numNotCenterAlign)
  seconds = math.floor(math.max(seconds, 0))
  local timeSpan = CS.System.TimeSpan(0, 0, seconds)
  local units = 15
  local widgetWidth = math.ceil(2.5 * fontSize)
  local numberFontSize = math.floor(fontSize * 1.4)
  local hours = timeSpan.Hours
  if showAllUnit then
  elseif timeSpan.Days >= 3 then
    units = 8
    if timeSpan.Days > 99 then
      widgetWidth = math.ceil(3.1578947368421053 * fontSize)
    end
  else
    units = 7
    hours = timeSpan.Hours + timeSpan.Days * 24
  end
  local unitCount = bitCount(units)
  local percentage = 100 / unitCount / 2
  local pattern = "<c width=%f%% align=center><font size=%d>%02d</font></c><c width=%f%% align=center>%s</c>"
  if numNotCenterAlign then
    pattern = "<font size=%d>%02d</font>%s"
  end
  local dayPattern = "<c width=%f%% align=center><font size=%d>%d</font></c><c width=%f%% align=center>%s</c>"
  if numNotCenterAlign then
    dayPattern = "<font size=%d>%d</font>%s"
  end
  local result = ""
  local isCenterNumber = not numNotCenterAlign
  if units & 8 ~= 0 then
    if numNotCenterAlign then
      result = result .. string.format(dayPattern, numberFontSize, timeSpan.Days, WU.GetString("Window_DayUnit"))
    else
      result = result .. string.format(dayPattern, percentage, numberFontSize, timeSpan.Days, percentage, WU.GetString("Window_DayUnit"))
    end
  end
  if units & 4 ~= 0 then
    if numNotCenterAlign then
      result = result .. string.format(pattern, numberFontSize, hours, WU.GetString("Window_HourUnit"))
    else
      result = result .. string.format(pattern, percentage, numberFontSize, hours, percentage, WU.GetString("Window_HourUnit"))
    end
  end
  if units & 2 ~= 0 then
    if numNotCenterAlign then
      result = result .. string.format(pattern, numberFontSize, timeSpan.Minutes, WU.GetString("Window_MinUnit"))
    else
      result = result .. string.format(pattern, percentage, numberFontSize, timeSpan.Minutes, percentage, WU.GetString("Window_MinUnit"))
    end
  end
  if units & 1 ~= 0 then
    if numNotCenterAlign then
      result = result .. string.format(pattern, numberFontSize, timeSpan.Seconds, WU.GetString("Window_SecUnit"))
    else
      result = result .. string.format(pattern, percentage, numberFontSize, timeSpan.Seconds, percentage, WU.GetString("Window_SecUnit"))
    end
  end
  if not numNotCenterAlign then
    result = "<l width=" .. widgetWidth * unitCount .. ">" .. result .. "</l>"
  end
  return result
end

function WU.RenderElpasedTime(elpasedTime)
  local result = ""
  local timeSpan = CS.System.TimeSpan(0, 0, elpasedTime)
  if timeSpan.TotalDays >= 365 then
    result = WU.GetString("Window_YearsAgo", timeSpan.TotalDays // 365)
  elseif timeSpan.TotalDays >= 30 then
    result = WU.GetString("Window_MonthsAgo", timeSpan.TotalDays // 30)
  elseif timeSpan.TotalDays >= 1 then
    result = WU.GetString("Window_DaysAgo", math.floor(timeSpan.TotalDays))
  elseif 1 <= timeSpan.TotalHours then
    result = WU.GetString("Window_HoursAgo", math.floor(timeSpan.TotalHours))
  elseif timeSpan.TotalMinutes > 3 then
    result = WU.GetString("Window_MinutesAgo", math.floor(timeSpan.TotalMinutes))
  else
    result = WU.GetString("Window_SecondsAgo")
  end
  return result
end

function WU.RenderTimeSpan(seconds)
  local result = ""
  local timeSpan = CS.System.TimeSpan(0, 0, seconds)
  if 0 < timeSpan.Days then
    result = WU.GetString("Window_Day", timeSpan.Days)
  end
  if 0 < timeSpan.Hours or 0 < timeSpan.Minutes or 0 < timeSpan.Seconds then
    result = result .. WU.GetString("Window_Hour", timeSpan.Hours)
  end
  if 0 < timeSpan.Minutes or 0 < timeSpan.Seconds then
    result = result .. WU.GetString("Window_Min", timeSpan.Minutes)
  end
  if 0 < timeSpan.Seconds or result == "" then
    result = result .. WU.GetString("Window_Sec", timeSpan.Seconds)
  end
  return result
end

function WU.ActivityTypeToUnlockWindowType(activityType, activityId)
  if activityType == PB.enum.ActivityType.StarResDungeon then
    return PB.enum.UnlockWindow.ActivityStageChoose1
  elseif activityType == PB.enum.ActivityType.CollectionDungeon then
    return PB.enum.UnlockWindow.ActivityStageChoose2
  elseif activityType == PB.enum.ActivityType.Tower then
    return PB.enum.UnlockWindow.Tower
  elseif activityType == PB.enum.ActivityType.DeployMission then
    return PB.enum.UnlockWindow.Deploy
  elseif activityType == PB.enum.ActivityType.GoldDungeon then
    return PB.enum.UnlockWindow.DungeonGold
  elseif activityType == PB.enum.ActivityType.Tournament_AsyncPvp then
    return PB.enum.UnlockWindow.AsyncPvp
  elseif activityType == PB.enum.ActivityType.BurstLinkPvp then
    return PB.enum.UnlockWindow.BurstLink
  elseif activityType == PB.enum.ActivityType.RacePVP then
    return PB.enum.UnlockWindow.RacePvp
  elseif activityType == PB.enum.ActivityType.RacePVPActivity then
    return PB.enum.UnlockWindow.RacePVPActivity
  elseif activityType == PB.enum.ActivityType.ChallengeMode then
    return PB.enum.UnlockWindow.Challenge
  elseif activityType == PB.enum.ActivityType.DungeonTeam then
    return PB.enum.UnlockWindow.DungeonTeam
  elseif activityType == PB.enum.ActivityType.Maze then
    -- No dedicated UnlockWindow enum exists in this APK. MazeInfo 1 is a
    -- level-30 activity, so reuse the existing level-30 activity gate.
    return PB.enum.UnlockWindow.DungeonTeam
  elseif activityType == PB.enum.ActivityType.TriggeredEvent then
    return PB.enum.UnlockWindow.TriggeredEvent
  elseif activityType == PB.enum.ActivityType.EventDungeon then
    return PB.enum.UnlockWindow.EventDungeonSummer
  elseif activityType == PB.enum.ActivityType.EventDungeon_Autumn2018Hard then
    return PB.enum.UnlockWindow.EventDungeon_Autumn2018Hard
  elseif activityType == PB.enum.ActivityType.EventDungeon_Autumn2018 then
    return PB.enum.UnlockWindow.EventDungeon_Autumn2018
  end
end

function WU.WindowIsLocked1(windowEnumOrTable, playerLevel)
  if CS.ResourceManager.Instance.ConstConfig.Debug.DisableWindowLock then
    return false
  end
  playerLevel = playerLevel or CS.DataBindingManager.Instance:GetData("fci/baseinfo").level
  local data = CS.DataBindingManager.Instance:GetData("UnlockWindowData")
  local windowEnums = {}
  if type(windowEnumOrTable) == "table" then
    for i = 1, #windowEnumOrTable do
      table.insert(windowEnums, windowEnumOrTable[i])
    end
  else
    table.insert(windowEnums, windowEnumOrTable)
  end
  if data ~= nil then
    local unlockMinLevel = data[windowEnums[1]]
    for i = 2, #windowEnums do
      if unlockMinLevel == nil or data[windowEnums[i]] and unlockMinLevel > data[windowEnums[i]] then
        unlockMinLevel = data[windowEnums[i]]
      end
    end
    if unlockMinLevel ~= nil then
      return playerLevel < unlockMinLevel, unlockMinLevel
    else
      return false
    end
  else
    info("WindowUtil", "Check Window Lock failed , UnlockWindowData is missing")
    return false
  end
end

function WU.WindowIsLocked(windowEnum, showTip, playerLevel)
  if windowEnum == nil then
    return false
  end
  if CS.ResourceManager.Instance.ConstConfig.Debug.DisableWindowLock then
    return false
  end
  local unlockInfo = CS.DataBindingManager.Instance:GetData("UnlockWindowData")
  if unlockInfo == nil or unlockInfo[windowEnum] == nil then
    info("WindowUtil", "Check Window Lock failed , UnlockWindowData is missing")
    return false
  end
  local unlockLevel = unlockInfo[windowEnum]
  playerLevel = playerLevel or CS.DataBindingManager.Instance:GetData("fci/baseinfo").level
  if unlockLevel > playerLevel then
    if showTip ~= nil then
      WU.ShowHintText(WU.GetString("WindowUnlock_Hint", WU.GetString("UnlockType_" .. showTip), unlockLevel))
    end
    return true, unlockLevel
  else
    return false
  end
end

function WU.WindowTableIsLocked(windowEnum, showTip, playerLevel)
  if windowEnum == nil then
    return false
  end
  if CS.ResourceManager.Instance.ConstConfig.Debug.DisableWindowLock then
    return false
  end
  local unlockInfo = CS.DataBindingManager.Instance:GetData("UnlockWindowData")
  if unlockInfo == nil then
    info("WindowUtil", "Check Window Lock failed , UnlockWindowData is missing")
    return false
  end
  local unlockLevel
  for i = 1, #windowEnum do
    local t = unlockInfo[windowEnum[i]]
    if t ~= nil and (unlockLevel == nil or unlockLevel > t) then
      unlockLevel = t
    end
  end
  if unlockLevel == nil then
    info("WindowUtil", "Check Window Lock failed , UnlockWindowData is missing")
    return false
  end
  playerLevel = playerLevel or CS.DataBindingManager.Instance:GetData("fci/baseinfo").level
  if unlockLevel > playerLevel then
    if showTip ~= nil then
      WU.ShowHintText(WU.GetString("WindowUnlock_Hint", WU.GetString("UnlockType_" .. showTip), unlockLevel))
    end
    return true, unlockLevel
  else
    return false
  end
end

function WU.TutorialFight()
  if WU.IsStandaloneGame() then
    return false
  end
  local dataBind = CS.DataBindingManager.Instance
  if dataBind and dataBind:GetData("CurrentTutorial") == 1 then
    return true
  end
  return false
end

function WU.EnterStoryDungeon(m_dungeonId, m_selectActorGroupId, cartoonID, dungeonType)
  local param = {dungeonId = m_dungeonId, selectActorGroupId = m_selectActorGroupId}
  local dataBind = CS.DataBindingManager.Instance
  local DBH = require("Manager/DataBindingHandler")
  dataBind:GameRequest("fci/dungeon/"):Post(param, function(result)
    DBH.ResChange(result.resChange)
    if cartoonID ~= nil then
      dataBind:SetData("Dungeon/DungeonUid", result.dungeonUid)
      WU.EnterCartoonLevel(cartoonID, nil, "endfight")
    else
      WU.EnterGameLevel(result.dungeonUid, result.baseInfo, m_dungeonId, dungeonType, 1, true)
    end
  end)
end

function WU.ExitStoryDungeon()
  local dataBind = CS.DataBindingManager.Instance
  dataBind:GameRequest("fci/dungeon/{Dungeon/DungeonUid}/balance"):Post({}, function(response2)
    if response2.chapterSummaryInfo and dataBind:GetData("fci/dungeon/chapter-summary") then
      table.merge(dataBind:GetData("fci/dungeon/chapter-summary"), response2.chapterSummaryInfo)
    end
    for i = 1, #response2.chapterInfo do
      local chapterInfo = response2.chapterInfo[i]
      dataBind:SetData("fci/dungeon/chapter/" .. chapterInfo.recordChapter.chapterId, chapterInfo)
    end
    if response2.chapterSummaryInfo then
      dataBind:SetData("fci/dungeon/chapter-summary", response2.chapterSummaryInfo)
    end
    dataBind:SetData("DungeonBalance/result", response2.balanceInfo)
    dataBind:GameRequest("fci/dungeon/{Dungeon/DungeonUid}"):Delete(function(response3)
      if WU.TutorialFight() then
        dataBind:SetData("TutorialSign", 0)
        local S = require("Common/Singleton")
        S:Get("TutorialManager").TutorialDone(1)
        S:Get("TutorialManager").EndTutorial()
      end
      WU.ReturnCity("ChapterNormal")
    end)
  end)
end

function WU.ReturnCity(windowParam)
  WU.SaveCitySceneContext(windowParam)
  CS.GameGlobal.Instance:ChangeLevel("SceneCity")
end

function WU.SaveCitySceneContext(windowParam)
  if windowParam == "" then
    windowParam = nil
  end
  CS.DataBindingManager.Instance:SetData("SceneContext/SceneCity", windowParam)
end

function WU.SimpleCheckCheat(dungeonType)
end

function WU.SyncActorAttr(dungeonType, baseInfo)
  if baseInfo == nil then
    return
  end
  if baseInfo.useServerActor ~= nil and not baseInfo.useServerActor then
    return
  end
  if baseInfo.actors == nil then
    return
  end
  if #baseInfo.actors == 0 then
    return
  end
  local fixActor = {}
  for i = 1, 6 do
    fixActor[i] = baseInfo.actors[i] or {}
  end
  local fightActorInfo = {
    {
      playerId = CS.DataBindingManager.Instance:GetData("playerId"),
      actors = fixActor
    }
  }
  CS.DataBindingManager.Instance:SetData("FightBuffAffix", baseInfo.fightBuffs)
  CS.DataBindingManager.Instance:SetData("FightActorInfo", fightActorInfo)
  CS.DataBindingManager.Instance:SetData("FightActorInfoSynced", true)
end

function WU.EnterGameLevel(uid, baseInfo, dungeonId, dungeonType, team, playCartoon)
  WU.SyncActorAttr(dungeonType, baseInfo)
  local dataBind = CS.DataBindingManager.Instance
  dataBind:SetData("Dungeon/DungeonUid", uid)
  dataBind:SetData("FightSelfTeam", team)
  dataBind:SetData("FightDungeonType", dungeonType)
  dataBind:SetData("WindowDungeon/DungeonId", dungeonId)
  WU.SimpleCheckCheat(dungeonType)
  local fade = true
  CS.SoundManager.Instance:PlaySound("Sound/system/s_System_Fighting")
  local charperId = dataBind:GetData("WindowDungeon/ChapterId")
  local cartoon = WU.GetCartoonInfo(charperId, dungeonId, 0)
  if playCartoon and cartoon then
    WU.EnterCartoonLevel(cartoon.id, nil)
  else
    CS.GameGlobal.Instance:LoadGameLevel(tostring(dungeonId), fade)
  end
end

function WU.GetCartoonInfo(charperId, dungeonId, winPlay)
  local cartoon = PB.all("CartoonConfig"):where(function(_, e)
    return e.chapterId == charperId and e.dungeonId == dungeonId and e.winPlay == winPlay
  end):toarray():first()
  if cartoon == nil and charperId and dungeonId then
    local rewardInfo = PB.get("DungeonReward", dungeonId)
    if rewardInfo and rewardInfo.type == 1 then
      warning("debug", "no find cartoon in CartoonConfig.xlsx by chapterid:" .. tostring(charperId) .. " dungeonid:" .. tostring(dungeonId))
    end
  end
  return cartoon
end

function WU.EnterCartoonLevel(id, window, exParam)
  CS.DataBindingManager.Instance:SetData("CartoonInfo/CartoonId", id)
  CS.DataBindingManager.Instance:SetData("CartoonInfo/CartoonWindow", window)
  CS.DataBindingManager.Instance:SetData("CartoonInfo/CartoonExParam", exParam)
  local fade = true
  if WU.TutorialFight() then
    fade = false
  end
  CS.GameGlobal.Instance:ChangeLevel("SceneCartoon", CS.GameGlobal.ResetLevel.None, true, fade)
end

function WU.IsStandaloneGame()
  return false
end

function WU.GetCurrentScene()
  return CS.UnityEngine.SceneManagement.SceneManager.GetActiveScene()
end

function WU.GetAttrName(type)
  return WU.GetString("Window_ActorAttrName_" .. type)
end

function WU.GetItemDesc(id)
  local info = PB.get("ItemInfo", id)
  if info.funcType == PB.enum.ItemFuncType.DeploySpeedUp then
    return WU.GetString("ItemDescribe_" .. id, info.param[1] // 3600)
  elseif info.funcType == PB.enum.ItemFuncType.Box then
    return WU.GetString("ItemDescribe_" .. id, info.maxUseCount)
  else
    return WU.GetString("ItemDescribe_" .. id, info.param[1], info.param[2], info.param[3], info.param[4])
  end
end

function WU.GetItemName(id, type)
  local name
  if type == PB.enum.ResourceType.ResEquip then
    name = WU.GetString("EquipName_" .. id)
  elseif type == PB.enum.ResourceType.ResActor then
    name = WU.GetString("ActorName_" .. id)
  elseif type == PB.enum.ResourceType.ResItem then
    name = WU.GetString("ItemName_" .. id)
  elseif type == PB.enum.ResourceType.ResMedal then
    name = WU.GetString("MedalName_" .. id)
  else
    name = WU.GetString("ResourceTypeName_" .. type)
  end
  return name
end

function WU.CheckinGiftUndrawn()
  local resGifts = PB.all("SigninGift")
  local checkinData = CS.DataBindingManager.Instance:GetData("fci/signin")
  for i = 1, #resGifts do
    if resGifts[i].days <= checkinData.days and not table.has(checkinData.giftGot, resGifts[i].days) then
      return true
    end
  end
  return false
end

function WU.AttachmentToResourceType(attachment)
  local showRes = {}
  if attachment.actor ~= nil and attachment.actor.id ~= nil and attachment.actor.id > 0 then
    showRes.type = PB.enum.ResourceType.ResActor
    showRes.id = attachment.actor.id
  elseif attachment.equip ~= nil and attachment.equip.id ~= nil and 0 < attachment.equip.id then
    showRes.type = PB.enum.ResourceType.ResEquip
    showRes.id = attachment.equip.id
  elseif attachment.baseRes ~= nil and attachment.baseRes.id ~= nil then
    showRes.type = attachment.baseRes.type
    showRes.id = attachment.baseRes.id
    showRes.count = attachment.baseRes.count
  end
  showRes.ownerBoxId = attachment.ownerBoxId
  return showRes
end

function WU.SetNaviationHomeCallback(callback)
  _ENV["$"](WU.FindWindow("Navigation"))["$$SetHomeCallback"](callback)
end

function WU.SetNaviationBackCallback(callback)
  _ENV["$"](WU.FindWindow("Navigation"))["$$SetBackCallback"](callback)
end

function WU.SetRuleCallback(winName, callback)
  local DB = CS.DataBindingManager.Instance
  local list = DB:GetData("WindowRuleCallback")
  if list == nil then
    list = {}
  end
  list[winName] = callback
  DB:SetData("WindowRuleCallback", list)
end

function WU.SetWindowTitle(winName, textureName)
  local DB = CS.DataBindingManager.Instance
  local list = DB:GetData("WindowTitle")
  if list == nil then
    list = {}
  end
  list[winName] = textureName
  DB:SetData("WindowTitle", list)
end

function WU.SetGameDataCache(name, value)
  local DB = CS.DataBindingManager.Instance
  local gameDataCache = DB:GetData("gameDataCache")
  gameDataCache[name] = value
  DB:SetData("gameDataCache", gameDataCache)
end

function WU.GetGameDataCache(name)
  local DB = CS.DataBindingManager.Instance
  local gameDataCache = DB:GetData("gameDataCache")
  return gameDataCache[name]
end

function WU.IsEnoughMoney(price, resType)
  if resType == nil then
    resType = PB.enum.ResourceType.ResPlayerMoney
  end
  local allMoney = CS.DataBindingManager.Instance:GetData("fci/resource/ResPlayerMoney_0") or 0
  local payMoney = CS.DataBindingManager.Instance:GetData("fci/resource/ResPlayerPayMoney_0") or 0
  if resType == PB.enum.ResourceType.ResPlayerMoney then
    if price > allMoney then
      return false, price, 0
    end
    local bindMoney = allMoney - payMoney
    if price > bindMoney then
      local temp = price - math.max(0, bindMoney)
      if payMoney >= temp then
        return true, math.max(0, bindMoney), temp
      end
      return false, price, 0
    else
      return true, price, 0
    end
  else
    return price <= payMoney, 0, price
  end
end

function WU.GetActivityWeekRender(activityInfo, bShowHour)
  if activityInfo.weekdays == nil or #activityInfo.weekdays == 0 then
    return ""
  end
  local str = WU.GetString("Window_EveryWeek")
  for i, v in ipairs(activityInfo.weekdays) do
    if i ~= 1 then
      str = str .. "|"
    end
    local temp = WU.GetString(string.format("Window_Number_%d", v))
    str = str .. temp
  end
  if bShowHour == true then
    for i, v in ipairs(activityInfo.dayOpenTime) do
      if i ~= 1 then
        str = str .. " / "
      end
      str = str .. WU.RenderTimeOfDay(v.timestampStart) .. "~" .. WU.RenderTimeOfDay(v.timestampEnd)
    end
  end
  str = str .. WU.GetString("Window_Open")
  return str
end

function WU.GetOpenHourRender(activityInfo)
  if activityInfo.dayOpenTime == nil or #activityInfo.dayOpenTime == 0 then
    return ""
  end
  local str = ""
  for i, v in ipairs(activityInfo.dayOpenTime) do
    if i ~= 1 then
      str = str .. " / "
    end
    str = str .. WU.RenderTimeOfDay(v.timestampStart) .. "~" .. WU.RenderTimeOfDay(v.timestampEnd)
  end
  str = str .. WU.GetString("Window_Open")
  return str
end

function WU.RenderTimeOfDay(seconds)
  if seconds + 1 == 86400 then
    seconds = seconds + 1
  end
  local hour = math.floor(seconds / 3600)
  local min = math.floor(seconds % 3600 / 60)
  return string.format("%02d:%02d", hour, min)
end

function WU.IsShareSwitchOn()
  local accountCache = CS.DataBindingManager.Instance:GetData("accountCache")
  local switch = CS.DataBindingManager.Instance:GetData("AccountSwitch")
  if switch and switch.Share and (accountCache.socialPlatform == PB.enum.MSDKLoginPlatform.MSDK_QQ or accountCache.socialPlatform == PB.enum.MSDKLoginPlatform.MSDK_Wechat) then
    return true
  end
end

function WU.ToggleTopBar(windowName, visible)
  local config = PB.get("UIWindowConfig", windowName)
  if config == nil then
    return
  end
  if config.showNavigation then
    WU.ToggleRendering(WU.FindWindow("Navigation"), visible)
  end
  if config.showResource then
    WU.ToggleRendering(WU.FindWindow("Resource"), visible)
  end
  if config.showMessenger then
    WU.ToggleRendering(WU.FindWindow("Messenger"), visible)
  end
  if config.showSort then
    WU.ToggleRendering(WU.FindWindow("Sort"), visible)
  end
  if config.showShare then
    WU.ToggleRendering(WU.FindWindow("ShareScreen"), visible)
  end
  local achi = WU.FindWindow("AchievementUnlock")
  if achi ~= nil then
    WU.ToggleRendering(achi, visible)
  end
end

function WU.OpenUrl(url)
  CS.SceneBaseImpl.CurrentScene:OpenRawUrl(url)
end

function WU.OpenUrlWithDir(url, screenDir)
  CS.SceneBaseImpl.CurrentScene:OpenRawUrl(url, screenDir)
end

function WU.OpenUrlExternal(url)
  CS.SceneBaseImpl.CurrentScene:OpenUrlExternal(url)
end

function WU.GetMonsterAttr(monsterID, lv, upIndex)
  if monsterID == nil or monsterID == 0 then
    return
  end
  local indexInfo, attrsInfo
  local monster_info = PB.get("MonsterInfo", monsterID)
  local actorInfo = {}
  actorInfo.attrs = {}
  for i = 1, PB.enum.AttrType.Max - 1 do
    local newAttr = {}
    newAttr.type = i
    newAttr.value = 0
    newAttr.isRatio = false
    table.insert(actorInfo.attrs, newAttr)
  end
  if monster_info ~= nil then
    local index = monster_info.levelUpIndex
    if upIndex ~= nil and upIndex ~= 0 then
      index = upIndex
    end
    indexInfo = PB.get("MonsterLevelUpIndex", index)
    if indexInfo == nil then
      warning("GamePlay", "not find level up index = " .. index .. " monsterID = " .. monsterID)
    end
    local baseInfo = PB.get("MonsterLevelUpBase", lv)
    if baseInfo ~= nil then
      attrsInfo = baseInfo.attrs
    end
    local actor_base_info = PB.get("ActorInfo", monster_info.actorID, 1)
    if actor_base_info ~= nil and PB.get("ActorConfig", monster_info.actorID).kind == 1 then
      for k = 1, #actor_base_info.attrs do
        if actor_base_info.attrs[k].type == PB.enum.AttrType.Speed then
          actorInfo.attrs[PB.enum.AttrType.Speed].value = actor_base_info.attrs[k].value * 2
          break
        end
      end
    end
  end
  if attrsInfo ~= nil then
    for i = 1, #attrsInfo do
      local attrs = attrsInfo[i]
      local rate = 1
      if indexInfo ~= nil then
        for j = 1, #indexInfo.attrs do
          if indexInfo.attrs[j].type == attrs.type then
            rate = indexInfo.attrs[j].value / 10000
            break
          end
        end
      end
      local attr = actorInfo.attrs[attrs.type]
      if attr then
        attr.value = math.floor(attrs.value * rate)
        attr.isRatio = attrs.isRatio
      else
        error("GamePlay", "cannot find actionInfo.Attrs by attr.type: " .. attrs.type)
      end
    end
  else
    warning("GamePlay", "not find any attts at monsterID = " .. monsterID .. " level = " .. lv)
  end
  return actorInfo.attrs
end

function WU.CheckWindowTempSwitch(name)
  local type
  local resConfigs = PB.all("TempEntranceSwitchWindow")
  for i = 1, #resConfigs do
    local windows = resConfigs[i].windows
    if table.has(windows, name) then
      type = resConfigs[i].type
      break
    end
  end
  local DB = CS.DataBindingManager.Instance
  local open = true
  if type ~= nil and DB:GetData("fci/tempEntranceSwitchInfo") ~= nil then
    local switchInfo = DB:GetData("fci/tempEntranceSwitchInfo")[type]
    if switchInfo then
      open = switchInfo.switch
      if not open then
        WU.ShowMessageOK(switchInfo.tips)
      end
    end
  end
  return open
end

function WU.IsPreCommit()
  local switch = CS.DataBindingManager.Instance:GetData("AccountSwitch")
  return switch and switch.IOSPrecommit
end

function WU.OpenActorDecompose(params)
  local DB = CS.DataBindingManager.Instance
  params = params or {}
  DB:SetData("ActorDecompose/Context", nil)
  WU.AcquireWindowAsync("ActorDecompose", function()
    DB:SetData("ActorDecompose/Context", params)
  end)
end

function WU.IsDeviceModelOrNot(deviceModel)
  local currentModel = string.sub(CS.UnityEngine.SystemInfo.deviceModel, 1, string.len(deviceModel))
  return currentModel == deviceModel
end

function WU.PlayMovie(path, callback)
  CS.UnityEngine.GameObject.Find("movie"):GetComponent(typeof(CS.MovieProcess)):StartMovie(callback, path, true)
end

function WU.IsFirstTimeInClient(dataName)
  local configManager = S:Get("ConfigDataManager")
  local preferences = configManager.GetPreferences()
  local tag = "FirstTime_" .. dataName
  return preferences[tag] == nil or preferences[tag] == false
end

function WU.RecordFirstTimeInClient(dataName)
  local configManager = S:Get("ConfigDataManager")
  local preferences = configManager.GetPreferences()
  local tag = "FirstTime_" .. dataName
  if preferences[tag] then
    return
  end
  preferences[tag] = true
  configManager.SetPreferences(preferences)
end

function WU.GetClientUrlParam(url)
  local DB = CS.DataBindingManager.Instance
  local isQQInstalled = CS.Msdk.WGPlatform.Instance:WGIsPlatformInstalled(CS.Msdk.ePlatform.ePlatform_QQ)
  local isWXInstalled = CS.Msdk.WGPlatform.Instance:WGIsPlatformInstalled(CS.Msdk.ePlatform.ePlatform_Weixin)
  local isInstallQQ = 0
  local isInstallWX = 0
  local temp
  if isQQInstalled then
    temp = "&qi=1"
  else
    temp = "&qi=0"
  end
  url = url .. temp
  if isWXInstalled then
    temp = "&wi=1"
  else
    temp = "&wi=0"
  end
  url = url .. temp
  local temp = "&platid=0"
  temp = "&platid=1"
  url = url .. temp
  local worldId = DB:GetData("worldId")
  if worldId then
    url = url .. "&z=" .. worldId
  end
  local worldName = DB:GetData("WorldName")
  if worldName then
    worldName = CS.UnityEngine.WWW.EscapeURL(worldName)
    url = url .. "&zn=" .. worldName
  end
  local playerId = DB:GetData("playerId")
  if playerId then
    url = url .. "&roleid=" .. playerId
  end
  local player = DB:GetData("fci/baseinfo/")
  if player then
    local name = CS.UnityEngine.WWW.EscapeURL(player.name)
    url = url .. "&role=" .. name
  end
  info("Debug", "client url:" .. url)
  return url
end

return function(target)
  return WU, CS.DataBindingManager.Instance, _ENV["$"](target)
end
