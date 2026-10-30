#!/usr/bin/env python3
"""Audit and optionally batch-decompile recovered Lua inputs.

The APK contains two different byte layouts that are both commonly called
``FCI`` in old notes:

* ``00 66 63 69 01`` is the runtime dump layout used by the verified
  opcode-map/unluac converter;
* ``46 43 49 00`` (``FCI\\0``) is the protected asset payload.  Its key and
  decode step are not recovered by this repository, so it must never be sent
  to a decompiler or treated as source.

This tool is deliberately an evidence collector.  It never modifies an
input, executes Lua, or invents a decompiled placeholder.  If a converter is
provided, it is invoked only for the runtime-dump layout and every result is
recorded in a JSON manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


RUNTIME_MAGIC = bytes.fromhex("0066636901")
ASSET_MAGIC = b"FCI\x00"
DEFAULT_SUFFIXES = {".luac", ".luap", ".fci", ".txt", ".bytes"}


def _digest(path: Path) -> tuple[int, str, bytes]:
    digest = hashlib.sha256()
    prefix = b""
    size = 0
    with path.open("rb") as stream:
        prefix = stream.read(16)
        size = len(prefix)
        digest.update(prefix)
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest(), prefix


def classify_prefix(prefix: bytes) -> str:
    """Return a conservative layout label for an input prefix."""

    if prefix.startswith(RUNTIME_MAGIC):
        return "runtime_fci"
    if prefix.startswith(ASSET_MAGIC):
        return "protected_asset_fci"
    return "unknown"


def _iter_files(root: Path, suffixes: set[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file() and (not suffixes or path.suffix.lower() in suffixes):
            yield path


def load_inventory(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("inventory must contain an entries list")
    result = []
    for entry in entries:
        item = entry.get("path") if isinstance(entry, dict) else entry
        if isinstance(item, str) and "/data/lua/" in item:
            result.append(item)
    return sorted(set(result))


def audit_tree(
    source_root: Path,
    output_root: Path,
    *,
    converter: Path | None = None,
    suffixes: set[str] | None = None,
) -> dict[str, Any]:
    """Audit all candidate files below *source_root*.

    ``converter`` must be a Python executable script compatible with the
    existing single-file converter (``input output``).  The invocation uses a
    list of arguments and does not go through a shell.
    """

    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source directory does not exist: {source_root}")
    suffixes = DEFAULT_SUFFIXES if suffixes is None else suffixes
    output_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for source in _iter_files(source_root, suffixes):
        size, sha256, prefix = _digest(source)
        layout = classify_prefix(prefix)
        row: dict[str, Any] = {
            "source": source.relative_to(source_root).as_posix(),
            "absoluteSource": str(source),
            "sizeBytes": size,
            "sha256": sha256,
            "layout": layout,
        }
        if layout == "protected_asset_fci":
            row.update(status="blocked_protected_payload", reason=(
                "FCI asset encryption/decode key is not recovered; refusing to "
                "pass protected bytes to a decompiler"
            ))
        elif layout == "runtime_fci" and converter is None:
            row.update(status="ready_no_converter", reason=(
                "runtime FCI layout is recognized, but no verified converter was supplied"
            ))
        elif layout == "runtime_fci":
            target = output_root / Path(row["source"])
            target = target.with_suffix(".lua")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.resolve() == source:
                raise ValueError(f"refusing to overwrite input: {source}")
            command = [sys.executable, str(converter.resolve()), str(source), str(target)]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)
            row["command"] = command
            row["converterReturnCode"] = completed.returncode
            if completed.stdout:
                row["stdout"] = completed.stdout[-4000:]
            if completed.stderr:
                row["stderr"] = completed.stderr[-4000:]
            if completed.returncode == 0 and target.is_file():
                row.update(status="decompiled", output=str(target))
            else:
                target.unlink(missing_ok=True)
                row.update(status="decompile_failed", reason="converter returned failure or no output")
        else:
            row.update(status="ignored_unknown", reason="prefix is not a recognized FCI layout")
        rows.append(row)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "schemaVersion": 1,
        "tool": "audit_lua_decompilation.py",
        "sourceRoot": str(source_root),
        "outputRoot": str(output_root),
        "converter": str(converter.resolve()) if converter else None,
        "candidateCount": len(rows),
        "counts": counts,
        "files": rows,
        "limitations": [
            "Protected FCI asset payloads are listed but not decrypted.",
            "A successful decompiler invocation proves only conversion output, not runtime equivalence.",
            "The tool never executes Lua or modifies source inputs.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--converter", type=Path, help="verified single-file converter script")
    parser.add_argument(
        "--suffix",
        action="append",
        dest="suffixes",
        help="candidate extension (repeatable; defaults to .luac/.luap/.fci/.txt/.bytes)",
    )
    parser.add_argument("--inventory", type=Path, help="optional APK inventory to record expected module count")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    suffixes = {s.lower() if s.startswith(".") else f".{s.lower()}" for s in args.suffixes} if args.suffixes else None
    report = audit_tree(args.source_root, args.output_root, converter=args.converter, suffixes=suffixes)
    if args.inventory:
        expected = load_inventory(args.inventory)
        report["inventory"] = {
            "path": str(args.inventory.resolve()),
            "expectedLuaModuleCount": len(expected),
            "expectedPaths": expected,
        }
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output_report), "candidateCount": report["candidateCount"], "counts": report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
