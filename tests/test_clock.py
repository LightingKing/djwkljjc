from __future__ import annotations

from datetime import datetime, timezone
import unittest

from server.core.clock import RecoveryResult, ServerClock, recover_resource


class ClockTests(unittest.TestCase):
    def test_injected_source_and_offset_are_deterministic(self):
        source = lambda: 1_700_000_000
        clock = ServerClock(source=source, offset_seconds=120)
        self.assertEqual(clock.now(), 1_700_000_120)
        self.assertEqual(clock.adjust(-20), 100)
        self.assertEqual(clock.now(), 1_700_000_100)
        clock.set_offset(0)
        self.assertEqual(clock.now(), 1_700_000_000)

    def test_daily_key_uses_configured_refresh_boundary(self):
        clock = ServerClock(source=lambda: 0)
        before = datetime(2026, 10, 28, 5, 59, 59, tzinfo=timezone.utc)
        after = datetime(2026, 10, 28, 6, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(clock.daily_key(before, refresh_at="06:00:00"), "2026-10-27")
        self.assertEqual(clock.daily_key(after, refresh_at="06:00:00"), "2026-10-28")
        self.assertEqual(clock.next_daily_boundary(before, refresh_at="06:00"), after)

    def test_weekly_key_and_boundary(self):
        clock = ServerClock(source=lambda: 0)
        sunday = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
        monday = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
        self.assertEqual(clock.weekly_key(sunday, refresh_at="06:00", week_start=0), "2026-10-26")
        self.assertEqual(clock.weekly_key(monday, refresh_at="06:00", week_start=0), "2026-11-02")
        self.assertEqual(clock.next_weekly_boundary(sunday, refresh_at="06:00", week_start=0), monday)

    def test_period_elapsed_counts_reset_buckets(self):
        clock = ServerClock(source=lambda: 0)
        start = datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)
        finish = datetime(2026, 10, 29, 5, 59, tzinfo=timezone.utc)
        self.assertEqual(clock.period_elapsed(start, finish, period="daily", refresh_at="06:00"), 1)
        self.assertEqual(clock.period_elapsed(start, finish, period="weekly", refresh_at="06:00"), 0)
        with self.assertRaises(ValueError):
            clock.period_elapsed(start, finish, period="monthly")

    def test_recovery_keeps_partial_interval_and_caps_at_capacity(self):
        result = recover_resource(
            balance=2,
            last_recovery_at=100,
            now=100 + 2 * 60 + 30,
            capacity=10,
            interval_seconds=60,
            amount_per_interval=1,
        )
        self.assertEqual(result, RecoveryResult(balance=4, recovered=2, intervals=2, last_recovery_at=220))
        capped = recover_resource(
            balance=9,
            last_recovery_at=100,
            now=1_000,
            capacity=10,
            interval_seconds=60,
        )
        self.assertEqual(capped.balance, 10)
        self.assertEqual(capped.recovered, 1)
        self.assertEqual(capped.last_recovery_at, 1_000)

    def test_recovery_rejects_invalid_timeline_or_values(self):
        with self.assertRaises(ValueError):
            recover_resource(balance=0, last_recovery_at=10, now=9, capacity=10, interval_seconds=60)
        with self.assertRaises(ValueError):
            recover_resource(balance=11, last_recovery_at=0, now=60, capacity=10, interval_seconds=60)
        with self.assertRaises(ValueError):
            recover_resource(balance=0, last_recovery_at=0, now=60, capacity=10, interval_seconds=0)


if __name__ == "__main__":
    unittest.main()
