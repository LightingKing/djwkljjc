"""Visible cycle rewards must survive offline catchup, retries and rollback."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import unittest
from unittest.mock import patch

import test_hidden_challenge as challenge_harness
import test_guild_dungeons as guild_harness


class ChallengeCycleRewardTests(unittest.TestCase):
    def setUp(self):
        challenge_harness.HiddenChallengeTests.setUp(self)
        self.module=self.game.hidden_challenge

    def tearDown(self):
        self.temp.cleanup()

    def win(self, uid=100):
        actors=self.game.dungeons._fight_team(self.profile,1)[1]
        self.module.begin_run(self.profile,uid,21,actors,{})
        self.module.record_win(self.profile,uid)

    def mails(self):
        return [m for m in self.game.systems._state(self.profile).get('mails',[]) if m['title']=='练度竞赛赛季排名奖励']

    def test_original_rank_medal_is_delivered_after_cycle_and_claimed_once(self):
        self.win()
        self.module.settle_due(self.profile)
        self.assertEqual(self.mails(),[])
        self.now=1793570400+604800
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:self.module.settle_due(self.profile),range(2)))
        self.assertEqual(len(self.mails()),1)
        mail=self.mails()[0]
        self.assertEqual(mail['attachments'],[{'type':8,'id':70001,'count':1,'param':0}])
        payload=json.dumps({'attachmentsGot':True})
        status,first=self.game.mailbox.handle_http('PATCH',f"/fci/mail/{mail['id']}",payload,self.profile)
        self.assertEqual(status,200)
        self.assertTrue(first['resChange'])
        self.assertEqual(self.game.mailbox.handle_http('PATCH',f"/fci/mail/{mail['id']}",payload,self.profile)[1]['resChange'],[])
        medal=self.game.systems._state(self.profile)['medals']['70001']
        self.assertEqual(medal['expireTime'],self.now+28*86400)

    def test_offline_catchup_restart_clock_rollback_and_nonparticipant(self):
        self.module.settle_due(self.profile)
        self.assertEqual(self.mails(),[])
        self.win()
        self.now+=10*604800
        self.module.settle_due(self.profile)
        first=self.mails()
        self.assertEqual(len(first),1)
        from hidden_challenge import LocalHiddenChallenge
        self.module=LocalHiddenChallenge(self.game)
        self.now-=10*604800
        self.module.settle_due(self.profile)
        self.assertEqual(self.mails(),first)
        with self.assertRaisesRegex(ValueError,'clock_rollback'):
            self.win(101)
        self.now+=10*604800
        self.module.settle_due(self.profile)
        self.assertEqual(self.mails(),first)

    def test_mail_creation_and_claim_marker_roll_back_together(self):
        self.win();self.now=1793570400+604800
        before=self.game.systems._state(self.profile)
        with patch.object(self.module,'_save',side_effect=RuntimeError('cycle marker failed')):
            with self.assertRaisesRegex(RuntimeError,'cycle marker failed'):
                self.module.settle_due(self.profile)
        self.assertEqual(self.game.systems._state(self.profile),before)
        self.module.settle_due(self.profile)
        self.assertEqual(len(self.mails()),1)

    def test_mail_claim_failure_rolls_back_medal_and_allows_safe_retry(self):
        self.win();self.now=1793570400+604800;self.module.settle_due(self.profile)
        mail=self.mails()[0];path=f"/fci/mail/{mail['id']}"
        request=json.dumps({'attachmentsGot':True})
        before=self.game.systems._state(self.profile)
        with patch.object(self.game.mailbox,'_save',side_effect=RuntimeError('claim marker failed')):
            with self.assertRaisesRegex(RuntimeError,'claim marker failed'):
                self.game.mailbox.handle_http('PATCH',path,request,self.profile)
        self.assertEqual(self.game.systems._state(self.profile),before)
        first=self.game.mailbox.handle_http('PATCH',path,request,self.profile)[1]
        self.assertTrue(first['resChange'])
        self.assertEqual(self.game.mailbox.handle_http('PATCH',path,request,self.profile)[1]['resChange'],[])

    def test_current_rank_resets_but_history_and_permanent_score_prizes_remain(self):
        self.win();self.now=1793570400+604800;self.module.settle_due(self.profile)
        status,current=self.module.handle_http('GET','/fci/challenge-mode-rank/top/50/dungeon/21',None,self.profile)
        self.assertEqual(status,200)
        self.assertFalse(any(r['playerId']==str(self.profile['player_id']) for r in current['rankInfo']))
        history=self.module.handle_http('GET','/fci/challenge-mode/season/21',None,self.profile)[1]
        self.assertTrue(any(r['playerId']==str(self.profile['player_id']) for r in history['rankList']))
        self.assertIn('21',self.module._state(self.profile)['scoreRecord'])


class GuildCycleRewardTests(unittest.TestCase):
    def setUp(self):
        guild_harness.GuildDungeonTests.setUp(self)
        self.module=self.game.guild_dungeons
        for name in ('dungeon_call','enter','fight'):
            setattr(self,name,getattr(guild_harness.GuildDungeonTests,name).__get__(self))

    def tearDown(self):
        self.temp.cleanup()

    def challenge(self):
        self.now[0]=1793570400+5*86400+3600
        self.week=self.module._week()
        self.did=self.module._boss()
        return self.fight(self.did)

    def mails(self):
        return [m for m in self.game.systems._state(self.profile).get('mails',[]) if m['title']=='公会副本周期贡献奖励']

    def score(self):
        return self.game.guild._run(self.profile,lambda conn,state,player,now,notices:state['guilds'][str(self.gid)]['baseInfo']['score'])

    def test_original_top_three_formula_shared_score_personal_mail_and_profit_once(self):
        self.challenge()
        summary=self.dungeon_call('GET',f'/fci/guild/{self.gid}/dungeon/summary/')[1]
        total=sum(row['score'] for row in summary['memberRank'][:3])
        from guild_dungeons import STAGES
        config=STAGES[self.did]
        guild_score=min(25000,max(0,(total-config['scoreDelta'])*config['exchangeScaleForGuild']//10000))
        personal=min(500,max(0,(total-config['scoreDelta'])*config['exchangeScaleForPerson']//10000))
        old_score=self.score();self.now[0]=self.week+604800-21600
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:self.module.settle_due(self.profile),range(2)))
        self.assertEqual(self.score(),old_score+guild_score)
        self.assertEqual(len(self.mails()),1)
        self.assertEqual(self.mails()[0]['attachments'],[{'type':17,'id':0,'count':personal,'param':0}])
        profits=self.module.active_profits(self.profile)
        self.assertEqual([(p['id'],p['type'],p['add']) for p in profits],[(503,3,1500)])
        self.assertEqual(profits[0]['endTime']-profits[0]['startTime'],608000)
        rules=self.module.active_profit_rules(self.profile)
        self.assertEqual((rules[0]['dungeonType'],rules[0]['isRatio']),([2],True))
        self.assertEqual(self.game.dungeons._profit_scaled(100,rules,dungeon_type=2,profit_type=3,rng=None),115)
        self.assertEqual(self.game.dungeons._profit_scaled(100,rules,dungeon_type=15,profit_type=3,rng=None),100)
        self.module.settle_due(self.profile)
        self.assertEqual(self.score(),old_score+guild_score)

    def test_nonparticipant_gets_neither_mail_nor_robot_only_guild_payout(self):
        self.now[0]=1793570400+5*86400+3600
        self.enter(self.module._boss())
        old=self.score();self.now[0]=self.module._week()+604800
        self.module.settle_due(self.profile)
        self.assertEqual(self.mails(),[])
        self.assertEqual(self.score(),old)
        self.assertEqual(self.module.active_profits(self.profile),[])

    def test_offline_catchup_does_not_extend_expired_profit_or_allow_clock_replay(self):
        self.challenge();old=self.score();self.now[0]=self.week+4*604800
        self.module.settle_due(self.profile)
        self.assertEqual(len(self.mails()),1)
        self.assertGreater(self.score(),old)
        self.assertEqual(self.module.active_profits(self.profile),[])
        settled=self.score();self.now[0]=self.week+5*86400+3600
        self.module.settle_due(self.profile)
        self.assertEqual(len(self.mails()),1)
        self.assertEqual(self.score(),settled)
        self.assertEqual(self.dungeon_call('POST','/fci/guild-dungeon/',{'dungeonId':self.did})[0],409)
        self.now[0]=self.week+4*604800
        self.module.settle_due(self.profile)
        self.assertEqual(len(self.mails()),1)

    def test_personal_mail_failure_rolls_back_shared_score_and_settlement(self):
        self.challenge();old=self.score();self.now[0]=self.week+604800
        with patch('guild_dungeons.send_mail',side_effect=RuntimeError('mail failed')):
            with self.assertRaisesRegex(RuntimeError,'mail failed'):
                self.module.settle_due(self.profile)
        self.assertEqual(self.score(),old)
        self.assertEqual(self.mails(),[])
        self.module.settle_due(self.profile)
        self.assertGreater(self.score(),old)
        self.assertEqual(len(self.mails()),1)

    def test_two_participants_get_one_mail_each_but_spectator_gets_only_shared_buff(self):
        from test_guild_client import GuildClientTests
        member,member_auth=GuildClientTests.new_player(self)
        spectator,spectator_auth=GuildClientTests.new_player(self)
        for profile,auth in ((member,member_auth),(spectator,spectator_auth)):
            args={'playerId':profile['player_id']}
            self.assertEqual(self.native('PUT',f'/fci/guild/{self.gid}/pending-members/',args,auth=auth)[0],200)
            self.assertEqual(self.native('PUT',f'/fci/guild/{self.gid}/members/',args)[0],200)
        self.challenge()
        president=self.profile
        try:
            self.profile=member
            self.fight(self.did)
        finally:
            self.profile=president
        before=self.score();self.now[0]=self.week+604800-21600
        self.module.settle_due(spectator)
        after=self.score()
        self.assertGreater(after,before)
        self.assertFalse([m for m in self.game.systems._state(spectator).get('mails',[]) if m['title']=='公会副本周期贡献奖励'])
        self.assertEqual(self.module.active_profits(spectator)[0]['id'],503)
        self.module.settle_due(president)
        self.assertEqual(len(self.mails()),1)
        self.assertFalse([m for m in self.game.systems._state(member).get('mails',[]) if m['title']=='公会副本周期贡献奖励'])
        from guild_dungeons import LocalGuildDungeons
        self.module=LocalGuildDungeons(self.game)
        self.module.settle_due(member)
        self.module.settle_due(member)
        delivered=[m for m in self.game.systems._state(member).get('mails',[]) if m['title']=='公会副本周期贡献奖励']
        self.assertEqual(len(delivered),1)
        self.assertEqual(delivered[0]['attachments'],self.mails()[0]['attachments'])
        self.assertEqual(self.score(),after)

    def test_cycle_profit_changes_real_normal_battle_actor_exp(self):
        from dungeon import STAGE_REWARD_ROWS
        from grants import grant_rewards
        from google.protobuf import message_factory
        from types import SimpleNamespace
        self.challenge();self.now[0]=self.week+604800-21600
        self.module.settle_due(self.profile)
        # Existing ratio rewards probabilistically round fractional units.
        # Fix that draw so the exact original 15% lower bound is repeatable.
        self.game.dungeons._drop_rng=SimpleNamespace(random=lambda:1.0)
        grant_rewards(self.game,self.profile,[{'type':6,'id':0,'count':100}])
        status,entry=self.game.dungeons._enter(self.profile,json.dumps({'dungeonId':21,'actorGroupId':1}))
        self.assertEqual(status,200,entry)
        uid=entry['dungeonUid']
        report=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('FightActiveData'))(
            dungeonType=2,stageID=21,gameRound=3,winTeam=1)
        self.assertEqual(self.game.dungeons._active(self.profile,uid,report.SerializeToString())[0],200)
        status,result=self.game.dungeons._balance(self.profile,uid,b'{}')
        self.assertEqual(status,200,result)
        gains=result['balanceInfo']['expChange']
        self.assertTrue(gains)
        expected=STAGE_REWARD_ROWS[21]['expActor']*11500//10000
        self.assertTrue(all(r['addExp']==expected for r in gains))
        self.assertEqual(self.game.dungeons._balance(self.profile,uid,b'{}')[1],result)

    def test_contribution_mail_claim_failure_rolls_back_currency_and_retries_once(self):
        self.challenge();self.now[0]=self.week+604800-21600
        self.module.settle_due(self.profile)
        mail=self.mails()[0];path=f"/fci/mail/{mail['id']}"
        request=json.dumps({'attachmentsGot':True})
        before=self.game._profile(self.account)['resources']
        with patch.object(self.game.mailbox,'_save',side_effect=RuntimeError('contribution marker failed')):
            with self.assertRaisesRegex(RuntimeError,'contribution marker failed'):
                self.game.mailbox.handle_http('PATCH',path,request,self.profile)
        self.assertEqual(self.game._profile(self.account)['resources'],before)
        result=self.game.mailbox.handle_http('PATCH',path,request,self.profile)[1]
        self.assertTrue(result['resChange'])
        self.assertEqual(self.game.mailbox.handle_http('PATCH',path,request,self.profile)[1]['resChange'],[])
