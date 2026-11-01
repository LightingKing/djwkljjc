"""One-time, transactional conversion of legacy UTC reset markers.

Balances, inventories, characters and historical order timestamps are never
rewritten. Period labels are converted at their original boundary, never at
the server restart time. Legacy labels without a claim timestamp may allow
one extra reset; they must not consume a later Beijing day. Already-migrated
databases are not converted a second time.
"""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from .reset import ResetPolicy

VERSION = 1
LEGACY = ResetPolicy(utc_offset_seconds=0)
TABLES = ("local_tasks", "local_system_state", "activity_mode_state", "local_social_state",
          "local_realtime_pvp_state", "local_profile_features", "local_event_state")
POLICY_FIELDS = ("utc_offset_seconds", "daily_refresh", "signin_refresh", "week_start")


def _integer(record, key, policy, *, weekly=False, epoch_week=False):
    value = record.get(key)
    if value is None or value == "" or value == 0:
        return
    if epoch_week:
        stamp = int(value) * 7 * 86400
    elif weekly:
        stamp = LEGACY.day_start_from_index(int(value) * 7 - 3)
    else:
        stamp = LEGACY.day_start_from_index(int(value))
    record[key] = policy.week_index(stamp) if weekly or epoch_week else policy.day_index(stamp)


def _date(record, key, policy, *, signin=False, legacy_offset=21600, claimed_at=None):
    value = record.get(key)
    if not value:
        return
    if claimed_at:
        record[key] = policy.day_key(int(claimed_at), signin=signin)
        return
    try:
        stamp = date.fromisoformat(str(value))
    except ValueError:
        # The old standalone economy fallback used a string integer day key.
        if not str(value).lstrip("-").isdigit():
            raise ValueError("invalid persisted reset date: " + str(value))
        stamp = date(1970, 1, 1) + timedelta(days=int(value))
    epoch = int(datetime.combine(stamp, datetime.min.time(), timezone.utc).timestamp()) + legacy_offset
    record[key] = policy.day_key(epoch, signin=signin)


def _convert(table, state, now, policy):
    if table == "local_tasks":
        for key in ("day", "loginDay"):
            _integer(state, key, policy)
        _integer(state, "week", policy, weekly=True)
    elif table == "local_system_state":
        from systems import GACHA_TYPES
        for pool_id, pool in state.get("gachaPools", {}).items():
            _integer(pool, "day", policy)
            next_free = int(pool.get("nextFree", 0) or 0)
            interval = int(GACHA_TYPES.get(int(pool_id), {}).get("gachaFreeInterval", 0) or 0)
            if next_free > now and interval > 0:
                # Legacy free draws used draw_time + interval. Recover that
                # timestamp so a restart after 06:00 does not postpone a due draw.
                pool["nextFree"] = policy.next_boundary(next_free - interval)
        _integer(state.get("favourGiftDaily", {}), "day", policy)
    elif table == "activity_mode_state":
        marks = state.get("dailyRefresh", {})
        for key in marks:
            _integer(marks, key, policy)
        marks = state.get("weeklyRefresh", {})
        for key in marks:
            _integer(marks, key, policy, weekly=True)
        for key in ("material", "tower", "deploy", "triggered"):
            _integer(state.get(key, {}), "day", policy)
    elif table == "local_social_state":
        _integer(state.get("relationPoint", {}), "day", policy)
    elif table == "local_realtime_pvp_state":
        for mode in ("race", "burst"):
            # The old key was date(now + 6h), so its labelled day began at
            # 18:00 UTC on the preceding calendar date.
            _date(state.get(mode, {}), "day", policy, legacy_offset=-21600)
    elif table == "local_profile_features":
        _integer(state.get("share", {}), "weekKey", policy, epoch_week=True)
    elif table == "local_event_state":
        for key in ("signin_date", "everyday_date"):
            _date(state, key, policy, signin=True)
        for key in ("continuous_date", "web_signin_date"):
            _date(state, key, policy)
        for entry in state.get("activity_signins", {}).values():
            _date(entry, "date", policy, signin=True)
        # Rebase the active 46 calendar by actual calendar dates.  Unlike the
        # old rotation, subsequent windows are always a full ten days.
        window = state.get("everyday_window")
        if window and ":" in str(window):
            activity, old_start = str(window).rsplit(":", 1)
            old_start = int(old_start)
            claimed_dates = set()
            for day in state.get("everyday_signins", []):
                boundary = old_start + (int(day) - 1) * 86400
                claimed_dates.add(policy.day_key(boundary, signin=True))
            # The schedule is attached after LocalGame construction.  Defer
            # mapping calendar dates into its window until the first request.
            state["everyday_migrated_dates"] = sorted(claimed_dates)


def migrate_reset_policy(game) -> None:
    policy, now = game.reset_policy, game._now()
    signature_fields = {field: getattr(policy, field) for field in POLICY_FIELDS}
    signature = json.dumps(signature_fields, sort_keys=True, separators=(",", ":"))
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
                        old_signature = json.loads(recorded[0])
                        if {field: old_signature.get(field) for field in POLICY_FIELDS} != signature_fields:
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
                                _date(record, "dayKey", policy, claimed_at=record.get("startTime"))
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
