#!/usr/bin/env python3
"""Build a read-only content/resource index from the recovered client data.

The index intentionally reports evidence instead of claiming that a server
feature is implemented.  ActorConfig and ActorSkin are read from the JSON
exports.  SceneConfig/CollectionsScenes can be supplied as JSON exports too;
when they are not exported, the small ``xlsx__*.lua`` records are read with a
conservative field parser.  Full asset paths are checked exactly. UnityPy reads palette textures without
modifying bundles; palette validation remains unverified if it is unavailable.
This tool never executes client logic or changes client/server content.
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


# These are the paths assembled by the client, not fragments to search for.
# UI consumers include widgetactorroller.lua:155,161, newactorget.lua:189,200,
# collectionsactordetail.lua:244 and widgetactorwikiitem.lua:36.
ACTOR_UI_PATHS = {
    "portrait": "texture/actorportrait/actor_portrait_{animRes}",
    "portraitFull": "texture/actorportraitfull/actor_portrait_full_{animRes}",
    "headBig": "texture/actorheadbig/actor_head_big_{animRes}",
    "headRect": "texture/actorheadrectangle/actor_head_{animRes}",
    "logo": "texture/actorlogo/actor_logo_{animRes}",
    "intro": "texture/actorintroduction/introduction_{animRes}",
    "gachaHead": "texture/actorgachahead/gacha_head_{animRes}",
    "skillHead": "texture/actorskillhead/actor_skill_head_{animRes}",
    "journeyHead": "texture/actorjourneyhead/actor_head_{animRes}",
    "selectTeam": "texture/actorgroup/selectteam_{animRes}",
    "infightMain": "texture/actorinfighthead/main/infight_head_{animRes}",
}


def _coverage(present: Iterable[bool]) -> str:
    values = list(present)
    if not values:
        return "no_fields"
    return "complete" if all(values) else "partial" if any(values) else "missing"


class AssetCatalog:
    """Only case/separators are normalized; asset names are otherwise exact."""

    def __init__(self, root: Path):
        self.root = root
        self.paths = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        self.by_name = {p.casefold(): p for p in self.paths}
        self.palettes: dict[str, tuple[dict[str, Any], list[Any] | None]] = {}

    def exact(self, path: str | None) -> dict[str, Any]:
        actual = self.by_name.get(path.replace("\\", "/").casefold()) if path else None
        return {"expectedPath": path, "present": actual is not None, "matchedPaths": [actual] if actual else []}

    def directory(self, path: str | None) -> dict[str, Any]:
        # Directory boundary is significant: voice/foo must not match voice/foobar.
        prefix = path.replace("\\", "/").rstrip("/").casefold() + "/" if path else None
        matches = sorted(p for key, p in self.by_name.items() if prefix and key.startswith(prefix))
        return {"expectedPath": path, "present": bool(matches), "matchedPaths": matches, "fileCount": len(matches)}

    def palette(self, prefab: str | None, index: int | None) -> dict[str, Any]:
        # common__actorutil.lua:1153-1162: Unity row index is bottom-up.
        path = f"dependencies/sprite2d/role/{prefab}/{prefab}_palette" if prefab else None
        evidence = self.exact(path)
        result = {**evidence, "rowIndex": index}
        if not evidence["present"]:
            return {**result, "status": "missing"}
        if path not in self.palettes:
            metadata: dict[str, Any] = {}
            rows = None
            try:
                import UnityPy
                env = UnityPy.load(str(self.root / evidence["matchedPaths"][0]))
                textures = [obj for obj in env.objects if obj.type.name == "Texture2D"]
                if len(textures) != 1:
                    metadata = {"status": "ambiguous_texture" if textures else "no_texture"}
                else:
                    texture = textures[0].read()
                    image = texture.image.convert("RGBA")
                    width, height = image.size
                    pixels = image.load()
                    rows = [tuple(pixels[x, height - 1 - y] for x in range(width)) for y in range(height)]
                    metadata = {"textureName": texture.m_Name, "width": width, "height": height}
            except ImportError:
                metadata = {"status": "unverified", "reason": "UnityPy is not installed"}
            except Exception as exc:
                metadata = {"status": "unverified", "reason": f"{type(exc).__name__}: {exc}"}
            self.palettes[path] = metadata, rows
        metadata, rows = self.palettes[path]
        result.update(metadata)
        if rows is None:
            return result
        if not isinstance(index, int) or index < 0 or index >= len(rows):
            return {**result, "status": "out_of_range"}
        row = rows[index]
        if all(pixel[3] == 0 for pixel in row) or all(pixel[:3] == (0, 0, 0) for pixel in row):
            status = "empty_row"
        elif index != 0 and row == rows[0]:
            status = "same_as_row0"
        else:
            status = "ok"
        return {**result, "status": status}


def _shared_resources(row: Mapping[str, Any], catalog: AssetCatalog) -> dict[str, Any]:
    prefab, logic, voice, skin_ui = (row.get(k) for k in ("prefabRes", "logicRes", "voiceRes", "skinUIRes"))
    return {
        "animationRole": catalog.exact(f"animation/role/{prefab}" if prefab else None),
        "animationPreview": catalog.exact(f"animation/preview/{prefab}" if prefab else None),
        "logic": catalog.exact(f"data/logicproto/{logic}" if logic else None),
        "voice": catalog.directory(f"audio/voice/{voice}" if voice else None),
        "skinHead": catalog.exact(f"texture/actorskinhead/actorskinhead_{skin_ui}" if skin_ui is not None else None),
        "skinHeadBig": catalog.exact(f"texture/actorskinheadbig/actorskinhead_big_{skin_ui}" if skin_ui is not None else None),
    }


def _skill_index(actor: Mapping[str, Any], skills: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [s for s in skills if s.get("actorId") == actor.get("id")]
    active = [s for s in rows if s.get("displayIndex") in (1, 2, 3)
              and str(s.get("cmdStr", "")).strip() and not s.get("isPassive") and not s.get("isLink")]
    slots = sorted({s["displayIndex"] for s in active})
    # Sub/support cards expose one active slot; main cards expose three.
    # window__burstlinkactorskill__burstlinkactorskill.lua:36-53.
    expected = [1] if actor.get("kind") == 2 else [1, 2, 3]
    missing = sorted(set(expected) - set(slots))
    return {
        "table": "ActorSkill", "rowCount": len(rows),
        "activeSkills": [{k: s.get(k) for k in ("skillId", "displayIndex", "cmdStr")} for s in active],
        "activeDisplayIndexes": slots, "expectedActiveSlots": expected,
        "missingActiveSlots": missing, "passiveSkillIds": [s.get("skillId") for s in rows if s.get("isPassive")],
        "status": "cannot_battle" if not slots else "incomplete" if missing else "complete",
    }


def _actor_index(rows: list[dict[str, Any]], catalog: AssetCatalog, skills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        ui = {key: catalog.exact(template.format(animRes=row["animRes"]) if row.get("animRes") else None)
              for key, template in ACTOR_UI_PATHS.items()}
        resources = _shared_resources(row, catalog)
        palette = catalog.palette(row.get("prefabRes"), 0)
        skill = _skill_index(row, skills)
        present = sum(resource["present"] for resource in ui.values())
        output.append({
            "id": row.get("id"), "role": row.get("role"), "kind": row.get("kind"),
            "category": row.get("category"), "openTime": row.get("openTime"),
            "resourceFields": {k: row.get(k) for k in ("animRes", "prefabRes", "logicRes", "voiceRes", "skinUIRes")},
            "uiResources": ui, "uiPresentCount": present, "uiExpectedCount": len(ui),
            "uiStatus": _coverage(r["present"] for r in ui.values()),
            "resources": resources, "palette": palette, "skills": skill,
            "status": _coverage([*(r["present"] for r in ui.values()), *(r["present"] for r in resources.values()),
                                  palette["status"] == "ok", skill["status"] == "complete"]),
        })
    return output


def _skin_index(rows: list[dict[str, Any]], actor_rows: list[dict[str, Any]], catalog: AssetCatalog) -> list[dict[str, Any]]:
    actors = {a["id"]: a for a in actor_rows}
    output = []
    for row in rows:
        variants, warnings = [], []
        ids, palettes = row.get("actorIds", []), row.get("paletteIndexs", [])
        if len(ids) != len(palettes):
            warnings.append("actorIds/paletteIndexs length mismatch")
        for i, actor_id in enumerate(ids):
            index = palettes[i] if i < len(palettes) else None
            actor = actors.get(actor_id)
            if actor is None:
                variants.append({"actorId": actor_id, "paletteIndex": index, "status": "missing_actor_config"})
                continue
            shared = _shared_resources(actor, catalog)
            resources = {key: shared[key] for key in ("animationRole", "animationPreview", "skinHead", "skinHeadBig")}
            palette = catalog.palette(actor.get("prefabRes"), index)
            if not resources["skinHead"]["present"]:
                warnings.append(f"actor {actor_id}: small skin icon missing")
            checks = [resources[key]["present"] for key in ("animationRole", "animationPreview", "skinHeadBig")]
            checks.append(palette["status"] == "ok")
            variants.append({"actorId": actor_id, "paletteIndex": index, "resources": resources,
                             "palette": palette, "status": _coverage(checks)})
        states = [v["status"] for v in variants]
        complete = bool(states) and all(s == "complete" for s in states) and not len(ids) != len(palettes)
        status = "complete" if complete else "partial" if any(s in ("complete", "partial") for s in states) else "missing"
        output.append({**{k: row.get(k) for k in ("id", "actorIds", "openTime", "sources", "paletteIndexs", "exchangeDefault", "exchangeGacha")},
                       "variants": variants, "warnings": warnings, "status": status})
    return output


def _scene_index(rows: list[dict[str, Any]], catalog: AssetCatalog, table: str) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        fields = {field: row[field] for field in ("sceneName", "textureRes", "effectRes", "bgmRes") if row.get(field)}
        paths = {"textureRes": "texture/background/", "effectRes": "effect/ui/", "bgmRes": "audio/music/"}
        resources = {field: catalog.exact(prefix + str(row[field])) for field, prefix in paths.items() if row.get(field)}
        output.append({"table": table, "id": row.get("id", row.get("sceneName", 0)), "fields": fields,
                       "openTime": row.get("openTime"), "resources": resources,
                       "status": _coverage(r["present"] for r in resources.values()) if resources else "unverified",
                       "note": "SceneConfig.sceneName names a runtime scene; no bundle path is inferred." if not resources else None})
    return output


def build_index(asset_root: Path, config_dir: Path, lua_dir: Path | None = None) -> dict[str, Any]:
    if not asset_root.is_dir():
        raise FileNotFoundError(f"Asset root does not exist: {asset_root}")
    catalog = AssetCatalog(asset_root)
    lua_dir = lua_dir or config_dir
    actor_rows = _json_rows(config_dir / "ActorConfig.json")
    skin_rows = _json_rows(config_dir / "ActorSkin.json")
    skill_rows = _json_rows(config_dir / "ActorSkill.json")
    scene_rows = _json_rows(config_dir / "SceneConfig.json") or _lua_rows(lua_dir / "xlsx__sceneconfig.lua", "SceneConfig")
    collection_rows = _json_rows(config_dir / "CollectionsScenes.json") or _lua_rows(lua_dir / "xlsx__collectionsscenes.lua", "CollectionsScenes")
    return {
        "schemaVersion": 2,
        "source": {"assetRoot": str(asset_root), "fileCount": len(catalog.paths), "configDir": str(config_dir), "luaDir": str(lua_dir)},
        "characters": _actor_index(actor_rows, catalog, skill_rows),
        "skins": _skin_index(skin_rows, actor_rows, catalog),
        "scenes": _scene_index(scene_rows, catalog, "SceneConfig") + _scene_index(collection_rows, catalog, "CollectionsScenes"),
        "notes": [
            "Exact normalized full paths only; voice uses an exact directory boundary. No substring or numeric-ID match is used.",
            "UI, shared animation/voice, skin palettes and active skills are separate evidence; 0/11 UI does not mean shared resources are absent.",
            "Skin status checks role/preview animation, large skin head and a nonempty authored palette row. Small skin icons are separately reported warnings.",
            "Palette rows are read-only decoded with UnityPy; missing decoder or failed decode never counts as a verified row.",
            "Evidence is automated and does not establish runtime loading or server availability; no content has been opened.",
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
