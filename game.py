"""Minimal, persistent local game state for the original LJJC client.

This module implements only the authenticated local guest seam.  It uses the
extracted protobuf descriptor set to build the real bootstrap messages used by
``PushService``; it does not contact Tencent or claim that a third-party
account has logged in.  Shop and recharge calls are handled by the separate
local-only ``LocalEconomy`` sandbox and never contact an external payment
provider.

The production local defaults are explicitly marked as local policy in
``data/local-defaults.json``: actor 1001, scene 0, and descriptor-backed
resource types 6/7.  Observed ActorInfo rows are used for actor attributes;
initial resources and progression remain local policy because the original
PlayerInitConfig is unavailable.
Callers can pass ``None``/empty lists to retain an empty bootstrap while
investigating a different profile.
"""

from __future__ import annotations

from server.core.reset import ResetPolicy
from server.core.atomic import AtomicMutations

import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from google.protobuf import descriptor_pb2, descriptor_pool, empty_pb2, duration_pb2, json_format, message_factory, timestamp_pb2
from google.protobuf.message import DecodeError

from server.core.clock import recover_resource

try:
    from .entering import handle_http as handle_entering_http
    from .client_compat import handle_http as handle_client_compat, unwrap_reply as unwrap_client_reply
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from entering import handle_http as handle_entering_http
    from client_compat import handle_http as handle_client_compat, unwrap_reply as unwrap_client_reply
try:
    from . import actors as actor_store
    from .equipment import apply_worn_equipment, fill_equip_package
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store
    from equipment import apply_worn_equipment, fill_equip_package
try:
    from .tasks import LocalTasks
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from tasks import LocalTasks
try:
    from .dungeon import LocalDungeons
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from dungeon import LocalDungeons
try:
    from .events import LocalEvents
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from events import LocalEvents
try:
    from .economy import LocalEconomy, apply_energy_recovery
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from economy import LocalEconomy, apply_energy_recovery
try:
    from .systems import LocalSystems
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from systems import LocalSystems
try:
    from .social import LocalSocial
    from .mall import LocalMall
    from .local_mailbox import LocalMailbox
    from .achievements import LocalAchievements
    from .activity_modes import LocalActivityModes
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from social import LocalSocial
    from mall import LocalMall
    from local_mailbox import LocalMailbox
    from achievements import LocalAchievements
    from activity_modes import LocalActivityModes
try:
    from .realtime_pvp import LocalRealtimePvp
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from realtime_pvp import LocalRealtimePvp
try:
    from .profile_features import LocalProfileFeatures
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from profile_features import LocalProfileFeatures
try:
    from .welfare_features import LocalWelfareFeatures
    from .maze import LocalMaze
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from welfare_features import LocalWelfareFeatures
    from maze import LocalMaze
try:
    from .storage.compat import (
        configure_legacy_connection,
        ensure_unified_store,
        resolve_unified_store_path,
        shared_store_lock,
    )
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from storage.compat import (
        configure_legacy_connection,
        ensure_unified_store,
        resolve_unified_store_path,
        shared_store_lock,
    )


Body = bytes | bytearray | str | None
Response = Tuple[int, Dict[str, Any]]
Message = Tuple[str, bytes]


_DEFAULTS_PATH = Path(__file__).parent / "data" / "local-defaults.json"
_ACTOR_INFO_PATH = Path(__file__).parent / "data" / "config" / "ActorInfo.json"
_PLAYER_LEVEL_INIT_PATH = Path(__file__).parent / "data" / "config" / "PlayerLevelInitConfig.json"
_ECONOMY_POLICY_PATH = Path(__file__).parent / "data" / "server-only" / "economy.yaml"
_FALLBACK_LOCAL_DEFAULTS: Dict[str, Any] = {
    "scene_id": 0,
    "actor_ids": [1001],
    "resources": [
        {"type": 6, "id": 0, "count": 120, "param": 0},
        {"type": 7, "id": 0, "count": 20000, "param": 0},
    ],
}


def _load_energy_policy() -> Dict[str, int]:
    """Load the local power recovery rule from the reviewed economy config.

    The retired service's recovery table is unavailable, so this policy is
    explicitly local and comes from ``data/server-only/economy.yaml``.  A
    conservative fallback keeps old installs readable if the optional config
    file is absent or malformed.
    """

    fallback = {"type": 6, "id": 0, "param": 0, "capacity": 120, "recovery_seconds": 360, "amount": 1}
    try:
        import yaml

        raw = yaml.safe_load(_ECONOMY_POLICY_PATH.read_text(encoding="utf-8"))
        resource = raw.get("resources", {}).get("player_power", {}) if isinstance(raw, Mapping) else {}
        if not isinstance(resource, Mapping):
            return fallback
        value = dict(fallback)
        value["type"] = int(resource.get("type", value["type"]))
        value["capacity"] = max(0, int(resource.get("capacity", value["capacity"])))
        value["recovery_seconds"] = max(1, int(resource.get("recoverySeconds", value["recovery_seconds"])))
        value["amount"] = max(1, int(resource.get("recoveryAmount", value["amount"])))
        return value
    except (ImportError, OSError, TypeError, ValueError, AttributeError):
        return fallback


def _load_local_defaults() -> Dict[str, Any]:
    """Load reviewed local-only defaults, with a safe code fallback."""

    try:
        data = json.loads(_DEFAULTS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("defaults must be an object")
        actor_ids = data.get("actor_ids")
        resources = data.get("resources")
        if not isinstance(actor_ids, list) or not all(isinstance(value, int) for value in actor_ids):
            raise ValueError("actor_ids must be integer list")
        if not isinstance(resources, list) or not all(isinstance(value, dict) for value in resources):
            raise ValueError("resources must be object list")
        return data
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(_FALLBACK_LOCAL_DEFAULTS)


def _load_actor_info() -> dict[int, dict[str, Any]]:
    """Load observed ActorInfo rows without treating a partial export as full."""

    try:
        payload = json.loads(_ACTOR_INFO_PATH.read_text(encoding="utf-8"))
        rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return result
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        try:
            actor_id = int(raw.get("id", 0))
        except (TypeError, ValueError):
            continue
        if actor_id <= 0:
            continue
        attrs: list[dict[str, Any]] = []
        for item in raw.get("attrs", []) or []:
            if not isinstance(item, Mapping):
                continue
            try:
                attr_type = int(item.get("type", 0))
                value = int(item.get("value", 0))
            except (TypeError, ValueError):
                continue
            if attr_type <= 0 or value < 0:
                continue
            attrs.append({
                "type": attr_type,
                "value": value,
                "isRatio": bool(item.get("isRatio", False)),
            })
        result[actor_id] = {
            "attrs": attrs,
            "quality": max(1, int(raw.get("quality", 1) or 1)),
        }
    return result


def _load_tutorial_steps(defaults: Mapping[str, Any]) -> list[dict[str, int]]:
    """Load tutorial IDs and sequence from the extracted table.

    The checked-in Tutorial table is a partial runtime sample (its metadata
    reports more observed rows than are stored), so using only those rows would
    silently remove later tutorial steps.  The fallback lives in
    ``data/local-defaults.json`` rather than in executable code and can be
    replaced when a complete table is recovered.
    """
    # Prefer the complete table exported from the client APK
    # (tools/export_client_tables.py Tutorial).  Window TutorialManager reads
    # ``m_allTutorial[step.id].status`` for every TutorialStep id, so a
    # missing id aborts TriggerTutorial and stops all later guidance.
    client_path = Path(__file__).parent / "data" / "client-tables" / "Tutorial.json"
    try:
        client_rows = json.loads(client_path.read_text(encoding="utf-8")).get("rows", [])
        client_steps = sorted(
            {
                int(row["id"]): int(row.get("sequence", row["id"]))
                for row in client_rows
                if isinstance(row, Mapping) and str(row.get("id", "")).isdigit()
            }.items(),
            key=lambda item: (item[1], item[0]),
        )
        if client_steps:
            return [{"id": tutorial_id, "sequence": sequence} for tutorial_id, sequence in client_steps]
    except (OSError, TypeError, ValueError, json.JSONDecodeError, AttributeError):
        pass
    config_path = Path(__file__).parent / "data" / "config" / "Tutorial.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        rows = payload.get("rows", []) if isinstance(payload, dict) else []
        observed = int(payload.get("observedCount", len(rows))) if isinstance(payload, dict) else len(rows)
        steps_by_id: dict[int, int] = {}
        for row in rows:
            if not isinstance(row, Mapping) or not str(row.get("id", "")).isdigit():
                continue
            tutorial_id = int(row["id"])
            try:
                sequence = int(row.get("sequence", tutorial_id))
            except (TypeError, ValueError):
                sequence = tutorial_id
            steps_by_id[tutorial_id] = sequence
        if steps_by_id and observed <= len(rows):
            return [
                {"id": tutorial_id, "sequence": sequence}
                for tutorial_id, sequence in sorted(steps_by_id.items(), key=lambda item: (item[1], item[0]))
            ]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    fallback = defaults.get("tutorial_ids", []) if isinstance(defaults, Mapping) else []
    if isinstance(fallback, list):
        values = sorted({int(value) for value in fallback if isinstance(value, int) and value > 0})
        if values:
            return [{"id": value, "sequence": index} for index, value in enumerate(values, start=1)]
    return []


def _load_initial_level(defaults: Mapping[str, Any]) -> int:
    """Read the first observed player level from the extracted config.

    ``PlayerInitConfig`` is unavailable in the captured APK, but the first
    ``PlayerLevelInitConfig`` row is present and explicitly records level 1.
    Using that row keeps profile creation data-driven while retaining a safe
    local fallback for older archives that do not include the table.
    """

    try:
        payload = json.loads(_PLAYER_LEVEL_INIT_PATH.read_text(encoding="utf-8"))
        rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
        if isinstance(rows, list) and rows and isinstance(rows[0], Mapping):
            level = int(rows[0].get("level", 0))
            if level > 0:
                return level
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    try:
        level = int(defaults.get("initial_level", 1))
        return level if level > 0 else 1
    except (AttributeError, TypeError, ValueError):
        return 1


def _normalise_resource_rows(value: Any) -> list[dict[str, int]]:
    """Return a safe, JSON-compatible resource list from a saved profile.

    Old local builds wrote the resource list as JSON and a crashed or manually
    interrupted write could leave one malformed row.  Login and passive
    recovery must remain available in that situation; silently skipping the
    malformed row is safer than turning a damaged save into a server error or
    allowing a negative balance to reach the client.
    """

    if not isinstance(value, list):
        return []
    result: list[dict[str, int]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        try:
            resource_type = int(raw.get("type", 0))
            resource_id = int(raw.get("id", 0))
            count = max(0, int(raw.get("count", 0)))
            param = int(raw.get("param", 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if resource_type < 0 or resource_id < 0 or param < 0:
            continue
        entry = {"type": resource_type, "id": resource_id, "count": count}
        # ``param`` was optional in the original JSON seam.  Preserve its
        # absence so a legacy profile does not change shape merely because it
        # was opened by a newer server.
        if "param" in raw:
            entry["param"] = param
        result.append(entry)
    return result


# PlayerHeadPhoto id 1 (getWay 1, freely available) is the default avatar.
DEFAULT_HEAD_PHOTO = 1

_LOCAL_DEFAULTS = _load_local_defaults()
_LOCAL_TUTORIAL_STEPS = _load_tutorial_steps(_LOCAL_DEFAULTS)
_LOCAL_TUTORIAL_IDS = [step["id"] for step in _LOCAL_TUTORIAL_STEPS]
_LOCAL_TUTORIAL_SEQUENCE = {step["id"]: step["sequence"] for step in _LOCAL_TUTORIAL_STEPS}
_LOCAL_DEFAULT_ACTOR_IDS = [int(value) for value in _LOCAL_DEFAULTS.get("actor_ids", [])]
_LOCAL_DEFAULT_RESOURCES = [dict(value) for value in _LOCAL_DEFAULTS.get("resources", [])]
_LOCAL_DEFAULT_SIGNBOARD = dict(_LOCAL_DEFAULTS.get("signboard", {"signboard": 101, "unlockedSignboardId": [101], "checkSignboardTime": 0}))
_LOCAL_DEFAULT_ACTOR_TEMPLATE = dict(_LOCAL_DEFAULTS.get("actor", {}))
_LOCAL_INITIAL_LEVEL = _load_initial_level(_LOCAL_DEFAULTS)
_ACTOR_INFO = _load_actor_info()


def _message_pool(descriptor_path: Path) -> descriptor_pool.DescriptorPool:
    """Load the extracted descriptor set, skipping only known bad candidates."""

    data = descriptor_pb2.FileDescriptorSet.FromString(descriptor_path.read_bytes())
    # The client's runtime descriptor (Data/descriptor, lua-protobuf) marks
    # repeated numeric scalar fields as PACKED and its decoder only accepts the
    # packed wire form.  The extracted descriptor set here leaves them unpacked,
    # so SerializeToString() emits unpacked repeats that the client misreads
    # (e.g. ApiChapterInfo.result -> ChapterSummary.new_dungeon_id throws
    # "un-finished bytes").  Force packed=true on every repeated numeric scalar
    # field so the server's wire output matches the client's schema.
    _PACKABLE_TYPES = frozenset({
        descriptor_pb2.FieldDescriptorProto.TYPE_INT32,
        descriptor_pb2.FieldDescriptorProto.TYPE_INT64,
        descriptor_pb2.FieldDescriptorProto.TYPE_UINT32,
        descriptor_pb2.FieldDescriptorProto.TYPE_UINT64,
        descriptor_pb2.FieldDescriptorProto.TYPE_SINT32,
        descriptor_pb2.FieldDescriptorProto.TYPE_SINT64,
        descriptor_pb2.FieldDescriptorProto.TYPE_FIXED32,
        descriptor_pb2.FieldDescriptorProto.TYPE_FIXED64,
        descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED32,
        descriptor_pb2.FieldDescriptorProto.TYPE_SFIXED64,
        descriptor_pb2.FieldDescriptorProto.TYPE_BOOL,
        descriptor_pb2.FieldDescriptorProto.TYPE_ENUM,
        descriptor_pb2.FieldDescriptorProto.TYPE_FLOAT,
        descriptor_pb2.FieldDescriptorProto.TYPE_DOUBLE,
    })

    def _force_packed(message_proto: descriptor_pb2.DescriptorProto) -> None:
        for field in message_proto.field:
            if (field.label == descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
                    and field.type in _PACKABLE_TYPES):
                field.options.packed = True
        for nested in message_proto.nested_type:
            _force_packed(nested)

    for file_descriptor in data.file:
        for message_proto in file_descriptor.message_type:
            _force_packed(message_proto)

    pool = descriptor_pool.Default()
    pending: list[descriptor_pb2.FileDescriptorProto] = []
    for file_descriptor in data.file:
        # A bytes-valued syntax is evidence of a concatenated metadata stream,
        # not a usable FileDescriptorProto.  Do not coerce it into a schema.
        if isinstance(file_descriptor.syntax, bytes):
            continue
        try:
            pool.FindFileByName(file_descriptor.name)
        except KeyError:
            pending.append(file_descriptor)
    while pending:
        remaining: list[descriptor_pb2.FileDescriptorProto] = []
        for file_descriptor in pending:
            try:
                pool.Add(file_descriptor)
            except Exception:
                remaining.append(file_descriptor)
        if len(remaining) == len(pending):
            # Optional activity descriptors may remain unavailable.  The
            # bootstrap graph is checked below and must still resolve.
            break
        pending = remaining
    return pool


def _class(pool: descriptor_pool.DescriptorPool, full_name: str):
    return message_factory.GetMessageClass(pool.FindMessageTypeByName(full_name))


def _auth_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    # The original client uses ``Authorization: Token <access-token>`` for
    # post-login FCI requests.  Keep AccountToken for the local account seam
    # and older test clients; both schemes still go through the same local
    # verifier and neither is accepted without a valid stored token.
    if len(parts) != 2 or parts[0].lower() not in {"token", "accounttoken"}:
        return None
    return parts[1].strip() or None


def _route(path: str) -> str:
    route = (path or "").split("?", 1)[0] or "/"
    if route != "/":
        route = route.rstrip("/") + "/"
    return route


def _body_object(body: Body) -> Mapping[str, Any]:
    """Decode the local JSON seam without treating arbitrary bytes as JSON."""

    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


class LocalGame:
    """Authenticated local player profile and protobuf login bootstrap."""

    def __init__(
        self,
        state_dir: str | Path,
        accounts: Any,
        *,
        descriptor_path: str | Path | None = None,
        clock: Optional[Callable[[], int]] = None,
        initial_actor_id: Optional[int] = _LOCAL_DEFAULT_ACTOR_IDS[0] if _LOCAL_DEFAULT_ACTOR_IDS else None,
        initial_actor_ids: Optional[Iterable[int]] = None,
        initial_resources: Optional[Iterable[Mapping[str, int]]] = _LOCAL_DEFAULT_RESOURCES,
        initial_level: Optional[int] = _LOCAL_INITIAL_LEVEL,
        scene_id: int = int(_LOCAL_DEFAULTS.get("scene_id", 0)),
        scene_random: bool = False,
        unified_store_path: str | Path | None = None,
        reset_policy: ResetPolicy | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.unified_store_path = resolve_unified_store_path(self.state_dir, unified_store_path)
        if self.unified_store_path is not None:
            ensure_unified_store(self.unified_store_path)
            self.db_path = self.unified_store_path
        else:
            self.db_path = self.state_dir / "game.sqlite3"
        self.accounts = accounts
        self._clock = clock or (lambda: int(time.time()))
        self.reset_policy = reset_policy or ResetPolicy()
        self.daily_key = lambda value=None: self.reset_policy.day_key(self._now() if value is None else int(value))
        self.energy_policy = _load_energy_policy()
        self._atomic = AtomicMutations(self)
        # Serialize all profile mutations for a database path, including when
        # two local game objects are opened by a test or a hot-reload tool.
        # SQLite's busy timeout handles external processes; this shared lock
        # prevents same-process lost updates before they reach SQLite.
        self._lock = shared_store_lock(self.db_path)
        self.initial_actor_id = initial_actor_id
        if initial_actor_ids is None:
            self.initial_actor_ids = [int(initial_actor_id)] if initial_actor_id is not None else []
        else:
            self.initial_actor_ids = [int(actor_id) for actor_id in initial_actor_ids]
            if initial_actor_id is None and self.initial_actor_ids:
                self.initial_actor_id = self.initial_actor_ids[0]
        self.initial_resources = [dict(item) for item in (initial_resources or [])]
        try:
            parsed_level = int(initial_level if initial_level is not None else _LOCAL_INITIAL_LEVEL)
        except (TypeError, ValueError, OverflowError):
            parsed_level = _LOCAL_INITIAL_LEVEL
        self.initial_level = max(1, parsed_level)
        self.scene_id = int(scene_id)
        self.scene_random = bool(scene_random)
        schema_path = Path(descriptor_path) if descriptor_path else Path(__file__).parent / "data" / "descriptor_set.pb"
        self._pool = _message_pool(schema_path)
        # Fail early if the bootstrap graph is not present; optional activity
        # descriptors are deliberately not part of this check.
        self._types = {
            name: _class(self._pool, name)
            for name in (
                "ScLoginBegin",
                "ScLoginTutorial",
                "DBGamePlayer.Actors",
                "DBGamePlayer.EquipPackage",
                "DBGamePlayer.ItemPackage",
                "ScLoginEnd",
                # ApiChapterInfo.result is the typed result payload carried
                # inside RestfulResponse.message (response type 1).  Keeping
                # this nested descriptor, rather than the JSON envelope,
                # preserves uint32 map keys for Unity's PB decoder.
                "ApiChapterInfo.result",
                # Chapter red-point bindings request each chapter detail as a
                # separate typed ``ApiChapterInfo.get`` resource.
                "ApiChapterInfo.get",
                # Standard Story/Normal scenes PATCH this exact message after
                # the client-side fight logic reaches a terminal state.
                "FightActiveData",
                # Normal chapter entry uses a wireformat ApiDungeon request
                # (Story entry uses the small JSON seam).  Keep the real
                # descriptor available so LocalDungeons can accept both
                # observed request shapes.
                "ApiDungeon",
                "ApiAutoFightConf",
                "ApiTutorial",
                # Formation support (助战) list: request {token} and the
                # repeated DungeonAssistantInfo reply.
                "ApiDungeonAssistants",
                "ApiDungeonAssistants.result",
            )
        }
        self._initialize()
        self.dungeons = LocalDungeons(self, clock=self._clock)
        # Shop and payment state lives in its own SQLite file.  The economy
        # handler receives the authenticated account alongside the profile so
        # resource changes are written back to game_profiles atomically.
        self.economy = LocalEconomy(
            self.state_dir,
            self,
            clock=self._clock,
            unified_store_path=self.unified_store_path,
        )
        # Sign-in and activity claims share the game profile database so their
        # resource deltas survive service restarts and remain visible in the
        # next login bootstrap.
        self.events = LocalEvents(self, clock=self._clock)
        # Actor/card/equipment/mail/relation/PVP state is local-only and
        # intentionally separate from the retired service's unavailable data.
        self.systems = LocalSystems(self, clock=self._clock)
        self.social = LocalSocial(self, clock=self._clock)
        from guild import LocalGuild
        self.guild = LocalGuild(self, clock=self._clock)
        # Mall: level/event gift packs and sandbox recharge (mall.py).
        self.mall = LocalMall(self, clock=self._clock)
        self.mailbox = LocalMailbox(self, clock=self._clock)
        # RacePVP/BurstLink use a separate deterministic local match state so
        # real-time rounds cannot collide with async-PVP or social records.
        self.realtime_pvp = LocalRealtimePvp(self, clock=self._clock)
        # Player profile, notice, collection and web-vote state is kept in a
        # separate local table so it can evolve without changing the legacy
        # bootstrap schema.  It is still in the same SQLite/WAL store.
        self.profile_features = LocalProfileFeatures(self, clock=self._clock)
        # Welfare tabs and the newly exposed Maze activity use isolated
        # profile-feature state so their local rules remain restart-safe.
        self.welfare = LocalWelfareFeatures(self, clock=self._clock)
        self.maze = LocalMaze(self, clock=self._clock)
        # Daily/main tasks from the client TaskInfo table (tasks.py).
        self.tasks = LocalTasks(self, clock=self._clock)
        # Achievements (历程) and collections (图鉴/novels).
        self.achievements = LocalAchievements(self, clock=self._clock)
        # Material/gold dungeon, tower, deploy, triggered events.
        self.activity_modes = LocalActivityModes(self, clock=self._clock)
        from server.core.redpoints import LocalRedpoints
        self.redpoints = LocalRedpoints(self)
        from cultivation import LocalCultivation
        self.cultivation = LocalCultivation(self, clock=self._clock)
        from tournament import LocalTournament
        self.tournament = LocalTournament(self)
        # ``notifier(profile, push_type)`` -> BasePushMessage; wired by the
        # server.  BaseInfoChange makes the client re-read fci/baseinfo/.
        self.base_push_notifier: Optional[Callable[[Mapping[str, Any], int], Any]] = None
        self._migrate_profiles()
        from server.core.reset_migration import migrate_reset_policy
        migrate_reset_policy(self)

    def _connect(self) -> sqlite3.Connection:
        borrowed = self.transaction_connection()
        if borrowed is not None:
            return borrowed
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        configure_legacy_connection(connection)
        return connection

    def atomic_mutation(self):
        return self._atomic.transaction()

    def transaction_connection(self):
        return self._atomic.borrowed()

    def after_commit(self, callback):
        return self._atomic.after_commit(callback)

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS game_profiles (
                    account_id INTEGER PRIMARY KEY,
                    player_id TEXT NOT NULL UNIQUE,
                    level INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    scene_id INTEGER NOT NULL,
                    scene_random INTEGER NOT NULL,
                    actor_id INTEGER,
                    resources_json TEXT NOT NULL,
                    actor_ids_json TEXT NOT NULL DEFAULT '[]',
                    signboard_json TEXT NOT NULL DEFAULT '{}',
                    tutorials_json TEXT NOT NULL DEFAULT '{}',
                    tutorial_steps_json TEXT NOT NULL DEFAULT '{}',
                    auto_fight_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(game_profiles)")
            }
            if "actor_ids_json" not in columns:
                connection.execute(
                    "ALTER TABLE game_profiles ADD COLUMN actor_ids_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "signboard_json" not in columns:
                connection.execute(
                    "ALTER TABLE game_profiles ADD COLUMN signboard_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "tutorials_json" not in columns:
                connection.execute(
                    "ALTER TABLE game_profiles ADD COLUMN tutorials_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "tutorial_steps_json" not in columns:
                connection.execute(
                    "ALTER TABLE game_profiles ADD COLUMN tutorial_steps_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "auto_fight_json" not in columns:
                connection.execute(
                    "ALTER TABLE game_profiles ADD COLUMN auto_fight_json TEXT NOT NULL DEFAULT '{}'"
                )
            # Recovery state is separate from the profile JSON so a legacy
            # profile can be upgraded without rewriting its saved resources.
            # The ledger table records every passive grant for diagnostics and
            # rollback/audit tooling; it is deliberately local-only.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_resource_recovery (
                    account_id INTEGER PRIMARY KEY,
                    resource_type INTEGER NOT NULL,
                    resource_id INTEGER NOT NULL DEFAULT 0,
                    param INTEGER NOT NULL DEFAULT 0,
                    capacity INTEGER NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    amount_per_interval INTEGER NOT NULL,
                    last_recovery_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_resource_ledger (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('grant','consume')),
                    resource_type INTEGER NOT NULL,
                    resource_id INTEGER NOT NULL,
                    param INTEGER NOT NULL,
                    delta INTEGER NOT NULL,
                    balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
                    reason TEXT NOT NULL,
                    idempotency_key TEXT,
                    created_at INTEGER NOT NULL,
                    UNIQUE(account_id, idempotency_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_local_resource_ledger_account_time "
                "ON local_resource_ledger(account_id, created_at, entry_id)"
            )

    def _migration_backup_path(self) -> Path:
        """Choose a non-overwriting backup path for a profile migration."""

        base = self.db_path.with_name(self.db_path.name + ".preconfig-migration.bak")
        if not base.exists():
            return base
        index = 1
        while True:
            candidate = self.db_path.with_name(
                self.db_path.name + f".preconfig-migration.{index}.bak"
            )
            if not candidate.exists():
                return candidate
            index += 1

    def _migrate_profiles(self) -> None:
        """Apply newly verified non-empty defaults without overwriting state.

        A profile created by an earlier build may have no actor/resources
        because runtime config was unavailable.  Once the caller supplies a
        verified config value, fill only those still-empty columns.  Existing
        actor IDs and resource lists are preserved.  A byte-for-byte SQLite
        backup is made before the first write and is never overwritten.
        """

        if self.initial_actor_id is None and not self.initial_resources:
            return
        updates: list[tuple[int, Optional[int], Optional[str], Optional[str], Optional[str]]] = []
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT account_id, actor_id, actor_ids_json, resources_json, signboard_json FROM game_profiles"
            ).fetchall()
            for row in rows:
                actor_id = row["actor_id"]
                try:
                    actor_ids = json.loads(row["actor_ids_json"] or "[]")
                except (TypeError, ValueError):
                    actor_ids = []
                resources_json = row["resources_json"]
                signboard_json = row["signboard_json"] or "{}"
                try:
                    resources = json.loads(resources_json)
                except (TypeError, ValueError):
                    # Malformed historical state is left untouched.
                    continue
                new_actor = actor_id
                new_actor_ids_json: Optional[str] = None
                new_resources_json: Optional[str] = None
                new_signboard_json: Optional[str] = None
                if new_actor is None and self.initial_actor_id is not None:
                    new_actor = int(self.initial_actor_id)
                if not actor_ids:
                    actor_ids_to_store = (
                        [int(new_actor)]
                        if new_actor is not None
                        else list(self.initial_actor_ids)
                    )
                    if actor_ids_to_store:
                        new_actor_ids_json = json.dumps(actor_ids_to_store, separators=(",", ":"))
                if resources == [] and self.initial_resources:
                    new_resources_json = json.dumps(self.initial_resources, separators=(",", ":"))
                try:
                    signboard = json.loads(signboard_json)
                except (TypeError, ValueError):
                    signboard = {}
                if not isinstance(signboard, dict) or not signboard.get("signboard"):
                    new_signboard_json = json.dumps(_LOCAL_DEFAULT_SIGNBOARD, separators=(",", ":"))
                if new_actor != actor_id or new_actor_ids_json is not None or new_resources_json is not None or new_signboard_json is not None:
                    # Keep the existing actor_id/resources semantics while
                    # carrying the expanded multi-actor list separately.
                    updates.append((int(row["account_id"]), new_actor, new_actor_ids_json, new_resources_json, new_signboard_json))
            if not updates:
                return
            backup_path = self._migration_backup_path()
            shutil.copy2(self.db_path, backup_path)
            now = self._now()
            for account_id, actor_id, actor_ids_json, resources_json, signboard_json in updates:
                assignments = ["actor_id = ?", "updated_at = ?"]
                params: list[Any] = [actor_id, now]
                if actor_ids_json is not None:
                    assignments.insert(1, "actor_ids_json = ?")
                    params.insert(1, actor_ids_json)
                if resources_json is not None:
                    assignments.insert(1, "resources_json = ?")
                    params.insert(1, resources_json)
                if signboard_json is not None:
                    assignments.insert(1, "signboard_json = ?")
                    params.insert(1, signboard_json)
                params.append(account_id)
                connection.execute(
                    f"UPDATE game_profiles SET {', '.join(assignments)} WHERE account_id = ?",
                    params,
                )

    def _now(self) -> int:
        return int(self._clock())

    def _recover_passive_resources(
        self,
        connection: sqlite3.Connection,
        account_id: int,
        resources: list[dict[str, Any]],
    ) -> int:
        """Apply elapsed local power recovery while a profile is read.

        ``resources_json`` remains the compatibility source consumed by the
        client.  A small recovery-state row provides a monotonic marker, and
        each non-zero grant is written to ``local_resource_ledger`` in the
        same SQLite transaction.  This makes login, reconnect, and any other
        profile read converge on one deterministic balance without requiring a
        background worker.
        """

        # Client rule (PlayerLevelInitConfig.incInfo): +1 power per 450 s up
        # to the level cap; a balance above the cap is kept, not clamped.
        now = self._now()
        apply_energy_recovery(connection, int(account_id), resources, now=now)
        return now

    def _profile(self, account: Mapping[str, Any]) -> Dict[str, Any]:
        account_id = int(account["accountId"])
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM game_profiles WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                now = self._now()
                player_id = f"local-{account_id}"
                resources_json = json.dumps(self.initial_resources, separators=(",", ":"))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO game_profiles
                    (account_id, player_id, level, name, scene_id, scene_random,
                     actor_id, resources_json, actor_ids_json, signboard_json,
                     tutorials_json, tutorial_steps_json, auto_fight_json, created_at, updated_at)
                    VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        player_id,
                        self.initial_level,
                        self.scene_id,
                        int(self.scene_random),
                        self.initial_actor_id,
                        resources_json,
                        json.dumps(self.initial_actor_ids, separators=(",", ":")),
                        json.dumps(_LOCAL_DEFAULT_SIGNBOARD, separators=(",", ":")),
                        json.dumps({str(tutorial_id): 0 for tutorial_id in _LOCAL_TUTORIAL_IDS}, separators=(",", ":")),
                        json.dumps({str(tutorial_id): 0 for tutorial_id in _LOCAL_TUTORIAL_IDS}, separators=(",", ":")),
                        json.dumps({"banedSkills": {}}, separators=(",", ":")),
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM game_profiles WHERE account_id = ?", (account_id,)
                ).fetchone()
            result = dict(row)
            if not str(result.get("name") or "").strip():
                # Profiles used to be created with an empty name, which the
                # client shows as a blank player name.  Default to the login
                # username (password accounts) or a numbered 零境使 name.
                username = str(account.get("username") or "").strip()
                default_name = username[:16] if username else f"零境使{account_id % 1000000:06d}"
                connection.execute(
                    "UPDATE game_profiles SET name = ?, updated_at = ? WHERE account_id = ?",
                    (default_name, self._now(), account_id),
                )
                result["name"] = default_name
            raw_resources = result.pop("resources_json")
            try:
                decoded_resources = json.loads(raw_resources)
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded_resources = []
            result["resources"] = _normalise_resource_rows(decoded_resources)
            # Repair malformed legacy JSON once, while keeping the account,
            # actor and progression columns untouched.  This makes later
            # logins deterministic and gives the recovery/ledger code a
            # canonical shape to operate on.
            if result["resources"] != decoded_resources:
                connection.execute(
                    "UPDATE game_profiles SET resources_json = ?, updated_at = ? WHERE account_id = ?",
                    (
                        json.dumps(result["resources"], separators=(",", ":")),
                        self._now(),
                        int(account_id),
                    ),
                )
            self._recover_passive_resources(connection, int(account_id), result["resources"])
            try:
                raw_actor_ids = json.loads(result.pop("actor_ids_json") or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_actor_ids = []
            result["actor_ids"] = []
            if isinstance(raw_actor_ids, list):
                for value in raw_actor_ids:
                    try:
                        actor_id = int(value)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if actor_id > 0 and actor_id not in result["actor_ids"]:
                        result["actor_ids"].append(actor_id)
            if not result["actor_ids"] and result["actor_id"] is not None:
                try:
                    actor_id = int(result["actor_id"])
                except (TypeError, ValueError, OverflowError):
                    actor_id = 0
                if actor_id > 0:
                    result["actor_ids"] = [actor_id]
            try:
                result["signboard"] = json.loads(result.pop("signboard_json") or "{}")
            except (TypeError, ValueError):
                result["signboard"] = {}
            try:
                raw_tutorials = json.loads(result.pop("tutorials_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_tutorials = {}
            result["tutorials"] = {}
            if isinstance(raw_tutorials, Mapping):
                for key, value in raw_tutorials.items():
                    try:
                        result["tutorials"][str(key)] = 1 if int(value) else 0
                    except (TypeError, ValueError, OverflowError):
                        continue
            for tutorial_id in _LOCAL_TUTORIAL_IDS:
                result["tutorials"].setdefault(str(tutorial_id), 0)
            try:
                raw_tutorial_steps = json.loads(result.pop("tutorial_steps_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_tutorial_steps = {}
            result["tutorial_steps"] = {}
            if isinstance(raw_tutorial_steps, Mapping):
                for key, value in raw_tutorial_steps.items():
                    try:
                        result["tutorial_steps"][str(key)] = max(0, int(value))
                    except (TypeError, ValueError, OverflowError):
                        continue
            for tutorial_id in _LOCAL_TUTORIAL_IDS:
                result["tutorial_steps"].setdefault(str(tutorial_id), 0)
            try:
                result["auto_fight"] = json.loads(result.pop("auto_fight_json") or "{}")
            except (TypeError, ValueError):
                result["auto_fight"] = {}
            if not isinstance(result["auto_fight"], dict):
                result["auto_fight"] = {}
            baned_skills = result["auto_fight"].get("banedSkills", {})
            clean_skills: dict[str, int] = {}
            if isinstance(baned_skills, Mapping):
                for key, value in baned_skills.items():
                    try:
                        parsed = int(value)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if (0 <= parsed <= 0xFFFFFFFF if str(key) in ('1', '2') else parsed in (0, 1)):
                        clean_skills[str(key)] = parsed
            result["auto_fight"]["banedSkills"] = clean_skills
            result["scene_random"] = bool(result["scene_random"])
            return result

    def _authorized(self, authorization: Optional[str]) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
        token = _auth_token(authorization)
        account = self.accounts.verify_access_token(token or "")
        if account is None:
            return None, (401, {"error": "invalid_account_token", "message": "local Token or AccountToken required"})
        return account, None

    # Base resource types per the client's WindowUtil (ResPlayerExp, Title,
    # Money/迷子, Power, Gold, HonourPoint, RelationPoint, GuildScore,
    # TowerScore, AchievementPoint, GachaPoint, MonthCardDays, PayMoney,
    # DungeonPlayTimesLimit).  The client reads fci/resource/<Type>_0 for
    # each of these without nil checks (e.g. TlogUtil.CollectBalanceData at
    # the end of every battle), so the original server always sent them.
    _BASE_RESOURCE_TYPES = (1, 3, 5, 6, 7, 9, 10, 17, 19, 21, 23, 24, 27, 28)

    def _resource_message(self, profile: Mapping[str, Any]):
        # Held balances come from the profile; base resources the player has
        # none of are sent as an explicit zero (no value is invented).
        resources = [dict(item) for item in profile["resources"]]
        present = {
            int(item.get("type", 0))
            for item in resources
            if int(item.get("id", 0) or 0) == 0
        }
        for resource_type in self._BASE_RESOURCE_TYPES:
            if resource_type not in present:
                resources.append({"type": resource_type, "id": 0, "count": 0, "param": 0})
        return resources

    def _tutorial_http(
        self,
        method: str,
        path: str,
        body: Body,
        profile: Mapping[str, Any],
    ) -> Optional[Response]:
        route = _route(path)
        prefix = "/fci/tutorial/"
        if not route.startswith(prefix):
            return None
        suffix = route[len(prefix) :].strip("/")
        method = method.upper()
        if suffix == "":
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            return 200, [
                {
                    "id": tutorial_id,
                    "sequence": int(_LOCAL_TUTORIAL_SEQUENCE.get(tutorial_id, tutorial_id)),
                    "status": int(profile["tutorials"].get(str(tutorial_id), 0)),
                    "step": int(profile.get("tutorial_steps", {}).get(str(tutorial_id), 0)),
                }
                for tutorial_id in _LOCAL_TUTORIAL_IDS
            ]
        parts = suffix.split("/")
        if not parts[0].isdigit():
            return None
        tutorial_id = int(parts[0])
        if tutorial_id not in _LOCAL_TUTORIAL_IDS:
            return 501, {"error": "tutorial_not_implemented", "tutorialId": tutorial_id}
        if len(parts) > 1:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            raw_step = parts[1]
            if not raw_step.isdigit():
                return 400, {"error": "invalid_tutorial_step", "tutorialId": tutorial_id}
            step = int(raw_step)
            # Manager_TutorialManager posts every reached TutorialStep and
            # only sends the final PUT once the tutorial is complete.  Keep
            # the highest reached step so a reconnect can resume safely; a
            # repeated or out-of-order POST is idempotent and never regresses
            # the saved progress.
            current_steps = dict(profile.get("tutorial_steps", {}))
            current = int(current_steps.get(str(tutorial_id), 0) or 0)
            if step > current:
                current_steps[str(tutorial_id)] = step
                with self._lock, closing(self._connect()) as connection, connection:
                    connection.execute(
                        "UPDATE game_profiles SET tutorial_steps_json = ?, updated_at = ? WHERE account_id = ?",
                        (
                            json.dumps(current_steps, separators=(",", ":")),
                            self._now(),
                            int(profile["account_id"]),
                        ),
                    )
            # Step progress is local and intentionally has no reward/result
            # side effect; the subsequent PUT carries status=1 when needed.
            return 200, {}
        if method != "PUT":
            return 405, {"error": "method_not_allowed", "message": "PUT required"}
        request = _body_object(body)
        raw_status = request.get("status")
        raw_force_finish = request.get("stepForceFinish", request.get("step_force_finish", False))
        force_finish = bool(raw_force_finish)
        if raw_status is None and body is not None:
            try:
                message = self._types["ApiTutorial"]()
                raw = bytes(body) if not isinstance(body, str) else body.encode("utf-8")
                message.ParseFromString(raw)
                raw_status = int(message.status)
                force_finish = bool(message.step_force_finish)
            except (TypeError, ValueError):
                raw_status = None
        try:
            status = int(raw_status if raw_status is not None else 0)
        except (TypeError, ValueError):
            return 400, {"error": "invalid_tutorial_status"}
        status = 1 if status else 0
        # No ordering gate: the client sequences forced tutorials itself
        # (Manager_TutorialManager) and window guides (id >= 1000) are
        # independent; it resets a guide to Undone when this PUT fails, so a
        # server-side order check made guides replay forever.
        tutorials = dict(profile["tutorials"])
        tutorial_steps = dict(profile.get("tutorial_steps", {}))
        tutorials[str(tutorial_id)] = status
        if status == 0:
            # Resetting a tutorial also resets its step cursor.  This mirrors
            # a fresh local tutorial while retaining other tutorial states.
            tutorial_steps[str(tutorial_id)] = 0
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE game_profiles SET tutorials_json = ?, tutorial_steps_json = ?, updated_at = ? WHERE account_id = ?",
                (
                    json.dumps(tutorials, separators=(",", ":")),
                    json.dumps(tutorial_steps, separators=(",", ":")),
                    self._now(),
                    int(profile["account_id"]),
                ),
            )
        return 200, {}

    @staticmethod
    def _normalise_baned_skills(value: Any) -> Optional[Dict[str, int]]:
        if not isinstance(value, Mapping):
            return None
        result: Dict[str, int] = {}
        for key, raw_value in value.items():
            if not isinstance(raw_value, (int, str)):
                return None
            try:
                skill_state = int(raw_value)
            except (TypeError, ValueError):
                return None
            # ApiAutoFightConf is map<string, uint32>. Keys 1 and 2 are
            # per-DungeonType bitmasks (auto/speed), not boolean skill flags.
            if not (0 <= skill_state <= 0xFFFFFFFF if str(key) in ('1', '2') else skill_state in (0, 1)):
                return None
            result[str(key)] = skill_state
        return result

    def _auto_fight_http(
        self,
        method: str,
        path: str,
        body: Body,
        profile: Mapping[str, Any],
    ) -> Optional[Response]:
        route = _route(path)
        if route != "/fci/auto-fight-conf/":
            return None
        method = method.upper()
        current = profile.get("auto_fight", {})
        if not isinstance(current, Mapping):
            current = {}
        current_skills = self._normalise_baned_skills(current.get("banedSkills", {})) or {}
        if method == "GET":
            return 200, {"banedSkills": current_skills}
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}

        request = _body_object(body)
        requested: Any = request.get("configureSkills", request.get("banedSkills"))
        skills = self._normalise_baned_skills(requested)
        if skills is None and not request and body is not None:
            # The native Restifizer request is a raw ApiAutoFightConf
            # protobuf whose configureSkills map has string keys and uint32
            # values. DungeonUtil also sends the plain JSON object above.
            try:
                message = self._types["ApiAutoFightConf"]()
                message.ParseFromString(bytes(body) if not isinstance(body, str) else body.encode("utf-8"))
                skills_field = getattr(message, "configureSkills", None)
                if skills_field is None:
                    skills_field = getattr(message, "configure_skills", {})
                skills = self._normalise_baned_skills(dict(skills_field))
            except (TypeError, ValueError, DecodeError):
                skills = None
        if skills is None:
            return 400, {"error": "invalid_auto_fight_config"}
        stored = {"banedSkills": skills}
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE game_profiles SET auto_fight_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(stored, separators=(",", ":")), self._now(), int(profile["account_id"])),
            )
        return 200, stored

    def local_actor_ids(self, profile: Mapping[str, Any]) -> list[int]:
        """Actor id per uid, positional: ``ids[uid - 1]`` (0 for a removed uid).

        The canonical store is the uid-keyed instance map (actors.py); this
        view keeps the older ``ids.index(actor_id) + 1`` / ``ids[uid - 1]``
        idioms working.  Copies of one character appear once per uid.
        """

        try:
            records = actor_store.ordered(profile, self.systems._state(profile))
        except Exception:
            raw = profile.get("actor_ids") or ([profile["actor_id"]] if profile.get("actor_id") is not None else [])
            return [int(value) for value in raw]
        if not records:
            return []
        ids = [0] * max(int(record["uid"]) for record in records)
        for record in records:
            ids[int(record["uid"]) - 1] = int(record["id"])
        return ids

    def _append_local_actor(self, actors: Any, actor_id: int, uid: int, index: int, *,
                            level: int = 1, star: int = 1, quality: Optional[int] = None,
                            exp: int = 0, skill_levels: Optional[Mapping[str, Any]] = None) -> None:
        """Append a schema-complete local test actor.

        The IDs/model fields come from the live ActorConfig sample.  When an
        observed ActorInfo row exists, its attributes and quality are used;
        missing rows retain the reviewed local fallback because the table is
        explicitly partial.
        """

        actor = actors.player_actors.add()
        actor.id = int(actor_id)
        actor.uid = int(uid)
        actor.level = max(1, int(level))
        actor.cur_exp = max(0, int(exp))
        actor.star = max(1, int(star))
        observed = _ACTOR_INFO.get(int(actor_id), {})
        if quality is None:
            quality = actor_store.base_quality(int(actor_id)) if int(actor_id) in actor_store.BASE_QUALITY else int(
                observed.get("quality", _LOCAL_DEFAULT_ACTOR_TEMPLATE.get("local_test_quality", 1)))
        actor.quality = int(quality)
        # Client tables (ActorInfo/ActorSkill/promotions) give every character
        # its own stats and skills; the captured template is only a fallback.
        table_attrs = actor_store.compute_attrs(int(actor_id), actor.quality, actor.level, actor.star)
        table_skills = actor_store.skill_ids(int(actor_id), actor.star)
        actor.actor_skin = 0
        actor.default_index = int(index)
        actor.is_normal = True
        actor.is_new = True
        actor.is_new_card = True
        skills = table_skills or _LOCAL_DEFAULT_ACTOR_TEMPLATE.get("local_test_skills", [])
        levels = skill_levels if isinstance(skill_levels, Mapping) else {}
        if isinstance(skills, list):
            for skill_id in skills:
                skill = actor.skills.add()
                skill.id = int(skill_id)
                saved_skill = levels.get(str(skill_id), 1)
                skill.level = max(1, int((saved_skill.get('level', 1) if isinstance(saved_skill, Mapping)
                                          else saved_skill) or 1))
                skill.is_read = bool(saved_skill.get('read', False)) if isinstance(saved_skill, Mapping) else False
        attrs: Any = table_attrs or observed.get("attrs")
        if not isinstance(attrs, list) or not attrs:
            attrs = _LOCAL_DEFAULT_ACTOR_TEMPLATE.get("local_test_attributes", {})
        if isinstance(attrs, Mapping):
            attrs = [
                {"type": attr_type, "value": value, "isRatio": False}
                for attr_type, value in attrs.items()
            ]
        if isinstance(attrs, list):
            for item in attrs:
                if not isinstance(item, Mapping):
                    continue
                attr = actor.attrs.add()
                attr.type = int(item.get("type", 0))
                attr.value = int(item.get("value", 0))
                attr.is_ratio = bool(item.get("isRatio", item.get("is_ratio", False)))

    def login_messages(self, account: Mapping[str, Any]) -> List[Message]:
        """Return the exact bootstrap push message names and protobuf bytes."""

        with self._lock:
            return self._login_messages_locked(account)

    def _login_messages_locked(self, account: Mapping[str, Any]) -> List[Message]:
        profile = self._profile(account)
        now_ms = self._now() * 1000
        self.mall.settle_month_card(profile)
        profile = self._profile(account)
        self.guild.player_data(profile)
        self.cultivation.sync(profile)
        messages: list[Message] = []

        begin = self._types["ScLoginBegin"](
            player_id=str(profile["player_id"]),
            social_picture="",
            utc_offset_seconds=self.reset_policy.utc_offset_seconds,
            time_ms_server=now_ms,
        )
        messages.append(("ScLoginBegin", begin.SerializeToString()))
        tutorial = self._types["ScLoginTutorial"]()
        for tutorial_id in _LOCAL_TUTORIAL_IDS:
            entry = tutorial.tutorial_info.add()
            entry.id = tutorial_id
            entry.sequence = int(_LOCAL_TUTORIAL_SEQUENCE.get(tutorial_id, tutorial_id))
            entry.status = int(profile["tutorials"].get(str(tutorial_id), 0))
        messages.append(("ScLoginTutorial", tutorial.SerializeToString()))

        actors = self._types["DBGamePlayer.Actors"]()
        actors.unlocked_skins.append(0)
        # One STActor per owned instance (uid-keyed, copies allowed), with the
        # saved level/star/EXP so a reconnect reflects local play.
        try:
            system_state = self.systems._state(profile)
            records = actor_store.ordered(profile, system_state)
        except Exception:
            system_state = {}
            records = [actor_store.new_record(actor_id, uid)
                       for uid, actor_id in enumerate(profile.get("actor_ids") or [profile.get("actor_id") or 1001], 1)]
        extras_changed = False
        for record in records:
            uid = int(record["uid"])
            actor_id = int(record["id"])
            self._append_local_actor(
                actors, actor_id, uid, uid - 1,
                level=int(record.get("level", 1) or 1), star=int(record.get("star", 1) or 1),
                quality=int(record.get("quality") or actor_store.base_quality(actor_id)),
                exp=int(record.get("exp", 0) or 0), skill_levels=record.get("skills"),
            )
            actors.player_actors[-1].is_lock = bool(record.get("locked", False))
            actors.player_actors[-1].actor_skin = int(record.get("skin", 0) or 0)
            try:
                # Worn 藏品: bodyEquips, equipAttrs, suit specialAttrIds.
                apply_worn_equipment(actors.player_actors[-1], profile, system_state)
            except Exception:
                pass
            try:
                # Persisted NEW flag and talents.
                extras_changed = self.systems.login_actor_extras(profile, system_state, record, actors.player_actors[-1]) or extras_changed
            except Exception:
                pass
            if actor_id not in actors.actor_id_map:
                actors.actor_id_map[actor_id] = uid
        if extras_changed and system_state:
            self.systems._save(profile, system_state)
        messages.append(("DBGamePlayer.Actors", actors.SerializeToString()))

        equips = self._types["DBGamePlayer.EquipPackage"]()
        try:
            fill_equip_package(self, profile, equips)
        except Exception:
            pass
        messages.append(("DBGamePlayer.EquipPackage", equips.SerializeToString()))
        try:
            # Daily ticket top-ups (80002/80006) must land before the item list.
            self.activity_modes.refresh_daily_items(profile)
            self.realtime_pvp.race.refresh_daily_items(profile)
        except Exception:
            pass
        try:
            # MazeEnter reads both ticket counts immediately, before Summary
            # can repair the client's item cache. Settle Maze before bootstrap.
            self.maze.refresh_daily_items(profile)
        except Exception:
            logging.getLogger(__name__).exception("login maze ticket refresh failed")
        items = self._types["DBGamePlayer.ItemPackage"]()
        # Items owned in local play (drops, rewards) live in LocalSystems
        # state; include them so a re-login shows the real bag.  Item 90001
        # is read unconditionally by TlogUtil.CollectBalanceData after every
        # battle (``fci/item/90001``.count), so it is always present.
        try:
            owned_items = self.systems._state(profile).get("items", {})
        except Exception:
            owned_items = {}
        item_ids = set()
        if isinstance(owned_items, Mapping):
            for key, value in owned_items.items():
                if not str(key).isdigit() or not isinstance(value, Mapping):
                    continue
                count = int(value.get("count", 0) or 0)
                if count <= 0:
                    continue
                entry = items.items.add()
                entry.id = int(key)
                entry.count = count
                entry.got_time = int(value.get("gotTime", 0) or 0)
                entry.is_new = bool(value.get("isNew", False))
                item_ids.add(int(key))
        # Always-read items (count used without nil checks): 90001 by
        # TlogUtil after every battle, gacha tickets 60001/60002 by
        # Window_Gacha's cost check (GachaType 1/2 cost), and 80007/80008
        # by MazeEnter.OnEnter, including locked or fully spent tickets.
        for always_present in (60001, 60002, 80007, 80008, 90001):
            if always_present not in item_ids:
                items.items.add(id=always_present, count=0)
        messages.append(("DBGamePlayer.ItemPackage", items.SerializeToString()))

        end = self._types["ScLoginEnd"]()
        end.base_info.level = int(profile["level"])
        end.base_info.name = str(profile["name"])
        end.base_info.friend_apply_level_limit = self.social.friend_apply_level_limit(profile)
        if "head_photo" in end.base_info.DESCRIPTOR.fields_by_name:
            try:
                head_photo = int(self.profile_features._state(profile).get("headPhoto", 0) or 0)
            except Exception:
                head_photo = 0
            end.base_info.head_photo = head_photo or DEFAULT_HEAD_PHOTO
        end.base_info.home_back_ground.current = int(profile["scene_id"])
        end.base_info.home_back_ground.is_random = bool(profile["scene_random"])
        for resource in self._resource_message(profile):
            entry = end.base_resource.add()
            for field in ("type", "id", "count", "param"):
                if field in resource:
                    setattr(entry, field, int(resource[field]))
        messages.append(("ScLoginEnd", end.SerializeToString()))
        return messages

    def _chapter_result_message(self, profile: Mapping[str, Any], *, compact: bool = True):
        """Build the real typed ``ApiChapterInfo.result`` protobuf payload."""

        message = self._types["ApiChapterInfo.result"]()
        # ParseDict performs the schema conversion, including converting the
        # JSON-looking map keys ("1", "1001", "2011", ...) to uint32 map
        # keys.  It also rejects accidental fields instead of silently
        # inventing a response shape.
        json_format.ParseDict(self.dungeons.chapter_snapshot(profile, compact=compact), message)
        return message

    def _chapter_detail_message(self, profile: Mapping[str, Any], chapter_id: int):
        """Build the typed ``ApiChapterInfo.get`` payload used by red points."""

        message = self._types["ApiChapterInfo.get"]()
        json_format.ParseDict(self.dungeons.chapter_detail(profile, chapter_id), message)
        return message

    def _legacy_tournament_message(self, route: str, payload: Any) -> Any:
        """Use typed responses for the legacy async-PvP HTTP resources."""
        route = _route(route)
        names = {
            "/fci/asyncpvprecord": "ApiAsyncPvpRecordInfo.result",
            "/fci/asyncpvpmatch": "ApiAsyncPvpMatch.result",
            "/fci/asyncpvptargetdetail": "ApiAsyncPvpTargetDetail.result",
        }
        name = next((value for key, value in names.items()
                     if route == key or route.startswith(key + "/")), None)
        if name is None or not isinstance(payload, Mapping):
            return payload
        if name.endswith("TargetDetail.result") and "result" not in payload:
            payload = {"result": payload}
        try:
            message = message_factory.GetMessageClass(self._pool.FindMessageTypeByName(name))()
            json_format.ParseDict(dict(payload), message)
            return message
        except Exception:
            return payload

    def _handle_http_unlocked(
        self,
        method: str,
        path: str,
        body: Body = None,
        content_type: Optional[str] = None,
        authorization: Optional[str] = None,
    ) -> Optional[Response]:
        """Handle the smallest post-login HTTP profile surface.

        Unknown paths return ``None`` for the existing fixture router.  The
        returned dictionaries are raw JSON objects; the parent server adds the
        original client's RestfulResponse envelope.
        """

        route = _route(path)
        routes = {
            "/fci/login/": "login",
            "/fci/guild-player/": "guild",
            "/fci/dungeon/chapter/": "dungeon",
            "/fci/applylist/": "applylist",
        }
        action = routes.get(route)
        # Chapter details are a separate REST resource from the chapter
        # summary.  The client reads these paths while binding red points,
        # and expects the typed ``ApiChapterInfo.get`` response envelope.
        if action is None and route.startswith("/fci/dungeon/chapter/"):
            chapter_id = route[len("/fci/dungeon/chapter/") :].strip("/")
            if chapter_id.isdigit():
                action = "dungeon"
        if action is None:
            # Leave non-FCI paths (patch/clientupdate/fixtures) to the parent
            # fixture router; authenticating them here would turn them into
            # a misleading 401 before a fixture can answer.
            # BurstLink's native matcher is addressed through the separate
            # matchUrl host (``/match/BurstLinkPVPMatch``) while all player
            # state remains under /fci.  The local server keeps both seams in
            # the same authenticated dispatcher so the original client can
            # enter a deterministic robot room without a retired match host.
            if not (route.startswith("/fci/") or route.startswith("/match/")):
                return None
            # Entering routes are authenticated with the same local access
            # token, then delegated to the evidence-bounded entering module.
            account, error = self._authorized(authorization)
            if error is not None:
                return error
            assert account is not None
            profile = self._profile(account)
            self.mall.settle_month_card(profile)
            profile = self._profile(account)
            economy_profile = dict(profile)
            economy_profile["account"] = account
            cultivation_result = self.cultivation.handle_http(method.upper(), path, body, profile)
            if cultivation_result is not None:
                return cultivation_result
            tournament_result = self.tournament.handle_http(method, path, body, profile)
            if tournament_result is not None:
                status, result = tournament_result
                return status, self._legacy_tournament_message(route, result) if status == 200 else result
            if route.rstrip('/').lower() == '/fci/rolepresent' or route.lower().startswith('/fci/rolepresent/'):
                # Welfare2/WlfRolePresent: GET is an array. POST selects by
                # URL and sends JSON null (there is no body selection field).
                if method.upper() == 'GET':
                    return 200, self.profile_features.pending_role_presents(profile)
                if method.upper() != 'POST':
                    return 405, {'error': 'method_not_allowed'}
                suffix = route.rstrip('/').split('/')[-1]
                if not suffix.isdigit():
                    return 400, {'error': 'invalid_role_present_id'}
                present_id = int(suffix)
                with self.atomic_mutation():
                    eligible = self.profile_features.pending_role_presents(profile)
                    chosen = next((row for row in eligible if row['presentId'] == present_id), None)
                    if chosen is None:
                        return 409, {'error': 'role_present_not_available'}
                    # Misc.rolePresentMaxNum is the account-wide daily cap,
                    # separate from the per-role once-per-day marker.
                    day = self.reset_policy.day_index(self._now())
                    feature_state = self.profile_features._state(profile)
                    claims = feature_state.setdefault('rolePresents', {})
                    daily_cap = 3
                    try:
                        from economy import _client_rows
                        daily_cap = max(0, int(_client_rows('Misc')[0].get('rolePresentMaxNum', daily_cap)))
                    except (IndexError, KeyError, TypeError, ValueError):
                        pass
                    claimed_today = sum(1 for key in claims if str(key).startswith(f'{day}:'))
                    if claimed_today >= daily_cap:
                        return 409, {'error': 'role_present_daily_limit', 'limit': daily_cap}
                    from grants import grant_rewards
                    changes = grant_rewards(self, profile, chosen['rewards'])
                    claims[f'{day}:{chosen["roleId"]}'] = self._now()
                    self.profile_features._save(profile, feature_state)
                    return 200, {'presents': chosen, 'resChange': changes}
            guild_result = self.guild.handle_http(method, path, body, profile)
            if guild_result is not None:
                return guild_result
            modes_result = self.activity_modes.handle_http(method, path, body, profile)
            if modes_result is not None:
                return modes_result
            compat_result = handle_client_compat(self, method, path, body, profile)
            if compat_result is not None:
                return compat_result
            # The activity window calls these on every refresh / mode entry
            # and resets the game on an error reply; the client ignores the
            # POST reply and only needs an empty tournament list.
            if route.rstrip("/").lower() == "/fci/tournament/summary" and method.upper() == "GET":
                return 200, {"activityInfo": []}
            if route.rstrip("/").lower() == "/fci/redpoint/data" and method.upper() in {"POST", "PATCH", "PUT"}:
                return self.redpoints.mark(profile, body)
            mall_result = self.mall.handle_http(method, path, body, profile)
            if mall_result is not None:
                return mall_result
            events_result = self.events.handle_http(method, path, body, profile)
            if events_result is not None:
                return self.mall.merge_activity_list(method, path, events_result)
            social_result = self.social.handle_http(method, path, body, profile)
            if social_result is not None:
                return social_result
            realtime_result = self.realtime_pvp.handle_http(method, path, body, profile)
            if realtime_result is not None:
                return realtime_result
            tasks_result = self.tasks.handle_http(method, path, body, profile)
            if tasks_result is not None:
                return tasks_result
            mailbox_result = self.mailbox.handle_http(method, path, body, profile)
            if mailbox_result is not None:
                return mailbox_result
            achievements_result = self.achievements.handle_http(method, path, body, profile)
            if achievements_result is not None:
                return achievements_result
            welfare_result = self.welfare.handle_http(method, path, body, profile)
            if welfare_result is not None:
                return welfare_result
            maze_result = self.maze.handle_http(method, path, body, profile)
            if maze_result is not None:
                return maze_result
            systems_result = self.systems.handle_http(method, path, body, profile)
            if systems_result is not None:
                return systems_result
            # Profile features fills the notice/web-activity seams that are
            # outside LocalSystems' player profile implementation.
            profile_result = self.profile_features.handle_http(method, path, body, profile)
            if profile_result is not None:
                return profile_result
            economy_result = self.economy.handle_http(method, path, body, economy_profile)
            if economy_result is not None:
                return economy_result
            auto_fight_result = self._auto_fight_http(method, path, body, profile)
            if auto_fight_result is not None:
                return auto_fight_result
            tutorial_result = self._tutorial_http(method, path, body, profile)
            if tutorial_result is not None:
                return tutorial_result
            dungeon_result = self.dungeons.handle_http(method, path, body, profile)
            if dungeon_result is not None:
                return dungeon_result
            result = handle_entering_http(method, path, profile)
            # Window_City stores fci/ContinuousLogin/ (novice-gift tab, red
            # points) only from these redpoint fields.
            if (method.upper() == "GET" and route.rstrip("/") == "/fci/redpoint/data"
                    and result is not None and result[0] == 200 and isinstance(result[1], dict)):
                event_redpoints = self.events.redpoint_fields(profile)
                # Present.presentId is uint32 in ApiRolePresent.redpoint;
                # older local records used the internal day:role key here.
                # Normalize that key at the HTTP boundary while retaining it
                # in the profile ledger for idempotency.
                try:
                    role_present = event_redpoints.get("rolePresentRedpoint", {}).get("presents", [])
                    for present in role_present:
                        if isinstance(present, dict):
                            present["presentId"] = int(present.get("roleId", present.get("presentId", 0)) or 0)
                except Exception:
                    pass
                result = (200, {**result[1], **event_redpoints, **self.mall.redpoint_fields(profile),
                                **self.achievements.redpoint_fields(profile),
                                **self.activity_modes.redpoint_fields(profile),
                                **(self.social.redpoint_fields(profile) if hasattr(self.social, "redpoint_fields") else {}),
                                **self.redpoints.fields(profile)})
            return result

        account, error = self._authorized(authorization)
        if error is not None:
            return error
        assert account is not None
        profile = self._profile(account)
        self.mall.settle_month_card(profile)
        profile = self._profile(account)
        economy_profile = dict(profile)
        economy_profile["account"] = account
        guild_result = self.guild.handle_http(method, path, body, profile)
        if guild_result is not None:
            return guild_result
        modes_result = self.activity_modes.handle_http(method, path, body, profile)
        if modes_result is not None:
            return modes_result
        compat_result = handle_client_compat(self, method, path, body, profile)
        if compat_result is not None:
            return compat_result
        mall_result = self.mall.handle_http(method, path, body, profile)
        if mall_result is not None:
            return mall_result
        events_result = self.events.handle_http(method, path, body, profile)
        if events_result is not None:
            return self.mall.merge_activity_list(method, path, events_result)
        social_result = self.social.handle_http(method, path, body, profile)
        if social_result is not None:
            return social_result
        realtime_result = self.realtime_pvp.handle_http(method, path, body, profile)
        if realtime_result is not None:
            return realtime_result
        tasks_result = self.tasks.handle_http(method, path, body, profile)
        if tasks_result is not None:
            return tasks_result
        mailbox_result = self.mailbox.handle_http(method, path, body, profile)
        if mailbox_result is not None:
            return mailbox_result
        achievements_result = self.achievements.handle_http(method, path, body, profile)
        if achievements_result is not None:
            return achievements_result
        welfare_result = self.welfare.handle_http(method, path, body, profile)
        if welfare_result is not None:
            return welfare_result
        maze_result = self.maze.handle_http(method, path, body, profile)
        if maze_result is not None:
            return maze_result
        systems_result = self.systems.handle_http(method, path, body, profile)
        if systems_result is not None:
            return systems_result
        profile_result = self.profile_features.handle_http(method, path, body, profile)
        if profile_result is not None:
            return profile_result
        economy_result = self.economy.handle_http(method, path, body, economy_profile)
        if economy_result is not None:
            return economy_result
        method = method.upper()
        if action == "login":
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            # The Lua callback ignores this response body.  Return an empty
            # local object rather than claiming a remote operation succeeded.
            self.tasks.on_login(profile)
            return 200, {}
        if action == "dungeon":
            # Keep the chapter route in the dungeon module so the exact same
            # local chapter graph is used by direct dungeon requests and the
            # post-login PushService request.
            if route == "/fci/dungeon/chapter/" and method == "GET":
                # Keep the compact graph used by the original bootstrap.  A
                # per-chapter detail request exposes later configured rows.
                return 200, self._chapter_result_message(profile, compact=True)
            if route.startswith("/fci/dungeon/chapter/") and method == "GET":
                chapter_id = route[len("/fci/dungeon/chapter/") :].strip("/")
                if chapter_id.isdigit():
                    return 200, self._chapter_detail_message(profile, int(chapter_id))
            result = self.dungeons.handle_http(method, path, body, profile)
            if result is not None:
                return result
            return 501, {"error": "dungeon_route_not_implemented"}
        if method != "GET":
            return 405, {"error": "method_not_allowed", "message": "GET required"}
        if action == "guild":
            return 200, self.guild.player_data(profile)
        return 200, []

    def handle_http(
        self,
        method: str,
        path: str,
        body: Body = None,
        content_type: Optional[str] = None,
        authorization: Optional[str] = None,
    ) -> Optional[Response]:
        """Serialize one authenticated dispatch across all local modules.

        HTTP handlers can be called concurrently by ``ThreadingHTTPServer``.
        Holding the per-profile/process lock for the complete dispatch keeps
        the read-modify-write resource ledger and the adjacent JSON state
        updates atomic, while the underlying modules continue to use the same
        re-entrant lock for direct unit-test calls.
        """

        with self._lock:
            route_path, separator, query = path.partition('?')
            path = re.sub(r"/{2,}", "/", route_path) + separator + query
            before = self._base_info_marker(method, authorization)
            result = unwrap_client_reply(
                method, path, self._handle_http_unlocked(method, path, body, content_type, authorization)
            )
            if result is not None and result[0] == 200 and method.upper() != 'GET' and authorization:
                account, error = self._authorized(authorization)
                if account is not None and error is None:
                    try:
                        self.cultivation.sync(self._profile(account))
                    except Exception:
                        # The primary operation is already committed. A derived
                        # task refresh must not turn success into a retryable 500.
                        logging.getLogger(__name__).exception("post-request cultivation refresh failed")
            if before is not None:
                self._push_base_info_if_changed(before)
            return result

    def _base_info_marker(self, method: str, authorization: Optional[str]) -> Optional[tuple]:
        """(account, level, name) before a state-changing request, else None."""

        if method.upper() == "GET" or self.base_push_notifier is None or not authorization:
            return None
        try:
            account, error = self._authorized(authorization)
            if error is not None or account is None:
                return None
            profile = self._profile(account)
            return account, int(profile.get("level", 1) or 1), str(profile.get("name", ""))
        except Exception:
            return None

    def _push_base_info_if_changed(self, before: tuple) -> None:
        """The city/HUD only re-reads fci/baseinfo/ on a BaseInfoChange push
        (Manager_PushService); battles, rewards and renames change it."""

        account, level, name = before
        try:
            profile = self._profile(account)
            if (int(profile.get("level", 1) or 1), str(profile.get("name", ""))) != (level, name):
                self.base_push_notifier(profile, 2)  # PushType_BaseInfoChange
        except Exception:
            pass


__all__ = ["LocalGame", "Message"]
