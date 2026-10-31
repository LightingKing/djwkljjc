"""Local implementations for the five welfare tabs that were hidden in 2.0.4.

The retired service did not leave a complete event database, so this module
keeps the client visible and uses small, deterministic account-local records.
All mutations are idempotent and go through the same resource/grant helpers as
the other local activities.  The public tables (jigsaw/postcard) are copied
from the extracted client; seal/theme defaults are explicitly local policy.
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

CHIP_ORDER = [20002, 20116, 20014, 20128, 20107, 20108, 20012, 20006]
JIGSAW_REWARDS = {
    5: [{"type": 11, "id": 60001, "count": 1}, {"type": 7, "id": 0, "count": 50000}],
    15: [{"type": 11, "id": 60001, "count": 2}, {"type": 11, "id": 90001, "count": 10}, {"type": 7, "id": 0, "count": 150000}],
    30: [{"type": 11, "id": 60001, "count": 3}, {"type": 5, "id": 0, "count": 200}, {"type": 7, "id": 0, "count": 350000}],
}
POSTCARD_REWARDS = {
    0: [{"type": 5, "id": 0, "count": 300}, {"type": 11, "id": 60001, "count": 3}],
    1: [{"type": 11, "id": 60001, "count": 1}, {"type": 29, "id": 100107, "count": 1}, {"type": 7, "id": 0, "count": 12000}, {"type": 11, "id": 10001, "count": 10}],
    2: [{"type": 11, "id": 60001, "count": 1}, {"type": 29, "id": 100907, "count": 1}, {"type": 7, "id": 0, "count": 12000}, {"type": 11, "id": 10001, "count": 10}],
    3: [{"type": 11, "id": 60001, "count": 1}, {"type": 29, "id": 101406, "count": 1}, {"type": 7, "id": 0, "count": 12000}, {"type": 11, "id": 10001, "count": 10}],
    4: [{"type": 11, "id": 60001, "count": 1}, {"type": 6, "id": 0, "count": 20}, {"type": 7, "id": 0, "count": 12000}, {"type": 11, "id": 10001, "count": 10}],
}


def _route(path: str) -> str:
    return (urlsplit(path or "").path or "/").rstrip("/").lower() or "/"


def _json(body: Body) -> dict[str, Any]:
    if isinstance(body, Mapping):
        return dict(body)
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _request(game: Any, body: Body, message_name: str) -> dict[str, Any]:
    """Decode the native wire request, falling back to the JSON seam."""
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
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class LocalWelfareFeatures:
    """Stateful local handlers for seal/chip/postcard/jigsaw/theme tabs."""

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._clock = clock or game._now

    def _now(self) -> int:
        return int(self._clock())

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        state = self.game.profile_features._state(profile)
        welfare = state.setdefault("welfare", {})
        welfare.setdefault("seal", {"cells": [], "rewards": [], "newOpenFlag": True})
        welfare.setdefault("chips", {"settle": [], "exchange": [], "modifyCount": 0, "season": 1})
        welfare.setdefault("postcard", {"cells": {}, "received": []})
        welfare.setdefault("jigsaw", {"cells": [], "received": []})
        welfare.setdefault("theme", {"tasks": {}, "points": 0, "rewards": []})
        return state

    def _save(self, profile: Mapping[str, Any], state: dict[str, Any]) -> None:
        self.game.profile_features._save(profile, state)

    def _message(self, name: str):
        try:
            from google.protobuf import message_factory
            return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()
        except Exception:
            return None

    @staticmethod
    def _append_changes(result: Any, changes: list[dict[str, Any]]) -> None:
        field = getattr(result, "res_change", None)
        if field is None:
            return
        from google.protobuf import json_format
        for change in changes:
            dynamic = field.add()
            try:
                json_format.ParseDict(change, dynamic)
            except Exception:
                continue

    def _consume(self, profile: Mapping[str, Any], resource: Mapping[str, Any]) -> tuple[bool, list[dict[str, Any]], Optional[str]]:
        rid, amount, rtype = _int(resource.get("id")), _int(resource.get("count"), 1), _int(resource.get("type"))
        if rtype == 11:
            systems = getattr(self.game, "systems", None)
            if systems is None:
                return False, [], "item_store_unavailable"
            store = systems._state(profile)
            item = store.setdefault("items", {}).setdefault(str(rid), {"count": 0})
            current = _int(item.get("count"))
            if current < amount:
                return False, [], "insufficient_item"
            item["count"] = current - amount
            systems._save(profile, store)
            return True, [{"baseRes": {"type": 11, "id": rid, "param": 0, "count": item["count"], "countDelta": -amount}}], None
        ok, changes, error = self.game.economy._change_resources(profile, [dict(resource, count=-amount)])
        return ok, changes, error

    # ------------------------------- seal (5x5; 12 local-policy rewards)
    def _seal(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        data = state["welfare"]["seal"]
        cells = {int(v) for v in data.get("cells", []) if _int(v) > 0}
        rewards = {int(v) for v in data.get("rewards", []) if _int(v) > 0}
        payload = _json(body)
        if route.endswith("/flag"):
            if method == "GET":
                return 200, {"newOpenFlag": bool(data.get("newOpenFlag", True))}
            if method not in {"PATCH", "POST", "PUT"}:
                return 405, {"error": "method_not_allowed"}
            data["newOpenFlag"] = bool(payload.get("newOpenFlag", payload.get("isNew", False))); self._save(profile, state)
            return 200, {"newOpenFlag": bool(data["newOpenFlag"])}
        if route.endswith("/fill") or route.startswith("/fci/seal/fill/"):
            if method != "POST": return 405, {"error": "method_not_allowed"}
            cell_id = _int(route.rsplit("/", 1)[-1], _int(payload.get("cellId", payload.get("cell_id"))))
            if not 1 <= cell_id <= 25: return 400, {"error": "invalid_cell_id"}
            if cell_id in cells: return 200, {"cellId": cell_id, "resChange": []}
            # The historical SealCell rows are unavailable; one 1016 token is
            # the reviewed local default and is exposed in GET below.
            ok, changes, error = self._consume(profile, {"type": 11, "id": 1016, "param": 0, "count": 1})
            if not ok: return 409, {"error": error or "seal_cost_not_enough", "message": "not enough seal tokens"}
            cells.add(cell_id); data["cells"] = sorted(cells); self._save(profile, state)
            result = self._message("ApiSealFill.result")
            if result is not None:
                result.cell_id = cell_id; self._append_changes(result, changes); return 200, result
            return 200, {"cellId": cell_id, "resChange": changes}
        if "/reward" in route:
            if method != "POST": return 405, {"error": "method_not_allowed"}
            reward_id = _int(route.rsplit("/", 1)[-1], _int(payload.get("rewardId", payload.get("reward_id"))))
            if not 1 <= reward_id <= 12: return 400, {"error": "invalid_reward_id"}
            if reward_id in rewards: return 200, {"rewardId": reward_id, "resChange": []}
            # 1-5 rows, 6-10 columns, 11 diagonal, 12 full board.
            complete = ((reward_id <= 5 and all((reward_id - 1) * 5 + c in cells for c in range(1, 6))) or
                        (6 <= reward_id <= 10 and all((c - 1) * 5 + reward_id - 5 in cells for c in range(1, 6))) or
                        (reward_id == 11 and all(i * 6 + 1 in cells for i in range(5))) or
                        (reward_id == 12 and len(cells) == 25))
            if not complete: return 409, {"error": "seal_reward_not_ready"}
            reward = [{"type": 7, "id": 0, "count": 100 * reward_id}]
            changes = grant_rewards(self.game, profile, reward); rewards.add(reward_id); data["rewards"] = sorted(rewards); self._save(profile, state)
            result = self._message("ApiSealReward.result")
            if result is not None:
                result.reward_id = reward_id; self._append_changes(result, changes); return 200, result
            return 200, {"rewardId": reward_id, "resChange": changes}
        if method != "GET": return 405, {"error": "method_not_allowed"}
        cell_info = [{"info": {"cellId": i, "cost": {"type": 11, "id": 1016, "count": 1}}, "fill": i in cells} for i in range(1, 26)]
        reward_info = []
        for i in range(1, 13):
            reward_info.append({"info": {"rewardId": i, "rewardKind": 4 if i == 12 else (3 if i == 11 else 1), "reward": [{"type": 7, "id": 0, "count": 100 * i}], "quality": 1}, "got": i in rewards})
        result = self._message("ApiSeal.result")
        if result is not None:
            from google.protobuf import json_format
            try:
                json_format.ParseDict({"cellInfo": cell_info, "rewardInfo": reward_info}, result); return 200, result
            except Exception:
                pass
        return 200, {"cellInfo": cell_info, "rewardInfo": reward_info}

    # --------------------------------- chip exchange (two changes per season)
    def _chips(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        data = state["welfare"]["chips"]
        def result(values: Mapping[str, Any]) -> dict[str, Any]:
            # ApiChipExchange.result contains one ChipExchangeInfo message.
            # Keep the historical fields inside that message so protobuf and
            # JSON clients decode the same shape.
            return {"chipExchangeInfo": dict(values)}
        if method == "GET":
            return 200, result({"lastActivitySeason": _int(data.get("season"), 1), "modifyCount": _int(data.get("modifyCount")), "settlePosInfo": list(data.get("settle", [])), "exchangePosInfo": list(data.get("exchange", []))})
        if method != "POST": return 405, {"error": "method_not_allowed"}
        payload = _request(self.game, body, "ApiChipExchange")
        selected = payload.get("chips", payload.get("settlePosInfo", []))
        if not isinstance(selected, list) or len(selected) != len(CHIP_ORDER): return 400, {"error": "invalid_chip_exchange_list"}
        selected = [_int(v) for v in selected]
        if any(v not in CHIP_ORDER for v in selected) or len(set(selected)) != len(selected): return 400, {"error": "invalid_chip_exchange_list"}
        if selected == list(data.get("settle", [])): return 200, result({"lastActivitySeason": 1, "modifyCount": _int(data.get("modifyCount")), "settlePosInfo": selected, "exchangePosInfo": list(data.get("exchange", []))})
        if _int(data.get("modifyCount")) >= 2: return 409, {"error": "chip_exchange_limit_reached"}
        data["settle"] = selected; data["exchange"] = selected[:]; data["modifyCount"] = _int(data.get("modifyCount")) + 1; self._save(profile, state)
        return 200, result({"lastActivitySeason": 1, "modifyCount": _int(data["modifyCount"]), "settlePosInfo": selected, "exchangePosInfo": selected[:]})

    # ------------------------------------------ postcard (4*18 cells; 5 prizes)
    def _postcard(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        data = state["welfare"]["postcard"]; cells = data.setdefault("cells", {}); received = {int(v) for v in data.setdefault("received", [])}
        payload = _json(body)
        if not payload and "lighten" in route:
            payload = _request(self.game, body, "ApiPostcardLighten")
        elif not payload and ("receiveprize" in route or "receive-prize" in route):
            payload = _request(self.game, body, "ApiPostcardReceive")
        # Native routes are /postcard/<postcardId>/lighten and
        # /postcard/<postcardId>/receiveprize; the final path component is an
        # action, so do not accidentally default every request to postcard 1.
        parts = route.split("/")
        path_id = _int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        postcard_id = path_id or _int(payload.get("postcardId", payload.get("postcard_id")), 1)
        if method == "GET":
            infos = [{"postcardId": pid, "postcardCell": [{"cellId": cid, "isLighten": f"{pid}:{cid}" in cells} for cid in range(1, 19)]} for pid in range(1, 5)]
            prizes = [{"prizeReceiveStatus": 0 in received}] + [{"prizeReceiveStatus": pid in received} for pid in range(1, 5)]
            return 200, {"isFirstEnter": not bool(cells), "postcardInfo": infos, "prizeInfo": prizes}
        if method != "POST": return 405, {"error": "method_not_allowed"}
        if "lighten" in route:
            if not 1 <= postcard_id <= 4: return 400, {"error": "invalid_postcard_id"}
            cell_id = _int(payload.get("cellId", payload.get("cell_id")))
            if not 1 <= cell_id <= 18: return 400, {"error": "invalid_cell_id"}
            key = f"{postcard_id}:{cell_id}"
            if key in cells: return 200, {"cellId": cell_id, "resChange": []}
            ok, changes, error = self._consume(profile, {"type": 11, "id": 2001, "param": 0, "count": 1})
            if not ok: return 409, {"error": error or "postcard_cost_not_enough"}
            cells[key] = True; self._save(profile, state); return 200, {"cellId": cell_id, "resChange": changes}
        if "receiveprize" in route or "receive-prize" in route:
            target = postcard_id if postcard_id else _int(payload.get("postcardId"))
            if target and target not in range(1, 5): return 400, {"error": "invalid_postcard_id"}
            key = target if target else 0
            if key in received: return 200, {"resChange": []}
            ready = len([k for k in cells if (key == 0 or k.startswith(f"{key}:"))]) >= (72 if key == 0 else 18)
            if not ready: return 409, {"error": "postcard_prize_not_ready"}
            changes = grant_rewards(self.game, profile, POSTCARD_REWARDS[key]); received.add(key); data["received"] = sorted(received); self._save(profile, state); return 200, {"postcardId": key, "resChange": changes}
        # initial POST fci/Postcard/ marks the first visit; no state mutation
        data["visited"] = True; self._save(profile, state); return 200, {"isFirstEnter": False}

    # ------------------------------------------- jigsaw (30 cells; 4 prizes)
    def _jigsaw(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        data = state["welfare"]["jigsaw"]; cells = {int(v) for v in data.setdefault("cells", [])}; received = {int(v) for v in data.setdefault("received", [])}; payload = _json(body)
        if not payload and "receive" in route:
            payload = _request(self.game, body, "ApiJigsawPuzzleReceive")
        elif not payload and route not in {"/fci/jigsawpuzzle", "/fci/jigsaw-puzzle"}:
            payload = _request(self.game, body, "ApiJigsawPuzzleLighten")
        if method == "GET": return 200, {"isFirstEnter": not bool(cells), "lightenCell": sorted(cells), "receivePrize": sorted(received)}
        if method != "POST": return 405, {"error": "method_not_allowed"}
        if "receive" in route:
            light_num = _int(payload.get("lightNum", payload.get("light_num")))
            if light_num not in JIGSAW_REWARDS: return 400, {"error": "invalid_light_num"}
            if light_num in received: return 200, {"resChange": []}
            if len(cells) < light_num: return 409, {"error": "jigsaw_prize_not_ready"}
            changes = grant_rewards(self.game, profile, JIGSAW_REWARDS[light_num]); received.add(light_num); data["received"] = sorted(received); self._save(profile, state); return 200, {"resChange": changes}
        if route in {"/fci/jigsawpuzzle", "/fci/jigsaw-puzzle"}:
            data["visited"] = True; self._save(profile, state); return 200, {"isFirstEnter": False, "resChange": []}
        cell_id = _int(payload.get("cellId", payload.get("cell_id")))
        if not 1 <= cell_id <= 30: return 400, {"error": "invalid_cell_id"}
        if cell_id in cells: return 200, {"resChange": []}
        ok, changes, error = self._consume(profile, {"type": 11, "id": 1016, "param": 0, "count": 25})
        if not ok: return 409, {"error": error or "jigsaw_cost_not_enough"}
        cells.add(cell_id); data["cells"] = sorted(cells); self._save(profile, state); return 200, {"resChange": changes}

    def _theme(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        data = state["welfare"]["theme"]; payload = _json(body)
        if method == "GET":
            rows = []
            for task_id in range(1, 4):
                done = _int(data["tasks"].get(str(task_id))) > 0
                rows.append({"id": task_id, "progress": 1 if done else 0, "target": 1, "rewardGot": done, "activePoint": 20, "category": 102})
            return 200, rows
        if method != "POST": return 405, {"error": "method_not_allowed"}
        task_id = _int(route.rsplit("/", 1)[-1], _int(payload.get("taskId", payload.get("task_id"))))
        if task_id <= 0: return 400, {"error": "invalid_task_id"}
        if _int(data["tasks"].get(str(task_id))) > 0: return 200, {"rewardGot": True, "resChange": []}
        data["tasks"][str(task_id)] = self._now(); data["points"] = _int(data.get("points")) + 20; self._save(profile, state)
        tasks = getattr(self.game, "tasks", None)
        if tasks is not None and hasattr(tasks, "record_extra_active_point"):
            tasks.record_extra_active_point(profile, 20, point_type=1)
        return 200, {"taskId": task_id, "extraActivePoint": _int(data["points"]), "rewardGot": True, "resChange": grant_rewards(self.game, profile, [{"type": 7, "id": 0, "count": 1000}])}

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = _route(path); method = method.upper(); state = self._state(profile)
        if route.startswith("/fci/seal"):
            return self._seal(method, route, body, profile, state)
        if route.startswith("/fci/chipexchange"):
            return self._chips(method, route, body, profile, state)
        if route.startswith("/fci/postcard"):
            return self._postcard(method, route, body, profile, state)
        if route.startswith("/fci/jigsawpuzzle") or route.startswith("/fci/jigsaw-puzzle"):
            return self._jigsaw(method, route, body, profile, state)
        if route.startswith("/fci/themeactivitytasks") or route.startswith("/fci/theme-activity-tasks"):
            return self._theme(method, route, body, profile, state)
        parts = route.split("/")
        if len(parts) == 5 and parts[:3] == ["", "fci", "task"] and parts[3].isdigit() and parts[4] == "reward":
            return self._theme(method, route, body, profile, state)
        return None


__all__ = ["LocalWelfareFeatures"]
