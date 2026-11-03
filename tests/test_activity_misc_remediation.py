"""Regression tests for activity date/error contracts found in the 2026-11-03 review."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
from game import LocalGame
from server.core.client_errors import client_error_reply
from server.core.activity_schedule import ActivitySchedule
import test_systems


ANCHOR = int(datetime(2026, 10, 26, 6, tzinfo=timezone(timedelta(hours=8))).timestamp())


class ActivityMiscRemediationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [ANCHOR + 3 * 86400 + 3600]
        self.accounts = LocalAccounts(self.root / "accounts", clock=lambda: self.now[0])
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def test_signin_and_cultivation_activity_dates_use_current_cycle(self):
        status, rows = self.game.events.handle_http("GET", "/fci/activity/", None, self.profile)
        self.assertEqual(status, 200)
        by_id = {int(row["activityId"]): row for row in rows}
        # 32001/53001/53002 must never expose Unix epoch timestamps.  Their
        # local compatibility window is the same 28-day welfare cycle.
        expected_start = ANCHOR
        expected_end = ANCHOR + 28 * 86400
        for activity_id in (32001, 53001, 53002):
            with self.subTest(activity_id=activity_id):
                self.assertIn(activity_id, by_id)
                self.assertEqual(by_id[activity_id]["timestampStart"], expected_start)
                self.assertEqual(by_id[activity_id]["timestampEnd"], expected_end)
                self.assertEqual(by_id[activity_id]["timestampClose"], expected_end)

    def test_guild_dungeon_rejects_legacy_scores_and_requires_membership(self):
        status, payload = self.game.handle_http(
            "POST", "/fci/guild/dungeon/settle", b"{}", "application/json", self.auth
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "invalid_request"})
        self.assertEqual(client_error_reply(status, payload), (509, {"notify": {"code": "Error_RequestFailed"}}))
        status, payload = self.game.handle_http(
            "POST", "/fci/guild-dungeon/", b'{"dungeonId":6101}', "application/json", self.auth
        )
        self.assertEqual((status, payload), (403, {"error": "guild_not_member"}))
        self.assertEqual(client_error_reply(status, payload), (509, {"notify": {"code": "Error_GuildHaveNoPermission"}}))

    def test_limited_gacha_without_pool_id_is_rejected(self):
        status, payload = self.game.handle_http(
            "POST", "/fci/gachatrade/", b'{"count":1}', "application/json", self.auth
        )
        self.assertEqual((status, payload), (400, {"error": "gacha_id_required"}))

    def test_continuous_login_phase_is_open_for_the_whole_cycle(self):
        source = Path(__file__).resolve().parents[1] / "data/server-only/activity-schedule.yaml"
        schedule = ActivitySchedule(source, clock=lambda: self.now[0])
        for day in (0, 7, 21, 27):
            self.now[0] = ANCHOR + day * 86400 + 3600
            active = {int(row.get("activityType", 0)) for row in schedule.snapshot()["active"]}
            self.assertIn(25, active)
        status, rows = self.game.events.handle_http("GET", "/fci/activity/", None, self.profile)
        self.assertEqual(status, 200)
        continuous = next(row for row in rows if int(row["type"]) == 25)
        self.assertEqual(continuous["timestampEnd"], 0)
        self.assertTrue(continuous["forever"])

    def test_diff_actor_inherit_closed_activity_is_not_open_notify(self):
        self.assertEqual(
            client_error_reply(409, {"error": "actor_inherit_activity_unavailable"}),
            (509, {"notify": {"code": "Error_ActivityNotStart"}}),
        )

    def test_theme_extra_active_point_resets_at_the_next_activity_cycle(self):
        first = self.game.tasks.record_extra_active_point(self.profile, 20, point_type=1)
        self.assertEqual(first["extraActivePoint"], 20)
        season = self.game.tasks._state(self.profile)["extraActivePoint"]["1"]["season"]
        self.now[0] = ANCHOR + 28 * 86400 + 3600
        second = self.game.tasks.extra_active_point_snapshot(self.profile, point_type=1)
        self.assertEqual(second["extraActivePoint"], 0)
        state = self.game.tasks._state(self.profile)
        self.assertGreater(state["extraActivePoint"]["1"]["season"], season)
        self.assertEqual(state["extraActivePoint"]["1"]["rewardGot"], [])

    def test_redpoint_bootstrap_contains_welfare_results(self):
        status, payload = self.game.handle_http("GET", "/fci/redpoint/data", None, "application/json", self.auth)
        self.assertEqual(status, 200)
        self.assertIn("sealResult", payload)
        self.assertIn("sealFlagResult", payload)
        self.assertIn("postcardResult", payload)
        self.assertTrue(payload["sealResult"]["newOpenFlag"])
        self.assertTrue(payload["sealFlagResult"]["newOpenFlag"])
        self.assertTrue(payload["postcardResult"]["isFirstEnter"])

if __name__ == "__main__":
    unittest.main()
