"""Owned characters as uid-keyed instances, and their client-table stats.

Instances
  The client addresses every character by uid (``fci/actor/<uid>``, group
  ``actorUid``) and allows several copies of the same character, so the
  canonical store is ``LocalSystems state["actorInstances"]`` =
  ``{str(uid): {uid, id, level, star, exp, quality, locked, skin, skills,
  favourLevel, favourExp}}`` with ``state["nextActorUid"]``.  Older saves kept
  one record per actor *id* in ``state["actors"]`` and derived uid = position
  + 1 in ``profile.actor_ids + state["actors"]`` order; :func:`instances`
  migrates them with exactly those uids so saved groups keep pointing at the
  same characters.  uids are never reused.

Stats and skills (client formulas, Common_ActorUtil)
  attrs = ActorInfo[id, quality].attrs
        + floor(ActorAttrLevelPromotion[id] * (level-1)/(maxLevel-1) * qualityAttrRatio)
        + floor(sum over stars <= star of ActorAttrStarPromotion[id, s] * qualityAttrRatio)
  skills = the character's non-passive ActorSkill rows, minus skills a higher
  star still has to unlock (ActorAttrStarPromotion.unlockSkillId); the fight
  reads skill levels and special talents from this list (Logic_InitGameLogic).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

MIN_ACTOR_ID = 1000
MAX_ACTOR_LEVEL = 60  # Misc.maxActorLevel
_TABLES = Path(__file__).resolve().parent / "data" / "client-tables"


def _rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_TABLES / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [row for row in payload.get("rows", []) if isinstance(row, dict)]


ACTOR_INFO: dict[tuple[int, int], dict[str, Any]] = {
    (int(row["id"]), int(row.get("quality", 1) or 1)): row for row in _rows("ActorInfo") if row.get("id")
}
BASE_QUALITY: dict[int, int] = {}
for _actor_id, _quality in ACTOR_INFO:
    BASE_QUALITY[_actor_id] = min(_quality, BASE_QUALITY.get(_actor_id, _quality))
LEVEL_PROMOTION: dict[int, list[dict[str, Any]]] = {
    int(row["actorId"]): list(row.get("attrs") or []) for row in _rows("ActorAttrLevelPromotion") if row.get("actorId")
}
STAR_PROMOTION: dict[tuple[int, int], dict[str, Any]] = {
    (int(row["actorId"]), int(row.get("star", 0) or 0)): row for row in _rows("ActorAttrStarPromotion") if row.get("actorId")
}
SKILLS: dict[int, list[int]] = {}
for _row in _rows("ActorSkill"):
    if _row.get("actorId") and _row.get("skillId") and not _row.get("isPassive"):
        SKILLS.setdefault(int(_row["actorId"]), []).append(int(_row["skillId"]))
for _list in SKILLS.values():
    _list.sort()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def base_quality(actor_id: int) -> int:
    return BASE_QUALITY.get(int(actor_id), 1)


def compute_attrs(actor_id: int, quality: int, level: int, star: int) -> list[dict[str, Any]]:
    """STAttr list (``{type, value, isRatio}``) for one character."""

    actor_id, level, star = int(actor_id), max(1, int(level)), max(1, int(star))
    info = ACTOR_INFO.get((actor_id, int(quality))) or ACTOR_INFO.get((actor_id, base_quality(actor_id)))
    if info is None:
        return []
    ratio = float(info.get("qualityAttrRatio", 1.0) or 1.0)
    order: list[int] = []
    values: dict[int, float] = {}
    ratios: dict[int, bool] = {}

    def add(attr: Mapping[str, Any], value: float) -> None:
        kind = _int(attr.get("type"))
        if kind <= 0:
            return
        if kind not in values:
            order.append(kind)
            values[kind] = 0
            ratios[kind] = bool(attr.get("isRatio", False))
        values[kind] += value

    for attr in info.get("attrs") or []:
        add(attr, _int(attr.get("value")))
    factor = (level - 1) / (MAX_ACTOR_LEVEL - 1)
    for attr in LEVEL_PROMOTION.get(actor_id, []):
        add(attr, math.floor(_int(attr.get("value")) * factor * ratio))
    star_totals: dict[int, float] = {}
    star_attrs: dict[int, Mapping[str, Any]] = {}
    for current in range(1, star + 1):
        for attr in (STAR_PROMOTION.get((actor_id, current)) or {}).get("attrs") or []:
            kind = _int(attr.get("type"))
            star_totals[kind] = star_totals.get(kind, 0) + _int(attr.get("value")) * ratio
            star_attrs[kind] = attr
    for kind, total in star_totals.items():
        add(star_attrs[kind], math.floor(total))
    return [{"type": kind, "value": int(values[kind]), "isRatio": ratios[kind]} for kind in order]


def skill_ids(actor_id: int, star: int = 1) -> list[int]:
    actor_id = int(actor_id)
    locked = {
        _int(row.get("unlockSkillId"))
        for (owner, row_star), row in STAR_PROMOTION.items()
        if owner == actor_id and row_star > int(star) and row.get("unlockSkillId")
    }
    return [skill for skill in SKILLS.get(actor_id, []) if skill not in locked]


def new_record(actor_id: int, uid: int) -> dict[str, Any]:
    return {"uid": int(uid), "id": int(actor_id), "level": 1, "star": 1, "exp": 0,
            "quality": base_quality(actor_id), "locked": False, "skin": 0, "skills": {},
            "favourLevel": 1, "favourExp": 0}


def instances(profile: Mapping[str, Any], state: MutableMapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The uid-keyed instance store, migrating an id-keyed save in place."""

    store = state.get("actorInstances")
    if isinstance(store, dict) and store:
        return store
    ordered_ids: list[int] = []
    raw = profile.get("actor_ids") or ([profile["actor_id"]] if profile.get("actor_id") is not None else [])
    for value in raw:
        if _int(value) >= MIN_ACTOR_ID and _int(value) not in ordered_ids:
            ordered_ids.append(_int(value))
    legacy = state.get("actors") if isinstance(state.get("actors"), Mapping) else {}
    for key in legacy:
        if str(key).isdigit() and int(key) >= MIN_ACTOR_ID and int(key) not in ordered_ids:
            ordered_ids.append(int(key))
    store = {}
    for position, actor_id in enumerate(ordered_ids, start=1):
        record = new_record(actor_id, position)
        old = legacy.get(str(actor_id))
        if isinstance(old, Mapping):
            for key in ("level", "star", "exp", "quality", "locked", "skin", "skills", "favourLevel", "favourExp"):
                if key in old:
                    record[key] = old[key]
        store[str(position)] = record
    state["actorInstances"] = store
    state["nextActorUid"] = max([int(key) for key in store] + [0]) + 1
    return store


def get(profile: Mapping[str, Any], state: MutableMapping[str, Any], uid: Any) -> Optional[dict[str, Any]]:
    return instances(profile, state).get(str(_int(uid)))


def first_uid(profile: Mapping[str, Any], state: MutableMapping[str, Any], actor_id: Any) -> Optional[int]:
    actor_id = _int(actor_id)
    store = instances(profile, state)
    for key in sorted(store, key=int):
        if _int(store[key].get("id")) == actor_id:
            return int(key)
    return None


def resolve(profile: Mapping[str, Any], state: MutableMapping[str, Any], ref: Any) -> Optional[dict[str, Any]]:
    """A client reference -> instance: a uid (< 1000) or a legacy actor id."""

    ref = _int(ref)
    if ref >= MIN_ACTOR_ID:
        uid = first_uid(profile, state, ref)
        return get(profile, state, uid) if uid else None
    return get(profile, state, ref) if ref > 0 else None


def add(profile: Mapping[str, Any], state: MutableMapping[str, Any], actor_id: int) -> dict[str, Any]:
    store = instances(profile, state)
    uid = max(_int(state.get("nextActorUid")), max([int(key) for key in store] + [0]) + 1)
    record = new_record(actor_id, uid)
    store[str(uid)] = record
    state["nextActorUid"] = uid + 1
    return record


def remove(profile: Mapping[str, Any], state: MutableMapping[str, Any], uid: Any) -> Optional[dict[str, Any]]:
    return instances(profile, state).pop(str(_int(uid)), None)


def ordered(profile: Mapping[str, Any], state: MutableMapping[str, Any]) -> list[dict[str, Any]]:
    store = instances(profile, state)
    return [store[key] for key in sorted(store, key=int)]


__all__ = [
    "MIN_ACTOR_ID", "MAX_ACTOR_LEVEL", "base_quality", "compute_attrs", "skill_ids", "new_record",
    "instances", "get", "first_uid", "resolve", "add", "remove", "ordered",
]
