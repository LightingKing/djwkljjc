local db=CS.DataBindingManager.Instance
local scene=CS.UnityEngine.SceneManagement.SceneManager.GetActiveScene().name
local function p(v) if v==nil then return 'nil' end return tostring(v) end
local c=db:GetData('fci/dungeon/chapter/1001')
local r=db:GetData('DungeonBalance/result')
local login=CS.UIWindowManager.Instance:FindWindow('Login')
return 'scene='..p(scene)..';login='..p(login)..';chapter='..p(c)..';dnum='..p(c and c.recordDungeons and c.recordDungeons[2012])..';dstr='..p(c and c.recordDungeons and c.recordDungeons['2012'])..';result='..p(r)..';win='..p(r and r.win)..';exp='..p(r and r.resChange)
