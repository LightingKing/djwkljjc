"""Thread-safe local push session registry.

The retired client keeps a long-lived WebSocket open after login.  HTTP
handlers and gameplay modules therefore need a small, transport-independent
way to deliver a message to the account's active connection.  ``PushService``
owns only session membership and fan-out; protobuf encoding remains the
responsibility of the caller, so this layer cannot invent an official schema.

All account selectors are local identifiers (normally ``openId``).  No
network or third-party push provider is contacted.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Iterable


class PushServiceError(ValueError):
    """Raised when a push session or event is malformed."""


@dataclass(frozen=True)
class PushEvent:
    """A message ready for delivery through a local push connection."""

    message_type: str
    data: bytes
    seq_cs_received: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.message_type, str) or not self.message_type.strip():
            raise PushServiceError("message_type must be a non-empty string")
        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            raise PushServiceError("push data must be bytes")
        if self.seq_cs_received is not None and int(self.seq_cs_received) < 0:
            raise PushServiceError("seq_cs_received must be non-negative")


def account_key(account: Mapping[str, Any] | str | int) -> str:
    """Return the stable local selector used by the session registry."""

    if isinstance(account, Mapping):
        for name in ("openId", "open_id", "player_id", "account_id", "id"):
            value = account.get(name)
            if value is not None and str(value).strip():
                return str(value)
        raise PushServiceError("account has no local identifier")
    value = str(account).strip()
    if not value:
        raise PushServiceError("account selector is empty")
    return value


# --------------------------------------------------------------- ScTasksChange
# ``Manager/PushService`` registers ``ScTasksChange`` -> ``OnScTasksChange`` ->
# ``Common/TaskUtil.InitData(pushData)`` (manager__pushservice.lua:37/264).
# The push transport hands the inner bytes to ``WebSocket.DecodeToLuaTable``,
# i.e. a protobuf decode, so the payload must be protobuf, not JSON.  The
# ``.proto`` for this message was not recovered into data/descriptor_set.pb;
# the field numbers below are the compiled constants of the client's own
# generated classes (IL2CPP dump.cs: ``ScTasksChange`` TypeDefIndex 5479 and
# ``ApiTask.Types.result`` TypeDefIndex 5454).  All scalar fields are proto3
# uint32/bool; repeated scalars use the proto3 packed encoding (parsers accept
# packed and unpacked alike).
SC_TASKS_CHANGE = "ScTasksChange"
_TASK_RESULT_UINT = {  # ApiTask.result uint32 fields (5 = res TaskInfo is never sent)
    "id": 1, "progress": 2, "startTime": 6, "endTime": 7, "param": 8,
    "actorCultivationActivityId": 9, "actorCultivationRewardProgress": 10,
}
_TASK_RESULT_BOOL = {"isDone": 3, "rewardGot": 4, "actorCultivationVipOnly": 11, "dailyRefresh": 12}
# Always written, even when zero: TaskUtil/WidgetTaskSlot read these directly.
_TASK_RESULT_ALWAYS = ("id", "progress", "isDone", "rewardGot", "endTime")


def _varint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _uint32(value: Any, name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PushServiceError(f"{name} must be an unsigned integer") from None
    if number < 0 or number > 0xFFFFFFFF:
        raise PushServiceError(f"{name} out of uint32 range")
    return number


def _field_varint(number: int, value: int) -> bytes:
    return _varint(number << 3) + _varint(value)


def _field_bytes(number: int, value: bytes) -> bytes:
    return _varint(number << 3 | 2) + _varint(len(value)) + value


def _packed(number: int, values: Any, name: str) -> bytes:
    if values is None:
        return b""
    if isinstance(values, (str, bytes, Mapping)) or not hasattr(values, "__iter__"):
        raise PushServiceError(f"{name} must be a list")
    body = b"".join(_varint(_uint32(value, name)) for value in values)
    return _field_bytes(number, body) if body else b""


def encode_task_result(task: Mapping[str, Any]) -> bytes:
    """Encode one ``ApiTask.result`` (a ``tasks[]`` element of ``/fci/task/``)."""

    if not isinstance(task, Mapping):
        raise PushServiceError("task entry must be an object")
    fields: list[tuple[int, bytes]] = []
    for name, number in _TASK_RESULT_UINT.items():
        if name in task or name in _TASK_RESULT_ALWAYS:
            fields.append((number, _field_varint(number, _uint32(task.get(name, 0), name))))
    for name, number in _TASK_RESULT_BOOL.items():
        if name in task or name in _TASK_RESULT_ALWAYS:
            fields.append((number, _field_varint(number, 1 if task.get(name) else 0)))
    return b"".join(data for _, data in sorted(fields))


def encode_tasks_change(payload: Mapping[str, Any]) -> bytes:
    """Encode the ``GET /fci/task/`` JSON shape as protobuf ``ScTasksChange``.

    ScTasksChange: 1 clearAll bool, 2 tasks repeated ApiTask.result,
    3 delTasks, 4 activePoint, 5 activePointRewardGot, 6 disableCategory,
    7 taskTargetDone (uint32 / repeated uint32).
    """

    if not isinstance(payload, Mapping):
        raise PushServiceError("tasks payload must be an object")
    tasks = payload.get("tasks") or []
    if isinstance(tasks, (str, bytes, Mapping)):
        raise PushServiceError("tasks must be a list")
    out = bytearray(_field_varint(1, 1 if payload.get("clearAll") else 0))
    for task in tasks:
        out += _field_bytes(2, encode_task_result(task))
    out += _packed(3, payload.get("delTasks"), "delTasks")
    # Always present: TaskUtil.GetActivePointList treats a missing value as 0.
    out += _field_varint(4, _uint32(payload.get("activePoint") or 0, "activePoint"))
    out += _packed(5, payload.get("activePointRewardGot"), "activePointRewardGot")
    out += _packed(6, payload.get("disableCategory"), "disableCategory")
    out += _packed(7, payload.get("taskTargetDone"), "taskTargetDone")
    return bytes(out)


class PushService:
    """Track authenticated connections and fan out local events.

    A user may have multiple sessions (for example a desktop emulator and a
    phone), so ``publish`` sends to every active session for the account.  A
    failed write removes only that session and does not hide the exception
    from the audit callback.  ``send_sc`` is expected to be thread-safe; the
    built-in ``push._Connection`` serializes its writes.
    """

    def __init__(self, audit_log: Callable[..., Any] | Callable[[dict[str, Any]], Any] | None = None) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, dict[int, Any]] = {}
        # Numeric local account id -> session key.  Game modules address a
        # player by ``profile["account_id"]`` while connections register by
        # ``openId``; this lets them reach the same sessions.
        self._account_ids: dict[int, str] = {}
        self._audit_log = audit_log

    def _audit(self, **fields: Any) -> None:
        if self._audit_log is None:
            return
        safe = {"kind": "push_service", **fields}
        try:
            if hasattr(self._audit_log, "write"):
                self._audit_log.write(**safe)
            else:
                self._audit_log(safe)
        except Exception:
            # Observability must never make a game connection fail.
            pass

    def register(self, connection: Any, account: Mapping[str, Any] | str | int) -> str:
        if connection is None or not callable(getattr(connection, "send_sc", None)):
            raise PushServiceError("connection must provide send_sc")
        key = account_key(account)
        numeric_id = None
        if isinstance(account, Mapping):
            raw_id = account.get("accountId", account.get("account_id"))
            if raw_id is not None and not isinstance(raw_id, bool):
                try:
                    numeric_id = int(raw_id)
                except (TypeError, ValueError):
                    numeric_id = None
        with self._lock:
            self._sessions.setdefault(key, {})[id(connection)] = connection
            if numeric_id is not None:
                self._account_ids[numeric_id] = key
        self._audit(action="register", account=key, sessions=self.connection_count(key))
        return key

    def unregister(self, connection: Any, account: Mapping[str, Any] | str | int | None = None) -> bool:
        keys = [account_key(account)] if account is not None else None
        removed = False
        with self._lock:
            selected = keys if keys is not None else list(self._sessions)
            for key in selected:
                sessions = self._sessions.get(key)
                if not sessions:
                    continue
                if sessions.pop(id(connection), None) is not None:
                    removed = True
                if not sessions:
                    self._sessions.pop(key, None)
                    for numeric_id in [n for n, k in self._account_ids.items() if k == key]:
                        self._account_ids.pop(numeric_id, None)
        if removed:
            self._audit(action="unregister", sessions=self.connection_count(keys[0]) if keys else None)
        return removed

    def connection_count(self, account: Mapping[str, Any] | str | int | None = None) -> int:
        with self._lock:
            if account is None:
                return sum(len(value) for value in self._sessions.values())
            return len(self._sessions.get(account_key(account), {}))

    def key_for_account_id(self, account_id: Any) -> str | None:
        """Session key for a numeric local account id, or None when offline."""

        try:
            numeric_id = int(account_id)
        except (TypeError, ValueError):
            return None
        with self._lock:
            key = self._account_ids.get(numeric_id)
            return key if key is not None and self._sessions.get(key) else None

    def accounts(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._sessions))

    def publish(
        self,
        account: Mapping[str, Any] | str | int,
        message_type: str,
        data: bytes,
        *,
        seq_cs_received: int | None = None,
    ) -> int:
        event = PushEvent(message_type, data, seq_cs_received)
        key = account_key(account)
        with self._lock:
            targets = tuple(self._sessions.get(key, {}).values())
        delivered = 0
        for connection in targets:
            try:
                ack = connection.last_client_sequence if event.seq_cs_received is None else event.seq_cs_received
                connection.send_sc(int(ack), event.message_type, event.data)
                delivered += 1
            except (BrokenPipeError, ConnectionError, OSError):
                self.unregister(connection, key)
                self._audit(action="delivery_failed", account=key, message_type=event.message_type)
        self._audit(action="publish", account=key, message_type=event.message_type, delivered=delivered)
        return delivered

    def publish_json(
        self,
        account: Mapping[str, Any] | str | int,
        message_type: str,
        payload: Any,
        *,
        seq_cs_received: int | None = None,
    ) -> int:
        """Publish a compact UTF-8 JSON payload for local-only envelopes.

        This is useful for events whose protobuf descriptor has not yet been
        recovered.  Typed game messages should call ``publish`` with their
        serialized protobuf bytes instead.
        """

        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.publish(account, message_type, data, seq_cs_received=seq_cs_received)

    def red_point(self, account: Mapping[str, Any] | str | int, payload: Any) -> int:
        return self.publish_json(account, "ScRedPointData", payload)

    def mail(self, account: Mapping[str, Any] | str | int, payload: Any) -> int:
        return self.publish_json(account, "ScMail", payload)

    def payment(self, account: Mapping[str, Any] | str | int, payload: Any) -> int:
        return self.publish_json(account, "ScPurchaseDelevery", payload)

    def chat(self, account: Mapping[str, Any] | str | int, payload: Any) -> int:
        return self.publish_json(account, "ScChat", payload)

    def tasks_change(self, account: Mapping[str, Any] | str | int, payload: Mapping[str, Any]) -> int:
        """Push a ``/fci/task/``-shaped payload as protobuf ``ScTasksChange``."""

        return self.publish(account, SC_TASKS_CHANGE, encode_tasks_change(payload))

    def base_push_for_account_id(self, account_id: Any, push_type: int, params: Iterable[Any] = ()) -> int:
        """Send one ``BasePushMessage`` to an account's sessions; 0 when offline."""

        key = self.key_for_account_id(account_id)
        if key is None:
            return 0
        return self.publish(key, BASE_PUSH_MESSAGE, encode_base_push(push_type, params))

    def tasks_change_for_account_id(self, account_id: Any, payload: Mapping[str, Any]) -> int:
        """``tasks_change`` addressed by ``profile["account_id"]``; 0 when offline."""

        key = self.key_for_account_id(account_id)
        if key is None:
            return 0
        return self.tasks_change(key, payload)


# ------------------------------------------------------------ BasePushMessage
# Manager/PushService.OnBasePushMessage (manager__pushservice.lua:38/309)
# dispatches on PushType: BaseInfoChange (2) makes the client re-GET
# fci/baseinfo/ (city level, name, avatar), BaseResChange (1) re-GETs
# fci/baseresource/.  Field numbers are the client's compiled constants
# (IL2CPP dump.cs BasePushMessage: PushType = 1 int32, Params = 2 repeated
# string); the message is not in data/descriptor_set.pb.
BASE_PUSH_MESSAGE = "BasePushMessage"
PUSH_TYPE_BASE_RES_CHANGE = 1
PUSH_TYPE_BASE_INFO_CHANGE = 2


def encode_base_push(push_type: int, params: Iterable[Any] = ()) -> bytes:
    out = bytearray(_field_varint(1, int(push_type)))
    for value in params:
        out += _field_bytes(2, str(value).encode("utf-8"))
    return bytes(out)


__all__ = [
    "BASE_PUSH_MESSAGE",
    "PUSH_TYPE_BASE_INFO_CHANGE",
    "PUSH_TYPE_BASE_RES_CHANGE",
    "encode_base_push",
    "PushEvent",
    "PushService",
    "PushServiceError",
    "SC_TASKS_CHANGE",
    "account_key",
    "encode_task_result",
    "encode_tasks_change",
]
