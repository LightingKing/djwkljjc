from __future__ import annotations

from datetime import datetime, timezone
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
from test_task_push import _sc_tasks_change_class
import test_systems


def utc(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


class ResetPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.now = utc('2026-10-30T21:59:59')
        self.accounts = LocalAccounts(root / 'accounts', clock=lambda: self.now)
        self.game = LocalGame(root / 'game', self.accounts, clock=lambda: self.now)
        self.account, _ = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.services, self.connections = [], []
        for _ in range(2):
            service, connection = PushService(), Connection()
            service.register(connection, self.account)
            wire_game_notifications(self.game, service)
            self.services.append(service)
            self.connections.append(connection)
        self.scheduler = ResetScheduler(self.game, self.services)
        self.assertEqual(self.scheduler.tick(), 1)
        self.clear()

    def tearDown(self):
        self.scheduler.stop()
        self.temp.cleanup()

    def clear(self):
        for connection in self.connections:
            connection.messages.clear()

    def push_types(self, connection=0):
        return [_one(_parse_fields(data), 1, 0, 0)
                for name, data in self.connections[connection].messages if name == 'BasePushMessage']

    def test_six_oclock_settles_state_and_refreshes_both_listeners_once(self):
        state = self.game.activity_modes._state(self.profile)
        state['tower'].update(locked=True, towerFloor=5)
        state['material']['records'] = {'52101': {'doneTimes': 7}}
        self.game.activity_modes._save(self.profile, state)
        self.now += 1
        self.assertEqual(self.scheduler.tick(), 1)
        stored = self.game.activity_modes._state(self.profile)
        self.assertFalse(stored['tower']['locked'])
        self.assertEqual(stored['tower']['towerFloor'], 0)
        self.assertEqual(stored['material']['records']['52101']['doneTimes'], 0)
        for i, connection in enumerate(self.connections):
            self.assertTrue({21, 42, 71, 76, 1}.issubset(set(self.push_types(i))))
            self.assertNotIn(32, self.push_types(i))
            task_frames = [data for name, data in connection.messages if name == 'ScTasksChange']
            self.assertEqual(len(task_frames), 1)
            payload = _sc_tasks_change_class().FromString(task_frames[0])
            self.assertFalse(payload.clearAll)
            self.assertEqual(payload.activePoint, 0)
        self.clear()
        self.assertEqual(self.scheduler.tick(), 0)
        self.assertTrue(all(not c.messages for c in self.connections))

    def test_midnight_signin_refresh_does_not_reset_six_oclock_state(self):
        self.now = utc('2026-10-31T15:59:59')
        self.scheduler.tick()
        self.clear()
        self.now += 1
        self.assertEqual(self.scheduler.tick(), 1)
        for i in range(2):
            self.assertEqual(self.push_types(i), [32])

    def test_dispatch_high_water_survives_restart_and_clock_rollback(self):
        self.now += 1
        self.scheduler.tick()
        self.clear()
        restarted = ResetScheduler(self.game, self.services)
        self.assertEqual(restarted.tick(), 0)
        self.now -= 86400
        self.assertEqual(restarted.tick(), 0)
        self.now += 86400
        self.assertEqual(restarted.tick(), 0)
        self.assertTrue(all(not c.messages for c in self.connections))

    def test_monday_weekly_refresh_and_offline_catchup(self):
        self.now = utc('2026-11-01T21:59:59')
        self.scheduler.tick()
        self.clear()
        for service, connection in zip(self.services, self.connections):
            service.unregister(connection)
        self.now += 1
        self.assertEqual(self.scheduler.tick(), 0)
        self.services[0].register(self.connections[0], self.account)
        self.assertEqual(self.scheduler.tick(), 1)
        self.assertIn(88, self.push_types())
        self.assertIn(74, self.push_types())
        self.assertEqual(self.connections[1].messages, [])

    def test_online_world_messages_run_once_a_minute_for_connected_accounts(self):
        with patch.object(self.game.social, 'tick', wraps=self.game.social.tick) as social_tick:
            for _ in range(59):
                self.now += 1
                self.scheduler.tick()
            self.assertEqual(social_tick.call_count, 0)
            self.clear()
            self.now += 1
            self.scheduler.tick()
            self.assertEqual(social_tick.call_count, 1)
            for connection in self.connections:
                self.assertEqual([name for name, _ in connection.messages], ['WorldMessage'])
            self.clear()
            restarted = ResetScheduler(self.game, self.services)
            restarted.tick()
            self.assertTrue(all(not connection.messages for connection in self.connections))
            self.now -= 60
            restarted.tick()
            self.assertTrue(all(not connection.messages for connection in self.connections))
            for service, connection in zip(self.services, self.connections):
                service.unregister(connection)
            self.now += 120
            before = social_tick.call_count
            self.scheduler.tick()
            self.assertEqual(social_tick.call_count, before)
            for service, connection in zip(self.services, self.connections):
                service.register(connection, self.account)
            self.scheduler.tick()
            self.assertEqual(social_tick.call_count, before + 1)
            for connection in self.connections:
                self.assertEqual([name for name, _ in connection.messages], ['WorldMessage'])


if __name__ == '__main__':
    unittest.main()
