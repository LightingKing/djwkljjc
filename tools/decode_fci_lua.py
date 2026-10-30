"""Offline decoder for the original LJJC APK's FCI\\0-protected Lua TextAssets.

Reverse-engineered from matched (ciphertext, runtime-plaintext) pairs on 2026-10-28.
The original client (defunct since 2020) is read only; nothing here modifies the APK.

Format
------
Each `assets/Patch/assetbundle/data/lua/*` AssetBundle holds one TextAsset whose
raw bytes are `b"FCI\\x00"` + ciphertext_body. The decrypted container always begins
with the magic `b"\\x00fci\\x01"` followed by a length-prefixed asset name and the
Lua 5.3 payload (prefix `00 66 63 69 01`, decodable by runtime/ljjc-lua/decompile.py).

Transform (length preserving)
-----------------------------
    key[j]   = cipher_body[j] XOR 0x00,0x66,0x63,0x69   for j in 0..3   (plaintext magic)
    plain[i] = cipher_body[i] XOR key[(i % 4 + i // 4) % 4]

The 4-byte key is per file but recoverable from the ciphertext alone, because the
first four plaintext bytes are always the constant magic `00 66 63 69`.

Validated byte-exact against every available runtime dump in
analysis/ljjc-local/lua-runtime/ (Common_ActorUtil, Common_DungeonUtil,
Common_GuildUtil, Common_MailUtil, Scene_SceneFight, Window_Login_Login, ...).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import UnityPy

MAGIC_CIPHER = b"FCI\x00"
MAGIC_PLAIN4 = bytes([0x00, 0x66, 0x63, 0x69])   # "\0fci"
MAGIC_PLAIN5 = bytes([0x00, 0x66, 0x63, 0x69, 0x01])
LUA_PREFIX = "assets/Patch/assetbundle/data/lua/"


def decode_fci(raw: bytes) -> tuple[bytes, list[int]]:
    """Decode one FCI\\0 TextAsset payload. Returns (plaintext, 4-byte key)."""
    if raw[:4] != MAGIC_CIPHER:
        raise ValueError(f"not an FCI payload: {raw[:4]!r}")
    body = raw[4:]
    key = [body[j] ^ MAGIC_PLAIN4[j] for j in range(4)]
    out = bytearray(len(body))
    for i in range(len(body)):
        out[i] = body[i] ^ key[(i % 4 + i // 4) % 4]
    return bytes(out), key


def _text_asset_bytes(bundle_bytes: bytes) -> bytes | None:
    for obj in UnityPy.load(bundle_bytes).objects:
        if obj.type.name == "TextAsset":
            # surrogateescape keeps the TextAsset byte-exact (plain str would lose >=0x80)
            return obj.read_typetree()["m_Script"].encode("utf-8", "surrogateescape")
    return None


def iter_lua_entries(zf: zipfile.ZipFile):
    for name in zf.namelist():
        if name.startswith(LUA_PREFIX) and not name.endswith("/"):
            yield name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("apk", type=Path, help="original LJJC APK (read only)")
    ap.add_argument("--output-root", type=Path, required=True,
                    help="directory to write decoded .luac files")
    ap.add_argument("--report", type=Path, required=True, help="audit JSON path")
    args = ap.parse_args(argv)

    args.output_root.mkdir(parents=True, exist_ok=True)
    zf = zipfile.ZipFile(args.apk)
    apk_sha = hashlib.sha256(args.apk.read_bytes()).hexdigest()

    records = []
    ok = bad = 0
    for entry in sorted(iter_lua_entries(zf)):
        rel = entry[len(LUA_PREFIX):]                 # e.g. common/actorutil
        rec = {"apk_entry": entry, "rel": rel}
        try:
            raw = _text_asset_bytes(zf.read(entry))
            if raw is None:
                raise ValueError("no TextAsset in bundle")
            plain, key = decode_fci(raw)
            rec["cipher_len"] = len(raw)
            rec["plain_len"] = len(plain)
            rec["key"] = [f"0x{b:02x}" for b in key]
            rec["magic_ok"] = plain[:5] == MAGIC_PLAIN5
            rec["sha256"] = hashlib.sha256(plain).hexdigest()
            out = args.output_root / (rel.replace("/", "__") + ".luac")
            out.write_bytes(plain)
            rec["output"] = str(out.relative_to(args.output_root))
            rec["status"] = "decoded" if rec["magic_ok"] else "decoded_magic_mismatch"
            ok += 1 if rec["magic_ok"] else 0
            bad += 0 if rec["magic_ok"] else 1
        except Exception as exc:  # noqa: BLE001 - record every failure, keep going
            rec["status"] = "error"
            rec["error"] = f"{type(exc).__name__}: {exc}"
            bad += 1
        records.append(rec)

    report = {
        "apk": str(args.apk),
        "apk_sha256": apk_sha,
        "total": len(records),
        "decoded_magic_ok": ok,
        "problems": bad,
        "records": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"total={len(records)} decoded_magic_ok={ok} problems={bad}")
    print(f"output={args.output_root}  report={args.report}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
