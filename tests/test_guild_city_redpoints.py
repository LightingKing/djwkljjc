"""Original City/GuildUtil bindings consume real login and redpoint payloads."""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

from google.protobuf import json_format, message_factory
from lupa.lua53 import LuaRuntime

from external_assets import external_path, require_file
import test_guild_client as harness
from guild_dungeons import _rows


class GuildCityRedpointTests(unittest.TestCase):
    def setUp(self):
        harness.GuildClientTests.setUp(self)
        self.addCleanup(self.temp.cleanup)
        self.native=harness.GuildClientTests.native.__get__(self)
        self.now[0]=1793707200
        root=external_path('LJJC_DECOMPILED_LUA','analysis/lua-decompiled')
        def source(name):
            path=require_file(root/name,'LJJC_DECOMPILED_LUA')
            return path.read_text(encoding='utf-8')
        self.guild_lua=source('common__guildutil.lua')
        self.city_lua=re.search(r'^function RegisterGuildDungeonRed\(\).*?^end$',
            source('window__city__city.lua'),re.M|re.S).group()
        self.player_lua=re.search(r'^function OnGuildPlayerChange\(guildPlayer\).*?^end$',
            source('manager__pushservice.lua'),re.M|re.S).group()
        self.cache_lua='\n'.join(line.strip() for line in source('manager__restifizermanager.lua').splitlines()
            if 'this:SetData(' in line and 'data.guildDungeon' in line)
        self.assertEqual(len(self.cache_lua.splitlines()),3)

    def join(self,gid=1):
        status,result=self.native('PUT',f'/fci/guild/{gid}/pending-members/',{'playerId':self.profile['player_id']})
        self.assertEqual(status,200,result)

    def quit(self,gid=1):
        status,result=self.native('POST',f'/fci/guild/{gid}/members/quit/',{'playerId':self.profile['player_id']})
        self.assertEqual(status,200,result)

    def login_payloads(self):
        messages=dict(self.game.login_messages(self.account))
        end=self.game._types['ScLoginEnd'].FromString(messages['ScLoginEnd'])
        self.assertEqual(end.base_info.level,self.profile['level'])
        self.assertEqual(self.native('POST','/fci/login/',None)[0],200)
        status,player=self.native('GET','/fci/guild-player/')
        self.assertEqual(status,200)
        player_cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('DBGamePlayer.GuildData'))
        player=json_format.MessageToDict(player_cls.FromString(json_format.ParseDict(player,player_cls()).SerializeToString()),
                                        always_print_fields_with_no_presence=True)
        status,redpoints=self.native('GET','/fci/redpoint/data')
        self.assertEqual(status,200)
        projected={key:redpoints[key] for key in ('guildDungeonSummary','guildDungeonScoreReward','guildDungeonProgressReward')}
        # These are optional original messages, not ad hoc bool redpoints.
        cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiRedPointData.result'))
        decoded=cls.FromString(json_format.ParseDict(projected,cls()).SerializeToString())
        return player,projected,decoded

    def client(self,player,data,lua=None):
        if lua is None:
            lua=LuaRuntime(unpack_returned_tuples=True)
            lua.execute('''
                cache={}; red={}; m_handler={GuildChange=function() end}; warnings={}
                CS={GameTime={serverUtc=0}}
                DB={GetData=function(_,key) return cache[key] end,
                    SyncRemote=function() end,
                    GameRequest=function() return {SyncInput=function(self) return self end} end}
                this={GetData=DB.GetData,SetData=function(_,key,value) cache[key]=value end,
                      Bind=function(_,key,callback) callback(cache[key]) end,
                      StopCoroutine=function() end,DelayInvokeInSeconds=function() return 1 end}
                RU={SetRedMark=function(key,value) red[key]=value or false end}
                function table.find(items,match)
                    for k,v in pairs(items or {}) do if match(k,v) then return k,v end end
                end
                function wrap(items)
                    return setmetatable(items,{__index={toarray=function(self) return self end,
                        where=function(self,fn) return table.where(self,fn) end}})
                end
                function table.where(items,fn)
                    local result={}; for k,v in pairs(items) do if fn(k,v) then result[#result+1]=v end end
                    return wrap(result)
                end
                function table.copy(items,dst) for k,v in pairs(items) do dst[k]=v end end
                _ENV['!']=function(value) return value end
                PB={enum={GuildMemberType={President=2,VicePresident=1},
                          DungeonType={AssistGuild=15,ChallengeGuild=16},
                          GuildChallengeDungeonStatus={Close=1,Open=2,Unopen=3},
                          ActivityType={GuildAssistDungeon=49,GuildChallengeDungeon=50}},
                    all=function(name) return wrap(tables[name]) end,
                    get=function(name,id)
                        for _,row in pairs(tables[name]) do if (row.dungeonId or row.id)==id then return row end end
                    end}
                ACU={IsOpenForDoing=function() return true end}
                S={Get=function() return {GetActivitiesByTypeSync=function(kind)
                    local found={}; for _,a in pairs(activities) do if a.type==kind then found[#found+1]=a end end
                    return found
                end} end}
                function require(name)
                    if name=='Common/WindowUtil' then return function() return {},DB end end
                    if name=='Common/PbHelper' then return PB end
                    if name=='Common/ActivityUtil' then return ACU end
                    if name=='Common/Singleton' then return S end
                    return {}
                end
                function warning(_,message) warnings[#warnings+1]=message end
            ''')
            lua.globals().tables=lua.table_from({name:_rows(name) for name in
                ('GuildDungeon','GuildDungeonAssistScoreReward','GuildDungeonChallengeScoreReward','GuildDungeonChallengeProgressReward')},recursive=True)
            lua.globals().activities=lua.table_from(self.native('GET','/fci/activity/')[1],recursive=True)
            lua.globals().GU=lua.execute(self.guild_lua)
            lua.execute(self.player_lua)
            lua.execute(self.city_lua)
        lua.globals().CS.GameTime.serverUtc=self.now[0]
        lua.globals().player=lua.table_from(player,recursive=True)
        lua.globals().data=lua.table_from(data,recursive=True)
        lua.execute('cache["fci/guild-player"]=player; OnGuildPlayerChange(player)')
        lua.execute(self.cache_lua)
        lua.globals().RegisterGuildDungeonRed()
        return lua

    def test_no_guild_login_preserves_native_absent_dungeon_state(self):
        player,data,wire=self.login_payloads()
        self.assertEqual(player['guildId'],0)
        lua=self.client(player,data)
        self.assertFalse(lua.globals().red['Relation/Guild/Dungeon/ScoreReward'])
        self.assertFalse(lua.globals().red['Relation/Guild/Dungeon/ProgressReward'])
        self.assertIsNone(lua.globals().cache['DungeonGuild/Summary'])
        self.assertFalse(wire.HasField('guild_dungeon_summary'))
        self.assertFalse(wire.HasField('guild_dungeon_score_reward'))
        self.assertFalse(wire.HasField('guild_dungeon_progress_reward'))

    def test_cold_process_resolves_original_redpoint_dependencies_on_first_load(self):
        script='''
from pathlib import Path
from game import _message_pool
pool=_message_pool(Path('data/descriptor_set.pb'))
result=pool.FindMessageTypeByName('ApiRedPointData.result')
expected={'guild_dungeon_summary':'ApiGuildDungeonSummary.get',
          'guild_dungeon_score_reward':'ApiGuildDungeonScoreReward.get',
          'guild_dungeon_progress_reward':'ApiGuildDungeonProgressReward.get'}
for name,target in expected.items():
    field=result.fields_by_name[name]
    assert field.has_presence and field.message_type.full_name==target
'''
        completed=subprocess.run([sys.executable,'-c',script],cwd=Path(__file__).resolve().parents[1],
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)

    def test_member_has_complete_native_summary_and_leaving_clears_cached_rewards(self):
        self.join()
        player,data,wire=self.login_payloads()
        self.assertEqual(len(data['guildDungeonSummary']['dungeonRecords']),6)
        self.assertTrue(wire.HasField('guild_dungeon_summary'))
        lua=self.client(player,data)
        self.assertTrue(lua.globals().GU.HandleGuildDungeonSummaryData(lua.globals().cache['DungeonGuild/Summary']))
        self.assertEqual(len(lua.globals().warnings),0)
        self.quit()
        player,data,wire=self.login_payloads()
        lua=self.client(player,data,lua)
        self.assertIsNone(lua.globals().cache['DungeonGuild/Summary'])
        self.assertIsNone(lua.globals().cache['fci/guild-dungeon/reward'])
        self.assertIsNone(lua.globals().cache['fci/guild-dungeon/progress-reward'])

    def test_member_cooldown_keeps_projection_complete_but_blocks_entry(self):
        self.join();self.quit();self.join();self.quit();self.join(2)
        player,data,_=self.login_payloads()
        self.assertGreater(player['guildDungeonLimitEndTime'],self.now[0])
        self.assertEqual(len(data['guildDungeonSummary']['dungeonRecords']),6)
        self.assertGreater(player['guildDungeonActorGroupId'],0)
        lua=self.client(player,data)
        self.assertFalse(lua.globals().red['Relation/Guild/Dungeon/ScoreReward'])
        self.assertFalse(lua.globals().red['Relation/Guild/Dungeon/ProgressReward'])
        self.assertEqual(self.native('POST','/fci/guild-dungeon/',{'dungeonId':6101})[0],409)
