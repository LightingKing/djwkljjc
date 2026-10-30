"""Actor growth from the real client UI: uid routing, levelupgrade, starupgrade.

The client addresses characters as ``fci/actor/<uid>/...`` where uid is the
1-based login-bootstrap position (``game.local_actor_ids``), levels up with
``POST fci/actor/<uid>/levelupgrade/ {foodRes:[{type:11,id,count}]}`` and
stars up with ``POST fci/actor/<uid>/starupgrade/ {}``.  Both callbacks pass
``result.resChange`` (item ``baseRes`` deltas + one ``actor``) to
DBH.ResChange.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
import actors as actor_store
from dungeon import ACTOR_LEVELS
from game import LocalGame
import test_systems


def _expected_roll(level: int, exp: int, limit: int) -> tuple[int, int]:
    """Independent reference for the client's predict loop (WidgetActorLevelup)."""
    while level < limit and level + 1 in ACTOR_LEVELS and exp >= ACTOR_LEVELS[level + 1]:
        exp -= ACTOR_LEVELS[level + 1]
        level += 1
    if level == limit and level + 1 in ACTOR_LEVELS:
        exp = min(exp, ACTOR_LEVELS[level + 1])
    return level, exp


class ActorUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, self.token = test_systems.LocalSystemsTests._guest(self.accounts)

    def tearDown(self) -> None:
        self._temp.cleanup()

    # -- helpers -----------------------------------------------------------
    def _call(self, method: str, path: str, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, "application/json", self.token)

    def _profile(self):
        return self.game._profile(self.account)

    def _state(self):
        return self.game.systems._state(self._profile())

    def _edit_state(self, edit) -> None:
        profile = self._profile()
        state = self.game.systems._state(profile)
        edit(state)
        self.game.systems._save(profile, state)

    def _grant(self, item_id: int, count: int) -> None:
        status, _ = self._call("POST", "/fci/item/", {"itemId": item_id, "count": count})
        self.assertEqual(status, 200)

    def _set_player_level(self, level: int) -> None:
        status, _ = self._call("POST", "/fci/baseresource/level/", {"level": level})
        self.assertEqual(status, 200)

    def _item_count(self, item_id: int) -> int:
        return int((self._state().get("items", {}).get(str(item_id)) or {}).get("count", 0))

    def _stored_actor(self, actor_id: int = 1001) -> dict:
        # Characters are uid-keyed instances; this is the first copy of the id.
        import actors as actor_store
        store = actor_store.instances(self.game._profile(self.account), self._state())
        return next(store[key] for key in sorted(store, key=int) if int(store[key]["id"]) == actor_id)

    def _bootstrap_actors(self):
        for name, payload in self.game.login_messages(self.account):
            if name == "DBGamePlayer.Actors":
                actors = self.game._types["DBGamePlayer.Actors"]()
                actors.ParseFromString(payload)
                return actors
        self.fail("login bootstrap has no DBGamePlayer.Actors")

    @staticmethod
    def _split(result):
        base = [entry.base_res for entry in result.res_change if entry.HasField("base_res")]
        actors = [entry.actor for entry in result.res_change if entry.HasField("actor")]
        return base, actors

    # -- uid routing -------------------------------------------------------
    def test_path_number_is_the_bootstrap_uid(self):
        profile = self._profile()
        self._edit_state(lambda state: self.game.systems.new_actor(profile, state, 1002).update(level=3))
        bootstrap = self._bootstrap_actors()
        self.assertEqual(dict(bootstrap.actor_id_map), {1001: 1, 1002: 2})

        status, first = self._call("GET", "/fci/actor/1/")
        self.assertEqual(status, 200)
        self.assertEqual((first.result.id, first.result.uid), (1001, 1))
        status, second = self._call("GET", "/fci/actor/2/")
        self.assertEqual(status, 200)
        self.assertEqual((second.result.id, second.result.uid, second.result.level), (1002, 2, 3))
        # Legacy actor ids keep working and report the bootstrap uid.
        status, legacy = self._call("GET", "/fci/actor/1002/")
        self.assertEqual(status, 200)
        self.assertEqual((legacy.result.id, legacy.result.uid), (1002, 2))

    def test_legacy_save_migrates_actor_ids_to_stable_instance_uids(self):
        # A genuine pre-instance save has no actorInstances or nextActorUid;
        # modifying its obsolete actor map after migration is not a save migration.
        def seed_legacy(state):
            state.pop("actorInstances", None)
            state.pop("nextActorUid", None)
            state["actors"] = {
                "1001": {"id": 1001, "level": 5, "star": 2, "exp": 31},
                "1002": {"id": 1002, "level": 3, "star": 1, "exp": 7},
            }
        self._edit_state(seed_legacy)
        bootstrap = self._bootstrap_actors()
        self.assertEqual(dict(bootstrap.actor_id_map), {1001: 1, 1002: 2})
        first = self._stored_actor(1001)
        second = self._stored_actor(1002)
        self.assertEqual((first["uid"], first["level"], first["star"], first["exp"]), (1, 5, 2, 31))
        self.assertEqual((second["uid"], second["level"], second["exp"]), (2, 3, 7))
        self.assertEqual(dict(self._bootstrap_actors().actor_id_map), dict(bootstrap.actor_id_map))
        self.assertEqual(self._state()["nextActorUid"], 3)

    def test_unknown_uid_is_404_and_never_persists_a_fake_actor(self):
        for method, path, payload in (
            ("GET", "/fci/actor/7/", None),
            ("POST", "/fci/actor/7/levelupgrade/", {"foodRes": [{"type": 11, "id": 10001, "count": 1}]}),
            ("POST", "/fci/actor/7/starupgrade/", {}),
            ("POST", "/fci/actor/7/setactorlock/", {"isLock": True}),
        ):
            status, payload = self._call(method, path, payload)
            self.assertEqual(status, 404, path)
            self.assertEqual(payload["error"], "actor_not_found")
        # Equip-on with an unknown actorUid must not create actor id "7".
        self._call("POST", "/fci/equip/20001/equipon/", {"actorUid": 7})
        self.assertEqual(self.game.local_actor_ids(self._profile()), [1001])
        self.assertEqual([(r["uid"], r["id"]) for r in self._state()["actorInstances"].values()], [(1, 1001)])

    def test_equip_on_reply_uses_bootstrap_uid(self):
        status, result = self._call("POST", "/fci/equip/20001/equipon/", {"actorUid": 1})
        self.assertEqual(status, 200)
        self.assertEqual((result.new_actor.id, result.new_actor.uid), (1001, 1))

    # -- levelupgrade ------------------------------------------------------
    def test_levelupgrade_consumes_exp_items_and_rolls_levels(self):
        self._set_player_level(30)
        self._grant(10001, 5)
        status, result = self._call("POST", "/fci/actor/1/levelupgrade/",
                                    {"foodRes": [{"type": 11, "id": 10001, "count": 5}]})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiActorLevelUp.post")

        level, exp = _expected_roll(1, 500, 30)
        self.assertGreater(level, 2)  # 500 EXP crosses several thresholds
        self.assertEqual((level, exp), (5, 500 - sum(ACTOR_LEVELS[n] for n in range(2, 6))))
        base, actors = self._split(result)
        self.assertEqual([(r.type, r.id, r.count, r.count_delta) for r in base], [(11, 10001, 0, -5)])
        self.assertEqual(len(actors), 1)
        self.assertEqual((actors[0].id, actors[0].uid, actors[0].level, actors[0].cur_exp), (1001, 1, level, exp))
        self.assertTrue(actors[0].attrs)  # full bootstrap record, DBH replaces fci/actor/<uid>

        self.assertEqual(self._item_count(10001), 0)
        stored = self._stored_actor()
        self.assertEqual((stored["level"], stored["exp"]), (level, exp))
        bootstrap = self._bootstrap_actors().player_actors[0]
        self.assertEqual((bootstrap.level, bootstrap.cur_exp), (level, exp))

    def test_levelupgrade_mixed_items_and_refusals(self):
        self._set_player_level(30)
        self._grant(10002, 2)
        status, payload = self._call("POST", "/fci/actor/1/levelupgrade/",
                                     {"foodRes": [{"type": 11, "id": 10002, "count": 3}]})
        self.assertEqual((status, payload["error"]), (409, "insufficient_item"))
        status, payload = self._call("POST", "/fci/actor/1/levelupgrade/",
                                     {"foodRes": [{"type": 11, "id": 30001, "count": 1}]})
        self.assertEqual((status, payload["error"]), (400, "invalid_food_res"))
        status, _ = self._call("GET", "/fci/actor/1/levelupgrade/")
        self.assertEqual(status, 405)
        self.assertEqual(self._item_count(10002), 2)
        self.assertEqual(self._stored_actor()["level"], 1)

        status, result = self._call("POST", "/fci/actor/1/levelupgrade/",
                                    {"foodRes": [{"type": 11, "id": 10002, "count": 2}]})
        self.assertEqual(status, 200)
        level, exp = _expected_roll(1, 600, 30)
        _, actors = self._split(result)
        self.assertEqual((actors[0].level, actors[0].cur_exp), (level, exp))

    def test_levelupgrade_is_capped_at_player_level(self):
        # Fresh guest: player level 1, so the bar can only fill to the next
        # threshold (WidgetActorLevelup.CanEatFood); overflow is wasted.
        self.assertEqual(int(self._profile()["level"]), 1)
        self._grant(10001, 6)
        status, result = self._call("POST", "/fci/actor/1/levelupgrade/",
                                    {"foodRes": [{"type": 11, "id": 10001, "count": 5}]})
        self.assertEqual(status, 200)
        _, actors = self._split(result)
        self.assertEqual((actors[0].level, actors[0].cur_exp), (1, ACTOR_LEVELS[2]))
        self.assertEqual(self._item_count(10001), 1)

        # A full bar at the limit refuses more food without consuming it.
        status, payload = self._call("POST", "/fci/actor/1/levelupgrade/",
                                     {"foodRes": [{"type": 11, "id": 10001, "count": 1}]})
        self.assertEqual((status, payload["error"], payload["levelLimit"]), (409, "actor_level_limit", 1))
        self.assertEqual(self._item_count(10001), 1)

        # Raising the player level lets the stored EXP roll over.
        self._set_player_level(3)
        status, result = self._call("POST", "/fci/actor/1/levelupgrade/",
                                    {"foodRes": [{"type": 11, "id": 10001, "count": 1}]})
        self.assertEqual(status, 200)
        _, actors = self._split(result)
        self.assertEqual((actors[0].level, actors[0].cur_exp), _expected_roll(1, ACTOR_LEVELS[2] + 100, 3))
        self.assertLessEqual(actors[0].level, 3)

    def test_legacy_levelup_route_is_unchanged(self):
        status, result = self._call("POST", "/fci/actor/1001/levelup/", {"levels": 1})
        self.assertEqual(status, 200)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiActorLevelUp.post")
        self.assertEqual(self._stored_actor()["level"], 2)

    # -- starupgrade -------------------------------------------------------
    def test_starupgrade_uses_actor_attr_star_promotion(self):
        # ActorAttrStarPromotion[1001, 2]: levelLimit 5, starCost 30001 x5.
        status, payload = self._call("POST", "/fci/actor/1/starupgrade/", {})
        self.assertEqual((status, payload["error"], payload["requiredLevel"]), (409, "actor_level_too_low", 5))

        self._edit_state(lambda state: actor_store.get(self._profile(), state, 1).update(level=5))
        self._grant(30001, 4)
        status, payload = self._call("POST", "/fci/actor/1/starupgrade/", {})
        self.assertEqual((status, payload["error"], payload["required"]), (409, "insufficient_item", 5))
        self.assertEqual(self._item_count(30001), 4)
        self.assertEqual(self._stored_actor()["star"], 1)

        self._grant(30001, 1)
        status, result = self._call("POST", "/fci/actor/1/starupgrade/", {})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiActorStarLevelUp.post")
        base, actors = self._split(result)
        self.assertEqual([(r.type, r.id, r.count, r.count_delta) for r in base], [(11, 30001, 0, -5)])
        self.assertEqual((actors[0].id, actors[0].uid, actors[0].star), (1001, 1, 2))
        self.assertEqual(self._item_count(30001), 0)
        self.assertEqual(self._stored_actor()["star"], 2)
        self.assertEqual(self._bootstrap_actors().player_actors[0].star, 2)

        # Star 3 needs level 20.
        status, payload = self._call("POST", "/fci/actor/1/starupgrade/", {})
        self.assertEqual((status, payload["requiredLevel"]), (409, 20))

    def test_starupgrade_stops_at_max_star(self):
        self._edit_state(lambda state: actor_store.get(self._profile(), state, 1).update(level=60, star=6))
        status, payload = self._call("POST", "/fci/actor/1/starupgrade/", {})
        self.assertEqual((status, payload["error"]), (409, "actor_star_max"))


if __name__ == "__main__":
    unittest.main()
