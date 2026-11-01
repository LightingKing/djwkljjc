"""ActorSkillDetail's repeated skilllearnning POST must upgrade and refresh UI."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.protobuf import message_factory

from accounts import LocalAccounts
from game import LocalGame
from local_mailbox import send_mail
from systems import ACTOR_STAR_PROMOTIONS
import test_systems


class NativeSkillUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / 'accounts')
        self.game = LocalGame(self.root / 'game', self.accounts, initial_level=30)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        profile = self.game._profile(self.account)
        rewards = [{"type": 7, "count": 10000}, {"type": 11, "id": 10004, "count": 100}]
        rewards.extend(ACTOR_STAR_PROMOTIONS[(1001, 2)]['starCost'])
        mail = send_mail(self.game, profile, 'skill QA', 'isolated inventory', rewards)
        self.ok('PATCH', f'/fci/mail/{mail["id"]}', {'attachmentsGot': True})

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, body=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        return self.game.handle_http(method, path, raw, 'application/wireformat' if isinstance(body, bytes)
                                     else 'application/json', self.auth)

    def ok(self, method, path, body=None):
        status, result = self.call(method, path, body)
        self.assertEqual(status, 200, result)
        return result

    def unlock_two_stars(self):
        self.ok('POST', '/fci/actor/1/levelupgrade/', {'foodRes': [{'type': 11, 'id': 10004, 'count': 20}]})
        self.ok('POST', '/fci/actor/1/starupgrade/', b'null')

    def gold(self):
        return next(row['count'] for row in self.game._profile(self.account)['resources'] if row['type'] == 7)

    def actor_update(self, result):
        updates = [row.actor for row in result.res_change if row.HasField('actor')]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0].uid, 1)
        return {row.id: row.level for row in updates[0].skills}

    def test_consecutive_native_clicks_upgrade_and_refresh_actor_without_relogin(self):
        self.unlock_two_stars()
        before = self.gold()
        for level in (2, 3):
            result = self.ok('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001002})
            self.assertEqual(result.new_skill.level, level)
            self.assertEqual(self.actor_update(result)[1001002], level)
        # Original SkillLevelup costIndex 1: level 2 costs 200, level 3 costs 500.
        self.assertEqual(before - self.gold(), 700)
        self.game = LocalGame(self.root / 'game', self.accounts)
        result = self.ok('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001002})
        self.assertEqual(self.actor_update(result)[1001002], 4)
        self.ok('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001002})
        before = self.gold()
        status, rejected = self.call('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001002})
        self.assertEqual((status, rejected['error']), (409, 'actor_skill_star_limit'))
        self.assertEqual(before, self.gold())

    def test_skill_owner_star_and_native_wire_validation_do_not_create_fake_skills(self):
        before = self.gold()
        self.assertEqual(self.call('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001002})[0], 409)
        for skill_id in (0, 2004001, 999999):
            status, error = self.call('POST', '/fci/actor/1/skilllearnning/', {'skillId': skill_id})
            self.assertEqual((status, error['error']), (400, 'actor_skill_not_found'))
        self.assertEqual(self.gold(), before)
        self.unlock_two_stars()
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName('ApiActorSkillLearnning'))
        result = self.ok('POST', '/fci/actor/1/skilllearnning/', cls(skill_id=1001002).SerializeToString())
        self.assertEqual(self.actor_update(result)[1001002], 2)

    def test_malformed_wire_request_is_rejected_without_changing_state(self):
        before = self.game.systems._state(self.game._profile(self.account))
        status, error = self.call('POST', '/fci/actor/1/skilllearnning/', b'\xff')
        self.assertEqual((status, error['error']), (400, 'invalid_skill_request'))
        self.assertEqual(self.game.systems._state(self.game._profile(self.account)), before)

    def test_failed_resource_payment_does_not_advance_skill(self):
        self.unlock_two_stars()
        self.game.economy._change_resources(self.game._profile(self.account), [{"type": 7, "count": -self.gold()}])
        before = self.ok('GET', '/fci/actor/1/').SerializeToString()
        self.assertEqual(self.call('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001002})[0], 409)
        self.assertEqual(self.ok('GET', '/fci/actor/1/').SerializeToString(), before)
        self.assertEqual(self.gold(), 0)

    def test_late_failures_roll_back_payment_and_saved_skill(self):
        self.unlock_two_stars()
        for failure in ('_items_take', '_save', '_st_actor'):
            with self.subTest(failure=failure):
                gold = self.gold()
                before = self.ok('GET', '/fci/actor/1/').SerializeToString()
                state = self.game.systems._state(self.game._profile(self.account))
                with patch.object(self.game.systems, failure, side_effect=RuntimeError('injected skill failure')):
                    with self.assertRaisesRegex(RuntimeError, 'injected skill failure'):
                        self.call('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001002})
                self.assertEqual(self.gold(), gold)
                self.assertEqual(self.ok('GET', '/fci/actor/1/').SerializeToString(), before)
                self.assertEqual(self.game.systems._state(self.game._profile(self.account)), state)

    def test_passive_skill_needs_its_unlock_star_even_when_level_cap_allows_upgrade(self):
        self.unlock_two_stars()
        gold = self.gold()
        for skill_id in (1001900, 1001901, 1001902, 1001903):
            with self.subTest(skill_id=skill_id):
                status, result = self.call('POST', '/fci/actor/1/skilllearnning/', {'skillId': skill_id})
                self.assertEqual((status, result['error']), (409, 'actor_skill_star_limit'))
        self.assertEqual(self.gold(), gold)
        profile = self.game._profile(self.account)
        mail = send_mail(self.game, profile, 'passive skill QA', 'isolated third-star materials',
                         ACTOR_STAR_PROMOTIONS[(1001, 3)]['starCost'])
        self.ok('PATCH', f'/fci/mail/{mail["id"]}', {'attachmentsGot': True})
        self.ok('POST', '/fci/actor/1/levelupgrade/', {'foodRes': [{'type': 11, 'id': 10004, 'count': 20}]})
        self.ok('POST', '/fci/actor/1/starupgrade/', b'null')
        result = self.ok('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001900})
        self.assertEqual(self.actor_update(result)[1001900], 2)
        self.assertEqual(self.call('POST', '/fci/actor/1/skilllearnning/', {'skillId': 1001901})[0], 409)

    def test_default_shadow_skill_upgrades_all_variants_with_one_payment(self):
        profile = self.game._profile(self.account)
        rewards = [{"type": 15, "id": 2007, "param": 1002, "count": 1}]
        rewards.extend(ACTOR_STAR_PROMOTIONS[(2007, 2)]['starCost'])
        mail = send_mail(self.game, profile, 'shadow skill QA', 'isolated actor and materials', rewards)
        claimed = self.ok('PATCH', f'/fci/mail/{mail["id"]}', {'attachmentsGot': True})
        uid = next(row['actor']['uid'] for row in claimed['resChange'] if row.get('actor', {}).get('id') == 2007)
        self.ok('POST', f'/fci/actor/{uid}/levelupgrade/', {'foodRes': [{'type': 11, 'id': 10004, 'count': 20}]})
        self.ok('POST', f'/fci/actor/{uid}/starupgrade/', b'null')
        for level in (2, 3):
            result = self.ok('POST', f'/fci/actor/{uid}/skilllearnning/', {'skillId': 2007001})
            actor = next(row.actor for row in result.res_change if row.HasField('actor'))
            skills = {row.id: row.level for row in actor.skills}
            self.assertEqual((skills[2007001], skills[2007002]), (level, level))
            self.assertEqual(len([row for row in result.res_change if row.HasField('base_res') and row.base_res.type == 7]), 1)
        self.game = LocalGame(self.root / 'game', self.accounts)
        actor = self.ok('GET', f'/fci/actor/{uid}/').result
        skills = {row.id: row.level for row in actor.skills}
        self.assertEqual((skills[2007001], skills[2007002]), (3, 3))
