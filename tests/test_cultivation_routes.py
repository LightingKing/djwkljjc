"""Character decompose, paid gacha, bag sell/use/compose, rename, favour
gifts and talent rerolls, as the decompiled 2.0.4 client sends and reads them
(see the request map at LocalSystems._cultivation)."""
from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actors as actor_store
import equipment
import systems
from accounts import LocalAccounts
from game import LocalGame
from google.protobuf import message_factory
import test_systems  # module import: keep its TestCase out of this module's collection


class CultivationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.game.systems._rng = random.Random(11)

    def tearDown(self) -> None:
        self._temp.cleanup()

    # -- helpers -----------------------------------------------------------
    def _state(self) -> dict:
        return self.game.systems._state(self.profile)

    def _save(self, state: dict) -> None:
        self.game.systems._save(self.profile, state)

    def _give(self, **counts) -> None:
        state = self._state()
        for key, count in counts.items():
            item_id = int(key.lstrip("i"))
            state.setdefault("items", {})[str(item_id)] = {"id": item_id, "count": count, "gotTime": 1, "isNew": True}
        self._save(state)

    def _item(self, item_id: int) -> int:
        return int(self._state().get("items", {}).get(str(item_id), {}).get("count", 0))

    def _resource(self, rtype: int) -> int:
        profile = self.game._profile(self.account)
        return next((r["count"] for r in profile["resources"] if r["type"] == rtype and not r.get("id")), 0)

    def _set_resource(self, rtype: int, value: int) -> None:
        ok, _c, _e = self.game.economy._change_resources(self.profile, [{"type": rtype, "id": 0, "count": value - self._resource(rtype)}])
        self.assertTrue(ok)

    def _call(self, method: str, path: str, payload=None) -> tuple:
        body = None if payload is None else json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, "application/json", self.auth)

    def _wire(self, method: str, path: str, message) -> tuple:
        return self.game.handle_http(method, path, message.SerializeToString(), "application/wireformat", self.auth)

    def _message(self, name: str):
        return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()

    def _add_actor(self, actor_id: int, **fields) -> int:
        state = self._state()
        record = actor_store.add(self.profile, state, actor_id)
        record.update(fields)
        self._save(state)
        return int(record["uid"])

    @staticmethod
    def _bases(result) -> list:
        return [c.base_res for c in result.res_change if c.HasField("base_res")]

    # -- (a) character decompose -----------------------------------------------
    def test_decompose_selected_characters_grants_chips_and_exp_books(self):
        first = self._add_actor(1002, quality=1, level=1)
        second = self._add_actor(1003, quality=2, level=3, exp=10)
        keep = self._add_actor(1004)
        state = self._state()
        worn = equipment.grant_equipment(self.game, self.profile, 21001, state=state)["equip"]["uid"]
        state["equips"][str(worn)]["actorUid"] = second
        state["actorGroups"] = {"1": {"members": [{"actorUid": second, "index": 0}, {"actorUid": 1, "index": 1}]}}
        self._save(state)
        status, result = self._call("POST", "/fci/actor/decompose/", {"uids": [first, second]})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiActorDecompose.post")
        removed = sorted((c.actor.uid, c.actor.count_delta) for c in result.res_change if c.HasField("actor"))
        self.assertEqual(removed, sorted([(first, -1), (second, -1)]))
        gained = {b.id: b.count_delta for b in self._bases(result)}
        # ActorDecompose (1002, q1) -> chip 20002 x1; (1003, q2) -> chip 20003 x3.
        self.assertEqual(gained[20002], 1)
        self.assertEqual(gained[20003], 3)
        # Level 3 + 10 EXP: (10 + 60 + 90) * 0.5 = 80 -> no 100-EXP book.
        self.assertNotIn(10001, gained)
        state = self._state()
        self.assertIsNone(actor_store.get(self.profile, state, first))
        self.assertIsNone(actor_store.get(self.profile, state, second))
        self.assertIsNotNone(actor_store.get(self.profile, state, keep))
        self.assertEqual(state["equips"][str(worn)]["actorUid"], 0)
        self.assertEqual(state["actorGroups"]["1"]["members"][0]["actorUid"], 0)
        self.assertEqual(self._item(20003), 3)

    def test_decompose_exp_refund_and_refusals(self):
        leveled = self._add_actor(1002, quality=1, level=5, exp=0)
        locked = self._add_actor(1003, locked=True)
        status, result = self._call("POST", "/fci/actor/decompose/", {"uids": [leveled]})
        self.assertEqual(status, 200)
        exp = sum(systems.ACTOR_LEVEL_ROWS[i].get("exp", 0) for i in range(1, 6)) * 0.5
        expected = dict(systems._greedy_exp_items(exp, systems.ACTOR_EXP_ITEMS))
        got = {b.id: b.count_delta for b in self._bases(result) if b.id in systems.ACTOR_EXP_ITEMS}
        self.assertEqual(got, expected)
        self.assertTrue(got)
        self.assertEqual(self._call("POST", "/fci/actor/decompose/", {"uids": [locked]})[1]["error"], "actor_locked")
        self.assertEqual(self._call("POST", "/fci/actor/decompose/", {"uids": [999]})[0], 404)
        everyone = [int(r["uid"]) for r in actor_store.ordered(self.profile, self._state()) if not r.get("locked")]
        self.assertEqual(self._call("POST", "/fci/actor/decompose/", {"uids": everyone + [locked]})[0], 409)

    def test_decompose_wireformat_body(self):
        uid = self._add_actor(1002, quality=1)
        message = self._message("ApiActorDecompose")
        message.uids.append(uid)
        status, _result = self._wire("POST", "/fci/actor/decompose/", message)
        self.assertEqual(status, 200)
        self.assertIsNone(actor_store.get(self.profile, self._state(), uid))

    # -- (b) gacha -------------------------------------------------------------
    def test_paid_pulls_use_tickets_then_money_and_add_characters(self):
        self._call("POST", "/fci/gachatrade/", {"id": 1, "gachaFree": True})  # newcomer ten-pull
        self._give(i60001=3)
        self._set_resource(5, 1000)
        status, result = self._call("POST", "/fci/gachatrade/", {"id": 1, "count": 5, "cinematic": "ATM", "gachaFree": False})
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result.actor_record), 5)
        actors = [c.actor for c in result.info.res_change if c.HasField("actor")]
        self.assertEqual(len(actors), 5)
        self.assertTrue(all(actor.count_delta == 1 for actor in actors))
        bases = [c.base_res for c in result.info.res_change if c.HasField("base_res")]
        self.assertIn((11, 60001, 0, -3), [(b.type, b.id, b.count, b.count_delta) for b in bases])
        self.assertEqual(next(b for b in bases if b.type == 5).count_delta, -300)  # 2 x 150
        self.assertEqual(self._item(60001), 0)
        self.assertEqual(self._resource(5), 700)

    def test_periodic_pool_ignores_pool_one_free_flag(self):
        self._give(i60002=1)
        status, result = self._call("POST", "/fci/gachatrade/", {"id": 2, "count": 1, "gachaFree": True})
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result.actor_record), 1)
        self.assertEqual(self._item(60002), 0)
        self._set_resource(5, 100)
        self.assertEqual(self._call("POST", "/fci/gachatrade/", {"id": 2, "count": 1})[0], 409)

    def test_chips_pool_spends_selected_chips(self):
        self._give(i20001=25)
        status, result = self._call("POST", "/fci/gachatrade/", {"id": 5, "cost": [{"type": 11, "id": 20001, "count": 25}], "gachaFree": True})
        self.assertEqual(status, 200, result)
        self.assertIn(len(result.actor_record), (2, 3))  # 25 chips: 2 + a 50% extra roll
        self.assertEqual(self._item(20001), 0)
        self._give(i20001=5)
        status, error = self._call("POST", "/fci/gachatrade/", {"id": 5, "cost": [{"type": 11, "id": 20001, "count": 5}]})
        self.assertEqual((status, error["error"]), (409, "gacha_chips_not_enough"))

    # -- (c) bag ---------------------------------------------------------------
    def test_item_sell_wireformat_pays_sell_price(self):
        self._give(i30002=4, i30001=2)
        self._set_resource(7, 0)
        message = self._message("ApiItemSell")
        entry = message.itemsell.add(); entry.id = 30002; entry.count = 3
        status, result = self._wire("POST", "/fci/itemsell/", message)
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiItemSell.result")
        changes = {(b.type, b.id): (b.count, b.count_delta) for b in self._bases(result)}
        self.assertEqual(changes[(11, 30002)], (1, -3))
        self.assertEqual(changes[(7, 0)], (6000, 6000))
        self.assertEqual(self._resource(7), 6000)
        status, error = self._call("POST", "/fci/itemsell", {"itemsell": [{"id": 30001, "count": 1}]})
        self.assertEqual((status, error["error"]), (409, "item_not_for_sale"))

    def test_manual_boxes_grant_the_chosen_row(self):
        self.assertEqual(systems.ITEM_INFO[121001]["funcType"], 12)
        self._give(i121001=1, i75003=1, i85003=1)
        before = len(actor_store.ordered(self.profile, self._state()))
        status, result = self._call("POST", "/fci/itemuse/", {"id": 121001, "count": 1, "index": 1})
        self.assertEqual(status, 200, result)
        chosen = systems.MANUAL_BOXES[121001][1]["inBox"]["id"]
        actors = [c.actor for c in result.res_change if c.HasField("actor")]
        self.assertEqual([(a.id, a.count_delta) for a in actors], [(chosen, 1)])
        self.assertIn((121001, 0, -1), [(b.id, b.count, b.count_delta) for b in self._bases(result)])
        self.assertEqual(len(actor_store.ordered(self.profile, self._state())), before + 1)
        status, result = self._call("POST", "/fci/itemuse/", {"id": 75003, "count": 1, "index": 0})
        self.assertEqual(status, 200, result)
        equips = [c.equip for c in result.res_change if c.HasField("equip")]
        self.assertEqual([(e.id, e.count_delta) for e in equips], [(10300, 1)])
        status, box_result = self._call("POST", "/fci/itemuse/", {"id": 85003, "count": 1})
        self.assertEqual(status, 200, box_result)
        self.assertEqual(self._item(85003), 0)
        self.assertIn((30011, 10, 10), [(b.id, b.count, b.count_delta) for b in self._bases(box_result)])
        self.assertEqual(self._call("POST", "/fci/itemuse/", {"id": 121001, "count": 1, "index": 0})[0], 409)

    def test_resource_compose_follows_recipes_and_chains(self):
        self._give(i30001=40)
        status, result = self._call("POST", "/fci/resourcecompose/", {"type": 11, "id": 30006, "count": 2})
        self.assertEqual(status, 200, result)
        changes = {b.id: b.count_delta for b in self._bases(result)}
        self.assertEqual(changes, {30006: 2, 30001: -10})
        # 30011 needs 6 x 30006: 2 owned, 4 composed from 20 x 30001.
        status, result = self._call("POST", "/fci/resourcecompose/", {"type": 11, "id": 30011, "count": 1})
        self.assertEqual(status, 200, result)
        changes = {b.id: b.count_delta for b in self._bases(result)}
        self.assertEqual(changes, {30011: 1, 30006: -2, 30001: -20})
        self.assertEqual((self._item(30001), self._item(30006), self._item(30011)), (10, 0, 1))
        status, error = self._call("POST", "/fci/resourcecompose/", {"type": 11, "id": 30016, "count": 1})
        self.assertEqual((status, error["error"]), (409, "resource_compose_failed"))
        self.assertEqual(self._item(30001), 10)

    # -- (d) rename ------------------------------------------------------------
    def test_first_rename_is_free_then_card_then_money(self):
        self.assertEqual(self._call("GET", "/fci/name/"), (200, {"notFirstRename": False}))
        self.assertEqual(self._call("PATCH", "/fci/name", {"name": "甲"}), (200, {"name": "甲", "resChange": []}))
        self.assertEqual(self._call("GET", "/fci/name/"), (200, {"notFirstRename": True}))
        self._set_resource(5, 0)
        self.assertEqual(self._call("PATCH", "/fci/name", {"name": "乙"})[0], 409)
        self._give(i80010=1)
        status, reply = self._call("PATCH", "/fci/name", {"name": "乙"})
        self.assertEqual(status, 200)
        self.assertEqual(reply["name"], "乙")
        self.assertEqual(reply["resChange"][0]["baseRes"]["id"], 80010)
        self.assertEqual(self._item(80010), 0)
        self._set_resource(5, 250)
        status, reply = self._call("PATCH", "/fci/name", {"name": "丙"})
        self.assertEqual(status, 200)
        self.assertEqual(self._resource(5), 50)
        self.assertEqual(self.game._profile(self.account)["name"], "丙")
        self.assertEqual(self._call("PATCH", "/fci/name", {"name": "a/b"})[0], 400)

    # -- (e) favour gifts --------------------------------------------------------
    def test_favour_gift_patch_levels_role_notepad(self):
        self._give(i53003=2, i10001=1)
        status, reply = self._call("PATCH", "/fci/FavourGift/1", {"usedItemList": [{"type": 11, "id": 53003, "count": 2}]})
        self.assertEqual(status, 200, reply)
        # 2 x 25 favour from level 0: FavourLevel[1] = 15 -> level 1 with 35.
        self.assertEqual((reply["roleNotepad"]["roleId"], reply["roleNotepad"]["favourLevel"], reply["roleNotepad"]["favour"]), (1, 1, 35))
        self.assertEqual(reply["resChange"][0]["baseRes"]["countDelta"], -2)
        self.assertEqual(self._item(53003), 0)
        self.assertEqual(self.game.profile_features.role_notepad(self.profile, 1)["favourLevel"], 1)
        bad = self._call("PATCH", "/fci/FavourGift/1", {"usedItemList": [{"type": 11, "id": 10001, "count": 1}]})
        self.assertEqual(bad[0], 400)
        self.assertEqual(self._call("PATCH", "/fci/FavourGift/1", {"usedItemList": [{"type": 11, "id": 53003, "count": 1}]})[0], 409)

    def test_favour_full_and_legacy_seam_use_the_role_notepad(self):
        status, result = self._call("POST", "/fci/actor/1/actorfavourfull/", {})
        self.assertEqual(status, 200)
        self.assertTrue(result.result)
        self.assertEqual(self.game.profile_features.role_notepad(self.profile, 1)["favourLevel"], systems.MAX_ROLE_FAVOUR_LEVEL)
        self._give(i53001=1)
        status, reply = self._call("POST", "/fci/actor/1/favour/", {"itemId": 53001, "count": 1})
        self.assertEqual((status, reply["error"]), (409, "favour_level_max"))
        self.assertEqual(self._item(53001), 1)

    # -- (f) talents -------------------------------------------------------------
    def test_talent_reset_session_and_select(self):
        uid = self._add_actor(1002, quality=2)
        self.assertEqual(self._call("GET", f"/fci/talent/reset/{uid}"), (200, {"talentPos": 0, "talentResetInfo": []}))
        record = actor_store.get(self.profile, self._state(), uid)
        self.assertEqual(len(record["talents"]), 2)  # quality 2 -> two talents
        self._give(i20002=1, i90001=1)
        first = self._call("POST", "/fci/talent/reset/", {"actorUid": uid, "talentPos": 2})
        self.assertEqual(first[0], 200, first[1])
        self.assertEqual((first[1]["talentPos"], len(first[1]["talentResetInfo"])), (2, 1))
        self.assertEqual(first[1]["resChange"][0]["baseRes"]["id"], 20002)  # own chip first
        second = self._call("POST", "/fci/talent/reset/", {"actorUid": uid, "talentPos": 2})[1]
        self.assertEqual(second["resChange"][0]["baseRes"]["id"], 90001)  # then the generic chip
        self.assertEqual(self._call("POST", "/fci/talent/reset/", {"actorUid": uid, "talentPos": 2})[0], 409)
        self.assertEqual(self._call("GET", f"/fci/talent/reset/{uid}")[1]["talentPos"], 2)
        with mock.patch.dict(systems.ACTOR_QUALITY_INFO, {(1002, 2): {"talentResetUpgradeRate": 0, "talentResetUpgradeFloor": 0}}):
            status, reply = self._call("POST", "/fci/talent/select/", {"actorUid": uid, "select": True, "selectTalentPos": 2})
        self.assertEqual(status, 200, reply)
        chosen = second["talentResetInfo"][1]
        self.assertEqual(reply["actor"]["talentInfo"][1], chosen)
        self.assertEqual(reply["actor"]["countDelta"], 0)
        self.assertNotIn("upgradeTalentInfo", reply)
        self.assertEqual(self._call("GET", f"/fci/talent/reset/{uid}")[1], {"talentPos": 0, "talentResetInfo": []})

    def test_talent_select_quality_up_after_floor(self):
        uid = self._add_actor(1002, quality=1)
        self._give(i20002=1)
        self._call("POST", "/fci/talent/reset/", {"actorUid": uid, "talentPos": 1})
        with mock.patch.dict(systems.ACTOR_QUALITY_INFO, {(1002, 1): {"talentResetUpgradeRate": 0, "talentResetUpgradeFloor": 1}}):
            status, reply = self._call("POST", "/fci/talent/select/", {"actorUid": uid, "select": False})
        self.assertEqual(status, 200, reply)
        self.assertIn("upgradeTalentInfo", reply)
        self.assertEqual(reply["actor"]["quality"], 2)
        self.assertEqual(len(reply["actor"]["talentInfo"]), 2)
        self.assertEqual(actor_store.get(self.profile, self._state(), uid)["quality"], 2)

    def test_login_hook_sets_new_flag_and_talents(self):
        state = self._state()
        record = actor_store.ordered(self.profile, state)[0]
        container = self.game._types["DBGamePlayer.Actors"]()
        message = container.player_actors.add()
        # State migration already seeds talents; projecting login must not reroll.
        self.assertFalse(self.game.systems.login_actor_extras(self.profile, state, record, message))
        self.assertTrue(message.is_new_card)
        self.assertEqual(len(message.talent_info), len(record["talents"]))
        equipment.set_new_flags(self.profile, state, [{"idOrUid": record["uid"], "type": 15}])
        self.assertFalse(self.game.systems.login_actor_extras(self.profile, state, record, message))
        self.assertFalse(message.is_new or message.is_new_card)

    # -- tasks -----------------------------------------------------------------
    def test_star_upgrade_records_tasks(self):
        calls = []
        state = self._state()
        record = actor_store.ordered(self.profile, state)[0]
        row = systems.ACTOR_STAR_PROMOTIONS.get((int(record["id"]), 2), {})
        for cost in row.get("starCost") or []:
            if cost.get("type") == 11:
                self._give(**{f"i{cost['id']}": cost["count"]})
        state = self._state()
        actor_store.ordered(self.profile, state)[0]["level"] = max(1, int(row.get("levelLimit", 1) or 1))
        self._save(state)
        self._set_resource(7, 10_000_000)
        with mock.patch.object(self.game.tasks, "record", side_effect=lambda *a, **k: calls.append(a[1])):
            status, _ = self._call("POST", f"/fci/actor/{record['uid']}/starupgrade/", {})
        self.assertEqual(status, 200)
        self.assertIn("TaskType_StarLevelupMTimes", calls)


if __name__ == "__main__":
    unittest.main()
