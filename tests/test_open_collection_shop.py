"""Batch-two collection purchases through the shipped GachaScoreShop wire API."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import actors
from game import LocalGame
from gacha_score_shop import LocalGachaScoreShop
from grants import grant_rewards
from server import FixtureServer, JsonlLogger
import test_actor_remediation as fixtures

ROOT = Path(__file__).resolve().parents[1]
ACTOR_IDS = {1409, 2015, 1017, 1509, 2127, 2133}
BLOCKED_ACTORS = {1501, 1503, 1505, 1506, 1515, 1516, 1517, 1601, 1607, 2018, 2106, 2115, 2016, 2017, 1511, 2134}
SKINS = {r['id']: r for r in json.loads((ROOT / 'data/client-tables/ActorSkin.json').read_text(encoding='utf-8'))['rows']}


class OpenCollectionShopTests(unittest.TestCase):
    call, state, save, add = (fixtures.ActorRemediationTests.call, fixtures.ActorRemediationTests.state,
                              fixtures.ActorRemediationTests.save, fixtures.ActorRemediationTests.add)
    tearDown = fixtures.ActorRemediationTests.tearDown

    def setUp(self):
        fixtures.ActorRemediationTests.setUp(self)
        self.schedule = patch.object(self.game.events, '_activity_list', return_value=(200, [{'type': 13, 'activityId': 13001}]))
        self.schedule.start()
        self.addCleanup(self.schedule.stop)
        self.shop = LocalGachaScoreShop(self.game)

    def goods(self):
        status, shop = self.shop.handle_http('GET', '/fci/gacha-shop/', None, self.profile)
        self.assertEqual(status, 200, shop)
        return list(shop.goods)

    def good(self, resource_type, resource_id):
        result = next((g for g in self.goods() if g.good.type == resource_type and g.good.id == resource_id), None)
        self.assertIsNotNone(result, f'visible original-wire product missing: {resource_type}/{resource_id}')
        return result

    def fund(self, good):
        with self.game.atomic_mutation():
            grant_rewards(self.game, self.profile, [{'type': good.cost.type, 'id': good.cost.id,
                                                   'count': good.cost.count, 'param': good.cost.param}])

    def trade(self, good_id):
        request = self.game.systems._message('ApiGachaShopTrade')
        request.good_id = good_id
        if self.shop.game is not self.game:
            self.shop = LocalGachaScoreShop(self.game)
        return self.shop.handle_http('POST', '/fci/gacha-shop/trade', request.SerializeToString(), self.profile)

    def test_six_collection_characters_are_visible_obtainable_and_persist(self):
        for actor_id in sorted(ACTOR_IDS):
            with self.subTest(actor_id=actor_id):
                good = self.good(15, actor_id)
                self.assertEqual((good.cost.type, good.cost.count, good.good.param), (23, 100000, 101))
                self.fund(good)
                status, result = self.trade(good.good_id)
                self.assertEqual(status, 200, result)
                received = [c.actor for c in result.res_change if c.HasField('actor')]
                self.assertEqual([(a.id, a.quality) for a in received], [(actor_id, 3)])
                self.assertEqual(len(received[0].talent_info), 3)
        self.assertFalse(BLOCKED_ACTORS & {g.good.id for g in self.goods() if g.good.type == 15})
        reboot = LocalGame(self.root / 'game', self.accounts)
        owned = {a['id'] for a in actors.ordered(self.profile, reboot.systems._state(self.profile))}
        self.assertTrue(ACTOR_IDS <= owned)

    def test_existing_collection_character_converts_to_original_purple_chips_once(self):
        decomposes = json.loads((ROOT / 'data/client-tables/ActorDecompose.json').read_text(encoding='utf-8'))['rows']
        for actor_id in sorted(ACTOR_IDS):
            with self.subTest(actor_id=actor_id):
                self.add(actor_id)
                good = self.good(15, actor_id)
                self.fund(good)
                status, first = self.trade(good.good_id)
                self.assertEqual(status, 200, first)
                expected = next(r['decomposeRes'] for r in decomposes if r['id'] == actor_id and r['quality'] == 3)
                actual = [(c.base_res.type, c.base_res.id, c.base_res.count_delta) for c in first.res_change
                          if c.HasField('base_res') and c.base_res.count_delta > 0]
                self.assertEqual(actual, [(r['type'], r['id'], r['count']) for r in expected])
                self.assertFalse(any(c.HasField('actor') for c in first.res_change))
                before = self.state()
                self.assertEqual(len(self.trade(good.good_id)[1].res_change), 0)
                self.assertEqual(self.state(), before)

    def test_exact_195_future_skin_whitelist_uses_original_shard_costs(self):
        future = {rid for rid, r in SKINS.items() if r.get('openTime', '').startswith('2029')}
        expected = future - {113401, 113402, 113403, 113405}
        actual = {g.good.id: g for g in self.goods() if g.good.type == 29 and g.good.id in future}
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), 195)
        self.assertNotIn(113404, {g.good.id for g in self.goods() if g.good.type == 29})
        for sid, good in actual.items():
            with self.subTest(skin=sid):
                cost = SKINS[sid]['exchangeDefault']
                self.assertEqual((good.cost.type, good.cost.id, good.cost.count), (cost['type'], cost['id'], cost['count']))

    def test_skin_purchase_concurrent_replay_equip_and_relogin(self):
        good = self.good(29, 100101)
        self.fund(good)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.trade(good.good_id), range(2)))
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual(sum(bool(r.res_change) for _, r in results), 1)
        self.assertEqual(self.state()['items'][str(good.cost.id)]['count'], 0)
        uid = actors.first_uid(self.profile, self.state(), 1001)
        request = self.game.systems._message('ApiActorSkin')
        request.actor_skin = 100101
        status, result = self.game.handle_http('PATCH', f'/fci/actor/{uid}/skin', request.SerializeToString(),
                                               'application/wireformat', self.auth)
        self.assertEqual(status, 200, result)
        self.assertEqual(result.actor_skin, 100101)
        reboot = LocalGame(self.root / 'game', self.accounts)
        blob = dict(reboot.login_messages(self.account))['DBGamePlayer.Actors']
        login = reboot._types['DBGamePlayer.Actors']()
        login.ParseFromString(blob)
        self.assertIn(100101, login.unlocked_skins)
        self.assertEqual(next(a.actor_skin for a in login.player_actors if a.uid == uid), 100101)
        with patch.object(reboot.events, '_activity_list', return_value=(200, [{'type': 13, 'activityId': 13001}])):
            self.game = reboot
            before = self.state()
            self.assertEqual(len(self.trade(good.good_id)[1].res_change), 0)
            self.assertEqual(self.state(), before)

    def test_skin_shards_insufficient_is_native_notify_and_does_not_spend(self):
        good = self.good(29, 100101)
        before = self.state()
        status, error = self.trade(good.good_id)
        self.assertEqual((status, error), (409, {'error': 'insufficient_item'}))
        self.assertEqual(self.state(), before)
        server = FixtureServer(('127.0.0.1', 0), self.root, JsonlLogger(self.root / 'http.jsonl'), accounts=self.accounts, game=self.game)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            before = self.state()
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            req = self.game.systems._message('ApiGachaShopTrade'); req.good_id = good.good_id
            conn.request('POST', '/fci/gacha-shop/trade', req.SerializeToString(),
                         {'Authorization': self.auth, 'Content-Type': 'application/wireformat'})
            resp = conn.getresponse(); raw = resp.read(); conn.close()
            self.assertEqual(resp.status, 509)
            payload = json.loads(raw)
            self.assertEqual(payload, {'notify': {'code': 'Error_ItemNumNotEnough'}})
            self.assertEqual(self.state(), before)
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_payment_grant_and_claim_roll_back_together(self):
        for resource_type, resource_id in [(15, 1409), (29, 100101)]:
            with self.subTest(resource=resource_id):
                good = self.good(resource_type, resource_id)
                self.fund(good)
                before_state = self.state()
                before_resources = self.game._profile(self.account)['resources']
                with patch('gacha_score_shop.grant_rewards', side_effect=RuntimeError('isolated grant failure')):
                    with self.assertRaisesRegex(RuntimeError, 'isolated grant failure'):
                        self.trade(good.good_id)
                self.assertEqual(self.state(), before_state)
                self.assertEqual(self.game._profile(self.account)['resources'], before_resources)
                self.assertIn(good.good_id, {g.good_id for g in self.goods()})
                self.assertEqual(self.trade(good.good_id)[0], 200)

    def test_unowned_or_wrong_actor_skin_cannot_be_equipped_for_free(self):
        uid = actors.first_uid(self.profile, self.state(), 1001)
        before = self.state()
        self.assertEqual(self.call('PATCH', f'/fci/actor/{uid}/skin', {'actorSkin': 100101})[0], 409)
        self.assertEqual(self.state(), before)
        with self.game.atomic_mutation():
            grant_rewards(self.game, self.profile, [{'type': 29, 'id': 100201, 'count': 1}])
        before = self.state()
        self.assertEqual(self.call('PATCH', f'/fci/actor/{uid}/skin', {'actorSkin': 100201})[0], 409)
        self.assertEqual(self.state(), before)

    def test_legacy_actor_patch_cannot_bypass_skin_ownership(self):
        uid = actors.first_uid(self.profile, self.state(), 1001)
        before = self.state()
        self.assertEqual(self.call('PATCH', f'/fci/actor/{uid}', {'actorSkin': 100101})[0], 409)
        self.assertEqual(self.state(), before)

    def test_mixed_skin_unlock_does_not_open_missing_character_variant(self):
        good = self.good(29, 101701)
        self.fund(good)
        self.assertEqual(self.trade(good.good_id)[0], 200)
        unlocked = self.state()['unlockedSkins']
        self.assertIn(101701, unlocked.get('1017', []))
        self.assertNotIn(101701, unlocked.get('2017', []))

