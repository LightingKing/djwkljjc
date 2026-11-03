"""C1 native request replay: rewards, Monday 06:00 rotation and push 44.

All wins/claims use the original bare protobuf requests. Only the clock and
write-failure injection are controlled; no fixture writes wins or claim flags.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from external_assets import external_path, require_file

from google.protobuf import json_format, message_factory

from accounts import LocalAccounts
from game import LocalGame
from push import _one, _parse_fields
from server.core.notifications import wire_game_notifications
from server.core.push_service import PushService
from server.core.reset_scheduler import ResetScheduler
from test_notifications import Connection
from test_welfare_maze_local import guest
from tournament import FRAGMENT_ITEMS, TOURNAMENT_ID, WEEKLY_FRAGMENT_POOLS
import tournament

try:
    from lupa.lua53 import LuaRuntime
except ImportError:
    LuaRuntime = None


def epoch(text):
    return int(datetime.fromisoformat(text).timestamp())


class TournamentRewardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = epoch('2026-11-02T05:59:59+08:00')
        self.accounts = LocalAccounts(self.root / 'accounts', clock=lambda: self.now)
        self.game = LocalGame(self.root / 'game', self.accounts, initial_level=23, clock=lambda: self.now)
        self.account, self.auth = guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.services, self.connections = [], []

    def tearDown(self):
        self.temp.cleanup()

    def wire(self, name, **fields):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        return json_format.ParseDict(fields, cls()).SerializeToString()

    def request(self, method, path, body=None):
        return self.game.handle_http(method, path, body, 'application/wireformat', self.auth)

    def call(self, method, path, body=None, response_type=None):
        code, result = self.request(method, path, body)
        self.assertEqual(code, 200, (path, result))
        if response_type:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(response_type))
            message = result if hasattr(result, 'SerializeToString') else json_format.ParseDict(result, cls())
            return json_format.MessageToDict(cls.FromString(message.SerializeToString()),
                                            always_print_fields_with_no_presence=True)
        return result

    def info(self):
        return self.call('GET', f'/fci/tournament/info/5/{TOURNAMENT_ID}/',
                         response_type='ApiTournamentInfo.result')['tournamentConf'][0]

    def record(self):
        return self.call('GET', '/fci/asyncpvprecord/', response_type='ApiAsyncPvpRecordInfo.result')

    def win(self, count=1):
        self.call('POST', '/fci/tournament/enter/', self.wire('ApiTournamentEnter',
                  tournamentId=TOURNAMENT_ID, activityType=5))
        targets = self.call('GET', f'/fci/tournament/enter/5/{TOURNAMENT_ID}/')['asyncPvpTargetInfo']
        for target in [t for t in targets if not t['isWin']][:count]:
            entered = self.call('POST', '/fci/tournament/dungeonenter/', self.wire('ApiTournamentDungeonEnter',
                tournamentId=TOURNAMENT_ID, activityType=5, targetUid=target['uid']))
            self.call('PATCH', f"/fci/tournamentdungeon/{entered['dungeonUid']}/active/",
                self.wire('FightActiveData', dungeonType=3, stageID=entered['dungeonId'], gameRound=4, winTeam=1))
            self.call('POST', '/fci/tournament/dungeonbalance/', self.wire('ApiTournamentDungeonBalance', activityType=5))

    def claim_body(self, win_num):
        return self.wire('ApiTournamentWinReward', tournamentId=TOURNAMENT_ID, activityType=5, winNum=win_num)

    def claim(self, win_num):
        return self.call('POST', '/fci/tournament/winreward/', self.claim_body(win_num),
                         response_type='ApiTournamentWinReward.result')

    def item(self, item_id):
        return self.game.systems._state(self.profile).get('items', {}).get(str(item_id), {}).get('count', 0)

    def snapshot(self):
        with closing(self.game._connect()) as connection:
            return tuple(connection.iterdump())

    def connect(self):
        for _ in range(2):
            service, connection = PushService(), Connection()
            service.register(connection, self.account)
            wire_game_notifications(self.game, service)
            self.services.append(service)
            self.connections.append(connection)
        self.scheduler = ResetScheduler(self.game, self.services)
        self.assertEqual(self.scheduler.tick(), 1)
        self.clear()

    def clear(self):
        for connection in self.connections:
            connection.messages.clear()

    @staticmethod
    def arena_pushes(connection):
        return [data for name, data in connection.messages
                if name == 'BasePushMessage' and _one(_parse_fields(data), 1, 0, 0) == 44]

    def test_five_native_wins_grant_direct_fragment_matching_preview_and_persist(self):
        config = self.info()
        rewards = {r['winNum']: r['reward'] for r in config['reward']}
        fragment = rewards[5]['id']
        self.assertIn(fragment, WEEKLY_FRAGMENT_POOLS[config['weekIndex']])
        self.assertEqual(rewards[5]['count'], 1)
        before_coin, before_fragment = self.item(60001), self.item(fragment)
        self.assertEqual(self.record()['leftRewardsWinNum'], [])
        self.win(5)
        self.assertEqual(self.record()['leftRewardsWinNum'], [1, 3, 5])
        for n in (1, 3, 5):
            reward = self.claim(n)['resChange'][0]['baseRes']
            self.assertEqual((reward['id'], reward['countDelta']), (rewards[n]['id'], rewards[n]['count']))
        self.assertEqual(self.item(60001), before_coin + 3)
        self.assertEqual(self.item(fragment), before_fragment + 1)
        self.assertEqual(self.item(123003), 0)
        self.assertEqual(self.record()['leftRewardsWinNum'], [])
        self.game = LocalGame(self.root / 'game', self.accounts, clock=lambda: self.now)
        self.assertEqual(self.info(), config)
        self.assertEqual(self.request('POST', '/fci/tournament/winreward/', self.claim_body(5))[0], 409)
        self.assertEqual(self.item(fragment), before_fragment + 1)

    def test_monday_six_am_rotates_pool_and_resets_once_with_native_push_to_both_listeners(self):
        self.now = epoch('2026-11-01T23:59:59+08:00')
        previous = self.info()
        self.win()
        self.claim(1)
        self.connect()
        self.now += 1  # Monday midnight: sign-in only.
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertEqual(self.info(), previous)
        self.assertEqual(self.record()['todayMaxWinNum'], 1)
        self.assertTrue(all(not self.arena_pushes(c) for c in self.connections))
        self.now = epoch('2026-11-02T05:59:59+08:00')
        self.assertEqual(self.scheduler.tick(), 0)
        self.clear()
        self.now += 1
        self.assertEqual(self.scheduler.tick(), 1)
        current = self.info()
        self.assertNotEqual(current['weekIndex'], previous['weekIndex'])
        self.assertEqual(current['reward'][:2], previous['reward'][:2])
        self.assertIn(current['reward'][2]['reward']['id'], WEEKLY_FRAGMENT_POOLS[current['weekIndex']])
        self.assertNotEqual(current['reward'][2]['reward']['id'], previous['reward'][2]['reward']['id'])
        record = self.record()
        self.assertEqual((record['todayMaxWinNum'], record['leftRewardsWinNum']), (0, []))
        self.assertEqual(record['nextRefreshSyncpvpRecordTime'], epoch('2026-11-03T06:00:00+08:00'))
        for connection in self.connections:
            self.assertEqual(len(self.arena_pushes(connection)), 1)
        self.clear()
        self.assertEqual(ResetScheduler(self.game, self.services).tick(), 0)
        self.assertEqual(self.info(), current)
        self.assertTrue(all(not c.messages for c in self.connections))
        self.win()
        self.claim(1)
        self.clear()
        self.now -= 86400  # Clock rollback never reopens an already claimed day.
        self.assertEqual(self.scheduler.tick(), 0)
        self.assertEqual(self.info(), current)
        self.assertEqual(self.record()['todayMaxWinNum'], 1)
        self.assertEqual(self.request('POST', '/fci/tournament/winreward/', self.claim_body(1))[0], 409)
        self.assertTrue(all(not self.arena_pushes(c) for c in self.connections))

    def test_twenty_four_concurrent_native_claims_award_one_fragment(self):
        fragment = self.info()['reward'][2]['reward']['id']
        self.win(5)
        before = self.item(fragment)
        body = self.claim_body(5)
        with ThreadPoolExecutor(max_workers=24) as pool:
            replies = list(pool.map(lambda _: self.request('POST', '/fci/tournament/winreward/', body), range(24)))
        self.assertEqual([code for code, _ in replies].count(200), 1)
        self.assertEqual([code for code, _ in replies].count(409), 23)
        self.assertEqual(self.item(fragment), before + 1)
        self.assertEqual(self.record()['leftRewardsWinNum'], [1, 3])

    def test_failed_reward_or_claim_write_rolls_back_bag_record_and_notifications(self):
        self.win(5)
        config = self.info()
        fragment = config['reward'][2]['reward']['id']
        before_count = self.item(fragment)
        pushes = []
        self.game.base_push_notifier = lambda *args: pushes.append(args)
        original = tournament.grant_rewards
        def fail_after_grant(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('injected reward failure')
        for target, attribute, effect in (
            (tournament, 'grant_rewards', fail_after_grant),
            (self.game.tournament, '_save', RuntimeError('injected claim failure')),
        ):
            with self.subTest(write=attribute):
                rows = self.snapshot()
                with patch.object(target, attribute, side_effect=effect):
                    with self.assertRaisesRegex(RuntimeError, 'injected'):
                        self.request('POST', '/fci/tournament/winreward/', self.claim_body(5))
                self.assertEqual(self.snapshot(), rows)
                self.assertEqual(pushes, [])
                self.assertEqual(self.info(), config)
                self.assertIn(5, self.record()['leftRewardsWinNum'])
        self.claim(5)
        self.assertEqual(self.item(fragment), before_count + 1)
        self.assertEqual([(args[1], args[2]) for args in pushes], [(3, (fragment,))])

    def test_failed_daily_reset_retries_without_push_or_lost_claim(self):
        self.win()
        self.claim(1)
        self.connect()
        self.now += 1
        with patch.object(self.game.tournament, '_save', side_effect=RuntimeError('injected reset failure')):
            with self.assertLogs('server.core.reset_scheduler', level='ERROR'):
                self.assertEqual(self.scheduler.tick(), 0)
        self.assertTrue(all(not self.arena_pushes(c) for c in self.connections))
        with closing(self.game._connect()) as connection:
            state = json.loads(connection.execute('SELECT state_json FROM local_tournament_state').fetchone()[0])
        self.assertEqual((state['wins'], state['claimed']), (1, [1]))
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertEqual(self.record()['todayMaxWinNum'], 0)
        for connection in self.connections:
            self.assertEqual(len(self.arena_pushes(connection)), 1)

    def test_offline_catchup_draw_is_stable_and_stays_in_current_week_pool(self):
        self.win()
        self.claim(1)
        self.now += 15 * 86400
        current = self.info()
        self.assertEqual(self.record()['todayMaxWinNum'], 0)
        self.assertIn(current['reward'][2]['reward']['id'], WEEKLY_FRAGMENT_POOLS[current['weekIndex']])
        self.game = LocalGame(self.root / 'game', self.accounts, clock=lambda: self.now)
        self.assertEqual(self.info(), current)
        self.assertEqual(len(FRAGMENT_ITEMS), len(set(FRAGMENT_ITEMS)))
        self.assertFalse(set(WEEKLY_FRAGMENT_POOLS[0]) & set(WEEKLY_FRAGMENT_POOLS[1]))
        self.assertEqual(set().union(*map(set, WEEKLY_FRAGMENT_POOLS)), set(FRAGMENT_ITEMS))

    @unittest.skipIf(LuaRuntime is None, 'native Lua test requires hot-update/requirements.txt')
    def test_original_push44_handler_fetches_native_record_and_broadcasts_refresh(self):
        source_root = external_path("LJJC_DECOMPILED_LUA", "analysis/lua-decompiled")
        require_file(source_root / "manager__pushservice.lua", "LJJC_DECOMPILED_LUA")
        source = (source_root / 'manager__pushservice.lua').read_text(encoding='utf-8')
        handler = re.search(r'^function m_handler.AsyncPvpDailyRefresh\(\).*?^end$', source, re.M | re.S).group()
        runtime = LuaRuntime()
        runtime.execute('''
            m_handler={}; this={}; requests={}; values={}; broadcasts={}
            function this:GameRequest(path)
              requests[#requests+1]=path
              return {Get=function(_,callback) callback(response) end}
            end
            function this:SetData(path,value) values[path]=value end
            function this:BroadcastGameEvent(event) broadcasts[#broadcasts+1]=event end
        ''')
        response = self.record()
        runtime.globals().response = runtime.table_from(response)
        runtime.execute(handler)
        runtime.globals().m_handler.AsyncPvpDailyRefresh()
        self.assertEqual(runtime.globals().requests[1], 'fci/asyncpvprecord/')
        self.assertEqual(runtime.globals().broadcasts[1], 'AsyncPvpDailyRefresh')
        self.assertEqual(runtime.globals()['values']['fci/asyncpvprecord/']['todayMaxWinNum'], 0)


if __name__ == '__main__':
    unittest.main()
