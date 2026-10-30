#!/usr/bin/env python3
"""Build the versioned API contract table from extracted interface metadata."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

import yaml


def _normalise(interface: dict[str, Any], source_path: Path) -> dict[str, Any]:
    route = str(interface.get("route") or "")
    request_fields = interface.get("request_fields")
    nested = interface.get("nested_request_result_modes")
    return {
        "interface": interface.get("interface"),
        "file": interface.get("file"),
        "view": interface.get("view"),
        "route": route,
        "description": interface.get("description"),
        "requestFields": request_fields if isinstance(request_fields, list) else [],
        "responseFields": nested if isinstance(nested, dict) else {},
        "implementationStatus": "observed_unverified",
        "source": str(source_path),
    }


def build(source: Path) -> dict[str, Any]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    interfaces = raw.get("interfaces") if isinstance(raw, dict) else None
    if not isinstance(interfaces, list):
        raise ValueError("interface-map.json must contain an interfaces list")
    entries = [_normalise(item, source) for item in interfaces if isinstance(item, dict)]
    by_file = Counter(str(item.get("file") or "unknown") for item in entries)
    return {
        "schemaVersion": 1,
        "source": str(source),
        "statusLegend": {
            "observed_unverified": "Extracted from client descriptors; server implementation still needs contract tests.",
            "implemented": "Covered by a local route test and response-shape assertion.",
            "fixture": "Served from a captured fixture without local business logic.",
            "unimplemented": "Known client interface with no local route yet.",
        },
        "summary": {
            "interfaceCount": len(entries),
            "uniqueRoutes": len({item["route"] for item in entries}),
            "byFile": dict(sorted(by_file.items())),
        },
        "interfaces": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"source does not exist: {args.source}")
    document = build(args.source.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"wrote {document['summary']['interfaceCount']} interfaces to {args.output}")


if __name__ == "__main__":
    main()
