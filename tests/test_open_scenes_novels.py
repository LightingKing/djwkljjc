"""Original scene task gates and durable local collection delivery."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from game import LocalGame
from grants import grant_rewards
from tasks import LocalTasks
from achievements import LocalAchievements, NOVELS
import test_actor_remediation as fixtures
from external_assets import external_path, require_file
from lupa.lua53 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
SCENE_TASK_IDS = {31001, 31005, 31007, 31008, 31009}
MAIL_SCENE_IDS = {31002, 31003, 31004, 31006, 31010, 31011, 31012, 31015, 31016}
FUTURE_SCENE_IDS = {31003, 31004, 31006, 31010, 31011, 31012}


class OpenScenesNovelsTests(unittest.TestCase):
    setUp = fixtures.ActorRemediationTests.setUp
    tearDown = fixtures.ActorRemediationTests.tearDown
    call, state, save, add = (fixtures.ActorRemediationTests.call, fixtures.ActorRemediationTests.state,
                              fixtures.ActorRemediationTests.save, fixtures.ActorRemediationTests.add)

    def rows(self):
        status, payload = self.game.tasks.handle_http('GET', '/fci/task', None, self.profile)
        self.assertEqual(status, 200)
        return {r['id']: r for r in payload['tasks']}

    def scene_mails(self):
        return [m for m in self.state().get('mails', [])
                if {a['id'] for a in m['attachments']} == MAIL_SCENE_IDS]

    def test_original_scene_tasks_visible_with_real_progress_and_persist(self):
        now = [1793750400]
        self.game.tasks = LocalTasks(self.game, clock=lambda: now[0])
        rows = self.rows()
        self.assertTrue(SCENE_TASK_IDS <= set(rows))
        self.assertTrue(all(not rows[i]['isDone'] and rows[i]['progress'] == 0 for i in SCENE_TASK_IDS))
        for _ in range(2):
            self.game.tasks.on_battle_win(self.profile, 44)
        self.game.tasks.on_battle_win(self.profile, 999999)
        self.game.tasks.record(self.profile, 35, amount=90)
        rows = self.rows()
        self.assertEqual(rows[31001]['progress'], 2)
        self.assertEqual(rows[31008]['progress'], 90)
        self.assertFalse(rows[31008]['isDone'])
        self.assertEqual(rows[31005]['progress'], 0)
        now[0] += 8 * 86400
        self.game.tasks = LocalTasks(self.game, clock=lambda: now[0])
        rows = self.rows()
        self.assertEqual(rows[31001]['progress'], 2)
        self.assertEqual(rows[31008]['progress'], 90)
        self.game.tasks.record(self.profile, 35)
        self.assertTrue(self.rows()[31008]['isDone'])
        status, listed = self.game.tasks.handle_http('GET', '/fci/sceneTasks', None, self.profile)
        self.assertEqual(status, 200)
        self.assertEqual({r['id'] for r in listed}, SCENE_TASK_IDS)

    def test_scene_tasks_import_durable_original_history_without_completing_early(self):
        state = self.game.tasks._state(self.profile)
        state['dungeonWins'].update({'44': 910, '4001': 90})
        self.game.tasks._save(self.profile, state)
        rows = self.rows()
        self.assertTrue(rows[31001]['isDone'])
        self.assertEqual(rows[31008]['progress'], 90)
        self.assertFalse(rows[31008]['isDone'])

    def test_scene_task_original_910_threshold_and_claim_rollback(self):
        self.rows()
        state = self.game.tasks._state(self.profile)
        self.assertIn('31001', state['tasks'])
        # An imported, durable clear ledger is the original recordBeforeGot source.
        state['tasks']['31001'].update(progress=909, isDone=False)
        state['dungeonWins']['44'] = 909
        self.game.tasks._save(self.profile, state)
        self.assertEqual(self.game.tasks.handle_http('POST', '/fci/task/31001/reward', None, self.profile)[0], 409)
        self.game.tasks.on_battle_win(self.profile, 44)
        self.assertTrue(self.rows()[31001]['isDone'])
        with patch('tasks.grant_rewards', side_effect=RuntimeError('injected grant failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                self.game.tasks.handle_http('POST', '/fci/task/31001/reward', None, self.profile)
        self.assertFalse(self.rows()[31001]['rewardGot'])
        status, reward = self.game.tasks.handle_http('POST', '/fci/task/31001/reward', None, self.profile)
        self.assertEqual(status, 200, reward)
        self.assertEqual(self.state()['items']['31001']['count'], 1)
        self.assertEqual(self.game.tasks.handle_http('POST', '/fci/task/31001/reward', None, self.profile)[0], 409)
        self.assertEqual(self.state()['items']['31001']['count'], 1)

    def test_scene_mail_once_concurrently_restarts_and_deleted_mail(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.game.tasks.on_login(self.profile), range(2)))
        mails = self.scene_mails()
        self.assertEqual(len(mails), 1)
        self.assertTrue(all(a['type'] == 11 and a['count'] == 1 for a in mails[0]['attachments']))
        self.assertNotIn(31014, {a['id'] for a in mails[0]['attachments']})
        self.assertNotIn('31015', self.state().get('items', {}))
        status, reward = self.call('PATCH', f"/fci/mail/{mails[0]['id']}", {'attachmentsGot': True})
        self.assertEqual(status, 200, reward)
        self.assertTrue(all(self.state()['items'][str(i)]['count'] == 1 for i in MAIL_SCENE_IDS))
        status, replay = self.call('PATCH', f"/fci/mail/{mails[0]['id']}", {'attachmentsGot': True})
        self.assertEqual(status, 200)
        self.assertEqual(replay['resChange'], [])
        self.call('DELETE', f"/fci/mail/{mails[0]['id']}")
        self.game = LocalGame(self.root / 'game', self.accounts)
        self.game.tasks.on_login(self.profile)
        self.rows()
        self.assertEqual(self.scene_mails(), [])
        self.assertTrue(all(self.state()['items'][str(i)]['count'] == 1 for i in MAIL_SCENE_IDS))

    def test_scene_delivery_failure_rolls_back_mail_and_marker(self):
        original = self.game.systems._save
        calls = [0]
        def fail_marker(profile, state):
            calls[0] += 1
            original(profile, state)
            if calls[0] == 2:
                raise RuntimeError('delivery marker failure')
        with patch.object(self.game.systems, '_save', side_effect=fail_marker):
            with self.assertRaisesRegex(RuntimeError, 'marker failure'):
                self.game.tasks.on_login(self.profile)
        self.assertEqual(self.scene_mails(), [])
        self.game.tasks.on_login(self.profile)
        self.assertEqual(len(self.scene_mails()), 1)

    def test_background_selection_checks_default_ownership_and_persists(self):
        for scene in (0, 1, 3, 31013):
            self.assertEqual(self.call('POST', '/fci/home-back-ground', {'homeBackGround': scene})[0], 200)
        before = self.game.profile_features._state(self.profile)['homeBackground']
        for scene in (31001, 31014, 31015, 999999, -1):
            status, error = self.call('POST', '/fci/home-back-ground', {'homeBackGround': scene})
            self.assertEqual(status, 409, (scene, error))
            self.assertEqual(self.game.profile_features._state(self.profile)['homeBackground'], before)
        with self.game.atomic_mutation():
            grant_rewards(self.game, self.profile, [{'type': 11, 'id': 31015, 'count': 1}])
        self.assertEqual(self.call('POST', '/fci/home-back-ground', {'homeBackGround': 31015, 'isRandom': True})[0], 200)
        self.game = LocalGame(self.root / 'game', self.accounts)
        result = self.call('GET', '/fci/home-back-ground')[1]
        self.assertEqual(result['result']['current'], 31015)
        self.assertTrue(result['result']['isRandom'])

    def test_novel_excludes_incomplete_characters_and_keeps_six_new_links(self):
        self.assertEqual(set(NOVELS), set(range(1, 26)))
        self.add(2134, star=6)
        self.assertEqual(self.call('GET', '/fci/CollectionsNovel/4')[1]['novels'], [])
        for actor_id, novel_id in ((1409, 5), (1509, 5), (2015, 14), (1017, 16), (2133, 13)):
            with self.subTest(actor_id=actor_id):
                self.add(actor_id, star=3)
                result = self.call('GET', f'/fci/CollectionsNovel/{novel_id}')[1]
                self.assertIn(actor_id, {a['actorId'] for a in result['novelDetail']['actorInfo']})
        self.add(2127, star=3)
        self.assertFalse(any(2127 in novel['actors'] for novel in NOVELS.values()))
        self.assertEqual(self.call('GET', '/fci/CollectionsNovel/999')[0], 404)

    def test_novel_reward_save_failure_rolls_back_then_claim_survives_restart(self):
        self.add(2117, star=3)
        self.call('GET', '/fci/CollectionsNovel/2')
        before = self.game._profile(self.account)
        ach = self.game.achievements
        with patch.object(ach, '_save', side_effect=RuntimeError('novel save failure')):
            with self.assertRaisesRegex(RuntimeError, 'novel save failure'):
                ach.handle_http('POST', '/fci/CollectionsNovel/2', None, self.profile)
        self.assertEqual(self.game._profile(self.account), before)
        result = self.call('POST', '/fci/CollectionsNovel/2')
        self.assertEqual(result[0], 200, result)
        self.assertEqual(result[1]['novels'][0]['curRewardNum'], 1)
        self.assertEqual(self.call('POST', '/fci/CollectionsNovel/2')[0], 409)
        self.game = LocalGame(self.root / 'game', self.accounts)
        self.assertEqual(self.call('POST', '/fci/CollectionsNovel/2')[0], 409)
        self.assertEqual(self.call('GET', '/fci/NovelCover/2')[1]['coverInfo'], [{'coverId': 2}])

    def test_scene_patch_changes_only_six_dates_and_exact_music_name(self):
        source_root = external_path('LJJC_DECOMPILED_LUA', 'analysis/lua-decompiled')
        original = require_file(source_root / 'xlsx__collectionsscenes.lua', 'LJJC_DECOMPILED_LUA')
        source = ROOT / 'client-patch/hot-update/sources/collectionsscenes-content-open.lua'
        def load(path, local):
            lua = LuaRuntime(unpack_returned_tuples=True)
            lua.execute('ProtobufI=function(_,r) return r end; LocalServer=' + str(local).lower() + '; require=function(_) return {IsLocalAccountServer=function() return LocalServer end} end')
            table = lua.execute(path.read_text(encoding='utf-8'))[1]
            return {int(row['id'] or 0): {k: v for k, v in row.items()} for _, row in table.items()}
        old = load(original, False)
        local = load(source if source.exists() else original, True)
        self.assertEqual(len(old), 19)
        self.assertEqual(set(local), set(old))
        for sid in FUTURE_SCENE_IDS:
            self.assertEqual(local[sid]['openTime'], '2020-01-01 00:00:00')
        self.assertEqual(local[31011]['bgmRes'], 'sleeping peacefully')
        for sid, row in old.items():
            for key, value in row.items():
                if (sid in FUTURE_SCENE_IDS and key == 'openTime') or (sid == 31011 and key == 'bgmRes'):
                    continue
                self.assertEqual(local[sid][key], value, (sid, key))
