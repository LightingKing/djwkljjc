from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import equipment
from accounts import LocalAccounts
from game import LocalGame


class LocalSystemsMaintenanceTests(unittest.TestCase):
    @staticmethod
    def _guest(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        second_token = "AccountToken " + second[1]["accountToken"]
        body = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
        auth = accounts.handle("POST", "/Auth/", body, "application/wireformat", second_token)
        return accounts.verify_access_token(auth[1]["tokenAccess"]), "Token " + auth[1]["tokenAccess"]

    @staticmethod
    def _call(game: LocalGame, token: str, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        return game.handle_http(method, path, body, "application/json", token)

    def test_actor_and_equipment_costs_locks_and_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, token = self._guest(accounts)
            profile_before = game._profile(account)
            gold_before = next(x["count"] for x in profile_before["resources"] if x["type"] == 7)

            status, level_result = self._call(game, token, "POST", "/fci/actor/1001/levelup/", {"levels": 1})
            self.assertEqual(status, 200)
            self.assertEqual(level_result.DESCRIPTOR.full_name, "ApiActorLevelUp.post")
            profile_after = game._profile(account)
            gold_after = next(x["count"] for x in profile_after["resources"] if x["type"] == 7)
            self.assertEqual(gold_before - gold_after, 100)

            status, _ = self._call(game, token, "POST", "/fci/actor/1001/setactorlock/", {"isLock": True})
            self.assertEqual(status, 200)
            status, _ = self._call(game, token, "POST", "/fci/actor/1001/skill/learn/", {"skillId": 1001001})
            self.assertEqual(status, 200)
            status, _ = self._call(game, token, "POST", "/fci/actor/1001/starlevelup/", {})
            self.assertEqual(status, 200)

            # Enhance needs a real owned item plus food (a never-granted uid
            # such as 20001 is 404): grant equipment 20000 and material 40001.
            profile = game._profile(account)
            equip_uid = equipment.grant_equipment(game, profile, 20000)["equip"]["uid"]
            state = game.systems._state(profile)
            state.setdefault("items", {})["40001"] = {"id": 40001, "count": 1}
            game.systems._save(profile, state)
            status, _ = self._call(
                game, token, "POST", f"/fci/equip/{equip_uid}/enhance/",
                {"foodRes": [{"type": 11, "id": 40001, "count": 1}]},
            )
            self.assertEqual(status, 200)
            status, _ = self._call(game, token, "POST", f"/fci/equip/{equip_uid}/setequiplock/", {"isLock": True})
            self.assertEqual(status, 200)
            status, _ = self._call(game, token, "POST", f"/fci/equip/{equip_uid}/decompose/", {})
            self.assertEqual(status, 409)

            restarted = LocalGame(root / "game", accounts)
            status, actor = self._call(restarted, token, "GET", "/fci/actor/1001/")
            self.assertEqual(status, 200)
            self.assertEqual(actor.result.level, 2)
            self.assertTrue(actor.result.is_lock)
            self.assertTrue(any(skill.id == 1001001 for skill in actor.result.skills))

    def test_items_resources_and_cultivation_are_atomic_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _account, token = self._guest(accounts)

            self.assertEqual(self._call(game, token, "POST", "/fci/item/", {"id": 55, "count": 3})[0], 200)
            self.assertEqual(self._call(game, token, "POST", "/fci/item/use/", {"id": 55, "count": 2})[0], 200)
            self.assertEqual(self._call(game, token, "POST", "/fci/item/sell/", {"id": 55, "count": 1})[0], 200)
            status, items = self._call(game, token, "GET", "/fci/playeritem/")
            self.assertEqual(status, 200)
            self.assertEqual(items["items"], [])

            # Three low tier resources are consumed and one higher tier unit
            # is granted in the same economy update.
            status, result = self._call(game, token, "POST", "/fci/resource/compose/", {"type": 6, "id": 0, "count": 1})
            self.assertEqual(status, 200)
            self.assertEqual(result.DESCRIPTOR.full_name, "ApiResourceCompose.result")

            status, cultivation = self._call(game, token, "POST", "/fci/actorcultivation/", {"actorId": 1001, "progress": 3})
            self.assertEqual(status, 200)
            self.assertEqual(cultivation.result.progress, 3)

            restarted = LocalGame(root / "game", accounts)
            status, cultivation = self._call(restarted, token, "GET", "/fci/actorcultivation/", {"actorId": 1001})
            self.assertEqual(status, 200)
            self.assertEqual(cultivation["progress"], 3)

    def test_insufficient_level_up_does_not_change_actor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, token = self._guest(accounts)
            profile = game._profile(account)
            # Spend the starter gold through the same economy seam used by
            # the operation under test, leaving an exact zero balance.
            starter_gold = next(x["count"] for x in profile["resources"] if x["type"] == 7)
            ok, _changes, _error = game.economy._change_resources(
                profile, [{"type": 7, "id": 0, "param": 0, "count": -starter_gold}]
            )
            self.assertTrue(ok)
            status, payload = self._call(game, token, "POST", "/fci/actor/1001/levelup/", {"levels": 1})
            self.assertEqual(status, 409)
            status, actor = self._call(game, token, "GET", "/fci/actor/1001/")
            self.assertEqual(status, 200)
            self.assertEqual(actor.result.level, 1)

    def test_actor_like_is_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _account, token = self._guest(accounts)

            status, first = self._call(game, token, "GET", "/fci/actor/1001/actor-like/")
            self.assertEqual(status, 200)
            self.assertEqual(first.DESCRIPTOR.full_name, "ApiActorLike.get")
            self.assertEqual((first.like_num, first.is_like), (0, False))

            status, liked = self._call(game, token, "POST", "/fci/actor/1001/actor-like/", {})
            self.assertEqual(status, 200)
            self.assertEqual(liked.DESCRIPTOR.full_name, "ApiActorLike.post")
            self.assertEqual((liked.like_num, liked.is_like), (1, True))
            # Retries from the client are safe and do not double count.
            status, repeated = self._call(game, token, "POST", "/fci/actor/1001/actor-like/", {})
            self.assertEqual(status, 200)
            self.assertEqual((repeated.like_num, repeated.is_like), (1, True))

            restarted = LocalGame(root / "game", accounts)
            status, persisted = self._call(restarted, token, "GET", "/fci/actor/1001/actor-like/")
            self.assertEqual(status, 200)
            self.assertEqual((persisted.like_num, persisted.is_like), (1, True))
            status, unliked = self._call(restarted, token, "DELETE", "/fci/actor/1001/actor-like/")
            self.assertEqual(status, 200)
            self.assertEqual((unliked.like_num, unliked.is_like), (0, False))

    def test_actor_inheritance_keeps_source_and_consumes_same_actor_material(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, token = self._guest(accounts)
            import actors as actor_store
            profile = game._profile(account)
            state = game.systems._state(profile)
            material = game.systems.new_actor(profile, state, 1001)
            material["level"] = 2
            material_uid = material["uid"]
            game.systems._save(profile, state)
            status, inherited = self._call(game, token, "POST", "/fci/actor/1/inheriting/",
                                           {"targetActorUid": material_uid})
            self.assertEqual(status, 200, inherited)
            self.assertEqual(inherited.DESCRIPTOR.full_name, "ApiActorInheriting.post")
            actors = {c.actor.uid: c.actor for c in inherited.res_change if c.HasField("actor")}
            self.assertEqual((actors[1].level, actors[1].count_delta), (2, 0))
            self.assertEqual(actors[material_uid].count_delta, -1)
            restarted = LocalGame(root / "game", accounts)
            saved = restarted.systems._state(profile)
            self.assertEqual(actor_store.get(profile, saved, 1)["level"], 2)
            self.assertIsNone(actor_store.get(profile, saved, material_uid))

    def test_m4_typed_skin_equipment_resource_and_seal_routes_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _account, token = self._guest(accounts)

            status, skins = self._call(
                game, token, "GET", "/fci/actor/1001/unlockedactorskins/"
            )
            self.assertEqual((status, skins.DESCRIPTOR.full_name), (200, "ApiUnlockedActorSkins.result"))
            status, selected = self._call(
                game, token, "POST", "/fci/actor/1001/skin/", {"skinId": 7}
            )
            self.assertEqual((status, selected.DESCRIPTOR.full_name), (200, "ApiActorSkin.result"))
            status, skins = self._call(
                game, token, "GET", "/fci/actor/1001/unlockedactorskins/"
            )
            self.assertEqual(list(skins.unlocked_skins), [7])

            status, equipped = self._call(
                game, token, "POST", "/fci/equip/20001/equipon/", {"actorUid": 1001}
            )
            self.assertEqual((status, equipped.DESCRIPTOR.full_name), (200, "ApiEquipOn.result"))
            status, plan = self._call(
                game, token, "POST", "/fci/equip/custommatch/",
                {"planId": 2, "matchName": "本地方案", "equipUids": [20001]},
            )
            self.assertEqual((status, plan.DESCRIPTOR.full_name), (200, "ApiEquipCustomMatch.result"))

            status, resources = self._call(game, token, "GET", "/fci/playerbaseresource/")
            self.assertEqual((status, resources.DESCRIPTOR.full_name), (200, "ApiPlayerBaseResource.result"))
            status, flag = self._call(game, token, "POST", "/fci/resource/newflag/", {"isNew": True})
            self.assertEqual((status, flag.DESCRIPTOR.full_name), (200, "ApiResourceNewFlag.patch"))
            status, filled = self._call(game, token, "POST", "/fci/seal/fill/", {"cellId": 4})
            self.assertEqual((status, filled.DESCRIPTOR.full_name), (200, "ApiSealFill.result"))

            restarted = LocalGame(root / "game", accounts)
            status, persisted = self._call(
                restarted, token, "GET", "/fci/actor/1001/unlockedactorskins/"
            )
            self.assertEqual(status, 200)
            self.assertEqual(list(persisted.unlocked_skins), [7])


if __name__ == "__main__":
    unittest.main()
