"""Native local implementation of the asynchronous tournament arena.

The selected dungeon/robot pool is a local D rule (4001, five deterministic
targets); native result payloads and state transitions follow the extracted
client contracts.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import random
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
# D rule: the original weekly drop table is unavailable. Alternate two equal
# pools from the native Role/ItemInfo fragments, drawing one fragment per day.
_TABLES = Path(__file__).parent / "data/client-tables"
_ITEMS = {row["id"]: row for row in json.loads((_TABLES / "ItemInfo.json").read_text(encoding="utf-8"))["rows"]}
FRAGMENT_ITEMS = tuple(sorted({row["decomposeRes"]["id"] for row in
    json.loads((_TABLES / "Role.json").read_text(encoding="utf-8"))["rows"]
    if row.get("decomposeRes", {}).get("type") == 11
    and _ITEMS.get(row["decomposeRes"]["id"], {}).get("funcType") == 3}))
WEEKLY_FRAGMENT_POOLS = (FRAGMENT_ITEMS[::2], FRAGMENT_ITEMS[1::2])
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
            # Native InitGameLogic.buildActorFromData clears a whole team
            # unless it has exactly DEFINE.ACTOR_NUM (six) seats. Empty
            # support seats still need STActor entries with id/uid zero.
            "targetBattleActors": [json_format.MessageToDict(actor) for actor in actors.player_actors]
                                  + self._empty_support_slots(),
        }

    @staticmethod
    def _empty_support_slots():
        return [{"id": 0, "uid": 0, "defaultIndex": slot, "attrs": [], "skills": []}
                for slot in range(3, 6)]

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
        reset = day > _int(state.get("day"))
        if reset:
            # A pause/connection loss can leave the native dungeon open over
            # the 06:00 reset.  Keep that one in-progress run long enough for
            # its result to be uploaded and balanced; only completed runs are
            # discarded with the daily record.
            run = state.get("run")
            carry_run = run if isinstance(run, dict) and run.get("active") and not run.get("balanced") else None
            if carry_run is not None:
                carry_run["crossedReset"] = True
            state.update(day=day, wins=0, losses=0, refreshCount=0, claimed=[],
                         run=carry_run, targets=self._targets())
            state.pop("fifthReward", None)
        # Repair already persisted placeholder teams without resetting wins,
        # claims, target identities, or an in-progress native battle.
        for index, target in enumerate(state["targets"]):
            actors = target.get("targetBattleActors") or []
            if len(actors) not in (3, 6) or any(not actor.get("attrs") or not actor.get("skills")
                                               for actor in actors if _int(actor.get("id"))):
                target["targetBattleActors"] = self._target(index)["targetBattleActors"]
            elif len(actors) == 3:
                # Already persisted real three-main lineups need only empty
                # seats, preserving their generation, skills and attributes.
                target["targetBattleActors"] = actors + self._empty_support_slots()
        state["week"] = max(_int(state.get("week")), week)
        if "fifthReward" not in state:
            pool = WEEKLY_FRAGMENT_POOLS[state["week"] % len(WEEKLY_FRAGMENT_POOLS)]
            # Stable per account/day, including rollback retries and restarts.
            # Persist the actual reward so preview and claim always agree.
            seed = hashlib.sha256(f"arena-v1:{profile['account_id']}:{state['day']}:{state['week']}".encode()).digest()
            state["fifthReward"] = {"type": 11, "id": random.Random(seed).choice(pool), "count": 1}
        if reset:
            self._notify(profile, 44)
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

    def _notify(self, profile, push_type, params=()):
        notifier = getattr(self.game, "base_push_notifier", None)
        if notifier is not None:
            self.game.after_commit(lambda: notifier(profile, push_type, params))

    def refresh_daily_record(self, profile):
        """Online calendar hook; _load owns the durable reset and push 44."""
        if _int(profile.get("level")) >= TOURNAMENT_LEVEL_LIMIT:
            return self._run(profile, lambda c, s, n: self._record(s, n))

    def _record(self, state, now=None):
        wins, claimed = _int(state.get("wins")), {_int(v) for v in state.get("claimed", [])}
        now = int(self.game._now()) if now is None else int(now)
        now = max(now, policy_for(self.game).day_start_from_index(state["day"]))
        return {"todayMaxWinNum": wins, "leftRewardsWinNum": [n for n in (1, 3, 5) if n <= wins and n not in claimed],
                "nextRefreshSyncpvpRecordTime": policy_for(self.game).next_boundary(now, "day"),
                "canRefresh": _int(state.get("refreshCount")) < 3}

    @staticmethod
    def _conf(state):
        return {"id": TOURNAMENT_ID, "weekIndex": state["week"] % len(WEEKLY_FRAGMENT_POOLS), "levelLimit": TOURNAMENT_LEVEL_LIMIT,
                "dungeonId": TOURNAMENT_DUNGEON_ID, "startTimeStamp": 0, "endTimeStamp": 0,
                "preStartTimeStamp": 0, "reward": [
                    {"winNum": 1, "reward": {"type": 11, "id": 60001, "count": 1}},
                    {"winNum": 3, "reward": {"type": 11, "id": 60001, "count": 2}},
                    {"winNum": 5, "reward": dict(state["fifthReward"])},
                ], "rewardType": 0, "rewardId": 0, "endDieNum": 0, "winDropId": [], "failDropId": []}

    @staticmethod
    def _activity():
        return {"type": 5, "activityId": TOURNAMENT_ID, "timestampPreheat": 0, "timestampStart": 0,
                "timestampEnd": 0, "timestampClose": 0, "forever": True, "category": 3,
                "levelLimit": TOURNAMENT_LEVEL_LIMIT, "weekdays": []}

    @staticmethod
    def _burst_activity():
        # BurstLink is a separate native tournament entry.  The client uses
        # the summary status to choose Join -> actor selection -> actor group;
        # omitting it leaves the menu stuck on the remote-only flow.
        return {"type": 18, "activityId": 18001, "timestampPreheat": 0, "timestampStart": 0,
                "timestampEnd": 0, "timestampClose": 0, "forever": True, "category": 3,
                "levelLimit": 28, "weekdays": []}

    def _burst_status(self, profile):
        state = self.game.realtime_pvp._state(profile)
        native = self.game.realtime_pvp._native(state)
        active = native.get("active")
        if isinstance(active, Mapping) and not active.get("balanced"):
            return 3  # TournamentStatus_Group_End
        if len(native.get("main") or []) + len(native.get("assistant") or []) >= 6:
            return 2  # TournamentStatus_Group
        if native.get("paid"):
            return 1  # TournamentStatus_Participation
        return 0  # TournamentStatus_NoParticipation

    def _consume_refresh_item(self, profile):
        state = self.game.systems._state(profile); entry = state.setdefault("items", {}).setdefault(str(REFRESH_ITEM_ID), {"count": 0})
        before = _int(entry.get("count"))
        if before < 1: raise TournamentError("insufficient_refresh_item")
        entry["count"] = before - 1; self.game.systems._save(profile, state)
        return [{"baseRes": {"type": 11, "id": REFRESH_ITEM_ID, "param": 0, "count": before - 1, "countDelta": -1}}]

    def _after_win(self, profile, state):
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
                return self._run(profile, lambda c, s, n: (200, {"activityInfo": [
                    {"activity": self._activity(), "status": _int(s.get("status")), "redPoint": 0},
                    {"activity": self._burst_activity(), "status": self._burst_status(profile), "redPoint": 0},
                ]}))
            if route == "/fci/asyncpvprecord":
                if method != "GET": return 405, {"error": "method_not_allowed"}
                return 200, self._run(profile, lambda c, s, n: self._record(s))
            if re.fullmatch(r"/fci/tournament/info/5/\d+", route):
                if method != "GET": return 405, {"error": "method_not_allowed"}
                if int(route.rsplit("/", 1)[-1]) != TOURNAMENT_ID: return 404, {"error": "tournament_not_found"}
                return 200, self._run(profile, lambda c, s, n: {
                    "tournamentInfo": [{"tournamentId": TOURNAMENT_ID, "haveMysteryReward": True}], "tournamentConf": [self._conf(s)]})
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
                return 200, self._run(profile, lambda c, s, n: self._dungeon_quit(s, n))
            if route == "/fci/tournament/winreward":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                payload = self._request("winreward", body); response = {"resChange": [], "asyncPvpRecord": {}}
                self._validate_tournament(payload)
                def claim(c, s, n):
                    target = _int(payload.get("winNum")); claimed = {_int(v) for v in s.get("claimed", [])}
                    if target not in (1, 3, 5) or _int(s.get("wins")) < target: raise TournamentError("win_reward_locked")
                    if target in claimed: raise TournamentError("win_reward_got")
                    s.setdefault("claimed", []).append(target)
                    reward = next(r["reward"] for r in self._conf(s)["reward"] if r["winNum"] == target)
                    response["resChange"] = grant_rewards(self.game, profile, [reward])
                    response["asyncPvpRecord"] = self._record(s)
                    self._notify(profile, 3, (reward["id"],))
                    return response
                return 200, self._run(profile, claim)
            if route == "/fci/tournament/enter":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                payload = self._request("enter", body)
                return 200, self._run(profile, lambda c, s, n: self._enter(profile, s, payload))
            if route == "/fci/tournament/quit":
                if method != "POST": return 405, {"error": "method_not_allowed"}
                return 200, self._run(profile, lambda c, s, n: self._dungeon_quit(s, n))
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
            # If the client moved to another opponent without first sending a
            # result (for example after a disconnected pause), close the
            # orphaned run as a loss.  This releases the arena without
            # fabricating a win and prevents the old snapshot from blocking
            # every subsequent opponent until the next reset.
            if not run.get("snapshot"):
                run.update(active=False, balanced=True, win=False,
                           response={"balanceInfo": {"balanced": True,
                                                     "balanceTime": now,
                                                     "win": False,
                                                     "targetsDone": [],
                                                     "resChange": [],
                                                     "profits": []},
                                      "dieNum": _int(state.get("losses")) + 1,
                                      "winNum": _int(state.get("wins"))})
                state["losses"] = _int(state.get("losses")) + 1
            else:
                raise TournamentError("tournament_run_active")
        uid = max(1000000000001, _int(run.get("uid")) + 1, now * 1000)
        state["run"] = {"uid": uid, "target_uid": target["uid"], "dungeon_id": TOURNAMENT_DUNGEON_ID, "active": True, "snapshot": None, "balanced": False, "win": False}
        return {"dungeonUid": uid, "dungeonId": TOURNAMENT_DUNGEON_ID}

    def _save_active(self, state, uid, payload):
        run = state.get("run")
        if not run or _int(run.get("uid")) != uid: raise TournamentError("tournament_run_not_found", 404)
        if run.get("balanced") or not run.get("active"): raise TournamentError("tournament_run_closed")
        # SceneFight sends winTeam=0 when the player exits/pauses before a
        # side has won. Proto3 omits both zero-valued fields, so a native
        # FightActiveData containing the non-zero dungeon/stage identity is
        # still a valid first-round pause even when gameRound and winTeam are
        # absent from the decoded mapping. An entirely empty message remains
        # malformed because it has no valid dungeon/stage identity.
        if "dungeonType" not in payload and "dungeon_type" not in payload:
            raise TournamentError("tournament_result_required", 400)
        if "stageID" not in payload and "stage_id" not in payload:
            raise TournamentError("tournament_result_required", 400)
        win_value = payload.get("winTeam", payload.get("win_team"))
        if win_value is None:
            win_value = 0
            payload["winTeam"] = 0
        if "gameRound" not in payload and "game_round" not in payload:
            payload["gameRound"] = 0
        if _int(win_value) not in (0, 1, 2):
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
        if win: self.game.after_commit(lambda: self._after_win(profile, state))
        return response

    @staticmethod
    def _dungeon_quit(state, now):
        if not state.get("run"): raise TournamentError("tournament_run_not_found", 404)
        run = state["run"]
        if run.get("balanced"):
            return {"result": True}
        # A direct quit is the native abandon path. Close the run now so a
        # subsequent opponent/refresh is not blocked by a stale active UID.
        # If an active result was already uploaded, retain that result; an
        # otherwise unreported quit is a loss and is made idempotent through
        # the cached balance response used by dungeonbalance.
        snapshot = run.get("snapshot") or {}
        win = _int(snapshot.get("winTeam", snapshot.get("win_team"))) == 1
        run.update(active=False, balanced=True, win=win)
        for target in state.get("targets", []):
            if target.get("uid") == run.get("target_uid"):
                target["isWin"] = win
                break
        if win:
            state["wins"] = min(TARGET_COUNT, _int(state.get("wins")) + 1)
        else:
            state["losses"] = _int(state.get("losses")) + 1
        run["response"] = {
            "balanceInfo": {"balanced": True, "balanceTime": now, "win": win,
                            "targetsDone": [], "resChange": [], "profits": []},
            "dieNum": _int(state.get("losses")), "winNum": _int(state.get("wins")),
        }
        return {"result": True}


__all__ = ["LocalTournament", "ASYNC_PVP_ACTIVITY_TYPE"]
