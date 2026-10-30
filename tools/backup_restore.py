"""Backup and restore utility for the unified local SQLite store."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Allow direct ``python tools/backup_restore.py`` execution from the repo.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.core.storage import UnifiedStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backup or restore a local LJJC SQLite store")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create a consistent SQLite backup")
    backup.add_argument("--database", type=Path, required=True)
    backup.add_argument("--destination", type=Path, required=True)
    restore = commands.add_parser("restore", help="integrity-check and restore a backup")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backup":
        path = UnifiedStore(args.database).backup_to(args.destination)
    else:
        path = UnifiedStore.restore_from(args.source, args.destination)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
