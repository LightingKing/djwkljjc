"""商店 / 体力金币购买 / 体力恢复 against the 2.0.4 client's reads.

- window__shop__shop.lua iterates fci/Shop/ as a list (v.baseInfo...) and
  stores fci/Shop/<id>/Goods/ as a list; the ShopBuy flow posts
  ApiShopTrade{info, shopId, buyCount} without an index.
- window__resourceexchange__resourceexchange.lua: GET fci/resbuyprice/<type>
  and POST fci/resbuyprice/resbuy/ read {resChange, cost, buy, remainBuyTimes}.
- window__resource__resource.lua polls fci/baseresource/update/ and predicts
  recovery from PlayerLevelInitConfig.incInfo (max / inc / incSeconds).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import client_compat  # noqa: E402
from accounts import LocalAccounts  # noqa: E402
from economy import (  # noqa: E402
    _client_rows,
    apply_energy_recovery,
    energy_policy,
    load_shop_catalog,
    next_shop_refresh,
)
from game import LocalGame  # noqa: E402
from server.core.clock import ServerClock  # noqa: E402

START = int(datetime(2026, 10, 26, 7, 0, tzinfo=timezone(timedelta(hours=8))).timestamp())  # Monday 07:00 Beijing


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


class _World:
    """A guest account on a LocalGame driven by an injectable clock."""

    def __init__(self, root: Path, now: int = START) -> None:
        self.now = [now]
        self.clock = ServerClock(source=lambda: self.now[0])
        self.accounts = LocalAccounts(root / "accounts", clock=self.clock.now)
        self.game = LocalGame(root / "game", self.accounts, clock=self.clock.now)
        self.game.daily_key = lambda value=None: self.clock.daily_key(value, refresh_at="06:00:00")
        self.account, self.auth = _guest(self.accounts)
        self.game._profile(self.account)  # profile row + recovery marker at ``now``

    def call(self, method: str, path: str, body=None, content_type: str = "application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def direct(self, method: str, path: str, body=None):
        """Economy without the HTTP auth/profile pass (clock may be days ahead)."""
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        return self.game.economy.handle_http(method, path, body, dict(self.account))

    def profile(self) -> dict:
        return self.game._profile(self.account)

    def resource(self, rtype: int, rid: int = 0) -> int:
        with closing(self.game._connect()) as connection:
            raw = connection.execute(
                "SELECT resources_json FROM game_profiles WHERE account_id = ?", (self.account["accountId"],)
            ).fetchone()[0]
        return sum(int(r.get("count", 0)) for r in json.loads(raw) if r.get("type") == rtype and r.get("id", 0) == rid)

    def set_resource(self, rtype: int, value: int) -> None:
        """Rewrite one balance in the durable row (no recovery side effects)."""
        account_id = self.account["accountId"]
        with self.game._lock, closing(self.game._connect()) as connection, connection:
            raw = connection.execute(
                "SELECT resources_json FROM game_profiles WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
            rows = [r for r in json.loads(raw) if not (r.get("type") == rtype and r.get("id", 0) == 0)]
            rows.append({"type": rtype, "id": 0, "count": int(value), "param": 0})
            connection.execute(
                "UPDATE game_profiles SET resources_json = ? WHERE account_id = ?", (json.dumps(rows), account_id)
            )

    def set_level(self, level: int) -> None:
        with self.game._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE game_profiles SET level = ? WHERE account_id = ?", (int(level), self.account["accountId"])
            )

    def marker(self) -> int:
        with closing(self.game._connect()) as connection:
            return int(connection.execute(
                "SELECT last_recovery_at FROM local_resource_recovery WHERE account_id = ?",
                (self.account["accountId"],),
            ).fetchone()[0])


class ShopCatalogTests(unittest.TestCase):
    def test_catalog_is_built_from_shop_sourced_client_rows(self):
        catalog = load_shop_catalog()
        shops = {spec["shopId"]: spec for spec in catalog["shops"]}
        # m_commonShopId = 1, m_EQUIP_SHOP_ID = 11, GuildShopId = 12.
        self.assertEqual(list(shops), [1, 11, 12, 20, 21, 23])
        self.assertTrue(shops[12]["baseInfo"]["guild"])
        items = {row["id"]: row for row in _client_rows("ItemInfo")}
        equips = {row["equipId"]: row for row in _client_rows("EquipInfo")}
        for spec in catalog["shops"]:
            self.assertEqual(spec["baseInfo"]["maxPosition"], sum(count for _, count in spec["slots"]))
            for pool_name, _count in spec["slots"]:
                self.assertTrue(spec["pools"][pool_name], (spec["shopId"], pool_name))
            for pool in spec["pools"].values():
                for good in pool:
                    rows = items if good["good"]["type"] == 11 else equips
                    # Existing shop goods retain Goto 0. Reopened autumn
                    # shop 20 additionally supplies the native claw ticket
                    # (Goto 25), with its local price recorded in shop.yaml.
                    expected_source = 25 if spec['shopId'] == 20 and good['good']['id'] == 61001 else 0
                    self.assertIn(expected_source, rows[good["good"]["id"]].get("sources", []), good)
                    self.assertTrue(good["cost"] and good["good"]["count"] > 0)
        # Every equip of the DropDisplayShop suits shown by Window_RuleShop
        # for the equip shop can be offered by shop 11.
        offered = {good["good"]["id"] for good in shops[11]["pools"]["equips"]}
        suits = {row["id"]: row for row in _client_rows("EquipSuitAttr")}
        displayed = {
            equip_id
            for row in _client_rows("DropDisplayShop") if row.get("shopId") == 11
            for equip_id in suits[row["id"]]["equipId"] if equip_id in equips
        }
        self.assertTrue(displayed)
        self.assertLessEqual(displayed, offered)

    def test_next_refresh_follows_shop_type_fields(self):
        daily = {"refreshTimeInDay": "06:00:00"}
        self.assertEqual(next_shop_refresh(daily, START), START + 23 * 3600)
        self.assertEqual(next_shop_refresh(daily, START - 3601), START - 3600)
        weekly = {"refreshTimeInDay": "06:00:00", "refreshDayOfWeek": 1}  # Monday
        self.assertEqual(next_shop_refresh(weekly, START), START - 3600 + 7 * 86400)
        monthly = {"refreshTimeInDay": "06:00:00", "refreshDayOfMonth": 1}
        self.assertEqual(
            next_shop_refresh(monthly, START),
            int(datetime(2026, 11, 1, 6, tzinfo=timezone(timedelta(hours=8))).timestamp()),
        )


class ShopRouteTests(unittest.TestCase):
    def test_shop_list_and_goods_are_plain_lists_the_window_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            status, shops = world.call("GET", "/fci/Shop/")
            self.assertEqual(status, 200)
            self.assertEqual([shop["baseInfo"]["shopId"] for shop in shops], [1, 11, 12, 20, 21, 23])
            for shop in shops:
                base = shop["baseInfo"]
                for key in ("playerLevel", "clientShow", "activity", "refreshLimit", "refreshFree",
                            "displayCost", "vipSuperExtraCount"):
                    self.assertIn(key, base)
                # OnShopInfo does value.endTime ~= 0 and endTime - serverUtc.
                self.assertEqual(shop["endTime"], 0)
                self.assertGreater(shop["nextRefreshTime"], world.now[0])
                self.assertTrue(shop["newShop"])
            # The refresh button exists only when nextRefreshCost is sent.
            self.assertEqual([("nextRefreshCost" in shop) for shop in shops], [True, True, False, False, False, False])
            self.assertEqual(shops[1]["baseInfo"]["displayCost"][0], {"type": 7, "id": 0, "count": 0, "param": 0})

            status, goods = world.call("GET", "/fci/Shop/1/Goods/")
            self.assertEqual(status, 200)
            self.assertEqual([good["position"] for good in goods], list(range(8)))
            for good in goods:
                self.assertEqual(set(good["good"]), {"type", "id", "count", "param"})
                self.assertEqual(set(good["cost"][0]), {"type", "id", "count", "param"})
                self.assertEqual((good["buyCountRecord"], good["sold"], good["guildLevelLimit"]), (0, False, 0))
            self.assertEqual(len({good["shopGoodId"] for good in goods}), 8)

            # Window_Shop: Patch({new_shop = false}) when the tab is opened.
            status, patched = world.call("PATCH", "/fci/Shop/1", {"new_shop": False})
            self.assertEqual((status, patched["newShop"]), (200, False))
            again = LocalGame(Path(temp) / "game", world.accounts, clock=world.clock.now)
            _status, shops = again.handle_http("GET", "/fci/Shop/", None, None, world.auth)
            self.assertEqual([shop["newShop"] for shop in shops], [False, True, True, True, True, True])

    def test_trade_buy_count_limit_bag_and_task_progress(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(7, 500_000)
            recorded = []
            original = world.game.tasks.record
            world.game.tasks.record = lambda profile, goal, amount=1, **kw: recorded.append((goal, amount, kw)) or original(profile, goal, amount, **kw)
            _status, goods = world.call("GET", "/fci/Shop/1/Goods/")
            good = next(g for g in goods if g["buyLimit"] == 2 and g["cost"][0]["type"] == 7)
            price = good["cost"][0]["count"]
            status, result = world.call("POST", "/fci/ShopTrade", {"shopId": 1, "buyCount": 2, "info": good})
            self.assertEqual(status, 200, result)
            self.assertEqual((result["buyCount"], result["info"]["sold"]), (2, True))
            self.assertEqual(world.resource(7), 500_000 - 2 * price)
            bag = world.game.systems._state(world.profile())["items"]
            self.assertEqual(bag[str(good["good"]["id"])]["count"], 2 * good["good"]["count"])
            self.assertIn(("ShopP0BuyMTimes", 1, {"param": 1}), recorded)
            status, again = world.call("POST", "/fci/ShopTrade", {"shopId": 1, "info": good})
            self.assertEqual((status, again["error"]), (409, "shop_good_limit_reached"))
            self.assertEqual(world.resource(7), 500_000 - 2 * price)

    def test_unaffordable_trade_changes_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(7, 0)
            _status, goods = world.call("GET", "/fci/Shop/1/Goods/")
            good = next(g for g in goods if g["cost"][0]["type"] == 7)
            status, result = world.call("POST", "/fci/ShopTrade", {"shopId": 1, "info": good})
            self.assertEqual((status, result["error"]), (409, "insufficient_resource"))
            _status, after = world.call("GET", "/fci/Shop/1/Goods/")
            self.assertEqual(after, goods)
            self.assertNotIn(str(good["good"]["id"]), world.game.systems._state(world.profile()).get("items", {}))

    def test_equip_shop_grants_an_equipment_instance(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(7, 1_000_000)
            _status, goods = world.call("GET", "/fci/Shop/11/Goods/")
            self.assertEqual(len(goods), 6)
            # Level 1 + levelWindow 10: only the requireLevel 10 equips.
            equips = {row["equipId"]: row for row in _client_rows("EquipInfo")}
            self.assertEqual({equips[g["good"]["id"]]["requireLevel"] for g in goods}, {10})
            status, result = world.call("POST", "/fci/ShopTrade", {"shopId": 11, "info": goods[3]})
            self.assertEqual(status, 200, result)
            granted = [change["equip"] for change in result["resChange"] if "equip" in change]
            self.assertEqual([equip["id"] for equip in granted], [goods[3]["good"]["id"]])
            stored = world.game.systems._state(world.profile())["equips"]
            self.assertIn(goods[3]["good"]["id"], {record["id"] for record in stored.values()})

    def test_paid_refresh_cost_and_limit_and_guild_shop_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(7, 100_000)
            for count in range(1, 6):
                status, result = world.call("POST", "/fci/Shop/1/Goods/", {"refresh_free": False})
                self.assertEqual((status, result["refreshCount"]), (200, count))
                self.assertEqual(result["resChange"][0]["baseRes"]["countDelta"], -10000)
            status, result = world.call("POST", "/fci/Shop/1/Goods/", {"refresh_free": False})
            self.assertEqual((status, result["error"]), (409, "shop_refresh_limit_reached"))
            self.assertEqual(world.resource(7), 50_000)
            status, result = world.call("POST", "/fci/Shop/12/Goods/", {"refresh_free": False})
            self.assertEqual((status, result["error"]), (409, "shop_refresh_unavailable"))
            # The guild shop opens at level 23 (UnlockWindow_Guild).
            _status, goods = world.call("GET", "/fci/Shop/12/Goods/")
            status, result = world.call("POST", "/fci/ShopTrade", {"shopId": 12, "info": goods[0]})
            self.assertEqual((status, result["error"]), (403, "shop_locked"))

    def test_legacy_single_test_shop_is_rebuilt_from_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            legacy = {"shop": [{"shopId": 1, "goods": [{"position": 0, "shopGoodId": 10001,
                                                       "good": {"type": 11, "id": 20001, "count": 1}}]}],
                      "pending": {}, "purchases": {}}
            world.game.economy._state(world.account)
            world.game.economy._save_state(world.account, legacy)
            _status, shops = world.call("GET", "/fci/Shop/")
            self.assertEqual(len(shops), 6)
            _status, goods = world.call("GET", "/fci/Shop/1/Goods/")
            self.assertNotIn(10001, {good["shopGoodId"] for good in goods})
            self.assertEqual(len(goods), 8)


class ResBuyTests(unittest.TestCase):
    def test_power_price_follows_refresh_price_rows_with_daily_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(5, 10_000)
            status, quote = world.call("GET", "/fci/resbuyprice/10001")
            self.assertEqual(status, 200)
            self.assertEqual((quote["cost"]["count"], quote["buy"], quote["remainBuyTimes"]),
                             (60, {"type": 6, "id": 0, "count": 100, "param": 0}, 4))
            spent = 0
            for nth, (price, next_price) in enumerate([(60, 80), (80, 100), (100, 150), (150, 150)], start=1):
                world.set_resource(6, 0)
                status, result = world.call("POST", "/fci/resbuyprice/resbuy/",
                                            {"resbuypriceType": 10001})
                self.assertEqual(status, 200, result)
                spent += price
                changes = {change["baseRes"]["type"]: change["baseRes"] for change in result["resChange"]}
                self.assertEqual(changes[5]["countDelta"], -price)
                self.assertEqual((changes[6]["countDelta"], changes[6]["count"]), (100, 100))
                # SetView shows the next purchase.
                self.assertEqual((result["cost"]["count"], result["remainBuyTimes"]), (next_price, 4 - nth))
                self.assertEqual(result["buy"]["count"], 100)
            self.assertEqual(world.resource(5), 10_000 - spent)
            world.set_resource(6, 0)
            status, result = world.call("POST", "/fci/resbuyprice/resbuy/", {"resbuypriceType": 10001})
            self.assertEqual((status, result["error"]), (409, "resbuy_no_times"))
            _status, quote = world.call("GET", "/fci/resbuyprice/10001")
            self.assertEqual(quote["remainBuyTimes"], 0)
            # The counter resets at the next 06:00 daily bucket.
            world.now[0] += 86400
            status, quote = world.direct("GET", "/fci/resbuyprice/10001/")
            self.assertEqual((status, quote["cost"]["count"], quote["remainBuyTimes"]), (200, 60, 4))

    def test_power_purchase_needs_power_below_cap_and_may_exceed_it(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(5, 1000)
            # Level 1: incInfo.max = 60; Window_ReachPowerLimit at/above it.
            world.set_resource(6, 60)
            status, result = world.call("POST", "/fci/resbuyprice/resbuy/", {"resbuypriceType": 10001})
            self.assertEqual((status, result["error"], result["max"]), (409, "power_reach_limit", 60))
            self.assertEqual(world.resource(5), 1000)
            world.set_resource(6, 59)
            status, result = world.call("POST", "/fci/resbuyprice/resbuy/", {"resbuypriceType": 10001})
            self.assertEqual(status, 200, result)
            self.assertEqual(world.resource(6), 159)
            # Above the cap nothing recovers and nothing is clamped.
            world.now[0] += 450 * 10
            status, hud = world.direct("GET", "/fci/baseresource/update/")
            self.assertEqual(status, 200)
            self.assertEqual(hud["resChange"][0]["baseRes"]["count"], 159)
            self.assertEqual(hud["lastResIncTime"], {"6": world.now[0]})
            self.assertEqual(world.resource(6), 159)

    def test_unlimited_gold_pack_accepts_wireformat(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(5, 100)
            # ApiResBuy{resbuyprice_type(1) = 1000201}; resbuyprice.proto is a
            # damaged descriptor the pool skips, so encode it by hand.
            value, wire = 1000201, bytearray([0x08])  # key: field 1, varint
            while value > 0x7F:
                wire.append((value & 0x7F) | 0x80)
                value >>= 7
            wire.append(value)
            gold = world.resource(7)
            status, result = world.call("POST", "/fci/resbuyprice/resbuy/", bytes(wire),
                                        "application/wireformat")
            self.assertEqual(status, 200, result)
            self.assertTrue(result["buyResult"])
            self.assertNotIn("remainBuyTimes", result)  # no count column: unlimited
            self.assertEqual((world.resource(5), world.resource(7)), (40, gold + 60000))
            status, result = world.call("POST", "/fci/resbuyprice/resbuy/", {"resbuypriceType": 1000201})
            self.assertEqual((status, result["error"]), (409, "insufficient_resource"))

    def test_buyless_type_charges_counts_and_runs_module_hook(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.set_resource(5, 100)
            calls = []
            # RefreshPriceType_DeployTimes (10): Window_Deploy.Refresh posts
            # resbuy and then re-reads fci/deploy/mission/.
            world.game.economy.resbuy_hooks[10] = lambda profile, params: calls.append(params) or [
                {"baseRes": {"type": 0, "id": 0, "param": 0, "count": 0, "countDelta": 0}}]
            status, result = world.call("POST", "/fci/resbuyprice/resbuy/", {"resbuypriceType": 10})
            self.assertEqual(status, 200, result)
            self.assertEqual(calls, [0])
            self.assertEqual((result["cost"]["count"], result["remainBuyTimes"]), (15, 9))
            self.assertEqual(len(result["resChange"]), 2)
            self.assertEqual(world.resource(5), 85)

    def test_unknown_types_and_stateless_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            status, quote = world.call("GET", "/fci/resbuyprice/99999")
            self.assertEqual((status, quote["remainBuyTimes"], quote["cost"]["count"]), (200, 0, 0))
            status, result = world.call("POST", "/fci/resbuyprice/resbuy/", {"resbuypriceType": 99999})
            self.assertEqual((status, result["error"]), (404, "unknown_price_type"))
            status, quote = world.call("GET", "/fci/resbuyprice/10007")  # ChgName (Window_Rename)
            self.assertEqual((status, quote["cost"]), (200, {"type": 5, "id": 0, "count": 200, "param": 0}))
        status, quote = client_compat.handle_http(SimpleNamespace(), "GET", "/fci/resbuyprice/10001", None, {})
        self.assertEqual((status, quote["cost"]["count"], quote["remainBuyTimes"]), (200, 60, 4))


class EnergyTests(unittest.TestCase):
    def test_policy_is_the_client_player_level_table(self):
        self.assertEqual({k: energy_policy(1)[k] for k in ("type", "max", "inc", "incSeconds", "limit")},
                         {"type": 6, "max": 60, "inc": 1, "incSeconds": 450, "limit": 120})
        self.assertEqual(energy_policy(50)["max"], 158)
        self.assertEqual(energy_policy(99)["max"], 256)
        self.assertEqual(energy_policy(500)["max"], 256)
        self.assertEqual(energy_policy(0)["max"], 60)

    def test_recovery_uses_level_cap_interval_and_never_clamps(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            world.profile()  # creates the recovery marker at START
            start = world.marker()
            account_id = world.account["accountId"]

            def run(at: int, level: int = 10) -> dict:
                with world.game._lock, closing(world.game._connect()) as connection, connection:
                    raw = connection.execute(
                        "SELECT resources_json FROM game_profiles WHERE account_id = ?", (account_id,)
                    ).fetchone()[0]
                    resources = json.loads(raw)
                    return apply_energy_recovery(connection, account_id, resources, now=at, level=level)

            world.set_resource(6, 70)  # level 10: max 78
            info = run(start + 450 * 3 + 100)
            self.assertEqual((info["recovered"], info["lastRecoveryAt"]), (3, start + 1350))
            self.assertEqual(world.resource(6), 73)
            info = run(start + 450 * 30)
            self.assertEqual((info["recovered"], world.resource(6)), (5, 78))
            self.assertEqual(info["lastRecoveryAt"], start + 450 * 30)
            world.set_resource(6, 150)  # e.g. a level reward or purchase
            info = run(start + 450 * 60)
            self.assertEqual((info["recovered"], world.resource(6)), (0, 150))
            self.assertEqual(info["lastRecoveryAt"], start + 450 * 60)
            # Clock rollback re-anchors without minting.
            world.set_resource(6, 10)
            info = run(start + 450 * 50)
            self.assertEqual((info["recovered"], info["lastRecoveryAt"], world.resource(6)), (0, start + 450 * 50, 10))
            # Re-running the same instant does not grant twice.
            self.assertEqual(run(start + 450 * 52)["recovered"], 2)
            self.assertEqual(run(start + 450 * 52)["recovered"], 0)
            with closing(world.game._connect()) as connection:
                deltas = [row[0] for row in connection.execute(
                    "SELECT delta FROM local_resource_ledger WHERE account_id = ? AND reason = 'passive_energy_recovery'",
                    (account_id,),
                )]
            self.assertEqual(deltas, [3, 5, 2])

    def test_hud_update_route_serves_recovery_state(self):
        with tempfile.TemporaryDirectory() as temp:
            world = _World(Path(temp))
            status, hud = world.call("GET", "/fci/baseresource/update/")
            self.assertEqual(status, 200)
            self.assertEqual(hud["resChange"][0]["baseRes"]["type"], 6)
            self.assertEqual(hud["lastResIncTime"], {"6": world.marker()})
            # Level 1 (max 60): two 450 s ticks since the marker.
            marker = world.marker()
            world.set_resource(6, 50)
            world.now[0] = marker + 2 * 450 + 30
            status, hud = world.direct("GET", "/fci/baseresource/update/")
            self.assertEqual(status, 200)
            self.assertEqual(hud["resChange"], [{"baseRes": {"type": 6, "id": 0, "param": 0, "count": 52, "countDelta": 2}}])
            self.assertEqual(hud["lastResIncTime"], {"6": marker + 900})
            self.assertEqual(world.resource(6), 52)


if __name__ == "__main__":
    unittest.main()
