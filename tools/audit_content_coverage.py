#!/usr/bin/env python3
"""Audit whether recovered configuration entries have APK resource evidence.

This is the reproducible M2.8 check.  It deliberately reports evidence and
coverage separately from implementation status: a resource name matching a
configuration row proves that the client contains an asset, but it does not
prove that the retired server data or every runtime dependency was recovered.
The tool only reads the generated APK inventory and JSON configuration tables.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _entries(inventory: Mapping[str, Any]) -> list[str]:
    raw = inventory.get("entries", [])
    if not isinstance(raw, list):
        return []
    return [str(item.get("path", "")) for item in raw if isinstance(item, Mapping) and item.get("path")]


def _matches(paths: Iterable[str], tokens: Iterable[str]) -> list[str]:
    normalized = [(path, path.casefold()) for path in paths]
    needles = tuple(token.casefold().strip("/\\") for token in tokens if str(token).strip("/\\"))
    if not needles:
        return []
    return sorted({path for path, lowered in normalized if any(needle in lowered for needle in needles)})


def _actor_evidence(row: Mapping[str, Any]) -> list[str]:
    # These fields are direct resource names in ActorConfig; preserving them
    # in the report makes every match auditable without guessing an asset ID.
    return [
        str(row.get(field, ""))
        for field in ("prefabRes", "logicRes", "voiceRes", "animRes")
        if str(row.get(field, "")).strip()
    ]


def _chapter_evidence(row: Mapping[str, Any]) -> list[str]:
    index = row.get("chapterIndex")
    if isinstance(index, int) or (isinstance(index, str) and index.isdigit()):
        value = int(index)
        return [f"story_chapter{value}", f"story_c{value:02d}", f"chapter{value}"]
    return []


def _story_evidence(row: Mapping[str, Any]) -> list[str]:
    # StoryDungeon IDs are server-side IDs.  Do not infer a chapter from the
    # numeric suffix; only use a stable, generic story token as evidence.
    dungeon_id = row.get("dungeonId")
    return ["story"] if dungeon_id is not None else []


def _row_key(table: str, row: Mapping[str, Any], index: int) -> str:
    for field in ("id", "chapterId", "dungeonId", "roleId"):
        value = row.get(field)
        if value not in (None, ""):
            return f"{table}:{value}"
    return f"{table}:row-{index + 1}"


def _table_tokens(table: str, row: Mapping[str, Any]) -> list[str]:
    if table == "ActorConfig":
        return _actor_evidence(row)
    if table == "ChapterInfo":
        return _chapter_evidence(row)
    if table == "StoryDungeon":
        return _story_evidence(row)
    return []


def audit_content(inventory: Mapping[str, Any], config_dir: Path) -> dict[str, Any]:
    """Return a deterministic content evidence report."""

    paths = _entries(inventory)
    tables: list[dict[str, Any]] = []
    selected_tables = ("ActorConfig", "ChapterInfo", "StoryDungeon")
    for table_name in selected_tables:
        source = config_dir / f"{table_name}.json"
        table: Mapping[str, Any] = {}
        if source.is_file():
            try:
                loaded = _read_json(source)
                table = loaded if isinstance(loaded, Mapping) else {}
            except (OSError, ValueError, json.JSONDecodeError):
                table = {}
        rows = table.get("rows", []) if isinstance(table, Mapping) else []
        rows = rows if isinstance(rows, list) else []
        records: list[dict[str, Any]] = []
        for index, raw_row in enumerate(rows):
            row = raw_row if isinstance(raw_row, Mapping) else {}
            tokens = _table_tokens(table_name, row)
            evidence = _matches(paths, tokens)
            records.append({
                "key": _row_key(table_name, row, index),
                "tokens": tokens,
                "evidence": evidence,
                "status": "evidence" if evidence else "no_evidence",
            })
        complete = str(table.get("completeness", "unknown")) if isinstance(table, Mapping) else "missing"
        evidence_rows = sum(record["status"] == "evidence" for record in records)
        if not records:
            status = "no_rows"
        elif complete == "partial" or evidence_rows != len(records):
            status = "partial"
        else:
            status = "complete"
        tables.append({
            "table": table_name,
            "source": str(source),
            "completeness": complete,
            "rowCount": len(records),
            "evidenceRowCount": evidence_rows,
            "status": status,
            "rows": records,
        })

    return {
        "schemaVersion": 1,
        "inventorySource": inventory.get("source", {}),
        "tables": tables,
        "summary": {
            "tables": len(tables),
            "complete": sum(table["status"] == "complete" for table in tables),
            "partial": sum(table["status"] == "partial" for table in tables),
            "noRows": sum(table["status"] == "no_rows" for table in tables),
        },
        "notes": [
            "Evidence means only that an APK inventory path contains a recovered resource token.",
            "Partial configuration tables are not promoted to complete content.",
            "This report does not infer missing server data or validate gameplay behavior.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inventory = _read_json(args.inventory)
    if not isinstance(inventory, Mapping):
        raise SystemExit("inventory must be a JSON object")
    report = audit_content(inventory, args.config_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(f"audited {summary['tables']} tables: {summary['complete']} complete, {summary['partial']} partial")


if __name__ == "__main__":
    main()

