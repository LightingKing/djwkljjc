#!/usr/bin/env python3
"""Read-only APK triage for the local LJJC reconstruction.

The scanner only reads the ZIP central directory and selected small text/binary
entries.  It never installs, executes, rewrites, or extracts an APK.  The
output is intended to make a static inspection reproducible before any client
patching work is attempted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path


URL_RE = re.compile(rb"(?i)(?:https?|wss?)://[^\x00\x09\x0a\x0d\x20\"'<>\\]{4,500}")
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".lua",
    ".proto",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
INTERESTING_HOSTS = (
    "actgames",
    "fcicv",
    "game.qq.com",
    "midas",
    "msdk",
    "qq.com",
    "tencent",
    "tpns",
    "xyapi",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def printable_urls(payload: bytes) -> list[str]:
    urls: list[str] = []
    for match in URL_RE.findall(payload):
        value = match.decode("utf-8", "ignore").rstrip(".,);]}")
        if value and value not in urls:
            urls.append(value)
    return urls


def scan(apk: Path, max_read_bytes: int = 16 * 1024 * 1024) -> dict[str, object]:
    if not apk.is_file():
        raise FileNotFoundError(apk)
    with zipfile.ZipFile(apk) as archive:
        infos = sorted(archive.infolist(), key=lambda item: item.filename)
        names = [item.filename.replace("\\", "/") for item in infos]
        top_levels = Counter(name.split("/", 1)[0] for name in names)
        abis = Counter(
            name.split("/", 2)[1]
            for name in names
            if name.startswith("lib/") and name.count("/") >= 2
        )
        libraries = [name for name in names if name.startswith("lib/") and name.endswith(".so")]
        text_candidates: list[dict[str, object]] = []
        url_hits: list[dict[str, str]] = []
        for info, name in zip(infos, names):
            suffix = Path(name).suffix.lower()
            # Scan small config/text entries and dex/native metadata for ASCII
            # endpoint strings.  Skip huge Unity bundles and media files.
            should_scan = suffix in TEXT_SUFFIXES or name.endswith((".dex", ".so", ".dat"))
            if not should_scan or info.file_size > max_read_bytes:
                continue
            payload = archive.read(info)
            if suffix in TEXT_SUFFIXES:
                text_candidates.append({"path": name, "bytes": info.file_size})
            for url in printable_urls(payload):
                lower = url.lower()
                if any(host in lower for host in INTERESTING_HOSTS):
                    url_hits.append({"entry": name, "url": url})
        # Keep URLs deterministic and de-duplicated while retaining source entry.
        unique_urls: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for hit in url_hits:
            key = (hit["entry"], hit["url"])
            if key not in seen:
                seen.add(key)
                unique_urls.append(hit)
        return {
            "schemaVersion": 1,
            "readOnly": True,
            "source": {
                "path": str(apk.resolve()),
                "sizeBytes": apk.stat().st_size,
                "sha256": sha256(apk),
            },
            "zip": {
                "entries": len(names),
                "duplicateNames": len(names) - len(set(names)),
                "topLevels": dict(sorted(top_levels.items())),
                "nativeAbis": dict(sorted(abis.items())),
                "nativeLibraries": libraries,
                "textCandidates": text_candidates,
            },
            "urlCandidates": unique_urls,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    report = scan(args.apk)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source = report["source"]
    print(f"read-only scan: {source['path']}")
    print(f"sha256: {source['sha256']}")
    print(f"entries: {report['zip']['entries']}")
    print(f"url candidates: {len(report['urlCandidates'])}")
    print(f"wrote: {args.output}")


if __name__ == "__main__":
    main()
