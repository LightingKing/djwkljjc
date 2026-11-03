"""Disable the uninitialized MSDK Bugly log bridge in a separate LAN client.

Original metadata declares Msdk.AndroidConnector.BuglyLog as static extern void
BuglyLog(int level, string log). Returning at the adapter entry avoids its JNI
dependency. Unity/Android console logging and every other SDK entry stay intact.
This tool reads the original APK and emits only replacement SO files and a report.
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
        "symbolValue": 0x151F0,
        "offset": 0x151F0,
        "function": bytes.fromhex(
            "53e82ac9ffff81c3062b0a008d6424e8e80bb8ffff8b542424890424895424088b542420"
            "89542404e803b8ffff8d6424185bc3"),
        "return": bytes.fromhex("c3"),  # ret; cdecl caller releases its arguments.
    },
    "armeabi-v7a": {
        "sha256": "e9324023307ff2852b1eece0e2cb9fac9c92fc0caa06e4c0ec64e7553f2417eb",
        "symbolValue": 0x1440B,  # Bit zero denotes Thumb, not a byte offset.
        "offset": 0x1440A,
        "function": bytes.fromhex("38b50c460546fef79aeb29462246bde838403ff02abc"),
        "return": bytes.fromhex("7047"),  # bx lr before any stack/register changes.
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
    if original[offset:offset + len(function)] != function:
        raise ValueError(f"{abi}: BuglyLog function bytes differ from the audited source")
    output = original[:offset] + replacement + original[offset + len(replacement):]
    if len(output) != len(original) or output[:offset] != original[:offset] or (
            output[offset + len(replacement):] != original[offset + len(replacement):]):
        raise ValueError("unexpected modification outside the BuglyLog entry")
    return output, {
        "entry": f"lib/{abi}/libMsdkAdapter.so", "abi": abi,
        "symbol": "BuglyLog", "signature": "void BuglyLog(int32 level, const char *log)",
        "symbolValue": spec["symbolValue"], "fileOffset": offset,
        "originalSha256": digest, "sha256": hashlib.sha256(output).hexdigest(),
        "length": len(output), "patchLength": len(replacement),
        "beforeHex": original[offset:offset + len(replacement)].hex(),
        "afterHex": replacement.hex(), "otherBytesUnchanged": True,
        "behavior": "Return before forwarding a log to uninitialized MSDK JNI",
        "deviceValidation": "not performed by this tool",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.apk.resolve()
    report_path = args.report.resolve()
    destinations = {abi: (args.output_dir / "lib" / abi / "libMsdkAdapter.so").resolve()
                    for abi in PATCHES}
    if source == report_path or source in destinations.values() or report_path in destinations.values():
        parser.error("output files and report must be separate from the input APK and each other")
    # Verify both source libraries before writing either output.
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
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
