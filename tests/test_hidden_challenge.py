"""Native cultivation competition contracts and transaction boundaries."""
import importlib.util
import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from google.protobuf import json_format, message_factory

from accounts import LocalAccounts
from game import LocalGame
import test_systems


class HiddenChallengeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1793764800
        self.accounts = LocalAccounts(self.root / 'accounts')
        self.game = LocalGame(self.root / 'game', self.accounts, clock=lambda: self.now,
                              initial_level=60, initial_actor_ids=[1001, 1002],
                              initial_resources=[{'type': 6, 'count': 9999}])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.module = None
        if importlib.util.find_spec('hidden_challenge'):
            from hidden_challenge import LocalHiddenChallenge
            self.module = LocalHiddenChallenge(self.game)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, route, body=None):
        raw = json.dumps(body).encode() if body is not None else None
        if self.module:
            return self.module.handle_http(method, route, raw, self.profile)
        return self.game.handle_http(method, route, raw, 'application/json', self.auth) or (501, {'error':'unhandled'})

    def score_win(self, uid=123, dungeon=21):
        self.assertIsNotNone(self.module, 'competition must persist real run snapshots')
        uids = self.game.dungeons._fight_team(self.profile, 1)[1]
        self.module.begin_run(self.profile, uid, dungeon, uids, {})
        return self.module.record_win(self.profile, uid)

    def test_rank_and_season_use_native_shape_and_stable_robot_records(self):
        status, ranks = self.call('GET', '/fci/challenge-mode-rank/top/50/dungeon/21')
        self.assertEqual(status, 200)
        self.assertEqual(len(ranks['rankInfo']), 49)
        self.assertTrue(all(r['score'] < 0 and r['actorGroup'] for r in ranks['rankInfo']))
        self.assertEqual(self.call('GET', '/fci/challenge-mode/season/21')[1]['rankList'], ranks['rankInfo'])

    def test_real_win_lower_score_rewards_once_and_survives_restart(self):
        score = self.score_win()
        self.assertGreater(score, 0)
        reward = self.call('GET', '/fci/challenge-mode/reward')[1]
        self.assertEqual(reward['scoreRecord']['21'], score)
        from hidden_challenge import SCORE_REWARDS, LocalHiddenChallenge
        eligible = next(r for r in SCORE_REWARDS if r['dungeonId'] == 21 and r['score'] >= score)
        payload = {'dungeonId': 21, 'score': eligible['score']}
        self.assertTrue(self.module.redpoint_fields(self.profile)['cultivationPvpReward'])
        status, first = self.call('POST', '/fci/challenge-mode/reward', payload)
        self.assertEqual(status, 200)
        self.assertTrue(first['resChange'])
        self.assertIn(payload['score'], first['rewardRecords']['21']['rewardGot'])
        self.assertEqual(self.call('POST', '/fci/challenge-mode/reward', payload)[0], 409)
        self.module = LocalHiddenChallenge(self.game)
        self.assertEqual(self.call('GET', '/fci/challenge-mode/reward')[1]['rewardRecords'], first['rewardRecords'])
        self.assertEqual(self.module.record_win(self.profile, 123), score)

    def test_unknown_run_and_unearned_reward_cannot_mint_items(self):
        status, _ = self.call('POST', '/fci/challenge-mode/reward', {'dungeonId': 21, 'score': 999999})
        self.assertEqual(status, 409)
        self.assertIsNotNone(self.module)
        with self.assertRaises(ValueError):
            self.module.record_win(self.profile, 999)

    def test_reward_and_claim_roll_back_together(self):
        self.score_win()
        from hidden_challenge import SCORE_REWARDS
        target = max((r for r in SCORE_REWARDS if r['dungeonId'] == 21), key=lambda r:r['score'])
        before = self.game.systems._state(self.profile)
        with patch.object(self.module, '_save', side_effect=RuntimeError('save failure')):
            with self.assertRaisesRegex(RuntimeError, 'save failure'):
                self.call('POST', '/fci/challenge-mode/reward', {'dungeonId':21,'score':target['score']})
        self.assertEqual(self.game.systems._state(self.profile), before)
        self.assertEqual(self.call('GET', '/fci/challenge-mode/reward')[1]['rewardRecords'], {})

    def test_reduction_snapshot_is_owned_bounded_and_score_is_immutable(self):
        self.assertIsNotNone(self.module)
        uids = self.game.dungeons._fight_team(self.profile, 1)[1]
        with self.assertRaises(ValueError):
            self.module.begin_run(self.profile, 1, 21, uids, {'9999999': 1})
        with self.assertRaises(ValueError):
            self.module.begin_run(self.profile, 1, 21, uids, {str(uids[0]): 0})
        low = self.module.begin_run(self.profile, 1, 21, uids, {str(uids[0]): 500})
        again = self.module.begin_run(self.profile, 1, 21, uids, {})
        self.assertEqual(low, again)
        self.assertEqual(low['reduceInfo'][str(uids[0])], 500)

    def battle_call(self, method, route, payload=None):
        raw = payload if isinstance(payload,bytes) else json.dumps(payload).encode() if payload is not None else None
        return self.game.dungeons.handle_http(method,route,raw,self.profile)

    def enter_challenge(self):
        uid = self.game.dungeons._fight_team(self.profile,1)[1][0]
        args = {'dungeonId':21,'selectActorGroupId':1,'isChallengeMode':True,'reduceInfo':{str(uid):500}}
        status,result=self.battle_call('POST','/fci/dungeon/',args)
        self.assertEqual(status,200,result)
        self.assertTrue(result['baseInfo']['isChallengeMode'])
        self.assertEqual(result['baseInfo']['reduceInfo'][str(uid)],500)
        original=self.game.dungeons._fight_actor(self.profile,1001,uid)
        hp=lambda actor:next(a['value'] for a in actor['attrs'] if a['type']==1)
        self.assertEqual(hp(result['baseInfo']['actors'][0]),max(1,hp(original)//2))
        self.assertEqual(self.battle_call('POST','/fci/dungeon/',args)[1],dict(result,resChange=[]))
        return result['dungeonUid']

    def test_native_entry_reduction_win_exit_reward_and_restart(self):
        uid=self.enter_challenge()
        cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('FightActiveData'))
        active=cls(stageID=21,dungeonType=2,gameRound=3,winTeam=1)
        self.assertEqual(self.battle_call('PATCH',f'/fci/dungeon/{uid}/active/',active.SerializeToString())[0],200)
        self.assertEqual(self.battle_call('DELETE',f'/fci/dungeon/{uid}/')[0],200)
        status,result=self.battle_call('POST',f'/fci/dungeon/{uid}/balance/',{})
        self.assertEqual(status,200,result)
        self.assertTrue(result['balanceInfo']['win'])
        self.assertGreater(self.module._state(self.profile)['scoreRecord']['21'],0)
        from hidden_challenge import LocalHiddenChallenge,SCORE_REWARDS
        self.module=LocalHiddenChallenge(self.game)
        target=max((r for r in SCORE_REWARDS if r['dungeonId']==21),key=lambda r:r['score'])
        self.assertEqual(self.call('POST','/fci/challenge-mode/reward',{'dungeonId':21,'score':target['score']})[0],200)
        state=self.module._state(self.profile)
        self.assertEqual(self.battle_call('POST',f'/fci/dungeon/{uid}/balance/',{})[1],result)
        self.assertEqual(self.module._state(self.profile),state)

    def test_challenge_rejects_wrong_stage_zero_round_and_terminal_rewrite(self):
        uid=self.enter_challenge()
        path=f'/fci/dungeon/{uid}/active/'
        report={'stageID':21,'dungeonType':2,'gameRound':0,'winTeam':1}
        self.assertEqual(self.battle_call('PATCH',path,report)[0],400)
        self.assertEqual(self.battle_call('PATCH',path,dict(report,gameRound=3,stageID=22))[0],400)
        report['gameRound']=3
        self.assertEqual(self.battle_call('PATCH',path,report)[0],200)
        self.assertEqual(self.battle_call('PATCH',path,dict(report,winTeam=2))[0],409)

    def test_challenge_win_record_failure_rolls_back_entire_settlement(self):
        uid=self.enter_challenge()
        self.battle_call('PATCH',f'/fci/dungeon/{uid}/active/',{'stageID':21,'dungeonType':2,'gameRound':3,'winTeam':1})
        before=self.game.systems._state(self.profile)
        with patch.object(self.game.hidden_challenge,'record_win',side_effect=RuntimeError('record failed')):
            with self.assertRaisesRegex(RuntimeError,'record failed'):
                self.battle_call('POST',f'/fci/dungeon/{uid}/balance/',{})
        self.assertEqual(self.game.systems._state(self.profile),before)
        with closing(self.game._connect()) as conn:
            row=conn.execute('SELECT status,balance_json FROM local_dungeons WHERE dungeon_uid=?',(uid,)).fetchone()
        self.assertEqual(row['status'],'active')
        self.assertIsNone(row['balance_json'])

    def test_closed_season_and_normal_story_win_do_not_record_competition(self):
        self.assertEqual(self.battle_call('POST','/fci/dungeon/',{'dungeonId':29,'isChallengeMode':True})[0],409)
        status,result=self.battle_call('POST','/fci/dungeon/',{'dungeonId':21})
        self.assertEqual(status,200,result)
        uid=result['dungeonUid']
        self.battle_call('PATCH',f'/fci/dungeon/{uid}/active/',{'stageID':21,'dungeonType':2,'gameRound':3,'winTeam':1})
        self.assertEqual(self.battle_call('POST',f'/fci/dungeon/{uid}/balance/',{})[0],200)
        self.assertEqual(self.module._state(self.profile)['scoreRecord'],{})

    def test_original_reward_wire_and_rank_response_roundtrip(self):
        self.score_win()
        from hidden_challenge import SCORE_REWARDS
        reward=max((r for r in SCORE_REWARDS if r['dungeonId']==21),key=lambda r:r['score'])
        cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiChallengeModeReward'))
        status,result=self.module.handle_http('POST','/fci/challenge-mode/reward',
            cls(dungeon_id=21,score=reward['score']).SerializeToString(),self.profile)
        self.assertEqual(status,200,result)
        for name,payload in [('ApiChallengeModeReward.post',result),
                             ('ApiChallengeModeRank.get',self.call('GET','/fci/challenge-mode-rank/top/50/dungeon/21')[1]),
                             ('ApiChallengeModeSeason.get',self.call('GET','/fci/challenge-mode/season/21')[1])]:
            with self.subTest(message=name):
                message=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()
                json_format.ParseDict(payload,message)

    def test_later_season_590x_wire_entry_and_old_run_settlement(self):
        uid=self.enter_challenge()
        self.now=1793570400+2*604800+3600  # 45003 includes original stage 5911.
        cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiDungeon'))
        request=cls(dungeon_id=5911,select_actor_group_id=1,is_challenge_mode=True)
        status,result=self.battle_call('POST','/fci/dungeon/',request.SerializeToString())
        self.assertEqual(status,200,result)
        self.assertEqual(result['baseInfo']['dungeonId'],5911)
        self.assertEqual(result['baseInfo']['dungeonType'],2)
        self.assertTrue(result['baseInfo']['actors'][0]['skills'])
        self.assertEqual(self.battle_call('PATCH',f'/fci/dungeon/{uid}/active/',
            {'stageID':21,'dungeonType':2,'gameRound':3,'winTeam':1})[0],200)
        self.assertEqual(self.battle_call('POST',f'/fci/dungeon/{uid}/balance/',{})[0],200)
        self.assertIn('21',self.module._state(self.profile)['scoreRecord'])
        from hidden_challenge import SCORE_REWARDS
        target=max((r for r in SCORE_REWARDS if r['dungeonId']==21),key=lambda r:r['score'])
        args={'dungeonId':21,'score':target['score']}
        self.assertEqual(self.call('POST','/fci/challenge-mode/reward',args)[0],200)
        self.now+=10*604800
        self.assertEqual(self.call('POST','/fci/challenge-mode/reward',args)[0],409)


if __name__ == '__main__':
    unittest.main()
