from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from accounts import LocalAccounts
from economy import energy_policy
from events import LocalEvents
from game import LocalGame
from server.core.activity_schedule import ActivitySchedule
from server.core.clock import ServerClock


def _power(profile: dict) -> int:
    return next(item for item in profile["resources"] if item["type"] == 6)["count"]


def _ledger(game: LocalGame, account: dict) -> list[tuple]:
    with closing(game._connect()) as connection:
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT operation, resource_type, delta, balance_after, reason FROM local_resource_ledger "
                "WHERE account_id = ? ORDER BY rowid",
                (account["accountId"],),
            ).fetchall()
        ]


def _guest(accounts: LocalAccounts) -> dict:
    """Create the same local guest account shape used by the API tests."""

    first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    account_token = "AccountToken " + first[1]["accountToken"]
    entry = accounts.handle(
        "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", account_token
    )[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", account_token)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    auth_token = "AccountToken " + second[1]["accountToken"]
    value = entry.encode() if isinstance(entry, str) else entry
    request = b"\x0a" + bytes((len(value),)) + value + b"\x12\x05guest"
    result = accounts.handle("POST", "/Auth/", request, "application/wireformat", auth_token)
    return accounts.verify_access_token(result[1]["tokenAccess"])


class LongTermStabilityTests(unittest.TestCase):
    def test_passive_power_recovery_persists_and_writes_audit_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            now = [1_767_247_140]  # 2026-01-01 05:59 UTC
            clock = ServerClock(source=lambda: now[0])
            accounts = LocalAccounts(root / "state", clock=clock.now)
            game = LocalGame(root / "state", accounts, clock=clock.now)
            account = _guest(accounts)

            # Client rule (PlayerLevelInitConfig.incInfo): +1 power per 450 s
            # up to 58 + 2 * level, i.e. 60 at level 1.
            policy = energy_policy(1)
            self.assertEqual((policy["max"], policy["inc"], policy["incSeconds"]), (60, 1, 450))

            initial = game._profile(account)
            self.assertEqual(_power(initial), 120)
            ok, _changes, error = game.economy._change_resources(
                initial, [{"type": 6, "id": 0, "param": 0, "count": -10}]
            )
            self.assertTrue(ok, error)
            self.assertEqual(_power(game._profile(account)), 110)

            # A balance above the cap is kept (never clamped) and does not
            # recover, so no audit entry is written.
            now[0] += 2 * 450
            above_cap = game._profile(account)
            self.assertEqual(_power(above_cap), 110)
            self.assertEqual(_ledger(game, account), [])

            # Below the cap, two 450 s intervals recover two points after
            # reconnect and write exactly one audit entry.
            ok, _changes, error = game.economy._change_resources(
                above_cap, [{"type": 6, "id": 0, "param": 0, "count": -60}]
            )
            self.assertTrue(ok, error)
            self.assertEqual(_power(game._profile(account)), 50)
            now[0] += 2 * 450
            recovered = game._profile(account)
            self.assertEqual(_power(recovered), 52)
            self.assertEqual(_ledger(game, account), [("grant", 6, 2, 52, "passive_energy_recovery")])

            # Re-reading at the same timestamp must not duplicate the grant,
            # including after constructing a fresh game instance.
            again = LocalGame(root / "state", accounts, clock=clock.now)
            reread = again._profile(account)
            self.assertEqual(_power(reread), 52)
            with closing(again._connect()) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM local_resource_ledger WHERE account_id = ?",
                        (account["accountId"],),
                    ).fetchone()[0],
                    1,
                )

    def test_clock_rollback_reanchors_recovery_without_minting(self):
        """An admin clock adjustment backwards must not create free energy.

        M8.3 exercises the normal forward 28-day timeline.  This companion
        case covers the failure boundary that can occur when the local admin
        panel changes its clock offset: a profile read while time is behind
        the persisted marker must re-anchor the marker, then only recover
        elapsed intervals after the new anchor.
        """
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            start = int(datetime(2026, 10, 26, 6, tzinfo=timezone.utc).timestamp())
            now = [start]
            clock = ServerClock(source=lambda: now[0])
            accounts = LocalAccounts(root / "state", clock=clock.now)
            game = LocalGame(root / "state", accounts, clock=clock.now)
            account = _guest(accounts)

            interval = energy_policy(1)["incSeconds"]  # 450 s, level-1 cap 60
            profile = game._profile(account)
            # Drop below the level-1 cap so passive recovery is active (a
            # balance above the cap never recovers).
            ok, _changes, error = game.economy._change_resources(
                profile, [{"type": 6, "id": 0, "param": 0, "count": -70}]
            )
            self.assertTrue(ok, error)
            self.assertEqual(_power(game._profile(account)), 50)

            # Establish a persisted recovery marker, then move the injected
            # server clock backwards as the admin panel is allowed to do.
            now[0] += 4 * interval
            self.assertEqual(_power(game._profile(account)), 54)
            now[0] -= 3 * interval
            rolled_back = game._profile(account)
            self.assertEqual(_power(rolled_back), 54)
            self.assertEqual(_ledger(game, account), [("grant", 6, 4, 54, "passive_energy_recovery")])

            # One interval after the rollback re-anchor yields exactly one
            # point: without the re-anchor the clock would still be behind the
            # old marker (54), and replaying the old window would give more.
            now[0] += interval
            recovered = game._profile(account)
            self.assertEqual(_power(recovered), 55)
            self.assertEqual(
                _ledger(game, account),
                [
                    ("grant", 6, 4, 54, "passive_energy_recovery"),
                    ("grant", 6, 1, 55, "passive_energy_recovery"),
                ],
            )

    def test_twenty_eight_day_restart_loop_covers_daily_weekly_and_month_card_state(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            start = int(datetime(2026, 10, 26, 6, tzinfo=timezone.utc).timestamp())
            now = [start]
            clock = ServerClock(source=lambda: now[0])
            accounts = LocalAccounts(root / "state", clock=clock.now)
            game = LocalGame(root / "state", accounts, clock=clock.now)
            game.daily_key = lambda value=None: clock.daily_key(value, refresh_at="06:00:00")
            account = _guest(accounts)

            # Establish the recovery marker and a non-full balance before the
            # simulated restarts begin.
            profile = game._profile(account)
            ok, _changes, error = game.economy._change_resources(
                profile, [{"type": 6, "id": 0, "param": 0, "count": -14}]
            )
            self.assertTrue(ok, error)
            self.assertEqual(_power(game._profile(account)), 106)
            sign_in_days = []
            sign_in_power = 0
            # Include both endpoints: 29 sign-ins exercise 28 daily reset
            # boundaries and four weekly buckets, which is the full activity
            # cycle used by the local schedule.
            for day in range(29):
                restarted = LocalGame(root / "state", accounts, clock=clock.now)
                restarted.daily_key = lambda value=None, _clock=clock: _clock.daily_key(value, refresh_at="06:00:00")
                active_profile = restarted._profile(account)
                events = LocalEvents(restarted, clock=clock.now)
                status, signin = events.handle_http("POST", "/fci/signin", None, active_profile)
                self.assertEqual(status, 200)
                sign_in_days.append(int(signin.days))
                sign_in_power += sum(
                    change.base_res.count_delta
                    for change in signin.res_change
                    if change.HasField("base_res") and change.base_res.type == 6
                )
                if day < 28:
                    now[0] += 86400

            self.assertEqual(sign_in_days, list(range(1, 30)))
            final = game._profile(account)
            # The balance stays above the level-1 cap (60) throughout, so it
            # never passively recovers, and every sign-in power reward is kept
            # on top of it rather than cut off at a cap.  (Daily sign-in drops
            # are seeded per account id, so the reward total varies per run.)
            self.assertEqual(_power(final), 106 + sign_in_power)
            self.assertGreater(_power(final), energy_policy(1)["max"])
            self.assertEqual(
                [row for row in _ledger(game, account) if row[4] == "passive_energy_recovery"], []
            )
            self.assertEqual(clock.period_elapsed(start, now[0], period="weekly", refresh_at="06:00"), 4)

    def test_activity_rotation_is_deterministic_across_cycle_boundaries(self):
        source = Path(__file__).resolve().parents[1] / "data" / "server-only" / "activity-schedule.yaml"
        schedule = ActivitySchedule(source, clock=lambda: 0)
        anchor = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        at_start = schedule.snapshot(anchor)
        at_week = schedule.snapshot(anchor.timestamp() + 7 * 86400)
        at_cycle = schedule.snapshot(anchor.timestamp() + 28 * 86400)
        self.assertEqual(at_start["cycleDay"], 0)
        self.assertEqual({item["id"] for item in at_start["active"]}, {"sign-in", "shop"})
        self.assertEqual({item["id"] for item in at_week["active"]}, {"sign-in", "shop", "sandbox-gacha"})
        self.assertEqual({item["id"] for item in at_cycle["active"]}, {"sign-in", "shop"})


if __name__ == "__main__":
    unittest.main()
