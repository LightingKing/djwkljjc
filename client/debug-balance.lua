local db=CS.DataBindingManager.Instance
local c=db:GetData('fci/dungeon/chapter/1001')
local result=db:GetData('DungeonBalance/result')
local function probe(v)
  if v==nil then return 'nil' end
  return tostring(v)
end
return 'chapter='..probe(c)..';dnum='..probe(c and c.recordDungeons and c.recordDungeons[2012])..';dstr='..probe(c and c.recordDungeons and c.recordDungeons['2012'])..';result='..probe(result)..';win='..probe(result and result.win)..';targets='..probe(result and result.targetsDone)
