"""Activity ticket refresh and native dungeon calls share the outer transaction."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from google.protobuf import message_factory
from accounts import LocalAccounts
from game import LocalGame
import test_systems


class ActivityAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        now = int(datetime(2026, 10, 26, 12, tzinfo=timezone.utc).timestamp())
        self.accounts = LocalAccounts(root / 'accounts')
        self.game = LocalGame(root / 'game', self.accounts, initial_level=30, clock=lambda: now)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.modes = self.game.activity_modes
        self.game.systems._state(self.profile)

    def tearDown(self):
        self.temp.cleanup()

    def state(self):
        profile = self.game._profile(self.account)
        with closing(self.game._connect()) as conn:
            runs = [tuple(row) for row in conn.execute('SELECT * FROM activity_mode_runs ORDER BY dungeon_uid')]
        return {'level': profile['level'], 'resources': profile['resources'],
                'bag': self.game.systems._state(profile), 'modes': self.modes._state(profile), 'runs': runs}

    def enter_material(self):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiActivityDungeon'))
        body = cls(select_actor_group_id=1, dungeon_id=52101, activity_type=2,
                   activity_id=2001, multi_power=1).SerializeToString()
        return self.game.handle_http('POST', '/fci/activity-dungeon/', body, 'application/wireformat', self.auth)

    def test_ticket_refresh_can_nest_and_outer_rollback_restores_bag_and_markers(self):
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'outer failed'):
            with self.game.atomic_mutation():
                self.assertTrue(self.modes.refresh_daily_items(self.profile))
                raise ValueError('outer failed')
        self.assertEqual(self.state(), before)
        with self.game.atomic_mutation():
            granted = self.modes.refresh_daily_items(self.profile)
        self.assertTrue(granted)
        committed = self.state()
        self.assertEqual(self.modes.refresh_daily_items(self.profile), [])
        self.assertEqual(self.state(), committed)

    def test_marker_write_failure_rolls_back_ticket_inventory_and_allows_retry(self):
        before = self.state()
        with closing(self.game._connect()) as conn, conn:
            conn.execute("CREATE TRIGGER fail_refresh BEFORE INSERT ON activity_mode_state "
                         "BEGIN SELECT RAISE(ABORT, 'marker failure'); END")
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'marker failure'):
                self.modes.refresh_daily_items(self.profile)
            self.assertEqual(self.state(), before)
        finally:
            with closing(self.game._connect()) as conn, conn:
                conn.execute('DROP TRIGGER fail_refresh')
        self.assertTrue(self.modes.refresh_daily_items(self.profile))

    def test_native_entry_failure_refunds_cost_and_restores_the_actual_profile(self):
        before = self.state()
        profiles = []
        original = self.modes._change_resources
        def remember_profile(profile, *args, **kwargs):
            profiles.append(profile)
            return original(profile, *args, **kwargs)
        with patch.object(self.modes, '_change_resources', side_effect=remember_profile), \
             patch.object(self.modes, '_insert_run', side_effect=RuntimeError('run insert failed')):
            with self.assertRaisesRegex(RuntimeError, 'run insert failed'):
                self.enter_material()
        self.assertTrue(profiles)
        self.assertEqual(profiles[-1]['resources'], before['resources'])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.enter_material()[0], 200)

    def test_native_balance_failure_restores_claim_rewards_records_and_profile(self):
        status, entered = self.enter_material()
        self.assertEqual(status, 200)
        uid = entered['dungeonUid']
        active = self.game._types['FightActiveData'](dungeonType=5, stageID=52101, gameRound=1, winTeam=1)
        self.assertEqual(self.game.handle_http('PATCH', f'/fci/dungeon/{uid}/active/', active.SerializeToString(),
                                              'application/wireformat', self.auth)[0], 200)
        before = self.state()
        profiles = []
        original = self.modes._grant_player_exp
        def remember_profile(profile, *args, **kwargs):
            profiles.append(profile)
            return original(profile, *args, **kwargs)
        with patch.object(self.modes, '_grant_player_exp', side_effect=remember_profile), \
             patch.object(self.modes, '_store_balance', side_effect=RuntimeError('balance receipt failed')):
            with self.assertRaisesRegex(RuntimeError, 'balance receipt failed'):
                self.game.handle_http('POST', f'/fci/activity-dungeon/{uid}/balance/', b'{}',
                                      'application/json', self.auth)
        self.assertEqual(self.state(), before)
        self.assertTrue(profiles)
        self.assertEqual((profiles[-1]['level'], profiles[-1]['resources']), (before['level'], before['resources']))
        self.assertEqual(self.game.handle_http('POST', f'/fci/activity-dungeon/{uid}/balance/', b'{}',
                                              'application/json', self.auth)[0], 200)

    def test_activity_exp_restores_only_failed_child_and_commits_outer_changes(self):
        with self.game.atomic_mutation():
            self.modes._grant_player_exp(self.profile, 1)
            before = deepcopy((self.profile['level'], self.profile['resources']))
            try:
                with self.game.atomic_mutation():
                    self.modes._grant_player_exp(self.profile, 500)
                    raise ValueError('child failed')
            except ValueError:
                pass
            self.assertEqual((self.profile['level'], self.profile['resources']), before)
        saved = self.game._profile(self.account)
        self.assertEqual((saved['level'], saved['resources']), before)
