"""Safe local backups for the offline server state.

The running service owns a few SQLite files while the storage migration is
still staged.  This module backs up each database through SQLite's online
backup API, so WAL pages are included and the source file is never copied
while it is being written.  It deliberately accepts only paths selected by
the caller; the admin surface never exposes arbitrary filesystem paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Iterable


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def backup_sqlite(source: str | Path, destination: str | Path) -> Path:
    """Create a consistent backup of one SQLite database."""

    source_path = Path(source)
    target = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() == target.resolve():
        raise ValueError("backup destination must differ from the live database")
    temporary = target.with_name(target.name + ".tmp")
    temporary.unlink(missing_ok=True)
    source_connection = sqlite3.connect(str(source_path), timeout=10)
    try:
        target_connection = sqlite3.connect(str(temporary), timeout=10)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    temporary.replace(target)
    return target


def restore_sqlite(source: str | Path, destination: str | Path) -> Path:
    """Restore one backup after an integrity check, replacing the destination."""

    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(destination_path.name + ".restore.tmp")
    temporary.unlink(missing_ok=True)
    source_connection = sqlite3.connect(str(source_path), timeout=10)
    try:
        check = source_connection.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise ValueError("backup integrity check failed")
        target_connection = sqlite3.connect(str(temporary), timeout=10)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            target_connection.close()
    finally:
        source_connection.close()
    temporary.replace(destination_path)
    return destination_path


def backup_state(
    state_dir: str | Path,
    destination_root: str | Path,
    *,
    names: Iterable[str] = ("accounts.sqlite3", "game.sqlite3", "economy.sqlite3", "local.sqlite3"),
    timestamp: str | None = None,
) -> Path:
    """Back up every present state database into one timestamped directory."""

    state = Path(state_dir)
    root = Path(destination_root)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", stamp):
        raise ValueError("timestamp must use YYYYMMDDTHHMMSSZ")
    target_root = root / stamp
    target_root.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    try:
        for name in names:
            if not _SAFE_NAME.fullmatch(str(name)) or Path(name).name != name:
                raise ValueError(f"invalid database name: {name!r}")
            source = state / name
            if source.is_file():
                backup_sqlite(source, target_root / name)
                copied.append(name)
        (target_root / "manifest.txt").write_text(
            "created_at=" + stamp + "\n" + "databases=" + ",".join(copied) + "\n",
            encoding="utf-8",
        )
        return target_root
    except BaseException:
        for path in target_root.glob("*"):
            path.unlink(missing_ok=True)
        target_root.rmdir()
        raise


def list_backups(destination_root: str | Path) -> list[Path]:
    root = Path(destination_root)
    if not root.is_dir():
        return []
    return sorted((path for path in root.iterdir() if path.is_dir() and re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", path.name)), reverse=True)


def prune_backups(destination_root: str | Path, keep: int) -> list[Path]:
    """Keep the newest ``keep`` backup directories and remove older ones."""

    if keep < 1:
        raise ValueError("keep must be positive")
    backups = list_backups(destination_root)
    removed = backups[keep:]
    for directory in removed:
        for path in directory.glob("*"):
            path.unlink(missing_ok=True)
        directory.rmdir()
    return removed


class DailyBackupScheduler:
    """Create at most one state backup for each server-clock date.

    The scheduler is deliberately small and daemon based.  It does not own
    the HTTP server lifecycle and it never mutates the live database directly;
    ``backup_sqlite`` uses SQLite's online backup API for each file.  A caller
    can invoke :meth:`run_once` synchronously in tests or start the periodic
    worker with :meth:`start` in the service process.
    """

    def __init__(
        self,
        state_dir: str | Path,
        destination_root: str | Path,
        *,
        retention: int = 7,
        interval_seconds: float = 60.0,
        clock=None,
        lock: threading.Lock | threading.RLock | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).resolve()
        self.destination_root = Path(destination_root).resolve()
        self.retention = max(1, int(retention))
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.clock = clock or time.time
        self.lock = lock or threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.RLock()
        self._last_run: str | None = None
        self._last_error: str | None = None

    def _now(self) -> datetime:
        value = self.clock()
        if isinstance(value, datetime):
            current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return current.astimezone(timezone.utc)
        return datetime.fromtimestamp(float(value), timezone.utc)

    @property
    def last_run(self) -> str | None:
        with self._state_lock:
            return self._last_run

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    def run_once(self, *, force: bool = False) -> Path | None:
        """Back up today's state, returning the directory when created.

        Existing backups for the same UTC day are treated as successful and
        skipped.  ``force=True`` is useful for an operator or a deterministic
        test and still uses a collision-safe timestamp.
        """

        current = self._now()
        day = current.strftime("%Y%m%d")
        with self.lock:
            existing = [item for item in list_backups(self.destination_root) if item.name.startswith(day)]
            if existing and not force:
                with self._state_lock:
                    self._last_run = existing[0].name
                    self._last_error = None
                return None
            stamp = current.strftime("%Y%m%dT%H%M%SZ")
            if (self.destination_root / stamp).exists():
                # A same-second backup can be produced by an explicit admin
                # action.  Let the next scheduler tick try again rather than
                # deleting or replacing an operator-created backup.
                with self._state_lock:
                    self._last_run = stamp
                    self._last_error = "backup_already_exists"
                return None
            try:
                created = backup_state(self.state_dir, self.destination_root, timestamp=stamp)
                prune_backups(self.destination_root, self.retention)
            except FileExistsError:
                with self._state_lock:
                    self._last_run = stamp
                    self._last_error = "backup_already_exists"
                return None
            except (OSError, ValueError) as exc:
                with self._state_lock:
                    self._last_error = type(exc).__name__ + ": " + str(exc)
                return None
            with self._state_lock:
                self._last_run = created.name
                self._last_error = None
            return created

    def _worker(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.run_once()

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._worker,
                name="daily-state-backup",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout)))
        with self._state_lock:
            self._thread = None


__all__ = [
    "DailyBackupScheduler",
    "backup_sqlite",
    "restore_sqlite",
    "backup_state",
    "list_backups",
    "prune_backups",
]
