"""Run the Welfare2 lifecycle/click callbacks used by the shipped 2.0.4 prefabs."""
from pathlib import Path
import unittest

from lupa.lua53 import LuaRuntime


PATCH = Path(__file__).resolve().parents[1] / "client-patch/hot-update"


class WelfareV11Tests(unittest.TestCase):
    def runtime(self, source):
        lua = LuaRuntime()
        lua.execute("""
          store={}; opened={}; hints={}; requests={}
          function node()
            local n={active=true,UITexture={},UILabel={},UISprite={}}
            n.gameObject={SetActive=function(_,value) n.active=value end}
            return n
          end
          REF={Main=node(),Detail=node(),FullScreen=node(),ButtonConfirm=node(),
            ConditionRewardTexture=node(),ButtonConditionReward=node(),LabelActorTips=node(),
            LabelActorDesc=node(),IconActorName=node(),IconActorTag=node(),LabelKind=node(),
            LabelAtkKind=node(),TextureActor=node()}
          _ENV['$']=function(value) return value end
          _ENV['!']=function(value) return value end
          table.linq=function(values)
            return {where=function(self,predicate)
              local result={}
              for k,v in pairs(values) do if predicate(k,v) then table.insert(result,v) end end
              return {toarray=function() return result end}
            end}
          end
          WU={BindButtonEvent=function(node,callback) node.click=callback end,
            GetString=function(key) return key end,IsShareSwitchOn=function() return false end,
            ShowHintText=function(text) table.insert(hints,text) end,
            AcquireWindowAsync=function(name,callback)
              table.insert(opened,name)
              if callback then callback(window) end
            end,
            RecycleWindow=function(name) recycled=name end}
          this={GetData=function(_,key) return store[key] end,
            SetData=function(_,key,value) store[key]=value end,
            GameRequest=function(_,path)
              table.insert(requests,path)
              return {Get=function(_,callback) callback(response) end}
            end}
          DB=this
          DBH={ResChange=function(change) applied=change end}
          AM={GetActivityByType=function(kind,callback) callback(activities) end,
            IsActivityReallyOpen=function() return activityOpen end}
          whitelist={actorId=1008,activityId=33001,targetId={1007,1018}}
          PB={enum={ActivityType={DiffActorInherit=33,Postcard=24}},
            all=function(name) assert(name=='DiffActorInherit'); return {whitelist} end,
            get=function(name,id,cell)
              if name=='ActorConfig' then return {id=id,animRes='mikoto',kind=1,category=1} end
              if name=='PostcardCell' then return {cost={type=11,id=2001,count=1}} end
            end}
          function require(name)
            if name=='Common/WindowUtil' then return function() return WU,DB,REF end end
            if name=='Common/PbHelper' then return PB end
            if name=='Manager/DataBindingHandler' then return DBH end
            if name=='Common/Singleton' then return {Get=function() return AM end} end
            return {}
          end
        """)
        lua.execute((PATCH / "sources" / source).read_text(encoding="utf-8"))
        return lua

    def test_postcard_entry_hides_uninitialized_detail_colliders_on_every_visit(self):
        lua = self.runtime("wlfpostcard-v11.lua")
        lua.execute("""
          -- Both pages are active in the original wlfpostcard prefab.
          InitContent=function()
            assert(REF.Main.active and not REF.Detail.active and not REF.FullScreen.active)
          end
          OnEnable()
          REF.Main.active=false; REF.Detail.active=true; REF.FullScreen.active=true
          OnEnable()
          StopPrePieceEffect=function() end
          RefreshDetail=function(card) assert(card.postcardId==1) end
        """)

    def test_postcard_click_uses_native_lighten_path_and_keeps_default_error_handler(self):
        lua = self.runtime("wlfpostcard-v11.lua")
        # Set only the native cached state; execute the production click handler.
        source = (PATCH / "sources/wlfpostcard-v11.lua").read_text(encoding="utf-8")
        lua.execute(source + """
          function cachedPostcards(value) m_postcards=value; m_currenPostcardIndex=1 end
        """)
        lua.execute("""
          cachedPostcards({postcardInfo={{postcardId=3,postcardCell={{cellId=7,isLighten=false}}}}})
          store['fci/item/2001']={count=1}
          local tile={['@id']=7,['@index']=1,['@state']=false}
          this.GameRequest=function(_,path)
            assert(path=='fci/Postcard/Lighten/3')
            return {Post=function(_,body,success,failure)
              assert(body.cellId==7 and failure==nil)
              success({resChange='paid-once'})
            end}
          end
          UpdatePostcardDataAndUI=function() updated=true end
          ShowPutIntoEffect=function(value) animated=value end
          OnPieceClick(tile)
          assert(tile['@state'] and updated and animated==tile and applied=='paid-once')
        """)

    def test_inheritance_tab_initializes_native_whitelist_and_refreshes_owned_actors(self):
        lua = self.runtime("wlfdecrepittransport-v11.lua")
        lua.execute("""
          activities={{activityId=33001}}; activityOpen=true
          store['fci/actor/']={{uid=1,id=1008,quality=2,level=10},
            {uid=2,id=1007,quality=4,level=80},{uid=3,id=1008,quality=3,level=1}}
          window={['$$InitData']=function(activity,actors)
            assert(activity==whitelist and #activity.targetId==2)
            assert(activity.targetId[1]==1007 and activity.targetId[2]==1018)
            selected=actors
          end}
          Awake(); OnEnable(); REF.ButtonConfirm.click()
          assert(opened[1]=='DecrepitTransportation')
          assert(#selected==2 and selected[1].uid==3 and selected[2].uid==1)
          -- A subsequent visit/click must not use an actor consumed since the last list.
          store['fci/actor/']={{uid=9,id=1008,quality=2,level=4}}
          REF.ButtonConfirm.click()
          assert(#selected==1 and selected[1].uid==9)
          store['fci/actor/']={}
          REF.ButtonConfirm.click()
          assert(#opened==2 and hints[#hints]=='Window_DecrepitTransportNoActor')
        """)

    def test_true_love_assigns_original_art_and_opens_native_reward_selector(self):
        lua = self.runtime("wlfconditionreward-v11.lua")
        lua.execute("""
          window={['$$SetBoxIdTrueLove']=function(id,callback)
            selectedBox=id; rewardCallback=callback
          end}
          store.ConditionReward={id=121001}
          Start()
          assert(REF.ConditionRewardTexture.UITexture.mainTexturePath=='Texture/Welfare/ture_love')
          REF.ButtonConditionReward.click()
          assert(opened[1]=='ManualBoxActor' and selectedBox==121001 and #requests==0)
          rewardCallback(true)
          assert(store.shouldActiveCondition==false and recycled=='Welfare' and opened[2]=='City')
        """)

    def test_true_love_cache_miss_fetches_qualification_before_opening_selector(self):
        lua = self.runtime("wlfconditionreward-v11.lua")
        lua.execute("""
          window={['$$SetBoxIdTrueLove']=function(id) selectedBox=id end}
          response={hasRewardQualification=true,isRewardGot=false,manualBoxReward={id=121001}}
          Start(); REF.ButtonConditionReward.click()
          assert(requests[1]=='fci/conditionreward' and selectedBox==121001)
          assert(store.ConditionReward.id==121001 and store.shouldActiveCondition)
          store.ConditionReward=nil; response.isRewardGot=true
          REF.ButtonConditionReward.click()
          assert(#opened==1 and store.shouldActiveCondition==false)
        """)
