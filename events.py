"""Persistent local activity and sign-in compatibility surface.

This module implements a loopback-only test economy.  It accepts JSON and
descriptor-backed protobuf requests, stores claim state in SQLite, and returns
the real response message types used by the client.
"""

from __future__ import annotations

from server.core.reset import policy_for, period_due

import json
import random
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

from google.protobuf import json_format, message_factory
from google.protobuf.message import Message

try:  # package import (server.py) or flat PYTHONPATH (tests/tools)
    from .grants import grant_rewards
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from grants import grant_rewards

try:  # PyYAML is a runtime dependency of the activity schedule as well
    import yaml
except ImportError:  # pragma: no cover - minimal environments
    yaml = None

_CLIENT_TABLE_DIR = Path(__file__).resolve().parent / "data" / "client-tables"

ACTIVITY_TYPE_CONTINUOUS_LOGIN = 25  # ActivityType_ContinuousLogin
ACTIVITY_TYPE_TEN_CHECKIN = 32  # ActivityType_TenCheckin
ACTIVITY_TYPE_EVERYDAY_CHECKIN = 46  # ActivityType_EveryDayCheckin
# Welfare2 shows a tab only for an open activity; these local login rewards
# stay open even when the activity schedule has no phase for them.
ALWAYS_ON_ACTIVITY_TYPES = (ACTIVITY_TYPE_CONTINUOUS_LOGIN, ACTIVITY_TYPE_EVERYDAY_CHECKIN, ACTIVITY_TYPE_TEN_CHECKIN)
# Activity ids the client keys on: WlfEveryDayCheckin only renders 46001/46003
# and ActivitySigninGift has rows for 32001/46001/46003.
DEFAULT_ACTIVITY_IDS = {
    ACTIVITY_TYPE_CONTINUOUS_LOGIN: 25001,
    ACTIVITY_TYPE_TEN_CHECKIN: 32001,
    ACTIVITY_TYPE_EVERYDAY_CHECKIN: 46001,
}

# Permanent challenge-menu modes (gold/material dungeon, tower, deploy, ...).
# Window_Activity lists only ActivityCategory_WeeklyActivity (1) / Tournament
# (3) records whose type is in an ActivitySort row, so these are served as
# always-open fci/activity/ records described by the ``permanentActivities``
# section of the schedule document (data/server-only/activity-schedule.yaml).
ACTIVITY_SCHEDULE_SOURCE = Path(__file__).resolve().parent / "data" / "server-only" / "activity-schedule.yaml"
ACTIVITY_CATEGORY_WEEKLY = 1  # ActivityCategory_WeeklyActivity
ACTIVITY_TIME_TYPE_FOREVER = 1  # ActivityTimeType_Forever ("Window_OpenForever")
_PERMANENT_CACHE: dict[str, tuple[tuple[int, int], list[dict[str, Any]]]] = {}


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _permanent_entries(source: Optional[Path]) -> list[dict[str, Any]]:
    """Validated ``permanentActivities.entries`` of a schedule document.

    A document without the section (or a missing file) lists none.  Entries
    need a positive ``type``; ``activityId`` defaults to ``type * 1000 + 1``
    (the client's 25001/46001/2001 numbering), ``category``/``timeType`` to the
    section ``defaults`` (WeeklyActivity / Forever).  ``weekdays`` are client
    weekdays (Monday=1 .. Sunday=7, ACU.IsOpenForDoing) and are sorted and
    de-duplicated; ``enabled: false`` hides an entry.  The parsed document is
    cached per file and reloaded when its mtime/size changes.
    """

    if source is None or yaml is None:
        return []
    try:
        stat = source.stat()
    except OSError:
        return []
    key = str(source)
    stamp = (int(stat.st_mtime_ns), int(stat.st_size))
    cached = _PERMANENT_CACHE.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        document = None
    section = document.get("permanentActivities") if isinstance(document, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    defaults = section.get("defaults") if isinstance(section.get("defaults"), Mapping) else {}
    raw_entries = section.get("entries") if isinstance(section.get("entries"), list) else []
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping) or not bool(raw.get("enabled", True)):
            continue
        activity_type = _int_or(raw.get("type"), 0)
        if activity_type <= 0:
            continue
        weekdays = raw.get("weekdays") if isinstance(raw.get("weekdays"), list) else []
        entries.append({
            "type": activity_type,
            "activityId": _int_or(raw.get("activityId"), 0) or activity_type * 1000 + 1,
            "category": _int_or(raw.get("category", defaults.get("category")), ACTIVITY_CATEGORY_WEEKLY),
            "timeType": _int_or(raw.get("timeType", defaults.get("timeType")), ACTIVITY_TIME_TYPE_FOREVER),
            "levelLimit": max(0, _int_or(raw.get("levelLimit", 0), 0)),
            "weekdays": sorted({day for day in (_int_or(value, 0) for value in weekdays) if 1 <= day <= 7}),
        })
    _PERMANENT_CACHE[key] = (stamp, entries)
    return entries


def _client_table_rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_CLIENT_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


def _table_resource(value: Any) -> dict[str, int]:
    value = value if isinstance(value, Mapping) else {}
    return {key: int(value.get(key, 0) or 0) for key in ("type", "id", "count", "param")}


def _days_table(rows: list[dict[str, Any]], field: str) -> dict[int, list[dict[str, int]]]:
    table: dict[int, list[dict[str, int]]] = {}
    for row in rows:
        try:
            day = int(row.get("days", 0) or 0)
        except (TypeError, ValueError):
            continue
        rewards = [_table_resource(item) for item in row.get(field) or [] if isinstance(item, Mapping)]
        if day > 0 and rewards:
            table[day] = rewards
    return table


def _activity_days_table(rows: list[dict[str, Any]]) -> dict[int, dict[int, list[dict[str, int]]]]:
    tables: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            tables.setdefault(int(row.get("activityId", 0) or 0), []).append(row)
        except (TypeError, ValueError):
            continue
    return {activity_id: _days_table(items, "reward") for activity_id, items in tables.items() if activity_id}


# Client tables exported by tools/export_client_tables.py.
CONTINUOUS_LOGIN_PRIZES = _days_table(_client_table_rows("ContinuousLoginPrize"), "prize")  # 10-day newcomer gift
CONTINUOUS_LOGIN_MAX_DAY = max(CONTINUOUS_LOGIN_PRIZES) if CONTINUOUS_LOGIN_PRIZES else 10
SIGNIN_GIFTS = _days_table(_client_table_rows("SigninGift"), "reward")  # cumulative 3/7/14/21
ACTIVITY_SIGNIN_GIFTS = _activity_days_table(_client_table_rows("ActivitySigninGift"))
DAILY_SIGNIN_DROPS: list[tuple[int, dict[str, int]]] = [
    (int(row.get("probability", 0) or 0), _table_resource(row))
    for row in _client_table_rows("DropDisplayDailySignin")
    if int(row.get("probability", 0) or 0) > 0 and int(row.get("count", 0) or 0) > 0
]


def _message_to_dict(message: Any) -> dict[str, Any]:
    try:
        return json_format.MessageToDict(message, always_print_fields_with_no_presence=True)
    except TypeError:  # older protobuf
        return json_format.MessageToDict(message, including_default_value_fields=True)


class LocalEvents:
    """Activity/sign-in state owned by a :class:`LocalGame` instance."""

    # Type 7 is the starter gold resource in local-defaults.json.  This is the
    # explicit local reward for generic activity claims; sign-in and login
    # rewards come from the client tables loaded above.
    ACTIVITY_REWARD = {"type": 7, "id": 0, "count": 50, "param": 0}

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_event_state (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _now(self) -> int:
        return int(self._clock())

    def _date(self, *, signin: bool = False) -> str:
        return self._date_at(self._now(), signin=signin)

    def _date_at(self, timestamp: int, *, signin: bool = False) -> str:
        return policy_for(self.game).day_key(timestamp, signin=signin)

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM local_event_state WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                state: dict[str, Any] = {
                    "signin_days": 0,
                    "signin_date": "",
                    "signin_gifts": [],
                    "activity_signins": {},
                    "everyday_signins": [],
                    "everyday_date": "",
                    "continuous_days": 0,
                    "continuous_date": "",
                    "continuous_rewards": [],
                    # Web签到 is a separate activity from the ordinary
                    # account签到 endpoints.  The recovered vote.proto
                    # descriptor is malformed and is intentionally not
                    # loaded by the server, so this state is exposed through
                    # the bounded JSON compatibility seam below.
                    "web_signin_days": 0,
                    "web_signin_date": "",
                    "web_signin_global_rewards": [],
                    "activity_claims": [],
                    "activity_spin_count": 0,
                    "activity_explore_count": 0,
                    "activity_treasure_count": 0,
                    "activity_dispatch_count": 0,
                    "activity_rebate_claimed": False,
                    "gacha_count": 0,
                    "gacha_score": 0,
                    "gacha_shop_claims": [],
                    "deploy_missions": [],
                    "achievement_claims": [],
                    "gift_codes": [],
                    "condition_reward_got": False,
                    "subscribed": False,
                }
                # Another local game object may initialize the same account at
                # the same time.  INSERT OR IGNORE makes initialization
                # idempotent; reload the winning row below so the caller never
                # returns an uncommitted shadow state.
                connection.execute(
                    "INSERT OR IGNORE INTO local_event_state(account_id,state_json,updated_at) VALUES (?,?,?)",
                    (account_id, json.dumps(state, separators=(",", ":")), self._now()),
                )
                row = connection.execute(
                    "SELECT state_json FROM local_event_state WHERE account_id = ?", (account_id,)
                ).fetchone()
                if row is None:
                    return state
            try:
                state = json.loads(row["state_json"] or "{}")
            except (TypeError, ValueError):
                state = {}
            if not isinstance(state, dict):
                state = {}
            defaults = {
                "signin_days": 0,
                "signin_date": "",
                "signin_gifts": [],
                "activity_signins": {},
                "everyday_signins": [],
                "everyday_date": "",
                "continuous_days": 0,
                "continuous_date": "",
                "continuous_rewards": [],
                "web_signin_days": 0,
                "web_signin_date": "",
                "web_signin_global_rewards": [],
                "activity_claims": [],
                "activity_spin_count": 0,
                "activity_explore_count": 0,
                "activity_treasure_count": 0,
                "activity_dispatch_count": 0,
                "activity_rebate_claimed": False,
                "gacha_count": 0,
                "gacha_score": 0,
                "gacha_shop_claims": [],
                "deploy_missions": [],
                "achievement_claims": [],
                "gift_codes": [],
                "condition_reward_got": False,
                "subscribed": False,
            }
            for key, value in defaults.items():
                state.setdefault(key, value)
            return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_event_state SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    @staticmethod
    def _body_object(body: Any) -> dict[str, Any]:
        if body is None:
            return {}
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    def _request(self, body: Any, type_name: str) -> Message | None:
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return None
        try:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(type_name))
            message = cls()
            message.ParseFromString(raw)
            return message
        except Exception:
            return None

    def _resource(self, value: Mapping[str, Any], *, delta: Optional[int] = None):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("STResource"))
        result = cls()
        result.type = int(value.get("type", 0))
        result.id = int(value.get("id", 0))
        result.count = int(value.get("count", 0))
        result.param = int(value.get("param", 0))
        if delta is not None:
            result.count_delta = int(delta)
        return result

    def _dynamic(self, value: Mapping[str, Any], *, delta: int):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("STResourceDynamic"))
        result = cls()
        result.base_res.CopyFrom(self._resource(value, delta=delta))
        return result

    def _grant(self, profile: Mapping[str, Any], reward: Mapping[str, Any]):
        """Grant an event reward through the canonical economy mutation seam.

        The old implementation edited ``resources_json`` directly.  That made
        event rewards diverge from shop/养成 mutations and could overwrite a
        newer balance when a caller held a stale login snapshot.  Economy owns
        the read/modify/write operation, so the same profile lock and atomic
        validation are used for every local reward.
        """
        economy = getattr(self.game, "economy", None)
        if economy is None:
            return None
        ok, changes, _error = economy._change_resources(profile, [dict(reward)])
        if not ok or not changes:
            return None
        change = changes[0]
        return self._dynamic(change, delta=int(change.get("countDelta", 0)))

    def _requested_days(self, body: Any, type_name: str) -> int:
        """``days`` from a JSON body or the descriptor-backed request message."""
        request = self._body_object(body)
        value = request.get("days", request.get("day"))
        if value in (None, ""):
            message = self._request(body, type_name)
            value = getattr(message, "days", 0) if message is not None else 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _res_changes(self, entries: Any) -> list[Message]:
        """Convert :func:`grant_rewards` resChange dicts to ``STResourceDynamic``."""
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("STResourceDynamic"))
        messages: list[Message] = []
        for entry in entries or []:
            if not isinstance(entry, Mapping):
                continue
            message = cls()
            try:
                json_format.ParseDict(dict(entry), message, ignore_unknown_fields=True)
            except json_format.ParseError:
                base = entry.get("baseRes")
                if not isinstance(base, Mapping):
                    continue
                message = self._dynamic(base, delta=int(base.get("countDelta", 0) or 0))
            messages.append(message)
        return messages

    def _pay(self, profile: Mapping[str, Any], rewards: Any) -> list[Message]:
        """Pay client-table STResource rows through the shared grant seam."""
        rewards = [dict(reward) for reward in rewards or [] if isinstance(reward, Mapping)]
        if not rewards:
            return []
        return self._res_changes(grant_rewards(self.game, profile, rewards))

    @staticmethod
    def _daily_signin_drop(profile: Mapping[str, Any], day_key: str) -> list[dict[str, int]]:
        """One weighted DropDisplayDailySignin row, stable per account and day."""
        total = sum(weight for weight, _ in DAILY_SIGNIN_DROPS)
        if total <= 0:
            return []
        seed = "ljjc-daily-signin:%d:%s" % (int(profile["account_id"]), day_key)
        roll = random.Random(seed).randrange(total)
        for weight, reward in DAILY_SIGNIN_DROPS:
            if roll < weight:
                return [dict(reward)]
            roll -= weight
        return [dict(DAILY_SIGNIN_DROPS[-1][1])]

    def _signin_message(self, state: Mapping[str, Any], today: str) -> Message:
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiSignin.result"))
        result = cls()
        result.signed = not period_due(state.get("signin_date"), today)
        result.days = int(state.get("signin_days", 0))
        result.gift_got.extend(int(x) for x in state.get("signin_gifts", []))
        return result

    def _signin(self, method: str, body: Any, profile: Mapping[str, Any]):
        """``fci/signin/`` (WlfCheckin): one DropDisplayDailySignin draw per day."""
        if method not in {"GET", "POST"}:
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
        state = self._state(profile)
        today = self._date(signin=True)
        changes: list[Message] = []
        if method == "POST" and period_due(state.get("signin_date"), today):
            state["signin_days"] = max(0, int(state.get("signin_days", 0))) + 1
            state["signin_date"] = today
            changes = self._pay(profile, self._daily_signin_drop(profile, today))
            self._save(profile, state)
        result = self._signin_message(state, today)
        result.res_change.extend(changes)
        return 200, result

    def _signin_gift(self, method: str, body: Any, profile: Mapping[str, Any]):
        """``fci/signin/gift/ {days}``: a SigninGift cumulative milestone (3/7/14/21)."""
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        day = self._requested_days(body, "ApiSigninGift")
        rewards = SIGNIN_GIFTS.get(day)
        if not rewards:
            return 400, {"error": "invalid_days", "message": "days must be a SigninGift milestone", "days": day}
        state = self._state(profile)
        if day > int(state.get("signin_days", 0)):
            return 409, {"error": "days_not_reached", "days": day, "signinDays": int(state.get("signin_days", 0))}
        gifts = {int(x) for x in state.get("signin_gifts", [])}
        if day in gifts:
            return 409, {"error": "already_claimed", "days": day}
        changes = self._pay(profile, rewards)
        gifts.add(day)
        if set(SIGNIN_GIFTS) <= gifts:
            # WlfCheckin.OnBtnGiftClick resets its view to days=0/giftGot={}
            # after the last milestone; start the next cumulative round too.
            state["signin_days"] = 0
            state["signin_gifts"] = []
        else:
            state["signin_gifts"] = sorted(gifts)
        self._save(profile, state)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiSigninGift.result"))
        result = cls()
        result.res_change.extend(changes)
        return 200, result

    def _activity_signin(self, method: str, path: str, profile: Mapping[str, Any]):
        with self.game.atomic_mutation():
            return self._activity_signin_atomic(method, path, profile)

    def _activity_signin_atomic(self, method: str, path: str, profile: Mapping[str, Any]):
        """``fci/activity-signin[/<id>]`` (WlfTenCheckin): ActivitySigninGift by day.

        ``day`` is the row the client may claim today (``index == day and not
        signed``), so an unsigned account reports the next unclaimed day.
        """
        if method not in {"GET", "POST"}:
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
        parts = path.rstrip("/").split("/")
        activity_id = int(parts[-1]) if parts and parts[-1].isdigit() else DEFAULT_ACTIVITY_IDS[ACTIVITY_TYPE_TEN_CHECKIN]
        rows = ACTIVITY_SIGNIN_GIFTS.get(activity_id, {})
        if activity_id != 32001 or not rows:
            return 404, {"error": "activity_signin_not_open"}
        last_day = max(rows) if rows else 0
        state = self._state(profile)
        records = dict(state.get("activity_signins", {}))
        record = dict(records.get(str(activity_id), {"day": 0, "date": ""}))
        today = self._date(signin=True)
        claimed = int(record.get("day", 0))
        signed_today = not period_due(record.get("date"), today)
        changes: list[Message] = []
        if method == "POST" and not signed_today and (not last_day or claimed < last_day):
            claimed += 1
            records[str(activity_id)] = {"day": claimed, "date": today}
            state["activity_signins"] = records
            changes = self._pay(profile, rows.get(claimed, []))
            self._save(profile, state)
            signed_today = True
        finished = bool(last_day) and claimed >= last_day
        if method == "GET":
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiActivitySignin.get"))
            result = cls()
            result.day = claimed if (signed_today or finished) else claimed + 1
            result.activity_id = activity_id
            result.signed = bool(signed_today or finished)
            return 200, result
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiActivitySignin.post"))
        result = cls()
        result.res_change.extend(changes)
        return 200, result

    def _current_activity(self, activity_type: int) -> Message | None:
        status, payload = self._activity("GET", "/fci/activity/?type=%d" % int(activity_type))
        if status == 200 and isinstance(payload, Message) and payload.HasField("result"):
            if int(payload.result.type) == int(activity_type):
                return payload.result
        return None

    def _everyday_signin(self, method: str, path: str, profile: Mapping[str, Any]):
        """``fci/everyday-signin/<id>/`` (WlfEveryDayCheckin).

        The claimable row is the activity day ``(now - timestampStart) / 1 day
        + 1`` exactly as the client computes ``nowDay``; rewards come from the
        ActivitySigninGift rows of the open EveryDayCheckin activity.
        """
        if method not in {"GET", "POST"}:
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
        activity = self._current_activity(ACTIVITY_TYPE_EVERYDAY_CHECKIN)
        if activity is None:
            return 409, {"error": "activity_not_open"}
        activity_id = int(activity.activity_id)
        suffix = path.rstrip("/").rsplit("/", 1)[-1]
        if suffix.isdigit() and int(suffix) != activity_id:
            return 409, {"error": "activity_not_open", "activityId": int(suffix)}
        start = int(activity.timestamp_start)
        now = self._now()
        policy = policy_for(self.game)
        now_day = policy.day_index(now, signin=True) - policy.day_index(start, signin=True) + 1 if now >= start else 0
        state = self._state(profile)
        dirty = False
        window = "%d:%d" % (activity_id, start)
        if "everyday_migrated_dates" in state:
            mapped = []
            for value in state.pop("everyday_migrated_dates"):
                stamp = policy.parse_config_datetime(value)
                number = policy.day_index(stamp, signin=True) - policy.day_index(start, signin=True) + 1
                if 1 <= number <= len(ACTIVITY_SIGNIN_GIFTS.get(activity_id, {})):
                    mapped.append(number)
            state["everyday_window"] = window
            state["everyday_signins"] = sorted(set(mapped))
            dirty = True
        old_start = _int_or(str(state.get("everyday_window") or "").rsplit(":", 1)[-1], 0)
        rollback = old_start > start
        if not rollback and state.get("everyday_window") != window:
            state["everyday_window"] = window
            state["everyday_signins"] = []
            dirty = True
        days = sorted({int(x) for x in state.get("everyday_signins", [])})
        rows = ACTIVITY_SIGNIN_GIFTS.get(activity_id, {})
        changes: list[Message] = []
        if method == "POST" and not rollback and now_day in rows and now_day not in days:
            changes = self._pay(profile, rows[now_day])
            days = sorted(set(days) | {now_day})
            state["everyday_signins"] = days
            state["everyday_date"] = self._date(signin=True)
            dirty = True
        if dirty:
            self._save(profile, state)
        if method == "GET":
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiEveryDaySignin.get"))
            result = cls(); result.signined_days.extend(days)
            return 200, result
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiEveryDaySignin.post"))
        result = cls(); result.signined_days.extend(days)
        result.res_change.extend(changes)
        return 200, result

    def _continuous_tick(self, state: dict[str, Any]) -> bool:
        """Advance the consecutive-login streak once per daily bucket.

        ``continuous_days`` is the current streak (curContinuousLogin) and
        ``continuous_max`` the best streak reached (maxContinuousLogin): the
        client lets every ``index <= maxContinuousLogin`` be claimed.  Missing
        a day restarts the streak and arms the one-shot InterruptPrompt.
        """
        today = self._date()
        last = str(state.get("continuous_date") or "")
        if not period_due(last, today):
            return False
        streak = max(0, int(state.get("continuous_days", 0) or 0))
        best = max(int(state.get("continuous_max", 0) or 0), streak)
        if last and streak > 0 and last == self._date_at(self._now() - 86400):
            streak += 1
        else:
            if last and streak > 0 and best < CONTINUOUS_LOGIN_MAX_DAY:
                state["continuous_interrupt"] = True
            streak = 1
        state["continuous_days"] = streak
        state["continuous_max"] = max(best, streak)
        state["continuous_date"] = today
        return True

    def _continuous_message(self, state: Mapping[str, Any]) -> Message:
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiContinuousLogin.result"))
        cap = CONTINUOUS_LOGIN_MAX_DAY
        streak = int(state.get("continuous_days", 0) or 0)
        best = max(int(state.get("continuous_max", 0) or 0), streak)
        result = cls(cur_continuous_login=min(cap, streak), max_continuous_login=min(cap, best))
        result.receive_prize_record.extend(sorted(int(x) for x in state.get("continuous_rewards", [])))
        return result

    def _continuous(self, method: str, path: str, body: Any, profile: Mapping[str, Any]):
        """``fci/ContinuousLogin`` (WlfViewNoviceGift): the 10-day ContinuousLoginPrize."""
        state = self._state(profile)
        if self._continuous_tick(state):
            self._save(profile, state)
        lower = path.rstrip("/").lower()
        if lower.endswith("interruptprompt"):
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            interrupted = bool(state.get("continuous_interrupt", False))
            if interrupted:
                state["continuous_interrupt"] = False
                self._save(profile, state)
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiContinuousLoginPrompt.result"))
            return 200, cls(is_interrupt=interrupted)
        if lower.endswith("receiveprize"):
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            day = self._requested_days(body, "ApiContinuousLoginReceive")
            rewards = CONTINUOUS_LOGIN_PRIZES.get(day)
            if not rewards:
                return 400, {"error": "invalid_days", "message": "days must be a ContinuousLoginPrize day", "days": day}
            reached = int(self._continuous_message(state).max_continuous_login)
            if day > reached:
                return 409, {"error": "days_not_reached", "days": day, "maxContinuousLogin": reached}
            claimed = {int(x) for x in state.get("continuous_rewards", [])}
            if day in claimed:
                return 409, {"error": "already_claimed", "days": day}
            changes = self._pay(profile, rewards)
            state["continuous_rewards"] = sorted(claimed | {day})
            self._save(profile, state)
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiContinuousLoginReceive.result"))
            result = cls()
            result.res_change.extend(changes)
            return 200, result
        if method != "GET":
            return 405, {"error": "method_not_allowed", "message": "GET required"}
        return 200, self._continuous_message(state)

    def redpoint_fields(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Login-reward members of ``GET fci/redpoint/data`` (ApiRedPointData.result).

        city.lua copies ``continuousLoginResult`` into ``fci/ContinuousLogin/``
        (the Welfare ViewNoviceGift tab needs it) and RestifizerManager copies
        ``signinResult`` into ``fci/signin``.  Reading it counts today's login.
        """
        with self._lock:
            state = self._state(profile)
            if self._continuous_tick(state):
                self._save(profile, state)
            presents = self.game.profile_features.pending_role_presents(profile)
            _status, activity_signin = self._activity_signin("GET", "/fci/activity-signin/32001", profile)
            return {
                "continuousLoginResult": _message_to_dict(self._continuous_message(state)),
                "signinResult": _message_to_dict(self._signin_message(state, self._date(signin=True))),
                "activitySigninGet": _message_to_dict(activity_signin),
                "rolePresentRedpoint": {"presents": presents},
            }

    def _web_signin(self, method: str, body: Any, profile: Mapping[str, Any]):
        """Run the retired web-sign-in panel as a deterministic local activity.

        ``vote.proto`` contains the original ``ApiWebSignin`` messages, but
        its malformed syntax marker means the descriptor is deliberately
        excluded from the runtime pool (see :func:`game._message_pool`).  The
        client therefore receives a schema-shaped JSON response.  Rewards are
        explicitly local sandbox values and are persisted per account; the
        global seven-day milestone is granted at most once.
        """
        if method not in {"GET", "POST"}:
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
        state = self._state(profile)
        today = self._date()
        rewards: list[dict[str, int]] = []
        signed = not period_due(state.get("web_signin_date"), today)
        days = max(0, int(state.get("web_signin_days", 0)))
        if method == "POST" and not signed:
            days += 1
            state["web_signin_days"] = days
            state["web_signin_date"] = today
            reward = self._grant(profile, {"type": 7, "id": 0, "count": 50, "param": 0})
            if reward is not None:
                rewards.append({"type": 7, "id": 0, "count": 50})
            # This is a local compatibility milestone rather than a claimed
            # official event value.  Record it before saving so a retry cannot
            # mint the reward again.
            milestones = {int(value) for value in state.get("web_signin_global_rewards", [])}
            if days >= 7 and 7 not in milestones:
                milestone = self._grant(profile, {"type": 7, "id": 0, "count": 200, "param": 0})
                if milestone is not None:
                    rewards.append({"type": 7, "id": 0, "count": 200})
                milestones.add(7)
                state["web_signin_global_rewards"] = sorted(milestones)
            self._save(profile, state)
            signed = True

        # The response shape mirrors ApiWebSignin.get/post field names in
        # camelCase.  Keep the target catalog stable so a UI can render the
        # panel even though the original server's event table is unavailable.
        target_finished = 7 in {int(value) for value in state.get("web_signin_global_rewards", [])}
        target = {
            "signInCount": 7,
            "finished": target_finished,
            "rewards": [{"type": 7, "id": 0, "count": 200}],
        }
        if method == "GET":
            return 200, {
                "sandbox": True,
                "signInRewards": [{"type": 7, "id": 0, "count": 50}],
                "globalSignInCount": days,
                "globalTargetRewards": [target],
                "isSignIn": signed,
            }
        return 200, {
            "sandbox": True,
            "result": bool(rewards),
            "rewards": rewards,
            "globalSignInCount": days,
            "globalTargetRewards": [target],
        }

    def _subscribe(self, method: str, body: Any, profile: Mapping[str, Any]):
        """Handle the local ``Subscribe`` preference without contacting SDKs.

        The retired client sends ``ApiSubscribe.op_type`` to toggle platform
        subscription prompts.  The original platform callback is unavailable
        offline, so the preference is persisted per account and exposed using
        the recovered response descriptor.  Local semantics are deliberately
        small and deterministic: 0 queries, 1 subscribes, and 2 unsubscribes.
        """
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        request = self._body_object(body)
        message = self._request(body, "ApiSubscribe")
        raw_op = request.get("opType", request.get("op_type"))
        if raw_op is None and message is not None:
            raw_op = getattr(message, "op_type", 0)
        try:
            op_type = int(raw_op or 0)
        except (TypeError, ValueError):
            return 400, {"error": "invalid_op_type", "message": "opType must be an integer"}

        state = self._state(profile)
        if op_type == 1:
            state["subscribed"] = True
        elif op_type == 2:
            state["subscribed"] = False
        elif op_type != 0:
            return 400, {"error": "invalid_op_type", "message": "opType must be 0, 1, or 2"}
        if op_type in {1, 2}:
            self._save(profile, state)

        result = self._message("ApiSubscribe.result")
        if result is None:
            return None
        result.errcode = 0
        result.is_subscribed = bool(state.get("subscribed", False))
        return 200, result

    def _message(self, full_name: str):
        """Construct an optional descriptor-backed response message."""
        try:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(full_name))
        except Exception:
            return None
        return cls()

    def _claim_activity_reward(self, profile: Mapping[str, Any], state: dict[str, Any], key: str,
                               reward: Mapping[str, Any] | None = None):
        """Grant a local activity reward once and return its dynamic delta."""
        claims = {str(value) for value in state.get("activity_claims", [])}
        if key in claims:
            return None
        claims.add(key)
        state["activity_claims"] = sorted(claims)
        return self._grant(profile, reward or self.ACTIVITY_REWARD)

    def _spend_gold(self, profile: Mapping[str, Any], amount: int = 1000):
        """Spend local starter gold through the shared economy balance code."""
        economy = getattr(self.game, "economy", None)
        if economy is None:
            return False, [], "economy_unavailable"
        return economy._change_resources(
            profile,
            [{"type": 7, "id": 0, "param": 0, "count": -abs(int(amount))}],
        )

    def _activity_claim_api(self, method: str, path: str, body: Any, profile: Mapping[str, Any]):
        """Handle the descriptor-backed activity mini-games in local mode.

        The retired service exposed many small endpoints.  Their original
        catalog is unavailable, so each local endpoint is deterministic and
        uses the real response descriptor when one is present.  Rewards are
        local gold and every claim is persisted in ``local_event_state``.
        """
        action = re.sub(r"[^a-z0-9]", "", (urlsplit(path or "").path or "").lower())
        request = self._body_object(body)
        state = self._state(profile)
        now = self._now()

        # Purple-pairs / puzzle activity.
        if "purblepairscreate" in action or "purplepairscreate" in action:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            result = self._message("ApiActivityPurblePairsCreate.result")
            if result is None:
                return None
            result.data.extend([1, 2, 3, 4])
            result.can_reward = True
            return 200, result
        if "purblepairsbalance" in action or "purplepairsbalance" in action:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            reward = self._claim_activity_reward(profile, state, "purple-pairs")
            self._save(profile, state)
            result = self._message("ApiActivityPurblePairsBalance.result")
            if result is None:
                return None
            if reward is not None:
                result.res_change.add().CopyFrom(reward)
            return 200, result

        # Gacha verification is a read-only local summary.  It never calls a
        # payment provider and is derived from the persisted draw counter.
        if "gachaverify" in action:
            if method not in {"GET", "POST"}:
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            result = self._message("ApiGachaVerify.result")
            if result is None:
                return None
            count = int(state.get("gacha_count", 0))
            total = result.total_info[1]
            total.type = 1; total.count = count
            actors = result.actor_info[1001]
            actors.quality_info[1] = count
            purple = result.purple_actor_info[1]
            purple.random_count = count % 10
            purple.force_count = count // 10
            purple.random_count_orange = count % 50
            purple.force_count_orange = count // 50
            verify_time = result.verify_time.add()
            verify_time.verify_start_time = now - 86400
            verify_time.verify_end_time = now
            return 200, result

        if "gachaplotemote" in action:
            if method not in {"GET", "POST"}:
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            result = self._message("ApiGachaPlotEmote.result")
            if result is None:
                return None
            plot_id = int(request.get("id", request.get("actorId", 0)) or 0)
            seen = {str(item) for item in state.get("gachaPlotEmotes", [])}
            result.is_first_plot_emote = str(plot_id) not in seen
            result.actor_video_url = f"local://gacha/{plot_id or 0}"
            if method == "POST" and str(plot_id) not in seen:
                seen.add(str(plot_id)); state["gachaPlotEmotes"] = sorted(seen); self._save(profile, state)
            return 200, result

        # gachainfo/gachatrade are owned by LocalSystems (client GachaType /
        # GachaActorPool tables); this module only handles gacha side routes.
        if "gachashoptrade" in action:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            result = self._message("ApiGachaShopTrade.post")
            if result is None:
                return None
            good_id = int(request.get("goodId", request.get("good_id", 1)) or 1)
            claim_key = f"gacha-shop:{good_id}"
            if claim_key in {str(value) for value in state.get("activity_claims", [])}:
                # A retried shop request must be a no-op.  Returning the same
                # typed envelope lets the client finish its callback without
                # charging the player a second time.
                return 200, result
            ok, spend_changes, error = self._spend_gold(profile, 1000)
            if not ok:
                return 409, {"error": error or "insufficient_resource"}
            reward = self._claim_activity_reward(profile, state, f"gacha-shop:{good_id}",
                                                  {"type": 11, "id": 20001, "count": 1, "param": 0})
            self._save(profile, state)
            for change in spend_changes:
                result.res_change.add().CopyFrom(self._dynamic(change, delta=int(change.get("countDelta", 0))))
            if reward is not None:
                result.res_change.add().CopyFrom(reward)
            return 200, result
        if "gachashop" in action:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            result = self._message("ApiGachaShop.get")
            if result is None:
                return None
            result.next_refresh_time = now + 86400
            good = result.goods.add(); good.good_id = 1; good.cost.type = 7; good.cost.count = 1000
            good.good.type = 11; good.good.id = 20001; good.good.count = 1
            return 200, result
        if "gachapoolverify" in action:
            if method != "GET" and method != "POST":
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            result = self._message("ApiGachaPoolVerify.result")
            if result is None:
                return None
            result.cur_server_time = now
            return 200, result

        if "activitybuffshop" in action:
            if method == "GET":
                result = self._message("ApiActivityBuffShop.get")
                if result is None:
                    return None
                entry = result.shop_info.add(); entry.goods_id = 1; entry.grade = 1
                return 200, result
            if method == "POST":
                result = self._message("ApiActivityBuffShop.post")
                if result is None:
                    return None
                good_id = int(request.get("goodsId", request.get("goods_id", 1)) or 1)
                claim_key = f"activity-buff-shop:{good_id}"
                if claim_key in {str(value) for value in state.get("activity_claims", [])}:
                    return 200, result
                ok, spend_changes, error = self._spend_gold(profile, 1000)
                if not ok:
                    return 409, {"error": error or "insufficient_resource"}
                reward = self._claim_activity_reward(profile, state, f"activity-buff-shop:{good_id}",
                                                      {"type": 11, "id": 20001, "count": 1, "param": 0})
                self._save(profile, state)
                for change in spend_changes:
                    result.res_change.add().CopyFrom(self._dynamic(change, delta=int(change.get("countDelta", 0))))
                if reward is not None:
                    result.res_change.add().CopyFrom(reward)
                return 200, result
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}

        # Deploy mission activity.  A fresh local guest sees one immediately
        # claimable mission, with rewards persisted by mission id.
        if "deploymissionsummary" in action:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            result = self._message("ApiDeployMissionSummary.get")
            if result is None:
                return None
            result.accomplish = 0
            return 200, result
        if "deploymission" in action:
            if action.endswith("deploymission") or "deploymissionget" in action:
                if method != "GET":
                    return 405, {"error": "method_not_allowed", "message": "GET required"}
                result = self._message("ApiDeployMission.get")
                if result is None:
                    return None
                info = result.mission_info.add(); info.id = 1; info.status = 0; info.start_timestamp = now
                info.end_timestamp = now + 3600
                result.accomplish_num = 0
                return 200, result
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            if "reward" in action:
                type_name = "ApiDeployMissionReward.result"
            elif "speedup" in action:
                type_name = "ApiDeployMissionSpeedUp.result"
            elif "discard" in action:
                type_name = "ApiDeployMissionDiscard.result"
            else:
                type_name = "ApiDeployMissionAttend.post"
            result = self._message(type_name)
            if result is None:
                return None
            mission_id = int(request.get("id", request.get("missionId", 1)) or 1)
            reward = self._claim_activity_reward(profile, state, f"deploy:{mission_id}",
                                                  {"type": 7, "id": 0, "count": 100, "param": 0})
            self._save(profile, state)
            if hasattr(result, "res_change") and reward is not None:
                result.res_change.add().CopyFrom(reward)
            for field in ("attend_result", "reward_result", "speedup_result", "discard_result"):
                if field in result.DESCRIPTOR.fields_by_name:
                    setattr(result, field, True)
            return 200, result

        # Qualification for the retired return-player campaign is server-only.
        # Until a dated eligibility/reward source exists, never grant a placeholder.
        if "conditionreward" in action:
            if method == "GET":
                # 真爱之契 is an account-level one-shot manual box.  The
                # original server's qualification flag is unavailable, so
                # local accounts are all eligible and receive the original
                # ordinary blue actor list from ManualBox 121001.
                choices = []
                seen = set()
                for row in _client_table_rows("ManualBox"):
                    if int(row.get("id", 0) or 0) != 121001:
                        continue
                    box = row.get("inBox") or {}
                    actor_id = int(box.get("id", 0) or 0)
                    if int(box.get("type", 0) or 0) != 15 or actor_id in seen:
                        continue
                    seen.add(actor_id)
                    choices.append(actor_id)
                result = self._message("ApiConditionReward.get")
                if result is None:
                    return None
                result.has_reward_qualification = not bool(state.get("condition_reward_got"))
                result.is_reward_got = bool(state.get("condition_reward_got", False))
                # The descriptor exposes the manual box as one STResource;
                # the client expands its choices from ManualBox 121001.
                result.manual_box_reward.type = 11
                result.manual_box_reward.id = 121001
                result.manual_box_reward.count = 1
                return 200, result
            if method == "POST":
                if state.get("condition_reward_got"):
                    return 409, {"error": "condition_reward_already_claimed"}
                request = self._body_object(body)
                index = request.get("manualBoxIndex", request.get("manual_box_index", request.get("index")))
                wire_request = self._request(body, "ApiConditionReward")
                if index is None and wire_request is not None:
                    index = int(getattr(wire_request, "manual_box_index", 0))
                # Proto3 omits a zero-valued manualBoxIndex, so an empty
                # wire request is the native selection of the first actor.
                if index is None and body in (b"", ""):
                    index = 0
                try:
                    index = int(index)
                except (TypeError, ValueError):
                    return 400, {"error": "manual_box_index_required"}
                choices = []
                seen = set()
                for row in _client_table_rows("ManualBox"):
                    if int(row.get("id", 0) or 0) != 121001:
                        continue
                    box = row.get("inBox") or {}; actor_id = int(box.get("id", 0) or 0)
                    if int(box.get("type", 0) or 0) == 15 and actor_id not in seen:
                        seen.add(actor_id); choices.append(actor_id)
                # ManualBox is the source of truth.  Do not retain the old
                # hard-coded 57-row limit when the client table changes.
                if not 0 <= index < len(choices):
                    return 400, {"error": "invalid_manual_box_index"}
                from grants import grant_rewards
                changes = grant_rewards(self.game, profile, [{"type": 15, "id": choices[index], "count": 1, "param": 1002}])
                state["condition_reward_got"] = True
                self._save(profile, state)
                result = self._message("ApiConditionReward.post")
                if result is None:
                    return None
                for change in changes:
                    dynamic = result.manual_box_res_change.add()
                    json_format.ParseDict(change, dynamic)
                return 200, result
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
        if "achievementsummary" in action:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            result = self._message("ApiAchievementSummary.get")
            return (200, result) if result is not None else None
        if "achievementreward" in action:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            result = self._message("ApiAchievementReward.result")
            if result is None:
                return None
            reward = self._claim_activity_reward(profile, state, "achievement-reward",
                                                  {"type": 7, "id": 0, "count": 100, "param": 0})
            self._save(profile, state)
            if reward is not None:
                result.res_change.add().CopyFrom(reward)
            return 200, result

        # Gift code is local-only and accepts a small explicit code set.
        if "giftcode" in action or "getgift" in action:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            code = str(request.get("code", "")).strip().upper()
            result = self._message("ApiGiftCode.result")
            if result is None:
                return None
            if code in {"LOCAL-START", "LOCAL-TEST", "LJJC-LOCAL"} and code not in state.get("gift_codes", []):
                reward = self._claim_activity_reward(profile, state, f"gift:{code}",
                                                      {"type": 7, "id": 0, "count": 500, "param": 0})
                state["gift_codes"] = sorted(set(state.get("gift_codes", [])) | {code}); self._save(profile, state)
                if reward is not None and hasattr(result.gift_code_info, "res_change"):
                    result.gift_code_info.res_change.add().CopyFrom(reward)
            return 200, result

        # The APK contains several optional activity panels whose payload
        # descriptors are not present in the recovered descriptor set.  Keep
        # their local endpoints reachable with schema-shaped empty JSON.  This
        # allows the client to close those panels cleanly instead of treating
        # the retired server as unreachable, while all real claims above stay
        # typed and persisted.
        if action.startswith('fcithemeactivitytasks'):
            return None
        ignored_basic = ("activitysignin", "everydaysignin", "continuouslogin", "signin")
        optional_activity = (
            "activitytask", "activityexplore", "turntable", "digtreasure",
            "activitydispatch", "dispatch", "rechargerebate", "loginreward",
            "activitylogin", "activityrebate", "activitygroup", "activityedu",
        )
        if not any(token in action for token in ignored_basic) and any(token in action for token in optional_activity):
            if method not in {"GET", "POST"}:
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            kind = next((token for token in optional_activity if token in action), "activity")
            ident = str(request.get("id", request.get("activityId", request.get("subActivityId", 1))) or 1)
            if method == "POST":
                return 409, {"error": "activity_not_open"}
            if "task" in action:
                return 200, {"tasks": [{"id": int(ident), "progress": 1, "maxProgress": 1, "rewardGot": f"optional:task:{ident}" in state.get("activity_claims", [])}], "reward": [], "received": []}
            if "dispatch" in action:
                return 200, {"tasks": [], "missionInfo": [], "accomplishNum": 0}
            if "turntable" in action:
                return 200, {"count": 0, "canReward": False, "reward": []}
            if "digtreasure" in action:
                return 200, {"grid": [], "layer": 0, "reward": []}
            if "rebate" in action:
                return 200, {"reward": [], "got": False}
            return 200, {}
        return None

    def _activity_list(self, method: str):
        """``GET fci/activity/``: every active activity as a plain JSON list.

        Manager_ActivityManager.OnActivityListGet iterates the stored reply as
        activity records (type/activityId), so the typed one-entry
        ``ApiActivity.result`` wrapper would leave ActivityList with a single
        accidental entry.  Each active schedule phase is built with the same
        logic as the per-type route.  The always-on local login activities
        (ContinuousLogin, EveryDayCheckin) are appended when the schedule
        lacks them, because Welfare2 only shows tabs for open activities.
        Finally the permanent challenge-menu modes (:meth:`_permanent_rows`)
        are appended for every type no earlier record already covers.
        """

        types: list[int] = []
        now = self._now()
        snapshot: Optional[Mapping[str, Any]] = None
        schedule = getattr(self.game, "activity_schedule", None)
        if schedule is not None:
            try:
                snapshot = schedule.snapshot(now)
                for phase in snapshot.get("active", []):
                    activity_type = int(phase.get("activityType", phase.get("type", 0)) or 0)
                    if activity_type and activity_type not in types:
                        types.append(activity_type)
            except (TypeError, ValueError, AttributeError):
                types, snapshot = [], None
        rows: list[dict[str, Any]] = []
        if not types:
            status, single = self._activity(method, "/fci/activity/")
            if status == 200 and single.HasField("result"):
                rows.append(_message_to_dict(single.result))
        for activity_type in types:
            status, single = self._activity(method, f"/fci/activity/?type={activity_type}")
            if status == 200 and single.HasField("result"):
                rows.append(_message_to_dict(single.result))
        present = {int(row.get("type", 0) or 0) for row in rows}
        for activity_type in ALWAYS_ON_ACTIVITY_TYPES:
            if activity_type in present:
                continue
            status, single = self._activity(method, f"/fci/activity/?type={activity_type}")
            if status == 200 and single.HasField("result"):
                rows.append(_message_to_dict(single.result))
        present = {int(row.get("type", 0) or 0) for row in rows}
        rows.extend(row for row in self._permanent_rows(now, snapshot) if row["type"] not in present)
        return 200, rows

    def _permanent_source(self) -> Optional[Path]:
        """Schedule document describing the permanent modes.

        An attached :class:`ActivitySchedule` is authoritative (its document
        may omit the section to list none); without one the shipped
        data/server-only/activity-schedule.yaml applies.
        """

        schedule = getattr(self.game, "activity_schedule", None)
        if schedule is None:
            return ACTIVITY_SCHEDULE_SOURCE
        source = getattr(schedule, "source", None)
        return Path(source) if source else ACTIVITY_SCHEDULE_SOURCE

    def _permanent_rows(self, now: int, snapshot: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Always-open challenge-menu records (gold/material dungeon, tower, ...).

        Each entry is an ``Activities`` record with no end (``timestampEnd`` 0,
        ``forever``), so ACU.IsActivityOn/IsForeverActivity hold, and
        ``timeType`` Forever for the menu row.  CollectionDungeon entries
        carry ``weekdays``: the material slots and ACU.IsOpenForDoing open an
        entry only on its weekdays.  ``timestampStart`` is the schedule anchor
        (stable across days), or the current daily-reset floor while the clock
        is before the anchor.
        """

        entries = _permanent_entries(self._permanent_source())
        if not entries:
            return []
        anchor = self._anchor_timestamp(snapshot)
        start = anchor if anchor <= now else anchor + ((now - anchor) // 86400) * 86400
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiActivity.result"))
        rows: list[dict[str, Any]] = []
        for entry in entries:
            activity = cls().result
            activity.type = entry["type"]; activity.activity_id = entry["activityId"]
            activity.time_preheat = "local"; activity.time_start = "local"; activity.time_end = "local"
            activity.timestamp_preheat = start; activity.timestamp_start = start; activity.timestamp_end = 0
            activity.weekdays.extend(entry["weekdays"])
            activity.forever = True
            activity.category = entry["category"]
            activity.time_type = entry["timeType"]
            activity.level_limit = entry["levelLimit"]
            if entry["type"] == 23 and entry["activityId"] == 23001:
                # DungeonTeam.lua indexes dayOpenTime[1] unconditionally.
                # The recovered permanent-menu row had no historical window,
                # so expose the local daily 06:00–24:00 opening explicitly.
                day = activity.day_open_time.add()
                day.time_start = "local"
                day.time_end = "local"
                day.timestamp_start = 6 * 3600
                day.timestamp_end = 24 * 3600
            welfare = getattr(self.game, "welfare", None)
            if welfare is not None and entry["type"] in (16, 26, 43, 44, 48, 53):
                window = welfare.cycle_window(now)
                activity.timestamp_preheat = window["start"]
                activity.timestamp_start = window["start"]
                activity.timestamp_end = window["end"]
                activity.timestamp_close = window["end"]
                activity.season = window["season"]
                activity.forever = False
                activity.time_type = 3  # ActivityTimeType_Period
            rows.append(_message_to_dict(activity))
        return rows

    def _anchor_timestamp(self, snapshot: Optional[Mapping[str, Any]]) -> int:
        """Schedule anchor (daily-reset aligned) as a Unix timestamp."""
        anchor = snapshot.get("anchor") if isinstance(snapshot, Mapping) else None
        if anchor:
            try:
                return policy_for(self.game).parse_config_datetime(str(anchor))
            except (TypeError, ValueError):
                pass
        # Protobuf activity timestamps are unsigned; Jan 2 is the first
        # positive 06:00 Beijing anchor after the Unix epoch.
        return policy_for(self.game).day_start_from_index(1)

    def _cycle_start(self, snapshot: Optional[Mapping[str, Any]], now: int) -> int:
        """Start of the current schedule cycle, aligned to the anchor's time of day."""
        anchor = self._anchor_timestamp(snapshot)
        day_floor = anchor + ((now - anchor) // 86400) * 86400
        try:
            cycle_day = int((snapshot or {}).get("cycleDay", 0) or 0)
        except (TypeError, ValueError):
            cycle_day = 0
        return day_floor - cycle_day * 86400

    def _always_on_activity(self, cls: Any, activity_type: int, now: int,
                            snapshot: Optional[Mapping[str, Any]]):
        """A local ContinuousLogin/EveryDayCheckin entry when no phase covers it.

        ContinuousLogin is open-ended (``timestampEnd`` 0, ``forever``).  The
        EveryDayCheckin calendar runs in windows of its ActivitySigninGift row
        count (10 days for 46001) restarting at each schedule cycle, so it lines
        up with a scheduled sign-in phase starting on cycle day 0.
        ``timestampEnd`` is one second before the next window, which makes the
        client's ``allDayCount`` equal the row count.
        """
        result = cls()
        activity = result.result
        activity.type = activity_type
        activity.activity_id = DEFAULT_ACTIVITY_IDS.get(activity_type, 1)
        activity.time_preheat = "local"; activity.time_start = "local"; activity.time_end = "local"
        activity.category = 1
        cycle_start = self._cycle_start(snapshot, now)
        if activity_type in (ACTIVITY_TYPE_EVERYDAY_CHECKIN, ACTIVITY_TYPE_TEN_CHECKIN):
            window = len(ACTIVITY_SIGNIN_GIFTS.get(activity.activity_id, {})) or 10
            # Independent ten-day windows: a 28-day event rotation must not
            # truncate days 9/10, and the client changes sign-in days at midnight.
            policy = policy_for(self.game)
            cycle_start = policy.period_start(self._anchor_timestamp(snapshot), signin=True)
            if activity_type == ACTIVITY_TYPE_TEN_CHECKIN:
                # Keep the ten-day tab inside the configured local welfare
                # cycle instead of exposing an open-ended/epoch end date.
                welfare = getattr(self.game, "welfare", None)
                welfare_window = welfare.cycle_window(now) if welfare is not None else None
                start = int((welfare_window or {}).get("start", cycle_start))
                end = int((welfare_window or {}).get("end", start + 28 * 86400))
            else:
                cycle_day = (now - cycle_start) // 86400
                start = cycle_start + (cycle_day // window) * window * 86400
                end = start + window * 86400
            activity.timestamp_preheat = start; activity.timestamp_start = start
            activity.timestamp_end = end - 1
            if activity_type == ACTIVITY_TYPE_TEN_CHECKIN:
                activity.timestamp_end = end
                activity.timestamp_close = end
            activity.forever = False
        else:
            day_floor = cycle_start + ((now - cycle_start) // 86400) * 86400
            activity.timestamp_preheat = day_floor; activity.timestamp_start = day_floor
            activity.timestamp_end = 0
            activity.forever = True
        return 200, result

    def _activity(self, method: str, path: str = ""):
        if method not in {"GET", "HEAD"}:
            return 405, {"error": "method_not_allowed", "message": "GET required"}
        path_name = (urlsplit(path or "").path or "").rstrip("/").lower()
        response_type = "ApiActivity.result"
        if path_name.endswith("/activity-preopen"):
            response_type = "ApiActivityPreOpen.result"
        elif path_name.endswith("/activity-open"):
            response_type = "ApiActivityOpen.result"
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(response_type))
        now = self._now()
        # ``ApiActivity.result`` carries one activity entry.  The client uses
        # the activity type to decide which local panel to open; type 1 is a
        # retired dungeon activity and leaves the city entry inert.  Expose
        # the implemented local shop by default, while allowing diagnostics
        # and older clients to request the compatible sign-in/continuous-login
        # entries with ``?type=46``/``?type=25`` or a numeric path suffix.
        activity_type = 12  # ActivityType_Shop
        selected_phase: Optional[Mapping[str, Any]] = None
        schedule = getattr(self.game, "activity_schedule", None)
        schedule_snapshot = None
        if schedule is not None:
            try:
                schedule_snapshot = schedule.snapshot(now)
                active = schedule_snapshot.get("active", [])
                if active:
                    selected_phase = active[0]
                    activity_type = int(selected_phase.get("activityType", activity_type))
            except (TypeError, ValueError, AttributeError):
                # The city entry remains available if an optional schedule
                # file is malformed or an older test double lacks snapshot().
                activity_type, selected_phase, schedule_snapshot = 12, None, None
        parsed = urlsplit(path or "")
        suffix = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        query = parse_qs(parsed.query)
        requested = query.get("type", query.get("activityType", [suffix]))
        explicit = bool(requested and str(requested[0]).isdigit())
        if explicit:
            activity_type = int(requested[0])
        always_on = response_type == "ApiActivity.result" and activity_type in ALWAYS_ON_ACTIVITY_TYPES
        if always_on and activity_type == ACTIVITY_TYPE_EVERYDAY_CHECKIN:
            return self._always_on_activity(cls, activity_type, now, schedule_snapshot)
        if schedule_snapshot is not None:
            active = schedule_snapshot.get("active", [])
            if explicit:
                selected_phase = None
                for phase in active:
                    try:
                        if int(phase.get("activityType", phase.get("type", 0))) == activity_type:
                            selected_phase = phase
                            break
                    except (TypeError, ValueError):
                        continue
            if selected_phase is None:
                if explicit and always_on:
                    return self._always_on_activity(cls, activity_type, now, schedule_snapshot)
                return 200, cls()
        elif explicit and always_on:
            return self._always_on_activity(cls, activity_type, now, None)
        activity_id = DEFAULT_ACTIVITY_IDS.get(activity_type, 1)
        if selected_phase is not None and selected_phase.get("activityId") not in (None, ""):
            try:
                activity_id = int(selected_phase.get("activityId")) or activity_id
            except (TypeError, ValueError):
                pass
        category = 4 if activity_type == 12 else 1
        activity = cls().result
        activity.type = activity_type; activity.activity_id = activity_id
        activity.time_preheat = "local"; activity.time_start = "local"; activity.time_end = "local"
        activity.timestamp_preheat = now; activity.timestamp_start = now; activity.timestamp_end = now + 86400 * 365
        if selected_phase is not None:
            try:
                day_start = int(selected_phase.get("dayStart", schedule_snapshot.get("cycleDay", 0)))
                day_end = int(selected_phase.get("dayEnd", day_start + 1))
                cycle_start = self._cycle_start(schedule_snapshot, now)
                activity.timestamp_preheat = cycle_start + day_start * 86400
                activity.timestamp_start = activity.timestamp_preheat
                activity.timestamp_end = cycle_start + day_end * 86400
                if bool(selected_phase.get("forever", False)):
                    activity.timestamp_end = 0
                    activity.timestamp_close = 0
            except (TypeError, ValueError):
                pass
        activity.forever = selected_phase is None or bool(selected_phase.get("forever", False))
        activity.category = category
        result = cls(); result.result.CopyFrom(activity)
        return 200, result

    def _handle_http_unlocked(self, method: str, path: str, body: Any, profile: Mapping[str, Any]):
        """Return ``(status, payload)`` for a route, or ``None``."""
        raw = (path or "").split("?", 1)[0].rstrip("/") or "/"
        route = raw[4:] if raw.startswith("/fci") else raw
        lower = route.lower(); method = method.upper()
        if lower == "/activity" and method == "GET":
            query = parse_qs(urlsplit(path or "").query, keep_blank_values=True)
            if not {"type", "activityType"}.intersection(query):
                return self._activity_list(method)
        if lower in {"/activity", "/activity-preopen", "/activity-open"} or lower.startswith("/activity/"):
            return self._activity(method, path)
        activity_api = self._activity_claim_api(method, path, body, profile)
        if activity_api is not None:
            return activity_api
        if lower == "/signin": return self._signin(method, body, profile)
        if lower == "/signin/gift": return self._signin_gift(method, body, profile)
        if lower == "/activity-signin" or lower.startswith("/activity-signin/"):
            return self._activity_signin(method, route, profile)
        if lower == "/everyday-signin" or lower.startswith("/everyday-signin/"):
            return self._everyday_signin(method, route, profile)
        if lower in {"/websignin", "/websignin/", "/web-signin", "/web-signin/"}:
            return self._web_signin(method, body, profile)
        if lower.startswith("/continuouslogin"):
            return self._continuous(method, route, body, profile)
        if lower == "/subscribe":
            return self._subscribe(method, body, profile)
        return None


    def handle_http(self, method: str, path: str, body: Any, profile: Mapping[str, Any]):
        """Serialize claim and reward mutations for a local account."""
        with self._lock:
            return self._handle_http_unlocked(method, path, body, profile)



__all__ = ["LocalEvents"]
