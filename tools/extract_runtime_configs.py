#!/usr/bin/env python3
"""Normalize captured runtime config responses into deterministic JSON tables."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any


def _safe_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return value or "unnamed"


def _iter_payloads(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        whole = json.loads(text)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, list):
        # A clean export may be a JSON array of payload objects rather than
        # JSONL.  Treat each object as an independent observation.
        for index, item in enumerate(whole, 1):
            if isinstance(item, dict):
                yield index, item
        return
    if isinstance(whole, dict):
        values = ((whole.get("payload") or {}).get("values") if isinstance(whole.get("payload"), dict) else None)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    try:
                        payload = json.loads(value)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        yield 1, payload
            return
        yield 1, whole
        return
    for line_number, line in enumerate(text.splitlines(), 1):
        try:
            outer = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(outer, dict):
            continue
        values = ((outer.get("payload") or {}).get("values") if isinstance(outer.get("payload"), dict) else None)
        if not isinstance(values, list):
            # Keep ordinary JSONL records too; _iter_tables will ignore
            # records which do not contain a table-shaped ``rows`` list.
            yield line_number, outer
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield line_number, payload


def _iter_tables(node: object, name_hint: str | None = None):
    """Find table-shaped dictionaries in both live and offline exports."""
    if not isinstance(node, dict):
        return
    rows = node.get("rows")
    if isinstance(rows, list):
        # Root exports sometimes put the table directly at the top level.  A
        # stable fallback keeps that evidence instead of silently dropping it.
        table_name = name_hint or str(node.get("table") or node.get("name") or "root")
        yield table_name, node
    for key, value in node.items():
        if isinstance(value, dict):
            yield from _iter_tables(value, str(key))


def _merge_schema(current: dict[str, Any] | None, value: object) -> dict[str, Any]:
    """Infer a small, deterministic JSON schema from observed rows.

    The runtime exports are data samples rather than protobuf schemas.  This
    deliberately records only facts visible in those samples: scalar kinds,
    object fields and array item shapes.  Conflicting observations are kept as
    a sorted ``types`` list instead of coercing values or guessing a canonical
    type.
    """
    if value is None:
        observed = {"null"}
    elif isinstance(value, bool):
        observed = {"boolean"}
    elif isinstance(value, int) and not isinstance(value, bool):
        observed = {"integer"}
    elif isinstance(value, float):
        observed = {"number"}
    elif isinstance(value, str):
        observed = {"string"}
    elif isinstance(value, list):
        item_schema: dict[str, Any] | None = None
        for item in value:
            item_schema = _merge_schema(item_schema, item)
        result: dict[str, Any] = {"types": ["array"]}
        if item_schema is not None:
            result["items"] = item_schema
        return _merge_schema_nodes(current, result)
    elif isinstance(value, dict):
        fields: dict[str, Any] = {}
        for key in sorted(value):
            fields[str(key)] = _merge_schema(None, value[key])
        result = {"types": ["object"], "fields": fields}
        return _merge_schema_nodes(current, result)
    else:
        observed = {type(value).__name__}

    if current is None:
        return {"types": sorted(observed)}
    merged = set(current.get("types", [])) | observed
    result = dict(current)
    result["types"] = sorted(merged)
    return result


def _merge_schema_nodes(current: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge two inferred schema nodes while retaining nested fields."""
    if current is None:
        return incoming
    result = dict(current)
    result["types"] = sorted(set(current.get("types", [])) | set(incoming.get("types", [])))
    if "fields" in incoming:
        fields = dict(current.get("fields", {}))
        for key, node in incoming["fields"].items():
            fields[key] = _merge_schema_nodes(fields.get(key), node)
        result["fields"] = {key: fields[key] for key in sorted(fields)}
    if "items" in incoming:
        result["items"] = _merge_schema_nodes(current.get("items"), incoming["items"])
    return result


def infer_schema(rows: list[object]) -> dict[str, Any]:
    """Return a stable schema summary for a table's observed rows."""
    schema: dict[str, Any] | None = None
    for row in rows:
        schema = _merge_schema_nodes(schema, _merge_schema(None, row))
    return schema or {"types": ["unknown"]}


def _completeness(selected: dict[str, Any]) -> str:
    if selected.get("error") and not selected.get("rows"):
        return "error"
    observed = int(selected.get("count", 0) or 0)
    rows = len(selected.get("rows") or [])
    if observed > rows:
        return "partial"
    if observed == rows and observed > 0:
        return "complete_observed"
    return "empty"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_rows(rows: object) -> str | None:
    if not isinstance(rows, list):
        return None
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def extract(inputs: list[Path]) -> dict[str, Any]:
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_files: dict[str, set[str]] = defaultdict(set)
    for path in inputs:
        for _line_number, payload in _iter_payloads(path):
            for table_name, table in _iter_tables(payload):
                rows = table.get("rows")
                candidate = {
                    "table": table_name,
                    "count": int(table.get("count", len(rows)) or 0),
                    "rows": rows,
                    "error": table.get("error"),
                    "source": path.name,
                }
                candidates[table_name].append(candidate)
                source_files[table_name].add(path.name)

    tables: dict[str, dict[str, Any]] = {}
    for table_name in sorted(candidates):
        options = candidates[table_name]
        # Prefer the response with the most rows; retain all source names for
        # provenance and keep error-only observations visible in the index.
        selected = max(options, key=lambda item: (len(item["rows"]), item["count"], item["source"]))
        tables[table_name] = {
            "schemaVersion": 1,
            "table": table_name,
            "observedCount": int(selected["count"]),
            "rowCount": len(selected["rows"]),
            "completeness": _completeness(selected),
            "sourceFiles": sorted(source_files[table_name]),
            "sourceRecords": [
                {
                    "name": path.name,
                    "path": str(path),
                    "sha256": _sha256_bytes(path.read_bytes()),
                }
                for path in sorted(inputs, key=lambda item: (item.name, str(item)))
                if path.name in source_files[table_name]
            ],
            "rowsSha256": _sha256_rows(selected["rows"]),
            "schema": infer_schema(selected["rows"]),
            "rows": selected["rows"],
        }
        if selected.get("error"):
            tables[table_name]["selectedError"] = selected["error"]
    return {
        "schemaVersion": 1,
        "sources": [str(path) for path in inputs],
        "sourceRecords": [
            {
                "name": path.name,
                "path": str(path),
                "sha256": _sha256_bytes(path.read_bytes()),
            }
            for path in inputs
        ],
        "tableCount": len(tables),
        "tables": {
            name: {
                "rowCount": table["rowCount"],
                "observedCount": table["observedCount"],
                "completeness": table["completeness"],
                "sourceFiles": table["sourceFiles"],
                "sourceRecords": table["sourceRecords"],
                "rowsSha256": table["rowsSha256"],
                "schema": table["schema"],
                **({"selectedError": table["selectedError"]} if "selectedError" in table else {}),
            }
            for name, table in tables.items()
        },
    }, tables


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    args = parser.parse_args()
    inputs = [path.resolve() for path in args.inputs]
    missing = [path for path in inputs if not path.is_file()]
    if missing:
        raise SystemExit("missing input: " + ", ".join(map(str, missing)))
    index, tables = extract(inputs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        output = args.output_dir / f"{_safe_name(name)}.json"
        output.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(tables)} config tables to {args.output_dir}")


if __name__ == "__main__":
    main()
