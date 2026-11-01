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
from profile_features import actor_role, role_head_photo
from server.core.reset import policy_for


RULES = _client_rows("GuildMisc")[0]
ROLES = {(int(r["decomposeRes"]["type"]), int(r["decomposeRes"]["id"])): r
         for r in _client_rows("Role") if r.get("decomposeRes")}
ACTOR_ROLES = {int(r["id"]): int(r["role"]) for r in _client_rows("ActorConfig") if r.get("role")}
CULTIVATION_ROLES = {ACTOR_ROLES[int(r["actorId"])] for r in _client_rows("ActorCultivation")}
BOT_WISHES = tuple(dict.fromkeys([min(ROLES)] + sorted(
    key for key, role in ROLES.items() if int(role["id"]) in CULTIVATION_ROLES)))
HEAD_PHOTOS = frozenset(int(row["id"]) for row in _client_rows("PlayerHeadPhoto"))
# The original GuildSkill prefab has four slots. ProfitDetail supplies their
# effect parameters; the fee table is not present in the extracted client
# tables, so the native contract uses the documented local rule below.
SKILL_PROFITS = [r for r in _client_rows("ProfitDetail") if 101 <= int(r["id"]) <= 104]
# ProfitDetail rows 101–104 carry a five-hour duration in the recovered
# client tables; use that value for both the slot projection and activation.
SKILL_DURATION = 18000
SKILL_SCORE_COST = 500
SKILL_MAX_USE_COUNT = 1
LOCAL_GUILD_BOT_COUNT = 5
ROUTE_PATTERNS = (
    r"/fci/guild-recommend/", r"/fci/guild-search/", r"/fci/guild-player/",
    r"/fci/guild-player/invited/", r"/fci/guild-player/invited/\d+/", r"/fci/guild/",
    r"/fci/guild/\d+/", r"/fci/guild/\d+/members/",
    r"/fci/guild/\d+/members/[^/]+/",
    r"/fci/guild/\d+/pending-members/", r"/fci/guild/\d+/pending-members/[^/]+/",
    r"/fci/guild/\d+/invited-members/",
    r"/fci/guild/\d+/members/quit/", r"/fci/guild/\d+/donate/",
    r"/fci/guild/\d+/donate/(?:request|send|touch)/",
    r"/fci/guild/\d+/(?:vote|skill|record|president|vice-president|socialgroup)/",
    r"/fci/guild/\d+/vice-president/[^/]+/",
    r"/fci/guild/\d+/group/unlink/",
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
        bots = ([dict(bot, playerId=f"local-guild-{gid}-bot-{i + 1}")
                 for i, bot in enumerate(self.game.social.bots.all()[:LOCAL_GUILD_BOT_COUNT])]
                if profile else self.game.social.bots.all()[(gid - 1) * 5:gid * 5])
        members = {b["playerId"]: self._member(b, president=(n == 0)) for n, b in enumerate(bots)}
        if profile:
            for member in members.values():
                member["type"] = 0
            members[profile["player_id"]] = self._member(profile, president=True)
        president = profile["player_id"] if profile else next(iter(members))
        base = {"id": gid, "name": name, "icon": str(body.get("icon", "1")),
                "billboard": str(body.get("billboard", "本地机器人公会")), "skin": _int(body.get("skin")),
                "level": 1, "exp": 0, "presidentName": members[president]["name"],
                "president": president, "vicePresident": [], "createTime": now,
                "qqGroup": "", "membersCount": len(members), "score": 0}
        guild = {"baseInfo": base, "members": members, "botIds": list(b["playerId"] for b in bots),
                 "donateInfo": {}, "receipts": {}, "skills": {}, "pendingMembers": {},
                 "invitedMembers": {}, "voteInfo": {"inVote": False, "endTime": 0,
                 "playersSupport": [], "playersKick": []}}
        guild["localBots"] = {bot["playerId"]: bot for bot in bots} if profile else {}
        guild["localBotVersion"] = 1 if profile else 0
        guild["memberLastActive"] = {member_id: now for member_id in members}
        self._new_week(guild, now)
        return guild

    def bot_profile(self, player_id):
        """Read an existing guild bot without creating a member or friend."""
        if not re.fullmatch(r"local-guild-\d+-bot-\d+", str(player_id)):
            return None
        with self._lock, closing(self.game._connect()) as conn:
            row = conn.execute("SELECT state_json FROM local_guild_state WHERE id=1").fetchone()
            if row:
                bot = json.loads(row["state_json"]).get("guildBots", {}).get(str(player_id))
                if bot:
                    return dict(bot, actors=list(bot["actors"]))
        return None

    def _add_local_bots(self, guild, now):
        """One-time migration: preserve real wishes and never refill on GET."""
        if guild.get("localBotVersion") or int(guild["baseInfo"]["id"]) < 100:
            return
        gid = guild["baseInfo"]["id"]
        local_bots = guild.setdefault("localBots", {})
        for i, template in enumerate(self.game.social.bots.all()[:LOCAL_GUILD_BOT_COUNT]):
            if len(guild["members"]) >= RULES["maxMemberCount"]:
                break
            pid = f"local-guild-{gid}-bot-{i + 1}"
            bot = dict(template, playerId=pid)
            local_bots[pid] = bot
            guild["members"][pid] = self._member(bot)
            guild["botIds"].append(pid)
            guild.setdefault("memberLastActive", {})[pid] = now
            guild.setdefault("botInventory", {})[pid] = {str(key[1]): 10 for key in ROLES}
            guild["donateInfo"]["donatePlayerInfo"][pid] = {"donateCountWeekly": 0, "playerName": bot["name"]}
            guild["donateInfo"]["donates"][pid] = self._donation(pid, BOT_WISHES[i % len(BOT_WISHES)], now)
        guild["baseInfo"]["membersCount"] = len(guild["members"])
        guild["localBotVersion"] = 1

    def _member(self, profile, president=False):
        if "account_id" in profile:
            appearance = self.game.profile_features._state(profile)
            photo = self.game.profile_features._head_photo_value(appearance)
            if photo not in HEAD_PHOTOS and photo != 0:
                photo = role_head_photo(actor_role(photo))
            social_picture = str(appearance.get("socialPicture", ""))
        else:
            photo = self.game.profile_features.bot_head_photo(profile)
            social_picture = ""
        return {"playerId": str(profile.get("player_id", profile.get("playerId", ""))),
                "name": str(profile.get("name", "")), "level": _int(profile.get("level"), 1),
                "type": 2 if president else 0, "lastLogoutTime": 0, "guildScoreRecord": 0,
                "guildScore": 0, "headPhoto": photo if photo == 0 or photo in HEAD_PHOTOS else 1,
                "socialPicture": social_picture, "gamecenterLoginTime": 0}

    def _new_week(self, guild, now):
        start = self._policy().period_start(now, "week")
        guild["donateInfo"] = {"nextBalanceTime": self._policy().next_boundary(now, "week"),
                               "donates": {}, "donatePlayerInfo": {}}
        guild["botInventory"] = {}
        guild["botIds"] = [pid for pid in guild["botIds"] if pid in guild["members"]]
        for i, pid in enumerate(guild["botIds"]):
            # Finite local supplies; one shard per bot per human request.
            guild["botInventory"][pid] = {str(key[1]): 10 for key in ROLES}
            guild["donateInfo"]["donatePlayerInfo"][pid] = {
                "donateCountWeekly": 0, "playerName": guild["members"][pid]["name"]}
            if i < len(BOT_WISHES) or pid in guild.get("localBots", {}):
                resource = BOT_WISHES[i % len(BOT_WISHES)]
                guild["donateInfo"]["donates"][pid] = self._donation(pid, resource, start)

    def _repair_guilds(self, conn, state, now):
        """Repair legacy invite recipients before any guild's weekly settlement.

        The old invite query omitted account_id and also created player rows
        for bots/nonexistent IDs. Resolve ownership from the authoritative
        profile table; keep unresolvable rows for audit outside the payee map.
        Real players' membership and outstanding donation history stay intact.
        """
        bots = {bot["playerId"]: bot for bot in self.game.social.bots.all()}
        accounts = {str(row["player_id"]): int(row["account_id"])
                    for row in conn.execute("SELECT player_id,account_id FROM game_profiles")}
        for pid, player in list(state["players"].items()):
            if pid in accounts:
                player["accountId"] = accounts[pid]
            else:
                state.setdefault("orphanedPlayers", {})[pid] = state["players"].pop(pid)
        for guild in state["guilds"].values():
            self._add_local_bots(guild, now)
            guild_bots = dict(bots, **guild.get("localBots", {}))
            for pid in guild["members"]:
                guild.setdefault("memberLastActive", {}).setdefault(pid, now)
            guild.setdefault("skills", {})
            guild.setdefault("pendingMembers", {})
            guild.setdefault("invitedMembers", {})
            guild.setdefault("voteInfo", {"inVote": False, "endTime": 0,
                                           "playersSupport": [], "playersKick": []})
            guild["baseInfo"].setdefault("score", 0)
            guild["botIds"] = [pid for pid in guild["botIds"] if pid in guild["members"]]
            for pid, member in guild["members"].items():
                if pid in guild_bots:
                    member["headPhoto"] = self.game.profile_features.bot_head_photo(guild_bots[pid])
                else:
                    row = conn.execute("SELECT f.state_json FROM local_profile_features f "
                                       "JOIN game_profiles p ON p.account_id=f.account_id WHERE p.player_id=?", (pid,)).fetchone()
                    if row:
                        appearance = json.loads(row["state_json"])
                        photo = self.game.profile_features._head_photo_value(appearance)
                        if photo not in HEAD_PHOTOS and photo != 0:
                            photo = role_head_photo(actor_role(photo))
                        member.update(headPhoto=photo, socialPicture=str(appearance.get("socialPicture", "")))
                    elif _int(member.get("headPhoto")) not in HEAD_PHOTOS and member.get("headPhoto") != 0:
                        member["headPhoto"] = role_head_photo(actor_role(member.get("headPhoto")))
            requests = guild["donateInfo"]["donates"]
            present = {(r["donateRes"]["type"], r["donateRes"]["id"]) for pid, r in requests.items() if pid in guild["botIds"]}
            for resource in BOT_WISHES:
                if resource in present:
                    continue
                # Preserve every request that has received a real donation.
                candidates = [pid for pid in guild["botIds"] if pid not in requests or (
                    not sum(requests[pid]["donatesCount"].values()) and
                    (requests[pid]["donateRes"]["type"], requests[pid]["donateRes"]["id"]) not in BOT_WISHES)]
                if candidates:
                    requests[candidates[0]] = self._donation(candidates[0], resource, self._policy().period_start(now, "week"))
                    present.add(resource)

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
        # Keep a profile after a bot is kicked or its guild is dissolved so a
        # real friend relationship never becomes an invisible dangling ID.
        registry = state.setdefault("guildBots", {})
        for guild in state["guilds"].values():
            registry.update(guild.get("localBots", {}))
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
                    # An unknown/deleted profile must not block other guilds.
                    # Preserve any received aid for manual account recovery;
                    # robot wishes have no real account to credit.
                    if pid not in guild["botIds"] and not self.game.social.bots.get(pid) and sum(request["donatesCount"].values()):
                        key = f'{guild["baseInfo"]["id"]}:{boundary}:{pid}'
                        state.setdefault("orphanedDonations", {})[key] = {
                            "guildId": guild["baseInfo"]["id"], "boundary": boundary,
                            "request": request}
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

    @staticmethod
    def _notify_members(state, guild, notices, *types):
        for pid in guild["members"]:
            owner = state["players"].get(pid)
            if owner:
                for kind in types:
                    notices.append(({"account_id": owner["accountId"], "player_id": pid}, kind, []))

    def _refresh_votes(self, state, now, notices):
        for guild in state["guilds"].values():
            vote = guild["voteInfo"]
            president = str(guild["baseInfo"]["president"])
            if vote.get("inVote") and now >= vote["endTime"]:
                eligible = set(guild["members"]) - {president}
                kick = set(vote["playersKick"]) & eligible
                support = set(vote["playersSupport"]) & eligible
                if len(kick) > len(support):
                    # Prefer an actual player; bots do not fabricate ballots.
                    candidates = [guild["members"][pid] for pid in eligible if pid in state["players"]]
                    if candidates:
                        successor = min(candidates, key=lambda member: (
                            0 if member["type"] == 1 else 1,
                            -_int(member.get("guildScoreRecord")), member["playerId"]))
                        target = successor["playerId"]
                        if president in guild["members"]:
                            guild["members"][president]["type"] = 0
                        if president in state["players"]:
                            state["players"][president]["data"]["type"] = 0
                        successor["type"] = 2
                        state["players"][target]["data"]["type"] = 2
                        guild["baseInfo"].update(president=target, presidentName=successor["name"])
                        guild["baseInfo"]["vicePresident"] = [pid for pid in guild["baseInfo"]["vicePresident"] if pid != target]
                        self._notify_members(state, guild, notices, 25, 26, 27)
                vote["inVote"] = False
                guild["voteCooldownUntil"] = vote["endTime"] + RULES["voteCoolDown"]
                self._notify_members(state, guild, notices, 57)
            elif (not vote.get("inVote") and now >= _int(guild.get("voteCooldownUntil"))
                  and now - guild["memberLastActive"].get(president, now) >= RULES["voteStartPresidentLeave"]
                  and any(pid != president and pid in state["players"] for pid in guild["members"])):
                guild["voteInfo"] = {"inVote": True, "endTime": now + RULES["voteLastTime"],
                                     "playersSupport": [], "playersKick": []}
                self._notify_members(state, guild, notices, 57)

    def _run(self, profile, callback):
        notices = []
        with self.game.atomic_mutation():
            with self._lock, closing(self.game._connect()) as conn:
                try:
                    now = self._now()
                    state = self._load(conn, now)
                    player = state["players"].setdefault(str(profile["player_id"]),
                        {"accountId": int(profile["account_id"]), "data": self._player_default()})
                    self._repair_guilds(conn, state, now)
                    current = state["guilds"].get(str(player["data"]["guildId"]))
                    if current and str(profile["player_id"]) in current["members"]:
                        current["memberLastActive"][str(profile["player_id"])] = now
                    self._refresh(conn, state, now, notices)
                    self._refresh_votes(state, now, notices)
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

    def active_profits(self, profile):
        """Return the currently effective native ProfitDetail rows for a member.

        ``systems`` uses this projection for GET ``/fci/profit/``; expiry is
        cleaned by the same state load that serves guild details.
        """
        def read(conn, state, player, now, notices):
            gid = _int(player.get("guildId"))
            guild = state["guilds"].get(str(gid))
            if guild is None:
                return []
            return [{"id": row["id"], "type": row["type"], "add": row["add"],
                     "startTime": row["startTime"], "endTime": row["endTime"]}
                    for row in self._skills(guild, now) if row["endTime"] > now]
        return self._run(profile, read)

    def active_profit_rules(self, profile):
        """Return active effect filters for battle reward integration."""
        def read(conn, state, player, now, notices):
            gid = _int(player.get("guildId"))
            guild = state["guilds"].get(str(gid))
            if guild is None:
                return []
            active = {row["id"] for row in self._skills(guild, now) if row["endTime"] > now}
            return [{"id": _int(row["id"]), "type": _int(row.get("type")), "add": _int(row.get("add")),
                     "isRatio": bool(row.get("isRatio")), "dungeonType": list(row.get("dungeonType") or []),
                     "resType": _int(row.get("resType")), "funcType": _int(row.get("funcType"))}
                    for row in SKILL_PROFITS if _int(row.get("id")) in active]
        return self._run(profile, read)

    def _request(self, body, type_name):
        raw = body.encode() if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
            if value is None:  # Native Post(nil) may serialize as JSON null.
                return {}
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

    def _skills(self, guild, now=None):
        now = self._now() if now is None else int(now)
        saved = guild.setdefault("skills", {})
        result = []
        for row in SKILL_PROFITS:
            skill_id = int(row["id"])
            active = saved.get(str(skill_id)) or {}
            start = _int(active.get("startTime"))
            end = _int(active.get("endTime"))
            if end and end <= now:
                start = end = 0
                saved.pop(str(skill_id), None)
            result.append({"id": skill_id, "type": _int(row.get("type")),
                           "add": _int(row.get("add")), "time": SKILL_DURATION,
                           "maxUseCount": SKILL_MAX_USE_COUNT,
                           "scoreCost": SKILL_SCORE_COST, "startTime": start, "endTime": end})
        return result

    def _detail(self, guild, changes=None, now=None):
        return {"baseInfo": guild["baseInfo"], "members": list(guild["members"].values()),
                "pendingMembers": list(guild.get("pendingMembers", {}).values()),
                "invitedMembers": list(guild.get("invitedMembers", {}).values()),
                "skills": self._skills(guild, now),
                "donateInfo": guild["donateInfo"], "resChange": changes or [],
                "voteInfo": guild.get("voteInfo", {"inVote": False, "endTime": 0,
                                                     "playersSupport": [], "playersKick": []})}

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
                             ("invited-members/", "ApiGuildMemberInvite"), ("socialgroup/", "ApiGuildSocialGroup"),
                             ("vote/", "ApiGuildVotePresident"), ("vice-president/", "ApiGuildVicePresident"),
                             ("president/", "ApiGuildPresident"),
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
            invited = {_int(value) for value in player.get("invitedGuildId", [])}
            return 200, [guild["baseInfo"] for key, guild in state["guilds"].items()
                         if _int(key) in invited]
        declined = re.fullmatch(r"/fci/guild-player/invited/(\d+)/", route)
        if declined and method == "DELETE":
            declined_id = int(declined[1])
            player["invitedGuildId"] = [value for value in player.get("invitedGuildId", [])
                                        if _int(value) != declined_id]
            invited_guild = state["guilds"].get(str(declined_id))
            if invited_guild:
                invited_guild.get("invitedMembers", {}).pop(pid, None)
            return 200, {}
        if route == "/fci/guild/" and method == "POST":
            if player["guildId"]:
                existing = state["guilds"].get(str(player["guildId"]))
                if existing and existing["baseInfo"]["president"] == pid and existing["baseInfo"]["name"] == str(body.get("name", "")).strip():
                    return 200, self._detail(existing, now=now)
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
            return 200, self._detail(guild, changes, now)
        match = re.fullmatch(r"/fci/guild/(\d+)/(.*)", route)
        if not match:
            return 405, {"error": "method_not_allowed"}
        gid, action = int(match[1]), match[2]
        guild = state["guilds"].get(str(gid))
        if guild is None:
            raise GuildError("guild_not_found", 404)
        guild.setdefault("pendingMembers", {})
        guild.setdefault("invitedMembers", {})
        guild.setdefault("voteInfo", {"inVote": False, "endTime": 0,
                                       "playersSupport": [], "playersKick": []})
        member = guild["members"].get(pid)
        in_guild = player["guildId"] == gid and member is not None
        is_president = in_guild and str(guild["baseInfo"].get("president")) == pid
        is_vice_president = in_guild and _int(member.get("type")) == 1
        manager = is_president or is_vice_president

        # Guild administration uses the native verbs from the client.  The
        # local service keeps bot and player members in the same state map, so
        # all of these operations are durable and idempotent.
        if action == "" and method == "PATCH":
            if not manager:
                raise GuildError("guild_manager_required", 403)
            updates = {}
            cost = 0
            for field, limit in (("name", RULES["maxNameLen"]), ("billboard", RULES["maxBillboardLen"])):
                if field in body:
                    value = str(body[field]).strip()
                    if field == "name" and not 1 <= len(value) <= limit:
                        raise GuildError("invalid_guild_name_or_billboard", 400)
                    if field == "billboard" and len(value) > limit:
                        raise GuildError("invalid_guild_name_or_billboard", 400)
                    if field == "name" and value != guild["baseInfo"]["name"]:
                        if any(other["baseInfo"]["id"] != gid and
                               other["baseInfo"]["name"].casefold() == value.casefold()
                               for other in state["guilds"].values()):
                            raise GuildError("guild_name_exists")
                        cost += RULES["renameCost"]
                    updates[field] = value
            for field in ("icon", "skin", "qqGroup"):
                if field in body:
                    if field == "icon":
                        if not 1 <= _int(body[field]) <= RULES["badgeCount"]:
                            raise GuildError("invalid_guild_icon", 400)
                        value = str(_int(body[field]))
                        if value != guild["baseInfo"]["icon"]:
                            cost += RULES["editIconCost"]
                        updates[field] = value
                    else:
                        updates[field] = str(body[field]) if field == "qqGroup" else _int(body[field])
            if guild["baseInfo"]["score"] < cost:
                raise GuildError("guild_score_insufficient")
            # Native PATCH has no request nonce. A transport retry of an
            # already-applied no-op must receive the same ApiGuild.result,
            # including its original resource delta, without charging again.
            if cost == 0 and guild.get("_lastPatchRequest") == dict(body):
                cached = guild.get("_lastPatchResponse")
                if isinstance(cached, dict):
                    return 200, json.loads(json.dumps(cached, ensure_ascii=False))
            guild["baseInfo"].update(updates)
            guild["baseInfo"]["score"] -= cost
            self._notify_members(state, guild, notices, 26)
            # WidgetGuildEdit only consumes ApiGuild.result.baseInfo and
            # resChange after PATCH. Keep the response at that native shape;
            # members/skills are refreshed by their own bound GET routes.
            changes = []
            if cost:
                changes.append({"baseRes": {"type": 17, "id": 0, "param": 0,
                                             "count": int(guild["baseInfo"]["score"]),
                                             "countDelta": -int(cost)}})
            response = {"baseInfo": dict(guild["baseInfo"]), "resChange": changes}
            guild["_lastPatchRequest"] = dict(body)
            guild["_lastPatchResponse"] = json.loads(json.dumps(response, ensure_ascii=False))
            return 200, response

        if action == "group/unlink/" and method == "POST":
            if not manager:
                raise GuildError("guild_manager_required", 403)
            guild["baseInfo"]["qqGroup"] = ""
            self._notify_members(state, guild, notices, 26)
            # Msdk.eFlag.eFlag_Succ is 0 in the shipped IL2CPP constants.
            return 200, {"ret": 0, "errorcode": 0}

        if action in ("socialgroup", "socialgroup/") and method == "PATCH":
            if not manager:
                raise GuildError("guild_manager_required", 403)
            if body.get("resetQq") or body.get("resetWx"):
                guild["baseInfo"]["qqGroup"] = ""
            elif "qqGroupId" in body:
                guild["baseInfo"]["qqGroup"] = str(body.get("qqGroupId") or "")
            return 200, {"qqGroupId": str(guild["baseInfo"].get("qqGroup", "")),
                         "isBoundWx": bool(body.get("isBoundWx", False)), "joinQq": False,
                         "joinWx": False, "resetQq": False, "resetWx": False}

        if action == "president/" and method == "POST":
            if not is_president:
                raise GuildError("guild_manager_required", 403)
            target = str(body.get("newPresident", ""))
            if target not in guild["members"]:
                raise GuildError("guild_member_not_found", 404)
            old = str(guild["baseInfo"].get("president", pid))
            guild["members"].get(old, {}).update(type=0)
            guild["members"][target].update(type=2)
            old_state = state["players"].get(old)
            if old_state:
                old_state["data"]["type"] = 0
            target_state = state["players"].get(target)
            if target_state:
                target_state["data"].update(guildId=gid, type=2)
            guild["baseInfo"]["president"] = target
            guild["baseInfo"]["presidentName"] = guild["members"][target].get("name", target)
            guild["baseInfo"]["vicePresident"] = [v for v in guild["baseInfo"].get("vicePresident", []) if str(v) != target]
            if guild["voteInfo"].get("inVote"):
                guild["voteInfo"]["inVote"] = False
                guild["voteInfo"]["endTime"] = now
                guild["voteCooldownUntil"] = now + RULES["voteCoolDown"]
                self._notify_members(state, guild, notices, 57)
            if target == pid:
                player["type"] = 2
            return 200, guild["baseInfo"]

        if action == "vice-president/" and method == "PUT":
            if not is_president:
                raise GuildError("guild_manager_required", 403)
            target = str(body.get("playerId", ""))
            if target not in guild["members"]:
                raise GuildError("guild_member_not_found", 404)
            if target == str(guild["baseInfo"].get("president")):
                raise GuildError("guild_president_cannot_be_vice", 409)
            vice = [str(v) for v in guild["baseInfo"].get("vicePresident", []) if str(v) in guild["members"]]
            if target not in vice and len(vice) >= _int(RULES.get("viceCount"), 3):
                raise GuildError("guild_vice_president_full", 409)
            if target not in vice:
                vice.append(target)
            guild["baseInfo"]["vicePresident"] = vice
            guild["members"][target]["type"] = 1
            target_state = state["players"].get(target)
            if target_state:
                target_state["data"]["type"] = 1
                notices.append(({"account_id": target_state["accountId"], "player_id": target}, 25, []))
            return 200, guild["baseInfo"]

        vice_match = re.fullmatch(r"vice-president/([^/]+)/", action)
        if vice_match and method == "DELETE":
            if not is_president:
                raise GuildError("guild_manager_required", 403)
            target = vice_match.group(1)
            if target == str(guild["baseInfo"].get("president")):
                raise GuildError("guild_president_cannot_be_vice", 409)
            guild["baseInfo"]["vicePresident"] = [v for v in guild["baseInfo"].get("vicePresident", []) if str(v) != target]
            if target in guild["members"]:
                guild["members"][target]["type"] = 0
            target_state = state["players"].get(target)
            if target_state and target_state["data"]["guildId"] == gid:
                target_state["data"]["type"] = 0
                notices.append(({"account_id": target_state["accountId"], "player_id": target}, 25, []))
            return 200, guild["baseInfo"]

        invite_match = action == "invited-members/"
        if invite_match and method == "PUT":
            if not manager:
                raise GuildError("guild_manager_required", 403)
            target = str(body.get("playerId", ""))
            if not target:
                raise GuildError("invalid_player_id", 400)
            if any(target in candidate["members"] for candidate in state["guilds"].values()):
                raise GuildError("already_in_guild", 409)
            row = conn.execute("SELECT account_id,player_id,name,level FROM game_profiles WHERE player_id=?", (target,)).fetchone()
            if row is None:
                raise GuildError("profile_not_found", 404)
            member = {"playerId": target, "name": str(row["name"]),
                      "level": _int(row["level"], 1), "applyTime": now,
                      "headPhoto": 0, "socialPicture": ""}
            guild["invitedMembers"][target] = member
            invited = state["players"].setdefault(target, {"accountId": int(row["account_id"]),
                                                               "data": self._player_default()})
            invited["accountId"] = int(row["account_id"])
            invited["data"].setdefault("invitedGuildId", [])
            if gid not in invited["data"]["invitedGuildId"]:
                invited["data"]["invitedGuildId"].append(gid)
            return 200, member

        pending_match = re.fullmatch(r"pending-members(?:/([^/]+))?/", action)
        if pending_match and method == "DELETE":
            if not manager:
                raise GuildError("guild_manager_required", 403)
            target = pending_match.group(1)
            if target:
                guild["pendingMembers"].pop(target, None)
            else:
                guild["pendingMembers"].clear()
            return 200, self._detail(guild, now=now)

        member_match = re.fullmatch(r"members/([^/]+)/", action)
        if member_match and method == "DELETE":
            if not manager:
                raise GuildError("guild_manager_required", 403)
            target = member_match.group(1)
            if target == str(guild["baseInfo"].get("president")):
                raise GuildError("cannot_kick_president", 409)
            if target not in guild["members"]:
                raise GuildError("guild_member_not_found", 404)
            if is_vice_president and _int(guild["members"][target].get("type")) != 0:
                raise GuildError("guild_vice_kick_forbidden", 403)
            guild["members"].pop(target, None)
            guild["botIds"] = [value for value in guild["botIds"] if value != target]
            guild["baseInfo"]["vicePresident"] = [value for value in guild["baseInfo"].get("vicePresident", []) if str(value) != target]
            guild["baseInfo"]["membersCount"] = len(guild["members"])
            target_state = state["players"].get(target)
            if target_state:
                target_state["data"].update(guildId=0, type=0, lastQuitTime=now)
                notices.append(({"account_id": target_state["accountId"], "player_id": target}, 25, []))
            return 200, guild["baseInfo"]
        if method == "GET" and action in ("", "members/"):
            return 200, self._detail(guild, now=now) if action == "" else list(guild["members"].values())
        if method == "PUT" and action in ("pending-members/", "members/"):
            requested = str(body.get("playerId", ""))
            # Managers approve a pending player with PUT /members/.  A normal
            # player keeps the original self-join flow used by the client.
            if action == "members/" and requested != pid:
                if not manager or requested not in guild["pendingMembers"]:
                    raise GuildError("guild_member_approval_required", 403)
                row = conn.execute("SELECT account_id,player_id,name,level FROM game_profiles WHERE player_id=?", (requested,)).fetchone()
                if row is None:
                    raise GuildError("profile_not_found", 404)
                target_state = state["players"].setdefault(requested, {"accountId": int(row["account_id"]), "data": self._player_default()})
                if target_state["data"]["guildId"]:
                    raise GuildError("already_in_guild")
                if len(guild["members"]) >= RULES["maxMemberCount"]:
                    raise GuildError("guild_full")
                guild["pendingMembers"].pop(requested)
                guild["members"][requested] = self._member(dict(row))
                guild["baseInfo"]["membersCount"] = len(guild["members"])
                target_state["data"].update(guildId=gid, type=0, joinTime=now,
                                             pendingGuildId=[], invitedGuildId=[])
                notices.append(({"account_id": target_state["accountId"], "player_id": requested}, 25, []))
                # The pending-list view is cached by the client and only
                # refreshes on GuildPendingMemberChange (PushType 29).
                self._notify_members(state, guild, notices, 29)
                return 200, guild["baseInfo"]
            if requested != pid:
                raise GuildError("guild_self_join_only", 403)
            if player["guildId"] == gid:
                return 200, {}
            if player["guildId"]:
                raise GuildError("already_in_guild")
            if _int(profile.get("level")) < RULES["createMinLevel"]:
                raise GuildError("guild_level_locked", 403)
            if len(guild["members"]) >= RULES["maxMemberCount"]:
                raise GuildError("guild_full")
            if action == "pending-members/" and not self.game.social.bots.get(str(guild["baseInfo"]["president"])):
                appearance = self._member(profile)
                guild["pendingMembers"][pid] = {key: appearance[key] for key in (
                    "playerId", "name", "level", "headPhoto", "socialPicture")}
                guild["pendingMembers"][pid]["applyTime"] = now
                if gid not in player["pendingGuildId"]:
                    player["pendingGuildId"].append(gid)
                notices.append((profile, 25, []))
                return 200, {}
            if action == "members/" and pid not in guild["invitedMembers"]:
                raise GuildError("guild_invitation_required", 403)
            guild["members"][pid] = self._member(profile)
            guild["baseInfo"]["membersCount"] = len(guild["members"])
            guild["pendingMembers"].pop(pid, None)
            guild["invitedMembers"].pop(pid, None)
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
                return 200, self._skills(guild, now)
            if method == "POST":
                is_vice = pid in {str(v) for v in guild["baseInfo"].get("vicePresident", [])}
                if is_vice:
                    player["type"] = 1
                if player["type"] not in (1, 2):
                    raise GuildError("guild_skill_only_allowed_admin", 403)
                skill_id = _int(body.get("id"))
                use_count = _int(body.get("useCount", body.get("use_count")), 1)
                if use_count != 1:
                    raise GuildError("guild_skill_invalid_use_count", 400)
                row = next((item for item in SKILL_PROFITS if _int(item.get("id")) == skill_id), None)
                if row is None:
                    raise GuildError("guild_skill_not_found", 404)
                skills = guild.setdefault("skills", {})
                current = skills.get(str(skill_id)) or {}
                if _int(current.get("endTime")) > now:
                    raise GuildError("guild_skill_opened_before")
                if _int(guild["baseInfo"].get("score")) < SKILL_SCORE_COST:
                    raise GuildError("guild_skill_score_insufficient")
                guild["baseInfo"]["score"] = _int(guild["baseInfo"].get("score")) - SKILL_SCORE_COST
                start, end = now, now + SKILL_DURATION
                active = {"startTime": start, "endTime": end, "useCount": use_count}
                skills[str(skill_id)] = active
                info = next(item for item in self._skills(guild, now) if item["id"] == skill_id)
                profit = {"id": skill_id, "type": _int(row.get("type")), "add": _int(row.get("add")),
                          "startTime": start, "endTime": end}
                # The native client refreshes skill slots and profit details for
                # every online member after the shared transaction commits.
                for member_id, member in guild["members"].items():
                    owner = state["players"].get(str(member_id))
                    if owner:
                        notices.append(({"account_id": owner["accountId"], "player_id": member_id}, 56, []))
                        notices.append(({"account_id": owner["accountId"], "player_id": member_id}, 51, []))
                return 200, {"guildSkillInfo": info, "baseInfo": guild["baseInfo"], "profit": profit}
        if method == "GET" and action == "pending-members/":
            return 200, list(guild["pendingMembers"].values())
        if method == "GET" and action == "invited-members/":
            return 200, list(guild["invitedMembers"].values())
        if method == "GET" and action == "record/":
            return 200, []
        if method == "GET" and action == "vote/":
            return 200, guild["voteInfo"]
        if method == "POST" and action == "vote/":
            if not guild["voteInfo"].get("inVote"):
                raise GuildError("guild_vote_not_open")
            if pid == str(guild["baseInfo"]["president"]):
                raise GuildError("guild_president_cannot_vote", 403)
            support = bool(body.get("voteSupport"))
            field = "playersSupport" if support else "playersKick"
            opposite = "playersKick" if support else "playersSupport"
            if pid in guild["voteInfo"][opposite]:
                raise GuildError("guild_vote_already_cast")
            values = [str(v) for v in guild["voteInfo"].setdefault(field, [])]
            if pid not in values:
                values.append(pid)
            guild["voteInfo"][field] = values
            self._notify_members(state, guild, notices, 57)
            return 200, guild["voteInfo"]
        if method == "GET" and action == "donate/":
            return 200, guild["donateInfo"]
        if method == "POST" and action.startswith("donate/"):
            if now < player["limitEndTime"]:
                raise GuildError("guild_function_cooldown")
            return self._aid(conn, state, guild, player, profile, action, body, now, notices)
        return 405, {"error": "method_not_allowed"}

    def _aid(self, conn, state, guild, player, profile, action, body, now, notices):
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
        # GuildSkill spends shared guild popularity. Each accepted donation
        # contributes the same auditable ten points as the member refund.
        guild["baseInfo"]["score"] = _int(guild["baseInfo"].get("score")) + score
        guild["members"][pid]["guildScoreRecord"] = player["guildScoreRecord"]
        guild["members"][pid]["guildScore"] += score
        result = {"donate": request, "donatePlayerInfo": info["donatePlayerInfo"], "resChange": changes}
        if receipt_key:
            guild["receipts"][receipt_key] = {"fingerprint": fingerprint, "result": json.loads(_dump(result))}
        cultivation = getattr(self.game, "cultivation", None)
        if cultivation is not None:
            cultivation.record(profile, 82, param=res["id"])
        notices.extend([(profile, 3, [str(res["id"])]), (profile, 1, []), (profile, 58, []), (profile, 25, [])])
        for member_id, member in guild["members"].items():
            owner = state["players"].get(str(member_id))
            if owner:
                notices.append(({"account_id": owner["accountId"], "player_id": member_id}, 55,
                                [guild["baseInfo"]["score"], member_id, member.get("guildScoreRecord", 0)]))
        return 200, result


__all__ = ["LocalGuild", "ROUTE_PATTERNS"]
