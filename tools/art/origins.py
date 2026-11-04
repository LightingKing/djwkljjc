"""Provenance records and explicit user approval bound to exact PNG bytes."""
from __future__ import annotations

from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import re

from PIL import Image

from .paths import within_root


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _provenance(origin: dict) -> None:
    if not isinstance(origin, dict) or origin.get("kind") not in {"generated", "derived", "original-copy"}:
        raise ValueError("origin provenance kind must be generated, derived or original-copy")
    if origin["kind"] == "generated" and not str(origin.get("prompt", "")).strip():
        raise ValueError("generated origin requires the actual generation prompt")
    if origin["kind"] != "generated" and not str(origin.get("operation", "")).strip():
        raise ValueError("derived/copied origin requires an explicit operation")
    refs = origin.get("references")
    if not isinstance(refs, list) or (origin["kind"] != "generated" and not refs):
        raise ValueError("origin references must record source paths and SHA256")
    for ref in refs:
        if (not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not ref["path"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(ref.get("sha256", "")))):
            raise ValueError("origin reference requires a path and SHA256")


def _png(raw: bytes) -> Image.Image:
    with Image.open(io.BytesIO(raw)) as image:
        if image.format != "PNG":
            raise ValueError("art input must be PNG")
        image.load()
        if image.mode not in {"RGBA", "RGB", "L"}:
            raise ValueError("PNG mode must be RGBA, RGB or L")
        return image.copy()


def make_origin_record(asset_root: Path, path: str, *, generated_at: str, origin: dict) -> dict:
    """Record a draft. This function never grants approval."""
    file = within_root(asset_root, path)
    _provenance(origin)
    try:
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("generatedAt must be an ISO generation date/time") from exc
    raw = file.read_bytes()
    image = _png(raw)
    return {"path": path, "sha256": digest(raw), "width": image.width, "height": image.height,
            "mode": image.mode, "generatedAt": generated_at, "origin": origin,
            "approval": {"status": "pending"}}


def verify_approved_asset(entry: dict, asset_root: Path) -> bytes:
    """Read only the PNG reviewed by the user; refuse pending or changed art."""
    _provenance(entry.get("origin"))
    if not entry.get("generatedAt"):
        raise ValueError("art provenance needs generatedAt")
    approval = entry.get("approval", {})
    if (approval.get("status") != "approved" or approval.get("confirmedBy") != "user"
            or not approval.get("confirmedAt") or not approval.get("evidence")):
        raise ValueError("art needs explicit user approval before packaging")
    expected = entry.get("sha256")
    if not re.fullmatch(r"[0-9a-f]{64}", str(expected)) or approval.get("sha256") != expected:
        raise ValueError("approval SHA256 must bind the exact reviewed image hash")
    raw = within_root(asset_root, entry["path"]).read_bytes()
    if digest(raw) != expected:
        raise ValueError("art SHA256 changed since review")
    image = _png(raw)
    if (image.width, image.height, image.mode) != (entry.get("width"), entry.get("height"), entry.get("mode")):
        raise ValueError("art dimensions or mode differ from the provenance record")
    return raw


def write_origins(path: Path, entries: list[dict]) -> None:
    """Save hashes/provenance only, with deterministic order and no image data."""
    if len({r["path"] for r in entries}) != len(entries):
        raise ValueError("duplicate art origins path")
    data = {"schemaVersion": 1, "usage": "personal-offline-fan-art-no-distribution",
            "assets": sorted(entries, key=lambda entry: entry["path"])}
    Path(path).write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
