"""Small, local-only RFC 6455 transport for the original LJJC push channel.

The game protocol puts a protobuf ``CsMessage`` or ``ScMessage`` directly in a
WebSocket binary frame.  This module intentionally stops at the authenticated
connect and heartbeat seam.  It does not manufacture ``ScLoginEnd`` or any
game state.  A caller can supply ``on_login``/``initializer`` when the real
post-login schema has been implemented.

``handle_upgrade`` is synchronous because it is intended to be called by a
``BaseHTTPRequestHandler`` worker for the ``/push`` route.  It only accepts
local account access tokens returned by :class:`accounts.LocalAccounts`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

try:  # The HTTP server already depends on this optional package.
    import lz4.frame as _lz4_frame
except ImportError:  # pragma: no cover - exercised only in minimal installs.
    _lz4_frame = None

from accounts import WireFormatError


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_CONTROL_BYTES = 125
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DEFAULT_SERVER_SEQUENCE = 0
DEFAULT_HEARTBEAT_INTERVAL_MS = 30_000

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class PushProtocolError(ValueError):
    """A malformed or unsupported WebSocket/application message."""


def _read_exact(stream: Any, size: int) -> bytes:
    if size < 0 or size > MAX_MESSAGE_BYTES + 14:
        raise PushProtocolError("frame exceeds limit")
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise EOFError("websocket peer closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _read_varint(data: bytes, offset: int) -> Tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if offset >= len(data):
            raise PushProtocolError("truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if shift == 63 and byte > 1:
                raise PushProtocolError("protobuf varint overflow")
            return value, offset
        shift += 7
    raise PushProtocolError("protobuf varint too long")


def _parse_fields(data: bytes) -> Dict[int, list[Tuple[int, Any]]]:
    """Parse protobuf wire fields while retaining wire type for validation."""

    if len(data) > MAX_MESSAGE_BYTES:
        raise PushProtocolError("protobuf message exceeds 1 MiB")
    fields: Dict[int, list[Tuple[int, Any]]] = {}
    offset = 0
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        number, wire_type = tag >> 3, tag & 7
        if number <= 0 or wire_type in (3, 4):
            raise PushProtocolError("unsupported protobuf field")
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            value = data[offset : offset + 8]
            if len(value) != 8:
                raise PushProtocolError("truncated protobuf fixed64")
            offset += 8
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            if length > MAX_MESSAGE_BYTES or offset + length > len(data):
                raise PushProtocolError("truncated protobuf bytes")
            value = data[offset : offset + length]
            offset += length
        elif wire_type == 5:
            value = data[offset : offset + 4]
            if len(value) != 4:
                raise PushProtocolError("truncated protobuf fixed32")
            offset += 4
        else:
            raise PushProtocolError("unsupported protobuf wire type")
        fields.setdefault(number, []).append((wire_type, value))
    return fields


def _one(fields: Mapping[int, list[Tuple[int, Any]]], number: int, wire_type: int, default: Any = None) -> Any:
    values = fields.get(number)
    if not values:
        return default
    actual_type, value = values[-1]
    if actual_type != wire_type:
        raise PushProtocolError(f"protobuf field {number} has wrong wire type")
    return value


def _string(fields: Mapping[int, list[Tuple[int, Any]]], number: int, default: str = "") -> str:
    value = _one(fields, number, 2, None)
    if value is None:
        return default
    try:
        return bytes(value).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PushProtocolError("protobuf string is not UTF-8") from exc


def _varint_field(number: int, value: int) -> bytes:
    # protobuf int32/int64 negative values are encoded as ten-byte varints;
    # all sequence, timestamp and session values used here are non-negative.
    if value < 0:
        value &= (1 << 64) - 1
    tag = number << 3
    output = bytearray()
    while tag >= 0x80:
        output.append((tag & 0x7F) | 0x80)
        tag >>= 7
    output.append(tag)
    while value >= 0x80:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def _length_delimited(number: int, value: bytes) -> bytes:
    # A length-delimited tag is the same tag encoding with wire type 2.
    tag = number << 3 | 2
    output = bytearray()
    while tag >= 0x80:
        output.append((tag & 0x7F) | 0x80)
        tag >>= 7
    output.append(tag)
    length = len(value)
    while length >= 0x80:
        output.append((length & 0x7F) | 0x80)
        length >>= 7
    output.append(length)
    output.extend(value)
    return bytes(output)


def _encode_message(message_type: str, data: bytes, *, lz4: bool = False) -> bytes:
    output = bytearray()
    if lz4:
        output.extend(_varint_field(1, 1))
    if message_type:
        output.extend(_length_delimited(2, message_type.encode("utf-8")))
    output.extend(_length_delimited(3, data))
    return bytes(output)


def _encode_cs_message(seq_cs: int, seq_sc_received: int, message: bytes) -> bytes:
    return b"".join(
        (_varint_field(1, seq_cs), _varint_field(2, seq_sc_received), _length_delimited(3, message))
    )


def _encode_sc_message(seq_sc: int, seq_cs_received: int, message: bytes) -> bytes:
    return b"".join(
        (_varint_field(1, seq_sc), _varint_field(2, seq_cs_received), _length_delimited(3, message))
    )


def _encode_connect(session_id: int, seq_cs_received: int, seq_sc: int) -> bytes:
    return b"".join(
        (_varint_field(1, session_id), _varint_field(4, seq_cs_received), _varint_field(5, seq_sc))
    )


def _encode_heartbeat(time_ms_server: int, time_ms_client: int, interval_ms: int) -> bytes:
    return b"".join(
        (_varint_field(1, time_ms_server), _varint_field(2, time_ms_client), _varint_field(3, interval_ms))
    )


def _decode_envelope(payload: bytes, expected: str) -> Tuple[int, int, str, bytes, bool]:
    outer = _parse_fields(payload)
    seq_one = _one(outer, 1, 0, 0)
    seq_two = _one(outer, 2, 0, 0)
    inner = _one(outer, 3, 2, None)
    if inner is None or not isinstance(seq_one, int) or not isinstance(seq_two, int):
        raise PushProtocolError(f"{expected} envelope is incomplete")
    message_fields = _parse_fields(bytes(inner))
    compressed = bool(_one(message_fields, 1, 0, 0))
    message_type = _string(message_fields, 2)
    data = _one(message_fields, 3, 2, None)
    if not message_type or data is None:
        raise PushProtocolError("Message type/data is missing")
    raw_data = bytes(data)
    if compressed:
        # Only accept an explicitly identifiable LZ4 frame.  A raw LZ4 block
        # has no trustworthy boundary here and must not be guessed.
        if not raw_data.startswith(b"\x04\x22\x4d\x18"):
            raise PushProtocolError("unsupported_lz4_encoding")
        if _lz4_frame is None:
            raise PushProtocolError("lz4_unavailable")
        try:
            raw_data = _lz4_frame.decompress(raw_data)
        except Exception as exc:  # do not expose compressed bytes in logs
            raise PushProtocolError("invalid_lz4_frame") from exc
        if len(raw_data) > MAX_MESSAGE_BYTES:
            raise PushProtocolError("decompressed_message_exceeds_limit")
    return int(seq_one), int(seq_two), message_type, raw_data, compressed


def _read_frame(stream: Any, *, allow_unmasked: bool = False) -> Tuple[bool, int, bytes]:
    first, second = _read_exact(stream, 2)
    fin = bool(first & 0x80)
    rsv = first & 0x70
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if rsv:
        raise PushProtocolError("unsupported websocket extension")
    if length == 126:
        length = struct.unpack("!H", _read_exact(stream, 2))[0]
    elif length == 127:
        raw_length = struct.unpack("!Q", _read_exact(stream, 8))[0]
        if raw_length & (1 << 63):
            raise PushProtocolError("invalid websocket length")
        length = raw_length
    if length > MAX_MESSAGE_BYTES:
        raise PushProtocolError("websocket message exceeds 1 MiB")
    if opcode >= 0x8 and (not fin or length > MAX_CONTROL_BYTES):
        raise PushProtocolError("invalid websocket control frame")
    if not masked and not allow_unmasked:
        raise PushProtocolError("client frame is not masked")
    if not masked:
        # UnityTcpClient in the isolated local AVD has been observed to emit
        # unmasked client frames.  This branch is opt-in and only intended
        # for a loopback listener; the default remains RFC6455-strict.
        return fin, opcode, _read_exact(stream, int(length))
    mask = _read_exact(stream, 4)
    payload = bytearray(_read_exact(stream, int(length)))
    for index in range(len(payload)):
        payload[index] ^= mask[index % 4]
    return fin, opcode, bytes(payload)


def _write_frame(stream: Any, opcode: int, payload: bytes, *, fin: bool = True) -> None:
    if len(payload) > MAX_MESSAGE_BYTES:
        raise PushProtocolError("outgoing message exceeds 1 MiB")
    if opcode >= 0x8 and (not fin or len(payload) > MAX_CONTROL_BYTES):
        raise PushProtocolError("invalid outgoing control frame")
    first = (0x80 if fin else 0) | opcode
    length = len(payload)
    if length < 126:
        header = bytes((first, length))
    elif length <= 0xFFFF:
        header = bytes((first, 126)) + struct.pack("!H", length)
    else:
        header = bytes((first, 127)) + struct.pack("!Q", length)
    stream.write(header + payload)  # servers never mask RFC 6455 frames
    flush = getattr(stream, "flush", None)
    if flush is not None:
        flush()


def _close_payload(code: int, reason: str = "") -> bytes:
    safe_reason = reason.encode("utf-8", "replace")[:123]
    return struct.pack("!H", code) + safe_reason


def _audit(audit_log: Any, **fields: Any) -> None:
    """Write only non-sensitive transport metadata to the caller's logger."""

    if audit_log is None:
        return
    safe = {
        key: value
        for key, value in fields.items()
        if key in {"kind", "direction", "message_type", "seq", "ack", "payload_bytes", "status", "reason"}
    }
    try:
        if callable(audit_log):
            audit_log(safe)
        elif hasattr(audit_log, "write"):
            audit_log.write(**safe)
        elif hasattr(audit_log, "append"):
            audit_log.append(safe)
    except Exception:
        # Diagnostics must not turn a live connection into a protocol error.
        return


def _header(handler: Any, name: str) -> str:
    headers = getattr(handler, "headers", None)
    if headers is None:
        return ""
    value = headers.get(name, "")
    return str(value or "")


def _reject_http(handler: Any, status: int, reason: str) -> None:
    try:
        handler.send_response(status, reason)
        handler.send_header("Content-Length", "0")
        handler.send_header("Connection", "close")
        handler.end_headers()
    except Exception:
        # Test doubles and an already-closing HTTP stream may not implement
        # BaseHTTPRequestHandler's response helpers.
        try:
            handler.wfile.write(
                f"HTTP/1.1 {status} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii")
            )
        except Exception:
            pass


def _accept_key(key: str) -> str:
    digest = hashlib.sha1((key.strip() + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _rpc_fallback_type(message_type: str) -> Optional[str]:
    """Return a safe acknowledgement type for an unsupported local RPC.

    Game RPCs use names such as ``base.cs_free_recharge`` and their response
    is dispatched under the same name by this client build.  Older low-level
    ``CsFoo`` messages conventionally pair with ``ScFoo``.  Restrict the
    fallback to known client RPC namespaces; random message names must still
    be rejected and logged as unsupported.
    """

    if not isinstance(message_type, str):
        return None
    # These protocol messages have explicit handlers below.  Falling back to
    # ``ScConnect``/``ScHeartbeat``/``ScLogin`` before reaching those handlers
    # breaks authentication and emits a fake login response.
    if message_type in {"CsConnect", "CsHeartbeat", "CsLogin"}:
        return None
    if message_type.startswith("Cs") and len(message_type) > 2:
        return "Sc" + message_type[2:]
    if ".cs_" in message_type and message_type.split(".", 1)[0] in {
        "base",
        "card",
        "activity",
        "month_card",
        "recharge_gift",
        "storyline_sys",
        "teach",
        "guild",
        "relation",
        "mail",
        "task",
    }:
        return message_type
    return None


@dataclass
class _Connection:
    handler: Any
    accounts: Any
    audit_log: Any
    on_login: Optional[Callable[..., Any]]
    initializer: Optional[Callable[..., Any]]
    on_business_message: Optional[Callable[..., Any]] = None
    server_sequence: int = DEFAULT_SERVER_SEQUENCE
    heartbeat_interval_ms: int = DEFAULT_HEARTBEAT_INTERVAL_MS
    allow_unmasked_local: bool = False
    push_service: Any = None
    clock: Callable[[], int] = lambda: int(time.time())
    account: Optional[Mapping[str, Any]] = None
    session_id: Optional[int] = None
    authenticated: bool = False
    # The outer CsMessage sequence most recently received.  Initializers use
    # this as ScMessage.seq_cs_received when they explicitly send a later
    # business response through ``connection.send_sc``.
    last_client_sequence: int = 0
    fragmented: Optional[bytearray] = None
    fragment_opcode: Optional[int] = None
    write_lock: Any = None

    def __post_init__(self) -> None:
        # ``send_sc`` may be called by PushService while ``run`` is handling
        # an incoming frame.  Serialise frame writes and sequence allocation
        # so concurrent notifications cannot interleave bytes or reuse a
        # server sequence number.
        self.write_lock = threading.Lock()

    @property
    def stream_in(self) -> Any:
        return self.handler.rfile

    @property
    def stream_out(self) -> Any:
        return self.handler.wfile

    def send_sc(self, seq_cs_received: int, message_type: str, data: bytes) -> None:
        inner = _encode_message(message_type, data)
        with self.write_lock:
            sequence = self.server_sequence
            payload = _encode_sc_message(sequence, seq_cs_received, inner)
            _write_frame(self.stream_out, OP_BINARY, payload)
            self.server_sequence += 1
        _audit(
            self.audit_log,
            kind="push_message",
            direction="out",
            message_type=message_type,
            seq=sequence,
            ack=seq_cs_received,
            payload_bytes=len(data),
        )

    def close(self, code: int = 1000, reason: str = "") -> None:
        try:
            with self.write_lock:
                _write_frame(self.stream_out, OP_CLOSE, _close_payload(code, reason))
        except Exception:
            pass

    def _dispatch_message(self, seq_cs: int, seq_sc_received: int, message_type: str, data: bytes) -> bool:
        self.last_client_sequence = seq_cs
        _audit(
            self.audit_log,
            kind="push_message",
            direction="in",
            message_type=message_type,
            seq=seq_cs,
            ack=seq_sc_received,
            payload_bytes=len(data),
        )
        if message_type == "CsConnect":
            if self.authenticated:
                raise PushProtocolError("duplicate_connect")
            fields = _parse_fields(data)
            token = _string(fields, 2)
            open_id = _string(fields, 6)
            session_id = _one(fields, 3, 0, 0)
            # The client keeps the last server sequence across a WebSocket
            # reconnect and sends it in both CsMessage.seq_sc_received and
            # CsConnect.seq_sc_received.  Resume from that high-water mark
            # before emitting ScConnect.  Starting at zero makes the client
            # reject the handshake with ``Server sequence mismatch`` and then
            # drops every later BaseInfoChange push, which leaves the city HUD
            # on the old player level even though the battle settlement was
            # persisted correctly.
            client_server_sequence = max(
                0,
                int(seq_sc_received),
                int(_one(fields, 5, 0, 0) or 0),
            )
            if not token or not open_id or not isinstance(session_id, int):
                raise PushProtocolError("incomplete_connect")
            account = self.accounts.verify_access_token(token)
            if not account or account.get("openId") != open_id:
                raise PushProtocolError("invalid_guest_token")
            self.account = account
            self.session_id = secrets.randbelow(2_000_000_000) + 1
            self.authenticated = True
            if self.push_service is not None:
                self.push_service.register(self, account)
            self.server_sequence = max(self.server_sequence, client_server_sequence)
            self.send_sc(seq_cs, "ScConnect", _encode_connect(self.session_id, seq_cs, self.server_sequence))
            return True

        if not self.authenticated:
            raise PushProtocolError("connect_required")

        if message_type == "CsHeartbeat":
            fields = _parse_fields(data)
            client_time = _one(fields, 1, 0, 0)
            if not isinstance(client_time, int):
                raise PushProtocolError("invalid_heartbeat")
            now_ms = int(self.clock()) * 1000
            self.send_sc(
                seq_cs,
                "ScHeartbeat",
                _encode_heartbeat(now_ms, client_time, self.heartbeat_interval_ms),
            )
            return True

        if message_type == "CsLogin":
            fields = _parse_fields(data)
            # Keep the callback input typed and useful, but never log or echo
            # the token/device fields.  The callback is the explicit seam for
            # a later, fully implemented local initializer.
            login_fields: Dict[str, Any] = {}
            names = {
                1: "social_entry", 2: "device_type", 3: "device", 4: "channel",
                5: "client_version", 6: "social_platform", 7: "device_id",
                8: "install_channel", 9: "register_channel", 10: "telecom_oper",
                11: "network", 12: "is_game_center", 13: "token", 14: "real_imei",
                15: "system_software", 16: "idfa", 17: "social_type",
            }
            for number, name in names.items():
                if number in fields:
                    wire_type, value = fields[number][-1]
                    login_fields[name] = (
                        bytes(value).decode("utf-8", "replace") if wire_type == 2 else int(value)
                    )
            callback = self.on_login or self.initializer
            if callback is not None:
                callback(self.account, login_fields, self)
            # Deliberately do not send ScLoginBegin/ScLoginEnd here.
            return True

        if self.authenticated and self.account is not None and self.on_business_message is not None:
            if self.on_business_message(self.account, message_type, data, self):
                return True

        # The retired client still sends a large number of namespace RPCs
        # after login (recharge, task, activity and growth modules).  The
        # local server does not have their original protobuf descriptors, so
        # a hard drop leaves the Lua request lock waiting until timeout.  For
        # those RPC names, return a same-type protobuf envelope carrying the
        # request bytes as a conservative acknowledgement.  Keep this after
        # Connect/Login/Heartbeat handling: those protocol messages must be
        # decoded and answered by their dedicated paths above.
        fallback_type = _rpc_fallback_type(message_type)
        if fallback_type is not None:
            self.send_sc(seq_cs, fallback_type, data)
            _audit(
                self.audit_log,
                kind="push_message",
                direction="in",
                message_type=message_type,
                seq=seq_cs,
                ack=seq_sc_received,
                payload_bytes=len(data),
                status="unsupported_fallback",
                reason="rpc_schema_unavailable",
            )
            return True

        _audit(
            self.audit_log,
            kind="push_message",
            direction="in",
            message_type=message_type,
            seq=seq_cs,
            ack=seq_sc_received,
            payload_bytes=len(data),
            status="unsupported",
            reason="message_type_not_implemented",
        )
        return False

    def run(self) -> Dict[str, Any]:
        try:
            while True:
                try:
                    fin, opcode, payload = _read_frame(
                        self.stream_in, allow_unmasked=self.allow_unmasked_local
                    )
                    if opcode == OP_CLOSE:
                        if len(payload) == 1:
                            raise PushProtocolError("invalid_close_frame")
                        self.close(1000)
                        return {"status": "closed", "reason": "peer_close"}
                    if opcode == OP_PING:
                        with self.write_lock:
                            _write_frame(self.stream_out, OP_PONG, payload)
                        continue
                    if opcode == OP_PONG:
                        continue
                    if opcode == OP_TEXT:
                        self.close(1003, "binary required")
                        return {"status": "rejected", "reason": "text_frame"}
                    if opcode == OP_CONTINUATION:
                        if self.fragmented is None:
                            raise PushProtocolError("unexpected_continuation")
                        self.fragmented.extend(payload)
                        if len(self.fragmented) > MAX_MESSAGE_BYTES:
                            raise PushProtocolError("websocket_message_exceeds_limit")
                        if not fin:
                            continue
                        payload = bytes(self.fragmented)
                        opcode = self.fragment_opcode or OP_BINARY
                        self.fragmented = None
                        self.fragment_opcode = None
                    elif not fin:
                        if opcode != OP_BINARY:
                            raise PushProtocolError("fragmented_nonbinary")
                        self.fragmented = bytearray(payload)
                        self.fragment_opcode = opcode
                        continue
                    if opcode != OP_BINARY:
                        raise PushProtocolError("unsupported_frame_opcode")
                    seq_cs, seq_sc_received, message_type, data, _compressed = _decode_envelope(payload, "CsMessage")
                    self._dispatch_message(seq_cs, seq_sc_received, message_type, data)
                except (EOFError, ConnectionError, BrokenPipeError):
                    return {"status": "closed", "reason": "peer_disconnect"}
                except socket.timeout:
                    _audit(self.audit_log, kind="push_connection", status="timeout", reason="read_timeout")
                    self.close(1001, "timeout")
                    return {"status": "closed", "reason": "timeout"}
                except PushProtocolError as exc:
                    reason = str(exc)
                    _audit(self.audit_log, kind="push_connection", status="rejected", reason=reason)
                    self.close(1002, "protocol error")
                    return {"status": "rejected", "reason": reason}
        finally:
            if self.push_service is not None:
                self.push_service.unregister(self, self.account)


def handle_upgrade(
    handler: Any,
    accounts: Any,
    audit_log: Any = None,
    *,
    on_login: Optional[Callable[..., Any]] = None,
    initializer: Optional[Callable[..., Any]] = None,
    on_business_message: Optional[Callable[..., Any]] = None,
    timeout: float = 20.0,
    server_sequence: int = DEFAULT_SERVER_SEQUENCE,
    heartbeat_interval_ms: int = DEFAULT_HEARTBEAT_INTERVAL_MS,
    allow_unmasked_local: bool = False,
    push_service: Any = None,
    clock: Callable[[], int] | None = None,
) -> Dict[str, Any]:
    """Upgrade a BaseHTTPRequestHandler and serve one local push session.

    The callback, when provided, receives ``(account, login_fields,
    connection)`` after a valid ``CsLogin``.  It must explicitly send any
    future business messages; this function never claims login success by
    fabricating ``ScLoginBegin``/``ScLoginEnd``.  ``allow_unmasked_local`` is
    deliberately false by default.  A caller may enable it only for an
    explicitly loopback-only compatibility listener after observing the
    legacy client behavior; it does not relax any other validation.
    """

    method = str(getattr(handler, "command", "GET")).upper()
    upgrade = _header(handler, "Upgrade").lower().strip()
    connection = {part.strip().lower() for part in _header(handler, "Connection").split(",")}
    version = _header(handler, "Sec-WebSocket-Version").strip()
    key = _header(handler, "Sec-WebSocket-Key").strip()
    if method != "GET" or upgrade != "websocket" or "upgrade" not in connection or version != "13" or not key:
        _reject_http(handler, 400, "Bad Request")
        return {"status": "rejected", "reason": "invalid_websocket_upgrade"}
    try:
        decoded_key = base64.b64decode(key, validate=True)
    except Exception:
        decoded_key = b""
    if len(decoded_key) != 16:
        _reject_http(handler, 400, "Bad Request")
        return {"status": "rejected", "reason": "invalid_websocket_key"}

    try:
        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", _accept_key(key))
        handler.end_headers()
        flush = getattr(handler.wfile, "flush", None)
        if flush is not None:
            flush()
    except Exception:
        return {"status": "closed", "reason": "upgrade_write_failed"}

    connection_socket = getattr(handler, "connection", None)
    old_timeout = None
    try:
        if connection_socket is not None:
            old_timeout = connection_socket.gettimeout()
            connection_socket.settimeout(timeout)
        session = _Connection(
            handler=handler,
            accounts=accounts,
            audit_log=audit_log,
            on_login=on_login,
            initializer=initializer,
            on_business_message=on_business_message,
            server_sequence=server_sequence,
            heartbeat_interval_ms=heartbeat_interval_ms,
            allow_unmasked_local=allow_unmasked_local,
            push_service=push_service,
            clock=clock or (lambda: int(time.time())),
        )
        _audit(audit_log, kind="push_connection", status="upgraded")
        return session.run()
    finally:
        if connection_socket is not None:
            try:
                connection_socket.settimeout(old_timeout)
            except Exception:
                pass


__all__ = [
    "DEFAULT_HEARTBEAT_INTERVAL_MS",
    "DEFAULT_SERVER_SEQUENCE",
    "MAX_MESSAGE_BYTES",
    "PushProtocolError",
    "handle_upgrade",
]
