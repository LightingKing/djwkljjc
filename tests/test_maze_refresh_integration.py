"""Login/bootstrap and online-reset integration for native Maze ticket bindings."""
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from accounts import LocalAccounts
from game import LocalGame
from push import _parse_fields, _one
from server.core.notifications import wire_game_notifications
from server.core.push_service import PushService
from server.core.reset_scheduler import ResetScheduler
from test_notifications import Connection
from test_welfare_maze_local import guest


def epoch(text):
    return int(datetime.fromisoformat(text).timestamp())


class MazeRefreshIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = epoch('2026-11-02T05:59:59+08:00')
        self.accounts = LocalAccounts(self.root / 'accounts', clock=lambda: self.now)
        self.game = LocalGame(self.root / 'game', self.accounts, initial_level=60, clock=lambda: self.now)
        self.account, self.auth = guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.services, self.connections = [], []
        self.scheduler = None

    def tearDown(self):
        if self.scheduler:
            self.scheduler.stop()
        self.temp.cleanup()

    def login_items(self, game=None, account=None):
        game, account = game or self.game, account or self.account
        messages = dict(game.login_messages(account))
        package = game._types['DBGamePlayer.ItemPackage'].FromString(messages['DBGamePlayer.ItemPackage'])
        self.assertEqual(len({row.id for row in package.items}), len(package.items))
        return {row.id: row.count for row in package.items}

    def counts(self):
        items = self.game.systems._state(self.profile)['items']
        return tuple(items.get(str(item_id), {}).get('count', 0) for item_id in (80007, 80008))

    def enter(self, maze_id):
        status, result = self.game.handle_http('POST', '/fci/Maze/Enter/',
            json.dumps({'mazeId': maze_id, 'mazeFloor': 1}).encode(), authorization=self.auth)
        self.assertEqual(status, 200, result)
        return result

    def connect(self):
        for _ in range(2):
            service, connection = PushService(), Connection()
            service.register(connection, self.account)
            wire_game_notifications(self.game, service)
            self.services.append(service)
            self.connections.append(connection)
        self.scheduler = ResetScheduler(self.game, self.services)

    def clear(self):
        for connection in self.connections:
            connection.messages.clear()

    def maze_pushes(self, connection):
        result = []
        for name, raw in connection.messages:
            if name != 'BasePushMessage':
                continue
            fields = _parse_fields(raw)
            if _one(fields, 1, 0, 0) != 3:
                continue
            params = [int(value.decode()) for wire_type, value in fields.get(2, []) if wire_type == 2]
            selected = [value for value in params if value in (80007, 80008)]
            if selected:
                result.append(selected)
        return result

    def test_login_supplies_before_item_package_and_keeps_zero_bindings_at_all_levels(self):
        for level, expected in ((1, (0, 0)), (29, (0, 0)), (30, (1, 0)), (59, (1, 0)), (60, (1, 3))):
            with self.subTest(level=level):
                game = LocalGame(self.root / f'level-{level}', self.accounts, initial_level=level, clock=lambda: self.now)
                account, _ = guest(self.accounts)
                first = self.login_items(game, account)
                self.assertEqual((first[80007], first[80008]), expected)
                again = self.login_items(game, account)
                self.assertEqual((again[80007], again[80008]), expected)
                status, summary = game.maze.handle_http('GET', '/fci/Maze/Summary/', None, game._profile(account))
                self.assertEqual((status, summary.maze_id), (200, 0))
                self.assertEqual(self.login_items(game, account), again)

    def test_native_entry_spends_tickets_and_relogin_always_sends_zero_count(self):
        self.assertEqual((self.login_items()[80007], self.login_items()[80008]), (1, 3))
        entered = self.enter(4)
        self.assertEqual(entered.res_change[0].base_res.id, 80008)
        self.assertEqual(entered.res_change[0].base_res.count_delta, -3)
        self.assertEqual(self.login_items()[80008], 0)
        self.assertEqual(self.counts(), (1, 0))
        self.now += 1  # Monday 06:00 supplies daily and weekly tickets.
        package = self.login_items()
        self.assertEqual((package[80007], package[80008]), (2, 3))
        self.assertEqual(self.login_items(), package)
        self.game = LocalGame(self.root / 'game', self.accounts, clock=lambda: self.now)
        self.assertEqual(self.login_items(), package)
        self.now += 6 * 3600
        self.assertEqual((self.login_items()[80007], self.login_items()[80008]), (2, 3))

    def test_online_six_am_and_weekly_grants_emit_one_native_item_push_per_listener(self):
        self.login_items()
        self.connect()
        self.assertEqual(self.scheduler.tick(), 1)
        self.enter(4)
        self.clear()
        self.now += 1
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertEqual(self.counts(), (2, 3))
        for connection in self.connections:
            self.assertEqual(self.maze_pushes(connection), [[80007, 80008]])
        self.clear()
        self.assertEqual(self.scheduler.tick(), 0)
        self.assertTrue(all(not c.messages for c in self.connections))
        self.login_items()
        self.assertTrue(all(not self.maze_pushes(c) for c in self.connections))
        # Sign-in midnight must not grant the next day's Maze tickets early.
        self.now = epoch('2026-11-03T00:00:00+08:00')
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertEqual(self.counts(), (2, 3))
        self.assertTrue(all(not self.maze_pushes(c) for c in self.connections))
        self.clear()
        self.now = epoch('2026-11-03T06:00:00+08:00')
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertEqual(self.counts(), (3, 3))
        for connection in self.connections:
            self.assertEqual(self.maze_pushes(connection), [[80007]])

    def test_offline_catchup_caps_and_persisted_dispatch_does_not_repeat_after_restart(self):
        self.login_items()
        self.connect()
        self.scheduler.tick()
        self.enter(4)
        for service, connection in zip(self.services, self.connections):
            service.unregister(connection)
        self.clear()
        self.now += 15 * 86400
        self.assertEqual(self.scheduler.tick(), 0)
        self.assertEqual(self.counts(), (1, 0))
        items = self.login_items()
        self.assertEqual((items[80007], items[80008]), (3, 3))
        self.assertTrue(all(not c.messages for c in self.connections))
        self.services[0].register(self.connections[0], self.account)
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertFalse(self.maze_pushes(self.connections[0]))
        self.clear()
        restarted = ResetScheduler(self.game, self.services)
        self.assertEqual(restarted.tick(), 0)
        self.now -= 86400
        self.assertEqual(restarted.tick(), 0)
        self.assertEqual(self.counts(), (3, 3))
        self.assertFalse(self.maze_pushes(self.connections[0]))

    def test_failed_online_maze_marker_write_retries_without_duplicate_ticket_or_push(self):
        self.login_items()
        self.connect()
        self.scheduler.tick()
        self.enter(4)
        self.clear()
        with closing(self.game._connect()) as connection:
            before_dispatch = tuple(connection.execute('SELECT * FROM local_reset_dispatch').fetchone())
        before_supply = self.game.profile_features._state(self.profile)['maze']['supply']
        self.now += 1
        with patch.object(self.game.maze, '_save', side_effect=RuntimeError('injected maze marker failure')):
            with self.assertLogs('server.core.reset_scheduler', level='ERROR'):
                self.assertEqual(self.scheduler.tick(), 0)
        self.assertEqual(self.scheduler.last_error, 'reset_failed')
        self.assertEqual(self.counts(), (1, 0))
        self.assertEqual(self.game.profile_features._state(self.profile)['maze']['supply'], before_supply)
        with closing(self.game._connect()) as connection:
            self.assertEqual(tuple(connection.execute('SELECT * FROM local_reset_dispatch').fetchone()), before_dispatch)
        self.assertTrue(all(not self.maze_pushes(c) for c in self.connections))
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertEqual(self.counts(), (2, 3))
        for connection in self.connections:
            self.assertEqual(self.maze_pushes(connection), [[80007, 80008]])

    def test_other_ticket_refresh_failure_does_not_skip_maze_bootstrap(self):
        with patch.object(self.game.activity_modes, 'refresh_daily_items', side_effect=RuntimeError('unrelated mode failure')):
            package = self.login_items()
        self.assertEqual((package[80007], package[80008]), (1, 3))

    def test_failed_maze_login_refresh_sends_safe_zero_fields_and_retries(self):
        with patch.object(self.game.maze, '_save', side_effect=RuntimeError('injected maze marker failure')):
            with self.assertLogs('game', level='ERROR'):
                package = self.login_items()
        self.assertEqual((package[80007], package[80008]), (0, 0))
        self.assertEqual(self.counts(), (0, 0))
        package = self.login_items()
        self.assertEqual((package[80007], package[80008]), (1, 3))


if __name__ == '__main__':
    unittest.main()
