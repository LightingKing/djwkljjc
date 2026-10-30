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
from typing import Any, Iterable, Mapping

try:
    from . import actors as actor_store
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store

RES_ITEM = 11
RES_ACTOR = 15


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
    state = systems._state(profile) if systems is not None else None
    now = int(time.time())
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
                new_instances.append(actor_store.add(profile, state, rid))
        else:
            ledger.append({"type": rtype, "id": rid, "param": _int(reward.get("param")), "count": count})
    if state is not None:
        systems._save(profile, state)
    if ledger and economy is not None:
        ok, deltas, _ = economy._change_resources(profile, ledger)
        if ok:
            for delta in deltas:
                changes.append({"baseRes": {"type": delta["type"], "id": delta["id"], "param": delta["param"],
                                            "count": delta["count"], "countDelta": delta["countDelta"]}})
    if new_instances and hasattr(game, "_append_local_actor"):
        from google.protobuf import json_format

        container = game._types["DBGamePlayer.Actors"]()
        for record in new_instances:
            uid = int(record["uid"])
            game._append_local_actor(container, int(record["id"]), uid, uid - 1)
            container.player_actors[-1].count_delta = 1  # DBH.UpdateActors adds only countDelta > 0
            try:
                actor = json_format.MessageToDict(container.player_actors[-1], always_print_fields_with_no_presence=True)
            except TypeError:  # older protobuf
                actor = json_format.MessageToDict(container.player_actors[-1], including_default_value_fields=True)
            changes.append({"actor": actor})
    return changes


__all__ = ["grant_rewards", "RES_ITEM", "RES_ACTOR"]
