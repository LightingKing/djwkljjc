"""Local player mailbox (``/fci/mail/`` and ``/fci/mail-auto-delete/``).

Routes the original 2.0.4 client calls (decompiled Lua):

* ``GET    fci/mail/``   ``Manager/RestifizerManager`` -> ``MailUtil.OnMails``
  reads ``{mails, clearAll, delMails}`` (the ScMails object) at top level.
  ``MailExpireCheck`` drops every mail whose ``expireTime - serverUtc <= 1``,
  so each DbMail must carry a future ``expireTime``.
* ``PATCH  fci/mail/<id>`` ``{read: true}`` / ``{attachmentsGot: true}`` and
  ``PATCH fci/mail/`` ``{attachmentsGot: true}`` (claim all), all answered
  with ApiMail.result as JSON: ``{result, resChange, delMails,
  attachmentsGot}``.  ``Window/Mail/Mail.HandlerMailPatchResponse`` does
  ``#result.resChange``, so ``resChange`` is always a list of resChange
  entries (``{"baseRes": {...}}``) and ``attachmentsGot`` / ``delMails`` are
  lists of mail ids.
* ``DELETE fci/mail/<id>`` - the client ignores the body and removes the mail
  locally on success (also used when a mail's countdown expires).
* ``GET/POST fci/mail-auto-delete/`` ``{autoDelete}`` -> ApiMailAutoDelete
  result ``{autoDelete, delMails}``; the window binds the GET and stores the
  POST reply under the same key, so both have that shape.  With auto-delete
  on, a finished mail (read, nothing left to claim) is deleted by the read /
  claim that finished it and listed in that reply's ``delMails``.

Also served, never sent by the client: ``DELETE fci/mail/`` (batch; body
``{"ids": [...]}`` or, without ids, every finished mail), ``PATCH fci/mail/``
``{read: true}`` (read all), and the historical local-only routes
(``POST fci/mail/<read|obtain|collect|delete>/`` with ``{"id": n}``,
``GET fci/mail/<id>/``) which keep their typed ApiMail.result /
ApiMailAutoDelete.result replies for existing tools and tests.

Storage is LocalSystems ``state["mails"]`` (unchanged record shape, so the
seeded welcome mail and older profiles stay readable)::

    {"id", "title", "content", "senderName",
     "attachments": [{"type", "id", "count", "param"}],
     "read", "attachmentsGot",
     # added by this module; filled in for older records on first access
     "createTime", "startTime", "expireTime", "important"}

plus ``state["mail_last_id"]`` (monotonic id) and ``state["mail_auto_delete"]``.
Other modules deliver mail with :func:`send_mail`.  Attachments are paid via
:func:`grants.grant_rewards` (items into the bag, actors as instances,
everything else through the economy ledger).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Iterable, Mapping, Optional
from urllib.parse import urlsplit

try:  # package import (tests/tools) and flat import (server.py) both work
    from .grants import grant_rewards
except ImportError:  # pragma: no cover - flat module layout
    from grants import grant_rewards


Body = bytes | bytearray | str | None
Response = tuple[int, Any]
# ``notifier(profile, payload)``: payload is ScMails-shaped
# ``{"mails": [...], "clearAll": False, "delMails": [...]}`` (MailUtil.OnMails).
MailNotifier = Callable[[Mapping[str, Any], dict], Any]

_log = logging.getLogger(__name__)

_DAY = 86400
DEFAULT_SENDER = "Local Sandbox"
DEFAULT_EXPIRE_DAYS = 30
# ``expire_days=None``: no practical expiry (the client needs a finite time).
NO_EXPIRY_DAYS = 3650
# Records stored before expiry was modelled (the seeded welcome mail): the old
# handler advertised a rolling one-year end time, so keep a year from upgrade.
LEGACY_EXPIRE_DAYS = 365
# Misc.maxPlayerMail; the client trims its own list to this, lowest ids first.
MAX_MAILS = 99

_MAIL = "/fci/mail"
_AUTO_DELETE = "/fci/mail-auto-delete"
_LEGACY_CLAIM = {"obtain", "collect"}
_LEGACY_DELETE = {"delete", "auto-delete"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _json(body: Body) -> dict[str, Any]:
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        value = json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except (UnicodeDecodeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _route(path: str) -> str:
    return (urlsplit(path or "").path or "/").rstrip("/").lower() or "/"


def _resource(value: Any) -> Optional[dict[str, int]]:
    """Flat STResource from ``{type,id,count,param}`` or ``{"baseRes": {...}}``."""

    if not isinstance(value, Mapping):
        return None
    if isinstance(value.get("baseRes"), Mapping):
        value = value["baseRes"]
    count = _int(value.get("count"))
    if count <= 0:
        return None
    return {"type": _int(value.get("type")), "id": _int(value.get("id")),
            "count": count, "param": _int(value.get("param"))}


def _attachments(mail: Mapping[str, Any]) -> list[dict[str, int]]:
    return [res for res in (_resource(item) for item in mail.get("attachments") or []) if res]


def _merge(resources: Iterable[Mapping[str, int]]) -> list[dict[str, int]]:
    """Sum equal resources so a claim-all shows one reward line per resource."""

    merged: dict[tuple[int, int, int], dict[str, int]] = {}
    for res in resources:
        key = (res["type"], res["id"], res["param"])
        if key in merged:
            merged[key]["count"] += res["count"]
        else:
            merged[key] = dict(res)
    return list(merged.values())


def _finished(mail: Mapping[str, Any]) -> bool:
    """Read with nothing left to claim (what auto-delete removes)."""

    return bool(mail.get("read")) and (not _attachments(mail) or bool(mail.get("attachmentsGot")))


def _started(mail: Mapping[str, Any], now: int) -> bool:
    start = _int(mail.get("startTime"))
    return start <= 0 or now >= start


def _normalise(state: dict[str, Any], now: int) -> tuple[bool, list[int]]:
    """Upgrade stored records in place and drop expired ones.

    Returns ``(changed, expired_ids)``.  Expired mails are gone server-side
    (unclaimed attachments with them), as the client deletes them too.
    """

    raw = state.get("mails")
    changed = not isinstance(raw, list)
    mails = raw if isinstance(raw, list) else []
    kept: list[dict[str, Any]] = []
    expired: list[int] = []
    for value in mails:
        if not isinstance(value, dict) or _int(value.get("id")) <= 0:
            changed = True
            continue
        before = dict(value)
        value["attachments"] = _attachments(value)
        value.setdefault("title", "")
        value.setdefault("content", "")
        value.setdefault("senderName", DEFAULT_SENDER)
        value["read"] = bool(value.get("read", False))
        value["attachmentsGot"] = bool(value.get("attachmentsGot", False))
        value["important"] = bool(value.get("important", False))
        if _int(value.get("createTime")) <= 0:
            value["createTime"] = now
        if "startTime" not in value:
            value["startTime"] = _int(value["createTime"])
        if _int(value.get("expireTime")) <= 0:
            value["expireTime"] = max(now, _int(value["createTime"])) + LEGACY_EXPIRE_DAYS * _DAY
        if value != before:
            changed = True
        if _int(value["expireTime"]) - now <= 1:
            expired.append(_int(value["id"]))
            changed = True
            continue
        kept.append(value)
    state["mails"] = kept
    last = max([_int(state.get("mail_last_id"))] + [_int(item["id"]) for item in kept])
    if _int(state.get("mail_last_id")) != last:
        state["mail_last_id"] = last
        changed = True
    if not isinstance(state.get("mail_auto_delete"), bool):
        state["mail_auto_delete"] = _flag(state.get("mail_auto_delete"))
        changed = True
    return changed, expired


def mail_view(mail: Mapping[str, Any]) -> dict[str, Any]:
    """DbMail as the client reads it (camelCase JSON, every field present)."""

    attachments = _attachments(mail)
    expire = _int(mail.get("expireTime"))
    return {
        "id": _int(mail.get("id")),
        "title": str(mail.get("title", "")),
        "content": str(mail.get("content", "")),
        "senderName": str(mail.get("senderName", DEFAULT_SENDER)),
        "createTime": _int(mail.get("createTime")),
        "startTime": _int(mail.get("startTime")),
        "endTime": expire,
        "expireTime": expire,
        "attachments": [{"baseRes": dict(res)} for res in attachments],
        "important": bool(mail.get("important", False)),
        "deleted": False,
        "read": bool(mail.get("read", False)),
        "attachmentsGot": bool(mail.get("attachmentsGot", False)),
        "globalMail": False,
        "attachmentsReason": 0,
        "levelMin": 0,
        "attachmentsCount": len(attachments),
        "globalMailType": 0,
        # MailVersionCheck: "" splits to no parts and passes.
        "version": "",
        "globalMailId": 0,
    }


def _clock_of(game: Any) -> Callable[[], Any]:
    mailbox = getattr(game, "mailbox", None)
    if mailbox is not None and hasattr(mailbox, "_now"):
        return mailbox._now
    return getattr(game, "_now", None) or time.time


def send_mail(
    game: Any,
    profile: Mapping[str, Any],
    title: str,
    content: str,
    attachments: Iterable[Mapping[str, Any]] = (),
    expire_days: Optional[int] = DEFAULT_EXPIRE_DAYS,
    *,
    sender_name: str = DEFAULT_SENDER,
    important: bool = False,
    start_time: Optional[int] = None,
    now: Optional[int] = None,
) -> dict[str, Any]:
    """Deliver one mail to ``profile``'s mailbox; return the stored record.

    ``attachments`` are STResource-like rows (``{type,id,count,param}`` or
    resChange-style ``{"baseRes": {...}}``); empty counts are dropped.
    ``expire_days`` counts from delivery (or ``start_time``); ``None`` means no
    practical expiry.  ``start_time`` (epoch seconds) delays visibility: the
    client holds such mail until then (MailUtil.MailTimeCheck) and the server
    refuses to claim it early.  The mailbox keeps the newest ``MAX_MAILS``.
    """

    if expire_days is None:
        expire_days = NO_EXPIRY_DAYS
    if _int(expire_days) <= 0:
        raise ValueError("expire_days must be positive (or None for no expiry)")
    now = _int(now if now is not None else _clock_of(game)())
    start = _int(start_time) if start_time else 0
    with game._lock:
        state = game.systems._state(profile)
        _normalise(state, now)
        mails: list[dict[str, Any]] = state["mails"]
        mail_id = _int(state.get("mail_last_id")) + 1
        state["mail_last_id"] = mail_id
        record = {
            "id": mail_id,
            "title": str(title),
            "content": str(content),
            "senderName": str(sender_name),
            "attachments": [res for res in (_resource(item) for item in attachments or ()) if res],
            "read": False,
            "attachmentsGot": False,
            "createTime": now,
            "startTime": start or now,
            "expireTime": max(now, start) + _int(expire_days) * _DAY,
            "important": bool(important),
        }
        mails.append(record)
        trimmed: list[int] = []
        if len(mails) > MAX_MAILS:
            mails.sort(key=lambda item: _int(item.get("id")))
            trimmed = [_int(item["id"]) for item in mails[: len(mails) - MAX_MAILS]]
            del mails[: len(mails) - MAX_MAILS]
        game.systems._save(profile, state)
    mailbox = getattr(game, "mailbox", None)
    notifier = getattr(mailbox, "notifier", None)
    if notifier is not None:
        payload = {"mails": [mail_view(record)], "clearAll": False, "delMails": trimmed}
        def notify():
            try:
                notifier(profile, payload)
            except Exception:  # pragma: no cover - a live push is best effort
                _log.debug("mail notifier failed", exc_info=True)
        game.after_commit(notify)
    return dict(record)


class LocalMailbox:
    """``/fci/mail/`` routes over LocalSystems ``state["mails"]``."""

    def __init__(self, game: Any, clock: Optional[Callable[[], Any]] = None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or getattr(game, "_now", None) or time.time
        # Optional live delivery of send_mail() results (ScMails push).
        self.notifier: Optional[MailNotifier] = None

    def _now(self) -> int:
        return int(self._clock())

    # -- state -----------------------------------------------------------
    def _load(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        state = self.game.systems._state(profile)
        changed, _ = _normalise(state, self._now())
        if changed:
            self.game.systems._save(profile, state)
        return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        self.game.systems._save(profile, state)

    @staticmethod
    def _find(state: Mapping[str, Any], mail_id: int) -> Optional[dict[str, Any]]:
        return next((item for item in state["mails"] if _int(item.get("id")) == mail_id), None)

    @staticmethod
    def _remove(state: dict[str, Any], ids: Iterable[int]) -> list[int]:
        wanted = set(ids)
        removed = [_int(item["id"]) for item in state["mails"] if _int(item.get("id")) in wanted]
        state["mails"] = [item for item in state["mails"] if _int(item.get("id")) not in wanted]
        return removed

    def _auto_sweep(self, state: dict[str, Any], ids: Optional[Iterable[int]] = None) -> list[int]:
        if not state.get("mail_auto_delete"):
            return []
        scope = None if ids is None else set(ids)
        doomed = [_int(item["id"]) for item in state["mails"]
                  if _finished(item) and (scope is None or _int(item["id"]) in scope)]
        return self._remove(state, doomed)

    # -- operations ------------------------------------------------------
    def _claim(self, profile: Mapping[str, Any], ids: Optional[list[int]]) -> dict[str, Any]:
        """Claim attachments of ``ids`` (None = every mail). Exactly once each."""

        now = self._now()
        state = self._load(profile)
        targets = [item for item in state["mails"]
                   if (ids is None or _int(item["id"]) in ids) and _started(item, now)]
        if ids is None:
            targets = [item for item in targets if _attachments(item)]
        target_ids = [_int(item["id"]) for item in targets]
        pending = [item for item in targets if not item.get("attachmentsGot")]
        rewards = _merge(res for item in pending for res in _attachments(item))
        # grant_rewards loads and saves the same state row itself (bag items,
        # actor instances), so mark the mails on a fresh copy afterwards.
        changes = grant_rewards(self.game, profile, rewards) if rewards else []
        race = getattr(getattr(self.game, "realtime_pvp", None), "race", None)
        if race is not None:
            race.record_mail_claims(profile, [_int(item["id"]) for item in pending])
        state = self._load(profile)
        for item in state["mails"]:
            if _int(item["id"]) in target_ids:
                item["attachmentsGot"] = True
                item["read"] = True
        views = {_int(item["id"]): mail_view(item) for item in state["mails"] if _int(item["id"]) in target_ids}
        deleted = self._auto_sweep(state, target_ids)
        self._save(profile, state)
        # Claim-all reports the mails it paid; a single claim always echoes
        # its id so the window marks it (a retry pays nothing again).
        got = target_ids if ids is not None else [_int(item["id"]) for item in pending]
        return {"changes": changes, "got": got, "deleted": deleted, "views": views}

    def _read(self, profile: Mapping[str, Any], ids: Optional[list[int]]) -> dict[str, Any]:
        now = self._now()
        state = self._load(profile)
        targets = [item for item in state["mails"]
                   if (ids is None or _int(item["id"]) in ids) and _started(item, now)]
        for item in targets:
            item["read"] = True
        target_ids = [_int(item["id"]) for item in targets]
        views = {_int(item["id"]): mail_view(item) for item in targets}
        deleted = self._auto_sweep(state, target_ids)
        self._save(profile, state)
        return {"changes": [], "got": [], "deleted": deleted, "views": views}

    def _delete(self, profile: Mapping[str, Any], ids: Optional[list[int]]) -> list[int]:
        state = self._load(profile)
        if ids is None:
            ids = [_int(item["id"]) for item in state["mails"] if _finished(item)]
        removed = self._remove(state, ids)
        if removed:
            self._save(profile, state)
        return removed

    # -- replies ---------------------------------------------------------
    def _list(self, profile: Mapping[str, Any]) -> Response:
        state = self._load(profile)
        mails = sorted(state["mails"], key=lambda item: _int(item.get("id")))
        return 200, {"mails": [mail_view(item) for item in mails], "clearAll": False, "delMails": []}

    @staticmethod
    def _patch_reply(outcome: Mapping[str, Any], mail_id: Optional[int] = None) -> dict[str, Any]:
        reply: dict[str, Any] = {}
        if mail_id is not None:
            reply["result"] = outcome["views"].get(mail_id) or {"id": mail_id}
        reply["resChange"] = list(outcome["changes"])
        reply["delMails"] = list(outcome["deleted"])
        reply["attachmentsGot"] = list(outcome["got"])
        return reply

    def _patch_one(self, profile: Mapping[str, Any], mail_id: int, request: Mapping[str, Any]) -> Response:
        state = self._load(profile)
        mail = self._find(state, mail_id)
        if mail is None or not _started(mail, self._now()):
            # Expired or deleted elsewhere: tell the window to drop it rather
            # than fail the request (HandlerMailPatchResponse reads delMails).
            gone = [mail_id] if mail is None else []
            return 200, {"result": {"id": mail_id}, "resChange": [], "delMails": gone, "attachmentsGot": []}
        if _flag(request.get("attachmentsGot")):
            outcome = self._claim(profile, [mail_id])
        elif _flag(request.get("read")):
            outcome = self._read(profile, [mail_id])
        else:
            outcome = {"changes": [], "got": [], "deleted": [], "views": {mail_id: mail_view(mail)}}
        return 200, self._patch_reply(outcome, mail_id)

    def _patch_all(self, profile: Mapping[str, Any], request: Mapping[str, Any]) -> Response:
        if _flag(request.get("attachmentsGot")):
            outcome = self._claim(profile, None)
        elif _flag(request.get("read")):
            outcome = self._read(profile, None)
        else:
            outcome = {"changes": [], "got": [], "deleted": [], "views": {}}
        return 200, self._patch_reply(outcome)

    def _auto_delete(self, method: str, profile: Mapping[str, Any], body: Body) -> Response:
        state = self._load(profile)
        if method in {"POST", "PATCH", "PUT"}:
            request = _json(body)
            state["mail_auto_delete"] = _flag(request.get("autoDelete", request.get("auto_delete")))
        elif method != "GET":
            return 405, {"error": "method_not_allowed"}
        deleted = self._auto_sweep(state)
        if method != "GET" or deleted:
            self._save(profile, state)
        return 200, {"autoDelete": bool(state["mail_auto_delete"]), "delMails": deleted}

    # -- historical local-only routes (typed replies) ---------------------
    def _message(self, name: str, payload: Mapping[str, Any]):
        from google.protobuf import json_format, message_factory

        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        return json_format.ParseDict(dict(payload), cls(), ignore_unknown_fields=True)

    def _legacy(self, method: str, mail_id: int, action: Optional[str], profile: Mapping[str, Any]) -> Response:
        if method == "GET":
            state = self._load(profile)
            mail = self._find(state, mail_id)
            view = mail_view(mail) if mail is not None else {"id": mail_id}
            return 200, self._message("ApiMail.result", {"result": view})
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        state = self._load(profile)
        if self._find(state, mail_id) is None:
            return 404, {"error": "mail_not_found", "mailId": mail_id}
        if action in _LEGACY_DELETE:
            removed = self._delete(profile, [mail_id])
            return 200, self._message("ApiMailAutoDelete.result", {"autoDelete": True, "delMails": removed})
        if action in _LEGACY_CLAIM:
            outcome = self._claim(profile, [mail_id])
        else:
            outcome = self._read(profile, [mail_id])
        return 200, self._message("ApiMail.result", self._patch_reply(outcome, mail_id))

    # -- dispatch --------------------------------------------------------
    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = _route(path)
        if route == _AUTO_DELETE:
            with self._lock:
                return self._auto_delete(method.upper(), profile, body)
        if route != _MAIL and not route.startswith(_MAIL + "/"):
            return None
        method = method.upper()
        parts = [part for part in route[len(_MAIL):].split("/") if part]
        with self._lock:
            if not parts:
                if method == "GET":
                    return self._list(profile)
                if method in {"PATCH", "PUT"}:
                    return self._patch_all(profile, _json(body))
                if method == "DELETE":
                    request = _json(body)
                    raw = request.get("ids", request.get("delMails", request.get("mailIds")))
                    ids = [_int(value) for value in raw] if isinstance(raw, list) else None
                    return 200, {"delMails": self._delete(profile, ids)}
                if method == "POST":
                    request = _json(body)
                    return self._legacy(method, _int(request.get("id", request.get("mailId", 1)), 1), None, profile)
                return 405, {"error": "method_not_allowed"}
            if parts[0].isdigit():
                mail_id = int(parts[0])
                action = parts[1] if len(parts) > 1 else None
                if action is None and method in {"PATCH", "PUT"}:
                    return self._patch_one(profile, mail_id, _json(body))
                if method == "DELETE":
                    # Idempotent: the window only drops the mail on success.
                    self._delete(profile, [mail_id])
                    return 200, {"delMails": [mail_id]}
                return self._legacy(method, mail_id, action, profile)
            request = _json(body)
            mail_id = _int(request.get("id", request.get("mailId", 1)), 1)
            return self._legacy(method, mail_id, parts[0], profile)


__all__ = ["LocalMailbox", "send_mail", "mail_view", "DEFAULT_EXPIRE_DAYS", "MAX_MAILS"]
