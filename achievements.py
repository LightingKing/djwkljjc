"""Local 历程 (Journey) system: achievements and the 图鉴 collections.

Achievements (``Window/Achievement``, ``Window/AchievementDetail``)
  * ``GET  /fci/achievement/``            -> plain JSON list of
    ``{id, progress, isDone, rewardGot, doneTime}`` (ApiAchievement.result);
    ``HandleAchievementData`` walks it with ``ipairs`` and looks every id up
    in AchievementInfo, so only real ids are sent.
  * ``POST /fci/achievement/<id>/reward/`` -> ``{resChange, newAchievements}``
    (ApiAchievementReward.result); AchievementDetail.GetReward feeds
    ``resChange`` to DBH.ResChange/WU.ShowRewards and stores
    ``newAchievements`` as the whole ``fci/achievement/`` list again.

  Rows come from the client's AchievementInfo table (123 rows).  A row is
  listed when its ``preId`` chain predecessor is done (chains such as
  101 -> 110 share one counter) and, for ``hide`` rows, only once it is done.
  Rewards are the row's own STResource list: ResAchievementPoint (21) goes to
  the resource ledger, which is what ``fci/resource/ResAchievementPoint_0`` and
  the AchievementPointTarget trophy (``AU.GetAchievementInfo``) are computed
  from on the client.  AchievementPointTarget itself carries no rewards; the
  trophy/level for a point total is exposed as :func:`trophy`.  ResMedal (8)
  rewards are written to LocalSystems ``state["medals"]`` (the ``fci/medal/``
  store) and ResActorSkin (29) to ``state["unlockedSkins"]``; everything else
  goes through :func:`grants.grant_rewards`.

  Progress (AchievementType enum, descriptor_set.pb) is derived from real
  player state on every :meth:`LocalAchievements.refresh`:
    1  BalanceP0DungeonMTimes       won (``balanced``) local_dungeons rows
    4  ActorP0StarP1                character instances with star >= P1
    6  ActorP0TotalSkillLevelP1     instances whose skill levels sum >= P1
    10 SuitEquipType                distinct suit pieces (EquipSuitAttr) collected
    11 EquipQualityP0               equipment instances of quality >= P0 obtained
    12 GachaP0 / 60 GachaP0CountM   draws in the gacha pools listed in params
    22 TowerP0FloorM                won tower floors
    27 PowerCostM                   stage energy (Dungeon stage cost) of started runs
    64 ActorSkinQualityP0MTimes     unlocked skins of ActorSkin quality >= P0
    66 ActorPnQualityP1M            listed characters owned at quality >= P0
    71 BalanceP0DungeonMTimesActorPN wins with the listed characters in the team
  Goals with no local source (talents, link skills, deploy missions, rerolls,
  battle-only feats) progress through :meth:`LocalAchievements.record`, the
  same call shape as ``LocalTasks.record``.  Stored progress never decreases,
  so consuming a character or decomposing equipment never undoes progress.

Collections (``Window/Portal``, ``Common/NotepadUtil``)
  * ``GET fci/CollectionsActor``        -> ``{actors: [{id, addTime, ...}]}``
  * ``GET fci/CollectionsEquip``        -> ``{equips: [{equipId}]}``
  * ``GET fci/CollectionsNovel``        -> ``{novels: [NovelInfo]}``
  * ``GET fci/CollectionsNovel/<id>``   -> ``{novels, novelDetail: {actorInfo}}``
  * ``POST fci/CollectionsNovel/<id>``  -> ``{resChange, novels}`` (claim cover)
  * ``PUT fci/CollectionsNovel/<id>``   -> ``{novels}`` (body ``{newCover}``)
  * ``GET fci/NovelCover/<id>``         -> ``{coverInfo: [{coverId}]}``
  The collected sets are every character id / equipment id the player has
  ever owned (current state merged into a persisted history).  A novel is
  unlocked once any of its (open) characters is collected; its score is the
  sum of those characters' best star, compared with CollectionsNovelReward
  ``score`` exactly as CollectionsNovelDetail.SetViewActors shows it.

Live updates: :meth:`refresh` returns ids that became done and calls
``notifier(profile, ids)`` when one is set (wire it to the ``AchievementDone``
push; the client then shows Window_AchievementUnlock and the red mark).
"""

from __future__ import annotations

from server.core.reset import DEFAULT_RESET_POLICY, policy_for

import json
import logging
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlsplit

try:  # package import (tests/tools) and flat import (server.py) both work
    from . import actors as actor_store
    from .grants import grant_rewards
except ImportError:  # pragma: no cover - flat module layout
    import actors as actor_store
    from grants import grant_rewards


Body = bytes | bytearray | str | None
Response = tuple[int, Any]
AchievementNotifier = Callable[[Mapping[str, Any], list], Any]

_log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent
_TABLE_DIR = _ROOT / "data" / "client-tables"
_DESCRIPTOR = _ROOT / "data" / "descriptor_set.pb"

# ResourceType (common.proto).
RES_MEDAL = 8
RES_ACHIEVEMENT_POINT = 21
RES_NOVEL_COVER = 22
RES_ACTOR_SKIN = 29

# AchievementType values with a derived source.
BALANCE_P0_DUNGEON_M_TIMES = 1
ACTOR_P0_STAR_P1 = 4
ACTOR_P0_TOTAL_SKILL_LEVEL_P1 = 6
SUIT_EQUIP_TYPE = 10
EQUIP_QUALITY_P0 = 11
GACHA_P0 = 12
TOWER_P0_FLOOR_M = 22
POWER_COST_M = 27
GACHA_P0_COUNT_M = 60
ACTOR_SKIN_QUALITY_P0_M_TIMES = 64
ACTOR_PN_QUALITY_P1_M = 66
BALANCE_P0_DUNGEON_M_TIMES_ACTOR_PN = 71

# Actor openTime strings are server (Beijing) wall-clock times.


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _route(path: str) -> str:
    return (urlsplit(path or "").path or "/").rstrip("/").lower() or "/"


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


def _resource(raw: Any) -> Optional[dict[str, int]]:
    if not isinstance(raw, Mapping) or _int(raw.get("count")) <= 0:
        return None
    return {"type": _int(raw.get("type")), "id": _int(raw.get("id")),
            "count": _int(raw.get("count")), "param": _int(raw.get("param"))}


def _achievement_def(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": _int(row.get("id")),
        "preId": _int(row.get("preId")),
        "hide": bool(_int(row.get("hide"))),
        "unlockLevel": _int(row.get("unlockLevel")),
        "goalType": _int(row.get("goalType")),
        "maxProgress": max(1, _int(row.get("maxProgress"), 1)),
        "param": [_int(value) for value in (row.get("param") or [])],
        "reward": [r for r in (_resource(item) for item in (row.get("reward") or [])) if r],
    }


ACHIEVEMENTS: dict[int, dict[str, Any]] = {
    ach["id"]: ach for ach in (_achievement_def(row) for row in _rows("AchievementInfo")) if ach["id"]
}
POINT_TARGETS: list[dict[str, int]] = sorted(
    ({"trophy": _int(row.get("trophy")), "level": _int(row.get("level")),
      "targetAchievementPoint": _int(row.get("targetAchievementPoint"))} for row in _rows("AchievementPointTarget")),
    key=lambda row: row["targetAchievementPoint"],
)
# NotepadUtil.CreateNovelsData lists Novel rows with isNovel and active.
NOVELS: dict[int, dict[str, Any]] = {
    _int(row["id"]): {"actors": [_int(a) for a in row.get("actors") or []],
                      "covers": [_int(c) for c in row.get("covers") or []]}
    for row in _rows("Novel") if row.get("id") and row.get("isNovel") and row.get("active")
}
NOVEL_REWARDS: dict[int, list[dict[str, Any]]] = {
    _int(row["novelId"]): [
        {"score": _int(item.get("score")), "reward": _resource(item.get("reward"))}
        for item in row.get("rewards") or [] if isinstance(item, Mapping)
    ]
    for row in _rows("CollectionsNovelReward") if row.get("novelId")
}
EQUIP_QUALITY: dict[int, int] = {
    _int(row["equipId"]): _int(row.get("quality"), 1) for row in _rows("EquipInfo") if row.get("equipId")
}
SUIT_EQUIPS: frozenset[int] = frozenset(
    _int(equip_id) for row in _rows("EquipSuitAttr") for equip_id in row.get("equipId") or [] if _int(equip_id)
)
SKIN_QUALITY: dict[int, int] = {_int(row["id"]): _int(row.get("quality"), 1) for row in _rows("ActorSkin") if row.get("id")}
SKIN_ACTORS: dict[int, list[int]] = {
    _int(row["id"]): [_int(a) for a in row.get("actorIds") or []] for row in _rows("ActorSkin") if row.get("id")
}
ACTOR_OPEN_TIME: dict[int, str] = {_int(row["id"]): str(row.get("openTime") or "") for row in _rows("ActorConfig") if row.get("id")}

_TYPE_NAMES: Optional[dict[str, int]] = None


def achievement_type(goal: int | str) -> int:
    """Resolve an AchievementType number or name ("PowerCostM", "AchievementType_PowerCostM")."""

    global _TYPE_NAMES
    if isinstance(goal, int):
        return goal
    text = str(goal).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    if _TYPE_NAMES is None:
        names: dict[str, int] = {}
        try:
            from google.protobuf import descriptor_pb2

            files = descriptor_pb2.FileDescriptorSet()
            files.ParseFromString(_DESCRIPTOR.read_bytes())
            for proto in files.file:
                for enum in list(proto.enum_type) + [e for m in proto.message_type for e in m.enum_type]:
                    if enum.name == "AchievementType":
                        for value in enum.value:
                            names[value.name.lower()] = value.number
        except Exception:  # pragma: no cover - descriptor missing/damaged
            names = {}
        _TYPE_NAMES = names
    key = text.lower()
    return _TYPE_NAMES.get(key, _TYPE_NAMES.get("achievementtype_" + key, 0))


def trophy(point: int) -> dict[str, int]:
    """AchievementPointTarget row for a point total (AU.GetAchievementInfo)."""

    current = POINT_TARGETS[0] if POINT_TARGETS else {"trophy": 1, "level": 1, "targetAchievementPoint": 0}
    for row in POINT_TARGETS:
        if max(0, _int(point)) >= row["targetAchievementPoint"]:
            current = row
    return dict(current)


def _stage_energy_cost() -> Mapping[int, int]:
    try:
        from .dungeon import STAGE_ENERGY_COST
    except ImportError:
        try:
            from dungeon import STAGE_ENERGY_COST
        except ImportError:  # pragma: no cover
            return {}
    return STAGE_ENERGY_COST


def _parse_open_time(text: str, policy=DEFAULT_RESET_POLICY) -> Optional[int]:
    try:
        return policy.parse_config_datetime(text)
    except (ValueError, TypeError):
        return None


class LocalAchievements:
    """Per-account achievement + collection state stored in ``local_achievements``."""

    def __init__(self, game: Any, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or getattr(game, "_now", None)
        # Injected by the server (AchievementDone push); None = no push.
        self.notifier: Optional[AchievementNotifier] = None
        with self._lock, closing(game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_achievements (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    # ------------------------------------------------------------- storage
    def _now(self) -> int:
        try:
            return int(self._clock())
        except Exception:
            import time

            return int(time.time())

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "achievements": {},     # str(id) -> {progress, isDone, rewardGot, doneTime}
            "counters": {},         # "goal:param" -> recorded amount
            "actors": {},           # str(actorId) -> {addTime, star}
            "equips": {},           # str(equipId) -> addTime
            "equipSeen": {},        # str(equip uid) -> equipId
            "novels": {},           # str(novelId) -> {curRewardNum, curCover}
        }

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM local_achievements WHERE account_id = ?", (account_id,)
            ).fetchone()
            state: Any = {}
            if row is not None:
                try:
                    state = json.loads(row["state_json"] or "{}")
                except (TypeError, ValueError):
                    state = {}
            else:
                connection.execute(
                    "INSERT OR IGNORE INTO local_achievements(account_id, state_json, updated_at) VALUES (?, ?, ?)",
                    (account_id, json.dumps(self._default_state()), self._now()),
                )
        if not isinstance(state, dict):
            state = {}
        for key, value in self._default_state().items():
            if not isinstance(state.get(key), dict):
                state[key] = value
        return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_achievements SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    def _systems_state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        systems = getattr(self.game, "systems", None)
        if systems is None:
            return {}
        try:
            state = systems._state(profile)
        except Exception:
            return {}
        return state if isinstance(state, dict) else {}

    def _dungeon_rows(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        queries = (
            "SELECT dungeon_id, status, mode, floor, actor_uids_json FROM local_dungeons WHERE account_id = ?",
            "SELECT dungeon_id, status, actor_uids_json FROM local_dungeons WHERE account_id = ?",
        )
        for query in queries:
            try:
                with closing(self.game._connect()) as connection:
                    return [dict(row) for row in connection.execute(query, (int(profile["account_id"]),)).fetchall()]
            except Exception:  # table absent (fresh store) or older schema
                continue
        return []

    # ------------------------------------------------------------ snapshot
    def _actor_open(self, actor_id: int, now: int) -> bool:
        opened = _parse_open_time(ACTOR_OPEN_TIME.get(int(actor_id), ""), policy_for(self.game))
        return opened is None or opened <= now

    def _sync_collections(self, profile: Mapping[str, Any], state: dict[str, Any],
                          systems_state: dict[str, Any]) -> list[dict[str, Any]]:
        """Merge currently owned characters/equipment into the collected history."""

        now = self._now()
        try:
            instances = actor_store.ordered(profile, systems_state) if systems_state else []
        except Exception:
            instances = []
        collected = state["actors"]
        for record in instances:
            actor_id = _int(record.get("id"))
            if actor_id < actor_store.MIN_ACTOR_ID:
                continue
            entry = collected.setdefault(str(actor_id), {"addTime": now, "star": 0})
            entry["star"] = max(_int(entry.get("star")), _int(record.get("star"), 1))
        equips = systems_state.get("equips") if isinstance(systems_state.get("equips"), Mapping) else {}
        for key, record in equips.items():
            if not isinstance(record, Mapping):
                continue
            equip_id = _int(record.get("id", record.get("equipId")))
            if equip_id not in EQUIP_QUALITY:
                continue
            uid = _int(record.get("uid"), _int(key))
            state["equipSeen"].setdefault(str(uid), equip_id)
            state["equips"].setdefault(str(equip_id), now)
        return instances

    def _counter(self, state: Mapping[str, Any], goal: int, param: int) -> int:
        return _int(state["counters"].get(f"{goal}:{param}"))

    def _derived(self, ach: Mapping[str, Any], ctx: dict[str, Any]) -> Optional[int]:
        goal, params = ach["goalType"], ach["param"]
        p0 = params[0] if params else 0
        p1 = params[1] if len(params) > 1 else 0
        if goal in (BALANCE_P0_DUNGEON_M_TIMES, BALANCE_P0_DUNGEON_M_TIMES_ACTOR_PN, TOWER_P0_FLOOR_M, POWER_COST_M):
            if "dungeons" not in ctx:  # one query per refresh, not per row
                ctx["dungeons"] = self._dungeon_rows(ctx["profile"])
            rows = ctx["dungeons"]
            won = [row for row in rows if row.get("status") == "balanced"]
            if goal == BALANCE_P0_DUNGEON_M_TIMES:
                return sum(1 for row in won if not p0 or _int(row.get("dungeon_id")) == p0)
            if goal == TOWER_P0_FLOOR_M:
                return sum(1 for row in won if row.get("mode") == "tower" and (not p0 or _int(row.get("floor")) >= p0))
            if goal == POWER_COST_M:
                cost = _stage_energy_cost()
                return sum(_int(cost.get(_int(row.get("dungeon_id")))) for row in rows)
            needed = set(params[1:])
            count = 0
            for row in won:
                if p0 and _int(row.get("dungeon_id")) != p0:
                    continue
                try:
                    uids = [_int(uid) for uid in json.loads(row.get("actor_uids_json") or "[]")]
                except (TypeError, ValueError):
                    continue
                team = {ctx["byUid"][uid] for uid in uids if uid in ctx["byUid"]}
                count += 1 if needed <= team else 0
            return count
        instances = ctx["instances"]
        if goal == ACTOR_P0_STAR_P1:
            return sum(1 for r in instances if (not p0 or _int(r.get("id")) == p0) and _int(r.get("star"), 1) >= p1)
        if goal == ACTOR_P0_TOTAL_SKILL_LEVEL_P1:
            return sum(1 for r in instances if (not p0 or _int(r.get("id")) == p0) and self._skill_total(r) >= p1)
        if goal == ACTOR_PN_QUALITY_P1_M:
            listed = set(params[1:])
            return len({_int(r.get("id")) for r in instances
                        if _int(r.get("id")) in listed and _int(r.get("quality"), 1) >= p0})
        state = ctx["state"]
        if goal == SUIT_EQUIP_TYPE:
            return sum(1 for key in state["equips"] if _int(key) in SUIT_EQUIPS)
        if goal == EQUIP_QUALITY_P0:
            return sum(1 for equip_id in state["equipSeen"].values() if EQUIP_QUALITY.get(_int(equip_id), 0) >= p0)
        if goal in (GACHA_P0, GACHA_P0_COUNT_M):
            pools = ctx["systems"].get("gachaPools") if isinstance(ctx["systems"].get("gachaPools"), Mapping) else {}
            wanted = set(params)
            return sum(_int((pool or {}).get("total")) for key, pool in pools.items()
                       if isinstance(pool, Mapping) and (not wanted or _int(key) in wanted))
        if goal == ACTOR_SKIN_QUALITY_P0_M_TIMES:
            unlocked = ctx["systems"].get("unlockedSkins") if isinstance(ctx["systems"].get("unlockedSkins"), Mapping) else {}
            skins = {_int(s) for values in unlocked.values() if isinstance(values, list) for s in values}
            return sum(1 for skin in skins if SKIN_QUALITY.get(skin, 0) >= p0)
        return None

    @staticmethod
    def _skill_total(record: Mapping[str, Any]) -> int:
        stored = record.get("skills") if isinstance(record.get("skills"), Mapping) else {}
        total = 0
        for skill_id in actor_store.skill_ids(_int(record.get("id")), _int(record.get("star"), 1)):
            value = stored.get(str(skill_id))
            level = _int(value.get("level"), 1) if isinstance(value, Mapping) else _int(value, 1)
            total += max(1, level)
        return total

    def _update(self, profile: Mapping[str, Any], state: dict[str, Any]) -> list[int]:
        """Recompute every row; returns ids that just became done."""

        systems_state = self._systems_state(profile)
        instances = self._sync_collections(profile, state, systems_state)
        ctx: dict[str, Any] = {
            "profile": profile, "state": state, "systems": systems_state, "instances": instances,
            "byUid": {_int(r.get("uid")): _int(r.get("id")) for r in instances},
        }
        now = self._now()
        done: list[int] = []
        for ach_id, ach in sorted(ACHIEVEMENTS.items()):
            entry = state["achievements"].setdefault(
                str(ach_id), {"progress": 0, "isDone": False, "rewardGot": False, "doneTime": 0})
            if entry.get("isDone"):
                entry["progress"] = ach["maxProgress"]
                continue
            p0 = ach["param"][0] if ach["param"] else 0
            value = max(_int(self._derived(ach, ctx)), self._counter(state, ach["goalType"], p0))
            progress = min(ach["maxProgress"], max(_int(entry.get("progress")), value))
            entry["progress"] = progress
            if progress >= ach["maxProgress"]:
                entry["isDone"] = True
                entry["doneTime"] = now
                done.append(ach_id)
        return done

    def _visible(self, profile: Mapping[str, Any], state: Mapping[str, Any], ach: Mapping[str, Any]) -> bool:
        entries = state["achievements"]
        entry = entries.get(str(ach["id"])) or {}
        if ach["hide"] and not entry.get("isDone"):
            return False
        if ach["unlockLevel"] and _int(profile.get("level"), 1) < ach["unlockLevel"] and not entry.get("isDone"):
            return False
        pre = ach["preId"]
        return not pre or pre not in ACHIEVEMENTS or bool((entries.get(str(pre)) or {}).get("isDone"))

    def _payload(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for ach_id, ach in sorted(ACHIEVEMENTS.items()):
            if not self._visible(profile, state, ach):
                continue
            entry = state["achievements"].get(str(ach_id)) or {}
            rows.append({"id": ach_id, "progress": _int(entry.get("progress")), "isDone": bool(entry.get("isDone")),
                         "rewardGot": bool(entry.get("rewardGot")), "doneTime": _int(entry.get("doneTime"))})
        return rows

    def _can_reward(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
        return any(row["isDone"] and not row["rewardGot"] for row in self._payload(profile, state))

    def _notify(self, profile: Mapping[str, Any], done: list[int]) -> None:
        notifier = self.notifier
        if notifier is None or not done:
            return
        try:
            notifier(profile, list(done))
        except Exception:  # an offline/broken push client must not fail gameplay
            _log.debug("achievement push notification failed", exc_info=True)

    # ------------------------------------------------------------ public API
    def refresh(self, profile: Mapping[str, Any]) -> list[int]:
        """Recompute progress from current state; returns ids that just became done."""

        with self._lock:
            state = self._state(profile)
            done = self._update(profile, state)
            self._save(profile, state)
        self._notify(profile, done)
        return done

    def record(self, profile: Mapping[str, Any], goal: int | str, amount: int = 1, *,
               param: Optional[int] = None, value: Optional[int] = None) -> list[int]:
        """Progress for goals without a derived source, e.g. ``record(p, "DeployDoneM")``.

        ``amount`` adds to the goal's counter; ``value`` raises it to at least
        ``value``.  Rows whose first param is 0 read the generic counter, rows
        with a specific first param read the counter recorded with that
        ``param``.  Returns ids that became done.
        """

        goal_type = achievement_type(goal)
        if not goal_type:
            return []
        keys = [f"{goal_type}:0"] + ([f"{goal_type}:{_int(param)}"] if param is not None and _int(param) else [])
        with self._lock:
            state = self._state(profile)
            counters = state["counters"]
            for key in keys:
                current = _int(counters.get(key))
                counters[key] = max(current, _int(value)) if value is not None else current + max(0, _int(amount))
            done = self._update(profile, state)
            self._save(profile, state)
        self._notify(profile, done)
        return done

    def can_reward(self, profile: Mapping[str, Any]) -> bool:
        with self._lock:
            state = self._state(profile)
            done = self._update(profile, state)
            self._save(profile, state)
            result = self._can_reward(profile, state)
        self._notify(profile, done)
        return result

    def redpoint_fields(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Fields to merge into ``GET fci/redpoint/data`` (RestifizerManager reads
        ``achievementSummaryGet.canReward`` for the Journey/Achievement mark)."""

        return {"achievementSummaryGet": {"canReward": self.can_reward(profile)}}

    @staticmethod
    def achievement_point(profile: Mapping[str, Any]) -> int:
        """Current ResAchievementPoint balance (for player detail / myspace)."""

        for item in profile.get("resources") or []:
            if isinstance(item, Mapping) and _int(item.get("type")) == RES_ACHIEVEMENT_POINT and not _int(item.get("id")):
                return _int(item.get("count"))
        return 0

    # ------------------------------------------------------------- rewards
    def _grant(self, profile: Mapping[str, Any], rewards: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        rewards = [dict(r) for r in rewards if r]
        special = [r for r in rewards if r["type"] in (RES_MEDAL, RES_ACTOR_SKIN)]
        changes = grant_rewards(self.game, profile, [r for r in rewards if r not in special])
        if special and getattr(self.game, "systems", None) is not None:
            systems_state = self.game.systems._state(profile)
            now = self._now()
            for reward in special:
                if reward["type"] == RES_MEDAL:
                    systems_state.setdefault("medals", {})[str(reward["id"])] = {
                        "id": reward["id"], "gotTime": now, "isNew": True}
                else:
                    unlocked = systems_state.setdefault("unlockedSkins", {})
                    for actor_id in SKIN_ACTORS.get(reward["id"], []):
                        owned = unlocked.setdefault(str(actor_id), [])
                        if reward["id"] not in owned:
                            owned.append(reward["id"])
                            owned.sort()
                changes.append({"baseRes": {"type": reward["type"], "id": reward["id"], "param": 0,
                                            "count": 1, "countDelta": 1}})
            self.game.systems._save(profile, systems_state)
            notify = getattr(self.game, "base_push_notifier", None)
            if notify is not None:
                for reward in special:
                    if reward["type"] == RES_MEDAL:
                        self.game.after_commit(lambda medal_id=reward["id"]: notify(profile, 49, (medal_id,)))
        return changes

    # ----------------------------------------------------------- novels
    def _owned_covers(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> set[int]:
        covers = {_int(item.get("id")) for item in profile.get("resources") or []
                  if isinstance(item, Mapping) and _int(item.get("type")) == RES_NOVEL_COVER and _int(item.get("count")) > 0}
        for key, entry in state["novels"].items():
            for reward in NOVEL_REWARDS.get(_int(key), [])[:_int((entry or {}).get("curRewardNum"))]:
                if reward["reward"] and reward["reward"]["type"] == RES_NOVEL_COVER:
                    covers.add(reward["reward"]["id"])
        return covers

    def _novel_actors(self, novel_id: int, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        now = self._now()
        result = []
        for actor_id in NOVELS.get(novel_id, {}).get("actors", []):
            entry = state["actors"].get(str(actor_id))
            if entry is not None and self._actor_open(actor_id, now):
                result.append({"actorId": actor_id, "star": _int(entry.get("star")), "time": _int(entry.get("addTime"))})
        return result

    def _novel_info(self, novel_id: int, state: Mapping[str, Any]) -> dict[str, Any]:
        entry = state["novels"].get(str(novel_id)) or {}
        claimed = _int(entry.get("curRewardNum"))
        rewards = NOVEL_REWARDS.get(novel_id, [])
        actors = self._novel_actors(novel_id, state)
        score = sum(actor["star"] for actor in actors)
        can = bool(actors) and claimed < len(rewards) and score >= rewards[claimed]["score"]
        covers = NOVELS.get(novel_id, {}).get("covers") or [novel_id]
        return {"novelId": novel_id, "curRewardNum": claimed, "canGetReward": can,
                "curCover": _int(entry.get("curCover")) or covers[0]}

    def _unlocked_novels(self, state: Mapping[str, Any]) -> list[int]:
        return [novel_id for novel_id in sorted(NOVELS) if self._novel_actors(novel_id, state)]

    def _novel_http(self, method: str, novel_id: Optional[int], body: Body,
                    profile: Mapping[str, Any]) -> tuple[Response, list[int]]:
        with self._lock:
            state = self._state(profile)
            done = self._update(profile, state)
            try:
                return self._novel_route(method, novel_id, body, profile, state), done
            finally:
                self._save(profile, state)

    def _novel_route(self, method: str, novel_id: Optional[int], body: Body,
                     profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        if novel_id is None:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            return 200, {"novels": [self._novel_info(n, state) for n in self._unlocked_novels(state)]}
        if novel_id not in NOVELS:
            return 404, {"error": "unknown_novel", "novelId": novel_id}
        if method == "GET":
            actors = self._novel_actors(novel_id, state)
            return 200, {"novels": [self._novel_info(novel_id, state)] if actors else [],
                         "novelDetail": {"actorInfo": actors}}
        if method == "POST":
            info = self._novel_info(novel_id, state)
            if not info["canGetReward"]:
                return 409, {"error": "novel_reward_unavailable", "novelId": novel_id}
            reward = NOVEL_REWARDS[novel_id][info["curRewardNum"]]["reward"]
            changes = self._grant(profile, [reward] if reward else [])
            entry = state["novels"].setdefault(str(novel_id), {})
            entry["curRewardNum"] = info["curRewardNum"] + 1
            if reward and reward["type"] == RES_NOVEL_COVER:
                entry["curCover"] = reward["id"]
            return 200, {"resChange": changes, "novels": [self._novel_info(novel_id, state)]}
        if method in ("PUT", "PATCH"):
            cover = _int(_json(body).get("newCover"))
            covers = NOVELS[novel_id]["covers"] or [novel_id]
            if cover not in covers or (cover != novel_id and cover not in self._owned_covers(profile, state)):
                return 409, {"error": "novel_cover_locked", "cover": cover}
            state["novels"].setdefault(str(novel_id), {})["curCover"] = cover
            return 200, {"novels": [self._novel_info(novel_id, state)]}
        return 405, {"error": "method_not_allowed", "message": "GET, POST or PUT required"}

    # --------------------------------------------------------------- HTTP
    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = _route(path)
        method = (method or "GET").upper()
        if route == "/fci/achievement":
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            with self._lock:
                state = self._state(profile)
                done = self._update(profile, state)
                self._save(profile, state)
                payload = self._payload(profile, state)
            self._notify(profile, done)
            return 200, payload
        match = re.fullmatch(r"/fci/achievement/(\d+)/reward", route)
        if match:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._claim(int(match.group(1)), profile)
        if route in ("/fci/collectionsactor", "/fci/collectionsequip"):
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            with self._lock:
                state = self._state(profile)
                done = self._update(profile, state)
                self._save(profile, state)
            self._notify(profile, done)
            if route == "/fci/collectionsactor":
                return 200, {"actors": [
                    {"id": _int(key), "addTime": _int(entry.get("addTime")), "activePoster": False, "canReward": False}
                    for key, entry in sorted(state["actors"].items(), key=lambda kv: _int(kv[0]))]}
            return 200, {"equips": [{"equipId": _int(key)} for key in sorted(state["equips"], key=_int)]}
        match = re.fullmatch(r"/fci/collectionsnovel(?:/(\d+))?", route)
        if match:
            result, done = self._novel_http(method, int(match.group(1)) if match.group(1) else None, body, profile)
            self._notify(profile, done)
            return result
        match = re.fullmatch(r"/fci/novelcover(?:/(\d+))?", route)
        if match:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            with self._lock:
                owned = self._owned_covers(profile, self._state(profile))
            if match.group(1):
                novel_id = int(match.group(1))
                owned &= set(NOVELS.get(novel_id, {}).get("covers") or [novel_id])
            return 200, {"coverInfo": [{"coverId": cover} for cover in sorted(owned)]}
        return None

    def _claim(self, ach_id: int, profile: Mapping[str, Any]) -> Response:
        ach = ACHIEVEMENTS.get(ach_id)
        if ach is None:
            return 404, {"error": "unknown_achievement", "id": ach_id}
        with self._lock:
            state = self._state(profile)
            done = self._update(profile, state)
            entry = state["achievements"][str(ach_id)]
            if entry.get("rewardGot"):
                self._save(profile, state)
                result: Response = (409, {"error": "achievement_reward_already_got", "id": ach_id})
            elif not entry.get("isDone") or not self._visible(profile, state, ach):
                self._save(profile, state)
                result = (409, {"error": "achievement_not_done", "id": ach_id})
            else:
                entry["rewardGot"] = True
                self._save(profile, state)
                changes = self._grant(profile, ach["reward"])
                result = (200, {"resChange": changes, "newAchievements": self._payload(profile, state)})
        self._notify(profile, done)
        return result


__all__ = ["LocalAchievements", "achievement_type", "trophy", "ACHIEVEMENTS", "NOVELS", "NOVEL_REWARDS"]
