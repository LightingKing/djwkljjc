"""Native local implementation of the asynchronous tournament arena.

The selected dungeon/robot pool is a local D rule (4001, five deterministic
targets); native result payloads and state transitions follow the extracted
client contracts.
"""
from __future__ import annotations

from contextlib import closing
import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory
from google.protobuf.message import DecodeError

from grants import grant_rewards
from server.core.reset import policy_for

ASYNC_PVP_ACTIVITY_TYPE = 5
TOURNAMENT_ID = 5001
TOURNAMENT_DUNGEON_ID = 4001
TOURNAMENT_LEVEL_LIMIT = 23
TARGET_COUNT = 5
REFRESH_ITEM_ID = 80001
# Local lineups use distinct main characters with their real client-table
# attributes and skills.  Refresh rotates the lineup as well as its uid.
BOT_ACTOR_POOL = (1001, 1002, 1003, 1004, 1005, 1006)
_REQUEST_SCHEMAS = {
    "match": "ApiAsyncPvpMatch",
    "enter": "ApiTournamentEnter", "balance": "ApiTournamentBalance",
    "quit": "ApiTournamentQuit", "winreward": "ApiTournamentWinReward",
    "dungeonenter": "ApiTournamentDungeonEnter",
    "dungeonbalance": "ApiTournamentDungeonBalance",
    "dungeonquit": "ApiTournamentDungeonQuit", "active": "FightActiveData",
}


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


class TournamentError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class LocalTournament:
    def __init__(self, game):
        self.game = game
        with self.game._lock, closing(game._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS local_tournament_state (account_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, updated_at INTEGER NOT NULL)")

    def _request(self, action, body):
        if isinstance(body, Mapping):
            return dict(body)
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
            if value is None:
                return {}
            if not isinstance(value, dict):
                raise TournamentError("invalid_request", 400)
            return value
        except (UnicodeDecodeError, json.JSONDecodeError):
            schema = _REQUEST_SCHEMAS.get(action)
            if schema is None:
                return {}
            try:
                request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(schema))()
                request.ParseFromString(raw)
                return json_format.MessageToDict(request)
            except (KeyError, TypeError, ValueError, DecodeError) as exc:
                raise TournamentError("invalid_request", 400) from exc

    def handles(self, path, body=None):
        route = urlsplit(path).path.rstrip("/").lower()
        if route in ("/fci/tournament/summary", "/fci/asyncpvprecord", "/fci/asyncpvpmatch"):
            return True
        if route.startswith("/fci/asyncpvptargetdetail/"):
            return True
        if re.fullmatch(r"/fci/tournament/(?:info|enter|cost)/5/\d+", route):
            return True
        if re.fullmatch(r"/fci/tournamentdungeon/\d+/active", route):
            return True
        match = re.fullmatch(r"/fci/tournament/([^/]+)", route)
        if match is None or match[1] not in _REQUEST_SCHEMAS:
            return False
        try:
            payload = self._request(match[1], body)
        except TournamentError:
            # A malformed native payload must reach the route and become a
            # structured 400 instead of escaping from dispatch as a 500.
            return True
        activity_type = payload.get("activityType", payload.get("activity_type"))
        if activity_type is not None:
            return str(activity_type) == str(ASYNC_PVP_ACTIVITY_TYPE)
        return match[1] in {"dungeonenter", "dungeonbalance", "dungeonquit", "winreward"} or "tournamentId" in payload

    def _target(self, index, generation=0):
        uid = f"local-async-bot-{index + 1:03d}" + (f"-r{generation}" if generation else "")
        actors = self.game._types["DBGamePlayer.Actors"]()
        for slot in range(3):
            actor_id = BOT_ACTOR_POOL[(index + generation + slot) % len(BOT_ACTOR_POOL)]
            self.game._append_local_actor(actors, actor_id, 100000 + generation * 100 + index * 10 + slot,
                                          slot, level=TOURNAMENT_LEVEL_LIMIT)
        return {
            "uid": uid, "name": f"异步对手{index + 1}",
            "level": TOURNAMENT_LEVEL_LIMIT, "headPhoto": 1, "socialPicture": "",
            "playerId": uid, "isWin": False,
            "targetBattleActors": [json_format.MessageToDict(actor) for actor in actors.player_actors],
        }

    def _targets(self):
        return [self._target(index) for index in range(TARGET_COUNT)]

    def _default_state(self, now):
        return {"day": policy_for(self.game).day_index(now), "week": policy_for(self.game).week_index(now),
                "status": 0, "selectActorGroupId": 0, "wins": 0, "losses": 0,
                "refreshCount": 0, "claimed": [], "targets": self._targets(), "run": None}

    def _load(self, conn, profile, now):
        row = conn.execute("SELECT state_json FROM local_tournament_state WHERE account_id=?", (profile["account_id"],)).fetchone()
        state = json.loads(row["state_json"]) if row else self._default_state(now)
        state.setdefault("targets", self._targets()); state.setdefault("claimed", []); state.setdefault("run", None)
        day, week = policy_for(self.game).day_index(now), policy_for(self.game).week_index(now)
        if _int(state.get("day")) != day:
            state.update(day=day, wins=0, losses=0, refreshCount=0, claimed=[], run=None, targets=self._targets())
        # Repair already persisted placeholder teams without resetting wins,
        # claims, target identities, or an in-progress native battle.
        for index, target in enumerate(state["targets"]):
            if not target.get("targetBattleActors") or any(not actor.get("attrs") or not actor.get("skills")
                                                           for actor in target["targetBattleActors"]):
                target["targetBattleActors"] = self._target(index)["targetBattleActors"]
        state["week"] = week
        return state

    def _save(self, conn, profile, state, now):
        conn.execute("""INSERT INTO local_tournament_state(account_id,state_json,updated_at) VALUES(?,?,?)
                       ON CONFLICT(account_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at""",
                    (profile["account_id"], json.dumps(state, ensure_ascii=False, separators=(",", ":")), now))

    def _run(self, profile, callback):
        with self.game.atomic_mutation():
            with self.game._lock, closing(self.game._connect()) as conn:
                now = int(self.game._now()); state = self._load(conn, profile, now)
                result = callback(conn, state, now); self._save(conn, profile, state, now)
                return result

    def _record(self, state, now=None):
        wins, claimed = _int(state.get("wins")), {_int(v) for v in state.get("claimed", [])}
        now = int(self.game._now()) if now is None else int(now)
        return {"todayMaxWinNum": wins, "leftRewardsWinNum": [n for n in (1, 3, 5) if n <= wins and n not in claimed],
                "nextRefreshSyncpvpRecordTime": policy_for(self.game).next_boundary(now, "day"),
                "canRefresh": _int(state.get("refreshCount")) < 3}

    @staticmethod
    def _conf():
        return {"id": TOURNAMENT_ID, "weekIndex": 0, "levelLimit": TOURNAMENT_LEVEL_LIMIT,
                "dungeonId": TOURNAMENT_DUNGEON_ID, "startTimeStamp": 0, "endTimeStamp": 0,
                "preStartTimeStamp": 0, "reward": [
                    {"winNum": 1, "reward": {"type": 11, "id": 60001, "count": 1}},
                    {"winNum": 3, "reward": {"type": 11, "id": 60001, "count": 2}},
                    {"winNum": 5, "reward": {"type": 11, "id": 123003, "count": 1}},
                ], "rewardType": 0, "rewardId": 0, "endDieNum": 0, "winDropId": [], "failDropId": []}

    @staticmethod
    def _activity():
        return {"type": 5, "activityId": TOURNAMENT_ID, "timestampPreheat": 0, "timestampStart": 0,
                "timestampEnd": 0, "timestampClose": 0, "forever": True, "category": 3,
                "levelLimit": TOURNAMENT_LEVEL_LIMIT, "weekdays": []}

    def _consume_refresh_item(self, profile):
        state = self.game.systems._state(profile); entry = state.setdefault("items", {}).setdefault(str(REFRESH_ITEM_ID), {"count": 0})
        before = _int(entry.get("count"))
        if before < 1: raise TournamentError("insufficient_refresh_item")
        entry["count"] = before - 1; self.game.systems._save(profile, state)
        return [{"baseRes": {"type": 11, "id": REFRESH_ITEM_ID, "param": 0, "count": before - 1, "countDelta": -1}}]

    def _after_win(self, profile, state, response, rewards, record_tasks=True):
        if rewards: response["resChange"] = grant_rewards(self.game, profile, rewards)
        if not record_tasks:
            return
        try:
            self.game.tasks.on_battle_win(profile, TOURNAMENT_DUNGEON_ID, 0, mode="tournament", dungeon_type=3, activity_type=5, activity_id=TOURNAMENT_ID)
            self.game.tasks.record(profile, 35, amount=1)
            self.game.tasks.record(profile, 36, value=_int(state.get("wins")))
        except Exception:
            pass

    def handle_http(self, method, path, body, profile):
        if not self.handles(path, body): return None
        method, route = method.upper(), urlsplit(path).path.rstrip("/").lower()
        try:
            if route == "/fci/tournament/summary":
                if method != "GET": return 405, {"error": "method_not_allowed"}
                return self._run(profile, lambda c, s, n: (200, {"activityInfo": [{"activity": self._activity(), "status": _int(s.get("status")), "redPoint": 0}]}))
            if route == "/fci/asyncpvprecord":
                if method != "GET": return 405, {"error": "method_not_allowed"}
                return 200, self._run(profile, lambda c, s, n: self._record(s))
            if re.fullmatch(r"/fci/tournament/info/5/\d+", route):
                if method != "GET": return 405, {"error": "method_not_allowed"}
                if int(route.rsplit("/", 1)[-1]) != TOURNAMENT_ID: return 404, {"error": "tournament_not_found"}
                return 200, {"tournamentInfo": [{"tournamentId": TOURNAMENT_ID, "haveMysteryReward": True}], "tournamentConf": [self._conf()]}
            if re.fullmatch(r"/fci/tournament/cost/5/\d+", route):
                if method != "GET": return 405, {"error": "method_not_allowed"}
                if int(route.rsplit("/", 1)[-1]) != TOURNAMENT_ID: return 404, {"error": "tournament_not_found"}
                return 200, {"cost": {"type": 0, "id": 0, "param": 0, "count": 0}}
            if re.fullmatch(r"/fci/tournament/enter/5/\d+", route):
                if method != "GET": return 405, {"error": "method_not_allowed"}
                if int(route.rsplit("/", 1)[-1]) != TOURNAMENT_ID: return 404, {"error": "tournament_not_found"}
                return 200, self._run(profile, lambda c, s, n: {"selectActorGroupId": _int(s.get("selectActorGroupId")), "dieNum": _int(s.get("losses")), "winNum": _int(s.get("wins")), "asyncPvpTargetInfo": s["targets"], "asyncPvpRecord": self._record(s)})
            if route.startswith("/fci/asyncpvptargetdetail/"):
                if method != "GET": return 405, {"error": "method_not_allowed"}
                uid = route.rsplit("/", 1)[-1]
                target = self._run(profile, lambda c, s, n: self._target_detail(s, uid))
                # TournamentActorGroup.RequestTargetDetail reads response.uid
                # and response.targetBattleActors, with no .result unwrap.
                # Send the leaf message so the legacy dispatcher preserves its
                # type instead of wrapping it in ApiAsyncPvpTargetDetail.result.
                # Otherwise FightActorInfo[2].actors is nil and TLog crashes
                # before the native battle can start (real-client P0 evidence).
                cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(
                    "DBAsyncPvpBattleRecord.BattleRecord"))
                return 200, json_format.ParseDict(target, cls())
            if route == "/fci/asyncpvpmatch":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                payload = self._request("match", body)
                return 200, self._run(profile, lambda c, s, n: self._refresh_target(profile, s, payload))
            if route.endswith("/active") and re.fullmatch(r"/fci/tournamentdungeon/\d+/active", route):
                if method != "PATCH": return 405, {"error": "method_not_allowed"}
                payload = self._request("active", body); uid = int(route.split("/")[-2])
                return 200, self._run(profile, lambda c, s, n: self._save_active(s, uid, payload))
            if route == "/fci/tournament/dungeonenter":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                payload = self._request("dungeonenter", body)
                self._validate_tournament(payload)
                if _int(profile.get("level")) < TOURNAMENT_LEVEL_LIMIT: raise TournamentError("tournament_level_locked", 403)
                return 200, self._run(profile, lambda c, s, n: self._dungeon_enter(s, payload, n))
            if route == "/fci/tournament/dungeonbalance":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                return 200, self._run(profile, lambda c, s, n: self._dungeon_balance(profile, s, n))
            if route == "/fci/tournament/dungeonquit":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                return 200, self._run(profile, lambda c, s, n: self._dungeon_quit(s))
            if route == "/fci/tournament/winreward":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                payload = self._request("winreward", body); response = {"resChange": [], "asyncPvpRecord": {}}
                self._validate_tournament(payload)
                def claim(c, s, n):
                    target = _int(payload.get("winNum")); claimed = {_int(v) for v in s.get("claimed", [])}
                    if target not in (1, 3, 5) or _int(s.get("wins")) < target: raise TournamentError("win_reward_locked")
                    if target in claimed: raise TournamentError("win_reward_got")
                    s.setdefault("claimed", []).append(target); reward = next(r["reward"] for r in self._conf()["reward"] if r["winNum"] == target)
                    response["asyncPvpRecord"] = self._record(s)
                    self.game.after_commit(lambda: self._after_win(profile, s, response, [reward], record_tasks=False))
                    return response
                return 200, self._run(profile, claim)
            if route == "/fci/tournament/enter":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                payload = self._request("enter", body)
                return 200, self._run(profile, lambda c, s, n: self._enter(profile, s, payload))
            if route == "/fci/tournament/quit":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                return 200, self._run(profile, lambda c, s, n: self._dungeon_quit(s))
            if route == "/fci/tournament/balance":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                return 200, {"resChange": []}
        except TournamentError as exc:
            return exc.status, {"error": exc.code}
        return None

    @staticmethod
    def _validate_tournament(payload):
        if _int(payload.get("activityType", payload.get("activity_type")), 5) != 5: raise TournamentError("activity_type_mismatch", 400)
        if _int(payload.get("tournamentId", payload.get("tournament_id"))) != TOURNAMENT_ID:
            raise TournamentError("tournament_not_found", 404)

    def _enter(self, profile, state, payload):
        self._validate_tournament(payload)
        if _int(profile.get("level")) < TOURNAMENT_LEVEL_LIMIT: raise TournamentError("tournament_level_locked", 403)
        state["status"] = 1; return {"enterResult": True, "resChange": []}

    def _target_detail(self, state, uid):
        for target in state["targets"]:
            if target["uid"] == uid: return target
        raise TournamentError("target_not_found", 404)

    def _refresh_target(self, profile, state, payload):
        # Proto3 omits the zero-valued index, so an empty ApiAsyncPvpMatch
        # wire body is the native request for slot 0.
        index = _int(payload.get("index"), 0)
        if not 0 <= index < len(state["targets"]): raise TournamentError("invalid_target_index", 400)
        target = state["targets"][index]
        if target.get("isWin"): raise TournamentError("tournament_target_defeated")
        if (state.get("run") or {}).get("active") and state["run"].get("target_uid") == target["uid"]:
            raise TournamentError("tournament_run_active")
        if _int(state.get("refreshCount")) >= 3: raise TournamentError("refresh_limit")
        changes = self._consume_refresh_item(profile); state["refreshCount"] = _int(state.get("refreshCount")) + 1
        replacement = self._target(index, state["refreshCount"])
        state["targets"][index] = replacement
        return {"index": index, "targetActor": replacement, "resChange": changes}

    def _dungeon_enter(self, state, payload, now):
        target = self._target_detail(state, str(payload.get("target_uid", payload.get("targetUid", ""))))
        if target.get("isWin"): raise TournamentError("tournament_target_defeated")
        run = state.get("run") or {}
        if run.get("active"):
            # Native clients can retry/re-enter after a lost response or a
            # disconnect, including an old run with an empty parsed snapshot.
            if run.get("target_uid") == target["uid"]:
                return {"dungeonUid": run["uid"], "dungeonId": TOURNAMENT_DUNGEON_ID}
            raise TournamentError("tournament_run_active")
        uid = max(1000000000001, _int(run.get("uid")) + 1, now * 1000)
        state["run"] = {"uid": uid, "target_uid": target["uid"], "dungeon_id": TOURNAMENT_DUNGEON_ID, "active": True, "snapshot": None, "balanced": False, "win": False}
        return {"dungeonUid": uid, "dungeonId": TOURNAMENT_DUNGEON_ID}

    def _save_active(self, state, uid, payload):
        run = state.get("run")
        if not run or _int(run.get("uid")) != uid: raise TournamentError("tournament_run_not_found", 404)
        if run.get("balanced") or not run.get("active"): raise TournamentError("tournament_run_closed")
        if _int(payload.get("winTeam", payload.get("win_team"))) not in (1, 2):
            raise TournamentError("tournament_result_required", 400)
        if _int(payload.get("dungeonType"), 3) != 3 or _int(payload.get("stageID"), TOURNAMENT_DUNGEON_ID) != TOURNAMENT_DUNGEON_ID:
            raise TournamentError("tournament_snapshot_mismatch", 400)
        if run.get("snapshot") and run["snapshot"] != payload:
            raise TournamentError("tournament_result_already_saved")
        run["snapshot"] = dict(payload)
        return {}

    def _dungeon_balance(self, profile, state, now):
        run = state.get("run")
        if not run: raise TournamentError("tournament_run_not_found", 404)
        if run.get("balanced"): return run["response"]
        if not run.get("active"): raise TournamentError("tournament_run_closed")
        if not run.get("snapshot"):
            raise TournamentError("tournament_snapshot_required")
        win = _int((run.get("snapshot") or {}).get("winTeam", (run.get("snapshot") or {}).get("win_team"))) == 1
        run.update(active=False, balanced=True, win=win)
        for target in state.get("targets", []):
            if target.get("uid") == run.get("target_uid"):
                target["isWin"] = win
                break
        if win: state["wins"] = min(TARGET_COUNT, _int(state.get("wins")) + 1)
        else: state["losses"] = _int(state.get("losses")) + 1
        response = {"balanceInfo": {"balanced": True, "balanceTime": now, "win": win, "targetsDone": [], "resChange": [], "profits": []}, "dieNum": _int(state.get("losses")), "winNum": _int(state.get("wins"))}
        run["response"] = response
        if win: self.game.after_commit(lambda: self._after_win(profile, state, {"resChange": []}, []))
        return response

    @staticmethod
    def _dungeon_quit(state):
        if not state.get("run"): raise TournamentError("tournament_run_not_found", 404)
        state["run"]["active"] = False
        return {"result": True}


__all__ = ["LocalTournament", "ASYNC_PVP_ACTIVITY_TYPE"]
