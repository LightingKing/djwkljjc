"""Missing role content must not be made obtainable before isolated acceptance."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import actors
from accounts import LocalAccounts
from game import LocalGame
from local_mailbox import send_mail
import systems
import test_systems

ROOT = Path(__file__).resolve().parents[1]
MAIN = (1503, 1505, 1506, 1511, 1515, 1516, 1601, 1607)
LIMITS = (*MAIN, 1517)


class RoleCompletionTests(unittest.TestCase):
    def test_missing_active_slots_are_complete_without_duplicate_asuna_skills(self):
        for actor_id in MAIN:
            with self.subTest(actor=actor_id):
                rows = [r for r in systems.ACTOR_SKILL_ROWS.values()
                        if r.get('actorId') == actor_id and not r.get('isPassive') and not r.get('isLink')]
                self.assertEqual({r.get('displayIndex') for r in rows if r.get('displayIndex', 0) > 0}, {1, 2, 3})
                for slot in (1, 2, 3):
                    row = next(r for r in rows if r.get('displayIndex') == slot)
                    self.assertTrue(row.get('cmdStr'))
                    self.assertTrue(row.get('costIndex'))
                    self.assertIn(row['skillId'], actors.skill_ids(actor_id))
        for skill_id, command in ((1511001, 'InputCardCmd10003'), (1511002, 'InputCardCmd10004')):
            self.assertEqual(systems.ACTOR_SKILL_ROWS[skill_id]['cmdStr'], command)

    def test_all_six_star_caps_exist_including_existing_tatsuya_variant(self):
        for actor_id in LIMITS:
            for star in range(1, 7):
                with self.subTest(actor=actor_id, star=star):
                    caps = systems.ACTOR_SKILL_LIMITS.get((actor_id, star))
                    self.assertIsNotNone(caps)
                    self.assertGreaterEqual(len(caps), 7)
                    self.assertTrue(all(c > 0 for c in caps[:3]))

    def test_overlay_preserves_original_rows_and_is_idempotent(self):
        from role_completion import apply_table_overlay
        for table in ('ActorSkill', 'ActorSkillMaxLevel', 'ActorConfig'):
            original = json.loads((ROOT / f'data/client-tables/{table}.json').read_text(encoding='utf-8'))['rows']
            snapshot = copy.deepcopy(original)
            applied = apply_table_overlay(table, original)
            self.assertEqual(original, snapshot)
            self.assertEqual(apply_table_overlay(table, applied), applied)
            self.assertGreaterEqual(len(applied), len(original))
        self.assertEqual(apply_table_overlay('UnrelatedTable', [{'id': 1, 'n': 2}]), [{'id': 1, 'n': 2}])

    def test_pending_role_content_does_not_expand_obtainability(self):
        policy = json.loads((ROOT / 'data/server-only/content-open-20261104.json').read_text(encoding='utf-8'))
        self.assertEqual(set(policy['openCharacterIds']), {1409, 2015, 1017, 1509, 2127, 2133})

    def test_local_model_changes_are_explicit_and_iriya_stays_unchanged(self):
        self.assertEqual(systems.ACTOR_CONFIG[1601]['logicRes'], 'ruri2')
        self.assertEqual(systems.ACTOR_CONFIG[1601]['prefabRes'], 'ruri2')
        self.assertEqual(systems.ACTOR_CONFIG[2115]['logicRes'], 'izaya')
        self.assertEqual(systems.ACTOR_CONFIG[2115]['prefabRes'], 'izaya')
        self.assertEqual(systems.ACTOR_CONFIG[2134]['logicRes'], 'satan')


class CompletedRoleUpgradeTests(unittest.TestCase):
    def test_real_learning_route_pays_advances_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accounts = LocalAccounts(root / 'accounts')
            game = LocalGame(root / 'game', accounts, initial_level=30)
            account, auth = test_systems.LocalSystemsTests._guest(accounts)
            profile = game._profile(account)
            state = game.systems._state(profile)
            # Isolated fixture only; public grants/shops remain closed.
            record = actors.new_record(1503, 90001)
            record['star'] = 2
            record['level'] = 15
            actors.instances(profile, state)['90001'] = record
            game.systems._save(profile, state)
            mail = send_mail(game, profile, 'isolated role skill fixture', 'test only', [{'type': 7, 'count': 10000}])
            def call(method, path, payload):
                return game.handle_http(method, path, json.dumps(payload).encode(), 'application/json', auth)
            self.assertEqual(call('PATCH', f'/fci/mail/{mail["id"]}', {'attachmentsGot': True})[0], 200)
            before = next(r['count'] for r in game._profile(account)['resources'] if r['type'] == 7)
            for expected in (2, 3):
                status, result = call('POST', '/fci/actor/90001/skilllearnning/', {'skillId': 1503001})
                self.assertEqual(status, 200, result)
                self.assertEqual(result.new_skill.level, expected)
            after = next(r['count'] for r in game._profile(account)['resources'] if r['type'] == 7)
            self.assertEqual(before - after, 700)
            game = LocalGame(root / 'game', accounts)
            status, result = call('POST', '/fci/actor/90001/skilllearnning/', {'skillId': 1503001})
            self.assertEqual(status, 200, result)
            self.assertEqual(result.new_skill.level, 4)
            self.assertEqual(call('POST', '/fci/actor/90001/skilllearnning/', {'skillId': 1003001})[0], 400)
