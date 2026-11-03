"""Create a narrowly scoped MSDK SetPermission no-op diagnostic library.

This is intended only after the BuglyLog probe. The original APK is read-only;
the generated shared objects are separate files and do not touch tprt, il2cpp,
MSDKSystem, or any other SDK entry point.
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
        "symbolValue": 0x124E0,
        "offset": 0x124E0,
        "function": bytes.fromhex(
            "53e83af6ffff81c316580a008d6424e8e81be5ffff8b54242089042489542404"
            "e89be5ffff8d6424185bc3"),
        "return": bytes.fromhex("c3"),
    },
    "armeabi-v7a": {
        "sha256": "e9324023307ff2852b1eece0e2cb9fac9c92fc0caa06e4c0ec64e7553f2417eb",
        "symbolValue": 0x13C39,
        "offset": 0x13C38,
        "function": bytes.fromhex("10b50446fef784ef2146bde810403ff0ddbe"),
        "return": bytes.fromhex("7047"),
    },
}


def patch_library(original: bytes, abi: str) -> tuple[bytes, dict]:
    """Replace only the audited SetPermission entry with an ABI return."""
    if abi not in PATCHES:
        raise ValueError(f"unsupported original APK ABI: {abi}")
    spec = PATCHES[abi]
    digest = hashlib.sha256(original).hexdigest()
    if digest != spec["sha256"]:
        raise ValueError(f"{abi}: libMsdkAdapter.so does not match the verified original")
    offset, function, replacement = spec["offset"], spec["function"], spec["return"]
    if original[offset:offset + len(function)] != function:
        raise ValueError(f"{abi}: SetPermission function bytes differ from audited source")
    output = original[:offset] + replacement + original[offset + len(replacement):]
    if len(output) != len(original) or output[:offset] != original[:offset] or (
            output[offset + len(replacement):] != original[offset + len(replacement):]):
        raise ValueError("unexpected modification outside SetPermission")
    return output, {
        "entry": f"lib/{abi}/libMsdkAdapter.so", "abi": abi,
        "symbol": "SetPermission", "signature": "void SetPermission(int32 permission)",
        "symbolValue": spec["symbolValue"], "fileOffset": offset,
        "originalSha256": digest, "sha256": hashlib.sha256(output).hexdigest(),
        "length": len(output), "patchLength": len(replacement),
        "beforeHex": original[offset:offset + len(replacement)].hex(),
        "afterHex": replacement.hex(), "otherBytesUnchanged": True,
        "behavior": "Return before forwarding permission state to uninitialized MSDK JNI",
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
