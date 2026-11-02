from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from google.protobuf import message_factory

from accounts import LocalAccounts
from game import LocalGame
import mall as mall_module
from mall import (
    ACTIVITY_TYPE_MALL, MALL_ACTIVITY_START, LocalMall, MallCatalog, build_iaphandler_patch, window_start,
)
from test_systems import LocalSystemsTests

TABLES = ROOT / "data" / "client-tables"
IAP_SOURCE = ROOT.parents[1] / "analysis" / "lua-decompiled" / "manager__iaphandler.lua"
GEM60 = "com.tencent.fcicv.Gem60.android"
GEM1280 = "com.tencent.fcicv.Gem1280.android"
GEM64800 = "com.tencent.fcicv.Gem64800.android"
GEM648000 = "com.tencent.fcicv.Gem648000.android"


def _table(name):
    return json.loads((TABLES / f"{name}.json").read_text(encoding="utf-8"))["rows"]


class MallCatalogTests(unittest.TestCase):
    def test_tabs_list_the_client_table_rows(self):
        catalog = MallCatalog()
        level_rows = [r for r in _table("Mall") if r.get("giftPage") == 1 and not r.get("isMonth")]
        self.assertEqual([r["id"] for r in catalog.page(1)], sorted(r["id"] for r in level_rows))
        self.assertEqual(len(level_rows), 8)
        self.assertEqual([r.get("limitLv", 0) for r in catalog.page(1)], [0, 10, 20, 30, 40, 50, 55, 60])
        # 迷子充值: IAPHandler.GetProducts (platform 12 = android_midas, showInShop, by price).
        shop = catalog.shop_products(12)
        self.assertEqual([r["count"] for r in shop], [60, 300, 680, 1280, 3280, 6480, 64800, 648000])
        self.assertEqual([r["price"] for r in shop], [6.0, 30.0, 68.0, 128.0, 328.0, 648.0, 6480.0, 64800.0])

    def test_event_gift_activities_cover_distinct_packs(self):
        catalog = MallCatalog()
        page2 = {r["activityId"]: r for r in _table("Mall") if r.get("giftPage") == 2}
        self.assertEqual(len(catalog.activity_gift_rows(dedupe=False)), len(page2))
        deduped = catalog.activity_gift_rows()
        self.assertTrue(0 < len(deduped) < len(page2))
        self.assertTrue(all(r["activityId"] in page2 for r in deduped))

    def test_purchase_windows(self):
        now = 1767247140 - 28800  # 2026-01-01 05:59 Beijing (before the 06:00 reset)
        self.assertEqual(window_start(1, now), 1767160800 - 28800)  # 2025-12-31 06:00
        self.assertEqual(window_start(1, now + 120), 1767247200 - 28800)
        self.assertEqual(window_start(2, now + 120), 1766988000 - 28800)  # Monday 2025-12-29 06:00
        self.assertEqual(window_start(3, now + 120), 1767247200 - 28800)  # 2026-01-01 06:00
        self.assertEqual(window_start(4, now), 0)


class LocalMallTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, self.auth = LocalSystemsTests._guest(self.accounts)
        self.now = [1793318400]
        self.mall = LocalMall(self.game, clock=lambda: self.now[0])

    def tearDown(self):
        self._temp.cleanup()

    def profile(self):
        return self.game._profile(self.account)

    def balance(self, rtype):
        return sum(r["count"] for r in self.profile()["resources"] if r["type"] == rtype and r["id"] == 0)

    def set_level(self, level):
        self.profile()  # the profile row is created on first read
        with closing(self.game._connect()) as connection, connection:
            connection.execute("UPDATE game_profiles SET level = ? WHERE account_id = ?",
                               (level, int(self.account["accountId"])))

    def add(self, rtype, count):
        ok, _, _ = self.game.economy._change_resources(self.profile(), [{"type": rtype, "id": 0, "param": 0, "count": count}])
        self.assertTrue(ok)

    def call(self, method, path, body=None):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        return self.mall.handle_http(method, path, body, self.profile())

    def recharge(self, product_id, wire=False):
        if wire:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiPurchaseTouchProduct"))
            body = cls(product_id=product_id, is_guest=True, platform=12, channel=5, count=60).SerializeToString()
        else:
            body = {"productId": product_id, "isGuest": True, "platform": 12, "channel": 5, "count": 60}
        status, touch = self.call("POST", "/fci/purchasetouch/", body)
        self.assertEqual(status, 200, touch)
        self.assertTrue(touch["tradeData"])
        status, delivered = self.call("POST", "/fci/purchasedelevery/",
                                      {"productId": product_id, "tradeData": touch["tradeData"], "platform": 12, "channel": 5})
        self.assertEqual(status, 200, delivered)
        return touch, delivered

    def test_mall_info_is_plain_json_with_explicit_maps(self):
        status, info = self.call("GET", "/fci/mall-info/")
        self.assertEqual(status, 200)
        self.assertIsInstance(info, dict)  # not a typed protobuf: empty maps must reach Lua as {}
        self.assertEqual(info["mallRecord"]["summary"], {})
        self.assertEqual(info["purchaseRecord"]["buyRecord"], {})
        self.assertEqual(info["mallRecord"]["playerId"], self.profile()["player_id"])
        json.dumps(info)

    def test_recharge_credits_paid_mizi_once(self):
        touch, delivered = self.recharge(GEM60, wire=True)
        self.assertTrue(touch["isSandbox"])
        self.assertEqual((self.balance(5), self.balance(27)), (120, 60))
        self.assertEqual(delivered["productId"], GEM60)
        self.assertEqual(delivered["buyCount"], 1)
        self.assertEqual({c["baseRes"]["type"]: c["baseRes"]["countDelta"] for c in delivered["resChange"]}, {5: 120, 27: 60})
        self.assertTrue(all(c["inBox"] == [] for c in delivered["resChange"]))
        # A retried delivery returns the same transaction without a second credit.
        status, again = self.call("POST", "/fci/purchasedelevery/", {"tradeData": touch["tradeData"]})
        self.assertEqual((status, again["transactionId"]), (200, delivered["transactionId"]))
        self.assertEqual(self.balance(5), 120)
        info = self.call("GET", "/fci/mall-info/")[1]
        self.assertEqual(info["purchaseRecord"]["buyRecord"][GEM60]["buyCount"], 1)
        orders = self.mall.orders(self.profile())
        self.assertEqual([(o["product_id"], o["status"]) for o in orders], [(GEM60, "delivered")])

    def test_delivery_reply_cache_is_bounded_and_old_token_replays_without_credit(self):
        profile = self.profile()
        state = self.mall._state(profile)
        for index in range(mall_module.MAX_DELIVERED_REPLIES + 7):
            token = f"local-sandbox:synthetic-{index}"
            self.mall._remember_delivery(state, token, {
                "transactionId": f"order-{index}", "productId": GEM60,
                "channel": 5, "resChange": [{"baseRes": {"type": 5, "countDelta": 120}}],
            })
        self.assertEqual(len(state["delivered"]), mall_module.MAX_DELIVERED_REPLIES)
        self.assertEqual(len(state["deliveredTokens"]), mall_module.MAX_DELIVERED_REPLIES + 7)
        self.mall._save(profile, state)

        # The oldest full reply has been evicted, but its marker still makes
        # a retry idempotent and returns no second resource delta.
        status, replay = self.call("POST", "/fci/purchasedelevery/", {"tradeData": "local-sandbox:synthetic-0"})
        self.assertEqual(status, 200)
        self.assertEqual((replay["transactionId"], replay["resChange"]), ("order-0", []))

    def test_recharge_bonus_is_bound_mizi(self):
        self.recharge(GEM1280)
        self.assertEqual((self.balance(5), self.balance(27)), (2560, 1280))
        self.recharge(GEM1280)
        self.assertEqual((self.balance(5), self.balance(27)), (2560 + 1280 + 120, 2560))

    def test_large_recharge_tiers_deliver_the_declared_amounts(self):
        self.recharge(GEM64800)
        self.assertEqual((self.balance(5), self.balance(27)), (129600, 64800))
        self.recharge(GEM648000)
        self.assertEqual((self.balance(5), self.balance(27)), (1425600, 712800))

    def test_goods_product_delivers_its_contents(self):
        _, delivered = self.recharge("com.tencent.fcicv.re10.android")  # 1 RMB pack, limit 1 forever
        state = self.game.systems._state(self.profile())
        self.assertEqual(state["items"]["60001"]["count"], 1)
        self.assertEqual(self.balance(5), 20)
        status, refused = self.call("POST", "/fci/purchasetouch/", {"productId": "com.tencent.fcicv.re10.android"})
        self.assertEqual((status, refused["error"]), (409, "purchase_limit_reached"))
        self.assertTrue(delivered["resChange"])

    def test_unknown_products_and_tokens_fall_through_to_economy(self):
        self.assertIsNone(self.call("POST", "/fci/purchasetouch/", {"productId": "local.sandbox.60"}))
        self.assertIsNone(self.call("POST", "/fci/purchasedelevery/", {"tradeData": "local-sandbox:other"}))
        self.assertIsNone(self.call("POST", "/fci/purchasedelevery/", {"transactionReceipt": "remote"}))
        self.assertIsNone(self.call("POST", "/fci/mall-trade", {"id": 10001}))
        self.assertIsNone(self.call("GET", "/fci/shop/"))
        self.assertEqual(self.call("PATCH", "/fci/syncmidas", b""), (200, {}))

    def test_level_gift_requires_level_and_respects_limit(self):
        self.add(7, 200000)
        status, refused = self.call("POST", "/fci/mall-trade", {"id": 25})  # limitLv 20, 75000 gold
        self.assertEqual((status, refused["error"]), (409, "player_level_too_low"))
        self.set_level(20)
        gold, money = self.balance(7), self.balance(5)
        status, result = self.call("POST", "/fci/mall-trade", {"id": 25})
        self.assertEqual(status, 200, result)
        self.assertEqual(set(result), {"resChange", "buyCount"})
        self.assertEqual(result["buyCount"], 1)
        self.assertEqual((self.balance(7), self.balance(5)), (gold - 75000, money + 500))
        deltas = {(c["baseRes"]["type"], c["baseRes"]["countDelta"]) for c in result["resChange"] if "baseRes" in c}
        self.assertIn((7, -75000), deltas)
        self.assertIn((5, 500), deltas)
        status, refused = self.call("POST", "/fci/mall-trade", {"id": 25})
        self.assertEqual((status, refused["error"]), (409, "mall_good_limit_reached"))
        self.assertEqual(self.balance(7), gold - 75000)
        info = self.call("GET", "/fci/mall-info/")[1]
        self.assertEqual(info["mallRecord"]["summary"]["25"]["buyCount"], 1)

    def test_level_gift_equipment_and_actor_rewards(self):
        self.set_level(30)
        self.add(7, 100000)
        status, result = self.call("POST", "/fci/mall-trade", {"id": 30})
        self.assertEqual(status, 200, result)
        equips = [c["equip"] for c in result["resChange"] if "equip" in c]
        expected = sorted(r["id"] for r in MallCatalog().mall[30]["reward"] if r.get("type") == 12)
        self.assertEqual(sorted(e["id"] for e in equips), expected)
        self.assertEqual(len(self.game.systems._state(self.profile()).get("equips", {})), len(expected))
        # Gift 1 (new-player pack) costs 1 paid 迷子 and contains actor 1009.
        self.recharge(GEM60)
        money = self.balance(5)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiMallTrade"))
        status, result = self.mall.handle_http("POST", "/fci/mall-trade", cls(id=1).SerializeToString(), self.profile())
        self.assertEqual(status, 200, result)
        self.assertEqual((self.balance(27), self.balance(5)), (59, money - 1))
        actors = [c["actor"] for c in result["resChange"] if "actor" in c]
        self.assertEqual([a["id"] for a in actors], [1009])
        self.assertEqual(actors[0]["countDelta"], 1)

    def test_event_gift_paid_mizi_and_daily_reset(self):
        rows = {r["id"]: r for r in self.mall.activity_gifts}
        daily = next(r for r in rows.values() if r.get("maxCountClearInterval") == 1
                     and r.get("resourceType") == 27 and r.get("maxBuyCount"))
        price, limit = daily["price"], daily["maxBuyCount"]
        status, refused = self.call("POST", "/fci/mall-trade", {"id": daily["id"]})
        self.assertEqual((status, refused["error"]), (409, "insufficient_resource"))
        self.add(27, price * (limit + 1))
        for count in range(1, limit + 1):
            status, result = self.call("POST", "/fci/mall-trade", {"id": daily["id"]})
            self.assertEqual((status, result.get("buyCount")), (200, count))
        self.assertEqual(self.call("POST", "/fci/mall-trade", {"id": daily["id"]})[0], 409)
        self.assertEqual(self.balance(27), price)
        self.now[0] += 86400
        info = self.call("GET", "/fci/mall-info/")[1]
        self.assertEqual(info["mallRecord"]["summary"][str(daily["id"])]["buyCount"], 0)
        status, result = self.call("POST", "/fci/mall-trade", {"id": daily["id"]})
        self.assertEqual((status, result["buyCount"]), (200, 1))

    def test_bound_mizi_is_spent_before_paid_mizi(self):
        gift = next(r for r in self.mall.activity_gifts if r.get("resourceType") == 5)
        self.add(5, gift["price"])
        self.add(27, 10)  # total = price + 10, paid = 10, bound = price
        status, _ = self.call("POST", "/fci/mall-trade", {"id": gift["id"]})
        self.assertEqual(status, 200)
        self.assertEqual((self.balance(5), self.balance(27)), (10, 10))

    def test_closed_activity_gifts_and_product_gifts_cannot_be_traded(self):
        opened = self.mall.open_activity_ids
        closed = next(r for r in _table("Mall") if r.get("giftPage") == 2 and r["activityId"] not in opened)
        self.assertEqual(self.call("POST", "/fci/mall-trade", {"id": closed["id"]})[1]["error"], "mall_activity_closed")
        product_gift = next(r for r in _table("Mall") if r.get("productId") and not r.get("isMonth"))
        self.assertEqual(self.call("POST", "/fci/mall-trade", {"id": product_gift["id"]})[0], 400)

    def test_activity_rows_open_every_event_gift(self):
        rows = self.mall.activity_rows()
        self.assertEqual({r["activityId"] for r in rows}, {r["activityId"] for r in self.mall.activity_gifts} | {37001})
        for row in rows:
            self.assertEqual(row["type"], 37 if row["activityId"] == 37001 else ACTIVITY_TYPE_MALL)
            self.assertEqual((row["timestampPreheat"], row["timestampEnd"], row["weekdays"]), (0, 0, []))
            self.assertLessEqual(row["timestampStart"], self.now[0])
        merged = self.mall.merge_activity_list("GET", "/fci/activity/", (200, [{"type": 12, "activityId": 1}]))
        self.assertEqual(len(merged[1]), 1 + len(rows))
        untouched = (200, {"result": {}})
        self.assertIs(self.mall.merge_activity_list("GET", "/fci/activity/", untouched), untouched)
        self.assertEqual(self.mall.redpoint_fields(self.profile())["mallInfoResult"]["mallRecord"]["summary"], {})

    @unittest.skipUnless(hasattr(LocalGame, "_handle_http_unlocked"), "LocalGame dispatch missing")
    def test_game_dispatch_when_wired(self):
        if getattr(self.game, "mall", None) is None:
            self.skipTest("LocalMall is not wired into LocalGame yet")
        status, info = self.game.handle_http("GET", "/fci/mall-info/", authorization=self.auth)
        self.assertEqual(status, 200)
        self.assertIn("mallRecord", info)
        status, activities = self.game.handle_http("GET", "/fci/activity/", authorization=self.auth)
        self.assertEqual(status, 200)
        self.assertTrue(any(a.get("type") == ACTIVITY_TYPE_MALL for a in activities))
        body = json.dumps({"productId": GEM60, "count": 60}).encode()
        status, touch = self.game.handle_http("POST", "/fci/purchasetouch/", body, "application/json", self.auth)
        self.assertEqual(status, 200)
        body = json.dumps({"tradeData": touch["tradeData"]}).encode()
        status, delivered = self.game.handle_http("POST", "/fci/purchasedelevery/", body, "application/json", self.auth)
        self.assertEqual((status, delivered["productId"]), (200, GEM60))


class IAPHandlerPatchTests(unittest.TestCase):
    @unittest.skipUnless(IAP_SOURCE.is_file(), "decompiled IAPHandler not present")
    def test_patch_replaces_midas_paths_and_compiles(self):
        patched = build_iaphandler_patch(IAP_SOURCE.read_text(encoding="utf-8"))
        purchase_impl = patched.split("function PurchaseImpl(", 1)[1].split("\nend\n", 1)[0]
        self.assertNotIn("m_iap", purchase_impl)
        self.assertIn('"fci/purchasedelevery/"', purchase_impl)
        get_info = patched.split("function GetActivityInfo(", 1)[1].split("\nend\n", 1)[0]
        self.assertIn('"fci/purchase-activity-info/"', get_info)
        self.assertIn('OnGetActivityInfo("{}")', get_info)
        self.assertNotIn("m_midasInfoCache.accessToken", patched)
        midas_proxy = patched.split("function MidasProxy(", 1)[1].split("\nend\n", 1)[0]
        self.assertNotIn("UpdateMidasUserInfo", midas_proxy)
        self.assertIn("function OnLocalDelivered(", patched)
        try:
            import lupa.lua53 as lua53
        except ImportError:
            self.skipTest("lupa not installed")
        runtime = lua53.LuaRuntime(encoding=None)
        loader = runtime.eval("function(src) local f, err = load(src, '=iaphandler', 't', {}); return f ~= nil, err end")
        ok, err = loader(patched.encode("utf-8"))
        self.assertTrue(ok, err)

    def test_patch_fails_loudly_on_unknown_source(self):
        with self.assertRaises(ValueError):
            build_iaphandler_patch("function Other()\nend\n")


if __name__ == "__main__":
    unittest.main()
