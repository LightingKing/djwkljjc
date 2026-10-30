"""Carrying equipment (藏品 tab), one-key carry and equipment plans (搭配).

Requests and the reply fields read are the decompiled client's
(Window_ActorEquipCompare, Window_ActorInfo/WidgetActorEquip,
Window_EquipMatch, Window_EquipMatchRename, Window_EquipDetail).
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actors as actor_store
import equipment
from accounts import LocalAccounts
from game import LocalGame
import test_systems  # module import: keep its TestCase out of this module's collection

Q1 = {0: 20000, 1: 21001, 2: 22002, 3: 23003}  # requireLevel 1, one per pos
Q2_POS2 = 12102  # quality 2, pos 2 (III), requireLevel 10
SUIT_1300 = [10300, 11300, 12300, 13300]  # quality 4 suit, requireLevel 40


class EquipCarryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(account)
        self.rng = random.Random(7)

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

    def _add_actor(self, actor_id: int, level: int = 1) -> int:
        state = self._state()
        record = actor_store.add(self.profile, state, actor_id)
        record["level"] = level
        self.game.systems._save(self.profile, state)
        return int(record["uid"])

    def _set_level(self, uid: int, level: int) -> None:
        state = self._state()
        actor_store.get(self.profile, state, uid)["level"] = level
        self.game.systems._save(self.profile, state)

    def _post(self, path: str, payload) -> tuple:
        return self.game.handle_http("POST", path, json.dumps(payload).encode(), "application/json", self.auth)

    def _record(self, uid: int) -> dict:
        return self._state()["equips"][str(uid)]

    @staticmethod
    def _attrs(attrs) -> dict:
        return {attr.type: attr.value for attr in attrs}

    # -- equip / unequip -----------------------------------------------------
    def test_equip_on_fills_slot_and_reply_records(self):
        state = self._state()
        state["equips"]["20001"] = {"id": 20001, "uid": 20001, "level": 1, "actorUid": 0}  # old bogus row
        self.game.systems._save(self.profile, state)
        uid = self._grant(Q1[2])
        status, result = self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": uid})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiEquipOn.result")
        self.assertEqual((result.new_actor.uid, result.new_actor.id), (1, 1001))
        self.assertEqual(dict(result.new_actor.body_equips), {"2": uid})
        self.assertEqual((result.new_equip.uid, result.new_equip.id, result.new_equip.actor_uid), (uid, Q1[2], 1))
        self.assertFalse(result.new_equip.is_new)
        self.assertTrue(result.new_equip.random_attrs)
        # No previous owner / displaced item: the same records again, never
        # an empty default record (DBH.UpdateActors would index fci/actor/0).
        self.assertEqual(result.old_actor.uid, 1)
        self.assertEqual(result.old_equip.uid, uid)
        # equipAttrs = EquipBaseAttr(level 1) + random attrs; attrs include them.
        expected = {a["type"]: a["value"] for a in equipment.equip_attrs_for([self._record(uid)])}
        self.assertEqual(expected[1], 108)  # EquipBaseAttr 220 level 1: Hp 108, PhysicalDefense 12
        self.assertEqual(self._attrs(result.new_actor.equip_attrs), expected)
        container = self.game._types["DBGamePlayer.Actors"]()
        self.game._append_local_actor(container, 1001, 1, 0)
        base = self._attrs(container.player_actors[-1].attrs)
        totals = self._attrs(result.new_actor.attrs)
        for attr_type in set(base) | set(expected):
            self.assertEqual(totals.get(attr_type, 0), base.get(attr_type, 0) + expected.get(attr_type, 0))
        self.assertEqual(self._record(uid)["actorUid"], 1)
        self.assertNotIn("20001", self._state()["equips"])  # placeholder pruned

    def test_unknown_equip_or_actor_is_rejected_without_new_rows(self):
        uid = self._grant(Q1[0])
        status, payload = self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": 4242})
        self.assertEqual((status, payload["error"]), (404, "equip_not_found"))
        status, payload = self._post("/fci/equip/on/", {"actorUid": 77, "equipUid": uid})
        self.assertEqual((status, payload["error"]), (404, "actor_not_found"))
        self.assertEqual(sorted(self._state()["equips"]), [str(uid)])

    def test_level_limit(self):
        uid = self._grant(Q2_POS2)
        status, payload = self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": uid})
        self.assertEqual((status, payload["error"]), (409, "equip_level_limit"))
        self._set_level(1, 10)
        status, result = self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": uid})
        self.assertEqual(status, 200)
        self.assertEqual(dict(result.new_actor.body_equips), {"2": uid})

    def test_swap_in_same_slot_bags_the_old_item(self):
        first, second = self._grant(Q1[2]), self._grant(Q1[2])
        self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": first})
        status, result = self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": second})
        self.assertEqual(status, 200)
        self.assertEqual(dict(result.new_actor.body_equips), {"2": second})
        self.assertEqual((result.old_equip.uid, result.old_equip.actor_uid), (first, 0))
        self.assertEqual(self._record(first)["actorUid"], 0)
        self.assertEqual(self._record(second)["actorUid"], 1)

    def test_moving_an_item_from_another_character(self):
        other = self._add_actor(1002)
        uid = self._grant(Q1[1])
        mine = self._grant(Q1[1])
        self._post("/fci/equip/on/", {"actorUid": other, "equipUid": uid})
        self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": mine})
        status, result = self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": uid})
        self.assertEqual(status, 200)
        self.assertEqual(dict(result.new_actor.body_equips), {"1": uid})
        self.assertEqual(result.old_actor.uid, other)
        self.assertEqual(dict(result.old_actor.body_equips), {})
        self.assertEqual(list(result.old_actor.equip_attrs), [])
        self.assertEqual((result.old_equip.uid, result.old_equip.actor_uid), (mine, 0))

    def test_unequip_by_body_pos_and_unequip_all(self):
        uids = {pos: self._grant(equip_id) for pos, equip_id in Q1.items()}
        for uid in uids.values():
            self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": uid})
        status, result = self._post("/fci/equip/off/", {"actorUid": 1, "bodyPos": 0})
        self.assertEqual(status, 200)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiEquipOff.result")
        self.assertEqual((result.new_equip.uid, result.new_equip.actor_uid), (uids[0], 0))
        self.assertEqual(dict(result.new_actor.body_equips), {str(p): uids[p] for p in (1, 2, 3)})
        # WidgetActorEquip "unequip all": fci/equip/off/1 reads offEquips.
        status, result = self._post("/fci/equip/off/1", {"actorUid": 1})
        self.assertEqual(status, 200)
        self.assertEqual(sorted(item.uid for item in result.off_equips), sorted(uids[p] for p in (1, 2, 3)))
        self.assertTrue(all(item.actor_uid == 0 for item in result.off_equips))
        self.assertEqual(dict(result.new_actor.body_equips), {})
        self.assertEqual(list(result.new_actor.equip_attrs), [])
        self.assertTrue(all(self._record(uid)["actorUid"] == 0 for uid in uids.values()))

    # -- one-key carry --------------------------------------------------------
    def test_on_all_carries_the_client_selection(self):
        a, b = self._grant(Q1[0]), self._grant(Q1[3])
        status, result = self._post("/fci/equip/on-all/", {"actorUid": 1, "equipUids": [a, b]})
        self.assertEqual(status, 200)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiEquipOnAll.result")
        self.assertEqual(dict(result.new_actor.body_equips), {"0": a, "3": b})
        self.assertEqual(sorted(item.uid for item in result.new_equips), sorted([a, b]))
        self.assertTrue(all(item.actor_uid == 1 for item in result.new_equips))

    def test_on_all_without_list_picks_best_per_empty_slot(self):
        other = self._add_actor(1002)
        self._set_level(1, 12)
        low = self._grant(Q1[2], level=1, randomAttrs=[])
        high_level = self._grant(Q1[2], level=3, randomAttrs=[])
        better_quality = self._grant(Q2_POS2, level=1, randomAttrs=[])  # q2 beats any q1
        too_high = self._grant(SUIT_1300[0])  # requireLevel 40: skipped
        weak = self._grant(Q1[0], level=1, randomAttrs=[])
        strong = self._grant(Q1[0], level=1, randomAttrs=[{"type": 7, "value": 300, "isRatio": True}])
        worn_elsewhere = self._grant(Q1[1], level=5)
        self._post("/fci/equip/on/", {"actorUid": other, "equipUid": worn_elsewhere})
        status, result = self._post("/fci/equip/on-all/", {"actorUid": 1})
        self.assertEqual(status, 200)
        self.assertEqual(dict(result.new_actor.body_equips), {"0": strong, "2": better_quality})
        self.assertEqual(self._record(worn_elsewhere)["actorUid"], other)
        for uid in (low, high_level, too_high, weak):
            self.assertEqual(self._record(uid)["actorUid"], 0)

    # -- plans (搭配) ------------------------------------------------------------
    def test_plan_save_load_rename_and_lock(self):
        other = self._add_actor(1002)
        uids = {pos: self._grant(equip_id) for pos, equip_id in Q1.items()}
        for pos in (0, 2):
            self._post("/fci/equip/on/", {"actorUid": 1, "equipUid": uids[pos]})
        # Empty account: entering default shape.
        status, plans = self.game.handle_http("GET", "/fci/equip/custom-match", None, "application/json", self.auth)
        self.assertEqual((status, plans), (200, {"equipCustomMatch": {}}))
        # EquipMatch.SaveEquipMatchImpl: {equipUids = bodyEquips values, matchIndex}
        status, result = self._post("/fci/equip/custom-match", {"equipUids": [uids[0], uids[2]], "matchIndex": 2})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiEquipCustomMatch.result")
        self.assertEqual(dict(result.equip_custom_match[2].match_equip_uids), {0: uids[0], 2: uids[2]})
        self.assertEqual({item.uid: item.match_index for item in result.change_equips}, {uids[0]: 2, uids[2]: 2})
        status, plans = self.game.handle_http("GET", "/fci/equip/custom-match", None, "application/json", self.auth)
        self.assertEqual(plans.DESCRIPTOR.full_name, "ApiEquipCustomMatch.result")
        self.assertEqual(dict(plans.equip_custom_match[2].match_equip_uids), {0: uids[0], 2: uids[2]})
        # Saving an item into plan 3 moves it out of plan 2.
        status, result = self._post("/fci/equip/custom-match", {"equipUids": [uids[2]], "matchIndex": 3})
        self.assertEqual(dict(result.equip_custom_match[2].match_equip_uids), {0: uids[0]})
        self.assertEqual(dict(result.equip_custom_match[3].match_equip_uids), {2: uids[2]})
        self.assertEqual(self._record(uids[2])["matchIndex"], 3)
        # Rename (EquipMatchRename reads result.matchName).
        status, result = self._post("/fci/equip/modify-match-name/", {"matchIndex": 2, "matchName": "推图"})
        self.assertEqual((status, result.match_name), (200, "推图"))
        # Load plan 2 on another character: the item moves over.
        self._post("/fci/equip/on/", {"actorUid": other, "equipUid": uids[1]})
        status, result = self._post("/fci/equip/apply-match", {"actorUid": other, "matchIndex": 2})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiEquipApplyMatch.result")
        actors = {actor.uid: actor for actor in result.change_actors}
        self.assertEqual(dict(actors[other].body_equips), {"0": uids[0], "1": uids[1]})
        self.assertEqual(dict(actors[1].body_equips), {"2": uids[2]})
        self.assertEqual({item.uid: item.actor_uid for item in result.change_equips}, {uids[0]: other})
        # EquipDetail.SetLock on a plan item: removed from the plan.
        status, result = self._post(f"/fci/equip/{uids[0]}/setlock/", {"isLock": False})
        self.assertEqual(status, 200)
        self.assertEqual((result.equip.uid, result.equip.match_index, result.equip.actor_uid), (uids[0], 0, other))
        self.assertTrue(result.equip.random_attrs)
        self.assertEqual(dict(result.equip_custom_match[2].match_equip_uids), {})
        self.assertEqual(result.equip_custom_match[2].match_name, "推图")
        status, result = self._post(f"/fci/equip/{uids[3]}/setlock/", {"isLock": True})
        self.assertTrue(result.equip.is_lock)
        status, payload = self._post("/fci/equip/apply-match", {"actorUid": 1, "matchIndex": 5})
        self.assertEqual((status, payload["error"]), (404, "match_not_found"))
        status, payload = self._post("/fci/equip/custom-match", {"equipUids": [uids[3]], "matchIndex": 9})
        self.assertEqual(status, 400)

    # -- character records everywhere -------------------------------------------
    def test_suit_special_attrs_and_actor_projections(self):
        self._set_level(1, 40)
        uids = [self._grant(equip_id) for equip_id in SUIT_1300]
        status, result = self._post("/fci/equip/on-all/", {"actorUid": 1, "equipUids": uids})
        self.assertEqual(status, 200)
        self.assertEqual(list(result.new_actor.special_attr_ids), [23421])  # highest activated tier
        state = self._state()
        # Actor upgrade replies (_st_actor) keep the slots.
        projected = self.game.systems._st_actor(actor_store.get(self.profile, state, 1), self.profile, state)
        self.assertEqual(dict(projected.body_equips), {str(p): uids[p] for p in range(4)})
        # Login bootstrap helper: game._append_local_actor + apply_worn_equipment.
        container = self.game._types["DBGamePlayer.Actors"]()
        self.game._append_local_actor(container, 1001, 1, 0, level=40)
        actor = container.player_actors[-1]
        bare = self._attrs(actor.attrs)
        equipment.apply_worn_equipment(actor, self.profile, state)
        equipment.apply_worn_equipment(actor, self.profile, state)  # idempotent
        equip_attrs = self._attrs(actor.equip_attrs)
        self.assertEqual(dict(actor.body_equips), {str(p): uids[p] for p in range(4)})
        for attr_type, value in self._attrs(actor.attrs).items():
            self.assertEqual(value, bare.get(attr_type, 0) + equip_attrs.get(attr_type, 0))
        self.assertEqual(equipment.body_equips_for(self.profile, state, 1), {str(p): uids[p] for p in range(4)})
        # Bootstrap equipment package: owners normalised (legacy id 1001 -> uid 1).
        state["equips"][str(uids[0])]["actorUid"] = 1001
        package = self.game._types["DBGamePlayer.EquipPackage"]()
        equipment.fill_equip_package(self.game, self.profile, package, state=state)
        self.assertEqual({item.uid: item.actor_uid for item in package.equips}, {uid: 1 for uid in uids})

    def test_legacy_local_aliases_still_work(self):
        uid = self._grant(Q1[3])
        body = {"equipUid": uid, "actorUid": 1}
        status, result = self._post("/fci/equip/equipon/", body)
        self.assertEqual((status, dict(result.new_actor.body_equips)), (200, {"3": uid}))
        status, result = self._post("/fci/equip/equipoff/", body)
        self.assertEqual((status, dict(result.new_actor.body_equips)), (200, {}))
        self.assertEqual(self._record(uid)["actorUid"], 0)


if __name__ == "__main__":
    unittest.main()
