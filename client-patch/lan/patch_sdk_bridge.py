"""Create a narrowly scoped MSDK setBridge no-op diagnostic library.

The entry is the managed AndroidConnector.setBridge bridge adapter. The
original APK is read-only; this emits separate shared objects and changes only
the exported adapter entry. It does not alter BuglyLog, SetPermission,
MSDKSystem, tprt, il2cpp, or any other native function.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


PATCHES = {
    "x86": {
        "sha256": "c87ebe781c413a1b13bec0dab6ef2f9fa223468f39bbc096fbf8f5e6a1c61ded",
        "symbolValue": 0x12370,
        "offset": 0x12370,
        "size": 34,
        "function": bytes.fromhex(
            "53e8aaf7ffff81c386590a008d6424e88b442420890424"
            "e864feffff8d6424185bc3"),
        "return": bytes.fromhex("c3"),
    },
    "armeabi-v7a": {
        "sha256": "e9324023307ff2852b1eece0e2cb9fac9c92fc0caa06e4c0ec64e7553f2417eb",
        "symbolValue": 0x13BCD,  # Thumb bit is part of the ELF symbol value.
        "offset": 0x13BCC,
        "size": 4,
        "function": bytes.fromhex("fff794bf"),
        "return": bytes.fromhex("7047"),
    },
}


def patch_library(original: bytes, abi: str) -> tuple[bytes, dict]:
    if abi not in PATCHES:
        raise ValueError(f"unsupported original APK ABI: {abi}")
    spec = PATCHES[abi]
    digest = hashlib.sha256(original).hexdigest()
    if digest != spec["sha256"]:
        raise ValueError(f"{abi}: libMsdkAdapter.so does not match the verified original")
    offset, function, replacement = spec["offset"], spec["function"], spec["return"]
    if len(function) != spec["size"] or original[offset:offset + len(function)] != function:
        raise ValueError(f"{abi}: setBridge function bytes differ from audited source")
    output = original[:offset] + replacement + original[offset + len(replacement):]
    changed = [i for i, (left, right) in enumerate(zip(original, output)) if left != right]
    expected = list(range(offset, offset + len(replacement)))
    if len(output) != len(original) or changed != expected:
        raise ValueError("unexpected modification outside setBridge")
    return output, {
        "entry": f"lib/{abi}/libMsdkAdapter.so", "abi": abi,
        "symbol": "setBridge", "signature": "void setBridge(delegate)",
        "nativeSignature": "void setBridge(char *(*bridge)(const char *))",
        "symbolValue": spec["symbolValue"], "fileOffset": offset,
        "functionSize": spec["size"], "originalSha256": digest,
        "sha256": hashlib.sha256(output).hexdigest(), "length": len(output),
        "patchLength": len(replacement), "beforeHex": original[offset:offset + len(replacement)].hex(),
        "afterHex": replacement.hex(), "otherBytesUnchanged": True,
        "behavior": "Return before forwarding the managed bridge callback to MSDKSystem",
        "deviceValidation": "not performed by this tool",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    source, report_path = args.apk.resolve(), args.report.resolve()
    destinations = {abi: (args.output_dir / "lib" / abi / "libMsdkAdapter.so").resolve()
                    for abi in PATCHES}
    if source == report_path or source in destinations.values() or report_path in destinations.values():
        parser.error("output files and report must be separate from input APK")
    patches = []
    with zipfile.ZipFile(source) as archive:
        for abi in PATCHES:
            patched, item = patch_library(archive.read(f"lib/{abi}/libMsdkAdapter.so"), abi)
            patches.append((destinations[abi], patched, item))
    for path, payload, _ in patches:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    report = {"sourceApk": str(source), "libraries": [item for _, _, item in patches]}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
