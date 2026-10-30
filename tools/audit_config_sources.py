#!/usr/bin/env python3
"""Audit APK configuration asset names against recovered JSON tables.

The APK inventory records paths only; it does not decode AssetBundles or Lua
payloads.  This tool therefore reports source availability and extraction
state without treating an inventory path as recovered data.  It is useful for
M2.2/M2.8 because it makes the missing table surface explicit and repeatable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory_paths(inventory: Mapping[str, Any]) -> list[str]:
    entries = inventory.get("entries", [])
    if not isinstance(entries, list):
        return []
    return sorted(
        str(item["path"])
        for item in entries
        if isinstance(item, Mapping)
        and isinstance(item.get("path"), str)
        and "/data/" in str(item["path"]).casefold()
        and ("/xlsx/" in str(item["path"]).casefold() or "/config" in str(item["path"]).casefold())
    )


def _asset_table_name(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    parts = normalized.casefold().split("/")
    for marker in ("xlsx",):
        if marker not in parts:
            continue
        index = len(parts) - 1 - parts[::-1].index(marker)
        if index + 1 < len(parts) and parts[index + 1]:
            return parts[index + 1]
    return None


def _config_tables(config_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not config_dir.is_dir():
        return result
    for path in sorted(config_dir.glob("*.json")):
        if path.name.casefold() == "index.json":
            continue
        try:
            value = _read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        result[path.stem.casefold()] = {
            "name": path.stem,
            "path": str(path),
            "fileSha256": _sha256(path),
            "completeness": str(value.get("completeness", "unknown")),
            "rowCount": int(value.get("rowCount", 0) or 0),
            "observedCount": int(value.get("observedCount", 0) or 0),
            "rowsSha256": value.get("rowsSha256"),
        }
    return result


def audit_config_sources(inventory_path: Path, config_dir: Path) -> dict[str, Any]:
    inventory = _read_json(inventory_path)
    paths = _inventory_paths(inventory)
    xlsx_paths = [path for path in paths if "/xlsx/" in path.casefold()]
    non_xlsx_config_paths = [path for path in paths if "/xlsx/" not in path.casefold()]
    assets: dict[str, list[str]] = {}
    for path in paths:
        table = _asset_table_name(path)
        if table:
            assets.setdefault(table, []).append(path)
    configs = _config_tables(config_dir)
    names = sorted(set(assets) | set(configs))
    tables: list[dict[str, Any]] = []
    for name in names:
        asset_paths = sorted(assets.get(name, []))
        config = configs.get(name)
        if asset_paths and config:
            status = "asset_and_extracted"
        elif asset_paths:
            status = "asset_only"
        else:
            status = "extracted_without_inventory_asset"
        tables.append({
            "table": config["name"] if config else name,
            "normalizedName": name,
            "status": status,
            "assetPaths": asset_paths,
            "extracted": config,
            "decoded": False,
        })
    summary = {
        "tables": len(tables),
        "inventoryPathCount": len(paths),
        "xlsxAssetPathCount": len(xlsx_paths),
        "xlsxDistinctTableNames": len(assets),
        "nonXlsxConfigPathCount": len(non_xlsx_config_paths),
        "assetAndExtracted": sum(row["status"] == "asset_and_extracted" for row in tables),
        "assetOnly": sum(row["status"] == "asset_only" for row in tables),
        "extractedWithoutInventoryAsset": sum(row["status"] == "extracted_without_inventory_asset" for row in tables),
    }
    config_source: dict[str, Any] = {
        "path": str(config_dir.resolve()),
        "tableCount": len(configs),
    }
    index_path = config_dir / "index.json"
    if index_path.is_file():
        config_source["indexPath"] = str(index_path.resolve())
        config_source["indexSha256"] = _sha256(index_path)
    return {
        "schemaVersion": 1,
        "inventorySource": {
            "path": str(inventory_path.resolve()),
            "sha256": _sha256(inventory_path),
            "entryCount": len(inventory.get("entries", [])) if isinstance(inventory.get("entries"), list) else 0,
        },
        "configSource": config_source,
        "inventoryPaths": {
            "allConfigCandidates": paths,
            "xlsxAssets": xlsx_paths,
            "nonXlsxConfigCandidates": non_xlsx_config_paths,
        },
        "summary": summary,
        "tables": tables,
        "notes": [
            "An APK inventory path proves only that a named asset was listed; it does not prove that its AssetBundle or Lua payload was decoded.",
            "asset_only rows remain blocked until a decoder produces a schema and rows with provenance.",
            "decoded is intentionally false for every row in this report because this audit never parses protected payloads.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit_config_sources(args.inventory, args.config_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["audit_config_sources"]
