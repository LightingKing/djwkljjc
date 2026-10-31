"""Challenge-menu activity modes reached from Window_Activity (client 2.0.4).

Material dungeons (ActivityType 2 CollectionDungeon, DungeonType 5
WeekActivity), the gold dungeon (17 / DungeonType 9), the tower (3 /
DungeonType 4), deploy missions (10) and triggered events (31 / DungeonType
13).  Routes and reply fields come from the decompiled client Lua; replies
are plain camelCase JSON shaped like what each callback reads.  Any reply
with an ``error`` key makes the client show a dialog (and may reset it), so
only requests the client UI itself prevents are refused.

Client evidence (analysis/lua-decompiled):
  * window__activityactorgroup: POST fci/activity-dungeon/ wireformat
    ApiActivityDungeon {activityId, activityType, dungeonId,
    selectActorGroupId, multiPower, aura*} -> DBH.ResChange(resChange),
    WU.EnterGameLevel(dungeonUid, baseInfo); records via table.find(records)
    so GET fci/activity-dungeon/record/ must never be null.
  * scene__scenefight: WeekActivity and Gold PATCH fci/dungeon/{uid}/active/
    (not their own prefix), then POST fci/activity-dungeon/{uid}/balance/ /
    fci/gold-dungeon/{uid}/balance/; Tower PATCH fci/tower-dungeon/{uid}/
    active/ then POST .../balance (no slash); Triggered uses
    fci/triggered-dungeon/{uid}/active|balance/.  The balance reply is stored
    as DungeonBalance/result (flat BalanceInfo).
  * common__dungeonutil DU.Restart DELETEs fci/dungeon/{uid} for every type.
  * window__dungeonbalance__dungeonbalancegold: the first two resChange
    entries with baseRes.countDelta >= 0 are (base gold, kill gold).
"""
from __future__ import annotations

from server.core.reset import policy_for, period_due
from server.core.atomic import track_mapping

import json
import random
import sys
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory

try:
    from . import actors as actor_store
    from . import equipment
    from .grants import grant_rewards
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store
    import equipment
    from grants import grant_rewards

Response = tuple[int, Any]
_TABLE_DIR = Path(__file__).resolve().parent / "data" / "client-tables"


def _rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _ints(value: Any) -> list[int]:
    return [_int(item) for item in value] if isinstance(value, (list, tuple)) else []


def _res(value: Any) -> dict[str, int]:
    value = value if isinstance(value, Mapping) else {}
    return {"type": _int(value.get("type")), "id": _int(value.get("id")),
            "param": _int(value.get("param")), "count": _int(value.get("count"))}


def _base_res(delta: Mapping[str, Any]) -> dict[str, Any]:
    return {"baseRes": {"type": _int(delta.get("type")), "id": _int(delta.get("id")), "param": _int(delta.get("param")),
                        "count": _int(delta.get("count")), "countDelta": _int(delta.get("countDelta"))}}


# ----------------------------------------------------------------- tables
DUNGEON_REWARD = {_int(r.get("dungeonId")): r for r in _rows("DungeonReward") if _int(r.get("dungeonId"))}
ACTIVITY_DUNGEON = {_int(r.get("activityId")): r for r in _rows("ActivityDungeon") if _int(r.get("activityId"))}
GOLD_LEVELS = {_int(r.get("level")): r for r in _rows("GoldDungeonLevel") if _int(r.get("level"))}
TOWER_FLOORS = {_int(r.get("floor")): r for r in _rows("TowerFloor") if _int(r.get("floor"))}
MAX_TOWER_FLOOR = max(TOWER_FLOORS, default=0)
TOWER_FLOOR_REWARDS = {_int(r.get("towerFloor")): [_res(x) for x in r.get("reward") or []]
                       for r in _rows("TowerFloorReward") if _int(r.get("towerFloor"))}
TOWER_GROUP_UNLOCK = {_int(r.get("groupId")): _int(r.get("level")) for r in _rows("TowerGroupUnlockLv")
                      if _int(r.get("groupId"))}
DEPLOY_MISSIONS = {_int(r.get("id")): r for r in _rows("DeployMission") if _int(r.get("id")) and not r.get("guildMission")}
DEPLOY_COUNT = {_int(r.get("level")): _int(r.get("missionCount")) for r in _rows("DeployCount") if _int(r.get("level"))}
TRIGGERED_POOLS = {_int(r.get("poolId")): r for r in _rows("TriggeredEvent") if _int(r.get("poolId"))}
MISC: dict[str, Any] = (_rows("Misc") or [{}])[0]
ACTOR_ROLE = {_int(r.get("id")): _int(r.get("role")) for r in _rows("ActorConfig") if _int(r.get("id"))}
ACTOR_LEVELS = {_int(r.get("level")): _int(r.get("exp")) for r in _rows("ActorLevelup") if _int(r.get("level"))}
PLAYER_LEVELS = {_int(r.get("level")): r for r in _rows("PlayerLevelInitConfig") if _int(r.get("level"))}
MAX_PLAYER_LEVEL = max(PLAYER_LEVELS, default=0)
DEPLOY_SPEEDUP_ITEMS = {_int(r.get("id")): (_ints(r.get("param")) or [0])[0] for r in _rows("ItemInfo")
                        if _int(r.get("funcType")) == 5}
# DailyRefresh type 1 (ResourceRefresh_Daily) rows: item -> (count, limit).
DAILY_REFRESH = {_int((r.get("refreshRes") or {}).get("id")): (_int((r.get("refreshRes") or {}).get("count")),
                                                                _int(r.get("refreshLimit")))
                 for r in _rows("DailyRefresh")
                 if _int(r.get("type")) == 1 and _int((r.get("refreshRes") or {}).get("type")) == 11}
WEEKLY_REFRESH = {_int((r.get("refreshRes") or {}).get("id")): (_int((r.get("refreshRes") or {}).get("count")),
                                                                 _int(r.get("refreshLimit")))
                  for r in _rows("DailyRefresh")
                  if _int(r.get("type")) == 2 and _int((r.get("refreshRes") or {}).get("type")) == 11}
# 80009 is absent from the extracted DailyRefresh rows, but the local
# remediation policy keeps the event ticket usable: one per Beijing day,
# capped at three.  A recovered original row can replace this override.
DAILY_REFRESH.setdefault(80009, (1, 3))

DAY = 86400
UID_BASE = 9_000_000_000_000  # disjoint from dungeon.py's millisecond uids
RES_PLAYER_EXP, RES_POWER, RES_GOLD, RES_ITEM, RES_EQUIP = 1, 6, 7, 11, 12
ATTR_HP = 1
DUNGEON_TYPE = {"material": 5, "gold": 9, "tower": 4, "triggered": 13}
ACTIVITY_TYPE = {"material": 2, "gold": 17, "tower": 3, "triggered": 31}
MAX_ACTOR_LEVEL = _int(MISC.get("maxActorLevel"), 60) or 60
WEEKDAY_GRACE = 300  # a request sent just before local midnight still counts for that day
# Local policy when the schedule document is unavailable (events.py/YAML).
DEFAULT_WEEKDAYS = {2001: [1, 4, 7], 2002: [2, 5, 7], 2003: [3, 6, 7]}


def _first_cost_id(dungeon_ids: list[int], default: int) -> int:
    for dungeon_id in dungeon_ids:
        for cost in (DUNGEON_REWARD.get(dungeon_id) or {}).get("costWin") or []:
            if _int(cost.get("type")) == RES_ITEM and _int(cost.get("id")):
                return _int(cost.get("id"))
    return default


GOLD_TICKET = _first_cost_id(sorted({_int(r.get("dungeonId")) for r in GOLD_LEVELS.values() if r.get("dungeonId")}), 80002)
TRIGGERED_FIGHT_TICKET = _first_cost_id([8001], 80006)
REFRESHED_ITEMS = tuple(sorted(DAILY_REFRESH))


def _costs(row: Optional[Mapping[str, Any]], key: str, multi: int = 1) -> list[dict[str, int]]:
    out = []
    for item in (row or {}).get(key) or []:
        res = _res(item)
        if res["type"] and res["count"] > 0:
            out.append({**res, "count": -res["count"] * max(1, multi)})
    return out


def _team_statistics(active: Any) -> tuple[Any, Any, Any, Any]:
    """(own team, enemy team, own statistic, enemy statistic) of FightActiveData."""

    if active is None:
        return None, None, None, None
    teams = list(getattr(active, "teamList", []) or [])
    own = next((team for team in teams if int(team.team) == 1), None)
    enemy = next((team for team in teams if int(team.team) == 2), None)
    if own is None and enemy is None and teams:
        own, enemy = teams[0], (teams[1] if len(teams) > 1 else None)

    def stat(team: Any) -> Any:
        return team.statistic if team is not None and team.HasField("statistic") else None

    return own, enemy, stat(own), stat(enemy)


def _target_value(target_type: int, params: list[int], own: Any, enemy: Any) -> int:
    """Logic/Target/Target<Type>.Update(statisticSelf, statisticEnemy)."""

    enemy_dead = list(getattr(enemy, "deadInfo", []) or []) if enemy is not None else []
    enemy_init = int(getattr(enemy, "initCount", 0) or 0) if enemy is not None else 0
    extra = params[2] if len(params) > 2 else 0
    if target_type == 1:
        return len(own.linkIdList)
    if target_type == 2:
        return int(own.gameRound)
    if target_type == 3:
        return len(own.deadInfo)
    if target_type == 4:
        return int(own.totalDamage)
    if target_type == 5:
        return len(own.bigSkillList)
    if target_type == 6:
        return sum(1 for value in own.cardNumList if (int(value) % 10000) // 10 == extra)
    if target_type == 7:
        return int(enemy_dead[-1].kind) if enemy_dead and enemy_init == len(enemy_dead) else -1
    if target_type == 8:
        return sum(1 for dead in enemy_dead if int(dead.kind) == extra)
    if target_type == 9:
        if enemy_dead:
            if extra == 1:
                return int(enemy_dead[0].seat)
            if extra == 2 and enemy_init == len(enemy_dead):
                return int(enemy_dead[-1].seat)
        return 0
    if target_type == 10:
        source = enemy if extra == 2 else own
        return sum(1 for value in getattr(source, "buffKindList", []) or [] if int(value) != 0)
    raise ValueError(target_type)


def _compare(value: int, compare: int, operation: int) -> bool:
    """FightHelp.compareValue with the PB Operation enum."""

    return {0: value == compare, 1: value != compare, 2: value < compare, 3: value <= compare,
            4: value > compare, 5: value >= compare}.get(operation, False)


def _max_hp(actor: Mapping[str, Any]) -> int:
    for attr in actor.get("attrs") or []:
        if isinstance(attr, Mapping) and _int(attr.get("type")) == ATTR_HP:
            return max(0, _int(attr.get("value")))
    return 0


class LocalActivityModes:
    """Material / gold / tower / deploy / triggered-event routes."""

    def __init__(self, game: Any, clock: Optional[Any] = None) -> None:
        self.game = game
        self._clock = clock or getattr(game, "_clock", None) or (lambda: int(time.time()))
        self._lock = game._lock
        self.rng = random.Random()
        self._initialize()
        # Deploy "refresh missions" is bought through economy's
        # fci/resbuyprice/resbuy/ (RefreshPriceType_DeployTimes = 10).
        hooks = getattr(getattr(game, "economy", None), "resbuy_hooks", None)
        if isinstance(hooks, dict) and hasattr(self, "_deploy_refresh_hook"):
            hooks.setdefault(10, self._deploy_refresh_hook)

    # ------------------------------------------------------------ storage
    def _connect(self):
        return self.game._connect()

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_mode_runs (
                    dungeon_uid INTEGER PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    dungeon_id INTEGER NOT NULL,
                    activity_id INTEGER NOT NULL DEFAULT 0,
                    multi_power INTEGER NOT NULL DEFAULT 1,
                    group_id INTEGER NOT NULL DEFAULT 0,
                    actor_uids_json TEXT NOT NULL DEFAULT '[]',
                    extra_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    closed_at INTEGER,
                    active_data BLOB,
                    balance_json TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS activity_mode_runs_account ON activity_mode_runs(account_id, mode, status)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS activity_mode_state ("
                "account_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, updated_at INTEGER NOT NULL)"
            )

    def _now(self) -> int:
        return int(self._clock())

    def _day(self, now: Optional[int] = None) -> int:
        return policy_for(self.game).day_index(self._now() if now is None else now)

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "dailyRefresh": {},
            "weeklyRefresh": {},
            "material": {"day": None, "records": {}},
            "gold": {"lastLevel": 0},
            "tower": {"day": None, "locked": False, "towerFloor": 0, "towerFloorRecord": 0,
                      "actorGroups": None, "confirmed": {}, "enemyRecord": None, "rewardGot": []},
            "deploy": {"day": None, "missions": [], "accomplishNum": 0},
            "triggered": {"day": None, "finish": 0, "events": [], "passiveCount": 0, "lastPassive": 0, "seq": 0},
        }

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute("SELECT state_json FROM activity_mode_state WHERE account_id = ?",
                                     (int(profile["account_id"]),)).fetchone()
        state = self._default_state()
        try:
            loaded = json.loads(row["state_json"]) if row is not None else {}
        except (TypeError, ValueError):
            loaded = {}
        for key, default in state.items():
            if isinstance(loaded.get(key), dict):
                default.update(loaded[key])
        return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO activity_mode_state(account_id, state_json, updated_at) VALUES(?, ?, ?)",
                (int(profile["account_id"]), json.dumps(state, separators=(",", ":")), self._now()),
            )

    # ------------------------------------------------------------ requests
    def _message_class(self, name: str):
        pool = getattr(self.game, "_pool", None)
        if pool is None:
            return None
        try:
            return message_factory.GetMessageClass(pool.FindMessageTypeByName(name))
        except Exception:  # pragma: no cover - descriptor missing
            return None

    def _request(self, body: Any, type_name: Optional[str] = None) -> dict[str, Any]:
        """JSON object or wireformat ``type_name`` request as a camelCase dict."""

        if isinstance(body, Mapping):
            return dict(body)
        if body is None:
            return {}
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        if not raw.strip():
            return {}
        if raw.lstrip()[:1] in (b"{", b"["):
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return {}
            return dict(value) if isinstance(value, Mapping) else {}
        cls = self._message_class(type_name) if type_name else None
        if cls is None:
            return {}
        message = cls()
        try:
            message.ParseFromString(raw)
            return json_format.MessageToDict(message)
        except Exception:
            return {}

    def _parse_active(self, body: Any):
        cls = getattr(self.game, "_types", {}).get("FightActiveData")
        if cls is None or body is None:
            return None
        message = cls()
        value: Any = body
        if not isinstance(body, Mapping):
            raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
            if not raw.strip():
                return None
            if raw.lstrip()[:1] != b"{":
                try:
                    message.ParseFromString(raw)
                    return message
                except Exception:
                    return None
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return None
        try:
            json_format.ParseDict(dict(value), message, ignore_unknown_fields=True)
            return message
        except Exception:
            return None

    def _decode_active(self, raw: Any):
        cls = getattr(self.game, "_types", {}).get("FightActiveData")
        if cls is None or not raw:
            return None
        message = cls()
        try:
            message.ParseFromString(bytes(raw))
        except Exception:
            return None
        return message

    # ------------------------------------------------------------ runs
    def _insert_run(self, profile: Mapping[str, Any], mode: str, dungeon_id: int, *, activity_id: int = 0,
                    multi_power: int = 1, group_id: int = 0, actor_uids: Any = (),
                    extra: Optional[Mapping[str, Any]] = None) -> tuple[int, int]:
        """New active run; any other active run of the mode is closed first
        (fight-again / restart post the next run before deleting the old)."""

        now = self._now()
        account_id = int(profile["account_id"])
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE activity_mode_runs SET status = 'closed', closed_at = ? "
                "WHERE account_id = ? AND mode = ? AND status = 'active'", (now, account_id, mode))
            current = connection.execute("SELECT MAX(dungeon_uid) FROM activity_mode_runs").fetchone()[0]
            uid = max(int(current or 0), UID_BASE) + 1
            connection.execute(
                "INSERT INTO activity_mode_runs (dungeon_uid, account_id, mode, dungeon_id, activity_id, multi_power, "
                "group_id, actor_uids_json, extra_json, status, created_at) VALUES (?,?,?,?,?,?,?,?,?, 'active', ?)",
                (uid, account_id, mode, int(dungeon_id), int(activity_id), max(1, int(multi_power)), int(group_id),
                 json.dumps([int(v) for v in actor_uids]), json.dumps(dict(extra or {})), now),
            )
        return uid, now

    def _run(self, profile: Mapping[str, Any], uid: int) -> Optional[dict[str, Any]]:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM activity_mode_runs WHERE dungeon_uid = ? AND account_id = ?",
                                     (int(uid), int(profile["account_id"]))).fetchone()
        if row is None:
            return None
        run = dict(row)
        for key, default in (("actor_uids_json", []), ("extra_json", {})):
            try:
                run[key[:-5]] = json.loads(run.get(key) or "null") or default
            except (TypeError, ValueError):
                run[key[:-5]] = default
        return run

    def _close(self, profile: Mapping[str, Any], uid: int) -> Response:
        """Quit (DELETE): idempotent; unknown/finished uids are acknowledged too."""

        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE activity_mode_runs SET status = 'closed', closed_at = ? "
                "WHERE dungeon_uid = ? AND account_id = ? AND status = 'active'",
                (self._now(), int(uid), int(profile["account_id"])))
        return 200, {}

    def _store_active(self, profile: Mapping[str, Any], uid: int, body: Any) -> Response:
        """Fight-end PATCH .../active/: keep the FightActiveData (reply ignored)."""

        message = self._parse_active(body)
        if message is not None:
            with self._lock, closing(self._connect()) as connection, connection:
                connection.execute(
                    "UPDATE activity_mode_runs SET active_data = ? WHERE dungeon_uid = ? AND account_id = ? "
                    "AND status = 'active'", (message.SerializeToString(), int(uid), int(profile["account_id"])))
        return 200, {}

    def _claim(self, profile: Mapping[str, Any], uid: int, won: bool) -> bool:
        with self._lock, closing(self._connect()) as connection, connection:
            updated = connection.execute(
                "UPDATE activity_mode_runs SET status = ?, closed_at = ? WHERE dungeon_uid = ? AND account_id = ? "
                "AND status = 'active'", ("balanced" if won else "failed", self._now(), int(uid),
                                          int(profile["account_id"])))
        return updated.rowcount == 1

    def _store_balance(self, profile: Mapping[str, Any], uid: int, payload: Mapping[str, Any]) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("UPDATE activity_mode_runs SET balance_json = ? WHERE dungeon_uid = ? AND account_id = ?",
                               (json.dumps(payload, separators=(",", ":")), int(uid), int(profile["account_id"])))

    @staticmethod
    def _won(active: Any, body: Any) -> bool:
        if active is not None and int(getattr(active, "winTeam", 0) or 0) in (1, 2):
            return int(active.winTeam) == 1
        # Local tools may post {"win": true}; the client always PATCHes first.
        if isinstance(body, (bytes, bytearray, str)) and body:
            try:
                value = json.loads(body.decode("utf-8") if not isinstance(body, str) else body)
            except (UnicodeDecodeError, ValueError):
                value = None
            if isinstance(value, Mapping) and "win" in value:
                return bool(value["win"])
        return False

    def _balance(self, profile: Mapping[str, Any], uid: int, body: Any) -> Optional[Response]:
        run = self._run(profile, uid)
        if run is None:
            return None
        if run["status"] != "active":
            if run.get("balance_json"):
                try:
                    cached = json.loads(run["balance_json"])
                except (TypeError, ValueError):
                    cached = None
                if isinstance(cached, dict):
                    return 200, cached
            return 200, self._result(profile, won=False)
        active = self._decode_active(run.get("active_data"))
        won = self._won(active, body)
        if not self._claim(profile, uid, won):
            return self._balance(profile, uid, body)
        handler = {"material": "_material_balance", "gold": "_gold_balance",
                   "tower": "_tower_balance", "triggered": "_triggered_balance"}.get(run["mode"])
        handler = getattr(self, handler, None) if handler else None
        payload = handler(profile, run, active, won) if handler else self._result(profile, won=won)
        self._store_balance(profile, uid, payload)
        return 200, payload

    def _result(self, profile: Mapping[str, Any], *, won: bool, res_change: Any = (), exp_changes: Any = (),
                targets: Any = (), first: bool = False, level_before: Optional[int] = None,
                event: Optional[Mapping[str, Any]] = None, **extra: Any) -> dict[str, Any]:
        """Flat DbDungeon.BalanceInfo (DungeonBalance/result)."""

        level = max(1, _int(profile.get("level"), 1))
        payload = {
            "balanced": True, "balanceTime": self._now(), "win": bool(won), "targetsDone": list(targets),
            "resChange": list(res_change), "expChange": list(exp_changes), "favourChange": [],
            "playerLevel": level, "playerLevelBeforeBalance": int(level_before or level),
            "firstFinish": bool(first), "profits": [], "loseNum": 0, "expReturn": [],
        }
        if event:
            # Only a real event: DungeonBalance calls DBH.TriggeredEventChange
            # on any non-nil eventInfo.
            payload["eventInfo"] = dict(event)
        payload.update(extra)
        return payload

    # ------------------------------------------------------------ fighters
    def _fight_team(self, profile: Mapping[str, Any], group_id: Any) -> tuple[list[int], list[int]]:
        return self.game.dungeons._fight_team(profile, group_id)

    def _fight_actor(self, profile: Mapping[str, Any], actor_id: int, uid: int) -> dict[str, Any]:
        return self.game.dungeons._fight_actor(profile, actor_id, uid)

    def _aura(self, profile: Mapping[str, Any], request: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """Support (助战) aura via dungeon.py when available (optional)."""

        resolver = getattr(getattr(self.game, "dungeons", None), "_resolve_aura", None)
        try:
            return resolver(profile, request) if resolver is not None else None
        except Exception:
            return None

    def _apply_aura(self, actors: list[dict[str, Any]], aura: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not aura or not _int(aura.get("actorId")):
            return actors
        module = sys.modules.get(type(getattr(self.game, "dungeons", None)).__module__)
        apply = getattr(module, "_apply_aura", None)
        try:
            return apply(actors, _int(aura.get("actorId")), _int(aura.get("quality"))) if apply else actors
        except Exception:
            return actors

    def _base_info(self, profile: Mapping[str, Any], mode: str, dungeon_id: int, actor_ids: list[int],
                   actor_uids: list[int], created: int, *, activity_id: int = 0, floor: int = 0,
                   aura: Optional[Mapping[str, Any]] = None, actors: Optional[list[dict[str, Any]]] = None,
                   event_key: str = "") -> dict[str, Any]:
        """DbDungeon.BaseInfo: WU.SyncActorAttr fights with ``actors``."""

        if actors is None:
            actors = [self._fight_actor(profile, actor_id, uid) for actor_id, uid in zip(actor_ids, actor_uids)]
        aura = aura or {}
        return {
            "dungeonId": int(dungeon_id), "actorUid": list(actor_uids), "createTime": int(created), "quitTime": 0,
            "activityType": ACTIVITY_TYPE[mode], "activityId": int(activity_id), "towerFloor": int(floor),
            "actorId": list(actor_ids), "auraPlayerId": str(aura.get("playerId", "") or ""),
            "auraActorId": _int(aura.get("actorId")), "auraStranger": bool(aura.get("stranger", False)),
            "auraActorQuality": _int(aura.get("quality")), "dungeonType": DUNGEON_TYPE[mode],
            "actors": self._apply_aura(actors, aura), "eventKey": str(event_key or ""), "medalId": 0,
            "reduceInfo": [], "isChallengeMode": False, "fightBuffs": [], "npcCnt": 0,
        }

    # ------------------------------------------------------------ resources
    def _resource_count(self, profile: Mapping[str, Any], rtype: int, rid: int = 0) -> int:
        return sum(_int(item.get("count")) for item in profile.get("resources", []) or []
                   if isinstance(item, Mapping) and _int(item.get("type")) == rtype and _int(item.get("id")) == rid)

    def _change_resources(self, profile: Mapping[str, Any], changes: list[Mapping[str, Any]]) -> Optional[list]:
        if not changes:
            return []
        ok, deltas, _ = self.game.economy._change_resources(profile, [dict(item) for item in changes])
        return [_base_res(delta) for delta in deltas] if ok else None

    def _can_pay(self, profile: Mapping[str, Any], costs: list[Mapping[str, Any]]) -> bool:
        need: dict[tuple[int, int], int] = {}
        for cost in costs:
            key = (_int(cost.get("type")), _int(cost.get("id")))
            need[key] = need.get(key, 0) - _int(cost.get("count"))
        for (rtype, rid), amount in need.items():
            have = self._item_count(profile, rid) if rtype == RES_ITEM else self._resource_count(profile, rtype, rid)
            if have < amount:
                return False
        return True

    def _pay(self, profile: Mapping[str, Any], costs: list[Mapping[str, Any]], *, clamp: bool = False) -> Optional[list]:
        """Spend STResource costs (negative counts); items live in systems state.

        ``clamp`` pays what is available instead of refusing (win costs whose
        balance was checked at entry)."""

        changes: list[dict[str, Any]] = []
        ledger = []
        for cost in costs:
            if _int(cost.get("type")) == RES_ITEM:
                entry = self._change_item(profile, _int(cost.get("id")), _int(cost.get("count")), clamp=clamp)
                if entry is None and not clamp:
                    return None
                if entry is not None:
                    changes.append(entry)
            else:
                amount = _int(cost.get("count"))
                if clamp:
                    have = self._resource_count(profile, _int(cost.get("type")), _int(cost.get("id")))
                    amount = -min(-amount, max(0, have))
                if amount:
                    ledger.append({**_res(cost), "count": amount})
        paid = self._change_resources(profile, ledger)
        if paid is None:
            return None
        return changes + paid

    def _item_count(self, profile: Mapping[str, Any], item_id: int) -> int:
        items = self.game.systems._state(profile).get("items") or {}
        entry = items.get(str(int(item_id)))
        return _int(entry.get("count")) if isinstance(entry, Mapping) else 0

    def _change_item(self, profile: Mapping[str, Any], item_id: int, delta: int, *,
                     clamp: bool = False) -> Optional[dict[str, Any]]:
        systems = self.game.systems
        state = systems._state(profile)
        items = state.setdefault("items", {})
        entry = dict(items.get(str(int(item_id))) or {})
        current = _int(entry.get("count"))
        new = current + int(delta)
        if new < 0:
            if not clamp:
                return None
            new = 0
        if new == current:
            return None
        entry["count"] = new
        if new > current:
            entry["gotTime"] = self._now()
        items[str(int(item_id))] = entry
        systems._save(profile, state)
        return {"baseRes": {"type": RES_ITEM, "id": int(item_id), "param": 0, "count": new, "countDelta": new - current}}

    def _grant(self, profile: Mapping[str, Any], rewards: list[Mapping[str, Any]], *, dungeon_type: int = 0) -> list[dict[str, Any]]:
        """Grant drops (player EXP excluded); apply active guild drop skills."""

        merged: dict[tuple[int, int], dict[str, int]] = {}
        rules = []
        if dungeon_type:
            try:
                rules = list(self.game.guild.active_profit_rules(profile))
            except Exception:
                rules = []
        try:
            from dungeon import ITEM_FUNC_TYPES, LocalDungeons
        except Exception:
            ITEM_FUNC_TYPES, LocalDungeons = {}, None
        for reward in rewards:
            res = _res(reward)
            if res["type"] <= 0 or res["count"] <= 0 or res["type"] == RES_PLAYER_EXP:
                continue
            if rules and LocalDungeons is not None and res["type"] in (RES_ITEM, RES_EQUIP):
                res["count"] = LocalDungeons._profit_scaled(
                    res["count"], rules, dungeon_type=dungeon_type, profit_type=8,
                    res_type=res["type"], func_type=ITEM_FUNC_TYPES.get(res["id"], 0), rng=self.rng,
                )
            merged.setdefault((res["type"], res["id"]), {**res, "count": 0})["count"] += res["count"]
        others = [item for item in merged.values() if item["type"] != RES_EQUIP]
        changes = grant_rewards(self.game, profile, others) if others else []
        for item in merged.values():
            if item["type"] != RES_EQUIP:
                continue
            for _ in range(item["count"]):
                try:
                    changes.append(equipment.grant_equipment(self.game, profile, item["id"], rng=self.rng))
                except (ValueError, KeyError):
                    break
        return changes

    def refresh_daily_items(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Settle table-backed daily/weekly tickets with their markers atomically.

        Missing/zero counts (80009 in the recovered table) grant nothing.
        Offline accrual stops at the configured cap; rollback never replays a
        previously settled period.  The scheduler can safely call this again.
        """
        with self.game.atomic_mutation():
            policy, now = policy_for(self.game), self._now()
            state = self._state(profile)
            sys_state = self.game.systems._state(profile)
            items = sys_state.setdefault("items", {})
            changes, dirty = [], False
            for key, rules, current in (("dailyRefresh", DAILY_REFRESH, policy.day_index(now)),
                                        ("weeklyRefresh", WEEKLY_REFRESH, policy.week_index(now))):
                marks = state.setdefault(key, {})
                for item_id, (increment, limit) in rules.items():
                    if increment <= 0 or limit <= 0:
                        continue
                    previous = marks.get(str(item_id))
                    periods = 1 if previous is None else current - _int(previous)
                    if periods <= 0:
                        continue
                    marks[str(item_id)] = current
                    dirty = True
                    entry = dict(items.get(str(item_id)) or {})
                    owned = _int(entry.get("count"))
                    total = max(owned, min(limit, owned + periods * increment))
                    if total == owned:
                        continue
                    entry.update(count=total, gotTime=now)
                    entry.setdefault("isNew", False)
                    items[str(item_id)] = entry
                    changes.append({"baseRes": {"type": RES_ITEM, "id": item_id, "param": 0,
                                                "count": total, "countDelta": total - owned}})
            if not dirty:
                return []
            # Both inventories and period markers live in the game's database,
            # including legacy split-store mode.  A crash cannot commit one
            # without the other; do not call two independent module _save's.
            with closing(self.game._connect()) as connection, connection:
                if changes:
                    connection.execute("UPDATE local_system_state SET state_json = ?, updated_at = ? WHERE account_id = ?",
                                       (json.dumps(sys_state, separators=(",", ":")), now, int(profile["account_id"])))
                connection.execute("INSERT INTO activity_mode_state(account_id, state_json, updated_at) VALUES (?, ?, ?) "
                                   "ON CONFLICT(account_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                                   (int(profile["account_id"]), json.dumps(state, separators=(",", ":")), now))
            return changes

    # ------------------------------------------------------------ EXP
    @staticmethod
    def _player_level_exp(profile: Mapping[str, Any]) -> tuple[int, int]:
        exp = next((_int(item.get("count")) for item in profile.get("resources", []) or []
                    if isinstance(item, Mapping) and _int(item.get("type")) == RES_PLAYER_EXP), 0)
        return max(1, _int(profile.get("level"), 1)), max(0, exp)

    def _grant_player_exp(self, profile: Mapping[str, Any], delta: int) -> tuple[int, dict[str, Any]]:
        """Player EXP with PlayerLevelInitConfig level-ups and levelReward
        (incInfo.limit caps), like story balance.  Returns (level before,
        resChange entry whose count is the pre-rollover EXP, as story sends)."""

        level_before, exp_before = self._player_level_exp(profile)
        delta = max(0, int(delta))
        entry = {"baseRes": {"type": RES_PLAYER_EXP, "id": 0, "param": 0,
                             "count": exp_before + delta, "countDelta": delta}}
        if delta == 0:
            return level_before, entry
        resources = [dict(item) for item in profile.get("resources", []) or [] if isinstance(item, Mapping)]
        exp_res = next((item for item in resources if _int(item.get("type")) == RES_PLAYER_EXP), None)
        if exp_res is None:
            exp_res = {"type": RES_PLAYER_EXP, "id": 0, "count": exp_before, "param": 0}
            resources.append(exp_res)
        level, exp = level_before, exp_before + delta
        while MAX_PLAYER_LEVEL and level < MAX_PLAYER_LEVEL:
            need = max(1, _int((PLAYER_LEVELS.get(level + 1) or {}).get("exp")))
            if exp < need:
                break
            exp -= need
            level += 1
        exp_res["count"] = exp
        for reached in range(level_before + 1, level + 1):
            for reward in (PLAYER_LEVELS.get(reached) or {}).get("levelReward") or []:
                rtype, rid, count = _int(reward.get("type")), _int(reward.get("id")), _int(reward.get("count"))
                if rtype <= 0 or count <= 0:
                    continue
                target = next((item for item in resources
                               if _int(item.get("type")) == rtype and _int(item.get("id")) == rid), None)
                if target is None:
                    target = {"type": rtype, "id": rid, "count": 0, "param": 0}
                    resources.append(target)
                current = _int(target.get("count"))
                granted = current + count
                for inc in (PLAYER_LEVELS.get(level) or {}).get("incInfo") or []:
                    if isinstance(inc, Mapping) and _int(inc.get("type")) == rtype and inc.get("limit"):
                        granted = max(current, min(granted, _int(inc["limit"])))
                target["count"] = granted
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute("UPDATE game_profiles SET level = ?, resources_json = ?, updated_at = ? WHERE account_id = ?",
                               (level, json.dumps(resources, separators=(",", ":")), self._now(),
                                int(profile["account_id"])))
        if isinstance(profile, dict):
            track_mapping(self.game, profile, "level", "resources")
            profile["level"], profile["resources"] = level, resources
        if level > level_before:
            tasks = getattr(self.game, "tasks", None)
            if tasks is not None:
                tasks.on_level(profile)
        return level_before, entry

    def _grant_actor_exp(self, profile: Mapping[str, Any], dungeon_id: int, team_uids: list[int],
                         runs: int) -> list[dict[str, Any]]:
        """DungeonReward.expActor (slots 0-2) / expAssistant (3-5) x runs,
        ActorLevelup thresholds, capped at the player's level."""

        row = DUNGEON_REWARD.get(int(dungeon_id)) or {}
        systems = self.game.systems
        state = systems._state(profile)
        cap = max(1, min(MAX_ACTOR_LEVEL, _int(profile.get("level"), 1)))
        changes = []
        for index, uid in enumerate(list(team_uids)[:6]):
            actor = actor_store.get(profile, state, uid) if _int(uid) > 0 else None
            gain = _int(row.get("expActor" if index < 3 else "expAssistant")) * max(1, runs)
            if actor is None or gain <= 0:
                continue
            level_before = max(1, _int(actor.get("level"), 1))
            exp_before = max(0, _int(actor.get("exp")))
            level, exp = level_before, exp_before + gain
            while level < cap and level + 1 in ACTOR_LEVELS and exp >= max(1, ACTOR_LEVELS[level + 1]):
                exp -= max(1, ACTOR_LEVELS[level + 1])
                level += 1
            if level >= cap and level + 1 in ACTOR_LEVELS:
                exp = min(exp, max(0, ACTOR_LEVELS[level + 1] - 1))
            actor["level"], actor["exp"] = level, exp
            changes.append({"actorUid": int(uid), "level": level_before, "exp": exp_before,
                            "levelNew": level, "expNew": exp, "addExp": gain})
        if changes:
            systems._save(profile, state)
        return changes

    def _selected_group(self, profile: Mapping[str, Any], mode_key: str, default: int = 1) -> int:
        try:
            selected = self.game.systems._state(profile).get("selectedGroupIds") or {}
        except Exception:
            return default
        return _int(selected.get(mode_key), default) if mode_key in selected else default

    @staticmethod
    def _battle_stats(active: Any) -> dict[str, int]:
        _, _, own, enemy = _team_statistics(active)
        if own is None:
            return {}
        return {"linkSkills": len(own.linkIdList), "bigSkills": len(own.bigSkillList),
                "kills": len(enemy.deadInfo) if enemy is not None else 0}

    def _targets_done(self, dungeon_id: int, active: Any, won: bool) -> list[int]:
        """Star targets met (Logic/Target/TargetLogic.checkTarget); a win
        without fight statistics (local tools) meets every target."""

        if not won:
            return []
        targets = [t for t in (DUNGEON_REWARD.get(int(dungeon_id)) or {}).get("target") or []
                   if isinstance(t, Mapping) and _int(t.get("id")) > 0]
        _, _, own, enemy = _team_statistics(active)
        if own is None:
            return [_int(t["id"]) for t in targets]
        done = []
        for target in targets:
            params = _ints(target.get("param"))
            if len(params) < 2:
                continue
            try:
                value = _target_value(_int(target.get("type")), params, own, enemy)
            except (ValueError, AttributeError, TypeError):
                continue
            if _compare(value, params[0], params[1]):
                done.append(_int(target["id"]))
        return done

    # ------------------------------------------------------------ routing
    def handle_http(self, method: str, path: str, body: Any, profile: Mapping[str, Any]) -> Optional[Response]:
        parts = [part for part in (urlsplit(path or "").path or "").lower().split("/") if part]
        if len(parts) < 2 or parts[0] != "fci":
            return None
        handler = getattr(self, {
            "activity-dungeon": "_material_http",
            "gold-dungeon": "_gold_http",
            "tower": "_tower_http",
            "tower-dungeon": "_tower_dungeon_http",
            "deploy": "_deploy_http",
            "triggered-event": "_triggered_event_http",
            "triggered-dungeon": "_triggered_dungeon_http",
            "dungeon": "_shared_dungeon_http",
        }.get(parts[1], ""), None)
        if handler is None:
            return None
        # Entry costs, run creation and balance receipts use several module
        # saves; all of them must succeed together, including nested refreshes.
        with self.game.atomic_mutation():
            return handler(method.upper(), parts[2:], body, profile)

    def _run_routes(self, method: str, sub: list[str], body: Any, profile: Mapping[str, Any]) -> Optional[Response]:
        """``{uid}`` DELETE, ``{uid}/active`` PATCH, ``{uid}/balance`` POST."""

        if not sub or not sub[0].isdigit():
            return None
        uid = int(sub[0])
        if len(sub) == 1 and method == "DELETE":
            return self._close(profile, uid)
        if len(sub) == 2 and sub[1] == "active" and method in {"PATCH", "POST", "PUT"}:
            return self._store_active(profile, uid, body)
        if len(sub) == 2 and sub[1] == "balance" and method == "POST":
            return self._balance(profile, uid, body)
        return None

    def _shared_dungeon_http(self, method: str, sub: list[str], body: Any,
                             profile: Mapping[str, Any]) -> Optional[Response]:
        """fci/dungeon/{uid}/... for this module's uids only (WeekActivity/Gold
        PATCH active there; DU.Restart DELETEs there)."""

        if not sub or not sub[0].isdigit() or int(sub[0]) <= UID_BASE or self._run(profile, int(sub[0])) is None:
            return None
        return self._run_routes(method, sub, body, profile)

    # ================================================== material (type 2)
    # Window_ActivityStageChoose binds GET fci/activity-dungeon/summary/
    # (summary.selectActorGroupId) and GET .../record/ (table.find over
    # records: dungeonId, finished, targetsDone, multiPower, doneTimes).
    def _material_http(self, method: str, sub: list[str], body: Any, profile: Mapping[str, Any]) -> Optional[Response]:
        if sub == ["summary"] and method == "GET":
            return 200, {"selectActorGroupId": self._selected_group(profile, "activity-dun")}
        if sub == ["record"] and method == "GET":
            return 200, self._material_records(profile)
        if not sub and method == "POST":
            return self._material_create(profile, body)
        result = self._run_routes(method, sub, body, profile)
        if result is None and sub and sub[0].isdigit() and method in {"DELETE", "PATCH"}:
            return 200, {}
        return result

    def _material_state(self, state: dict[str, Any]) -> dict[str, Any]:
        material = state["material"]
        today = self._day()
        if period_due(material.get("day"), today):
            material["day"] = today
            for record in (material.get("records") or {}).values():
                record["doneTimes"] = 0
        material.setdefault("records", {})
        return material

    def _material_records(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        records = self._material_state(self._state(profile))["records"]
        return [
            {"dungeonId": int(key), "unlocked": True, "finished": bool(value.get("finished")),
             "targetsDone": _ints(value.get("targetsDone")), "multiPower": _int(value.get("multiPower")),
             "doneTimes": _int(value.get("doneTimes"))}
            for key, value in sorted(records.items(), key=lambda item: _int(item[0]))
        ]

    @staticmethod
    def _material_activity(dungeon_id: int, activity_id: int) -> Optional[int]:
        """Base ActivityDungeon id (2001-2003) listing ``dungeon_id``; a
        multiple-drop id (2101-2103) maps back to its base activity."""

        order = ([activity_id] if activity_id in ACTIVITY_DUNGEON else []) + sorted(ACTIVITY_DUNGEON)
        for candidate in order:
            if dungeon_id in _ints(ACTIVITY_DUNGEON[candidate].get("dungeonId")):
                base = next((other for other, row in ACTIVITY_DUNGEON.items()
                             if _int(row.get("multipleActivityId")) == candidate), candidate)
                return base
        return None

    def _material_weekdays(self, activity_id: int) -> Optional[list[int]]:
        """Client weekdays (Mon=1..Sun=7) of the always-open entry events.py
        serves for ``activity_id``; [] = every day, None = not listed."""

        events = getattr(self.game, "events", None)
        module = sys.modules.get(type(events).__module__) if events is not None else None
        reader = getattr(module, "_permanent_entries", None)
        source = getattr(events, "_permanent_source", None)
        if reader is not None and source is not None:
            try:
                entries = reader(source())
            except Exception:
                entries = None
            if entries:
                for entry in entries:
                    if _int(entry.get("type")) == ACTIVITY_TYPE["material"] and _int(entry.get("activityId")) == activity_id:
                        return _ints(entry.get("weekdays"))
                return None
        return list(DEFAULT_WEEKDAYS.get(activity_id, []))

    def _material_open(self, activity_id: int) -> bool:
        weekdays = self._material_weekdays(activity_id)
        if weekdays is None:
            return False
        if not weekdays:
            return True
        now = self._now()
        days = {policy_for(self.game).local_datetime(now).isoweekday(),
                policy_for(self.game).local_datetime(now - WEEKDAY_GRACE).isoweekday()}
        return bool(days & set(weekdays))

    def _material_create(self, profile: Mapping[str, Any], body: Any) -> Response:
        request = self._request(body, "ApiActivityDungeon")
        dungeon_id = _int(request.get("dungeonId"))
        row = DUNGEON_REWARD.get(dungeon_id)
        activity_id = self._material_activity(dungeon_id, _int(request.get("activityId")))
        if row is None or activity_id is None:
            return 404, {"error": "activity_dungeon_not_found", "dungeonId": dungeon_id}
        if not self._material_open(activity_id):
            return 409, {"error": "activity_closed_today", "activityId": activity_id}
        if _int(row.get("unlockLevel")) > _int(profile.get("level"), 1):
            return 409, {"error": "level_too_low", "unlockLevel": _int(row.get("unlockLevel"))}
        state = self._state(profile)
        records = self._material_state(state)["records"]
        pre = _int(row.get("preDungeonId"))
        if pre and not (records.get(str(pre)) or {}).get("finished"):
            return 409, {"error": "pre_dungeon_not_finished", "preDungeonId": pre}
        record = records.get(str(dungeon_id)) or {}
        enter_times = _int(row.get("enterTimes"))
        if enter_times and _int(record.get("doneTimes")) >= enter_times:
            return 409, {"error": "dungeon_done_times"}
        # ActorGroupBase.SetMultiPowerStatus: option i (multiPower i+1) needs
        # i < starCount, starCount = #targetsDone once finished, else 1.
        stars = len(_ints(record.get("targetsDone"))) if record.get("finished") else 1
        multi = max(1, min(3, _int(request.get("multiPower"), 1), max(1, stars)))
        entry_cost, win_cost = _costs(row, "cost", multi), _costs(row, "costWin", multi)
        if not self._can_pay(profile, entry_cost + win_cost):
            return 409, {"error": "insufficient_resource", "resChange": []}
        actor_ids, actor_uids = self._fight_team(profile, request.get("selectActorGroupId"))
        if not any(actor_uids):
            return 409, {"error": "profile_has_no_actors"}
        paid = self._pay(profile, entry_cost)
        if paid is None:
            return 409, {"error": "insufficient_resource", "resChange": []}
        aura = self._aura(profile, request)
        uid, created = self._insert_run(profile, "material", dungeon_id, activity_id=activity_id, multi_power=multi,
                                        group_id=_int(request.get("selectActorGroupId")), actor_uids=actor_uids,
                                        extra={"aura": aura} if aura else None)
        return 200, {
            "dungeonUid": uid,
            "baseInfo": self._base_info(profile, "material", dungeon_id, actor_ids, actor_uids, created,
                                        activity_id=activity_id, aura=aura),
            "resChange": paid,
        }

    def _material_balance(self, profile: Mapping[str, Any], run: Mapping[str, Any], active: Any,
                          won: bool) -> dict[str, Any]:
        dungeon_id, multi = int(run["dungeon_id"]), max(1, int(run["multi_power"]))
        row = DUNGEON_REWARD.get(dungeon_id) or {}
        if not won:
            level_before, exp_entry = self._grant_player_exp(profile, 0)
            return self._result(profile, won=False, res_change=[exp_entry], level_before=level_before)
        targets = self._targets_done(dungeon_id, active, True)
        energy_changes = self._pay(profile, _costs(row, "costWin", multi), clamp=True) or []
        energy = sum(-c["count"] for c in _costs(row, "cost", multi) + _costs(row, "costWin", multi)
                     if c["type"] == RES_POWER)
        display_exp = sum(_int(item.get("count")) for item in row.get("dropDisplay") or []
                          if _int(item.get("type")) == RES_PLAYER_EXP)
        level_before, exp_entry = self._grant_player_exp(profile, display_exp * multi if display_exp else energy)
        exp_changes = self._grant_actor_exp(profile, dungeon_id, run.get("actor_uids") or [], multi)
        state = self._state(profile)
        records = self._material_state(state)["records"]
        record = records.setdefault(str(dungeon_id), {})
        first = not record.get("finished")
        drops = [{**_res(item), "count": _res(item)["count"] * multi} for item in row.get("dropDisplay") or []]
        if first:
            drops += [_res(item) for item in row.get("dropDisplayFirst") or []]
        if len(targets) >= len(_ints(record.get("targetsDone"))):
            record["targetsDone"] = targets
        record.update(finished=True, multiPower=multi, doneTimes=_int(record.get("doneTimes")) + 1)
        self._save(profile, state)
        reward_changes = self._grant(profile, drops, dungeon_type=DUNGEON_TYPE["material"])
        welfare = getattr(self.game, "welfare", None)
        if welfare is not None:
            reward_changes.extend(welfare.grant_battle_materials(
                profile, dungeon_id, dungeon_type=DUNGEON_TYPE["material"], runs=multi))
        tasks = getattr(self.game, "tasks", None)
        if tasks is not None:
            tasks.on_battle_win(profile, dungeon_id, energy, mode="activity", stats=self._battle_stats(active),
                                actor_uids=run.get("actor_uids") or (), dungeon_type=DUNGEON_TYPE["material"],
                                activity_type=ACTIVITY_TYPE["material"], activity_id=int(run.get("activity_id") or 0))
        trigger = getattr(self, "trigger_passive_event", None)
        event = trigger(profile, dungeon_id) if trigger is not None else None
        return self._result(profile, won=True, res_change=[exp_entry] + energy_changes + reward_changes,
                            exp_changes=exp_changes, targets=targets, first=first, level_before=level_before,
                            event=event)

    # ================================================== gold (type 17)
    # Window_DungeonGold/DungeonGoldActorGroup: POST fci/gold-dungeon/
    # {selectActorGroupId, aura*} (JSON; DU.Restart sends wireformat
    # ApiGoldDungeon); the stage is GoldDungeonLevel[player level].dungeonId
    # and DungeonReward.costWin is ticket 80002.  DungeonBalanceGold's "fight
    # again" fires DELETE fci/gold-dungeon/{old} without waiting and POSTs the
    # next run, so creation closes the old run and DELETE always succeeds.
    def _gold_http(self, method: str, sub: list[str], body: Any, profile: Mapping[str, Any]) -> Optional[Response]:
        if not sub and method == "POST":
            return self._gold_create(profile, body)
        if sub == ["record"] and method == "GET":
            return 200, {"lastGoldDungeonLevel": _int(self._state(profile)["gold"].get("lastLevel"))}
        result = self._run_routes(method, sub, body, profile)
        if result is None and sub and sub[0].isdigit() and method in {"DELETE", "PATCH"}:
            return 200, {}
        return result

    def _gold_create(self, profile: Mapping[str, Any], body: Any) -> Response:
        request = self._request(body, "ApiGoldDungeon")
        refreshed = self.refresh_daily_items(profile)
        level = max(1, _int(profile.get("level"), 1))
        gold_row = GOLD_LEVELS.get(level) or {}
        dungeon_id = _int(gold_row.get("dungeonId"))
        if not dungeon_id:
            return 409, {"error": "gold_dungeon_locked", "level": level}
        win_cost = _costs(DUNGEON_REWARD.get(dungeon_id), "costWin") or [
            {"type": RES_ITEM, "id": GOLD_TICKET, "param": 0, "count": -1}]
        if not self._can_pay(profile, win_cost):
            return 409, {"error": "not_enough_gold_ticket", "resChange": refreshed}
        actor_ids, actor_uids = self._fight_team(profile, request.get("selectActorGroupId"))
        if not any(actor_uids):
            return 409, {"error": "profile_has_no_actors"}
        aura = self._aura(profile, request)
        uid, created = self._insert_run(profile, "gold", dungeon_id, group_id=_int(request.get("selectActorGroupId")),
                                        actor_uids=actor_uids,
                                        extra={"level": level, "rewardGold": _int(gold_row.get("rewardGold")),
                                               "rewardGoldKill": _int(gold_row.get("rewardGoldKill")), "aura": aura})
        return 200, {
            "dungeonUid": uid,
            "baseInfo": self._base_info(profile, "gold", dungeon_id, actor_ids, actor_uids, created, aura=aura),
            "resChange": refreshed,
        }

    def _gold_balance(self, profile: Mapping[str, Any], run: Mapping[str, Any], active: Any,
                      won: bool) -> dict[str, Any]:
        """resChange starts with (base gold, kill gold) baseRes entries, both
        present and non-negative even when 0; kill gold is rewardGoldKill per
        enemy in teamList[2].statistic.deadInfo (the count the window splits
        it by).  The ticket (costWin) is spent once per finished run."""

        extra = run.get("extra") or {}
        dungeon_id = int(run["dungeon_id"])
        _, _, _, enemy = _team_statistics(active)
        kills = len(enemy.deadInfo) if enemy is not None else 0
        base_gold = max(0, _int(extra.get("rewardGold")))
        kill_gold = max(0, _int(extra.get("rewardGoldKill")) * kills)
        gold = self._change_resources(profile, [
            {"type": RES_GOLD, "id": 0, "param": 0, "count": base_gold},
            {"type": RES_GOLD, "id": 0, "param": 0, "count": kill_gold},
        ]) or [{"baseRes": {"type": RES_GOLD, "id": 0, "param": 0, "count": self._resource_count(profile, RES_GOLD),
                            "countDelta": 0}}] * 2
        ticket = self._pay(profile, _costs(DUNGEON_REWARD.get(dungeon_id), "costWin"), clamp=True) or []
        state = self._state(profile)
        state["gold"]["lastLevel"] = _int(extra.get("level"))
        self._save(profile, state)
        tasks = getattr(self.game, "tasks", None)
        if tasks is not None:
            tasks.on_battle_win(profile, dungeon_id, 0, mode="gold", stats=self._battle_stats(active),
                                actor_uids=(run.get("actor_uids") or ()) if won else (), dungeon_type=DUNGEON_TYPE["gold"],
                                activity_type=ACTIVITY_TYPE["gold"], activity_id=int(run.get("activity_id") or 0))
            if base_gold + kill_gold:
                tasks.record(profile, "GoldGetM", base_gold + kill_gold)
        current_gold = self._resource_count(profile, RES_GOLD)
        gold = [{"baseRes": {"type": RES_GOLD, "id": 0, "param": 0,
                              "count": current_gold, "countDelta": base_gold}},
                {"baseRes": {"type": RES_GOLD, "id": 0, "param": 0,
                              "count": current_gold, "countDelta": kill_gold}}]
        return self._result(profile, won=won, res_change=gold + ticket, killCount=kills)

    # ================================================== tower (type 3)
    # Window_Tower: GET fci/tower/summary/ {locked, towerFloor (next floor,
    # 0 = not started this cycle), towerFloorRecord, selectActorGroupId,
    # wipeFloorFree, wipeFloorPay, rewardGot}.  TowerActorGroup/Select: GET/
    # PUT fci/tower/actorgroup/ (bare map "1".."N" of {members[6]}); once a
    # run is locked GET/PUT fci/tower/actorgroup-confirm/ (members carry
    # actor/hp/dead; GU.GetHpFillAmount needs hp on every member with an
    # actor; GU.GroupIsOK reads members[1..6]).  POST fci/tower-dungeon/
    # {dungeonId, selectActorGroupId, towerFloor} -> {dungeonUid, baseInfo,
    # resChange, enemyRecord{hp[], card[]}} (enemyRecord.hp is indexed).
    # The cycle resets daily at 06:00 Beijing (local policy); wipe targets are
    # the TowerFloor.record checkpoints at/below the best floor.
    @staticmethod
    def _tower_group_count() -> int:
        return max(len(TOWER_GROUP_UNLOCK), _int(MISC.get("maxTowerActorGroup"), 3), 1)

    def _tower_normalise(self, profile: Mapping[str, Any], raw: Any) -> dict[str, Any]:
        state = self.game.systems._state(profile)
        seen: set[int] = set()
        groups = {}
        for gid in range(1, self._tower_group_count() + 1):
            group = raw.get(str(gid)) if isinstance(raw, Mapping) else None
            slots = [0] * 6
            for member in (group or {}).get("members") or []:
                if not isinstance(member, Mapping):
                    continue
                index, uid = _int(member.get("index")), _int(member.get("actorUid"))
                if not 0 <= index < 6 or uid <= 0 or uid in seen or slots[index]:
                    continue
                if actor_store.get(profile, state, uid) is None:
                    continue
                slots[index] = uid
                seen.add(uid)
            groups[str(gid)] = {"members": [{"actorUid": slots[i], "index": i} for i in range(6)]}
        return groups

    def _tower_default_groups(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Group 1 starts as the story formation so the tower is playable."""

        systems = self.game.systems
        try:
            state = systems._state(profile)
            groups = systems._normalise_actor_groups(state.get("actorGroups")) or systems._default_actor_groups()
            selected = _int((state.get("selectedGroupIds") or {}).get("storymode"), 1) or 1
            story = groups.get(str(selected)) or groups.get("1") or {}
        except Exception:
            story = {}
        return self._tower_normalise(profile, {"1": story})

    def _tower_state(self, profile: Mapping[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        tower = state["tower"]
        today = self._day()
        if period_due(tower.get("day"), today):
            tower.update(day=today, locked=False, towerFloor=0, confirmed={}, enemyRecord=None)
        if not isinstance(tower.get("actorGroups"), dict) or not tower["actorGroups"]:
            tower["actorGroups"] = self._tower_default_groups(profile)
        if not isinstance(tower.get("rewardGot"), list):
            tower["rewardGot"] = []
        return tower

    @staticmethod
    def _tower_wipe_floors(record: int) -> tuple[int, int]:
        checkpoints = [floor for floor in sorted(TOWER_FLOORS) if TOWER_FLOORS[floor].get("record") and floor <= record]
        return (checkpoints[-2] if len(checkpoints) > 1 else 0), (checkpoints[-1] if checkpoints else 0)

    def _tower_summary(self, profile: Mapping[str, Any], tower: Mapping[str, Any]) -> dict[str, Any]:
        record = _int(tower.get("towerFloorRecord"))
        free, pay = self._tower_wipe_floors(record)
        return {"locked": bool(tower.get("locked")), "towerFloor": _int(tower.get("towerFloor")),
                "towerFloorRecord": record, "selectActorGroupId": self._selected_group(profile, "tower", 0),
                "wipeFloorFree": free, "wipeFloorPay": pay, "rewardGot": sorted(_ints(tower.get("rewardGot")))}

    def _tower_snapshot(self, profile: Mapping[str, Any], tower: Mapping[str, Any]) -> dict[str, Any]:
        """DbTower.GroupConfirm per group: live fighters at full HP."""

        level = max(1, _int(profile.get("level"), 1))
        state = self.game.systems._state(profile)
        confirmed = {}
        for gid in range(1, self._tower_group_count() + 1):
            unlocked = level >= TOWER_GROUP_UNLOCK.get(gid, 0)
            slots = ((tower.get("actorGroups") or {}).get(str(gid)) or {}).get("members") or []
            by_index = {_int(m.get("index")): _int(m.get("actorUid")) for m in slots if isinstance(m, Mapping)}
            members = []
            for index in range(6):
                uid = by_index.get(index, 0) if unlocked else 0
                record = actor_store.get(profile, state, uid) if uid else None
                if record is None:
                    members.append({"index": index, "actorUid": 0, "hp": 0, "dead": False})
                    continue
                actor = self._fight_actor(profile, int(record["id"]), uid)
                members.append({"index": index, "actorUid": uid, "actor": actor, "hp": _max_hp(actor), "dead": False})
            # energySp/energyMp stay absent until a fight records them (the
            # fight then starts from its stage defaults).
            confirmed[str(gid)] = {"members": members, "card": []}
        return confirmed

    def _tower_http(self, method: str, sub: list[str], body: Any, profile: Mapping[str, Any]) -> Optional[Response]:
        if sub == ["summary"] and method in {"GET", "POST"}:
            state = self._state(profile)
            summary = self._tower_summary(profile, self._tower_state(profile, state))
            self._save(profile, state)
            return 200, summary
        if sub == ["actorgroup"] and method in {"GET", "PUT", "POST", "PATCH"}:
            state = self._state(profile)
            tower = self._tower_state(profile, state)
            if method != "GET":
                incoming = self._request(body, "ApiTowerActorGroup").get("actorGroups")
                merged = dict(tower["actorGroups"])
                if isinstance(incoming, Mapping):
                    merged.update({str(key): value for key, value in incoming.items()})
                tower["actorGroups"] = self._tower_normalise(profile, merged)
            self._save(profile, state)
            return 200, tower["actorGroups"]
        if sub == ["actorgroup-confirm"] and method in {"GET", "PUT", "POST", "PATCH"}:
            state = self._state(profile)
            tower = self._tower_state(profile, state)
            if method != "GET" and tower.get("locked"):
                self._tower_reorder(tower, self._request(body, "ApiTowerActorGroupConfirm").get("actorGroups"))
            self._save(profile, state)
            return 200, tower["confirmed"] if tower.get("locked") else self._tower_snapshot(profile, tower)
        if sub == ["wipe"] and method == "POST":
            return self._tower_wipe(profile, body)
        if sub == ["floor-reward"] and method == "POST":
            return self._tower_floor_reward(profile, body)
        return None

    @staticmethod
    def _tower_reorder(tower: dict[str, Any], incoming: Any) -> None:
        """Locked drag-drop (ApiTowerActorGroupConfirm): move snapshots to the
        new slot indexes, keeping each member's actor/hp/dead."""

        if not isinstance(incoming, Mapping):
            return
        for gid, group in incoming.items():
            target = (tower.get("confirmed") or {}).get(str(gid))
            if not isinstance(target, dict) or not isinstance(group, Mapping):
                continue
            wanted: dict[int, int] = {}
            for member in group.get("members") or []:
                uid, index = _int(member.get("actorUid")), _int(member.get("index"))
                if uid > 0 and 0 <= index < 6:
                    wanted.setdefault(uid, index)
            members = sorted(target.get("members") or [], key=lambda m: _int(m.get("index")))
            by_uid = {_int(m.get("actorUid")): m for m in members if _int(m.get("actorUid"))}
            slots: list[Optional[dict]] = [None] * 6
            for uid, index in wanted.items():
                if uid in by_uid and slots[index] is None:
                    slots[index] = by_uid.pop(uid)
            for member in members:
                uid = _int(member.get("actorUid"))
                if uid and uid in by_uid:
                    index = _int(member.get("index"))
                    if not 0 <= index < 6 or slots[index] is not None:
                        index = next(i for i in range(6) if slots[i] is None)
                    slots[index] = by_uid.pop(uid)
            for index in range(6):
                member = slots[index] or {"actorUid": 0, "hp": 0, "dead": False}
                member["index"] = index
                slots[index] = member
            target["members"] = slots

    def _tower_dungeon_http(self, method: str, sub: list[str], body: Any,
                            profile: Mapping[str, Any]) -> Optional[Response]:
        if not sub and method == "POST":
            return self._tower_create(profile, body)
        result = self._run_routes(method, sub, body, profile)
        if result is None and sub and sub[0].isdigit() and method in {"DELETE", "PATCH"}:
            return 200, {}
        return result

    def _tower_create(self, profile: Mapping[str, Any], body: Any) -> Response:
        request = self._request(body)
        state = self._state(profile)
        tower = self._tower_state(profile, state)
        floor = _int(tower.get("towerFloor")) or 1
        if floor not in TOWER_FLOORS:
            return 409, {"error": "tower_top_reached", "towerFloor": floor}
        dungeon_id = _int(TOWER_FLOORS[floor].get("dungeonId"))
        if not tower.get("locked"):
            # The first fight of a cycle locks every group as a snapshot.
            tower.update(confirmed=self._tower_snapshot(profile, tower), locked=True, towerFloor=floor)
        group_id = _int(request.get("selectActorGroupId")) or self._selected_group(profile, "tower", 1) or 1
        group = (tower.get("confirmed") or {}).get(str(group_id))
        members = sorted((group or {}).get("members") or [], key=lambda m: _int(m.get("index")))
        self._save(profile, state)
        if not any(m.get("actor") and not m.get("dead") for m in members):
            return 409, {"error": "tower_group_unavailable", "selectActorGroupId": group_id}
        enemy = tower.get("enemyRecord") if isinstance(tower.get("enemyRecord"), Mapping) else None
        if enemy is not None and _int(enemy.get("floor")) != floor:
            enemy = None
        actors = [dict(m.get("actor") or {}) for m in members]
        uids = [_int(m.get("actorUid")) if m.get("actor") else 0 for m in members]
        ids = [_int(actor.get("id")) for actor in actors]
        uid, created = self._insert_run(profile, "tower", dungeon_id, group_id=group_id, actor_uids=uids,
                                        extra={"floor": floor})
        return 200, {
            "dungeonUid": uid,
            "baseInfo": self._base_info(profile, "tower", dungeon_id, ids, uids, created, floor=floor, actors=actors),
            "resChange": [],
            "enemyRecord": {"hp": _ints((enemy or {}).get("hp")), "card": _ints((enemy or {}).get("card"))},
        }

    def _tower_balance(self, profile: Mapping[str, Any], run: Mapping[str, Any], active: Any,
                       won: bool) -> dict[str, Any]:
        """Own HP/death from teamList[team 1].statistic.recordHpList (seat
        order = member index, FightHelp.recordTeamHp) plus its energySp/Mp;
        a lost floor keeps the enemy recordHpList for the next attempt."""

        floor = _int((run.get("extra") or {}).get("floor"), 1)
        dungeon_id = int(run["dungeon_id"])
        state = self._state(profile)
        tower = self._tower_state(profile, state)
        own_team, _, own_stat, enemy_stat = _team_statistics(active)
        group = (tower.get("confirmed") or {}).get(str(run["group_id"])) if tower.get("locked") else None
        if isinstance(group, dict):
            hp_list = list(own_stat.recordHpList) if own_stat is not None else []
            for member in group.get("members") or []:
                if not member.get("actor"):
                    continue
                index = _int(member.get("index"))
                if index < len(hp_list):
                    member["hp"] = max(0, int(hp_list[index]))
                    member["dead"] = member["hp"] <= 0
                elif not won:
                    member["hp"], member["dead"] = 0, True
            if own_team is not None:
                group["energySp"], group["energyMp"] = int(own_team.energySp), int(own_team.energyMp)
        first = False
        if won:
            first = floor > _int(tower.get("towerFloorRecord"))
            if group is not None and _int(tower.get("towerFloor")) == floor:
                tower["towerFloor"] = floor + 1
            tower["towerFloorRecord"] = max(_int(tower.get("towerFloorRecord")), floor)
            tower["enemyRecord"] = None
        elif group is not None:
            enemy_hp = [int(value) for value in enemy_stat.recordHpList] if enemy_stat is not None else []
            tower["enemyRecord"] = {"floor": floor, "hp": enemy_hp, "card": []} if enemy_hp else None
        self._save(profile, state)
        res_change: list[dict[str, Any]] = []
        if won:
            res_change = self._grant(profile, (DUNGEON_REWARD.get(dungeon_id) or {}).get("dropDisplay") or [])
            tasks = getattr(self.game, "tasks", None)
            if tasks is not None:
                tasks.on_battle_win(profile, dungeon_id, 0, mode="tower", floor=floor, stats=self._battle_stats(active),
                                    actor_uids=run.get("actor_uids") or (), dungeon_type=DUNGEON_TYPE["tower"],
                                    activity_type=ACTIVITY_TYPE["tower"], activity_id=int(run.get("activity_id") or 0))
        return self._result(profile, won=won, res_change=res_change, first=first,
                            towerFloor=_int(tower.get("towerFloor")))

    def _tower_wipe(self, profile: Mapping[str, Any], body: Any) -> Response:
        """POST fci/tower/wipe/ {wipeToFloor}: free (or paid, TowerFloor.
        wipeCost) skip to a checkpoint, paying every skipped floor's drops."""

        target = _int(self._request(body, "ApiTowerWipe").get("wipeToFloor"))
        state = self._state(profile)
        tower = self._tower_state(profile, state)
        free, pay = self._tower_wipe_floors(_int(tower.get("towerFloorRecord")))
        if _int(tower.get("towerFloor")) != 0 or target <= 0 or target not in (free, pay):
            return 409, {"error": "tower_wipe_unavailable", "wipeToFloor": target}
        paid: list[dict[str, Any]] = []
        if target == pay and target != free:
            cost = _res(TOWER_FLOORS[target].get("wipeCost"))
            if cost["type"] and cost["count"] > 0:
                paid = self._pay(profile, [{**cost, "count": -cost["count"]}])
                if paid is None:
                    return 409, {"error": "insufficient_resource", "resChange": []}
        drops = []
        for floor in range(1, target + 1):
            row = DUNGEON_REWARD.get(_int((TOWER_FLOORS.get(floor) or {}).get("dungeonId"))) or {}
            drops.extend(row.get("dropDisplay") or [])
        tower["towerFloor"] = target + 1
        self._save(profile, state)
        return 200, {"towerFloor": target + 1, "resChange": paid + self._grant(profile, drops)}

    def _tower_floor_reward(self, profile: Mapping[str, Any], body: Any) -> Response:
        """POST fci/tower/floor-reward/ {floorReward}: one-time
        TowerFloorReward for a reached best floor -> {resChange, rewardGot}."""

        floor = _int(self._request(body, "ApiTowerFloorReward").get("floorReward"))
        state = self._state(profile)
        tower = self._tower_state(profile, state)
        got = sorted(set(_ints(tower.get("rewardGot"))))
        if floor not in TOWER_FLOOR_REWARDS or floor > _int(tower.get("towerFloorRecord")):
            return 409, {"error": "tower_reward_locked", "floorReward": floor}
        if floor in got:
            return 200, {"resChange": [], "rewardGot": got}
        got = sorted(got + [floor])
        tower["rewardGot"] = got
        self._save(profile, state)
        return 200, {"resChange": self._grant(profile, TOWER_FLOOR_REWARDS[floor]), "rewardGot": got}

    def redpoint_fields(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Window_City reads fci/redpoint/data.towerSummaryResult."""

        state = self._state(profile)
        summary = self._tower_summary(profile, self._tower_state(profile, state))
        self._save(profile, state)
        return {"towerSummaryResult": summary}

    # ================================================== deploy (type 10)
    # Window_Deploy: GET fci/deploy/mission/ {missionInfo[{id, roleid[],
    # status, startTimestamp, endTimestamp}], accomplishNum}; the ongoing
    # cap is DeployCount[level].missionCount (DoDeploy).  DeployMissionDetail:
    # POST fci/deploy/deploymissionattend/ {actorUid[], id} -> {attendResult,
    # isPlayStory, resChange}; .../deploymissiondiscard/ {id};
    # .../deploymissionspeedup/ {id, cost{type,id,param,count}} (endTimestamp
    # -= ItemInfo.param); .../deploymissionreward/ {id} -> {rewardResult,
    # resChange, favourChange}.  Refresh = fci/resbuyprice/resbuy/ type 10.
    DEPLOY_OFFER_COUNT = 8  # local policy: missions offered per day

    @staticmethod
    def _role_params(condition: Mapping[str, Any]) -> list[int]:
        text = str(condition.get("param", "") or "")
        out = []
        for part in text.split("&"):
            try:
                out.append(int(float(part)))
            except ValueError:
                continue
        return out

    def _owned_roles(self, profile: Mapping[str, Any]) -> set[int]:
        state = self.game.systems._state(profile)
        return {ACTOR_ROLE.get(int(r["id"]), 0) for r in actor_store.ordered(profile, state)} - {0}

    def _mission_feasible(self, mission_id: int, roles: set[int]) -> bool:
        """Deploy.CheckAvailableMission for an unstarted mission."""

        row = DEPLOY_MISSIONS.get(mission_id) or {}
        available = 0
        for condition in row.get("roleCon") or []:
            kind = _int(condition.get("type"))
            if kind == 1:
                params = self._role_params(condition)
                available += 1 if params and params[0] in roles else 0
            elif kind == 0:
                available = len(roles)
                break
        return available >= max(1, _int(row.get("needRoleNum"), 1))

    def _offer_missions(self, profile: Mapping[str, Any], exclude: set[int], count: int) -> list[dict[str, Any]]:
        roles = self._owned_roles(profile)
        pool = [mission_id for mission_id in sorted(DEPLOY_MISSIONS) if mission_id not in exclude]
        self.rng.shuffle(pool)
        pool.sort(key=lambda mission_id: 0 if self._mission_feasible(mission_id, roles) else 1)
        return [{"id": mission_id, "roleid": [], "status": 0, "startTimestamp": 0, "endTimestamp": 0}
                for mission_id in pool[:max(0, count)]]

    def _deploy_state(self, profile: Mapping[str, Any], state: dict[str, Any], *, reroll: bool = False) -> dict[str, Any]:
        """Daily refresh (06:00 Beijing): started missions stay, the rest are
        re-offered and accomplishNum restarts."""

        deploy = state["deploy"]
        today = self._day()
        if period_due(deploy.get("day"), today) or reroll:
            kept = [m for m in deploy.get("missions") or [] if _int(m.get("startTimestamp"))]
            fresh = self._offer_missions(profile, {_int(m.get("id")) for m in kept},
                                         self.DEPLOY_OFFER_COUNT - len(kept))
            deploy["missions"] = kept + fresh
            if period_due(deploy.get("day"), today):
                deploy["accomplishNum"] = 0
            deploy["day"] = max(_int(deploy.get("day")), today)
        return deploy

    def _deploy_payload(self, deploy: Mapping[str, Any]) -> dict[str, Any]:
        now = self._now()
        missions = []
        for mission in deploy.get("missions") or []:
            start, end = _int(mission.get("startTimestamp")), _int(mission.get("endTimestamp"))
            status = 0 if not start else (2 if end <= now else 1)
            missions.append({"id": _int(mission.get("id")), "roleid": _ints(mission.get("roleid")), "status": status,
                             "startTimestamp": start, "endTimestamp": end})
        return {"missionInfo": missions, "accomplishNum": _int(deploy.get("accomplishNum"))}

    def _deploy_refresh_hook(self, profile: Mapping[str, Any], params: int = 0) -> list[dict[str, Any]]:
        state = self._state(profile)
        self._deploy_state(profile, state, reroll=True)
        self._save(profile, state)
        return []

    def _deploy_http(self, method: str, sub: list[str], body: Any, profile: Mapping[str, Any]) -> Optional[Response]:
        if sub == ["mission"] and method == "GET":
            # RestifizerManager syncs this at every login: top up the daily
            # tickets server-side as early as possible.
            self.refresh_daily_items(profile)
            state = self._state(profile)
            deploy = self._deploy_state(profile, state)
            self._save(profile, state)
            return 200, self._deploy_payload(deploy)
        if len(sub) != 1 or method != "POST":
            return None
        action = {"deploymissionattend": self._deploy_attend, "deploymissiondiscard": self._deploy_discard,
                  "deploymissionspeedup": self._deploy_speedup, "deploymissionreward": self._deploy_reward}.get(sub[0])
        if action is None:
            return None
        request = self._request(body)
        state = self._state(profile)
        deploy = self._deploy_state(profile, state)
        mission = next((m for m in deploy["missions"] if _int(m.get("id")) == _int(request.get("id"))), None)
        if mission is None:
            self._save(profile, state)
            return 404, {"error": "deploy_mission_not_found", "id": _int(request.get("id"))}
        return action(profile, state, deploy, mission, request)

    def _deploy_attend(self, profile, state, deploy, mission, request) -> Response:
        row = DEPLOY_MISSIONS.get(_int(mission.get("id"))) or {}
        if _int(mission.get("startTimestamp")):
            return 409, {"error": "deploy_mission_started"}
        if _int(deploy.get("accomplishNum")) >= _int(MISC.get("maxAccomplishDeployNum"), 40):
            return 409, {"error": "deploy_accomplish_limit"}
        level = max(1, _int(profile.get("level"), 1))
        cap = DEPLOY_COUNT.get(level) or max(DEPLOY_COUNT.values(), default=2)
        if sum(1 for m in deploy["missions"] if _int(m.get("startTimestamp"))) >= cap:
            return 409, {"error": "deploy_count_limit", "missionCount": cap}
        actors = self.game.systems._state(profile)
        roles = []
        for uid in _ints(request.get("actorUid")):
            record = actor_store.get(profile, actors, uid)
            role = ACTOR_ROLE.get(int(record["id"]), 0) if record is not None else 0
            if role and role not in roles:
                roles.append(role)
        busy = {role for m in deploy["missions"] if _int(m.get("startTimestamp")) for role in _ints(m.get("roleid"))}
        if len(roles) < max(1, _int(row.get("needRoleNum"), 1)) or busy & set(roles):
            return 409, {"error": "deploy_roles_invalid"}
        paid = self._pay(profile, _costs(row, "cost"))
        if paid is None:
            return 409, {"error": "insufficient_resource", "resChange": []}
        now = self._now()
        mission.update(roleid=roles, status=1, startTimestamp=now, endTimestamp=now + max(0, _int(row.get("time"))))
        self._save(profile, state)
        return 200, {"attendResult": True, "isPlayStory": False, "resChange": paid}

    def _deploy_discard(self, profile, state, deploy, mission, request) -> Response:
        """Give up: the mission returns to unstarted (no refund)."""

        mission.update(roleid=[], status=0, startTimestamp=0, endTimestamp=0)
        self._save(profile, state)
        return 200, {"discardResult": True, "resChange": []}

    def _deploy_speedup(self, profile, state, deploy, mission, request) -> Response:
        cost = _res(request.get("cost"))
        seconds = DEPLOY_SPEEDUP_ITEMS.get(cost["id"], 0)
        if not _int(mission.get("startTimestamp")) or cost["type"] != RES_ITEM or seconds <= 0:
            return 409, {"error": "deploy_speedup_invalid"}
        spent = self._change_item(profile, cost["id"], -1)
        if spent is None:
            return 409, {"error": "insufficient_resource", "resChange": []}
        mission["endTimestamp"] = max(_int(mission.get("startTimestamp")), _int(mission.get("endTimestamp")) - seconds)
        self._save(profile, state)
        return 200, {"speedupResult": True, "resChange": [spent]}

    def _deploy_reward(self, profile, state, deploy, mission, request) -> Response:
        row = DEPLOY_MISSIONS.get(_int(mission.get("id"))) or {}
        start, end = _int(mission.get("startTimestamp")), _int(mission.get("endTimestamp"))
        if not start or end > self._now() + 2:
            return 409, {"error": "deploy_mission_not_finished"}
        roles = _ints(mission.get("roleid"))
        deploy["missions"] = [m for m in deploy["missions"] if m is not mission]
        deploy["accomplishNum"] = _int(deploy.get("accomplishNum")) + 1
        self._save(profile, state)
        rewards = [_res(item) for item in row.get("reward") or []]
        exp = sum(item["count"] for item in rewards if item["type"] == RES_PLAYER_EXP)
        changes = [self._grant_player_exp(profile, exp)[1]] if exp else []
        changes += self._grant(profile, rewards)
        favour_changes = self._deploy_favour(profile, roles, max(0, end - start))
        tasks = getattr(self.game, "tasks", None)
        if tasks is not None:
            tasks.record(profile, "DeployDoneM")
            for role in roles:
                tasks.record(profile, "RoleIdP0Deploy", param=role)
        achievements = getattr(self.game, "achievements", None)
        if achievements is not None and hasattr(achievements, "record"):
            achievements.record(profile, "DeployDoneM")
        return 200, {"rewardResult": True, "resChange": changes, "favourChange": favour_changes}

    def _deploy_favour(self, profile: Mapping[str, Any], roles: list[int], seconds: int) -> list[dict[str, Any]]:
        """Each deployed role gains 1 favour per Misc.deployFavourCycle."""

        amount = seconds // max(1, _int(MISC.get("deployFavourCycle"), 3600))
        features = getattr(self.game, "profile_features", None)
        if amount <= 0 or features is None or not hasattr(features, "add_role_favour"):
            return []
        try:
            before = {_int(n.get("roleId")): _int(n.get("favourLevel")) for n in features.role_notepads(profile)}
        except Exception:
            before = {}
        changes = []
        for role in roles:
            try:
                notepad = features.add_role_favour(profile, role, amount)
            except Exception:
                notepad = None
            if notepad:
                changes.append({"roleId": role, "favour": _int(notepad.get("favour")),
                                "favourLevel": _int(notepad.get("favourLevel")),
                                "oldFavourLevel": before.get(role, _int(notepad.get("favourLevel")))})
        return changes

    # ================================================== triggered (type 31)
    # Window_TriggeredEvent: GET fci/triggered-event/ {eventInfo[EventInfo],
    # dailyTriggeredEventFinish, displayTriggeredEventTicket}; POST {ticketId:
    # Misc.triggeredEventTickets[1] = 80009} searches one event (same shape
    # plus resChange).  Window_City reads PB.get("DungeonReward",
    # eventInfo[1].eventId).costWin[1], so events are dungeon ids (8001...).
    # TriggeredActorGroup: POST fci/triggered-dungeon/ {eventKey,
    # selectActorGroupId, activityType 31, aura*}; a win spends costWin
    # (ticket 80006, daily +5 cap 5) and counts toward the 5 finishes/day.
    TRIGGER_TICKET = (_ints(MISC.get("triggeredEventTickets")) or [80009])[0]
    TRIGGER_LIFETIME = _int(MISC.get("triggeredEventLastTime"), 10800) or 10800
    TRIGGER_DAILY_FINISH = _int(MISC.get("dayTriggeredEventFinishLimit"), 5) or 5
    TRIGGER_MAX_OWN = _int(MISC.get("maxTriggeredEventOwnCount"), 10) or 10
    TRIGGER_PASSIVE_DAILY = _int(MISC.get("dayPassiveTriggeredEventLimit"), 3) or 3
    TRIGGER_PASSIVE_INTERVAL = _int(MISC.get("triggeredEventPassiveInterval"), 300)

    def _triggered_state(self, state: dict[str, Any]) -> dict[str, Any]:
        trig = state["triggered"]
        today, now = self._day(), self._now()
        if period_due(trig.get("day"), today):
            trig.update(day=today, finish=0, passiveCount=0)
        trig["events"] = [event for event in trig.get("events") or []
                          if _int(event.get("createTime")) + _int(event.get("lastTime")) > now]
        return trig

    def _triggered_payload(self, profile: Mapping[str, Any], trig: Mapping[str, Any]) -> dict[str, Any]:
        return {"eventInfo": [dict(event) for event in trig.get("events") or []],
                "dailyTriggeredEventFinish": _int(trig.get("finish")),
                "displayTriggeredEventTicket": self._item_count(profile, self.TRIGGER_TICKET) > 0}

    def _roll_pool(self, pool: Mapping[str, Any]) -> int:
        """TriggeredEvent.event weights (per 10000); an entry without id is
        "nothing happens"."""

        entries = [(e, _int(e.get("probability"))) for e in pool.get("event") or [] if isinstance(e, Mapping)]
        total = sum(weight for _, weight in entries)
        if total <= 0:
            return 0
        roll = self.rng.randrange(total)
        for entry, weight in entries:
            if roll < weight:
                return _int(entry.get("id")) if _int(entry.get("type"), 1) == 1 else 0
            roll -= weight
        return 0

    @staticmethod
    def _pool_for(level: int, *, active: bool, series: int = 0, ticket: int = 0) -> Optional[Mapping[str, Any]]:
        """Highest-level pool of a family (poolId // 100) the player reached."""

        candidates = [
            pool for pool_id, pool in TRIGGERED_POOLS.items()
            if bool(pool.get("isActive")) == active and 0 < _int(pool.get("level")) <= level
            and (not series or pool_id // 100 == series) and (not ticket or _int(pool.get("ticketId")) == ticket)
        ]
        return max(candidates, key=lambda pool: _int(pool.get("level")), default=None)

    def _new_event(self, profile: Mapping[str, Any], trig: dict[str, Any], event_id: int) -> dict[str, Any]:
        trig["seq"] = _int(trig.get("seq")) + 1
        now = self._now()
        event = {
            "eventId": int(event_id), "eventType": 1, "senderId": str(profile.get("player_id", "") or ""),
            "senderName": str(profile.get("name", "") or ""), "createTime": now, "lastTime": self.TRIGGER_LIFETIME,
            "friendSend": False, "eventKey": f"{int(profile['account_id'])}-{trig['seq']}-{now}",
            "eventLevel": _int((DUNGEON_REWARD.get(int(event_id)) or {}).get("unlockLevel")),
        }
        trig["events"].append(event)
        return event

    def trigger_passive_event(self, profile: Mapping[str, Any], dungeon_id: int) -> Optional[dict[str, Any]]:
        """Roll DungeonReward.triggeredEventPool after a won stage (the pool
        family at the player's level; Misc daily/interval/own limits).
        Returns the new EventInfo for balance ``eventInfo`` or None."""

        pool_id = _int((DUNGEON_REWARD.get(int(dungeon_id)) or {}).get("triggeredEventPool"))
        pool = self._pool_for(max(1, _int(profile.get("level"), 1)), active=False, series=pool_id // 100) if pool_id else None
        if pool is None:
            return None
        state = self._state(profile)
        trig = self._triggered_state(state)
        now = self._now()
        if (_int(trig.get("passiveCount")) >= self.TRIGGER_PASSIVE_DAILY or len(trig["events"]) >= self.TRIGGER_MAX_OWN
                or now - _int(trig.get("lastPassive")) < self.TRIGGER_PASSIVE_INTERVAL):
            return None
        event_id = self._roll_pool(pool)
        if not event_id or event_id not in DUNGEON_REWARD:
            return None
        event = self._new_event(profile, trig, event_id)
        trig["passiveCount"] = _int(trig.get("passiveCount")) + 1
        trig["lastPassive"] = now
        self._save(profile, state)
        return dict(event)

    def _triggered_event_http(self, method: str, sub: list[str], body: Any,
                              profile: Mapping[str, Any]) -> Optional[Response]:
        if sub or method not in {"GET", "POST"}:
            return None
        self.refresh_daily_items(profile)
        state = self._state(profile)
        trig = self._triggered_state(state)
        changes: list[dict[str, Any]] = []
        if method == "POST":
            ticket = _int(self._request(body, "ApiTriggeredEvent").get("ticketId")) or self.TRIGGER_TICKET
            pool = self._pool_for(max(1, _int(profile.get("level"), 1)), active=True, ticket=ticket)
            if pool is not None and len(trig["events"]) < self.TRIGGER_MAX_OWN:
                spent = self._change_item(profile, ticket, -1)
                if spent is not None:
                    changes.append(spent)
                    event_id = self._roll_pool(pool)
                    if event_id in DUNGEON_REWARD:
                        self._new_event(profile, trig, event_id)
        self._save(profile, state)
        payload = self._triggered_payload(profile, trig)
        if method == "POST":
            payload["resChange"] = changes
        return 200, payload

    def _triggered_dungeon_http(self, method: str, sub: list[str], body: Any,
                                profile: Mapping[str, Any]) -> Optional[Response]:
        if not sub and method == "POST":
            return self._triggered_create(profile, body)
        result = self._run_routes(method, sub, body, profile)
        if result is None and sub and sub[0].isdigit() and method in {"DELETE", "PATCH"}:
            return 200, {}
        return result

    def _triggered_create(self, profile: Mapping[str, Any], body: Any) -> Response:
        request = self._request(body)
        refreshed = self.refresh_daily_items(profile)
        state = self._state(profile)
        trig = self._triggered_state(state)
        self._save(profile, state)
        key = str(request.get("eventKey") or "")
        event = next((e for e in trig["events"] if str(e.get("eventKey")) == key), None)
        if event is None:
            return 409, {"error": "triggered_event_expired", "eventKey": key}
        if _int(trig.get("finish")) >= self.TRIGGER_DAILY_FINISH:
            return 409, {"error": "triggered_event_daily_limit"}
        dungeon_id = _int(event.get("eventId"))
        row = DUNGEON_REWARD.get(dungeon_id) or {}
        if _int(row.get("unlockLevel")) > _int(profile.get("level"), 1):
            return 409, {"error": "level_too_low", "unlockLevel": _int(row.get("unlockLevel"))}
        if not self._can_pay(profile, _costs(row, "costWin")):
            return 409, {"error": "not_enough_triggered_ticket", "resChange": refreshed}
        actor_ids, actor_uids = self._fight_team(profile, request.get("selectActorGroupId"))
        if not any(actor_uids):
            return 409, {"error": "profile_has_no_actors"}
        aura = self._aura(profile, request)
        uid, created = self._insert_run(profile, "triggered", dungeon_id, group_id=_int(request.get("selectActorGroupId")),
                                        actor_uids=actor_uids, extra={"eventKey": key, "aura": aura})
        return 200, {
            "dungeonUid": uid,
            "baseInfo": self._base_info(profile, "triggered", dungeon_id, actor_ids, actor_uids, created,
                                        aura=aura, event_key=key),
            "resChange": refreshed,
        }

    def _triggered_balance(self, profile: Mapping[str, Any], run: Mapping[str, Any], active: Any,
                           won: bool) -> dict[str, Any]:
        """A win closes the event, spends costWin and pays dropDisplay
        (dropDisplayFirst the first time that dungeon is cleared); a loss
        keeps the event until it expires."""

        dungeon_id = int(run["dungeon_id"])
        row = DUNGEON_REWARD.get(dungeon_id) or {}
        level_before, exp_entry = self._grant_player_exp(profile, 0)
        if not won:
            return self._result(profile, won=False, res_change=[exp_entry], level_before=level_before)
        key = str((run.get("extra") or {}).get("eventKey") or "")
        state = self._state(profile)
        trig = self._triggered_state(state)
        trig["events"] = [event for event in trig["events"] if str(event.get("eventKey")) != key]
        trig["finish"] = _int(trig.get("finish")) + 1
        cleared = set(_ints(trig.get("cleared")))
        first = dungeon_id not in cleared
        trig["cleared"] = sorted(cleared | {dungeon_id})
        self._save(profile, state)
        ticket = self._pay(profile, _costs(row, "costWin"), clamp=True) or []
        drops = row.get("dropDisplayFirst") if first and row.get("dropDisplayFirst") else row.get("dropDisplay")
        exp_changes = self._grant_actor_exp(profile, dungeon_id, run.get("actor_uids") or [], 1)
        grants = self._grant(profile, drops or [])
        tasks = getattr(self.game, "tasks", None)
        if tasks is not None:
            tasks.on_battle_win(profile, dungeon_id, 0, mode="triggered", stats=self._battle_stats(active),
                                actor_uids=run.get("actor_uids") or (), dungeon_type=DUNGEON_TYPE["triggered"],
                                activity_type=ACTIVITY_TYPE["triggered"], activity_id=int(run.get("activity_id") or 0))
        return self._result(profile, won=True, res_change=[exp_entry] + ticket + grants, exp_changes=exp_changes,
                            first=first, level_before=level_before)
