"""Small routes whose reply shape the original client reads directly.

Each entry below was found by auditing the decompiled client Lua against the
server's actual replies: the route was either unanswered (501), answered by a
broad prefix handler with the wrong message, or answered with a typed wrapper
the client stores as-is.  They are answered here, before the feature modules.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

_CLIENT_TABLE_DIR = Path(__file__).resolve().parent / "data" / "client-tables"


def _rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_CLIENT_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


# RefreshPrice rows by type, ordered by purchase count (count = nth purchase).
_REFRESH_PRICE: dict[int, list[dict[str, Any]]] = {}
for _row in _rows("RefreshPrice"):
    _REFRESH_PRICE.setdefault(int(_row.get("type", 0) or 0), []).append(_row)
for _list in _REFRESH_PRICE.values():
    _list.sort(key=lambda row: int(row.get("count", 0) or 0))


def _resource(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {"type": 0, "id": 0, "count": 0}
    return {"type": int(value.get("type", 0) or 0), "id": int(value.get("id", 0) or 0),
            "count": int(value.get("count", 0) or 0)}


# Plain replies with no player state behind them locally.
_STATIC: dict[str, Any] = {
    # Window_City bulletin button reads response.activities.
    "/fci/webactivity": {"activities": []},
    # PushService/SceneFight store these maps as received.
    "/fci/event-dungeon/record": {},
    # ApiDeployMission.get: no deploy mission row exists locally (the typed
    # reply carried one empty entry the client rendered as a mission).
    "/fci/deploy/mission": {"missionInfo": [], "accomplishNum": 0},
    # Only read while a CultivationPvP activity is open.
    "/fci/challenge-mode/reward": {"scoreRecord": {}, "rewardRecords": []},
}


# Stateful routes answered by LocalEconomy (economy.py) ahead of the broad
# prefix handlers: the per-type daily RefreshPrice counter (GET price / POST
# fci/resbuyprice/resbuy/) and the power recovery state the HUD polls.
_ECONOMY_ROUTES = ("/fci/resbuyprice", "/fci/baseresource/update")


def handle_http(game: Any, method: str, path: str, body: Any, profile: Mapping[str, Any]) -> Optional[tuple[int, Any]]:
    route = (urlsplit(path or "").path or "/").rstrip("/").lower()
    economy = getattr(game, "economy", None)
    if economy is not None and (route == _ECONOMY_ROUTES[1] or route.startswith(_ECONOMY_ROUTES[0] + "/")):
        result = economy.handle_http(method, path, body, profile)
        if result is not None:
            return result
    if method.upper() != "GET":
        return None
    if route in _STATIC:
        value = _STATIC[route]
        return 200, json.loads(json.dumps(value))
    if route.startswith("/fci/resbuyprice/"):
        # Stateless fallback (no economy module): the first price from the
        # client RefreshPrice table (e.g. ChgName 10007 = 200 迷子).
        parts = route.split("/")
        try:
            price_type = int(parts[3])
        except (IndexError, ValueError):
            return 404, {"error": "unknown_price_type"}
        rows = _REFRESH_PRICE.get(price_type, [])
        if not rows:
            return 200, {"cost": {"type": 0, "id": 0, "count": 0}, "buy": {"type": 0, "id": 0, "count": 0}, "remainBuyTimes": 0}
        first = rows[0]
        return 200, {"cost": _resource(first.get("cost")), "buy": _resource(first.get("buy")), "remainBuyTimes": len(rows)}
    return None


def unwrap_reply(method: str, path: str, result: Any) -> Any:
    """Return the plain JSON the client stores for wrapped typed replies.

    GET fci/mail/: MailUtil.OnMails reads message.mails/clearAll/delMails at
    the top level, so ApiMail.get{get: ScMails} becomes the ScMails object.
    GET fci/RoleNotePad/: records are looked up per role, so the single
    ApiRoleNotePad.result{role_notepad} becomes a list.
    """

    if method.upper() != "GET" or not isinstance(result, tuple) or len(result) != 2:
        return result
    status, payload = result
    if status != 200 or not hasattr(payload, "DESCRIPTOR"):
        return result
    route = (urlsplit(path or "").path or "/").rstrip("/").lower()
    from google.protobuf import json_format

    def as_dict(message: Any) -> dict[str, Any]:
        try:
            return json_format.MessageToDict(message, always_print_fields_with_no_presence=True)
        except TypeError:  # older protobuf
            return json_format.MessageToDict(message, including_default_value_fields=True)

    name = payload.DESCRIPTOR.full_name
    if route == "/fci/mail" and name == "ApiMail.get":
        mails = as_dict(payload.get)
        mails.setdefault("mails", [])
        mails.setdefault("delMails", [])
        mails.setdefault("clearAll", False)
        return status, mails
    if route == "/fci/rolenotepad" and name == "ApiRoleNotePad.result":
        record = payload.role_notepad
        return status, ([as_dict(record)] if record.ListFields() else [])
    return result


__all__ = ["handle_http", "unwrap_reply"]
