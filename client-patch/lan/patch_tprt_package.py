"""Retarget the original TPShell package-name check for the LAN APK.

The original tprt library embeds the production package name and verifies its
own file CRC before libil2cpp's startup decoder runs. The LAN APK deliberately
uses a separate Android package, so this patch changes only that embedded
NUL-terminated string. Four bytes in the adjacent zero padding are adjusted
to preserve the audited whole-file CRC for each ABI; this keeps the native
decoder key and every code/data byte outside the padding unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import zlib

ORIGINAL_PACKAGE = b"com.tencent.tmgp.fcicv"
TARGET_PACKAGE = b"local.ljjc.lan"

PATCHES = {
    "x86": {
        "sha256": "e121a05539e3543281f7184e264e5d28f90bb688e4a67105085af198bbe6ae3b",
        "crc32": 0xE86D06B4,
        "packageOffset": 0x34434,
        "crcFixOffset": 0x3444A,
        "crcFix": bytes.fromhex("98f372de"),
    },
    "armeabi-v7a": {
        "sha256": "cf1b0947bc35b3265ed9730e2d8a990a81032379274017934557a210c97d89b5",
        "crc32": 0x61BB02AD,
        "packageOffset": 0x234E4,
        "crcFixOffset": 0x234FA,
        "crcFix": bytes.fromhex("98f372de"),
    },
}


def patch_library(original: bytes, abi: str, *, package: bytes = TARGET_PACKAGE) -> tuple[bytes, dict]:
    if abi not in PATCHES:
        raise ValueError(f"unsupported ABI: {abi}")
    spec = PATCHES[abi]
    if len(package) > len(ORIGINAL_PACKAGE):
        raise ValueError("target package does not fit the original fixed-size string")
    if b"\0" in package or not package:
        raise ValueError("target package must be a nonempty NUL-free byte string")
    if hashlib.sha256(original).hexdigest() != spec["sha256"]:
        raise ValueError(f"{abi}: libtprt.so does not match the audited original")
    offset = spec["packageOffset"]
    slot = original[offset:offset + len(ORIGINAL_PACKAGE)]
    if slot != ORIGINAL_PACKAGE:
        raise ValueError(f"{abi}: embedded package string differs from audited source")
    fix_offset = spec["crcFixOffset"]
    if original[fix_offset:fix_offset + 4] != b"\0" * 4:
        raise ValueError(f"{abi}: CRC compensation padding is not zero")
    replacement = package + b"\0" * (len(ORIGINAL_PACKAGE) - len(package))
    output = bytearray(original)
    output[offset:offset + len(ORIGINAL_PACKAGE)] = replacement
    output[fix_offset:fix_offset + 4] = spec["crcFix"]
    output = bytes(output)
    if zlib.crc32(output) & 0xFFFFFFFF != spec["crc32"]:
        raise ValueError(f"{abi}: CRC compensation failed")
    changed = [i for i, (a, b) in enumerate(zip(original, output)) if a != b]
    allowed = set(range(offset, offset + len(ORIGINAL_PACKAGE))) | set(range(fix_offset, fix_offset + 4))
    if not set(changed).issubset(allowed) or not all(output[i] != original[i] for i in changed):
        raise ValueError(f"{abi}: unexpected bytes changed")
    return output, {
        "entry": f"lib/{abi}/libtprt.so", "abi": abi,
        "originalSha256": hashlib.sha256(original).hexdigest(),
        "sha256": hashlib.sha256(output).hexdigest(),
        "originalCrc32": f"{spec['crc32']:08x}",
        "outputCrc32": f"{zlib.crc32(output) & 0xFFFFFFFF:08x}",
        "packageOffset": offset, "packageBefore": ORIGINAL_PACKAGE.decode(),
        "packageAfter": package.decode(), "crcFixOffset": fix_offset,
        "crcFixHex": spec["crcFix"].hex(), "otherBytesUnchanged": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--package", default=TARGET_PACKAGE.decode())
    args = parser.parse_args(argv)
    source = args.apk.resolve(); report_path = args.report.resolve()
    if source == report_path:
        parser.error("input APK and report must be separate")
    package = args.package.encode("ascii")
    outputs = []
    with zipfile.ZipFile(source) as archive:
        for abi in PATCHES:
            patched, item = patch_library(archive.read(f"lib/{abi}/libtprt.so"), abi, package=package)
            path = (args.output_dir / "lib" / abi / "libtprt.so").resolve()
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(patched)
            outputs.append(item)
    report = {"sourceApk": str(source), "libraries": outputs}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
