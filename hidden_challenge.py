"""Activity 45: persistent cultivation competition using native JSON contracts.

The retired ranking service is unavailable. The explicit local score is the
sum of (20*level + 100*star + 10*quality)*reduction/1000 for deployed actors.
Lower is better, as required by Window_City's score <= reward.score check.
Only server-recorded, successfully settled runs can improve a record.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from google.protobuf import json_format, message_factory
from google.protobuf.message import DecodeError

import actors
from grants import grant_rewards
from local_mailbox import send_mail

ROOT = Path(__file__).resolve().parent / 'data/client-tables'


def rows(name):
    return json.loads((ROOT / (name + '.json')).read_text(encoding='utf-8'))['rows']


ACTIVITIES = {r['activityId']: r for r in rows('ChallengeModeDungeon')}
DUNGEONS = frozenset(d for row in ACTIVITIES.values() for d in row['dungeonId'])
SCORE_REWARDS = rows('ChallengeModeScoreReward')
REWARDS = {(r['dungeonId'], r['score']): r for r in SCORE_REWARDS}
RANK_REWARDS = {(r['type'],r['rank']):r['reward'] for r in rows('ChallengeModeRankReward')}
SEASON_ANCHOR = 1793570400


def season_start(now):
    return SEASON_ANCHOR + ((int(now)-SEASON_ANCHOR)//604800)*604800


def season_activity_id(now):
    # D/local: first distinct season starts 2026-11-02 06:00 Asia/Shanghai.
    # 45011..45020 repeat the first ten dungeon sets in the original table.
    return 45001 + ((int(now) - SEASON_ANCHOR) // 604800) % 10


class LocalHiddenChallenge:
    def __init__(self, game):
        self.game = game
        with game._lock, closing(game._connect()) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS local_hidden_challenge '
                         '(account_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)')

    def _state(self, profile):
        with closing(self.game._connect()) as conn:
            row = conn.execute('SELECT state_json FROM local_hidden_challenge WHERE account_id=?',
                               (profile['account_id'],)).fetchone()
        return json.loads(row[0]) if row else {'scoreRecord': {}, 'rewardRecords': {}, 'runs': {}, 'teams': {}}

    def _save(self, profile, state):
        with closing(self.game._connect()) as conn, conn:
            conn.execute('INSERT INTO local_hidden_challenge VALUES (?,?) ON CONFLICT(account_id) '
                         'DO UPDATE SET state_json=excluded.state_json',
                         (profile['account_id'], json.dumps(state, separators=(',',':'))))

    def begin_run(self, profile, uid, dungeon_id, actor_uids, reduce_info):
        if int(dungeon_id) not in DUNGEONS:
            raise ValueError('invalid_challenge_dungeon')
        with self.game.atomic_mutation():
            state = self._state(profile)
            previous = state['runs'].get(str(uid))
            if previous:
                if previous['dungeonId'] != int(dungeon_id):
                    raise ValueError('challenge_run_identity_mismatch')
                return previous
            week=season_start(self.game._now())
            if week<int(state.get('maxObservedWeek',week)):
                raise ValueError('clock_rollback')
            state['maxObservedWeek']=week
            owned = self.game.systems._state(profile)
            uids = [int(u) for u in actor_uids if int(u)]
            if not uids or len(set(uids)) != len(uids):
                raise ValueError('invalid_actor_group')
            if not isinstance(reduce_info, dict):
                raise ValueError('invalid_reduction')
            if any(str(k) not in {str(u) for u in uids} for k in reduce_info):
                raise ValueError('invalid_reduction_actor')
            score, team, ratios = 0, [], {}
            for uid_actor in uids:
                actor = actors.get(profile, owned, uid_actor)
                if actor is None:
                    raise ValueError('actor_not_owned')
                ratio = reduce_info.get(str(uid_actor), reduce_info.get(uid_actor, 1000))
                if isinstance(ratio, bool) or not isinstance(ratio, int) or not 1 <= ratio <= 1000:
                    raise ValueError('invalid_reduction')
                ratios[str(uid_actor)] = ratio
                base = 20*int(actor.get('level',1)) + 100*int(actor.get('star',1)) + 10*int(actor.get('quality',1))
                score += max(1, (base*ratio + 999)//1000)
                team.append(self.game.systems._st_actor_json(actor, profile, owned, 1))
            run = {'dungeonId':int(dungeon_id), 'score':score, 'actorGroup':team, 'reduceInfo':ratios,
                   'won':False,'seasonStart':week,'activityId':season_activity_id(week)}
            state['runs'][str(uid)] = run
            self._save(profile, state)
            return run

    def run_snapshot(self, profile, uid):
        return self._state(profile)['runs'].get(str(uid))

    def enter(self, profile, request):
        """Freeze the original dungeon entry and score in its transaction."""
        try:
            with self.game.atomic_mutation():
                dungeon=int(request.get('dungeonId',0))
                _,entries=self.game.events._activity_list('GET')
                activity=next((r for r in entries if r['type']==45 and
                               r.get('timestampStart',0)<=self.game._now() and
                               (not r.get('timestampEnd') or self.game._now()<r['timestampEnd'])),None)
                season=ACTIVITIES.get(int(activity['activityId'])) if activity is not None else None
                if not season or dungeon not in season['dungeonId']:
                    return 409,{'error':'activity_not_open'}
                with closing(self.game._connect()) as conn:
                    active=conn.execute("SELECT dungeon_uid FROM local_dungeons WHERE account_id=? "
                                        "AND mode='cultivation' AND dungeon_id=? AND status='active' ORDER BY dungeon_uid DESC LIMIT 1",
                                        (profile['account_id'],dungeon)).fetchone()
                if active:
                    old=self.run_snapshot(profile,active[0])
                    if old and old.get('entry'):
                        return 200,dict(old['entry'],resChange=[])
                args=dict(request,multiPower=1,difficulty='normal')
                status,entry=self.game.dungeons._enter_impl(profile,json.dumps(args),cultivation=True)
                if status!=200:
                    return status,entry
                uid=entry['dungeonUid']
                self.begin_run(profile,uid,dungeon,entry['baseInfo']['actorUid'],request.get('reduceInfo',{}))
                state=self._state(profile);run=state['runs'][str(uid)]
                base=entry['baseInfo'];base['isChallengeMode']=True;base['reduceInfo']=run['reduceInfo']
                # WU.SyncActorAttr installs these six server actors directly.
                # The six scaled stats follow AU.GetActorCultivationDegreeAttr;
                # skill/talent levels keep the client's minimum-one rule.
                for actor in base['actors']:
                    ratio=run['reduceInfo'].get(str(actor.get('uid',0)),1000)
                    for attr in actor.get('attrs',[]):
                        if attr['type'] in (1,3,5,6,13,14):
                            attr['value']=max(1 if attr['type']==1 else 0,attr['value']*ratio//1000)
                    for skill in actor.get('skills',[]):
                        skill['level']=max(1,skill.get('level',1)*ratio//1000)
                    for talent in actor.get('talentInfo',[]):
                        if 'talentValue' in talent:
                            talent['talentValue']=max(1,talent['talentValue']*ratio//1000)
                run['entry']=entry
                self._save(profile,state)
                return status,entry
        except (ValueError,TypeError) as exc:
            return 400,{'error':str(exc)}

    def active(self, profile, uid, body):
        with self.game.atomic_mutation():
            run=self.run_snapshot(profile,uid)
            report=self.game.dungeons._parse_active(body)
            if report is None:
                return 400,{'error':'invalid_fight_active_data'}
            if (int(report.stageID)!=run['dungeonId'] or int(report.dungeonType)!=2 or
                    int(report.winTeam) not in (0,1,2) or int(report.gameRound)<0 or
                    (int(report.winTeam) and int(report.gameRound)<=0)):
                return 400,{'error':'invalid_battle_identity'}
            with closing(self.game._connect()) as conn:
                row=conn.execute('SELECT active_data FROM local_dungeons WHERE dungeon_uid=? AND account_id=?',
                                 (uid,profile['account_id'])).fetchone()
            if row and row['active_data']:
                old=self.game.dungeons._decode_active_bytes(bytes(row['active_data']))
                if old is not None and int(old.winTeam) and old!=report:
                    return 409,{'error':'dungeon_not_active'}
            return self.game.dungeons._active_impl(profile,uid,body)

    def balance(self, profile, uid, body=None):
        with self.game.atomic_mutation():
            status,payload=self.game.dungeons._balance_impl(profile,uid,body)
            if status==200 and payload.get('balanceInfo',{}).get('win'):
                self.record_win(profile,uid)
            return status,payload

    def close(self, profile, uid):
        with self.game.atomic_mutation():
            with closing(self.game._connect()) as conn:
                row=conn.execute('SELECT status,active_data FROM local_dungeons WHERE dungeon_uid=? AND account_id=?',
                                 (uid,profile['account_id'])).fetchone()
            if row and row['status']=='active' and row['active_data']:
                active=self.game.dungeons._decode_active_bytes(bytes(row['active_data']))
                if active is not None and int(active.winTeam):
                    status,payload=self.balance(profile,uid)
                    if status!=200:
                        return status,payload
            return self.game.dungeons._close_impl(profile,uid)

    def record_win(self, profile, uid):
        """Call inside the dungeon settlement transaction, after validating win."""
        with self.game.atomic_mutation():
            state = self._state(profile)
            run = state['runs'].get(str(uid))
            if run is None:
                raise ValueError('challenge_run_not_found')
            if run['won']:
                return run['score']
            run['won'] = True
            key = str(run['dungeonId'])
            old = state['scoreRecord'].get(key)
            if old is None or run['score'] < old:
                state['scoreRecord'][key] = run['score']
                state['teams'][key] = run['actorGroup']
            # A run keeps the season in which it entered. Once a season has
            # been paid, late completions may improve permanent score prizes
            # but cannot change its frozen rank or issue another medal.
            if 'seasonStart' in run:
                cycle=state.setdefault('rankSeasons',{}).setdefault(str(run['seasonStart']),
                    {'activityId':run['activityId'],'stages':{},'settled':False,'mails':{}})
                if not cycle['settled']:
                    prior=cycle['stages'].get(key)
                    if prior is None or run['score']<prior['score']:
                        cycle['stages'][key]={'score':run['score'],'actorGroup':run['actorGroup']}
            self._save(profile, state)
            return run['score']

    def _ranks(self, profile, dungeon, top=50, state=None, historical=False):
        if state is None:
            saved=self._state(profile);week=season_start(self.game._now())
            seasons=saved.get('rankSeasons',{})
            if historical:
                cycle=next((cycle for start,cycle in sorted(seasons.items(),key=lambda pair:int(pair[0]),reverse=True)
                            if int(start)<week and str(dungeon) in cycle.get('stages',{})),{})
            else:
                cycle=seasons.get(str(week),{})
            record=cycle.get('stages',{}).get(str(dungeon))
            state={'scoreRecord':{str(dungeon):record['score']} if record else {},
                   'teams':{str(dungeon):record['actorGroup']} if record else {}}
        thresholds = sorted(r['score'] for r in SCORE_REWARDS if r['dungeonId'] == dungeon)
        floor, ceiling = min(thresholds), max(thresholds)
        # Fixed identities and scores across GET/restart; no rewards are issued
        # to robots. They are display opponents, never account records.
        values = [{'playerId':f'local-cultivation-{dungeon}-{i}', 'playerName':f'练度演练员{i:02d}',
                   'score':-(floor + (ceiling-floor)*i//49),
                   'actorGroup':[{'id':1001+(i%6),'quality':2}]}
                  for i in range(1,50)]
        if str(dungeon) in state['scoreRecord']:
            values.append({'playerId':str(profile['player_id']), 'playerName':profile.get('name',''),
                           'score':-state['scoreRecord'][str(dungeon)],
                           'actorGroup':[{key:actor[key] for key in ('id','quality') if key in actor}
                                         for actor in state['teams'].get(str(dungeon),[])]})
        return sorted(values, key=lambda r:(-r['score'],r['playerId']))[:max(1,min(50,int(top)))]

    def settle_due(self, profile):
        """Deliver earned original rank medals once after each local week.

        Only recorded wins create rank seasons. Delivery is lazy on login /
        activity reads, so offline users catch up without fabricating played
        weeks. Mail creation and the permanent receipt share one transaction.
        """
        with self.game.atomic_mutation():
            state=self._state(profile);before=json.dumps(state,sort_keys=True)
            if not state.get('runs') and not state.get('rankSeasons'):
                return
            now=self.game._now();week=season_start(now)
            state['maxObservedWeek']=max(week,int(state.get('maxObservedWeek',week)))
            for key,cycle in state.get('rankSeasons',{}).items():
                if int(key)+604800>now or cycle.get('settled'):
                    continue
                config=ACTIVITIES[cycle['activityId']]
                for did,record in cycle['stages'].items():
                    snapshot={'scoreRecord':{did:record['score']},'teams':{did:record['actorGroup']}}
                    ranks=self._ranks(profile,int(did),state=snapshot)
                    rank=next(i for i,row in enumerate(ranks,1) if row['playerId']==str(profile['player_id']))
                    reward_type=config['dungeonReward'][config['dungeonId'].index(int(did))]
                    mail=send_mail(self.game,profile,'练度竞赛赛季排名奖励',
                        f"本地赛季 {cycle['activityId']}，关卡 {did}，排名 {rank}。奖励采用原版排名表。",
                        RANK_REWARDS[(reward_type,rank)],sender_name='本地练度竞赛',important=True)
                    cycle['mails'][did]={'mailId':mail['id'],'rank':rank}
                cycle['settled']=True
                cycle['settledAt']=now
            if json.dumps(state,sort_keys=True)!=before:
                self._save(profile,state)

    def redpoint_fields(self, profile):
        state = self._state(profile)
        available = any(str(r['dungeonId']) in state['scoreRecord']
                        and state['scoreRecord'][str(r['dungeonId'])] <= r['score']
                        and r['score'] not in state['rewardRecords'].get(str(r['dungeonId']),{}).get('rewardGot',[])
                        for r in SCORE_REWARDS)
        return {'cultivationPvpReward': available}

    def handle_http(self, method, path, body, profile):
        route = urlsplit(path).path.rstrip('/').lower()
        if not route.startswith(('/fci/challenge-mode/', '/fci/challenge-mode-rank/')):
            return None
        method = method.upper()
        match = re.fullmatch(r'/fci/challenge-mode-rank/top/(\d+)/dungeon/(\d+)', route)
        season = re.fullmatch(r'/fci/challenge-mode/season/(\d+)', route)
        if method == 'GET' and (match or season):
            dungeon = int(match[2] if match else season[1])
            if dungeon not in DUNGEONS:
                return 404, {'error':'unknown_challenge_dungeon'}
            with self.game.atomic_mutation():
                ranks = self._ranks(profile, dungeon, int(match[1]) if match else 50,historical=bool(season))
            return 200, {'rankInfo' if match else 'rankList': ranks}
        if route != '/fci/challenge-mode/reward':
            return 404, {'error':'unknown_challenge_route'}
        if method == 'GET':
            with self.game.atomic_mutation():
                state = self._state(profile)
                return 200, {k:state[k] for k in ('scoreRecord','rewardRecords')}
        if method != 'POST':
            return 405, {'error':'method_not_allowed'}
        try:
            try:
                request = body if isinstance(body, dict) else json.loads(body or '{}')
            except (ValueError,TypeError):
                cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiChallengeModeReward'))
                request=json_format.MessageToDict(cls.FromString(bytes(body)))
            dungeon, threshold = int(request['dungeonId']), int(request['score'])
        except (ValueError, TypeError, KeyError, DecodeError):
            return 400, {'error':'invalid_request'}
        reward = REWARDS.get((dungeon, threshold))
        with self.game.atomic_mutation():
            state = self._state(profile)
            best = state['scoreRecord'].get(str(dungeon))
            got = state['rewardRecords'].get(str(dungeon),{}).get('rewardGot',[])
            if reward is None or best is None or best > threshold or threshold in got:
                return 409, {'error':'reward_not_available'}
            changes = grant_rewards(self.game, profile, reward['reward'])
            state['rewardRecords'].setdefault(str(dungeon), {'rewardGot':[]})['rewardGot'].append(threshold)
            self._save(profile, state)
            return 200, {'rewardRecords':state['rewardRecords'], 'resChange':changes}
