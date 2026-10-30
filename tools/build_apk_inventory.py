#!/usr/bin/env python3
"""Build a deterministic inventory for the original APK without extracting it."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
import zipfile


def _category(name: str) -> str:
    normalized = name.replace("\\", "/")
    if normalized.startswith("assets/bin/Data/"):
        return "unity-data"
    if normalized.startswith("lib/"):
        return "native-library"
    if normalized.startswith("assets/"):
        return "assets"
    if normalized.startswith("res/"):
        return "android-resources"
    if normalized.startswith("META-INF/"):
        return "signing-metadata"
    if normalized in {"AndroidManifest.xml", "resources.arsc", "classes.dex", "classes2.dex"} or normalized.endswith(".dex"):
        return "android-runtime"
    return "other"


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def build_inventory(apk: Path) -> dict[str, object]:
    categories: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "compressed_bytes": 0, "uncompressed_bytes": 0})
    top_levels: Counter[str] = Counter()
    abis: Counter[str] = Counter()
    extensions: Counter[str] = Counter()
    entries: list[dict[str, object]] = []
    with zipfile.ZipFile(apk) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            name = info.filename.replace("\\", "/")
            category = _category(name)
            stats = categories[category]
            stats["files"] += 1
            stats["compressed_bytes"] += int(info.compress_size)
            stats["uncompressed_bytes"] += int(info.file_size)
            top_levels[name.split("/", 1)[0]] += 1
            suffix = Path(name).suffix.lower() or "<none>"
            extensions[suffix] += 1
            if name.startswith("lib/"):
                parts = name.split("/")
                if len(parts) > 1:
                    abis[parts[1]] += 1
            entries.append({
                "path": name,
                "category": category,
                "compressedBytes": int(info.compress_size),
                "uncompressedBytes": int(info.file_size),
                "compression": "stored" if info.compress_type == zipfile.ZIP_STORED else "deflated" if info.compress_type == zipfile.ZIP_DEFLATED else str(info.compress_type),
            })
    return {
        "schemaVersion": 1,
        "source": {
            "path": str(apk),
            "sizeBytes": apk.stat().st_size,
            "sha256": _sha256(apk),
        },
        "summary": {
            "files": len(entries),
            "categories": {key: categories[key] for key in sorted(categories)},
            "topLevelEntries": dict(sorted(top_levels.items())),
            "extensions": dict(sorted(extensions.items())),
            "nativeAbis": dict(sorted(abis.items())),
        },
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.apk.is_file():
        raise SystemExit(f"APK does not exist: {args.apk}")
    inventory = build_inventory(args.apk.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {inventory['summary']['files']} entries to {args.output}")


if __name__ == "__main__":
    main()
