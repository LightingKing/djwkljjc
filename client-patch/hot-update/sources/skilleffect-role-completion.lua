local WU, DB = require("Common/WindowUtil")()
local U = require("Common/Util")
local PB = require("Common/PbHelper")
local TU = require("Common/TextUtil")
local SE = {}
local m_actorSkillComboMap

function SE.GetActorComboEffect(skillId, level, actionIndex, speIndex)
  local effect = {}
  local actorCombo = SE.GetActorSkillCombo(skillId)
  if actorCombo then
    effect = SE.GetSkillEffect(actorCombo.skillList, level, actionIndex, speIndex)
  end
  return effect
end

function SE.GetActorLinkEffect(linkId, star, actionIndex, speIndex)
  local effect = {}
  local level = math.max(2, star or 2) - 1
  local actorLink = PB.get("ActorLink", linkId)
  local skillList = {}
  for _, link in ipairs(actorLink.link) do
    table.insert(skillList, link.skillID)
  end
  if actorLink then
    effect = SE.GetSkillEffect(skillList, level, actionIndex, speIndex)
  end
  return effect
end

function SE.GetSkillEffect(skillIds, level, actionIndex, speIndex)
  local effect = {
    attrs = {},
    buffs = {},
    triggers = {}
  }
  local triggerIndex = 0
  local attrList = {}
  local buffDict = _ENV["!"]({})
  local buffIndexDict = _ENV["!"]({})
  local buffPriorityDict = {}
  if type(skillIds) == "number" then
    skillIds = {skillIds}
  end
  for _, skillId in ipairs(skillIds) do
    local actionDamages = PB.all("ActionDamage"):where(function(k, v)
      return v.skillId == skillId
    end)
    if not actionDamages:empty() then
      for _, actionDamage in pairs(actionDamages) do
        local damageIndex = PB.get("DamageIndex", actionDamage.index, 1)
        local deltaRatio = (damageIndex.upRate - 10000) / 10000 / math.max(1, damageIndex.maxLevel - 1)
        local levelRatio = 1 + deltaRatio * (level - 1)
        for i = 1, #damageIndex.attrs do
          local attr = damageIndex.attrs[i]
          if attrList[i] then
            attrList[i].value = attrList[i].value + attr.value * levelRatio
            attrList[i].delta = attrList[i].delta + attr.value * deltaRatio
          else
            attrList[i] = {
              type = attr.type,
              isRatio = attr.isRatio,
              value = attr.value * levelRatio,
              delta = attr.value * deltaRatio
            }
          end
        end
      end
    end
    for i = 1, #attrList do
      local attr = attrList[i]
      attr.value = math.floor(attr.value + 0.5)
      attr.delta = math.floor(attr.delta + 0.5)
      effect.attrs[i - 1] = {
        value = U.FormatAttrValue(attr),
        delta = U.FormatAttrValue({
          isRatio = attr.isRatio,
          value = attr.delta
        })
      }
    end
    actionIndex = actionIndex or 1
    local actionBuffs = PB.all("ActionBuff"):where(function(k, v)
      return v.skillId == skillId
    end)
    for _, actionBuff in pairs(actionBuffs) do
      if actionBuff and actionIndex <= #actionBuff.buffIndex then
        local buffIndex = PB.get("BuffIndex", actionBuff.buffIndex[actionIndex])
        if not buffIndexDict[buffIndex] then
          buffIndexDict[buffIndex] = true
          for i = 1, #buffIndex.buffId do
            local buffData = PB.get("BuffData", buffIndex.buffId[i], buffIndex.owner[i])
            if buffData then
              if buffDict[buffData.id .. buffData.owner] then
                error("duplicated buffs found within ActorSkill " .. skillId)
              elseif 1 > buffData.priority then
                error("invalid priority for BuffData with id " .. buffData.id)
              elseif buffPriorityDict[buffData.priority] then
                error("BuffData " .. buffPriorityDict[buffData.priority] .. " and " .. buffData.id .. " share same priority within ActorSkill " .. skillId)
              else
                buffDict[buffData.id .. buffData.owner] = {
                  buffData = buffData,
                  launchChance = buffIndex.launchChance[i]
                }
                buffPriorityDict[buffData.priority] = buffData.id
              end
            end
          end
        end
      end
    end
    local buffList = buffDict:toarray()
    table.sort(buffList, function(a, b)
      return a.buffData.priority < b.buffData.priority
    end)
    for i = 1, #buffList do
      effect.buffs[i - 1] = SE.GetBuffEffect(buffList[i].buffData, level)
      effect.buffs[i - 1].launchChance = buffList[i].launchChance
    end
    local actorSkill = PB.get("ActorSkill", skillId)
    if actorSkill then
      local index_val = actorSkill.specialIndex[speIndex or 1]
      if index_val ~= nil and 0 < index_val then
        effect.triggers[triggerIndex] = SE.GetSpecialEffect(index_val, level)
        triggerIndex = triggerIndex + 1
      end
    end
  end
  return effect
end

function SE.GetActorTalentEffect(talent)
  return SE.GetSpecialEffect(talent.talentId, talent.talentValue)
end

function GetSpecialAttrInfoIndexRecursively(index)
  local result = _ENV["!"]({index})
  local specialAttrInfo = PB.get("SpecialAttrInfo", index)
  if specialAttrInfo then
    for _, childId in pairs(specialAttrInfo.childSpecialAttrInfoId) do
      result:append(GetSpecialAttrInfoIndexRecursively(childId))
    end
  else
    error("SpecialAttrInfo", "key " .. index .. " was not found in SpecialAttrInfo.xlsx")
  end
  return result
end

function SE.GetSpecialEffect(index, level)
  local effect = {
    attrs = {},
    buffs = {}
  }
  level = level or 1
  local indexList = GetSpecialAttrInfoIndexRecursively(index)
  local attrList = {}
  local buffDict = _ENV["!"]({})
  local buffPriorityDict = {}
  for _, index in pairs(indexList) do
    local specialAttrInfo = PB.get("SpecialAttrInfo", index)
    if specialAttrInfo then
      if specialAttrInfo.timing == 7 then
        local specialLevelUpIndex = PB.get("SpecialLevelUpIndex", index)
        for i = 1, #specialLevelUpIndex.attrs do
          local attr = specialLevelUpIndex.attrs[i]
          local levelRatio = 1 + (specialLevelUpIndex.upRate[i] - 10000) / 10000 / math.max(1, specialAttrInfo.maxLevel - 1) * (level - 1)
          attrList[i] = {
            type = attr.type,
            isRatio = attr.isRatio,
            value = attr.value * levelRatio
          }
        end
      else
        local buffIndex = PB.get("BuffIndex", index)
        for i = 1, #buffIndex.buffId do
          local buffData = PB.get("BuffData", buffIndex.buffId[i], buffIndex.owner[i])
          if not buffData or 1 > buffData.priority then
          elseif buffPriorityDict[buffData.priority] then
          else
            buffDict[buffData.id .. buffData.owner] = {
              buffData = buffData,
              launchChance = buffIndex.launchChance[i]
            }
            buffPriorityDict[buffData.priority] = buffData.id
          end
        end
      end
    else
      error("SpecialAttrInfo", "key " .. index .. " was not found in SpecialAttrInfo.xlsx")
    end
  end
  for i = 1, #attrList do
    local attr = attrList[i]
    attr.value = math.floor(attr.value + 0.5)
    effect.attrs[i - 1] = {
      value = U.FormatAttrValue(attr)
    }
  end
  local buffList = buffDict:toarray()
  table.sort(buffList, function(a, b)
    return a.buffData.priority < b.buffData.priority
  end)
  for i = 1, #buffList do
    effect.buffs[i - 1] = SE.GetBuffEffect(buffList[i].buffData, level)
    effect.buffs[i - 1].launchChance = buffList[i].launchChance
  end
  return effect
end

function SE.GetBuffEffect(buffData, level)
  local effect = {
    maxRound = buffData.maxRound,
    maxStack = buffData.maxStack,
    paramInt = {},
    value = {},
    delta = {}
  }
  for p = 1, #buffData.paramInt do
    effect.paramInt[p - 1] = buffData.paramInt[p]
  end
  local deltaRatio = (buffData.upRate - 10000) / 10000 / math.max(1, buffData.maxLevel - 1)
  local levelRatio = 1 + deltaRatio * (level - 1)
  if buffData.type == PB.enum.BuffFightType.XiShou then
    effect.value[0] = math.floor(effect.paramInt[2] * levelRatio + 0.5)
    effect.value[1] = math.floor(effect.paramInt[3] * levelRatio + 0.5)
    effect.value[2] = math.floor(effect.paramInt[4] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[2] * deltaRatio + 0.5)
    effect.delta[1] = math.floor(effect.paramInt[3] * deltaRatio + 0.5)
    effect.delta[2] = math.floor(effect.paramInt[4] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.HpChange then
    effect.value[0] = math.floor(effect.paramInt[1] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[1] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.ActionFeedBack then
    effect.value[0] = math.floor(effect.paramInt[1] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[1] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.AbHurt then
    effect.value[0] = math.floor(effect.paramInt[1] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[1] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.Attr then
    effect.value[0] = math.floor(effect.paramInt[2] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[2] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.Rebound then
    effect.value[0] = math.floor(effect.paramInt[1] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[1] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.KeepAlive then
    effect.value[0] = math.floor(effect.paramInt[1] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[1] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.HpAddChange then
    effect.value[0] = math.floor(effect.paramInt[0] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[0] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.MpOnceChange then
    effect.value[0] = math.floor(effect.paramInt[0] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[0] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.SpOnceChange then
    effect.value[0] = math.floor(effect.paramInt[0] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[0] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.MpCostChange then
    effect.value[0] = math.floor(effect.paramInt[0] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[0] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.SkillDamageChange then
    effect.value[0] = math.floor(effect.paramInt[2] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[2] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.ChangeRoundAddMp then
    effect.value[0] = math.floor(effect.paramInt[0] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[0] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.SpRateChange then
    effect.value[0] = math.floor(effect.paramInt[1] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[1] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.SpToMp then
    effect.value[0] = math.floor(effect.paramInt[1] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[1] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.StackDelete then
    effect.value[0] = math.floor(effect.paramInt[0] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[0] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.BuffValChange then
    effect.value[0] = math.floor(effect.paramInt[2] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[2] * deltaRatio + 0.5)
  elseif buffData.type == PB.enum.BuffFightType.SkillReset then
    effect.value[0] = math.floor(effect.paramInt[0] * levelRatio + 0.5)
    effect.delta[0] = math.floor(effect.paramInt[0] * deltaRatio + 0.5)
  end
  return effect
end

function SE.GetActorSkillCombo(skillId)
  if not m_actorSkillComboMap then
    m_actorSkillComboMap = {}
    local actorCombos = PB.all("ActorCombo")
    for _, actorCombo in pairs(actorCombos) do
      for _, skillId in pairs(actorCombo.skillList) do
        m_actorSkillComboMap[skillId] = actorCombo
      end
    end
  end
  return m_actorSkillComboMap[skillId]
end

function SE.GetActorSkillCost(skillId)
  local actorCombo = SE.GetActorSkillCombo(skillId)
  if actorCombo then
    return {
      costMp = actorCombo.costMp,
      costSp = actorCombo.costSp
    }
  else
    return nil
  end
end

function SE.GetSkillCostDesc(sp, mp)
  if 0 < sp then
    return "<font color=#FFBA00>" .. sp .. WU.GetString("Window_SPCost") .. "</font>"
  else
    return "<font color=#009CFF>" .. mp .. WU.GetString("Window_MPCost") .. "</font>"
  end
end

function SE.GetSkillLevel(actor, skillId)
  local skillLevel = 0
  local _, skill = table.find(actor.skills, function(_, v)
    return v.id == skillId
  end)
  if skill then
    skillLevel = skill.level
  end
  return skillLevel
end

function SE.IsSkillRead(actor, skillId)
  local result = false
  local _, skill = table.find(actor.skills, function(_, v)
    return v.id == skillId
  end)
  if skill then
    result = skill.isRead
  end
  return result
end

function SE.SetSkillRead(actor, skillId)
  local _, skill = table.find(actor.skills, function(_, v)
    return v.id == skillId
  end)
  if skill then
    skill.isRead = true
  end
end

function SE.GetActorSkills(actorId, activeOnly)
  local skills = _ENV["!"]({})
  for _, skill in ipairs(PB.all("ActorSkill")) do
    if skill.actorId == actorId and not skill.isLink and skill.displayIndex > 0 and (not activeOnly or not skill.isPassive) then
      local skillGroup = skills[skill.displayIndex] or {}
      table.insert(skillGroup, skill)
      skills[skill.displayIndex] = skillGroup
    end
  end
  return skills
end

function SE.GetSkillCount(actor)
  local curLevelSum = 0
  local skills = SE.GetActorSkills(actor.id)
  for _, skillGroup in pairs(skills) do
    curLevelSum = curLevelSum + SE.GetSkillLevel(actor, skillGroup[1].skillId)
  end
  return curLevelSum
end

function SE.HasSkillToUpgrade(actor)
  local skillFlag = false
  local skills = SE.GetActorSkills(actor.id)
  for i = 1, #skills do
    local skillGroup = skills[i]
    local skillLevel = SE.GetSkillLevel(actor, skillGroup[1].skillId)
    if 0 < skillLevel then
      local nextSkillLevelup = PB.get("SkillLevelup", skillGroup[1].costIndex, skillLevel + 1)
      if nextSkillLevelup ~= nil then
        local actorSkillMaxLevel = PB.get("ActorSkillMaxLevel", actor.id, actor.star)
        if actorSkillMaxLevel == nil then
          error("ActorSkillMaxLevel", "ActorSkillMaxLevel.xlsx error, id : " .. actor.id .. " star : " .. actor.star)
          return false
        end
        local skillMaxLevel = actorSkillMaxLevel.maxLevel[skillGroup[1].displayIndex]
        if skillMaxLevel == nil then
          error("ActorSkillMaxLevel", "can't find max skill level, actorId: " .. tostring(actor.id) .. " actorStar: " .. tostring(actor.star) .. " displayIndex: " .. tostring(skillGroup[1].displayIndex))
          return false
        end
        local skillLevelSatisfied = skillLevel < skillMaxLevel
        local affordAble = true
        local cost = nextSkillLevelup.cost
        if skillLevelSatisfied then
          for j = 1, #cost do
            local costCount = cost[j].count
            local stockCount = 0
            if cost[j].type == PB.enum.ResourceType.ResItem then
              stockCount = DB:GetData("fci/item/" .. cost[j].id).count
            else
              stockCount = DB:GetData("fci/resource/" .. PB.enum.ResourceType.__keys[cost[j].type] .. "_" .. cost[j].id)
            end
            if costCount > stockCount then
              affordAble = false
              break
            end
          end
        end
        skillFlag = skillLevelSatisfied and affordAble
        if skillFlag == true then
          break
        end
      end
    end
  end
  return skillFlag
end

function SE.IsSkillUnlockedRaw(skill)
  local unlockStarCount = 0
  if skill.isPassive then
    local _, actorAttrStarPromotion = PB.all("ActorAttrStarPromotion"):find(function(_, v)
      return v.unlockSkillId == skill.skillId
    end)
    if actorAttrStarPromotion then
      unlockStarCount = actorAttrStarPromotion.star
    end
  end
  return unlockStarCount
end

function SE.IsSkillUnlocked(actor, skill)
  local unlocked = true
  local unlockStarCount = 0
  if skill.isPassive then
    local _, actorAttrStarPromotion = PB.all("ActorAttrStarPromotion"):find(function(_, v)
      return v.unlockSkillId == skill.skillId
    end)
    if actorAttrStarPromotion and actor.star < actorAttrStarPromotion.star then
      unlockStarCount = actorAttrStarPromotion.star
      unlocked = false
    end
  end
  return unlocked, unlockStarCount
end

function SE.GetSkillDetail(actor, actorSkillGroup, describeColor, actionIndex, speIndex)
  local result = {}
  local defaultSkill = actorSkillGroup[1]
  result.displayIndex = defaultSkill.displayIndex
  result.skillLevel = math.max(1, SE.GetSkillLevel(actor, defaultSkill.skillId))
  result.skillIds = {}
  result.skillNames = {}
  result.mainTexturePaths = {}
  result.kindIcons = {}
  local actorConfig = PB.get("ActorConfig", actor.id)
  if actorConfig then
    for _, skill in ipairs(actorSkillGroup) do
      table.insert(result.skillIds, skill.skillId)
      table.insert(result.skillNames, WU.GetString("SkillName_" .. skill.skillId))
      table.insert(result.mainTexturePaths, SE.GetSkillTexturePath(skill, actorConfig.id))
      if not skill.isPassive then
        table.insert(result.kindIcons, SE.GetActorSkillCombo(skill.skillId).kindIcon)
      end
    end
  else
    error("ActorConfig", "ActorConfig.xlsx error , id  " .. tostring(actor.id))
  end
  local levelLimit = PB.get("ActorSkillMaxLevel", actor.id, actor.star)
  local max = PB.get("ActorSkillMaxLevel", actor.id, 6)
  if levelLimit and max then
    result.isMax = max.maxLevel[result.displayIndex] == result.skillLevel
    result.isLevelLimit = levelLimit.maxLevel[result.displayIndex] <= result.skillLevel
    if not result.isMax then
      for i = 1, 6 do
        local actorSkillMaxLevel = PB.get("ActorSkillMaxLevel", actor.id, i)
        if actorSkillMaxLevel.maxLevel[result.displayIndex] > result.skillLevel then
          result.nextLevelStar = actorSkillMaxLevel.star
          break
        end
      end
      local skillLevelup = PB.get("SkillLevelup", defaultSkill.costIndex, result.skillLevel + 1)
      result.levelupCosts = skillLevelup.cost
    end
    result.describes = {}
    result.nextDescribes = {}
    for _, skill in ipairs(actorSkillGroup) do
      local effect = {}
      effect.current = SE.GetSkillEffectCommon(skill, result.skillLevel, actionIndex, speIndex)
      table.insert(result.describes, WU.FormatString(TU.render(WU.GetString("SkillDescribe_" .. skill.skillId), effect.current), describeColor))
      if not result.isMax then
        effect.next = SE.GetSkillEffectCommon(skill, result.skillLevel + 1, actionIndex, speIndex)
        table.insert(result.nextDescribes, WU.FormatString(TU.render(WU.GetString("NextSkillDescribe_" .. skill.skillId), effect), describeColor))
      end
    end
  else
    error("ActorSkillMaxLevel", "ActorSkillMaxLevel.xlsx error, id " .. tostring(actor.id) .. " star " .. tostring(actor.star))
  end
  return result
end

function SE.GetSkillEffectCommon(skill, level, actionIndex, speIndex)
  local effect
  if skill.isPassive then
    effect = SE.GetSpecialEffect(skill.specialIndex[speIndex or 1], level)
  else
    effect = SE.GetActorComboEffect(skill.skillId, level, actionIndex, speIndex)
  end
  return effect
end

-- D/local: only exact missing skill icons use the same-Role original skill artwork.
local LocalCompletionSkillTextures = {[1503001]="Texture/ActorSkillBig/actor_skill_100301",[1503002]="Texture/ActorSkillBig/actor_skill_100302",[1503015]="Texture/ActorSkillBig/actor_skill_100303",[1505001]="Texture/ActorSkillBig/actor_skill_100501",[1505003]="Texture/ActorSkillBig/actor_skill_100502",[1505015]="Texture/ActorSkillBig/actor_skill_100503",[1506003]="Texture/ActorSkillBig/actor_skill_100601",[1506004]="Texture/ActorSkillBig/actor_skill_100602",[1506015]="Texture/ActorSkillBig/actor_skill_100603",[1515001]="Texture/ActorSkillBig/actor_skill_101501",[1515002]="Texture/ActorSkillBig/actor_skill_101502",[1515015]="Texture/ActorSkillBig/actor_skill_101503",[1516001]="Texture/ActorSkillBig/actor_skill_101601",[1516002]="Texture/ActorSkillBig/actor_skill_101602",[1516015]="Texture/ActorSkillBig/actor_skill_101603",[1601001]="Texture/ActorSkillBig/actor_skill_110101",[1601002]="Texture/ActorSkillBig/actor_skill_110102",[1601015]="Texture/ActorSkillBig/actor_skill_110103",[1607001]="Texture/ActorSkillBig/actor_skill_210701",[1607002]="Texture/ActorSkillBig/actor_skill_210702",[1607003]="Texture/ActorSkillBig/actor_skill_210702",[1511001]="Texture/ActorSkillBig/actor_skill_101101",[1511002]="Texture/ActorSkillBig/actor_skill_101102"}
function SE.GetSkillTexturePath(skill, actorId)
  if WU.IsLocalContentServer() and LocalCompletionSkillTextures[skill.skillId] then
    return LocalCompletionSkillTextures[skill.skillId]
  end
  if skill.isPassive then
    local actorConfig = PB.get("ActorConfig", actorId)
    local index = fif(actorConfig.kind == PB.enum.ActorType.Sub, skill.resIndex + 2, skill.resIndex)
    return "Texture/ActorSkillBig/actor_skill_" .. index
  else
    return "Texture/ActorSkillBig/actor_skill_" .. actorId .. string.format("%02d", skill.resIndex)
  end
end

function SE.IsSuperSkill(skill)
  return not skill.isPassive and skill.displayIndex == 3
end

function SE.GetActivityBuffDesc(goodsId, grade, upgrade)
  local localeStr = fif(upgrade, "ActivityBuffGoodUpgradeDesc_", "ActivityBuffGoodDesc_") .. goodsId
  local xlsxConfig = PB.get("ActivityBuffGoods", goodsId, grade)
  local upgradeXlsxConfig = PB.get("ActivityBuffGoods", goodsId, grade + 1)
  if xlsxConfig.buffType == PB.enum.ActivityBuffType.Fight then
    local tpl = WU.GetString(localeStr)
    local tplParam = GetActivityFightBuffTplParam(xlsxConfig.buffId, false)
    if upgrade then
      local upgradeTplParam = GetActivityFightBuffTplParam(upgradeXlsxConfig.buffId, true)
      table.merge(tplParam, upgradeTplParam)
    end
    return TU.render(tpl, tplParam)
  else
    local profit = PB.get("ProfitDetail", xlsxConfig.buffId)
    if upgrade then
      local upgradeProfit = PB.get("ProfitDetail", upgradeXlsxConfig.buffId)
      return WU.GetString(localeStr, profit.add, upgradeProfit.add)
    else
      return WU.GetString(localeStr, profit.add)
    end
  end
end

function SE.GetTplParamByFightBuffId(fightBuffId)
  return GetActivityFightBuffTplParam(fightBuffId, false)
end

function GetActivityFightBuffTplParam(fightBuffId, upgrade)
  local tplParam = {}
  local affixIds = _ENV["!"]({})
  local team1 = PB.get("FightBuff", fightBuffId).team1
  local team2 = PB.get("FightBuff", fightBuffId).team2
  for i = 1, 6 do
    if team1[i] then
      local affixList = team1[i].affixList
      for j = 1, #affixList do
        if affixList[j] ~= 0 then
          table.insert(affixIds, affixList[j])
        end
      end
    end
    if team2[i] then
      local affixList = team2[i].affixList
      for j = 1, #affixList do
        if affixList[j] ~= 0 then
          table.insert(affixIds, affixList[j])
        end
      end
    end
  end
  affixIds = affixIds:distinct()
  local buffs = {}
  local buffsDup = {}
  for i = 1, #affixIds do
    local affixInfo = PB.get("AffixInfo", affixIds[i])
    local specialAttrInfo = PB.get("SpecialAttrInfo", affixInfo.index)
    if specialAttrInfo == nil then
      error("GamePlay", string.format("SpecialAttrInfo.xlsx\233\135\140\230\178\161\230\156\137\239\188\140index: %s, fightBuffId: %s", affixInfo.index, fightBuffId))
    end
    local specialAttrInfoIds = _ENV["!"](specialAttrInfo.childSpecialAttrInfoId):duplicate()
    table.insert(specialAttrInfoIds, specialAttrInfo.index)
    for j = 1, #specialAttrInfoIds do
      local buffIndex = PB.get("BuffIndex", specialAttrInfoIds[j])
      local buffIds = buffIndex.buffId
      local owners = buffIndex.owner
      for k = 1, #buffIds do
        local data = PB.get("BuffData", buffIds[k], owners[k])
        local dupKey = string.format("%s_%s", data.id, data.owner)
        local dup = buffsDup[dupKey]
        if dup then
          if dup.level ~= affixInfo.level then
            error("GamePlay", string.format("buffData\230\156\137\233\135\141\229\164\141\239\188\140id: %s, fightBuffId: %s", data.id, fightBuffId))
          end
        else
          local buff = {
            level = affixInfo.level,
            data = data
          }
          table.insert(buffs, buff)
          buffsDup[dupKey] = buff
        end
      end
    end
  end
  table.sort(buffs, function(a, b)
    return a.data.priority < b.data.priority
  end)
  for i = 1, #buffs do
    local dataEnv = {}
    local rate = buffs[i].level / buffs[i].data.maxLevel * buffs[i].data.upRate / 10000
    for j = 1, #buffs[i].data.paramInt do
      dataEnv["p" .. tostring(j - 1)] = buffs[i].data.paramInt[j] * rate
      dataEnv.maxRound = buffs[i].data.maxRound
    end
    tplParam[fif(upgrade, "upgradeBuff", "buff") .. tostring(i - 1)] = dataEnv
  end
  return tplParam
end

return SE
