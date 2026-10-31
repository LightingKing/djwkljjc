"""One SQLite transaction across legacy module context managers.

Borrowed handles cannot commit or close their owner's transaction. Production
uses one database; legacy test stores attach their economy database to the same
connection for rollback consistency.
"""
from contextlib import contextmanager
from copy import deepcopy
import logging
import sqlite3
import threading

from storage.compat import configure_legacy_connection, shared_process_lock


def track_mapping(game, mapping, *keys):
    """Track only fields a caller is about to change, when a transaction exists."""
    atomic = getattr(game, '_atomic', None)
    if atomic is not None:
        atomic.track_mapping(mapping, *keys)


class BorrowedConnection:
    def __init__(self, owner):
        self.owner = owner
        self.contexts = []

    def __getattr__(self, key):
        return getattr(self.owner.connection, key)

    def __enter__(self):
        scope = self.owner.atomic._savepoint(self.owner)
        scope.__enter__()
        self.contexts.append(scope)
        return self

    def __exit__(self, kind, value, traceback):
        return self.contexts.pop().__exit__(kind, value, traceback)

    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        self.owner.failed = True


class AtomicMutations:
    def __init__(self, game):
        self.game = game
        self.local = threading.local()

    def borrowed(self):
        owner = getattr(self.local, 'owner', None)
        return None if owner is None else BorrowedConnection(owner)

    def after_commit(self, callback):
        owner = getattr(self.local, 'owner', None)
        if owner is None:
            return callback()
        owner.callbacks.append(callback)
        return 0

    def track_mapping(self, mapping, *keys):
        owner = getattr(self.local, 'owner', None)
        if owner is None or not isinstance(mapping, dict):
            return
        # A small undo journal, not a copy of the game state. Keeping successful
        # child entries lets a later parent rollback unwind them in reverse.
        snapshot = {key: deepcopy(mapping[key]) for key in keys if key in mapping}
        def restore():
            for key in keys:
                if key in snapshot:
                    mapping[key] = snapshot[key]
                else:
                    mapping.pop(key, None)
        owner.undo.append(restore)

    @staticmethod
    def _discard_scope(owner, callback_start, undo_start):
        del owner.callbacks[callback_start:]
        undo = owner.undo[undo_start:]
        del owner.undo[undo_start:]
        for restore in reversed(undo):
            restore()

    @contextmanager
    def _savepoint(self, owner):
        """One rollback boundary for explicit and legacy connection contexts."""
        owner.savepoint_counter += 1
        savepoint = f"ljjc_nested_{owner.savepoint_counter}"
        callback_start, undo_start = len(owner.callbacks), len(owner.undo)
        owner.connection.execute(f'SAVEPOINT {savepoint}')
        try:
            yield
        except BaseException:
            try:
                owner.connection.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                owner.connection.execute(f'RELEASE SAVEPOINT {savepoint}')
            except BaseException:
                # If SQLite cannot recover the child, never commit a parent
                # whose state is now uncertain even if its caller catches it.
                owner.failed = True
                raise
            finally:
                self._discard_scope(owner, callback_start, undo_start)
            raise
        else:
            try:
                owner.connection.execute(f'RELEASE SAVEPOINT {savepoint}')
            except BaseException:
                owner.failed = True
                raise

    @contextmanager
    def transaction(self):
        owner = getattr(self.local, 'owner', None)
        if owner is not None:
            with self._savepoint(owner):
                yield
            return
        game = self.game
        callbacks = []
        with game._lock, shared_process_lock(game.db_path):
            connection = sqlite3.connect(str(game.db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            configure_legacy_connection(connection)
            try:
                economy = getattr(game, 'economy', None)
                if economy is not None and economy.db_path.resolve() != game.db_path.resolve():
                    connection.execute('ATTACH DATABASE ? AS legacy_economy', (str(economy.db_path),))
                connection.execute('BEGIN IMMEDIATE')
                owner = type('Transaction', (), {})()
                owner.connection, owner.callbacks, owner.failed = connection, callbacks, False
                owner.atomic, owner.undo = self, []
                owner.savepoint_counter = 0
                self.local.owner = owner
                yield
                if owner.failed:
                    raise RuntimeError('nested mutation failed; transaction rolled back')
                connection.commit()
            except BaseException:
                connection.rollback()
                if owner is not None:
                    self._discard_scope(owner, 0, 0)
                else:
                    callbacks.clear()
                raise
            finally:
                if owner is not None:
                    owner.undo.clear()
                self.local.owner = None
                connection.close()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logging.exception('post-commit notification failed')
