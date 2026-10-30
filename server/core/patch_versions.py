"""Select incremental patches without publishing or mutating a manifest.

The 2.0.4 client sends ``major.minor.build.revision.patch``.  Its native
patcher prompts whenever history is nonempty, so the server must filter it.
An empty history is a *plain JSON* PatchVersionInfo, not a RestfulResponse.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping


class PatchVersionError(ValueError):
    """The client version or the configured patch manifest is invalid."""


class PatchVersionMismatch(PatchVersionError):
    """Incremental Lua patches cannot be applied to another APK baseline."""


def parse_version(value: str) -> tuple[tuple[int, int, int, int], int]:
    """Parse a complete client version, e.g. ``2.0.4.59162.4``."""
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+\.\d+", value, re.ASCII):
        raise PatchVersionError("expected a five-component numeric client version")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 2_147_483_647 for part in parts):
        raise PatchVersionError("version component exceeds int32")
    return parts[:4], parts[4]


def select_patch_manifest(manifest: Mapping[str, Any], client_version: str | None) -> dict[str, Any]:
    """Return a detached manifest containing only patches the client needs.

    A missing version preserves the legacy full-history response. Invalid
    versions fail explicitly. On a different APK baseline, only an existing
    version-0 full-application entry may be returned; incremental entries
    are never offered to an incompatible APK. The caller owns HTTP errors.
    """
    if not isinstance(manifest, Mapping):
        raise PatchVersionError("manifest must be an object")
    baseline, _ = parse_version(manifest.get("version"))
    history = manifest.get("history")
    if not isinstance(history, list):
        raise PatchVersionError("manifest history must be a list")
    seen: set[int] = set()
    for entry in history:
        if not isinstance(entry, Mapping):
            raise PatchVersionError("patch entry must be an object")
        for key in ("version", "length", "checksum"):
            value = entry.get(key)
            if type(value) is not int or not 0 <= value <= 2_147_483_647:
                raise PatchVersionError(f"patch {key} must be a nonnegative int32")
        if entry["version"] in seen:
            raise PatchVersionError("duplicate patch version")
        seen.add(entry["version"])
        urls = entry.get("urls")
        if not isinstance(urls, list) or not urls or any(not isinstance(url, str) or not url for url in urls):
            raise PatchVersionError("patch urls must contain nonempty strings")
    result = deepcopy(dict(manifest))
    if client_version is None or client_version == "":
        return result
    client_baseline, patch = parse_version(client_version)
    if client_baseline != baseline:
        full_app = [entry for entry in result["history"] if entry["version"] == 0]
        if not full_app:
            raise PatchVersionMismatch("client APK baseline differs from the configured patch baseline")
        result["history"] = full_app
        return result
    result["history"] = sorted(
        (entry for entry in result["history"] if entry["version"] > patch),
        key=lambda entry: entry["version"],
    )
    return result
