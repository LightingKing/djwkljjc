-- Runtime-only first-battle request. The installed APK is unchanged.
-- This is the readiness-gated equivalent of analysis/ljjc-local/start_local_battle.lua.
local db=CS.DataBindingManager.Instance
local scene=CS.UnityEngine.SceneManagement.SceneManager.GetActiveScene().name
if scene~='SceneCity' then
  return 'FirstBattle waiting scene='..tostring(scene)
end

-- Common/DungeonUtil reads these exact local binding keys before entering a
-- story dungeon. Do not request the dungeon while the chapter payload is
-- still absent; that would race the normal post-login initialization.
local summary=db:GetData('fci/dungeon/chapter-summary')
local chapter=db:GetData('fci/dungeon/chapter/1001')
if summary==nil or chapter==nil or chapter.recordDungeons==nil then
  return 'FirstBattle waiting chapter data'
end

local WU=require('Common/WindowUtil')()
local PB=require('Common/PbHelper')
if WU==nil or type(WU.EnterStoryDungeon)~='function' then
  return 'FirstBattle waiting WindowUtil'
end

-- The stock story-balance path calls WU.ReturnCity("ChapterNormal") after
-- deleting the dungeon.  That is correct for ordinary stages, but it drops
-- this tutorial battle on the normal-chapter tab, where the local build has
-- no implemented stages and everything appears grey.  Keep the original
-- callback and redirect this one return to the story sub-window.  The wrapper
-- is installed before entering the fight and removes itself after the first
-- matching return, so later normal-stage navigation is unaffected.
if WU.__localStoryReturnPatched ~= true then
  local returnCity=WU.ReturnCity
  if type(returnCity)~='function' then
    return 'FirstBattle waiting ReturnCity'
  end
  WU.ReturnCity=function(windowParam)
    if windowParam=='ChapterNormal' and CS.DataBindingManager.Instance:GetData('WindowDungeon/ChapterId')==1001 then
      CS.DataBindingManager.Instance:SetData('WindowDungeon/ChapterId',1001)
      WU.__localStoryReturnPatched=false
      return returnCity('ChapterNormal, DungeonStory')
    end
    return returnCity(windowParam)
  end
  WU.__localStoryReturnPatched=true
end

db:SetData('WindowDungeon/ChapterId',1001)
WU.EnterStoryDungeon(2012,nil,nil,PB.enum.DungeonType.Story)
return 'Requested original story dungeon 2012'
