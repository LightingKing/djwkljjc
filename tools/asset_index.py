#!/usr/bin/env python3
"""Build a read-only content/resource index from the recovered client data.

The index intentionally reports evidence instead of claiming that a server
feature is implemented.  ActorConfig and ActorSkin are read from the JSON
exports.  SceneConfig/CollectionsScenes can be supplied as JSON exports too;
when they are not exported, the small ``xlsx__*.lua`` records are read with a
conservative field parser.  The asset root is only enumerated: Unity bundles
are never modified or executed.  UnityPy is therefore optional and is not
needed for the path-level index.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


def _json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    rows = value.get("rows", []) if isinstance(value, Mapping) else []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _lua_rows(path: Path, table_name: str) -> list[dict[str, Any]]:
    """Extract stable scalar fields from decompiled ProtobufI records.

    This is deliberately a limited parser.  It only consumes named scalar
    fields and never evaluates Lua.  Nested records and arrays are left out;
    the JSON exports remain the authoritative source when available.
    """
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    marker = f'ProtobufI("{table_name}"'
    rows: list[dict[str, Any]] = []
    for match in re.finditer(re.escape(marker), text):
        start = text.find("{", match.end())
        if start < 0:
            continue
        depth = 0
        end = start
        in_string = False
        escaped = False
        for pos in range(start, len(text)):
            char = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
        block = text[start : end + 1]
        row: dict[str, Any] = {}
        for key, raw in re.findall(r'(?m)\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*("(?:\\.|[^"])*"|-?\d+(?:\.\d+)?)', block):
            if raw.startswith('"'):
                try:
                    row[key] = json.loads(raw)
                except json.JSONDecodeError:
                    continue
            else:
                row[key] = float(raw) if "." in raw else int(raw)
        if row:
            rows.append(row)
    return rows


def _find(paths: Iterable[str], tokens: Iterable[str]) -> list[str]:
    needles = [str(token).strip("/\\").casefold() for token in tokens if str(token).strip("/\\")]
    return sorted({path for path in paths if any(token in path.casefold() for token in needles)})


def _resource_paths(paths: list[str], tokens: Iterable[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for token in tokens:
        value = str(token)
        if value:
            result[value] = _find(paths, [value])
    return result


def _status(groups: Mapping[str, list[str]]) -> str:
    present = [bool(value) for value in groups.values()]
    if not present:
        return "no_fields"
    if all(present):
        return "complete"
    if any(present):
        return "partial"
    return "missing"


def _actor_index(rows: list[dict[str, Any]], paths: list[str]) -> list[dict[str, Any]]:
    fields = ("animRes", "prefabRes", "logicRes", "voiceRes", "skinUIRes")
    output = []
    for row in rows:
        tokens = {field: str(row[field]) for field in fields if row.get(field) not in (None, "")}
        resources = _resource_paths(paths, tokens.values())
        output.append({
            "id": row.get("id"),
            "role": row.get("role"),
            "kind": row.get("kind"),
            "category": row.get("category"),
            "openTime": row.get("openTime"),
            "resourceFields": tokens,
            "resources": resources,
            "status": _status(resources),
        })
    return output


def _skin_index(rows: list[dict[str, Any]], paths: list[str]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        skin_id = row.get("id")
        actor_ids = [str(value) for value in row.get("actorIds", []) if value not in (None, "")]
        id_matches = _find(paths, [str(skin_id)]) if skin_id is not None else []
        actor_matches = _find(paths, actor_ids)
        output.append({
            "id": skin_id,
            "actorIds": row.get("actorIds", []),
            "openTime": row.get("openTime"),
            "sources": row.get("sources", []),
            "paletteIndexs": row.get("paletteIndexs", []),
            "exchangeDefault": row.get("exchangeDefault"),
            "exchangeGacha": row.get("exchangeGacha"),
            "skinIdMatches": id_matches,
            "baseActorMatches": actor_matches,
            "status": "skin_id_evidence" if id_matches else ("base_actor_evidence" if actor_matches else "missing"),
        })
    return output


def _scene_index(rows: list[dict[str, Any]], paths: list[str], table: str) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        tokens = {field: str(row[field]) for field in ("sceneName", "textureRes", "effectRes", "bgmRes") if row.get(field) not in (None, "")}
        resources = _resource_paths(paths, tokens.values())
        output.append({"table": table, "id": row.get("id"), "fields": tokens, "resources": resources, "status": _status(resources)})
    return output


def build_index(asset_root: Path, config_dir: Path, lua_dir: Path | None = None) -> dict[str, Any]:
    paths = sorted(p.relative_to(asset_root).as_posix() for p in asset_root.rglob("*") if p.is_file())
    lua_dir = lua_dir or config_dir
    actor_rows = _json_rows(config_dir / "ActorConfig.json")
    skin_rows = _json_rows(config_dir / "ActorSkin.json")
    scene_rows = _json_rows(config_dir / "SceneConfig.json")
    collection_rows = _json_rows(config_dir / "CollectionsScenes.json")
    if not scene_rows:
        scene_rows = _lua_rows(lua_dir / "xlsx__sceneconfig.lua", "SceneConfig")
    if not collection_rows:
        collection_rows = _lua_rows(lua_dir / "xlsx__collectionsscenes.lua", "CollectionsScenes")
    return {
        "schemaVersion": 1,
        "source": {"assetRoot": str(asset_root), "fileCount": len(paths), "configDir": str(config_dir)},
        "characters": _actor_index(actor_rows, paths),
        "skins": _skin_index(skin_rows, paths),
        "scenes": _scene_index(scene_rows, paths, "SceneConfig") + _scene_index(collection_rows, paths, "CollectionsScenes"),
        "notes": [
            "Path matches are evidence that a named asset exists; they do not prove runtime loading or server availability.",
            "ActorSkin has palette/exchange fields but no direct animation field; baseActorMatches and skinIdMatches are reported separately.",
            "The scanner is read-only and does not decrypt, modify, or rebuild Unity AssetBundles.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--lua-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_index(args.asset_root, args.config_dir, args.lua_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"indexed {report['source']['fileCount']} assets, {len(report['characters'])} characters, {len(report['skins'])} skins, {len(report['scenes'])} scenes")


if __name__ == "__main__":
    main()
