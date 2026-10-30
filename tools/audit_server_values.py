#!/usr/bin/env python3
"""Audit provenance metadata for values used only by the local server.

The retired production service is unavailable, so this tool makes the
confidence boundary explicit.  It inherits a file-level ``provenance`` block
for leaf values and reports every value without inventing a source or calling
local policy an official value.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


_META_KEYS = {"schemaVersion", "provenance", "generated", "confidence", "date", "asOf"}
_CONFIDENCE = {"A", "B", "C", "D"}


def _provenance(value: object, inherited: Mapping[str, Any]) -> dict[str, Any]:
    current = dict(inherited)
    if isinstance(value, Mapping):
        candidate = value.get("provenance")
        if isinstance(candidate, Mapping):
            current.update(candidate)
        elif isinstance(candidate, str):
            current["source"] = candidate
        if candidate is not None:
            for key in ("confidence", "date", "asOf", "generated"):
                if key in value:
                    current[key] = value[key]
    return current


def _walk(
    value: object,
    path: str,
    inherited: Mapping[str, Any],
    output: list[dict[str, Any]],
) -> None:
    if isinstance(value, Mapping):
        current = _provenance(value, inherited)
        for key in sorted(value, key=str):
            key_text = str(key)
            if not path and key_text in _META_KEYS:
                continue
            child_path = f"{path}.{key_text}" if path else key_text
            _walk(value[key], child_path, current, output)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", inherited, output)
        return

    source = inherited.get("source")
    confidence = inherited.get("confidence")
    date = inherited.get("date") or inherited.get("asOf") or inherited.get("generated")
    if isinstance(date, (_datetime.date, _datetime.datetime)):
        date = date.isoformat()
    status = "documented"
    if not source or confidence not in _CONFIDENCE:
        status = "needs-source"
    output.append({
        "path": path,
        "value": value,
        "source": source,
        "date": date,
        "confidence": confidence,
        "status": status,
        "localPolicy": confidence == "D",
    })


def audit(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            files.append({"file": path.name, "status": "invalid"})
            continue
        file_entries: list[dict[str, Any]] = []
        _walk(raw, "", {}, file_entries)
        for item in file_entries:
            item["file"] = path.name
        entries.extend(file_entries)
        files.append({
            "file": path.name,
            "status": "ok" if all(item["status"] == "documented" for item in file_entries) else "needs-source",
            "entries": len(file_entries),
        })
    by_confidence = {confidence: sum(item["confidence"] == confidence for item in entries) for confidence in sorted(_CONFIDENCE)}
    return {
        "schemaVersion": 1,
        "root": str(root),
        "files": files,
        "summary": {
            "files": len(files),
            "entries": len(entries),
            "documented": sum(item["status"] == "documented" for item in entries),
            "needsSource": sum(item["status"] == "needs-source" for item in entries),
            "localPolicy": sum(item["localPolicy"] for item in entries),
            "byConfidence": by_confidence,
        },
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/server-only"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"value directory does not exist: {args.root}")
    result = audit(args.root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
