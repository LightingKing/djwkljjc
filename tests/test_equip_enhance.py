"""藏品 enhance / decompose and persisted NEW flags.

Requests and reply fields are the decompiled client's:
Window_ActorEquipLevelup(+Material/Result) posts ``fci/equip/<uid>/enhance/
{foodRes}`` and reads ``result.resChange`` (change.equip with the target uid,
baseRes countDelta > 0 as returned items); Window_Bag posts
``fci/equip/decompose/ {equipUids}``; Common_SortUtil.ClearNewFlag patches
``fci/new-flag/`` with ProtobufT("ApiResourceNewFlag").  Expected numbers
follow EquipUtil (TryEnhanceEquip / DecomposedExp / DecomposedRes) and
ActorUtil.DecomposeToExpItems with the exported EquipEnhance / EquipInfo /
ItemInfo tables.
"""
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
from accounts import LocalAccounts
from game import LocalGame
from google.protobuf import message_factory
import test_systems  # module import: keep its TestCase out of this module's collection

Q1 = 20000      # quality 1: levelUpExp 20/40/60, max level 4, baseExp 15
Q1_POS1 = 21001
Q2 = 10100      # quality 2: levelUpExp 40/70/100/150/..., baseExp 110
Q3 = 10200      # quality 3: baseExp 960
GOLD = 7


class EquipEnhanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.account = account
        self.profile = self.game._profile(account)
        self.rng = random.Random(3)

    def tearDown(self) -> None:
        self._temp.cleanup()

    # -- helpers -----------------------------------------------------------
    def _state(self) -> dict:
        return self.game.systems._state(self.profile)

    def _grant(self, equip_id: int, **fields) -> int:
        uid = equipment.grant_equipment(self.game, self.profile, equip_id, rng=self.rng)["equip"]["uid"]
        if fields:
            state = self._state()
            state["equips"][str(uid)].update(fields)
            self.game.systems._save(self.profile, state)
        return uid

    def _give_items(self, **counts) -> None:
        state = self._state()
        for key, count in counts.items():
            item_id = int(key.lstrip("i"))
            state.setdefault("items", {})[str(item_id)] = {"id": item_id, "count": count, "gotTime": 1, "isNew": True}
        self.game.systems._save(self.profile, state)

    def _gold(self) -> int:
        profile = self.game._profile(self.account)
        return next((r["count"] for r in profile["resources"] if r["type"] == GOLD and not r.get("id")), 0)

    def _set_gold(self, value: int) -> None:
        delta = value - self._gold()
        ok, _changes, _error = self.game.economy._change_resources(self.profile, [{"type": GOLD, "id": 0, "count": delta}])
        self.assertTrue(ok)

    def _post(self, path: str, payload) -> tuple:
        return self.game.handle_http("POST", path, json.dumps(payload).encode(), "application/json", self.auth)

    def _wire(self, name: str, **fields):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        return cls(**fields)

    def _record(self, uid: int):
        return self._state()["equips"].get(str(uid))

    def _item(self, item_id: int) -> int:
        return int(self._state().get("items", {}).get(str(item_id), {}).get("count", 0))

    @staticmethod
    def _split(result):
        bases = [change.base_res for change in result.res_change if change.HasField("base_res")]
        equips = [change.equip for change in result.res_change if change.HasField("equip")]
        return bases, equips

    def _enhance(self, uid: int, food: list) -> tuple:
        return self._post(f"/fci/equip/{uid}/enhance/", {"foodRes": food})

    # -- enhance -------------------------------------------------------------
    def test_items_level_up_and_cost_three_gold_per_exp(self):
        uid = self._grant(Q1)
        self._give_items(i40002=3)
        self._set_gold(10_000)
        status, result = self._enhance(uid, [{"type": 11, "id": 40002, "count": 1}])
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiEquipEnhance.result")
        bases, equips = self._split(result)
        # 80 EXP: L1 (20) -> L2 (40) -> L3 with 20/60 on the bar.
        target = next(equip for equip in equips if equip.uid == uid)
        self.assertEqual((target.level, target.cur_exp, target.count_delta), (3, 20, 0))
        self.assertEqual(len(target.random_attrs), 1)  # no genNewRandAttr at levels 2-3
        item = next(base for base in bases if base.type == 11 and base.id == 40002)
        self.assertEqual((item.count, item.count_delta), (2, -1))
        gold = next(base for base in bases if base.type == GOLD)
        self.assertEqual(gold.count_delta, -240)
        self.assertEqual(self._gold(), 10_000 - 240)
        self.assertEqual((self._record(uid)["level"], self._record(uid)["exp"]), (3, 20))
        self.assertEqual(self._item(40002), 2)

    def test_reaching_max_level_rolls_attr_and_wastes_item_overflow(self):
        uid = self._grant(Q1)
        self._give_items(i40003=1)
        self._set_gold(10_000)
        status, result = self._enhance(uid, [{"type": 11, "id": 40003, "count": 1}])
        self.assertEqual(status, 200, result)
        bases, equips = self._split(result)
        target = equips[-1]
        # 240 EXP, 120 needed for L4 (the last q1 level): overflow from items
        # is lost, only the used 120 EXP are paid; level 4 adds an attribute.
        self.assertEqual((target.level, target.cur_exp), (4, 0))
        self.assertEqual(len(target.random_attrs), 2)
        self.assertEqual(next(b for b in bases if b.type == GOLD).count_delta, -360)
        self.assertFalse([b for b in bases if b.count_delta > 0])
        self.assertEqual(len(self._record(uid)["randomAttrs"]), 2)
        self._give_items(i40001=1)
        status, error = self._enhance(uid, [{"type": 11, "id": 40001, "count": 1}])
        self.assertEqual((status, error["error"]), (409, "equip_level_max"))

    def test_equipment_food_charges_only_unpaid_exp(self):
        uid = self._grant(Q2)
        food = self._grant(Q1, level=3, exp=10)  # base 15, enhance 10+20+40=70 -> kept 35
        self._set_gold(1_000)
        status, result = self._enhance(uid, [{"type": 12, "id": Q1, "count": 0, "param": food, "quality": 1}])
        self.assertEqual(status, 200, result)
        bases, equips = self._split(result)
        eaten = next(equip for equip in equips if equip.uid == food)
        self.assertEqual(eaten.count_delta, -1)
        target = next(equip for equip in equips if equip.uid == uid)
        self.assertEqual((target.level, target.cur_exp), (2, 10))  # 50 EXP: 40 for L2, 10 left
        self.assertEqual(next(b for b in bases if b.type == GOLD).count_delta, -45)  # (50 - 35) * 3
        self.assertIsNone(self._record(food))

    def test_equipment_overflow_returns_exp_items_and_refunds_gold(self):
        uid = self._grant(Q1, level=3, exp=0)
        food = self._grant(Q2, level=5, exp=30)  # enhance 390 -> kept 195, total 305
        self._set_gold(0)
        status, result = self._enhance(uid, [{"type": 12, "id": Q2, "count": 0, "param": food}])
        self.assertEqual(status, 200, result)
        bases, equips = self._split(result)
        self.assertEqual(equips[-1].uid, uid)
        self.assertEqual(equips[-1].level, 4)
        # Used 60 (all payable), overflow 245 of which 195 already paid:
        # (60 - 195) * 3 = -405 -> refund; returned EXP min(305, 245) = 245
        # -> one 240-EXP item (5 lost), as Window_ActorEquipLevelupMaterial previews.
        gold = next(b for b in bases if b.type == GOLD)
        self.assertEqual(gold.count_delta, 405)
        self.assertEqual(self._gold(), 405)
        returned = [(b.id, b.count_delta) for b in bases if b.type == 11 and b.count_delta > 0]
        self.assertEqual(returned, [(40003, 1)])
        self.assertEqual(self._item(40003), 1)

    def test_base_exp_overflow_returns_items_without_refund(self):
        uid = self._grant(Q1, level=3, exp=0)
        food = self._grant(Q3)  # base 960, nothing invested
        self._set_gold(1_000)
        status, result = self._enhance(uid, [{"type": 12, "id": Q3, "param": food}])
        self.assertEqual(status, 200, result)
        bases, _equips = self._split(result)
        self.assertEqual(next(b for b in bases if b.type == GOLD).count_delta, -180)
        returned = sorted((b.id, b.count_delta) for b in bases if b.type == 11 and b.count_delta > 0)
        self.assertEqual(returned, [(40001, 1), (40003, 1), (40004, 1)])  # 900 = 640 + 240 + 15 (+5 lost)

    def test_wireformat_request_is_accepted(self):
        uid = self._grant(Q1)
        self._give_items(i40001=2)
        self._set_gold(1_000)
        message = self._wire("ApiEquipEnhance")
        food = message.food_res.add(); food.type = 11; food.id = 40001; food.count = 2
        status, result = self.game.handle_http("POST", f"/fci/equip/{uid}/enhance/", message.SerializeToString(),
                                               "application/wireformat", self.auth)
        self.assertEqual(status, 200, result)
        self.assertEqual(self._split(result)[1][-1].level, 2)  # 30 EXP: L2 with 10
        self.assertEqual(self._record(uid)["exp"], 10)

    def test_invalid_food_is_refused_without_changes(self):
        uid = self._grant(Q1)
        actor_uid = int(actor_store.ordered(self.profile, self._state())[0]["uid"])
        worn = self._grant(Q1_POS1, actorUid=actor_uid)
        locked = self._grant(Q1, locked=True)
        planned = self._grant(Q1)
        state = self._state()
        equipment.save_plan(state, 1, [planned])
        self.game.systems._save(self.profile, state)
        self._give_items(i40001=1, i10001=5)
        self._set_gold(1_000)
        cases = [
            ([{"type": 12, "id": Q1_POS1, "param": worn}], 409, "equip_worn"),
            ([{"type": 12, "id": Q1, "param": locked}], 409, "equip_locked"),
            ([{"type": 12, "id": Q1, "param": planned}], 409, "equip_in_plan"),
            ([{"type": 12, "id": Q1, "param": uid}], 400, "invalid_food_res"),
            ([{"type": 12, "id": Q1, "param": 999}], 404, "equip_not_found"),
            ([{"type": 11, "id": 10001, "count": 1}], 400, "invalid_food_res"),  # ActorExp book
            ([{"type": 11, "id": 40001, "count": 2}], 409, "insufficient_item"),
            ([], 400, "material_not_selected"),
        ]
        for food, status, error in cases:
            got_status, body = self._enhance(uid, food)
            self.assertEqual((got_status, body["error"]), (status, error), food)
        self.assertEqual(self._enhance(424242, [{"type": 11, "id": 40001, "count": 1}])[0], 404)
        self._set_gold(0)
        status, body = self._enhance(uid, [{"type": 11, "id": 40001, "count": 1}])
        self.assertEqual((status, body["error"]), (409, "insufficient_resource"))
        self.assertEqual(self._item(40001), 1)
        self.assertEqual((self._record(uid)["level"], self._record(uid).get("exp", 0)), (1, 0))
        for kept in (worn, locked, planned):
            self.assertIsNotNone(self._record(kept))

    def test_level_limit_follows_player_level_config(self):
        with mock.patch.dict(equipment.PLAYER_EQUIP_LEVEL_LIMIT, {1: 2}):
            self.assertEqual(equipment.level_limit(Q2, 1), 2)
            self.assertEqual(equipment.level_limit(Q1, 1), 2)
        self.assertEqual(equipment.level_limit(Q1, 1), 4)
        self.assertEqual(equipment.level_limit(Q2, 30), 8)

    def test_enhance_records_tasks(self):
        uid = self._grant(Q1)
        self._give_items(i40002=1)
        self._set_gold(1_000)
        calls = []
        with mock.patch.object(self.game.tasks, "record", side_effect=lambda *a, **k: calls.append((a[1:], k))):
            self.assertEqual(self._enhance(uid, [{"type": 11, "id": 40002, "count": 1}])[0], 200)
        goals = [args[0] for args, _ in calls]
        self.assertIn("TaskType_EquipEnhanceMTimes", goals)
        levels = [kw["param"] for args, kw in calls if args[0] == "TaskType_EquipEnhanceP0LevelMCount"]
        self.assertEqual(levels, [2, 3])

    def test_enhancing_a_worn_item_refreshes_its_carrier(self):
        actor_uid = int(actor_store.ordered(self.profile, self._state())[0]["uid"])
        uid = self._grant(Q1, actorUid=actor_uid)
        self._give_items(i40002=1)
        self._set_gold(1_000)
        status, result = self._enhance(uid, [{"type": 11, "id": 40002, "count": 1}])
        self.assertEqual(status, 200, result)
        actors = [change.actor for change in result.res_change if change.HasField("actor")]
        self.assertEqual([(a.uid, a.count_delta) for a in actors], [(actor_uid, 0)])
        self.assertEqual(dict(actors[0].body_equips), {"0": uid})

    # -- decompose -----------------------------------------------------------
    def test_decompose_grants_exp_items_and_gold(self):
        plain = self._grant(Q1)                       # 15 EXP -> 40001 x1
        enhanced = self._grant(Q2, level=3, exp=20)   # 110 + floor(130 / 2) = 175 -> 40002 x2 + 40001 x1
        keep = self._grant(Q1)
        self._set_gold(0)
        status, result = self._post("/fci/equip/decompose/", {"equipUids": [plain, enhanced]})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiEquipDecompose.result")
        bases, equips = self._split(result)
        self.assertEqual(sorted((e.uid, e.count_delta) for e in equips), sorted([(plain, -1), (enhanced, -1)]))
        self.assertEqual(sorted((b.id, b.count_delta) for b in bases if b.type == 11), [(40001, 2), (40002, 2)])
        self.assertEqual(next(b for b in bases if b.type == GOLD).count_delta, 195)  # 65 kept enhance EXP * 3
        self.assertIsNone(self._record(plain))
        self.assertIsNone(self._record(enhanced))
        self.assertIsNotNone(self._record(keep))
        self.assertTrue(self._state()["items"]["40001"]["isNew"])
        self.assertEqual(self._gold(), 195)

    def test_decompose_refuses_protected_items_atomically(self):
        actor_uid = int(actor_store.ordered(self.profile, self._state())[0]["uid"])
        free = self._grant(Q1)
        worn = self._grant(Q1_POS1, actorUid=actor_uid)
        locked = self._grant(Q1, locked=True)
        for blocked, error in ((worn, "equip_worn"), (locked, "equip_locked")):
            status, body = self._post("/fci/equip/decompose/", {"equipUids": [free, blocked]})
            self.assertEqual((status, body["error"]), (409, error))
        self.assertIsNotNone(self._record(free))
        self.assertEqual(self._post("/fci/equip/decompose/", {"equipUids": []})[0], 400)

    def test_decompose_wireformat(self):
        uid = self._grant(Q1)
        message = self._wire("ApiEquipDecompose")
        message.equip_uids.append(uid)
        status, result = self.game.handle_http("POST", "/fci/equip/decompose/", message.SerializeToString(),
                                               "application/wireformat", self.auth)
        self.assertEqual(status, 200, result)
        self.assertIsNone(self._record(uid))
        self.assertEqual(self._item(40001), 1)

    # -- NEW flags -----------------------------------------------------------
    def _clear(self, *entries, wire: bool = True):
        if wire:
            message = self._wire("ApiResourceNewFlag")
            message.is_new = False
            for ref, rtype in entries:
                info = message.res_info.add(); info.id_or_uid = ref; info.type = rtype
            return self.game.handle_http("PATCH", "/fci/new-flag/", message.SerializeToString(),
                                         "application/wireformat", self.auth)
        payload = {"resInfo": [{"idOrUid": ref, "type": rtype} for ref, rtype in entries], "isNew": False}
        return self.game.handle_http("PATCH", "/fci/new-flag/", json.dumps(payload).encode(), "application/json", self.auth)

    def test_new_flags_persist_for_equipment_items_and_characters(self):
        uid = self._grant(Q1)
        self._give_items(i40001=1)
        actor = actor_store.ordered(self.profile, self._state())[0]
        self.assertTrue(equipment.actor_is_new(actor))
        self.assertTrue(self._record(uid)["isNew"])
        self.assertEqual(self._clear((uid, 12), (40001, 11), (int(actor["uid"]), 15)), (200, {}))
        state = self._state()
        self.assertFalse(state["equips"][str(uid)]["isNew"])
        self.assertFalse(state["items"]["40001"]["isNew"])
        self.assertFalse(equipment.actor_is_new(actor_store.get(self.profile, state, int(actor["uid"]))))
        # Login package reflects the cleared equipment flag.
        package = self.game._types["DBGamePlayer.EquipPackage"]()
        equipment.fill_equip_package(self.game, self.profile, package)
        self.assertFalse(next(e for e in package.equips if e.uid == uid).is_new)

    def test_new_flag_json_body_and_unknown_refs(self):
        uid = self._grant(Q1)
        self.assertEqual(self._clear((uid, 12), (999999, 12), (5, 99), wire=False), (200, {}))
        self.assertFalse(self._record(uid)["isNew"])
        # The historical empty acknowledgement still answers 200 {}.
        self.assertEqual(self.game.handle_http("PATCH", "/fci/new-flag/", b"\x10\x00", "application/wireformat", self.auth), (200, {}))


if __name__ == "__main__":
    unittest.main()
