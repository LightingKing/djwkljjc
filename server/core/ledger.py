"""Transactional resource ledger built on :mod:`server.core.storage`."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time
from typing import Iterable, Mapping

from .storage import UnifiedStore


@dataclass(frozen=True)
class ResourceKey:
    resource_type: int
    resource_id: int = 0
    param: int = 0


@dataclass(frozen=True)
class LedgerChange:
    account_id: int
    key: ResourceKey
    delta: int
    balance: int
    operation: str
    reason: str
    entry_id: int | None = None


class InsufficientResource(ValueError):
    """Raised when a consume operation would make a balance negative."""


class Ledger:
    def __init__(self, store: UnifiedStore, *, clock=None):
        self.store = store
        self._clock = clock or (lambda: int(time.time()))

    def balance(self, account_id: int, key: ResourceKey) -> int:
        connection = self.store.connect()
        try:
            row = connection.execute(
                "SELECT balance FROM resources WHERE account_id=? AND resource_type=? AND resource_id=? AND param=?",
                (account_id, key.resource_type, key.resource_id, key.param),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()

    def grant(
        self,
        account_id: int,
        key: ResourceKey,
        amount: int,
        *,
        reason: str,
        idempotency_key: str | None = None,
        max_balance: int | None = None,
    ) -> LedgerChange:
        if amount < 0:
            raise ValueError("grant amount must be non-negative")
        return self._apply(account_id, key, amount, "grant", reason, idempotency_key, max_balance)

    def consume(
        self,
        account_id: int,
        key: ResourceKey,
        amount: int,
        *,
        reason: str,
        idempotency_key: str | None = None,
    ) -> LedgerChange:
        if amount < 0:
            raise ValueError("consume amount must be non-negative")
        return self._apply(account_id, key, -amount, "consume", reason, idempotency_key, None)

    def apply_many(
        self,
        account_id: int,
        changes: Iterable[Mapping[str, object]],
        *,
        reason: str,
        idempotency_prefix: str | None = None,
    ) -> list[LedgerChange]:
        """Apply a batch atomically; any insufficient item rolls back all."""
        result: list[LedgerChange] = []
        with self.store.transaction() as connection:
            for index, change in enumerate(changes):
                key = ResourceKey(int(change["type"]), int(change.get("id", 0)), int(change.get("param", 0)))
                delta = int(change["count"])
                operation = "grant" if delta >= 0 else "consume"
                idem = f"{idempotency_prefix}:{index}" if idempotency_prefix else None
                result.append(self._apply_in_connection(connection, account_id, key, delta, operation, reason, idem, None))
        return result

    def _apply(self, account_id, key, delta, operation, reason, idempotency_key, max_balance):
        with self.store.transaction() as connection:
            return self._apply_in_connection(connection, account_id, key, delta, operation, reason, idempotency_key, max_balance)

    def _apply_in_connection(self, connection, account_id, key, delta, operation, reason, idempotency_key, max_balance):
        if idempotency_key:
            old = connection.execute(
                "SELECT entry_id, delta, balance_after, operation, reason FROM ledger_entries WHERE account_id=? AND idempotency_key=?",
                (account_id, idempotency_key),
            ).fetchone()
            if old:
                return LedgerChange(account_id, key, int(old[1]), int(old[2]), str(old[3]), str(old[4]), int(old[0]))
        row = connection.execute(
            "SELECT balance, max_balance FROM resources WHERE account_id=? AND resource_type=? AND resource_id=? AND param=?",
            (account_id, key.resource_type, key.resource_id, key.param),
        ).fetchone()
        before = int(row[0]) if row else 0
        configured_max = max_balance if max_balance is not None else (int(row[1]) if row and row[1] is not None else None)
        after = before + delta
        if after < 0:
            raise InsufficientResource(f"resource {key} balance {before} < {-delta}")
        if configured_max is not None:
            after = min(after, configured_max)
            delta = after - before
        now = int(self._clock())
        connection.execute(
            "INSERT INTO resources(account_id,resource_type,resource_id,param,balance,max_balance,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id,resource_type,resource_id,param) DO UPDATE SET balance=excluded.balance,max_balance=excluded.max_balance,updated_at=excluded.updated_at",
            (account_id, key.resource_type, key.resource_id, key.param, after, configured_max, now),
        )
        cursor = connection.execute(
            "INSERT INTO ledger_entries(account_id,operation,resource_type,resource_id,param,delta,balance_after,reason,idempotency_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (account_id, operation, key.resource_type, key.resource_id, key.param, delta, after, reason, idempotency_key, now),
        )
        return LedgerChange(account_id, key, delta, after, operation, reason, int(cursor.lastrowid))

