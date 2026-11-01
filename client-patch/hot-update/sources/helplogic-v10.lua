local PB = require("Common/PbHelper")
local DEFINE = require("Logic/Define")
local MYRANDOM = require("Internal/random")
local HP_INDEX = PB.enum.AttrType.Hp
local ATTR_MAX = PB.enum.AttrType.Max
local TLog = require("Common/TlogUtil")
local fight_random
local team_random = {}
local FightHelp = {}

local function IsLocalFight()
  local dataBindingManager = CS and CS.DataBindingManager and CS.DataBindingManager.Instance
  if dataBindingManager == nil then
    return false
  end
  local function getFlag(key)
    local ok, value = pcall(function()
      return dataBindingManager:GetData(key)
    end)
    return ok and value == true
  end
  return getFlag("LocalFight")
      or getFlag("BurstLink/LocalFight")
      or getFlag("LocalBurstLinkFight")
      or getFlag("TeamPve/LocalFight")
end

function FightHelp.initGlobalSeed()
  if fight_random == nil then
    fight_random = MYRANDOM:new(0)
    team_random[1] = MYRANDOM:new(0)
    team_random[2] = MYRANDOM:new(0)
  end
end

function FightHelp.setGlobalSeed(seedFight, seed1, seed2)
  fight_random:setSeed(seedFight)
  team_random[1]:setSeed(seed1)
  team_random[2]:setSeed(seed2)
end

function FightHelp.getGlobalSeed(activeData)
  activeData.fightSeed = fight_random:getSeed()
  activeData.team1Seed = team_random[1]:getSeed()
  activeData.team2Seed = team_random[2]:getSeed()
end

function FightHelp.compareValue(source, target, operator)
  if operator == PB.enum.Operation.LessThan then
    return source < target
  elseif operator == PB.enum.Operation.LessThanOrEqualTo then
    return source <= target
  elseif operator == PB.enum.Operation.GreaterThan then
    return target < source
  elseif operator == PB.enum.Operation.GreaterThanOrEqualTo then
    return target <= source
  elseif operator == PB.enum.Operation.EqualTo then
    return source == target
  elseif operator == PB.enum.Operation.NotEqualTo then
    return source ~= target
  end
  return false
end

function FightHelp.getBuffData(id, owner)
  return PB.get("BuffData", id, owner)
end

function FightHelp.getComboNum(actorID, scard)
  local count = 0
  local rows = PB.all("ActorCombo")
  for i = 1, #rows do
    if rows[i].actorId == actorID then
      if scard then
        if rows[i].sCount ~= 0 then
          count = count + 1
        end
      elseif rows[i].sCount == 0 then
        count = count + 1
      end
    end
  end
  return count
end

function FightHelp.getCardPlace(actorID, index, sIndex)
  local result = 0
  local combo_info = PB.get("ActorCombo", actorID, index, sIndex)
  if combo_info ~= nil then
    result = combo_info.placeIndex
  end
  return result
end

function FightHelp.getAutoSettingKey(linkIndex, actorID, cardIndex, scardIndex)
  local index = "empty"
  if linkIndex ~= 0 then
    index = tostring(linkIndex)
  else
    index = actorID .. "," .. cardIndex .. "," .. scardIndex
    local combo_info = PB.get("ActorCombo", actorID, cardIndex, scardIndex)
    if combo_info ~= nil and 0 < combo_info.placeIndex then
      if scardIndex == 0 then
        index = actorID .. "," .. combo_info.placeIndex .. "," .. scardIndex
      end
      if cardIndex == 0 then
        index = actorID .. "," .. cardIndex .. "," .. combo_info.placeIndex - 2
      end
    end
  end
  return index
end

function FightHelp.getActorClose(actorID, skin)
  local result = 0
  local skinInfo = PB.get("ActorSkin", skin)
  if skinInfo ~= nil then
    for i = 1, #skinInfo.actorIds do
      if skinInfo.actorIds[i] == actorID then
        if #skinInfo.paletteIndexs == #skinInfo.actorIds then
          result = skinInfo.paletteIndexs[i]
          break
        end
        warning("GamePlay", "error data " .. actorID .. "  " .. skin)
        break
      end
    end
  end
  return result
end

function FightHelp.getDefineParam(stageID)
  local dungeonInfo = PB.get("DungeonMonster", stageID)
  if dungeonInfo ~= nil then
    local index = dungeonInfo.modeIndex
    if index == 0 then
      index = 1
    end
    local define = PB.get("DungeonModeIndex", index)
    if define ~= nil then
      return define
    end
  end
  warning("GamePlay", "Not Find define " .. stageID)
  local result = {
    time = DEFINE.ROUND_TIME,
    maxSp = DEFINE.ENERGY_MAX_SP,
    maxMp = DEFINE.ENERGY_MAX_MP,
    initSp = DEFINE.ENERGY_INIT_SP,
    initMp = DEFINE.ENERGY_INIT_MP,
    changeRate = DEFINE.ENERGY_MP_TO_SP_RATE,
    addSp = DEFINE.ENERGY_ADD_SP,
    addMp = DEFINE.ENERGY_ADD_MP,
    roundSp = DEFINE.ENERGY_ROUND_SP,
    roundMp = DEFINE.ENERGY_ROUND_MP,
    infiniteSkill = DEFINE.INFINITE_SKILL
  }
  return result
end

function FightHelp.isGameOver(activeData)
  if activeData.winTeam ~= nil and activeData.winTeam ~= 0 then
    return true
  end
  return false
end

function FightHelp.isMultiPlayerMode(activeData)
  if activeData.playerCount ~= nil and activeData.playerCount > 1 then
    return true
  end
  return false
end

function FightHelp.isInfiniteCard(dungeonType, team)
  if team == 2 and (FightHelp.isDungeonPVE(dungeonType) or dungeonType == PB.enum.DungeonType.None or dungeonType == PB.enum.DungeonType.TeamPve) then
    return true
  end
  return false
end

function FightHelp.isNetMode(dungeonType)
  -- Local v10 rooms use FightLuaLogic and REST active/balance; an unmarked
  -- BurstLink/TeamPve room still follows the original FightService path.
  if dungeonType == PB.enum.DungeonType.BurstLinkPvp or dungeonType == PB.enum.DungeonType.TeamPve then
    return not IsLocalFight()
  end
  return false
end

function FightHelp.isDungeonPVE(dungeonType)
  if dungeonType == PB.enum.DungeonType.Story or dungeonType == PB.enum.DungeonType.Normal or dungeonType == PB.enum.DungeonType.Tower or dungeonType == PB.enum.DungeonType.WeekActivity or dungeonType == PB.enum.DungeonType.Training or dungeonType == PB.enum.DungeonType.Challenge or dungeonType == PB.enum.DungeonType.Gold or dungeonType == PB.enum.DungeonType.RacePVP or dungeonType == PB.enum.DungeonType.TeamPve or dungeonType == PB.enum.DungeonType.Maze or dungeonType == PB.enum.DungeonType.Triggered or dungeonType == PB.enum.DungeonType.SemiFixed or dungeonType == PB.enum.DungeonType.AssistGuild or dungeonType == PB.enum.DungeonType.ChallengeGuild then
    return true
  end
  return false
end

function FightHelp.isDungeonSelfMonster(dungeonType)
  if dungeonType == PB.enum.DungeonType.Story or dungeonType == PB.enum.DungeonType.Training or dungeonType == PB.enum.DungeonType.Challenge then
    return true
  end
  return false
end

function FightHelp.checkGameOver(fightData, checkTeamInfo, targetTeamInfo, gameround)
  if FightHelp.isGameOver(fightData) then
    return
  end
  if checkTeamInfo == nil or targetTeamInfo == nil then
    return
  end
  local winType = PB.enum.WinType.KillAll
  local param = checkTeamInfo.winParam
  if #param ~= 0 then
    winType = param[1]
  end
  local winTeam = 0
  local targetTeamAlive = targetTeamInfo.statistic.aliveCount
  if targetTeamAlive == nil or targetTeamAlive == 0 then
    winTeam = checkTeamInfo.team
  end
  if winTeam == 0 and (winType == PB.enum.WinType.KillSingle or winType == PB.enum.WinType.KillSingleInRound or winType == PB.enum.WinType.NoDeadSingle) then
    for i = 1, #targetTeamInfo.statistic.deadInfo do
      local deadInfo = targetTeamInfo.statistic.deadInfo[i]
      if deadInfo.seat == param[2] then
        winTeam = checkTeamInfo.team
        break
      end
    end
  end
  if winTeam == 0 and (winType == PB.enum.WinType.KillALLInRound or winType == PB.enum.WinType.KillSingleInRound) and gameround > param[3] then
    winTeam = targetTeamInfo.team
  end
  if winTeam == 0 and (winType == PB.enum.WinType.NoDeadAll or winType == PB.enum.WinType.NoDeadSingle or winType == PB.enum.WinType.NoDeadAnyInRound) and 0 < #checkTeamInfo.statistic.deadInfo then
    winTeam = targetTeamInfo.team
  end
  if winTeam == 0 and (winType == PB.enum.WinType.NoDeadAllInRound or winType == PB.enum.WinType.NoDeadSingleInRound or winType == PB.enum.WinType.NoDeadAnyInRound) and gameround > param[3] then
    winTeam = checkTeamInfo.team
  end
  if winTeam == 0 and winType == PB.enum.WinType.NoDeadSingleInRound then
    for i = 1, #checkTeamInfo.statistic.deadInfo do
      local deadInfo = checkTeamInfo.statistic.deadInfo[i]
      if deadInfo.seat == param[2] then
        winTeam = targetTeamInfo.team
        break
      end
    end
  end
  fightData.winTeam = winTeam
end

function FightHelp.getActorSCardCount(actor)
  local result = 0
  local count = FightHelp.getComboNum(actor.actorInfo.id, true)
  for i = 1, count do
    if FightHelp.isActorHaveSCard(actor, i) then
      result = result + 1
    end
  end
  return result
end

function FightHelp.isActorHaveSCard(actor, sIndex)
  local skillInfo = PB.get("ActorCombo", actor.actorInfo.id, 0, sIndex)
  if skillInfo == nil then
    return false
  end
  if #skillInfo.skillList == 0 then
    return false
  end
  for k = 1, #skillInfo.skillList do
    local find_id = skillInfo.skillList[k]
    local findOne = false
    for j = 1, #actor.actorInfo.skills do
      if actor.actorInfo.skills[j].id == find_id then
        findOne = true
        break
      end
    end
    if findOne == false then
      return false
    end
  end
  return true
end

function FightHelp.getTeamInfo(teamList, team)
  for i = 1, #teamList do
    if teamList[i].team == team then
      return teamList[i]
    end
  end
  return nil
end

function FightHelp.getMainActor(teamList, owner)
  if not owner.isAssist then
    return owner
  end
  local main_seat = owner.baseSeat
  if 6 < main_seat then
    main_seat = main_seat - 6
  else
    main_seat = main_seat - 3
  end
  local actorTable = FightHelp.getAllActors(teamList, true)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    if actor.team == owner.team and actor.baseSeat == main_seat then
      return actor
    end
  end
  warning("GamePlay", "Not Find Actor " .. owner.uniqueID)
  return owner
end

function FightHelp.getTeamActorsByKind(teamList, team, kind, isAll)
  local result = {}
  if kind == 1 or kind == 4 then
    result = FightHelp.getTeamActorsByAssist(teamList, team, false)
  elseif kind == 2 then
    result = FightHelp.getTeamActorsByAssist(teamList, team, true)
  else
    result = FightHelp.getTeamActors(teamList, team)
  end
  if 0 < #result and isAll == false then
    local val = FightHelp.getRandom(1, #result)
    result = {
      result[val]
    }
  end
  return result
end

function FightHelp.getTeamActorsByAssist(teamList, team, isAssist)
  local result = {}
  local actorTable = FightHelp.getAllActors(teamList, true)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    if actor.team == team and actor.isAssist == isAssist then
      table.insert(result, actor)
    end
  end
  return result
end

function FightHelp.getTeamActorsBySeat(teamList, team, seat)
  local actorTable = FightHelp.getAllActors(teamList, true)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    local dynamic_seat = actor.seat
    if actor.team == team and dynamic_seat == seat then
      return actor
    end
  end
  return nil
end

function FightHelp.getActorByUid(teamInfo, uid)
  for j = 1, #teamInfo.playerList do
    local actorList = teamInfo.playerList[j].actorList
    for k = 1, #actorList do
      if actorList[k].uniqueID == uid then
        return actorList[k]
      end
    end
  end
  warning("GamePlay", "Not Find Actor " .. uid)
  return nil
end

function FightHelp.getTeamLinkActorTable(teamInfo, linkID, uid)
  local link_info = teamInfo.linkInfoList
  for i = 1, #link_info do
    if link_info[i].linkID == linkID then
      local result = link_info[i].uidList
      for j = 1, #result do
        if result[j] == uid then
          return result
        end
      end
    end
  end
  return {}
end

function FightHelp.getTeamActors(teamList, team)
  local result = {}
  local actorTable = FightHelp.getAllActors(teamList, true)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    if actor.team == team then
      table.insert(result, actor)
    end
  end
  return result
end

function FightHelp.refreshSequence(activeData)
  local delList = {}
  for i = 1, #activeData.teamList do
    local teamInfo = activeData.teamList[i]
    for j = 1, #teamInfo.playerList do
      local actorList = teamInfo.playerList[j].actorList
      for k = 1, #actorList do
        local actorID = actorList[k].actorInfo.id
        if actorID ~= nil and actorID ~= 0 and not actorList[k].isAssist and 0 >= actorList[k].dynamicAttrs[HP_INDEX].value then
          table.insert(delList, actorList[k].uniqueID)
        end
      end
    end
  end
  local sequence = activeData.sequenceList
  for i = 1, #delList do
    for k = 1, #sequence do
      if sequence[k].uid == delList[i] then
        sequence[k].dead = true
        break
      end
    end
  end
end

function FightHelp.getActivePlayerID(activeData)
  for i = 1, #activeData.teamList do
    local teamInfo = activeData.teamList[i]
    for j = 1, #teamInfo.playerList do
      local playerInfo = teamInfo.playerList[j]
      local actorList = playerInfo.actorList
      local curCount = #activeData.activeActorList
      for m = 0, #activeData.activeActorList do
        for k = 1, #actorList do
          if activeData.activeActorList[m] == actorList[k].uniqueID then
            curCount = curCount - 1
            break
          end
        end
      end
      if curCount == 0 then
        return playerInfo.playerId
      end
    end
  end
  return ""
end

function FightHelp.getNextActiveActor(activeData, cur_actor)
  local roundchange = false
  local find_actor = 0
  local sequence = {}
  local base_list = activeData.sequenceList
  for i = 1, #base_list do
    if not base_list[i].dead then
      table.insert(sequence, base_list[i].uid)
    end
  end
  for i = 1, #sequence do
    if cur_actor == sequence[i] then
      if i == #sequence then
        roundchange = true
        find_actor = sequence[1]
        break
      end
      find_actor = sequence[i + 1]
      break
    end
  end
  if cur_actor == 0 and find_actor == 0 and 0 < #sequence then
    find_actor = sequence[1]
  end
  return {find_actor, roundchange}
end

function FightHelp.getActiveList(curActor, activeData)
  local result = {}
  for i = 1, #activeData.teamList do
    local teamInfo = activeData.teamList[i]
    local link_info = teamInfo.linkInfoList
    for j = 1, #link_info do
      for k = 1, #link_info[j].uidList do
        if link_info[j].uidList[k] == curActor then
          return {
            link_info[j].uidList,
            teamInfo.team
          }
        end
      end
    end
  end
  return {result, 0}
end

function FightHelp.isSelfEnergy(dungeonType)
  if dungeonType == PB.enum.DungeonType.TeamPve then
    return true
  end
  return false
end

function FightHelp.getCurActiveSeat(teamInfo, dungeonType, actorUid)
  local activeSeat = -1
  if FightHelp.isSelfEnergy(dungeonType) then
    local activeActor = FightHelp.getActorByUid(teamInfo, actorUid)
    if activeActor ~= nil then
      activeSeat = activeActor.baseSeat
    end
  end
  return activeSeat
end

function FightHelp.getCurEnergy(teamInfo, dungeonType, actorUid)
  local activeSeat = FightHelp.getCurActiveSeat(teamInfo, dungeonType, actorUid)
  local energyMp = FightHelp.getTeamEnergy(1, teamInfo, activeSeat)
  local energySp = FightHelp.getTeamEnergy(2, teamInfo, activeSeat)
  return {energyMp, energySp}
end

function FightHelp.getTeamEnergy(kind, teamInfo, seat)
  local result = 0
  local link_info = teamInfo.linkInfoList
  for i = 1, #link_info do
    if seat == -1 or link_info[i].ownerSeat == seat then
      if kind == 1 then
        result = link_info[i].mpVal
      elseif kind == 2 then
        result = link_info[i].spVal
      end
    end
  end
  return result
end

function FightHelp.addTeamEnergy(kind, teamInfo, val, maxVal, seat, source)
  TLog.CollectEnergy({
    action = "change",
    kind = kind,
    teamInfo = teamInfo,
    val = val,
    maxVal = maxVal,
    source = source
  })
  local result = 0
  local link_info = teamInfo.linkInfoList
  for i = 1, #link_info do
    if seat == -1 or link_info[i].ownerSeat == seat then
      if kind == 1 then
        link_info[i].mpVal = math.max(0, math.min(link_info[i].mpVal + val, maxVal))
        result = link_info[i].mpVal
      elseif kind == 2 then
        link_info[i].spVal = math.max(0, math.min(link_info[i].spVal + val, maxVal))
        result = link_info[i].spVal
      end
    end
  end
  return result
end

function FightHelp.setTeamEnergy(kind, teamInfo, val, seat)
  local result = 0
  local link_info = teamInfo.linkInfoList
  for i = 1, #link_info do
    if seat == -1 or link_info[i].ownerSeat == seat then
      if kind == 1 then
        link_info[i].mpVal = math.max(0, val)
        result = link_info[i].mpVal
      elseif kind == 2 then
        link_info[i].spVal = math.max(0, val)
        result = link_info[i].spVal
      end
    end
  end
  return result
end

function FightHelp.printActorsUniqueId(actors)
  if actors ~= nil then
    local text = ""
    for i = 1, #actors do
      text = text .. actors[i].uniqueID
      if i ~= #actors then
        text = text .. ", "
      end
    end
    print("GamePlay", text)
  else
    error("GamePlay", "param actors is nil")
  end
end

function FightHelp.getAllActors(teamList, alive)
  local result = {}
  for i = 1, #teamList do
    local teamInfo = teamList[i]
    for j = 1, #teamInfo.playerList do
      local actorList = teamInfo.playerList[j].actorList
      for k = 1, #actorList do
        local actorID = actorList[k].actorInfo.id
        if actorID ~= nil and actorID ~= 0 then
          if alive then
            if 0 < actorList[k].dynamicAttrs[HP_INDEX].value then
              table.insert(result, actorList[k])
            end
          else
            table.insert(result, actorList[k])
          end
        end
      end
    end
  end
  return result
end

function FightHelp.refreshDeadAndLockCard(teamInfo)
  for j = 1, #teamInfo.playerList do
    local actorList = teamInfo.playerList[j].actorList
    for k = 1, #actorList do
      local actorID = actorList[k].actorInfo.id
      if actorID ~= nil and actorID ~= 0 then
        local dead = false
        if 0 >= actorList[k].dynamicAttrs[HP_INDEX].value then
          dead = true
        end
        local id = actorList[k].uniqueID
        local lock = actorList[k].lock
        FightHelp.setDeadAndLockCardImpl(teamInfo.allCardList, id, dead, lock)
        FightHelp.setDeadAndLockCardImpl(teamInfo.cardsInHand, id, dead, lock)
        FightHelp.setDeadAndLockCardImpl(teamInfo.cardsInStack, id, dead, lock)
      end
    end
  end
end

function FightHelp.setDeadAndLockCardImpl(cardList, id, dead, lock)
  if cardList ~= nil then
    for c = 1, #cardList do
      local card = cardList[c]
      if card.actorRuntimeID == id then
        card.dead = dead
        card.lock = lock
      end
    end
  end
end

function FightHelp.removeDeadCard(teamInfo)
  local delList = {}
  for j = 1, #teamInfo.cardsInHand do
    local card = teamInfo.cardsInHand[j]
    if card.dead then
      table.insert(delList, card)
    end
  end
  for i = 1, #delList do
    local card = delList[i]
    for k = 1, #teamInfo.cardsInHand do
      if teamInfo.cardsInHand[k] == card then
        table.remove(teamInfo.cardsInHand, k)
        break
      end
    end
  end
end

function FightHelp.recordTeamHp(teamList)
  for i = 1, #teamList do
    local teamInfo = teamList[i]
    teamInfo.statistic.recordHpList = {}
    teamInfo.energyMp = FightHelp.getTeamEnergy(1, teamInfo, -1)
    teamInfo.energySp = FightHelp.getTeamEnergy(2, teamInfo, -1)
    for j = 1, #teamInfo.playerList do
      local actorList = teamInfo.playerList[j].actorList
      for k = 1, #actorList do
        local hp = 0
        if actorList[k].dynamicAttrs ~= nil and actorList[k].dynamicAttrs[HP_INDEX] ~= nil then
          local baseTotalHp = FightHelp.getActorBaseAttr(actorList[k], HP_INDEX)
          local percent = 1
          if baseTotalHp ~= 0 then
            percent = math.min(1, actorList[k].baseHp / baseTotalHp)
          end
          local result_val = actorList[k].dynamicAttrs[HP_INDEX].value
          local pVal = result_val * percent
          if actorList[k].isAssist then
            hp = math.floor(pVal)
          else
            hp = math.ceil(pVal)
          end
          if 0 < result_val then
            hp = math.max(hp, 1)
          end
        end
        teamInfo.statistic.recordHpList[actorList[k].baseSeat] = hp
      end
    end
  end
end

function FightHelp.getRandom(min, max)
  local result = fight_random:next(min, max)
  return result
end

function FightHelp.getTeamRandom(team, min, max)
  local result = team_random[team]:next(min, max)
  return result
end

function FightHelp.isRandomChanceMet(val)
  local random = FightHelp.getRandom(0, 9999)
  if val > random then
    return true
  end
  return false
end

function FightHelp.getProportionRandomIndex(weightList)
  local result = 1
  local total = 0
  for i = 1, #weightList do
    total = total + weightList[i]
  end
  local random = FightHelp.getRandom(0, total - 1)
  local temp = 0
  for i = 1, #weightList do
    temp = temp + weightList[i]
    if random < temp then
      result = i
      break
    end
  end
  return result
end

function FightHelp.split(szFullString, szSeparator)
  local nFindStartIndex = 1
  local nSplitIndex = 1
  local nSplitArray = {}
  while true do
    local nFindLastIndex = string.find(szFullString, szSeparator, nFindStartIndex)
    if not nFindLastIndex then
      nSplitArray[nSplitIndex] = string.sub(szFullString, nFindStartIndex, string.len(szFullString))
      break
    end
    nSplitArray[nSplitIndex] = string.sub(szFullString, nFindStartIndex, nFindLastIndex - 1)
    nFindStartIndex = nFindLastIndex + string.len(szSeparator)
    nSplitIndex = nSplitIndex + 1
  end
  return nSplitArray
end

function FightHelp.getIntPart(x)
  if x <= 0 then
    return math.ceil(x)
  end
  if math.ceil(x) == x then
    x = math.ceil(x)
  else
    x = math.ceil(x) - 1
  end
  return x
end

function FightHelp.clearTeamRunTimeInfo(teamList)
  for i = 1, #teamList do
    local teamInfo = teamList[i]
    teamInfo.rtAssistBuffList = {}
    local list = teamInfo.playerList
    for j = 1, #list do
      for k = 1, #list[j].actorList do
        local actor = list[j].actorList[k]
        actor.rtAttBuffOnce = false
        actor.rtAttBuffAfterOnce = false
        actor.rtHurtBuffOnce = false
        actor.rtHurtBuffAfterOnce = false
        actor.rtAttBuffList = {}
        actor.rtAttBuffAfterList = {}
        actor.rtDeadOnce = false
      end
    end
  end
end

function FightHelp.fixGameSeat(teamList)
  local actorTable = FightHelp.getAllActors(teamList, true)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    if actor.isAssist ~= true then
      local fixSeat = FightHelp.getAliveCount(teamList, actor.team, actor.seat) + 1
      actor.seat = fixSeat
    end
  end
end

function FightHelp.getAliveCount(teamList, team, seat)
  local aliveCount = 0
  local actorTable = FightHelp.getAllActors(teamList, true)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    if actor.team == team and actor.isAssist ~= true and seat > actor.seat then
      aliveCount = aliveCount + 1
    end
  end
  return aliveCount
end

function FightHelp.blendActorBuff(table1, table2)
  for k, v in pairs(table2) do
    if 0 < #v then
      table1[k] = v
    end
  end
end

function FightHelp.copyActorBuff(actor)
  local result = {}
  for i = 1, #actor.buffList do
    local buff = actor.buffList[i]
    local showbuff = {
      buffKey = buff.buffKey,
      round = buff.round,
      stack = buff.stack,
      state = buff.state,
      val = buff.val,
      exRound = buff.exRound,
      mergeState = buff.mergeState,
      mergeList = buff.mergeList,
      isNewHit = buff.isNewHit,
      removeType = buff.removeType,
      ownerUid = buff.ownerUid,
      sourceId = buff.sourceId,
      indexId = buff.indexId,
      contentVal = buff.contentVal,
      rate = buff.rate
    }
    table.insert(result, showbuff)
  end
  return result
end

function FightHelp.clearAllNewHitBuff(teamList)
  local actorTable = FightHelp.getAllActors(teamList, false)
  for k = 1, #actorTable do
    FightHelp.clearBuffNewHit(actorTable[k])
  end
end

function FightHelp.clearBuffNewHit(actor)
  for i = 1, #actor.buffList do
    local buff = actor.buffList[i]
    buff.isNewHit = false
  end
end

function FightHelp.refreshActionFeedBackBuff(teamList, buffType, hitTable)
  FightHelp.clearAllActorHpChangeBuff(teamList, buffType)
  for k, v in pairs(hitTable) do
    if not k.isAssist and 0 < #v then
      FightHelp.refreshActorHpChange(teamList, k, buffType)
    end
  end
end

function FightHelp.refreshHpChangeBuff(teamList, team, uniqueID, buffType)
  local actorTable = FightHelp.clearAllActorHpChangeBuff(teamList, buffType)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    if actor.team == team and not actor.isAssist and actor.uniqueID == uniqueID then
      FightHelp.refreshActorHpChange(teamList, actor, buffType)
    end
  end
end

function FightHelp.clearAllActorHpChangeBuff(teamList, buffType)
  local actorTable = FightHelp.getAllActors(teamList, false)
  for k = 1, #actorTable do
    local actor = actorTable[k]
    local changeList = actor.hpChangeList
    for i = #changeList, 1, -1 do
      local idx, val = table.find(changeList, function(k, v)
        return v.kind == buffType
      end)
      if idx then
        table.remove(changeList, idx)
      end
    end
  end
  return actorTable
end

function FightHelp.refreshActorHpChange(teamList, actor, buffType)
  local total_change = 0
  local damage_change = 0
  local team = actor.team
  local otherTeam = 1
  if team == 1 then
    otherTeam = 2
  end
  local actorTable = FightHelp.getAllActors(teamList, false)
  local otherTeamInfo = FightHelp.getTeamInfo(teamList, otherTeam)
  for b = 1, #actor.buffList do
    local buff = actor.buffList[b]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == buffType then
      local one_change_val = buff.val
      if 0 < one_change_val then
        one_change_val = FightHelp.changeHpAddByBuff(actor, one_change_val)
      end
      if one_change_val < 0 then
        local attackId = buff.ownerUid
        if attackId ~= 0 then
          for k = 1, #actorTable do
            local attack = actorTable[k]
            if attack.team == otherTeam and attack.uniqueID == attackId then
              FightHelp.recordPlayerDamage(otherTeamInfo, attack, -one_change_val)
              break
            end
          end
        end
        damage_change = damage_change + one_change_val
      end
      total_change = total_change + one_change_val
      local change_data = {}
      change_data.kind = buffType
      change_data.val = one_change_val
      table.insert(actor.hpChangeList, change_data)
      FightHelp.changeDynamicAttr(actor, one_change_val, HP_INDEX, 1, FightHelp.getActorBaseAttr(actor, HP_INDEX))
    end
  end
  if damage_change < 0 then
    local activeTeamInfo = FightHelp.getTeamInfo(teamList, team)
    activeTeamInfo.statistic.totalDamage = activeTeamInfo.statistic.totalDamage - damage_change
  end
end

function FightHelp.changeHpAddByBuff(target, hurt)
  local result = hurt
  local percentVal = 0
  for i = 1, #target.buffList do
    local buff = target.buffList[i]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.HpAddChange then
      local val = buff.val
      percentVal = percentVal + val / 10000
    end
  end
  result = hurt * (1 + percentVal)
  if result <= 0 then
    result = 1
  end
  return math.floor(result)
end

function FightHelp.getChangeBuffVal(type1, type2, type3, target, kind)
  if target == nil then
    return 0
  end
  local val = 0
  for i = 1, #target.buffList do
    local buff = target.buffList[i]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.BuffValChange then
      local main_type = buffData.paramInt[1]
      local child_type = buffData.paramInt[2]
      local sign_type = buffData.paramInt[5]
      local sign_match = false
      if sign_type == 0 or sign_type == nil then
        sign_match = true
      elseif sign_type == type3 then
        sign_match = true
      end
      if main_type == type1 and child_type == type2 and kind == buffData.paramInt[4] and sign_match then
        val = val + buff.val
      end
    end
  end
  return val
end

function FightHelp.isSkillDamageChangeMatchSkill(kind, actorID, assist, card, scard, link)
  local match = false
  if kind == nil or kind == 0 then
    match = true
  elseif kind == 1 then
    if not assist and FightHelp.getCardPlace(actorID, card, scard) == 1 then
      match = true
    end
  elseif kind == 2 then
    if not assist and FightHelp.getCardPlace(actorID, card, scard) == 2 then
      match = true
    end
  elseif kind == 3 then
    if not assist and 1 <= scard then
      match = true
    end
  elseif kind == 4 then
    if assist then
      match = true
    end
  elseif kind == 5 then
    if 0 < link then
      match = true
    end
  else
    warning("GamePlay", "not match mp cost, kind = " .. kind)
  end
  return match
end

function FightHelp.getSpRateChangeByBuff(actor)
  local changeVal = 0
  local percentVal = 0
  for i = 1, #actor.buffList do
    local buff = actor.buffList[i]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.SpRateChange then
      local param = buffData.paramInt[1]
      local val = buff.val
      if param == 0 or param == nil then
        changeVal = changeVal + val
      else
        percentVal = percentVal + val / 10000
      end
    end
  end
  return {changeVal, percentVal}
end

function FightHelp.getMpCostByBuff(actor, card, scard, link)
  local change = 0
  for i = 1, #actor.buffList do
    local buff = actor.buffList[i]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.MpCostChange then
      local val = buff.val
      local kind = buffData.paramInt[2]
      if FightHelp.isMpCostMatchSkill(kind, actor.actorInfo.id, actor.isAssist, card, scard, link) then
        change = change + val
      end
    end
  end
  return change
end

function FightHelp.isMpCostMatchSkill(kind, actorID, assist, card, scard, link)
  local match = false
  if kind == nil or kind == 0 or kind == 1 then
    match = true
  elseif kind == 2 then
    if not assist and FightHelp.getCardPlace(actorID, card, scard) == 1 then
      match = false
    else
      match = true
    end
  elseif kind == 3 then
    if not assist and FightHelp.getCardPlace(actorID, card, scard) == 2 then
      match = true
    end
  elseif kind == 4 then
    if assist then
      match = true
    end
  elseif kind == 5 then
    if 0 < link then
      match = true
    end
  else
    warning("GamePlay", "not match mp cost, kind = " .. kind)
  end
  return match
end

function FightHelp.deleteBuff(buff, gameround)
  buff.state = PB.enum.BuffFightState.Delete
  TLog.CollectBuff({
    action = "del",
    buffInfo = buff,
    round = gameround
  })
end

function FightHelp.removeActorBuff(teamList, timePoint, team, uniqueID, gameround)
  local team_equal = false
  if team == 0 then
    team_equal = true
  end
  local actorList = FightHelp.getAllActors(teamList, false)
  for k = 1, #actorList do
    local actor_equal = false
    if team_equal then
      actor_equal = true
    elseif actorList[k].team == team and actorList[k].uniqueID == uniqueID then
      actor_equal = true
    end
    if actor_equal then
      local buffChange = false
      for b = 1, #actorList[k].buffList do
        local buff = actorList[k].buffList[b]
        local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
        if buff.state ~= PB.enum.BuffFightState.Delete then
          if buffData.endPoint == timePoint then
            local owner_equal = false
            if team_equal then
              if not actorList[k].isAssist then
                if buff.ownerUid == uniqueID then
                  owner_equal = true
                elseif buff.ownerUid - 3 == uniqueID then
                  owner_equal = true
                elseif buff.ownerUid - 6 == uniqueID then
                  owner_equal = true
                end
              end
            else
              owner_equal = true
            end
            if owner_equal and buff.round ~= nil and 0 < buff.round then
              if buff.exRound ~= nil and 0 < buff.exRound then
                buff.exRound = buff.exRound - 1
              else
                buff.round = buff.round - 1
                if 0 >= buff.round then
                  buffChange = true
                  FightHelp.deleteBuff(buff, gameround)
                end
              end
            end
          end
          if buffData.type == PB.enum.BuffFightType.Remove or buffData.type == PB.enum.BuffFightType.MpOnceChange or buffData.type == PB.enum.BuffFightType.SpOnceChange or buffData.type == PB.enum.BuffFightType.SkillReset or buffData.type == PB.enum.BuffFightType.SpToMp then
            warning("GamePlay", "Remove buff exist? " .. buff.buffKey)
            buffChange = true
            FightHelp.deleteBuff(buff, gameround)
          end
          if buffData.type == PB.enum.BuffFightType.XiShou and (buff.val == nil or 0 >= buff.val) then
            buffChange = true
            FightHelp.deleteBuff(buff, gameround)
            if buff.val ~= nil and 0 > buff.val then
              warning("GamePlay", buff.val)
            end
          end
          if buffData.type == PB.enum.BuffFightType.StackDelete and buff.stack >= buff.val then
            buffChange = true
            FightHelp.deleteBuff(buff, gameround)
          end
        end
      end
      if buffChange then
        FightHelp.updateAllBuff(actorList[k], teamList, gameround)
      end
    end
  end
end

function FightHelp.ShouldBeRemoved(removeBuffType, typeId, del_num, check_buff)
  local result = false
  local can_del = false
  if check_buff.mergeState == 0 then
    if 0 < del_num then
      can_del = true
    end
  else
    can_del = true
  end
  if can_del and check_buff.state ~= PB.enum.BuffFightState.Delete then
    local check_buffData = FightHelp.getBuffData(check_buff.buffKey.id, check_buff.buffKey.owner)
    if check_buffData.type ~= PB.enum.BuffFightType.Remove then
      if removeBuffType == 1 then
        if table.has(check_buffData.categoryList, typeId) then
          result = true
        end
      elseif removeBuffType == 2 then
        if check_buffData.id == typeId then
          result = true
        end
      elseif removeBuffType == 3 then
        if check_buffData.type == typeId then
          result = true
        end
      else
        error("GamePlay", "removeBuffType[" .. removeBuffType .. "] is invalid")
      end
    end
  end
  return result
end

function FightHelp.refreshShowBuff(result)
  for i = 1, #result do
    local buff = result[i]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete then
      if buffData.type == PB.enum.BuffFightType.Remove then
        buff.state = PB.enum.BuffFightState.Delete
        local removeBuffType = buffData.paramInt[1]
        local typeId = buffData.paramInt[2]
        local del_num = buffData.paramInt[3]
        if del_num == nil then
          del_num = 9999
        end
        for j = 1, #result do
          local check_buff = result[j]
          if del_num == 0 and check_buff.mergeState == 0 then
            break
          end
          if FightHelp.ShouldBeRemoved(removeBuffType, typeId, del_num, check_buff) then
            if check_buff.mergeState == 0 then
              del_num = del_num - 1
            end
            check_buff.state = PB.enum.BuffFightState.Delete
            check_buff.removeType = 1
          end
        end
      elseif buffData.type == PB.enum.BuffFightType.XiShou then
        if buff.val == nil or 0 >= buff.val then
          buff.state = PB.enum.BuffFightState.Delete
          if buff.val ~= nil and 0 > buff.val then
            warning("GamePlay", buff.val)
          end
        end
      elseif buffData.type == PB.enum.BuffFightType.StackDelete then
        if buff.stack >= buff.val then
          buff.state = PB.enum.BuffFightState.Delete
        end
      elseif buffData.type == PB.enum.BuffFightType.MpOnceChange or buffData.type == PB.enum.BuffFightType.SpOnceChange or buffData.type == PB.enum.BuffFightType.SkillReset or buffData.type == PB.enum.BuffFightType.SpToMp then
        buff.state = PB.enum.BuffFightState.Delete
      end
    end
  end
end

function FightHelp.updateAllBuff(actor, teamList, gameround)
  local teamInfo = FightHelp.getTeamInfo(teamList, actor.team)
  FightHelp.refreshRemoveBuff(actor, teamInfo, gameround)
  FightHelp.refreshAttrBuff(actor)
  FightHelp.refreshLockBuff(actor, teamInfo)
end

function FightHelp.refreshRemoveBuff(actor, teamInfo, gameround)
  if actor.buffList == nil then
    return
  end
  for i = 1, #actor.buffList do
    local buff = actor.buffList[i]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete then
      if buffData.type == PB.enum.BuffFightType.Remove then
        FightHelp.deleteBuff(buff, gameround)
        local removeBuffType = buffData.paramInt[1]
        local typeId = buffData.paramInt[2]
        local del_num = buffData.paramInt[3]
        if del_num == nil then
          del_num = 9999
        end
        for j = 1, #actor.buffList do
          local check_buff = actor.buffList[j]
          if del_num == 0 and check_buff.mergeState == 0 then
            break
          end
          if FightHelp.ShouldBeRemoved(removeBuffType, typeId, del_num, check_buff) then
            if check_buff.mergeState == 0 then
              del_num = del_num - 1
            end
            FightHelp.deleteBuff(check_buff, gameround)
            check_buff.removeType = 1
            if buff.ownerUid ~= nil and check_buff.mergeState == 0 then
              local team = buff.ownerUid // 100
              if actor.team == 1 then
                if team == actor.team then
                  table.insert(teamInfo.statistic.buffKindList, buff.sourceId)
                end
              elseif actor.team == 2 and team ~= actor.team then
                table.insert(teamInfo.statistic.buffKindList, buff.sourceId)
              end
            end
          end
        end
      elseif buffData.type == PB.enum.BuffFightType.MpOnceChange then
        FightHelp.deleteBuff(buff, gameround)
        local mp_num = buff.val
        local activeSeat = -1
        if FightHelp.isSelfEnergy(teamInfo.dungeonType) then
          activeSeat = actor.baseSeat
        end
        FightHelp.addTeamEnergy(1, teamInfo, mp_num, FightHelp.getDefineParam(teamInfo.stageParam).maxMp, activeSeat, 3)
      elseif buffData.type == PB.enum.BuffFightType.SpOnceChange then
        FightHelp.deleteBuff(buff, gameround)
        local sp_num = buff.val
        local activeSeat = -1
        if FightHelp.isSelfEnergy(teamInfo.dungeonType) then
          activeSeat = actor.baseSeat
        end
        FightHelp.addTeamEnergy(2, teamInfo, sp_num, FightHelp.getDefineParam(teamInfo.stageParam).maxSp, activeSeat, 3)
      elseif buffData.type == PB.enum.BuffFightType.SpToMp then
        FightHelp.deleteBuff(buff, gameround)
        local spVal = math.max(0, buffData.paramInt[1])
        local activeSeat = -1
        if FightHelp.isSelfEnergy(teamInfo.dungeonType) then
          activeSeat = actor.baseSeat
        end
        local changeSp = math.min(spVal, FightHelp.getTeamEnergy(2, teamInfo, activeSeat))
        if buff.indexId ~= 9999 then
          FightHelp.addTeamEnergy(2, teamInfo, -changeSp, FightHelp.getDefineParam(teamInfo.stageParam).maxSp, activeSeat, 3)
        else
          changeSp = spVal
        end
        local mp_num = math.ceil(changeSp * buff.val / 10000)
        FightHelp.addTeamEnergy(1, teamInfo, mp_num, FightHelp.getDefineParam(teamInfo.stageParam).maxMp, activeSeat, 3)
      elseif buffData.type == PB.enum.BuffFightType.StackDelete then
        if buff.stack >= buff.val then
          FightHelp.deleteBuff(buff, gameround)
        end
      elseif buffData.type == PB.enum.BuffFightType.SkillReset then
        FightHelp.deleteBuff(buff, gameround)
        for c = 1, #teamInfo.cardsInHand do
          local card = teamInfo.cardsInHand[c]
          local uid = card.actorRuntimeID
          if uid == actor.uniqueID or uid - 3 == actor.uniqueID then
            local cardType = buff.val
            local match = false
            if cardType == nil or cardType == 0 then
              match = true
            elseif cardType == 1 then
              if not card.sCard and FightHelp.getCardPlace(card.actorID, card.skillIndex, 0) == 1 and not card.assist then
                match = true
              end
            elseif cardType == 2 then
              if not card.sCard and FightHelp.getCardPlace(card.actorID, card.skillIndex, 0) == 2 and not card.assist then
                match = true
              end
            elseif cardType == 3 then
              if card.sCard then
                match = true
              end
            elseif cardType == 4 then
              if card.assist then
                match = true
              end
            elseif cardType == 5 and card.linkID ~= 0 then
              match = true
            end
            if match then
              local use_count = 99999
              local fireCount = 0
              if card.sCard then
                fireCount = PB.get("ActorCombo", actor.actorInfo.id, 0, card.skillIndex).fireCount
              elseif card.linkID ~= 0 then
                fireCount = PB.get("ActorLink", card.linkID).fireCount
              else
                fireCount = PB.get("ActorCombo", actor.actorInfo.id, card.skillIndex, 0).fireCount
              end
              if FightHelp.getDefineParam(teamInfo.stageParam).infiniteSkill ~= 1 and 0 < fireCount then
                use_count = fireCount
              end
              if FightHelp.isInfiniteCard(teamInfo.dungeonType, actor.team) then
                use_count = 99999
              end
              card.useCount = use_count
            end
          end
        end
      end
    end
  end
end

function FightHelp.getRoundMpChange(teamInfo, uid)
  local actor = FightHelp.getActorByUid(teamInfo, uid)
  if actor == nil then
    return 0
  end
  local result = 0
  for i = 1, #actor.buffList do
    local buff = actor.buffList[i]
    local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
    if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.ChangeRoundAddMp then
      local val = buff.val
      result = result + val
    end
  end
  return math.floor(result)
end

function FightHelp.refreshAttrBuff(actor)
  if actor.buffList == nil then
    return
  end
  for n = HP_INDEX + 1, ATTR_MAX - 1 do
    local changeVal = 0
    local percentVal = 0
    for i = 1, #actor.buffList do
      local buff = actor.buffList[i]
      local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
      if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.Attr then
        local kind = buffData.paramInt[1]
        if kind == n then
          local param = buffData.paramInt[2]
          local val = buff.val
          if param == 0 or param == nil then
            changeVal = changeVal + val
          else
            percentVal = percentVal + val
          end
        end
      end
    end
    local baseVal = FightHelp.getActorBaseAttr(actor, n)
    actor.dynamicAttrs[n].value = baseVal * (1 + percentVal / 10000) + changeVal
    if 0 > actor.dynamicAttrs[n].value and n ~= PB.enum.AttrType.CriticalResistanceRate then
      actor.dynamicAttrs[n].value = 0
    end
  end
end

function FightHelp.refreshLockBuff(actor, teamInfo)
  local islock = false
  if actor.buffList ~= nil then
    for i = 1, #actor.buffList do
      local buff = actor.buffList[i]
      local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
      if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.LockCard then
        islock = true
        break
      end
    end
  end
  local link_info = teamInfo.linkInfoList
  local result = {}
  for i = 1, #link_info do
    for j = 1, #link_info do
      for k = 1, #link_info[j].uidList do
        if link_info[j].uidList[k] == actor.uniqueID then
          result = link_info[j].uidList
          break
        end
      end
    end
  end
  for r = 1, #result do
    local one_actor = FightHelp.getActorByUid(teamInfo, result[r])
    if one_actor ~= nil then
      one_actor.lock = islock
    end
  end
  actor.lock = islock
end

function FightHelp.changeDynamicAttr(actor, val, index, min, max)
  actor.dynamicAttrs[index].value = actor.dynamicAttrs[index].value + val
  if min > actor.dynamicAttrs[index].value then
    actor.dynamicAttrs[index].value = min
  end
  if 0 < max and max < actor.dynamicAttrs[index].value then
    actor.dynamicAttrs[index].value = max
  end
  TLog.CollectHp({
    action = "change",
    actorData = actor,
    changeVal = val,
    minVal = min,
    maxVal = max
  })
end

function FightHelp.getActorBaseAttr(actor, index)
  for m = 1, #actor.actorInfo.attrs do
    local attr = actor.actorInfo.attrs[m]
    if attr.type == index then
      return attr.value
    end
  end
  return 0
end

function FightHelp.getRuntimeAttrBuff(actor)
  local result = {}
  if actor.buffList == nil then
    return result
  end
  for n = HP_INDEX + 1, ATTR_MAX - 1 do
    local changeVal = 0
    local percentVal = 0
    for i = 1, #actor.buffList do
      local buff = actor.buffList[i]
      local buffData = FightHelp.getBuffData(buff.buffKey.id, buff.buffKey.owner)
      if buff.state ~= PB.enum.BuffFightState.Delete and buffData.type == PB.enum.BuffFightType.Attr then
        local kind = buffData.paramInt[1]
        if kind == n then
          local param = buffData.paramInt[2]
          local val = buff.val
          if param == 0 or param == nil then
            changeVal = changeVal + val
          else
            percentVal = percentVal + val
          end
        end
      end
    end
    table.insert(result, {
      type = n,
      isRatio = true,
      value = percentVal
    })
    table.insert(result, {
      type = n,
      isRatio = false,
      value = changeVal
    })
  end
  return result
end

function FightHelp.recordPlayerDamage(teamInfo, attack, damage)
  for j = 1, #teamInfo.playerList do
    local playerID = teamInfo.playerList[j].playerId
    local actorList = teamInfo.playerList[j].actorList
    for k = 1, #actorList do
      if actorList[k].uniqueID == attack.uniqueID then
        local addInfo
        if teamInfo.statistic.damageList ~= nil then
          for f = 1, #teamInfo.statistic.damageList do
            if teamInfo.statistic.damageList[f].playerId == playerID then
              addInfo = teamInfo.statistic.damageList[f]
              break
            end
          end
        end
        if addInfo == nil then
          addInfo = {playerId = playerID, damage = 0}
          table.insert(teamInfo.statistic.damageList, addInfo)
        end
        addInfo.damage = addInfo.damage + damage
        return
      end
    end
  end
end

function FightHelp.clearTeamUseCardData(teamList)
  for i = 1, #teamList do
    teamList[i].useResult = 0
    teamList[i].hitKeyList = {}
    teamList[i].hitResultList = {}
    teamList[i].buffKeyList = {}
    teamList[i].buffResultList = {}
    teamList[i].useCardList = {}
    teamList[i].useActionList = {}
    teamList[i].useLinkID = 0
    teamList[i].useCard = 0
    teamList[i].useSCard = 0
    teamList[i].useActorList = {}
    teamList[i].useSupportActor = 0
  end
end

return FightHelp
