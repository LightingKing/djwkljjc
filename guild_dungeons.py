"""Native guild assist/challenge dungeons with local cooperating guild bots.

Original GuildDungeon/MonsterLevelUp* tables determine stages, enemy HP and
rewards. Local policy: five bots each contribute 10% of every boss's base HP
once per Beijing week. Player damage and claims persist atomically. This is
an offline cooperative substitute, not a recovered live guild service.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory
from google.protobuf.message import DecodeError

import actors
from grants import grant_rewards
from local_mailbox import send_mail


def _rows(name):
    return json.loads((Path(__file__).parent / 'data/client-tables' / (name+'.json')).read_text(encoding='utf-8'))['rows']


STAGES = {r['dungeonId']:r for r in _rows('GuildDungeon')}
MONSTERS = {r['dungeonID']:r for r in _rows('DungeonMonster')}
MONSTER_INFO = {r['monsterID']:r for r in _rows('MonsterInfo')}
LEVEL_BASE = {r['level']:r for r in _rows('MonsterLevelUpBase')}
LEVEL_INDEX = {r['index']:r for r in _rows('MonsterLevelUpIndex')}
ACTOR_CONFIG = {r['id']:r for r in _rows('ActorConfig')}
ASSIST_REWARDS = {r['score']:r['rewards'] for r in _rows('GuildDungeonAssistScoreReward')}
CHALLENGE_REWARDS = {(r['dungeonId'],r['score']):r['rewards'] for r in _rows('GuildDungeonChallengeScoreReward')}
PROGRESS_REWARDS = {(r['dungeonId'],r['progress']):r['rewards'] for r in _rows('GuildDungeonChallengeProgressReward')}
PROFITS = {r['id']:r for r in _rows('ProfitDetail') if r['id'] in (501,502,503)}
BEIJING = timezone(timedelta(hours=8))
UID_BASE = 7_000_000_000_000


def _copy(value):
    return json.loads(json.dumps(value))


def _base_hp(dungeon):
    row = MONSTERS[dungeon]
    hp = []
    for i, monster_id in enumerate(row['monsterId']):
        if not monster_id:
            hp.append(0)
            continue
        index = (row.get('levelUpIndex') or [])[i] if i < len(row.get('levelUpIndex') or []) else 0
        index = index or MONSTER_INFO[monster_id]['levelUpIndex']
        base = next(a.get('value',0) for a in LEVEL_BASE[row['level'][i]]['attrs'] if a['type']==1)
        factor = next(a.get('value',0) for a in LEVEL_INDEX[index]['attrs'] if a['type']==1)
        hp.append(base*factor//10000)
    return (hp+[0]*6)[:6]


class GuildDungeonError(Exception):
    def __init__(self, reason, status=409):
        self.reason, self.status = reason, status


class LocalGuildDungeons:
    # Values from descriptor_set.pb/GuildChallengeDungeonStatus, not UI tab indices.
    CLOSE, OPEN, UNOPEN = 1, 2, 3

    def __init__(self, game):
        self.game = game
        with game._lock, closing(game._connect()) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS local_hidden_guild_state '
                         '(guild_id INTEGER,week INTEGER,state_json TEXT NOT NULL,PRIMARY KEY(guild_id,week))')
            conn.execute('CREATE TABLE IF NOT EXISTS local_hidden_guild_player '
                         '(account_id INTEGER,week INTEGER,state_json TEXT NOT NULL,PRIMARY KEY(account_id,week))')
            conn.execute('CREATE TABLE IF NOT EXISTS local_hidden_guild_runs '
                         '(uid INTEGER PRIMARY KEY,account_id INTEGER NOT NULL,guild_id INTEGER NOT NULL,'
                         'week INTEGER NOT NULL,run_json TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS local_hidden_guild_clock '
                         '(id INTEGER PRIMARY KEY CHECK(id=1), max_seen INTEGER NOT NULL)')

    def _max_seen(self):
        with closing(self.game._connect()) as conn:
            row=conn.execute('SELECT max_seen FROM local_hidden_guild_clock WHERE id=1').fetchone()
        return int(row[0]) if row else 0

    def _week(self):
        local = datetime.fromtimestamp(self.game._now(), BEIJING) - timedelta(hours=6)
        start = (local-timedelta(days=local.weekday())).replace(hour=6,minute=0,second=0,microsecond=0)
        return int(start.timestamp())

    def _phase(self):
        local = datetime.fromtimestamp(self.game._now(), BEIJING)
        if local.weekday() == 0 and local.hour < 6:
            return self.CLOSE
        return self.OPEN if local.weekday() >= 5 else self.UNOPEN

    def _boss(self):
        return 6001 + (self._week() // 604800) % 3

    def _membership(self, profile):
        def read(conn, state, player, now, notices):
            guild_id = int(player.get('guildId',0))
            guild = state['guilds'].get(str(guild_id))
            if not guild or str(profile['player_id']) not in guild['members']:
                raise GuildDungeonError('guild_not_member',403)
            if int(player.get('guildDungeonLimitEndTime',0)) > now:
                raise GuildDungeonError('guild_function_cooldown')
            return guild_id, _copy(guild)
        return self.game.guild._run(profile, read)

    def _state(self, guild_id, guild):
        week = self._week()
        with closing(self.game._connect()) as conn:
            row = conn.execute('SELECT state_json FROM local_hidden_guild_state WHERE guild_id=? AND week=?',
                               (guild_id,week)).fetchone()
        if row:
            return json.loads(row[0])
        bots = list(guild.get('botIds',[]))[:5]
        stages = {}
        for dungeon in [6101,6102,6103,6104,6105,self._boss()]:
            base = _base_hp(dungeon)
            damage = sum(base)//10
            stages[str(dungeon)] = {'damage':damage*len(bots),
                'records':{pid:{'score':damage,'actorGroup':[{'id':1001+i,'level':60,'star':3,'quality':2}]}
                           for i,pid in enumerate(bots)}}
        return {'stages':stages}

    def _save_guild(self, guild_id, state):
        self._save_cycle(guild_id,self._week(),state)

    def _save_cycle(self, guild_id, week, state):
        with closing(self.game._connect()) as conn, conn:
            conn.execute('INSERT INTO local_hidden_guild_state VALUES (?,?,?) '
                         'ON CONFLICT(guild_id,week) DO UPDATE SET state_json=excluded.state_json',
                         (guild_id,week,json.dumps(state,separators=(',',':'))))

    def _player(self, profile):
        with closing(self.game._connect()) as conn:
            row = conn.execute('SELECT state_json FROM local_hidden_guild_player WHERE account_id=? AND week=?',
                               (profile['account_id'],self._week())).fetchone()
        if row:
            return json.loads(row[0])
        ids, uids = self.game.dungeons._fight_team(profile,1)
        return {'records':{},'rewardRecords':{},'progressRewards':{},'selectedGroupId':1,
                'groups':{'1':{'members':[{'index':i,'actorUid':uid,'actorGuildUid':0} for i,uid in enumerate(uids)]}},
                'rentActor':0,'rented':{}}

    def _save_player(self, profile, state):
        with closing(self.game._connect()) as conn, conn:
            conn.execute('INSERT INTO local_hidden_guild_player VALUES (?,?,?) '
                         'ON CONFLICT(account_id,week) DO UPDATE SET state_json=excluded.state_json',
                         (profile['account_id'],self._week(),json.dumps(state,separators=(',',':'))))

    def _progress(self, dungeon, state):
        return min(100, state['stages'][str(dungeon)]['damage']*100//max(1,sum(_base_hp(dungeon))))

    def _hp(self, dungeon, state):
        damage = min(sum(_base_hp(dungeon)), state['stages'][str(dungeon)]['damage'])
        hp = _base_hp(dungeon)
        # Remove shared damage in slot order, preserving the native six-slot
        # integer HP contract. Zero represents a defeated enemy on re-entry.
        for i,value in enumerate(hp):
            applied = min(value, damage)
            hp[i] -= applied
            damage -= applied
        return hp

    def _summary(self, profile, guild_state):
        stage_ids = [6101,6102,6103,6104,6105,self._boss()]
        records = [{'dungeonId':d, 'dungeonType':STAGES[d]['dungeonType'],
                    'progress':self._progress(d,guild_state), 'isFinished':self._progress(d,guild_state)>=100}
                   for d in stage_ids]
        selected = [self._boss()] if self._phase()==self.OPEN else stage_ids[:5]
        scores = {}
        for dungeon in selected:
            for player,row in guild_state['stages'][str(dungeon)]['records'].items():
                scores[player] = scores.get(player,0)+row['score']
        scores.setdefault(str(profile['player_id']),0)
        rank = [{'playerId':p,'score':s} for p,s in sorted(scores.items(),key=lambda x:(-x[1],x[0]))]
        return {'challengeStatus':self._phase(), 'dungeonRecords':records, 'memberRank':rank}

    def _request(self, body, message=None):
        if isinstance(body,dict):
            return body
        try:
            value = json.loads(body or '{}')
            if not isinstance(value,dict):
                raise ValueError()
            return value
        except (ValueError,TypeError):
            if message:
                try:
                    cls=message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(message))
                    return json_format.MessageToDict(cls.FromString(bytes(body)),always_print_fields_with_no_presence=True)
                except (KeyError,DecodeError,TypeError,ValueError):
                    pass
            raise GuildDungeonError('invalid_request',400)

    def _rent_pool(self, profile, guild_id, guild, player):
        state = self.game.systems._state(profile)
        pool=[]
        for i,pid in enumerate(guild.get('botIds',[])[:5]):
            actor = actors.new_record([1001,1002,1003,2001,2002][i], 700_000_000 + guild_id*10+i)
            actor.update(level=60,star=3,quality=2)
            item=self.game.systems._st_actor_json(actor,profile,state,1)
            pool.append({'playerId':pid,'actor':item,'fightTimes':0,'cultivationDegree':1500,'posEquip':{}})
        if player['rentActor']:
            actor = actors.get(profile,state,player['rentActor'])
            if actor:
                pool.append({'playerId':str(profile['player_id']),
                             'actor':self.game.systems._st_actor_json(actor,profile,state,1),
                             'fightTimes':0,'cultivationDegree':1000,'posEquip':{}})
        return pool

    def _team(self, profile, player, group_id):
        group = player['groups'].get(str(group_id))
        if not group:
            raise GuildDungeonError('invalid_actor_group',400)
        state=self.game.systems._state(profile)
        actor_ids, actor_uids, fighters=[0]*6,[0]*6,[{} for _ in range(6)]
        seen=set(); slots=set()
        for member in group['members']:
            index=int(member.get('index',-1))
            if not 0<=index<6 or index in slots:
                raise GuildDungeonError('invalid_actor_group',400)
            slots.add(index)
            uid=int(member.get('actorUid',0)); rented=int(member.get('actorGuildUid',0))
            if uid and rented or (uid or rented) in seen:
                raise GuildDungeonError('invalid_actor_group',400)
            if uid:
                actor=actors.get(profile,state,uid)
                if not actor:
                    raise GuildDungeonError('actor_not_owned',400)
                fighters[index]=self.game.systems._st_actor_json(actor,profile,state,1)
            elif rented:
                info=next((v for v in player['rented'].values() if int(v['actor']['uid'])==rented),None)
                if not info:
                    raise GuildDungeonError('actor_not_rented',400)
                fighters[index]=_copy(info['actor'])
            else:
                continue
            actor_ids[index]=int(fighters[index]['id']); actor_uids[index]=int(fighters[index]['uid'])
            if ACTOR_CONFIG[actor_ids[index]]['kind'] != (1 if index<3 else 2):
                raise GuildDungeonError('invalid_actor_group',400)
            seen.add(uid or rented)
        if any(actor_ids[i] and not actor_ids[i-3] for i in range(3,6)):
            raise GuildDungeonError('invalid_actor_group',400)
        if not any(actor_ids[:3]):
            raise GuildDungeonError('profile_has_no_actors')
        return actor_ids,actor_uids,fighters

    def _run(self, profile, uid):
        with closing(self.game._connect()) as conn:
            row=conn.execute('SELECT run_json,week,guild_id FROM local_hidden_guild_runs WHERE uid=? AND account_id=?',
                             (uid,profile['account_id'])).fetchone()
        if not row:
            raise GuildDungeonError('dungeon_not_active')
        if row['week'] != self._week():
            raise GuildDungeonError('activity_closed_today')
        return json.loads(row['run_json']),int(row['guild_id'])

    def _save_run(self, profile, uid, guild_id, run):
        with closing(self.game._connect()) as conn, conn:
            conn.execute('INSERT INTO local_hidden_guild_runs VALUES (?,?,?,?,?) ON CONFLICT(uid) '
                         'DO UPDATE SET run_json=excluded.run_json',
                         (uid,profile['account_id'],guild_id,self._week(),json.dumps(run,separators=(',',':'))))

    def _enter(self, profile, guild_id, guild_state, player, request):
        if self.game._now()<self._max_seen() or guild_state.get('settlement'):
            raise GuildDungeonError('clock_rollback')
        try:
            dungeon=int(request.get('dungeonId',0)); group=int(request.get('selectActorGroupId',1))
        except (TypeError,ValueError):
            raise GuildDungeonError('invalid_request',400)
        if dungeon not in guild_state['stages'] and str(dungeon) not in guild_state['stages']:
            raise GuildDungeonError('unknown_guild_dungeon',404)
        if self._phase()==self.CLOSE or (STAGES[dungeon]['dungeonType']==15) != (self._phase()==self.UNOPEN):
            raise GuildDungeonError('activity_closed_today')
        with closing(self.game._connect()) as conn:
            rows=conn.execute('SELECT uid,run_json FROM local_hidden_guild_runs WHERE account_id=? AND guild_id=? AND week=? ORDER BY uid DESC',
                              (profile['account_id'],guild_id,self._week())).fetchall()
            for row in rows:
                prior=json.loads(row['run_json'])
                if prior['status']=='active':
                    if prior['dungeonId']!=dungeon:
                        raise GuildDungeonError('dungeon_already_active')
                    return prior['entry']
            if self._progress(dungeon,guild_state)>=100:
                raise GuildDungeonError('dungeon_completed')
            uid=max(UID_BASE,int(conn.execute('SELECT COALESCE(MAX(uid),0) FROM local_hidden_guild_runs').fetchone()[0]))+1
        ids,uids,fighters=self._team(profile,player,group)
        base=self.game.dungeons._base_info(profile,dungeon,ids,uids,dungeon_type=STAGES[dungeon]['dungeonType'],
                                          activity_id=STAGES[dungeon]['activityId'])
        base['actors']=fighters
        native_fields={f.json_name for f in self.game._pool.FindMessageTypeByName('DbDungeon.BaseInfo').fields}
        base={key:value for key,value in base.items() if key in native_fields}
        base['reduceInfo']={}
        entry={'dungeonUid':uid,'baseInfo':base,'enemyHplist':self._hp(dungeon,guild_state)}
        run={'dungeonId':dungeon,'status':'active','entry':entry,'report':None,'result':None}
        self._save_run(profile,uid,guild_id,run)
        player['selectedGroupId']=group
        self._save_player(profile,player)
        self._save_guild(guild_id,guild_state)
        return entry

    def _active(self, profile, uid, guild_id, body):
        run,owner_guild=self._run(profile,uid)
        if owner_guild!=guild_id or run['status']!='active':
            raise GuildDungeonError('dungeon_not_active')
        report=self._request(body,'FightActiveData')
        if (int(report.get('stageID',0))!=run['dungeonId'] or
                int(report.get('dungeonType',0))!=STAGES[run['dungeonId']]['dungeonType']):
            raise GuildDungeonError('invalid_battle_identity',400)
        if int(report.get('gameRound',0))<0 or int(report.get('winTeam',0)) not in (0,1,2):
            raise GuildDungeonError('invalid_battle_result',400)
        if run['report'] and int(run['report'].get('winTeam',0)) and report!=run['report']:
            raise GuildDungeonError('dungeon_not_active')
        run['report']=report
        self._save_run(profile,uid,guild_id,run)
        return {'result':True}

    def _balance(self, profile, uid, guild_id, guild_state, player):
        run,owner_guild=self._run(profile,uid)
        if owner_guild!=guild_id:
            raise GuildDungeonError('dungeon_not_active')
        if run['result']:
            return run['result']
        if guild_state.get('settlement') or self.game._now()<self._max_seen():
            raise GuildDungeonError('activity_closed_today')
        report=run['report']
        if run['status']!='active' or not report or not int(report.get('winTeam',0)) or int(report.get('gameRound',0))<=0:
            raise GuildDungeonError('dungeon_not_active')
        did=run['dungeonId']; target=guild_state['stages'][str(did)]
        enemy=next((t for t in report.get('teamList',[]) if int(t.get('team',0))==2),{})
        damage=max(0,int(enemy.get('statistic',{}).get('totalDamage',0)))
        score=min(sum(_base_hp(did)),damage)
        if score>0 and STAGES[did]['dungeonType']==16:
            guild_state.setdefault('humanParticipants',{})[str(profile['player_id'])]=int(profile['account_id'])
        target['damage']=min(sum(_base_hp(did)),target['damage']+score)
        actor_group=[{key:actor[key] for key in ('id','quality','star','level') if key in actor}
                     for actor in run['entry']['baseInfo']['actors']]
        record=target['records'].get(str(profile['player_id']),{'score':0,'actorGroup':actor_group})
        if score>record['score']:
            record={'score':score,'actorGroup':actor_group}
        target['records'][str(profile['player_id'])]=record
        current=player['records'].setdefault(str(did),{'dungeonId':did,'dungeonType':STAGES[did]['dungeonType'],'score':0,'finishTimes':0})
        current['score']=max(current['score'],score);current['finishTimes']+=1
        result={'result':{'balanced':True,'balanceTime':self.game._now(),
                          'win':int(report['winTeam'])==1,'resChange':[],'profits':[],'targetsDone':[]},
                'score':score,'progress':self._progress(did,guild_state)}
        run.update(status='settled',result=result)
        self._save_guild(guild_id,guild_state)
        self._save_player(profile,player)
        self._save_run(profile,uid,guild_id,run)
        return result

    def _claim(self, profile, guild_state, player, request, progress=False):
        try:
            did=int(request.get('dungeonId',0));threshold=int(request['progress' if progress else 'score'])
        except (ValueError,TypeError,KeyError):
            raise GuildDungeonError('invalid_request',400)
        if progress:
            rewards=PROGRESS_REWARDS.get((did,threshold))
            got=player['progressRewards'].get(str(did),[])
            earned=(did==self._boss() and self._progress(did,guild_state)>=threshold
                    and player['records'].get(str(did),{}).get('finishTimes',0)>0)
        else:
            rewards=ASSIST_REWARDS.get(threshold) if did==0 else CHALLENGE_REWARDS.get((did,threshold))
            score=sum(r['score'] for r in player['records'].values() if r['dungeonType']==15) if did==0 else player['records'].get(str(did),{}).get('score',0)
            got=player['rewardRecords'].get(str(did),{}).get('rewardGot',[])
            earned=score>=threshold
        if not rewards or not earned or threshold in got:
            raise GuildDungeonError('reward_not_available')
        changes=grant_rewards(self.game,profile,rewards)
        if progress:
            player['progressRewards'].setdefault(str(did),[]).append(threshold)
            result={'rewardGot':player['progressRewards'][str(did)],'resChange':changes}
        else:
            player['rewardRecords'].setdefault(str(did),{'rewardGot':[]})['rewardGot'].append(threshold)
            result={'rewardRecords':player['rewardRecords'],'resChange':changes}
        self._save_player(profile,player)
        return result

    def settle_due(self, profile):
        """Settle completed guild challenges at Monday 00:00 Beijing.

        Shared popularity and the original profit tier are frozen once for
        the guild/week. Personal contribution mails are delivered only to
        accounts that actually dealt challenge damage, including offline
        members when they next log in. Late delivery never extends a buff.
        """
        with self.game.atomic_mutation():
            now=self.game._now()
            with closing(self.game._connect()) as conn:
                conn.execute('INSERT INTO local_hidden_guild_clock VALUES(1,?) '
                             'ON CONFLICT(id) DO UPDATE SET max_seen=MAX(max_seen,excluded.max_seen)',(now,))
                rows=conn.execute('SELECT guild_id,week,state_json FROM local_hidden_guild_state '
                                  'WHERE week+?<=? ORDER BY week,guild_id',(604800-21600,now)).fetchall()
            for row in rows:
                state=json.loads(row['state_json']);before=json.dumps(state,sort_keys=True)
                participants=state.get('humanParticipants',{})
                if not participants:
                    continue
                gid,week=int(row['guild_id']),int(row['week'])
                did=6001+(week//604800)%3;config=STAGES[did]
                if not state.get('settlement'):
                    scores=sorted((r['score'] for r in state['stages'][str(did)]['records'].values()),reverse=True)
                    top_three=sum(scores[:3])
                    excess=max(0,top_three-config['scoreDelta'])
                    shared=min(config['scoreUpLimitForGuild'],excess*config['exchangeScaleForGuild']//10000)
                    personal=min(config['scoreUpLimit'],excess*config['exchangeScaleForPerson']//10000)
                    reached=[r for r in config['challengeBuffs'] if top_three>=r['targetScore']]
                    profit_id=max(reached,key=lambda r:r['targetScore'])['id'] if reached else 0
                    end=week+604800-21600
                    state['settlement']={'topThreeScore':top_three,'guildScore':shared,'personalScore':personal,
                                         'profitId':profit_id,'startTime':end,
                                         'endTime':end+PROFITS[profit_id]['time'] if profit_id else end}
                    def credit(conn,guilds,player,clock,notices):
                        guild=guilds['guilds'].get(str(gid))
                        if guild is None:
                            return
                        guild['baseInfo']['score']=int(guild['baseInfo'].get('score',0))+shared
                        for pid in participants:
                            member=guild.get('members',{}).get(pid)
                            owner=guilds['players'].get(pid)
                            if member and owner and int(owner['data'].get('guildId',0))==gid:
                                owner['data']['guildScoreRecord']=int(owner['data'].get('guildScoreRecord',0))+personal
                                member['guildScoreRecord']=owner['data']['guildScoreRecord']
                                member['guildScore']=int(member.get('guildScore',0))+personal
                                notices.append(({'account_id':owner['accountId'],'player_id':pid},55,
                                                [guild['baseInfo']['score'],pid,member['guildScoreRecord']]))
                    self.game.guild._run(profile,credit)
                pid=str(profile['player_id'])
                receipts=state.setdefault('personalMails',{})
                if participants.get(pid)==int(profile['account_id']) and pid not in receipts:
                    amount=state['settlement']['personalScore']
                    mail=send_mail(self.game,profile,'公会副本周期贡献奖励',
                        f"公会 {gid}，挑战关卡 {did}。前三名总分 {state['settlement']['topThreeScore']}，按原表结算贡献。",
                        [{'type':17,'id':0,'count':amount}],sender_name='本地公会副本',important=True) if amount else None
                    receipts[pid]=mail['id'] if mail else 0
                if json.dumps(state,sort_keys=True)!=before:
                    self._save_cycle(gid,week,state)

    def _active_cycle_profit(self, profile):
        # Native guild profits benefit current members. Personal medals and
        # contribution mails remain participation-gated in settle_due().
        gid=self.game.guild._run(profile,lambda conn,state,player,now,notices:int(player.get('guildId',0)))
        if not gid:
            return None
        now=max(self.game._now(),self._max_seen())
        with closing(self.game._connect()) as conn:
            rows=conn.execute('SELECT state_json FROM local_hidden_guild_state WHERE guild_id=? ORDER BY week DESC',(gid,)).fetchall()
        for row in rows:
            settlement=json.loads(row[0]).get('settlement',{})
            if settlement.get('profitId') and settlement['startTime']<=now<settlement['endTime']:
                return settlement
        return None

    def active_profits(self, profile):
        settlement=self._active_cycle_profit(profile)
        if not settlement:
            return []
        row=PROFITS[settlement['profitId']]
        return [{'id':row['id'],'type':row['type'],'add':row['add'],
                 'startTime':settlement['startTime'],'endTime':settlement['endTime']}]

    def active_profit_rules(self, profile):
        settlement=self._active_cycle_profit(profile)
        if not settlement:
            return []
        row=PROFITS[settlement['profitId']]
        return [{'id':row['id'],'type':row['type'],'add':row['add'],'isRatio':row['isRatio'],
                 'dungeonType':row.get('dungeonType',[]),'resType':row.get('resType',0),'funcType':row.get('funcType',0)}]

    def redpoint_fields(self, profile):
        try:
            with self.game.atomic_mutation():
                gid,guild=self._membership(profile);state=self._state(gid,guild);player=self._player(profile)
                did=self._boss()
                return {'guildDungeonSummary':self._summary(profile,state),
                        'guildDungeonScoreReward':{'dungeonRecords':list(player['records'].values()),'rewardRecords':player['rewardRecords']},
                        'guildDungeonProgressReward':{'rewardGot':player['progressRewards'].get(str(did),[])}}
        except GuildDungeonError:
            return {'guildDungeonSummary':{'dungeonRecords':[],'memberRank':[],'challengeStatus':self.CLOSE},
                    'guildDungeonScoreReward':{'dungeonRecords':[],'rewardRecords':{}},
                    'guildDungeonProgressReward':{'rewardGot':[]}}

    def player_fields(self, profile):
        """Native login fields consumed by PushService's DungeonGuild bindings."""
        try:
            with self.game.atomic_mutation():
                self._membership(profile)
                player=self._player(profile)
                return {'rentActor':player['rentActor'],'rentedActors':player['rented'],
                        'actorGroupInfo':{'actorGroups':player['groups'],'selectId':player['selectedGroupId']},
                        'guildDungeonActorGroupId':player['selectedGroupId']}
        except GuildDungeonError:
            return {}

    def handle_http(self, method, path, body, profile):
        route=re.sub('/{2,}','/',urlsplit(path).path).rstrip('/').lower(); method=method.upper()
        summary=re.fullmatch(r'/fci/guild/(\d+)/dungeon/summary',route)
        rental=re.fullmatch(r'/fci/guild/(\d+)/rent-actor',route)
        group=route=='/fci/guild/guilddungeonactorgroup'
        select=route=='/fci/selectedgroupid/guild-dungeon'
        if not (summary or rental or group or select or route.startswith('/fci/guild-dungeon')):
            return None
        try:
            with self.game.atomic_mutation():
                gid,guild=self._membership(profile)
                if (summary or rental) and int((summary or rental)[1])!=gid:
                    raise GuildDungeonError('guild_not_found',404)
                state=self._state(gid,guild);player=self._player(profile)
                if summary and method=='GET':
                    return 200,self._summary(profile,state)
                if select:
                    if method=='GET':return 200,{'selectActorGroupId':player['selectedGroupId']}
                    request=self._request(body,'ApiSelectedActorGroup')
                    selected=int(request.get('selectActorGroupId',0))
                    if str(selected) not in player['groups']:raise GuildDungeonError('invalid_actor_group',400)
                    player['selectedGroupId']=selected;self._save_player(profile,player)
                    return 200,{'selectActorGroupId':selected}
                if group:
                    if method=='GET':return 200,player['groups']
                    if method not in ('PUT','PATCH'):return 405,{'error':'method_not_allowed'}
                    request=self._request(body,'ApiGuildDungeonActorGroup')
                    groups=request.get('actorGroups')
                    if not isinstance(groups,dict) or not groups or len(groups)>5:raise GuildDungeonError('invalid_actor_group',400)
                    candidate=_copy(player);candidate['groups']=groups
                    for key in groups:self._team(profile,candidate,int(key))
                    player['groups']=groups;self._save_player(profile,player)
                    return 200,groups
                if rental:
                    pool=self._rent_pool(profile,gid,guild,player)
                    if method=='GET':return 200,{'rentActorInfo':pool}
                    if method!='POST':return 405,{'error':'method_not_allowed'}
                    request=self._request(body,'ApiGuildRentActor')
                    if request.get('actorUid'):
                        uid=int(request['actorUid'])
                        if not actors.get(profile,self.game.systems._state(profile),uid):raise GuildDungeonError('actor_not_owned',400)
                        player['rentActor']=uid
                    else:
                        uid=int(request.get('actorGuildUid',0));kind=int(request.get('actorKind',0))
                        actor=next((r for r in pool if int(r['actor']['uid'])==uid and r['playerId']!=str(profile['player_id'])),None)
                        if not actor or kind not in (1,2) or ACTOR_CONFIG[actor['actor']['id']]['kind']!=kind:
                            raise GuildDungeonError('invalid_rental',400)
                        player['rented']={key:value for key,value in player['rented'].items()
                                          if ACTOR_CONFIG[value['actor']['id']]['kind']!=kind}
                        player['rented'][str(uid)]=actor
                    self._save_player(profile,player)
                    own=next((r for r in self._rent_pool(profile,gid,guild,player)
                              if r['playerId']==str(profile['player_id'])),{})
                    return 200,{'rentActorInfo':own,'rentedActorInfo':player['rented']}
                if route=='/fci/guild-dungeon-record' and method=='GET':
                    return 200,{'dungeonRecords':list(player['records'].values())}
                if route=='/fci/guild-dungeon/reward':
                    if method=='GET':return 200,{'dungeonRecords':list(player['records'].values()),'rewardRecords':player['rewardRecords']}
                    if method=='POST':return 200,self._claim(profile,state,player,self._request(body,'ApiGuildDungeonScoreReward'))
                if route=='/fci/guild-dungeon/progress-reward':
                    if method=='GET':return 200,{'rewardGot':player['progressRewards'].get(str(self._boss()),[])}
                    if method=='POST':return 200,self._claim(profile,state,player,self._request(body,'ApiGuildDungeonProgressReward'),True)
                record=re.fullmatch(r'/fci/guild-dungeon/record/(\d+)',route)
                if record and method=='GET':
                    did=int(record[1])
                    if str(did) not in state['stages']:raise GuildDungeonError('unknown_guild_dungeon',404)
                    records=state['stages'][str(did)]['records']
                    return 200,{'dungeonRecord':records,'memberRank':sorted(records,key=lambda p:(-records[p]['score'],p)),
                                'enemyHpList':self._hp(did,state)}
                if route=='/fci/guild-dungeon' and method=='POST':
                    return 200,self._enter(profile,gid,state,player,self._request(body,'ApiGuildDungeon'))
                battle=re.fullmatch(r'/fci/guild-dungeon/(\d+)(?:/(active|balance))?',route)
                if battle:
                    uid=int(battle[1]);action=battle[2]
                    if action=='active' and method=='PATCH':return 200,self._active(profile,uid,gid,body)
                    if action=='balance' and method=='POST':return 200,self._balance(profile,uid,gid,state,player)
                    if action is None and method=='DELETE':
                        run,owner=self._run(profile,uid)
                        if owner!=gid:raise GuildDungeonError('dungeon_not_active')
                        if run['status']=='active':
                            report=run.get('report') or {}
                            if int(report.get('winTeam',0)) and int(report.get('gameRound',0))>0:
                                # Guild ranking uses damage on both win and loss.
                                # Native clients can DELETE before requesting
                                # balance; keep the same atomic cached result.
                                self._balance(profile,uid,gid,state,player)
                            else:
                                run['status']='closed';self._save_run(profile,uid,gid,run)
                        return 200,{'result':True}
                return 404,{'error':'unknown_guild_dungeon_route'}
        except GuildDungeonError as exc:
            return exc.status,{'error':exc.reason}
        except (ValueError,TypeError,KeyError):
            return 400,{'error':'invalid_request'}
