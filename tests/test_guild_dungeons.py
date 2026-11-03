"""Original guild dungeon lifecycle, reward contracts, and durable retries."""
import importlib.util
import json
import unittest
import re
import threading
from contextlib import closing
from http.client import HTTPConnection
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from unittest.mock import patch

from google.protobuf import json_format, message_factory
import test_guild_client as harness
from external_assets import external_path, require_file
from server import FixtureServer, JsonlLogger


class GuildDungeonTests(unittest.TestCase):
    def setUp(self):
        harness.GuildClientTests.setUp(self)
        self.native = harness.GuildClientTests.native.__get__(self)
        self.now[0] = int(datetime(2026, 11, 4, 4, tzinfo=timezone.utc).timestamp())
        self.gid = self.native('POST', '/fci/guild/', {'name':'副本测试','icon':'1'})[1]['baseInfo']['id']
        self.module = None
        if importlib.util.find_spec('guild_dungeons'):
            from guild_dungeons import LocalGuildDungeons
            self.module = LocalGuildDungeons(self.game)

    def tearDown(self):
        self.temp.cleanup()

    def dungeon_call(self, method, route, body=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        if self.module:
            return self.module.handle_http(method, route, raw, self.profile)
        return self.game.handle_http(method, route, raw, 'application/json', self.auth)

    def enter(self, dungeon=6101):
        status, result = self.dungeon_call('POST', '/fci/guild-dungeon/', {'dungeonId':dungeon,'selectActorGroupId':1})
        self.assertEqual(status, 200, result)
        return result

    def fight(self, dungeon=6101):
        entered = self.enter(dungeon)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('FightActiveData'))
        active = cls(dungeonType=15 if dungeon >= 6100 else 16, stageID=dungeon, gameRound=3, winTeam=1)
        team = active.teamList.add(); team.team=2; team.statistic.totalDamage=200000
        uid = entered['dungeonUid']
        self.assertEqual(self.dungeon_call('PATCH', f'/fci/guild-dungeon/{uid}/active/', active.SerializeToString())[0], 200)
        status, result = self.dungeon_call('POST', f'/fci/guild-dungeon/{uid}/balance/', {})
        self.assertEqual(status, 200, result)
        return uid, result

    def test_summary_robots_stage_period_and_actor_group_contract(self):
        status, summary = self.dungeon_call('GET', f'/fci/guild/{self.gid}/dungeon/summary/')
        self.assertEqual(status, 200, summary)
        enum = self.game._pool.FindEnumTypeByName('GuildChallengeDungeonStatus')
        self.assertEqual(summary['challengeStatus'], enum.values_by_name['GuildChallengeDungeonStatus_Unopen'].number)
        self.assertEqual(len(summary['dungeonRecords']), 6)
        self.assertTrue(summary['memberRank'])
        groups = self.dungeon_call('GET', '/fci/guild/GuildDungeonActorGroup/')[1]
        self.assertTrue(groups['1']['members'][0]['actorUid'])
        selected = self.dungeon_call('GET', '/fci/SelectedGroupId/guild-dungeon/')[1]
        self.assertEqual(selected['selectActorGroupId'], 1)

    def test_real_fight_record_reward_redpoint_restart_and_repeat(self):
        uid, result = self.fight()
        self.assertTrue(result['result']['win'])
        self.assertGreaterEqual(result['score'], 100000)
        self.assertGreater(result['progress'], 0)
        self.assertEqual(self.dungeon_call('POST', f'/fci/guild-dungeon/{uid}/balance/', {})[1], result)
        reward_path = '/fci//guild-dungeon/reward/'  # Original Lua contains a double slash.
        self.assertTrue(self.module.redpoint_fields(self.profile)['guildDungeonScoreReward'])
        status, reward = self.dungeon_call('POST', reward_path, {'dungeonId':0,'score':100000})
        self.assertEqual(status, 200, reward)
        self.assertTrue(reward['resChange'])
        self.assertEqual(self.dungeon_call('POST', reward_path, {'dungeonId':0,'score':100000})[0], 409)
        from guild_dungeons import LocalGuildDungeons
        self.module = LocalGuildDungeons(self.game)
        record = self.dungeon_call('GET', '/fci/guild-dungeon-record/')[1]['dungeonRecords'][0]
        self.assertEqual(record['finishTimes'], 1)
        self.assertEqual(self.dungeon_call('POST', f'/fci/guild-dungeon/{uid}/balance/', {})[1], result)

    def test_wrong_identity_forged_win_and_unearned_claim_rejected(self):
        entered = self.enter(); uid = entered['dungeonUid']
        self.assertEqual(self.dungeon_call('POST', f'/fci/guild-dungeon/{uid}/balance/', {'win':True,'score':999999})[0], 409)
        self.assertEqual(self.dungeon_call('PATCH', f'/fci/guild-dungeon/{uid}/active/', {'stageID':6102,'dungeonType':15,'winTeam':1})[0], 400)
        self.assertEqual(self.dungeon_call('POST', '/fci/guild-dungeon/reward/', {'dungeonId':0,'score':100000})[0], 409)

    def test_challenge_progress_reward_and_concurrent_claim_once(self):
        self.now[0] = int(datetime(2026, 11, 7, 4, tzinfo=timezone.utc).timestamp())
        status, summary = self.dungeon_call('GET', f'/fci/guild/{self.gid}/dungeon/summary/')
        self.assertEqual(status, 200, summary)
        enum = self.game._pool.FindEnumTypeByName('GuildChallengeDungeonStatus')
        self.assertEqual(summary['challengeStatus'], enum.values_by_name['GuildChallengeDungeonStatus_Open'].number)
        did = summary['dungeonRecords'][-1]['dungeonId']
        uid, result = self.fight(did)
        target = {'dungeonId':did,'progress':20}
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda _:self.dungeon_call('POST','/fci/guild-dungeon/progress-reward/',target), range(2)))
        self.assertEqual(sorted(r[0] for r in replies), [200,409])
        self.assertIn(20, self.dungeon_call('GET','/fci/guild-dungeon/progress-reward/')[1]['rewardGot'])

    def test_rewards_rollback_if_record_save_fails(self):
        self.fight()
        before = self.game.systems._state(self.profile)
        with patch.object(self.module, '_save_player', side_effect=RuntimeError('save failed')):
            with self.assertRaisesRegex(RuntimeError,'save failed'):
                self.dungeon_call('POST','/fci/guild-dungeon/reward/',{'dungeonId':0,'score':100000})
        self.assertEqual(self.game.systems._state(self.profile), before)

    def test_native_rental_roundtrip_uid_map_and_login_restore(self):
        path = f'/fci/guild/{self.gid}/rent-actor/'
        status, pool = self.dungeon_call('GET', path)
        self.assertEqual(status, 200)
        response = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiGuildRentActor.get'))()
        json_format.ParseDict(pool, response)
        uid = pool['rentActorInfo'][0]['actor']['uid']
        request_cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiGuildRentActor'))
        status, result = self.dungeon_call('POST', path, request_cls(actor_guild_uid=int(uid),actor_kind=1).SerializeToString())
        self.assertEqual(status, 200, result)
        self.assertIn(str(uid), result['rentedActorInfo'])
        response = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiGuildRentActor.post'))()
        json_format.ParseDict(result, response)
        from guild_dungeons import LocalGuildDungeons
        restarted = LocalGuildDungeons(self.game)
        fields = restarted.player_fields(self.profile)
        self.assertEqual(fields['rentedActors'], result['rentedActorInfo'])
        response = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('DBGamePlayer.GuildData'))()
        json_format.ParseDict(fields, response)

    def test_group_rejects_duplicate_slots_and_support_in_main_slot(self):
        group = self.dungeon_call('GET', '/fci/guild/GuildDungeonActorGroup/')[1]['1']
        member = group['members'][0]
        duplicate = {'actorGroups':{'1':{'members':[member,dict(member, actorUid=0)]}}}
        self.assertEqual(self.dungeon_call('PUT','/fci/guild/GuildDungeonActorGroup/',duplicate)[0],400)

    def test_redpoint_fields_are_native_cache_objects_and_balance_has_native_schema(self):
        fields = self.module.redpoint_fields(self.profile)
        self.assertIsInstance(fields['guildDungeonScoreReward'],dict)
        self.assertIsInstance(fields['guildDungeonProgressReward'],dict)
        self.assertEqual(fields['guildDungeonSummary'], self.dungeon_call('GET',f'/fci/guild/{self.gid}/dungeon/summary/')[1])
        _,result = self.fight()
        message = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('DbDungeon.BalanceInfo'))()
        json_format.ParseDict(result['result'],message)

    def test_original_entry_wire_and_all_guild_response_descriptors(self):
        cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiGuildDungeon'))
        request=cls(select_actor_group_id=1,dungeon_id=6101).SerializeToString()
        status,entry=self.dungeon_call('POST','/fci/guild-dungeon/',request)
        self.assertEqual(status,200,entry)
        responses=[('ApiGuildDungeon.result',entry),
                   ('ApiGuildDungeonSummary.get',self.dungeon_call('GET',f'/fci/guild/{self.gid}/dungeon/summary/')[1]),
                   ('ApiGuildDungeonRecord.get',self.dungeon_call('GET','/fci/guild-dungeon/record/6101/')[1]),
                   ('ApiGuildDungeonScoreReward.get',self.dungeon_call('GET','/fci/guild-dungeon/reward/')[1])]
        for name,data in responses:
            with self.subTest(message=name):
                message=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()
                json_format.ParseDict(data,message)

    def test_http_entry_is_consumable_by_original_guild_fight_lua(self):
        from lupa.lua53 import LuaRuntime
        path=external_path('LJJC_DECOMPILED_LUA','analysis/lua-decompiled')/'window__dungeonguildactorgroup__dungeonguildactorgroup.lua'
        require_file(path,'LJJC_DECOMPILED_LUA')
        native=re.search(r'^function EnterDungeon\(\).*?^end$',path.read_text(encoding='utf-8'),re.M|re.S).group()
        cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiGuildDungeon'))
        server=FixtureServer(('127.0.0.1',0),self.root,JsonlLogger(self.root/'guild-http.jsonl'))
        server.accounts,server.game=self.accounts,self.game
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with closing(HTTPConnection('127.0.0.1',server.server_port,timeout=5)) as connection:
                connection.request('POST','/fci/guild-dungeon/',cls(dungeon_id=6101,select_actor_group_id=1).SerializeToString(),
                    headers={'Authorization':self.auth,'Content-Type':'application/wireformat'})
                reply=connection.getresponse();raw=reply.read();self.assertEqual(reply.status,200,raw)
            envelope_cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('RestfulResponse'))
            callback=json.loads(envelope_cls.FromString(raw).raw_json)
            lua=LuaRuntime()
            lua.execute('''
              m_dungeonId=6101; m_selectedGroupId=1; m_groups={}; stored={}
              _ENV['!']=function(t) return t end
              ProtobufT=function(_,t) return t end
              this={SetData=function(_,key,value) stored[key]=value end,
                    GetData=function(_,key) return 15 end,
                    GameRequest=function(_,path) return {Post=function(_,args,cb) cb(response) end} end}
              WU={EnterGameLevel=function(uid,base,id,kind,team,cartoon) entered={uid=uid,base=base,kind=kind} end}
            ''')
            lua.globals().response=lua.table_from(callback,recursive=True)
            lua.execute(native);lua.globals().EnterDungeon()
            self.assertEqual(lua.globals().entered.uid,callback['dungeonUid'])
            self.assertEqual(lua.globals().entered.kind,15)
            self.assertEqual(len(lua.globals().entered.base.actors),6)
            actual=lua.globals().stored['FightInitInfo'][2].hp
            self.assertEqual([actual[i] for i in range(1,7)],[hp if hp else -1 for hp in callback['enemyHplist']])
        finally:
            server.shutdown();server.server_close();thread.join(2)
