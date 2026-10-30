from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile, ZIP_STORED

from dev.m8_acceptance import inspect_archive
from server.core.activity_schedule import ActivitySchedule


class M8AcceptanceTests(unittest.TestCase):
    def _archive(self, root: Path, *, sensitive: bool = False) -> Path:
        source = root / "source.txt"
        source.write_text("stable", encoding="utf-8")
        archive = root / "bundle.zip"
        import hashlib

        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        files = [{"path": "source.txt", "sha256": digest, "bytes": source.stat().st_size}]
        if sensitive:
            files.append({"path": "runtime/state.sqlite3", "sha256": digest, "bytes": source.stat().st_size})
        manifest = {"format": 1, "files": files}
        with ZipFile(archive, "w", ZIP_STORED) as bundle:
            bundle.writestr("source.txt", source.read_bytes())
            if sensitive:
                bundle.writestr("runtime/state.sqlite3", source.read_bytes())
            bundle.writestr("DELIVERY-MANIFEST.json", json.dumps(manifest))
        return archive

    def test_archive_manifest_and_exclusions_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = inspect_archive(self._archive(Path(raw)))
            self.assertTrue(result["manifestValid"])
            self.assertEqual(result["sensitiveEntries"], [])
            self.assertTrue(result["passed"])

    def test_archive_rejects_runtime_state_even_with_a_valid_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            result = inspect_archive(self._archive(Path(raw), sensitive=True))
            self.assertFalse(result["passed"])
            self.assertIn("runtime/state.sqlite3", result["sensitiveEntries"])

    def test_activity_schedule_is_deterministic_for_all_28_days(self) -> None:
        source = Path(__file__).resolve().parents[1] / "data" / "server-only" / "activity-schedule.yaml"
        schedule = ActivitySchedule(source, clock=lambda: 0)
        anchor = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        first = [schedule.snapshot(anchor + timedelta(days=day))["cycleDay"] for day in range(28)]
        second = [schedule.snapshot(anchor + timedelta(days=day))["cycleDay"] for day in range(28)]
        self.assertEqual(first, list(range(28)))
        self.assertEqual(first, second)
        self.assertEqual(
            {item["id"] for item in schedule.snapshot(anchor + timedelta(days=7))["active"]},
            {"shop", "sandbox-gacha"},
        )
        self.assertEqual(
            {item["id"] for item in schedule.snapshot(anchor + timedelta(days=21))["active"]},
            {"shop"},
        )


if __name__ == "__main__":
    unittest.main()
