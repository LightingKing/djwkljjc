"""Compatibility bridge for the legacy modules' SQLite paths.

The first local-server revisions gave accounts, game state and economy three
database files.  The service now supports an explicit ``unified_store_path``
which lets the legacy modules keep their existing table names while sharing
one WAL-enabled SQLite file.  No data is copied or silently deleted: callers
that omit the option continue using their historical per-module files.

``ensure_unified_store`` also applies the versioned ``server.core`` schema
and adds the nullable legacy account/session columns required by
``accounts.py``.  This makes a freshly created ``UnifiedStore`` safe to open
through the older account implementation while preserving existing tests and
old databases.
"""

from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional

try:  # Package import when local-server is installed as a package.
    from ..server.core.storage import UnifiedStore
except ImportError:  # Direct ``python server.py`` / PYTHONPATH execution.
    from server.core.storage import UnifiedStore


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def resolve_unified_store_path(
    state_dir: str | Path,
    unified_store_path: str | Path | None = None,
) -> Optional[Path]:
    """Resolve an optional shared database path.

    ``None`` deliberately means legacy mode.  This keeps direct callers and
    existing tests isolated until they opt into the shared store.  Relative
    explicit paths are resolved against ``state_dir`` so a server config can
    use ``local.sqlite3`` without depending on the process working directory.
    """

    if unified_store_path is None:
        return None
    state = Path(state_dir)
    candidate = Path(unified_store_path).expanduser()
    if not candidate.is_absolute():
        candidate = state / candidate
    return candidate.resolve()


def shared_store_lock(path: str | Path) -> threading.RLock:
    """Return the process-wide lock associated with one SQLite path."""

    key = str(Path(path).expanduser().resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def ensure_unified_store(path: str | Path) -> Path:
    """Create/migrate the shared store and return its canonical path."""

    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    # UnifiedStore enables WAL, busy_timeout, foreign keys and migrations.
    # Its migration also calls the compatibility-column repair below for old
    # databases, so opening the same path repeatedly is idempotent.
    UnifiedStore(target)
    return target


def configure_legacy_connection(connection: sqlite3.Connection) -> None:
    """Apply the shared-store connection settings to a legacy module handle."""

    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA foreign_keys = ON")


@contextmanager
def shared_process_lock(path: str | Path):
    """Serialize read-modify-write operations across server processes.

    ``threading.RLock`` protects two ``LocalGame`` instances in one Python
    process, but it cannot protect a second worker process that opens the
    same SQLite file.  SQLite's WAL mode handles short transactions well, yet
    the social and PVP JSON state handlers perform a read followed by an
    update.  A tiny sidecar lock closes that lost-update window without
    changing the database schema.  The lock is advisory and local to this
    machine; it never creates a network or external-account dependency.
    """

    lock_path = Path(path).expanduser().resolve().with_name(
        Path(path).name + ".process.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


__all__ = [
    "configure_legacy_connection",
    "ensure_unified_store",
    "resolve_unified_store_path",
    "shared_process_lock",
    "shared_store_lock",
]
