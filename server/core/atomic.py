"""One SQLite transaction across legacy module context managers.

Borrowed handles cannot commit or close their owner's transaction. Production
uses one database; legacy test stores attach their economy database to the same
connection for rollback consistency.
"""
from contextlib import contextmanager
import logging
import sqlite3
import threading

from storage.compat import configure_legacy_connection, shared_process_lock


class BorrowedConnection:
    def __init__(self, owner):
        self.owner = owner

    def __getattr__(self, key):
        return getattr(self.owner.connection, key)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is not None:
            self.owner.failed = True
        return False

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

    @contextmanager
    def transaction(self):
        owner = getattr(self.local, 'owner', None)
        if owner is not None:
            # Nested mutations share the outer connection.  A savepoint keeps
            # a failed child operation from poisoning the whole request when
            # the caller deliberately catches that exception and continues.
            # Explicit ``borrowed().rollback()`` still marks the owner failed
            # because it represents a request-wide rollback decision.
            owner.savepoint_counter += 1
            savepoint = f"ljjc_nested_{owner.savepoint_counter}"
            owner.connection.execute(f'SAVEPOINT {savepoint}')
            try:
                yield
            except BaseException:
                owner.connection.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                owner.connection.execute(f'RELEASE SAVEPOINT {savepoint}')
                raise
            else:
                owner.connection.execute(f'RELEASE SAVEPOINT {savepoint}')
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
                owner.savepoint_counter = 0
                self.local.owner = owner
                yield
                if owner.failed:
                    raise RuntimeError('nested mutation failed; transaction rolled back')
                connection.commit()
            except BaseException:
                connection.rollback()
                callbacks.clear()
                raise
            finally:
                self.local.owner = None
                connection.close()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logging.exception('post-commit notification failed')
