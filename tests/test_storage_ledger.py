from __future__ import annotations

import sqlite3
from pathlib import Path
import tempfile
import unittest

from server.core.ledger import InsufficientResource, Ledger, ResourceKey
from server.core.storage import UnifiedStore


class StorageLedgerTests(unittest.TestCase):
    def _store(self, root: Path) -> UnifiedStore:
        store = UnifiedStore(root / "local.sqlite3")
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO accounts(account_id,entry,entry_type,open_id,created_at,updated_at) VALUES(1,'guest_test','guest','open_test',1,1)"
            )
        return store

    def test_migrations_are_idempotent_and_wal_is_enabled(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self._store(root)
            UnifiedStore(root / "local.sqlite3")
            connection = sqlite3.connect(root / "local.sqlite3")
            try:
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1)
                for table in ("accounts", "players", "resources", "ledger_entries", "payment_orders", "social_relations"):
                    self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())
            finally:
                connection.close()

    def test_grant_consume_and_idempotency(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Ledger(self._store(Path(raw)))
            key = ResourceKey(7)
            first = ledger.grant(1, key, 100, reason="starter", idempotency_key="starter-1")
            again = ledger.grant(1, key, 100, reason="starter", idempotency_key="starter-1")
            self.assertEqual(first.balance, again.balance)
            self.assertEqual(ledger.balance(1, key), 100)
            spent = ledger.consume(1, key, 30, reason="shop", idempotency_key="shop-1")
            self.assertEqual(spent.balance, 70)
            self.assertEqual(ledger.balance(1, key), 70)

    def test_insufficient_and_batch_rollback(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Ledger(self._store(Path(raw)))
            with self.assertRaises(InsufficientResource):
                ledger.consume(1, ResourceKey(7), 1, reason="too-much")
            ledger.grant(1, ResourceKey(7), 50, reason="seed")
            with self.assertRaises(InsufficientResource):
                ledger.apply_many(
                    1,
                    [{"type": 7, "count": -20}, {"type": 5, "count": -40}],
                    reason="atomic-purchase",
                )
            self.assertEqual(ledger.balance(1, ResourceKey(7)), 50)
            self.assertEqual(ledger.balance(1, ResourceKey(5)), 0)

    def test_backup_and_restore_integrity(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            store = self._store(root)
            Ledger(store).grant(1, ResourceKey(7), 12, reason="backup")
            backup = store.backup_to(root / "backups" / "snapshot.sqlite3")
            restored = UnifiedStore.restore_from(backup, root / "restored.sqlite3")
            connection = sqlite3.connect(restored)
            try:
                self.assertEqual(connection.execute("SELECT balance FROM resources WHERE account_id=1 AND resource_type=7").fetchone()[0], 12)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
