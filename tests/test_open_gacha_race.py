"""Native hidden UP/point-shop/race contracts, using isolated account state."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from google.protobuf import json_format
from lupa.lua53 import LuaRuntime
import yaml

from game import LocalGame
from external_assets import external_path, require_file
import test_gacha as gacha_fixtures
import test_race_client as race_fixtures


ROOT = Path(__file__).resolve().parents[1]
UP_IDS = list(range(14019, 14032)) + list(range(14034, 14041))


class OpenGachaTests(unittest.TestCase):
    setUp, tearDown = gacha_fixtures.GachaTests.setUp, gacha_fixtures.GachaTests.tearDown
    call, draw, _give_tickets = gacha_fixtures.GachaTests.call, gacha_fixtures.GachaTests.draw, gacha_fixtures.GachaTests._give_tickets

    def activities(self, *_args):
        return 200, [{"type": 14, "activityId": aid} for aid in UP_IDS] + [{"type": 13, "activityId": 13001}]

    def points(self):
        fresh = self.game._profile(self.accounts.verify_access_token(self.auth.split()[1]))
        return next((r["count"] for r in fresh["resources"] if r["type"] == 23), 0)

    def test_normal_up_native_list_and_independent_91_draw_pity_survive_restart(self):
        self._give_tickets(200)
        self.game.systems._rng = gacha_fixtures.FixedRollRandom(0.999)
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            pools = self.call("GET", "/fci/gachainfo/")[1]
            normal = next(row for row in pools if row["id"] == 1)
            self.assertEqual({x["activityId"] for x in normal["activityInfo"]}, {0, *UP_IDS})
            status, result = self.draw(90, activity_id=14020)
            self.assertEqual(status, 200, result)
            self.assertEqual(result.activity_info.get_activity_actor_count, 0)
            self.assertEqual(result.activity_info.cur_drop_activity_actor, 90)
        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now)
        reboot.systems._rng = gacha_fixtures.FixedRollRandom(0.999)
        with patch.object(reboot.events, "_activity_list", side_effect=self.activities):
            self.game = reboot
            status, result = self.draw(1, activity_id=14020)
            self.assertEqual(status, 200, result)
            self.assertEqual(result.actor_record[0].id, 1015)
            self.assertGreaterEqual(result.actor_record[0].quality, 3)
            self.assertEqual(result.activity_info.get_activity_actor_count, 1)
            normal = self.call("GET", "/fci/gachainfo/")[1][0]
            self.assertEqual(next(x for x in normal["activityInfo"] if x["activityId"] == 14021)["curDropActivityActor"], 0)

    def test_gacha_points_use_original_ratio_and_rollback_with_draw(self):
        self._give_tickets(5)
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            status, result = self.draw(2)
            self.assertEqual(status, 200, result)
            self.assertEqual(result.add_gacha_score, 2000)
            self.assertEqual(self.points(), 2000)
            self.assertEqual(sum(c.base_res.count_delta for c in result.info.res_change if c.HasField("base_res") and c.base_res.type == 23), 2000)
            before = self.game.systems._state(self.profile)
            with patch.object(self.game.systems, "_save", side_effect=RuntimeError("draw commit failed")):
                with self.assertRaisesRegex(RuntimeError, "draw commit failed"):
                    self.draw(1)
            self.assertEqual(self.points(), 2000)
            self.assertEqual(self.game.systems._state(self.profile), before)

    def test_point_shop_native_goods_and_insufficient_score_leave_assets_unchanged(self):
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            status, shop = self.call("GET", "/fci/gacha-shop/")
            self.assertEqual(status, 200, shop)
            self.assertTrue(shop.goods)
            self.assertTrue(all(g.cost.type == 23 and g.good.type in (15, 29) for g in shop.goods))
            before = self.game.systems._state(self.profile)
            status, error = self.call("POST", "/fci/gacha-shop/trade", {"good_id": shop.goods[0].good_id})
            self.assertEqual(status, 409, error)
            self.assertEqual(self.game.systems._state(self.profile), before)

    def test_point_shop_claim_is_atomic_idempotent_and_persistent(self):
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            shop = self.call("GET", "/fci/gacha-shop/")[1]
            good = shop.goods[0]
            self.assertEqual(good.cost.type, 23)
            self.game.economy._change_resources(self.profile, [{"type": 23, "id": 0, "count": good.cost.count, "param": 0}])
            request = {"good_id": good.good_id}
            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes = list(workers.map(lambda _: self.call("POST", "/fci/gacha-shop/trade", request), range(2)))
            self.assertTrue(all(status == 200 for status, _ in outcomes))
            self.assertEqual(sum(bool(result.res_change) for _, result in outcomes), 1)
            self.assertEqual(self.points(), 0)
            before = self.game.systems._state(self.profile)
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now)
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            status, retry = self.call("POST", "/fci/gacha-shop/trade", request)
            self.assertEqual(status, 200)
            self.assertEqual(len(retry.res_change), 0)
            self.assertEqual(self.game.systems._state(self.profile), before)
            self.assertNotIn(good.good_id, [g.good_id for g in self.call("GET", "/fci/gacha-shop/")[1].goods])

    def test_normal_up_login_redpoint_matches_pool_list(self):
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            status, red = self.call("GET", "/fci/redpoint/data")
            self.assertEqual(status, 200, red)
            normal = next((r for r in red["gachaInfoGet"]["gachaInfo"] if r["id"] == 1), None)
            self.assertIsNotNone(normal)
            self.assertEqual({r["activityId"] for r in normal["activityInfo"]}, {0, *UP_IDS})
            self.assertGreater(normal["nextFreeGachaTime"], 0)

    def test_point_shop_does_not_sell_already_owned_skin(self):
        from grants import grant_rewards
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            shop = self.call("GET", "/fci/gacha-shop/")[1]
            skin = next(g for g in shop.goods if g.good.type == 29)
            self.game.economy._change_resources(self.profile, [{"type": 23, "id": 0, "count": skin.cost.count, "param": 0}])
            with self.game.atomic_mutation():
                grant_rewards(self.game, self.profile, [{"type": 29, "id": skin.good.id, "count": 1}])
            self.assertNotIn(skin.good_id, [g.good_id for g in self.call("GET", "/fci/gacha-shop/")[1].goods])
            status, error = self.call("POST", "/fci/gacha-shop/trade", {"good_id": skin.good_id})
            self.assertEqual(status, 409, error)
            self.assertEqual(self.points(), skin.cost.count)

    def test_transport_key_replays_same_paid_draw_without_minting_points(self):
        self._give_tickets(3)
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            payload = {"id": 1, "count": 1, "requestId": "isolated-up-draw-1"}
            status, first = self.call("POST", "/fci/gachatrade/", payload)
            self.assertEqual(status, 200, first)
            before = self.game.systems._state(self.profile)
            status, replay = self.call("POST", "/fci/gachatrade/", payload)
            self.assertEqual(status, 200, replay)
            self.assertEqual(replay, first)
            self.assertEqual(self.game.systems._state(self.profile), before)
            self.assertEqual(self.points(), 1000)

        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now)
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            self.assertEqual(self.call("POST", "/fci/gachatrade/", payload)[1], first)
            status, error = self.call("POST", "/fci/gachatrade/", {**payload, "count": 2})
            self.assertEqual(status, 409, error)
            self.assertEqual(self.points(), 1000)

    def test_native_wire_query_key_replays_after_restart(self):
        self._give_tickets(2)
        req = self.game.systems._message("ApiGachaTrade")
        req.id, req.count, req.activity_id = 1, 1, 14020
        path = "/fci/gachatrade/?localRequestId=native-player-day-pool-count-1"
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            status, first = self.game.handle_http("POST", path, req.SerializeToString(), "application/wireformat", self.auth)
            self.assertEqual(status, 200, first)
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now)
        with patch.object(self.game.events, "_activity_list", side_effect=self.activities):
            status, again = self.game.handle_http("POST", path, req.SerializeToString(), "application/wireformat", self.auth)
            self.assertEqual(status, 200, again)
            self.assertEqual(again, first)
            self.assertEqual(self.points(), 1000)


class OpenRaceTests(unittest.TestCase):
    setUp, tearDown = race_fixtures.RaceClientTests.setUp, race_fixtures.RaceClientTests.tearDown
    call, info, enter, item, snapshot, fight = (race_fixtures.RaceClientTests.call, race_fixtures.RaceClientTests.info, race_fixtures.RaceClientTests.enter,
                                               race_fixtures.RaceClientTests.item, race_fixtures.RaceClientTests.snapshot, race_fixtures.RaceClientTests.fight)

    def test_native_race_uses_open_activity_and_reuses_reward_ledger(self):
        rows = [{"type": 42, "activityId": 42001, "levelLimit": 26}]
        with patch.object(self.game.events, "_activity_list", return_value=(200, rows)):
            run = self.enter()
            self.assertEqual((run["baseInfo"]["activityType"], run["baseInfo"]["activityId"]), (42, 42001))
            self.fight(run)
            status, reward = self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})
            self.assertEqual(status, 200)
            self.assertTrue(reward["resChange"])
        with patch.object(self.game.events, "_activity_list", return_value=(200, [])):
            self.call("DELETE", f"/fci/racepvp-dungeon/{run['dungeonUid']}")
            ordinary = self.enter()
            self.assertEqual(ordinary["baseInfo"]["activityType"], 20)
            self.assertEqual(self.call("POST", "/fci/RacePVPBattleScoreReward/", {"battleScore": 2000})[1], {"resChange": []})


class OpenGachaLuaTests(unittest.TestCase):
    def test_scheduled_ads_and_up_backgrounds_exist_as_exact_client_bundles(self):
        bundles = external_path("LJJC_ASSET_BUNDLE_ROOT", "analysis/ljjc_manifest_out/assets/Patch/assetbundle")
        rows = json.loads((ROOT / "data/client-tables/GachaAd.json").read_text(encoding="utf-8"))["rows"]
        ads = {row["activityId"]: row for row in rows}
        schedule = yaml.safe_load((ROOT / "data/server-only/activity-schedule.yaml").read_text(encoding="utf-8"))
        scheduled_ads = {r["activityId"] for r in schedule["permanentActivities"]["entries"] if r["type"] == 19}
        self.assertEqual(scheduled_ads, {*range(19001, 19010), 19011, 19021, *range(19040, 19061)})
        require_file(bundles / "texture/gacha/gacha_ad_19001", "LJJC_ASSET_BUNDLE_ROOT")
        for aid in scheduled_ads:
            with self.subTest(ad=aid):
                self.assertTrue((bundles / ads[aid]["displayName"].lower()).is_file())
        self.assertNotIn(14019, ads, "this one original UP row needs the tested normal-view fallback")
        for aid in set(UP_IDS) - {14019}:
            with self.subTest(up=aid):
                self.assertTrue((bundles / "window" / ads[aid]["displayName"].lower()).is_file())

    def test_local_native_draw_sends_stable_query_key_and_remote_preserves_request(self):
        source = ROOT / "client-patch/hot-update/sources/gacha-open-all.lua"
        lua = LuaRuntime()
        lua.execute("""
          localServer=true; calls={}; info={{id=1,gachaCountToday=5}}; CS={GameTime={serverUtc=1793577600}}
          this={GetData=function(self,k) if k=='fci/gachainfo/' then return info elseif k=='playerId' then return 'test-player' end end,
            SetData=function() end, GameRequest=function(self,p)
              local req={path=p}; function req:Query(k,v) self.key=v; return self end
              function req:Post(body,ok,fail) table.insert(calls,self) end; return req end}
          function require(name)
            if name=='Common/WindowUtil' then return function() return {},{},{} end end
            if name=='Common/PlatformUtil' then return {IsRecordGachaEnabled=function() return false end} end
            if name=='Common/Singleton' then return {Get=function(self,n)
              if n=='IAPHandler' then return {IsLocalAccountServer=function() return localServer end} end; return {} end} end
            return {}
          end
        """)
        lua.execute(source.read_text(encoding="utf-8"))
        lua.execute("RequestGacha({id=1,count=1}); RequestGacha({id=1,count=1}); assert(calls[1].key~=nil); assert(calls[1].key==calls[2].key)")
        lua.execute("info[1].gachaCountToday=6; RequestGacha({id=1,count=1}); assert(calls[3].key~=calls[1].key)")
        lua.execute("localServer=false; RequestGacha({id=1,count=1}); assert(calls[4].key==nil); assert(calls[4].path=='fci/gachatrade/')")

    def test_missing_up_ad_keeps_normal_view_and_existing_ad_still_loads(self):
        source = ROOT / "client-patch/hot-update/sources/gachanormal-open-all.lua"
        lua = LuaRuntime()
        lua.execute("""
          this={}; calls={}; REF={NodeEffect={},NodeNormal={gameObject={SetActive=function(self,v) calls.normal=v end}},
             WidgetGachaInsertCoin={['$SetActivity']=function(a) calls.activity=a.activityId end}}
          WU={RemoveAllChild=function() end, InstantiateWindowPrefab=function(node,name) calls.prefab=name;return {} end,
             HandleGameObjectAttached=function() end,TraverseChildren=function() end}
          PB={get=function(name,id) if id==14020 then return {displayName='GachaNormal/gacha_ad13'} end end}
          function require(name)
            if name=='Common/WindowUtil' then return function() return WU,{},REF end end
            if name=='Common/PbHelper' then return PB end
            return {}
          end
          _ENV['$']=function(x) return {} end
          function reRef(x) return {RoleButtons={}} end
        """)
        lua.execute(source.read_text(encoding="utf-8"))
        lua.execute("SetStatus({activityId=14019}); assert(calls.normal==true); assert(calls.activity==14019)")
        lua.execute("SetStatus({activityId=14020}); assert(calls.prefab=='GachaNormal/gacha_ad13')")
