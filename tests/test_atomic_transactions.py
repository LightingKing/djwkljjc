"""Nested SQLite contexts must roll back the same state and notifications."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from accounts import LocalAccounts
from game import LocalGame
from server.core.atomic import AtomicMutations
import test_systems


class AtomicScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'atomic.sqlite3'
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE probes(value TEXT)')
        self.game = SimpleNamespace(db_path=self.db, _lock=threading.RLock())
        self.atomic = AtomicMutations(self.game)

    def tearDown(self):
        self.temp.cleanup()

    def values(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return [row[0] for row in conn.execute('SELECT value FROM probes ORDER BY rowid')]

    def put(self, value):
        self.atomic.borrowed().execute('INSERT INTO probes(value) VALUES(?)', (value,))

    def test_caught_connection_context_failure_only_rolls_back_its_scope(self):
        calls = []
        with self.atomic.transaction():
            self.put('outer-before')
            self.atomic.after_commit(lambda: calls.append('outer-before'))
            try:
                with closing(self.atomic.borrowed()) as conn, conn:
                    self.put('failed-context')
                    self.atomic.after_commit(lambda: calls.append('failed-context'))
                    raise ValueError('local failure')
            except ValueError:
                pass
            self.put('outer-after')
            self.atomic.after_commit(lambda: calls.append('outer-after'))
            self.assertEqual(calls, [])
        self.assertEqual(self.values(), ['outer-before', 'outer-after'])
        self.assertEqual(calls, ['outer-before', 'outer-after'])

    def test_failed_child_discards_successful_grandchild_callbacks(self):
        calls = []
        with self.atomic.transaction():
            self.put('outer')
            self.atomic.after_commit(lambda: calls.append('outer'))
            try:
                with self.atomic.transaction():
                    self.put('child')
                    self.atomic.after_commit(lambda: calls.append('child'))
                    with self.atomic.transaction():
                        self.put('grandchild')
                        self.atomic.after_commit(lambda: calls.append('grandchild'))
                    raise ValueError('child failed')
            except ValueError:
                pass
            with self.atomic.transaction():
                self.put('surviving-child')
                self.atomic.after_commit(lambda: calls.append('surviving-child'))
        self.assertEqual(self.values(), ['outer', 'surviving-child'])
        self.assertEqual(calls, ['outer', 'surviving-child'])

    def test_same_borrowed_handle_can_nest_and_catch_its_inner_failure(self):
        with self.atomic.transaction():
            conn = self.atomic.borrowed()
            with conn:
                self.put('connection-outer')
                try:
                    with conn:
                        self.put('connection-inner')
                        raise LookupError('failed inner')
                except LookupError:
                    pass
                self.put('connection-after')
        self.assertEqual(self.values(), ['connection-outer', 'connection-after'])

    def test_outer_failure_discards_released_savepoints_and_callbacks(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'outer failed'):
            with self.atomic.transaction():
                with self.atomic.borrowed():
                    with self.atomic.transaction():
                        self.put('released-child')
                        self.atomic.after_commit(lambda: calls.append('released-child'))
                raise RuntimeError('outer failed')
        self.assertEqual(self.values(), [])
        self.assertEqual(calls, [])

    def test_explicit_rollback_still_aborts_the_owner_transaction(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'transaction rolled back'):
            with self.atomic.transaction():
                self.put('outer')
                self.atomic.after_commit(lambda: calls.append('outer'))
                with self.atomic.transaction():
                    self.atomic.borrowed().rollback()
        self.assertEqual(self.values(), [])
        self.assertEqual(calls, [])


class ProfileRollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root)
        self.game = LocalGame(root, self.accounts, unified_store_path='local.sqlite3')
        self.account, _ = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def financial_state(self, profile=None):
        profile = self.profile if profile is None else profile
        return deepcopy({key: profile[key] for key in ('level', 'resources')})

    def assert_state(self, expected):
        self.assertEqual(self.financial_state(), expected)
        self.assertEqual(self.financial_state(self.game._profile(self.account)), expected)

    def test_caught_child_restores_profile_then_outer_changes_commit(self):
        callbacks = []
        with self.game.atomic_mutation():
            self.assertTrue(self.game.economy._change_resources(self.profile, [{'type': 7, 'count': 100}])[0])
            outer = self.financial_state()
            try:
                with self.game.atomic_mutation():
                    with closing(self.game._connect()) as conn, conn:
                        self.assertTrue(self.game.economy._change_resources(self.profile, [{'type': 7, 'count': -80}])[0])
                        self.game.dungeons._grant_exp(self.profile, 50)
                        self.assertTrue(self.game.dungeons._resource_changes(self.profile, [{'type': 6, 'count': -1}])[0])
                        self.game.after_commit(lambda: callbacks.append('failed'))
                        raise ValueError('failed child')
            except ValueError:
                pass
            self.assert_state(outer)
            # Use the same dict after rollback; stale levels/resources must not
            # leak back through the next otherwise-successful dungeon update.
            self.assertTrue(self.game.dungeons._resource_changes(self.profile, [{'type': 7, 'count': 20}])[0])
            self.game.dungeons._grant_exp(self.profile, 1)
            committed = self.financial_state()
            self.game.after_commit(lambda: callbacks.append('outer'))
        self.assert_state(committed)
        self.assertEqual(self.profile['level'], 2)
        self.assertEqual(callbacks, ['outer'])

    def test_outer_rollback_restores_successful_nested_resource_and_exp_changes(self):
        before = self.financial_state()
        with self.assertRaisesRegex(ValueError, 'failed outer'):
            with self.game.atomic_mutation():
                self.assertTrue(self.game.economy._change_resources(self.profile, [{'type': 7, 'count': 100}])[0])
                with self.game.atomic_mutation():
                    self.game.dungeons._grant_exp(self.profile, 50)
                    self.assertTrue(self.game.dungeons._resource_changes(self.profile, [{'type': 6, 'count': -1}])[0])
                raise ValueError('failed outer')
        self.assert_state(before)

    def test_committed_changes_remain_in_the_profile_after_later_transaction_failure(self):
        with self.game.atomic_mutation():
            self.assertTrue(self.game.economy._change_resources(self.profile, [{'type': 7, 'count': 100}])[0])
            self.game.dungeons._grant_exp(self.profile, 1)
        committed = self.financial_state()
        with self.assertRaisesRegex(ValueError, 'next transaction'):
            with self.game.atomic_mutation():
                self.game.dungeons._grant_exp(self.profile, 50)
                raise ValueError('next transaction')
        self.assert_state(committed)

    def test_exp_write_failure_does_not_change_profile_without_an_outer_transaction(self):
        before = self.financial_state()
        with patch.object(self.game, '_connect', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                self.game.dungeons._grant_exp(self.profile, 50)
        self.assert_state(before)
