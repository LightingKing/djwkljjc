"""Real HTTP routes, native requests and typed pushes for cultivation hooks."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from google.protobuf import message_factory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from accounts import LocalAccounts
from game import LocalGame
from grants import grant_rewards
from push import _one, _parse_fields
from server.core.notifications import wire_game_notifications
from server.core.push_service import PushService
import test_systems
from test_task_push import _sc_tasks_change_class
from cultivation_flow_helpers import prepare_novel_collection


class Connection:
    last_client_sequence = 0

    def __init__(self):
        self.messages = []

    def send_sc(self, ack, name, data):
        self.messages.append((name, bytes(data)))


class CultivationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1793352000]
        self.accounts = LocalAccounts(self.root / "accounts", clock=lambda: self.now[0])
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0], initial_level=30,
                              initial_resources=[{"type": 6, "id": 0, "count": 500},
                                                 {"type": 7, "id": 0, "count": 200000}])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        profile = self.profile()
        system = self.game.systems._state(profile)
        # Keep actor uids distinct from both actor ids and role ids: accidentally
        # forwarding a uid as role 2 must not satisfy the signboard objective.
        self.game.systems.new_actor(profile, system, 1003)
        self.actor = self.game.systems.new_actor(profile, system, 1502, 3)["uid"]
        self.other = self.game.systems.new_actor(profile, system, 1514, 3)["uid"]
        system["actorInstances"][str(self.actor)]["quality"] = 3
        system["actorInstances"][str(self.other)]["quality"] = 3
        self.game.systems._save(profile, system)
        self.service, self.connection = PushService(), Connection()
        self.service.register(self.connection, self.account)
        wire_game_notifications(self.game, self.service)
        self.ok("GET", "/fci/actor-cultivation/")
        self.connection.messages.clear()

    def tearDown(self):
        self.temp.cleanup()

    def profile(self):
        return self.game._profile(self.account)

    def wire(self, name, **fields):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        return cls(**fields).SerializeToString()

    def call(self, method, path, payload=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        return self.game.handle_http(method, path, raw,
                                     "application/wireformat" if isinstance(payload, bytes) and payload != b"null" else "application/json", self.auth)

    def ok(self, method, path, payload=None):
        status, result = self.call(method, path, payload)
        self.assertEqual(status, 200, result)
        return result

    def task(self, tid):
        return next(row for row in self.ok("GET", "/fci/task/")["tasks"] if row["id"] == tid)

    def info(self, aid=53001):
        return next(row for row in self.ok("GET", "/fci/actor-cultivation/") if row["activityId"] == aid)

    def grant(self, rid, count):
        grant_rewards(self.game, self.profile(), [{"type": 11, "id": rid, "count": count}])

    def item(self, rid):
        return self.game.systems._state(self.profile())["items"].get(str(rid), {}).get("count", 0)

    def team(self, group, uid):
        request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiActorGroup"))()
        member = request.actor_groups[group].members.add()
        member.actor_uid, member.index = uid, 0
        self.ok("PUT", "/fci/ActorGroup/", request.SerializeToString())

    def start(self, group=2):
        return self.ok("POST", "/fci/dungeon/", self.wire("ApiDungeon", dungeon_id=1, select_actor_group_id=group))

    def finish(self, entered, win=True):
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"]()
        active.dungeonType, active.stageID, active.gameRound = entered["baseInfo"]["dungeonType"], 1, 2
        active.winTeam = 1 if win else 2
        self.ok("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString())
        return self.ok("POST", f"/fci/dungeon/{uid}/balance/", {})

    def test_signboard_native_request_uses_role_not_uid_and_emits_task_res(self):
        self.assertEqual(self.call("PATCH", "/fci/Signboard/", self.wire("ApiSignboard", signboard=999999))[0], 409)
        self.assertEqual(self.task(53020)["progress"], 0)
        self.grant(53003, 2)
        self.ok("PATCH", "/fci/FavourGift/2", {"usedItemList": [{"type": 11, "id": 53003, "count": 2}]})
        self.ok("PATCH", "/fci/Signboard/", self.wire("ApiSignboard", signboard=202))
        json_task = self.task(53020)
        self.assertEqual(json_task["progress"], 1)
        self.assertEqual(json_task["actorCultivationRewardProgress"], 2)
        for key in ("activePoint", "extraActivePoint", "gotoId", "preTaskId"):
            self.assertEqual(json_task["res"][key], 0)
        self.assertEqual(self.task(54020)["progress"], 0)
        frames = [data for name, data in self.connection.messages if name == "ScTasksChange"]
        self.assertTrue(frames)
        # ScTasksChange/ApiTask.result have recovered IL2CPP field constants,
        # while their nested TaskInfo has the original protobuf descriptor.
        message = _sc_tasks_change_class().FromString(frames[-1])
        task = next(row for row in message.tasks if row.id == 53020)
        self.assertTrue(task.isDone)
        self.assertEqual(task.actorCultivationActivityId, 53001)
        self.assertEqual(task.actorCultivationRewardProgress, json_task["actorCultivationRewardProgress"])
        self.assertEqual(next(row for row in message.tasks if row.id == 53001).actorCultivationRewardProgress, 1)
        task_fields = next(_parse_fields(data) for wire, data in _parse_fields(frames[-1])[2]
                           if _one(_parse_fields(data), 1, 0) == 53020)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("TaskInfo"))
        definition = cls.FromString(_one(task_fields, 5, 2))
        self.assertEqual((definition.id, definition.category, definition.goal_type), (53020, 104, 76))
        self.assertEqual(list(definition.task_param), [2])
        self.assertEqual((definition.active_point, definition.extra_active_point, definition.goto_id,
                          definition.pre_task_id), (0, 0, 0, 0))
        self.assertTrue(any(row.id < 53000 for row in message.tasks))
        activity_cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ActorCultivationInfoMessage"))
        activity_pushes = [activity_cls.FromString(data) for name, data in self.connection.messages
                           if name == "ActorCultivationInfoMessage"]
        self.assertTrue(activity_pushes)
        self.assertTrue(any(info.activity_id == 53001 for info in activity_pushes[-1].info))
        claimed = self.ok("POST", "/fci/task/53020/reward-actor-cultivation/", b"null")
        self.assertEqual(claimed["progress"], 2)
        self.assertEqual(self.call("POST", "/fci/task/53020/reward-actor-cultivation/", b"null")[0], 409)

    def test_native_empty_table_vip_and_read_marker_accept_json_null(self):
        profile = self.profile()
        self.game.economy._change_resources(profile, [{"type": 27, "count": 1280}])
        before = {r["type"]: r["count"] for r in self.profile()["resources"] if r["type"] in (5, 27)}
        result = self.ok("POST", "/fci/actor-cultivation/53001/upgrade-vip/?source=welfare", b"null")
        self.assertTrue(result["info"]["vip"])
        after = {r["type"]: r["count"] for r in self.profile()["resources"] if r["type"] in (5, 27)}
        self.assertEqual(after, {kind: count - 1280 for kind, count in before.items()})
        self.assertEqual(self.call("POST", "/fci/actor-cultivation/53001/upgrade-vip/", b"null")[0], 409)
        self.assertFalse(self.ok("PATCH", "/fci/actor-cultivation/53001/new/", b"null")["new"])
        self.assertEqual(self.call("POST", "/fci/actor-cultivation/progress-reward/", b"null")[0], 400)
        for body in (b"[]", b"true", b'"empty"'):
            self.assertEqual(self.call("POST", "/fci/actor-cultivation/53002/upgrade-vip/", body)[0], 400)

    def test_real_novel_collection_completes_only_matching_task_and_survives_reload(self):
        for novel_id, task_id, activity_id in ((8, 53024, 53001), (11, 54024, 53002)):
            with self.subTest(novel=novel_id):
                self.assertEqual(self.task(task_id)["progress"], 0)
                detail = prepare_novel_collection(self, novel_id)
                self.assertEqual(detail["novels"][0]["curRewardNum"], 0)
                self.ok("PUT", f"/fci/CollectionsNovel/{novel_id}", {"newCover": novel_id})
                self.assertEqual(self.task(task_id)["progress"], 0)
                self.assertEqual(self.call("POST", f"/fci/task/{task_id}/reward-actor-cultivation/", b"null")[0], 409)
                collected = self.ok("POST", f"/fci/CollectionsNovel/{novel_id}", b"null")
                self.assertEqual(collected["novels"][0]["curRewardNum"], 1)
                self.assertTrue(self.task(task_id)["isDone"])
                self.assertEqual(self.ok("POST", f"/fci/task/{task_id}/reward-actor-cultivation/", b"null")["progress"], 1)
                self.assertEqual(self.info(activity_id)["progress"], 1)
        self.assertFalse(self.game.profile_features._state(self.profile()).get("collections", {}).get("novels"))
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.assertEqual(self.info(53001)["progress"], 1)
        self.assertEqual(self.info(53002)["progress"], 1)
        ids = {row["id"] for row in self.ok("GET", "/fci/task/")["tasks"]}
        self.assertTrue({53024, 54024}.isdisjoint(ids))

    def test_talent_reset_only_paid_success_advances_matching_actor_task(self):
        self.assertEqual(self.task(53022)["progress"], 0)
        status, error = self.call("POST", "/fci/talent/reset/", {"actorUid": self.actor, "talentPos": 1})
        self.assertEqual((status, error["error"]), (409, "insufficient_item"))
        self.assertEqual(self.task(53022)["progress"], 0)
        self.grant(20002, 1)
        self.assertEqual(self.call("POST", "/fci/talent/reset/", {"actorUid": 1502, "talentPos": 1})[0], 404)
        self.assertEqual(self.item(20002), 1)
        result = self.ok("POST", "/fci/talent/reset/", {"actorUid": self.actor, "talentPos": 1})
        self.assertEqual(result["resChange"][0]["baseRes"]["countDelta"], -1)
        self.assertEqual(self.item(20002), 0)
        self.assertEqual(self.task(53022)["progress"], 1)
        self.assertEqual(self.task(54022)["progress"], 0)
        self.assertEqual(self.ok("POST", "/fci/task/53022/reward-actor-cultivation/", b"null")["progress"], 2)
        self.assertEqual(self.call("POST", "/fci/talent/reset/", {"actorUid": self.actor, "talentPos": 1})[0], 409)
        self.assertEqual(self.info()["progress"], 2)

    def test_battle_uses_entry_team_and_wins_once_including_native_active(self):
        self.team(2, self.actor)
        wrong_team = self.start(group=1)
        self.finish(wrong_team)
        self.assertEqual(self.task(53001)["progress"], 0)
        failed = self.start()
        self.finish(failed, win=False)
        self.assertEqual(self.task(53001)["progress"], 0)
        entered = self.start()
        self.assertIn(self.actor, entered["baseInfo"]["actorUid"])
        self.team(2, self.other)  # editing the preset cannot rewrite an ongoing fight
        self.finish(entered)
        self.assertEqual(self.task(53001)["progress"], 1)
        self.assertEqual(self.task(54001)["progress"], 0)
        self.assertEqual(self.task(53055)["progress"], 1)
        self.ok("POST", f'/fci/dungeon/{entered["dungeonUid"]}/balance/', {})
        self.assertEqual(self.task(53001)["progress"], 1)

    def test_guild_donation_native_validation_and_receipt_replay_progress_once(self):
        self.ok("PUT", "/fci/guild/1/pending-members/", {"playerId": self.profile()["player_id"]})
        donation = self.ok("GET", "/fci/guild/1/donate/")["donates"]["local-bot-002"]
        self.assertEqual(donation["donateRes"]["id"], 20002)
        native = self.wire("ApiGuildDonateSend", player_id_request="local-bot-002", res={"type": 11, "id": 20002, "count": 1})
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/send/", native)[0], 409)
        self.assertEqual(self.task(53027)["progress"], 0)
        self.grant(20002, 2)
        body = {"playerIdRequest": "local-bot-002", "res": {"type": 11, "id": 20002, "count": 1}, "requestId": "cultivation-donate"}
        with patch.object(self.game.cultivation, "record", wraps=self.game.cultivation.record) as recorder:
            first = self.ok("POST", "/fci/guild/1/donate/send/", body)
            replay = self.ok("POST", "/fci/guild/1/donate/send/", body)
            self.assertEqual(recorder.call_count, 1)
            self.assertEqual(recorder.call_args.kwargs["param"], 20002)
        self.assertTrue(any(row["baseRes"]["countDelta"] == -1 for row in first["resChange"]))
        self.assertTrue(all(row["baseRes"]["countDelta"] == 0 for row in replay["resChange"]))
        self.assertEqual(self.item(20002), 1)
        self.assertEqual(self.task(53027)["progress"], 1)
        self.assertEqual(self.task(54027)["progress"], 0)
        self.assertEqual(self.ok("POST", "/fci/task/53027/reward-actor-cultivation/", b"null")["progress"], 1)
        self.ok("POST", "/fci/guild/1/donate/send/", native)
        self.assertEqual(self.item(20002), 0)
        self.assertEqual(self.info()["progress"], 1)  # permanent task remains claimed

    def test_unavailable_specific_modes_are_hidden_and_not_claimable(self):
        ids = {row["id"] for row in self.ok("GET", "/fci/task/")["tasks"]}
        for tid in (53003, 54003, 53006, 54006):
            self.assertNotIn(tid, ids)
            self.assertEqual(self.call("POST", f"/fci/task/{tid}/reward-actor-cultivation/", {})[0], 404)
        self.assertTrue({53001, 53002, 53007, 53008, 53055} <= ids)

    def test_native_guild_wishes_cover_both_cultivation_roles(self):
        self.ok("PUT", "/fci/guild/1/pending-members/", {"playerId": self.profile()["player_id"]})
        wishes = self.ok("GET", "/fci/guild/1/donate/")["donates"]
        for item_id, task_id, other_task in ((20002, 53027, 54027), (20014, 54027, None)):
            with self.subTest(fragment=item_id):
                player_id = next((key for key, wish in wishes.items() if wish["donateRes"]["id"] == item_id), None)
                self.assertIsNotNone(player_id, wishes)
                self.assertEqual(self.task(task_id)["progress"], 0)
                self.grant(item_id, 1)
                native = self.wire("ApiGuildDonateSend", player_id_request=player_id,
                                   res={"type": 11, "id": item_id, "count": 1})
                self.ok("POST", "/fci/guild/1/donate/send/", native)
                self.assertEqual(self.item(item_id), 0)
                self.assertTrue(self.task(task_id)["isDone"])
                if other_task is not None:
                    self.assertEqual(self.task(other_task)["progress"], 0)
                claimed = self.ok("POST", f"/fci/task/{task_id}/reward-actor-cultivation/", b"null")
                self.assertEqual(claimed["progress"], 1)
                self.assertEqual(self.call("POST", "/fci/guild/1/donate/send/", native)[0], 409)


if __name__ == "__main__":
    unittest.main()
