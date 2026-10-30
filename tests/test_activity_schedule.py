from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from server.core.activity_schedule import ActivitySchedule


class ActivityScheduleTests(unittest.TestCase):
    def test_rotation_and_admin_anchor_are_deterministic(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "schedule.yaml"
            source.write_text(
                "cycle:\n  periodDays: 4\n  anchor: '2026-01-01T00:00:00Z'\n  phases:\n    - id: a\n      dayStart: 0\n      dayEnd: 2\n    - id: b\n      dayStart: 2\n      dayEnd: 4\n",
                encoding="utf-8",
            )
            state = root / "schedule.json"
            now = [1767225600]  # 2026-01-01 UTC
            schedule = ActivitySchedule(source, state_path=state, clock=lambda: now[0])
            self.assertEqual(schedule.snapshot()["active"][0]["id"], "a")
            now[0] += 2 * 86400
            self.assertEqual(schedule.snapshot()["active"][0]["id"], "b")
            schedule.set_anchor("2026-01-03T00:00:00Z")
            self.assertEqual(schedule.snapshot()["cycleDay"], 0)
            reloaded = ActivitySchedule(source, state_path=state, clock=lambda: now[0])
            self.assertEqual(reloaded.anchor.isoformat(), "2026-01-03T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
