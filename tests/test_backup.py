from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from server.core.backup import DailyBackupScheduler, backup_sqlite, backup_state, list_backups, prune_backups


class BackupTests(unittest.TestCase):
    def test_online_backup_includes_wal_committed_rows(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "state.sqlite3"
            connection = sqlite3.connect(source)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE values_table(value TEXT)")
            connection.execute("INSERT INTO values_table VALUES ('kept')")
            connection.commit()
            target = backup_sqlite(source, root / "backup.sqlite3")
            connection.close()
            restored = sqlite3.connect(target)
            try:
                self.assertEqual(restored.execute("SELECT value FROM values_table").fetchone()[0], "kept")
            finally:
                restored.close()

    def test_state_backup_skips_missing_databases_and_prunes_old_sets(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "state"
            state.mkdir()
            connection = sqlite3.connect(state / "game.sqlite3")
            connection.execute("CREATE TABLE t(v INTEGER)")
            connection.commit()
            connection.close()
            backups = root / "backups"
            first = backup_state(state, backups, timestamp="20261028T010101Z")
            second = backup_state(state, backups, timestamp="20261028T010102Z")
            self.assertEqual(list_backups(backups), [second, first])
            self.assertTrue((first / "game.sqlite3").is_file())
            self.assertFalse((first / "accounts.sqlite3").exists())
            removed = prune_backups(backups, 1)
            self.assertEqual(removed, [first])
            self.assertEqual(list_backups(backups), [second])

    def test_daily_scheduler_is_idempotent_per_day_and_supports_clock_offset(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "state"
            state.mkdir()
            sqlite3.connect(state / "local.sqlite3").close()
            now = [1_700_000_000]
            scheduler = DailyBackupScheduler(state, root / "backups", clock=lambda: now[0], retention=3)
            first = scheduler.run_once()
            self.assertIsNotNone(first)
            self.assertIsNone(scheduler.run_once())
            self.assertEqual(len(list_backups(root / "backups")), 1)
            now[0] += 86400
            second = scheduler.run_once()
            self.assertIsNotNone(second)
            self.assertEqual(len(list_backups(root / "backups")), 2)


if __name__ == "__main__":
    unittest.main()
