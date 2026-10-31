"""Deterministic local Maze (activity type 34) implementation.

The 2.0.4 client already contains MazeInfo, map widgets and settlement
windows; only ActivitySort/entry data was missing.  This module supplies a
small three-floor loop (MazeInfo 1) and keeps all progress in the profile
feature ledger.  The map seed is fixed so a restart cannot change a run.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

try:
    from .grants import grant_rewards
except ImportError:  # pragma: no cover
    from grants import grant_rewards

Body = bytes | bytearray | str | Mapping[str, Any] | None
Response = tuple[int, Any]


def _route(path: str) -> str:
    return (urlsplit(path or "").path or "/").rstrip("/").lower() or "/"


def _json(body: Body) -> dict[str, Any]:
    if isinstance(body, Mapping): return dict(body)
    if body is None: return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try: value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError): return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _request(game: Any, body: Body, message_name: str) -> dict[str, Any]:
    payload = _json(body)
    if payload or not isinstance(body, (bytes, bytearray)):
        return payload
    try:
        from google.protobuf import json_format, message_factory
        cls = message_factory.GetMessageClass(game._pool.FindMessageTypeByName(message_name))
        message = cls(); message.ParseFromString(bytes(body))
        return dict(json_format.MessageToDict(message, preserving_proto_field_name=True))
    except Exception:
        return payload


def _int(value: Any, default: int = 0) -> int:
    try: return int(value)
    except (TypeError, ValueError): return default


class LocalMaze:
    """Maze summary/enter/move/actor/settlement routes."""

    MAZE_INFO = {"id": 1, "difficulty": 30, "unlockLevel": 1, "maxActorCount": 8, "maxFloor": 3,
                 "cost": {"type": 11, "id": 80007, "count": 1}}

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._clock = clock or game._now

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        state = self.game.profile_features._state(profile)
        maze = state.setdefault("maze", {})
        maze.setdefault("mazeId", 0); maze.setdefault("floor", 0); maze.setdefault("playerCellIndex", 1)
        maze.setdefault("cells", []); maze.setdefault("selectedActors", []); maze.setdefault("isMazeOver", False)
        maze.setdefault("dungeonUid", 0)
        maze.setdefault("prizeId", [101]); maze.setdefault("received", False); maze.setdefault("seed", 33001)
        return state

    def _save(self, profile: Mapping[str, Any], state: dict[str, Any]) -> None:
        self.game.profile_features._save(profile, state)

    def _consume_ticket(self, profile: Mapping[str, Any]) -> tuple[bool, list[dict[str, Any]], Optional[str]]:
        systems = getattr(self.game, "systems", None)
        if systems is None:
            return False, [], "item_store_unavailable"
        bag = systems._state(profile)
        item = bag.setdefault("items", {}).setdefault("80007", {"count": 0})
        count = _int(item.get("count"))
        if count < 1:
            return False, [], "insufficient_item"
        item["count"] = count - 1; systems._save(profile, bag)
        return True, [{"baseRes": {"type": 11, "id": 80007, "param": 0, "count": item["count"], "countDelta": -1}}], None

    @staticmethod
    def _map_cells(floor: int, completed: int = 0) -> list[dict[str, Any]]:
        # 3x3, deterministic path 1 -> 2 -> 3 -> 6 -> 9.
        nexts = {1: 2, 2: 3, 3: 6, 6: 9, 9: 0}
        cells = []
        for idx in range(1, 10):
            cells.append({"nodeIndex": idx, "preCellIndex": 0 if idx == 1 else max(0, idx - 1),
                          "nextCellIndex": nexts.get(idx, 0), "branchIndex": 0, "eventId": 0,
                          "eventParam": "", "eventType": 0, "isComplete": idx <= completed})
        return cells

    def _maze_info(self, maze: Mapping[str, Any]) -> dict[str, Any]:
        floor = _int(maze.get("floor")) or 1
        # MazeResponseInfo has only curMazeFloor; maze id/difficulty live in
        # the outer summary and MazeInfo config.  mazeCells is a protobuf map,
        # so expose numeric keys as strings for json_format and the native
        # Restifizer decoder.
        cells = {str(cell["nodeIndex"]): {k: v for k, v in cell.items() if k != "nodeIndex"}
                 for cell in (maze.get("cells") or self._map_cells(floor))}
        return {"curMazeFloor": floor, "mazeMapDimension": 3,
                "mazeMapBuffId": 0, "playerCellIndex": _int(maze.get("playerCellIndex"), 1),
                "isMazeOver": bool(maze.get("isMazeOver")), "mazeCells": cells,
                "curBuff": [], "prizeId": list(maze.get("prizeId") or [101])}

    def _actors(self, profile: Mapping[str, Any], maze: Mapping[str, Any]) -> list[dict[str, Any]]:
        systems = getattr(self.game, "systems", None)
        if systems is None: return []
        state = systems._state(profile)
        try: records = list(__import__("actors").ordered(profile, state))
        except Exception: records = []
        result = []
        for record in records[:8]:
            actor = dict(record)
            actor.setdefault("attrs", []); actor.setdefault("level", 1); actor.setdefault("star", 1)
            result.append({"actor": actor, "hp": 1000, "dead": False})
        return result

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = _route(path); method = method.upper(); state = self._state(profile); maze = state["maze"]; payload = _json(body)
        if route in {"/fci/maze/summary", "/fci/maze/summary/"}:
            if method != "GET": return 405, {"error": "method_not_allowed"}
            return 200, {"isSelectActor": bool(maze.get("selectedActors")), "mazeId": _int(maze.get("mazeId")), "mazeInfo": self._maze_info(maze)}
        if route in {"/fci/maze/enter", "/fci/maze/enter/"}:
            if method != "POST": return 405, {"error": "method_not_allowed"}
            if not payload: payload = _request(self.game, body, "ApiMazeEnter")
            if _int(maze.get("mazeId")) and not bool(maze.get("isMazeOver")): return 409, {"error": "maze_already_active"}
            maze_id = _int(payload.get("mazeId", payload.get("maze_id", 1)), 1)
            if maze_id != 1: return 400, {"error": "maze_not_available", "mazeId": maze_id}
            ok, changes, error = self._consume_ticket(profile)
            if not ok: return 409, {"error": error or "maze_ticket_not_enough"}
            maze.update({"mazeId": 1, "floor": 1, "playerCellIndex": 1, "cells": self._map_cells(1), "selectedActors": [], "isMazeOver": False, "received": False, "dungeonUid": 0, "prizeId": [101]}); self._save(profile, state)
            return 200, {"mazeInfo": self._maze_info(maze), "resChange": changes}
        # MazeActorFilter posts the selected uid list before opening MazeMain.
        # The callback ignores the body, but the selection must be persisted so
        # the subsequent FightActorGroup GET exposes the same members after a
        # restart.  GET remains the actor-card source used by the filter.
        if route in {"/fci/maze/actor", "/fci/maze/actor/"}:
            if method == "GET":
                return 200, {"selectActors": self._actors(profile, maze)}
            if method != "POST": return 405, {"error": "method_not_allowed"}
            if not payload: payload = _request(self.game, body, "ApiMazeActor")
            raw_uids = payload.get("actorUids", payload.get("actor_uids", []))
            if not isinstance(raw_uids, (list, tuple)):
                return 409, {"error": "maze_invalid_actor_selection"}
            selected = [_int(uid) for uid in raw_uids]
            available = {_int(item.get("actor", {}).get("uid", item.get("actor", {}).get("id")))
                        for item in self._actors(profile, maze)}
            if (len(selected) == 0 or len(selected) != len(set(selected)) or len(selected) > 8
                    or any(uid <= 0 or uid not in available for uid in selected)):
                return 409, {"error": "maze_invalid_actor_selection"}
            maze["selectedActors"] = selected; self._save(profile, state)
            return 200, {}
        # MazeMain polls each member through this actor-specific binding.  Keep
        # the response shape identical to ApiMazeActor.result: actor + hp/dead.
        if route.startswith("/fci/mazeactor/"):
            if method != "GET": return 405, {"error": "method_not_allowed"}
            uid = _int(route.rsplit("/", 1)[-1])
            if uid <= 0:
                return 404, {"error": "actor_not_found"}
            for entry in self._actors(profile, maze):
                actor = entry.get("actor") if isinstance(entry, Mapping) else None
                if isinstance(actor, Mapping) and _int(actor.get("uid", actor.get("id"))) == uid:
                    return 200, entry
            return 404, {"error": "actor_not_found"}
        if route in {"/fci/maze/fightactorgroup", "/fci/maze/fightactorgroup/"}:
            if method == "GET": return 200, {"fightGroup": [{"members": [{"index": i, "actorUid": uid} for i, uid in enumerate(maze.get("selectedActors") or [])], "energyMp": 0, "energySp": 0}]}
            if method != "POST": return 405, {"error": "method_not_allowed"}
            if not payload: payload = _request(self.game, body, "ApiMazeFightActorGroup")
            members = payload.get("members", payload.get("actorUids", payload.get("actor_uids", [])))
            selected = [_int(v.get("actorUid", v.get("actor_uid", v))) if isinstance(v, Mapping) else _int(v) for v in members]
            available = {_int(item.get("actor", {}).get("uid", item.get("actor", {}).get("id")))
                        for item in self._actors(profile, maze)}
            if len(selected) != len(set(selected)) or len(selected) > 8 or any(uid <= 0 or uid not in available for uid in selected):
                return 409, {"error": "maze_invalid_actor_selection"}
            maze["selectedActors"] = selected; self._save(profile, state)
            return 200, {"fightGroup": [{"members": [{"index": i, "actorUid": uid} for i, uid in enumerate(maze["selectedActors"])]}]}
        if route in {"/fci/maze/receiveprize", "/fci/maze/receiveprize/"}:
            if method != "POST": return 405, {"error": "method_not_allowed"}
            if not maze.get("isMazeOver"): return 409, {"error": "maze_not_over"}
            if maze.get("received"): return 200, {"resChange": []}
            changes = grant_rewards(self.game, profile, [{"type": 11, "id": 60001, "count": 1}]); maze["received"] = True; maze["mazeId"] = 0; self._save(profile, state); return 200, {"resChange": changes}
        if route == "/fci/maze-dungeon" or route.startswith("/fci/maze-dungeon/"):
            suffix = route[len("/fci/maze-dungeon"):].strip("/")
            if suffix.isdigit() and suffix.endswith("active"):
                suffix = "active"
            if not suffix and method == "POST":
                if not maze.get("mazeId") or maze.get("isMazeOver"): return 409, {"error": "maze_not_active"}
                cell = _int(payload.get("cellIndex"), _int(maze.get("playerCellIndex")))
                current = _int(maze.get("playerCellIndex"), 1)
                cells = {int(row["nodeIndex"]): row for row in maze.get("cells") or self._map_cells(_int(maze.get("floor"), 1))}
                if cell != current or current not in cells:
                    return 409, {"error": "maze_cell_not_current"}
                uid = int(self._clock() * 1000) + cell
                maze["dungeonUid"] = uid; self._save(profile, state)
                return 200, {"dungeonUid": uid, "energyMp": 0, "energySp": 0, "myRecord": [{"actorUid": uid, "hp": 1000}], "baseInfo": {"dungeonId": 34001}, "enemyRecord": {"hp": [1000]}}
            if suffix.endswith("/active") and method == "PATCH": return 200, {}
            if suffix.endswith("/balance") and method == "POST":
                if not maze.get("dungeonUid"): return 409, {"error": "maze_dungeon_not_active"}
                idx = _int(maze.get("playerCellIndex"), 1); cells = {1: 2, 2: 3, 3: 6, 6: 9, 9: 9}
                next_idx = cells.get(idx, 0)
                if not next_idx:
                    return 409, {"error": "maze_invalid_cell"}
                maze["playerCellIndex"] = next_idx; maze["cells"] = self._map_cells(_int(maze.get("floor"), 1), next_idx); maze["dungeonUid"] = 0
                if next_idx == 9:
                    if _int(maze.get("floor"), 1) >= 3: maze["isMazeOver"] = True
                    else: maze["floor"] = _int(maze.get("floor"), 1) + 1; maze["playerCellIndex"] = 1; maze["cells"] = self._map_cells(_int(maze.get("floor"), 1))
                self._save(profile, state); info = self._maze_info(maze)
                return 200, {"isMazeOver": bool(maze.get("isMazeOver")), "autoMove": {"mazeInfo": info, "way": [next_idx]}, "result": {"win": True, "resChange": []}}
            if suffix.endswith("/active") and method != "PATCH": return 405, {"error": "method_not_allowed"}
            if suffix.endswith("/balance") and method != "POST": return 405, {"error": "method_not_allowed"}
            if suffix and suffix.isdigit() and method == "DELETE": maze["dungeonUid"] = 0; self._save(profile, state); return 200, {}
            return 405, {"error": "method_not_allowed"}
        if route in {"/fci/maze/move", "/fci/maze/move/"}:
            if method != "POST": return 405, {"error": "method_not_allowed"}
            if not payload: payload = _request(self.game, body, "ApiMazeMove")
            if not maze.get("mazeId"): return 409, {"error": "maze_not_active"}
            if maze.get("dungeonUid"):
                return 409, {"error": "maze_dungeon_active"}
            end = _int(payload.get("endCellIndex", payload.get("end_cell_index", payload.get("cellIndex"))), _int(maze.get("playerCellIndex")))
            current = _int(maze.get("playerCellIndex"), 1)
            cells = {int(row["nodeIndex"]): row for row in maze.get("cells") or self._map_cells(_int(maze.get("floor"), 1))}
            if end != _int(cells.get(current, {}).get("nextCellIndex")):
                return 409, {"error": "maze_move_not_adjacent"}
            maze["playerCellIndex"] = end; maze["cells"] = self._map_cells(_int(maze.get("floor"), 1), end); self._save(profile, state)
            return 200, {"way": [end], "newBuff": 0, "influenceBuff": [], "resChange": [], "mazeInfo": self._maze_info(maze)}
        return None


__all__ = ["LocalMaze"]
