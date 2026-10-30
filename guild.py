"""Local robot guilds using the retired client's actual routes and messages.

GuildMisc and Role are the rule sources. Robot membership, three one-shard
donations per request, and deterministic RNG are explicit local simulation
rules. Aid is settled at Sunday's configured daily refresh, in the same
SQLite transaction as its bag grant. Native send requests contain no nonce:
identical sends are valid separate donations. JSON callers may supply
requestId for durable retry protection; no time-window heuristic drops clicks.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import json
import logging
import random
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory

from economy import _client_rows
from server.core.reset import policy_for


RULES = _client_rows("GuildMisc")[0]
ROLES = {(int(r["decomposeRes"]["type"]), int(r["decomposeRes"]["id"])): r
         for r in _client_rows("Role") if r.get("decomposeRes")}
ACTOR_ROLES = {int(r["id"]): int(r["role"]) for r in _client_rows("ActorConfig") if r.get("role")}
# The original GuildSkill prefab has four slots. ProfitDetail supplies their
# actual display parameters; the GuildSkill fee table was not
# recovered. Leave its proto defaults at zero and reject activation below.
SKILL_PROFITS = [r for r in _client_rows("ProfitDetail") if 101 <= int(r["id"]) <= 104]
ROUTE_PATTERNS = (
    r"/fci/guild-recommend/", r"/fci/guild-search/", r"/fci/guild-player/",
    r"/fci/guild-player/invited/", r"/fci/guild/",
    r"/fci/guild/\d+/", r"/fci/guild/\d+/members/",
    r"/fci/guild/\d+/pending-members/", r"/fci/guild/\d+/invited-members/",
    r"/fci/guild/\d+/members/quit/", r"/fci/guild/\d+/donate/",
    r"/fci/guild/\d+/donate/(?:request|send|touch)/",
    r"/fci/guild/\d+/(?:vote|skill|record)/",
)


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _res(value, count=None):
    return {"type": _int(value.get("type")), "id": _int(value.get("id")),
            "param": _int(value.get("param")),
            "count": _int(value.get("count")) if count is None else count}


class GuildError(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


class LocalGuild:
    ROUTE_PATTERNS = ROUTE_PATTERNS

    def __init__(self, game, clock=None):
        self.game = game
        self._clock = clock or game._now
        self._lock = game._lock
        with self._lock, closing(game._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS local_guild_state "
                         "(id INTEGER PRIMARY KEY CHECK(id=1),state_json TEXT NOT NULL,updated_at INTEGER NOT NULL)")

    def _now(self):
        return int(self._clock())

    @staticmethod
    def handles(path):
        route = urlsplit(path).path.rstrip("/") + "/"
        return any(re.fullmatch(pattern, route) for pattern in ROUTE_PATTERNS)

    def _policy(self):
        return replace(policy_for(self.game), week_start=int(RULES["donateRefreshIsoWeekday"]) - 1)

    @staticmethod
    def _player_default():
        return {"guildId": 0, "type": 0, "joinTime": 0, "lastQuitTime": 0,
                "guildScoreRecord": 0, "invitedGuildId": [], "pendingGuildId": [],
                "lastGuildDonateBalanceTime": 0, "quitCount": 0, "limitEndTime": 0,
                "donateLimitEndTime": 0, "guildLevelChanged": False, "rentActor": 0,
                "rentedActors": {}, "actorGroupInfo": {"actorGroups": {}, "selectId": 0},
                "guildDungeonActorGroupId": 0, "guildDungeonLimitEndTime": 0}

    def _new_guild(self, gid, name, now, profile=None, body=None):
        body = body or {}
        bots = [] if profile else self.game.social.bots.all()[(gid - 1) * 5:gid * 5]
        members = {b["playerId"]: self._member(b, president=(n == 0)) for n, b in enumerate(bots)}
        if profile:
            members[profile["player_id"]] = self._member(profile, president=True)
        president = next(iter(members))
        base = {"id": gid, "name": name, "icon": str(body.get("icon", "1")),
                "billboard": str(body.get("billboard", "本地机器人公会")), "skin": _int(body.get("skin")),
                "level": 1, "exp": 0, "presidentName": members[president]["name"],
                "president": president, "vicePresident": [], "createTime": now,
                "qqGroup": "", "membersCount": len(members), "score": 0}
        guild = {"baseInfo": base, "members": members, "botIds": list(b["playerId"] for b in bots),
                 "donateInfo": {}, "receipts": {}}
        self._new_week(guild, now)
        return guild

    @staticmethod
    def _member(profile, president=False):
        return {"playerId": str(profile.get("player_id", profile.get("playerId", ""))),
                "name": str(profile.get("name", "")), "level": _int(profile.get("level"), 1),
                "type": 2 if president else 0, "lastLogoutTime": 0, "guildScoreRecord": 0,
                "guildScore": 0, "headPhoto": _int(profile.get("avatar", profile.get("actor_id"))),
                "socialPicture": "", "gamecenterLoginTime": 0}

    def _new_week(self, guild, now):
        start = self._policy().period_start(now, "week")
        guild["donateInfo"] = {"nextBalanceTime": self._policy().next_boundary(now, "week"),
                               "donates": {}, "donatePlayerInfo": {}}
        guild["botInventory"] = {}
        for i, pid in enumerate(guild["botIds"]):
            # Finite local supplies; one shard per bot per human request.
            guild["botInventory"][pid] = {str(key[1]): 10 for key in ROLES}
            guild["donateInfo"]["donatePlayerInfo"][pid] = {
                "donateCountWeekly": 0, "playerName": guild["members"][pid]["name"]}
            if i < 3:
                resource = sorted(ROLES)[i]
                guild["donateInfo"]["donates"][pid] = self._donation(pid, resource, start)

    @staticmethod
    def _donation(pid, resource, now):
        return {"requestPlayerId": pid, "donateRes": {"type": resource[0], "id": resource[1], "count": 0, "param": 0},
                "createTime": now, "loveScore": 0, "lastTouchTime": 0, "donatesCount": {}}

    def _load(self, conn, now):
        row = conn.execute("SELECT state_json FROM local_guild_state WHERE id=1").fetchone()
        if row:
            return json.loads(row["state_json"])
        return {"guilds": {str(i): self._new_guild(i, name, now) for i, name in
                           enumerate(("本地互助社", "电击训练社", "周末冒险社"), 1)}, "players": {}, "nextId": 100}

    def _save(self, conn, state, now):
        conn.execute("INSERT INTO local_guild_state(id,state_json,updated_at) VALUES(1,?,?) "
                     "ON CONFLICT(id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                     (_dump(state), now))

    def _system_state(self, conn, profile):
        row = conn.execute("SELECT state_json FROM local_system_state WHERE account_id=?", (profile["account_id"],)).fetchone()
        if row:
            return json.loads(row["state_json"])
        # Offline recipients only carry account/player IDs during settlement.
        # Recover their real starter actors before creating a new bag row.
        saved = conn.execute("SELECT actor_id,actor_ids_json FROM game_profiles WHERE account_id=?", (profile["account_id"],)).fetchone()
        seed = dict(profile)
        if saved:
            seed.update(actor_id=saved["actor_id"], actor_ids=json.loads(saved["actor_ids_json"]))
        return self.game.systems._default_state(seed)

    def _resources(self, conn, profile, changes, now):
        """Canonical economy/bag stores, joined to the guild transaction.

        grant_rewards/_change_resources open their own connections, so calling
        them here would split commits (and deadlock after BEGIN IMMEDIATE).
        This narrow adapter uses economy's resource normalization and the same
        bag schema, returning the identical STResourceDynamic contract.
        """
        row = conn.execute("SELECT resources_json FROM game_profiles WHERE account_id=?", (profile["account_id"],)).fetchone()
        if row is None:
            raise GuildError("profile_not_found", 404)
        resources = self.game.economy._resources({"resources": json.loads(row["resources_json"])})
        index = {}
        for res in resources:
            key = (res["type"], res["id"], res["param"])
            index[key] = index.get(key, 0) + res["count"]
        bag = self._system_state(conn, profile)
        deltas = {}
        for res in changes:
            key = (_int(res.get("type")), _int(res.get("id")), _int(res.get("param")))
            deltas[key] = deltas.get(key, 0) + _int(res.get("count"))
        result = []
        for (kind, rid, param), amount in deltas.items():
            if kind == 11:
                item = bag.setdefault("items", {}).setdefault(str(rid), {"count": 0})
                value = _int(item.get("count")) + amount
                if value < 0:
                    raise GuildError("insufficient_shards")
                item["count"] = value
                if amount > 0:
                    item.update(gotTime=now, isNew=True)
            else:
                value = index.get((kind, rid, param), 0) + amount
                if value < 0:
                    raise GuildError("insufficient_resource")
                index[kind, rid, param] = value
            result.append({"baseRes": {"type": kind, "id": rid, "param": param,
                                       "count": value, "countDelta": amount}})
        conn.execute("UPDATE game_profiles SET resources_json=?,updated_at=? WHERE account_id=?",
                     (_dump([{"type": k[0], "id": k[1], "param": k[2], "count": v} for k, v in index.items()]), now, profile["account_id"]))
        conn.execute("INSERT INTO local_system_state(account_id,state_json,updated_at) VALUES(?,?,?) "
                     "ON CONFLICT(account_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                     (profile["account_id"], _dump(bag), now))
        return result

    @staticmethod
    def _rng(*parts):
        return random.Random(int.from_bytes(hashlib.sha256(_dump(parts).encode()).digest(), "big"))

    def _refresh(self, conn, state, now, notices):
        for guild in state["guilds"].values():
            info = guild["donateInfo"]
            if now < info["nextBalanceTime"]:
                continue
            boundary = info["nextBalanceTime"]
            settled_players = set()
            for pid, request in info["donates"].items():
                player = state["players"].get(pid)
                if player is None:
                    continue
                count = sum(request["donatesCount"].values())
                for segment in reversed(RULES["loveSegment"]):
                    if request["loveScore"] >= segment["scoreLow"]:
                        ratio = self._rng(pid, request["createTime"], boundary).randint(segment["randomLow"], segment["randomHigh"])
                        count = count * ratio // 10000
                        break
                if count:
                    owner = {"account_id": player["accountId"], "player_id": pid}
                    self._resources(conn, owner, [_res(request["donateRes"], count)], now)
                    notices.append((owner, 3, [str(request["donateRes"]["id"])]))
                player["data"]["lastGuildDonateBalanceTime"] = boundary
                settled_players.add(pid)
            self._new_week(guild, now)
            current_players = set(guild["members"]) & state["players"].keys()
            for pid in sorted(current_players | settled_players):
                owner = {"account_id": state["players"][pid]["accountId"], "player_id": pid}
                notices.append((owner, 25, []))
                # Former members can still receive pending shards, but a
                # guildless client cannot fetch /guild/{guildId}/donate/.
                if pid in current_players:
                    notices.append((owner, 58, []))

    def _emit(self, notices):
        notifier = getattr(self.game, "base_push_notifier", None)
        if callable(notifier):
            for profile, push_type, params in notices:
                try:
                    notifier(profile, push_type, params)
                except Exception:
                    # The persisted operation must not fail after committing.
                    logging.getLogger(__name__).exception("guild notification failed after commit")

    def _run(self, profile, callback):
        notices = []
        with self.game.atomic_mutation():
            with self._lock, closing(self.game._connect()) as conn:
                try:
                    now = self._now()
                    state = self._load(conn, now)
                    player = state["players"].setdefault(str(profile["player_id"]),
                        {"accountId": int(profile["account_id"]), "data": self._player_default()})
                    self._refresh(conn, state, now, notices)
                    result = callback(conn, state, player["data"], now, notices)
                    self._save(conn, state, now)
                    if isinstance(profile, dict):
                        row = conn.execute("SELECT resources_json FROM game_profiles WHERE account_id=?", (profile["account_id"],)).fetchone()
                        if row:
                            profile["resources"] = json.loads(row["resources_json"])
                except Exception:
                    conn.rollback()
                    raise
        self._emit(notices)
        return result

    def player_data(self, profile):
        return self._run(profile, lambda conn, state, player, now, notices: dict(player))

    def _request(self, body, type_name):
        raw = body.encode() if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise GuildError("invalid_request", 400)
            return value
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                msg = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(type_name))()
                msg.ParseFromString(raw)
                return json_format.MessageToDict(msg)
            except Exception as exc:
                raise GuildError("invalid_request", 400) from exc

    @staticmethod
    def _skills():
        return [{"id": int(row["id"]), "type": _int(row.get("type")),
                 "add": _int(row.get("add")), "time": _int(row.get("time")),
                 "maxUseCount": 0, "scoreCost": 0, "startTime": 0, "endTime": 0}
                for row in SKILL_PROFITS]

    @staticmethod
    def _detail(guild, changes=None):
        return {"baseInfo": guild["baseInfo"], "members": list(guild["members"].values()),
                "pendingMembers": [], "invitedMembers": [], "skills": LocalGuild._skills(),
                "donateInfo": guild["donateInfo"], "resChange": changes or [],
                "voteInfo": {"inVote": False, "endTime": 0, "playersSupport": [], "playersKick": []}}

    def _collected_roles(self, conn, profile):
        bag = self._system_state(conn, profile)
        ids = {_int(v) for v in profile.get("actor_ids", [])}
        ids.update(_int(v.get("id")) for v in bag.get("actorInstances", {}).values())
        ids.update(_int(k) for k in bag.get("actors", {}))
        row = conn.execute("SELECT state_json FROM local_achievements WHERE account_id=?", (profile["account_id"],)).fetchone()
        if row:
            ids.update(_int(k) for k in json.loads(row["state_json"]).get("actors", {}))
        return {ACTOR_ROLES[i] for i in ids if i in ACTOR_ROLES}

    def handle_http(self, method, path, body, profile):
        route = urlsplit(path).path.rstrip("/") + "/"
        if not self.handles(route):
            return None
        method = method.upper()
        request_type = {"/fci/guild-search/": "ApiGuildSearch", "/fci/guild-player/": "ApiGuildPlayer"}.get(route, "ApiGuild")
        for suffix, name in (("donate/request/", "ApiGuildDonateRequest"), ("donate/send/", "ApiGuildDonateSend"),
                             ("donate/touch/", "ApiGuildDonateTouch"), ("pending-members/", "ApiGuildMemberPending"),
                             ("members/quit/", "ApiGuildMembersQuit"), ("members/", "ApiGuildMembers"),
                             ("skill/", "ApiGuildSkill")):
            if route.endswith(suffix):
                request_type = name
                break
        try:
            payload = self._request(body, request_type)
            return self._run(profile, lambda conn, state, player, now, notices:
                             self._dispatch(conn, state, player, now, notices, method, route, payload, profile))
        except GuildError as exc:
            return exc.status, {"error": exc.code}

    def _dispatch(self, conn, state, player, now, notices, method, route, body, profile):
        pid = str(profile["player_id"])
        if route in ("/fci/guild-recommend/", "/fci/guild-search/") and method == "POST":
            key = str(body.get("idOrName", "")).strip().casefold()
            return 200, [g["baseInfo"] for g in state["guilds"].values()
                         if route.endswith("recommend/") or key in (str(g["baseInfo"]["id"]), g["baseInfo"]["name"].casefold())
                         or (key and key in g["baseInfo"]["name"].casefold())][:int(RULES["randomBatchCount"])]
        if route == "/fci/guild-player/" and method in ("GET", "PATCH"):
            if method == "PATCH" and "guildLevelChanged" in body:
                player["guildLevelChanged"] = bool(body["guildLevelChanged"])
            return 200, dict(player)
        if route == "/fci/guild-player/invited/" and method == "GET":
            return 200, []
        if route == "/fci/guild/" and method == "POST":
            if player["guildId"]:
                existing = state["guilds"].get(str(player["guildId"]))
                if existing and existing["baseInfo"]["president"] == pid and existing["baseInfo"]["name"] == str(body.get("name", "")).strip():
                    return 200, self._detail(existing)
                raise GuildError("already_in_guild")
            if _int(profile.get("level")) < RULES["createMinLevel"]:
                raise GuildError("guild_level_locked", 403)
            name = str(body.get("name", "")).strip()
            if not 1 <= len(name) <= RULES["maxNameLen"] or len(str(body.get("billboard", ""))) > RULES["maxBillboardLen"]:
                raise GuildError("invalid_guild_name_or_billboard", 400)
            if any(g["baseInfo"]["name"].casefold() == name.casefold() for g in state["guilds"].values()):
                raise GuildError("guild_name_exists")
            if not 1 <= _int(body.get("icon", "1")) <= RULES["badgeCount"]:
                raise GuildError("invalid_guild_icon", 400)
            changes = self._resources(conn, profile, [_res(RULES["createCost"], -RULES["createCost"]["count"])], now)
            gid = state["nextId"]
            state["nextId"] += 1
            guild = self._new_guild(gid, name, now, profile, body)
            state["guilds"][str(gid)] = guild
            player.update(guildId=gid, type=2, joinTime=now, pendingGuildId=[], invitedGuildId=[])
            notices.extend([(profile, 25, []), (profile, 26, []), (profile, 1, [])])
            return 200, self._detail(guild, changes)
        match = re.fullmatch(r"/fci/guild/(\d+)/(.*)", route)
        if not match:
            return 405, {"error": "method_not_allowed"}
        gid, action = int(match[1]), match[2]
        guild = state["guilds"].get(str(gid))
        if guild is None:
            raise GuildError("guild_not_found", 404)
        if method == "GET" and action in ("", "members/"):
            return 200, self._detail(guild) if action == "" else list(guild["members"].values())
        if method == "PUT" and action in ("pending-members/", "members/"):
            if str(body.get("playerId", "")) != pid:
                raise GuildError("guild_self_join_only", 403)
            if player["guildId"] == gid:
                return 200, {}
            if player["guildId"]:
                raise GuildError("already_in_guild")
            if _int(profile.get("level")) < RULES["createMinLevel"]:
                raise GuildError("guild_level_locked", 403)
            if len(guild["members"]) >= RULES["maxMemberCount"]:
                raise GuildError("guild_full")
            guild["members"][pid] = self._member(profile)
            guild["baseInfo"]["membersCount"] = len(guild["members"])
            player.update(guildId=gid, type=0, joinTime=now, pendingGuildId=[], invitedGuildId=[])
            notices.extend([(profile, 25, []), (profile, 26, []), (profile, 27, [])])
            return 200, {}
        if action == "members/quit/" and method == "POST" and not player["guildId"] and player["lastQuitTime"]:
            if str(body.get("playerId", "")) != pid:
                raise GuildError("cannot_quit_other_player", 403)
            return 200, dict(player)
        if player["guildId"] != gid:
            raise GuildError("guild_membership_required", 403)
        if action == "members/quit/" and method == "POST":
            if str(body.get("playerId", "")) != pid:
                raise GuildError("cannot_quit_other_player", 403)
            if player["type"] == 2 and len(guild["members"]) > 1:
                raise GuildError("transfer_president_before_quit")
            if player["quitCount"] >= RULES["punishQuitCount"]:
                player["limitEndTime"] = now + RULES["guildFunctionLimitWhenQuit"]
                player["guildDungeonLimitEndTime"] = player["limitEndTime"]
            request = guild["donateInfo"]["donates"].get(pid)
            if request and sum(request["donatesCount"].values()):
                player["donateLimitEndTime"] = guild["donateInfo"]["nextBalanceTime"]
            elif request:
                del guild["donateInfo"]["donates"][pid]
            guild["members"].pop(pid, None)
            guild["baseInfo"]["membersCount"] = len(guild["members"])
            if not guild["members"]:
                del state["guilds"][str(gid)]
            player.update(guildId=0, type=0, lastQuitTime=now, quitCount=player["quitCount"] + 1)
            notices.append((profile, 25, []))
            return 200, dict(player)
        if action == "skill/":
            if method == "GET":
                return 200, self._skills()
            if method == "POST":
                raise GuildError("guild_skill_activation_unavailable", 403)
        if method == "GET" and action in ("pending-members/", "invited-members/", "record/"):
            return 200, []
        if method == "GET" and action == "vote/":
            return 200, self._detail(guild)["voteInfo"]
        if method == "GET" and action == "donate/":
            return 200, guild["donateInfo"]
        if method == "POST" and action.startswith("donate/"):
            if now < player["limitEndTime"]:
                raise GuildError("guild_function_cooldown")
            return self._aid(conn, guild, player, profile, action, body, now, notices)
        return 405, {"error": "method_not_allowed"}

    def _aid(self, conn, guild, player, profile, action, body, now, notices):
        pid, info = str(profile["player_id"]), guild["donateInfo"]
        if action == "donate/request/":
            if now < player["donateLimitEndTime"]:
                raise GuildError("guild_donation_cooldown")
            raw = body.get("res")
            if not isinstance(raw, Mapping):
                raise GuildError("invalid_shard", 400)
            res = _res(raw)
            key = res["type"], res["id"]
            role = ROLES.get(key)
            if role is None or res["param"] or res["count"] not in (0, 1):
                raise GuildError("invalid_shard", 400)
            if role["id"] not in self._collected_roles(conn, profile):
                raise GuildError("role_not_collected", 403)
            old = info["donates"].get(pid)
            if old and (old["donateRes"]["type"], old["donateRes"]["id"]) == key:
                return 200, old
            if old and sum(old["donatesCount"].values()):
                raise GuildError("donation_request_already_received")
            request = self._donation(pid, key, now)
            info["donates"][pid] = request
            for bot in guild["botIds"][:3]:
                count = guild["botInventory"][bot].get(str(key[1]), 0)
                if count > 0:
                    guild["botInventory"][bot][str(key[1])] = count - 1
                    request["donatesCount"][bot] = 1
                    info["donatePlayerInfo"][bot]["donateCountWeekly"] += 1
            notices.append((profile, 58, []))
            return 200, request
        if action == "donate/touch/":
            request = info["donates"].get(pid)
            if not request or not sum(request["donatesCount"].values()):
                raise GuildError("no_received_donation")
            # GuildSupport uses WU.IsToday(localTime): calendar midnight,
            # unlike the daily task refresh at 06:00.
            day = policy_for(self.game).local_datetime(now).date().isoformat()
            previous = (policy_for(self.game).local_datetime(request["lastTouchTime"]).date().isoformat()
                        if request["lastTouchTime"] else "")
            if day > previous and request["loveScore"] < RULES["maxLoveScore"]:
                gain = max(0, round(self._rng(pid, request["createTime"], day).gauss(RULES["loveScoreMu"], RULES["loveScoreSigma"])))
                request["loveScore"] = min(RULES["maxLoveScore"], request["loveScore"] + gain)
                request["lastTouchTime"] = now
                notices.append((profile, 58, []))
            return 200, request
        if action != "donate/send/":
            return 405, {"error": "method_not_allowed"}
        token = str(body.get("requestId", ""))
        if len(token) > 128:
            raise GuildError("invalid_request_id", 400)
        receipt_key = pid + ":" + token if token else None
        fingerprint = _dump({"res": body.get("res"), "playerIdRequest": body.get("playerIdRequest")})
        if receipt_key and receipt_key in guild["receipts"]:
            receipt = guild["receipts"][receipt_key]
            if receipt["fingerprint"] != fingerprint:
                raise GuildError("request_id_conflict")
            # Repair a lost response using current absolute balances and zero
            # deltas, so the client's historyGetCount is not incremented twice.
            result = json.loads(_dump(receipt["result"]))
            result["resChange"] = self._resources(conn, profile,
                [_res(change["baseRes"], 0) for change in result["resChange"]], now)
            current = info["donates"].get(result["donate"]["requestPlayerId"])
            if current and current["createTime"] == result["donate"]["createTime"]:
                result["donate"] = current
                result["donatePlayerInfo"] = info["donatePlayerInfo"]
            return 200, result
        target = str(body.get("playerIdRequest", ""))
        request = info["donates"].get(target)
        if target == pid:
            raise GuildError("cannot_donate_to_self", 400)
        if target not in guild["members"] or request is None:
            raise GuildError("donation_request_not_found", 404)
        raw = body.get("res")
        if not isinstance(raw, Mapping):
            raise GuildError("invalid_shard", 400)
        res = _res(raw)
        if res["count"] != 1 or res["param"] or (res["type"], res["id"]) != (request["donateRes"]["type"], request["donateRes"]["id"]):
            raise GuildError("invalid_shard", 400)
        if sum(request["donatesCount"].values()) >= RULES["donateMaxCount"]:
            raise GuildError("donation_request_full")
        donor = info["donatePlayerInfo"].setdefault(pid, {"donateCountWeekly": 0, "playerName": profile["name"]})
        if donor["donateCountWeekly"] >= RULES["donateMaxCountByOnePlayer"]:
            raise GuildError("weekly_donation_limit")
        # Both refunds are explicit client table values, never arbitrary gold.
        rewards = [RULES["donateRefund"], ROLES[(res["type"], res["id"])]["donateRefund"]]
        changes = self._resources(conn, profile, [_res(res, -1)] + [_res(r) for r in rewards], now)
        request["donatesCount"][pid] = request["donatesCount"].get(pid, 0) + 1
        donor["donateCountWeekly"] += 1
        score = RULES["donateRefund"]["count"]
        player["guildScoreRecord"] += score
        guild["members"][pid]["guildScoreRecord"] = player["guildScoreRecord"]
        guild["members"][pid]["guildScore"] += score
        result = {"donate": request, "donatePlayerInfo": info["donatePlayerInfo"], "resChange": changes}
        if receipt_key:
            guild["receipts"][receipt_key] = {"fingerprint": fingerprint, "result": json.loads(_dump(result))}
        cultivation = getattr(self.game, "cultivation", None)
        if cultivation is not None:
            cultivation.record(profile, 82, param=res["id"])
        notices.extend([(profile, 3, [str(res["id"])]), (profile, 1, []), (profile, 58, []), (profile, 25, [])])
        return 200, result


__all__ = ["LocalGuild", "ROUTE_PATTERNS"]
