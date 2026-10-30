"""The chosen formation reaches the fight; characters are uid-keyed copies."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actors as actor_store  # noqa: E402
from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
import test_systems  # noqa: E402


class FightTeamTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)

    def tearDown(self):
        self._temp.cleanup()

    def call(self, method, path, payload=None, content_type="application/json"):
        body = payload if isinstance(payload, (bytes, type(None))) else json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def _own(self, *actor_ids):
        profile = self.game._profile(self.account)
        state = self.game.systems._state(profile)
        uids = [actor_store.add(profile, state, actor_id)["uid"] for actor_id in actor_ids]
        self.game.systems._save(profile, state)
        return uids

    def test_story_fight_uses_the_selected_six_member_group(self):
        uids = self._own(1002, 1011, 2108, 1004, 1007)  # uids 2..6
        members = [{"actorUid": uid, "index": index} for index, uid in enumerate([1] + uids)]
        status, _ = self.call("PUT", "/fci/ActorGroup/", {"actorGroups": {"2": {"members": members}}})
        self.assertEqual(status, 200)
        status, entered = self.call("POST", "/fci/dungeon/", {"dungeonId": 2012, "selectActorGroupId": 2})
        self.assertEqual(status, 200)
        fighters = entered["baseInfo"]["actors"]
        self.assertEqual([actor["id"] for actor in fighters], [1001, 1002, 1011, 2108, 1004, 1007])
        self.assertEqual(entered["baseInfo"]["actorUid"], [1, 2, 3, 4, 5, 6])
        # Each fighter carries its own skills and table stats, not Misaka's.
        kirito = fighters[1]
        self.assertTrue(all(skill["id"] // 1000 == 1002 for skill in kirito["skills"]))
        self.assertEqual(next(a["value"] for a in kirito["attrs"] if a["type"] == 1), 308)

    def test_empty_slots_are_empty_objects_and_wireformat_group_is_read(self):
        (uid,) = self._own(1002)
        members = [{"actorUid": uid, "index": 3}, {"actorUid": 1, "index": 0}]
        self.call("PUT", "/fci/ActorGroup/", {"actorGroups": {"3": {"members": members}}})
        message = self.game._types["ApiDungeon"](select_actor_group_id=3, dungeon_id=1)
        status, entered = self.call("POST", "/fci/dungeon/", message.SerializeToString(), "application/wireformat")
        self.assertEqual(status, 200)
        fighters = entered["baseInfo"]["actors"]
        self.assertEqual(len(fighters), 6)
        self.assertEqual((fighters[0]["id"], fighters[3]["id"]), (1001, 1002))
        self.assertEqual(fighters[1], {})

    def test_copies_of_one_character_are_separate_uids(self):
        profile = self.game._profile(self.account)
        state = self.game.systems._state(profile)
        first = actor_store.add(profile, state, 1002)
        second = actor_store.add(profile, state, 1002)
        self.game.systems._save(profile, state)
        self.assertNotEqual(first["uid"], second["uid"])
        ids = self.game.local_actor_ids(self.game._profile(self.account))
        self.assertEqual(ids.count(1002), 2)
        # Levelling one copy leaves the other alone.
        status, _ = self.call("POST", f"/fci/actor/{second['uid']}/levelup/", {"levels": 1})
        self.assertEqual(status, 200)
        store = actor_store.instances(profile, self.game.systems._state(self.game._profile(self.account)))
        self.assertEqual((store[str(first["uid"])]["level"], store[str(second["uid"])]["level"]), (1, 2))

    def test_id_keyed_save_migrates_with_the_same_uids(self):
        profile = {"actor_ids": [1001]}
        state = {"actors": {"1001": {"level": 7}, "2108": {"level": 3, "star": 2}, "1": {"level": 9}}}
        store = actor_store.instances(profile, state)
        self.assertEqual({key: (r["id"], r["level"]) for key, r in store.items()}, {"1": (1001, 7), "2": (2108, 3)})
        self.assertEqual(actor_store.add(profile, state, 1002)["uid"], 3)

    def test_win_pays_exp_to_the_team_that_fought_and_drops_equipment(self):
        (uid,) = self._own(1002)
        members = [{"actorUid": 1, "index": 0}, {"actorUid": uid, "index": 3}]
        self.call("PUT", "/fci/ActorGroup/", {"actorGroups": {"2": {"members": members}}})
        # Stage 1 (chapter 1) has the Dungeon_12_1 equipment group.
        status, entered = self.call("POST", "/fci/dungeon/", {"dungeonId": 1, "selectActorGroupId": 2})
        dungeon_uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](dungeonType=1, stageID=1, gameRound=1, winTeam=1)
        self.call("PATCH", f"/fci/dungeon/{dungeon_uid}/active/", active.SerializeToString(), "application/wireformat")
        status, result = self.call("POST", f"/fci/dungeon/{dungeon_uid}/balance", {})
        self.assertEqual(status, 200)
        balance = result["balanceInfo"]
        self.assertEqual(sorted(change["actorUid"] for change in balance["expChange"]), [1, uid])
        equips = [change["equip"] for change in balance["resChange"] if "equip" in change]
        self.assertEqual(len(equips), 1)  # first clear always drops
        self.assertIn(equips[0]["id"], (20000, 21001, 22002, 23003))


if __name__ == "__main__":
    unittest.main()
