"""Fail closed for the native async arena until its dungeon config is known.

The real client's Tournament enter resource overlaps an older local PvE
compatibility route. Keep the native request from creating a guessed PvE run;
do not advertise activity type 5 or return a fabricated arena/robot payload.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory
from google.protobuf.message import DecodeError


ASYNC_PVP_ACTIVITY_TYPE = 5
_REQUEST_SCHEMAS = {
    "enter": "ApiTournamentEnter",
    "balance": "ApiTournamentBalance",
    "quit": "ApiTournamentQuit",
    "winreward": "ApiTournamentWinReward",
    "dungeonenter": "ApiTournamentDungeonEnter",
    "dungeonbalance": "ApiTournamentDungeonBalance",
    "dungeonquit": "ApiTournamentDungeonQuit",
}
_NATIVE_ONLY_ACTIONS = {"dungeonenter", "dungeonbalance", "dungeonquit", "winreward"}


class LocalTournament:
    def __init__(self, game):
        self.game = game

    def _request(self, action, body):
        if isinstance(body, Mapping):
            return body
        if not body:
            return {}
        try:
            payload = json.loads(body)
            return payload if isinstance(payload, dict) else {}
        except (ValueError, TypeError, UnicodeDecodeError):
            schema = _REQUEST_SCHEMAS.get(action)
            if schema is None:
                return {}
            try:
                request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(schema))()
                request.ParseFromString(body.encode("utf-8") if isinstance(body, str) else bytes(body))
                return json_format.MessageToDict(request)
            except (KeyError, TypeError, ValueError, DecodeError):
                return {}

    def handles(self, path, body=None):
        route = urlsplit(path).path.rstrip("/").lower()
        if route == "/fci/tournament/summary":
            return True
        if route == "/fci/asyncpvpmatch":
            return True
        if re.fullmatch(r"/fci/asyncpvptargetdetail/[^/]+", route):
            return True
        if re.fullmatch(r"/fci/tournamentdungeon/\d+/active", route):
            return True
        if re.fullmatch(r"/fci/tournament/(?:info|enter|cost)/5/\d+", route):
            return True
        match = re.fullmatch(r"/fci/tournament/([^/]+)", route)
        if match is None or match[1] not in _REQUEST_SCHEMAS:
            return False
        action = match[1]
        payload = self._request(action, body)
        activity_type = payload.get("activityType", payload.get("activity_type"))
        if activity_type is not None:
            # Explicit other activity types belong to their existing handlers.
            return str(activity_type) == str(ASYNC_PVP_ACTIVITY_TYPE)
        # These names are native arena resources, while plain /enter, /quit
        # and /balance without native fields retain the local PvE seam.
        return action in _NATIVE_ONLY_ACTIONS or "tournamentId" in payload or "tournament_id" in payload

    def handle_http(self, method, path, body, profile):
        if not self.handles(path, body):
            return None
        route = urlsplit(path).path.rstrip("/").lower()
        if route == "/fci/tournament/summary":
            if method.upper() != "GET":
                return 405, {"error": "method_not_allowed"}
            return 200, {"activityInfo": []}
        return 501, {
            "error": "tournament_asyncpvp_not_configured",
            "activityType": ASYNC_PVP_ACTIVITY_TYPE,
            "dungeonConfigured": False,
            "message": "异步竞技场副本配置尚未确认，暂未开放。",
        }


__all__ = ["LocalTournament", "ASYNC_PVP_ACTIVITY_TYPE"]
