"""Local weekly RacePVP rooms and the legacy BurstLink simulation.

Race uses the real client HTTP battle lifecycle and extracted config tables.
Its local policy is explicit: one player plus 19 deterministic robot records
per weekly room, rotating stages 3001-3004, six daily tickets, and synchronous
weekly matching/settlement. Robot scores are fixed within a week and seeded
from the account/week. Battle score is enemy TeamStatistic.totalDamage // 10,
matching Window_FightRound.AddPtVal. Segment/season reward timing was not
recovered; those tables are exposed as data, with no invented payouts.

The retired service used a WebSocket/FightService for these modes.  The
offline build cannot recover that service, so BurstLink still exposes the same
player-facing lifecycle as a small authenticated JSON state machine:

``info -> match -> round* -> balance``

Every match is seeded from the local account and match number.  Robot turns,
rank order, rewards and retry responses are therefore stable across process
restarts.  No external service, real player, or anti-cheat component is
contacted.  The route is intentionally separate from :mod:`social` so async
PVP and these round based modes cannot share or corrupt one another's state.
"""

from __future__ import annotations

from server.core.reset import policy_for, period_due

import hashlib
import json
import random
import re
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory

try:  # Package import when local-server is installed as a package.
    from .storage.compat import shared_process_lock
    from .economy import _client_rows
    from .grants import grant_rewards
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from storage.compat import shared_process_lock
    from economy import _client_rows
    from grants import grant_rewards


Body = bytes | bytearray | str | None
Response = tuple[int, Any]


def _int(value: Any, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


class RaceError(Exception):
    def __init__(self, error, status=409):
        self.error, self.status = error, status


class LocalRacePvp:
    """Persistent client-shaped room, ticket, run and reward state."""

    # activity_modes owns 9e12; ordinary dungeons use current epoch ms (~1e12).
    UID_BASE = 8_000_000_000_000
    TICKET_ID = 80004
    ACTIVITY_TYPE = 20
    # RacePVPStatus from descriptor_set.pb. Local matching/settlement happen
    # in the same transaction; the playable room is published in Fight.
    NONE, MATCH, FIGHT, SETTLE = 0, 1, 2, 3
    MISC = _client_rows("Misc")[0]
    PLAYER_COUNT = int(MISC["racePVPGroupMaxPlayer"])
    MAX_ROUNDS = int(MISC["racePVPRoundNum"])
    DAILY_TICKETS = int(MISC["racePVPTicketNum"])
    SEGMENTS = sorted(_client_rows("RacePVPSeg"), key=lambda row: row["score"])
    RANK_SCORES = {int(row["rank"]): int(row.get("score", 0)) for row in _client_rows("RacePVPRankScore")}
    SCORE_REWARDS = {int(row["battleScore"]): row["rewards"] for row in _client_rows("RacePVPBattleScoreReward")}
    HEAD_PHOTOS = frozenset(int(row["id"]) for row in _client_rows("PlayerHeadPhoto"))
    STAGES = {int(row["dungeonId"]): row for row in _client_rows("DungeonReward")
              if _int(row.get("type")) == 7 and 3001 <= _int(row.get("dungeonId")) <= 3004}
    ROUTES = {"/fci/racepvpinfo", "/fci/racepvpsummary", "/fci/racepvpbattlescorereward",
              "/fci/racepvpscore", "/fci/racepvp/info", "/fci/racepvp-dungeon"}

    def __init__(self, game, clock=None):
        self.game, self._clock = game, clock or game._now
        with game._lock, closing(game._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS local_race_pvp_state "
                         "(account_id INTEGER PRIMARY KEY,state_json TEXT NOT NULL,updated_at INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS local_race_pvp_runs "
                         "(dungeon_uid INTEGER PRIMARY KEY,account_id INTEGER NOT NULL,run_json TEXT NOT NULL)")

    def _now(self):
        return int(self._clock())

    @staticmethod
    def _copy(value):
        return json.loads(json.dumps(value))

    def _message(self, name, data=None):
        message = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()
        if data is not None:
            json_format.ParseDict(data, message)
        return message

    @staticmethod
    def _dict(message):
        return json_format.MessageToDict(message, always_print_fields_with_no_presence=True)

    def _request(self, body, name):
        if isinstance(body, Mapping):
            return dict(body)
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return {}
        try:
            if raw.lstrip()[:1] in (b"{", b"["):
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("object required")
                return data
            message = self._message(name)
            message.ParseFromString(raw)
            return self._dict(message)
        except Exception as exc:
            raise RaceError("invalid_race_request", 400) from exc

    def _notify(self, profile, kind, params=()):
        def notify():
            callback = getattr(self.game, "base_push_notifier", None)
            if callable(callback):
                callback(profile, kind, params)
        self.game.after_commit(notify)

    def _save(self, profile, state):
        with closing(self.game._connect()) as conn, conn:
            conn.execute("INSERT INTO local_race_pvp_state(account_id,state_json,updated_at) VALUES(?,?,?) "
                         "ON CONFLICT(account_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                         (profile["account_id"], json.dumps(state, ensure_ascii=False), self._now()))

    def _load(self, profile):
        with closing(self.game._connect()) as conn:
            row = conn.execute("SELECT state_json FROM local_race_pvp_state WHERE account_id=?", (profile["account_id"],)).fetchone()
        if row:
            return json.loads(row["state_json"])
        score = int(self.SEGMENTS[0]["score"])
        return {"week": None, "round": 0, "score": score, "oldScore": score, "day": None,
                "players": [], "rewardRecved": [], "activeUid": None, "isNew": True,
                "settlements": []}

    def _run(self, profile, uid):
        with closing(self.game._connect()) as conn:
            row = conn.execute("SELECT run_json FROM local_race_pvp_runs WHERE dungeon_uid=? AND account_id=?",
                               (uid, profile["account_id"])).fetchone()
        return json.loads(row["run_json"]) if row else None

    def _save_run(self, profile, run):
        with closing(self.game._connect()) as conn, conn:
            conn.execute("INSERT INTO local_race_pvp_runs(dungeon_uid,account_id,run_json) VALUES(?,?,?) "
                         "ON CONFLICT(dungeon_uid) DO UPDATE SET run_json=excluded.run_json",
                         (run["dungeonUid"], profile["account_id"], json.dumps(run, ensure_ascii=False)))

    def _ticket(self, profile, delta=0, minimum=None, *, notify=True):
        bag = self.game.systems._state(profile)
        item = bag.setdefault("items", {}).setdefault(str(self.TICKET_ID), {"count": 0})
        old = _int(item.get("count"))
        count = max(old, minimum) if minimum is not None else old + delta
        if count < 0:
            raise RaceError("race_ticket_exhausted")
        if count != old:
            item["count"] = count
            if count > old:
                item["gotTime"] = self._now()
            self.game.systems._save(profile, bag)
            if notify:
                self._notify(profile, 3, (self.TICKET_ID,))
        return {"baseRes": {"type": 11, "id": self.TICKET_ID, "count": count, "param": 0, "countDelta": count - old}}

    def _actors(self, profile, group=None):
        ids, uids = self.game.dungeons._fight_team(profile, group)
        return ids, uids, [self.game.dungeons._fight_actor(profile, aid, uid) for aid, uid in zip(ids, uids)]

    def _room(self, profile, state, week):
        _, _, actors = self._actors(profile)
        players = [{"playerId": str(profile["player_id"]), "name": profile["name"], "battleScore": 0,
                    "timestamp": week, "score": state["score"], "level": profile["level"],
                    "headPhoto": 0, "socialPicture": "", "battleActors": actors}]
        for index in range(1, self.PLAYER_COUNT):
            bot = self.game.social.bots.all()[(index - 1) % len(self.game.social.bots.all())]
            container = self.game._types["DBGamePlayer.Actors"]()
            for pos, aid in enumerate(bot["actors"], 1):
                self.game._append_local_actor(container, aid, pos, pos - 1, level=bot["level"])
            # These are explicitly local robot records, not recovered scores.
            score = 2000 + LocalRealtimePvp._digest_int(profile["account_id"], week, index, "race-score", modulo=31001)
            players.append({"playerId": f"local-race-bot-{index:02d}", "name": bot["name"],
                            "battleScore": score, "timestamp": week, "score": state["score"],
                            "level": bot["level"], "headPhoto": self.game.profile_features.bot_head_photo(bot), "socialPicture": "",
                            "battleActors": [self._dict(actor) for actor in container.player_actors]})
        return players

    def _refresh_appearance(self, profile, players):
        features = self.game.profile_features
        own = features._state(profile)
        for player in players:
            if player["playerId"] == str(profile["player_id"]):
                photo = features._head_photo_value(own)
                # Zero is the native platform-picture sentinel. A positive
                # value must index PlayerHeadPhoto, never ActorConfig.
                player["headPhoto"] = photo if photo == 0 or photo in self.HEAD_PHOTOS else 1
                player["socialPicture"] = str(own.get("socialPicture", ""))
            elif _int(player.get("headPhoto")) not in self.HEAD_PHOTOS:
                # Repair existing weekly rooms from the old actor-id avatar
                # implementation without recreating scores, runs or rewards.
                actors = [actor["id"] for actor in player.get("battleActors", []) if _int(actor.get("id")) > 0]
                player["headPhoto"] = features.bot_head_photo({"actors": actors})

    @staticmethod
    def _ranked(players):
        return sorted(players, key=lambda row: (-row["battleScore"], row["timestamp"], row["playerId"]))

    def _refresh(self, profile, state, *, notify=True):
        changes = []
        policy, now = policy_for(self.game), self._now()
        week, day = policy.period_start(now, "week"), policy.day_key(now)
        if state["week"] is None or week > state["week"]:
            if state["week"] is not None:
                state["status"] = self.SETTLE
                ranked = self._ranked(state["players"])
                own = next(row for row in ranked if row["playerId"] == profile["player_id"])
                rank = next(i for i, row in enumerate(ranked, 1) if row["playerId"] == profile["player_id"]) if own["battleScore"] else self.PLAYER_COUNT
                old = state["score"]
                segment = max((row for row in self.SEGMENTS if row["score"] <= old), key=lambda row: row["score"])
                floor = segment["score"] if segment.get("protect") else self.SEGMENTS[0]["score"]
                state["score"] = max(floor, old + self.RANK_SCORES[rank])
                state["oldScore"] = old
                state["settlements"].append({"week": state["week"], "rank": rank, "before": old, "after": state["score"]})
                state["settlements"] = state["settlements"][-52:]
                state["round"] += max(1, (week - state["week"]) // (7 * 86400))
            else:
                state["round"] = 1
            state.update(status=self.MATCH, week=week, rewardRecved=[], activeUid=None, isNew=True,
                         endTime=policy.next_boundary(now, "week"))
            stages = sorted(self.STAGES)
            state["dungeonId"] = stages[policy.week_index(now) % len(stages)]
            state["players"] = self._room(profile, state, week)
            state["status"] = self.FIGHT
        self._refresh_appearance(profile, state["players"])
        if period_due(state.get("day"), day):
            change = self._ticket(profile, minimum=self.DAILY_TICKETS, notify=notify)
            if change["baseRes"]["countDelta"]:
                changes.append(change)
            state["day"] = day
        self._save(profile, state)
        return changes

    def refresh_daily_items(self, profile):
        """Return persisted ticket deltas; the scheduler publishes them once.

        Login already sends the full ItemPackage. Direct Race HTTP requests
        use _refresh's normal notification path instead of this hook.
        """
        with self.game.atomic_mutation():
            return self._refresh(profile, self._load(profile), notify=False)

    def summary(self, profile):
        """Canonical summary for login/redpoint aggregation."""
        with self.game.atomic_mutation():
            state = self._load(profile)
            self._refresh(profile, state)
            return {"rewardTag": self._reward_tag(profile, state)}

    def _info(self, state):
        return {"dungeonId": state["dungeonId"], "players": self._ranked(state["players"]),
                "oldScore": state["oldScore"], "score": state["score"], "isNew": state["isNew"],
                "endTime": state["endTime"], "state": state["status"], "round": state["round"]}

    def _own(self, profile, state):
        return next(row for row in state["players"] if row["playerId"] == profile["player_id"])

    def _reward_tag(self, profile, state):
        score = self._own(profile, state)["battleScore"]
        return any(threshold <= score and threshold not in state["rewardRecved"] for threshold in self.SCORE_REWARDS)

    def _grant(self, profile, rewards):
        changes = grant_rewards(self.game, profile, rewards)
        bag = self.game.systems._state(profile)
        for row in changes:
            res = row.get("baseRes", {})
            if res.get("type") == 11:
                bag["items"][str(res["id"])]["gotTime"] = self._now()
                self._notify(profile, 3, (res["id"],))
        self.game.systems._save(profile, bag)
        return changes

    def _create(self, profile, state, body):
        request = self._request(body, "ApiRacePVPDungeon")
        stage = _int(request.get("dungeonId"))
        if stage != state["dungeonId"] or stage not in self.STAGES:
            raise RaceError("race_dungeon_not_current", 400)
        if _int(profile.get("level")) < 26:
            raise RaceError("race_level_locked", 403)
        active = self._run(profile, state["activeUid"]) if state["activeUid"] else None
        if active and active["status"] == "active":
            return 200, {"dungeonUid": active["dungeonUid"], "baseInfo": active["baseInfo"], "resChange": [self._ticket(profile)]}
        ids, uids, actors = self._actors(profile, request.get("selectActorGroupId"))
        if not any(uids):
            raise RaceError("profile_has_no_actors")
        cost = self.STAGES[stage]["cost"]
        if len(cost) != 1 or (cost[0]["type"], cost[0]["id"], cost[0]["count"]) != (11, self.TICKET_ID, 1):
            raise RaceError("unsupported_race_cost")
        change = self._ticket(profile, -1)
        now = self._now()
        with closing(self.game._connect()) as conn:
            uid = max(_int(conn.execute("SELECT MAX(dungeon_uid) FROM local_race_pvp_runs").fetchone()[0]), self.UID_BASE) + 1
        # Only fields declared by DbDungeon.BaseInfo are returned.
        base = {"dungeonId": stage, "dungeonType": 7, "activityType": self.ACTIVITY_TYPE, "activityId": 0,
                "actorUid": uids, "actorId": ids, "actors": actors, "createTime": now, "quitTime": 0,
                "auraPlayerId": "", "auraActorId": 0, "auraStranger": False, "auraActorQuality": 0,
                "towerFloor": 0, "eventKey": "", "medalId": 0, "reduceInfo": {}, "isChallengeMode": False,
                "fightBuffs": [], "npcCnt": 0}
        run = {"dungeonUid": uid, "week": state["week"], "status": "active", "baseInfo": base,
               "active": None, "balance": None, "createdAt": now}
        self._save_run(profile, run)
        state["activeUid"] = uid
        self._save(profile, state)
        return 200, {"dungeonUid": uid, "baseInfo": base, "resChange": [change]}

    def _active(self, profile, run, body):
        data = self._request(body, "FightActiveData")
        try:
            message = self._message("FightActiveData", data)
        except Exception as exc:
            raise RaceError("invalid_race_active", 400) from exc
        if message.dungeonType != 7 or message.stageID != run["baseInfo"]["dungeonId"]:
            raise RaceError("race_active_stage_mismatch", 400)
        if not 0 <= message.gameRound <= self.MAX_ROUNDS:
            raise RaceError("invalid_race_round", 400)
        data = self._dict(message)
        previous = run.get("active")
        if run["status"] != "active":
            if data == previous:
                return 200, {}
            raise RaceError("race_dungeon_closed")
        if previous and _int(previous.get("gameRound")) > message.gameRound:
            return 200, {}  # late snapshot cannot rewind the battle
        if previous and _int(previous.get("winTeam")) in (1, 2) and previous != data:
            raise RaceError("race_battle_already_finished")
        run["active"] = data
        self._save_run(profile, run)
        return 200, {}

    def _balance(self, profile, state, run):
        if run["balance"] is not None:
            return 200, self._copy(run["balance"])
        if run["status"] != "active":
            raise RaceError("race_dungeon_closed")
        if run["week"] != state["week"]:
            raise RaceError("race_round_expired")
        active = run.get("active")
        if not active:
            raise RaceError("race_fight_snapshot_required")
        # totalDamage measures damage RECEIVED by the team, not damage dealt.
        enemy = [team for team in active.get("teamList", []) if _int(team.get("team")) == 2]
        if len(enemy) != 1 or "statistic" not in enemy[0]:
            raise RaceError("race_enemy_statistics_required", 400)
        damage = _int(enemy[0]["statistic"].get("totalDamage"))
        if damage < 0:
            raise RaceError("invalid_race_damage", 400)
        score = damage // 10
        own = self._own(profile, state)
        if score > own["battleScore"]:
            own.update(battleScore=score, timestamp=self._now(), battleActors=run["baseInfo"]["actors"])
        # Common/DungeonUtil also balances a player's early surrender; the
        # persisted real damage is valid then even when winTeam is still 0.
        result = {"curBattleScore": score, "highBattleScore": own["battleScore"]}
        run.update(status="balanced", balance=result, balancedAt=self._now())
        self._save_run(profile, run)
        self._save(profile, state)
        return 200, result

    def _claim_reward(self, profile, state, body):
        request = self._request(body, "ApiRacePVPBattleScoreReward")
        threshold = _int(request.get("battleScore"))
        if threshold not in self.SCORE_REWARDS:
            raise RaceError("race_reward_not_found", 404)
        if threshold in state["rewardRecved"]:
            return 200, {"resChange": []}
        if self._own(profile, state)["battleScore"] < threshold:
            raise RaceError("race_reward_not_earned")
        changes = self._grant(profile, self.SCORE_REWARDS[threshold])
        state["rewardRecved"].append(threshold)
        self._save(profile, state)
        return 200, {"resChange": changes}

    def handle_http(self, method, path, body, profile):
        route = urlsplit(path).path.rstrip("/").casefold()
        own_match = re.fullmatch(r"/fci/racepvp-dungeon/(\d+)(?:/(active|balance))?", route)
        shared_match = re.fullmatch(r"/fci/dungeon/(\d+)(?:/(active|balance))?", route)
        match = own_match or shared_match
        if route not in self.ROUTES and not match:
            return None
        if shared_match and self._run(profile, int(shared_match[1])) is None:
            return None
        try:
            with self.game.atomic_mutation():
                state = self._load(profile)
                self._refresh(profile, state)
                method = method.upper()
                if route in ("/fci/racepvpinfo", "/fci/racepvp/info") and method == "GET":
                    result = self._info(state)
                    state["isNew"] = False
                    state["oldScore"] = state["score"]
                    self._save(profile, state)
                    return 200, result
                if route == "/fci/racepvpsummary" and method == "GET":
                    return 200, {"rewardTag": self._reward_tag(profile, state)}
                if route == "/fci/racepvpscore" and method == "GET":
                    segment = max((r for r in self.SEGMENTS if r["score"] <= state["score"]), key=lambda r: r["score"])
                    return 200, {"segId": segment["id"], "score": state["score"]}
                if route == "/fci/racepvpbattlescorereward":
                    if method == "GET":
                        return 200, {"rewardRecved": list(state["rewardRecved"])}
                    if method == "POST":
                        return self._claim_reward(profile, state, body)
                if route == "/fci/racepvp-dungeon" and method == "POST":
                    return self._create(profile, state, body)
                if match:
                    run = self._run(profile, int(match[1]))
                    if run is None:
                        raise RaceError("race_dungeon_not_found", 404)
                    action = match[2]
                    if action == "active" and method in ("PATCH", "PUT", "POST"):
                        return self._active(profile, run, body)
                    if action == "active" and method == "GET":
                        return 200, run.get("active") or {}
                    if action == "balance" and method == "POST":
                        return self._balance(profile, state, run)
                    if action is None and method == "DELETE":
                        if run["status"] == "active":
                            run.update(status="closed", closedAt=self._now())
                            self._save_run(profile, run)
                        if state["activeUid"] == run["dungeonUid"]:
                            state["activeUid"] = None
                            self._save(profile, state)
                        return 200, {}
                return 405, {"error": "method_not_allowed"}
        except RaceError as exc:
            return exc.status, {"error": exc.error}


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
    RACE_PLAYERS = LocalRacePvp.PLAYER_COUNT
    RACE_TICKETS = 6
    BURST_ROUNDS = 5
    BURST_PLAYERS = 3
    BURST_TICKETS = 1

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        self.race = LocalRacePvp(game, clock=self._clock)
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
        return policy_for(self.game).day_key(self._now())

    def _next_reset(self) -> int:
        return policy_for(self.game).next_boundary(self._now())

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
            if period_due(bucket.get("day"), day):
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
        race_result = self.race.handle_http(method, path, body, profile)
        if race_result is not None:
            return race_result
        # Preserve BurstLink's old simulation without allowing the obsolete
        # action-string Race routes to create a second reward/ticket economy.
        if any(token in route for token in ("racepvp", "race-pvp")):
            if any(token in route for token in ("/match", "/join", "/round", "/start", "/settle")):
                return 410, {"error": "race_requires_client_dungeon_flow"}
            return None
        if not any(token in route for token in ("burstlink", "burst-link")):
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


__all__ = ["LocalRealtimePvp", "LocalRacePvp"]
