"""Native Shop/20,21,23 contracts; prices are documented local rules."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from game import LocalGame
from grants import grant_rewards
from server.core.client_errors import client_error_reply
import test_shop_energy as shop_tests


class OpenEventShopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.world = shop_tests._World(self.root)
        self.world.set_level(60)

    def test_receipt_write_failure_rolls_back_cost_reward_and_limit(self):
        self.world.set_resource(7, 500000)
        _, goods = self.world.call('GET', '/fci/Shop/1/Goods/')
        good = next(g for g in goods if g['cost'][0]['type'] == 7)
        profile = self.world.profile()
        before = (self.world.resource(7), self.world.game.systems._state(profile),
                  self.world.game.economy._state(self.world.account))
        with closing(self.world.game.economy._connect()) as conn, conn:
            conn.execute("CREATE TRIGGER fail_shop_receipt BEFORE UPDATE ON economy_profiles "
                         "BEGIN SELECT RAISE(ABORT,'shop receipt failure'); END")
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'shop receipt failure'):
                self.world.call('POST', '/fci/ShopTrade/', {'shopId': 1, 'info': good, 'buyCount': 1})
            self.assertEqual(before, (self.world.resource(7), self.world.game.systems._state(profile),
                                     self.world.game.economy._state(self.world.account)))
        finally:
            with closing(self.world.game.economy._connect()) as conn, conn:
                conn.execute('DROP TRIGGER fail_shop_receipt')

    def test_three_shops_have_distinct_native_currency_and_crane_supply(self):
        all_ids = []
        for shop_id, currency in ((20, 2007), (21, 2008), (23, 2012)):
            with self.subTest(shop=shop_id):
                status, goods = self.world.call('GET', f'/fci/Shop/{shop_id}/Goods/')
                self.assertEqual(status, 200)
                self.assertTrue(goods)
                self.assertEqual({(c['type'], c['id']) for g in goods for c in g['cost']}, {(11, currency)})
                self.assertTrue(all(g['buyLimit'] == 1 for g in goods))
                all_ids.extend(g['shopGoodId'] for g in goods)
                if shop_id == 20:
                    self.assertIn(61001, [g['good']['id'] for g in goods])
        self.assertEqual(len(all_ids), len(set(all_ids)))

    def test_event_trade_cost_limit_and_restart_are_durable(self):
        for shop_id, currency in ((20, 2007), (21, 2008), (23, 2012)):
            with self.subTest(shop=shop_id):
                status, goods = self.world.call('GET', f'/fci/Shop/{shop_id}/Goods/')
                self.assertEqual(status, 200)
                good = goods[0]
                profile = self.world.profile()
                before = self.world.game.systems._state(profile)
                request = {'shopId': shop_id, 'info': good, 'buyCount': 1}
                rejected = self.world.call('POST', '/fci/ShopTrade/', request)
                self.assertEqual(rejected[0], 409)
                # LocalGame preserves diagnostic statuses; the HTTP server
                # applies this native notify adapter at the egress boundary.
                self.assertEqual(client_error_reply(*rejected), (509, {'notify': {'code': 'Error_ItemNumNotEnough'}}))
                self.assertEqual(before, self.world.game.systems._state(profile))
                grant_rewards(self.world.game, profile, [{'type': 11, 'id': currency, 'count': 1000}])
                status, result = self.world.call('POST', '/fci/ShopTrade/', request)
                self.assertEqual(status, 200)
                self.assertTrue(result['resChange'])
                after = self.world.game.systems._state(profile)
                self.assertNotEqual(self.world.call('POST', '/fci/ShopTrade/', request)[0], 200)
                self.assertEqual(after, self.world.game.systems._state(profile))
                self.world.game = LocalGame(self.root / 'game', self.world.accounts, clock=self.world.clock.now)
                status, restored = self.world.call('GET', f'/fci/Shop/{shop_id}/Goods/')
                self.assertEqual(status, 200)
                restored_good = next(g for g in restored if g['shopGoodId'] == good['shopGoodId'])
                self.assertTrue(restored_good['sold'])
                self.assertEqual(restored_good['buyCountRecord'], 1)
                self.assertNotEqual(self.world.call('POST', '/fci/ShopTrade/', request)[0], 200)
                self.assertEqual(after, self.world.game.systems._state(profile))


if __name__ == '__main__':
    unittest.main()
