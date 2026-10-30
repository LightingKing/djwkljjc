"""One-time, transactional conversion of legacy UTC reset markers.

Balances, inventories, characters and historical order timestamps are never
rewritten.  A current legacy period remains consumed in the current new
period; older markers retain the number of missed periods.  Each database
records the policy version in the same commit as its converted markers.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from .reset import ResetPolicy

VERSION = 1
LEGACY = ResetPolicy(utc_offset_seconds=0)
TABLES = ("local_tasks", "local_system_state", "activity_mode_state", "local_social_state",
          "local_realtime_pvp_state", "local_profile_features", "local_event_state")


def _integer(record, key, old, new):
    value = record.get(key)
    if value is None or value == "" or value == 0:
        return
    record[key] = int(value) + new - old


def _date(record, key, old, new):
    value = record.get(key)
    if not value:
        return
    try:
        stamp = date.fromisoformat(str(value))
    except ValueError:
        # The old standalone economy fallback used a string integer day key.
        if not str(value).lstrip("-").isdigit():
            raise ValueError("invalid persisted reset date: " + str(value))
        stamp = date(1970, 1, 1) + timedelta(days=int(value))
    record[key] = (stamp + (date.fromisoformat(new) - date.fromisoformat(old))).isoformat()


def _convert(table, state, now, policy):
    old_day, new_day = LEGACY.day_index(now), policy.day_index(now)
    old_week, new_week = LEGACY.week_index(now), policy.week_index(now)
    if table == "local_tasks":
        for key in ("day", "loginDay"):
            _integer(state, key, old_day, new_day)
        _integer(state, "week", old_week, new_week)
    elif table == "local_system_state":
        for pool in state.get("gachaPools", {}).values():
            _integer(pool, "day", old_day, new_day)
            if int(pool.get("nextFree", 0) or 0) > now:
                pool["nextFree"] = policy.next_boundary(now)
        _integer(state.get("favourGiftDaily", {}), "day", old_day, new_day)
    elif table == "activity_mode_state":
        marks = state.get("dailyRefresh", {})
        for key in marks:
            _integer(marks, key, old_day, new_day)
        for key in ("material", "tower", "deploy", "triggered"):
            _integer(state.get(key, {}), "day", old_day, new_day)
    elif table == "local_social_state":
        _integer(state.get("relationPoint", {}), "day", old_day, new_day)
    elif table == "local_realtime_pvp_state":
        old = datetime.fromtimestamp(now + 6 * 3600, timezone.utc).date().isoformat()
        for mode in ("race", "burst"):
            _date(state.get(mode, {}), "day", old, policy.day_key(now))
    elif table == "local_profile_features":
        _integer(state.get("share", {}), "weekKey", now // (7 * 86400), new_week)
    elif table == "local_event_state":
        for key in ("signin_date", "everyday_date"):
            _date(state, key, LEGACY.day_key(now), policy.day_key(now, signin=True))
        for key in ("continuous_date", "web_signin_date"):
            _date(state, key, LEGACY.day_key(now), policy.day_key(now))
        for entry in state.get("activity_signins", {}).values():
            _date(entry, "date", LEGACY.day_key(now), policy.day_key(now, signin=True))
        # Rebase the active 46 calendar by actual calendar dates.  Unlike the
        # old rotation, subsequent windows are always a full ten days.
        window = state.get("everyday_window")
        if window and ":" in str(window):
            activity, old_start = str(window).rsplit(":", 1)
            old_start = int(old_start)
            claimed_dates = set()
            delta = date.fromisoformat(policy.day_key(now, signin=True)) - date.fromisoformat(LEGACY.day_key(now))
            for day in state.get("everyday_signins", []):
                # A legacy 06:00 UTC calendar labelled this UTC date as its day.
                label = datetime.fromtimestamp(old_start + (int(day) - 1) * 86400, timezone.utc).date()
                claimed_dates.add((label + delta).isoformat())
            # The schedule is attached after LocalGame construction.  Defer
            # mapping calendar dates into its window until the first request.
            state["everyday_migrated_dates"] = sorted(claimed_dates)


def migrate_reset_policy(game) -> None:
    policy, now = game.reset_policy, game._now()
    signature = json.dumps(asdict(policy), sort_keys=True, separators=(",", ":"))
    paths = {Path(game.db_path).resolve(), Path(game.economy.db_path).resolve()}
    with game._lock:
        for path in sorted(paths):
            with closing(sqlite3.connect(str(path), timeout=10)) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute("CREATE TABLE IF NOT EXISTS local_reset_policy_migrations "
                                       "(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, policy_json TEXT NOT NULL)")
                    recorded = connection.execute("SELECT policy_json FROM local_reset_policy_migrations WHERE version = ?",
                                                  (VERSION,)).fetchone()
                    if recorded is not None:
                        if recorded[0] != signature:
                            raise ValueError("reset policy changed after migration; an explicit new migration is required")
                        connection.rollback()
                        continue
                    present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                    for table in TABLES:
                        if table not in present:
                            continue
                        for row in connection.execute(f"SELECT account_id, state_json FROM {table}").fetchall():
                            state = json.loads(row["state_json"])
                            if not isinstance(state, dict):
                                raise ValueError("invalid reset state in " + table)
                            _convert(table, state, now, policy)
                            connection.execute(f"UPDATE {table} SET state_json = ? WHERE account_id = ?",
                                               (json.dumps(state, ensure_ascii=False, separators=(",", ":")), row["account_id"]))
                    if "economy_profiles" in present:
                        from economy import next_shop_refresh
                        bases = {entry["shopId"]: entry["baseInfo"] for entry in game.economy._shop_catalog()["shops"]}
                        for row in connection.execute("SELECT account_id, shop_json, purchases_json FROM economy_profiles").fetchall():
                            shops, purchases = json.loads(row["shop_json"]), json.loads(row["purchases_json"])
                            for record in purchases.get("resBuy", {}).values():
                                _date(record, "dayKey", LEGACY.day_key(now), policy.day_key(now))
                            for shop in shops:
                                base = bases.get(int(shop.get("shopId", 0))) if isinstance(shop, dict) else None
                                if base is not None:
                                    previous = int(shop.get("nextRefreshTime", 0) or 0)
                                    # Preserve an already-due refresh for the normal
                                    # shop handler to settle, including its stock.
                                    if not previous or previous > now:
                                        shop["nextRefreshTime"] = next_shop_refresh(base, now, policy)
                            connection.execute("UPDATE economy_profiles SET shop_json = ?, purchases_json = ? WHERE account_id = ?",
                                               (json.dumps(shops, ensure_ascii=False, separators=(",", ":")),
                                                json.dumps(purchases, ensure_ascii=False, separators=(",", ":")), row["account_id"]))
                    connection.execute("INSERT INTO local_reset_policy_migrations VALUES (?, ?, ?)", (VERSION, now, signature))
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
