"""One place to grant client-table rewards (STResource lists) to a profile.

Rewards in the original tables are STResource rows ``{type, id, count, param}``
and each type lives in a different local store:
  - type 11 (ResItem)  -> LocalSystems state["items"] (bag + login ItemPackage)
  - type 15 (ResActor) -> a new character instance (actors.py; copies are
                          allowed), STActor in the reply
  - everything else    -> the profile resource ledger (economy._change_resources)
Granting items through the economy ledger would leave them invisible in the
bag, which is why every reward path should use :func:`grant_rewards`.

The returned list is DungeonBalance/Task-style ``resChange`` entries
(``{"baseRes": {...}}`` or ``{"actor": {...}}``) ready for a JSON reply.
"""
from __future__ import annotations

import time
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from . import actors as actor_store
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store

RES_ITEM = 11
RES_ACTOR = 15
RES_MEDAL = 8
RES_ACTOR_SKIN = 29
_MEDALS = {int(row["id"]): row for row in json.loads(
    (Path(__file__).parent / "data/client-tables/MedalInfo.json").read_text(encoding="utf-8"))["rows"]}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def grant_rewards(game: Any, profile: Mapping[str, Any], rewards: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Grant ``rewards`` (STResource-like mappings); return resChange entries."""

    rewards = [reward for reward in rewards if isinstance(reward, Mapping) and _int(reward.get("count")) > 0]
    if not rewards:
        return []
    systems = getattr(game, "systems", None)
    economy = getattr(game, "economy", None)
    changes: list[dict[str, Any]] = []
    ledger: list[dict[str, int]] = []
    new_instances: list[dict[str, Any]] = []
    medal_ids: list[int] = []
    skin_ids: list[int] = []
    state = systems._state(profile) if systems is not None else None
    now = int(game._now()) if hasattr(game, "_now") else int(time.time())
    for reward in rewards:
        rtype, rid, count = _int(reward.get("type")), _int(reward.get("id")), _int(reward.get("count"))
        if rtype == RES_ITEM and state is not None:
            entry = state.setdefault("items", {}).setdefault(str(rid), {"count": 0})
            entry["count"] = _int(entry.get("count")) + count
            entry["gotTime"] = now
            entry["isNew"] = True
            changes.append({"baseRes": {"type": RES_ITEM, "id": rid, "param": 0,
                                        "count": entry["count"], "countDelta": count}})
        elif rtype == RES_ACTOR and state is not None:
            for _ in range(count):
                new_instances.append(systems.new_actor(profile, state, rid, _int(reward.get("param"))))
        elif rtype == RES_ACTOR_SKIN and state is not None:
            try:
                owners = {
                    int(row["id"]): [_int(actor_id) for actor_id in (row.get("actorIds") or []) if _int(actor_id)]
                    for row in json.loads((Path(__file__).parent / "data/client-tables/ActorSkin.json").read_text(encoding="utf-8")).get("rows", [])
                    if row.get("id")
                }
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                owners = {}
            owner_ids = owners.get(rid) or [0]
            for owner in owner_ids:
                unlocked = state.setdefault("unlockedSkins", {}).setdefault(str(owner), [])
                if rid not in unlocked:
                    unlocked.append(rid)
                    unlocked.sort()
            skin_ids.append(rid)
        else:
            ledger.append({"type": rtype, "id": rid, "param": _int(reward.get("param")), "count": count})
            if rtype == RES_MEDAL and state is not None:
                # ResMedal is a unique cosmetic/buff record, not ResEquip (12).
                # The original Race medals expire after MedalInfo.expireDay.
                days = _int(_MEDALS.get(rid, {}).get("expireDay"))
                state.setdefault("medals", {})[str(rid)] = {
                    "id": rid, "gotTime": now, "isNew": True,
                    "expireTime": now + days * 86400 if days else 0,
                }
                medal_ids.append(rid)
    if state is not None:
        systems._save(profile, state)
    if ledger and economy is not None:
        ok, deltas, _ = economy._change_resources(profile, ledger)
        if ok:
            for delta in deltas:
                changes.append({"baseRes": {"type": delta["type"], "id": delta["id"], "param": delta["param"],
                                            "count": delta["count"], "countDelta": delta["countDelta"]}})
    for skin_id in skin_ids:
        changes.append({"baseRes": {"type": RES_ACTOR_SKIN, "id": skin_id, "param": 0, "count": 1, "countDelta": 1}})
    if new_instances:
        for record in new_instances:
            changes.append({"actor": systems._st_actor_json(record, profile, state, 1)})
    notifier = getattr(game, "base_push_notifier", None)
    if medal_ids and notifier is not None:
        game.after_commit(lambda: notifier(profile, 49, tuple(dict.fromkeys(medal_ids))))
    return changes


__all__ = ["grant_rewards", "RES_ITEM", "RES_ACTOR"]
