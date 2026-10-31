local WU, DB, REF = require("Common/WindowUtil")(this)
local PB = require("Common/PbHelper")
local LU = require("Common/ListUtil")
local AU = require("Common/ActorUtil")
local DBH = require("Manager/DataBindingHandler")
local m_buffRowList, m_curFloor, m_refMapUi, m_width, m_triggerCall

function SetupWindow()
  LU.Bind(REF.BuffWrapContent, {
    updateRow = UpdateBuffRowCell
  })
  WU.BindButtonEvent(REF.ButtonReward, ClickReward)
  WU.BindButtonEvent(REF.ButtonOK1, function()
    REF.MessageBoxOption.gameObject:SetActive(false)
    if m_triggerCall then
      m_triggerCall(true)
      m_triggerCall = nil
    end
  end)
  WU.BindButtonEvent(REF.ButtonCancel, function()
    REF.MessageBoxOption.gameObject:SetActive(false)
    if m_triggerCall then
      m_triggerCall(false)
      m_triggerCall = nil
    end
  end)
  WU.BindButtonEvent(REF.ButtonOK2, function()
    REF.MessageBoxOK.gameObject:SetActive(false)
    if m_triggerCall then
      m_triggerCall(true)
      m_triggerCall = nil
    end
  end)
end

function ClickReward()
  this:GameRequest("fci/Maze/ReceivePrize/"):Post({}, function(res)
    DBH.ResChange(res.resChange)
    WU.ShowRewards(res.resChange)
    ClearMazeCache()
    WU.RecycleWindow(this)
    WU.AcquireWindow("MazeEnter")
  end)
end

function ClearMazeCache()
  m_refMapUi["$$ClearCache"]()
  local actors = this:GetData("MazeActors")
  if actors then
    for i, v in ipairs(actors) do
      this:SetData("fci/mazeactor/" .. v.uid, nil)
    end
  end
  this:SetData("MazeActors", nil)
  this:SetData("fci/Maze/Actor/", nil)
  this:SetData("fci/Maze/FightActorGroup", nil)
end

function OnMazeTrigger(eventType, call)
  local refBox
  if eventType == PB.enum.MazeEventType.MazeEvent_Fight then
    REF.MessageBoxOption.gameObject:SetActive(true)
    refBox = _ENV["$"](REF.MessageBoxOption)
  else
    REF.MessageBoxOK.gameObject:SetActive(true)
    refBox = _ENV["$"](REF.MessageBoxOK)
  end
  local tipsLabel = refBox.HtmlLabelTips
  local tips = PB.all("MazeTips"):where(function(_, p)
    return p.type == eventType
  end):toarray()
  local index = math.random(1, #tips)
  local tipInfo = tips[index]
  tipsLabel.UIHtmlLabel.text = WU.GetString("MazeTips_" .. tipInfo.id)
  m_triggerCall = call
end

function InitWindow()
  this:Unbind("fci/MazeInfo", OnUpdateMazeInfo)
  this:Bind("fci/MazeInfo", OnUpdateMazeInfo)
  this:RegisterGameEvent("MazeMove", OnMazeMove)
  REF.MessageBoxOption.gameObject:SetActive(false)
  REF.MessageBoxOK.gameObject:SetActive(false)
  this:RegisterGameEvent("MazeTrigger", OnMazeTrigger)
  local key = "fci/Maze/Actor/"
  this:SetData(key, nil)
  this:Unbind(key)
  this:BindRemote(DB:GameRequest(key), function(res)
    OnGetActorPool(res)
  end)
  local actorGroup = this:GetData("fci/Maze/FightActorGroup/")
  if actorGroup == nil then
    this:GameRequest("fci/Maze/FightActorGroup/"):Get(function(resp)
      local actorGroup = {}
      actorGroup["1"] = resp.fightGroup[1]
      this:SetData("fci/Maze/FightActorGroup/", actorGroup)
    end)
  end
end

function GetActorsByRangeType(rangeType)
  if rangeType == PB.enum.MazeBuffRange.AllActor then
    local actors = this:GetData("MazeActors")
    if actors == nil then
      actors = {}
    end
    return actors
  else
    local actors = {}
    local temp = this:GetData("fci/Maze/FightActorGroup")
    if temp then
      temp = temp["1"]
      for i, v in ipairs(temp.members) do
        local t = {}
        t.uid = v.actorUid
        actors[#actors + 1] = t
      end
      return actors
    end
  end
end

function DoBuff(buffId, influenceRound, newBuff)
  local mazeInfo = PB.get("MazeBuff", buffId)
  if mazeInfo then
    if mazeInfo.type == PB.enum.MazeBuffType.AttrChange then
      if mazeInfo.param.type == PB.enum.AttrType.Hp and newBuff == buffId then
        local actors = GetActorsByRangeType(mazeInfo.range)
        for i2, actor in ipairs(actors) do
          local actorWrap = this:GetData("fci/mazeactor/" .. actor.uid)
          if actorWrap then
            actorWrap.hp = mazeInfo.param.value + actorWrap.hp
          end
        end
      end
    elseif mazeInfo.type == PB.enum.MazeBuffType.MpChange then
      local temp = this:GetData("fci/Maze/FightActorGroup")
      temp.energyMp = temp.energyMp + mazeInfo.param.value * influenceRound
    elseif mazeInfo.type == PB.enum.MazeBuffType.SpChange then
      local temp = this:GetData("fci/Maze/FightActorGroup")
      temp.energySp = temp.energySp + mazeInfo.param.value * influenceRound
    end
  else
    warning("Debug", "no mazebuff id:" .. buffId)
  end
end

function DoBuffList(data)
  for i, v in ipairs(data.influenceBuff) do
    DoBuff(v.buffId, v.influenceRound, data.newBuff)
  end
end

function UpdateBuffData(data)
  local buffMap = {}
  if data.curBuff then
    for i, v in ipairs(data.curBuff) do
      if v.buffId > 0 then
        local buffInfo = PB.get("MazeBuff", v.buffId)
        if buffInfo then
          v.isAll = buffInfo.range == PB.enum.MazeBuffRange.MazeBuffRange_AllActor
          buffMap[v.buffId] = v
        end
      end
    end
  end
  local mazeInfo = this:GetData("fci/MazeInfo/")
  if mazeInfo then
    mazeInfo.curBuff = data.curBuff
  end
  local isEnterNext = data.mazeMapDimension and 0 < data.mazeMapDimension
  if not isEnterNext then
    local temp = this:GetData("MazeBuff")
    if temp then
      for k, v in pairs(temp) do
        if v.buffId and v.buffId > 0 then
          local buffInfo = PB.get("MazeBuff", v.buffId)
          if buffInfo and (buffInfo.persistentType == PB.enum.MazeBuffPersistentType.MazeBuffPersistent_MapCell or buffInfo.persistentType == PB.enum.MazeBuffPersistentType.MazeBuffPersistent_MapBattle) then
            buffMap[v.buffId] = v
          end
        end
      end
    end
  end
  if data.mazeMapBuffId and 0 < data.mazeMapBuffId then
    local buff = {}
    buff.buffId = data.mazeMapBuffId
    buff.leftRound = -1
    buff.isAll = true
    buffMap[data.mazeMapBuffId] = buff
  end
  this:SetData("MazeBuff", buffMap)
end

function DoMapBuff(ways)
  if ways and 0 < ways then
    local temp = this:GetData("MazeBuff")
    if temp then
      for k, v in pairs(temp) do
        if v.buffId and 0 < v.buffId then
          local buffInfo = PB.get("MazeBuff", v.buffId)
          if buffInfo and buffInfo.persistentType == PB.enum.MazeBuffPersistentType.MazeBuffPersistent_MapCell then
            DoBuff(v.buffId, ways)
          end
        end
      end
    end
  end
end

function OnMazeMove(data)
  if data.way and #data.way <= 1 then
    WU.GetString("LocalMaze_Tips16")
  end
  DBH.ResChange(data.resChange)
  WU.ShowRewards(data.resChange)
  DoBuffList(data)
  DoMapBuff(#data.way - 1)
  local newMazeInfo = data.mazeInfo
  UpdateBuffData(newMazeInfo)
  local index
  if data.way and #data.way > 0 then
    index = data.way[#data.way]
  end
  if newMazeInfo.isMazeOver == true then
    local mazeInfo = this:GetData("fci/MazeInfo/")
    if mazeInfo then
      mazeInfo.isMazeOver = newMazeInfo.isMazeOver
    end
    SetPlayerIndex(index)
    UpdatePrize(newMazeInfo)
    DealMazeOver()
  elseif newMazeInfo.mazeMapDimension and 0 < newMazeInfo.mazeMapDimension then
    this:SetData("fci/MazeInfo/", newMazeInfo)
  else
    local cellData = this:GetData("MazeCellData" .. index)
    if cellData and cellData.isEnd then
      WU.ShowHintText(WU.GetString("LocalMaze_Tips24"))
    else
      SetPlayerIndex(index)
      UpdateRoad(data)
    end
  end
end

function DealMazeOver()
  if IsAllActorDead() then
    WU.ShowHintText(WU.GetString("LocalMaze_Tips26"))
  else
    WU.ShowHintText(WU.GetString("LocalMaze_Tips21"))
  end
end

function SetPlayerIndex(index)
  if index and 0 < index then
    this:SetData("MazePlayerIndex", index)
    local info = this:GetData("fci/MazeInfo/")
    if info then
      info.playerCellIndex = index
    end
  else
    WU.ShowHintText(WU.GetString("LocalMaze_Tips23"))
  end
end

function UpdateRoad(data)
  if data and data.way then
    for i, v in ipairs(data.way) do
      local cell = this:GetData("MazeCellData" .. v)
      if cell then
        cell.data.isComplete = true
        this:SetData("MazeCellData" .. v, cell)
      end
    end
    if #data.way > 0 then
      local function updateCell(index)
        local key = "MazeCellData" .. index
        
        local cell = this:GetData(key)
        if cell then
          this:SetData(key, cell)
        end
      end
      
      local last = data.way[#data.way]
      local left = last - 1
      local right = last + 1
      local up = last - m_width
      local down = last + m_width
      updateCell(left)
      updateCell(right)
      updateCell(up)
      updateCell(down)
    end
  end
end

function OnGetActorPool(res)
  if res == nil then
    return
  end
  local actors = {}
  for i, v in ipairs(res.selectActors) do
    actors[#actors + 1] = v.actor
    if v.hp == 0 and v.dead == false then
      local attrs = v.actor.attrs
      for k, v2 in pairs(attrs) do
        if v2.type == PB.enum.AttrType.Hp then
          v.hp = v2.value
          break
        end
      end
    end
    this:SetData("fci/mazeactor/" .. v.actor.uid, v)
  end
  this:SetData("fci/mazeactor", actors)
  this:SetData("MazeActors", actors)
  this:SetData("SortContext/Maze", actors)
  AU.OnActorClassify(actors, "ActorSelect")
end

function CreateMazeMap(data)
  if m_refMapUi then
    CS.UnityEngine.GameObject.Destroy(m_refMapUi["$gameObject"])
    m_refMapUi = nil
  end
  local maze = CS.ResourceManager.Instance:InstantiateWindowPrefab("MazeMain/MazeLayer" .. data.mazeMapDimension)
  if maze then
    local transform = maze.transform
    transform.parent = REF.LayerScrollView.transform
    transform.localPosition = CS.UnityEngine.Vector3.zero
    transform.localRotation = CS.UnityEngine.Quaternion.identity
    transform.localScale = CS.UnityEngine.Vector3.one
    local refMaze = _ENV["$"](maze)
    refMaze["$$SetData"](data)
    m_refMapUi = refMaze
    if REF.LayerScrollView then
      WU.HandleGameObjectAttached(REF.LayerScrollView.gameObject)
    end
  else
    error("Maze", "MazeMain/MazeLayer" .. data.mazeMapDimension .. " doesn't exist.")
  end
end

function UpdateFloor(data)
  if data.playerCellIndex == nil or data.playerCellIndex == 0 then
    data.playerCellIndex = 1
  end
  m_width = data.mazeMapDimension
  SetPlayerIndex(data.playerCellIndex)
  local str = WU.GetString("LocalMaze_Tips11", m_curFloor)
  REF.LabelFloor.UIHtmlLabel.text = str
  UpdatePrize(data)
  CreateMazeMap(data)
end

function IsAllActorDead()
  return AU.MazeActorAllDead()
end

function OnUpdateMazeInfo(data)
  if data == nil then
    return
  end
  if data.playerCellIndex == nil or data.playerCellIndex == 0 then
    data.playerCellIndex = 1
  end
  if data.curMazeFloor == nil then
    data.curMazeFloor = 1
  end
  UpdateBuffData(data)
  this:Unbind("MazeBuff", UpdateBuffList)
  this:Bind("MazeBuff", UpdateBuffList)
  m_curFloor = data.curMazeFloor
  UpdateFloor(data)
end

function UpdatePrize(data)
  local prize = {}
  local t = type(data.prizeId)
  if data.prizeId and t ~= "number" then
    for i, v in ipairs(data.prizeId) do
      local info = PB.get("MazePrize", v)
      for i2, v2 in ipairs(info.treasureBox) do
        local box = prize[v2.quality]
        if box == nil then
          box = {}
          box.count = v2.count
          box.dropId = v2.dropId
          box.quality = v2.quality
          prize[v2.quality] = box
        else
          box.count = v2.count + box.count
        end
      end
    end
  end
  for i = 1, 4 do
    local refBox = REF[string.format("SpriteBox%d", i)]
    refBox.gameObject:SetActive(false)
  end
  local index = 1
  for k, v in pairs(prize) do
    local refBox = REF[string.format("SpriteBox%d", index)]
    if refBox then
      refBox.gameObject:SetActive(true)
      refBox.UISprite.spriteName = "ResItem_mazebox_" .. k
      _ENV["$"](refBox).LabelNum.UILabel.text = tostring(v.count)
    end
    index = index + 1
  end
end

function UpdateBuffList(buffMap)
  if buffMap == nil then
    return
  end
  local buffList = {}
  for k, v in pairs(buffMap) do
    if v.buffId > 0 then
      if (v.persistentType == PB.enum.MazeBuffPersistentType.MazeBuffPersistent_Battle or v.persistentType == PB.enum.MazeBuffPersistentType.MazeBuffPersistent_Cell) and 0 < v.leftRound then
        buffList[#buffList + 1] = v
      elseif v.persistentType ~= PB.enum.MazeBuffPersistentType.MazeBuffPersistent_Battle and v.persistentType ~= PB.enum.MazeBuffPersistentType.MazeBuffPersistent_Cell then
        buffList[#buffList + 1] = v
      end
    end
  end
  m_buffRowList = {}
  local col = 4
  local len = math.ceil(#buffList / col)
  for i = 1, len do
    local temp = {}
    for i2 = 1, col do
      temp[i2] = buffList[(i - 1) * col + i2]
    end
    m_buffRowList[i] = temp
  end
  LU.Set(REF.BuffWrapContent, len)
end

function UpdateBuffRowCell(rowRef, wrapIndex, realIndex)
  if m_buffRowList and realIndex ~= nil and realIndex < #m_buffRowList then
    local index = realIndex + 1
    local buffRow = m_buffRowList[index]
    rowRef["$$SetData"](buffRow)
  end
end
