"""Local daily-routine and main-chain task system (``/fci/task/``).

The retired service fed the task window from ``GET /fci/task/`` (read once by
``Manager/RestifizerManager.DoEnteringRequests``) and from ``ScTasksChange``
pushes; both land in ``Common/TaskUtil.InitData``.  This module serves the
plain-JSON shape that function reads and the claim endpoints of
``Window/Task/WidgetTaskSlot`` and ``Window/Task/Task``:

* ``GET  /fci/task/``                    -> TaskUtil.InitData payload
* ``POST /fci/task/<id>/reward/``         -> ``{resChange, activePoint}``
* ``POST /fci/task/activepoint/reward/``  -> ``{resChange}`` (body ``{targetActivePoint}``)
* ``GET  /fci/week-activepoint/``         -> ``{weekActivePoint, rewardGot}``
* ``POST /fci/week-activepoint/reward/``  -> ``{resChange, rewardGot}`` (body ``{weekActivePoint}``)

Only TaskInfo categories 1 (Routine, daily) and 2 (Main, chained through
``preTaskId``) are offered; every other category is listed in
``disableCategory`` so the client hides its tab.  Daily tasks, the daily
active points and the milestone claims reset at 06:00 Beijing (the bucket the
rest of the local server uses); the weekly active points reset on Monday
06:00 Beijing.  Rewards are paid from the client's own TaskInfo /
TaskActivePointReward / WeekActivePointReward tables through
:func:`grants.grant_rewards`.

Progress comes from hooks the game calls (``on_battle_win``, ``on_login``,
``on_level``) plus the generic :meth:`LocalTasks.record` for goal types whose
source system is wired later.  Goal types without a source simply do not
progress.

Live updates: the client reads ``/fci/task/`` only once per session, so any
hook that changes progress (and every successful task / active-point claim,
which can unlock successors or CompletePnTask progress the client does not
derive itself) calls ``LocalTasks.notifier(profile, payload)`` with the same
payload shape.  The server wires it to ``PushService.tasks_change`` which sends
it as protobuf ``ScTasksChange``.  ``notifier`` is None by default (no-op).
"""

from __future__ import annotations

from server.core.reset import policy_for, period_due

import json
import logging
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlsplit

try:  # package import (tests/tools) and flat import (server.py) both work
    from .grants import grant_rewards
except ImportError:  # pragma: no cover - flat module layout
    from grants import grant_rewards


Body = bytes | bytearray | str | None
Response = tuple[int, Any]
# ``notifier(profile, payload)``: profile is the game profile passed to the
# hook (``profile["account_id"]`` selects the player); payload is the
# ``GET /fci/task/`` dict (the ScTasksChange push shape).
TaskNotifier = Callable[[Mapping[str, Any], dict], Any]

_log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
_TABLE_DIR = _ROOT / "data" / "client-tables"
_DESCRIPTOR = _ROOT / "data" / "descriptor_set.pb"


# TaskCategory enum (descriptor_set.pb).
CATEGORY_ROUTINE = 1
CATEGORY_MAIN = 2
CATEGORY_ACTIVITY = 102
CATEGORY_RANDOM = 104
# Category 104 is owned by LocalCultivation, which has actor-specific
# progress and daily random selection.  Keep it out of the generic task store.
OWNED_CATEGORIES = (CATEGORY_ROUTINE, CATEGORY_MAIN, CATEGORY_ACTIVITY)
# Deadline/Repeat/Newbie/Challenge/Favour have no local source yet; listing
# them in ``disableCategory`` hides their tabs (TaskUtil.CheckCategory).
DISABLED_CATEGORIES = [3, 4, 5, 6, 7]
# Routine task 16 is TaskType_PayTestReChargeRewardLogin (syncByServer): the
# closed-beta recharge rebate.  Nobody can earn it locally, so it is hidden.
HIDDEN_TASK_IDS = frozenset({16})

# TaskType enum values (descriptor_set.pb, enum TaskType) used below.
ONLINE_FROM_P0_TO_P1 = 1
BALANCE_P0_DUNGEON_M_TIMES = 2
BATTLE_LINK_P0_M_TIMES = 15
ACTOR_ID_P0_BIG_SKILL_M_TIMES = 16
POWER_COST_M = 17
ACTIVITY_P0_DUN_P1_M_TIMES = 18
KILL_ACTOR_ID_P0_M_TIMES = 19
BALANCE_PN_DUNGEON_M_TIMES_ACTOR_P0 = 67
TOWER_P0_FLOOR_M = 22
TOURNAMENT_JOIN_M_TIMES = 23
BALANCE_DUNGEON_PN_M_TIMES = 34
LOGIN_M_TIMES = 37
LEVEL_M = 38
COMPLETE_PN_TASK = 43
GAME_CENTER_LOGIN_M_TIMES = 53
TASK_ACTIVE_POINT_P0_M_TIMES = 64

# Goto 205 (DungeonGold) lists gold dungeons 20001-20006 in routine task 13 and
# main task 1206; the local gold mode uses its own stage id, so a gold-mode win
# counts as a win in that list.
GOLD_DUNGEON_IDS = frozenset(range(20001, 20007))
# Main tasks 1108/1207 (goto 206, ActivityStageChoose2) name activity stages
# 52101/52201/52301...; 5xxxx is the activity-stage id block.
ACTIVITY_DUNGEON_RANGE = range(50000, 60000)
_STORY_MODES = {None, "", "story", "normal", "elite", "hard"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _route(path: str) -> str:
    value = urlsplit(path or "").path or "/"
    return value.rstrip("/").lower() or "/"


def _json(body: Body) -> dict[str, Any]:
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


def _rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


def _rewards(raw: Any) -> list[dict[str, int]]:
    """STResource rows with proto3 defaults filled in (missing id/param = 0)."""

    result = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, Mapping) and _int(item.get("count")) > 0:
            result.append({"type": _int(item.get("type")), "id": _int(item.get("id")),
                           "count": _int(item.get("count")), "param": _int(item.get("param"))})
    return result


def _task_def(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _int(row.get("id")),
        "category": _int(row.get("category")),
        "goalType": _int(row.get("goalType")),
        "maxProgress": max(1, _int(row.get("maxProgress"), 1)),
        "taskParam": [_int(value) for value in (row.get("taskParam") or [])],
        "reward": _rewards(row.get("reward")),
        "activePoint": _int(row.get("activePoint")),
        "preTaskId": _int(row.get("preTaskId")),
        "recordBeforeGot": bool(row.get("recordBeforeGot")),
        "deleteAfterReward": bool(row.get("deleteAfterReward")),
        "extraActivePoint": _int(row.get("extraActivePoint")),
        "extraActivePointType": _int(row.get("extraActivePointType"), 1),
    }


TASKS: dict[int, dict[str, Any]] = {
    task["id"]: task
    for task in (_task_def(row) for row in _rows("TaskInfo"))
    if task["id"] and task["category"] in OWNED_CATEGORIES
}
ROUTINE_IDS = tuple(sorted(tid for tid, task in TASKS.items() if task["category"] == CATEGORY_ROUTINE))
MAIN_IDS = tuple(sorted(tid for tid, task in TASKS.items() if task["category"] == CATEGORY_MAIN))
ACTIVITY_IDS = tuple(sorted(tid for tid, task in TASKS.items() if task["category"] == CATEGORY_ACTIVITY))
RANDOM_IDS = tuple(sorted(tid for tid, task in TASKS.items() if task["category"] == CATEGORY_RANDOM))
ACTIVE_POINT_REWARDS: dict[int, list[dict[str, int]]] = {
    _int(row.get("targetActivePoint")): _rewards(row.get("reward"))
    for row in _rows("TaskActivePointReward") if _int(row.get("targetActivePoint")) > 0
}
WEEK_ACTIVE_POINT_REWARDS: dict[int, list[dict[str, int]]] = {
    _int(row.get("activePoint")): _rewards(row.get("reward"))
    for row in _rows("WeekActivePointReward") if _int(row.get("activePoint")) > 0
}
# Activity extra-active-point milestones.  The original client keeps these in
# ``DbPlayerTask.TaskExtraActivePointInfo`` (one accumulator per activity
# type), rather than in the ordinary daily/weekly task counters.
EXTRA_ACTIVE_POINT_REWARDS: dict[int, dict[int, list[dict[str, int]]]] = {}
for _row in _rows("TaskExtraActivePointReward"):
    _extra_type = _int(_row.get("activePointType"), 0)
    _extra_target = _int(_row.get("activePoint"), 0)
    if _extra_type > 0 and _extra_target > 0:
        EXTRA_ACTIVE_POINT_REWARDS.setdefault(_extra_type, {})[_extra_target] = _rewards(_row.get("reward"))

_TASK_TYPE_NAMES: Optional[dict[str, int]] = None


def task_type(goal: int | str) -> int:
    """Resolve a TaskType number or name ("LevelM", "TaskType_LevelM") to its value."""

    global _TASK_TYPE_NAMES
    if isinstance(goal, int):
        return goal
    text = str(goal).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    if _TASK_TYPE_NAMES is None:
        names: dict[str, int] = {}
        try:
            from google.protobuf import descriptor_pb2

            files = descriptor_pb2.FileDescriptorSet()
            files.ParseFromString(_DESCRIPTOR.read_bytes())
            for proto in files.file:
                for enum in list(proto.enum_type) + [e for m in proto.message_type for e in m.enum_type]:
                    if enum.name == "TaskType":
                        for value in enum.value:
                            names[value.name.lower()] = value.number
        except Exception:  # pragma: no cover - descriptor missing/damaged
            names = {}
        _TASK_TYPE_NAMES = names
    key = text.lower()
    return _TASK_TYPE_NAMES.get(key, _TASK_TYPE_NAMES.get("tasktype_" + key, 0))


class LocalTasks:
    """Per-account routine/main task state stored in ``local_tasks``."""

    def __init__(self, game: Any, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        # Injected by the server (PushService.tasks_change); None = no push.
        self.notifier: Optional[TaskNotifier] = None
        with self._lock, closing(game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_tasks (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    # ------------------------------------------------------------------ time
    def _now(self) -> int:
        return int(self._clock())

    def _day(self, now: int) -> int:
        return policy_for(self.game).day_index(now)

    def _week(self, now: int) -> int:
        return policy_for(self.game).week_index(now)

    def _hour(self, now: int) -> int:
        hour = policy_for(self.game).local_datetime(now).hour
        return 24 if hour == 0 else hour  # TaskUtil.CheckTaskTime maps 0h to 24

    # ----------------------------------------------------------------- state
    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "day": None,
            "week": None,
            "tasks": {},
            "activePoint": 0,
            "activePointRewardGot": [],
            "weekActivePoint": 0,
            "weekRewardGot": [],
            "loginDay": None,
            "dungeonWins": {},
            "towerMaxFloor": 0,
            # ``type`` -> {season, points, rewardGot}.  The local activity
            # season is deliberately stable until a future activity-season
            # source is restored; points therefore survive daily resets.
            "extraActivePoint": {},
        }

    def _seed_history(self, profile: Mapping[str, Any], state: dict[str, Any]) -> None:
        """Import already-balanced dungeons so recordBeforeGot tasks see old clears."""

        try:
            with closing(self.game._connect()) as connection:
                rows = connection.execute(
                    "SELECT dungeon_id, COUNT(*) AS wins FROM local_dungeons "
                    "WHERE account_id = ? AND status = 'balanced' GROUP BY dungeon_id",
                    (int(profile["account_id"]),),
                ).fetchall()
        except Exception:  # table absent (fresh store) or older schema
            return
        wins = state["dungeonWins"]
        for row in rows:
            wins[str(_int(row["dungeon_id"]))] = _int(row["wins"])

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM local_tasks WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is not None:
                try:
                    state = json.loads(row["state_json"] or "{}")
                except (TypeError, ValueError):
                    state = {}
                if not isinstance(state, dict):
                    state = {}
                for key, value in self._default_state().items():
                    state.setdefault(key, value)
                if not isinstance(state.get("extraActivePoint"), dict):
                    state["extraActivePoint"] = {}
                return state
            state = self._default_state()
            self._seed_history(profile, state)
            connection.execute(
                "INSERT OR IGNORE INTO local_tasks(account_id, state_json, updated_at) VALUES (?, ?, ?)",
                (account_id, json.dumps(state, separators=(",", ":")), self._now()),
            )
            return state

    @staticmethod
    def _extra_entry(state: dict[str, Any], point_type: int) -> dict[str, Any]:
        """Return a normalized per-type extra-active-point accumulator."""

        all_entries = state.setdefault("extraActivePoint", {})
        if not isinstance(all_entries, dict):
            all_entries = {}
            state["extraActivePoint"] = all_entries
        key = str(max(0, _int(point_type)))
        entry = all_entries.get(key)
        if not isinstance(entry, dict):
            entry = {}
            all_entries[key] = entry
        entry["season"] = _int(entry.get("season"), 1) or 1
        entry["points"] = max(0, _int(entry.get("points"), 0))
        got = entry.get("rewardGot", [])
        entry["rewardGot"] = sorted({_int(value) for value in got}) if isinstance(got, list) else []
        return entry

    def _extra_payload(self, state: Mapping[str, Any], point_type: int) -> dict[str, Any]:
        point_type = _int(point_type)
        milestones = EXTRA_ACTIVE_POINT_REWARDS.get(point_type, {})
        raw_all = state.get("extraActivePoint", {})
        raw_entry = raw_all.get(str(point_type), {}) if isinstance(raw_all, Mapping) else {}
        entry = raw_entry if isinstance(raw_entry, Mapping) else {}
        points = max(0, _int(entry.get("points"), 0))
        got = {_int(value) for value in (entry.get("rewardGot") or [])}
        progress = []
        for target, reward in sorted(milestones.items()):
            row = {
                "targetActivePoint": target,
                "rewardGot": target in got,
                "reward": list(reward),
            }
            # Some 2.0.4 JSON bindings used the generated long name.  Keeping
            # this alias costs nothing and lets both native request paths read
            # the same payload.
            row["rewardSTResource"] = list(reward)
            progress.append(row)
        return {
            "extraActivePointType": point_type,
            "extraActivePoint": points,
            "progress": progress,
        }

    def extra_active_point_snapshot(self, profile: Mapping[str, Any], point_type: int = 1) -> dict[str, Any]:
        """Return the native extra-active-point result for an activity type."""

        with self._lock:
            state = self._state(profile)
            self._extra_entry(state, point_type)
            self._save(profile, state)
            return self._extra_payload(state, point_type)

    def record_extra_active_point(self, profile: Mapping[str, Any], amount: int = 1,
                                  point_type: int = 1) -> dict[str, Any]:
        """Add activity points from a completed activity task/battle.

        Activity task handlers can call this hook without knowing the storage
        shape.  The value is monotonic within the local activity season; the
        client may continue displaying points after the final milestone.
        """

        amount = max(0, _int(amount))
        with self._lock:
            state = self._state(profile)
            entry = self._extra_entry(state, point_type)
            entry["points"] = entry["points"] + amount
            self._save(profile, state)
            return self._extra_payload(state, point_type)

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_tasks SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    # ------------------------------------------------------------- progress
    @staticmethod
    def _offered(state: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
        tasks = state["tasks"]
        for tid in ROUTINE_IDS:
            if tid not in HIDDEN_TASK_IDS:
                yield TASKS[tid]
        for tid in MAIN_IDS:
            task = TASKS[tid]
            pre = task["preTaskId"]
            if not pre or (tasks.get(str(pre)) or {}).get("rewardGot"):
                yield task
        # Activity tasks were previously filtered out with category 102/104.
        # Keep the client-visible set bounded: all 19 category-102 tasks are
        # available each local activity cycle, while three deterministic
        # category-104 tasks stand in for the unavailable random-roll service.
        for tid in ACTIVITY_IDS:
            yield TASKS[tid]

    def _history(self, task: Mapping[str, Any], state: Mapping[str, Any], now: int) -> int:
        """Progress a ``recordBeforeGot`` task already earned before it was offered."""

        goal, params = task["goalType"], task["taskParam"]
        wins = state["dungeonWins"]
        if goal == BALANCE_P0_DUNGEON_M_TIMES:
            target = params[0] if params else 0
            return sum(_int(v) for v in wins.values()) if target == 0 else _int(wins.get(str(target)))
        if goal == BALANCE_DUNGEON_PN_M_TIMES:
            return sum(_int(wins.get(str(dungeon_id))) for dungeon_id in set(params))
        if goal == TOWER_P0_FLOOR_M:
            floor = params[0] if params else 0
            return 1 if _int(state["towerMaxFloor"]) >= max(1, floor) else 0
        if goal in (LOGIN_M_TIMES, GAME_CENTER_LOGIN_M_TIMES):
            return 1 if state.get("loginDay") == self._day(now) else 0
        return 0

    def _status_value(self, task: Mapping[str, Any], profile: Mapping[str, Any], state: Mapping[str, Any],
                      now: int) -> Optional[int]:
        """Progress of state-derived goals (None for event-counted goals)."""

        goal, params = task["goalType"], task["taskParam"]
        if goal == LEVEL_M:
            return _int(profile.get("level"), 1)
        if goal == COMPLETE_PN_TASK:
            tasks = state["tasks"]
            return sum(1 for tid in params if (tasks.get(str(tid)) or {}).get("rewardGot"))
        if goal == TASK_ACTIVE_POINT_P0_M_TIMES:
            return _int(state["activePoint"])
        if goal == ONLINE_FROM_P0_TO_P1 and len(params) >= 2:
            return 1 if params[0] <= self._hour(now) < params[1] else 0
        return None

    def _sync(self, profile: Mapping[str, Any], state: dict[str, Any]) -> list[int]:
        """Apply resets, offer unlocked tasks and refresh state-derived progress."""

        now = self._now()
        day, week = self._day(now), self._week(now)
        tasks = state["tasks"]
        if period_due(state.get("day"), day):
            for tid in ROUTINE_IDS:
                tasks.pop(str(tid), None)
            for tid in ACTIVITY_IDS:
                tasks.pop(str(tid), None)
            for tid in RANDOM_IDS:
                tasks.pop(str(tid), None)
            state["day"], state["activePoint"], state["activePointRewardGot"] = day, 0, []
        if period_due(state.get("week"), week):
            state["week"], state["weekActivePoint"], state["weekRewardGot"] = week, 0, []
        changed: list[int] = []
        # Rewarding a task can unlock a successor whose state goal is already
        # met, which can in turn complete a CompletePnTask; loop to a fixpoint.
        for _ in range(4):
            dirty = False
            for task in list(self._offered(state)):
                key = str(task["id"])
                entry = tasks.get(key)
                if entry is None:
                    entry = {"progress": 0, "isDone": False, "rewardGot": False}
                    if task["recordBeforeGot"]:
                        entry["progress"] = min(task["maxProgress"], self._history(task, state, now))
                    tasks[key] = entry
                    dirty = True
                if entry.get("rewardGot"):
                    continue
                value = self._status_value(task, profile, state, now)
                progress = _int(entry.get("progress"))
                if value is not None:
                    progress = max(progress, value) if task["goalType"] == LEVEL_M else value
                if entry.get("isDone"):
                    progress = task["maxProgress"]
                progress = max(0, min(task["maxProgress"], progress))
                done = bool(entry.get("isDone")) or progress >= task["maxProgress"]
                if progress != _int(entry.get("progress")) or done != bool(entry.get("isDone")):
                    entry["progress"], entry["isDone"] = progress, done
                    changed.append(task["id"])
                    dirty = True
            if not dirty:
                break
        return changed

    def _advance(self, state: dict[str, Any], goal: int, amount: int = 1, *, match=None,
                 value: Optional[int] = None) -> list[int]:
        """Add ``amount`` (or raise to ``value``) on offered, unfinished ``goal`` tasks."""

        changed = []
        if amount <= 0 and value is None:
            return changed
        for task in self._offered(state):
            if task["goalType"] != goal or (match is not None and not match(task["taskParam"])):
                continue
            entry = state["tasks"].get(str(task["id"]))
            if entry is None or entry.get("rewardGot") or entry.get("isDone"):
                continue
            current = _int(entry.get("progress"))
            progress = max(current, value) if value is not None else current + amount
            progress = min(task["maxProgress"], progress)
            if progress != current:
                entry["progress"] = progress
                entry["isDone"] = progress >= task["maxProgress"]
                changed.append(task["id"])
        return changed

    def _mark_login(self, state: dict[str, Any]) -> list[int]:
        today = self._day(self._now())
        if not period_due(state.get("loginDay"), today):
            return []
        state["loginDay"] = today
        # GameCenterLoginMTimes asks for a launch through the publisher's
        # platform page; locally every login counts as that launch.
        return (self._advance(state, LOGIN_M_TIMES, 1)
                + self._advance(state, GAME_CENTER_LOGIN_M_TIMES, 1))

    def _run(self, profile: Mapping[str, Any], mutate) -> list[int]:
        payload = None
        with self._lock:
            state = self._state(profile)
            changed = self._sync(profile, state)
            changed += mutate(state)
            changed += self._sync(profile, state)
            self._save(profile, state)
            if changed and self.notifier is not None:
                payload = self._payload(profile, state)
        if payload is not None:
            self._notify(profile, payload)
        return sorted(set(changed))

    def _notify(self, profile: Mapping[str, Any], payload: dict) -> None:
        """Hand a fresh task payload to the push channel; never raises."""

        notifier = self.notifier
        if notifier is None:
            return
        try:
            notifier(profile, payload)
        except Exception:  # an offline/broken push client must not fail gameplay
            _log.debug("task push notification failed", exc_info=True)

    # ----------------------------------------------------------------- hooks
    def on_login(self, profile: Mapping[str, Any]) -> list[int]:
        """Call after a successful game login; returns task ids whose progress changed."""

        return self._run(profile, self._mark_login)

    def on_level(self, profile: Mapping[str, Any]) -> list[int]:
        """Call after the player level changes (reads ``profile["level"]``)."""

        return self._run(profile, lambda state: [])

    def on_battle_win(self, profile: Mapping[str, Any], dungeon_id: int, energy_spent: int = 0, *,
                      mode: Optional[str] = None, floor: Optional[int] = None,
                      stats: Optional[Mapping[str, Any]] = None, actor_uids=None,
                      cultivation_stats: Optional[Mapping[str, Any]] = None,
                      dungeon_type=0, activity_type=0, activity_id=0) -> list[int]:
        """Call once per won battle.

        ``mode`` is the local special mode ("gold", "tower", "maze",
        "tournament", "activity") or None for story/normal stages; ``floor``
        is the tower floor; ``stats`` may carry battle counters
        ``{"linkSkills", "bigSkills", "kills"}`` when the caller has them.
        """

        dungeon_id = _int(dungeon_id)
        energy = max(0, _int(energy_spent))
        mode = (str(mode).strip().lower() or None) if mode is not None else None
        stats = stats if isinstance(stats, Mapping) else {}

        def mutate(state: dict[str, Any]) -> list[int]:
            if dungeon_id:
                wins = state["dungeonWins"]
                wins[str(dungeon_id)] = _int(wins.get(str(dungeon_id))) + 1
            tower_floor = _int(floor) if mode == "tower" else 0
            if tower_floor:
                state["towerMaxFloor"] = max(_int(state["towerMaxFloor"]), tower_floor)
            changed = []
            if mode in _STORY_MODES:
                changed += self._advance(state, BALANCE_P0_DUNGEON_M_TIMES, 1,
                                         match=lambda p: not p or p[0] in (0, dungeon_id))
            gold = mode == "gold"
            changed += self._advance(state, BALANCE_DUNGEON_PN_M_TIMES, 1, match=lambda p: dungeon_id in p or (
                gold and bool(p) and set(p) <= GOLD_DUNGEON_IDS))
            if mode == "activity" or dungeon_id in ACTIVITY_DUNGEON_RANGE:
                changed += self._advance(state, ACTIVITY_P0_DUN_P1_M_TIMES, 1,
                                         match=lambda p: not p or p[0] == 0 or dungeon_id in p)
            # Category-102 theme tasks 60002-60005 use the original
            # actor/stage goal (67).  Their params contain the stage ids and,
            # for some rows, actor ids; matching the completed stage here is
            # the same criterion the client applies to its battle result.
            if mode in ("activity", "normal", "story", None) or dungeon_id in ACTIVITY_DUNGEON_RANGE:
                changed += self._advance(state, BALANCE_PN_DUNGEON_M_TIMES_ACTOR_P0, 1,
                                         match=lambda p: not p or dungeon_id in p)
            if mode == "tower":
                changed += self._advance(state, TOWER_P0_FLOOR_M, 1,
                                         match=lambda p: not p or p[0] == 0 or tower_floor >= p[0])
            if mode == "tournament":
                changed += self._advance(state, TOURNAMENT_JOIN_M_TIMES, 1)
            changed += self._advance(state, POWER_COST_M, energy)
            any_actor = lambda p: not p or p[0] == 0  # noqa: E731 - per-actor stats are not reported
            changed += self._advance(state, BATTLE_LINK_P0_M_TIMES, _int(stats.get("linkSkills")), match=any_actor)
            changed += self._advance(state, ACTOR_ID_P0_BIG_SKILL_M_TIMES, _int(stats.get("bigSkills")), match=any_actor)
            changed += self._advance(state, KILL_ACTOR_ID_P0_M_TIMES, _int(stats.get("kills")), match=any_actor)
            return changed

        cultivation = getattr(self.game, 'cultivation', None)
        if cultivation is not None and actor_uids:
            import actors
            system = self.game.systems._state(profile)
            actor_ids = [record['id'] for uid in actor_uids
                         if (record := actors.get(profile, system, uid)) is not None]
            cultivation_stats = cultivation_stats or (stats.get("cultivationStats", stats) if isinstance(stats, Mapping) else {})
            cultivation.on_battle_win(profile, dungeon_id, actor_ids=actor_ids,
                                      dungeon_type=dungeon_type, activity_type=activity_type,
                                      activity_id=activity_id,
                                      stats={**dict(cultivation_stats), "actorIds": actor_ids})
        return self._run(profile, mutate)

    def record(self, profile: Mapping[str, Any], goal: int | str, amount: int = 1, *,
               param: Optional[int] = None, value: Optional[int] = None) -> list[int]:
        """Generic progress for other systems (e.g. ``record(p, "DeployDoneM")``).

        ``amount`` adds to counted goals; ``value`` raises "reach M" goals to
        at least ``value``.  ``param`` must match the task's first taskParam
        unless that is 0/absent.
        """

        goal_type = task_type(goal)
        if not goal_type:
            return []

        def match(params: list[int]) -> bool:
            return not params or params[0] == 0 or (param is not None and _int(param) in params)

        changed = self._run(profile, lambda state: self._advance(state, goal_type, _int(amount), match=match,
                                                                  value=None if value is None else _int(value)))
        # Actor-cultivation owns category-104 state separately from the
        # generic task table.  Mirror trusted events into that store so
        # deployment, skill, talent and favour hooks advance both views.
        cultivation = getattr(self.game, "cultivation", None)
        if cultivation is not None:
            try:
                cultivation.record(profile, goal_type, amount=_int(amount), param=param,
                                   value=None if value is None else _int(value))
            except Exception:
                _log.debug("cultivation task hook failed", exc_info=True)
        return changed

    # ------------------------------------------------------------- payloads
    def _task_target_done(self, profile: Mapping[str, Any]) -> list[int]:
        # ``/fci/task/target-done-add/`` stays in LocalSystems; reuse its list.
        systems = getattr(self.game, "systems", None)
        try:
            done = systems._state(profile).get("taskTargetDone", []) if systems is not None else []
        except Exception:
            done = []
        return [_int(value) for value in done] if isinstance(done, list) else []

    def _payload(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        tasks = state["tasks"]
        rows, shown = [], set()
        for task in self._offered(state):
            entry = tasks.get(str(task["id"])) or {}
            if entry.get("rewardGot") and task["deleteAfterReward"]:
                continue
            shown.add(task["id"])
            # endTime 0 = no deadline; WidgetTaskSlot.Update and
            # TaskUtil.CheckTaskTime do arithmetic on it, so it must be present.
            row = {"id": task["id"], "progress": _int(entry.get("progress")),
                   "isDone": bool(entry.get("isDone")), "rewardGot": bool(entry.get("rewardGot")),
                   "endTime": 0}
            # The legacy TaskUtil binding only defines the five fields above
            # for routine/main tasks.  Activity task rows (60000+) use the
            # newer generated shape with category and extra-active-point
            # metadata; keeping the two wire shapes separate avoids breaking
            # older clients while still exposing C9 data to the activity tab.
            if task["id"] >= 60000:
                row.update({"category": task["category"],
                            "activePoint": task["activePoint"],
                            "extraActivePoint": task["extraActivePoint"],
                            "extraActivePointType": task["extraActivePointType"]})
            rows.append(row)
        cultivation = getattr(self.game, 'cultivation', None)
        extra = cultivation.task_snapshot(profile) if cultivation is not None else {}
        return {
            "tasks": rows + extra.get('tasks', []),
            # Make the owned categories authoritative when the client merges.
            "delTasks": [tid for tid in ROUTINE_IDS + MAIN_IDS + ACTIVITY_IDS + RANDOM_IDS if tid not in shown] + extra.get('delTasks', []),
            "disableCategory": list(DISABLED_CATEGORIES),
            "activePoint": _int(state["activePoint"]),
            "activePointRewardGot": [_int(v) for v in state["activePointRewardGot"]],
            "taskTargetDone": self._task_target_done(profile),
            # false keeps the special-category lists (present/theme/...) that
            # other endpoints put in the client's data binding.
            "clearAll": False,
        }

    def snapshot(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """The ``GET /fci/task/`` payload (also the ScTasksChange push shape)."""

        with self._lock:
            state = self._state(profile)
            self._sync(profile, state)
            self._save(profile, state)
            return self._payload(profile, state)

    # ------------------------------------------------------------------ HTTP
    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = _route(path)
        method = (method or "GET").upper()
        if route == "/fci/task":
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            with self._lock:
                state = self._state(profile)
                self._sync(profile, state)
                # The client requests this once per session start
                # (RestifizerManager.DoEnteringRequests), so it is a login.
                self._mark_login(state)
                self._sync(profile, state)
                self._save(profile, state)
                return 200, self._payload(profile, state)
        if route == "/fci/task/activepoint/reward":
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._claim_active_point(profile, _int(_json(body).get("targetActivePoint")))
        if route == "/fci/week-activepoint":
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            with self._lock:
                state = self._state(profile)
                self._sync(profile, state)
                self._save(profile, state)
                return 200, {"weekActivePoint": _int(state["weekActivePoint"]),
                             "rewardGot": [_int(v) for v in state["weekRewardGot"]]}
        if route == "/fci/week-activepoint/reward":
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._claim_week_active_point(profile, _int(_json(body).get("weekActivePoint")))
        # Activity extra-active-point window.  The native client first reads
        # ``.../extra-activepoint/<type>`` and then posts the selected
        # milestone to ``.../extra-activepoint/reward``.
        if route.startswith("/fci/task/extra-activepoint/"):
            suffix = route[len("/fci/task/extra-activepoint/"):].strip("/")
            if suffix.isdigit():
                if method != "GET":
                    return 405, {"error": "method_not_allowed", "message": "GET required"}
                point_type = _int(suffix)
                if point_type not in EXTRA_ACTIVE_POINT_REWARDS:
                    return 404, {"error": "unknown_active_point_type", "message": f"type {point_type} is not configured"}
                return 200, self.extra_active_point_snapshot(profile, point_type)
            if suffix == "reward":
                if method != "POST":
                    return 405, {"error": "method_not_allowed", "message": "POST required"}
                request = _json(body)
                point_type = _int(request.get("extraActivePointType", request.get("extra_active_point_type", 1)), 1)
                target = _int(request.get("extraActivePoint", request.get("extra_active_point", 0)))
                if point_type not in EXTRA_ACTIVE_POINT_REWARDS:
                    return 404, {"error": "unknown_active_point_type", "message": f"type {point_type} is not configured"}
                return self._claim_extra_active_point(profile, point_type, target)
        parts = route.split("/")
        # /fci/task/<id>/reward -> ["", "fci", "task", "<id>", "reward"]
        if len(parts) == 5 and parts[:3] == ["", "fci", "task"] and parts[4] == "reward" and parts[3].isdigit():
            task_id = int(parts[3])
            if task_id not in TASKS:
                return None  # present/theme/scene tasks stay with their own handlers
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._claim_task(profile, task_id)
        return None

    def _claim_task(self, profile: Mapping[str, Any], task_id: int) -> Response:
        task = TASKS[task_id]
        with self._lock:
            state = self._state(profile)
            self._sync(profile, state)
            entry = state["tasks"].get(str(task_id))
            offered = any(item["id"] == task_id for item in self._offered(state))
            if not offered or entry is None:
                return 409, {"error": "task_locked", "message": f"task {task_id} is not available"}
            if entry.get("rewardGot"):
                return 409, {"error": "task_reward_got", "message": f"task {task_id} reward already claimed"}
            if not entry.get("isDone"):
                return 409, {"error": "task_not_done", "message": f"task {task_id} is not complete"}
            entry["rewardGot"] = True
            points = task["activePoint"]
            if points:
                state["activePoint"] = _int(state["activePoint"]) + points
                state["weekActivePoint"] = _int(state["weekActivePoint"]) + points
            extra_points = _int(task.get("extraActivePoint"))
            if extra_points:
                extra = self._extra_entry(state, _int(task.get("extraActivePointType"), 1))
                extra["points"] += extra_points
            # WlfTheme stores this numeric total before updating its progress
            # bar. Omitting it makes the next Lua arithmetic use nil.
            extra_total = self._extra_payload(state, _int(task.get("extraActivePointType"), 1))["extraActivePoint"]
            self._sync(profile, state)  # successors, CompletePnTask, active-point task
            self._save(profile, state)
            changes = grant_rewards(self.game, profile, task["reward"])
            # WidgetTaskSlot only flips rewardGot locally; successors and
            # dependent progress reach the client through the push.
            payload = self._payload(profile, state) if self.notifier is not None else None
        if payload is not None:
            self._notify(profile, payload)
        return 200, {"resChange": changes, "activePoint": _int(state["activePoint"]),
                     "extraActivePoint": extra_total}

    def _claim_active_point(self, profile: Mapping[str, Any], target: int) -> Response:
        rewards = ACTIVE_POINT_REWARDS.get(target)
        if rewards is None:
            return 404, {"error": "unknown_active_point", "message": f"no TaskActivePointReward {target}"}
        with self._lock:
            state = self._state(profile)
            self._sync(profile, state)
            got = state["activePointRewardGot"]
            if target in got:
                return 409, {"error": "active_point_reward_got", "message": f"{target} already claimed"}
            if _int(state["activePoint"]) < target:
                return 409, {"error": "active_point_insufficient", "message": f"{state['activePoint']} < {target}"}
            got.append(target)
            self._save(profile, state)
            changes = grant_rewards(self.game, profile, rewards)
            payload = self._payload(profile, state) if self.notifier is not None else None
        if payload is not None:
            self._notify(profile, payload)
        return 200, {"resChange": changes}

    def _claim_week_active_point(self, profile: Mapping[str, Any], target: int) -> Response:
        rewards = WEEK_ACTIVE_POINT_REWARDS.get(target)
        if rewards is None:
            return 404, {"error": "unknown_week_active_point", "message": f"no WeekActivePointReward {target}"}
        with self._lock:
            state = self._state(profile)
            self._sync(profile, state)
            got = state["weekRewardGot"]
            if target in got:
                return 409, {"error": "week_active_point_reward_got", "message": f"{target} already claimed"}
            if _int(state["weekActivePoint"]) < target:
                return 409, {"error": "week_active_point_insufficient",
                             "message": f"{state['weekActivePoint']} < {target}"}
            got.append(target)
            self._save(profile, state)
            changes = grant_rewards(self.game, profile, rewards)
            return 200, {"resChange": changes, "rewardGot": [_int(v) for v in got]}

    def _claim_extra_active_point(self, profile: Mapping[str, Any], point_type: int, target: int) -> Response:
        milestones = EXTRA_ACTIVE_POINT_REWARDS.get(_int(point_type), {})
        rewards = milestones.get(_int(target))
        if rewards is None:
            return 404, {"error": "unknown_extra_active_point", "message": f"no milestone {target}"}
        with self._lock:
            state = self._state(profile)
            entry = self._extra_entry(state, point_type)
            got = entry["rewardGot"]
            target = _int(target)
            if target in got:
                return 409, {"error": "extra_active_point_reward_got", "message": f"{target} already claimed"}
            if _int(entry["points"]) < target:
                return 409, {"error": "extra_active_point_insufficient",
                             "message": f"{entry['points']} < {target}"}
            got.append(target)
            got.sort()
            self._save(profile, state)
            changes = grant_rewards(self.game, profile, rewards)
            payload = self._extra_payload(state, point_type)
        # ApiTaskExtraActivePointReward.Types.post has the claimed Progress
        # plus resource deltas.  Return the complete list so the native window
        # can update all five cells after one claim.
        return 200, {
            "extraActivePointType": _int(point_type),
            "progress": payload["progress"],
            "resChange": changes,
        }


__all__ = ["LocalTasks", "TASKS", "TaskNotifier", "task_type"]
