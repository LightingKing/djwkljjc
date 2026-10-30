"""Persistent local player profile, notice and web activity compatibility.

The retired service exposed these endpoints as small protobuf resources.  The
local implementation keeps the data in the same SQLite database as the game
profile and only models fields the client can use.  It is deliberately a
local sandbox: names, pictures, collections, notice flags and vote counts do
not leave the machine and no remote web activity is contacted.
"""

from __future__ import annotations

from server.core.reset import policy_for, period_due

import json
import time
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

from google.protobuf import message_factory

try:  # Package import when local-server is installed as a package.
    from . import actors as actor_store
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store


Body = bytes | bytearray | str | None
Response = tuple[int, Any]


def _route(path: str) -> str:
    value = urlsplit(path or "").path or "/"
    return value.rstrip("/") or "/"


def _json(body: Body) -> dict[str, Any]:
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- client tables
# Complete tables exported from the original client by
# tools/export_client_tables.py (data/client-tables); the partial data/config
# captures are only a fallback.
_DATA_DIR = Path(__file__).resolve().parent / "data"

# NotePadEventType (DBNotePadInfo.RoleNotePadInfo.Event.type).
NOTEPAD_EVENT_GET_ACTOR = 1
NOTEPAD_EVENT_FAVOUR_LV_UP = 2
NOTEPAD_EVENT_GET_HEAD_PHOTO = 4
# SignboardUnlockType.
SIGNBOARD_UNLOCK_FAVOUR_LEVEL = 1
SIGNBOARD_UNLOCK_ACTOR_GOT = 2
SIGNBOARD_UNLOCK_ACTOR_EVER_GOT_AND_FAVOUR = 3
SIGNBOARD_UNLOCK_GIFT = 4
# Window_RoleNotepadDetailBoard: boards drawn with these actors are only
# shown as unlocked when the server lists them in unlockedSignboardId.
_SPECIAL_BOARD_ACTORS = {1401, 1409}
RES_PLAYER_HEAD_PHOTO = 2
RES_SIGNBOARD = 30
DEFAULT_HEAD_PHOTO = 1


@lru_cache(maxsize=None)
def table_rows(name: str) -> tuple[dict[str, Any], ...]:
    """Rows of one exported client table (missing fields = proto3 defaults)."""

    for folder in ("client-tables", "config"):
        try:
            payload = json.loads((_DATA_DIR / folder / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = payload.get("rows") if isinstance(payload, Mapping) else None
        if isinstance(rows, list) and rows:
            return tuple(row for row in rows if isinstance(row, dict))
    return ()


def misc_value(key: str, default: int = 0) -> int:
    rows = table_rows("Misc")
    return _int(rows[0].get(key, default), default) if rows else default


@lru_cache(maxsize=None)
def _actor_roles() -> dict[int, int]:
    return {_int(row.get("id")): _int(row.get("role")) for row in table_rows("ActorConfig") if row.get("id")}


@lru_cache(maxsize=None)
def notepad_role_ids() -> frozenset[int]:
    """Role ids that have a RoleNotePad (Role.favourActor)."""

    return frozenset(_int(row.get("id")) for row in table_rows("Role") if row.get("id") and row.get("favourActor"))


@lru_cache(maxsize=None)
def _favour_thresholds() -> dict[int, int]:
    """FavourLevel: favour needed to go from ``level - 1`` to ``level``."""

    return {_int(row.get("level")): _int(row.get("favour")) for row in table_rows("FavourLevel") if row.get("level")}


def actor_role(actor_id: Any) -> int:
    """ActorConfig.role of one character id (0 when unknown)."""

    return _actor_roles().get(_int(actor_id), 0)


def role_head_photo(role_id: Any) -> int:
    """The role's PlayerHeadPhoto (getWay 1, imageRoleId = role), else the default."""

    for row in table_rows("PlayerHeadPhoto"):
        if _int(row.get("getWay")) == 1 and _int(row.get("imageRoleId")) == _int(role_id):
            return _int(row.get("id"))
    return DEFAULT_HEAD_PHOTO


def _ledger_ids(profile: Mapping[str, Any], resource_type: int) -> set[int]:
    raw = profile.get("resources")
    return {
        _int(item.get("id")) for item in (raw if isinstance(raw, list) else [])
        if isinstance(item, Mapping) and _int(item.get("type")) == resource_type and _int(item.get("count")) > 0
    }


class LocalProfileFeatures:
    """Local player-info, notices, collections and web-vote state."""

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        with self._lock, closing(game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_profile_features (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _now(self) -> int:
        return int(self._clock())

    def _message(self, name: str):
        # ``playeraction.proto`` in the recovered APK has a damaged syntax
        # field.  The pool safely skips that file, so a handful of player
        # profile messages are unavailable even though adjacent messages are
        # usable.  Callers fall back to the same camelCase JSON shape instead
        # of turning a cosmetic profile request into a 500.
        try:
            return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()
        except Exception:
            return None

    def _request(self, body: Body, name: str):
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return None
        try:
            value = self._message(name)
            value.ParseFromString(raw)
            return value
        except Exception:
            return None

    @staticmethod
    def _default_state(profile: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "socialPicture": "",
            "sex": 0,
            "headPhoto": 1,  # PlayerHeadPhoto 1 (default avatar)
            "fightMedal": 0,
            "homeBackground": {"current": 0, "isRandom": False},
            "friendApplyLevelLimit": 0,
            "signboard": dict(profile.get("signboard") or {"signboard": 101, "unlockedSignboardId": [101], "checkSignboardTime": 0}),
            "readNotices": [],
            "sharedNotices": [],
            "noticeRewards": [],
            "notepad": {},
            "collections": {"actors": [], "equips": [], "novels": []},
            "webVote": {
                "roles": [
                    {"roleId": 1001, "roleName": "逢坂大河", "image": "", "voteCount": 0, "sortId": 1, "novel": "", "novelId": 1},
                    {"roleId": 1002, "roleName": "司波深雪", "image": "", "voteCount": 0, "sortId": 2, "novel": "", "novelId": 2},
                    {"roleId": 1003, "roleName": "桐乃", "image": "", "voteCount": 0, "sortId": 3, "novel": "", "novelId": 3},
                ],
                "ticketNum": 10,
                "bangNum": 0,
                "myVotes": {},
            },
            "share": {"weeklyRewarded": False, "actorRewardGot": 0, "weekKey": None},
            "jigsaw": {"cells": [], "received": False},
            "postcard": {"cells": [], "received": False},
            "clawCrane": {"draws": 0, "prizes": [], "bingo": []},
        }

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM local_profile_features WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                state = self._default_state(profile)
                connection.execute(
                    "INSERT OR IGNORE INTO local_profile_features(account_id,state_json,updated_at) VALUES(?,?,?)",
                    (account_id, json.dumps(state, ensure_ascii=False, separators=(",", ":")), self._now()),
                )
                return state
            try:
                state = json.loads(row["state_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                state = {}
            if not isinstance(state, dict):
                state = {}
            defaults = self._default_state(profile)
            changed = False
            for key, value in defaults.items():
                if key not in state:
                    state[key] = value
                    changed = True
            if _int(state.get("headPhoto")) < 0:  # older saves kept -1 (uint32 field)
                state["headPhoto"] = 0
                changed = True
            if changed:
                connection.execute(
                    "UPDATE local_profile_features SET state_json = ?, updated_at = ? WHERE account_id = ?",
                    (json.dumps(state, ensure_ascii=False, separators=(",", ":")), self._now(), account_id),
                )
            return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_profile_features SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), ensure_ascii=False, separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    def _set_profile_columns(self, profile: Mapping[str, Any], **values: Any) -> None:
        allowed = {"name", "level"}
        values = {key: value for key, value in values.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                f"UPDATE game_profiles SET {assignments}, updated_at = ? WHERE account_id = ?",
                [*values.values(), self._now(), int(profile["account_id"])],
            )

    def _set_signboard_profile(self, profile: Mapping[str, Any], signboard: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE game_profiles SET signboard_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(signboard), ensure_ascii=False, separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    def _base_info(self, profile: Mapping[str, Any], state: Mapping[str, Any]):
        result = self._message("ApiPlayerBaseInfo.result")
        if result is None:
            background = state.get("homeBackground") if isinstance(state.get("homeBackground"), Mapping) else {}
            # Plain object: HandleRemoteChanged("fci/baseinfo/") (BaseInfoChange
            # push) stores this reply in place of ScLoginEnd's baseInfo.
            return {
                "level": _int(profile.get("level"), 1), "name": str(profile.get("name", "")),
                "lastLoginTime": _int(profile.get("updated_at"), self._now()),
                "socialPicture": str(state.get("socialPicture", "")), "headPhoto": self._head_photo_value(state),
                "fightMedal": _int(state.get("fightMedal")), "sex": _int(state.get("sex")),
                "homeBackGround": {"current": _int(background.get("current")), "isRandom": bool(background.get("isRandom", False))},
                "friendApplyLevelLimit": self.game.social.friend_apply_level_limit(profile), "sandbox": True,
            }
        result.level = _int(profile.get("level"), 1)
        result.name = str(profile.get("name", ""))
        result.last_login_time = _int(profile.get("updated_at"), self._now())
        result.social_picture = str(state.get("socialPicture", ""))
        result.head_photo = self._head_photo_value(state)
        result.fight_medal = _int(state.get("fightMedal"))
        result.sex = _int(state.get("sex"))
        background = state.get("homeBackground") if isinstance(state.get("homeBackground"), Mapping) else {}
        result.home_back_ground.current = _int(background.get("current"))
        result.home_back_ground.is_random = bool(background.get("isRandom", False))
        result.friend_apply_level_limit = self.game.social.friend_apply_level_limit(profile)
        return result

    def _social_picture(self, method: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        request = _json(body)
        message = self._request(body, "ApiPlayerSocialPicture")
        picture = request.get("socialPicture", request.get("social_picture", getattr(message, "social_picture", "") if message else ""))
        sex = _int(request.get("sex", getattr(message, "sex", state.get("sex", 0)) if message else state.get("sex", 0)))
        if method in {"POST", "PATCH", "PUT"}:
            state["socialPicture"] = str(picture)[:512]
            state["sex"] = max(0, sex)
            self._save(profile, state)
        result = self._message("ApiPlayerSocialPicture.result")
        if result is None:
            # Window_City reads result.sex / result.socialPicture directly.
            return 200, {"sex": _int(state.get("sex")), "socialPicture": str(state.get("socialPicture", "")), "sandbox": True}
        result.sex = _int(state.get("sex"))
        result.social_picture = str(state.get("socialPicture", ""))
        return 200, result

    def _home_background(self, method: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        request = _json(body)
        message = self._request(body, "ApiHomeBackGround")
        current = _int(request.get("homeBackGround", request.get("homeBackground", request.get("current", getattr(message, "home_back_ground", 0) if message else 0))))
        is_random = _int(request.get("isRandom", request.get("is_random", getattr(message, "is_random", 0) if message else 0))) != 0
        if method in {"POST", "PATCH", "PUT"}:
            state["homeBackground"] = {"current": max(0, current), "isRandom": is_random}
            self._save(profile, state)
        result = self._message("ApiHomeBackGround.result")
        if result is None:
            background = state.get("homeBackground", {})
            return 200, {"result": {"current": _int(background.get("current")), "isRandom": bool(background.get("isRandom", False)), "sandbox": True}}
        result.home_back_ground.current = _int(state.get("homeBackground", {}).get("current"))
        result.home_back_ground.is_random = bool(state.get("homeBackground", {}).get("isRandom", False))
        return 200, result

    # ------------------------------------------------------------ notepads
    def _owned_actor_ids(self, profile: Mapping[str, Any]) -> list[int]:
        systems = getattr(self.game, "systems", None)
        if systems is not None:
            try:
                return [_int(record.get("id")) for record in actor_store.ordered(profile, systems._state(profile))]
            except Exception:
                pass
        try:
            return [_int(value) for value in self.game.local_actor_ids(profile)]
        except Exception:
            return [_int(value) for value in profile.get("actor_ids") or []]

    def _new_notepad(self, role_id: int) -> dict[str, Any]:
        now = self._now()
        events = [{"type": NOTEPAD_EVENT_GET_ACTOR, "text": "{}", "time": now}]
        photo = role_head_photo(role_id)
        if photo != DEFAULT_HEAD_PHOTO or role_id == 1:
            events.append({"type": NOTEPAD_EVENT_GET_HEAD_PHOTO, "text": json.dumps({"id": photo}), "time": now})
        # FavourLevel row ``level`` is the favour needed to *reach* that
        # level (the detail window shows favour / FavourLevel[level + 1]),
        # so a newly met role starts at level 0: only the initial board
        # (Signboard 101, FavourLevel 0) is unlocked on a fresh account.
        return {"roleId": role_id, "favour": 0, "favourLevel": 0, "unlockTime": now,
                "presentState": 0, "readTag": 0, "events": events}

    def _sync_notepads(self, profile: Mapping[str, Any], state: dict[str, Any], *, save: bool = True) -> dict[str, Any]:
        """One RoleNotePad per owned Role (ActorConfig.role of owned characters).

        Notepads are never removed once a role was met (a decomposed
        character keeps its favour).  Entries keyed by anything that is not a
        notepad Role id (older saves keyed the notepad by actor id 1001) are
        dropped.  Returns ``state["notepad"]``.
        """

        notepads = state.get("notepad")
        changed = False
        if not isinstance(notepads, dict):
            notepads, changed = {}, True
        valid_roles = notepad_role_ids()
        for key in list(notepads):
            if not str(key).isdigit() or int(key) not in valid_roles or not isinstance(notepads[key], dict):
                del notepads[key]
                changed = True
        ever = {_int(value) for value in state.get("everActors", []) or []}
        owned = set(self._owned_actor_ids(profile))
        if not owned <= ever:
            ever |= owned
            state["everActors"] = sorted(ever)
            changed = True
        for actor_id in sorted(owned):
            role_id = actor_role(actor_id)
            if role_id in valid_roles and str(role_id) not in notepads:
                notepads[str(role_id)] = self._new_notepad(role_id)
                changed = True
        state["notepad"] = notepads
        if changed and save:
            self._save(profile, state)
        return notepads

    @staticmethod
    def _notepad_json(entry: Mapping[str, Any]) -> dict[str, Any]:
        events = entry.get("events") if isinstance(entry.get("events"), list) else []
        return {
            "roleId": _int(entry.get("roleId")),
            "favour": _int(entry.get("favour")),
            "favourLevel": _int(entry.get("favourLevel")),
            "notepadEvents": [
                {"type": _int(event.get("type")), "text": str(event.get("text", "{}") or "{}"), "time": _int(event.get("time"))}
                for event in events if isinstance(event, Mapping)
            ],
            "unlockTime": _int(entry.get("unlockTime")),
            "presentState": _int(entry.get("presentState")),
            "readTag": _int(entry.get("readTag")),
        }

    def role_notepads(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """All RoleNotePadInfo records (camelCase), one per owned role."""

        with self._lock:
            state = self._state(profile)
            notepads = self._sync_notepads(profile, state)
            self._sync_signboard(profile, state, notepads)
            return [self._notepad_json(notepads[key]) for key in sorted(notepads, key=int)]

    def role_notepad(self, profile: Mapping[str, Any], role_id: Any) -> Optional[dict[str, Any]]:
        """One RoleNotePadInfo (camelCase) or ``None`` when the role is not owned."""

        with self._lock:
            state = self._state(profile)
            entry = self._sync_notepads(profile, state).get(str(_int(role_id)))
            return None if entry is None else self._notepad_json(entry)

    def add_role_favour(self, profile: Mapping[str, Any], role_id: Any, amount: Any) -> Optional[dict[str, Any]]:
        """Add favour to one role's notepad and return the updated record.

        Level-ups follow the client (Window_RoleNotepadDetail): reaching level
        ``n`` costs FavourLevel[n].favour, carried favour is kept, the level is
        capped at Misc.maxRoleFavourLevel and each level-up appends a
        FavourLvUp event ({"favour_level": n}).  Boards unlocked by the new
        level are added to the Signboard unlock list.  ``None`` when the role
        has no notepad (not owned).  Intended for the FavourGift / present /
        battle-favour handlers, which own the item costs.
        """

        with self._lock:
            state = self._state(profile)
            notepads = self._sync_notepads(profile, state, save=False)
            entry = notepads.get(str(_int(role_id)))
            if entry is None:
                return None
            thresholds = _favour_thresholds()
            max_level = misc_value("maxRoleFavourLevel", 10) or 10
            level = _int(entry.get("favourLevel"))
            favour = _int(entry.get("favour")) + max(0, _int(amount))
            events = entry.setdefault("events", [])
            while level < max_level and thresholds.get(level + 1) and favour >= thresholds[level + 1]:
                favour -= thresholds[level + 1]
                level += 1
                events.append({"type": NOTEPAD_EVENT_FAVOUR_LV_UP, "text": json.dumps({"favour_level": level}), "time": self._now()})
            if level >= max_level:
                favour = 0
            entry["favourLevel"], entry["favour"] = level, favour
            self._save(profile, state)
            self._sync_signboard(profile, state, notepads)
            return self._notepad_json(entry)

    def _role_notepad(self, method: str, path: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """``fci/RoleNotePad/[<roleId>]`` (and the legacy ``role-notepad`` seam).

        Manager_RestifizerManager syncs ``fci/RoleNotePad/`` as a list and
        Window_RoleNotepad binds ``fci/RoleNotePad/<roleId>`` as one
        RoleNotePadInfo (``data.roleId``, ``data.notepadEvents``); both look the
        role up with ``PB.get("Role", roleId)``, so ids are Role ids (1-205),
        never character ids.  Plain camelCase JSON is returned.
        """

        lower = _route(path).lower()
        legacy = "role-notepad" in lower
        payload = _json(body)
        tail = lower.rsplit("/", 1)[-1]
        key = _int(tail) if tail.isdigit() else _int(payload.get("roleId", payload.get("role_id", 0)))
        if not key and _int(payload.get("actorId", payload.get("actor_id", 0))):
            key = actor_role(payload.get("actorId", payload.get("actor_id")))
        if key >= actor_store.MIN_ACTOR_ID:
            key = actor_role(key)  # a character id names its role
        notepads = self._sync_notepads(profile, state)
        self._sync_signboard(profile, state, notepads)
        if not key and method in {"GET", "HEAD"}:
            query = parse_qs(urlsplit(path).query, keep_blank_values=True)
            keys = sorted(notepads, key=int)
            if "uids" in query:
                uids = [value.strip() for group in query["uids"] for value in group.split(",")]
                if any(not value.isdecimal() or _int(value) <= 0 for value in uids):
                    return 400, {"error": "invalid_role_notepad_uids"}
                keys = [str(_int(value)) for value in uids]
                # Native DataBindingManager uses requestList[index - 1], so
                # every response must keep its request's slot, including repeats.
                # Reject a missing record instead of shifting subsequent slots.
                for item in keys:
                    if item not in notepads:
                        return 404, {"error": "role_notepad_not_found", "roleId": int(item)}
            return 200, [self._notepad_json(notepads[item]) for item in keys]
        entry = notepads.get(str(key))
        if entry is None:
            return 404, {"error": "role_notepad_not_found", "roleId": key}
        if method in {"POST", "PATCH", "PUT"}:
            message = self._request(body, "ApiRoleNotePadRead")
            read_tag = payload.get("readTag", payload.get("read_tag", getattr(message, "read_tag", None) if message else None))
            if read_tag is not None:
                entry["readTag"] = max(0, _int(read_tag))
            if "text" in payload:
                entry["text"] = str(payload.get("text", ""))[:2000]
            self._save(profile, state)
        elif method not in {"GET", "HEAD"}:
            return 405, {"error": "method_not_allowed"}
        record = self._notepad_json(entry)
        if legacy:
            result = self._message("ApiRoleNotePad.result")
            if result is not None:
                info = result.role_notepad
                info.role_id = record["roleId"]; info.favour = record["favour"]; info.favour_level = record["favourLevel"]
                info.unlock_time = record["unlockTime"]; info.present_state = record["presentState"]; info.read_tag = record["readTag"]
                for event in record["notepadEvents"]:
                    item = info.notepad_events.add(); item.type = event["type"]; item.text = event["text"]; item.time = event["time"]
                return 200, result
        return 200, record

    # ----------------------------------------------------------- signboards
    def _signboard_unlocks(self, profile: Mapping[str, Any], state: Mapping[str, Any],
                           notepads: Mapping[str, Any]) -> tuple[list[int], set[int]]:
        """(unlockedSignboardId, boards a PUT may select) from the Signboard rules.

        FavourLevel (1): the role's notepad favour level >= FavourLevel.
        ActorGot (2): the role is owned.  ActorEverGotAndFavourLevel (3): the
        board's character (BoardOfEmote / BoardOfAnimRes) was ever owned and
        the favour level is reached.  ActorSignboardGift (4): only explicit
        grants (ResSignboard resources or an explicit unlock).  Unlocks are
        monotonic, so earlier entries are kept.  The client itself treats
        type-3 boards as unlocked by favour alone (except the special boards
        it checks against the list), so those stay selectable.
        """

        stored = state.get("signboard") if isinstance(state.get("signboard"), Mapping) else {}
        unlocked = {_int(item) for item in stored.get("unlockedSignboardId", []) or [] if _int(item) > 0}
        unlocked |= _ledger_ids(profile, RES_SIGNBOARD)
        if not unlocked and _int(stored.get("signboard")) > 0:
            unlocked.add(_int(stored.get("signboard")))
        ever = {_int(value) for value in state.get("everActors", []) or []}
        selectable: set[int] = set()
        for row in table_rows("Signboard"):
            board, role = _int(row.get("signboardId")), _int(row.get("roleId"))
            if board <= 0 or not row.get("active", False):
                continue
            kind = _int(row.get("unlockType"))
            required = _int(row.get("FavourLevel"))
            notepad = notepads.get(str(role))
            level = _int(notepad.get("favourLevel")) if isinstance(notepad, Mapping) else -1
            actor = _int(row.get("BoardOfEmote") if _int(row.get("type")) == 1 else row.get("BoardOfAnimRes"))
            if kind == SIGNBOARD_UNLOCK_FAVOUR_LEVEL and level >= required:
                unlocked.add(board)
            elif kind == SIGNBOARD_UNLOCK_ACTOR_GOT and level >= 0:
                unlocked.add(board)
            elif kind == SIGNBOARD_UNLOCK_ACTOR_EVER_GOT_AND_FAVOUR and level >= required:
                if actor in ever:
                    unlocked.add(board)
                elif actor not in _SPECIAL_BOARD_ACTORS:
                    selectable.add(board)
        return sorted(unlocked), selectable | unlocked

    def _signboard_record(self, state: dict[str, Any]) -> dict[str, Any]:
        signboard = state.get("signboard")
        if not isinstance(signboard, dict):
            signboard = {"signboard": 101, "unlockedSignboardId": [101], "checkSignboardTime": 0}
            state["signboard"] = signboard
        return signboard

    def _sync_signboard(self, profile: Mapping[str, Any], state: dict[str, Any], notepads: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the rule-based unlock list where the bootstrap GET reads it
        (game_profiles.signboard_json, served by the Signboard GET)."""

        signboard = self._signboard_record(state)
        unlocked, _ = self._signboard_unlocks(profile, state, notepads)
        stored_profile = profile.get("signboard") if isinstance(profile.get("signboard"), Mapping) else {}
        profile_unlocked = [_int(item) for item in stored_profile.get("unlockedSignboardId", []) or []]
        if signboard.get("unlockedSignboardId") != unlocked or profile_unlocked != unlocked:
            signboard["unlockedSignboardId"] = unlocked
            signboard.setdefault("signboard", unlocked[0] if unlocked else 101)
            signboard.setdefault("checkSignboardTime", 0)
            self._save(profile, state)
            self._set_signboard_profile(profile, signboard)
            if isinstance(profile, dict):
                profile["signboard"] = dict(signboard, unlockedSignboardId=list(unlocked))
        return signboard

    def _signboard(self, method: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        request = _json(body)
        message = self._request(body, "ApiSignboard")
        signboard = self._signboard_record(state)
        notepads = self._sync_notepads(profile, state)
        if method in {"GET", "HEAD"}:
            signboard = self._sync_signboard(profile, state, notepads)
            return 200, {"signboard": _int(signboard.get("signboard"), 101),
                         "unlockedSignboardId": [_int(item) for item in signboard.get("unlockedSignboardId", [])],
                         "checkSignboardTime": _int(signboard.get("checkSignboardTime"))}
        fallback = signboard.get("signboard", 101)
        current = _int(request.get("signboard", getattr(message, "signboard", fallback) if message else fallback))
        if method in {"POST", "PATCH", "PUT"}:
            # A local sandbox may explicitly seed a recovered board id (tools
            # and tests); the original client only ever sends {signboard}.
            requested_unlocked = request.get("unlockedSignboardId", request.get("unlocked_signboard_id"))
            if isinstance(requested_unlocked, list):
                seeded = {_int(item) for item in signboard.get("unlockedSignboardId", []) or []}
                signboard["unlockedSignboardId"] = sorted({item for item in seeded | {_int(v) for v in requested_unlocked} if item > 0})
            unlocked, selectable = self._signboard_unlocks(profile, state, notepads)
            signboard["unlockedSignboardId"] = unlocked
            if current not in selectable:
                self._save(profile, state)
                return 409, {"error": "signboard_locked", "signboard": current}
            signboard["signboard"] = current
            signboard["checkSignboardTime"] = self._now()
            self._set_signboard_profile(profile, signboard)
            self._save(profile, state)
            cultivation = getattr(self.game, "cultivation", None)
            if cultivation is not None:
                board_row = next((row for row in table_rows("Signboard")
                                  if _int(row.get("signboardId")) == current), None)
                if board_row is not None:
                    cultivation.record(profile, 76, param=_int(board_row.get("roleId")))
        unlocked = signboard.get("unlockedSignboardId", [])
        result = self._message("ApiSignboard.result")
        if result is None:
            return 200, {"result": dict(signboard), "sandbox": True}
        result.signboard = _int(signboard.get("signboard"), 101)
        result.check_signboard_time = _int(signboard.get("checkSignboardTime"))
        result.unlocked_signboard_id.extend(_int(item) for item in unlocked)
        return 200, result

    # ---------------------------------------------------------- head photos
    def _head_photo_store(self, profile: Mapping[str, Any], state: dict[str, Any]) -> dict[str, int]:
        """Unlocked PlayerHeadPhoto ids -> first-seen time.

        getWay 1 photos belong to a role (imageRoleId) and unlock once a
        character of that role was owned (the role's notepad records a
        GetHeadPhoto event); other ways (getWay 2) only unlock through a
        granted ResPlayerHeadPhoto resource.  Photo 1 is the default avatar.
        """

        store = state.get("headPhotos")
        if not isinstance(store, dict):
            store = {}
        before = dict(store)
        self._sync_notepads(profile, state)
        roles = {actor_role(actor_id) for actor_id in state.get("everActors", []) or []}
        granted = _ledger_ids(profile, RES_PLAYER_HEAD_PHOTO)
        for row in table_rows("PlayerHeadPhoto"):
            photo = _int(row.get("id"))
            if photo <= 0 or str(photo) in store:
                continue
            if (photo == DEFAULT_HEAD_PHOTO or photo in granted
                    or (_int(row.get("getWay")) == 1 and _int(row.get("imageRoleId")) in roles)):
                store[str(photo)] = self._now()
        store.setdefault(str(DEFAULT_HEAD_PHOTO), self._now())
        state["headPhotos"] = store
        if store != before:
            self._save(profile, state)
        return store

    def _head_photos(self, method: str, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """``fci/headphoto/``: Window_MySpacePlayerHeadBox marks every
        PlayerHeadPhoto row missing from this STHeadPhoto list as locked."""

        if method not in {"GET", "HEAD"}:
            return 405, {"error": "method_not_allowed", "message": "GET required"}
        store = self._head_photo_store(profile, state)
        return 200, [{"id": int(key), "gotTime": _int(value), "isNew": False}
                     for key, value in sorted(store.items(), key=lambda item: int(item[0]))]

    # --------------------------------------------------------------- medals
    def medal_reply(self, method: str, route: str, profile: Mapping[str, Any]) -> Optional[Response]:
        """GET ``fci/medal/`` (STMedal list) and ``fci/medal/<id>`` (one STMedal).

        PushService.MedalChange stores ``fci/medal/<id>`` as one object
        (``result.id``; no id = the medal is gone) and the medal windows read
        ``fci/medal/`` as a list.  The medals themselves live in
        LocalSystems state["medals"].
        """

        lower = _route(route).lower()
        if method.upper() not in {"GET", "HEAD"} or not (lower == "/fci/medal" or lower.startswith("/fci/medal/")):
            return None
        tail = lower[len("/fci/medal"):].strip("/")
        if tail and not tail.isdigit():
            return None
        systems = getattr(self.game, "systems", None)
        raw = systems._state(profile).get("medals", {}) if systems is not None else {}
        values: Iterable[Any] = raw.values() if isinstance(raw, Mapping) else (raw if isinstance(raw, list) else [])
        medals = []
        for value in values:
            if isinstance(value, Mapping) and _int(value.get("id")) > 0:
                medals.append({"id": _int(value.get("id")), "gotTime": _int(value.get("gotTime", value.get("got_time"))),
                               "isNew": bool(value.get("isNew", value.get("is_new", False)))})
        medals.sort(key=lambda item: item["id"])
        if not tail:
            return 200, medals
        return 200, next((item for item in medals if item["id"] == int(tail)), {})

    def _notices(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        if not ("notice" in lower or "server-update-info" in lower or "broadcast" in lower):
            return None
        payload = _json(body)
        notice_id = _int(payload.get("noticeId", payload.get("id", 1)), 1)
        index = _int(payload.get("index", 0))
        if "read-status" in lower or lower.endswith("/read"):
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            key = f"{notice_id}:{index}"
            if key not in state.setdefault("readNotices", []):
                state["readNotices"].append(key)
            self._save(profile, state)
            if "status" in lower:
                result = self._message("ApiPlayerNoticeReadStatus.result")
                if result is None:
                    return 200, {"notices": [{"noticeId": notice_id, "readStatus": 1, "index": index}], "sandbox": True}
                item = result.notices.add(); item.notice_id = notice_id; item.read_status = 1; item.index = index
                return 200, result
            result = self._message("ApiPlayerNoticeRead.result")
            if result is None:
                return 200, {"result": {"ret": 1}, "sandbox": True}
            result.result.ret = 1; return 200, result
        if "share-status" in lower or lower.endswith("/share"):
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            key = f"{notice_id}:{index}"
            if key not in state.setdefault("sharedNotices", []):
                state["sharedNotices"].append(key)
            self._save(profile, state)
            if "status" in lower:
                result = self._message("ApiPlayerNoticeShareStatus.result")
                if result is None:
                    return 200, {"notices": [{"noticeId": notice_id, "shareStatus": 1, "index": index}], "sandbox": True}
                item = result.notices.add(); item.notice_id = notice_id; item.share_status = 1; item.index = index
                return 200, result
            result = self._message("ApiPlayerNoticeShare.result")
            if result is None:
                return 200, {"result": {"ret": 1}, "sandbox": True}
            result.result.ret = 1; return 200, result
        if "reward" in lower or "bind" in lower:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            key = f"{notice_id}:{index}"
            if key in state.setdefault("noticeRewards", []):
                return 409, {"error": "notice_reward_already_claimed"}
            state["noticeRewards"].append(key); self._save(profile, state)
            response_name = "ApiBindNoticeReward" if "bind" in lower else "ApiPlayerNoticeReward"
            result = self._message(response_name + ".result")
            if result is None:
                return 200, {"result": {"ret": 1}, "sandbox": True}
            result.result.ret = 1; return 200, result
        if method not in {"GET", "HEAD"}:
            return 405, {"error": "method_not_allowed", "message": "GET required"}
        if "reward" in lower and "summary" in lower:
            result = self._message("ApiNoticeRewardSummary.result")
            return (200, result) if result is not None else (200, {"rewards": [], "sandbox": True})
        # Notice sessions are intentionally empty: the local activity page is
        # the authoritative content source and no stale remote announcement
        # is fabricated for an offline client.
        if "get-session" in lower or lower.endswith("/notice") or lower.endswith("/notice-session"):
            return 200, {}
        if "server-update-info" in lower:
            return 200, {"timeInfo": {}}
        return 200, []

    def _collections_notepad(self, method: str, path: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = _route(path).lower(); payload = _json(body)
        if lower.startswith(("/fci/rolenotepad", "/fci/role-notepad")):
            return self._role_notepad(method, path, body, profile, state)
        if lower.startswith("/fci/signboard"):
            return self._signboard(method, body, profile, state)
        if lower.rstrip("/") == "/fci/headphoto":
            return self._head_photos(method, profile, state)
        if "collection" in lower or "collections" in lower or "novel-cover" in lower:
            collections = state.setdefault("collections", {"actors": [], "equips": [], "novels": []})
            if method == "POST":
                category = str(payload.get("category", payload.get("type", ""))).lower()
                if "actor" in lower or category in {"actor", "actors"}:
                    category = "actors"
                    item_id = _int(payload.get("id", payload.get("actorId", 0)))
                    value = {"id": item_id, "addTime": self._now()}
                elif "equip" in lower or category in {"equip", "equips"}:
                    category = "equips"
                    item_id = _int(payload.get("id", payload.get("equipId", 0)))
                    value = {"equipId": item_id}
                else:
                    category = "novels"
                    item_id = _int(payload.get("id", payload.get("novelId", payload.get("coverId", 0))))
                    value = {"novelId": item_id, "curCover": _int(payload.get("coverId", 0)), "curRewardNum": 0, "canGetReward": False}
                if item_id <= 0:
                    return 400, {"error": "invalid_collection_id"}
                existing = collections.setdefault(category, [])
                if not any(_int(item.get("id", item.get("equipId", item.get("novelId", 0)))) == item_id for item in existing if isinstance(item, Mapping)):
                    existing.append(value)
                self._save(profile, state)
            if "actor" in lower:
                result = self._message("ApiCollectionsActor.result")
                if result is None:
                    return 200, {"actors": list(collections.setdefault("actors", [])), "sandbox": True}
                for item in collections.setdefault("actors", []):
                    row = result.actors.add(); row.id = _int(item.get("id")); row.add_time = _int(item.get("addTime"))
                return 200, result
            if "equip" in lower:
                result = self._message("ApiCollectionsEquip.result")
                if result is None:
                    return 200, {"equips": list(collections.setdefault("equips", [])), "sandbox": True}
                for item in collections.setdefault("equips", []): result.equips.add().equip_id = _int(item.get("equipId"))
                return 200, result
            if "novel" in lower or "cover" in lower:
                result = self._message("ApiCollectionsNovel.result")
                if result is None:
                    return 200, {"novels": list(collections.setdefault("novels", [])), "sandbox": True}
                for item in collections.setdefault("novels", []):
                    row = result.novels.add(); row.novel_id = _int(item.get("novelId")); row.cur_cover = _int(item.get("curCover")); row.cur_reward_num = _int(item.get("curRewardNum")); row.can_get_reward = bool(item.get("canGetReward"))
                return 200, result
        return None

    def _web_vote(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        if not any(token in lower for token in ("webvote", "web-vote", "voterole", "vote-role")):
            return None
        vote = state.setdefault("webVote", {})
        roles = vote.setdefault("roles", [])
        payload = _json(body)
        role_id = _int(payload.get("roleId", payload.get("role_id", 0)))
        if method == "POST":
            if _int(vote.get("ticketNum"), 0) <= 0:
                return 409, {"error": "vote_ticket_exhausted"}
            selected = next((item for item in roles if _int(item.get("roleId")) == role_id), None)
            if selected is None:
                return 404, {"error": "vote_role_not_found"}
            selected["voteCount"] = _int(selected.get("voteCount")) + 1
            vote["ticketNum"] = _int(vote.get("ticketNum")) - 1
            my_votes = vote.setdefault("myVotes", {}); key = str(role_id); my_votes[key] = _int(my_votes.get(key)) + 1
            self._save(profile, state)
            result = self._message("ApiWebVote.post")
            if result is None:
                return 200, {"result": True, "voteCount": _int(selected["voteCount"]), "voteTicketNum": _int(vote["ticketNum"]), "voteRoleInfo": {"roleId": role_id, "voteCount": _int(my_votes[key]), "roleName": str(selected.get("roleName", ""))}, "sandbox": True}
            result.result = True; result.vote_count = _int(selected["voteCount"]); result.vote_ticket_num = _int(vote["ticketNum"]); result.vote_role_info.role_id = role_id; result.vote_role_info.vote_count = _int(my_votes[key]); result.vote_role_info.role_name = str(selected.get("roleName", "")); return 200, result
        if "summary" in lower:
            result = self._message("ApiWebVoteSummary.get")
            if result is None:
                return 200, {"roleList": [dict(item) for item in roles], "sandbox": True}
            for selected in roles:
                row = result.role_list.add(); row.role_id = _int(selected.get("roleId")); row.role_name = str(selected.get("roleName", "")); row.image = str(selected.get("image", "")); row.vote_count = _int(selected.get("voteCount")); row.sort_id = _int(selected.get("sortId")); row.novel = str(selected.get("novel", "")); row.novel_id = _int(selected.get("novelId"))
            return 200, result
        result = self._message("ApiWebVote.get")
        if result is None:
            return 200, {"roleList": [dict(item) for item in roles], "voteCount": sum(_int(item.get("voteCount")) for item in roles), "voteBangNum": _int(vote.get("bangNum")), "voteTicketNum": _int(vote.get("ticketNum")), "sandbox": True}
        for selected in roles:
            row = result.role_list.add(); row.role_id = _int(selected.get("roleId")); row.role_name = str(selected.get("roleName", "")); row.image = str(selected.get("image", "")); row.vote_count = _int(selected.get("voteCount")); row.sort_id = _int(selected.get("sortId")); row.novel = str(selected.get("novel", "")); row.novel_id = _int(selected.get("novelId"))
        result.vote_count = sum(_int(item.get("voteCount")) for item in roles); result.vote_bang_num = _int(vote.get("bangNum")); result.vote_ticket_num = _int(vote.get("ticketNum")); result.exchange_vote_bang_num = _int(vote.get("bangNum")); result.exchange_vote_ticket_num = _int(vote.get("ticketNum")); return 200, result

    def _mini_games(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """Persistent local seams for puzzle, postcard, claw-crane and share."""
        lower = route.lower(); payload = _json(body)
        if "jigsaw" in lower:
            game = state.setdefault("jigsaw", {"cells": [], "received": False})
            if method == "GET":
                return 200, {"lightenCell": list(game.get("cells", [])), "receivePrize": bool(game.get("received")), "isFirstEnter": not bool(game.get("cells")), "sandbox": True}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            if "receive" in lower:
                if game.get("received"): return 409, {"error": "jigsaw_prize_already_claimed"}
                ok, changes, error = self.game.economy._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": 100}])
                if not ok: return 409, {"error": error or "resource_update_failed"}
                game["received"] = True; self._save(profile, state); return 200, {"resChange": changes, "sandbox": True}
            cell_id = _int(payload.get("cellId", payload.get("cell_id", payload.get("id", 0))))
            if cell_id <= 0: return 400, {"error": "invalid_cell_id"}
            if cell_id not in game.setdefault("cells", []): game["cells"].append(cell_id); game["cells"].sort(); self._save(profile, state)
            return 200, {"cellId": cell_id, "resChange": [], "sandbox": True}
        if "postcard" in lower:
            game = state.setdefault("postcard", {"cells": [], "received": False})
            if method == "GET": return 200, {"isFirstEnter": not bool(game.get("cells")), "postcardInfo": list(game.get("cells", [])), "prizeInfo": {"received": bool(game.get("received"))}, "sandbox": True}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            if "receive" in lower:
                if game.get("received"): return 409, {"error": "postcard_prize_already_claimed"}
                ok, changes, error = self.game.economy._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": 120}])
                if not ok: return 409, {"error": error or "resource_update_failed"}
                game["received"] = True; self._save(profile, state); return 200, {"resChange": changes, "sandbox": True}
            cell_id = _int(payload.get("cellId", payload.get("cell_id", payload.get("id", 0))))
            if cell_id <= 0: return 400, {"error": "invalid_cell_id"}
            if cell_id not in game.setdefault("cells", []): game["cells"].append(cell_id); game["cells"].sort(); self._save(profile, state)
            return 200, {"cellId": cell_id, "resChange": [], "sandbox": True}
        if "clawcrane" in lower or "claw-crane" in lower:
            game = state.setdefault("clawCrane", {"draws": 0, "prizes": [], "bingo": []})
            if method == "GET": return 200, {"draws": _int(game.get("draws")), "prizes": list(game.get("prizes", [])), "bingo": list(game.get("bingo", [])), "sandbox": True}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            if "verify" in lower: return 200, {"verifyCount": {}, "verifyTime": {}, "sandbox": True}
            if "bingo" in lower:
                item_id = _int(payload.get("id", payload.get("prizeId", 1)), 1)
                if item_id not in game.setdefault("bingo", []): game["bingo"].append(item_id); self._save(profile, state)
                return 200, {"id": item_id, "resChange": [], "sandbox": True}
            game["draws"] = _int(game.get("draws")) + max(1, _int(payload.get("count", 1), 1)); prize = {"id": game["draws"], "type": 7, "count": 20}
            game.setdefault("prizes", []).append(prize); self._save(profile, state); return 200, {"prize": prize, "draws": game["draws"], "sandbox": True}
        if lower.startswith("/fci/share"):
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            share = state.setdefault("share", {"weeklyRewarded": False, "actorRewardGot": 0, "weekKey": None})
            # The client treats this as a weekly share reward.  Keep the
            # reset tied to the server clock so tests and long-running local
            # sessions behave consistently after a 7-day clock advance.
            current_week = policy_for(self.game).week_index(self._now())
            stored_week = share.get("weekKey")
            try:
                stored_week_int = int(stored_week) if stored_week is not None else None
            except (TypeError, ValueError):
                stored_week_int = None
            if period_due(stored_week_int, current_week):
                share["weekKey"] = current_week
                share["weeklyRewarded"] = False
                share["actorRewardGot"] = 0
            if share.get("weeklyRewarded"): return 200, {"weeklyRewarded": True, "actorRewardGot": _int(share.get("actorRewardGot")), "resChange": [], "sandbox": True}
            ok, changes, error = self.game.economy._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": 50}])
            if not ok: return 409, {"error": error or "resource_update_failed"}
            share["weeklyRewarded"] = True; share["actorRewardGot"] = _int(payload.get("actorUid", payload.get("actorId", 0))); self._save(profile, state)
            return 200, {"weeklyRewarded": True, "actorRewardGot": share["actorRewardGot"], "resChange": changes, "sandbox": True}
        return None

    @staticmethod
    def _actor_dict(actor: Any) -> dict[str, Any]:
        from google.protobuf import json_format
        try:
            return json_format.MessageToDict(actor, always_print_fields_with_no_presence=True)
        except TypeError:  # older protobuf
            return json_format.MessageToDict(actor, including_default_value_fields=True)

    def _show_group(self, actor_entries: list[tuple[int, int, int, int, int]]) -> dict[str, Any]:
        """Build DBRelationPlayerInfo.Group from (index, uid, actor_id, level, star)."""

        container = self.game._types["DBGamePlayer.Actors"]()
        members = []
        for index, uid, actor_id, level, star in actor_entries:
            self.game._append_local_actor(container, int(actor_id), int(uid), int(index),
                                          level=max(1, int(level)), star=max(1, int(star)))
            actor = container.player_actors[-1]
            members.append({"actor": self._actor_dict(actor), "actorUid": int(uid), "index": int(index), "equips": {}})
        return {"members": members}

    def _player_summary(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> dict[str, Any]:
        """The player's own PlayerSummaryInfo for the MySpace page."""

        systems = getattr(self.game, "systems", None)
        system_state = systems._state(profile) if systems is not None else {}
        import actors as actor_store
        actor_ids = self.game.local_actor_ids(profile)
        groups = systems._normalise_actor_groups(system_state.get("actorGroups")) if systems is not None else {}
        groups = groups or (systems._default_actor_groups() if systems is not None else {})
        selected = int((system_state.get("selectedGroupIds") or {}).get("storymode", 1) or 1)
        members = (groups.get(str(selected)) or groups.get("1") or {}).get("members", [])
        entries = []
        for member in members:
            record = actor_store.get(profile, system_state, member.get("actorUid", 0)) if systems is not None else None
            if record is not None:
                entries.append((int(member.get("index", 0)), int(record["uid"]), int(record["id"]),
                                int(record.get("level", 1) or 1), int(record.get("star", 1) or 1)))
        medals = system_state.get("medals", {}) if isinstance(system_state.get("medals"), Mapping) else {}
        aura_uid = int(state.get("auraActorUid", 0) or 0)
        aura_id = int(actor_ids[aura_uid - 1]) if 0 < aura_uid <= len(actor_ids) else 0
        return {
            "playerId": str(profile["player_id"]),
            "name": str(profile.get("name", "")),
            "level": int(profile.get("level", 1) or 1),
            "sex": int(state.get("sex", 0) or 0),
            "lastLogoutTime": self._now(),
            "headPhoto": self._head_photo_value(state),
            "deviceType": 0,
            "isApplied": False,
            "isInRelation": False,
            "socialOpenId": "",
            "socialPlatform": 0,
            "socialPicture": str(state.get("socialPicture", "")),
            "achievementPoint": 0,
            "sign": str(state.get("sign", "")),
            "messageBoardPermission": int(state.get("messageBoardPermission", 0) or 0),
            "favorBooks": [int(v) for v in state.get("favorBooks", []) or []],
            "favorRoles": [int(v) for v in state.get("favorRoles", []) or []],
            "showGroup": self._show_group(entries),
            "auraActorUid": aura_uid,
            "auraActorId": aura_id,
            "auraLastUsedTime": 0,
            "fightMedal": int(state.get("fightMedal", 0) or 0),
            "showMedals": [int(v) for v in state.get("showMedals", []) or []],
            "auraActorQuality": 1 if aura_id else 0,
            "guildId": int(self.game.guild.player_data(profile).get('guildId', 0)),
            "gamecenterLoginTime": 0,
            "qqvip": 0,
            "likeNums": int(state.get("likeNums", 0) or 0),
            "allMedals": sorted(int(key) for key in medals if str(key).isdigit()),
        }

    @staticmethod
    def _head_photo_value(state: Mapping[str, Any]) -> int:
        """Stored avatar for uint32 head_photo fields (0 = platform picture)."""

        return max(0, _int(state.get("headPhoto"), DEFAULT_HEAD_PHOTO))

    @staticmethod
    def bot_head_photo(bot: Mapping[str, Any]) -> int:
        """A real PlayerHeadPhoto id for a bot: the photo of its first character's role."""

        actors = [int(value) for value in bot.get("actors", []) or []]
        return role_head_photo(actor_role(actors[0])) if actors else DEFAULT_HEAD_PHOTO

    def _bot_summary(self, bot: Mapping[str, Any], is_friend: bool) -> dict[str, Any]:
        actors = [int(value) for value in bot.get("actors", [])][:3]
        level = int(bot.get("level", 1))
        entries = [(index, index + 1, actor_id, min(60, level), 1) for index, actor_id in enumerate(actors)]
        return {
            "playerId": str(bot["playerId"]), "name": str(bot["name"]), "level": level, "sex": 0,
            "lastLogoutTime": self._now(), "headPhoto": self.bot_head_photo(bot), "deviceType": 0,
            "isApplied": False, "isInRelation": bool(is_friend), "socialOpenId": "", "socialPlatform": 0,
            "socialPicture": "", "achievementPoint": 0, "sign": "", "messageBoardPermission": 0,
            "favorBooks": [], "favorRoles": [], "showGroup": self._show_group(entries),
            "auraActorUid": 1 if actors else 0, "auraActorId": actors[0] if actors else 0, "auraLastUsedTime": 0,
            "fightMedal": 0, "showMedals": [], "auraActorQuality": max(1, min(4, 1 + level // 15)) if actors else 0,
            "guildId": 0, "gamecenterLoginTime": 0, "qqvip": 0, "likeNums": 0, "allMedals": [],
        }

    def _player_page(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """MySpace (账号信息) page: ``fci/playerpageinfo[/<playerId>]`` and likes.

        WidgetMySpaceReal stores the GET reply as Space/SelectedPlayer and
        reads it directly as PlayerSummaryInfo (playerId, name, showGroup …),
        so the object is returned unwrapped.  PATCH applies the page edits
        (ApiRelationPlayerPageInfo) and returns the updated summary.
        """

        lower = route.lower().rstrip("/")
        if lower.startswith("/fci/pageclicklikeview"):
            target = route.rstrip("/").rsplit("/", 1)[-1]
            liked = state.setdefault("pageLikesGiven", [])
            if method == "POST" and target not in liked:
                liked.append(target)
                self._save(profile, state)
            elif method not in {"GET", "POST"}:
                return 405, {"error": "method_not_allowed"}
            return 200, {"isClicked": target in liked, "likeNums": int(state.get("likeNums", 0) or 0)}
        if not lower.startswith("/fci/playerpageinfo"):
            return None
        target = route.rstrip("/")[len("/fci/playerpageinfo"):].strip("/")
        self_id = str(profile["player_id"])
        if method in {"PATCH", "PUT", "POST"}:
            values = _json(body)
            previous_photo = _int(state.get("headPhoto"), DEFAULT_HEAD_PHOTO)
            previous_aura = _int(state.get("auraActorUid"))
            unlocked_photos = set(self._head_photo_store(profile, state))
            message = self._request(body, "ApiRelationPlayerPageInfo")
            if message is not None:
                for field, key in (("sign", "sign"), ("sex", "sex"), ("head_photo", "headPhoto"),
                                   ("fight_medal", "fightMedal"), ("aura_actor_uid", "auraActorUid"),
                                   ("message_board_permission", "messageBoardPermission")):
                    if message.HasField(field) if message.DESCRIPTOR.fields_by_name[field].has_presence else getattr(message, field):
                        state[key] = getattr(message, field)
                for field, key in (("favor_books", "favorBooks"), ("favor_roles", "favorRoles"), ("show_medals", "showMedals")):
                    if len(getattr(message, field)):
                        state[key] = [int(v) for v in getattr(message, field)]
            for key in ("sign", "sex", "headPhoto", "fightMedal", "auraActorUid", "messageBoardPermission",
                        "favorBooks", "favorRoles", "showMedals"):
                if key in values:
                    state[key] = values[key]
            # Window_MySpacePlayerHeadBox only offers unlocked photos and -1
            # (the platform picture); anything else is a locked avatar.
            photo = _int(state.get("headPhoto"), DEFAULT_HEAD_PHOTO)
            if photo != previous_photo and photo > 0 and str(photo) not in unlocked_photos:
                state["headPhoto"] = previous_photo
                return 409, {"error": "head_photo_locked", "headPhoto": photo}
            if photo < 0:
                # -1 = use the platform picture.  head_photo is uint32 in
                # PlayerSummaryInfo / baseinfo / ScLoginEnd, so keep it as 0,
                # which every avatar widget also draws as the platform picture.
                state["headPhoto"] = 0
            if _int(state.get("auraActorUid")) > 0 and previous_aura <= 0:
                # Bots can borrow the support from now on (LocalSocial).
                state["auraActorSince"] = self._now()
            self._save(profile, state)
            return 200, self._player_summary(profile, state)
        if method != "GET":
            return 405, {"error": "method_not_allowed"}
        if not target or target == self_id:
            return 200, self._player_summary(profile, state)
        social = getattr(self.game, "social", None)
        bot = social.bots.get(target) if social is not None else None
        if bot is None:
            return 404, {"error": "player_not_found", "playerId": target}
        friends = {str(item) for item in social._state(profile).get("friends", [])}
        return 200, self._bot_summary(bot, target in friends)

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        with self._lock:
            route = _route(path); method = method.upper(); state = self._state(profile)
            lower = route.lower()
            result = self._player_page(method, route, body, profile, state)
            if result is not None: return result
            result = self.medal_reply(method, route, profile)
            if result is not None: return result
            if lower in {"/fci/baseinfo", "/fci/baseinfo/"} and method == "GET": return 200, self._base_info(profile, state)
            if lower in {"/fci/socialpicture", "/fci/socialpicture/"}: return self._social_picture(method, body, profile, state)
            if lower in {"/fci/home-back-ground", "/fci/home-back-ground/", "/fci/homebackground", "/fci/homebackground/"}: return self._home_background(method, body, profile, state)
            result = self._notices(method, route, body, profile, state)
            if result is not None: return result
            result = self._collections_notepad(method, path, body, profile, state)
            if result is not None: return result
            result = self._mini_games(method, route, body, profile, state)
            if result is not None: return result
            result = self._web_vote(method, route, body, profile, state)
            if result is not None: return result
            return None


__all__ = ["LocalProfileFeatures"]
