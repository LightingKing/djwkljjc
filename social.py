"""Deterministic local social, bot, async-PVP and leaderboard compatibility.

The retired service is not contacted.  Every bot and message is generated
from a fixed seed and every player-specific mutation is persisted in the
existing game SQLite database.
"""

from __future__ import annotations

from server.core.reset import policy_for, period_due

import json
import random
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Optional
from urllib.parse import parse_qs, unquote, urlsplit

from google.protobuf import json_format, message_factory

try:  # Package import when local-server is installed as a package.
    from .storage.compat import shared_process_lock
    from .grants import grant_rewards
    from .profile_features import misc_value
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from storage.compat import shared_process_lock
    from grants import grant_rewards
    from profile_features import misc_value


Body = bytes | bytearray | str | None
Response = tuple[int, Any]


RES_RELATION_POINT = 10  # ResourceType.ResPlayerRelationPoint
_DAY = 86400
# Daily resets follow Misc.dailyRefreshTime (06:00) on the server clock, the
# same boundary the task module uses.
RESET_HOUR_UTC = 6


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json(body: Body) -> dict[str, Any]:
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


class LocalBotPool:
    """Stable bot records shared by friend, assist, PVP and rank screens."""

    NAMES = ("御坂训练员", "黑雪姬", "桐人练习生", "亚丝娜助手", "司波机器人", "深雪陪练", "逢坂大河", "夏娜演示员")
    # Bot progression is a local compatibility rule.  Keeping an explicit
    # epoch makes the result deterministic under the server clock used by
    # long-term tests and avoids pretending that we recovered the retired
    # service's hidden growth schedule.
    BOT_EPOCH = int(datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp())

    def __init__(self, *, seed: int = 20261028, count: int = 24, clock=None) -> None:
        rng = random.Random(seed)
        self._clock = clock or (lambda: int(datetime.now(tz=timezone.utc).timestamp()))
        self._bots: list[dict[str, Any]] = []
        for index in range(1, count + 1):
            level = 8 + rng.randrange(1, 45)
            score = 850 + rng.randrange(0, 700)
            actors = [1001 + rng.randrange(0, 8), 1001 + rng.randrange(0, 8), 1001 + rng.randrange(0, 8)]
            self._bots.append({
                "playerId": f"local-bot-{index:03d}",
                "name": self.NAMES[(index - 1) % len(self.NAMES)] + str(index),
                "level": level,
                "avatar": 1001 + (index % 6),
                "score": score,
                "actors": actors,
                "online": bool(index % 3),
            })

    def _snapshot(self, bot: Mapping[str, Any]) -> dict[str, Any]:
        """Return one bot with deterministic, slow local progression."""

        result = dict(bot, actors=list(bot["actors"]))
        try:
            elapsed = max(0, int(self._clock()) - self.BOT_EPOCH)
        except (TypeError, ValueError, OverflowError):
            elapsed = 0
        weeks = elapsed // (7 * 86400)
        # Progress is intentionally capped so rankings remain useful and do
        # not become an implicit player-level cheat after a large clock jump.
        result["level"] = min(60, int(result.get("level", 1)) + int(weeks))
        result["score"] = min(3000, int(result.get("score", 0)) + int(weeks) * 25)
        result["progressWeeks"] = int(weeks)
        return result

    def all(self) -> list[dict[str, Any]]:
        return [self._snapshot(bot) for bot in self._bots]

    def get(self, player_id: str) -> dict[str, Any] | None:
        for bot in self._bots:
            if str(bot["playerId"]) == str(player_id):
                return self._snapshot(bot)
        lookup = getattr(self, "guild_lookup", None)
        if callable(lookup):
            return lookup(str(player_id))
        return None

    def pick(self, account_id: int, *, offset: int = 0) -> dict[str, Any]:
        index = (int(account_id) + int(offset)) % len(self._bots)
        return self._snapshot(self._bots[index])


class LocalSocial:
    """Persistent local social surface owned by a :class:`LocalGame`."""

    def __init__(self, game: Any, *, clock=None, bots: LocalBotPool | None = None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        self.bots = bots or LocalBotPool(clock=self._clock)
        self.bots.guild_lookup = self._guild_bot
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_social_state (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _guild_bot(self, player_id):
        guild = getattr(self.game, "guild", None)
        return guild.bot_profile(player_id) if guild is not None else None

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "friends": ["local-bot-001", "local-bot-002"],
            "blocked": [],
            "likes": 0,
            # A few deterministic local messages keep the chat/danmu panel
            # populated on a fresh sandbox account without contacting a
            # remote chat service.  They are ordinary local state and can be
            # replaced by the player's own messages.
            "messages": [
                {"id": 1, "playerId": "local-bot-001", "name": "御坂训练员1", "text": "欢迎来到本地演示频道", "createdAt": 0, "localBot": True},
                {"id": 2, "playerId": "local-bot-002", "name": "黑雪姬2", "text": "好友和助战均使用沙盒机器人", "createdAt": 0, "localBot": True},
            ],
            "pvp": {
                "rating": 1000,
                "wins": 0,
                "losses": 0,
                "matches": [],
                # Keep the pending async-PVP ticket in the same durable
                # state as the result.  The retired client retries POSTs
                # after reconnects, so a process restart must not mint a
                # second opponent or apply the rating delta twice.
                "active": None,
                "lastResult": None,
            },
        }

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute("SELECT state_json FROM local_social_state WHERE account_id = ?", (account_id,)).fetchone()
            if row is None:
                state = self._default_state()
                connection.execute(
                    "INSERT INTO local_social_state(account_id,state_json,updated_at) VALUES (?,?,?)",
                    (account_id, json.dumps(state, separators=(",", ":")), self._now()),
                )
                return state
            try:
                value = json.loads(row["state_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                value = {}
            state = value if isinstance(value, dict) else {}
            defaults = self._default_state()
            for key, default in defaults.items():
                if key not in state:
                    state[key] = default
            state.setdefault("pvp", {}).setdefault("rating", 1000)
            state["pvp"].setdefault("wins", 0); state["pvp"].setdefault("losses", 0); state["pvp"].setdefault("matches", [])
            state["pvp"].setdefault("active", None)
            state["pvp"].setdefault("lastResult", None)
            return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_social_state SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), ensure_ascii=False, separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    def _request(self, body: Body, type_name: str) -> dict[str, Any]:
        """Accept the client's protobuf requests and the local JSON seam."""
        payload = _json(body)
        if payload or not body or isinstance(body, str):
            return payload
        try:
            request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(type_name))()
            request.ParseFromString(bytes(body))
            return json_format.MessageToDict(request)
        except (ValueError, KeyError, TypeError):
            return {}
        except Exception:  # Malformed protobuf is rejected by ordinary field validation.
            return {}

    def _base_push(self, profile, push_type, *params):
        notifier = getattr(self.game, "base_push_notifier", None)
        if notifier is not None:
            notifier(profile, push_type, tuple(str(value) for value in params))

    def _message_push(self, profile, name, payload):
        notifier = getattr(self.game, "social_message_notifier", None)
        if notifier is not None:
            notifier(profile, name, payload)

    def _applications(self, profile, state):
        """Generate a bounded daily batch; rejection/restart cannot reroll it."""
        unavailable = set(state.get("friends", [])) | set(state.get("blocked", []))
        pending = list(dict.fromkeys(str(pid) for pid in state.get("applications", [])
                                    if pid not in unavailable and self.bots.get(str(pid))))
        old_pending = list(state.get("applications", []))
        day = self._day(self._now())
        due = period_due(state.get("applicationDay"), day)
        added = []
        if due:
            bots = self.bots.all()
            offset = (int(profile["account_id"]) + day * 2) % len(bots)
            candidates = bots[offset:] + bots[:offset]
            limit = max(0, _int(state.get("friendApplyLevelLimit")))
            available = [bot["playerId"] for bot in candidates
                         if bot["playerId"] not in unavailable and bot["playerId"] not in pending
                         and _int(bot.get("level")) >= limit]
            added = available[:max(0, min(2, misc_value("maxFriendApplyListNum", 50) - len(pending)))]
            pending.extend(added)
            state["applicationDay"] = day
        state["applications"] = pending
        if due or pending != old_pending:
            self._save(profile, state)
        if added:
            self._base_push(profile, 33)
        return pending

    def friend_apply_level_limit(self, profile):
        with self._lock, self._process_lock():
            return _int(self._state(profile).get("friendApplyLevelLimit"))

    WORLD_SUBCHANNELS = 10
    WORLD_BOT_INTERVAL = 60

    def _channel_info(self, state):
        return {"channel": max(1, min(self.WORLD_SUBCHANNELS, _int(state.get("worldSubChannel"), 1))),
                "totalChannel": self.WORLD_SUBCHANNELS}

    def _append_chat(self, state, sender, content, channel, *, target="", guild_id=0):
        messages = state.setdefault("channelMessages", [])
        next_id = max(_int(state.get("nextChannelMessageId"), 1),
                      max((_int(item.get("id")) for item in messages), default=0) + 1)
        entry = {"id": next_id, "content": content, "time": self._now(),
                 "senderId": str(sender.get("player_id", sender.get("playerId", ""))),
                 "sender": str(sender.get("name", "")), "channel": channel,
                 "subChannelId": self._channel_info(state)["channel"] if channel == 1 else 0}
        if target:
            entry["targetId"] = target
        if guild_id:
            entry["guildId"] = guild_id
        messages.append(entry)
        del messages[:-100]
        state["nextChannelMessageId"] = next_id + 1
        return entry

    def _world_bot(self, profile, state, *, force=False):
        now = self._now()
        previous = state.get("worldBotAt")
        if not force and previous is not None and now - _int(previous) < self.WORLD_BOT_INTERVAL:
            return None
        available = [bot for bot in self.bots.all() if bot["playerId"] not in state.get("blocked", [])]
        if not available:
            return None
        bot = available[(now // self.WORLD_BOT_INTERVAL + int(profile["account_id"])) % len(available)]
        lines = ("今天也一起挑战关卡吧！", "需要助战的话，可以在好友列表找到我。", "准备好队伍，出发吧！")
        message = self._append_chat(state, bot, lines[(now // 60) % len(lines)], 1)
        state["worldBotAt"] = max(now, _int(previous, now))
        return message

    def tick(self, profile, *, connected=False, refresh_applications=True):
        """Call on login with connected=True, then normally for online players."""
        with self._lock, self._process_lock():
            state = self._state(profile)
            if refresh_applications:
                self._applications(profile, state)
            message = self._world_bot(profile, state, force=connected)
            if message:
                self._save(profile, state)
            if connected:
                self._message_push(profile, "ScWorldChatSubChannel", self._channel_info(state))
            if message:
                self._message_push(profile, "WorldMessage", message)

    def set_world_subchannel(self, profile, target_channel):
        channel = _int(target_channel)
        if not 1 <= channel <= self.WORLD_SUBCHANNELS:
            return 400, {"error": "invalid_world_subchannel"}
        with self._lock, self._process_lock():
            state = self._state(profile)
            state["worldSubChannel"] = channel
            message = self._world_bot(profile, state, force=True)
            self._save(profile, state)
            info = self._channel_info(state)
            self._message_push(profile, "ScWorldChatSubChannel", info)
            if message:
                self._message_push(profile, "WorldMessage", message)
            return 200, info

    @staticmethod
    def _route(path: str) -> str:
        return urlsplit(path or "").path.rstrip("/").casefold() or "/"

    def _summary(self, bot: Mapping[str, Any], is_friend: bool) -> dict[str, Any]:
        features = getattr(self.game, "profile_features", None)
        if features is not None:
            return features._bot_summary(bot, is_friend)
        return {"playerId": str(bot["playerId"]), "name": str(bot["name"]),
                "level": int(bot.get("level", 1)), "isInRelation": bool(is_friend)}

    # ------------------------------------------------------------ locking
    _held = threading.local()

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        """``shared_process_lock`` that a thread may re-enter.

        The sidecar file lock is not re-entrant, so a module that calls
        :meth:`record_assist_used` from inside another social operation must
        not lock the file twice.  Taken after the game lock.
        """

        held = getattr(self._held, "paths", None)
        if held is None:
            held = self._held.paths = {}
        key = str(self.game.db_path)
        if held.get(key):
            held[key] += 1
            try:
                yield
            finally:
                held[key] -= 1
            return
        with shared_process_lock(self.game.db_path):
            held[key] = 1
            try:
                yield
            finally:
                held[key] = 0

    # ------------------------------------------------------ relation points
    def _day(self, now: int) -> int:
        return policy_for(self.game).day_index(now)

    # Supports borrowed by bots per day (deterministic per account and day).
    BOT_AURA_USES_PER_DAY = (0, 1, 1, 2, 2, 3)
    # A long absence does not pile up more than this many days of bot uses.
    BOT_AURA_CATCHUP_DAYS = 7

    def _relation(self, state: dict[str, Any]) -> dict[str, Any]:
        relation = state.get("relationPoint")
        if not isinstance(relation, dict):
            relation = {}
            state["relationPoint"] = relation
        for key in ("pointPool", "canRecvCnt", "auraCanRecvCnt", "todayTotalGet", "day"):
            relation[key] = _int(relation.get(key))
        if not isinstance(relation.get("lastUse"), dict):
            relation["lastUse"] = {}
        today = self._day(self._now())
        if period_due(relation["day"], today):
            relation["day"] = today
            relation["todayTotalGet"] = 0
        return relation

    @staticmethod
    def _assist_points(is_friend: bool) -> int:
        if is_friend:
            return misc_value("auraRelationPointAddFriends", 25)
        return misc_value("auraRelationPointAddStranger", 20)

    def _aura_support(self, profile: Mapping[str, Any]) -> tuple[int, int]:
        """(auraActorUid, time it was set) from the MySpace page state."""

        features = getattr(self.game, "profile_features", None)
        if features is None:
            return 0, 0
        try:
            page = features._state(profile)
        except Exception:
            return 0, 0
        return _int(page.get("auraActorUid")), _int(page.get("auraActorSince"))

    def _simulate_bot_aura_uses(self, profile: Mapping[str, Any], state: dict[str, Any],
                                relation: dict[str, Any]) -> bool:
        """Bots borrowing the player's support feed auraCanRecvCnt/pointPool.

        On the retired service other players picking your support earned you
        relation points (ApiRelationRecvRelationPoint.aura_can_recv_cnt).
        Locally the bots do it: a fixed number of uses per day at fixed times
        from a per-account seed, so replays and restarts agree.  Only a player
        who set a support character (MySpace auraActorUid) can be borrowed.
        Returns whether the relation state changed.
        """

        now = self._now()
        since = relation.get("botSimUntil")
        aura_uid, aura_since = self._aura_support(profile)
        if since is None or aura_uid <= 0:
            changed = since != now
            relation["botSimUntil"] = now
            return changed
        since = max(_int(since), aura_since, now - self.BOT_AURA_CATCHUP_DAYS * _DAY)
        if since >= now:
            return False
        friends = {str(item) for item in state.get("friends", [])}
        blocked = {str(item) for item in state.get("blocked", [])}
        candidates = [bot for bot in self.bots.all() if bot["playerId"] not in blocked]
        account = int(profile["account_id"])
        for day in range(self._day(since), self._day(now) + 1):
            rng = random.Random(f"aura:{account}:{day}")
            start = policy_for(self.game).day_start_from_index(day)
            for _ in range(rng.choice(self.BOT_AURA_USES_PER_DAY)):
                moment = start + rng.randrange(_DAY)
                bot = candidates[rng.randrange(len(candidates))] if candidates else None
                if bot is not None and since < moment <= now:
                    relation["pointPool"] += self._assist_points(bot["playerId"] in friends)
                    relation["auraCanRecvCnt"] += 1
        relation["botSimUntil"] = now
        return True

    @staticmethod
    def _relation_counters(relation: Mapping[str, Any]) -> dict[str, int]:
        return {
            "todayTotalGet": _int(relation.get("todayTotalGet")),
            "auraCanRecvCnt": _int(relation.get("auraCanRecvCnt")),
            "canRecvCnt": _int(relation.get("canRecvCnt")),
            "pointPool": _int(relation.get("pointPool")),
        }

    def _relation_point(self, method: str, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """``fci/relationpoint/`` (ApiRelationRecvRelationPoint).

        GET: the counters Window_Friend reads to light the claim button
        (pointPool / canRecvCnt / auraCanRecvCnt > 0 while todayTotalGet is
        below Misc.relationPointDailyLimit).  POST: claim the pool up to
        today's cap; the reply carries resChange (DBH.ResChange) and
        getCntThisTime.  Points above the cap stay pooled for the next day.
        """

        relation = self._relation(state)
        self._simulate_bot_aura_uses(profile, state, relation)
        if method == "GET":
            self._save(profile, state)
            return 200, self._relation_counters(relation)
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
        limit = misc_value("relationPointDailyLimit", 400)
        amount = max(0, min(relation["pointPool"], limit - relation["todayTotalGet"]))
        changes: list[dict[str, Any]] = []
        if amount > 0:
            changes = grant_rewards(self.game, profile, [{"type": RES_RELATION_POINT, "id": 0, "param": 0, "count": amount}])
            relation["pointPool"] -= amount
            relation["todayTotalGet"] += amount
            relation["canRecvCnt"] = 0
            relation["auraCanRecvCnt"] = 0
        self._save(profile, state)
        return 200, {"resChange": changes, "getCntThisTime": amount, **self._relation_counters(relation)}

    def record_assist_used(self, profile: Mapping[str, Any], player_id: Any) -> dict[str, Any]:
        """Record that the player fought with ``player_id``'s support.

        For the dungeon module, once per battle that used a support
        (ApiDungeon.aura_player_id).  Adds Misc.auraRelationPointAddFriends
        (friend) or auraRelationPointAddStranger (stranger) to the relation
        point pool and starts the friend's Misc.auraCooldown (reported as
        lastUseTime in the support list; the client refuses friends still in
        cooldown, so a reuse inside it earns nothing).  Unknown, blocked or
        empty ids are ignored.  Returns ``{"playerId", "points", "stranger"}``.
        """

        target = str(player_id or "").strip()
        result: dict[str, Any] = {"playerId": target, "points": 0, "stranger": True}
        if not target or target == str(profile.get("player_id", "")) or self.bots.get(target) is None:
            return result
        with self._lock, self._process_lock():
            state = self._state(profile)
            if target in {str(item) for item in state.get("blocked", [])}:
                return result
            relation = self._relation(state)
            is_friend = target in {str(item) for item in state.get("friends", [])}
            result["stranger"] = not is_friend
            now = self._now()
            if is_friend:
                last = _int(relation["lastUse"].get(target))
                if last and now - last < misc_value("auraCooldown", 14400):
                    return result
                relation["lastUse"][target] = now
            points = self._assist_points(is_friend)
            relation["pointPool"] += points
            relation["canRecvCnt"] += 1
            self._save(profile, state)
            result["points"] = points
            return result

    def redpoint_fields(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """``relationPointRedpoint`` for ApiRedPointData (fci/redpoint/data)."""

        with self._lock, self._process_lock():
            state = self._state(profile)
            relation = self._relation(state)
            if self._simulate_bot_aura_uses(profile, state, relation):
                self._save(profile, state)
            pending = relation["pointPool"] > 0 or relation["canRecvCnt"] > 0 or relation["auraCanRecvCnt"] > 0
            room = relation["todayTotalGet"] < misc_value("relationPointDailyLimit", 400)
            return {"relationPointRedpoint": bool(pending and room)}

    def _blacklist(self, method: str, route: str, path: str, payload: Mapping[str, Any],
                   profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """``fci/blacklist/`` as Common_FriendUtil uses it.

        GET: PlayerSummaryInfo list.  POST {targetId}: the blocked player's
        summary (BlockPlayer inserts it into the cached list); blocking also
        ends the friendship.  DELETE ``fci/blacklist/<id>``: unblock.
        """

        blocked = [str(item) for item in state.setdefault("blocked", [])]
        tail = unquote(urlsplit(path or "").path.strip("/")[len("fci/blacklist"):].strip("/"))
        if method == "GET" and not tail:
            bots = (self.bots.get(player_id) for player_id in blocked)
            return 200, [self._summary(bot, False) for bot in bots if bot is not None]
        if method == "POST" and not tail:
            target = str(payload.get("targetId", payload.get("playerId", ""))).strip()
            bot = self.bots.get(target)
            if bot is None:
                return 404, {"error": "local_bot_not_found", "playerId": target}
            if target not in blocked:
                if len(blocked) >= misc_value("maxBlackContactNum", 200):
                    return 409, {"error": "blacklist_full", "playerId": target}
                blocked.append(target)
            state["blocked"] = blocked
            state["friends"] = [item for item in state.get("friends", []) if str(item) != target]
            pending = target in state.get("applications", [])
            state["applications"] = [item for item in state.get("applications", []) if item != target]
            self._save(profile, state)
            if pending:
                self._base_push(profile, 34, target)
            return 200, self._summary(bot, False)
        if method == "DELETE" and tail:
            state["blocked"] = [item for item in blocked if item != tail]
            self._save(profile, state)
            return 200, {}
        return 405, {"error": "method_not_allowed", "message": "GET/POST fci/blacklist/ or DELETE fci/blacklist/<id>"}

    def _client_relations(self, method: str, route: str, path: str, body: Body,
                          profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """Friend window routes as the original client calls them.

        Window_Friend / WidgetPlayer read plain JSON: friendlist and
        recommendrelation are lists of PlayerSummaryInfo, playersummary is one
        summary (playerId "" = not found), relationpoint has the point
        counters.  Requests are plain tables ({targetId}, {targetList,
        isAgree}).  Local bots accept a friend request immediately.
        """

        friends = [str(item) for item in state.setdefault("friends", [])]
        blocked = {str(item) for item in state.setdefault("blocked", [])}
        payload = self._request(body, "ApiRelationApplyList" if route.startswith("/fci/applylist") else "ApiRelationApplyFriend")
        if route == "/fci/friendlist":
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            bots = (self.bots.get(player_id) for player_id in friends if player_id not in blocked)
            return 200, [self._summary(bot, True) for bot in bots if bot is not None]
        if route.startswith("/fci/friendlist/"):
            # Window_PlayerOption: DELETE fci/friendlist/<playerId> removes the
            # friend (the client drops its cached row itself).
            target = unquote(urlsplit(path or "").path.rstrip("/").rsplit("/", 1)[-1])
            if method == "DELETE":
                state["friends"] = [item for item in friends if item != target]
                self._save(profile, state)
                return 200, {}
            if method == "GET":
                bot = self.bots.get(target) if target in friends and target not in blocked else None
                return 200, self._summary(bot, True) if bot is not None else {"playerId": ""}
            return 405, {"error": "method_not_allowed", "message": "GET or DELETE required"}
        if route == "/fci/blacklist" or route.startswith("/fci/blacklist/"):
            return self._blacklist(method, route, path, payload, profile, state)
        if route == "/fci/recommendrelation":
            return 200, [
                self._summary(bot, False) for bot in self.bots.all()
                if bot["playerId"] not in friends and bot["playerId"] not in blocked
            ][:10]
        if route == "/fci/relationpoint":
            return self._relation_point(method, profile, state)
        if route == "/fci/socialfriend":
            return 200, []
        if route == "/fci/applyfriend":
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            target = str(payload.get("targetId", payload.get("playerId", ""))).strip()
            bot = self.bots.get(target)
            if bot is None:
                return 404, {"error": "local_bot_not_found", "playerId": target}
            if target in blocked:
                return 409, {"error": "local_bot_blocked", "playerId": target}
            if target not in friends:
                if len(friends) >= misc_value("maxFriendNum", 200):
                    return 409, {"error": "friend_list_full", "errorCode": 346}
                friends.append(target)
                state["friends"] = friends
                pending = target in state.get("applications", [])
                state["applications"] = [item for item in state.get("applications", []) if item != target]
                self._save(profile, state)
                if pending:
                    self._base_push(profile, 34, target)
                self._base_push(profile, 35, target)
            return 200, self._summary(bot, True)
        if route == "/fci/applylist" or route.startswith("/fci/applylist/"):
            if method in {"POST", "PATCH", "PUT"} and "friendApplyLevelLimit" in payload:
                state["friendApplyLevelLimit"] = max(0, min(99, _int(payload.get("friendApplyLevelLimit"))))
                self._save(profile, state)
                return 200, {}
            pending = self._applications(profile, state)
            if method == "GET":
                return 200, [self._summary(self.bots.get(target), False) for target in pending]
            if method not in {"POST", "PATCH", "PUT", "DELETE"}:
                return 405, {"error": "method_not_allowed"}
            targets = payload.get("targetList")
            if not isinstance(targets, list):
                tail = unquote(urlsplit(path).path.rstrip("/").rsplit("/", 1)[-1])
                targets = [payload.get("targetId", tail if tail != "applylist" else "")]
            targets = list(dict.fromkeys(str(item) for item in targets if str(item or "").strip()))
            removed = [target for target in targets if target in pending]
            added = []
            if payload.get("isAgree"):
                if len(friends) + len(removed) > misc_value("maxFriendNum", 200):
                    return 409, {"error": "friend_list_full", "errorCode": 346}
                for target in removed:
                    if target not in friends and target not in blocked:
                        friends.append(target)
                        added.append(target)
            state["friends"] = friends
            state["applications"] = [target for target in pending if target not in removed]
            self._save(profile, state)
            for target in removed:
                self._base_push(profile, 34, target)
            for target in added:
                self._base_push(profile, 35, target)
            bot = self.bots.get(targets[0]) if targets else None
            return 200, self._summary(bot, targets[0] in friends) if bot else {"playerId": ""}
        if route.startswith("/fci/playersummary"):
            key = unquote(urlsplit(path or "").path.rstrip("/").rsplit("/", 1)[-1])
            if key.casefold() == str(profile.get("player_id", "")).casefold():
                features = getattr(self.game, "profile_features", None)
                if features is not None:
                    return 200, features._player_summary(profile, features._state(profile))
            bot = self.bots.get(key) or next((b for b in self.bots.all() if b["name"] == key), None)
            if bot is None:
                return 200, {"playerId": ""}
            return 200, self._summary(bot, bot["playerId"] in friends)
        return None

    def _friend(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        # Preserve the historical protobuf seams handled by LocalSystems;
        # the JSON social surface uses the explicit friend/ routes below.
        if route in {"/fci/friendlist", "/fci/relation/applyfriend"}:
            return None
        if not any(token in route for token in ("/fci/friend/", "/fci/socialfriend", "/fci/relation/friend/")) and route not in {"/fci/friend", "/fci/relation/friend"}:
            return None
        if not any(token in route for token in ("list", "recommend", "apply", "recall", "delete", "block", "like")) and route not in {"/fci/friend", "/fci/relation/friend"}:
            return None
        friends = [str(item) for item in state.setdefault("friends", [])]
        blocked = {str(item) for item in state.setdefault("blocked", [])}
        payload = _json(body)
        if method == "GET" and ("list" in route or "recommend" in route or route in {"/fci/friend", "/fci/relation/friend"}):
            ids = [item for item in friends if item not in blocked] if "recommend" not in route else [bot["playerId"] for bot in self.bots.all() if bot["playerId"] not in blocked]
            rows = []
            for player_id in ids:
                bot = self.bots.get(player_id)
                if bot is not None:
                    bot["isFriend"] = player_id in friends
                    rows.append(bot)
            return 200, {"friends": rows, "result": rows, "recommend": rows if "recommend" in route else []}
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        target = str(payload.get("playerId", payload.get("player_id", payload.get("targetId", "")))).strip()
        if not target:
            target = self.bots.pick(int(profile["account_id"]))["playerId"]
        bot = self.bots.get(target)
        if bot is None:
            return 404, {"error": "local_bot_not_found"}
        if "recall" in route or "delete" in route:
            state["friends"] = [value for value in friends if value != target]
            self._save(profile, state)
            return 200, {"ok": True, "playerId": target, "removed": True}
        if "block" in route:
            blocked = set(str(item) for item in state.setdefault("blocked", [])); blocked.add(target)
            state["blocked"] = sorted(blocked); state["friends"] = [value for value in friends if value != target]
            self._save(profile, state)
            return 200, {"ok": True, "playerId": target, "blocked": True}
        if target in blocked:
            return 409, {"error": "local_bot_blocked", "playerId": target}
        if "like" in route:
            state["likes"] = int(state.get("likes", 0)) + 1
            self._save(profile, state)
            return 200, {"ok": True, "playerId": target, "likeCount": state["likes"]}
        if target not in friends:
            friends.append(target); state["friends"] = friends
            self._save(profile, state)
        return 200, {"ok": True, "playerId": target, "friend": bot, "isFriend": True}

    # Fixed bot strangers always offered in the support list (besides friends).
    ASSIST_STRANGERS = 6

    def _assist(self, method: str, route: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        """Support (助战) list for the dungeon formation.

        Window_ActorGroupBase posts ``ApiDungeonAssistants {token}`` and reads
        the reply as a list of DungeonAssistantInfo whose ``token`` must echo
        the request.  Friends are offered first (stranger=false), then a fixed
        set of bot strangers.  Aura actor/quality pairs exist in the client's
        ActorQualityInfo table (actors 1001-1008, quality 1-4).
        """

        if not any(token in route for token in ("assistant", "assistants", "dungeonassist")):
            return None
        if method not in {"GET", "POST"}:
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        payload = _json(body)
        token = 0
        try:
            token = max(0, int(payload.get("token", 0) or 0))
        except (TypeError, ValueError):
            token = 0
        if not token and body:
            message_type = getattr(self.game, "_types", {}).get("ApiDungeonAssistants")
            if message_type is not None:
                try:
                    request = message_type()
                    request.ParseFromString(bytes(body) if not isinstance(body, str) else body.encode())
                    token = int(request.token)
                except Exception:
                    token = 0
        state = self._state(profile)
        friend_ids = [str(item) for item in state.get("friends", [])]
        blocked = {str(item) for item in state.get("blocked", [])}
        last_use = self._relation(state)["lastUse"]
        features = getattr(self.game, "profile_features", None)
        entries: list[tuple[Mapping[str, Any], bool]] = []
        for player_id in friend_ids:
            bot = self.bots.get(player_id)
            if bot is not None and player_id not in blocked:
                entries.append((bot, False))
        strangers = [bot for bot in self.bots.all() if bot["playerId"] not in friend_ids and bot["playerId"] not in blocked]
        entries.extend((bot, True) for bot in strangers[: self.ASSIST_STRANGERS])
        rows = []
        for bot, stranger in entries:
            level = int(bot.get("level", 1))
            rows.append({
                "playerId": str(bot["playerId"]),
                "name": str(bot["name"]),
                "level": level,
                # Friends cool down for Misc.auraCooldown after a battle
                # used them (Window_WidgetAuraPlayer counts from this).
                "lastUseTime": 0 if stranger else _int(last_use.get(str(bot["playerId"]))),
                "stranger": stranger,
                "auraActorId": int(bot["actors"][0]),
                "headPhoto": features.bot_head_photo(bot) if features is not None else 1,
                "auraActorQuality": max(1, min(4, 1 + level // 15)),
                "medal": 0,
                "token": token,
            })
        # ActorGroupBase.ResetAuraSelection reads the reply as a plain list
        # (``#assistants``, ``assistants[1].token``); a typed
        # ApiDungeonAssistants.result would be stored as {result = {...}} and
        # the support column would stay empty.
        return 200, rows

    def _player_name(self, player_id, profile):
        if player_id == str(profile.get("player_id", "")):
            return str(profile.get("name", ""))
        bot = self.bots.get(player_id)
        if bot:
            return str(bot["name"])
        with closing(self.game._connect()) as connection:
            row = connection.execute("SELECT name FROM game_profiles WHERE player_id=?", (player_id,)).fetchone()
        return str(row["name"]) if row else ""

    def _board(self, method, path, body, profile, state):
        parts = [unquote(part) for part in urlsplit(path).path.strip("/").split("/")]
        if len(parts) not in (3, 4) or not parts[2]:
            return 400, {"error": "invalid_message_path"}
        target = parts[2]
        boards = state.setdefault("boards", {})
        board = boards.setdefault(target, boards.pop(target.casefold(), []) if target != target.casefold() else [])
        # Old rows used playerId/name/time and recycled IDs after retention.
        # Repair duplicates once, retain content and persist the high-water mark.
        counters = state.setdefault("nextBoardMessageId", {})
        next_id = max(_int(counters.get(target), 1), max((_int(row.get("id")) for row in board), default=0) + 1)
        seen = set()
        for row in board:
            message_id = _int(row.get("id"))
            if message_id <= 0 or message_id in seen:
                message_id = next_id
                next_id += 1
            seen.add(message_id)
            row.update(id=message_id, sender=str(row.get("sender", row.get("playerId", ""))),
                       senderName=str(row.get("senderName", row.get("name", ""))),
                       to=str(row.get("to", "")), toName=str(row.get("toName", "")),
                       createTime=_int(row.get("createTime", row.get("time", row.get("createdAt", 0)))),
                       content=str(row.get("content", row.get("text", ""))))
        counters[target] = next_id
        if method == "GET" and len(parts) == 3:
            self._save(profile, state)
            query = parse_qs(urlsplit(path).query, keep_blank_values=True)
            def query_int(*names: str, default: int = 0) -> int:
                for name in names:
                    values = query.get(name)
                    if values:
                        return _int(values[0], default)
                return default
            # The client requests pages with perPage/page (some builds use
            # pageNo/pageSize).  When no pagination is requested preserve the
            # historical full-list response used by the profile screen.
            paged = any(name in query for name in ("page", "pageNo", "pageIndex", "perPage", "pageSize", "limit"))
            if not paged:
                return 200, list(board)
            page = max(1, query_int("page", "pageNo", "pageIndex", default=1))
            per_page = max(1, query_int("perPage", "pageSize", "limit", default=20))
            start = (page - 1) * per_page
            return 200, list(board[start:start + per_page])
        if method == "DELETE" and len(parts) == 4:
            message_id = _int(parts[3], -1)
            row = next((row for row in board if row["id"] == message_id), None)
            if row is None:
                return 404, {"error": "message_not_found"}
            me = str(profile.get("player_id", ""))
            if target != me and row["sender"] != me:
                return 403, {"error": "message_delete_forbidden"}
            board.remove(row)
            self._save(profile, state)
            return 200, {}
        if method != "POST" or len(parts) != 3:
            return 405, {"error": "method_not_allowed"}
        payload = self._request(body, "ApiPlayerMessage")
        content = str(payload.get("content", payload.get("message", payload.get("text", "")))).strip()
        if not content:
            return 400, {"error": "empty_message"}
        if len(content) > 128:
            return 400, {"error": "message_too_long"}
        recipient = str(payload.get("to") or "")
        entry = {"id": next_id, "sender": str(profile.get("player_id", "")),
                 "senderName": str(profile.get("name", "")), "content": content,
                 "to": recipient, "toName": self._player_name(recipient, profile) if recipient else "",
                 "createTime": self._now()}
        counters[target] = next_id + 1
        board.insert(0, entry)
        del board[50:]
        self._save(profile, state)
        return 200, entry

    def _channel_chat(self, payload, profile, state):
        channel = _int(payload.get("channel"))
        if channel not in (1, 3, 5):
            return 400, {"error": "invalid_chat_channel", "errorCode": 65}
        if _int(profile.get("level"), 1) < 12:
            return 403, {"error": "chat_level_required", "requiredLevel": 12, "errorCode": 21}
        content = str(payload.get("content", "")).strip()
        if not content:
            return 400, {"error": "empty_message", "errorCode": 71}
        if len(content) > 128:
            return 400, {"error": "message_too_long", "errorCode": 340}
        target = str(payload.get("targetId", ""))
        guild_id = 0
        bot = None
        if channel == 1:
            selected = self._channel_info(state)["channel"]
            if _int(payload.get("worldSubChannel"), selected) != selected:
                return 409, {"error": "world_subchannel_mismatch", "channel": selected}
        elif channel == 3:
            if target == str(profile.get("player_id", "")):
                return 400, {"error": "private_chat_to_self", "errorCode": 66}
            bot = self.bots.get(target)
            if bot is None:
                return 404, {"error": "local_bot_not_found", "errorCode": 16}
            if target in state.get("blocked", []):
                return 409, {"error": "local_bot_blocked"}
        elif channel == 5:
            guild = getattr(self.game, "guild", None)
            if guild is not None:
                guild_id, bot_ids = guild._run(profile, lambda conn, shared, player, now, notices:
                    (_int(player.get("guildId")), list(shared["guilds"].get(str(player.get("guildId")), {}).get("botIds", []))))
                bot = next((self.bots.get(pid) for pid in bot_ids if pid not in state.get("blocked", [])), None)
            if not guild_id:
                return 409, {"error": "guild_required", "errorCode": 68}
        last_sent = state.setdefault("chatLastSent", {})
        now = self._now()
        interval = 10 if channel == 1 else 1
        previous = last_sent.get(str(channel))
        if previous is not None and now - _int(previous) < interval:
            return 429, {"error": "chat_cooldown", "retryAfter": interval - (now - _int(previous)), "errorCode": 64}
        last_sent[str(channel)] = now
        entry = self._append_chat(state, profile, content, channel, target=target, guild_id=guild_id)
        response = None
        if channel == 1:
            response = self._world_bot(profile, state, force=True)
        elif bot:
            response = self._append_chat(state, bot, "收到，一起加油！", channel,
                                         target=str(profile.get("player_id", "")), guild_id=guild_id)
        self._save(profile, state)
        if channel == 1:
            self._message_push(profile, "WorldMessage", entry)
            if response:
                self._message_push(profile, "WorldMessage", response)
        else:
            # ChatRoom.PostDataRespAdapter appends the sender's private
            # message under the requested target using result.newContent.
            # Base24 is inbound-only: ChatUtil indexes it by senderId, so
            # echoing ourselves there creates a conversation with ourselves.
            # Guild messages have no response adapter and still need an echo.
            push_type = 24 if channel == 3 else 30
            if channel == 5:
                self._base_push(profile, push_type,
                                entry["senderId"], entry["sender"], entry["content"], entry["time"])
            if response:
                self._base_push(profile, push_type,
                                response["senderId"], response["sender"], response["content"], response["time"])
        if channel == 3:
            return 200, {"newContent": content}
        if channel == 1:
            # The native world-chat adapter appends this response directly.
            return 200, entry
        return 200, {}

    def _chat(self, method: str, route: str, path: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        if not any(token in route for token in ("/chat", "/danmu", "/player-message", "/recent-contact")):
            return None
        messages = state.setdefault("messages", [])
        if "/player-message" in route:
            return self._board(method, path, body, profile, state)
        if "/danmu" in route and method == "POST":
            # Danmu_CartoonController posts a wireformat ApiDanmu every few
            # seconds (empty content = poll) and expects a list of comments.
            # Bullet comments are not distributed locally: an empty list.
            return 200, []
        if method == "GET":
            if "recent-contact" in route:
                contacts = [self.bots.get(item) for item in state.get("friends", [])]
                return 200, {"contacts": [item for item in contacts if item is not None]}
            return 200, {"messages": messages[-50:], "danmu": messages[-50:]}
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        payload = self._request(body, "ApiChat")
        if "channel" in payload or "content" in payload:
            return self._channel_chat(payload, profile, state)
        text = str(payload.get("message", payload.get("text", payload.get("content", "")))).strip()
        if not text:
            return 400, {"error": "empty_message"}
        if len(text) > 128:
            return 400, {"error": "message_too_long"}
        # ``len(messages)`` stops being monotonic after the 100-message
        # retention window trims the oldest rows.  Persist a counter so a
        # reconnecting client never receives duplicate message IDs.
        next_id = max(
            int(state.get("nextMessageId", 1)),
            max((int(item.get("id", 0)) for item in messages if isinstance(item, Mapping)), default=0) + 1,
        )
        message = {"id": next_id, "playerId": str(profile.get("player_id", "local")), "name": str(profile.get("name", "Local Guest")), "text": text, "createdAt": self._now()}
        state["nextMessageId"] = next_id + 1
        messages.append(message); del messages[:-100]
        self._save(profile, state)
        return 200, {"ok": True, "message": message, "messages": messages[-50:]}

    def _pvp(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        if route in {"/fci/asyncpvprecord", "/fci/asyncpvpmatch", "/fci/match_mirror"}:
            return None
        if not any(token in route for token in ("/fci/asyncpvp/", "/fci/match-mirror/", "/fci/initasyncpvprobot")):
            return None
        pvp = state.setdefault("pvp", {"rating": 1000, "wins": 0, "losses": 0, "matches": [], "active": None, "lastResult": None})
        pvp.setdefault("active", None); pvp.setdefault("lastResult", None)
        if method == "GET" and any(token in route for token in ("record", "info", "rank")):
            return 200, {"rating": int(pvp.get("rating", 1000)), "wins": int(pvp.get("wins", 0)), "losses": int(pvp.get("losses", 0)), "matches": list(pvp.get("matches", []))[-20:]}
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        payload = _json(body)
        active = pvp.get("active")
        # A match request is safe to retry.  Return the same durable ticket
        # instead of silently creating another opponent.
        if not any(token in route for token in ("battle", "settle", "result")):
            if isinstance(active, Mapping):
                opponent_info = active.get("opponentInfo")
                if not isinstance(opponent_info, Mapping):
                    opponent_info = self.bots.get(str(active.get("opponent", ""))) or {"playerId": str(active.get("opponent", "local-bot-001"))}
                match_payload = {
                    "opponent": dict(opponent_info),
                    "ticket": int(active.get("ticket", 0)),
                    "matchId": str(active.get("matchId", "")),
                }
                return 200, {"ok": True, "duplicate": True, "match": match_payload, "rating": int(pvp.get("rating", 1000))}
            opponent = self.bots.pick(int(profile["account_id"]), offset=len(pvp.get("matches", [])))
            ticket = len(pvp.get("matches", [])) + 1
            active = {
                "matchId": f"async-{int(profile['account_id'])}-{ticket}",
                "opponent": opponent["playerId"],
                "opponentInfo": opponent,
                "ticket": ticket,
                "createdAt": self._now(),
            }
            pvp["active"] = active
            self._save(profile, state)
            return 200, {"ok": True, "duplicate": False, "match": {"opponent": opponent, "ticket": ticket, "matchId": active["matchId"]}, "rating": pvp["rating"]}

        # Legacy callers sometimes send a settle request without a prior
        # match request.  Preserve that compatibility by creating one
        # pending ticket first, then settling it exactly once.
        supplied_id = str(payload.get("matchId", payload.get("match_id", payload.get("ticket", ""))) or "").strip()
        if not isinstance(active, Mapping):
            previous = pvp.get("lastResult")
            if isinstance(previous, Mapping) and (
                not supplied_id
                or supplied_id in {str(previous.get("matchId", "")), str(previous.get("ticket", ""))}
            ):
                return 200, {"ok": True, "duplicate": True, "result": dict(previous), "rating": int(pvp.get("rating", 1000))}
            opponent = self.bots.pick(int(profile["account_id"]), offset=len(pvp.get("matches", [])))
            ticket = len(pvp.get("matches", [])) + 1
            active = {
                "matchId": f"async-{int(profile['account_id'])}-{ticket}",
                "opponent": opponent["playerId"],
                "opponentInfo": opponent,
                "ticket": ticket,
                "createdAt": self._now(),
            }
            pvp["active"] = active
        if supplied_id and supplied_id not in {str(active.get("matchId")), str(active.get("ticket"))}:
            return 404, {"error": "async_match_not_found", "matchId": supplied_id}
        if any(token in route for token in ("battle", "settle", "result")):
            win = bool(payload.get("win", payload.get("isWin", True)))
            delta = 25 if win else -20
            pvp["rating"] = max(0, int(pvp.get("rating", 1000)) + delta)
            pvp["wins" if win else "losses"] = int(pvp.get("wins" if win else "losses", 0)) + 1
            match = {
                "matchId": str(active.get("matchId")),
                "opponent": str(active.get("opponent", "local-bot-001")),
                "win": win,
                "delta": delta,
                "rating": pvp["rating"],
                "time": self._now(),
            }
            pvp.setdefault("matches", []).append(match); del pvp["matches"][:-50]
            pvp["lastResult"] = dict(match)
            pvp["active"] = None
            self._save(profile, state)
            return 200, {"ok": True, "duplicate": False, "result": match, "rating": pvp["rating"]}
        return 400, {"error": "async_pvp_action_required"}

    def _rank(self, method: str, route: str, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        if not any(token in route for token in ("/rank", "ranking", "leaderboard", "ranklist")):
            return None
        if method != "GET":
            return 405, {"error": "method_not_allowed", "message": "GET required"}
        pvp = state.get("pvp", {})
        player = {"playerId": str(profile.get("player_id", "local")), "name": str(profile.get("name", "Local Guest")), "level": int(profile.get("level", 1)), "score": int(pvp.get("rating", 1000)), "isSelf": True}
        rows = [player] + [dict(bot, isSelf=False) for bot in self.bots.all()]
        rows.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("playerId"))))
        for index, row in enumerate(rows, 1):
            row["rank"] = index
        return 200, {"ranking": rows[:100], "result": rows[:100], "self": next(row for row in rows if row["isSelf"])}

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        # ``_state`` followed by a JSON update is one logical transaction.
        # The thread lock alone cannot protect a second local worker process.
        route = self._route(path)
        if method.upper() in {"GET", "HEAD"} and (route == "/fci/medal" or route.startswith("/fci/medal/")):
            # Social is dispatched before LocalSystems, whose medal GET answers
            # {"medals": [...]} for both forms; the client needs the STMedal
            # list (fci/medal/) and one STMedal (fci/medal/<id>).
            features = getattr(self.game, "profile_features", None)
            result = features.medal_reply(method, path, profile) if features is not None else None
            if result is not None:
                return result
        with self._lock, self._process_lock():
            method = method.upper(); state = self._state(profile)
            for handler in (
                lambda: self._client_relations(method, route, path, body, profile, state),
                lambda: self._friend(method, route, body, profile, state),
                lambda: self._assist(method, route, body, profile),
                lambda: self._chat(method, route, path, body, profile, state),
                lambda: self._pvp(method, route, body, profile, state),
                lambda: self._rank(method, route, profile, state),
            ):
                result = handler()
                if result is not None:
                    return result
            return None


__all__ = ["LocalBotPool", "LocalSocial"]
