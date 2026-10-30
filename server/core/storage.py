"""Versioned SQLite storage used by the staged M3 migration.

The root compatibility modules can opt into this store through the shared
``unified_store_path`` bridge.  Direct callers that omit that option keep
their historical databases.  Migrations are idempotent, WAL is enabled, and
all callers get a transaction helper with a single connection per operation.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
from typing import Iterator


CURRENT_SCHEMA_VERSION = 1


class UnifiedStore:
    """Own a versioned local SQLite database and its migrations."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        # ``executescript`` intentionally manages its own transaction in
        # sqlite3. Run migrations on an autocommit connection rather than
        # nesting it inside ``transaction()``; otherwise sqlite reports that
        # the explicit COMMIT has no active transaction.
        connection = self.connect()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)"
            )
            applied = {
                int(row[0])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            if 1 not in applied:
                _migration_1(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES(1, CAST(strftime('%s','now') AS INTEGER))"
                )
            # The root compatibility modules predate UnifiedStore and still
            # use a few account/session columns that were intentionally absent
            # from the first M3 schema.  Repair those columns in place without
            # bumping the public migration count; this keeps old stores and
            # the existing migration contract readable.
            _ensure_legacy_compat_columns(connection)
        finally:
            connection.close()

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent SQLite backup, including WAL state."""

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
        source = self.connect()
        try:
            backup = sqlite3.connect(str(target))
            try:
                source.backup(backup)
                backup.commit()
            finally:
                backup.close()
        finally:
            source.close()
        return target

    @staticmethod
    def restore_from(source: str | Path, destination: str | Path) -> Path:
        """Restore a backup atomically into a new destination path."""

        source_path = Path(source)
        destination_path = Path(destination)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination_path.with_name(destination_path.name + ".restore.tmp")
        if temporary.exists():
            temporary.unlink()
        shutil.copy2(source_path, temporary)
        connection = sqlite3.connect(str(temporary))
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                connection.close()
                temporary.unlink(missing_ok=True)
                raise ValueError("backup integrity check failed")
        finally:
            connection.close()
        temporary.replace(destination_path)
        return destination_path


def _migration_1(connection: sqlite3.Connection) -> None:
    # The columns are intentionally small and stable. Feature modules can add
    # JSON payloads without reintroducing one SQLite file per subsystem.
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            account_id INTEGER PRIMARY KEY,
            entry TEXT NOT NULL UNIQUE,
            entry_type TEXT NOT NULL,
            open_id TEXT NOT NULL UNIQUE,
            expire_seconds INTEGER NOT NULL DEFAULT 0,
            access_token TEXT UNIQUE,
            access_expires_at INTEGER,
            login_type TEXT NOT NULL DEFAULT 'guest',
            username TEXT,
            password_hash TEXT,
            password_salt TEXT,
            password_reset_token TEXT,
            password_reset_expires_at INTEGER,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_token TEXT PRIMARY KEY,
            account_id INTEGER REFERENCES accounts(account_id),
            pending_entry TEXT,
            device_hash TEXT,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS players (
            account_id INTEGER PRIMARY KEY REFERENCES accounts(account_id),
            player_id INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            exp INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS resources (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            resource_type INTEGER NOT NULL,
            resource_id INTEGER NOT NULL DEFAULT 0,
            param INTEGER NOT NULL DEFAULT 0,
            balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
            max_balance INTEGER,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, resource_type, resource_id, param)
        );
        CREATE TABLE IF NOT EXISTS inventory (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            item_type INTEGER NOT NULL,
            item_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, item_type, item_id)
        );
        CREATE TABLE IF NOT EXISTS actors (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            actor_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, actor_id)
        );
        CREATE TABLE IF NOT EXISTS equipment (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            equipment_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, equipment_id)
        );
        CREATE TABLE IF NOT EXISTS dungeon_records (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            dungeon_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, dungeon_id)
        );
        CREATE TABLE IF NOT EXISTS tasks (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            task_id TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, task_id)
        );
        CREATE TABLE IF NOT EXISTS achievements (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            achievement_id TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, achievement_id)
        );
        CREATE TABLE IF NOT EXISTS activities (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            activity_id TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, activity_id)
        );
        CREATE TABLE IF NOT EXISTS mail (
            mail_id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            payload_json TEXT NOT NULL,
            claimed_at INTEGER,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shop_purchases (
            purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            product_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gacha_records (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            pool_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payment_orders (
            order_id TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            product_id TEXT NOT NULL,
            state TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS social_relations (
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            relation_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(account_id, relation_type, target_id)
        );
        CREATE TABLE IF NOT EXISTS device_bindings (
            device_hash TEXT PRIMARY KEY,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            entry TEXT NOT NULL UNIQUE,
            created_at INTEGER NOT NULL
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
        CREATE TABLE IF NOT EXISTS ledger_entries (
            entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            operation TEXT NOT NULL CHECK(operation IN ('grant','consume')),
            resource_type INTEGER NOT NULL,
            resource_id INTEGER NOT NULL,
            param INTEGER NOT NULL,
            delta INTEGER NOT NULL,
            balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
            reason TEXT NOT NULL,
            idempotency_key TEXT,
            created_at INTEGER NOT NULL,
            UNIQUE(account_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_ledger_account_time
            ON ledger_entries(account_id, created_at, entry_id);
        """
    )


def _ensure_legacy_compat_columns(connection: sqlite3.Connection) -> None:
    """Add columns needed by ``accounts.py`` to an existing unified DB.

    SQLite cannot drop ``NOT NULL`` from an existing column.  Early M3
    databases used ``sessions.account_id NOT NULL`` even though the original
    guest flow creates an unbound session first, so rebuild that one table when
    necessary while copying every column that exists in the old schema.
    """

    account_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(accounts)")
    }
    account_additions = {
        "expire_seconds": "INTEGER NOT NULL DEFAULT 0",
        "access_token": "TEXT",
        "access_expires_at": "INTEGER",
        "login_type": "TEXT NOT NULL DEFAULT 'guest'",
        "username": "TEXT",
        "password_hash": "TEXT",
        "password_salt": "TEXT",
        "password_reset_token": "TEXT",
        "password_reset_expires_at": "INTEGER",
        "updated_at": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in account_additions.items():
        if name not in account_columns:
            connection.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username "
        "ON accounts(username) WHERE username IS NOT NULL"
    )

    session_info = {
        row[1]: row for row in connection.execute("PRAGMA table_info(sessions)")
    }
    if not session_info:
        return
    account_id_info = session_info.get("account_id")
    if account_id_info is not None and account_id_info[3]:
        # Preserve rows from the old table.  A temporary name is deterministic
        # and cleaned up before the new schema becomes visible.
        old_name = "sessions_legacy_compat"
        connection.execute(f"DROP TABLE IF EXISTS {old_name}")
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute(f"ALTER TABLE sessions RENAME TO {old_name}")
            connection.execute(
                """
                CREATE TABLE sessions (
                    session_token TEXT PRIMARY KEY,
                    account_id INTEGER REFERENCES accounts(account_id),
                    pending_entry TEXT,
                    device_hash TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )
            old_columns = {row[1] for row in connection.execute(f"PRAGMA table_info({old_name})")}
            copy_columns = [
                name for name in
                ("session_token", "account_id", "pending_entry", "device_hash", "created_at", "expires_at")
                if name in old_columns
            ]
            if copy_columns:
                quoted = ", ".join(copy_columns)
                connection.execute(
                    f"INSERT INTO sessions ({quoted}) SELECT {quoted} FROM {old_name}"
                )
            connection.execute(f"DROP TABLE {old_name}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)"
            )
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
    else:
        for name, definition in {
            "pending_entry": "TEXT",
            "device_hash": "TEXT",
        }.items():
            if name not in session_info:
                connection.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")

