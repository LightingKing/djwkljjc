"""Native hidden-event routes, durable rewards and original-table constraints."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from google.protobuf import json_format, message_factory
import test_activity_modes as common

try:
    from hidden_event_dungeons import LocalHiddenEventDungeons
except ModuleNotFoundError:
    LocalHiddenEventDungeons = None


class HiddenEventDungeonTests(common.ModeTestBase):
    def setUp(self):
        super().setUp()
        self.hidden = getattr(self.game, "hidden_events", None)
        if self.hidden is None and LocalHiddenEventDungeons is not None:
            self.hidden = LocalHiddenEventDungeons(self.game)
        self.add_resource(6, 0, 10000)

    def call(self, method, path, payload=None):
        body = payload if isinstance(payload, (bytes, type(None))) else json.dumps(payload).encode()
        if self.hidden is None:
            return None
        return self.hidden.handle_http(method, path, body, self.profile())

    def enter(self, kind=27, aid=27001, did=2700101, prefix="event-dungeon"):
        return self.ok("POST", f"/fci/{prefix}/", {
            "activityType": kind, "activityId": aid, "dungeonId": did,
            "selectActorGroupId": 1, "multiPower": 1,
        })

    def win(self, uid, prefix="event-dungeon"):
        self.ok("PATCH", f"/fci/{prefix}/{uid}/active/", {"winTeam": 1})
        return self.ok("POST", f"/fci/{prefix}/{uid}/balance/", {})

    def test_summer_native_wire_entry_balance_and_relogin_are_idempotent(self):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiEventDungeon"))
        req = cls(select_actor_group_id=1, dungeon_id=2700101, activity_type=27, activity_id=27001, multi_power=1)
        entered = self.ok("POST", "/fci/event-dungeon/", req.SerializeToString())
        self.assertEqual(entered["baseInfo"]["activityType"], 27)
        self.assertEqual(entered["baseInfo"]["dungeonType"], 5)
        self.assertTrue(entered["baseInfo"]["actors"])
        uid = entered["dungeonUid"]
        result = self.win(uid)
        self.assertTrue(result["win"])
        self.assertTrue(result["resChange"])
        self.assertEqual(result["targetsDone"], [1, 2, 3])
        resources = self.profile()["resources"]
        self.hidden = LocalHiddenEventDungeons(self.game)
        self.assertEqual(self.ok("POST", f"/fci/event-dungeon/{uid}/balance/", {}), result)
        self.assertEqual(self.profile()["resources"], resources)
        record = self.ok("GET", "/fci/event-dungeon/record/")
        self.assertTrue(next(row for row in record if row["dungeonId"] == 2700101)["finished"])
        self.ok("DELETE", f"/fci/event-dungeon/{uid}")

    def test_star_reward_requires_stars_and_cannot_be_claimed_twice(self):
        result = self.call("POST", "/fci/event-dungeon/star-reward", {"activityId": 27001, "star": 3})
        self.assertIsNotNone(result)
        self.assertEqual(result[0], 409)
        self.win(self.enter()["dungeonUid"])
        info = self.ok("GET", "/fci/event-dungeon/star-reward")
        self.assertEqual(info["rewardSummary"]["27001"]["totalStar"], 3)
        self.assertIn(27001, self.hidden.claimable_activity_ids(self.profile()))
        reward = self.ok("POST", "/fci/event-dungeon/star-reward", {"activityId": 27001, "star": 3})
        self.assertTrue(any(r["baseRes"]["id"] == 60002 for r in reward["resChange"]))
        self.assertEqual(self.ok("POST", "/fci/event-dungeon/star-reward", {"activityId": 27001, "star": 3})["resChange"], [])
        self.assertNotIn(27001, self.hidden.claimable_activity_ids(self.profile()))

    def test_autumn_christmas_and_carnival_have_complete_records_and_lifecycles(self):
        for kind, aid, did, prefix in ((39, 39001, 2800101, "event-dungeon"),
                                      (40, 40001, 2800107, "event-dungeon"),
                                      (54, 54001, 3000101, "event-dungeon"),
                                      (51, 51002, 2900101, "semifixed-dungeon")):
            with self.subTest(activity=kind):
                records = self.ok("GET", f"/fci/activity/record/{kind}/{aid}")["records"]
                self.assertTrue(records)
                self.assertIn(did, [r["dungeonId"] for r in records])
                result = self.win(self.enter(kind, aid, did, prefix)["dungeonUid"], prefix)
                self.assertTrue(result["win"])
                self.assertTrue(result["resChange"])
                self.assertTrue(self.ok("GET", f"/fci/activity/star-reward/{kind}/{aid}")["totalStar"])

    def test_activity_mismatch_and_original_level_gate_are_rejected(self):
        bad = self.call("POST", "/fci/event-dungeon/", {"activityType": 27, "activityId": 27001, "dungeonId": 3000101})
        self.assertIsNotNone(bad)
        self.assertNotEqual(bad[0], 200)
        self.set_level(17)  # Original DungeonReward 2700101 requires level 18.
        later = self.call("POST", "/fci/event-dungeon/", {"activityType": 27, "activityId": 27001, "dungeonId": 2700101})
        self.assertNotEqual(later[0], 200)

    def test_no_active_result_and_closed_runs_never_award(self):
        uid = self.enter()["dungeonUid"]
        before = self.item(60002)
        rejected = self.call("POST", f"/fci/event-dungeon/{uid}/balance", {"win": True})
        self.assertEqual(rejected[0], 409)
        self.ok("DELETE", f"/fci/event-dungeon/{uid}")
        self.assertEqual(self.call("PATCH", f"/fci/event-dungeon/{uid}/active", {"winTeam": 1})[0], 409)
        self.assertEqual(self.item(60002), before)

    def test_balance_failure_rolls_back_claim_and_reward_for_retry(self):
        uid = self.enter()["dungeonUid"]
        self.ok("PATCH", f"/fci/event-dungeon/{uid}/active", {"winTeam": 1})
        before = self.profile()["resources"]
        with patch.object(self.hidden, "_save", side_effect=RuntimeError("injected persistence failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.call("POST", f"/fci/event-dungeon/{uid}/balance", {})
        self.assertEqual(self.profile()["resources"], before)
        self.assertTrue(self.ok("POST", f"/fci/event-dungeon/{uid}/balance", {})["firstFinish"])

    def test_semifixed_group_uses_original_npc_slots_and_stats(self):
        group = self.ok("GET", "/fci/semifixed-dungeon/actorgroup/2900101")
        members = group["groupInfo"]["members"]
        self.assertTrue(members[2]["isNpc"])
        self.assertTrue(members[5]["isNpc"])
        entered = self.enter(51, 51002, 2900101, "semifixed-dungeon")
        self.assertEqual(entered["baseInfo"]["npcCnt"], 2)
        npc = json.loads((Path(__file__).parents[1] / "data/client-tables/DungeonNPC.json").read_text(encoding="utf8"))["rows"]
        expected = next(r for r in npc if r["npcId"] == 101001)
        actor = entered["baseInfo"]["actors"][2]
        self.assertEqual(actor["id"], expected["actorId"])
        self.assertEqual(actor["attrs"], expected["attrs"])
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiEventDungeon.result"))
        json_format.ParseDict(entered, cls())
        self.assertEqual({s["level"] for s in actor["skills"]}, {10})

    def test_buff_shops_are_distinct_paid_once_and_applied_to_fight(self):
        self.set_item(2009, 500)
        initial = self.ok("GET", "/fci/BuffShop/51")["shopInfo"]
        self.assertEqual(next(g for g in initial if g["goodsId"] == 5101)["grade"], 0)
        self.ok("POST", "/fci/BuffShop/51", {"goodsId": 5101, "grade": 1})
        self.assertEqual(self.item(2009), 490)
        self.assertEqual(self.ok("POST", "/fci/BuffShop/51", {"goodsId": 5101, "grade": 1})["resChange"], [])
        self.assertEqual(self.item(2009), 490)
        self.assertNotEqual(self.call("POST", "/fci/BuffShop/54", {"goodsId": 5101, "grade": 1})[0], 200)
        fight = self.enter(51, 51002, 2900101, "semifixed-dungeon")
        self.assertTrue(fight["baseInfo"]["fightBuffs"])

    def test_claw_original_contract_cost_stock_and_restart(self):
        self.set_item(61001, 3)
        info = self.ok("GET", "/fci/ClawCranePrize/")
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiClawCranePrize.result"))
        json_format.ParseDict(info, cls())
        self.assertEqual({r["id"] for r in info["limitInfo"]}, {1, 2, 3})
        reward = self.ok("POST", "/fci/ClawCraneBingo/", {"requestId": "one"})
        self.assertTrue(reward["resChange"])
        self.assertIn(reward["id"], range(1, 11))
        self.assertEqual(self.item(61001), 2)
        self.hidden = LocalHiddenEventDungeons(self.game)
        self.assertEqual(self.ok("POST", "/fci/ClawCraneBingo/", {"requestId": "one"}), reward)
        self.assertEqual(self.item(61001), 2)
        self.assertIn("peripheralRecord", self.ok("GET", "/fci/ClawCraneRecord/"))

    def test_claw_insufficient_currency_returns_error_without_changing_state(self):
        self.set_item(61001, 0)
        reply = self.call("POST", "/fci/ClawCraneBingo/", {})
        self.assertIsNotNone(reply)
        self.assertEqual(reply[0], 409)
        self.assertEqual(self.item(61001), 0)

    def test_christmas_original_daily_and_weekly_tasks_are_listed(self):
        payload = self.game.tasks.snapshot(self.profile())
        rows = {r["id"]: r for r in payload["tasks"]}
        self.assertIn(115401, rows)
        self.assertIn(105401, rows)
        self.assertGreater(rows[105401]["endTime"], self.clock.now)

    def test_christmas_daily_condition_claim_and_day_reset(self):
        tasks, profile = self.game.tasks, self.profile()
        for _ in range(6):
            tasks.on_battle_win(profile, 3000101, mode="activity", activity_type=54, activity_id=54001,
                                stats={"statisticsPresent": True, "bigSkills": 0, "linkSkills": 1, "assistSkills": 1})
        rows = {r["id"]: r for r in tasks.snapshot(profile)["tasks"]}
        self.assertIn(115401, rows)
        self.assertTrue(rows[115401]["isDone"])
        self.assertFalse(rows[115402]["isDone"])
        reply = tasks.handle_http("POST", "/fci/task/115401/reward", None, profile)
        self.assertEqual(reply[0], 200)
        self.assertEqual(self.item(2011), 1)
        self.assertEqual(tasks.handle_http("POST", "/fci/task/115401/reward", None, profile)[0], 409)
        self.clock.now += 86400
        row = next(r for r in tasks.snapshot(profile)["tasks"] if r["id"] == 115401)
        self.assertEqual(row["progress"], 0)
        self.assertFalse(row["rewardGot"])

    def test_christmas_task_claim_rolls_back_receipt_on_grant_failure(self):
        tasks, profile = self.game.tasks, self.profile()
        for _ in range(6):
            tasks.on_battle_win(profile, 3000101, mode="activity", activity_type=54, activity_id=54001,
                                stats={"statisticsPresent": True, "bigSkills": 0})
        rows = {r["id"]: r for r in tasks.snapshot(profile)["tasks"]}
        self.assertIn(115401, rows)
        with patch("tasks.grant_rewards", side_effect=RuntimeError("injected task grant failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                tasks.handle_http("POST", "/fci/task/115401/reward", None, profile)
        self.assertFalse(next(r for r in tasks.snapshot(profile)["tasks"] if r["id"] == 115401)["rewardGot"])

    def test_christmas_weekly_progress_survives_day_but_resets_week(self):
        profile, tasks = self.profile(), self.game.tasks
        tasks.on_battle_win(profile, 3000101, mode="activity", activity_type=54, activity_id=54001)
        rows = {r["id"]: r for r in tasks.snapshot(profile)["tasks"]}
        self.assertIn(105401, rows)
        self.assertEqual(rows[105401]["progress"], 1)
        self.clock.now += 86400
        self.assertEqual(next(r for r in tasks.snapshot(profile)["tasks"] if r["id"] == 105401)["progress"], 1)
        self.clock.now += 7 * 86400
        self.assertEqual(next(r for r in tasks.snapshot(profile)["tasks"] if r["id"] == 105401)["progress"], 0)

    def test_schedule_time_weekday_and_level_are_enforced(self):
        for override in ({"timestampStart": self.clock.now + 10}, {"timestampEnd": self.clock.now},
                         {"timestampClose": self.clock.now}, {"weekdays": [7]}, {"levelLimit": 61}):
            with self.subTest(override=override), patch.object(self.game.events, "_activity_list", return_value=(200, [
                    {"type": 27, "activityId": 27001, **override}])):
                result = self.call("POST", "/fci/event-dungeon", {"activityType": 27, "activityId": 27001, "dungeonId": 2700101})
                self.assertEqual(result[0], 409)

    def test_clock_rollback_never_resets_daily_run_count(self):
        self.win(self.enter()["dungeonUid"])
        self.clock.now -= 86400
        record = next(r for r in self.ok("GET", "/fci/event-dungeon/record") if r["dungeonId"] == 2700101)
        self.assertEqual(record["doneTimes"], 1)

    def test_exhausted_claw_probability_does_not_move_to_another_prize(self):
        self.set_item(61001, 1)
        state = self.hidden._state(self.profile())
        state["claw"]["used"] = {"1": 1}
        self.hidden._save(self.profile(), state)
        with patch("hidden_event_dungeons.hashlib.sha256") as digest:
            digest.return_value.digest.return_value = bytes(32)  # original prize 1 interval
            result = self.ok("POST", "/fci/ClawCraneBingo", {"requestId": "exhausted"})
        self.assertEqual(result["id"], 0)
        self.assertEqual(self.item(75002), 0)
        self.assertEqual(self.item(61001), 0)

    def test_original_drop_cap_survives_spending_and_restart(self):
        state = self.game.systems._state(self.profile())
        state["items"]["2013"] = {"count": 0, "historyGetCount": 1060}
        self.game.systems._save(self.profile(), state)
        result = self.win(self.enter(54, 54001, 3000101)["dungeonUid"])
        self.assertEqual(self.item(2013), 0)
        self.assertFalse(any(r.get("baseRes", {}).get("id") == 2013 for r in result["resChange"]))

    def test_christmas_real_battle_statistics_and_native_task_bindings(self):
        for _ in range(6):
            uid = self.enter(54, 54001, 3000101)["dungeonUid"]
            self.fight("/fci/event-dungeon/", uid, kills=1)
            self.ok("POST", f"/fci/event-dungeon/{uid}/balance", {})
        tasks = self.game.tasks.handle_http("GET", "/fci/christmasDailyTasks", None, self.profile())
        self.assertIsNotNone(tasks)
        self.assertTrue(next(r for r in tasks[1] if r["id"] == 115401)["isDone"])

    def test_christmas_all_stages_goal_requires_every_listed_stage_per_progress(self):
        from tasks import TASKS
        tasks, profile = self.game.tasks, self.profile()
        stages = TASKS[115404]["taskParam"]
        for stage in stages[:-1]:
            tasks.on_battle_win(profile, stage, mode="activity", activity_type=54)
        def progress():
            return next(r for r in tasks.snapshot(profile)["tasks"] if r["id"] == 115404)["progress"]
        self.assertEqual(progress(), 0)
        tasks.on_battle_win(profile, stages[-1], mode="activity", activity_type=54)
        self.assertEqual(progress(), 1)
        for stage in stages:
            tasks.on_battle_win(profile, stage, mode="activity", activity_type=54)
        self.assertEqual(progress(), 2)

    def test_reward_wrong_http_method_cannot_claim(self):
        self.win(self.enter()["dungeonUid"])
        before = self.item(60002)
        for path, payload in (("/fci/event-dungeon/star-reward", {"activityId": 27001, "star": 3}),
                              ("/fci/activity/star-reward/27/27001", {"star": 3})):
            with self.subTest(path=path):
                self.assertEqual(self.call("DELETE", path, payload)[0], 405)
        self.assertEqual(self.item(60002), before)

    def test_failed_balance_does_not_push_uncommitted_task_progress(self):
        uid = self.enter(54, 54001, 3000101)["dungeonUid"]
        self.fight("/fci/event-dungeon/", uid, kills=1)
        pushed = []
        self.game.tasks.notifier = lambda *args: pushed.append(args)
        with patch.object(self.hidden, "_save", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self.call("POST", f"/fci/event-dungeon/{uid}/balance", {})
        self.assertEqual(pushed, [])

    def test_semifixed_cannot_put_main_actor_in_assist_slot(self):
        uid = self.own(1002)[0]
        members = [{"index": i, "isNpc": False, "actorUid": uid if i == 3 else 0} for i in range(6)]
        self.assertEqual(self.call("PUT", "/fci/semifixed-dungeon/actorgroup/2900101", {"groupInfo": {"members": members}})[0], 400)

    def test_native_game_dispatch_reaches_all_owned_routes(self):
        # This also catches the older ClawCrane and BuffShop compatibility
        # handlers accidentally shadowing the new native implementations.
        def request(method, path, data=None):
            body = json.dumps(data).encode() if data is not None else None
            result = self.game.handle_http(method, path, body, authorization=self.auth)
            self.assertIsNotNone(result, path)
            self.assertEqual(result[0], 200, result)
            return result[1]
        for kind, aid, did, prefix in ((27, 27001, 2700101, "event-dungeon"),
                                     (39, 39001, 2800101, "event-dungeon"),
                                     (40, 40001, 2800107, "event-dungeon"),
                                     (54, 54001, 3000101, "event-dungeon"),
                                     (51, 51002, 2900101, "semifixed-dungeon")):
            with self.subTest(kind=kind):
                entered = request("POST", f"/fci/{prefix}", {"activityType": kind, "activityId": aid, "dungeonId": did})
                uid = entered["dungeonUid"]
                request("PATCH", f"/fci/{prefix}/{uid}/active", {"winTeam": 1})
                balance = request("POST", f"/fci/{prefix}/{uid}/balance", {})
                self.assertTrue(balance["win"])
                self.assertEqual(request("POST", f"/fci/{prefix}/{uid}/balance", {}), balance)
                request("GET", f"/fci/activity/record/{kind}/{aid}")
        self.assertEqual({r["id"] for r in request("GET", "/fci/ClawCranePrize")["limitInfo"]}, {1, 2, 3})
        self.assertTrue(request("GET", "/fci/BuffShop/54")["shopInfo"])
