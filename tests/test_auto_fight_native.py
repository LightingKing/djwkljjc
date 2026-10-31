"""DungeonUtil.SendAutoFightSetting sends flags, including mode bitmasks."""
import json
import unittest

from game import LocalGame
import test_batch1_native_contract as native_http


class AutoFightNativeTests(unittest.TestCase):
    setUp = native_http.NativeHttpTests.setUp
    tearDown = native_http.NativeHttpTests.tearDown
    request = native_http.NativeHttpTests.request

    def test_native_json_bitmask_survives_restart(self):
        # FightRound.SetAutoFight stores 1 << DungeonType.AsyncPvp in key 1.
        # Key 2 stores the acceleration bitmask; other keys toggle skills.
        flags = {'1': 1 << 3, '2': (1 << 3) | (1 << 1), '3': 1, '1001001': 0}
        status, saved = self.request('POST', '/fci/auto-fight-conf',
                                    json.dumps({'configureSkills': flags}).encode())
        self.assertEqual((status, saved), (200, {'banedSkills': flags}))
        restarted = LocalGame(self.root / 'game', self.accounts)
        self.assertEqual(restarted.handle_http('GET', '/fci/auto-fight-conf/', authorization=self.auth),
                         (200, {'banedSkills': flags}))

    def test_native_protobuf_uint32_and_empty_configuration(self):
        message = self.game._types['ApiAutoFightConf']()
        message.configure_skills['1'] = 4294967295
        status, saved = self.request('POST', '/fci/auto-fight-conf/', message.SerializeToString(),
                                    'application/wireformat')
        self.assertEqual((status, saved), (200, {'banedSkills': {'1': 4294967295}}))
        self.assertEqual(self.request('POST', '/fci/auto-fight-conf/', b'{"configureSkills":{}}'),
                         (200, {'banedSkills': {}}))

    def test_invalid_payload_not_internal_error_or_state_change(self):
        valid = b'{"configureSkills":{"1":8}}'
        self.assertEqual(self.request('POST', '/fci/auto-fight-conf/', valid)[0], 200)
        before = self.game._profile(self.account)['auto_fight']
        for body in (b'{"configureSkills":{"1":-1}}', b'{"configureSkills":{"1":4294967296}}',
                     b'{"configureSkills":{"1":1.5}}', b'null', b'\xff'):
            with self.subTest(body=body):
                status, payload = self.request('POST', '/fci/auto-fight-conf/', body)
                self.assertEqual(status, 509, payload)
                self.assertIn('code', payload['notify'])
                self.assertEqual(self.game._profile(self.account)['auto_fight'], before)
