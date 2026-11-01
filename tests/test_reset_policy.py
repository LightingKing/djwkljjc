from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from accounts import LocalAccounts
from game import LocalGame
from server.core.clock import ServerClock
from server.core.reset import ResetPolicy
from server.core.reset_migration import migrate_reset_policy
from server.core.activity_schedule import ActivitySchedule
from mall import window_start
import test_systems


def ts(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


class ResetPolicyTests(unittest.TestCase):
    def test_local_day_week_and_month_boundaries_leave_epoch_unchanged(self):
        policy = ResetPolicy()
        before = ts("2026-11-01T21:59:59Z")
        after = before + 1
        self.assertEqual(policy.day_key(before), "2026-11-01")
        self.assertEqual(policy.day_key(after), "2026-11-02")
        self.assertEqual(policy.week_key(before), "2026-10-26")
        self.assertEqual(policy.week_key(after), "2026-11-02")
        self.assertEqual(policy.next_boundary(before, "week"), after)
        self.assertEqual(policy.period_start(after, "week"), after)
        month = ts("2026-10-31T22:00:00Z")
        self.assertEqual(policy.period_start(month - 1, "month"), ts("2026-09-30T22:00:00Z"))
        self.assertEqual(policy.next_boundary(month - 1, "month"), month)
        self.assertEqual(policy.day_key(ts("2026-10-30T15:59:59Z"), signin=True), "2026-10-30")
        self.assertEqual(policy.day_key(ts("2026-10-30T16:00:00Z"), signin=True), "2026-10-31")
        clock = ServerClock(source=lambda: before, offset_seconds=1, timezone_=policy.timezone)
        self.assertEqual(clock.now(), after)
        self.assertEqual(policy.parse_config_datetime("2026-11-2 06:00:00"), after)
        self.assertEqual(policy.parse_config_datetime("2026-11-01T22:00:00Z"), after)

    def test_schedule_override_migrates_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "schedule.json"
            state.write_text(json.dumps({"anchor": "2026-10-26T06:00:00Z", "activities": [
                {"id": "sign-in", "activityType": 46, "dayStart": 0, "dayEnd": 7}]}), encoding="utf-8")
            source = Path(__file__).resolve().parents[1] / "data/server-only/activity-schedule.yaml"
            first = ActivitySchedule(source, state_path=state)
            self.assertEqual(int(first.anchor.timestamp()), ts("2026-10-25T22:00:00Z"))
            self.assertEqual(first.phases[0]["dayEnd"], 28)
            saved = state.read_bytes()
            second = ActivitySchedule(source, state_path=state)
            self.assertEqual(second.anchor, first.anchor)
            self.assertEqual(state.read_bytes(), saved)


class ResetIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [ts("2026-10-30T21:59:59Z")]
        self.accounts = LocalAccounts(self.root, clock=lambda: self.now[0], unified_store_path="local.sqlite3")
        self.game = LocalGame(self.root, self.accounts, clock=lambda: self.now[0], unified_store_path="local.sqlite3")
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def test_all_direct_game_modules_share_the_login_policy(self):
        policy, now = self.game.reset_policy, self.now[0]
        self.assertEqual(self.game.tasks._day(now), policy.day_index(now))
        self.assertEqual(self.game.activity_modes._day(), policy.day_index(now))
        self.assertEqual(self.game.social._day(now), policy.day_index(now))
        self.assertEqual(self.game.realtime_pvp._day_key(), policy.day_key(now))
        self.assertEqual(self.game.events._date(), policy.day_key(now))
        self.assertEqual(self.game.economy._daily_key(), policy.day_key(now))
        self.assertEqual(window_start(1, now), policy.period_start(now))
        messages = dict(self.game.login_messages(self.account))
        begin = self.game._types["ScLoginBegin"]()
        begin.ParseFromString(messages["ScLoginBegin"])
        self.assertEqual(begin.utc_offset_seconds, 28800)
        self.assertEqual(begin.time_ms_server, now * 1000)

    def test_daily_and_weekly_tickets_are_idempotent_capped_and_rollback_safe(self):
        modes = self.game.activity_modes
        initial = modes.refresh_daily_items(self.profile)
        self.assertEqual({r["baseRes"]["id"] for r in initial}, {80001, 80002, 80003, 80005, 80006, 80009})
        self.assertEqual(modes.refresh_daily_items(self.profile), [])
        self.now[0] += 1
        rows = modes.refresh_daily_items(self.profile)
        self.assertEqual({r["baseRes"]["id"] for r in rows}, {80001, 80002})
        self.now[0] = ts("2026-11-01T22:00:00Z")
        modes.refresh_daily_items(self.profile)
        items = self.game.systems._state(self.profile)["items"]
        self.assertEqual({i: items[str(i)]["count"] for i in (80001, 80002, 80003, 80005, 80006)},
                         {80001: 3, 80002: 4, 80003: 4, 80005: 6, 80006: 5})
        self.assertEqual(items["80009"]["count"], 2)
        self.now[0] -= 14 * 86400
        self.assertEqual(modes.refresh_daily_items(self.profile), [])

    def test_80009_is_weekly_and_caps_at_ten(self):
        modes = self.game.activity_modes
        initial = modes.refresh_daily_items(self.profile)
        self.assertIn(80009, {row["baseRes"]["id"] for row in initial})
        self.assertEqual(self.game.systems._state(self.profile)["items"]["80009"]["count"], 1)

        # Crossing a Beijing day boundary alone must not replenish this
        # ticket; its cadence is the recovered weekly DailyRefresh row.
        self.now[0] += 86400
        self.assertNotIn(80009, {row["baseRes"]["id"] for row in modes.refresh_daily_items(self.profile)})
        self.assertEqual(self.game.systems._state(self.profile)["items"]["80009"]["count"], 1)

        # Nine further week boundaries reach the ten-ticket storage cap.
        for _ in range(9):
            self.now[0] += 7 * 86400
            modes.refresh_daily_items(self.profile)
        self.assertEqual(self.game.systems._state(self.profile)["items"]["80009"]["count"], 10)
        self.now[0] += 7 * 86400
        self.assertEqual(modes.refresh_daily_items(self.profile), [])
        self.assertEqual(self.game.systems._state(self.profile)["items"]["80009"]["count"], 10)

    def test_ticket_failure_rolls_back_inventory_and_marker_together(self):
        modes = self.game.activity_modes
        modes.refresh_daily_items(self.profile)
        before = self.game.systems._state(self.profile)
        marks = modes._state(self.profile)
        self.now[0] += 86400
        with closing(self.game._connect()) as connection, connection:
            connection.execute("CREATE TRIGGER fail_reset BEFORE UPDATE ON activity_mode_state "
                               "BEGIN SELECT RAISE(ABORT, 'injected reset failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            modes.refresh_daily_items(self.profile)
        self.assertEqual(self.game.systems._state(self.profile), before)
        self.assertEqual(modes._state(self.profile), marks)
        with closing(self.game._connect()) as connection, connection:
            connection.execute("DROP TRIGGER fail_reset")
        self.assertTrue(modes.refresh_daily_items(self.profile))
        self.assertEqual(modes.refresh_daily_items(self.profile), [])

    def test_migration_preserves_assets_claims_and_restarts(self):
        state = self.game.systems._state(self.profile)
        state["gachaPools"] = {"1": {"day": (self.now[0] - 21600) // 86400, "today": 3,
                                      "total": 99, "nextFree": self.now[0] + 86400}}
        self.game.systems._save(self.profile, state)
        legacy_day = (self.now[0] - 21600) // 86400
        task = self.game.tasks._state(self.profile)
        task.update(day=legacy_day, week=(legacy_day + 3) // 7, loginDay=legacy_day, activePoint=37)
        self.game.tasks._save(self.profile, task)
        self.now[0] += 1
        assets = self.game._profile(self.account)["resources"]
        with closing(self.game._connect()) as connection, connection:
            connection.execute("DELETE FROM local_reset_policy_migrations")
        migrate_reset_policy(self.game)
        converted = self.game.systems._state(self.profile)
        self.assertEqual(converted["actors"], state["actors"])
        self.assertEqual(self.game._profile(self.account)["resources"], assets)
        self.assertEqual(converted["gachaPools"]["1"]["today"], 3)
        self.assertEqual(converted["gachaPools"]["1"]["nextFree"], self.game.reset_policy.next_boundary(self.now[0]))
        self.assertEqual(self.game.tasks._state(self.profile)["day"], self.game.reset_policy.day_index(self.now[0]))
        migrate_reset_policy(self.game)
        self.assertEqual(self.game.systems._state(self.profile), converted)
        self.assertEqual(self.game.tasks._state(self.profile)["activePoint"], 37)
        restarted = LocalGame(self.root, self.accounts, clock=lambda: self.now[0], unified_store_path="local.sqlite3")
        self.assertEqual(restarted.systems._state(self.profile), converted)

    def test_free_draw_refreshes_at_six_not_twenty_four_hours_after_drawing(self):
        body = json.dumps({"id": 1, "gachaFree": True}).encode()
        result = self.game.handle_http("POST", "/fci/gachatrade/", body, "application/json", self.auth)
        self.assertEqual(result[0], 200)
        self.assertEqual(result[1].next_free_gacha_time, self.now[0] + 1)
        self.assertEqual(self.game.handle_http("POST", "/fci/gachatrade/", body, "application/json", self.auth)[0], 409)
        self.now[0] += 1
        self.assertEqual(self.game.handle_http("POST", "/fci/gachatrade/", body, "application/json", self.auth)[0], 200)

    def test_signin_midnight_does_not_reset_routine_tasks_or_repeat_on_rollback(self):
        self.now[0] = ts("2026-10-30T15:59:59Z")
        tasks = self.game.tasks._state(self.profile)
        self.game.tasks._sync(self.profile, tasks)
        tasks["activePoint"] = 37
        self.game.tasks._save(self.profile, tasks)
        _, first = self.game.events.handle_http("POST", "/fci/signin/", None, self.profile)
        self.assertEqual(first.days, 1)
        self.now[0] += 1
        _, second = self.game.events.handle_http("POST", "/fci/signin/", None, self.profile)
        self.assertEqual(second.days, 2)
        self.assertEqual(self.game.tasks.snapshot(self.profile)["activePoint"], 37)
        self.now[0] -= 1
        _, repeated = self.game.events.handle_http("POST", "/fci/signin/", None, self.profile)
        self.assertEqual(repeated.days, 2)
        self.assertFalse(repeated.res_change)
        self.now[0] = ts("2026-10-30T22:00:00Z")
        self.assertEqual(self.game.tasks.snapshot(self.profile)["activePoint"], 0)

    def test_signin_midnight_and_calendar_days_eight_to_ten(self):
        source = Path(__file__).resolve().parents[1] / "data/server-only/activity-schedule.yaml"
        self.game.activity_schedule = ActivitySchedule(source, clock=lambda: self.now[0])
        self.now[0] = ts("2026-11-02T00:00:00+08:00")
        for day in (8, 9, 10):
            status, value = self.game.events.handle_http("POST", "/fci/everyday-signin/46001/", b"{}", self.profile)
            self.assertEqual(status, 200)
            self.assertIn(day, list(value.signined_days))
            self.assertTrue(value.res_change)
            _, repeat = self.game.events.handle_http("POST", "/fci/everyday-signin/46001/", b"{}", self.profile)
            self.assertFalse(repeat.res_change)
            self.now[0] += 86400
        _, value = self.game.events.handle_http("POST", "/fci/everyday-signin/46001/", b"{}", self.profile)
        self.assertEqual(list(value.signined_days), [1])


if __name__ == "__main__":
    unittest.main()
