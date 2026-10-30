from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.protobuf import json_format, message_factory

import equipment
from accounts import LocalAccounts
from game import LocalGame
# Import the module, not the TestCase, so its tests are not collected twice.
try:
    import tests.test_systems as _systems_tests
except ImportError:  # unittest discover adds tests/ directly to sys.path
    import test_systems as _systems_tests

_guest = _systems_tests.LocalSystemsTests._guest

Q1_ITEMS = {20000, 21001, 22002, 23003}


class StageEquipmentRollTests(unittest.TestCase):
    def test_stage_groups_match_dungeon_reward(self):
        expected = {1: (1, 8), 2: (9, 17), 3: (18, 29), 4: (30, 43), 5: (44, 60),
                    6: (61, 81), 8: (82, 132), 9: (133, 162), 10: (163, 261)}
        for group, (first, last) in expected.items():
            for stage in (first, last):
                self.assertEqual(equipment.STAGE_EQUIP_GROUP[stage], group, stage)
        # Stage 12 also lists its own per-run group Dungeon_12_7.
        self.assertEqual(equipment.STAGE_EQUIP_GROUP[12], 2)
        self.assertEqual(set(equipment.STAGE_EQUIP_GROUP), set(range(1, 262)))

    def test_pool_sizes_by_quality(self):
        self.assertEqual(set(equipment.EQUIP_POOLS[1]), Q1_ITEMS)
        self.assertEqual({q: len(v) for q, v in equipment.EQUIP_POOLS.items()}, {1: 4, 2: 12, 3: 66, 4: 74})

    def test_first_clear_of_stage_one_drops_a_quality_one_item(self):
        for seed in range(50):
            equip_id = equipment.roll_stage_equipment(1, True, random.Random(seed))
            self.assertIn(equip_id, Q1_ITEMS)

    def test_tier_follows_group(self):
        rng = random.Random(7)
        for stage, quality in ((17, 1), (18, 2), (43, 2), (44, 3), (81, 3), (82, 4), (261, 4)):
            equip_id = equipment.roll_stage_equipment(stage, True, rng)
            self.assertEqual(equipment.EQUIP_INFO[equip_id]["quality"], quality, stage)

    def test_non_story_dungeon_drops_nothing(self):
        self.assertIsNone(equipment.roll_stage_equipment(100004, True, random.Random(1)))
        self.assertIsNone(equipment.roll_stage_equipment(0, True, random.Random(1)))

    def test_repeat_clear_rate_follows_rng(self):
        class FixedRng(random.Random):
            def __init__(self, value):
                super().__init__(0)
                self.value = value

            def random(self):
                return self.value

        self.assertIsNotNone(equipment.roll_stage_equipment(5, False, FixedRng(0.24)))
        self.assertIsNone(equipment.roll_stage_equipment(5, False, FixedRng(0.25)))
        rng = random.Random(1234)
        drops = sum(equipment.roll_stage_equipment(5, False, rng) is not None for _ in range(4000))
        self.assertTrue(850 <= drops <= 1150, drops)
        # Same seed, same sequence.
        first = [equipment.roll_stage_equipment(90, False, random.Random(99)) for _ in range(3)]
        second = [equipment.roll_stage_equipment(90, False, random.Random(99)) for _ in range(3)]
        self.assertEqual(first, second)

    def test_random_attrs_come_from_equip_random_attr(self):
        attrs = equipment.roll_random_attrs(20000, 1, random.Random(3))
        self.assertEqual(len(attrs), 1)
        table = equipment.RANDOM_ATTRS[equipment.EQUIP_INFO[20000]["randAttrId"]]
        allowed = [
            entry for key in ("rangeMain", "rangeSub", "rangeSpecial")
            for entry in table[key]["attrs"]
        ]
        attr = attrs[0]
        self.assertTrue(any(
            entry["type"] == attr["type"] and entry["min"] <= attr["value"] <= entry["max"]
            for entry in allowed
        ), attr)
        # Quality 1 adds attributes at levels 1 and 4.
        self.assertEqual(len(equipment.roll_random_attrs(20000, 4, random.Random(3))), 2)


class GrantEquipmentTests(unittest.TestCase):
    def _game(self, root: Path):
        accounts = LocalAccounts(root / "accounts")
        game = LocalGame(root / "game", accounts)
        account, _auth = _guest(accounts)
        return game, game._profile(account)

    def test_grant_persists_unique_uids_and_res_change_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            game, profile = self._game(Path(temp))
            rng = random.Random(5)
            entries = [equipment.grant_equipment(game, profile, 21001, rng=rng) for _ in range(3)]
            uids = [entry["equip"]["uid"] for entry in entries]
            self.assertEqual(len(set(uids)), 3)
            self.assertTrue(all(uid > equipment.UID_BASE for uid in uids))
            equip = entries[0]["equip"]
            self.assertEqual(equip["id"], 21001)
            self.assertEqual(equip["countDelta"], 1)
            self.assertEqual(equip["level"], 1)
            self.assertEqual(equip["actorUid"], 0)
            self.assertTrue(equip["isNew"])
            self.assertEqual(len(equip["randomAttrs"]), 1)

            # The entry is a valid STResourceDynamic in camelCase JSON.
            dynamic = message_factory.GetMessageClass(
                game._pool.FindMessageTypeByName("STResourceDynamic"))()
            json_format.ParseDict(json.loads(json.dumps(entries[0])), dynamic)
            self.assertEqual(dynamic.equip.uid, uids[0])
            self.assertEqual(dynamic.equip.count_delta, 1)

            stored = game.systems._state(profile)["equips"]
            for uid in uids:
                record = stored[str(uid)]
                self.assertEqual(record["id"], 21001)
                for key in ("level", "exp", "locked", "actorUid", "plan"):
                    self.assertIn(key, record)

            # A removed uid is never handed out again.
            state = game.systems._state(profile)
            state["equips"].pop(str(uids[-1]))
            game.systems._save(profile, state)
            again = equipment.grant_equipment(game, profile, 20000, rng=rng)["equip"]["uid"]
            self.assertGreater(again, max(uids))

    def test_grant_with_caller_state_defers_save(self):
        with tempfile.TemporaryDirectory() as temp:
            game, profile = self._game(Path(temp))
            state = game.systems._state(profile)
            entry = equipment.grant_equipment(game, profile, 22002, state=state, rng=random.Random(1))
            uid = str(entry["equip"]["uid"])
            self.assertIn(uid, state["equips"])
            self.assertNotIn(uid, game.systems._state(profile)["equips"])
            game.systems._save(profile, state)
            self.assertIn(uid, game.systems._state(profile)["equips"])

    def test_granted_item_works_with_equip_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = _guest(accounts)
            profile = game._profile(account)
            uid = equipment.grant_equipment(game, profile, 23003, rng=random.Random(2))["equip"]["uid"]
            body = json.dumps({"equipUid": uid, "actorUid": 1}).encode()
            status, _ = game.handle_http("POST", "/fci/equip/equipon/", body, "application/json", auth)
            self.assertEqual(status, 200)
            record = game.systems._state(profile)["equips"][str(uid)]
            self.assertEqual(record["id"], 23003)
            self.assertEqual(record["actorUid"], 1)
            status, _ = game.handle_http("POST", "/fci/equip/equipoff/", body, "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(game.systems._state(profile)["equips"][str(uid)]["actorUid"], 0)

    def test_fill_equip_package_includes_granted_items(self):
        with tempfile.TemporaryDirectory() as temp:
            game, profile = self._game(Path(temp))
            rng = random.Random(9)
            granted = {
                equipment.grant_equipment(game, profile, equip_id, rng=rng)["equip"]["uid"]: equip_id
                for equip_id in (20000, 21001)
            }
            # A placeholder row the generic routes create for an unknown id.
            state = game.systems._state(profile)
            state["equips"]["20001"] = {"id": 20001, "uid": 20001, "level": 1, "exp": 0,
                                        "locked": False, "actorUid": 0, "plan": 0}
            game.systems._save(profile, state)

            package = game._types["DBGamePlayer.EquipPackage"]()
            self.assertEqual(equipment.fill_equip_package(game, profile, package), 2)
            decoded = game._types["DBGamePlayer.EquipPackage"]()
            decoded.ParseFromString(package.SerializeToString())
            self.assertEqual({item.uid: item.id for item in decoded.equips}, granted)
            for item in decoded.equips:
                self.assertEqual(item.level, 1)
                self.assertTrue(item.is_new)
                self.assertEqual(len(item.random_attrs), 1)


if __name__ == "__main__":
    unittest.main()
