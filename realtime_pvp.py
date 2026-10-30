"""Deterministic local RacePVP and BurstLink compatibility.

The retired service used a WebSocket/FightService for these modes.  The
offline build cannot recover that service, so this module exposes the same
player-facing lifecycle as a small authenticated JSON state machine:

``info -> match -> round* -> balance``

Every match is seeded from the local account and match number.  Robot turns,
rank order, rewards and retry responses are therefore stable across process
restarts.  No external service, real player, or anti-cheat component is
contacted.  The route is intentionally separate from :mod:`social` so async
PVP and these round based modes cannot share or corrupt one another's state.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

try:  # Package import when local-server is installed as a package.
    from .storage.compat import shared_process_lock
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from storage.compat import shared_process_lock


Body = bytes | bytearray | str | None
Response = tuple[int, Any]


def _json(body: Body) -> dict[str, Any]:
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


class LocalRealtimePvp:
    """Persistent, deterministic local implementation for real-time modes."""

    RACE_ROUNDS = 8
    RACE_PLAYERS = 5
    RACE_TICKETS = 6
    BURST_ROUNDS = 5
    BURST_PLAYERS = 3
    BURST_TICKETS = 1

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_realtime_pvp_state (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _now(self) -> int:
        return int(self._clock())

    def _day_key(self) -> str:
        # The client refreshes daily content at 06:00.  The +6h bucket makes
        # the boundary deterministic while remaining independent of host TZ.
        return datetime.fromtimestamp(self._now() + 6 * 3600, timezone.utc).date().isoformat()

    def _next_reset(self) -> int:
        current = datetime.fromtimestamp(self._now(), timezone.utc)
        boundary = current.replace(hour=6, minute=0, second=0, microsecond=0)
        if boundary.timestamp() <= self._now():
            boundary += timedelta(days=1)
        return int(boundary.timestamp())

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "race": {"day": "", "tickets": LocalRealtimePvp.RACE_TICKETS,
                     "rating": 1000, "wins": 0, "losses": 0,
                     "nextMatch": 1, "matches": [], "active": None},
            "burst": {"day": "", "tickets": LocalRealtimePvp.BURST_TICKETS,
                      "wins": 0, "losses": 0, "nextMatch": 1,
                      "matches": [], "active": None},
        }

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM local_realtime_pvp_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                state = self._default_state()
                connection.execute(
                    "INSERT INTO local_realtime_pvp_state(account_id,state_json,updated_at) VALUES (?,?,?)",
                    (account_id, json.dumps(state, separators=(",", ":")), self._now()),
                )
            else:
                try:
                    state = json.loads(row["state_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    state = self._default_state()
            if not isinstance(state, dict):
                state = self._default_state()
            defaults = self._default_state()
            for mode, default in defaults.items():
                value = state.get(mode)
                if not isinstance(value, dict):
                    state[mode] = dict(default)
                    continue
                for key, fallback in default.items():
                    value.setdefault(key, fallback)
            self._refresh_tickets(state)
            return state

    def _refresh_tickets(self, state: dict[str, Any]) -> None:
        day = self._day_key()
        for mode, maximum in (("race", self.RACE_TICKETS), ("burst", self.BURST_TICKETS)):
            bucket = state[mode]
            if bucket.get("day") != day:
                bucket["day"] = day
                bucket["tickets"] = maximum

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_realtime_pvp_state SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), ensure_ascii=False, separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    @staticmethod
    def _route(path: str) -> str:
        return urlsplit(path or "").path.rstrip("/").casefold() or "/"

    @staticmethod
    def _digest_int(*values: object, modulo: int = 10_000) -> int:
        raw = "|".join(str(value) for value in values).encode("utf-8")
        return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % modulo

    def _bot(self, account_id: int, match_no: int, index: int, *, burst: bool = False) -> dict[str, Any]:
        names = ("御坂训练员", "黑雪姬", "桐人练习生", "亚丝娜助手", "司波机器人")
        player_id = f"local-{'burst' if burst else 'race'}-bot-{index:02d}"
        return {
            "playerId": player_id,
            "name": names[(account_id + match_no + index) % len(names)],
            "level": 1 + self._digest_int(account_id, match_no, index, "level", modulo=45),
            "actorIds": [1001 + self._digest_int(account_id, match_no, index, n, modulo=8) for n in range(3)],
            "isRobot": True,
            "score": 0,
        }

    def _player(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        actor_ids = [int(value) for value in profile.get("actor_ids", []) if str(value).isdigit()]
        if not actor_ids and profile.get("actor_id") is not None:
            actor_ids = [int(profile["actor_id"])]
        return {
            "playerId": str(profile.get("player_id", "local")),
            "name": str(profile.get("name", "本地玩家")) or "本地玩家",
            "level": int(profile.get("level", 1)),
            "actorIds": actor_ids[:3] or [1001],
            "isRobot": False,
            "score": 0,
        }

    def _summary(self, mode: str, bucket: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "mode": mode,
            "tickets": int(bucket.get("tickets", 0)),
            "maxTickets": self.RACE_TICKETS if mode == "race" else self.BURST_TICKETS,
            "nextResetAt": self._next_reset(),
            "rating": int(bucket.get("rating", 1000)),
            "wins": int(bucket.get("wins", 0)),
            "losses": int(bucket.get("losses", 0)),
            "activeMatchId": (bucket.get("active") or {}).get("matchId") if isinstance(bucket.get("active"), dict) else None,
            "matches": list(bucket.get("matches", []))[-20:],
        }

    def _info(self, mode: str, state: dict[str, Any]) -> Response:
        return 200, self._summary(mode, state[mode])

    def _new_match(self, mode: str, profile: Mapping[str, Any], state: dict[str, Any], payload: Mapping[str, Any]) -> Response:
        bucket = state[mode]
        self._refresh_tickets(state)
        active = bucket.get("active")
        if isinstance(active, dict) and active.get("status") in {"matching", "running"}:
            return 200, {"ok": True, "created": False, "match": active, "retry": True}
        if int(bucket.get("tickets", 0)) <= 0:
            return 409, {"error": "ticket_exhausted", "mode": mode, "nextResetAt": self._next_reset()}
        match_no = int(bucket.get("nextMatch", 1))
        bucket["nextMatch"] = match_no + 1
        account_id = int(profile["account_id"])
        burst = mode == "burst"
        player_count = self.BURST_PLAYERS if burst else self.RACE_PLAYERS
        participants = [self._player(profile)] + [self._bot(account_id, match_no, i, burst=burst) for i in range(1, player_count)]
        match_id = f"local-{mode}-{account_id}-{match_no:04d}"
        match = {
            "matchId": match_id,
            "mode": mode,
            "status": "running",
            "round": 0,
            "rounds": self.BURST_ROUNDS if burst else self.RACE_ROUNDS,
            "participants": participants,
            "actions": [],
            "createdAt": self._now(),
            "seed": self._digest_int(account_id, match_no, mode, modulo=2**31 - 1),
            "result": None,
            "settled": False,
        }
        bucket["tickets"] = int(bucket.get("tickets", 0)) - 1
        bucket["active"] = match
        self._save(profile, state)
        return 200, {"ok": True, "created": True, "match": match, "tickets": bucket["tickets"]}

    @staticmethod
    def _action_points(action: str, actor_count: int, round_no: int) -> int:
        values = {"attack": 12, "skill": 16, "defend": 8, "support": 10, "wait": 5, "auto": 9}
        return 100 + values.get(action.casefold(), 9) + actor_count * 2 + round_no

    def _advance(self, mode: str, profile: Mapping[str, Any], state: dict[str, Any], match: dict[str, Any], payload: Mapping[str, Any]) -> Response:
        expected = int(match.get("round", 0)) + 1
        try:
            round_no = int(payload.get("round", expected))
        except (TypeError, ValueError):
            return 400, {"error": "invalid_round", "expectedRound": expected}
        if round_no < expected:
            previous = next((row for row in match.get("actions", []) if int(row.get("round", -1)) == round_no), None)
            return 200, {"ok": True, "duplicate": True, "round": previous or round_no, "match": match}
        if round_no > expected:
            return 409, {"error": "round_out_of_order", "expectedRound": expected, "currentRound": match.get("round", 0)}
        player = match["participants"][0]
        action = str(payload.get("action", payload.get("skill", "auto"))).strip().casefold() or "auto"
        if action not in {"attack", "skill", "defend", "support", "wait", "auto"}:
            return 400, {"error": "invalid_action", "allowed": ["attack", "skill", "defend", "support", "wait", "auto"]}
        player_score = self._action_points(action, len(player.get("actorIds", [])), round_no)
        scores = [{"playerId": player["playerId"], "action": action, "score": player_score, "isSelf": True}]
        for index, bot in enumerate(match["participants"][1:], 1):
            bot_action = ("attack", "skill", "defend", "support")[self._digest_int(match["seed"], round_no, index, modulo=4)]
            bot_score = self._action_points(bot_action, len(bot.get("actorIds", [])), round_no) + self._digest_int(match["seed"], round_no, index, "bonus", modulo=8)
            scores.append({"playerId": bot["playerId"], "action": bot_action, "score": bot_score, "isSelf": False})
        for item in match["participants"]:
            found = next((row for row in scores if row["playerId"] == item["playerId"]), None)
            if found is not None:
                item["score"] = int(item.get("score", 0)) + int(found["score"])
        match["round"] = round_no
        match["actions"].append({"round": round_no, "scores": scores})
        if round_no >= int(match["rounds"]):
            match["status"] = "finished"
            ordered = sorted(match["participants"], key=lambda item: (-int(item.get("score", 0)), str(item["playerId"])))
            for rank, item in enumerate(ordered, 1):
                item["rank"] = rank
            player_rank = next(item["rank"] for item in ordered if item["playerId"] == player["playerId"])
            match["result"] = {"rank": player_rank, "winner": player_rank == 1, "scores": [{"playerId": item["playerId"], "score": item["score"], "rank": item["rank"]} for item in ordered]}
        self._save(profile, state)
        return 200, {"ok": True, "duplicate": False, "round": round_no, "scores": scores, "match": match}

    def _settle(self, mode: str, profile: Mapping[str, Any], state: dict[str, Any], match: dict[str, Any]) -> Response:
        if match.get("status") != "finished":
            while int(match.get("round", 0)) < int(match.get("rounds", 0)):
                status, _ = self._advance(mode, profile, state, match, {"round": int(match.get("round", 0)) + 1, "action": "auto"})
                if status != 200:
                    return status, {"error": "auto_finish_failed"}
        if match.get("settled"):
            return 200, {"ok": True, "duplicate": True, "settlement": match.get("settlement", {})}
        result = match.get("result") or {}
        won = bool(result.get("winner", False))
        bucket = state[mode]
        if won:
            bucket["wins"] = int(bucket.get("wins", 0)) + 1
        else:
            bucket["losses"] = int(bucket.get("losses", 0)) + 1
        if mode == "race":
            bucket["rating"] = max(0, int(bucket.get("rating", 1000)) + (25 if won else -15))
        reward = {"type": 7, "id": 0, "param": 0, "count": 100 if won else 40}
        changes: list[dict[str, Any]] = []
        try:
            ok, changes, error = self.game.economy._change_resources(profile, [reward])
        except (AttributeError, TypeError, ValueError):
            ok, error = False, "resource_ledger_unavailable"
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        settlement = {"win": won, "rank": int(result.get("rank", 0)), "reward": reward, "resChange": changes, "rating": int(bucket.get("rating", 1000))}
        match["settled"] = True
        match["settlement"] = settlement
        bucket.setdefault("matches", []).append({"matchId": match["matchId"], **settlement, "finishedAt": self._now()})
        bucket["matches"] = bucket["matches"][-50:]
        bucket["active"] = None
        self._save(profile, state)
        return 200, {"ok": True, "duplicate": False, "settlement": settlement}

    def _match_route(self, route: str) -> tuple[str, str | None, str | None]:
        mode = "burst" if "burstlink" in route or "burst-link" in route else "race"
        action = None
        if any(token in route for token in ("/balance", "/settle", "/result", "/finish")):
            action = "settle"
        elif any(token in route for token in ("/round", "/turn", "/action", "/battle")):
            action = "round"
        elif any(token in route for token in ("/match", "/join", "/start", "/matching")):
            action = "match"
        match_id = None
        match_match = re.search(r"/(?:match|room|dungeon)/([^/]+)", route)
        if match_match:
            candidate = match_match.group(1)
            if candidate not in {"info", "list", "record", "rank", "score", "summary"}:
                match_id = candidate
        return mode, action, match_id

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = self._route(path)
        if not any(token in route for token in ("racepvp", "race-pvp", "burstlink", "burst-link")):
            return None
        # ``_state`` followed by a JSON update is one logical transaction.
        # The thread lock alone cannot protect a second local worker process.
        with shared_process_lock(self.game.db_path), self._lock:
            # The bootstrap descriptor calls this exact capitalized endpoint.
            if route == "/fci/burstlinkpvpinfo":
                mode = "burst"
                return self._info(mode, self._state(profile))
            state = self._state(profile)
            mode, action, match_id = self._match_route(route)
            bucket = state[mode]
            if method.upper() == "GET" and action is None:
                return self._info(mode, state)
            if method.upper() == "GET" and action == "match":
                active = bucket.get("active")
                if isinstance(active, dict) and (match_id is None or active.get("matchId") == match_id):
                    return 200, {"ok": True, "match": active, "summary": self._summary(mode, bucket)}
                return 404, {"error": "match_not_found", "matchId": match_id}
            if method.upper() == "GET" and any(token in route for token in ("/record", "/rank", "/score", "/summary")):
                return 200, self._summary(mode, bucket)
            if method.upper() != "POST":
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            payload = _json(body)
            if action == "match":
                return self._new_match(mode, profile, state, payload)
            active = bucket.get("active")
            if not isinstance(active, dict):
                if action == "settle" and match_id:
                    previous = next((row for row in bucket.get("matches", []) if row.get("matchId") == match_id), None)
                    if isinstance(previous, dict):
                        settlement = {key: value for key, value in previous.items() if key not in {"matchId", "finishedAt"}}
                        return 200, {"ok": True, "duplicate": True, "settlement": settlement}
                return 409, {"error": "no_active_match", "mode": mode}
            if match_id and active.get("matchId") != match_id:
                return 404, {"error": "match_not_found", "matchId": match_id}
            if action == "round":
                return self._advance(mode, profile, state, active, payload)
            if action == "settle":
                return self._settle(mode, profile, state, active)
            # A bare POST to the mode route is treated as match search, which
            # mirrors the client's first click on RacePVP/BurstLink join.
            return self._new_match(mode, profile, state, payload)


__all__ = ["LocalRealtimePvp"]
