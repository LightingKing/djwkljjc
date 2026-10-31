"""Local implementations for the five welfare tabs that were hidden in 2.0.4.

The retired service did not leave a complete event database, so this module
keeps the client visible and uses small, deterministic account-local records.
All mutations are idempotent and go through the same resource/grant helpers as
the other local activities.  The public tables (jigsaw/postcard) are copied
from the extracted client; seal defaults are explicitly local policy. Theme
tasks use the authoritative task definitions and progress in LocalTasks.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from economy import _client_rows
from server.core.reset import policy_for

try:
    from .grants import grant_rewards
except ImportError:  # pragma: no cover
    from grants import grant_rewards

Body = bytes | bytearray | str | Mapping[str, Any] | None
Response = tuple[int, Any]

CHIP_ORDER = list(_client_rows("Misc")[0]["chipExchangeSet"])
CHIP_MODIFY_LIMIT = int(_client_rows("Misc")[0]["chipExchangeMaxModifyCount"])
CHIP_POSITIONS = {int(row["dungeonId"]): int(row["chipExchangePlayerPos"])
                  for row in _client_rows("DungeonReward") if row.get("chipExchangePlayerPos")}
ACTOR_CHIPS = {int(row["id"]) for row in _client_rows("ItemInfo") if int(row.get("funcType", 0)) == 3}
WELFARE_ACTIVITY_TYPES = (16, 26, 44, 48)
JIGSAW_REWARDS = {
    0: [{"type": 11, "id": 31014, "count": 1}],
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

    def cycle_window(self, now=None):
        """One 28-day local loop for API state and the activity UI clock."""
        now = self._now() if now is None else int(now)
        policy = policy_for(self.game)
        anchor = policy.parse_config_datetime("2026-10-26T06:00:00+08:00")
        days = 28
        schedule = getattr(self.game, "activity_schedule", None)
        if schedule is not None:
            snapshot = schedule.snapshot(now)
            anchor = policy.parse_config_datetime(snapshot["anchor"])
            days = max(1, int(snapshot.get("periodDays", days)))
        length = days * 86400
        start = anchor + ((now - anchor) // length) * length
        # A calendar label remains a valid uint32 even before the first local
        # schedule anchor, unlike a negative numbered season.
        season = int(policy.local_datetime(start).strftime("%Y%m%d"))
        return {"start": start, "end": start + length, "season": season}

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        state = self.game.profile_features._state(profile)
        welfare = state.setdefault("welfare", {})
        window = self.cycle_window()
        previous = welfare.get("cycleStart")
        if previous is not None and int(previous) < window["start"]:
            for key in ("seal", "chips", "postcard", "jigsaw", "earnedMaterials"):
                welfare.pop(key, None)
        if previous is None or int(previous) <= window["start"]:
            welfare.update(cycleStart=window["start"], cycleEnd=window["end"], season=window["season"])
        welfare.setdefault("seal", {"cells": [], "rewards": [], "newOpenFlag": True})
        welfare.setdefault("chips", {"settle": [], "exchange": [], "modifyCount": 0})
        welfare.setdefault("postcard", {"cells": {}, "received": []})
        welfare.setdefault("jigsaw", {"cells": [], "received": []})
        chips = welfare["chips"]
        chips["season"] = welfare["season"]
        if chips.get("effectiveAt") and self._now() >= chips["effectiveAt"]:
            chips["exchange"] = list(chips["settle"])
            chips.pop("effectiveAt")
        if "earnedMaterials" not in welfare:
            items = self.game.systems._state(profile).get("items", {})
            welfare["earnedMaterials"] = {
                "1016": _int(items.get("1016", {}).get("count")) + len(welfare["seal"]["cells"]) + 25 * len(welfare["jigsaw"]["cells"]),
                "2001": _int(items.get("2001", {}).get("count")) + len(welfare["postcard"]["cells"]),
            }
        self._save(profile, state)
        return state

    def replace_chip_rewards(self, profile, dungeon_id, rewards):
        """Apply the same Bonus-slot mapping that DungeonUtil displays."""
        position = CHIP_POSITIONS.get(int(dungeon_id))
        # The old generic chapter pool included postcard 2001 just because
        # its source is ChapterNormal. Seasonal tokens now come only from
        # grant_battle_materials so that pool cannot bypass the event cap.
        result = [dict(row) for row in rewards
                  if not (_int(row.get("type")) == 11 and _int(row.get("id")) in (1016, 2001))]
        if position is None:
            return result
        state = self._state(profile)
        exchange = state["welfare"]["chips"].get("exchange", [])
        if len(exchange) < position:
            return result
        target = _int(exchange[position - 1])
        if target in CHIP_ORDER:
            for row in result:
                if _int(row.get("type")) == 11 and _int(row.get("id")) in ACTOR_CHIPS:
                    row["id"] = target
        return result

    def grant_battle_materials(self, profile, dungeon_id, *, dungeon_type, runs=1):
        """Finite seasonal drops, called only after a real won battle."""
        if int(dungeon_type) not in (2, 5):
            return []
        with self.game.atomic_mutation():
            state = self._state(profile)
            earned = state["welfare"]["earnedMaterials"]
            policy = policy_for(self.game)
            # The event weekend begins Friday 06:00, not Friday midnight.
            day = policy.local_datetime(policy.period_start(self._now())).isoweekday()
            candy = (2 if int(dungeon_type) == 5 or int(dungeon_id) in CHIP_POSITIONS else 1)
            candy *= 2 if day in (5, 6, 7) else 1
            postcard = 2 if int(dungeon_type) == 5 else 1
            rewards = []
            for rid, count, cap in ((1016, candy, 775), (2001, postcard, 72)):
                amount = min(max(0, cap - _int(earned.get(str(rid)))), count * max(1, int(runs)))
                if amount:
                    rewards.append({"type": 11, "id": rid, "count": amount})
                    earned[str(rid)] = _int(earned.get(str(rid))) + amount
            changes = grant_rewards(self.game, profile, rewards)
            self._save(profile, state)
            return changes

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
        payload = _request(self.game, body, "ApiSealFlag") if route.endswith("/flag") else _json(body)
        if route.endswith("/flag"):
            if method == "GET":
                return 200, {"newOpenFlag": bool(data.get("newOpenFlag", True))}
            if method not in {"PATCH", "POST", "PUT"}:
                return 405, {"error": "method_not_allowed"}
            data["newOpenFlag"] = bool(payload.get("newOpenFlag", payload.get("new_open_flag", payload.get("isNew", False)))); self._save(profile, state)
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
            kind = 1 if i <= 5 else 2 if i <= 10 else 3 if i == 11 else 4
            index = i if i <= 5 else i - 5 if i <= 10 else 1
            reward_info.append({"info": {"rewardId": i, "rewardKind": kind, "rewardIndex": index, "reward": [{"type": 7, "id": 0, "count": 100 * i}], "quality": 1}, "got": i in rewards})
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
        def snapshot():
            return {"lastActivitySeason": _int(data["season"]), "modifyCount": _int(data.get("modifyCount")),
                    "settlePosInfo": list(data.get("settle", [])), "exchangePosInfo": list(data.get("exchange", []))}
        # The Lua UI stores this response directly in fci/chipexchange/.
        # DungeonStory.ReplaceChip and DungeonUtil read the top-level lists,
        # including the empty lists before any selection has been made.
        if method == "GET":
            return 200, snapshot()
        if method != "POST": return 405, {"error": "method_not_allowed"}
        payload = _request(self.game, body, "ApiChipExchange")
        selected = payload.get("chips", payload.get("settlePosInfo", []))
        if not isinstance(selected, list) or len(selected) != len(CHIP_ORDER): return 400, {"error": "invalid_chip_exchange_list"}
        selected = [_int(v) for v in selected]
        if any(v not in CHIP_ORDER for v in selected) or len(set(selected)) != len(selected): return 400, {"error": "invalid_chip_exchange_list"}
        if selected == list(data.get("settle", [])): return 200, snapshot()
        if _int(data.get("modifyCount")) >= CHIP_MODIFY_LIMIT: return 409, {"error": "chip_exchange_limit_reached"}
        policy = policy_for(self.game)
        local = policy.local_datetime(self._now())
        last_day = policy.local_datetime(state["welfare"]["cycleEnd"]).date() - timedelta(days=1)
        if 4 <= local.hour < 6 or local.date() >= last_day:
            return 409, {"error": "chip_exchange_closed_for_changes"}
        data["settle"] = selected
        data["effectiveAt"] = policy.next_boundary(self._now())
        data["modifyCount"] = _int(data.get("modifyCount")) + 1
        self._save(profile, state)
        return 200, snapshot()

    # ------------------------------------------ postcard (4*18 cells; 5 prizes)
    def _postcard(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        data = state["welfare"]["postcard"]; cells = data.setdefault("cells", {}); received = {int(v) for v in data.setdefault("received", [])}
        payload = _json(body)
        if not payload and "lighten" in route:
            payload = _request(self.game, body, "ApiPostcardLighten")
        elif not payload and ("receiveprize" in route or "receive-prize" in route):
            payload = _request(self.game, body, "ApiPostcardReceive")
        # WlfPostcard: POST /Postcard/Lighten/<id> {cellId}, and
        # POST /Postcard/ReceivePrize/ {postcardId}, including 0 for all four.
        native_lighten = re.fullmatch(r"/fci/postcard/lighten/(\d+)", route)
        legacy_path = re.fullmatch(r"/fci/postcard/(\d+)/(?:lighten|receiveprize)", route)
        path_match = native_lighten or legacy_path
        postcard_id = int(path_match[1]) if path_match else _int(payload.get("postcardId", payload.get("postcard_id")))
        if method == "GET":
            infos = [{"postcardId": pid, "postcardCell": [{"cellId": cid, "isLighten": f"{pid}:{cid}" in cells} for cid in range(1, 19)]} for pid in range(1, 5)]
            prizes = [{"prizeReceiveStatus": 0 in received}] + [{"prizeReceiveStatus": pid in received} for pid in range(1, 5)]
            return 200, {"isFirstEnter": not bool(data.get("visited") or cells), "postcardInfo": infos, "prizeInfo": prizes}
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
            target = postcard_id
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
        if method == "GET":
            # Repair the previously released full-board omission. Login and
            # the item push make the already-earned scene visible again.
            if len(cells) == 30 and 0 not in received:
                self._complete_jigsaw(profile, state, received)
            return 200, {"isFirstEnter": not bool(data.get("visited") or cells), "lightenCell": sorted(cells), "receivePrize": sorted(received)}
        if method != "POST": return 405, {"error": "method_not_allowed"}
        if "receive" in route:
            light_num = _int(payload.get("lightNum", payload.get("light_num")))
            if light_num not in JIGSAW_REWARDS: return 400, {"error": "invalid_light_num"}
            if light_num in received: return 200, {"resChange": []}
            if len(cells) < (light_num or 30): return 409, {"error": "jigsaw_prize_not_ready"}
            changes = grant_rewards(self.game, profile, JIGSAW_REWARDS[light_num]); received.add(light_num); data["received"] = sorted(received); self._save(profile, state); return 200, {"resChange": changes}
        if route in {"/fci/jigsawpuzzle", "/fci/jigsaw-puzzle"}:
            data["visited"] = True; self._save(profile, state); return 200, {"isFirstEnter": False, "resChange": []}
        cell_id = _int(payload.get("cellId", payload.get("cell_id")))
        if not 1 <= cell_id <= 30: return 400, {"error": "invalid_cell_id"}
        if cell_id in cells:
            changes = self._complete_jigsaw(profile, state, received) if len(cells) == 30 and 0 not in received else []
            return 200, {"resChange": changes}
        ok, changes, error = self._consume(profile, {"type": 11, "id": 1016, "param": 0, "count": 25})
        if not ok: return 409, {"error": error or "jigsaw_cost_not_enough"}
        cells.add(cell_id); data["cells"] = sorted(cells)
        if len(cells) == 30:
            changes.extend(self._complete_jigsaw(profile, state, received))
        self._save(profile, state)
        return 200, {"resChange": changes}

    def _complete_jigsaw(self, profile, state, received):
        if 0 in received:
            return []
        changes = grant_rewards(self.game, profile, JIGSAW_REWARDS[0])
        received.add(0)
        state["welfare"]["jigsaw"]["received"] = sorted(received)
        self._save(profile, state)
        notifier = getattr(self.game, "base_push_notifier", None)
        if callable(notifier):
            self.game.after_commit(lambda: notifier(profile, 3, ["31014"]))
        return changes

    def _theme(self, method: str, route: str, profile: Mapping[str, Any]) -> Response:
        # Native TaskUtil derives this list from GET /fci/task and pushes.
        # Keep the compatibility read consistent with those real tasks; all
        # claims must go through LocalTasks' ownership/completion checks.
        if method != "GET":
            return 405, {"error": "method_not_allowed"}
        if route not in {"/fci/themeactivitytasks", "/fci/theme-activity-tasks"}:
            return 404, {"error": "unknown_task_route"}
        return 200, [row for row in self.game.tasks.snapshot(profile)["tasks"]
                     if _int(row.get("category")) == 102]

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = _route(path)
        if not route.startswith(("/fci/seal", "/fci/chipexchange", "/fci/postcard", "/fci/jigsawpuzzle", "/fci/jigsaw-puzzle", "/fci/themeactivitytasks", "/fci/theme-activity-tasks")):
            return None
        with self.game.atomic_mutation():
            return self._handle_http(method, route, body, profile)

    def _handle_http(self, method, route, body, profile):
        method = method.upper(); state = self._state(profile)
        if route.startswith("/fci/seal"):
            return self._seal(method, route, body, profile, state)
        if route.startswith("/fci/chipexchange"):
            return self._chips(method, route, body, profile, state)
        if route.startswith("/fci/postcard"):
            return self._postcard(method, route, body, profile, state)
        if route.startswith("/fci/jigsawpuzzle") or route.startswith("/fci/jigsaw-puzzle"):
            return self._jigsaw(method, route, body, profile, state)
        if route.startswith("/fci/themeactivitytasks") or route.startswith("/fci/theme-activity-tasks"):
            return self._theme(method, route, profile)
        return None


__all__ = ["LocalWelfareFeatures"]
