from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from google.protobuf import json_format, message_factory
from accounts import LocalAccounts
from game import LocalGame
from local_mailbox import send_mail
from server.core.notifications import wire_game_notifications
from server.core.push_service import PushService
import test_systems


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root)
        self.game = LocalGame(root, self.accounts, unified_store_path='local.sqlite3')
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, body=None):
        return self.game.handle_http(method, path, body, authorization=self.auth)

    def test_redpoint_native_request_restart_and_aggregate(self):
        cls = message_factory.GetMessageClass(self.game.redpoints.pool.FindMessageTypeByName('ApiRedPointData'))
        body = cls(redpoint_type=46, redpoint_id=46001, redpoint_value=True).SerializeToString()
        self.assertEqual(self.call('POST', '//fci//redpoint/data', body)[0], 200)
        self.game = LocalGame(self.game.state_dir, self.accounts, unified_store_path='local.sqlite3')
        status, result = self.call('GET', '/fci/redpoint/data')
        self.assertEqual(status, 200)
        self.assertTrue(result['redpoint']['redpointType']['46']['redpointId']['46001']['redpointValue'])
        self.assertGreater(len(result['shopRedpoint']['shopInfo']), 0)
        cls = message_factory.GetMessageClass(self.game.redpoints.pool.FindMessageTypeByName('DBRedPoint'))
        json_format.ParseDict(result['redpoint'], cls())

    def test_failed_shared_transaction_rolls_back_state_and_notifications(self):
        messages = []
        class Connection:
            last_client_sequence = 0
            def send_sc(self, *args):
                messages.append(args)
        service = PushService()
        service.register(Connection(), self.account)
        wire_game_notifications(self.game, service)
        before = self.game.systems._state(self.profile)
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with self.game.atomic_mutation():
                state = self.game.systems._state(self.profile)
                state['atomicProbe'] = True
                self.game.systems._save(self.profile, state)
                send_mail(self.game, self.profile, 'Probe', 'Atomic', [{'type': 7, 'count': 1}])
                self.assertEqual(messages, [])
                raise RuntimeError('injected')
        self.assertEqual(self.game.systems._state(self.profile), before)
        self.assertEqual(messages, [])
        with self.game.atomic_mutation():
            send_mail(self.game, self.profile, 'Probe', 'Committed', [{'type': 7, 'count': 1}])
            self.assertEqual(messages, [])
        self.assertEqual([entry[1] for entry in messages], ['ScMails'])

    def test_inner_failure_rolls_back_to_savepoint_when_swallowed(self):
        with self.game.atomic_mutation():
            state = self.game.systems._state(self.profile)
            state['outerProbe'] = True
            self.game.systems._save(self.profile, state)
            try:
                with self.game.atomic_mutation():
                    nested = self.game.systems._state(self.profile)
                    nested['atomicProbe'] = True
                    self.game.systems._save(self.profile, nested)
                    raise ValueError('failed')
            except ValueError:
                pass
        committed = self.game.systems._state(self.profile)
        self.assertTrue(committed['outerProbe'])
        self.assertNotIn('atomicProbe', committed)

    def test_condition_reward_and_role_present_use_open_local_contracts(self):
        before = self.game._profile(self.account)['resources']
        status, payload = self.call('GET', '/fci/conditionreward/')
        self.assertEqual(status, 200)
        self.assertTrue(payload.has_reward_qualification)
        self.assertEqual(payload.manual_box_reward.id, 121001)
        status, claimed = self.call('POST', '/fci/conditionreward/', b'{"manualBoxIndex":0}')
        self.assertEqual(status, 200)
        self.assertGreater(len(claimed.manual_box_res_change), 0)
        status, present = self.call('GET', '/fci/RolePresent/')
        self.assertEqual(status, 200)
        # Both Welfare RolePresent panels iterate the GET result as an array.
        self.assertEqual(present, [])
        self.assertEqual(self.game._profile(self.account)['resources'], before)

