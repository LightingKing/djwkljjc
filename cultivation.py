"""Actor cultivation using APK TaskInfo/ActorCultivation reward definitions.

The APK omits the server's random daily-task distribution tables.  The local
policy therefore draws a stable, per-account subset of category-104 tasks,
refreshing ``refreshAfterGot`` tasks once per Beijing reset day.  The draw is
persisted so a relogin cannot reroll it.  Their contribution is capped at the
configured daily ``dailyTaskMaxProgress`` (70), with permanent tasks filling
the remaining progress.  A few task families have no trusted event source in
the recovered server seam; those tasks use the local rule that a validated
battle win counts as one completion when the target actor (or role) is
deployed.  This is an explicit local policy, not extracted data.

Only server hooks and saved actor state advance tasks.  HTTP clients cannot set
progress. ``endTime`` is the completion time, not an invented expiry date.
"""
from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory

import actors
from server.core.reset import policy_for
from tasks import task_type

_TABLES = Path(__file__).resolve().parent / "data" / "client-tables"


def _rows(name):
    return json.loads((_TABLES / (name + ".json")).read_text(encoding="utf-8"))["rows"]


CONFIG = {row["activityId"]: row for row in _rows("ActorCultivation")}
REWARDS = {(row["activityId"], row["progress"]): row for row in _rows("ActorCultivationProgressReward")}
TASKS = {row["id"]: row for row in _rows("TaskInfo") if row.get("category") == 104}
SUPPORTED_GOALS = frozenset({58, 68, 70, 71, 76, 78, 79, 81, 82, 83, 91})
# Goals for which the APK's original event payload is not recoverable.  The
# plan explicitly permits the two cosmetic objectives (skin/comment) to use a
# validated win as a local approximation.  The remaining combat objectives
# are evaluated from FightActiveData statistics below.
BATTLE_APPROX_GOALS = frozenset({74, 80})


def task_available(task):
    """Expose every category-104 task under the local event policy.

    ``SUPPORTED_GOALS`` remains useful documentation for goals with an exact
    server hook.  Missing hooks are intentionally opened as well: their
    progress is advanced by :meth:`LocalCultivation.on_battle_win` using the
    battle-win approximation below.
    """

    return isinstance(task, Mapping) and _int(task.get("category")) == 104

# The original task families are 530xx (1502) and 540xx (1514), while activity
# IDs are 53001 and 53002; these identifiers must not be conflated.
TASK_ACTIVITY = {tid: (53001 if tid < 54000 else 53002) for tid in TASKS}
ROLE_BY_ACTOR = {1502: 2, 1514: 14}
DAILY_DRAW_COUNT = 8
INFO_FIELDS = ("activityId", "actorId", "progress", "progressDaily", "progressReward",
               "progressRewardVip", "vip", "startTime", "endTime", "new", "showEnd")


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class CultivationError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)


class LocalCultivation:
    def __init__(self, game, clock=None):
        self.game = game
        self._clock = clock or game._now
        self.notifier = None  # (profile, {info: [...]}) -> ActorCultivationInfoMessage
        self.task_notifier = None  # (profile, partial ScTasksChange); merge daily/main fields
        with game._lock, closing(game._connect()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS local_cultivation ("
                               "account_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, updated_at INTEGER NOT NULL)")

    def _now(self):
        return int(self._clock())

    @contextmanager
    def _atomic(self, profile):
        previous = copy.deepcopy(dict(profile))
        try:
            with self.game.atomic_mutation():
                yield
        except Exception:
            if isinstance(profile, dict):
                profile.clear()
                profile.update(previous)
            raise

    def _state(self, profile):
        with closing(self.game._connect()) as connection:
            row = connection.execute("SELECT state_json FROM local_cultivation WHERE account_id = ?",
                                     (int(profile["account_id"]),)).fetchone()
        if row:
            state = json.loads(row["state_json"])
            state.setdefault("dailySelection", {})
            return state
        return {"activities": {}, "tasks": {}, "day": None, "dailySelection": {}}

    def _save(self, profile, state):
        with closing(self.game._connect()) as connection, connection:
            connection.execute("INSERT INTO local_cultivation VALUES (?, ?, ?) ON CONFLICT(account_id) "
                               "DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
                               (int(profile["account_id"]), json.dumps(state, separators=(",", ":")), self._now()))

    def _owned(self, profile, system_state=None):
        system_state = system_state if system_state is not None else self.game.systems._state(profile)
        result = {}
        for record in actors.instances(profile, system_state).values():
            for aid, config in CONFIG.items():
                if record["id"] == config["actorId"] and _int(record.get("quality")) >= config["actorQualityMin"]:
                    result.setdefault(aid, []).append(record)
        return result

    @staticmethod
    def _daily_task_level_ok(task, records, notepads):
        """Apply the level gates encoded by the exported task families."""

        goal, params = _int(task.get("goalType")), task.get("taskParam") or []
        if goal == 58:
            return max((_int(record.get("level")) for record in records), default=0) >= _int(task.get("maxProgress"))
        if goal == 83:
            role_id = _int(params[0]) if params else 0
            return _int((notepads.get(role_id) or {}).get("favourLevel")) >= _int(task.get("maxProgress"))
        return True

    def _daily_selection(self, profile, state, aid, records, notepads, day):
        """Return and persist a stable per-account daily draw for ``aid``."""

        selections = state.setdefault("dailySelection", {})
        key = str(aid)
        existing = selections.get(key)
        if isinstance(existing, list) and existing:
            return {_int(value) for value in existing}
        actor_id = _int(CONFIG[aid].get("actorId"))
        candidates = [
            (tid, task) for tid, task in TASKS.items()
            if TASK_ACTIVITY[tid] == aid and task_available(task)
            and task.get("refreshAfterGot")
            and self._daily_task_level_ok(task, records, notepads)
        ]
        seed = int.from_bytes(hashlib.sha256(
            f"{int(profile['account_id'])}:{int(day)}:{int(aid)}:{actor_id}".encode("ascii")
        ).digest()[:8], "big")
        rng = random.Random(seed)
        rng.shuffle(candidates)
        vip = [row for row in candidates if _int(row[1].get("actorCultivationRewardProgress")) >= 2]
        normal = [row for row in candidates if _int(row[1].get("actorCultivationRewardProgress")) < 2]
        chosen = normal[: max(0, DAILY_DRAW_COUNT - 2)] + vip[:2]
        if len(chosen) < DAILY_DRAW_COUNT:
            chosen += [row for row in candidates if row not in chosen][:DAILY_DRAW_COUNT - len(chosen)]
        selected = sorted({_int(tid) for tid, _ in chosen})
        selections[key] = selected
        return set(selected)

    def _sync(self, profile, state):
        system = self.game.systems._state(profile)
        features = getattr(self.game, "profile_features", None)
        achievements = getattr(self.game, "achievements", None)
        collected_novels = achievements._state(profile).get("novels", {}) if achievements is not None else {}
        notepads = {row["roleId"]: row for row in features.role_notepads(profile)} if features is not None else {}
        owned = self._owned(profile, system)
        now = self._now()
        day = policy_for(self.game).day_index(now)
        if state.get("day") is None or day > state["day"]:
            for key in list(state["tasks"]):
                if TASKS.get(_int(key), {}).get("refreshAfterGot"):
                    del state["tasks"][key]
            # progressDaily is the contribution made during the current
            # Beijing day.  It must be reset independently of the lifetime
            # progress counter when the clock rolls over.
            if state.get("day") is not None:
                for info in state.get("activities", {}).values():
                    info["progressDaily"] = 0
            state["dailySelection"] = {}
            state["day"] = day
        for aid, records in owned.items():
            selected_daily = self._daily_selection(profile, state, aid, records, notepads, day)
            info = state["activities"].setdefault(str(aid), {
                "activityId": aid, "actorId": CONFIG[aid]["actorId"], "progress": 0, "progressDaily": 0,
                "progressReward": 0, "progressRewardVip": 0, "vip": False, "startTime": now,
                "endTime": 0, "new": True, "showEnd": False})
            if info["progress"] >= CONFIG[aid]["maxProgress"]:
                continue
            for tid, task in TASKS.items():
                if TASK_ACTIVITY[tid] != aid or not task_available(task):
                    continue
                if task.get("refreshAfterGot") and tid not in selected_daily:
                    continue
                entry = state["tasks"].setdefault(str(tid), {"progress": 0, "rewardGot": False})
                if entry["rewardGot"]:
                    continue
                goal, params = task["goalType"], task.get("taskParam", [])
                value = 0
                if goal == 58:
                    value = max(_int(r.get("level")) for r in records)
                elif goal == 79:
                    value = int(any(r.get("locked") for r in records))
                elif goal == 83:
                    role = notepads.get(params[0], {})
                    value = _int(role.get("favourLevel"))
                elif goal == 81:
                    value = int(bool(system.get("actorLikes", {}).get(str(params[0]), {}).get("liked")))
                elif goal == 68:
                    # CollectionsNovel POST stores the claimed cover here. A
                    # visible novel or a selected default cover is not collected.
                    value = int(_int(collected_novels.get(str(params[0]), {}).get("curRewardNum")) > 0)
                elif goal == 78 and len(params) > 1:
                    value = int(any(t.get("talentId") == params[1] for r in records for t in r.get("talents", [])))
                entry["progress"] = min(task["maxProgress"], max(entry["progress"], value))
        return owned

    @staticmethod
    def _info(info):
        return {key: info[key] for key in INFO_FIELDS}

    def _infos(self, state):
        return [self._info(state["activities"][key]) for key in sorted(state["activities"], key=int)]

    def _task_payload(self, state):
        rows = []
        for tid, task in sorted(TASKS.items()):
            info = state["activities"].get(str(TASK_ACTIVITY[tid]))
            entry = state["tasks"].get(str(tid))
            daily = bool(task.get("refreshAfterGot"))
            selected = set(_int(value) for value in (state.get("dailySelection", {}).get(str(TASK_ACTIVITY[tid]), []) or []))
            if (not task_available(task) or not info or not entry or entry["rewardGot"] or info["progress"] >= CONFIG[info["activityId"]]["maxProgress"]
                    or daily and tid not in selected
                    or daily and info["progressDaily"] >= CONFIG[info["activityId"]]["dailyTaskMaxProgress"]):
                continue
            # TaskUtil keeps a supplied res instead of loading the client table.
            # Include TaskInfo's protobuf defaults: Lua treats nil ~= 0 as true.
            definition = {
                "id": 0, "priority": 0, "goalType": 0, "maxProgress": 0,
                "taskParam": [], "reward": [], "activePoint": 0, "preTaskId": 0,
                "gotoId": 0, "category": 0, "addFollowTask": False, "recordBeforeGot": False,
                "refreshAfterGot": False, "deleteAfterReward": False, "syncByServer": False,
                "life": 0, "series": 0, "checkWhenRefresh": False, "outstanding": False,
                "isActivity": False, "extraActivePointType": 0, "extraActivePoint": 0,
                "actorCultivationActivityId": 0, "actorCultivationRewardProgress": 0,
                "dailyRefresh": False,
                **task, "actorCultivationActivityId": info["activityId"], "dailyRefresh": daily,
            }
            # The APK marks the higher-value (two-point) rows as VIP-only.
            # This is the only stable grouping signal present in the exported
            # table; keep it in the wire descriptor so the client hides those
            # rows until the track is upgraded.
            vip_only = _int(task.get("actorCultivationRewardProgress")) >= 2
            rows.append({"id": tid, "progress": entry["progress"], "isDone": entry["progress"] >= task["maxProgress"],
                         "rewardGot": False, "endTime": 0, "dailyRefresh": daily, "actorCultivationVipOnly": vip_only,
                         "actorCultivationActivityId": info["activityId"],
                         "actorCultivationRewardProgress": task["actorCultivationRewardProgress"], "res": definition})
        shown = {row["id"] for row in rows}
        return {"tasks": rows, "delTasks": sorted(set(TASKS) - shown), "clearAll": False}

    def _notify(self, profile, state):
        if self.notifier:
            payload = {"info": self._infos(state)}
            self.game.after_commit(lambda payload=payload: self.notifier(profile, payload))
        if self.task_notifier:
            payload = self._task_payload(state)
            self.game.after_commit(lambda payload=payload: self.task_notifier(profile, payload))

    def snapshot(self, profile):
        with self._atomic(profile):
            state = self._state(profile)
            self._sync(profile, state)
            self._save(profile, state)
            return self._infos(state)

    def task_snapshot(self, profile):
        with self._atomic(profile):
            state = self._state(profile)
            self._sync(profile, state)
            self._save(profile, state)
            return self._task_payload(state)

    def sync(self, profile):
        """Call after actor acquisition/level/lock/favour/like changes or login."""
        with self._atomic(profile):
            state = self._state(profile)
            before = copy.deepcopy(state)
            self._sync(profile, state)
            self._save(profile, state)
            if state != before:
                self._notify(profile, state)

    def record(self, profile, goal, amount=1, *, param=None, params=None, value=None):
        """Trusted server-event hook, never an HTTP endpoint.

        ``params`` is the exact TaskInfo taskParam sequence; ``param`` is its
        first value for single-parameter goals. A shorter prefix cannot match
        a stricter task (e.g. generic talent changes cannot earn a named talent).
        """
        goal = task_type(goal)
        event_params = list(params) if params is not None else ([] if param is None else [_int(param)])
        with self._atomic(profile):
            state = self._state(profile)
            before = copy.deepcopy(state)
            owned = self._sync(profile, state)
            for tid, task in TASKS.items():
                if not task_available(task) or TASK_ACTIVITY[tid] not in owned or task["goalType"] != goal or task.get("taskParam", []) != event_params:
                    continue
                entry = state["tasks"].get(str(tid))
                if not entry or entry["rewardGot"]:
                    continue
                progress = max(entry["progress"], _int(value)) if value is not None else entry["progress"] + max(0, _int(amount))
                entry["progress"] = min(task["maxProgress"], progress)
            self._save(profile, state)
            if state != before:
                self._notify(profile, state)

    def on_battle_win(self, profile, dungeon_id, *, actor_ids=(), dungeon_type=0, activity_type=0,
                      activity_id=0, stats=None):
        """Count validated successful battle settlement and its persisted team.

        Actor-specific kill/skill/HP objectives require separate verified
        ``record`` events; aggregate client statistics do not establish them.
        """
        with self._atomic(profile):
            state = self._state(profile)
            before = copy.deepcopy(state)
            owned = self._sync(profile, state)
            deployed = {_int(aid) for aid in actor_ids}
            stats = stats if isinstance(stats, Mapping) else {}
            deployed = {_int(aid) for aid in actor_ids}
            # The normalised fields are supplied by tasks.LocalTasks from the
            # protobuf FightActiveData.  Accepting plain lists/dicts here also
            # keeps the hook deterministic for replay and unit tests.
            enemy_dead = list(stats.get("enemyDeadInfo") or stats.get("deadInfo") or [])
            own_dead = list(stats.get("ownDeadInfo") or [])
            enemy_buffs = list(stats.get("enemyBuffKindList") or [])
            own_buffs = list(stats.get("ownBuffKindList") or [])
            hp_list = list(stats.get("recordHpList") or [])
            actor_seats = { _int(k): _int(v) for k, v in (stats.get("actorSeats") or {}).items() }
            skill_counts = {_int(k): _int(v) for k, v in (stats.get("skillCounts") or {}).items()}
            skill_enemy_counts = {}
            for key, value in (stats.get("skillEnemyCounts") or {}).items():
                if isinstance(key, str):
                    key = tuple(_int(part) for part in key.replace(";", ",").split(",") if part.strip())
                elif isinstance(key, (list, tuple)):
                    key = tuple(_int(part) for part in key)
                else:
                    key = (_int(key),)
                skill_enemy_counts[key] = _int(value)

            def actor_hp(actor_id, threshold):
                seat = actor_seats.get(actor_id)
                if seat is None:
                    # In the client recordHpList is indexed by baseSeat (1..6).
                    # For synthetic requests actor_ids are already in seat order.
                    try:
                        seat = list(actor_ids).index(actor_id) + 1
                    except ValueError:
                        seat = 0
                value = hp_list[seat - 1] if 1 <= seat <= len(hp_list) else None
                return value is not None and _int(value) < _int(threshold)

            def killed_by(actor_id):
                # deadInfo.id is generated as killerRuntimeId*10000+victimId
                # by the client combat logger.  Fall back to the count when
                # test/replay payloads provide only a plain list of ids.
                count = 0
                for item in enemy_dead:
                    ident = _int(item.get("id") if isinstance(item, Mapping) else getattr(item, "id", item))
                    if ident // 10000 == actor_id or ident == actor_id:
                        count += 1
                return count or (_int(stats.get("kills")) if len(deployed) == 1 else 0)

            for tid, task in TASKS.items():
                if TASK_ACTIVITY[tid] not in owned or not task_available(task):
                    continue
                params = task.get("taskParam", [])
                if not params or params[0] not in deployed:
                    continue
                goal = task["goalType"]
                actor = _int(params[0]) if params else 0
                matches = (goal == 70 and (len(params) == 1 or params[1] == _int(dungeon_type))
                           or goal == 71 and len(params) > 2 and params[1] == _int(activity_type) and _int(activity_id) in params[2:]
                           or goal == 91 and _int(dungeon_id) in params[1:]
                           or goal in BATTLE_APPROX_GOALS
                           or goal == 73 and actor in deployed and len(params) > 1 and actor_hp(actor, params[1])
                           or goal == 74 and actor in deployed
                           or goal == 84 and actor in deployed and bool(enemy_buffs)
                           or goal == 85 and actor in deployed and bool(own_buffs)
                           or goal == 86 and actor in deployed and len(params) > 1 and bool(enemy_dead)
                           and (_int(stats.get("lastKillSkill"), _int(params[1])) == _int(params[1]))
                           or goal == 87 and actor in deployed and len(params) > 2 and bool(enemy_dead)
                           and _int((enemy_dead[0].get("seat") if isinstance(enemy_dead[0], Mapping) else getattr(enemy_dead[0], "seat", 0))) == _int(params[2])
                           or goal == 88 and actor in deployed and len(params) > 2 and (
                               skill_enemy_counts.get((actor, _int(params[1])), 0) >= _int(params[2])
                               or skill_enemy_counts.get((_int(params[1]),), 0) >= _int(params[2]))
                           or goal == 89 and actor in deployed and len(params) > 1 and skill_counts.get(_int(params[1]), 0) > 0
                           or goal == 90 and actor in deployed and killed_by(actor) > 0)
                entry = state["tasks"].get(str(tid))
                if matches and entry and not entry["rewardGot"]:
                    entry["progress"] = min(task["maxProgress"], entry["progress"] + 1)
            self._save(profile, state)
            if state != before:
                self._notify(profile, state)

    def _require(self, state, owned, activity_id):
        if activity_id not in CONFIG:
            raise CultivationError(404, "unknown_cultivation_activity")
        if activity_id not in owned:
            raise CultivationError(409, "cultivation_actor_not_eligible")
        return state["activities"][str(activity_id)]

    def _task_reward(self, state, owned, tid):
        if tid not in TASKS or not task_available(TASKS[tid]):
            raise CultivationError(404, "unknown_cultivation_task")
        task = TASKS[tid]
        info = self._require(state, owned, TASK_ACTIVITY[tid])
        if task.get("refreshAfterGot"):
            selected = {_int(value) for value in (state.get("dailySelection", {}).get(str(TASK_ACTIVITY[tid]), []) or [])}
            if tid not in selected:
                raise CultivationError(409, "cultivation_task_unavailable")
        entry = state["tasks"].get(str(tid))
        if entry is None:
            raise CultivationError(409, "cultivation_task_unavailable")
        if entry["rewardGot"]:
            raise CultivationError(409, "cultivation_task_already_claimed")
        if entry["progress"] < task["maxProgress"]:
            raise CultivationError(409, "cultivation_task_incomplete")
        config = CONFIG[info["activityId"]]
        progress = min(task["actorCultivationRewardProgress"], config["maxProgress"] - info["progress"])
        if task.get("refreshAfterGot"):
            progress = min(progress, config["dailyTaskMaxProgress"] - info["progressDaily"])
        if progress <= 0:
            raise CultivationError(409, "cultivation_progress_limit")
        entry["rewardGot"] = True
        info["progress"] += progress
        if task.get("refreshAfterGot"):
            info["progressDaily"] += progress
        return self._info(info)

    def _progress_reward(self, profile, state, owned, payload):
        aid, target = _int(payload.get("activityId")), _int(payload.get("progress"))
        if not isinstance(payload.get("vip", False), bool):
            raise CultivationError(400, "invalid_vip_flag")
        vip = payload.get("vip", False)
        info = self._require(state, owned, aid)
        if (aid, target) not in REWARDS:
            raise CultivationError(400, "invalid_cultivation_reward")
        if vip and not info["vip"]:
            raise CultivationError(409, "cultivation_vip_required")
        key = "progressRewardVip" if vip else "progressReward"
        if target <= info[key]:
            raise CultivationError(409, "cultivation_reward_already_claimed")
        if target > info["progress"]:
            raise CultivationError(409, "cultivation_progress_insufficient")
        # The client has one scalar marker per track. Claim every threshold
        # between the previous marker and the selected threshold, so clicking a
        # later reward never loses earlier unclaimed rewards.
        rewards = [reward for (activity, progress), row in sorted(REWARDS.items())
                   if activity == aid and info[key] < progress <= target
                   for reward in row.get("rewardVip" if vip else "reward", [])]
        changes = self.game.mall._grant(profile, rewards)
        info[key] = target
        if not vip and target == CONFIG[aid]["maxProgress"]:
            # The completion artwork replaces both reward lanes in the client.
            # Settle remaining VIP rewards before hiding that lane.
            if info["vip"] and info["progressRewardVip"] < target:
                remaining = [reward for (activity, progress), row in sorted(REWARDS.items())
                             if activity == aid and info["progressRewardVip"] < progress <= target
                             for reward in row.get("rewardVip", [])]
                changes.extend(self.game.mall._grant(profile, remaining))
                info["progressRewardVip"] = target
            info["endTime"] = self._now()
        return {"info": self._info(info), "resChange": changes}

    def handle_http(self, method, path, body, profile):
        route = urlsplit(path).path.rstrip("/").lower()
        task_match = re.fullmatch(r"/fci/task/(\d+)/reward-actor-cultivation", route)
        vip_match = re.fullmatch(r"/fci/actor-cultivation/(\d+)/upgrade-vip", route)
        new_match = re.fullmatch(r"/fci/actor-cultivation/(\d+)/new", route)
        if not route.startswith("/fci/actor-cultivation") and not task_match:
            return None
        if route == "/fci/actor-cultivation" and method == "GET":
            return 200, self.snapshot(profile)
        try:
            payload = json.loads(body) if body else {}
            # Restifizer serializes the client's empty Lua table as JSON null.
            # Only these operations have no request fields to validate.
            if payload is None and (task_match or vip_match or new_match):
                payload = {}
            if not isinstance(payload, dict):
                raise ValueError()
        except (ValueError, TypeError, UnicodeDecodeError):
            # GameRequest uses the native Restifizer serializer; support the
            # recovered protobuf request contracts as well as local JSON tools.
            schema = {"/fci/actor-cultivation/progress-reward": "ApiActorCultivationProgressReward",
                      "/fci/actor-cultivation/showend": "ApiActorCultivationShowEnd"}.get(route)
            if schema is None:
                return 400, {"error": "invalid_request_body"}
            try:
                request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(schema))()
                request.ParseFromString(body.encode("utf-8") if isinstance(body, str) else bytes(body))
                payload = json_format.MessageToDict(request)
            except Exception:
                return 400, {"error": "invalid_request_body"}
        try:
            with self._atomic(profile):
                state = self._state(profile)
                owned = self._sync(profile, state)
                if task_match and method == "POST":
                    result = self._task_reward(state, owned, int(task_match[1]))
                elif vip_match and method == "POST":
                    info = self._require(state, owned, int(vip_match[1]))
                    if info["vip"]:
                        raise CultivationError(409, "cultivation_vip_already_active")
                    if info["endTime"]:
                        raise CultivationError(409, "cultivation_already_completed")
                    changes = []
                    for cost in CONFIG[info["activityId"]]["resActiveVip"]:
                        changes.extend(self.game.mall._spend(profile, cost["type"], cost["count"]))
                    info["vip"] = True
                    result = {"info": self._info(info), "resChange": changes}
                elif route == "/fci/actor-cultivation/progress-reward" and method == "POST":
                    result = self._progress_reward(profile, state, owned, payload)
                elif new_match and method == "PATCH":
                    info = self._require(state, owned, int(new_match[1]))
                    info["new"] = False
                    result = self._info(info)
                elif route == "/fci/actor-cultivation/showend" and method == "PATCH":
                    aid = next((aid for aid, cfg in CONFIG.items() if cfg["actorId"] == _int(payload.get("actorId"))), 0)
                    info = self._require(state, owned, aid)
                    if not info["endTime"]:
                        raise CultivationError(409, "cultivation_not_completed")
                    info["showEnd"] = True
                    result = self._info(info)
                else:
                    raise CultivationError(405 if route == "/fci/actor-cultivation" or task_match or vip_match or new_match
                                           or route.endswith(("/showend", "/progress-reward")) else 404, "unsupported_cultivation_operation")
                self._save(profile, state)
                self._notify(profile, state)
                return 200, result
        except CultivationError as error:
            return error.status, {"error": error.code}
        except Exception as error:
            # Mall's errors retain their status/code, while unexpected failures
            # propagate after rollback for the server's normal error handling.
            from mall import MallError
            if isinstance(error, MallError):
                return error.status, {"error": error.code}
            raise
