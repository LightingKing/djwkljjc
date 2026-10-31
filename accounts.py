"""Local account endpoints for the LJJC compatibility server.

The original client uses ``application/wireformat`` (protobuf) for
``AccountSession`` and ``Auth`` and JSON for ``EntryReg`` and ``Account``.
The local implementation supports the original guest path plus a sandbox
username/password path.  It never contacts Tencent, QQ, WeChat, or accepts a
social credential.

Transport contract: ``handle`` receives the already-decompressed request
body.  The original HTTP request may carry ``Content-Encoding: lz4``; that is
a server/transport concern and must be decoded before calling this module.
The returned dictionary is raw JSON data for the caller's existing
``RestfulResponse.raw_json`` wrapper.  The guest lifecycle is:
``AccountSession`` (session A) -> ``EntryReg`` -> ``Account`` -> a fresh
``AccountSession`` (session B) -> ``Auth``.  ``Auth`` may bind session B to an
already registered local ``guest_...`` entry.  ``verify_access_token`` is the
read-only check for later local push/game routes.

The public entry point is :meth:`LocalAccounts.handle`.  Unknown paths return
``None`` so a caller can leave its existing fixture routing in place.
"""

from __future__ import annotations

import base64
import hmac
import json
import hashlib
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Union
from urllib.parse import urlsplit

try:
    from .storage.compat import (
        configure_legacy_connection,
        ensure_unified_store,
        resolve_unified_store_path,
        shared_store_lock,
    )
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from storage.compat import (
        configure_legacy_connection,
        ensure_unified_store,
        resolve_unified_store_path,
        shared_store_lock,
    )


Body = Union[bytes, bytearray, str, None]
Response = Tuple[int, Dict[str, Any]]


class WireFormatError(ValueError):
    """Raised when a protobuf wire-format message is malformed."""


def _read_varint(data: bytes, offset: int) -> Tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if offset >= len(data):
            raise WireFormatError("truncated varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if shift == 63 and byte > 1:
                raise WireFormatError("varint overflow")
            return value, offset
        shift += 7
    raise WireFormatError("varint too long")


def _parse_wire_fields(body: bytes) -> Dict[int, list]:
    """Parse enough protobuf wire format for the two original API messages.

    Values are ``int`` for wire type 0 and ``bytes`` for wire type 2.  Fixed
    width unknown fields are skipped, while groups are rejected because no
    supported request uses them.
    """

    fields: Dict[int, list] = {}
    offset = 0
    while offset < len(body):
        tag, offset = _read_varint(body, offset)
        number = tag >> 3
        wire_type = tag & 7
        if number <= 0:
            raise WireFormatError("invalid field number")
        if wire_type == 0:
            value, offset = _read_varint(body, offset)
        elif wire_type == 1:
            if offset + 8 > len(body):
                raise WireFormatError("truncated fixed64")
            value = body[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            length, offset = _read_varint(body, offset)
            end = offset + length
            if end > len(body):
                raise WireFormatError("truncated bytes")
            value = body[offset:end]
            offset = end
        elif wire_type == 5:
            if offset + 4 > len(body):
                raise WireFormatError("truncated fixed32")
            value = body[offset : offset + 4]
            offset += 4
        else:
            raise WireFormatError("unsupported protobuf wire type")
        fields.setdefault(number, []).append(value)
    return fields


def _text(fields: Mapping[int, list], number: int) -> str:
    values = fields.get(number, [])
    if not values:
        return ""
    value = values[-1]
    if not isinstance(value, bytes):
        raise WireFormatError("field is not length-delimited")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WireFormatError("field is not UTF-8") from exc


def _body_bytes(body: Body) -> bytes:
    if body is None:
        return b""
    if isinstance(body, str):
        return body.encode("utf-8")
    return bytes(body)


def _is_wireformat(content_type: Optional[str]) -> bool:
    if not content_type:
        return True
    return content_type.split(";", 1)[0].strip().lower() in {
        "application/wireformat",
        "application/octet-stream",
    }


def _is_json(content_type: Optional[str]) -> bool:
    if not content_type:
        return True
    return content_type.split(";", 1)[0].strip().lower() in {
        "application/json",
        "text/json",
    }


def _json_object(body: Body) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
    try:
        value = json.loads(_body_bytes(body).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, (400, {"error": "invalid_json", "message": "JSON body required"})
    if not isinstance(value, dict):
        return None, (400, {"error": "invalid_json", "message": "JSON object required"})
    return value, None


_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")


def _normalise_username(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()


def _validate_credentials(payload: Mapping[str, Any]) -> Tuple[str, str, Optional[Response]]:
    username = _normalise_username(
        payload.get("username", payload.get("account", payload.get("userName", "")))
    )
    password = payload.get("password", payload.get("pass", ""))
    if not isinstance(password, str):
        password = ""
    if not _USERNAME_RE.fullmatch(username):
        return "", "", (400, {"error": "invalid_username", "message": "username must be 3-64 ASCII characters"})
    if len(password) < 6 or len(password) > 256:
        return "", "", (400, {"error": "invalid_password", "message": "password must be 6-256 characters"})
    return username, password, None


class LocalAccounts:
    """Persistent local account implementation for the original guest flow.

    ``state_dir`` contains ``accounts.sqlite3``.  The optional clock and TTLs
    make expiry deterministic for tests while leaving the normal constructor
    dependency-free.
    """

    def __init__(
        self,
        state_dir: Union[str, Path],
        *,
        session_ttl_seconds: int = 900,
        access_ttl_seconds: int = 30 * 86400,
        clock: Optional[Callable[[], int]] = None,
        unified_store_path: str | Path | None = None,
    ) -> None:
        if session_ttl_seconds <= 0 or access_ttl_seconds <= 0:
            raise ValueError("token TTLs must be positive")
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.unified_store_path = resolve_unified_store_path(self.state_dir, unified_store_path)
        if self.unified_store_path is not None:
            ensure_unified_store(self.unified_store_path)
            self.db_path = self.unified_store_path
        else:
            self.db_path = self.state_dir / "accounts.sqlite3"
        self.session_ttl_seconds = int(session_ttl_seconds)
        self.access_ttl_seconds = int(access_ttl_seconds)
        self._clock = clock or (lambda: int(time.time()))
        # Reuse one process-wide lock for every instance that opens the same
        # SQLite path.  The HTTP server normally has one account handler, but
        # tests, hot reloads, and local admin tools can construct multiple
        # instances against one store; a per-instance lock would let those
        # callers race through read/modify/write account operations.
        self._lock = shared_store_lock(self.db_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        configure_legacy_connection(connection)
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id INTEGER PRIMARY KEY,
                    entry TEXT NOT NULL UNIQUE,
                    entry_type TEXT NOT NULL CHECK (entry_type = 'guest'),
                    open_id TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    expire_seconds INTEGER NOT NULL,
                    access_token TEXT UNIQUE,
                    access_expires_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_token TEXT PRIMARY KEY,
                    account_id INTEGER,
                    pending_entry TEXT,
                    device_hash TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS device_bindings (
                    device_hash TEXT PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    entry TEXT NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
                );
                CREATE TABLE IF NOT EXISTS device_binding_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_hash TEXT NOT NULL,
                    account_id INTEGER NOT NULL,
                    migrated_at INTEGER NOT NULL,
                    reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_expires
                    ON sessions(expires_at);
                """
            )
            # Databases created by the first local-server revision have no
            # device_hash column.  Keep those guest accounts and add only the
            # new hashed binding column in place.
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sessions)")
            }
            if "device_hash" not in columns:
                connection.execute("ALTER TABLE sessions ADD COLUMN device_hash TEXT")
            account_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(accounts)")
            }
            # Keep the legacy entry_type='guest' constraint intact so old
            # databases remain readable.  login_type carries the local
            # username/password capability without changing guest entries.
            additions = {
                "login_type": "TEXT NOT NULL DEFAULT 'guest'",
                "username": "TEXT",
                "password_hash": "TEXT",
                "password_salt": "TEXT",
                "password_reset_token": "TEXT",
                "password_reset_expires_at": "INTEGER",
            }
            for name, definition in additions.items():
                if name not in account_columns:
                    connection.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username "
                "ON accounts(username) WHERE username IS NOT NULL"
            )

    @staticmethod
    def _token(prefix: str) -> str:
        return prefix + secrets.token_urlsafe(32)

    @staticmethod
    def _account_id() -> int:
        # Keep the identifier in the positive 31-bit range.  The original
        # client exposes accountId through Lua/JSON numeric values, where a
        # large 64-bit integer would lose precision.
        return secrets.randbelow(2_000_000_000) + 1

    @staticmethod
    def _error(code: str, message: str) -> Dict[str, Any]:
        return {"error": code, "message": message}

    @staticmethod
    def _route(path: str) -> str:
        # Restifizer passes paths with a trailing slash; accepting both forms
        # is useful for local callers without changing the route semantics.
        parsed = urlsplit(path or "")
        route = parsed.path or "/"
        if route != "/":
            route = route.rstrip("/") + "/"
        return route

    def _now(self) -> int:
        return int(self._clock())

    def _authorized_session(
        self, connection: sqlite3.Connection, authorization: Optional[str]
    ) -> Tuple[Optional[sqlite3.Row], Optional[Response]]:
        if not authorization:
            return None, (401, self._error("missing_account_token", "AccountToken required"))
        parts = authorization.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "accounttoken" or not parts[1].strip():
            return None, (401, self._error("invalid_account_token", "invalid AccountToken"))
        token = parts[1].strip()
        row = connection.execute(
            "SELECT * FROM sessions WHERE session_token = ?", (token,)
        ).fetchone()
        if row is None:
            return None, (401, self._error("invalid_account_token", "invalid AccountToken"))
        if int(row["expires_at"]) <= self._now():
            return None, (401, self._error("expired_account_token", "AccountToken expired"))
        return row, None

    @staticmethod
    def _guest_json(body: Body) -> Tuple[Optional[Dict[str, Any]], Optional[Response]]:
        try:
            value = json.loads(_body_bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, (400, {"error": "invalid_json", "message": "JSON body required"})
        if not isinstance(value, dict):
            return None, (400, {"error": "invalid_json", "message": "JSON object required"})
        social_keys = {
            "socialType",
            "socialToken",
            "socialPlatform",
            "socialExpire",
            "bbrrUin",
        }
        if any(key in value for key in social_keys):
            return None, (403, {"error": "guest_only", "message": "social login is disabled"})
        if value.get("entryType") != "guest":
            return None, (403, {"error": "guest_only", "message": "guest login required"})
        return value, None

    def _new_pending_entry(self) -> str:
        return "guest_" + secrets.token_hex(16)

    @staticmethod
    def _device_hash(device_id: str) -> str:
        """Return a stable opaque binding key; never persist device_id itself."""

        normalized = device_id.strip()
        return hashlib.sha256(
            b"ljjc-local-device-binding-v1\0" + normalized.encode("utf-8")
        ).hexdigest()

    def _entry_result(self, entry: str, entry_type: str = "guest", username: str | None = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {"entry": {"entry": entry, "entryType": entry_type}}
        if username:
            result["username"] = username
        return result

    @staticmethod
    def _password_hash(password: str, salt: bytes | None = None) -> Tuple[str, str]:
        """Return a portable scrypt hash and base64 salt."""

        raw_salt = salt or secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=raw_salt,
            n=2**14,
            r=8,
            p=1,
            dklen=32,
        )
        encoded = "scrypt$16384$8$1$" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return encoded, base64.urlsafe_b64encode(raw_salt).decode("ascii").rstrip("=")

    @classmethod
    def _verify_password(cls, password: str, password_hash: str | None, salt_text: str | None) -> bool:
        if not password_hash or not salt_text or not password_hash.startswith("scrypt$16384$8$1$"):
            return False
        try:
            padding = "=" * (-len(salt_text) % 4)
            salt = base64.urlsafe_b64decode(salt_text + padding)
            expected = password_hash.rsplit("$", 1)[-1]
            actual, _ = cls._password_hash(password, salt)
            return hmac.compare_digest(actual.rsplit("$", 1)[-1], expected)
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _account_entry(account: sqlite3.Row) -> str:
        return str(account["entry"])

    def _auth_result(self, account: sqlite3.Row, now: int) -> Dict[str, Any]:
        access_token = self._token("local_access_")
        # Caller updates the row in its surrounding transaction.  Keeping the
        # shape identical to the guest Auth response lets the game bootstrap
        # use the same access token path for both local login modes.
        return {
            "accountId": int(account["account_id"]),
            "openId": account["open_id"],
            "tokenAccess": access_token,
            "createTime": now,
            "expireSeconds": self.access_ttl_seconds,
            "entry": self._account_entry(account),
            "entryType": "password" if account["login_type"] == "password" else "guest",
            "username": account["username"] or None,
        }

    def _session_account_id(self, connection: sqlite3.Connection, authorization: Optional[str]) -> Tuple[Optional[int], Optional[Response]]:
        if not authorization:
            return None, (401, {"error": "missing_account_token", "message": "AccountToken required"})
        parts = authorization.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "accounttoken" or not parts[1].strip():
            return None, (401, {"error": "invalid_account_token", "message": "invalid AccountToken"})
        row = connection.execute(
            "SELECT account_id, expires_at FROM sessions WHERE session_token = ?",
            (parts[1].strip(),),
        ).fetchone()
        if row is None:
            return None, (401, {"error": "invalid_account_token", "message": "invalid AccountToken"})
        if int(row["expires_at"]) <= self._now():
            return None, (401, {"error": "expired_account_token", "message": "AccountToken expired"})
        if row["account_id"] is None:
            return None, (409, {"error": "account_not_selected", "message": "login or bind an account first"})
        return int(row["account_id"]), None

    def _create_or_get_password_account(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        username: str,
        password: str,
    ) -> Tuple[Optional[sqlite3.Row], Optional[Response]]:
        existing = connection.execute(
            "SELECT * FROM accounts WHERE username = ?", (username,)
        ).fetchone()
        if existing is not None:
            if not self._verify_password(password, existing["password_hash"], existing["password_salt"]):
                return None, (409, {"error": "username_exists", "message": "username is already registered"})
            account = existing
        else:
            now = self._now()
            entry = "account_" + username
            password_hash, password_salt = self._password_hash(password)
            try:
                connection.execute(
                    """INSERT INTO accounts
                       (account_id, entry, entry_type, open_id, created_at,
                        expire_seconds, login_type, username, password_hash,
                        password_salt)
                       VALUES (?, ?, 'guest', ?, ?, ?, 'password', ?, ?, ?)""",
                    (
                        self._account_id(),
                        entry,
                        "local_account_" + secrets.token_hex(16),
                        now,
                        self.access_ttl_seconds,
                        username,
                        password_hash,
                        password_salt,
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM accounts WHERE username = ?", (username,)
                ).fetchone()
                if existing is None:
                    return None, (409, {"error": "username_exists", "message": "username is already registered"})
                account = existing
            else:
                account = connection.execute(
                    "SELECT * FROM accounts WHERE username = ?", (username,)
                ).fetchone()
        assert account is not None
        connection.execute(
            "UPDATE sessions SET account_id = ?, pending_entry = ? WHERE session_token = ?",
            (int(account["account_id"]), account["entry"], session["session_token"]),
        )
        return account, None

    def _handle_password_login(self, body: Body, content_type: Optional[str], authorization: Optional[str]) -> Response:
        if not _is_json(content_type):
            return 415, self._error("unsupported_content_type", "JSON required")
        payload, error = _json_object(body)
        if error is not None:
            return error
        assert payload is not None
        username, password, error = _validate_credentials(payload)
        if error is not None:
            return error
        now = self._now()
        with self._lock, closing(self._connect()) as connection, connection:
            account = connection.execute(
                "SELECT * FROM accounts WHERE username = ? AND login_type = 'password'",
                (username,),
            ).fetchone()
            if account is None or not self._verify_password(password, account["password_hash"], account["password_salt"]):
                return 401, self._error("invalid_credentials", "username or password is incorrect")
            if authorization:
                session, session_error = self._authorized_session(connection, authorization)
                if session_error is not None:
                    return session_error
                assert session is not None
                connection.execute(
                    "UPDATE sessions SET account_id = ?, pending_entry = ? WHERE session_token = ?",
                    (int(account["account_id"]), account["entry"], session["session_token"]),
                )
            result = self._auth_result(account, now)
            connection.execute(
                "UPDATE accounts SET access_token = ?, access_expires_at = ? WHERE account_id = ?",
                (result["tokenAccess"], now + self.access_ttl_seconds, int(account["account_id"])),
            )
            return 200, result

    def _handle_cached_password_auth(self, payload: dict, authorization: Optional[str]) -> Response:
        """Refresh a cached local login without retaining its password on-device."""
        token = payload.get("accessToken")
        identity = payload.get("username", payload.get("entry"))
        if not isinstance(token, str) or not token or not isinstance(identity, str) or not identity:
            return 401, self._error("invalid_credentials", "cached login is invalid")
        now = self._now()
        with self._lock, closing(self._connect()) as connection, connection:
            session, error = self._authorized_session(connection, authorization)
            if error is not None:
                return error
            account = connection.execute(
                "SELECT * FROM accounts WHERE access_token = ? AND login_type = 'password'", (token,)
            ).fetchone()
            if (account is None or int(account["access_expires_at"] or 0) <= now
                    or identity.casefold() not in {str(account["username"]).casefold(), str(account["entry"]).casefold()}):
                return 401, self._error("invalid_credentials", "cached login expired or invalid")
            if session["account_id"] is not None and int(session["account_id"]) != int(account["account_id"]):
                # AccountSession seeds a device's old guest binding before it
                # knows which local account the login window cached. A verified
                # password token may replace that seed, as password login does.
                # An explicitly rebound session still cannot switch scope here.
                binding = connection.execute(
                    "SELECT account_id, entry FROM device_bindings WHERE device_hash = ?",
                    (session["device_hash"],),
                ).fetchone() if session["device_hash"] else None
                if (binding is None or int(binding["account_id"]) != int(session["account_id"])
                        or binding["entry"] != session["pending_entry"]):
                    return 403, self._error("account_scope", "cached login belongs to another account")
            result = self._auth_result(account, now)
            connection.execute(
                "UPDATE sessions SET account_id = ?, pending_entry = ? WHERE session_token = ?",
                (int(account["account_id"]), account["entry"], session["session_token"]),
            )
            connection.execute(
                "UPDATE accounts SET access_token = ?, access_expires_at = ?, expire_seconds = ? WHERE account_id = ?",
                (result["tokenAccess"], now + self.access_ttl_seconds, self.access_ttl_seconds, int(account["account_id"])),
            )
            return 200, result

    def _handle_password_register(self, body: Body, content_type: Optional[str], authorization: Optional[str], route: str) -> Response:
        if not _is_json(content_type):
            return 415, self._error("unsupported_content_type", "JSON required")
        payload, error = _json_object(body)
        if error is not None:
            return error
        assert payload is not None
        username, password, error = _validate_credentials(payload)
        if error is not None:
            return error
        with self._lock, closing(self._connect()) as connection, connection:
            session, session_error = self._authorized_session(connection, authorization)
            if session_error is not None:
                return session_error
            assert session is not None
            account, account_error = self._create_or_get_password_account(connection, session, username, password)
            if account_error is not None:
                return account_error
            assert account is not None
            result = self._entry_result(account["entry"], "password", username)
            if route == "/Account/":
                result["accountId"] = int(account["account_id"])
            return 200, result

    def _handle_password_reset(self, body: Body, content_type: Optional[str], authorization: Optional[str]) -> Response:
        if not _is_json(content_type):
            return 415, self._error("unsupported_content_type", "JSON required")
        payload, error = _json_object(body)
        if error is not None:
            return error
        assert payload is not None
        new_password = payload.get("newPassword", payload.get("new_password", ""))
        if not isinstance(new_password, str) or not 6 <= len(new_password) <= 256:
            return 400, self._error("invalid_password", "newPassword must be 6-256 characters")
        with self._lock, closing(self._connect()) as connection, connection:
            account_id, auth_error = self._session_account_id(connection, authorization)
            if auth_error is not None:
                return auth_error
            assert account_id is not None
            account = connection.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
            if account is None:
                return 404, self._error("unknown_account", "account is not registered")
            old_password = payload.get("oldPassword", payload.get("old_password"))
            if old_password is not None and account["password_hash"] and not self._verify_password(str(old_password), account["password_hash"], account["password_salt"]):
                return 401, self._error("invalid_credentials", "old password is incorrect")
            password_hash, password_salt = self._password_hash(new_password)
            connection.execute(
                "UPDATE accounts SET password_hash = ?, password_salt = ?, login_type = 'password' WHERE account_id = ?",
                (password_hash, password_salt, account_id),
            )
            return 200, {"ok": True, "username": account["username"]}

    def _handle_exist_entry(self, body: Body, content_type: Optional[str]) -> Response:
        if not _is_json(content_type):
            return 415, self._error("unsupported_content_type", "JSON required")
        payload, error = _json_object(body)
        if error is not None:
            return error
        assert payload is not None
        username = _normalise_username(payload.get("username", payload.get("entry", "")))
        if not username:
            return 400, self._error("invalid_username", "username or entry required")
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT account_id, entry, username FROM accounts WHERE username = ? OR entry = ?",
                (username, username),
            ).fetchone()
            exists = row is not None
            return 200, {"exists": exists, "usernameExists": exists and bool(row["username"]), "entryExists": exists}

    def _local_verification(self, body: Body, content_type: Optional[str], *, require_session: bool,
                            authorization: Optional[str]) -> Response:
        """Handle verification/captcha seams without contacting a provider.

        The retired service used SMS/e-mail captcha providers.  The local
        account server has no such provider, so these endpoints are an
        explicit sandbox acknowledgement.  A caller may still send a code;
        it is recorded only in the response and never persisted as a secret.
        """

        if require_session:
            with self._lock, closing(self._connect()) as connection:
                _session, error = self._authorized_session(connection, authorization)
                if error is not None:
                    return error
        value: Dict[str, Any] = {}
        if _is_json(content_type) and body:
            payload, error = _json_object(body)
            if error is not None:
                return error
            assert payload is not None
            for key in ("verifyCode", "verify_code", "captcha", "code"):
                if key in payload:
                    value["code"] = str(payload[key])[:32]
                    break
        return 200, {"verifyCodePassed": True, "passwordVerifyCodePassed": True,
                     "sandbox": True, **value}

    def _handle_new_entry(self, body: Body, content_type: Optional[str], authorization: Optional[str]) -> Response:
        """Compatibility wrapper for the account service's NewEntry endpoint."""

        if not _is_json(content_type):
            return 415, self._error("unsupported_content_type", "JSON required")
        payload, error = _json_object(body)
        if error is not None:
            return error
        assert payload is not None
        # NewEntry is the historical combined registration endpoint.  Keep
        # the same local account creation rules as EntryReg/Account.
        return self._handle_entry_or_account("/EntryReg/", body, content_type, authorization)

    def _account_info(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "accountId": int(row["account_id"]),
            "openId": row["open_id"],
            "entry": row["entry"],
            "entryType": "password" if row["login_type"] == "password" else "guest",
            "username": row["username"] or None,
            "sandbox": True,
        }

    def _handle_account_entry(self, method: str, route: str, body: Body,
                              content_type: Optional[str], authorization: Optional[str]) -> Response:
        """Return or check local account-entry metadata.

        Only the caller's current local session may inspect account metadata;
        this avoids turning the compatibility endpoint into an account
        enumeration service while preserving the client flow.
        """

        method = method.upper()
        dynamic = route[len("/AccountEntry/"):].strip("/") if route.startswith("/AccountEntry/") else ""
        if dynamic.endswith("/exist"):
            dynamic = dynamic[:-5].rstrip("/")
            if method not in {"GET", "POST"}:
                return 405, self._error("method_not_allowed", "GET or POST required")
            with self._lock, closing(self._connect()) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM accounts WHERE entry = ? OR username = ?", (dynamic, dynamic)
                ).fetchone() is not None
            return 200, {"exists": exists, "entryExists": exists, "sandbox": True}
        if method == "POST":
            return self._handle_entry_or_account("/Account/", body, content_type, authorization)
        if method != "GET":
            return 405, self._error("method_not_allowed", "GET or POST required")
        with self._lock, closing(self._connect()) as connection:
            session, error = self._authorized_session(connection, authorization)
            if error is not None:
                return error
            assert session is not None
            entry = dynamic
            if not entry and _is_json(content_type) and body:
                payload, parse_error = _json_object(body)
                if parse_error is not None:
                    return parse_error
                assert payload is not None
                entry = str(payload.get("entry", payload.get("username", ""))).strip()
            if not entry:
                entry = str(session["pending_entry"] or "")
            row = connection.execute(
                "SELECT * FROM accounts WHERE entry = ? OR username = ?", (entry, entry)
            ).fetchone()
            if row is None:
                return 404, self._error("unknown_entry", "local account entry not found")
            if session["account_id"] is not None and int(session["account_id"]) != int(row["account_id"]):
                return 403, self._error("account_scope", "entry belongs to another local account")
            return 200, self._account_info(row)

    def _handle_guest_bind(self, body: Body, content_type: Optional[str], authorization: Optional[str]) -> Response:
        if not _is_json(content_type):
            return 415, self._error("unsupported_content_type", "JSON required")
        payload, error = _json_object(body)
        if error is not None:
            return error
        assert payload is not None
        username, password, error = _validate_credentials(payload)
        if error is not None:
            return error
        with self._lock, closing(self._connect()) as connection, connection:
            account_id, auth_error = self._session_account_id(connection, authorization)
            if auth_error is not None:
                return auth_error
            assert account_id is not None
            account = connection.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
            if account is None:
                return 404, self._error("unknown_account", "account is not registered")
            existing = connection.execute("SELECT * FROM accounts WHERE username = ?", (username,)).fetchone()
            if existing is not None and int(existing["account_id"]) != account_id:
                return 409, self._error("username_exists", "username is already registered")
            if existing is not None and not self._verify_password(password, existing["password_hash"], existing["password_salt"]):
                return 401, self._error("invalid_credentials", "username or password is incorrect")
            password_hash, password_salt = self._password_hash(password)
            connection.execute(
                "UPDATE accounts SET username = ?, password_hash = ?, password_salt = ?, login_type = 'password' WHERE account_id = ?",
                (username, password_hash, password_salt, account_id),
            )
            return 200, self._entry_result(account["entry"], "password", username)

    def _ensure_account(
        self,
        connection: sqlite3.Connection,
        session: sqlite3.Row,
        entry: str,
        device_hash: Optional[str] = None,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM accounts WHERE entry = ?", (entry,)
        ).fetchone()
        if row is None:
            now = self._now()
            account_id = self._account_id()
            open_id = "local_guest_" + secrets.token_hex(16)
            connection.execute(
                """INSERT INTO accounts
                   (account_id, entry, entry_type, open_id, created_at, expire_seconds)
                   VALUES (?, ?, 'guest', ?, ?, ?)""",
                (account_id, entry, open_id, now, self.access_ttl_seconds),
            )
            row = connection.execute(
                "SELECT * FROM accounts WHERE entry = ?", (entry,)
            ).fetchone()
        connection.execute(
            "UPDATE sessions SET account_id = ?, pending_entry = ? WHERE session_token = ?",
            (int(row["account_id"]), entry, session["session_token"]),
        )
        if device_hash:
            self._bind_device(connection, device_hash, row, entry)
        return row

    def _bind_device(
        self,
        connection: sqlite3.Connection,
        device_hash: str,
        account: sqlite3.Row,
        entry: str,
    ) -> None:
        """Bind one opaque device key to one guest account, idempotently.

        A pre-existing binding wins.  The conflict path is recorded for
        diagnosis rather than silently moving a device between accounts.
        """

        account_id = int(account["account_id"])
        existing = connection.execute(
            "SELECT account_id, entry FROM device_bindings WHERE device_hash = ?",
            (device_hash,),
        ).fetchone()
        if existing is None:
            try:
                connection.execute(
                    """INSERT INTO device_bindings
                       (device_hash, account_id, entry, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (device_hash, account_id, entry, self._now()),
                )
            except sqlite3.IntegrityError:
                # A legacy database may already contain the same entry under a
                # different hash.  Keep the first deterministic binding and
                # record why this request was not moved.
                existing = connection.execute(
                    "SELECT account_id FROM device_bindings WHERE entry = ?",
                    (entry,),
                ).fetchone()
                if existing is not None:
                    connection.execute(
                        """INSERT INTO device_binding_migrations
                           (device_hash, account_id, migrated_at, reason)
                           VALUES (?, ?, ?, ?)""",
                        (device_hash, int(existing["account_id"]), self._now(), "entry_already_bound"),
                    )
        elif int(existing["account_id"]) != account_id or existing["entry"] != entry:
            connection.execute(
                """INSERT INTO device_binding_migrations
                   (device_hash, account_id, migrated_at, reason)
                   VALUES (?, ?, ?, ?)""",
                (device_hash, int(existing["account_id"]), self._now(), "binding_conflict_kept_existing"),
            )

    def _rebind_device(
        self,
        connection: sqlite3.Connection,
        device_hash: str,
        account: sqlite3.Row,
        entry: str,
    ) -> None:
        """Move a local device binding after an explicit guest re-login.

        The client keeps a stable guest entry in its local compatibility seam.
        A reset or an earlier test run can leave the opaque device key bound to
        a different local guest account.  Once Auth has supplied an existing
        guest entry, that explicit request is sufficient to repair the local
        binding so the next AccountSession returns the same account again.
        """

        account_id = int(account["account_id"])
        existing = connection.execute(
            "SELECT account_id, entry FROM device_bindings WHERE device_hash = ?",
            (device_hash,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO device_bindings
                   (device_hash, account_id, entry, created_at)
                   VALUES (?, ?, ?, ?)""",
                (device_hash, account_id, entry, self._now()),
            )
            return
        if int(existing["account_id"]) == account_id and existing["entry"] == entry:
            return
        connection.execute(
            """INSERT INTO device_binding_migrations
               (device_hash, account_id, migrated_at, reason)
               VALUES (?, ?, ?, ?)""",
            (device_hash, account_id, self._now(), "binding_reassigned_by_guest_auth"),
        )
        connection.execute(
            """UPDATE device_bindings
               SET account_id = ?, entry = ?, created_at = ?
               WHERE device_hash = ?""",
            (account_id, entry, self._now(), device_hash),
        )

    def _handle_account_session(
        self, body: Body, content_type: Optional[str]
    ) -> Response:
        if not _is_wireformat(content_type):
            return 415, self._error("unsupported_content_type", "wireformat required")
        try:
            fields = _parse_wire_fields(_body_bytes(body))
            entry_type = _text(fields, 3)
            device_id = _text(fields, 5)
        except WireFormatError as exc:
            return 400, self._error("invalid_wireformat", str(exc))
        if entry_type not in ("", "guest", "password", "account", "username"):
            return 403, self._error("unsupported_entry_type", "only local guest or password sessions are supported")
        now = self._now()
        token = self._token("local_session_")
        device_hash = self._device_hash(device_id) if device_id.strip() else None
        with self._lock, closing(self._connect()) as connection, connection:
            pending_entry = None
            account_id = None
            if device_hash:
                binding = connection.execute(
                    "SELECT account_id, entry FROM device_bindings WHERE device_hash = ?",
                    (device_hash,),
                ).fetchone()
                if binding is not None:
                    account_id = int(binding["account_id"])
                    pending_entry = binding["entry"]
            connection.execute(
                """INSERT INTO sessions
                   (session_token, account_id, pending_entry, device_hash,
                    created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (token, account_id, pending_entry, device_hash, now, now + self.session_ttl_seconds),
            )
        return 200, {"accountToken": token}

    def _handle_entry_or_account(
        self,
        route: str,
        body: Body,
        content_type: Optional[str],
        authorization: Optional[str],
    ) -> Response:
        if not _is_json(content_type):
            return 415, self._error("unsupported_content_type", "JSON required")
        payload, parse_error = _json_object(body)
        if parse_error is not None:
            return parse_error
        assert payload is not None
        requested_type = str(payload.get("entryType", payload.get("loginType", "guest"))).casefold()
        if requested_type in {"password", "account", "username"} or "username" in payload or "userName" in payload:
            return self._handle_password_register(body, content_type, authorization, route)
        _, error = self._guest_json(body)
        if error is not None:
            return error
        with self._lock, closing(self._connect()) as connection, connection:
            session, error = self._authorized_session(connection, authorization)
            if error is not None:
                return error
            assert session is not None
            entry = session["pending_entry"] or self._new_pending_entry()
            connection.execute(
                "UPDATE sessions SET pending_entry = ? WHERE session_token = ?",
                (entry, session["session_token"]),
            )
            if route == "/Account/":
                self._ensure_account(connection, session, entry, session["device_hash"])
            return 200, self._entry_result(entry)

    def _handle_auth(
        self,
        body: Body,
        content_type: Optional[str],
        authorization: Optional[str],
    ) -> Response:
        if content_type and _is_json(content_type):
            payload, parse_error = _json_object(body)
            if parse_error is not None:
                return parse_error
            assert payload is not None
            if "accessToken" in payload:
                return self._handle_cached_password_auth(payload, authorization)
            requested_type = str(payload.get("entryType", payload.get("loginType", "password"))).casefold()
            if requested_type in {"password", "account", "username"} or "username" in payload or "userName" in payload:
                return self._handle_password_login(body, content_type, authorization)
        if not _is_wireformat(content_type):
            return 415, self._error("unsupported_content_type", "wireformat required")
        try:
            fields = _parse_wire_fields(_body_bytes(body))
            entry = _text(fields, 1)
            entry_type = _text(fields, 2)
            social_type = _text(fields, 3)
            social_token = _text(fields, 5)
            social_platform = fields.get(6, [0])[-1]
            social_expire = fields.get(7, [0])[-1]
            if not isinstance(social_platform, int) or not isinstance(social_expire, int):
                raise WireFormatError("social fields have invalid wire type")
        except WireFormatError as exc:
            return 400, self._error("invalid_wireformat", str(exc))
        if entry_type != "guest" or social_type or social_token or social_platform or social_expire:
            return 403, self._error("guest_only", "social login is disabled")
        if not entry:
            return 400, self._error("invalid_entry", "guest entry required")

        now = self._now()
        with self._lock, closing(self._connect()) as connection, connection:
            session, error = self._authorized_session(connection, authorization)
            if error is not None:
                return error
            assert session is not None
            account = connection.execute(
                "SELECT * FROM accounts WHERE entry = ? AND entry_type = 'guest'", (entry,)
            ).fetchone()
            if account is None:
                return 404, self._error("unknown_entry", "guest account is not registered")
            # GuestSignup normally creates the entry on session A, then the
            # client deliberately obtains a fresh session B before Auth.  If
            # that fresh session carries a stale binding from an earlier local
            # account, an explicit request for a registered guest entry repairs
            # the binding instead of failing every subsequent relaunch.
            if session["pending_entry"] not in (None, entry):
                if not session["device_hash"]:
                    return 404, self._error("unknown_entry", "guest entry is not registered")
                self._rebind_device(connection, session["device_hash"], account, entry)
                connection.execute(
                    """UPDATE sessions SET account_id = ?, pending_entry = ?
                       WHERE session_token = ?""",
                    (int(account["account_id"]), entry, session["session_token"]),
                )
            if session["pending_entry"] is None:
                connection.execute(
                    """UPDATE sessions SET account_id = ?, pending_entry = ?
                       WHERE session_token = ?""",
                    (int(account["account_id"]), entry, session["session_token"]),
                )
            access_token = self._token("local_access_")
            connection.execute(
                """UPDATE accounts SET access_token = ?, access_expires_at = ?
                   WHERE account_id = ?""",
                (access_token, now + self.access_ttl_seconds, int(account["account_id"])),
            )
            return 200, {
                "accountId": int(account["account_id"]),
                "openId": account["open_id"],
                "tokenAccess": access_token,
                "createTime": now,
                "expireSeconds": self.access_ttl_seconds,
            }

    def verify_access_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Return a local account record for a live access token, if any."""

        if not token:
            return None
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT * FROM accounts WHERE access_token = ?", (token,)
            ).fetchone()
            if row is None or row["access_expires_at"] is None:
                return None
            if int(row["access_expires_at"]) <= self._now():
                return None
            return {
                "accountId": int(row["account_id"]),
                "openId": row["open_id"],
                "entry": row["entry"],
                "entryType": "password" if row["login_type"] == "password" else "guest",
                "username": row["username"],
            }

    def admin_list_accounts(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        """Return redacted account metadata for the loopback admin panel.

        Password hashes, salts, access tokens and device identifiers are never
        returned.  The cap keeps the diagnostic endpoint bounded even if a
        local test session has generated many accounts.
        """

        limit = max(1, min(int(limit), 1000))
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT account_id, entry, entry_type, open_id, username,
                          login_type, created_at, access_expires_at
                   FROM accounts ORDER BY account_id LIMIT ?""",
                (limit,),
            ).fetchall()
            return [
                {
                    "accountId": int(row["account_id"]),
                    "entry": row["entry"],
                    "entryType": row["entry_type"],
                    "openId": row["open_id"],
                    "username": row["username"],
                    "loginType": row["login_type"],
                    "createdAt": int(row["created_at"]),
                    "accessExpiresAt": int(row["access_expires_at"])
                    if row["access_expires_at"] is not None else None,
                }
                for row in rows
            ]

    def admin_reset_password(self, account_id: int, new_password: str) -> bool:
        """Set a local account password and revoke old access tokens.

        This is intentionally an admin-only helper; the HTTP layer enforces
        loopback access before calling it.  It is useful for recovering a
        local sandbox account without exposing a password reset backdoor to
        the game client.
        """

        if not isinstance(new_password, str) or not 6 <= len(new_password) <= 256:
            raise ValueError("new_password must be 6-256 characters")
        password_hash, password_salt = self._password_hash(new_password)
        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """UPDATE accounts
                   SET password_hash = ?, password_salt = ?, login_type = 'password',
                       access_token = NULL, access_expires_at = NULL
                   WHERE account_id = ?""",
                (password_hash, password_salt, int(account_id)),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute("DELETE FROM sessions WHERE account_id = ?", (int(account_id),))
            return True

    def admin_revoke_sessions(self, account_id: int) -> int:
        """Revoke all active account sessions, returning deleted row count."""

        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM sessions WHERE account_id = ?", (int(account_id),))
            connection.execute(
                "UPDATE accounts SET access_token = NULL, access_expires_at = NULL WHERE account_id = ?",
                (int(account_id),),
            )
            return int(cursor.rowcount)

    def handle(
        self,
        method: str,
        path: str,
        body: Body = None,
        content_type: Optional[str] = None,
        authorization: Optional[str] = None,
    ) -> Optional[Response]:
        """Handle one local account request.

        Returns ``None`` for an unknown path.  Known routes always return a
        ``(status, JSON-dict)`` pair; callers can wrap the dict in their
        existing ``RestfulResponse.raw_json`` response envelope.
        """

        route = self._route(path)
        # Auth/<verify-token> is a read-only local inspection variant used by
        # a few client builds.  Keep the token in a separate variable so the
        # normal POST /Auth/ guest flow remains unchanged.
        auth_verify_token = None
        if route.startswith("/Auth/") and route != "/Auth/":
            auth_verify_token = route[len("/Auth/"):].strip("/")
        if route not in {
            "/AccountSession/",
            "/EntryReg/",
            "/Account/",
            "/Auth/",
            "/Password/",
            "/Password/reset/",
            "/PasswordReset/",
            "/AccountGuestBind/",
            "/AccountEntry/bind/",
            "/ExistEntry/",
            "/exist-entry/",
            "/NewEntry/",
            "/VerifyCode/",
            "/PasswordVerifyCode/",
            "/CurrentEntryModifyVerifyCode/",
            "/NewEntryModifyVerifyCode/",
            "/exist-entry-verify-code/",
            "/Captcha/",
            "/ExtraInfo/",
            "/ExtraInfoByAuth/",
            "/AccountEntry/",
        }:
            if auth_verify_token is None and not route.startswith("/AccountEntry/"):
                return None
        if auth_verify_token is not None:
            if method.upper() != "GET":
                return 405, self._error("method_not_allowed", "GET required")
            account = self.verify_access_token(auth_verify_token)
            if account is None:
                return 401, self._error("invalid_access_token", "local access token expired or unknown")
            return 200, {"auth": account, "sandbox": True}
        if route.startswith("/AccountEntry/") and route not in {"/AccountEntry/bind/"}:
            if route.endswith("/exist/") or route.count("/") >= 3:
                return self._handle_account_entry(method, route, body, content_type, authorization)
        if route == "/AccountEntry/":
            return self._handle_account_entry(method, route, body, content_type, authorization)
        if route == "/NewEntry/":
            if method.upper() != "POST":
                return 405, self._error("method_not_allowed", "POST required")
            return self._handle_new_entry(body, content_type, authorization)
        if route in {"/Captcha/", "/VerifyCode/", "/PasswordVerifyCode/",
                     "/CurrentEntryModifyVerifyCode/", "/NewEntryModifyVerifyCode/",
                     "/exist-entry-verify-code/"}:
            if method.upper() != "POST":
                return 405, self._error("method_not_allowed", "POST required")
            return self._local_verification(
                body, content_type,
                require_session=route not in {"/Captcha/", "/VerifyCode/"},
                authorization=authorization,
            )
        if route in {"/ExtraInfo/", "/ExtraInfoByAuth/"}:
            if method.upper() != "GET":
                return 405, self._error("method_not_allowed", "GET required")
            if route == "/ExtraInfoByAuth/":
                parts = (authorization or "").strip().split(None, 1)
                access_token = parts[1].strip() if len(parts) == 2 and parts[0].casefold() == "token" else ""
                account = self.verify_access_token(access_token)
                if account is None:
                    return 401, self._error("invalid_access_token", "local access token required")
                return 200, {"account": account, "sandbox": True}
            return 200, {"channel": "local", "region": "offline", "sandbox": True}
        if method.upper() != "POST":
            return 405, self._error("method_not_allowed", "POST required")
        if route == "/AccountSession/":
            return self._handle_account_session(body, content_type)
        if route in {"/EntryReg/", "/Account/"}:
            return self._handle_entry_or_account(route, body, content_type, authorization)
        if route == "/Auth/":
            return self._handle_auth(body, content_type, authorization)
        if route in {"/Password/", "/Password/reset/"}:
            if route == "/Password/reset/":
                return self._handle_password_reset(body, content_type, authorization)
            payload, parse_error = _json_object(body)
            if parse_error is not None:
                return parse_error
            assert payload is not None
            if any(key in payload for key in ("newPassword", "new_password", "oldPassword", "old_password")) or str(payload.get("action", "")).casefold() in {"change", "reset"}:
                return self._handle_password_reset(body, content_type, authorization)
            return self._handle_password_login(body, content_type, authorization)
        if route == "/PasswordReset/":
            return self._handle_password_reset(body, content_type, authorization)
        if route in {"/AccountGuestBind/", "/AccountEntry/bind/"}:
            return self._handle_guest_bind(body, content_type, authorization)
        return self._handle_exist_entry(body, content_type)


__all__ = ["LocalAccounts", "WireFormatError"]
