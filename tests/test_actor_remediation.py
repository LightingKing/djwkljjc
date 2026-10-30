"""Client contract regression coverage for actor creation, inheritance and resources."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actors
import equipment
import grants
import systems
from accounts import LocalAccounts
from game import LocalGame
import test_systems


class ActorRemediationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = LocalGame(self.root / "game", self.accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.game.systems._rng = random.Random(11)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None):
        return self.game.handle_http(method, path, None if payload is None else json.dumps(payload).encode(),
                                     "application/json", self.auth)

    def state(self):
        return self.game.systems._state(self.profile)

    def save(self, state):
        self.game.systems._save(self.profile, state)

    def add(self, actor_id=1001, **fields):
        state = self.state()
        actor = self.game.systems.new_actor(self.profile, state, actor_id)
        actor.update(fields)
        self.save(state)
        return actor["uid"]

    def login_actors(self, game=None):
        game = game or self.game
        payload = dict(game.login_messages(self.account))["DBGamePlayer.Actors"]
        message = game._types["DBGamePlayer.Actors"]()
        message.ParseFromString(payload)
        return {actor.uid: actor for actor in message.player_actors}

    def test_base_resource_is_plain_list_with_all_base_types(self):
        status, resources = self.call("GET", "/fci/baseresource/")
        self.assertEqual(status, 200)
        self.assertIsInstance(resources, list)
        self.assertTrue(set(self.game._BASE_RESOURCE_TYPES) <= {r["type"] for r in resources if not r.get("id")})
        self.assertEqual(len(self.game._BASE_RESOURCE_TYPES), 14)

    def test_free_gacha_immediately_returns_persisted_talents(self):
        status, result = self.call("POST", "/fci/gachatrade/", {"id": 1, "gachaFree": True})
        self.assertEqual(status, 200)
        drawn = next(change.actor for change in result.info.res_change if change.HasField("actor"))
        self.assertEqual(len(drawn.talent_info), drawn.quality)
        self.assertTrue(drawn.is_new and drawn.is_new_card)
        saved = self.state()["actorInstances"][str(drawn.uid)]
        self.assertEqual([dict(talentId=t.talent_id, talentValue=t.talent_value) for t in drawn.talent_info], saved["talents"])
        restarted = LocalGame(self.root / "game", self.accounts)
        self.assertEqual(list(self.login_actors(restarted)[drawn.uid].talent_info), list(drawn.talent_info))

    def test_common_and_item_grants_seed_after_final_quality(self):
        row = {"talentCountRandoms": [{"count": 4, "rate": 1}], "starRandoms": [{"star": 3, "rate": 1}]}
        with mock.patch.dict(systems.ACTOR_STAR_RANDOM, {(999, 1002): row}):
            changes = grants.grant_rewards(self.game, self.profile, [{"type": 15, "id": 1002, "count": 1, "param": 999}])
            actor = changes[0]["actor"]
            self.assertEqual((actor["quality"], actor["star"], len(actor["talentInfo"])), (4, 3, 4))
            self.assertEqual((actor["countDelta"], actor["isNew"]), (1, True))
            state = self.state()
            message = self.game.systems._message("ApiActorDecompose.post")
            self.assertIsNone(self.game.systems._grant_contents(self.profile, state,
                [{"type": 15, "id": 1002, "count": 1, "param": 999}], message))
            self.save(state)
            granted = message.res_change[0].actor
            self.assertEqual((granted.quality, granted.star, len(granted.talent_info)), (4, 3, 4))
            self.assertEqual(len(self.login_actors()[granted.uid].talent_info), 4)

    def test_legacy_talents_and_pending_reset_survive_migration_and_login(self):
        state = self.state()
        state.pop("actorInstances")
        state["actors"] = {"1001": {"quality": 2, "talents": [{"talentId": 2, "talentValue": 3}],
                                    "talentResetInfo": [{"talentId": 7, "talentValue": 2}], "talentResetPos": 1}}
        self.save(state)
        first = self.state()["actorInstances"]["1"]
        self.assertEqual(first["talents"], state["actors"]["1001"]["talents"])
        self.assertEqual(self.login_actors()[1].talent_reset_pos, 1)
        self.assertEqual(self.state()["actorInstances"]["1"]["talents"], first["talents"])

    def test_old_empty_talents_are_initialized_once(self):
        uid = self.add(1002, quality=3, talents=[])
        first = copy.deepcopy(self.state()["actorInstances"][str(uid)]["talents"])
        self.assertEqual(len(first), 3)
        self.login_actors()
        self.assertEqual(self.state()["actorInstances"][str(uid)]["talents"], first)

    def test_ordinary_inherit_consumes_material_merges_and_moves_best_equipment(self):
        uid = self.add(1001, level=12, star=3, quality=2, exp=25,
                       talents=[{"talentId": 7, "talentValue": 2}, {"talentId": 9, "talentValue": 1}],
                       skills={"1001001": {"level": 4}})
        state = self.state()
        state["actorInstances"]["1"]["talents"] = [{"talentId": 2, "talentValue": 1}]
        equip_uid = equipment.grant_equipment(self.game, self.profile, 21001, state=state)["equip"]["uid"]
        state["equips"][str(equip_uid)]["actorUid"] = uid
        self.save(state)
        status, response = self.call("POST", "/fci/actor/1/inheriting/", {"targetActorUid": uid, "useNewTalent": True})
        self.assertEqual(status, 200, response)
        changed = {r.actor.uid: r.actor for r in response.res_change if r.HasField("actor")}
        self.assertEqual((changed[1].count_delta, changed[uid].count_delta), (0, -1))
        self.assertEqual((changed[1].level, changed[1].star, changed[1].quality, changed[1].cur_exp), (12, 3, 2, 25))
        self.assertEqual([t.talent_id for t in changed[1].talent_info], [7, 9])
        self.assertEqual(next(s.level for s in changed[1].skills if s.id == 1001001), 4)
        self.assertEqual(response.equip_change[equip_uid], 1)
        self.assertNotIn(str(uid), self.state()["actorInstances"])
        restarted = LocalGame(self.root / "game", self.accounts)
        self.assertEqual(self.login_actors(restarted)[1].level, 12)
        before = self.state()
        self.assertEqual(self.call("POST", "/fci/actor/1/inheriting/", {"targetActorUid": uid})[0], 404)
        self.assertEqual(self.state(), before)

    def test_preserve_talent_choice_fills_only_distinct_groups(self):
        uid = self.add(1001, quality=3, talents=[{"talentId": 2, "talentValue": 2},
                {"talentId": 7, "talentValue": 1}, {"talentId": 9, "talentValue": 1}])
        state = self.state()
        state["actorInstances"]["1"]["talents"] = [{"talentId": 2, "talentValue": 5}]
        self.save(state)
        status, response = self.call("POST", "/fci/actor/1/inheriting/", {"targetActorUid": uid, "useNewTalent": False})
        self.assertEqual(status, 200, response)
        talents = self.state()["actorInstances"]["1"]["talents"]
        self.assertEqual(talents, [{"talentId": 2, "talentValue": 5}, {"talentId": 7, "talentValue": 1}, {"talentId": 9, "talentValue": 1}])

    def test_invalid_locked_busy_and_different_actor_requests_do_not_consume(self):
        uid = self.add(1001, locked=True)
        other = self.add(1002)
        for body, error in [({}, "actor_uid_required"), ({"targetActorUid": 1}, "actor_inherit_same_target"),
                            ({"targetActorUid": uid}, "actor_locked"),
                            ({"targetActorUid": other}, "actor_inherit_different_actor"),
                            ({"targetActorUid": uid, "useNewTalent": "false"}, "invalid_use_new_talent")]:
            before = self.state()
            status, response = self.call("POST", "/fci/actor/1/inheriting/", body)
            self.assertGreaterEqual(status, 400)
            self.assertEqual(response["error"], error)
            self.assertEqual(self.state(), before)
        state = self.state()
        state["actorInstances"][str(uid)]["locked"] = False
        state["actorGroups"] = {"1": {"members": [{"actorUid": uid, "index": 1}]}}
        self.save(state)
        self.assertEqual(self.call("POST", "/fci/actor/1/inheriting/", {"targetActorUid": uid})[1]["error"], "actor_in_use")

    def test_inheritance_accepts_real_large_uids_and_wire_request(self):
        state = self.state()
        state["nextActorUid"] = 1001
        self.save(state)
        uid = self.add(1001)
        self.assertEqual(uid, 1001)
        request = self.game.systems._message("ApiActorInheriting")
        request.target_actor_uid = uid
        result = self.game.handle_http("POST", "/fci/actor/1/inheriting/", request.SerializeToString(),
                                      "application/wireformat", self.auth)
        self.assertEqual(result[0], 200, result[1])
        self.assertEqual(set(self.state()["actorInstances"]), {"1"})

    def test_aura_and_deployed_materials_are_protected(self):
        uid = self.add(1001)
        features = self.game.profile_features._state(self.profile)
        features["auraActorUid"] = uid
        self.game.profile_features._save(self.profile, features)
        self.assertEqual(self.call("POST", "/fci/actor/1/inheriting/", {"targetActorUid": uid})[1]["error"], "actor_in_use")
        features["auraActorUid"] = 0
        self.game.profile_features._save(self.profile, features)
        modes = self.game.activity_modes._state(self.profile)
        modes["deploy"]["missions"] = [{"id": 1, "roleid": [1], "startTimestamp": 1}]
        self.game.activity_modes._save(self.profile, modes)
        self.assertEqual(self.call("POST", "/fci/actor/1/inheriting/", {"targetActorUid": uid})[1]["error"], "actor_in_use")
        self.assertIn(str(uid), self.state()["actorInstances"])

    def test_cross_actor_inherit_requires_activity_whitelist(self):
        source = self.add(1001)
        target = self.add(1002)
        before = self.state()
        response = self.call("POST", "/fci/DiffActorInherit/", {"actorUid": source, "targetActorUid": target})
        self.assertEqual(response, (409, {"error": "actor_inherit_activity_unavailable"}))
        self.assertEqual(self.state(), before)
        schedule = mock.Mock()
        schedule.snapshot.return_value = {"active": [{"activityId": 123}]}
        with mock.patch.object(self.game, "activity_schedule", schedule, create=True), mock.patch.object(systems,
                "DIFF_ACTOR_INHERIT", [{"actorId": 1001, "targetId": [1002], "activityId": 123}]):
            status, result = self.call("POST", "/fci/DiffActorInherit/", {"actorUid": source, "targetActorUid": target})
        self.assertEqual(status, 200, result)
        self.assertEqual(result.DESCRIPTOR.full_name, "ApiDiffActorInherit.post")
        self.assertNotIn(str(source), self.state()["actorInstances"])
        self.assertEqual(self.state()["actorInstances"][str(target)]["id"], 1002)

    def test_failure_after_state_write_rolls_back_material_rewards_and_equipment(self):
        uid = self.add(1001)
        state = self.state()
        equip_uid = equipment.grant_equipment(self.game, self.profile, 21001, state=state)["equip"]["uid"]
        state["equips"][str(equip_uid)]["actorUid"] = uid
        self.save(state)
        before = self.state()
        save = self.game.systems._save
        def fail(profile, state):
            save(profile, state)
            raise RuntimeError("injected failure after save")
        with mock.patch.object(self.game.systems, "_save", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                self.call("POST", "/fci/actor/1/inheriting/", {"targetActorUid": uid})
        self.assertEqual(self.state(), before)

    def test_concurrent_inherit_requests_consume_only_once(self):
        uid = self.add(1001)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.call("POST", "/fci/actor/1/inheriting/",
                                                        {"targetActorUid": uid}), range(2)))
        self.assertEqual(sorted(status for status, _ in results), [200, 404])
        self.assertEqual(set(self.state()["actorInstances"]), {"1"})
        self.assertEqual(self.state()["items"]["20001"]["count"], 1)

    def test_unknown_task_routes_do_not_grant_gold_and_extra_points_are_readable(self):
        before = self.game._profile(self.account)["resources"]
        for path in ["/fci/task/nonsense/", "/fci/task/extra-activepoint/reward/"]:
            self.assertEqual(self.call("POST", path, {"taskId": 99999})[0], 404)
        self.assertEqual(self.game._profile(self.account)["resources"], before)
        status, points = self.call("GET", "/fci/task/extra-activepoint/1")
        self.assertEqual(status, 200)
        self.assertEqual(points["extraActivePoint"], 0)
        self.assertEqual([p["targetActivePoint"] for p in points["progress"]], [120, 240, 360, 480, 600])


if __name__ == "__main__":
    unittest.main()
