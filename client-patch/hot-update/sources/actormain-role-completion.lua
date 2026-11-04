local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local U = require("Common/Util")
local AU = require("Common/ActorUtil")
local DBH = require("Manager/DataBindingHandler")
local HU = require("Common/HtmlUtil")
local EU = require("Common/EquipUtil")
local GU = require("Common/GroupUtil")
local SE = require("Common/SkillEffect")
local BGName = {
  "role_bg",
  "role_bg_sub"
}
local ActorTypePrefix = {
  "Window_ActorMainPrefix",
  "Window_ActorAssistPrefix"
}
local m_repeatedContext = {}
local m_actorUid = 0
local m_actor, m_shouldRecycle

function SetupWindow()
  WU.BindButtonEvent(REF.ButtonInherit, function()
    local actors = this:GetData("fci/actor/")
    local filteredActors = _ENV["!"](actors):where(function(_, v)
      return v.id == m_actor.id and v.uid ~= m_actor.uid
    end):toarray()
    if #filteredActors == 0 then
      WU.ShowHintText(WU.GetString("Window_NoSuitableActorsForInherit"))
    elseif m_actor.isLock then
      WU.ShowHintText(WU.GetString("Window_ActorIsLocked"))
    elseif not AU.ActorDeleteValidate(m_actor.uid) then
      WU.ShowHintText(WU.GetString("Window_StatusSpaceOrAura"))
    else
      this:SetData("ActorTalentInherit/Mode", 1)
      WU.AcquireWindowAsync("ActorTalentInherit")
    end
  end)
  WU.BindButtonEvent(REF.ButtonGoToNotepad, function()
    if not WU.WindowIsLocked(PB.enum.UnlockWindow.RoleNotepad, PB.enum.UnlockWindow.RoleNotepad) then
      local actorConfig = PB.get("ActorConfig", m_actor.id)
      this:SetData("RoleNotepadUpdateAble", true)
      this:SetData("Notepad/SelectedRoleId", actorConfig.role)
      this:SetData("Notepad/AllSortedNotepads", nil)
      WU.AcquireWindowAsync("RoleNotepadDetail")
    end
  end)
  WU.BindButtonEvent(REF.ButtonGoToCollectionsActor, function()
    if not WU.WindowIsLocked(PB.enum.UnlockWindow.Journey, PB.enum.UnlockWindow.Journey) then
      WU.AcquireWindowAsync("CollectionsActorDetail", function(w)
        _ENV["$"](w)["$$SetViewFrom"](2)
        local array = this:GetData("Collections/Actors")
        for _, v in pairs(array) do
          if v.id == m_actor.id then
            _ENV["$"](w)["$$SetViewFrom"](2)
            _ENV["$"](w)["$$SetActor"](v, false)
          end
        end
      end)
    end
  end)
  WU.BindButtonEvent(REF.ButtonLock, OnButtonLock)
  WU.BindButtonEvent(REF.ButtonComment, function(go)
    WU.AcquireWindowAsync("ActorComments", function(window)
      _ENV["$"](window)["$$SetWhereFrom"](2)
      _ENV["$"](window)["$$InitData"]()
    end)
  end)
  WU.BindButtonEvent(REF.ButtonEquip, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabEquip")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  WU.BindButtonEvent(REF.SpritePanelBG, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabCultivation")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  WU.BindButtonEvent(REF.ButtonTalent, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabTalent")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  WU.BindButtonEvent(REF.ButtonRelation, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabRelation")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  WU.BindButtonEvent(REF.SectionLevelup, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabCultivation")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  WU.BindButtonEvent(REF.SectionStarLevelup, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabStar")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  WU.BindButtonEvent(REF.ButtonSkill, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabSkill")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  WU.BindButtonEvent(REF.ButtonClothes, function(go)
    WU.AcquireWindowAsync("ActorSkin", function(window)
    end)
  end)
  WU.BindButtonEvent(REF.ButtonCultivation, function(go)
    WU.AcquireWindowAsync("ActorInfo", function(window)
      _ENV["$"](window)["$$SetSelectedTab"]("TabCultivation")
      _ENV["$"](window)["$$ShowSwitch"]()
    end)
  end)
  for i = 0, #REF.EquipSlotGrid - 1 do
    local slotRef = REF.EquipSlotGrid[i]
    slotRef["$$SetEquipPos"](i)
    slotRef["$$SetShowHighlight"](false)
  end
  REF.Equips.gameObject:SetActive(false)
  REF.WidgetActorRoller["$SetMode"]("actor")
end

function InitWindow()
  this:Bind("Actor/SelectedUid", OnCurrentActorUidChanged)
  if WU.IsShareSwitchOn() then
    this:RegisterGameEvent("UI/WindowUninited", OnWindowUninited)
  end
  this:RegisterGameEvent("EventEquipLevelup", UpdateActorEquipScore)
end

function SaveContext(context)
  table.insert(m_repeatedContext, m_actorUid)
end

function LoadContext(context)
  if 0 < #m_repeatedContext then
    this:SetData("Actor/SelectedUid", m_repeatedContext[#m_repeatedContext])
    table.remove(m_repeatedContext, #m_repeatedContext)
  end
end

function Focus(on)
  if on then
    REF["$Animator"]:Play("Open", -1, 0)
  end
  if on and m_shouldRecycle then
    m_shouldRecycle = false
    WU.ShowHintText(WU.GetString("ForceRecycleWindow_ActorIsDecomposed"))
    WU.RecycleWindow(this)
  end
end

function SetShareInfo()
  local UIConfig = {
    window = "ActorMain",
    logo = "white",
    playerBg = "black"
  }
  this:SetData("ShareConfig", UIConfig)
  RebuildUI(true)
  this:SetData("ShareActorId", m_actor.id)
end

function OnWindowUninited(window)
  if window.name == "Share" then
    RebuildUI(false)
    this:SetData("ShareActorId", nil)
  end
end

function RebuildUI(visible)
  REF.Equips.gameObject:SetActive(visible)
  REF.ShareBG.gameObject:SetActive(visible)
  REF.ShareButton.gameObject:SetActive(visible)
  REF.BottomBar.gameObject:SetActive(not visible)
  REF.BottomBarBG.gameObject:SetActive(not visible)
  if visible then
    _ENV["$"](REF.ShareCultivation).LabelNumber.UILabel.text = _ENV["$"](REF.ButtonCultivation).LabelNumber.UILabel.text
    _ENV["$"](REF.ShareSkill).LabelNumber.UILabel.text = _ENV["$"](REF.ButtonSkill).LabelNumber.UILabel.text
    _ENV["$"](REF.ShareTalent).LabelNumber.UILabel.text = _ENV["$"](REF.ButtonTalent).LabelNumber.UILabel.text
  end
end

function OnCurrentActorUidChanged(uid)
  if uid then
    m_shouldRecycle = false
    if m_actorUid then
      this:Unbind("fci/actor/" .. m_actorUid, OnActorChange)
    end
    m_actorUid = uid
    if m_actorUid then
      this:Bind("fci/actor/" .. m_actorUid, OnActorChange)
    end
  end
end

function HideOrShowGotoNotepad()
  local all = this:GetData("fci/RoleNotePad/")
  local hasNotepad = false
  local actorConfig = PB.get("ActorConfig", m_actor.id)
  for _, v in pairs(all) do
    if actorConfig.role == v.roleId then
      hasNotepad = true
      break
    end
  end
  REF.ButtonGoToNotepad.gameObject:SetActive(hasNotepad)
end

function ShowActorUnlockEffect(actor)
  local isFinish = false
  local isShowEnd = false
  local curInfo
  local actorCultivationInfo = DB:GetData("fci/actor-cultivation")
  if actorCultivationInfo and 0 < #actorCultivationInfo then
    for _, v in pairs(actorCultivationInfo) do
      if v.actorId == actor.id and v.endTime and 0 < v.endTime then
        isFinish = true
        isShowEnd = v.showEnd
        curInfo = v
        break
      end
    end
  end
  if isFinish then
    if not isShowEnd then
      if curInfo then
        curInfo.showEnd = true
      end
      this:GameRequest("fci/actor-cultivation/showend/"):SyncInput(false):Patch({
        actorId = actor.id
      })
      WU.AcquireWindowAsync("ActorTaskGetResult", function(ui)
        _ENV["$"](ui)["$$SetActor"](actor)
      end)
    else
      local info = PB.get("ActorUnlockEffect", actor.id)
      if info == nil then
        warning("Debug", "ActorUnlockEffect no actor:" .. actor.id)
      else
        REF.TextureBG.UITexture.mainTexturePath = "Texture/Background/" .. info.actorBgPath
      end
    end
  else
    local actorConfig = PB.get("ActorConfig", actor.id)
    REF.TextureBG.UITexture.mainTexturePath = "Texture/Background/" .. BGName[actorConfig.kind]
  end
end

function SetActor(actor)
  m_actor = actor
  ShowActorUnlockEffect(actor)
  ShowRedPoint()
  HideOrShowGotoNotepad()
  local curLevelSum = SE.GetSkillCount(m_actor)
  _ENV["$"](REF.ButtonSkill).LabelNumber.UILabel.text = curLevelSum
  _ENV["$"](REF.ButtonSkill).Layout.UITable:Reposition()
  UpdateActorEquipScore()
  REF.LabelActorLevel.UILabel.text = WU.GetString("Window_Level", actor.level)
  local maxStar = PB.all("Misc"):first().maxStarLevel
  REF.WidgetStarGridL["$SetStar"](m_actor.star, maxStar - m_actor.star, true)
  REF.WidgetActorCardS["$SetActorCustom"](actor)
  REF.WidgetActorCardS["$ShowCustom"]({showActorInfo = false})
  local isMaxStar = maxStar <= actor.star
  REF.LabelExp.gameObject:SetActive(not isMaxStar)
  REF.SpriteExpProgress.UISprite.spriteName = fif(isMaxStar, "levelup_bar_01", "levelup_bar_02")
  local actorTag = PB.get("ActorTag", actor.id)
  local actorConfig = PB.get("ActorConfig", actor.id)
  REF.LabelCharactorType.UILabel.text = WU.GetString(ActorTypePrefix[actorConfig.kind]) .. WU.GetString("tag" .. actorTag.tag[1])
  REF.SpriteLocked.gameObject:SetActive(actor.isLock)
  REF.SpriteUnlocked.gameObject:SetActive(not actor.isLock)
  REF.SpriteSub.gameObject:SetActive(actorConfig.kind == PB.enum.ActorType.Sub)
  REF.SpriteSub.UISprite.spriteName = "rolelist_sub_s_" .. actor.quality
  REF.SpriteActorType.UISprite.spriteName = "actor_type_" .. actor.id
  REF.SpriteActorName.UISprite.spriteName = "actor_name_" .. actorConfig.animRes
  -- D/local: preserve the original sprite; same-Role fallback only when it is absent.
  WU.CompleteRoleNameSprite(REF.SpriteActorName.UISprite)
  REF.LabelCV.UILabel.text = WU.GetString("ActorCV_" .. actor.id)
  REF.SpriteQualityBG.UISprite.spriteName = "quality_" .. actor.quality
  REF.SpriteQuality.UISprite.spriteName = string.lower(AU.ActorQuality2Tag[actor.quality])
end

function OnActorChange(actor)
  if actor ~= nil and actor.id ~= 0 then
    local totalScore = AU.ActorTalentScore(actor)
    _ENV["$"](REF.ButtonTalent).LabelNumber.UILabel.text = totalScore
    _ENV["$"](REF.ButtonTalent).Layout.UITable:Reposition()
    SetActor(actor)
    local maxActorLevel = AU.GetActorMaxLevel()
    local reachMaxLevel = maxActorLevel <= actor.level
    REF.LabelExp.gameObject:SetActive(not reachMaxLevel)
    REF.LabelMax.gameObject:SetActive(reachMaxLevel)
    if not reachMaxLevel then
      local actorLevelup = PB.get("ActorLevelup", actor.level + 1)
      REF.LabelExp.UILabel.text = actor.curExp .. "/" .. actorLevelup.exp
      REF.NodeExpProgressBar.UIProgressBar.value = actor.curExp / actorLevelup.exp
    else
      REF.NodeExpProgressBar.UIProgressBar.value = 1
    end
    _ENV["$"](REF.ButtonCultivation).LabelNumber.UILabel.text = string.format("%d", actor.cultivationTotal or 0)
    _ENV["$"](REF.ButtonCultivation).Layout.UITable:Reposition()
  else
    m_shouldRecycle = true
  end
end

function UpdateActorEquipScore()
  if m_actor then
    local equips = EU.GetEquips(m_actor)
    local equipScore = 0
    for _, equip in pairs(equips) do
      equipScore = equipScore + equip.level
    end
    _ENV["$"](REF.ButtonEquip).LabelNumber.UILabel.text = equipScore
    _ENV["$"](REF.ButtonEquip).Layout.UITable:Reposition()
  end
end

function OnButtonLock(go)
  this:GameRequest("fci/actor/" .. m_actorUid .. "/setlock/"):Post({
    isLock = not m_actor.isLock
  }, function(result)
    DBH.UpdateActors(result)
  end)
end

function ShowRedPoint()
  REF.SpriteRedPointSkill.gameObject:SetActive(SE.HasSkillToUpgrade(m_actor))
  REF.SpriteRedPointStar.gameObject:SetActive(AU.UpgradeStarEnable(m_actor))
  REF.SpriteRedPointEquip.gameObject:SetActive(EU.HasPosToEquip(m_actor))
end
