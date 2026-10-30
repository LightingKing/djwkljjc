from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import sys

from google.protobuf import message_factory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accounts import LocalAccounts
from game import LocalGame
from server.core.clock import ServerClock


class EconomyRoutesTests(unittest.TestCase):
    @staticmethod
    def _guest(accounts: LocalAccounts) -> tuple[dict, str]:
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        account_token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle(
            "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", account_token
        )[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", account_token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        second_token = "AccountToken " + second[1]["accountToken"]
        auth_body = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
        auth = accounts.handle("POST", "/Auth/", auth_body, "application/wireformat", second_token)
        account = accounts.verify_access_token(auth[1]["tokenAccess"])
        assert account is not None
        with closing(accounts._connect()) as connection:
            token = connection.execute(
                "SELECT access_token FROM accounts WHERE account_id = ?", (account["accountId"],)
            ).fetchone()[0]
        return account, "Token " + token

    @staticmethod
    def _gold(game: LocalGame, account: dict) -> int:
        return next(r["count"] for r in game._profile(account)["resources"] if r["type"] == 7)

    def test_shop_purchase_spends_gold_and_persists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)
            # Window_Shop iterates fci/Shop/ as a list reading v.baseInfo.
            status, shops = game.handle_http("GET", "/fci/Shop", authorization=auth)
            self.assertEqual(status, 200)
            self.assertIsInstance(shops, list)
            self.assertEqual(shops[0]["baseInfo"]["shopId"], 1)
            status, goods = game.handle_http("GET", "/fci/Shop/1/Goods", authorization=auth)
            self.assertEqual(status, 200)
            self.assertIsInstance(goods, list)
            good = next(g for g in goods if g["cost"][0]["type"] == 7 and g["good"]["type"] == 11)
            price = good["cost"][0]["count"]
            status, result = game.handle_http(
                "POST",
                "/fci/ShopTrade/",
                json.dumps({"shopId": 1, "buyCount": 1, "info": good}).encode(),
                "application/json",
                auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["buyCount"], 1)
            self.assertIn({"type": 7, "id": 0, "param": 0, "count": 20000 - price, "countDelta": -price},
                          [change["baseRes"] for change in result["resChange"]])
            self.assertEqual(self._gold(game, account), 20000 - price)
            # Items land in the bag (fci/item/), not in the resource ledger.
            items = game.systems._state(game._profile(account))["items"]
            self.assertEqual(items[str(good["good"]["id"])]["count"], good["good"]["count"])
            game_again = LocalGame(root / "game", accounts)
            status, goods_again = game_again.handle_http("GET", "/fci/Shop/1/Goods", authorization=auth)
            self.assertEqual(status, 200)
            again = next(g for g in goods_again if g["position"] == good["position"])
            self.assertEqual((again["shopGoodId"], again["buyCountRecord"]), (good["shopGoodId"], 1))

    def test_shop_goods_post_refresh_uses_descriptor_and_local_cost(self):
        """A paid refresh accepts the ApiShopGoods wireformat and charges the shop cost."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)
            shop_cls = message_factory.GetMessageClass(
                game._pool.FindMessageTypeByName("ApiShopGoods")
            )
            request = shop_cls(refresh_free=False)
            status, result = game.handle_http(
                "POST",
                "/fci/Shop/1/Goods",
                request.SerializeToString(),
                "application/wireformat",
                auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["refreshCount"], 1)
            self.assertEqual(len(result["info"]), 8)
            self.assertEqual(result["resChange"][0]["baseRes"]["countDelta"], -10000)
            game_again = LocalGame(root / "game", accounts)
            status, shops = game_again.handle_http(
                "GET", "/fci/Shop", authorization=auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(shops[0]["refreshCount"], 1)

    def test_shop_get_applies_overdue_daily_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = [1_800_000_000]
            clock = ServerClock(source=lambda: now[0])
            accounts = LocalAccounts(root / "accounts", clock=clock.now)
            game = LocalGame(root / "game", accounts, clock=clock.now)
            account, auth = self._guest(accounts)
            status, _paid = game.handle_http(
                "POST", "/fci/Shop/1/Goods", b'{"refresh_free":false}', "application/json", auth
            )
            self.assertEqual(status, 200)
            _status, goods = game.handle_http("GET", "/fci/Shop/1/Goods", authorization=auth)
            good = min((g for g in goods if g["cost"][0]["type"] == 7), key=lambda g: g["cost"][0]["count"])
            ok, _changes, error = game.economy._change_resources(
                game._profile(account), [{"type": 7, "id": 0, "param": 0, "count": good["cost"][0]["count"]}]
            )
            self.assertTrue(ok, error)
            status, _trade = game.handle_http(
                "POST", "/fci/ShopTrade/", json.dumps({"shopId": 1, "info": good}).encode(),
                "application/json", auth,
            )
            self.assertEqual(status, 200)
            _status, shops = game.handle_http("GET", "/fci/Shop", authorization=auth)
            boundary = shops[0]["nextRefreshTime"]
            self.assertEqual(shops[0]["refreshCount"], 1)
            now[0] = boundary
            status, shops = game.handle_http("GET", "/fci/Shop", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(shops[0]["refreshCount"], 0)
            self.assertEqual(shops[0]["nextRefreshTime"], boundary + 86400)
            status, after = game.handle_http("GET", "/fci/Shop/1/Goods", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual({g["buyCountRecord"] for g in after}, {0})

    def test_shop_free_refresh_is_available_once_per_local_day(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = [1_800_000_000]
            clock = ServerClock(source=lambda: now[0])
            accounts = LocalAccounts(root / "accounts", clock=clock.now)
            game = LocalGame(root / "game", accounts, clock=clock.now)
            _account, auth = self._guest(accounts)
            status, free = game.handle_http(
                "POST", "/fci/Shop/1/Goods", b'{"refreshFree":true}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(free["resChange"], [])
            self.assertEqual(free["refreshTimeFree"], now[0])
            status, duplicate = game.handle_http(
                "POST", "/fci/Shop/1/Goods", b'{"refreshFree":true}', "application/json", auth
            )
            self.assertEqual((status, duplicate["error"]), (409, "shop_free_refresh_unavailable"))
            # OnRefresh shows the next free refresh at refreshTimeFree + baseInfo.refreshFree.
            _status, shops = game.handle_http("GET", "/fci/Shop", authorization=auth)
            self.assertEqual(shops[0]["nextFreeRefreshTime"], now[0] + shops[0]["baseInfo"]["refreshFree"])
            now[0] += shops[0]["baseInfo"]["refreshFree"]
            # (direct call: the access token would expire over a simulated day)
            status, _again = game.economy.handle_http(
                "POST", "/fci/Shop/1/Goods", b'{"refresh_free":true}', _account
            )
            self.assertEqual(status, 200)
    def test_resource_batch_rejects_duplicate_costs_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, _auth = self._guest(accounts)
            profile = game._profile(account)
            before = next(item["count"] for item in profile["resources"] if item["type"] == 7)
            ok, changes, error = game.economy._change_resources(
                profile,
                [
                    {"type": 7, "id": 0, "param": 0, "count": -(before // 2)},
                    {"type": 7, "id": 0, "param": 0, "count": -(before // 2 + 1)},
                ],
            )
            self.assertFalse(ok)
            self.assertEqual((changes, error), ([], "insufficient_resource"))
            after = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 7)
            self.assertEqual(after, before)

    def test_recharge_model_sandbox_routes_cover_local_rewards_without_real_payment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)

            status, info = game.handle_http("GET", "/fci/recharge/info/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertTrue(info["sandbox"])
            self.assertGreaterEqual(len(info["fixedRechargeInfo"]), 3)

            status, checked = game.handle_http(
                "POST", "/fci/recharge/before-buy-check/", b'{"rechargeId":11}', "application/json", auth
            )
            self.assertEqual((status, checked["sandbox"]), (200, True))
            status, free = game.handle_http(
                "POST", "/fci/recharge/free/", b'{"rechargeId":11}', "application/json", auth
            )
            self.assertEqual((status, free["sandbox"]), (200, True))

            status, voucher = game.handle_http(
                "POST", "/fci/recharge/voucher/", b'{"rechargeId":51}', "application/json", auth
            )
            self.assertEqual(status, 200)
            status, duplicate = game.handle_http(
                "POST", "/fci/recharge/voucher/", b'{"rechargeId":51}', "application/json", auth
            )
            self.assertEqual((status, duplicate["error"]), (409, "recharge_reward_already_claimed"))

            for path, body in (
                ("/fci/recharge/gift-box/", {"giftboxId": 4005}),
                ("/fci/recharge/first-reward/", {}),
                ("/fci/recharge/month-card/reward/", {"monthCardCfgId": 1}),
                ("/fci/recharge/permit/buy-level/", {"permitId": 1, "level": 1}),
                ("/fci/recharge/permit/task-reward/", {"permitId": 1, "level": 1}),
                ("/fci/recharge/permit/level-reward/", {"permitId": 1, "level": 1}),
                ("/fci/recharge/platform-active-code/", {"code": "LOCAL-TEST-1"}),
            ):
                status, payload = game.handle_http(
                    "POST", path, json.dumps(body).encode(), "application/json", auth
                )
                self.assertEqual(status, 200, (path, payload))
                self.assertTrue(payload["sandbox"])

            status, duplicate_gift = game.handle_http(
                "POST", "/fci/recharge/gift-box/", b'{"giftboxId":4005}', "application/json", auth
            )
            self.assertEqual((status, duplicate_gift["error"]), (409, "recharge_reward_already_claimed"))

            paid = next(r["count"] for r in game._profile(account)["resources"] if r["type"] == 5)
            self.assertGreaterEqual(paid, 60 + 300 + 300 + 60 + 30 + 50 + 50 + 100)

    def test_concurrent_one_time_voucher_claim_is_serialized(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)

            def claim():
                return game.handle_http(
                    "POST", "/fci/recharge/voucher/", b'{"rechargeId":51}', "application/json", auth
                )[0]

            with ThreadPoolExecutor(max_workers=8) as pool:
                statuses = list(pool.map(lambda _index: claim(), range(8)))
            self.assertEqual(statuses.count(200), 1)
            self.assertEqual(statuses.count(409), 7)
            paid = next(r["count"] for r in game._profile(account)["resources"] if r["type"] == 5)
            self.assertEqual(paid, 300)

    def test_concurrent_claims_from_two_game_instances_are_serialized(self):
        """A hot reload must not reopen the same store with a private lock."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            account, auth = self._guest(accounts)
            game_a = LocalGame(root / "game", accounts)
            game_b = LocalGame(root / "game", accounts)
            game_a._profile(account)

            def claim(game):
                return game.handle_http(
                    "POST", "/fci/recharge/voucher/", b'{"rechargeId":51}',
                    "application/json", auth
                )[0]

            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(claim, (game_a, game_b)))
            self.assertEqual(statuses.count(200), 1)
            self.assertEqual(statuses.count(409), 1)
            paid = next(r["count"] for r in game_a._profile(account)["resources"] if r["type"] == 5)
            self.assertEqual(paid, 300)

    def test_recharge_uses_one_time_local_sandbox_token(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)
            body = json.dumps({"productId": "local.sandbox.60", "count": 1}).encode()
            status, touch = game.handle_http("POST", "/fci/purchasetouch", body, "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(touch.DESCRIPTOR.full_name, "ApiPurchaseTouchProduct.result")
            self.assertTrue(touch.is_sandbox)
            token = touch.trade_data
            status, rejected = game.handle_http(
                "POST", "/fci/purchasedelevery", b'{"transactionReceipt":"remote-receipt"}', "application/json", auth
            )
            self.assertEqual(status, 402)
            self.assertEqual(rejected["error"], "payment_verification_required")
            status, delivered = game.handle_http(
                "POST",
                "/fci/purchasedelevery",
                json.dumps({"tradeData": token}).encode(),
                "application/json",
                auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(delivered.DESCRIPTOR.full_name, "ApiPurchaseDelevery.result")
            self.assertTrue(delivered.transaction_id)
            paid = next(r["count"] for r in game._profile(account)["resources"] if r["type"] == 5)
            self.assertEqual(paid, 60)
            status, duplicate = game.handle_http(
                "POST",
                "/fci/purchasedelevery",
                json.dumps({"tradeData": token}).encode(),
                "application/json",
                auth,
            )
            self.assertEqual(status, 402)
            self.assertEqual(duplicate["error"], "payment_verification_required")

    def test_admin_billing_snapshot_lists_delivery_without_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)
            body = json.dumps({"productId": "local.sandbox.60", "count": 1}).encode()
            status, touch = game.handle_http("POST", "/fci/purchasetouch", body, "application/json", auth)
            self.assertEqual(status, 200)
            token = touch.trade_data
            snapshot = game.economy.admin_billing_snapshot()
            self.assertEqual(snapshot["pendingOrderCount"], 1)
            self.assertNotIn(token, json.dumps(snapshot, ensure_ascii=False))
            status, delivered = game.handle_http(
                "POST", "/fci/purchasedelevery", json.dumps({"tradeData": token}).encode(), "application/json", auth
            )
            self.assertEqual(status, 200)
            snapshot = game.economy.admin_billing_snapshot()
            self.assertEqual(snapshot["pendingOrderCount"], 0)
            self.assertEqual(snapshot["totalRecords"], 1)
            self.assertEqual(snapshot["records"][0]["productId"], "local.sandbox.60")
            self.assertEqual(snapshot["records"][0]["transactionId"], delivered.transaction_id)

    def test_native_wireformat_shop_trade_matches_info_not_default_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)

            _status, goods = game.handle_http("GET", "/fci/Shop/1/Goods", authorization=auth)
            # Window_ShopBuy sends ApiShopTrade{info, shopId, buyCount} with no
            # index: the good is matched by info.shopGoodId/position, never
            # by the proto3 default index 0.
            target = next(g for g in goods if g["position"] > 0 and g["cost"][0]["type"] == 7)
            shop_cls = message_factory.GetMessageClass(
                game._pool.FindMessageTypeByName("ApiShopTrade")
            )
            trade = shop_cls(shop_id=1, buy_count=1)
            trade.info.shopGoodId = target["shopGoodId"]
            trade.info.position = target["position"]
            status, shop_result = game.handle_http(
                "POST",
                "/fci/ShopTrade/",
                trade.SerializeToString(),
                "application/wireformat",
                auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(shop_result["buyCount"], 1)
            self.assertEqual(shop_result["info"]["shopGoodId"], target["shopGoodId"])
            _status, after = game.handle_http("GET", "/fci/Shop/1/Goods", authorization=auth)
            self.assertEqual(next(g for g in after if g["position"] == 0)["buyCountRecord"], 0)

    def test_month_card_uses_configured_six_am_reset_bucket(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = [1767247140]  # 2026-01-01 05:59 UTC
            clock = ServerClock(source=lambda: now[0])
            accounts = LocalAccounts(root / "accounts", clock=clock.now)
            game = LocalGame(root / "game", accounts, clock=clock.now)
            game.daily_key = lambda value=None: clock.daily_key(value, refresh_at="06:00:00")
            account, auth = self._guest(accounts)

            status, _first = game.handle_http(
                "POST", "/fci/recharge/month-card/reward/", b'{"monthCardCfgId":1}', "application/json", auth
            )
            self.assertEqual(status, 200)
            status, duplicate = game.handle_http(
                "POST", "/fci/recharge/month-card/reward/", b'{"monthCardCfgId":1}', "application/json", auth
            )
            self.assertEqual((status, duplicate["error"]), (409, "month_card_reward_already_claimed"))

            now[0] += 60
            status, _second = game.handle_http(
                "POST", "/fci/recharge/month-card/reward/", b'{"monthCardCfgId":1}', "application/json", auth
            )
            self.assertEqual(status, 200)
            paid = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 5)
            self.assertEqual(paid, 60)


if __name__ == "__main__":
    unittest.main()
