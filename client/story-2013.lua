-- Runtime-only request for the next verified story battle.  The installed APK
-- is unchanged; this is used after 2012 has been completed.
local db=CS.DataBindingManager.Instance
local scene=CS.UnityEngine.SceneManagement.SceneManager.GetActiveScene().name
if scene~='SceneCity' then
  return 'Story2013 waiting scene='..tostring(scene)
end
local chapter=db:GetData('fci/dungeon/chapter/1001')
local record=chapter and chapter.recordDungeons and chapter.recordDungeons[2013]
if chapter==nil or record==nil or record.unlocked~=true then
  return 'Story2013 waiting chapter data'
end
local WU=require('Common/WindowUtil')()
local PB=require('Common/PbHelper')
if WU==nil or type(WU.EnterStoryDungeon)~='function' then
  return 'Story2013 waiting WindowUtil'
end
if WU.__localStoryReturnPatched ~= true then
  local returnCity=WU.ReturnCity
  if type(returnCity)~='function' then
    return 'Story2013 waiting ReturnCity'
  end
  WU.ReturnCity=function(windowParam)
    if windowParam=='ChapterNormal' and CS.DataBindingManager.Instance:GetData('WindowDungeon/ChapterId')==1001 then
      WU.__localStoryReturnPatched=false
      return returnCity('ChapterNormal, DungeonStory')
    end
    return returnCity(windowParam)
  end
  WU.__localStoryReturnPatched=true
end
db:SetData('WindowDungeon/ChapterId',1001)
WU.EnterStoryDungeon(2013,nil,nil,PB.enum.DungeonType.Story)
return 'Requested original story dungeon 2013'
