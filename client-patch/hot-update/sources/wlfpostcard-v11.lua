local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local DBH = require("Manager/DataBindingHandler")
local S = require("Common/Singleton")
local m_activityManager = S:Get("ActivityManager")
local ACU = require("Common/ActivityUtil")
local RU = require("Common/RedMarkUtil")
local m_postcardActivityInfo, m_postcards, m_totalstatus, m_singlestatus, m_itemId
local m_currenPostcardIndex = 0
local m_showLongIntruduction = true
local m_shareOn
local m_effectOnIndex = -1
local m_fullscreenEffectOn = false
local m_actorName = {
  [1] = {lower = "mikoto", upper = "Mikoto"},
  [2] = {lower = "kirino", upper = "Kirino"},
  [3] = {
    lower = "kuroyukihime",
    upper = "Kuroyukihime"
  },
  [4] = {lower = "mashiro", upper = "Mashiro"}
}

function Awake()
  WU.BindButtonEvent(REF.DialogContainer, OnDialogContainerClick)
  WU.BindButtonEvent(REF.ButtonTotalAward, OnButtonTotalAwardClick)
  WU.BindButtonEvent(REF.Bottle, function()
    WU.ShowItemDetail(m_itemId, false)
  end)
  WU.TraverseChildren(REF.Postcards, function(go)
    WU.BindButtonEvent(_ENV["$"](go).ButtonPostcard, OnPostcardClick)
  end)
  WU.BindButtonEvent(REF.Bottle2, function()
    WU.ShowItemDetail(m_itemId, false)
  end)
  WU.BindButtonEvent(REF.ButtonBack, OnButtonBackClick)
  WU.BindButtonEvent(REF.ButtonAward, OnButtonAwardClick)
  WU.BindButtonEvent(REF.MaskClick, function()
    if m_fullscreenEffectOn then
      return
    end
    ToggleFullScreen(false)
  end)
  WU.BindButtonEvent(REF.TextureFull, function()
    if m_fullscreenEffectOn then
      return
    end
    ToggleFullScreen(true)
  end)
  WU.BindButtonEvent(REF.ButtonShare, function()
    local UIConfig = {
      window = "Welfare",
      logo = "black",
      playerBg = "black"
    }
    this:SetData("ShareConfig", UIConfig)
    ToggleFullScreen(true)
    WU.AcquireWindowAsync("Share", function()
    end)
  end)
  WU.TraverseChildren(REF.Pieces, function(go)
    WU.BindButtonEvent(_ENV["$"](go).BlockIcon, OnPieceClick)
  end)
  if WU.IsShareSwitchOn() then
    this:RegisterGameEvent("UI/WindowUninited", OnShareWindowUninited)
  end
end

function OnEnable()
  m_shareOn = WU.IsShareSwitchOn()
  m_fullscreenEffectOn = false
  -- The shipped Welfare2 prefab enables both pages. Hide the uninitialized
  -- detail colliders before showing the four selectable postcards.
  m_currenPostcardIndex = 0
  REF.Main.gameObject:SetActive(true)
  REF.Detail.gameObject:SetActive(false)
  REF.FullScreen.gameObject:SetActive(false)
  InitContent()
end

function OnDisable()
  if m_fullscreenEffectOn then
    local playTween = REF.Detail.UIPlayTween
    playTween.tweenGroup = 102
    playTween:Stop()
    playTween:ResetToBeginning()
    playTween.tweenGroup = 103
    playTween:ResetToBeginning()
    playTween:Stop()
    REF.Tools.gameObject:SetActive(true)
  end
end

function InitContent()
  REF.Container.gameObject:SetActive(false)
  m_activityManager.GetActivityByType(PB.enum.ActivityType.Postcard, function(activityList)
    if activityList then
      m_postcardActivityInfo = activityList[1]
    else
      m_postcardActivityInfo = nil
    end
  end)
  m_postcards = DB:GetData("fci/Postcard")
  if m_postcards == nil then
    DB:GameRequest("fci/Postcard"):Get(function(res)
      m_postcards = res
      DB:SetData("fci/Postcard", res)
      REF.Container.gameObject:SetActive(true)
      RefreshMain()
      ChangeRedStatus()
    end)
  else
    REF.Container.gameObject:SetActive(true)
    RefreshMain()
    ChangeRedStatus()
  end
end

function RefreshMain()
  local data = m_postcards
  local ref = _ENV["$"](REF.Main)
  if data.isFirstEnter then
    m_showLongIntruduction = false
    m_postcards.isFirstEnter = false
    this:GameRequest("fci/Postcard/"):Post({})
  else
    m_showLongIntruduction = true
  end
  OnDialogContainerClick()
  if m_currenPostcardIndex == 0 then
    m_totalstatus, m_singlestatus = GetRewardStatus()
  end
  if m_totalstatus == 0 then
    REF.ButtonTotalAward.UISprite.spriteName = "supply2"
  elseif m_totalstatus < 0 then
    REF.ButtonTotalAward.UISprite.spriteName = "supply3"
  elseif 0 < m_totalstatus then
    REF.ButtonTotalAward.UISprite.spriteName = "supply"
  end
  REF.EffectBox.gameObject:SetActive(m_totalstatus == 0)
  REF.ButtonTotalAward.UISprite:MakePixelPerfect()
  if m_currenPostcardIndex == 0 then
    for i = 1, #m_singlestatus do
      local ref = REF.Postcards[i - 1]
      _ENV["$"](ref.ButtonPostcard)["@id"] = m_postcards.postcardInfo[i].postcardId
      _ENV["$"](ref.ButtonPostcard)["@index"] = i
      SetPostcardUI(i)
    end
  else
    SetPostcardUI(m_currenPostcardIndex)
  end
  m_itemId = PB.get("PostcardCell", 1, 1).cost.id
  this:Bind("fci/item/" .. m_itemId, OnItemChange)
  m_currenPostcardIndex = 0
end

function RefreshDetail(postcardInfo)
  local ref = _ENV["$"](REF.Detail)
  local refPicture = _ENV["$"](REF.Pieces)
  REF.TextureFull.UITexture.mainTexturePath = "Texture/Welfare/2400" .. m_actorName[m_currenPostcardIndex].lower
  REF.TextureBig.UITexture.mainTexturePath = "Texture/Welfare/2400" .. m_actorName[m_currenPostcardIndex].lower
  if 0 < m_singlestatus[m_currenPostcardIndex] then
    ref.ButtonAward.UISprite.spriteName = "beach_gift"
    REF.TextureFull.gameObject:SetActive(false)
    REF.CARD.gameObject:SetActive(true)
    REF.ButtonShare.gameObject:SetActive(false)
    local records = postcardInfo.postcardCell
    for i = 1, #records do
      local BlockIcon = _ENV["$"](refPicture.root[i - 1].BlockIcon)
      BlockIcon["@id"] = records[i].cellId
      BlockIcon["@index"] = i
      BlockIcon["@state"] = records[i].isLighten
      local indexstr = fif(i < 10, "0" .. tostring(i), tostring(i))
      BlockIcon["$UITexture"].mainTexturePath = "Texture/WelfarePostcard/Postcard" .. m_actorName[m_currenPostcardIndex].upper .. "/postcard_" .. m_actorName[m_currenPostcardIndex].lower .. indexstr
      if not records[i].isLighten then
        BlockIcon["$UITexture"].color = CS.NGUIMath.HexToColor(1)
        BlockIcon["$UITexture"].solidColor = true
      else
        BlockIcon["$UITexture"].color = CS.NGUIMath.HexToColor(4294967295)
        BlockIcon["$UITexture"].solidColor = false
      end
      if #refPicture.root[i - 1].WhiteEffect ~= 0 then
        refPicture.root[i - 1].WhiteEffect[0]["$gameObject"]:SetActive(false)
      end
    end
  else
    if m_singlestatus[m_currenPostcardIndex] == 0 then
      ref.ButtonAward.UISprite.spriteName = "beach_gift_2"
    else
      ref.ButtonAward.UISprite.spriteName = "beach_gift_3"
    end
    REF.TextureFull.gameObject:SetActive(true)
    REF.CARD.gameObject:SetActive(false)
    REF.ButtonShare.gameObject:SetActive(m_shareOn)
  end
  ref.EffectBag.gameObject:SetActive(m_singlestatus[m_currenPostcardIndex] == 0)
  ref.ButtonAward.UISprite:MakePixelPerfect()
end

function ChangeRedStatus()
  local item = this:GetData("fci/item/" .. m_itemId)
  local itemCount = 0
  if item then
    itemCount = item.count or 0
  end
  if m_totalstatus == 0 then
    RU.SetRedMark("Welfare/Postcard", true)
    return
  else
    for i = 1, #m_singlestatus do
      if m_singlestatus[i] == 0 then
        RU.SetRedMark("Welfare/Postcard", true)
        return
      elseif 0 < m_singlestatus[i] and 0 < itemCount then
        RU.SetRedMark("Welfare/Postcard", true)
        return
      end
    end
  end
  RU.SetRedMark("Welfare/Postcard", false)
end

function UpdatePostcardDataAndUI()
  local postcardInfo = m_postcards.postcardInfo[m_currenPostcardIndex]
  if 0 < m_singlestatus[m_currenPostcardIndex] then
    m_singlestatus[m_currenPostcardIndex] = m_singlestatus[m_currenPostcardIndex] - 1
  end
  if m_singlestatus[m_currenPostcardIndex] == 0 then
    REF.ButtonAward.UISprite.spriteName = "beach_gift_2"
  elseif m_singlestatus[m_currenPostcardIndex] < 0 then
    REF.ButtonAward.UISprite.spriteName = "beach_gift_3"
  elseif 0 < m_singlestatus[m_currenPostcardIndex] then
    REF.ButtonAward.UISprite.spriteName = "beach_gift"
  end
  REF.ButtonAward.UISprite:MakePixelPerfect()
  ChangeRedStatus()
end

function OnDialogContainerClick(go)
  if m_postcardActivityInfo == nil then
    return
  end
  local timeStart = ACU.RenderTime2(m_postcardActivityInfo.timestampStart, true)
  local timeEnd = ACU.RenderTime2(m_postcardActivityInfo.timestampEnd, true)
  m_showLongIntruduction = not m_showLongIntruduction
  local content = ""
  local tGroup = 0
  if m_showLongIntruduction then
    content = WU.GetString("WindowPostcard_Intruduction", timeStart, timeEnd)
    REF.DialogContainer.UISprite.height = 200
  else
    content = WU.GetString("WindowPostcard_IntruductionSimple", timeStart, timeEnd)
    REF.DialogContainer.UISprite.height = 120
  end
  REF.LabelIntruduction.UIHtmlLabel.text = content
end

function OnButtonTotalAwardClick(go)
  local awardList = PB.get("PostcardPrize", 0).prize
  if m_totalstatus ~= 0 then
    ShowAward(m_totalstatus, awardList)
  else
    GetAward(0)
  end
end

function OnButtonAwardClick(go)
  local postcardId = m_postcards.postcardInfo[m_currenPostcardIndex].postcardId
  local awardList = PB.get("PostcardPrize", postcardId).prize
  if m_singlestatus[m_currenPostcardIndex] ~= 0 then
    ShowAward(m_singlestatus[m_currenPostcardIndex], awardList)
  else
    GetAward(postcardId, m_currenPostcardIndex)
  end
end

function OnPostcardClick(go)
  local index = _ENV["$"](go)["@index"]
  m_currenPostcardIndex = index
  StopPrePieceEffect()
  RefreshDetail(m_postcards.postcardInfo[index])
  REF.Main.gameObject:SetActive(false)
  REF.Detail.gameObject:SetActive(true)
end

function OnButtonBackClick(go)
  if not m_fullscreenEffectOn then
    RefreshMain()
    REF.Detail.gameObject:SetActive(false)
    REF.Main.gameObject:SetActive(true)
  end
end

function ToggleFullScreen(visible)
  REF.FullScreen.gameObject:SetActive(visible)
  local window = WU.FindWindow("Welfare")
  WU.ToggleRendering(_ENV["$"](window).Left, not visible)
  WU.ToggleTopBar("Welfare", not visible)
end

function OnItemChange(item)
  if item then
    _ENV["$"](REF.Main).LabelBottleCount.UIHtmlLabel.text = math.floor(item.count)
    _ENV["$"](REF.Detail).LabelBottleCount.UIHtmlLabel.text = math.floor(item.count)
  else
    _ENV["$"](REF.Main).LabelBottleCount.UIHtmlLabel.text = 0
    _ENV["$"](REF.Detail).LabelBottleCount.UIHtmlLabel.text = 0
  end
end

function OnShareWindowUninited()
  ToggleFullScreen(false)
end

function OnPieceClick(go)
  if m_itemId == nil then
    m_itemId = PB.get("PostcardCell", 1, 1).cost.id
  end
  local ref = _ENV["$"](go)
  if ref["@state"] then
    return
  end
  local bottle = DB:GetData("fci/item/" .. m_itemId)
  local bottleCount
  if bottle then
    bottleCount = bottle.count
  else
    bottleCount = 0
  end
  local postcardInfo = m_postcards.postcardInfo[m_currenPostcardIndex]
  local postcardId = postcardInfo.postcardId
  local cellId = ref["@id"]
  local cellIndex = ref["@index"]
  local costCount = PB.get("PostcardCell", postcardId, cellId).cost.count
  if bottleCount < costCount then
    WU.ShowHintText(WU.GetString("WindowPostcard_TipNoPiece"))
  else
    this:GameRequest("fci/Postcard/Lighten/" .. postcardId):Post({cellId = cellId}, function(res)
      DBH.ResChange(res.resChange)
      ref["@state"] = true
      m_postcards.postcardInfo[m_currenPostcardIndex].postcardCell[cellIndex].isLighten = true
      UpdatePostcardDataAndUI()
      ShowPutIntoEffect(ref)
    end)
  end
end

function SetPostcardUI(i)
  local ref = REF.Postcards[i - 1]
  if m_singlestatus[i] == 0 then
    ref.ButtonPostcard.UISprite.spriteName = "postcard_small_0" .. tostring(i)
    ref.SpriteSand.gameObject:SetActive(false)
  elseif m_singlestatus[i] < 0 then
    ref.ButtonPostcard.UISprite.spriteName = "postcard_small_0" .. tostring(i)
    ref.SpriteSand.gameObject:SetActive(false)
  elseif 0 < m_singlestatus[i] then
    ref.ButtonPostcard.UISprite.spriteName = "bottle_" .. tostring(i)
    ref.SpriteSand.gameObject:SetActive(true)
  end
  ref.ButtonPostcard.UISprite:MakePixelPerfect()
  if 0 < m_singlestatus[i] and m_singlestatus[i] < #m_postcards.postcardInfo[i].postcardCell then
    ref.BottleEffect.gameObject:SetActive(true)
  else
    ref.BottleEffect.gameObject:SetActive(false)
  end
  ref.PostcardEffect.gameObject:SetActive(m_singlestatus[i] == 0)
end

function ShowAward(state, awardList)
  if 0 < state then
    local title = WU.GetString("WindowPostcard_TotalAwardTittle")
    WU.AcquireWindowAsync("RewardPreview", function(ui)
      _ENV["$"](ui)["$$SetView"](awardList, title, "", false)
      for i, v in ipairs(awardList) do
        if v.type == PB.enum.ResourceType.ResActor then
          _ENV["$"](ui)["$$SetCustomActorInfo"](v.id, v.quality)
        end
      end
    end)
  elseif state < 0 then
    local title = WU.GetString("WindowPostcard_TotalAwardTittle")
    local buttonTitle = WU.GetString("WindowGacha_Close")
    WU.AcquireWindowAsync("RewardPreview", function(ui)
      _ENV["$"](ui)["$$SetView"](awardList, title, "", true, buttonTitle)
      for i, v in ipairs(awardList) do
        if v.type == PB.enum.ResourceType.ResActor then
          _ENV["$"](ui)["$$SetCustomActorInfo"](v.id, info.quality)
        end
      end
    end)
  end
end

function GetAward(postcardId, index)
  this:GameRequest("fci/Postcard/ReceivePrize/"):Post({postcardId = postcardId}, function(result)
    if index then
      m_singlestatus[index] = -1
      m_postcards.prizeInfo[index + 1].prizeReceiveStatus = true
    else
      m_postcards.prizeInfo[1].prizeReceiveStatus = true
      m_totalstatus = -1
    end
    DBH.ResChange(result.resChange)
    WU.ShowRewards(result.resChange)
    ChangeRedStatus()
    if index then
      REF.ButtonAward.UISprite.spriteName = "beach_gift_3"
      REF.ButtonAward.UISprite:MakePixelPerfect()
      REF.EffectBag.gameObject:SetActive(false)
    else
      REF.ButtonTotalAward.UISprite.spriteName = "supply3"
      REF.ButtonTotalAward.UISprite:MakePixelPerfect()
      REF.EffectBox.gameObject:SetActive(false)
    end
  end)
end

function GetRewardStatus()
  local single = {
    0,
    0,
    0,
    0
  }
  local total = 0
  local rewardList = m_postcards.prizeInfo
  for i = 2, #rewardList do
    if rewardList[i].prizeReceiveStatus then
      single[i - 1] = -1
    else
      local cells = m_postcards.postcardInfo[i - 1].postcardCell
      for ii = 1, #cells do
        if cells[ii].isLighten == false then
          single[i - 1] = single[i - 1] + 1
        end
      end
      if single[i - 1] ~= 0 then
        total = total + 1
      end
    end
  end
  if rewardList[1].prizeReceiveStatus then
    total = -1
  end
  return total, single
end

function ShowPutIntoEffect(ref)
  StopPrePieceEffect(true)
  local index = ref["@index"]
  m_effectOnIndex = index
  local playTween = REF.Detail.UIPlayTween
  local reff = REF.Pieces[index - 1]
  local indexstr = fif(index < 10, "0" .. tostring(index), tostring(index))
  local postcardTex = WU.AcquireAsset("Texture/WelfarePostcard/Postcard" .. m_actorName[m_currenPostcardIndex].upper .. "/postcard_" .. m_actorName[m_currenPostcardIndex].lower .. indexstr)
  local pMaskTex = WU.AcquireAsset("Texture/WelfarePostcard/PostcardEffect/postcardMask_" .. indexstr)
  local pGlow = WU.AcquireAsset("Texture/WelfarePostcard/PostcardEffect/postcardADD_" .. indexstr)
  _ENV["$"](reff.PieceEffect).Postcard.MeshRenderer.material:SetTexture("_MainTex", postcardTex)
  _ENV["$"](reff.PieceEffect).PostcardMASK.MeshRenderer.material:SetTexture("_MainTex", pMaskTex)
  _ENV["$"](reff.PieceEffect).PostcardGLOW.MeshRenderer.material:SetTexture("_MainTex", pGlow)
  _ENV["$"](reff.PieceEffect).FlowGlow.MeshRenderer.material:SetTexture("_MaskTex", postcardTex)
  TogglePieceEffect(true, reff)
  REF.LightEffect.EffectGenerator:Play()
  REF.TextureTint.TweenAlpha:ResetToBeginning()
  REF.TextureTint.TweenAlpha:PlayForward()
  REF.TextureTintADD.TweenAlpha:ResetToBeginning()
  REF.TextureTintADD.TweenAlpha:PlayForward()
  reff.PieceEffect.EffectGenerator:Play(function()
    TogglePieceEffect(false, reff)
    reff.BlockIcon.UITexture.color = CS.NGUIMath.HexToColor(4294967295)
    reff.BlockIcon.UITexture.solidColor = false
    m_effectOnIndex = -1
  end)
  playTween.tweenGroup = 102
  playTween:ResetToBeginning()
  playTween:Play(true)
  if m_singlestatus[m_currenPostcardIndex] == 0 then
    m_fullscreenEffectOn = true
    m_totalstatus = m_totalstatus - 1
    this:DelayInvokeInSeconds(2.5, function()
      REF.Tools.gameObject:SetActive(false)
      REF.FSStarEffect.gameObject:SetActive(true)
      REF.FSStarEffect.EffectGenerator:Play(function()
        REF.FSStarEffect.gameObject:SetActive(false)
        m_fullscreenEffectOn = false
      end)
      playTween.tweenGroup = 103
      playTween:ResetToBeginning()
      playTween:Play(true)
      local map = _ENV["!"]({})
      local order = _ENV["!"]({})
      local num = #REF.Pieces
      for i = 1, num do
        table.insert(map, i)
      end
      for i = 1, num do
        local t = math.random(1, num - i + 1)
        table.insert(order, map[t])
        table.remove(map, t)
      end
      for i = 1, #order do
        local t = i
        local index = order[i]
        local indexstr = fif(index < 10, "0" .. tostring(index), tostring(index))
        local pGlow = WU.AcquireAsset("Texture/WelfarePostcard/PostcardEffect/postcardADD_" .. indexstr)
        local t = _ENV["$"](REF.Pieces[index - 1].WhiteEffect).PostcardGLOW
        _ENV["$"](REF.Pieces[index - 1].WhiteEffect).PostcardGLOW.MeshRenderer.material:SetTexture("_MainTex", pGlow)
        this:DelayInvokeInSeconds(i * 0.06, function()
          REF.Pieces[index - 1].WhiteEffect.EffectGenerator:Play(function()
          end)
        end)
      end
      local effectEnd = false
      this:DelayInvokeInSeconds(1.3, function()
        REF.TextureFull.gameObject:SetActive(true)
        ToggleFullScreen(true)
        REF.ButtonShare.gameObject:SetActive(m_shareOn)
        REF.Tools.gameObject:SetActive(true)
        if effectEnd then
          REF.EffectBag.gameObject:SetActive(true)
        else
          effectEnd = true
        end
      end)
      local rotationTweens = REF.Detail.transform:GetComponents(typeof(CS.TweenScale))
      local t = rotationTweens[0].delay + rotationTweens[0].duration
      this:DelayInvokeInSeconds(t, function()
        REF.CARD.gameObject:SetActive(false)
        playTween:ResetToBeginning()
        if effectEnd then
          REF.EffectBag.gameObject:SetActive(true)
        else
          effectEnd = true
        end
      end)
    end)
  end
end

function StopPrePieceEffect(current)
  if m_effectOnIndex ~= -1 then
    local preReff = REF.Pieces[m_effectOnIndex - 1]
    TogglePieceEffect(false, preReff)
    if current then
      preReff.BlockIcon.UITexture.color = CS.NGUIMath.HexToColor(4294967295)
      preReff.BlockIcon.UITexture.solidColor = false
    else
      REF.FSStarEffect.gameObject:SetActive(false)
    end
    local playTween = REF.Detail.UIPlayTween
    playTween.tweenGroup = 102
    playTween:Finish()
    m_effectOnIndex = -1
  end
end

function TogglePieceEffect(visible, ref)
  REF.LightEffect.gameObject:SetActive(visible)
  REF.TextureTint.gameObject:SetActive(visible)
  REF.TextureTintADD.gameObject:SetActive(visible)
  if ref then
    ref.PieceEffect.gameObject:SetActive(visible)
  end
end
