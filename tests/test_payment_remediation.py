from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from google.protobuf import message_factory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from accounts import LocalAccounts
from game import LocalGame
import mall as mall_module
import test_systems

GEM60 = "com.tencent.fcicv.Gem60.android"
GEM1280 = "com.tencent.fcicv.Gem1280.android"
MONTH = "com.tencent.fcicv.Gem300m.android"
PACK = "com.tencent.fcicv.re10.android"


class PaymentRemediationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1793318400]
        self.accounts = LocalAccounts(self.root / "accounts", clock=lambda: self.now[0])
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def profile(self):
        return self.game._profile(self.account)

    def balance(self, kind):
        return sum(r["count"] for r in self.profile()["resources"] if r["type"] == kind and r["id"] == 0)

    def call(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        return self.game.mall.handle_http(method, path, body, self.profile())

    def touch(self, product):
        status, result = self.call("POST", "/fci/purchasetouch/", {"productId": product})
        self.assertEqual(status, 200, result)
        return result["tradeData"]

    def deliver(self, token):
        return self.call("POST", "/fci/purchasedelevery/", {"tradeData": token})

    def buy(self, product):
        token = self.touch(product)
        status, result = self.deliver(token)
        self.assertEqual(status, 200, result)
        return token, result

    def native(self, path, schema, **fields):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(schema))
        return self.game.handle_http("POST", path, cls(**fields).SerializeToString(), "application/wireformat", self.auth)

    def month_mails(self):
        return [mail for mail in self.game.systems._state(self.profile())["mails"] if "monthCardDay" in mail]

    def restart(self):
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])

    def test_pending_checkouts_are_bounded_and_expire_with_audit_records(self):
        with patch.object(mall_module, "MAX_PENDING_ORDERS", 3):
            tokens = [self.touch(GEM60) for _ in range(5)]
            state = self.game.mall._state(self.profile())
            self.assertEqual(set(state["pending"]), set(tokens[-3:]))
            orders = self.game.mall.orders(self.profile())
            self.assertEqual(len(orders), 5)
            self.assertEqual(sum(row["status"] == "superseded" for row in orders), 2)
            self.assertEqual(self.deliver(tokens[0]), (409, {"error": "purchase_token_expired"}))
            before = copy.deepcopy(self.profile()["resources"])
            self.now[0] += mall_module.PENDING_ORDER_TTL
            self.assertEqual(self.deliver(tokens[-1]), (409, {"error": "purchase_token_expired"}))
            self.assertEqual(self.game.mall._state(self.profile())["pending"], {})
            self.assertEqual(sum(row["status"] == "expired" for row in self.game.mall.orders(self.profile())), 3)
            self.assertEqual(self.profile()["resources"], before)
            self.restart()
            self.assertEqual(self.deliver(tokens[-1])[0], 409)
            self.assertEqual(self.deliver(self.touch(GEM60))[0], 200)

    def test_delivered_token_index_survives_both_json_cache_limits_and_restart(self):
        with patch.object(mall_module, "MAX_DELIVERED_REPLIES", 1), \
             patch.object(mall_module, "MAX_DELIVERED_TOKENS", 2):
            purchases = [self.buy(GEM60) for _ in range(4)]
            state = self.game.mall._state(self.profile())
            self.assertEqual((len(state["delivered"]), len(state["deliveredTokens"])), (1, 2))
            token, original = purchases[0]
            self.assertNotIn(token, state["deliveredTokens"])
            before = copy.deepcopy(self.profile()["resources"])
            self.restart()
            status, replay = self.native("/fci/purchasedelevery/", "ApiPurchaseDelevery",
                                         product_id=GEM60, trade_data=token)
            self.assertEqual((status, replay["transactionId"], replay["resChange"]),
                             (200, original["transactionId"], []))
            self.assertEqual(self.profile()["resources"], before)

    def test_legacy_full_replies_are_indexed_before_cache_trim(self):
        # Migration fixture: these are actual completed orders from the old
        # format, which stored full replies but no durable token markers.
        purchases = [self.buy(GEM60) for _ in range(3)]
        state = self.game.mall._state(self.profile())
        state.pop("deliveredTokens", None)
        self.game.mall._save(self.profile(), state)
        with closing(self.game._connect()) as connection, connection:
            connection.execute("DELETE FROM local_mall_receipts WHERE account_id=?",
                               (self.profile()["account_id"],))
        with patch.object(mall_module, "MAX_DELIVERED_REPLIES", 1):
            self.restart()
        token, original = purchases[0]
        self.assertNotIn(token, self.game.mall._state(self.profile())["delivered"])
        before = copy.deepcopy(self.profile()["resources"])
        status, replay = self.deliver(token)
        self.assertEqual((status, replay["transactionId"], replay["resChange"]),
                         (200, original["transactionId"], []))
        self.assertEqual(self.profile()["resources"], before)

    def test_receipt_index_rejects_other_accounts_and_guessed_order_tokens(self):
        token, reply = self.buy(GEM60)
        other, auth = test_systems.LocalSystemsTests._guest(self.accounts)
        other_profile = self.game._profile(other)
        self.assertNotEqual(self.profile()["account_id"], other_profile["account_id"])
        other_before = copy.deepcopy(other_profile["resources"])
        self.assertIsNone(self.game.mall.handle_http("POST", "/fci/purchasedelevery/",
                         json.dumps({"tradeData": token}).encode(), other_profile))
        self.assertEqual(self.game._profile(other)["resources"], other_before)
        self.assertIsNone(self.deliver("local-sandbox:" + reply["transactionId"]))
        self.assertIsNone(self.deliver(token + "forged"))

    def test_legacy_order_without_token_evidence_is_not_reconstructed(self):
        token, reply = self.buy(GEM60)
        state = self.game.mall._state(self.profile())
        state["delivered"].clear()
        state["deliveredTokens"].clear()
        self.game.mall._save(self.profile(), state)
        with closing(self.game._connect()) as connection, connection:
            connection.execute("DELETE FROM local_mall_receipts WHERE account_id=?",
                               (self.profile()["account_id"],))
        self.restart()
        self.assertTrue(any(row["order_id"] == reply["transactionId"]
                            for row in self.game.mall.orders(self.profile())))
        before = copy.deepcopy(self.profile()["resources"])
        self.assertIsNone(self.deliver(token))
        self.assertEqual(self.profile()["resources"], before)

    def test_first_bonus_replaces_promotion_and_survives_restart(self):
        token, reply = self.buy(GEM1280)
        self.assertEqual((self.balance(5), self.balance(27)), (2560, 1280))
        self.restart()
        retry_status, retry = self.deliver(token)
        self.assertEqual(retry_status, 200)
        self.assertEqual(retry['transactionId'], reply['transactionId'])
        self.assertEqual(retry['resChange'], [])
        self.buy(GEM1280)
        self.assertEqual((self.balance(5), self.balance(27)), (3960, 2560))
        info = self.call("GET", "/fci/purchase-activity-info/")[1]["mp_info"]["utp_mpinfo_detail"]
        self.assertIsInstance(info["rank_info"], list)
        self.assertIsInstance(info["range_info"], list)
        ranks = {r["num"]: r for r in info["rank_info"]}
        ranges = {r["num"]: r for r in info["range_info"]}
        self.assertEqual(set(ranks), {"60", "300", "680", "1280", "3280", "6480"})
        self.assertEqual(ranks["1280"]["send_ext"], "")
        self.assertNotEqual(ranks["60"]["send_ext"], "")
        self.assertEqual(ranges["1280"]["coin"][0]["send_num"], "120")

    def test_old_successful_orders_do_not_consume_new_offer(self):
        profile = self.profile()
        state = self.game.mall._state(profile)
        state["purchase"][GEM60] = {"buyCount": 9, "lastBuyStartTime": self.now[0]}
        self.game.mall._save(profile, state)
        self.buy(GEM60)
        self.assertEqual((self.balance(5), self.balance(27)), (120, 60))

    def test_paid_and_free_ledger_changes_are_canonical(self):
        ledger = self.game.economy._change_resources
        profile = self.profile()
        self.assertTrue(ledger(profile, [{"type": 27, "count": 300}, {"type": 5, "count": 50}])[0])
        self.assertEqual((self.balance(5), self.balance(27)), (350, 300))
        self.assertTrue(ledger(profile, [{"type": 5, "count": -100}])[0])
        self.assertEqual((self.balance(5), self.balance(27)), (250, 250))
        self.assertTrue(ledger(profile, [{"type": 27, "count": -100}])[0])
        self.assertEqual((self.balance(5), self.balance(27)), (150, 150))
        before = copy.deepcopy(profile["resources"])
        self.assertFalse(ledger(profile, [{"type": 5, "count": -90}, {"type": 5, "count": -90}])[0])
        self.assertEqual(profile["resources"], before)
        self.assertEqual((self.balance(5), self.balance(27)), (150, 150))

    def test_legacy_invalid_paid_balance_is_capped_without_creating_money(self):
        profile = self.profile()
        resources = [r for r in profile["resources"] if r["type"] not in (5, 27)]
        resources += [{"type": 5, "id": 0, "count": 100}, {"type": 27, "id": 0, "count": 300}]
        with closing(self.game._connect()) as connection, connection:
            connection.execute("UPDATE game_profiles SET resources_json = ? WHERE account_id = ?",
                               (json.dumps(resources), profile["account_id"]))
        ok, _, _ = self.game.economy._change_resources(profile, [{"type": 5, "count": -20}])
        self.assertTrue(ok)
        self.assertEqual((self.balance(5), self.balance(27)), (80, 80))

    def test_distinct_pending_orders_cannot_bypass_limit(self):
        tokens = [self.touch(PACK), self.touch(PACK)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.deliver, tokens))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        self.assertEqual(self.game.systems._state(self.profile())["items"]["60001"]["count"], 1)
        self.assertEqual(self.balance(5), 20)

    def test_activity_closed_after_order_refuses_delivery_and_preserves_pending_state(self):
        token = self.touch(PACK)
        profile = self.profile()
        activity_id = self.game.mall.catalog.mall_row_for_product(PACK)["activityId"]
        self.assertIn(activity_id, self.game.mall.open_activity_ids)
        before_resources = copy.deepcopy(profile["resources"])
        before_systems = self.game.systems._state(profile)
        before_mall = self.game.mall._state(profile)
        before_orders = self.game.mall.orders(profile)
        pushed = []
        self.game.base_push_notifier = lambda *args: pushed.append(args)
        self.game.mall.open_activity_ids.remove(activity_id)
        self.assertEqual(self.deliver(token), (409, {"error": "mall_activity_closed"}))
        self.assertEqual(self.profile()["resources"], before_resources)
        self.assertEqual(self.game.systems._state(self.profile()), before_systems)
        self.assertEqual(self.game.mall._state(self.profile()), before_mall)
        self.assertEqual(self.game.mall.orders(self.profile()), before_orders)
        self.assertTrue(all(row["status"] == "pending" for row in before_orders))
        self.assertEqual(pushed, [])
        # The rejected delivery did not consume its receipt; reopening permits
        # one delivery, and replay cannot pay the goods again.
        self.game.mall.open_activity_ids.add(activity_id)
        self.assertEqual(self.deliver(token)[0], 200)
        resources_after = copy.deepcopy(self.profile()["resources"])
        self.assertEqual(self.deliver(token)[1]["resChange"], [])
        self.assertEqual(self.profile()["resources"], resources_after)

    def test_month_purchase_renewal_missed_days_and_expiry(self):
        self.buy(MONTH)
        self.assertEqual(self.balance(24), 29)
        self.assertEqual(len(self.month_mails()), 1)
        attachments = {r["type"]: r["count"] for r in self.month_mails()[0]["attachments"]}
        self.assertEqual(attachments, {5: 50, 7: 20000})
        self.buy(MONTH)
        self.assertEqual(self.balance(24), 59)
        self.assertEqual(len(self.month_mails()), 1)
        self.restart()
        self.now[0] += 3 * 86400
        self.game.mall.settle_month_card(self.profile())
        self.assertEqual(self.balance(24), 56)
        self.assertEqual(len(self.month_mails()), 2)
        self.game.mall.settle_month_card(self.profile())
        self.assertEqual(len(self.month_mails()), 2)
        self.now[0] += 100 * 86400
        self.game.mall.settle_month_card(self.profile())
        self.assertEqual(self.balance(24), 0)
        self.assertEqual(len(self.month_mails()), 2)
        self.buy(MONTH)
        self.assertEqual(self.balance(24), 29)
        # Old mail has expired after 100 days; renewal creates only today's.
        self.assertEqual(len(self.month_mails()), 1)
        self.assertGreater(self.month_mails()[0]["id"], 2)

    def test_month_daily_mail_claim_is_free_currency_and_once(self):
        self.buy(MONTH)
        mail = self.month_mails()[0]
        before_total, before_paid, before_gold = self.balance(5), self.balance(27), self.balance(7)
        for _ in range(2):
            status, _ = self.game.mailbox.handle_http("PATCH", f'/fci/mail/{mail["id"]}',
                                                     b'{"attachmentsGot":true}', self.profile())
            self.assertEqual(status, 200)
        self.assertEqual((self.balance(5), self.balance(27)), (before_total + 50, before_paid))
        self.assertEqual(self.balance(7), before_gold + 20000)

    def test_month_daily_mail_matches_original_misc_configuration(self):
        misc = json.loads((ROOT / "data/client-tables/Misc.json").read_text(encoding="utf-8"))["rows"][0]
        self.assertEqual(misc["monthCardRewardDaily"], [{"type": 5, "count": 50}, {"type": 7, "count": 20000}])
        self.buy(MONTH)
        self.assertEqual(self.month_mails()[0]["attachments"], [
            {"type": row["type"], "id": row.get("id", 0), "param": row.get("param", 0), "count": row["count"]}
            for row in misc["monthCardRewardDaily"]])

    def test_month_has_exactly_thirty_daily_entitlements(self):
        self.buy(MONTH)
        for day in range(1, 30):
            self.now[0] += 86400
            self.restart()
            self.game.mall.settle_month_card(self.profile())
            self.assertEqual(self.balance(24), 29 - day)
            self.assertEqual(len(self.month_mails()), day + 1)
        state = self.game.mall._state(self.profile())["monthCard"]
        last_mail = state["lastMailId"]
        self.now[0] += 86400
        self.game.mall.settle_month_card(self.profile())
        self.assertEqual(self.balance(24), 0)
        self.assertEqual(self.game.mall._state(self.profile())["monthCard"]["lastMailId"], last_mail)

    def test_failed_delivery_rolls_back_mail_resources_order_and_offer(self):
        for product in (MONTH, GEM60, PACK):
            with self.subTest(product=product):
                token = self.touch(product)
                before_resources = copy.deepcopy(self.profile()["resources"])
                before_systems = self.game.systems._state(self.profile())
                before_mall = self.game.mall._state(self.profile())
                pushed = []
                self.game.mailbox.notifier = lambda *args: pushed.append(args)
                with patch.object(self.game.mall, "_order", side_effect=RuntimeError("disk failure")):
                    with self.assertRaisesRegex(RuntimeError, "disk failure"):
                        self.deliver(token)
                self.assertEqual(self.profile()["resources"], before_resources)
                self.assertEqual(self.game.systems._state(self.profile()), before_systems)
                self.assertEqual(self.game.mall._state(self.profile()), before_mall)
                self.assertIsNone(self.game.mall._receipt(self.profile(), token))
                self.assertEqual(pushed, [])
                self.assertEqual(self.deliver(token)[0], 200)

    def test_direct_purchase_has_separate_tab_trigger_and_product_activities(self):
        rows = self.game.mall.activity_rows()
        self.assertTrue(any(r["type"] == 37 and r["activityId"] == 37001 for r in rows))
        opened = {r["activityId"] for r in rows}
        direct = [r for r in self.game.mall.activity_gifts if r.get("giftPage") == 3]
        self.assertTrue(direct)
        self.assertTrue(all(r["activityId"] in opened for r in direct))

    def test_visible_repeated_products_buy_with_native_orders_on_both_platforms(self):
        mall = self.game.mall
        status, activities = self.game.handle_http("GET", "/fci/activity/", authorization=self.auth)
        self.assertEqual(status, 200, activities)
        opened = {row["activityId"] for row in activities if row["type"] == 15}
        for mall_id in (860, 870, 926, 927):
            with self.subTest(mall=mall_id):
                row = mall.catalog.mall[mall_id]
                product_id = row["productId"]
                product = mall.catalog.products[product_id]
                self.assertIn(row["activityId"], opened)
                first = mall.catalog.mall_row_for_product(product_id)
                self.assertNotIn(first["activityId"], opened)
                self.assertEqual(mall.catalog.mall_row_for_product(
                    product_id, open_activity_ids=mall.open_activity_ids)["id"], mall_id)
                count = product["maxBuyCount"]
                item_id, unit = product["purchaseRes"][0]["id"], product["purchaseRes"][0]["count"]
                before = self.game.systems._state(self.profile())["items"].get(str(item_id), {}).get("count", 0)
                for purchased in range(1, count + 1):
                    status, touched = self.native("/fci/purchasetouch/?source=direct", "ApiPurchaseTouchProduct",
                        product_id=product_id, is_guest=True, platform=product["platform"], channel=5, count=product["count"])
                    self.assertEqual(status, 200, touched)
                    fields = {"product_id": product_id, "trade_data": touched["tradeData"],
                              "platform": product["platform"], "channel": 5}
                    status, delivered = self.native("/fci/purchasedelevery/", "ApiPurchaseDelevery", **fields)
                    self.assertEqual(status, 200, delivered)
                    self.assertEqual(delivered["buyCount"], purchased)
                    status, replay = self.native("/fci/purchasedelevery/", "ApiPurchaseDelevery", **fields)
                    self.assertEqual((status, replay["resChange"]), (200, []))
                self.assertEqual(self.game.systems._state(self.profile())["items"][str(item_id)]["count"], before + count * unit)
                status, result = self.native("/fci/purchasetouch/", "ApiPurchaseTouchProduct",
                                             product_id=product_id, platform=product["platform"])
                self.assertEqual(status, 409, result)

    def test_duplicate_product_closure_rejects_native_delivery_without_losing_receipt(self):
        mall = self.game.mall
        product_id = mall.catalog.mall[860]["productId"]
        status, touched = self.native("/fci/purchasetouch/", "ApiPurchaseTouchProduct", product_id=product_id)
        self.assertEqual(status, 200, touched)
        fields = {"product_id": product_id, "trade_data": touched["tradeData"]}
        before = copy.deepcopy(mall._state(self.profile()))
        mall.open_activity_ids.remove(15860)
        status, result = self.native("/fci/purchasedelevery/", "ApiPurchaseDelevery", **fields)
        self.assertEqual(status, 409, result)
        self.assertEqual(mall._state(self.profile()), before)
        self.assertEqual(self.game.systems._state(self.profile())["items"].get("60001", {}).get("count", 0), 0)
        mall.open_activity_ids.add(15860)
        self.assertEqual(self.native("/fci/purchasedelevery/", "ApiPurchaseDelevery", **fields)[0], 200)


if __name__ == "__main__":
    unittest.main()
