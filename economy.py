"""Local-only shop and payment seams for the retired LJJC client.

The original server is unavailable, so this module deliberately implements a
small, deterministic *local sandbox* rather than pretending to verify an
Apple/Google/Midas receipt.  Shop purchases spend the profile's local
resources.  A recharge is only credited after a one-time token returned by
``purchasetouch`` is supplied to ``purchasedelevery``; arbitrary receipt text
never grants paid currency.

State that belongs to the economy is kept in a separate SQLite file so older
``game.sqlite3`` profiles remain readable and migrations stay reversible.
"""

from __future__ import annotations

from server.core.reset import DEFAULT_RESET_POLICY, ResetPolicy, policy_for, period_due
from server.core.atomic import track_mapping

import json
import os
import random
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from functools import lru_cache
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from google.protobuf import message_factory

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
Response = Tuple[int, Any]


def _route(path: str) -> str:
    value = urlsplit(path or "").path or "/"
    if value != "/":
        value = value.rstrip("/") + "/"
    return value


def _body_object(body: Body) -> Mapping[str, Any]:
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


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _protobuf_request(game: Any, body: Body, type_name: str) -> Any | None:
    """Decode a native wireformat request when the JSON seam is absent."""

    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
    if not raw:
        return None
    try:
        message_type = game._pool.FindMessageTypeByName(type_name)
        message = message_factory.GetMessageClass(message_type)()
        message.ParseFromString(raw)
        return message
    except Exception:
        return None


def _wire_varints(body: Body, names: Mapping[int, str]) -> dict[str, int]:
    """Decode the top-level varint fields of a protobuf body (others skipped)."""

    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
    result: dict[str, int] = {}
    position = 0

    def varint() -> int:
        nonlocal position
        value = shift = 0
        while True:
            if position >= len(raw) or shift > 63:
                raise ValueError("truncated varint")
            byte = raw[position]
            position += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7

    try:
        while position < len(raw):
            key = varint()
            field, wire_type = key >> 3, key & 7
            if wire_type == 0:
                value = varint()
                if field in names:
                    result[names[field]] = value
            elif wire_type == 2:
                position += varint()
            elif wire_type == 1:
                position += 8
            elif wire_type == 5:
                position += 4
            else:
                break
    except ValueError:
        return {}
    return result


# ---------------------------------------------------------------------------
# Client tables and server-only policy files
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CLIENT_TABLE_DIR = _DATA_DIR / "client-tables"
_SHOP_CATALOG_PATH = _DATA_DIR / "server-only" / "shop.yaml"
_ECONOMY_POLICY_PATH = _DATA_DIR / "server-only" / "economy.yaml"

RES_PLAYER_MONEY = 5
RES_PLAYER_POWER = 6
RES_PLAYER_GOLD = 7
RES_ITEM = 11
RES_EQUIP = 12
RES_ACTOR = 15
RES_DUNGEON_PLAY_TIMES_LIMIT = 28
# Granted outside the profile resource ledger (bag / equip / actor stores).
_INSTANCE_RESOURCE_TYPES = {RES_ITEM, RES_EQUIP, RES_ACTOR}


@lru_cache(maxsize=None)
def _client_rows(name: str) -> tuple[dict[str, Any], ...]:
    """Rows of an exported client table (``data/client-tables/<name>.json``)."""

    try:
        payload = json.loads((_CLIENT_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ()
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return tuple(row for row in rows if isinstance(row, dict))


def _st_resource(value: Any, *, count: Optional[int] = None) -> dict[str, int]:
    """A complete STResource mapping (absent proto3 fields become 0)."""

    if not isinstance(value, Mapping):
        value = {}
    return {
        "type": _int(value.get("type")),
        "id": _int(value.get("id")),
        "count": _int(value.get("count")) if count is None else int(count),
        "param": _int(value.get("param")),
    }


def _load_yaml(path: Path) -> Any:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # missing PyYAML, unreadable or malformed file
        return None


# --------------------------------------------------------------- energy ---

def _yaml_energy_fallback() -> dict[str, Any]:
    """Legacy ``economy.yaml`` power rule, used only without the client table."""

    value = {"type": RES_PLAYER_POWER, "id": 0, "param": 0, "max": 120, "inc": 1,
             "incSeconds": 360, "limit": 0, "level": 0, "source": "economy.yaml"}
    raw = _load_yaml(_ECONOMY_POLICY_PATH)
    power = raw.get("resources", {}).get("player_power", {}) if isinstance(raw, Mapping) else {}
    if isinstance(power, Mapping):
        value["max"] = max(0, _int(power.get("capacity"), value["max"]))
        value["incSeconds"] = max(1, _int(power.get("recoverySeconds"), value["incSeconds"]))
        value["inc"] = max(1, _int(power.get("recoveryAmount"), value["inc"]))
    return value


@lru_cache(maxsize=1)
def _level_energy_rows() -> dict[int, dict[str, int]]:
    """PlayerLevelInitConfig.incInfo[type == ResPlayerPower] by player level."""

    result: dict[int, dict[str, int]] = {}
    for row in _client_rows("PlayerLevelInitConfig"):
        level = _int(row.get("level"))
        for inc in row.get("incInfo") or []:
            if isinstance(inc, Mapping) and _int(inc.get("type")) == RES_PLAYER_POWER:
                result[level] = {
                    "max": _int(inc.get("max")),
                    "inc": max(1, _int(inc.get("inc"), 1)),
                    "incSeconds": max(1, _int(inc.get("incSeconds"), 1)),
                    "limit": _int(inc.get("limit")),
                }
    return result


def energy_policy(level: Any = 1) -> dict[str, Any]:
    """The client's power recovery rule for ``level``.

    ``Window_Resource`` (HUD) and ``Window_ResourceExchange`` read
    ``PlayerLevelInitConfig[level].incInfo`` for ResPlayerPower: natural
    recovery adds ``inc`` every ``incSeconds`` (450 s) until ``max`` (60 at
    level 1, +2 per level, 256 at 99).  ``max`` only stops recovery; power
    from level rewards or purchases may exceed it and is never clamped.
    """

    rows = _level_energy_rows()
    if not rows:
        return _yaml_energy_fallback()
    wanted = max(1, _int(level, 1))
    known = sorted(rows)
    chosen = known[0]
    for candidate in known:
        if candidate <= wanted:
            chosen = candidate
    row = rows[chosen]
    return {"type": RES_PLAYER_POWER, "id": 0, "param": 0, "max": row["max"], "inc": row["inc"],
            "incSeconds": row["incSeconds"], "limit": row["limit"], "level": chosen,
            "source": "PlayerLevelInitConfig"}


def apply_energy_recovery(
    connection: sqlite3.Connection,
    account_id: int,
    resources: list[dict[str, Any]],
    *,
    now: int,
    level: Any = None,
) -> dict[str, Any]:
    """Apply elapsed passive power recovery inside the caller's transaction.

    Drop-in for ``LocalGame._recover_passive_resources`` (same tables, same
    ledger idempotency key) with two differences: the cap and interval come
    from :func:`energy_policy` for the player's level, and a balance at or
    above the cap is left untouched (only the recovery marker moves), so
    energy from level-ups and purchases is never clamped away.  ``resources``
    is mutated in place; ``game_profiles.resources_json`` is rewritten only
    when something was recovered.
    """

    account_id, now = int(account_id), int(now)
    if level is None:
        row = connection.execute("SELECT level FROM game_profiles WHERE account_id = ?", (account_id,)).fetchone()
        level = row[0] if row is not None else 1
    policy = energy_policy(level)
    rtype, rid, param = int(policy["type"]), int(policy["id"]), int(policy["param"])
    cap, interval, amount = int(policy["max"]), int(policy["incSeconds"]), int(policy["inc"])
    result: dict[str, Any] = {"policy": policy, "recovered": 0, "lastRecoveryAt": now, "entry": None}
    entry = next(
        (item for item in resources
         if isinstance(item, dict) and _int(item.get("type"), -1) == rtype
         and _int(item.get("id")) == rid and _int(item.get("param")) == param),
        None,
    )
    # Profiles deliberately created without power stay without power.
    if entry is None or cap <= 0:
        return result
    result["entry"] = entry
    state = connection.execute(
        "SELECT last_recovery_at FROM local_resource_recovery WHERE account_id = ?", (account_id,)
    ).fetchone()
    if state is None:
        connection.execute(
            """
            INSERT INTO local_resource_recovery
            (account_id, resource_type, resource_id, param, capacity,
             interval_seconds, amount_per_interval, last_recovery_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, rtype, rid, param, cap, interval, amount, now, now),
        )
        return result
    previous = _int(state[0], now)
    current = max(0, _int(entry.get("count")))
    recovered = 0
    if now < previous:
        # A clock rollback must never mint power: re-anchor only.
        marker = now
    elif current >= cap:
        # Full or above the cap (level reward, purchase): keep the balance.
        marker = now
    else:
        intervals = (now - previous) // interval
        recovered = min(cap - current, intervals * amount)
        # Keep the unconsumed part of the running interval.
        marker = now if current + recovered >= cap else previous + intervals * interval
    if recovered > 0:
        entry["count"] = current + recovered
        connection.execute(
            """
            INSERT OR IGNORE INTO local_resource_ledger
            (account_id, operation, resource_type, resource_id, param, delta,
             balance_after, reason, idempotency_key, created_at)
            VALUES (?, 'grant', ?, ?, ?, ?, ?, 'passive_energy_recovery', ?, ?)
            """,
            (account_id, rtype, rid, param, recovered, int(entry["count"]),
             f"energy-recovery:{account_id}:{previous}:{now}", now),
        )
        connection.execute(
            "UPDATE game_profiles SET resources_json = ?, updated_at = ? WHERE account_id = ?",
            (json.dumps(resources, separators=(",", ":")), now, account_id),
        )
    connection.execute(
        """
        UPDATE local_resource_recovery
        SET resource_type = ?, resource_id = ?, param = ?, capacity = ?, interval_seconds = ?,
            amount_per_interval = ?, last_recovery_at = ?, updated_at = ?
        WHERE account_id = ?
        """,
        (rtype, rid, param, cap, interval, amount, int(marker), now, account_id),
    )
    result.update(recovered=int(recovered), lastRecoveryAt=int(marker))
    return result


# ------------------------------------------------------------ resbuy ---

@lru_cache(maxsize=1)
def _refresh_price_table() -> dict[int, tuple[dict[str, Any], ...]]:
    """RefreshPrice rows by type, ordered by the nth purchase (``count``)."""

    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in _client_rows("RefreshPrice"):
        grouped.setdefault(_int(row.get("type")), []).append(row)
    return {
        key: tuple(sorted(rows, key=lambda row: _int(row.get("count"))))
        for key, rows in grouped.items()
    }


# -------------------------------------------------------------- shop ---

_DEFAULT_SHOP_CATALOG: Dict[str, Any] = {
    # Used only when shop.yaml cannot be read: one common shop of actor EXP.
    "catalogVersion": 1,
    "shops": [{
        "shopId": 1, "playerLevel": 1, "refreshTimeInDay": "06:00:00",
        "refreshLimit": 5, "refreshFree": 86400, "refreshCost": {"type": RES_PLAYER_GOLD, "count": 10000},
        "displayCost": [{"type": RES_PLAYER_GOLD}],
        "slots": [{"pool": "exp", "count": 2}],
        "pools": {"exp": [
            {"shopGoodId": 101, "good": {"type": RES_ITEM, "id": 10001, "count": 10},
             "cost": [{"type": RES_PLAYER_GOLD, "count": 5000}], "buyLimit": 2, "chance": 1},
            {"shopGoodId": 102, "good": {"type": RES_ITEM, "id": 10002, "count": 5},
             "cost": [{"type": RES_PLAYER_GOLD, "count": 7500}], "buyLimit": 2, "chance": 1},
        ]},
    }],
}


def _quality_map(raw: Any) -> dict[int, Any]:
    return {_int(key): value for key, value in raw.items()} if isinstance(raw, Mapping) else {}


def _good_template(shop_good_id: int, good: Mapping[str, Any], costs: Any, *, buy_limit: Any,
                   chance: Any, require_level: int = 0) -> dict[str, Any]:
    return {
        "shopGoodId": int(shop_good_id),
        "good": _st_resource(good),
        "cost": [_st_resource(cost) for cost in (costs or []) if isinstance(cost, Mapping)],
        "buyLimit": max(0, _int(buy_limit)),
        "chance": max(0, _int(chance, 1)),
        "requireLevel": int(require_level),
    }


def _generate_pool(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand a ``generate`` pool from ItemInfo / EquipInfo client rows."""

    rule = spec.get("generate") if isinstance(spec.get("generate"), Mapping) else {}
    table = str(rule.get("table", ""))
    source = _int(rule.get("source"), 0)
    base = _int(spec.get("shopGoodIdBase"))
    count = max(1, _int(spec.get("count"), 1))
    cost_by_quality = _quality_map(spec.get("costByQuality"))
    chance_by_quality = _quality_map(spec.get("chanceByQuality"))
    result: list[dict[str, Any]] = []
    if table == "ItemInfo":
        rows, id_key, rtype = _client_rows("ItemInfo"), "id", RES_ITEM
    elif table == "EquipInfo":
        rows, id_key, rtype = _client_rows("EquipInfo"), "equipId", RES_EQUIP
    else:
        return result
    for row in rows:
        if source not in (row.get("sources") or []):
            continue
        if "funcType" in rule and _int(row.get("funcType")) != _int(rule.get("funcType")):
            continue
        quality = _int(row.get("quality"))
        if "quality" in rule and quality != _int(rule.get("quality")):
            continue
        costs = cost_by_quality.get(quality, spec.get("cost"))
        if not costs:
            continue
        item_id = _int(row.get(id_key))
        result.append(_good_template(
            base + item_id, {"type": rtype, "id": item_id, "count": count}, costs,
            buy_limit=spec.get("buyLimit"), chance=chance_by_quality.get(quality, spec.get("chance", 1)),
            require_level=_int(row.get("requireLevel")) if rtype == RES_EQUIP else 0,
        ))
    return result


def _valid_good(template: Mapping[str, Any]) -> bool:
    good = template.get("good", {})
    if _int(good.get("count")) <= 0 or not template.get("cost"):
        return False
    if good.get("type") == RES_ITEM:
        return any(_int(row.get("id")) == good.get("id") for row in _client_rows("ItemInfo"))
    if good.get("type") == RES_EQUIP:
        return any(_int(row.get("equipId")) == good.get("id") for row in _client_rows("EquipInfo"))
    return True


def _resolve_shop(raw: Mapping[str, Any], version: int) -> Optional[dict[str, Any]]:
    shop_id = _int(raw.get("shopId"))
    if shop_id <= 0:
        return None
    pools: dict[str, list[dict[str, Any]]] = {}
    raw_pools = raw.get("pools") if isinstance(raw.get("pools"), Mapping) else {}
    for name, pool in raw_pools.items():
        if isinstance(pool, Mapping) and "generate" in pool:
            goods = _generate_pool(pool)
        elif isinstance(pool, list):
            goods = [
                _good_template(_int(item.get("shopGoodId")), item.get("good", {}), item.get("cost"),
                               buy_limit=item.get("buyLimit"), chance=item.get("chance", 1))
                for item in pool if isinstance(item, Mapping) and _int(item.get("shopGoodId")) > 0
            ]
        else:
            goods = []
        pools[str(name)] = [good for good in goods if _valid_good(good)]
    slots = [
        (str(slot.get("pool")), max(0, _int(slot.get("count"), 1)))
        for slot in (raw.get("slots") or []) if isinstance(slot, Mapping)
    ]
    refresh_limit = max(0, _int(raw.get("refreshLimit")))
    refresh_cost = raw.get("refreshCost")
    base_info = {
        "shopId": shop_id,
        "playerLevel": max(0, _int(raw.get("playerLevel"), 1)),
        "refreshDayOfMonth": max(0, _int(raw.get("refreshDayOfMonth"))),
        "refreshDayOfWeek": max(0, _int(raw.get("refreshDayOfWeek"))),
        "refreshTimeInDay": str(raw.get("refreshTimeInDay") or "06:00:00"),
        "refreshLimitNotify": max(0, _int(raw.get("refreshLimitNotify"))),
        "refreshLimit": refresh_limit,
        "refreshFree": max(0, _int(raw.get("refreshFree"))),
        "maxPosition": sum(count for _, count in slots),
        "shufflePosition": bool(raw.get("shufflePosition", False)),
        "displayCost": [_st_resource(cost) for cost in (raw.get("displayCost") or []) if isinstance(cost, Mapping)],
        "refreshOld": False,
        "show": True,
        "activity": False,
        "guild": bool(raw.get("guild", False)),
        "vipSuperExtraCount": 0,
        "clientShow": bool(raw.get("clientShow", True)),
        "version": version,
    }
    return {
        "shopId": shop_id,
        "baseInfo": base_info,
        "refreshCost": _st_resource(refresh_cost) if isinstance(refresh_cost, Mapping) and refresh_limit else None,
        "levelWindow": _int(raw["levelWindow"]) if "levelWindow" in raw else None,
        "slots": slots,
        "pools": pools,
    }


@lru_cache(maxsize=None)
def _load_shop_catalog_cached(path: str) -> dict[str, Any]:
    raw = _load_yaml(Path(path))
    if not isinstance(raw, Mapping) or not isinstance(raw.get("shops"), list):
        raw = _DEFAULT_SHOP_CATALOG
    version = max(1, _int(raw.get("catalogVersion"), 1))
    shops = [shop for shop in (_resolve_shop(item, version) for item in raw["shops"] if isinstance(item, Mapping))
             if shop is not None]
    return {"version": version, "shops": shops}


def load_shop_catalog(path: str | Path = _SHOP_CATALOG_PATH) -> dict[str, Any]:
    """Resolved shop catalog: ``{"version", "shops": [spec, ...]}`` in tab order."""

    return _load_shop_catalog_cached(str(Path(path)))


def _parse_refresh_time(value: Any) -> tuple[int, int, int]:
    parts = [_int(part) for part in str(value or "06:00:00").split(":")[:3]]
    parts += [0] * (3 - len(parts))
    hour, minute, second = parts
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        return 6, 0, 0
    return hour, minute, second


def next_shop_refresh(base_info: Mapping[str, Any], now: int, policy: ResetPolicy = DEFAULT_RESET_POLICY) -> int:
    """Next automatic refresh (UTC) from ShopType's refresh fields.

    ``refreshDayOfMonth`` (1-31) makes it monthly, otherwise
    ``refreshDayOfWeek`` (1 = Monday .. 7 = Sunday) weekly, otherwise daily
    at ``refreshTimeInDay``.
    """

    hour, minute, second = _parse_refresh_time(base_info.get("refreshTimeInDay"))
    current = policy.local_datetime(now)
    candidate = current.replace(hour=hour, minute=minute, second=second, microsecond=0)
    day_of_month = _int(base_info.get("refreshDayOfMonth"))
    day_of_week = _int(base_info.get("refreshDayOfWeek"))
    if 1 <= day_of_month <= 31:
        for _ in range(400):
            if candidate.day == day_of_month and candidate > current:
                break
            candidate += timedelta(days=1)
    elif 1 <= day_of_week <= 7:
        candidate += timedelta(days=(day_of_week - 1 - candidate.weekday()) % 7)
        if candidate <= current:
            candidate += timedelta(days=7)
    elif candidate <= current:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


class LocalEconomy:
    """Persistent local shop and explicitly sandboxed payment handler."""

    # Product IDs are intentionally local and finite.  The optional aliases
    # make the seam convenient for a local UI without accepting arbitrary
    # numeric suffixes as free currency.
    _PRODUCTS: Dict[str, Dict[str, Any]] = {
        "local.sandbox.60": {"price": 6.0, "count": 60},
        "local.sandbox.300": {"price": 30.0, "count": 300},
        "local.sandbox.980": {"price": 98.0, "count": 980},
        "local.sandbox.1980": {"price": 198.0, "count": 1980},
        "local.sandbox.3280": {"price": 328.0, "count": 3280},
        "local.sandbox.6480": {"price": 648.0, "count": 6480},
        "local.sandbox.64800": {"price": 6480.0, "count": 64800},
        "local.sandbox.648000": {"price": 64800.0, "count": 648000},
        "local_60": {"price": 6.0, "count": 60},
        "local_300": {"price": 30.0, "count": 300},
        "local_980": {"price": 98.0, "count": 980},
        "local_1980": {"price": 198.0, "count": 1980},
        "local_3280": {"price": 328.0, "count": 3280},
        "local_6480": {"price": 648.0, "count": 6480},
        "local_64800": {"price": 6480.0, "count": 64800},
        "local_648000": {"price": 64800.0, "count": 648000},
    }

    # ``Mall`` is a separate cash/gift shop from the player ``Shop``.  The
    # retired service no longer supplies its catalog, so keep a small explicit
    # local catalog.  These IDs are also useful for clients whose static data
    # still points at the original low numbered mall entries.
    _MALL_PRODUCTS: Dict[int, Dict[str, Any]] = {
        1: {"cost": {"type": 7, "id": 0, "count": 1000, "param": 0}, "good": {"type": 11, "id": 20001, "count": 1, "param": 0}, "limit": 5},
        2: {"cost": {"type": 7, "id": 0, "count": 2500, "param": 0}, "good": {"type": 11, "id": 20002, "count": 1, "param": 0}, "limit": 3},
        3: {"cost": {"type": 7, "id": 0, "count": 5000, "param": 0}, "good": {"type": 6, "id": 0, "count": 10, "param": 0}, "limit": 2},
        10001: {"cost": {"type": 7, "id": 0, "count": 1000, "param": 0}, "good": {"type": 11, "id": 20001, "count": 1, "param": 0}, "limit": 5},
        10002: {"cost": {"type": 7, "id": 0, "count": 2500, "param": 0}, "good": {"type": 11, "id": 20002, "count": 1, "param": 0}, "limit": 3},
        10003: {"cost": {"type": 7, "id": 0, "count": 5000, "param": 0}, "good": {"type": 6, "id": 0, "count": 10, "param": 0}, "limit": 2},
    }

    # Recharge model IDs observed in the retired client.  They intentionally
    # map to local sandbox denominations; no platform receipt or real money
    # provider is involved.  Gift-box IDs are kept separate from the normal
    # product IDs so the client can render both shop sections.
    _RECHARGE_OFFERS: Dict[int, Dict[str, Any]] = {
        11: {"productId": "local.sandbox.60", "count": 60, "price": 0.0},
        51: {"productId": "local.sandbox.300", "count": 300, "price": 0.0},
        52: {"productId": "local.sandbox.980", "count": 980, "price": 0.0},
        100: {"productId": "local.sandbox.1980", "count": 1980, "price": 0.0},
        101: {"productId": "local.sandbox.3280", "count": 3280, "price": 0.0},
        102: {"productId": "local.sandbox.6480", "count": 6480, "price": 0.0},
        4005: {"productId": "local.sandbox.300", "count": 300, "price": 0.0, "giftBox": True},
        4006: {"productId": "local.sandbox.980", "count": 980, "price": 0.0, "giftBox": True},
        4007: {"productId": "local.sandbox.1980", "count": 1980, "price": 0.0, "giftBox": True},
    }

    def __init__(
        self,
        state_dir: str | Path,
        game: Any,
        *,
        clock: Optional[Callable[[], int]] = None,
        unified_store_path: str | Path | None = None,
        legacy_recharge_enabled: Optional[bool] = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.unified_store_path = resolve_unified_store_path(self.state_dir, unified_store_path)
        if self.unified_store_path is not None:
            ensure_unified_store(self.unified_store_path)
            self.db_path = self.unified_store_path
        else:
            self.db_path = self.state_dir / "economy.sqlite3"
        self.game = game
        self._clock = clock or (lambda: int(time.time()))
        # The retired ``/fci/recharge/*`` compatibility endpoints used to
        # mint currency without a purchase. Keep them disabled by default;
        # migration tooling can explicitly opt in without affecting the
        # real ``purchasetouch``/``purchasedelevery`` and Mall flows.
        if legacy_recharge_enabled is None:
            legacy_recharge_enabled = os.environ.get("LJJC_ENABLE_LEGACY_RECHARGE", "").strip().lower() in {
                "1", "true", "yes", "on",
            }
        self.legacy_recharge_enabled = bool(legacy_recharge_enabled)
        # A shop/payment state file may be opened by more than one local game
        # object (for example during a development reload).  Share the lock by
        # canonical SQLite path so read/modify/write mutations remain atomic.
        self._lock = shared_store_lock(self.db_path)
        # Side effects of a successful fci/resbuyprice/resbuy/ purchase that
        # belong to other modules, by RefreshPrice type (e.g. 10 DeployTimes
        # = refresh deploy missions).  hook(profile, params) -> resChange list.
        self.resbuy_hooks: Dict[int, Callable[[Mapping[str, Any], int], Any]] = {}
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if (connection := self.game.transaction_connection()) is not None:
            return connection
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        configure_legacy_connection(connection)
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS economy_profiles (
                    account_id INTEGER PRIMARY KEY,
                    shop_json TEXT NOT NULL,
                    pending_json TEXT NOT NULL,
                    purchases_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _now(self) -> int:
        return int(self._clock())

    def _daily_key(self, value: int | None = None) -> str:
        """The same local daily bucket for direct callers and HTTP service."""
        return policy_for(self.game).day_key(self._now() if value is None else int(value))

    @staticmethod
    def _account_id(account: Mapping[str, Any]) -> int:
        value = account.get("accountId", account.get("account_id"))
        if value is None:
            raise ValueError("authenticated account id is required")
        return int(value)

    def _account_for_game(self, account: Mapping[str, Any]) -> Mapping[str, Any]:
        if "accountId" in account:
            return account
        return {"accountId": self._account_id(account)}

    # ------------------------------------------------------------------
    # 商店 (fci/Shop/, fci/ShopTrade)
    #
    # window__shop__shop.lua binds fci/Shop/ as a *list* of ApiShop.result
    # objects (v.baseInfo.shopId / playerLevel / clientShow / activity,
    # nextRefreshTime, refreshCount, nextFreeRefreshTime, nextRefreshCost,
    # newShop, endTime) and fci/Shop/<id>/Goods/ as a *list* of
    # DBPlayerShop.Refreshed.Info.Single.  The client stores typed replies
    # with their wrapper ({get = [...]}), so every shop reply is plain
    # camelCase JSON.  The catalog comes from data/server-only/shop.yaml.
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_level(profile: Mapping[str, Any]) -> int:
        return max(1, _int(profile.get("level"), 1))

    def _shop_catalog(self) -> dict[str, Any]:
        return load_shop_catalog(getattr(self, "shop_catalog_path", _SHOP_CATALOG_PATH))

    def _shop_spec(self, shop_id: int) -> Optional[dict[str, Any]]:
        for spec in self._shop_catalog()["shops"]:
            if spec["shopId"] == int(shop_id):
                return spec
        return None

    def _generate_shop_goods(self, spec: Mapping[str, Any], *, level: int, seed: str) -> list[dict[str, Any]]:
        """Draw the shop's goods: each slot group picks from its pool by chance."""

        rng = random.Random(seed)
        window = spec.get("levelWindow")
        taken: set[int] = set()
        goods: list[dict[str, Any]] = []
        for pool_name, count in spec.get("slots", []):
            candidates = [
                template for template in spec.get("pools", {}).get(pool_name, [])
                if template["chance"] > 0 and template["shopGoodId"] not in taken
                and (window is None or template["requireLevel"] <= level + int(window))
            ]
            for _ in range(count):
                if not candidates:
                    break
                total = sum(template["chance"] for template in candidates)
                roll = rng.uniform(0, total)
                chosen = candidates[-1]
                for template in candidates:
                    roll -= template["chance"]
                    if roll <= 0:
                        chosen = template
                        break
                candidates.remove(chosen)
                taken.add(chosen["shopGoodId"])
                goods.append({
                    "position": len(goods),
                    "shopGoodId": chosen["shopGoodId"],
                    "good": dict(chosen["good"]),
                    "cost": [dict(cost) for cost in chosen["cost"]],
                    "buyCountRecord": 0,
                    "buyLimit": chosen["buyLimit"],
                    "sold": False,
                    "canRefresh": True,
                    "guildLevelLimit": 0,
                })
        return goods

    def _regenerate_shop(self, account_id: int, spec: Mapping[str, Any], shop: dict[str, Any], *,
                         now: int, level: int) -> None:
        serial = _int(shop.get("refreshSerial")) + 1
        shop["refreshSerial"] = serial
        shop["goods"] = self._generate_shop_goods(
            spec, level=level, seed=f"{account_id}:{spec['shopId']}:{serial}:{now}"
        )
        shop["generatedLevel"] = level

    def _new_shop_state(self, account_id: int, spec: Mapping[str, Any], *, now: int, level: int,
                        version: int) -> dict[str, Any]:
        base = spec["baseInfo"]
        shop: dict[str, Any] = {
            "shopId": spec["shopId"],
            "catalogVersion": version,
            "newShop": True,
            "refreshCount": 0,
            "nextRefreshTime": next_shop_refresh(base, now, policy_for(self.game)),
            # refreshFree > 0: the first free refresh is available at once;
            # afterwards it is lastFreeRefreshTime + refreshFree, which is
            # exactly what OnRefresh computes from refreshTimeFree.
            "nextFreeRefreshTime": now if base["refreshFree"] > 0 else 0,
            "lastFreeRefreshTime": 0,
            "startTime": now,
            "endTime": 0,
            "refreshSerial": 0,
        }
        self._regenerate_shop(account_id, spec, shop, now=now, level=level)
        return shop

    def _sync_shops(self, account: Mapping[str, Any], state: Dict[str, Any], level: int) -> bool:
        """Reconcile stored shops with the catalog and apply due auto refreshes."""

        catalog = self._shop_catalog()
        account_id = self._account_id(account)
        now = self._now()
        stored = {
            _int(shop.get("shopId")): shop
            for shop in state.get("shop", []) if isinstance(shop, dict)
        }
        changed = False
        shops: list[dict[str, Any]] = []
        for spec in catalog["shops"]:
            shop = stored.get(spec["shopId"])
            if shop is None or _int(shop.get("catalogVersion")) != catalog["version"]:
                # New account, new shop or catalog change (this also migrates
                # the old single local test shop).
                shop = self._new_shop_state(account_id, spec, now=now, level=level, version=catalog["version"])
                changed = True
            elif 0 < _int(shop.get("nextRefreshTime")) <= now:
                # Automatic refresh: new goods, purchase limits and the paid
                # refresh counter reset (UpdateWindow resets refreshCount too).
                self._regenerate_shop(account_id, spec, shop, now=now, level=level)
                shop["refreshCount"] = 0
                shop["nextRefreshTime"] = next_shop_refresh(spec["baseInfo"], now, policy_for(self.game))
                changed = True
            shops.append(shop)
        if [_int(shop.get("shopId")) for shop in state.get("shop", []) if isinstance(shop, dict)] != [
            shop["shopId"] for shop in shops
        ]:
            changed = True
        state["shop"] = shops
        return changed

    def _shop_json(self, spec: Mapping[str, Any], shop: Mapping[str, Any]) -> dict[str, Any]:
        """ApiShop.result as plain JSON (endTime/nextRefreshTime always set)."""

        base = json.loads(json.dumps(spec["baseInfo"]))
        value: dict[str, Any] = {
            "baseInfo": base,
            "newShop": bool(shop.get("newShop", False)),
            "refreshCount": _int(shop.get("refreshCount")),
            "nextRefreshTime": _int(shop.get("nextRefreshTime")),
            "nextFreeRefreshTime": _int(shop.get("nextFreeRefreshTime")),
            "startTime": _int(shop.get("startTime")),
            "endTime": _int(shop.get("endTime")),
        }
        # The refresh button is shown only when nextRefreshCost exists.
        if spec.get("refreshCost") and base["refreshLimit"] > 0:
            value["nextRefreshCost"] = dict(spec["refreshCost"])
        return value

    @staticmethod
    def _goods_json(shop: Mapping[str, Any]) -> list[dict[str, Any]]:
        goods = [dict(good) for good in shop.get("goods", []) if isinstance(good, Mapping)]
        for good in goods:
            good["good"] = _st_resource(good.get("good"))
            good["cost"] = [_st_resource(cost) for cost in good.get("cost", []) if isinstance(cost, Mapping)]
        return sorted(goods, key=lambda good: _int(good.get("position")))

    @staticmethod
    def _find_shop_state(state: Mapping[str, Any], shop_id: int) -> Optional[Dict[str, Any]]:
        for shop in state.get("shop", []):
            if isinstance(shop, dict) and _int(shop.get("shopId")) == int(shop_id):
                return shop
        return None

    @staticmethod
    def _match_good(shop: Mapping[str, Any], good_id: int, position: Optional[int],
                    index: Optional[int]) -> Optional[Dict[str, Any]]:
        goods = [good for good in shop.get("goods", []) if isinstance(good, dict)]
        if good_id:
            if position is not None:
                for good in goods:
                    if _int(good.get("shopGoodId")) == good_id and _int(good.get("position")) == position:
                        return good
            for good in goods:
                if _int(good.get("shopGoodId")) == good_id:
                    return good
            return None
        slot = position if position is not None else index
        if slot is not None:
            for good in goods:
                if _int(good.get("position")) == slot:
                    return good
        return None

    def _pay_and_grant(self, profile: Mapping[str, Any], costs: list[Mapping[str, Any]],
                       grants: list[Mapping[str, Any]]) -> tuple[bool, list[dict[str, Any]], str | None]:
        """Spend ``costs`` and grant ``grants``; return resChange entries.

        Costs and ledger resources (gold, power, money, ...) move in one
        atomic ``_change_resources`` batch, so an unaffordable purchase has
        no effect.  Items, equips and actors go to their own stores through
        ``grants.grant_rewards`` / ``equipment.grant_equipment`` afterwards.
        """

        item_costs: dict[int, int] = {}
        ledger = []
        for cost in costs:
            value = _st_resource(cost)
            if not value['count']:
                continue
            if value['type'] == RES_ITEM:
                item_costs[value['id']] = item_costs.get(value['id'], 0) + abs(value['count'])
            else:
                ledger.append(dict(value, count=-abs(value['count'])))
        bag_state = self.game.systems._state(profile) if item_costs else None
        for item_id, count in item_costs.items():
            if _int(bag_state.get('items', {}).get(str(item_id), {}).get('count')) < count:
                return False, [], 'insufficient_resource'
        instances: list[dict[str, int]] = []
        for grant in grants:
            value = _st_resource(grant)
            if value["count"] <= 0:
                continue
            if value["type"] in _INSTANCE_RESOURCE_TYPES:
                instances.append(value)
            else:
                ledger.append(value)
        changes: list[dict[str, Any]] = []
        if ledger:
            ok, deltas, error = self._change_resources(profile, ledger)
            if not ok:
                return False, [], error or "insufficient_resource"
            changes.extend({"baseRes": dict(delta)} for delta in deltas)
        if bag_state is not None:
            for item_id, count in item_costs.items():
                item = bag_state['items'][str(item_id)]
                item['count'] = _int(item.get('count')) - count
                changes.append({'baseRes': {'type': RES_ITEM, 'id': item_id, 'param': 0,
                                            'count': item['count'], 'countDelta': -count}})
            self.game.systems._save(profile, bag_state)
        if instances:
            try:
                from .grants import grant_rewards
                from . import equipment
            except ImportError:  # pragma: no cover - direct PYTHONPATH execution
                from grants import grant_rewards
                import equipment
            plain = [value for value in instances if value["type"] != RES_EQUIP]
            changes.extend(grant_rewards(self.game, profile, plain))
            for value in instances:
                if value["type"] == RES_EQUIP:
                    for _ in range(value["count"]):
                        changes.append(equipment.grant_equipment(self.game, profile, value["id"]))
        return True, changes, None

    def _shop_refresh(self, account: Mapping[str, Any], spec: Mapping[str, Any], shop: Dict[str, Any],
                      body: Body, record: Mapping[str, Any]) -> Response:
        """POST fci/Shop/<id>/Goods/ {refresh_free}: manual (free or paid) refresh.

        OnRefresh reads result.resChange, result.refreshCount (paid),
        result.refreshTimeFree (+ baseInfo.refreshFree = next free time) and
        stores result.info as the goods list.
        """

        payload = _body_object(body)
        request = _protobuf_request(self.game, body, "ApiShopGoods") if not payload else None
        free = bool(payload.get(
            "refresh_free",
            payload.get("refreshFree", getattr(request, "refresh_free", False) if request is not None else False),
        ))
        base = spec["baseInfo"]
        now = self._now()
        changes: list[dict[str, Any]] = []
        if free:
            next_free = _int(shop.get("nextFreeRefreshTime"))
            if base["refreshFree"] <= 0 or not next_free or now < next_free:
                return 409, {"error": "shop_free_refresh_unavailable", "nextFreeRefreshTime": next_free}
        else:
            cost = spec.get("refreshCost")
            if not cost or base["refreshLimit"] <= 0:
                return 409, {"error": "shop_refresh_unavailable"}
            if _int(shop.get("refreshCount")) >= base["refreshLimit"]:
                return 409, {"error": "shop_refresh_limit_reached", "refreshLimit": base["refreshLimit"]}
            ok, changes, error = self._pay_and_grant(record, [cost], [])
            if not ok:
                return 409, {"error": error or "insufficient_resource"}
            shop["refreshCount"] = _int(shop.get("refreshCount")) + 1
        self._regenerate_shop(self._account_id(account), spec, shop, now=now, level=self._profile_level(record))
        if free:
            shop["lastFreeRefreshTime"] = now
            shop["nextFreeRefreshTime"] = now + base["refreshFree"]
        return 200, {
            "info": self._goods_json(shop),
            "resChange": changes,
            "refreshCount": _int(shop.get("refreshCount")),
            "refreshTimeFree": _int(shop.get("lastFreeRefreshTime")),
        }

    def _shop_trade(self, account: Mapping[str, Any], state: Dict[str, Any], body: Body,
                    record: Mapping[str, Any]) -> Response:
        """POST fci/ShopTrade: ProtobufT("ApiShopTrade", {info, shopId, buyCount[, index]}).

        Window_ShopBuy sends no index, so the good is matched by
        info.shopGoodId (+ info.position).  buyCount multiplies the cost and
        the good and counts against buyLimit.  The reply is read as
        result.resChange (DBH.ResChange + WU.ShowRewards).
        """

        payload = _body_object(body)
        message = _protobuf_request(self.game, body, "ApiShopTrade") if not payload else None
        info = payload.get("info") if isinstance(payload.get("info"), Mapping) else {}
        message_info = message.info if message is not None and message.HasField("info") else None
        shop_id = _int(payload.get("shopId", payload.get("shop_id",
                       getattr(message, "shop_id", 0) if message is not None else 0)))
        buy_count = _int(payload.get("buyCount", payload.get("buy_count",
                         getattr(message, "buy_count", 0) if message is not None else 0)))
        buy_count = buy_count if buy_count > 0 else 1
        good_id = _int(info.get("shopGoodId", payload.get("shopGoodId", payload.get("goodId",
                       getattr(message_info, "shopGoodId", 0) if message_info is not None else 0))))
        if "position" in info or "position" in payload:
            position: Optional[int] = _int(info.get("position", payload.get("position")))
        elif message_info is not None:
            position = int(message_info.position)
        else:
            position = None
        index_value = payload.get("index", getattr(message, "index", None) if message is not None else None)
        index = _int(index_value) if index_value is not None else None
        if buy_count > 999:
            return 400, {"error": "invalid_buy_count", "max": 999}
        spec = self._shop_spec(shop_id)
        shop = self._find_shop_state(state, shop_id)
        if spec is None or shop is None:
            return 404, {"error": "shop_not_found"}
        if self._profile_level(record) < spec["baseInfo"]["playerLevel"]:
            return 403, {"error": "shop_locked", "playerLevel": spec["baseInfo"]["playerLevel"]}
        if spec["baseInfo"].get("guild") and not self._has_guild(record):
            # The client hides shop 12 for guildless players, but a direct
            # trade request must enforce the same gate server-side.
            return 403, {"error": "guild_required"}
        good = self._match_good(shop, good_id, position, index)
        if good is None:
            return 404, {"error": "shop_good_not_found"}
        limit = _int(good.get("buyLimit"))
        bought = _int(good.get("buyCountRecord"))
        if good.get("sold") or (limit and bought + buy_count > limit):
            return 409, {"error": "shop_good_limit_reached", "buyLimit": limit, "buyCountRecord": bought}
        costs = [dict(cost, count=_int(cost.get("count")) * buy_count)
                 for cost in good.get("cost", []) if isinstance(cost, Mapping)]
        if not costs:
            return 409, {"error": "shop_good_missing_cost"}
        product = _st_resource(good.get("good"))
        product["count"] *= buy_count
        ok, changes, error = self._pay_and_grant(record, costs, [product])
        if not ok:
            return 409, {"error": error or "insufficient_resource"}
        good["buyCountRecord"] = bought + buy_count
        if limit and good["buyCountRecord"] >= limit:
            good["sold"] = True
        self._save_state(account, state)
        tasks = getattr(self.game, "tasks", None)
        if tasks is not None:
            try:
                # TaskType_ShopP0BuyMTimes (49): one purchase in shop P0.
                tasks.record(record, "ShopP0BuyMTimes", 1, param=shop_id)
            except Exception:  # task progress must never undo a paid trade
                pass
        good_json = self._goods_json({"goods": [good]})[0]
        return 200, {"buyCount": _int(good["buyCountRecord"]), "info": good_json, "resChange": changes}

    def _has_guild(self, record: Mapping[str, Any]) -> bool:
        """Return the durable guild membership used by the guild shop gate."""

        guild = getattr(self.game, "guild", None)
        if guild is None:
            return False
        try:
            return _int(guild.player_data(record).get("guildId")) > 0
        except Exception:
            # A missing/corrupt guild row is treated as guildless.  This keeps
            # the shop from becoming an accidental privilege escalation while
            # leaving the guild subsystem to report its own diagnostics.
            return False

    def _shop_http(self, method: str, route: str, body: Body, account: Mapping[str, Any]) -> Optional[Response]:
        # Cost, bag reward and durable shop limit must commit together. This
        # boundary also covers direct compatibility calls and legacy stores.
        if route.lower().startswith(('/fci/shop/', '/fci/shoptrade/')):
            with self.game.atomic_mutation():
                return self._shop_http_transaction(method, route, body, account)
        return None

    def _shop_http_transaction(self, method: str, route: str, body: Body, account: Mapping[str, Any]) -> Optional[Response]:
        lower = route.lower()
        parts = [part for part in lower.split("/") if part]
        is_list = lower == "/fci/shop/"
        is_trade = lower == "/fci/shoptrade/"
        shop_id = int(parts[2]) if len(parts) >= 3 and parts[1] == "shop" and parts[2].isdigit() else None
        is_goods = shop_id is not None and len(parts) == 4 and parts[3] == "goods"
        is_shop = shop_id is not None and len(parts) == 3
        if not (is_list or is_trade or is_goods or is_shop):
            return None
        record = self.game._profile(self._account_for_game(account))
        state = self._state(account)
        if self._sync_shops(account, state, self._profile_level(record)):
            self._save_state(account, state)
        if is_list:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            return 200, [
                self._shop_json(spec, shop)
                for spec in self._shop_catalog()["shops"]
                for shop in [self._find_shop_state(state, spec["shopId"])] if shop is not None
            ]
        if is_trade:
            if method == "GET":
                return 200, []
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._shop_trade(account, state, body, record)
        spec = self._shop_spec(shop_id)
        shop = self._find_shop_state(state, shop_id)
        if spec is None or shop is None:
            return 404, {"error": "shop_not_found"}
        if is_goods:
            if method == "GET":
                return 200, self._goods_json(shop)
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            status, result = self._shop_refresh(account, spec, shop, body, record)
            if status == 200:
                self._save_state(account, state)
            return status, result
        if method == "GET":
            return 200, self._shop_json(spec, shop)
        if method in {"PATCH", "POST"}:
            # Window_Shop: Patch({new_shop = false}) when a "new" tab is opened.
            payload = _body_object(body)
            message = _protobuf_request(self.game, body, "ApiShop") if not payload else None
            shop["newShop"] = bool(payload.get(
                "new_shop", payload.get("newShop", getattr(message, "new_shop", False) if message is not None else False)
            ))
            self._save_state(account, state)
            return 200, self._shop_json(spec, shop)
        return 405, {"error": "method_not_allowed", "message": "GET or PATCH required"}

    # ------------------------------------------------------------------
    # 体力/金币购买 (fci/resbuyprice/)
    #
    # Window_ResourceExchange: GET fci/resbuyprice/<RefreshPriceType>[/<param>]
    # -> {cost, buy, remainBuyTimes}; POST fci/resbuyprice/resbuy/
    # {resbuypriceType, params} -> {resChange, cost, buy, remainBuyTimes}
    # where cost/remainBuyTimes describe the *next* purchase (SetView).  The
    # nth purchase of the day costs RefreshPrice row count == n; a type whose
    # rows have no count (the 1000201-1000203 gold packs) is unlimited and
    # its replies omit remainBuyTimes (nil = no limit in SetView).
    # ------------------------------------------------------------------

    @staticmethod
    def _resbuy_limited(rows: tuple[dict[str, Any], ...]) -> bool:
        return any(_int(row.get("count")) > 0 for row in rows)

    def _resbuy_record(self, state: Dict[str, Any], price_type: int, params: int) -> dict[str, Any]:
        """DBGamePlayer.RefreshPriceInfo-like daily counter per (type, params)."""

        records = state.setdefault("purchases", {}).setdefault("resBuy", {})
        key = f"{int(price_type)}:{int(params)}"
        record = records.get(key)
        today = self._daily_key()
        if not isinstance(record, dict) or period_due(record.get("dayKey"), today):
            record = {"type": int(price_type), "params": int(params), "attendTimes": 0,
                      "startTime": 0, "dayKey": today}
            records[key] = record
        return record

    def _resbuy_quote(self, price_type: int, bought: int) -> dict[str, Any]:
        rows = _refresh_price_table().get(int(price_type), ())
        if not rows:
            return {"cost": _st_resource({}), "buy": _st_resource({}), "remainBuyTimes": 0}
        if not self._resbuy_limited(rows):
            row = rows[0]
            return {"cost": _st_resource(row.get("cost")), "buy": _st_resource(row.get("buy"))}
        row = rows[min(max(0, bought), len(rows) - 1)]
        return {"cost": _st_resource(row.get("cost")), "buy": _st_resource(row.get("buy")),
                "remainBuyTimes": max(0, len(rows) - bought)}

    def _res_buy_price(self, account: Mapping[str, Any], price_type: int, params: int) -> Response:
        state = self._state(account)
        record = self._resbuy_record(state, price_type, params)
        return 200, self._resbuy_quote(price_type, _int(record.get("attendTimes")))

    def _res_buy(self, account: Mapping[str, Any], body: Body) -> Response:
        payload = _body_object(body)
        if not payload:
            # resbuyprice.proto does not load into the extracted descriptor
            # pool, so decode ApiResBuy{resbuyprice_type = 1, params = 2}
            # (both uint32) straight from the wireformat.
            payload = {"resbuypriceType": 0, "params": 0, **_wire_varints(body, {1: "resbuypriceType", 2: "params"})}
        price_type = _int(payload.get("resbuypriceType", payload.get("resbuyprice_type", payload.get("type"))))
        params = _int(payload.get("params"))
        rows = _refresh_price_table().get(price_type, ())
        if not rows:
            return 404, {"error": "unknown_price_type", "resbuypriceType": price_type}
        state = self._state(account)
        record = self._resbuy_record(state, price_type, params)
        bought = _int(record.get("attendTimes"))
        quote = self._resbuy_quote(price_type, bought)
        if self._resbuy_limited(rows) and quote.get("remainBuyTimes", 0) <= 0:
            return 409, {"error": "resbuy_no_times", "remainBuyTimes": 0}
        profile = self.game._profile(self._account_for_game(account))
        buy = dict(quote["buy"])
        if buy["type"] == RES_PLAYER_POWER and buy["count"] > 0:
            # Window_ResourceExchange.OnExchangeClick refuses a power purchase
            # at or above incInfo.max ("Window_ReachPowerLimit"); mirror it.
            policy = energy_policy(self._profile_level(profile))
            power = sum(_int(item.get("count")) for item in self._resources(profile)
                        if item["type"] == RES_PLAYER_POWER and item["id"] == 0)
            if power >= policy["max"]:
                return 409, {"error": "power_reach_limit", "power": power, "max": policy["max"]}
        grant = dict(buy)
        if grant["type"] == RES_DUNGEON_PLAY_TIMES_LIMIT and params:
            grant["id"] = params  # DungeonTimes is bought for one dungeon (params)
        ok, changes, error = self._pay_and_grant(profile, [quote["cost"]], [grant] if grant["count"] > 0 else [])
        if not ok:
            return 409, {"error": error or "insufficient_resource"}
        record["attendTimes"] = bought + 1
        record["startTime"] = self._now()
        self._save_state(account, state)
        hook = self.resbuy_hooks.get(price_type)
        if hook is not None:
            extra = hook(profile, params)
            if isinstance(extra, list):
                changes.extend(extra)
        reply = {"buyResult": True, **self._resbuy_quote(price_type, bought + 1), "resChange": changes}
        reply["buy"] = buy
        return 200, reply

    # ------------------------------------------------------------------
    # 体力 HUD (fci/baseresource/update/)
    # ------------------------------------------------------------------

    def _base_resource_update(self, account: Mapping[str, Any], profile: Mapping[str, Any]) -> Response:
        """Window_Resource.RequestResourceChange: the real recovery state.

        Applies elapsed recovery with the client's PlayerLevelInitConfig rule
        and returns ApiPlayerBaseResourceUpdate.result as JSON:
        resChange (the power STResourceDynamic, countDelta = recovered) and
        lastResIncTime {"6": marker}; the HUD keeps the marker only when the
        same type is in resChange, so the power entry is always included.
        """

        account_id = self._account_id(account)
        now = self._now()
        with self.game._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT level, resources_json FROM game_profiles WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                return 404, {"error": "profile_not_found"}
            try:
                resources = json.loads(row[1] or "[]")
            except (TypeError, ValueError):
                resources = []
            if not isinstance(resources, list):
                resources = []
            info = apply_energy_recovery(connection, account_id, resources, now=now, level=row[0])
        if isinstance(profile, dict):
            profile["resources"] = resources
        entry = info.get("entry")
        if entry is None:
            return 200, {"resChange": [], "lastResIncTime": {}}
        policy = info["policy"]
        change = {"baseRes": {"type": int(policy["type"]), "id": int(policy["id"]), "param": int(policy["param"]),
                              "count": _int(entry.get("count")), "countDelta": int(info["recovered"])}}
        return 200, {"resChange": [change], "lastResIncTime": {str(int(policy["type"])): int(info["lastRecoveryAt"])}}

    def energy_policy_for(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Client power rule (cap/interval) for a profile's current level."""

        return energy_policy(self._profile_level(profile))

    def _state(self, account: Mapping[str, Any]) -> Dict[str, Any]:
        account_id = self._account_id(account)
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM economy_profiles WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                state = {
                    "shop": [],
                    "pending": {},
                    "purchases": {},
                }
                connection.execute(
                    "INSERT OR IGNORE INTO economy_profiles(account_id, shop_json, pending_json, purchases_json, updated_at) VALUES(?,?,?,?,?)",
                    (
                        account_id,
                        json.dumps(state["shop"], ensure_ascii=False, separators=(",", ":")),
                        "{}",
                        "{}",
                        self._now(),
                    ),
                )
                return state
            try:
                shops = json.loads(row["shop_json"] or "[]")
            except (TypeError, ValueError):
                shops = []
            try:
                pending = json.loads(row["pending_json"] or "{}")
            except (TypeError, ValueError):
                pending = {}
            try:
                purchases = json.loads(row["purchases_json"] or "{}")
            except (TypeError, ValueError):
                purchases = {}
            if not isinstance(shops, list):
                shops = []
            if not isinstance(pending, dict):
                pending = {}
            if not isinstance(purchases, dict):
                purchases = {}
            return {"shop": shops, "pending": pending, "purchases": purchases}

    def _save_state(self, account: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE economy_profiles SET shop_json = ?, pending_json = ?, purchases_json = ?, updated_at = ? WHERE account_id = ?",
                (
                    json.dumps(state.get("shop", []), ensure_ascii=False, separators=(",", ":")),
                    json.dumps(state.get("pending", {}), ensure_ascii=False, separators=(",", ":")),
                    json.dumps(state.get("purchases", {}), ensure_ascii=False, separators=(",", ":")),
                    self._now(),
                    self._account_id(account),
                ),
            )

    def admin_billing_snapshot(self, *, limit: int = 200) -> Dict[str, Any]:
        """Return redacted local sandbox billing records for the admin panel.

        The legacy client stores completed local deliveries and recharge
        counters inside ``purchases_json``.  Keep this read-only diagnostic
        view separate from gameplay handlers so it cannot grant currency or
        replay a delivery.  Tokens and receipts are intentionally omitted;
        pending orders are represented only by a count.
        """

        limit = max(1, min(int(limit), 1000))
        records: list[Dict[str, Any]] = []
        pending_total = 0
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT account_id, pending_json, purchases_json, updated_at "
                "FROM economy_profiles ORDER BY updated_at DESC, account_id DESC"
            ).fetchall()
        for row in rows:
            account_id = int(row["account_id"])
            try:
                pending = json.loads(row["pending_json"] or "{}")
            except (TypeError, ValueError):
                pending = {}
            if isinstance(pending, dict):
                pending_total += len(pending)
            try:
                purchases = json.loads(row["purchases_json"] or "{}")
            except (TypeError, ValueError):
                purchases = {}
            if not isinstance(purchases, dict):
                continue
            for transaction_id, value in purchases.items():
                if not isinstance(value, Mapping):
                    continue
                if transaction_id == "__recharge__":
                    records.append({
                        "accountId": account_id,
                        "kind": "recharge-summary",
                        "totalCount": _int(value.get("totalCount")),
                        "claims": dict(value.get("claims", {})) if isinstance(value.get("claims"), Mapping) else {},
                        "monthCards": dict(value.get("monthCards", {})) if isinstance(value.get("monthCards"), Mapping) else {},
                        "permitLevels": dict(value.get("permitLevels", {})) if isinstance(value.get("permitLevels"), Mapping) else {},
                        "updatedAt": int(row["updated_at"]),
                    })
                    continue
                # Mall purchases are local shop mutations, not payment
                # deliveries.  They remain available through gameplay state.
                if not transaction_id or str(transaction_id).startswith("mall:"):
                    continue
                records.append({
                    "accountId": account_id,
                    "kind": "sandbox-delivery",
                    "transactionId": str(transaction_id),
                    "productId": str(value.get("productId", "")),
                    "count": _int(value.get("count"), 1),
                    "currency": str(value.get("currency", "LOCAL_SANDBOX")),
                    "completedAt": _int(value.get("completedAt")),
                    "resourceChanges": [dict(change) for change in value.get("resourceChanges", []) if isinstance(change, Mapping)],
                })
        records.sort(key=lambda item: (_int(item.get("completedAt", item.get("updatedAt", 0))), _int(item.get("accountId"))), reverse=True)
        return {
            "sandbox": True,
            "records": records[:limit],
            "totalRecords": len(records),
            "pendingOrderCount": pending_total,
        }

    def _resources(self, profile: Mapping[str, Any]) -> list[dict[str, int]]:
        result: list[dict[str, int]] = []
        raw = profile.get("resources", [])
        if not isinstance(raw, list):
            return result
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            result.append({
                "type": _int(item.get("type")),
                "id": _int(item.get("id")),
                "count": _int(item.get("count")),
                "param": _int(item.get("param")),
            })
        return result

    def _message(self, full_name: str):
        return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(full_name))()

    def _resource_message(self, value: Mapping[str, Any], *, delta: Optional[int] = None):
        result = self._message("STResource")
        result.type = _int(value.get("type"))
        result.id = _int(value.get("id"))
        result.count = _int(value.get("count"))
        result.param = _int(value.get("param"))
        if delta is not None:
            result.count_delta = int(delta)
        return result

    def _resource_dynamic_messages(self, changes: list[Mapping[str, Any]]):
        result = []
        for value in changes:
            dynamic = self._message("STResourceDynamic")
            dynamic.base_res.CopyFrom(
                self._resource_message(value, delta=_int(value.get("countDelta")))
            )
            result.append(dynamic)
        return result

    def _change_resources(self, profile: Mapping[str, Any], changes: list[Mapping[str, Any]]) -> tuple[bool, list[dict[str, int]], str | None]:
        """Apply one batch, including the total/paid currency relationship.

        A type-27 delta is an independent paid-currency change and also
        changes type 5. A negative type-5 delta spends free currency first.
        Callers must not supply a second delta for the automatic counterpart.
        """
        with self.game.atomic_mutation():
            with closing(self.game._connect()) as connection:
                row = connection.execute("SELECT resources_json FROM game_profiles WHERE account_id = ?",
                                         (int(profile["account_id"]),)).fetchone()
            if row is None:
                return False, [], "profile_not_found"
            current_profile = dict(profile)
            current_profile["resources"] = json.loads(row["resources_json"] or "[]")
            resources = self._resources(current_profile)
            balances: dict[tuple[int, int, int], int] = {}
            for item in resources:
                key = (item["type"], item["id"], item["param"])
                balances[key] = balances.get(key, 0) + item["count"]
            before = dict(balances)
            total_key, paid_key = (5, 0, 0), (27, 0, 0)
            # Old saves could have a paid balance above the total. Preserve
            # spendable money, and cap only the impossible paid portion.
            balances[total_key] = max(0, balances.get(total_key, 0))
            balances[paid_key] = min(balances[total_key], max(0, balances.get(paid_key, 0)))
            aggregate: dict[tuple[int, int, int], int] = {}
            for raw in changes:
                key = (_int(raw.get("type")), _int(raw.get("id")), _int(raw.get("param")))
                aggregate[key] = aggregate.get(key, 0) + _int(raw.get("count"))
            # Apply direct paid additions/charges first, then normal spending.
            paid_delta = aggregate.pop(paid_key, 0)
            if balances[paid_key] + paid_delta < 0:
                return False, [], "insufficient_resource"
            balances[paid_key] += paid_delta
            balances[total_key] += paid_delta
            normal_delta = aggregate.pop(total_key, 0)
            if balances[total_key] + normal_delta < 0:
                return False, [], "insufficient_resource"
            if normal_delta < 0:
                free = balances[total_key] - balances[paid_key]
                balances[paid_key] -= max(0, -normal_delta - free)
            balances[total_key] += normal_delta
            for key, delta in aggregate.items():
                if balances.get(key, 0) + delta < 0:
                    return False, [], "insufficient_resource"
                balances[key] = balances.get(key, 0) + delta
            result = [{"type": key[0], "id": key[1], "param": key[2], "count": count}
                      for key, count in balances.items()]
            deltas = [{**item, "countDelta": item["count"] - before.get((item["type"], item["id"], item["param"]), 0)}
                      for item in result
                      if item["count"] != before.get((item["type"], item["id"], item["param"]), 0)]
            with closing(self.game._connect()) as connection, connection:
                connection.execute("UPDATE game_profiles SET resources_json = ?, updated_at = ? WHERE account_id = ?",
                                   (json.dumps(result, separators=(",", ":")), self._now(), int(profile["account_id"])))
            if isinstance(profile, dict):
                track_mapping(self.game, profile, "resources")
                profile["resources"] = result
            return True, deltas, None

    @staticmethod
    def _mall_record_key(mall_id: int) -> str:
        return f"mall:{int(mall_id)}"

    def _mall_buy_count(self, state: Mapping[str, Any], mall_id: int) -> int:
        value = state.get("purchases", {}).get(self._mall_record_key(mall_id), {})
        return _int(value.get("buyCount", value.get("buy_count", 0))) if isinstance(value, Mapping) else 0

    def _mall_info_message(self, account: Mapping[str, Any], state: Mapping[str, Any]):
        result = self._message("ApiMallInfo.result")
        player_id = str(self.game._profile(self._account_for_game(account)).get("player_id", ""))
        result.mall_record.player_id = player_id
        result.purchase_record.player_id = player_id
        for mall_id in self._MALL_PRODUCTS:
            count = self._mall_buy_count(state, mall_id)
            if not count:
                continue
            summary = result.mall_record.summary[int(mall_id)]
            summary.buy_count = count
            summary.last_buy_start_time = _int(
                state.get("purchases", {}).get(self._mall_record_key(mall_id), {}).get("lastBuyStartTime", 0)
            )
        return result

    def _mall_trade(self, account: Mapping[str, Any], body: Body, state: Dict[str, Any]) -> Response:
        request = _body_object(body)
        message = _protobuf_request(self.game, body, "ApiMallTrade")
        mall_id = _int(request.get("id", getattr(message, "id", 0) if message is not None else 0))
        product = self._MALL_PRODUCTS.get(mall_id)
        if product is None:
            return 404, {"error": "mall_good_not_found"}
        bought = self._mall_buy_count(state, mall_id)
        limit = _int(product.get("limit"))
        if limit and bought >= limit:
            return 409, {"error": "mall_good_limit_reached"}
        profile = self.game._profile(self._account_for_game(account))
        cost = dict(product["cost"])
        good = dict(product["good"])
        ok, changes, error = self._change_resources(
            profile,
            [{**cost, "count": -abs(_int(cost.get("count")))}, good],
        )
        if not ok:
            return 409, {"error": error or "insufficient_resource"}
        now = self._now()
        state["purchases"][self._mall_record_key(mall_id)] = {
            "buyCount": bought + 1,
            "lastBuyStartTime": now,
        }
        self._save_state(account, state)
        result = self._message("ApiMallTrade.result")
        result.buy_count = bought + 1
        for dynamic in self._resource_dynamic_messages(changes):
            result.res_change.add().CopyFrom(dynamic)
        return 200, result

    @staticmethod
    def _request_value(body: Body, *keys: str, default: Any = None) -> Any:
        request = _body_object(body)
        for key in keys:
            if key in request:
                return request[key]
        return default

    def _recharge_offer(self, value: Any) -> Optional[Dict[str, Any]]:
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            numeric = None
        if numeric is not None and numeric in self._RECHARGE_OFFERS:
            return {"rechargeId": numeric, **self._RECHARGE_OFFERS[numeric]}
        if isinstance(value, str):
            for recharge_id, offer in self._RECHARGE_OFFERS.items():
                if value == offer.get("productId"):
                    return {"rechargeId": recharge_id, **offer}
        return None

    def _recharge_state(self, state: Mapping[str, Any]) -> Dict[str, Any]:
        raw = state.get("purchases", {}).get("__recharge__", {})
        if not isinstance(raw, Mapping):
            raw = {}
        result = dict(raw)
        result.setdefault("totalCount", 0)
        result.setdefault("claims", {})
        result.setdefault("monthCards", {})
        result.setdefault("permitLevels", {})
        result.setdefault("giftRewards", [])
        result.setdefault("firstReward", False)
        result.setdefault("activeCodes", [])
        return result

    def _save_recharge_state(self, account: Mapping[str, Any], state: Dict[str, Any], recharge: Mapping[str, Any]) -> None:
        state.setdefault("purchases", {})["__recharge__"] = dict(recharge)
        self._save_state(account, state)

    def _grant_paid_currency(self, account: Mapping[str, Any], count: int) -> tuple[bool, list[dict[str, int]], str | None]:
        profile = self.game._profile(self._account_for_game(account))
        return self._change_resources(profile, [{"type": 5, "id": 0, "param": 0, "count": int(count)}])

    def _recharge_info(self, account: Mapping[str, Any], state: Dict[str, Any], recharge_type: Any = None) -> Response:
        """Deprecated local-tool compatibility API; the 2.0.4 client uses LocalMall.

        These synthetic recharge rewards do not represent the real month card
        or first-purchase offer. New code must use mall.py's sandbox orders.
        """
        info = self._recharge_state(state)
        fixed = []
        gifts = []
        for recharge_id, offer in self._RECHARGE_OFFERS.items():
            row = {
                "id": recharge_id,
                "rechargeId": recharge_id,
                "productId": offer["productId"],
                "price": float(offer.get("price", 0.0)),
                "count": int(offer["count"]),
                "showInShop": True,
                "sandbox": True,
            }
            (gifts if offer.get("giftBox") else fixed).append(row)
        return 200, {
            "ok": True,
            "result": True,
            "code": 0,
            "sandbox": True,
            "rechargeType": _int(recharge_type),
            "fixedRechargeInfo": fixed,
            "giftBoxRechargeInfo": gifts,
            "firstRechargeInfo": {"reward": [{"type": 5, "id": 0, "count": 60, "param": 0}], "rewardGot": bool(info.get("firstReward"))},
            "totalRechargeCnt": _int(info.get("totalCount")),
            "monthCardInfo": info.get("monthCards", {}),
            "permitInfo": info.get("permitLevels", {}),
        }

    def _recharge_claim(self, account: Mapping[str, Any], state: Dict[str, Any], body: Body, kind: str, *, one_time: bool = False) -> Response:
        value = self._request_value(body, "rechargeId", "recharge_id", "id", "giftboxId", "gift_box_id", "giftId", "gift_id")
        offer = self._recharge_offer(value)
        if offer is None:
            return 404, {"error": "unknown_local_recharge", "message": "use a known local sandbox recharge id"}
        info = self._recharge_state(state)
        claims = dict(info.get("claims", {}))
        key = f"{kind}:{offer['rechargeId']}"
        if one_time and claims.get(key):
            return 409, {"error": "recharge_reward_already_claimed"}
        ok, changes, error = self._grant_paid_currency(account, int(offer["count"]))
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        claims[key] = _int(claims.get(key)) + 1
        info["claims"] = claims
        info["totalCount"] = _int(info.get("totalCount")) + int(offer["count"])
        self._save_recharge_state(account, state, info)
        return 200, {
            "ok": True,
            "result": True,
            "code": 0,
            "sandbox": True,
            "rechargeId": offer["rechargeId"],
            "rewardTbl": changes,
            "giftBoxRechargeInfo": [],
        }

    def _recharge_before_buy(self, body: Body) -> Response:
        value = self._request_value(body, "rechargeId", "recharge_id", "id")
        offer = self._recharge_offer(value)
        if offer is None:
            return 404, {"error": "unknown_local_recharge"}
        return 200, {"ok": True, "result": True, "code": 0, "sandbox": True, "rechargeId": offer["rechargeId"], "price": 0.0, "attachParams": {"rechargeId": offer["rechargeId"], "productId": offer["productId"]}}

    def _recharge_month_card(self, account: Mapping[str, Any], state: Dict[str, Any], body: Body) -> Response:
        month_id = _int(self._request_value(body, "monthCardCfgId", "month_card_cfg_id", "id", default=1), 1)
        info = self._recharge_state(state)
        cards = dict(info.get("monthCards", {}))
        today = self._daily_key()
        key = str(month_id)
        record = dict(cards.get(key, {}))
        if record.get("lastDay") == today:
            return 409, {"error": "month_card_reward_already_claimed", "monthCardCfgId": month_id}
        ok, changes, error = self._grant_paid_currency(account, 30)
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        record.update({"monthCardCfgId": month_id, "lastDay": today, "rewardCount": _int(record.get("rewardCount")) + 1})
        cards[key] = record; info["monthCards"] = cards
        self._save_recharge_state(account, state, info)
        return 200, {"ok": True, "result": True, "code": 0, "sandbox": True, "monthCardCfgId": month_id, "rewardTbl": changes}

    def _recharge_permit(self, account: Mapping[str, Any], state: Dict[str, Any], body: Body, action: str) -> Response:
        permit_id = _int(self._request_value(body, "permitId", "permit_id", "id", default=1), 1)
        level = _int(self._request_value(body, "level", "permitLevel", "permit_level", default=1), 1)
        info = self._recharge_state(state); permits = dict(info.get("permitLevels", {})); key = str(permit_id)
        current = _int(permits.get(key, 0))
        if action == "buy":
            if level < 1 or level > 100:
                return 400, {"error": "invalid_permit_level"}
            current = max(current, level)
            permits[key] = current
            info["permitLevels"] = permits
            self._save_recharge_state(account, state, info)
            return 200, {"ok": True, "result": True, "code": 0, "sandbox": True, "permitId": permit_id, "level": current}
        claim_key = f"{key}:{level}:{action}"
        claims = dict(info.get("claims", {}))
        if current < level:
            return 409, {"error": "permit_level_locked"}
        if claims.get(claim_key):
            return 409, {"error": "permit_reward_already_claimed"}
        ok, changes, error = self._grant_paid_currency(account, 50)
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        claims[claim_key] = 1; info["claims"] = claims
        self._save_recharge_state(account, state, info)
        return 200, {"ok": True, "result": True, "code": 0, "sandbox": True, "permitId": permit_id, "level": level, "rewardTbl": changes}

    def _recharge_active_code(self, account: Mapping[str, Any], state: Dict[str, Any], body: Body) -> Response:
        code = str(self._request_value(body, "code", "activeCode", "active_code", default="")).strip().upper()
        if not code.startswith("LOCAL-"):
            return 402, {"error": "active_code_verification_required"}
        info = self._recharge_state(state); used = set(str(x) for x in info.get("activeCodes", []))
        if code in used:
            return 409, {"error": "active_code_already_used"}
        ok, changes, error = self._grant_paid_currency(account, 100)
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        used.add(code); info["activeCodes"] = sorted(used); self._save_recharge_state(account, state, info)
        return 200, {"ok": True, "result": True, "code": 0, "sandbox": True, "rewardTbl": changes}

    def _purchase_product(self, product_id: str) -> Optional[Dict[str, Any]]:
        product = self._PRODUCTS.get(product_id)
        if product is None:
            return None
        return {"productId": product_id, **product}

    def _purchase_touch(self, account: Mapping[str, Any], body: Body) -> Response:
        request = _body_object(body)
        message = _protobuf_request(self.game, body, "ApiPurchaseTouchProduct")
        product_id = str(
            request.get(
                "productId",
                request.get(
                    "product_id",
                    getattr(message, "product_id", "") if message is not None else "",
                ),
            )
        )
        product = self._purchase_product(product_id)
        if product is None:
            return 400, {"error": "unknown_local_product", "message": "use a local.sandbox product id"}
        count = _int(
            request.get(
                "count", getattr(message, "count", 1) if message is not None else 1
            ),
            1,
        )
        if count < 1 or count > 10:
            return 400, {"error": "invalid_purchase_count"}
        state = self._state(account)
        token = "local-sandbox:" + secrets.token_urlsafe(24)
        state["pending"][token] = {
            "productId": product_id,
            "count": count,
            "createdAt": self._now(),
            "isGuest": bool(request.get("isGuest", request.get("is_guest", False))),
        }
        self._save_state(account, state)
        result = self._message("ApiPurchaseTouchProduct.result")
        result.trade_data = token
        result.is_sandbox = True
        return 200, result

    def _purchase_delivery(self, account: Mapping[str, Any], body: Body) -> Response:
        request = _body_object(body)
        message = _protobuf_request(self.game, body, "ApiPurchaseDelevery")
        token = str(
            request.get(
                "tradeData",
                request.get(
                    "trade_data",
                    getattr(message, "trade_data", "") if message is not None else "",
                ),
            )
        )
        if not token:
            # Native SDK payloads may put the local token in the receipt field.
            token = str(
                request.get(
                    "transactionReceipt",
                    request.get(
                        "transaction_receipt",
                        getattr(message, "transaction_receipt", "")
                        if message is not None
                        else "",
                    ),
                )
            )
        if not token:
            # Some local IAP adapters preserve the server trade token under an
            # order/transaction key while adding the platform receipt beside
            # it.  Only a value carrying our explicit prefix is accepted.
            for key in ("transactionId", "transaction_id", "orderId", "order_id", "purchaseToken", "purchase_token"):
                candidate = str(request.get(key, ""))
                if candidate.startswith("local-sandbox:"):
                    token = candidate
                    break
        state = self._state(account)
        pending = state["pending"].pop(token, None)
        if not isinstance(pending, Mapping) or not token.startswith("local-sandbox:"):
            return 402, {"error": "payment_verification_required", "message": "only a server-issued local sandbox token is accepted"}
        product = self._purchase_product(str(pending.get("productId", "")))
        if product is None:
            return 409, {"error": "pending_product_unavailable"}
        # One server-issued sandbox order is one fixed denomination.  The
        # client count field is a protocol detail and must never multiply
        # locally granted currency.
        quantity = 1
        profile = self.game._profile(self._account_for_game(account))
        ok, changes, error = self._change_resources(
            profile,
            [{"type": 5, "id": 0, "param": 0, "count": _int(product["count"]) * quantity}],
        )
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        transaction_id = token.removeprefix("local-sandbox:")
        state["purchases"][transaction_id] = {
            "productId": product["productId"],
            "count": quantity,
            "currency": "LOCAL_SANDBOX",
            "resourceChanges": changes,
            "completedAt": self._now(),
        }
        self._save_state(account, state)
        result = self._message("ApiPurchaseDelevery.result")
        result.transaction_id = transaction_id
        result.google_purchase_token = ""
        # Native protobuf deliveries carry channel in the parsed request; the
        # JSON seam uses the camel-case field.  Preserve either value in the
        # typed response so the client can finish its local IAP callback.
        result.channel = _int(
            request.get(
                "channel",
                getattr(message, "channel", 0) if message is not None else 0,
            )
        )
        return 200, result

    def _handle_http_unlocked(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = _route(path)
        method = method.upper()
        if route == "/fci/purchasetouch/":
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._purchase_touch(profile.get("account", profile), body)
        if route == "/fci/purchasedelevery/":
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._purchase_delivery(profile.get("account", profile), body)
        account = profile.get("account", profile)
        shop_result = self._shop_http(method, route, body, account)
        if shop_result is not None:
            return shop_result
        lower = route.lower()
        if lower == "/fci/resbuyprice/resbuy/":
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._res_buy(account, body)
        if lower.startswith("/fci/resbuyprice/"):
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            parts = [part for part in lower.split("/") if part]
            if len(parts) < 3 or not parts[2].isdigit():
                return 404, {"error": "unknown_price_type"}
            params = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 0
            return self._res_buy_price(account, int(parts[2]), params)
        if lower == "/fci/baseresource/update/":
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            return self._base_resource_update(account, profile)
        if route in {"/fci/mall-info/", "/fci/mall-info"}:
            account = profile.get("account", profile)
            state = self._state(account)
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            return 200, self._mall_info_message(account, state)
        if route in {"/fci/mall-trade/", "/fci/mall-trade"}:
            account = profile.get("account", profile)
            state = self._state(account)
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._mall_trade(account, body, state)
        # Recharge model compatibility routes.  These are deliberately JSON
        # sandbox endpoints because the original RPC descriptors are not part
        # of the extracted REST descriptor set.  The websocket layer also
        # returns an explicit unsupported fallback for the corresponding
        # ``base.cs_*`` calls; only these local routes grant test currency.
        if route.startswith("/fci/recharge") and not self.legacy_recharge_enabled:
            # Reject before opening or mutating the recharge profile. HTTP
            # egress maps 410 to the native notify shape, so the client does
            # not enter its reset-on-error path.
            return 410, {
                "error": "legacy_recharge_disabled",
                "message": "legacy recharge routes are disabled; use local sandbox purchase flow",
            }
        if route in {"/fci/recharge/", "/fci/recharge/info/", "/fci/recharge/info"}:
            account = profile.get("account", profile); state = self._state(account)
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            return self._recharge_info(account, state, self._request_value(body, "rechargeType", "recharge_type"))
        if route in {"/fci/recharge/before-buy-check/", "/fci/recharge/before-buy-check"}:
            return self._recharge_before_buy(body) if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/free/", "/fci/recharge/free"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_claim(account, state, body, "free") if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/voucher/", "/fci/recharge/voucher"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_claim(account, state, body, "voucher", one_time=True) if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/gift-box/", "/fci/recharge/gift-box"}:
            account = profile.get("account", profile); state = self._state(account)
            # A gift-box claim is a one-time grant for a specific local offer.
            # Keeping the claim marker in the recharge state prevents a retry
            # or reconnect from minting the same sandbox currency again.
            return self._recharge_claim(account, state, body, "gift-box", one_time=True) if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/first-reward/", "/fci/recharge/first-reward"}:
            account = profile.get("account", profile); state = self._state(account); info = self._recharge_state(state)
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            if info.get("firstReward"):
                return 409, {"error": "first_recharge_reward_already_claimed"}
            ok, changes, error = self._grant_paid_currency(account, 60)
            if not ok:
                return 409, {"error": error or "resource_update_failed"}
            info["firstReward"] = True; self._save_recharge_state(account, state, info)
            return 200, {"ok": True, "result": True, "code": 0, "sandbox": True, "rewardTbl": changes}
        if route in {"/fci/recharge/gift-reward/", "/fci/recharge/gift-reward"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_claim(account, state, body, "gift-reward", one_time=True) if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/month-card/reward/", "/fci/recharge/month-card/reward"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_month_card(account, state, body) if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/permit/buy-level/", "/fci/recharge/permit/buy-level"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_permit(account, state, body, "buy") if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/permit/task-reward/", "/fci/recharge/permit/task-reward"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_permit(account, state, body, "task") if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/permit/level-reward/", "/fci/recharge/permit/level-reward"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_permit(account, state, body, "level") if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        if route in {"/fci/recharge/platform-active-code/", "/fci/recharge/platform-active-code"}:
            account = profile.get("account", profile); state = self._state(account)
            return self._recharge_active_code(account, state, body) if method == "POST" else (405, {"error": "method_not_allowed", "message": "POST required"})
        return None


    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        """Serialize economy mutations per process/account store.

        The legacy handlers read state, mutate the game profile, and persist
        purchase markers across several helper calls.  Holding the module lock
        over the whole dispatch makes duplicate claims and purchases atomic for
        concurrent local HTTP requests.
        """
        with self._lock:
            return self._handle_http_unlocked(method, path, body, profile)



__all__ = ["LocalEconomy", "apply_energy_recovery", "energy_policy", "load_shop_catalog", "next_shop_refresh"]
