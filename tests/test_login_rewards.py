"""Login rewards paid from the client tables (ContinuousLoginPrize, SigninGift,
DropDisplayDailySignin, ActivitySigninGift) and the always-on Welfare
activities that expose them."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from google.protobuf import json_format  # noqa: E402

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
from server.core.activity_schedule import ActivitySchedule  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_systems as _systems  # noqa: E402  (module import: its tests are not re-collected)

DAY = 86400
GOLD = 7  # ResPlayerGold
ANCHOR = int(datetime(2026, 10, 26, 6, tzinfo=timezone(timedelta(hours=8))).timestamp())


def _changes(message) -> list[dict]:
    return json_format.MessageToDict(message).get("resChange", [])


class LoginRewardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.now = [ANCHOR + 2 * DAY + 3600]
        clock = lambda: self.now[0]  # noqa: E731
        self.accounts = LocalAccounts(root / "accounts", clock=clock)
        self.game = LocalGame(root / "game", self.accounts, clock=clock)
        self.account, _auth = _systems.LocalSystemsTests._guest(self.accounts)
        self.root = root

    def tearDown(self) -> None:
        self._temp.cleanup()

    def profile(self):
        return self.game._profile(self.account)

    def gold(self) -> int:
        return next((int(item["count"]) for item in self.profile()["resources"] if item["type"] == GOLD), 0)

    def call(self, method: str, path: str, body=None):
        return self.game.events.handle_http(method, path, body, self.profile())

    def next_day(self) -> None:
        self.now[0] += DAY

    # -- ContinuousLogin (10-day newcomer gift) ---------------------------
    def test_continuous_login_summary_and_day_one_prize(self):
        status, summary = self.call("GET", "/fci/ContinuousLogin/")
        self.assertEqual(status, 200)
        self.assertEqual(summary.DESCRIPTOR.full_name, "ApiContinuousLogin.result")
        self.assertEqual((summary.cur_continuous_login, summary.max_continuous_login), (1, 1))
        self.assertEqual(list(summary.receive_prize_record), [])

        before = self.gold()
        status, claim = self.call("POST", "/fci/ContinuousLogin/ReceivePrize/", b'{"days":1}')
        self.assertEqual(status, 200)
        self.assertEqual(claim.DESCRIPTOR.full_name, "ApiContinuousLoginReceive.result")
        self.assertEqual(self.gold() - before, 6000)
        self.assertEqual(_changes(claim)[0]["baseRes"]["countDelta"], 6000)

        _, summary = self.call("GET", "/fci/ContinuousLogin/")
        self.assertEqual(list(summary.receive_prize_record), [1])
        status, repeat = self.call("POST", "/fci/ContinuousLogin/ReceivePrize/", b'{"days":1}')
        self.assertEqual((status, repeat["error"]), (409, "already_claimed"))
        self.assertEqual(self.gold() - before, 6000)

    def test_unreached_continuous_login_day_is_refused(self):
        before = self.gold()
        status, refused = self.call("POST", "/fci/ContinuousLogin/ReceivePrize/", b'{"days":2}')
        self.assertEqual((status, refused["error"]), (409, "days_not_reached"))
        status, invalid = self.call("POST", "/fci/ContinuousLogin/ReceivePrize/", b'{"days":11}')
        self.assertEqual((status, invalid["error"]), (400, "invalid_days"))
        self.assertEqual(self.gold(), before)

    def test_day_five_prize_creates_asuna(self):
        self.assertNotIn(1011, self.game.local_actor_ids(self.profile()))
        for _ in range(4):
            self.call("GET", "/fci/ContinuousLogin/")
            self.next_day()
        _, summary = self.call("GET", "/fci/ContinuousLogin/")
        self.assertEqual((summary.cur_continuous_login, summary.max_continuous_login), (5, 5))

        status, claim = self.call("POST", "/fci/ContinuousLogin/ReceivePrize/", b'{"days":5}')
        self.assertEqual(status, 200)
        actors = [change["actor"] for change in _changes(claim) if "actor" in change]
        self.assertEqual([actor["id"] for actor in actors], [1011])
        self.assertIn(1011, self.game.local_actor_ids(self.profile()))

    def test_missed_day_restarts_streak_but_keeps_reached_prizes(self):
        for _ in range(3):
            self.call("GET", "/fci/ContinuousLogin/")
            self.next_day()
        self.next_day()  # skip a day
        _, summary = self.call("GET", "/fci/ContinuousLogin/")
        self.assertEqual((summary.cur_continuous_login, summary.max_continuous_login), (1, 3))
        _, prompt = self.call("GET", "/fci/ContinuousLogin/InterruptPrompt")
        self.assertTrue(prompt.is_interrupt)
        _, prompt = self.call("GET", "/fci/ContinuousLogin/InterruptPrompt")
        self.assertFalse(prompt.is_interrupt)
        status, _claim = self.call("POST", "/fci/ContinuousLogin/ReceivePrize/", b'{"days":3}')
        self.assertEqual(status, 200)

    def test_redpoint_fields_carry_continuous_login_result(self):
        fields = self.game.events.redpoint_fields(self.profile())
        self.assertEqual(
            fields["continuousLoginResult"],
            {"curContinuousLogin": 1, "maxContinuousLogin": 1, "receivePrizeRecord": []},
        )
        self.assertEqual(fields["signinResult"]["days"], 0)

    # -- Daily sign-in and SigninGift milestones --------------------------
    def test_daily_signin_pays_one_drop_display_row(self):
        from events import DAILY_SIGNIN_DROPS

        status, signin = self.call("POST", "/fci/signin/")
        self.assertEqual(status, 200)
        self.assertTrue(signin.signed)
        changes = _changes(signin)
        self.assertEqual(len(changes), 1)
        paid = changes[0]["baseRes"]
        rows = {(row["type"], row["id"], row["count"]) for _, row in DAILY_SIGNIN_DROPS}
        self.assertIn((paid["type"], paid.get("id", 0), paid["countDelta"]), rows)
        _, repeat = self.call("POST", "/fci/signin/")
        self.assertEqual((repeat.days, len(repeat.res_change)), (1, 0))

    def test_seven_day_signin_gift_pays_100000_coins(self):
        for day in range(7):
            if day:
                self.next_day()
            _, signin = self.call("POST", "/fci/signin/")
        self.assertEqual(signin.days, 7)
        status, early = self.call("POST", "/fci/signin/gift/", b'{"days":14}')
        self.assertEqual((status, early["error"]), (409, "days_not_reached"))
        status, invalid = self.call("POST", "/fci/signin/gift/", b'{"days":5}')
        self.assertEqual((status, invalid["error"]), (400, "invalid_days"))

        before = self.gold()
        status, gift = self.call("POST", "/fci/signin/gift/", b'{"days":7}')
        self.assertEqual(status, 200)
        self.assertEqual(gift.DESCRIPTOR.full_name, "ApiSigninGift.result")
        self.assertEqual(self.gold() - before, 100000)
        _, state = self.call("GET", "/fci/signin/")
        self.assertEqual(list(state.gift_got), [7])
        status, repeat = self.call("POST", "/fci/signin/gift/", b'{"days":7}')
        self.assertEqual((status, repeat["error"]), (409, "already_claimed"))

    # -- Welfare activity list --------------------------------------------
    def test_activity_list_includes_always_on_login_activities(self):
        status, rows = self.call("GET", "/fci/activity/")
        self.assertEqual(status, 200)
        by_type = {row["type"]: row for row in rows}
        self.assertIn(25, by_type)  # ActivityType_ContinuousLogin
        self.assertIn(46, by_type)  # ActivityType_EveryDayCheckin
        self.assertEqual(by_type[25]["activityId"], 25001)
        self.assertEqual(by_type[25]["timestampEnd"], 0)
        self.assertLessEqual(by_type[25]["timestampStart"], self.now[0])
        self.assertEqual(by_type[46]["activityId"], 46001)
        self.assertLessEqual(by_type[46]["timestampStart"], self.now[0])
        self.assertLess(self.now[0], by_type[46]["timestampEnd"])

    def test_everyday_calendar_without_schedule_keeps_its_window(self):
        _, rows = self.call("GET", "/fci/activity/")
        start = next(row for row in rows if row["type"] == 46)["timestampStart"]
        today = (self.now[0] - start) // DAY + 1
        self.assertLess(today, 10)  # the next day stays in the same 10-day window
        status, first = self.call("POST", "/fci/everyday-signin/46001/", b"{}")
        self.assertEqual((status, list(first.signined_days)), (200, [today]))
        self.assertEqual(len(first.res_change), 1)
        _, repeat = self.call("POST", "/fci/everyday-signin/46001/", b"{}")
        self.assertEqual(len(repeat.res_change), 0)
        self.next_day()
        _, second = self.call("POST", "/fci/everyday-signin/46001/", b"{}")
        self.assertEqual(list(second.signined_days), [today, today + 1])

    def test_schedule_without_login_phases_still_lists_them_and_pays_calendar(self):
        source = self.root / "schedule.yaml"
        source.write_text(json.dumps({"cycle": {
            "periodDays": 28, "anchor": "2026-10-26T06:00:00+08:00",
            "phases": [{"id": "shop", "dayStart": 0, "dayEnd": 28, "activityType": 12}],
        }}), encoding="utf-8")
        self.game.activity_schedule = ActivitySchedule(source, clock=lambda: self.now[0])
        self.now[0] = ANCHOR + 22 * DAY + 3600  # cycle day 22 -> calendar window [20, 30)
        status, rows = self.call("GET", "/fci/activity/")
        self.assertEqual(status, 200)
        by_type = {row["type"]: row for row in rows}
        self.assertEqual(set(by_type), {12, 25, 32, 46})
        self.assertEqual(by_type[46]["timestampStart"], ANCHOR - 6 * 3600 + 20 * DAY)
        self.assertEqual(by_type[46]["timestampEnd"], ANCHOR - 6 * 3600 + 30 * DAY - 1)

        # Day 3 of the window pays ActivitySigninGift 46001 day 3 (5x item 10003).
        status, claim = self.call("POST", "/fci/everyday-signin/46001/", b"{}")
        self.assertEqual(status, 200)
        self.assertEqual(list(claim.signined_days), [3])
        paid = _changes(claim)[0]["baseRes"]
        self.assertEqual((paid["type"], paid["id"], paid["countDelta"]), (11, 10003, 5))
        _, state = self.call("GET", "/fci/everyday-signin/46001/")
        self.assertEqual(list(state.signined_days), [3])


if __name__ == "__main__":
    unittest.main()
