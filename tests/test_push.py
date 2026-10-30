import base64
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from accounts import LocalAccounts  # noqa: E402
from push import (  # noqa: E402
    DEFAULT_HEARTBEAT_INTERVAL_MS,
    _decode_envelope,
    _encode_cs_message,
    _encode_message,
    _length_delimited,
    _rpc_fallback_type,
    _varint_field,
    handle_upgrade,
)


def _read_exact(sock, size):
    output = bytearray()
    while len(output) < size:
        chunk = sock.recv(size - len(output))
        if not chunk:
            raise EOFError("peer closed")
        output.extend(chunk)
    return bytes(output)


def _masked_frame(opcode, payload, key=b"\x01\x02\x03\x04"):
    payload = bytes(payload)
    masked = bytes(value ^ key[index % 4] for index, value in enumerate(payload))
    if len(payload) < 126:
        header = bytes((0x80 | opcode, 0x80 | len(payload)))
    elif len(payload) <= 0xFFFF:
        header = bytes((0x80 | opcode, 0x80 | 126)) + len(payload).to_bytes(2, "big")
    else:
        header = bytes((0x80 | opcode, 0x80 | 127)) + len(payload).to_bytes(8, "big")
    return header + key + masked


def _unmasked_frame(opcode, payload):
    payload = bytes(payload)
    if len(payload) >= 126:
        raise AssertionError("test helper only handles short unmasked frames")
    return bytes((0x80 | opcode, len(payload))) + payload


def _server_frame(sock):
    first, second = _read_exact(sock, 2)
    assert first & 0x80
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(sock, 8), "big")
    assert not (second & 0x80), "server frame must not be masked"
    return first & 0x0F, _read_exact(sock, length)


def _wire_string(number, value):
    raw = value.encode("utf-8")
    return _length_delimited(number, raw)


def _make_guest_account(accounts):
    status, session = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    assert status == 200
    authz = "AccountToken " + session["accountToken"]
    status, entry_result = accounts.handle(
        "POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz
    )
    assert status == 200
    entry = entry_result["entry"]["entry"]
    auth_body = _wire_string(1, entry) + _wire_string(2, "guest")
    status, auth = accounts.handle("POST", "/Auth/", auth_body, "application/wireformat", authz)
    assert status == 200
    record = accounts.verify_access_token(auth["tokenAccess"])
    assert record is not None
    return auth["tokenAccess"], record["openId"]


class _FakeHandler:
    command = "GET"

    def __init__(self, sock):
        self.connection = sock
        self.rfile = sock.makefile("rb")
        self.wfile = sock.makefile("wb")
        key = base64.b64encode(b"0123456789abcdef").decode("ascii")
        self.headers = {
            "Upgrade": "websocket",
            "Connection": "keep-alive, Upgrade",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": key,
        }

    def send_response(self, status, reason=None):
        self.wfile.write(f"HTTP/1.1 {status} {reason or ''}\r\n".encode("ascii"))

    def send_header(self, name, value):
        self.wfile.write(f"{name}: {value}\r\n".encode("ascii"))

    def end_headers(self):
        self.wfile.write(b"\r\n")


class PushTransportTests(unittest.TestCase):
    def _start(self, accounts, audit=None, on_login=None, allow_unmasked_local=False):
        server_sock, client_sock = socket.socketpair()
        handler = _FakeHandler(server_sock)
        result = []

        def run():
            result.append(
                handle_upgrade(
                    handler,
                    accounts,
                    audit,
                    on_login=on_login,
                    timeout=2,
                    allow_unmasked_local=allow_unmasked_local,
                )
            )

        thread = threading.Thread(target=run)
        thread.start()
        return server_sock, client_sock, thread, result

    @staticmethod
    def _read_upgrade(client_sock):
        data = bytearray()
        while b"\r\n\r\n" not in data:
            data.extend(client_sock.recv(4096))
        return bytes(data)

    def test_masked_handshake_connect_heartbeat_ping_and_close(self):
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state)
            token, open_id = _make_guest_account(accounts)
            audit = []
            server_sock, client_sock, thread, result = self._start(accounts, audit)
            try:
                self.assertIn(b"101 Switching Protocols", self._read_upgrade(client_sock))

                connect_data = b"".join(
                    (
                        _wire_string(2, token),
                        # The original WebSocket object starts m_sessionId,
                        # m_csSequence and m_scSequence at zero.  PostSend-
                        # Package increments only the outer CsMessage
                        # sequence to 1; CsConnect.seq_cs was built before
                        # that increment.
                        _varint_field(3, 0),
                        _varint_field(4, 0),
                        _varint_field(5, 0),
                        _wire_string(6, open_id),
                        _varint_field(7, 1),
                        _varint_field(8, 1),
                    )
                )
                connect = _encode_cs_message(1, 0, _encode_message("CsConnect", connect_data))
                client_sock.sendall(_masked_frame(0x2, connect))
                opcode, response = _server_frame(client_sock)
                self.assertEqual(opcode, 0x2)
                seq, ack, message_type, data, compressed = _decode_envelope(response, "ScMessage")
                self.assertEqual((seq, ack, message_type, compressed), (0, 1, "ScConnect", False))
                connect_fields = {number: value[-1][1] for number, value in __import__("push")._parse_fields(data).items()}
                self.assertGreater(connect_fields[1], 0)
                self.assertEqual(connect_fields[4], 1)
                self.assertEqual(connect_fields[5], 0)

                heartbeat_data = _varint_field(1, 123456)
                heartbeat = _encode_cs_message(2, 0, _encode_message("CsHeartbeat", heartbeat_data))
                client_sock.sendall(_masked_frame(0x2, heartbeat))
                opcode, response = _server_frame(client_sock)
                self.assertEqual(opcode, 0x2)
                seq, ack, message_type, data, _ = _decode_envelope(response, "ScMessage")
                self.assertEqual((seq, ack, message_type), (1, 2, "ScHeartbeat"))
                heartbeat_fields = {number: value[-1][1] for number, value in __import__("push")._parse_fields(data).items()}
                self.assertGreater(heartbeat_fields[1], 0)
                self.assertEqual(heartbeat_fields[2], 123456)
                self.assertEqual(heartbeat_fields[3], DEFAULT_HEARTBEAT_INTERVAL_MS)

                client_sock.sendall(_masked_frame(0x9, b"ping"))
                self.assertEqual(_server_frame(client_sock), (0xA, b"ping"))

                client_sock.sendall(_masked_frame(0x8, b"\x03\xe8"))
                self.assertEqual(_server_frame(client_sock), (0x8, b"\x03\xe8"))
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result[0]["reason"], "peer_close")
                self.assertNotIn(token, str(audit))
                self.assertNotIn(open_id, str(audit))
            finally:
                client_sock.close()
                server_sock.close()

    def test_cslogin_calls_initializer_without_fake_success_response(self):
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state)
            token, open_id = _make_guest_account(accounts)
            called = []

            def initializer(account, login_fields, connection):
                called.append((account, login_fields, connection.authenticated, connection.last_client_sequence))

            server_sock, client_sock, thread, result = self._start(accounts, [], initializer)
            try:
                self.assertIn(b"101 Switching Protocols", self._read_upgrade(client_sock))
                connect_data = _wire_string(2, token) + _wire_string(6, open_id) + _varint_field(3, 0)
                client_sock.sendall(_masked_frame(2, _encode_cs_message(1, 0, _encode_message("CsConnect", connect_data))))
                _server_frame(client_sock)
                login_data = _wire_string(1, "guest") + _varint_field(2, 1) + _wire_string(13, token)
                client_sock.sendall(_masked_frame(2, _encode_cs_message(2, 0, _encode_message("CsLogin", login_data))))
                client_sock.settimeout(0.2)
                with self.assertRaises(socket.timeout):
                    client_sock.recv(1)
                self.assertEqual(len(called), 1)
                self.assertEqual(called[0][1]["social_entry"], "guest")
                self.assertTrue(called[0][2])
                self.assertEqual(called[0][3], 2)
                client_sock.settimeout(1)
                client_sock.sendall(_masked_frame(8, b"\x03\xe8"))
                _server_frame(client_sock)
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result[0]["reason"], "peer_close")
            finally:
                client_sock.close()
                server_sock.close()

    def test_rpc_fallback_keeps_handshake_messages_on_dedicated_paths(self):
        # The generic local acknowledgement is intentionally unavailable for
        # the three handshake messages; those paths must continue to perform
        # token verification and login initialization above it.
        self.assertIsNone(_rpc_fallback_type("CsConnect"))
        self.assertIsNone(_rpc_fallback_type("CsHeartbeat"))
        self.assertIsNone(_rpc_fallback_type("CsLogin"))
        self.assertEqual(_rpc_fallback_type("base.cs_free_recharge"), "base.cs_free_recharge")
        self.assertEqual(_rpc_fallback_type("CsTaskInfo"), "ScTaskInfo")
        self.assertIsNone(_rpc_fallback_type("unknown.cs_free_recharge"))

    def test_unmasked_client_frame_is_rejected(self):
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state)
            server_sock, client_sock, thread, result = self._start(accounts)
            try:
                self.assertIn(b"101 Switching Protocols", self._read_upgrade(client_sock))
                client_sock.sendall(b"\x82\x00")
                opcode, payload = _server_frame(client_sock)
                self.assertEqual(opcode, 0x8)
                self.assertEqual(int.from_bytes(payload[:2], "big"), 1002)
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result[0]["reason"], "client frame is not masked")
            finally:
                client_sock.close()
                server_sock.close()

    def test_unmasked_client_frame_requires_explicit_local_opt_in(self):
        with tempfile.TemporaryDirectory() as state:
            accounts = LocalAccounts(state)
            server_sock, client_sock, thread, result = self._start(accounts, allow_unmasked_local=True)
            try:
                self.assertIn(b"101 Switching Protocols", self._read_upgrade(client_sock))
                client_sock.sendall(_unmasked_frame(0x9, b"legacy-local"))
                self.assertEqual(_server_frame(client_sock), (0xA, b"legacy-local"))
                client_sock.sendall(_masked_frame(0x8, b"\x03\xe8"))
                self.assertEqual(_server_frame(client_sock), (0x8, b"\x03\xe8"))
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result[0]["reason"], "peer_close")
            finally:
                client_sock.close()
                server_sock.close()


if __name__ == "__main__":
    unittest.main()
