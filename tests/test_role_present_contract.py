"""Native welfare calls: GET list, POST RolePresent/<id> with JSON null."""
import json
import threading
import unittest
from unittest.mock import patch

from grants import grant_rewards
from local_mailbox import send_mail
from profile_features import table_rows
import test_batch1_native_contract as native_http


class RolePresentContractTests(unittest.TestCase):
    setUp = native_http.NativeHttpTests.setUp
    tearDown = native_http.NativeHttpTests.tearDown
    request = native_http.NativeHttpTests.request
    def prepare_present(self):
        # Seed consumables only; earn eligibility via the actual gift route.
        role_id = self.game.profile_features.role_notepads(self.profile)[0]['roleId']
        grant_rewards(self.game, self.profile, [{'type': 11, 'id': 53003, 'count': 200}])
        status, gift = self.request('PATCH', f'/fci/FavourGift/{role_id}',
                                    b'{"usedItemList":[{"type":11,"id":53003,"count":200}]}')
        self.assertEqual(status, 200, gift)
        self.assertGreaterEqual(gift['roleNotepad']['favourLevel'], 9)
        day = self.game.reset_policy.day_index(self.game._now())
        while (int(self.account['accountId']) * 31 + day * 17 + role_id * 13) % 100 >= 70:
            day += 1
        return role_id, self.game.reset_policy.day_start_from_index(day) + 1

    def test_native_list_path_id_null_claim_and_relogin(self):
        role_id, now = self.prepare_present()
        with patch.object(self.game, '_now', return_value=now), patch.object(self.game.profile_features, '_clock', return_value=now):
            status, pending = self.request('GET', '/fci/RolePresent/')
            self.assertEqual(status, 200)
            self.assertIsInstance(pending, list)
            chosen = next(row for row in pending if row['roleId'] == role_id)
            self.assertIsInstance(chosen['presentId'], int)
            status, red = self.request('GET', '/fci/redpoint/data')
            self.assertEqual(status, 200)
            self.assertEqual(red['rolePresentRedpoint']['presents'], pending)
            self.assertIn(chosen['rewards'][0]['id'], {r['id'] for r in table_rows('ItemInfo')})
            path = '/fci/RolePresent/' + str(chosen['presentId'])
            status, result = self.request('POST', path, b'null')
            self.assertEqual(status, 200, result)
            self.assertTrue(result['resChange'])
            item = str(chosen['rewards'][0]['id'])
            after = self.game.systems._state(self.game._profile(self.account))['items'][item]
            self.assertEqual(self.request('POST', path, b'null')[0], 509)
            self.game.login_messages(self.account)
            self.assertNotIn(role_id, [p['roleId'] for p in self.request('GET', '/fci/RolePresent/')[1]])
            self.assertEqual(self.game.systems._state(self.game._profile(self.account))['items'][item], after)

    def test_invalid_path_and_body_cannot_choose_a_present(self):
        role_id, now = self.prepare_present()
        with patch.object(self.game, '_now', return_value=now), patch.object(self.game.profile_features, '_clock', return_value=now):
            before = self.game.systems._state(self.game._profile(self.account))['items']
            for path in ('/fci/RolePresent/', '/fci/RolePresent/bad', '/fci/RolePresent/999999'):
                self.assertEqual(self.request('POST', path, json.dumps({'roleId': role_id}).encode())[0], 509)
            self.assertEqual(self.game.systems._state(self.game._profile(self.account))['items'], before)

    def test_daily_account_cap_is_three_present_claims(self):
        rows = [
            {"presentId": role_id, "roleId": role_id, "presentType": 1,
             "rewards": [{"type": 11, "id": 20001, "count": 5}]}
            for role_id in (1001, 1002, 1003, 1004)
        ]
        with patch.object(self.game.profile_features, 'pending_role_presents', return_value=rows):
            statuses = [self.request('POST', f'/fci/RolePresent/{role_id}', b'null')[0]
                        for role_id in (1001, 1002, 1003, 1004)]
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3], 509)


class LoginAndMatchContractTests(unittest.TestCase):
    setUp = native_http.NativeHttpTests.setUp
    tearDown = native_http.NativeHttpTests.tearDown
    request = native_http.NativeHttpTests.request
    def test_login_cannot_overwrite_concurrent_native_mail_claim(self):
        self.game.login_messages(self.account)
        before_count = self.game.systems._state(self.game._profile(self.account))['items']['80001']['count']
        mail = send_mail(self.game, self.profile, 'QA', 'QA', [{'type': 11, 'id': 80001, 'count': 3}],
                         sender_name='QA', now=self.game._now())
        entered, release, attempted, claimed = (threading.Event() for _ in range(4))
        failures, results = [], []
        original = self.game.systems.login_actor_extras
        def checkpoint(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test checkpoint timed out')
            original(*args)
            return True
        def login():
            try:
                self.game.login_messages(self.account)
            except BaseException as exc:
                failures.append(exc)
        def claim():
            attempted.set()
            try:
                results.append(self.request('PATCH', f'/fci/mail/{mail["id"]}', b'{"attachmentsGot":true}'))
            except BaseException as exc:
                failures.append(exc)
            finally:
                claimed.set()
        with patch.object(self.game.systems, 'login_actor_extras', side_effect=checkpoint):
            login_thread = threading.Thread(target=login)
            claim_thread = threading.Thread(target=claim)
            login_thread.start()
            try:
                self.assertTrue(entered.wait(5))
                claim_thread.start()
                self.assertTrue(attempted.wait(2))
                self.assertFalse(claimed.wait(0.3), 'claim must wait for the login state write')
            finally:
                release.set()
                login_thread.join(5)
                if claim_thread.ident:
                    claim_thread.join(5)
        self.assertFalse(failures)
        self.assertEqual(results[0][0], 200)
        state = self.game.systems._state(self.game._profile(self.account))
        self.assertEqual(state['items']['80001']['count'], before_count + 3)
        self.assertTrue(next(m for m in state['mails'] if m['id'] == mail['id'])['attachmentsGot'])
        self.request('PATCH', f'/fci/mail/{mail["id"]}', b'{"attachmentsGot":true}')
        self.assertEqual(self.game.systems._state(self.game._profile(self.account))['items']['80001']['count'], before_count + 3)

    def test_match_errors_use_native_notify(self):
        for status, expected in ((409, 509), (500, 507)):
            with patch.object(self.game, 'handle_http', return_value=(status, {'error': 'match_not_available'})):
                wire_status, body = self.request('POST', '/match/BurstLinkPVPMatch', b'null')
            self.assertEqual(wire_status, expected)
            self.assertIn('code', body['notify'])
            self.assertNotIn('error', body)
