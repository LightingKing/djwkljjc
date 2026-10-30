from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from google.protobuf import message_factory

from accounts import LocalAccounts
from game import LocalGame
from local_mailbox import send_mail
from server.core.notifications import wire_game_notifications
from server.core.push_service import PushService, encode_base_push
import test_systems


class Connection:
    last_client_sequence = 0

    def __init__(self):
        self.messages = []

    def send_sc(self, ack, name, data):
        self.messages.append((name, data))


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.now = 1793352000
        self.accounts = LocalAccounts(root / 'accounts', clock=lambda: self.now)
        self.game = LocalGame(root / 'game', self.accounts, clock=lambda: self.now)
        self.account, _ = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.connections = []
        self.services = []
        for _ in range(2):
            service, connection = PushService(), Connection()
            service.register(connection, self.account)
            wire_game_notifications(self.game, service)
            wire_game_notifications(self.game, service)
            self.services.append(service)
            self.connections.append(connection)

    def tearDown(self):
        self.temp.cleanup()

    def test_new_mail_is_descriptor_encoded_persisted_and_fanned_out_once(self):
        record = send_mail(self.game, self.profile, '月卡奖励', '奖励已送达',
                           [{'type': 5, 'id': 0, 'count': 50}, {'type': 7, 'count': 20000}])
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ScMails'))
        for connection in self.connections:
            self.assertEqual(len(connection.messages), 1)
            name, data = connection.messages[0]
            self.assertEqual(name, 'ScMails')
            message = cls.FromString(data)
            self.assertFalse(message.clear_all)
            self.assertEqual(list(message.del_mails), [])
            self.assertEqual(message.mails[0].id, record['id'])
            self.assertGreater(message.mails[0].expire_time, self.now)
            self.assertEqual(message.mails[0].title, '月卡奖励')
            self.assertEqual([r.base_res.count for r in message.mails[0].attachments], [50, 20000])
        saved = self.game.systems._state(self.profile)['mails']
        self.assertTrue(any(m['id'] == record['id'] for m in saved))

    def test_completed_achievements_refresh_and_push_each_id(self):
        self.game.achievements.notifier(self.profile, [1001, 1002])
        expected = [('BasePushMessage', encode_base_push(19)),
                    ('BasePushMessage', encode_base_push(20, [1001])),
                    ('BasePushMessage', encode_base_push(20, [1002]))]
        for connection in self.connections:
            self.assertEqual(connection.messages, expected)

    def test_base_push_preserves_parameters_and_two_argument_callers(self):
        self.game.base_push_notifier(self.profile, 3, [80001])
        self.game.base_push_notifier(self.profile, 49, [201, 202])
        self.game.base_push_notifier(self.profile, 2)
        for connection in self.connections:
            self.assertEqual(connection.messages, [
                ('BasePushMessage', encode_base_push(3, [80001])),
                ('BasePushMessage', encode_base_push(49, [201, 202])),
                ('BasePushMessage', encode_base_push(2))])

    def test_offline_mail_remains_available_on_reconnect(self):
        for service, connection in zip(self.services, self.connections):
            service.unregister(connection)
        record = send_mail(self.game, self.profile, 'offline', 'saved', [])
        status, payload = self.game.mailbox.handle_http('GET', '/fci/mail/', None, self.profile)
        self.assertEqual(status, 200)
        self.assertIn(record['id'], [m['id'] for m in payload['mails']])
        self.assertTrue(all(not c.messages for c in self.connections))


if __name__ == '__main__':
    unittest.main()
