"""Local equipment drops for story/normal stages and equipment instances.

Provenance
----------
What the original client data proves:

* ``DungeonReward`` (client table, exported to
  ``data/client-tables/DungeonReward.json``): every story/normal stage 1-261
  (type 2) lists exactly one shared equipment drop group ``Dungeon_12_<n>``
  in both ``dropId`` and ``dropIdFirst``.  Stage 12 also lists its own per-run
  group ``Dungeon_12_7`` (the ``Dungeon_<stage>_7`` coin pattern); that token
  is excluded.  Groups: 1 = stages 1-8, 2 = 9-17, 3 = 18-29, 4 = 30-43,
  5 = 44-60, 6 = 61-81, 8 = 82-132, 9 = 133-162, 10 = 163-261 (7 is unused).
* ``EquipInfo`` (156 rows) tiers: requireLevel 1 = quality 1 (20000, 21001,
  22002, 23003, one per slot), 10 = q2 (12), 25 = q3 (66), 40 = q4 (74).
  Most q2-q4 rows name ``sources`` 52xxx, which ``Goto`` resolves to
  ``WeekActivityPrepare`` (the weekly equipment dungeons 2001-2003).
* ``EquipRandomAttr`` (keyed by ``EquipInfo.randAttrId``) has three weighted
  ranges, rangeMain/rangeSub/rangeSpecial (probability 1700/7800/500 of
  10000), each a list of ``{type, isRatio, min, max, segment}``.
* ``EquipEnhance.genNewRandAttr`` marks the levels that add a random
  attribute (level 1 for every quality); the client counts them in
  ``EquipUtil.GetEquipRandAttrCount`` and shows ``STEquip.randomAttrs``.
* Client reads: ``DungeonUtil.GetDungeonBalanceRewards`` and
  ``WindowUtil`` keep ``change.equip`` when ``countDelta > 0``;
  ``DataBindingHandler.UpdateEquips`` stores it at ``fci/equip/<uid>``;
  ``PushService.OnEquips`` loads ``DBGamePlayer.EquipPackage.equips`` at
  login.

What is a local rule (the contents of the ``Dungeon_12_<n>`` groups, drop
rates and random-attribute value steps were server-only):

* Group -> quality pool: groups 1-2 -> q1, 3-4 -> q2, 5-7 -> q3, 8+ -> q4;
  the item is picked uniformly from every EquipInfo row of that quality.
* Drop chance: 100% on a stage's first clear, 25% on a repeat clear.
* Random attribute: pick a range by its probability, an entry of that range
  uniformly, then one of ``segment`` evenly spaced values from min to max.
* Equipment uids come from a per-account counter in LocalSystems state
  (``equipUidSeq``) that starts above 1,000,000 and only ever increases, so
  a decomposed item's uid is never reused.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from . import actors as actor_store
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store

_CLIENT_TABLE_DIR = Path(__file__).resolve().parent / "data" / "client-tables"

REPEAT_DROP_RATE = 0.25
UID_BASE = 1_000_000
UID_STATE_KEY = "equipUidSeq"
_GROUP_TOKEN = re.compile(r"Dungeon_12_(\d+)")


def _rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_CLIENT_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_stage_groups() -> dict[int, int]:
    """Map dungeon id -> its ``Dungeon_12_<n>`` equipment group."""

    groups: dict[int, int] = {}
    for row in _rows("DungeonReward"):
        dungeon_id = _int(row.get("dungeonId"))
        if not dungeon_id:
            continue
        own_run_group = f"Dungeon_{dungeon_id}_7"
        found = set()
        for token in list(row.get("dropId") or []) + list(row.get("dropIdFirst") or []):
            token = str(token)
            match = _GROUP_TOKEN.fullmatch(token)
            if match and token != own_run_group:
                found.add(int(match.group(1)))
        if len(found) == 1:
            groups[dungeon_id] = found.pop()
    return groups


EQUIP_INFO: dict[int, dict[str, Any]] = {
    _int(row.get("equipId")): row for row in _rows("EquipInfo") if _int(row.get("equipId"))
}
EQUIP_POOLS: dict[int, list[int]] = {}
for _equip_id, _row in sorted(EQUIP_INFO.items()):
    EQUIP_POOLS.setdefault(_int(_row.get("quality")), []).append(_equip_id)
RANDOM_ATTRS: dict[int, dict[str, Any]] = {
    _int(row.get("id")): row for row in _rows("EquipRandomAttr") if _int(row.get("id"))
}
# (quality, level) pairs whose EquipEnhance row adds a random attribute.
RAND_ATTR_LEVELS: frozenset[tuple[int, int]] = frozenset(
    (_int(row.get("quality")), _int(row.get("level")))
    for row in _rows("EquipEnhance")
    if row.get("genNewRandAttr")
)
STAGE_EQUIP_GROUP: dict[int, int] = _load_stage_groups()
# EquipBaseAttr[(baseAttrId, level)] -> attrs (EA.GetBaseAttrMap).
BASE_ATTRS: dict[tuple[int, int], list[dict[str, Any]]] = {
    (_int(row.get("id")), _int(row.get("level"))): [attr for attr in row.get("attr") or [] if isinstance(attr, Mapping)]
    for row in _rows("EquipBaseAttr")
    if _int(row.get("id"))
}
# EquipSuitAttr by member equipId (EU.GetSuit).
SUITS: dict[int, dict[str, Any]] = {}
for _suit in _rows("EquipSuitAttr"):
    for _member in _suit.get("equipId") or []:
        SUITS[_int(_member)] = _suit
# AttrToScore ratio by attr type (EA.GetEquipScore).
ATTR_SCORE: dict[int, float] = {
    _int(row.get("type")): float(row.get("ratio") or 0) for row in _rows("AttrToScore") if _int(row.get("type"))
}
# Equipment plans (搭配): CostumeInfo:last().maxCount slots, matchIndex 1..N.
_COSTUME = _rows("CostumeInfo")
MAX_MATCH_COUNT = max([_int(row.get("maxCount")) for row in _COSTUME] + [0]) or 6
EQUIP_POSITIONS = (0, 1, 2, 3)  # EquipPos I-IV; EquipInfo.pos (absent = 0)


def group_quality(group: int) -> int:
    """Local rule: equipment quality dropped by ``Dungeon_12_<group>``."""

    if group <= 2:
        return 1
    if group <= 4:
        return 2
    if group <= 7:
        return 3
    return 4


def roll_stage_equipment(dungeon_id: int, first_finish: bool, rng: Optional[random.Random] = None) -> Optional[int]:
    """Return the equipId a won story/normal stage drops, or None."""

    group = STAGE_EQUIP_GROUP.get(_int(dungeon_id))
    if group is None:
        return None
    pool = EQUIP_POOLS.get(group_quality(group)) or []
    if not pool:
        return None
    rng = rng or random
    if not first_finish and rng.random() >= REPEAT_DROP_RATE:
        return None
    return int(rng.choice(pool))


def _roll_attr(table: Mapping[str, Any], rng: Any) -> Optional[dict[str, Any]]:
    ranges = [table.get("rangeMain"), table.get("rangeSub"), table.get("rangeSpecial")]
    ranges += list(table.get("rangeArray") or [])
    ranges = [
        item for item in ranges
        if isinstance(item, Mapping) and _int(item.get("probability")) > 0 and item.get("attrs")
    ]
    total = sum(_int(item.get("probability")) for item in ranges)
    if total <= 0:
        return None
    pick = rng.randrange(total)
    chosen = ranges[-1]
    for item in ranges:
        pick -= _int(item.get("probability"))
        if pick < 0:
            chosen = item
            break
    attr = rng.choice([value for value in chosen["attrs"] if isinstance(value, Mapping)])
    low, high = _int(attr.get("min")), _int(attr.get("max"))
    if high < low:
        low, high = high, low
    steps = _int(attr.get("segment"))
    if steps > 1:
        value = low + (high - low) * rng.randrange(steps) // (steps - 1)
    else:
        value = rng.randint(low, high)
    return {"type": _int(attr.get("type")), "value": value, "isRatio": bool(attr.get("isRatio", False))}


def roll_random_attrs(equip_id: int, level: int = 1, rng: Optional[random.Random] = None) -> list[dict[str, Any]]:
    """Roll the random attributes an item of ``level`` carries.

    One attribute per EquipEnhance level 1..``level`` with genNewRandAttr.
    Returns [] when the item or its EquipRandomAttr row is unknown.
    """

    return roll_level_attrs(equip_id, 1, max(1, _int(level, 1)), rng)


def roll_level_attrs(equip_id: int, from_level: int, to_level: int,
                     rng: Optional[random.Random] = None) -> list[dict[str, Any]]:
    """Roll one attribute per genNewRandAttr level in ``from_level..to_level``.

    Enhancing appends these to ``randomAttrs`` in level order; the levelup
    result window shows the last ``EU.GetEquipRandAttrCount(old+1, new)``
    entries as the new ones.
    """

    info = EQUIP_INFO.get(_int(equip_id))
    if info is None:
        return []
    table = RANDOM_ATTRS.get(_int(info.get("randAttrId")))
    if table is None:
        return []
    quality = _int(info.get("quality"))
    rng = rng or random
    result = []
    for each_level in range(max(1, _int(from_level, 1)), _int(to_level) + 1):
        if (quality, each_level) in RAND_ATTR_LEVELS:
            attr = _roll_attr(table, rng)
            if attr is not None:
                result.append(attr)
    return result


def _now(game: Any) -> int:
    clock = getattr(game, "_clock", None)
    try:
        return int(clock()) if callable(clock) else int(time.time())
    except (TypeError, ValueError):
        return int(time.time())


def _allocate_uid(state: dict[str, Any]) -> int:
    equips = state.setdefault("equips", {})
    highest = UID_BASE
    for key, record in equips.items():
        highest = max(highest, _int(key))
        if isinstance(record, Mapping):
            highest = max(highest, _int(record.get("uid")))
    uid = max(highest, _int(state.get(UID_STATE_KEY))) + 1
    state[UID_STATE_KEY] = uid
    return uid


def _random_attrs(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for attr in record.get("randomAttrs") or []:
        if isinstance(attr, Mapping):
            result.append({
                "type": _int(attr.get("type")),
                "value": _int(attr.get("value")),
                "isRatio": bool(attr.get("isRatio", attr.get("is_ratio", False))),
            })
    return result


def st_equip_json(record: Mapping[str, Any], count_delta: int = 0) -> dict[str, Any]:
    """Project a stored equipment row as camelCase ``STEquip`` JSON."""

    return {
        "uid": _int(record.get("uid", record.get("id"))),
        "id": _int(record.get("id", record.get("uid"))),
        "curExp": max(0, _int(record.get("exp"))),
        "level": max(1, _int(record.get("level"), 1)),
        "actorUid": max(0, _int(record.get("actorUid", record.get("actor_uid")))),
        "isLock": bool(record.get("locked", record.get("isLock", False))),
        "isNew": bool(record.get("isNew", False)),
        "countDelta": int(count_delta),
        "randomAttrs": _random_attrs(record),
        "randAttrIndex": [_int(value) for value in record.get("randAttrIndex") or []],
        "matchIndex": max(0, _int(record.get("matchIndex", record.get("match_index")))),
    }


def fill_st_equip(message: Any, record: Mapping[str, Any]) -> Any:
    """Fill an ``STEquip`` protobuf message from a stored equipment row."""

    value = st_equip_json(record)
    message.uid = value["uid"]
    message.id = value["id"]
    message.cur_exp = value["curExp"]
    message.level = value["level"]
    message.actor_uid = value["actorUid"]
    message.is_lock = value["isLock"]
    message.is_new = value["isNew"]
    message.match_index = value["matchIndex"]
    del message.random_attrs[:]
    for attr in value["randomAttrs"]:
        entry = message.random_attrs.add()
        entry.type = max(0, attr["type"])
        entry.value = attr["value"]
        entry.is_ratio = attr["isRatio"]
    del message.rand_attr_index[:]
    message.rand_attr_index.extend(max(0, index) for index in value["randAttrIndex"])
    return message


def grant_equipment(
    game: Any,
    profile: Mapping[str, Any],
    equip_id: int,
    *,
    state: Optional[dict[str, Any]] = None,
    rng: Optional[random.Random] = None,
) -> dict[str, Any]:
    """Create one equipment instance and return its resChange entry.

    The row is stored in LocalSystems ``state["equips"][str(uid)]`` with the
    same fields the systems.py equip routes use (id, uid, level, exp,
    locked, actorUid, plan) plus isNew/randomAttrs/gotTime.  When ``state``
    is given the caller owns it and must ``systems._save`` it; otherwise the
    state is loaded and saved here.
    """

    equip_id = _int(equip_id)
    if equip_id not in EQUIP_INFO:
        raise ValueError(f"unknown equipId {equip_id}")
    owns_state = state is None
    if owns_state:
        state = game.systems._state(profile)
    uid = _allocate_uid(state)
    record = {
        "id": equip_id,
        "uid": uid,
        "level": 1,
        "exp": 0,
        "locked": False,
        "actorUid": 0,
        "plan": 0,
        "isNew": True,
        "randomAttrs": roll_random_attrs(equip_id, 1, rng),
        "gotTime": _now(game),
    }
    state.setdefault("equips", {})[str(uid)] = record
    if owns_state:
        game.systems._save(profile, state)
    return {"equip": st_equip_json(record, count_delta=1)}


def fill_equip_package(
    game: Any,
    profile: Mapping[str, Any],
    message: Any,
    *,
    state: Optional[Mapping[str, Any]] = None,
) -> int:
    """Append stored equipment to a ``DBGamePlayer.EquipPackage`` message.

    Rows whose id is not an EquipInfo id (placeholders the generic equip
    routes create for unknown uids) are skipped, because the client's
    DBH.AppendEquipInfo needs the table row.  Returns the number added.
    """

    if state is None:
        try:
            state = game.systems._state(profile)
        except Exception:
            return 0
    equips = state.get("equips") if isinstance(state, Mapping) else None
    if not isinstance(equips, Mapping):
        return 0
    added = 0
    # Ownership as the STActor projections derive it (legacy actor ids and
    # removed characters normalised), so login equipment and actors agree.
    try:
        owners = effective_owners(profile, state) if isinstance(state, dict) else {}
    except Exception:
        owners = {}
    records = [record for record in equips.values() if isinstance(record, Mapping)]
    for record in sorted(records, key=lambda item: _int(item.get("uid", item.get("id")))):
        if not is_real(record):
            continue
        uid = _int(record.get("uid", record.get("id")))
        if uid in owners:
            record = dict(record)
            record["actorUid"] = owners[uid]
        fill_st_equip(message.equips.add(), record)
        added += 1
    return added



# ---------------------------------------------------------------------------
# Carrying equipment (藏品 tab) and equipment plans (搭配)
# ---------------------------------------------------------------------------
#
# Client contract (decompiled Lua, client 2.0.4):
#
# * Window_ActorInfo/WidgetActorEquip reads ``actor.bodyEquips[tostring(pos)]``
#   (STActor.body_equips: map<string pos, uint32 equipUid>), shows
#   ``REF.EquipEmpty`` ("尚未携带藏品") while it is empty, and renders
#   ``actor.equipAttrs`` beside ``actor.attrs`` (attrs already include the
#   equipment part: WidgetActorDetails shows ``attrs - equipAttrs`` + equip).
# * ``equip.actorUid`` marks the owner; DBH.UpdateActors/UpdateEquips replace
#   ``fci/actor/<uid>`` / ``fci/equip/<uid>`` wholesale with each reply record.
# * Plans: ``Equip/Match/<index>`` = DBGamePlayer.EquipCustomMatch
#   {matchName, matchEquipUids: map<uint32 pos, uint32 uid>} (WidgetEquipMatchSlot
#   reads ``matchEquipUids[pos]``); an item belongs to at most one plan and
#   carries it as ``STEquip.matchIndex`` (bag/levelup exclude matchIndex ~= 0).
#
# Ownership is stored once, on the equipment row (``actorUid`` = the owner's
# character uid; 0 = in the bag).  Body equipment is derived from it, so every
# STActor projection (equip replies, actor upgrades, login bootstrap) agrees.

def is_real(record: Any) -> bool:
    """A stored row the client can show: an EquipInfo id and a positive uid."""

    return (isinstance(record, Mapping) and _int(record.get("id")) in EQUIP_INFO
            and _int(record.get("uid", record.get("id"))) > 0)


def equip_pos(equip_id: Any) -> Optional[int]:
    info = EQUIP_INFO.get(_int(equip_id))
    return None if info is None else _int(info.get("pos"))


def require_level(equip_id: Any) -> int:
    info = EQUIP_INFO.get(_int(equip_id))
    return 0 if info is None else _int(info.get("requireLevel"))


def base_attrs(equip_id: Any, level: Any = 1) -> list[dict[str, Any]]:
    """EquipBaseAttr attrs of an item at ``level`` (EA.GetBaseAttrMapByEquipId)."""

    info = EQUIP_INFO.get(_int(equip_id))
    if info is None or not _int(info.get("baseAttrId")):
        return []
    rows = BASE_ATTRS.get((_int(info.get("baseAttrId")), max(1, _int(level, 1)))) or []
    return [{"type": _int(attr.get("type")), "value": _int(attr.get("value")),
             "isRatio": bool(attr.get("isRatio", False))} for attr in rows]


def equip_score(record: Mapping[str, Any]) -> float:
    """EA.GetEquipScore: AttrToScore-weighted base + random attributes."""

    total = 0.0
    attrs = base_attrs(record.get("id"), record.get("level", 1)) + _random_attrs(record)
    for attr in attrs:
        total += ATTR_SCORE.get(attr["type"], 0.0) * attr["value"]
    return total


def carry_sort_key(record: Mapping[str, Any]) -> tuple:
    """WidgetActorEquip candidate order: quality, level, score, uid (all desc)."""

    info = EQUIP_INFO.get(_int(record.get("id")), {})
    return (-_int(info.get("quality")), -max(1, _int(record.get("level"), 1)),
            -equip_score(record), -_int(record.get("uid")))


def owner_uid(profile: Mapping[str, Any], state: dict[str, Any], ref: Any) -> int:
    """Normalise a stored ``actorUid`` to an owned character uid (0 = none).

    Rows written by older routes may hold a legacy actor id (>= 1000, e.g.
    ActorInheriting stores the reference the client sent); those resolve to
    the first copy.  A reference to a character that no longer exists is 0.
    """

    ref = _int(ref)
    if ref <= 0:
        return 0
    record = actor_store.resolve(profile, state, ref)
    return _int(record.get("uid")) if record is not None else 0


def prune_placeholders(state: dict[str, Any]) -> list[str]:
    """Drop rows whose id is not an EquipInfo id.

    Old generic routes created such rows for unknown uids (for example uid
    20001 from a POST fci/equip/on with no ``id``); the client can neither
    show nor carry them.  Returns the removed keys.
    """

    equips = state.get("equips")
    if not isinstance(equips, dict):
        return []
    removed = [key for key, record in equips.items() if not is_real(record)]
    for key in removed:
        equips.pop(key, None)
    return removed


def effective_owners(profile: Mapping[str, Any], state: dict[str, Any]) -> dict[int, int]:
    """equipUid -> carrying character uid for every real row (0 = bag).

    A character carries at most one item per pos; if stored rows ever claim
    the same slot twice, the lowest uid wins and the others count as bagged.
    """

    equips = state.get("equips")
    result: dict[int, int] = {}
    if not isinstance(equips, Mapping):
        return result
    taken: set[tuple[int, int]] = set()
    for record in sorted((r for r in equips.values() if is_real(r)), key=lambda r: _int(r.get("uid"))):
        uid = _int(record.get("uid"))
        owner = owner_uid(profile, state, record.get("actorUid"))
        slot = (owner, equip_pos(record.get("id")) or 0)
        if owner and slot in taken:
            owner = 0
        if owner:
            taken.add(slot)
        result[uid] = owner
    return result


def worn_equips(profile: Mapping[str, Any], state: dict[str, Any], actor_uid: Any) -> dict[int, dict[str, Any]]:
    """pos -> stored row of everything ``actor_uid`` carries (one per pos)."""

    actor_uid = _int(actor_uid)
    result: dict[int, dict[str, Any]] = {}
    equips = state.get("equips")
    if actor_uid <= 0 or not isinstance(equips, Mapping):
        return result
    for uid, owner in effective_owners(profile, state).items():
        if owner == actor_uid:
            record = equips.get(str(uid))
            if is_real(record):
                result[equip_pos(record.get("id")) or 0] = record
    return result


def body_equips_for(profile: Mapping[str, Any], state: dict[str, Any], actor_uid: Any) -> dict[str, int]:
    """STActor.bodyEquips for ``actor_uid``: ``{str(pos): equipUid}``."""

    return {str(pos): _int(record.get("uid")) for pos, record in sorted(worn_equips(profile, state, actor_uid).items())}


def equip_attrs_for(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Summed equipment attributes (base at item level + random), per type."""

    totals: dict[int, dict[str, Any]] = {}
    for record in records:
        for attr in base_attrs(record.get("id"), record.get("level", 1)) + _random_attrs(record):
            if attr["type"] <= 0:
                continue
            entry = totals.setdefault(attr["type"], {"type": attr["type"], "value": 0, "isRatio": attr["isRatio"]})
            entry["value"] += attr["value"]
    return [totals[key] for key in sorted(totals)]


def special_attr_ids_for(records: list[Mapping[str, Any]]) -> list[int]:
    """Equipment special attributes.

    EquipInfo.specialAttrId of each item plus the highest activated tier of
    every suit (EA.GetSuitInfo keeps only the highest activated ``suitAttr``;
    a tier is activated when the distinct suit pieces worn >= suitCount).
    """

    result: list[int] = []
    worn_ids = {_int(record.get("id")) for record in records}
    for equip_id in sorted(worn_ids):
        special = _int(EQUIP_INFO.get(equip_id, {}).get("specialAttrId"))
        if special and special not in result:
            result.append(special)
    seen: set[int] = set()
    for equip_id in sorted(worn_ids):
        suit = SUITS.get(equip_id)
        if suit is None or _int(suit.get("id")) in seen:
            continue
        seen.add(_int(suit.get("id")))
        count = len(worn_ids & {_int(value) for value in suit.get("equipId") or []})
        best = None
        for tier in suit.get("suitAttr") or []:
            if isinstance(tier, Mapping) and _int(tier.get("suitCount")) and count >= _int(tier.get("suitCount")):
                if best is None or _int(tier.get("suitCount")) >= _int(best.get("suitCount")):
                    best = tier
        if best is not None and _int(best.get("specialAttrId")) and _int(best.get("specialAttrId")) not in result:
            result.append(_int(best.get("specialAttrId")))
    return result


def apply_worn_equipment(actor: Any, profile: Mapping[str, Any], state: dict[str, Any]) -> Any:
    """Write the character's carried equipment into an ``STActor`` message.

    Sets ``body_equips``, ``equip_attrs`` and ``special_attr_ids`` and adds the
    equipment attributes onto ``attrs`` per type (the client treats ``attrs``
    as the total and ``equipAttrs`` as the equipment share).  Idempotent: an
    existing ``equip_attrs`` share is subtracted first, so calling it again on
    the same message does not double count.  Uses ``actor.uid``.
    """

    by_type = {attr.type: attr for attr in actor.attrs}
    for old in actor.equip_attrs:
        if old.type in by_type:
            by_type[old.type].value -= old.value
    del actor.equip_attrs[:]
    actor.body_equips.clear()
    del actor.special_attr_ids[:]
    worn = worn_equips(profile, state, _int(actor.uid))
    for pos, record in sorted(worn.items()):
        actor.body_equips[str(pos)] = _int(record.get("uid"))
    records = [worn[pos] for pos in sorted(worn)]
    for attr in equip_attrs_for(records):
        entry = actor.equip_attrs.add()
        entry.type = attr["type"]
        entry.value = attr["value"]
        entry.is_ratio = attr["isRatio"]
        target = by_type.get(attr["type"])
        if target is None:
            target = actor.attrs.add()
            target.type = attr["type"]
            target.value = 0
            target.is_ratio = attr["isRatio"]
            by_type[attr["type"]] = target
        target.value += attr["value"]
    actor.special_attr_ids.extend(special_attr_ids_for(records))
    return actor


# -- plans --------------------------------------------------------------------

def _plan_map(raw: Any, equips: Mapping[str, Any]) -> dict[int, int]:
    """Stored plan equipment as ``{pos: uid}`` (accepts the old uid list)."""

    selected = raw.get("equipUids", raw.get("matchEquipUids", {})) if isinstance(raw, Mapping) else {}
    result: dict[int, int] = {}
    if isinstance(selected, Mapping):
        for key, uid in selected.items():
            if str(key).isdigit() and _int(uid) > 0:
                result[_int(key)] = _int(uid)
    elif isinstance(selected, list):
        for uid in selected:
            record = equips.get(str(_int(uid))) if _int(uid) > 0 else None
            pos = equip_pos(record.get("id")) if is_real(record) else None
            if pos is not None and pos not in result:
                result[pos] = _int(uid)
    return result


def plans(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Normalised plans ``{index: {"matchName": str, "equipUids": {pos: uid}}}``.

    Entries that no longer point at a real stored item in its slot are dropped.
    """

    equips = state.get("equips") if isinstance(state.get("equips"), Mapping) else {}
    raw_plans = state.get("equipPlans") if isinstance(state.get("equipPlans"), Mapping) else {}
    result: dict[int, dict[str, Any]] = {}
    for key, raw in raw_plans.items():
        index = _int(key)
        if not 1 <= index <= MAX_MATCH_COUNT:
            continue
        name = str(raw.get("matchName", "")) if isinstance(raw, Mapping) else ""
        mapping = {pos: uid for pos, uid in _plan_map(raw, equips).items()
                   if is_real(equips.get(str(uid))) and equip_pos(equips[str(uid)].get("id")) == pos}
        result[index] = {"matchName": name, "equipUids": mapping}
    return result


def store_plans(state: dict[str, Any], value: Mapping[int, Mapping[str, Any]]) -> None:
    state["equipPlans"] = {
        str(index): {"matchName": str(plan.get("matchName", ""))[:32],
                     "equipUids": {str(pos): _int(uid) for pos, uid in sorted(plan.get("equipUids", {}).items())}}
        for index, plan in sorted(value.items())
    }


def sync_match_indexes(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Set every row's ``matchIndex`` from the plans; return the changed rows."""

    owner: dict[int, int] = {}
    for index, plan in sorted(plans(state).items()):
        for uid in plan["equipUids"].values():
            owner.setdefault(uid, index)
    changed = []
    equips = state.get("equips") if isinstance(state.get("equips"), Mapping) else {}
    for record in equips.values():
        if not isinstance(record, dict):
            continue
        value = owner.get(_int(record.get("uid")), 0)
        if _int(record.get("matchIndex")) != value:
            record["matchIndex"] = value
            changed.append(record)
    return changed


def save_plan(state: dict[str, Any], index: int, equip_uids: list[Any], name: Optional[str] = None) -> list[dict[str, Any]]:
    """Save carried items as plan ``index`` (EquipMatch.SaveEquipMatchImpl).

    Items move out of any other plan (one plan per item).  Returns the rows
    whose ``matchIndex`` changed.
    """

    equips = state.get("equips") if isinstance(state.get("equips"), Mapping) else {}
    mapping: dict[int, int] = {}
    for uid in equip_uids:
        record = equips.get(str(_int(uid)))
        if not is_real(record):
            continue
        pos = equip_pos(record.get("id"))
        if pos is not None and pos not in mapping:
            mapping[pos] = _int(uid)
    current = plans(state)
    chosen = set(mapping.values())
    for other_index, plan in current.items():
        if other_index != index:
            plan["equipUids"] = {pos: uid for pos, uid in plan["equipUids"].items() if uid not in chosen}
    old_name = current.get(index, {}).get("matchName", "")
    current[index] = {"matchName": old_name if name is None else str(name)[:32], "equipUids": mapping}
    store_plans(state, current)
    return sync_match_indexes(state)


def remove_from_plans(state: dict[str, Any], equip_uid: Any) -> list[dict[str, Any]]:
    """Take one item out of every plan (EquipDetail lock: "remove from match")."""

    current = plans(state)
    for plan in current.values():
        plan["equipUids"] = {pos: uid for pos, uid in plan["equipUids"].items() if uid != _int(equip_uid)}
    store_plans(state, current)
    return sync_match_indexes(state)


def fill_custom_match(message_map: Any, value: Mapping[int, Mapping[str, Any]]) -> None:
    """Fill a ``map<uint32, DBGamePlayer.EquipCustomMatch>`` field."""

    for index, plan in sorted(value.items()):
        entry = message_map[int(index)]
        entry.match_name = str(plan.get("matchName", ""))
        entry.match_equip_uids.clear()
        for pos, uid in sorted(plan.get("equipUids", {}).items()):
            entry.match_equip_uids[int(pos)] = int(uid)


# ---------------------------------------------------------------------------
# Enhance (藏品强化), decompose (分解) and NEW flags
# ---------------------------------------------------------------------------
#
# Client contract (decompiled Lua, client 2.0.4):
#
# * Window_ActorEquipLevelup posts ``fci/equip/<uid>/enhance/ {foodRes}``;
#   each food is ``{type=11 ResItem, id, count}`` (ItemFuncType.EquipExp,
#   EXP = ItemInfo.param[1] per item) or ``{type=12 ResEquip, id, count=0,
#   param=<equip uid>}`` (EXP = EU.DecomposedExp).  Candidates are bagged,
#   unlocked, outside any plan and not the target itself
#   (Window_ActorEquipLevelupMaterial).  The callback runs DBH.ResChange on
#   ``result.resChange`` and finds ``change.equip`` with the target uid.
# * EU.TryEnhanceEquip: levels follow EquipEnhance[quality, level].levelUpExp
#   up to EU.GetEquipLevelLimit = min(PlayerLevelInitConfig.equipLevelLimit,
#   last EquipEnhance level of the quality); EXP beyond the bar of the final
#   level is overflow.
# * Gold (Misc.equipExpToResource, 3 gold per EXP): fed EXP costs gold except
#   the already-paid enhance EXP of fodder equipment; overflow of that EXP is
#   refunded (the window shows NodeReturn when the cost is negative).
# * Overflow EXP that came from equipment is returned as EquipExp items
#   (AU.DecomposeToExpItems: largest item first, floor at each step; the
#   material window previews exactly these).
# * Window_Bag posts ``fci/equip/decompose/ {equipUids}`` for bagged,
#   unlocked, plan-free items; each yields DecomposeToExpItems(DecomposedExp)
#   plus EU.DecomposedRes (3 gold per enhance EXP kept after
#   EquipEnhance.enhanceLostRate).  The reply's ``equip`` entries carry
#   countDelta -1 so DBH.UpdateEquips drops ``fci/equip/<uid>``.
# * Common_SortUtil.ClearNewFlag patches ``fci/new-flag/`` with
#   ApiResourceNewFlag {resInfo:[{idOrUid, type}], isNew=false}: type 15 =
#   character uid (isNewCard), 12 = equipment uid, 11 = item id, 8 = medal id.

RES_GOLD = 7
RES_MEDAL = 8
RES_ITEM = 11
RES_EQUIP = 12
RES_ACTOR = 15
# Misc[1].equipExpToResource (xlsx__misc.lua:5).
EXP_TO_RESOURCE: dict[str, int] = {"type": RES_GOLD, "id": 0, "count": 3}
ENHANCE: dict[tuple[int, int], dict[str, Any]] = {
    (_int(row.get("quality")), _int(row.get("level"))): row
    for row in _rows("EquipEnhance") if _int(row.get("level"))
}
MAX_LEVEL: dict[int, int] = {}
for (_quality, _level) in ENHANCE:
    MAX_LEVEL[_quality] = max(MAX_LEVEL.get(_quality, 0), _level)
# ItemInfo rows with funcType ItemFuncType.EquipExp (2): id -> EXP per item.
EQUIP_EXP_ITEMS: dict[int, int] = {
    _int(row.get("id")): _int((row.get("param") or [0])[0])
    for row in _rows("ItemInfo")
    if _int(row.get("id")) and _int(row.get("funcType")) == 2 and row.get("param")
}
# PlayerLevelInitConfig: player level -> equipLevelLimit.
PLAYER_EQUIP_LEVEL_LIMIT: dict[int, int] = {
    _int(row.get("level")): _int(row.get("equipLevelLimit"))
    for row in _rows("PlayerLevelInitConfig") if _int(row.get("level"))
}


def quality_of(equip_id: Any) -> int:
    info = EQUIP_INFO.get(_int(equip_id))
    return 0 if info is None else _int(info.get("quality"))


def level_up_exp(quality: int, level: int) -> int:
    return _int((ENHANCE.get((quality, level)) or {}).get("levelUpExp"))


def max_level(equip_id: Any) -> int:
    """EU.GetEquipMaxLevel: the last EquipEnhance level of the item's quality."""

    return MAX_LEVEL.get(quality_of(equip_id), 0)


def level_limit(equip_id: Any, player_level: Any) -> int:
    """EU.GetEquipLevelLimit: min(equipLevelLimit of the player level, max level)."""

    cap = max_level(equip_id)
    limit = PLAYER_EQUIP_LEVEL_LIMIT.get(_int(player_level, 1))
    return cap if limit is None else min(limit, cap)


def equip_exp(record: Mapping[str, Any]) -> tuple[int, int]:
    """EU.GetEquipExp: (EquipInfo.baseExp, EXP invested by enhancing)."""

    info = EQUIP_INFO.get(_int(record.get("id")))
    enhance = max(0, _int(record.get("exp")))
    if info is None:
        return 0, enhance
    quality = _int(info.get("quality"))
    for level in range(1, max(1, _int(record.get("level"), 1))):
        enhance += level_up_exp(quality, level)
    return _int(info.get("baseExp")), enhance


def decomposed_exp(record: Mapping[str, Any]) -> tuple[int, int]:
    """EU.DecomposedExp: (total EXP it is worth, enhance EXP kept after loss)."""

    info = EQUIP_INFO.get(_int(record.get("id")))
    if info is None:
        return 0, 0
    base, enhance = equip_exp(record)
    row = ENHANCE.get((_int(info.get("quality")), max(1, _int(record.get("level"), 1)))) or {}
    kept = int(math.floor(enhance * (1 - _int(row.get("enhanceLostRate")) / 10000)))
    return kept + base, kept


def decompose_to_exp_items(exp: int) -> list[tuple[int, int]]:
    """AU.DecomposeToExpItems(exp, EquipExp): [(item id, count)], largest first."""

    result = []
    remaining = max(0, _int(exp))
    for item_id, value in sorted(EQUIP_EXP_ITEMS.items(), key=lambda kv: (-kv[1], kv[0])):
        if value <= 0:
            continue
        count, remaining = divmod(remaining, value)
        if count:
            result.append((item_id, count))
    return result


def try_enhance(record: Mapping[str, Any], exp_add: int, limit: int) -> dict[str, int]:
    """EU.TryEnhanceEquip with an explicit level limit."""

    quality = quality_of(record.get("id"))
    target = max(1, _int(record.get("level"), 1))
    exp_left = max(0, _int(record.get("exp"))) + exp_add
    while limit > target:
        need = level_up_exp(quality, target)
        if exp_left >= need and limit >= target + 1:
            exp_left -= need
            target += 1
        else:
            break
    to_next = level_up_exp(quality, target)
    overflow = max(exp_left - to_next, 0)
    return {"targetLevel": target, "expToNextLevel": to_next, "expUsed": exp_add - overflow,
            "expLeft": exp_left, "expOverflow": overflow}


def food_blocker(profile: Mapping[str, Any], state: dict[str, Any], record: Any) -> Optional[str]:
    """Why an item may not be eaten/decomposed (None = allowed).

    Window_Bag / Window_ActorEquipLevelupMaterial only offer items with
    ``actorUid == 0 and not isLock and matchIndex == 0``.
    """

    if not isinstance(record, Mapping):
        return "equip_not_found"
    if record.get("locked", record.get("isLock", False)):
        return "equip_locked"
    if not is_real(record):
        return "equip_not_found"
    if owner_uid(profile, state, record.get("actorUid")):
        return "equip_worn"
    uid = _int(record.get("uid"))
    if any(uid in plan["equipUids"].values() for plan in plans(state).values()):
        return "equip_in_plan"
    return None


def plan_enhance(record: Mapping[str, Any], item_food: Mapping[int, int],
                 equip_food: list[Mapping[str, Any]], player_level: Any) -> dict[str, Any]:
    """What feeding ``item_food`` ({item id: count}) and ``equip_food`` does.

    Mirrors the client preview: targetLevel/curExp of the item, ``gold``
    (positive = cost, negative = refund) and ``returnItems`` [(id, count)].
    """

    item_exp = sum(EQUIP_EXP_ITEMS.get(_int(item_id), 0) * _int(count) for item_id, count in item_food.items())
    selected = item_exp
    kept_total = 0
    for food in equip_food:
        total, kept = decomposed_exp(food)
        selected += total
        kept_total += kept
    limit = level_limit(record.get("id"), player_level)
    info = try_enhance(record, selected, limit)
    used_with_cost = min(info["expUsed"], selected - kept_total)
    overflow_with_loss = min(info["expOverflow"], kept_total) if info["expOverflow"] > 0 else 0
    exp_return = min(selected - item_exp, info["expOverflow"])
    return {
        "exp": selected,
        "limit": limit,
        "targetLevel": info["targetLevel"],
        "curExp": info["expLeft"] - info["expOverflow"],
        "gold": (used_with_cost - overflow_with_loss) * EXP_TO_RESOURCE["count"],
        "returnItems": decompose_to_exp_items(exp_return) if exp_return > 0 else [],
    }


def decompose_yield(records: list[Mapping[str, Any]]) -> tuple[dict[int, int], int]:
    """Window_Bag decompose preview: ({exp item id: count}, gold)."""

    items: dict[int, int] = {}
    gold = 0
    for record in records:
        total, kept = decomposed_exp(record)
        for item_id, count in decompose_to_exp_items(total):
            items[item_id] = items.get(item_id, 0) + count
        if kept > 0:
            gold += EXP_TO_RESOURCE["count"] * kept
    return items, gold


def add_items(state: dict[str, Any], counts: Mapping[int, int], now: int) -> list[dict[str, int]]:
    """Add bag items in ``state`` and return their baseRes deltas (absolute count)."""

    items = state.setdefault("items", {})
    changes = []
    for item_id, count in counts.items():
        if _int(count) <= 0:
            continue
        entry = items.setdefault(str(_int(item_id)), {"id": _int(item_id), "count": 0})
        entry["count"] = _int(entry.get("count")) + _int(count)
        entry["gotTime"] = int(now)
        entry["isNew"] = True
        changes.append({"type": RES_ITEM, "id": _int(item_id), "param": 0,
                        "count": entry["count"], "countDelta": _int(count)})
    return changes


def actor_is_new(record: Optional[Mapping[str, Any]]) -> bool:
    """STActor.is_new / is_new_card for a stored character instance.

    Characters are NEW until the client clears the badge through
    ``PATCH fci/new-flag`` (type 15); rows saved before the flag existed
    count as new, as the login bootstrap always reported.
    """

    return bool(record.get("isNew", True)) if isinstance(record, Mapping) else True


def set_new_flags(profile: Mapping[str, Any], state: dict[str, Any],
                  res_info: list[Mapping[str, Any]], is_new: bool = False) -> int:
    """Persist ApiResourceNewFlag entries; return how many records changed."""

    stores = {
        RES_EQUIP: state.get("equips"),
        RES_ITEM: state.get("items"),
        RES_MEDAL: state.get("medals"),
    }
    changed = 0
    for entry in res_info:
        rtype, ref = _int(entry.get("type")), _int(entry.get("idOrUid", entry.get("id_or_uid")))
        if ref <= 0:
            continue
        if rtype == RES_ACTOR:
            record = actor_store.get(profile, state, ref)
        else:
            store = stores.get(rtype)
            record = store.get(str(ref)) if isinstance(store, Mapping) else None
        if isinstance(record, dict):
            record["isNew"] = bool(is_new)
            changed += 1
    return changed
