"""Permanent challenge-menu (挑战/活动) entries in ``GET fci/activity/``.

Window_Activity (window__activity__activity.lua) groups ActivityList records of
ActivityCategory_WeeklyActivity (1) / Tournament (3), keeps only types named in
the selected ActivitySort row (InitSort always selects row 1), shows a group
when ``ACU.IsActivitiesOn`` and orders unlocked groups (PlayerLevelInitConfig
unlock levels) before locked ones.  These tests replay that filter against the
local server reply.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from external_assets import external_path, require_file

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
from server.core.activity_schedule import ActivitySchedule  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_systems as _systems  # noqa: E402  (module import: its tests are not re-collected)

DAY = 86400
ANCHOR = int(datetime(2026, 10, 26, 6, tzinfo=timezone(timedelta(hours=8))).timestamp())  # a Monday
SHIPPED_SCHEDULE = ROOT / "data" / "server-only" / "activity-schedule.yaml"
LUA_DIR = external_path("LJJC_DECOMPILED_LUA", "analysis/lua-decompiled")

# xlsx ActivitySort row 1 (sortType 1), the order InitSort applies on open.
ACTIVITY_SORT_ROW1 = [39, 40, 31, 27, 2, 3, 17, 30, 10, 5, 20, 42, 18, 23]
# WU.ActivityTypeToUnlockWindowType + PlayerLevelInitConfig.unlockWindow levels.
UNLOCK_LEVEL = {2: 10, 3: 16, 17: 18, 30: 18, 10: 20, 31: 32, 5: 23, 18: 28, 20: 26, 23: 30}
# xlsx ActivityDungeon: base material activities and their multipleActivityId.
COLLECTION_IDS = {2001: 2101, 2002: 2102, 2003: 2103}
# Type 23 is the locally opened TeamDungeon activity (D3).  It is a
# permanent entry alongside the existing material, tower, arena and race
# activities, even though its level gate remains 30.
PERMANENT_TYPES = {2, 3, 5, 10, 17, 18, 20, 23, 30, 31}


def is_activity_on(activity: dict, now: int) -> bool:
    """ACU.IsActivityOn."""
    low = activity["timestampPreheat"] or activity["timestampStart"]
    high = activity["timestampClose"] or activity["timestampEnd"]
    return (low == 0 or low <= now) and (high == 0 or high >= now)


def is_open_for_doing(activity: dict, now: int, weekday: int) -> bool:
    """ACU.IsOpenForDoing (no dayOpenTime); weekday is Monday=1 .. Sunday=7."""
    end = activity["timestampEnd"]
    if not (now >= activity["timestampStart"] and (now < end or end == 0)):
        return False
    return not activity["weekdays"] or weekday in activity["weekdays"]


def menu_types(rows: list[dict], now: int, level: int) -> list[int]:
    """Window_Activity.OnActivityListChange + SortActivityList for sort row 1."""
    groups: dict[int, list[dict]] = {}
    for row in rows:
        if row["category"] in (1, 3):
            groups.setdefault(row["type"], []).append(row)
    unlocked, locked = [], []
    for activity_type in ACTIVITY_SORT_ROW1:
        group = groups.get(activity_type)
        if group and any(is_activity_on(row, now) for row in group):
            (unlocked if UNLOCK_LEVEL.get(activity_type, 0) <= level else locked).append(activity_type)
    return unlocked + locked


class PermanentActivityEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.now = [ANCHOR + 3 * DAY + 3600]  # Thursday 2026-10-29 07:00 Beijing
        clock = lambda: self.now[0]  # noqa: E731
        self.accounts = LocalAccounts(self.root / "accounts", clock=clock)
        self.game = LocalGame(self.root / "game", self.accounts, clock=clock)
        self.account, self.auth = _systems.LocalSystemsTests._guest(self.accounts)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def rows(self) -> list[dict]:
        status, rows = self.game.events.handle_http("GET", "/fci/activity/", None, self.game._profile(self.account))
        self.assertEqual(status, 200)
        self.assertIsInstance(rows, list)
        return rows

    def attach_schedule(self, document) -> None:
        source = self.root / "schedule.yaml"
        source.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
        self.game.activity_schedule = ActivitySchedule(source, clock=lambda: self.now[0])

    # -- default (no schedule service attached: shipped yaml section) -------
    def test_permanent_modes_are_listed_as_weekly_forever_entries(self):
        rows = self.rows()
        permanent = [row for row in rows if row["type"] in PERMANENT_TYPES]
        self.assertEqual({row["type"] for row in permanent}, PERMANENT_TYPES)
        by_id = {row["activityId"]: row for row in permanent}
        self.assertEqual(
            sorted(by_id), [2001, 2002, 2003, 3001, 5001, 10001, 17001, 18001, 20001, 23001, 30001, 31001],
        )
        self.assertEqual({row["activityId"] for row in rows if row["type"] == 53}, {53001, 53002})
        for row in permanent:
            expected_category = 3 if row["type"] in {5, 18} else 1
            self.assertEqual(row["category"], expected_category)
            self.assertEqual(row["timeType"], 1)  # ActivityTimeType_Forever
            self.assertTrue(row["forever"])
            self.assertEqual(row["timestampEnd"], 0)
            self.assertEqual(row["timestampClose"], 0)
            self.assertLess(row["timestampStart"], self.now[0])
            self.assertLessEqual(row["timestampPreheat"], row["timestampStart"])
            self.assertTrue(is_activity_on(row, self.now[0]))
        # Existing entries are unchanged.
        types = [row["type"] for row in rows]
        self.assertIn(12, types)
        self.assertIn(25, types)
        self.assertIn(46, types)

    def test_rows_carry_the_full_activities_field_set(self):
        rows = self.rows()
        login = next(row for row in rows if row["type"] == 25)
        for row in rows:
            if row["type"] in PERMANENT_TYPES:
                self.assertEqual(set(row), set(login))
                self.assertIsInstance(row["weekdays"], list)
                if row["type"] == 23:
                    self.assertEqual([(part["timestampStart"], part["timestampEnd"]) for part in row["dayOpenTime"]], [(21600, 86400)])
                else:
                    self.assertEqual(row["dayOpenTime"], [])

    def test_activity_ids_are_unique_through_the_full_dispatch(self):
        # ActivityManager keys m_mapActivityById by activityId alone.
        status, rows = self.game.handle_http("GET", "/fci/activity/", authorization=self.auth)
        self.assertEqual(status, 200)
        ids = [row["activityId"] for row in rows]
        self.assertEqual(len(ids), len(set(ids)), ids)
        self.assertTrue(PERMANENT_TYPES <= {row["type"] for row in rows})

    def test_collection_dungeon_weekdays_cover_every_day(self):
        collection = [row for row in self.rows() if row["type"] == 2]
        self.assertEqual({row["activityId"] for row in collection}, set(COLLECTION_IDS))
        covered: set[int] = set()
        for row in collection:
            self.assertTrue(row["weekdays"], row)  # empty weekdays lock every material slot
            self.assertTrue(set(row["weekdays"]) <= set(range(1, 8)))
            covered |= set(row["weekdays"])
        self.assertEqual(covered, set(range(1, 8)))
        # ActivityStageChoose.OnCurrentActivityChange indexes the first
        # IsOpenForDoing entry unconditionally: one must be open every day.
        for offset in range(7):
            moment = self.now[0] + offset * DAY
            weekday = datetime.fromtimestamp(moment, timezone(timedelta(hours=8))).isoweekday()
            self.assertTrue(any(is_open_for_doing(row, moment, weekday) for row in collection), weekday)
        self.assertTrue(all(is_open_for_doing(row, self.now[0], 7) for row in collection))

    def test_challenge_menu_lists_permanent_modes_with_collection_first_at_level_10(self):
        rows = self.rows()
        # Forced tutorial 14 (Lv10, UnlockWindow ActivityStageChoose2) clicks
        # the first Activity slot: CollectionDungeon is the only unlocked group.
        self.assertEqual(menu_types(rows, self.now[0], 10), [2, 31, 3, 17, 30, 10, 5, 20, 18, 23])
        self.assertEqual(menu_types(rows, self.now[0], 32), [31, 2, 3, 17, 30, 10, 5, 20, 18, 23])
        # Before this change the menu was empty: 12/15/25/46 are not sorted.
        legacy = [row for row in rows if row["type"] not in PERMANENT_TYPES]
        self.assertEqual(menu_types(legacy, self.now[0], 60), [])

    def test_entries_stay_open_across_days(self):
        for _ in range(9):
            self.now[0] += DAY
            self.assertEqual(menu_types(self.rows(), self.now[0], 60), [31, 2, 3, 17, 30, 10, 5, 20, 18, 23])

    # -- attached schedule document ----------------------------------------
    def test_shipped_schedule_document_lists_the_permanent_modes(self):
        self.game.activity_schedule = ActivitySchedule(SHIPPED_SCHEDULE, clock=lambda: self.now[0])
        rows = self.rows()
        self.assertTrue(PERMANENT_TYPES <= {row["type"] for row in rows})
        # The section does not disturb the rotating phases.
        phases = {phase["id"] for phase in self.game.activity_schedule.activities()}
        self.assertEqual(phases, {"sign-in", "shop", "sandbox-gacha", "diff-actor-inherit"})
        anchor = next(row for row in rows if row["type"] == 17)["timestampStart"]
        self.assertEqual(anchor, ANCHOR)

    def test_schedule_document_without_section_lists_no_permanent_modes(self):
        self.attach_schedule({"cycle": {
            "periodDays": 28, "anchor": "2026-10-26T06:00:00Z",
            "phases": [{"id": "shop", "dayStart": 0, "dayEnd": 28, "activityType": 12}],
        }})
        self.assertEqual({row["type"] for row in self.rows()}, {12, 25, 32, 46})

    def test_schedule_document_entries_are_validated(self):
        self.attach_schedule({
            "cycle": {"periodDays": 28, "anchor": "2026-10-26T06:00:00Z", "phases": [
                {"id": "gold-event", "dayStart": 0, "dayEnd": 28, "activityType": 17, "activityId": 17009},
            ]},
            "permanentActivities": {"defaults": {"category": 1, "timeType": 1}, "entries": [
                {"type": 2, "activityId": 2001, "weekdays": [7, 1, 9, 1, "x"]},
                {"type": 3, "enabled": False},
                {"type": 17, "activityId": 17001},  # a scheduled phase owns type 17
                {"activityId": 5},  # no type
                {"type": "bad"},
                {"type": 10},
            ],
        }})
        rows = self.rows()
        by_type: dict[int, list[dict]] = {}
        for row in rows:
            by_type.setdefault(row["type"], []).append(row)
        self.assertEqual(by_type[2][0]["weekdays"], [1, 7])
        self.assertNotIn(3, by_type)
        self.assertEqual([row["activityId"] for row in by_type[17]], [17009])
        self.assertEqual(by_type[10][0]["activityId"], 10001)  # type * 1000 + 1
        self.assertEqual(set(by_type), {2, 10, 17, 25, 32, 46})

    def test_schedule_document_changes_are_picked_up(self):
        self.attach_schedule({"cycle": {"periodDays": 28, "anchor": "2026-10-26T06:00:00Z", "phases": []},
                              "permanentActivities": {"entries": [{"type": 3}]}})
        self.assertIn(3, {row["type"] for row in self.rows()})
        source = self.root / "schedule.yaml"
        source.write_text(json.dumps({"cycle": {"periodDays": 28, "anchor": "2026-10-26T06:00:00Z", "phases": []},
                                      "permanentActivities": {"entries": [{"type": 3}, {"type": 17}]}}),
                          encoding="utf-8")
        future = datetime.now().timestamp() + 5
        os.utime(source, (future, future))
        self.assertEqual({row["type"] for row in self.rows()} & PERMANENT_TYPES, {3, 17})


class ClientEvidenceTests(unittest.TestCase):
    """Cross-check the constants above against the decompiled client when present."""

    def lua(self, name: str) -> str:
        path = LUA_DIR / name
        require_file(path, "LJJC_DECOMPILED_LUA")
        return path.read_text(encoding="utf-8")

    def test_activity_sort_row1_matches_client(self):
        source = self.lua("xlsx__activitysort.lua")
        block = source.split('ProtobufI("ActivitySort", {', 2)[1]
        self.assertIn("sortType = 1,", block)
        types = [int(value) for value in re.findall(r"\[\d+\] = (\d+)", block.split("})", 1)[0])]
        self.assertEqual(types, ACTIVITY_SORT_ROW1)
        every_row = {int(value) for value in re.findall(r"\[\d+\] = (\d+)", source.split("local indexed", 1)[0])}
        self.assertTrue(PERMANENT_TYPES <= every_row)
        self.assertNotIn(34, every_row)  # Maze can never be listed by the 2.0.4 window

    def test_collection_activity_ids_match_activity_dungeon(self):
        source = self.lua("xlsx__activitydungeon.lua")
        pairs = dict(
            (int(a), int(b)) for a, b in re.findall(r"activityId = (\d+),.*?multipleActivityId = (\d+)", source, re.S)
        )
        self.assertEqual({k: v for k, v in pairs.items() if k in COLLECTION_IDS}, COLLECTION_IDS)

    def test_unlock_levels_match_player_level_init_config(self):
        source = self.lua("xlsx__playerlevelinitconfig.lua")
        levels: dict[int, int] = {}
        for body in re.findall(r'ProtobufI\("PlayerLevelInitConfig", \{(.*?)\n  \}\)', source, re.S):
            level = int(re.search(r"level = (\d+)", body).group(1))
            unlock = re.search(r"unlockWindow = \{(.*?)\}", body, re.S)
            for value in re.findall(r"= (\d+)", unlock.group(1) if unlock else ""):
                if int(value):
                    levels[int(value)] = level
        # ActivityType -> UnlockWindow (WU.ActivityTypeToUnlockWindowType)
        window = {2: 7, 3: 9, 17: 18, 30: 16, 10: 13, 31: 32, 5: 10, 18: 26, 20: 27, 23: 31}
        self.assertEqual({t: levels.get(w, 0) for t, w in window.items()}, UNLOCK_LEVEL)


if __name__ == "__main__":
    unittest.main()
