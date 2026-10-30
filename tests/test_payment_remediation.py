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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from accounts import LocalAccounts
from game import LocalGame
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

    def month_mails(self):
        return [mail for mail in self.game.systems._state(self.profile())["mails"] if "monthCardDay" in mail]

    def restart(self):
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])

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
        before_total, before_paid = self.balance(5), self.balance(27)
        for _ in range(2):
            status, _ = self.game.mailbox.handle_http("PATCH", f'/fci/mail/{mail["id"]}',
                                                     b'{"attachmentsGot":true}', self.profile())
            self.assertEqual(status, 200)
        self.assertEqual((self.balance(5), self.balance(27)), (before_total + 50, before_paid))

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
                self.assertEqual(pushed, [])
                self.assertEqual(self.deliver(token)[0], 200)

    def test_direct_purchase_has_separate_tab_trigger_and_product_activities(self):
        rows = self.game.mall.activity_rows()
        self.assertTrue(any(r["type"] == 37 and r["activityId"] == 37001 for r in rows))
        opened = {r["activityId"] for r in rows}
        direct = [r for r in self.game.mall.activity_gifts if r.get("giftPage") == 3]
        self.assertTrue(direct)
        self.assertTrue(all(r["activityId"] in opened for r in direct))


if __name__ == "__main__":
    unittest.main()
