#!/usr/bin/env python3
"""Compare two extracted configuration directories without guessing values.

The historical client exports are often partial or contain an explicit Lua
error.  This verifier treats those states as evidence and reports them as
``inconclusive``; it never promotes a partial table to a complete one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _digest_rows(value: dict[str, Any]) -> str | None:
    rows = value.get("rows")
    if not isinstance(rows, list):
        return None
    body = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _table_files(root: Path) -> dict[str, Path]:
    return {path.stem: path for path in root.glob("*.json") if path.name != "index.json"}


def verify(expected_root: Path, actual_root: Path) -> dict[str, Any]:
    expected_files = _table_files(expected_root)
    actual_files = _table_files(actual_root)
    names = sorted(set(expected_files) | set(actual_files))
    rows: list[dict[str, Any]] = []
    for name in names:
        if name not in expected_files:
            rows.append({"table": name, "status": "missing_expected"})
            continue
        if name not in actual_files:
            rows.append({"table": name, "status": "missing_actual"})
            continue
        expected = _load(expected_files[name])
        actual = _load(actual_files[name])
        expected_state = str(expected.get("completeness", "unknown"))
        actual_state = str(actual.get("completeness", "unknown"))
        expected_digest = _digest_rows(expected)
        actual_digest = _digest_rows(actual)
        comparable = expected_state == "complete_observed" and actual_state == "complete_observed" and expected_digest is not None and actual_digest is not None
        if not comparable:
            status = "inconclusive"
        elif expected_digest == actual_digest and expected.get("schema") == actual.get("schema"):
            status = "match"
        else:
            status = "different"
        rows.append({
            "table": name,
            "status": status,
            "expectedCompleteness": expected_state,
            "actualCompleteness": actual_state,
            "expectedRowCount": expected.get("rowCount"),
            "actualRowCount": actual.get("rowCount"),
            "expectedObservedCount": expected.get("observedCount"),
            "actualObservedCount": actual.get("observedCount"),
            "expectedRowsSha256": expected_digest,
            "actualRowsSha256": actual_digest,
            "schemaEqual": expected.get("schema") == actual.get("schema"),
        })
    counts = {status: sum(1 for row in rows if row.get("status") == status) for status in {
        "match", "different", "missing_expected", "missing_actual", "inconclusive"
    }}
    return {
        "schemaVersion": 1,
        "expectedRoot": str(expected_root),
        "actualRoot": str(actual_root),
        "tableCount": len(rows),
        "counts": counts,
        "verified": counts["different"] == 0 and counts["missing_expected"] == 0 and counts["missing_actual"] == 0 and counts["inconclusive"] == 0,
        "tables": rows,
        "notes": [
            "Only complete_observed tables with serialized rows are compared as verified.",
            "Partial or error tables remain inconclusive and require a stronger source.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected", type=Path)
    parser.add_argument("actual", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.expected, args.actual):
        if not path.is_dir():
            raise SystemExit(f"missing configuration directory: {path}")
    result = verify(args.expected.resolve(), args.actual.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"tableCount": result["tableCount"], "counts": result["counts"], "verified": result["verified"]}, ensure_ascii=False))
    return 0 if result["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["verify"]
