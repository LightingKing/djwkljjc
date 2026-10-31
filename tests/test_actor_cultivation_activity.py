from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from google.protobuf import json_format, message_factory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import actors
from accounts import LocalAccounts
from cultivation import LocalCultivation, REWARDS, TASKS, task_available
from game import LocalGame
from grants import grant_rewards
from cultivation_flow_helpers import prepare_novel_collection
import test_systems


class ActorCultivationActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1793318400]
        self.accounts = LocalAccounts(self.root / "accounts", clock=lambda: self.now[0])
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0], initial_level=30)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.module = getattr(self.game, "cultivation", None) or LocalCultivation(self.game)

    def tearDown(self):
        self.temp.cleanup()

    def own(self, actor_id=1502, **changes):
        state = self.game.systems._state(self.profile)
        record = actors.add(self.profile, state, actor_id)
        record.update(quality=3, **changes)
        self.game.systems._save(self.profile, state)
        return record["uid"]

    def call(self, method, path, payload=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        return self.module.handle_http(method, path, body, self.profile)

    def info(self):
        return self.module.snapshot(self.profile)[0]

    def claim(self, tid):
        return self.call("POST", f"/fci/task/{tid}/reward-actor-cultivation/", b"null")

    def reward(self, progress, vip=False):
        return self.call("POST", "/fci/actor-cultivation/progress-reward/",
                         {"activityId": 53001, "progress": progress, "vip": vip})

    def balance(self, kind, rid=0):
        return sum(row["count"] for row in self.game._profile(self.account)["resources"]
                   if row["type"] == kind and row.get("id", 0) == rid)

    def seed_progress(self, value, daily=0):
        self.module.snapshot(self.profile)
        with self.game.atomic_mutation():
            state = self.module._state(self.profile)
            state["activities"]["53001"].update(progress=value, progressDaily=daily)
            self.module._save(self.profile, state)

    def upgrade(self):
        return self.call("POST", "/fci/actor-cultivation/53001/upgrade-vip/", b"null")

    def test_eligibility_and_wire_task_definition(self):
        self.assertEqual(self.module.snapshot(self.profile), [])
        uid = self.own()
        state = self.game.systems._state(self.profile)
        state["actorInstances"][str(uid)]["quality"] = 2
        self.game.systems._save(self.profile, state)
        self.assertEqual(self.module.snapshot(self.profile), [])
        self.assertEqual(self.upgrade()[0], 409)
        state["actorInstances"][str(uid)]["quality"] = 3
        self.game.systems._save(self.profile, state)
        info = self.info()
        self.assertEqual((info["activityId"], info["actorId"], info["endTime"]), (53001, 1502, 0))
        tasks = self.module.task_snapshot(self.profile)["tasks"]
        self.assertEqual({t["id"] for t in tasks}, {tid for tid, t in TASKS.items()
                                                  if tid < 54000 and task_available(t)})
        self.assertTrue(all(t["actorCultivationActivityId"] == 53001 and t["res"]["category"] == 104 for t in tasks))
        # C10 keeps the original rows visible.  Missing role-level telemetry
        # uses the documented local battle approximation, so an untouched row
        # is a normal incomplete-task conflict rather than a hidden 404.
        self.assertEqual(self.claim(53030)[0], 409)
        self.assertEqual(self.claim(53003)[0], 409)
        self.assertEqual(self.claim(53006)[0], 409)

    def test_claim_requires_real_progress_and_does_not_accept_client_override(self):
        self.own()
        self.assertEqual(self.claim(53001)[1]["error"], "cultivation_task_incomplete")
        self.assertEqual(self.call("POST", "/fci/actor-cultivation/", {"actorId": 1502, "progress": 100})[0], 405)
        for _ in range(5):
            self.module.on_battle_win(self.profile, 1, actor_ids=[1502], dungeon_type=2)
        status, info = self.claim(53001)
        self.assertEqual((status, info["progress"], info["progressDaily"]), (200, 1, 1))
        self.assertEqual(self.claim(53001)[0], 409)
        self.assertNotIn(53001, [t["id"] for t in self.module.task_snapshot(self.profile)["tasks"]])
        self.assertEqual(self.claim(54001)[0], 409)

    def test_task_json_preserves_reward_progress_and_all_original_descriptor_defaults(self):
        self.own()
        self.own(actor_id=1514)
        tasks = {row["id"]: row for row in self.module.task_snapshot(self.profile)["tasks"]}
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("TaskInfo"))
        for tid, task in tasks.items():
            with self.subTest(task=tid):
                self.assertEqual(task["actorCultivationRewardProgress"], TASKS[tid]["actorCultivationRewardProgress"])
                definition = json_format.ParseDict(task["res"], cls())
                self.assertEqual(task["res"], json_format.MessageToDict(
                    definition, always_print_fields_with_no_presence=True))
                for key in ("activePoint", "extraActivePoint", "preTaskId"):
                    self.assertEqual(task["res"][key], 0)
        self.assertEqual((tasks[53001]["actorCultivationRewardProgress"],
                          tasks[53020]["actorCultivationRewardProgress"]), (1, 2))
        self.assertEqual((tasks[53001]["res"]["gotoId"], tasks[53023]["res"]["gotoId"]), (0, 209))

    def test_wrong_team_or_dungeon_does_not_complete_task(self):
        self.own()
        for _ in range(5):
            self.module.on_battle_win(self.profile, 1, actor_ids=[1001], dungeon_type=2)
            self.module.on_battle_win(self.profile, 1, actor_ids=[1502], dungeon_type=5)
        self.assertEqual(self.claim(53001)[0], 409)
        self.assertEqual(self.claim(53002)[0], 200)

    def test_stored_level_lock_and_favour_are_authoritative(self):
        self.own(level=25, locked=True)
        self.assertEqual(self.claim(53023)[0], 200)
        self.assertEqual(self.claim(53038)[0], 200)
        self.assertEqual(self.claim(53039)[0], 200)
        self.assertEqual(self.claim(53040)[0], 409)
        self.game.profile_features.add_role_favour(self.profile, 2, 100000)
        self.assertEqual(self.claim(53047)[0], 200)
        self.assertEqual(self.info()["progressDaily"], 0)

    def test_exact_event_matching_preserves_specific_objective(self):
        self.own()
        self.module.record(self.profile, "ActorP0TalentM", param=1502)
        self.assertEqual(self.claim(53022)[0], 200)
        state = self.module._state(self.profile)
        self.assertEqual(state["tasks"]["53028"]["progress"], 0)

    def test_daily_reset_restart_and_clock_rollback_preserve_claims_and_cap(self):
        self.own(locked=True)
        self.claim(53023)
        self.module.record(self.profile, 76, param=2)
        self.assertEqual(self.claim(53020)[0], 200)
        self.now[0] += 86400
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.module = getattr(self.game, "cultivation", None) or LocalCultivation(self.game)
        self.profile = self.game._profile(self.account)
        self.assertEqual(self.claim(53023)[0], 409)
        self.assertEqual(self.claim(53020)[1]["error"], "cultivation_task_incomplete")
        self.module.record(self.profile, 76, param=2)
        self.assertEqual(self.claim(53020)[0], 200)
        self.now[0] -= 86400
        self.assertEqual(self.claim(53020)[0], 409)
        self.assertEqual(self.info()["progressDaily"], 4)
        self.seed_progress(99, daily=70)
        self.now[0] += 2 * 86400
        self.module.record(self.profile, 76, param=2)
        self.assertEqual(self.claim(53020)[1]["error"], "cultivation_progress_limit")
        self.assertEqual(self.info()["progressDaily"], 70)

    def test_vip_uses_paid_balance_and_rejects_duplicate(self):
        self.own()
        self.game.economy._change_resources(self.profile, [{"type": 5, "count": 5000}])
        self.assertEqual(self.upgrade()[0], 409)
        self.assertFalse(self.info()["vip"])
        self.game.economy._change_resources(self.profile, [{"type": 27, "count": 1500}])
        status, result = self.upgrade()
        self.assertEqual(status, 200, result)
        self.assertTrue(result["info"]["vip"])
        self.assertEqual((self.balance(5), self.balance(27)), (5220, 220))
        self.assertEqual(self.upgrade()[0], 409)
        self.assertEqual(self.balance(27), 220)

    def test_vip_concurrency_charges_once(self):
        self.own()
        self.game.economy._change_resources(self.profile, [{"type": 27, "count": 3000}])
        with ThreadPoolExecutor(max_workers=2) as executor:
            replies = list(executor.map(lambda _: self.upgrade(), range(2)))
        self.assertEqual(sorted(status for status, _ in replies), [200, 409])
        self.assertEqual(self.balance(27), 1720)

    def test_cumulative_threshold_claim_and_two_tracks(self):
        self.own()
        self.seed_progress(6)
        self.assertEqual(self.reward(6, vip=True)[0], 409)
        self.assertEqual(self.reward(9)[0], 409)
        self.assertEqual(self.reward(4)[0], 400)
        status, result = self.reward(6)
        self.assertEqual(status, 200, result)
        expected = sum(r["count"] for progress in (3, 6) for r in REWARDS[(53001, progress)]["reward"])
        self.assertEqual(self.game.systems._state(self.profile)["items"]["10004"]["count"], expected)
        self.assertEqual(self.reward(3)[0], 409)
        self.game.economy._change_resources(self.profile, [{"type": 27, "count": 1280}])
        self.assertEqual(self.upgrade()[0], 200)
        self.assertEqual(self.reward(6, vip=True)[0], 200)
        self.assertEqual((self.info()["progressReward"], self.info()["progressRewardVip"]), (6, 6))

    def test_completion_grants_avatar_skin_and_remaining_vip_before_artwork(self):
        self.own()
        self.game.economy._change_resources(self.profile, [{"type": 27, "count": 1280}])
        self.assertEqual(self.upgrade()[0], 200)
        self.seed_progress(100, 70)
        status, result = self.reward(100)
        self.assertEqual(status, 200, result)
        info = result["info"]
        self.assertEqual((info["endTime"], info["progressReward"], info["progressRewardVip"]), (self.now[0], 100, 100))
        self.assertEqual(self.balance(2, 502), 1)
        self.assertEqual(self.balance(29, 100210), 1)
        self.assertEqual(self.game.systems._state(self.profile)["items"]["86001"]["count"], 1)
        self.assertEqual(self.reward(100)[0], 409)
        self.assertEqual(self.call("PATCH", "/fci/actor-cultivation/showend", {"actorId": 1502})[0], 200)
        self.assertTrue(self.info()["showEnd"])

    def test_failed_reward_rolls_back_items_resources_and_claim_marker(self):
        self.own()
        self.seed_progress(51)
        before = copy.deepcopy(self.game.systems._state(self.profile))
        with patch.object(self.module, "_save", side_effect=RuntimeError("write failed")):
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                self.reward(51)
        self.assertEqual(self.game.systems._state(self.profile)["items"], before["items"])
        self.assertEqual(self.balance(2, 502), 0)
        self.assertEqual(self.info()["progressReward"], 0)

    def test_failed_vip_does_not_charge_or_notify(self):
        self.own()
        self.game.economy._change_resources(self.profile, [{"type": 27, "count": 1280}])
        notified = []
        self.module.notifier = lambda profile, payload: notified.append(payload)
        with patch.object(self.module, "_save", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.upgrade()
        self.assertEqual(self.balance(27), 1280)
        self.assertFalse(self.info()["vip"])
        self.assertEqual(notified, [])

    def test_native_descriptor_requests_reward_and_showend(self):
        self.own()
        self.seed_progress(100, 70)
        request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiActorCultivationProgressReward"))()
        request.activity_id, request.progress, request.vip = 53001, 100, False
        status, result = self.game.handle_http("POST", "/fci/actor-cultivation/progress-reward/",
                                             request.SerializeToString(), "application/wireformat", self.auth)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["info"]["progressReward"], 100)
        self.assertEqual(result["info"]["progressRewardVip"], 0)
        self.assertEqual(self.balance(29, 100210), 0)
        request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiActorCultivationShowEnd"))()
        request.actor_id = 1502
        status, result = self.game.handle_http("PATCH", "/fci/actor-cultivation/showend/",
                                             request.SerializeToString(), "application/wireformat", self.auth)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["showEnd"])

    def test_supported_task_sources_can_finish_without_arbitrary_progress(self):
        uid = self.own(level=52, locked=True)
        state = self.game.systems._state(self.profile)
        state["actorInstances"][str(uid)]["talents"] = [{"talentId": 1502131, "talentValue": 1}]
        self.game.systems._save(self.profile, state)
        status, liked = self.game.handle_http("POST", "/fci/ActorLike/1502", b"null", "application/json", self.auth)
        self.assertEqual(status, 200, liked)
        self.game.profile_features.add_role_favour(self.profile, 2, 100000)
        prepare_novel_collection(self, 8)
        status, collected = self.game.handle_http("POST", "/fci/CollectionsNovel/8", b"null", "application/json", self.auth)
        self.assertEqual(status, 200, collected)
        self.assertEqual(collected["novels"][0]["curRewardNum"], 1)
        status, joined = self.game.handle_http("PUT", "/fci/guild/1/pending-members/",
            json.dumps({"playerId": self.profile["player_id"]}), "application/json", self.auth)
        self.assertEqual(status, 200, joined)
        status, wishes = self.game.handle_http("GET", "/fci/guild/1/donate/", None, None, self.auth)
        self.assertEqual(status, 200, wishes)
        target = next(key for key, wish in wishes["donates"].items() if wish["donateRes"]["id"] == 20002)
        grant_rewards(self.game, self.profile, [{"type": 11, "id": 20002, "count": 1}])
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiGuildDonateSend"))
        donation = cls(player_id_request=target, res={"type": 11, "id": 20002, "count": 1})
        status, sent = self.game.handle_http("POST", "/fci/guild/1/donate/send/",
            donation.SerializeToString(), "application/wireformat", self.auth)
        self.assertEqual(status, 200, sent)
        for _ in range(90):
            self.module.on_battle_win(self.profile, 8, actor_ids=[1502], dungeon_type=2)
        permanent = [tid for tid, task in TASKS.items() if tid < 54000 and task_available(task)
                     and not task.get("refreshAfterGot")]
        for tid in permanent:
            self.assertEqual(self.claim(tid)[0], 200, tid)
        # The local approximation also completes the previously hidden
        # permanent rows, adding their configured cultivation progress.
        self.assertEqual(self.info()["progress"], 40)
        for day in range(9):
            self.now[0] += 86400
            for dungeon_type, times in ((2, 5), (5, 5), (3, 1), (9, 2), (13, 2)):
                for _ in range(times):
                    self.module.on_battle_win(self.profile, 8, actor_ids=[1502], dungeon_type=dungeon_type,
                                              activity_type=51, activity_id=51002)
            self.module.record(self.profile, 76, param=2)
            self.module.record(self.profile, 78, param=1502)
            for row in self.module.task_snapshot(self.profile)["tasks"]:
                if row["isDone"]:
                    status, result = self.claim(row["id"])
                    self.assertEqual(status, 200)
                    if result["progress"] == 100:
                        break
        self.assertEqual((self.info()["progress"], self.info()["progressDaily"]), (100, 60))
        self.assertEqual(self.reward(100)[0], 200)


if __name__ == "__main__":
    unittest.main()
