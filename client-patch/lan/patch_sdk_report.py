"""Create a narrowly scoped MSDK ReportUnityData no-op diagnostic library.

The original adapter is checked by whole-file SHA and the complete exported
function SHA before its entry is replaced. The generated files are separate;
no APK, device, service, or other native entry is modified.
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
        "symbolValue": 0x1A070, "offset": 0x1A070, "size": 1715,
        "functionSha256": "5da8dae59555a52a99961836794562110c8b1ead2407458fc03085d341a7afe5",
        "prefixHex": "55575653e8a77affff81c383dc09008d",
        "suffixHex": "248000000089442404e8ffd3ffffebcc",
        "return": bytes.fromhex("c3"),
    },
    "armeabi-v7a": {
        "sha256": "e9324023307ff2852b1eece0e2cb9fac9c92fc0caa06e4c0ec64e7553f2417eb",
        "symbolValue": 0x16655, "offset": 0x16654, "size": 628,
        "functionSha256": "134964be1f104c51283cf2f2cc41667ec2ba3683eef2993de7295d66a6b1bb10",
        "prefixHex": "2de9f04fbfb0954c0df178080d468146",
        "suffixHex": "35750400c972040034f20400d7710400",
        "return": bytes.fromhex("7047"),
    },
}


def patch_library(original: bytes, abi: str) -> tuple[bytes, dict]:
    if abi not in PATCHES:
        raise ValueError(f"unsupported original APK ABI: {abi}")
    spec = PATCHES[abi]
    whole_sha = hashlib.sha256(original).hexdigest()
    if whole_sha != spec["sha256"]:
        raise ValueError(f"{abi}: libMsdkAdapter.so does not match the verified original")
    offset, size, replacement = spec["offset"], spec["size"], spec["return"]
    function = original[offset:offset + size]
    if len(function) != size or hashlib.sha256(function).hexdigest() != spec["functionSha256"]:
        raise ValueError(f"{abi}: complete ReportUnityData bytes differ from audited source")
    if function[:16].hex() != spec["prefixHex"] or function[-16:].hex() != spec["suffixHex"]:
        raise ValueError(f"{abi}: ReportUnityData boundary bytes differ from audited source")
    output = original[:offset] + replacement + original[offset + len(replacement):]
    changed = [i for i, (left, right) in enumerate(zip(original, output)) if left != right]
    expected = list(range(offset, offset + len(replacement)))
    if len(output) != len(original) or changed != expected:
        raise ValueError("unexpected modification outside ReportUnityData")
    return output, {
        "entry": f"lib/{abi}/libMsdkAdapter.so", "abi": abi,
        "symbol": "ReportUnityData", "signature": "void ReportUnityData(string name, string jsonData)",
        "symbolValue": spec["symbolValue"], "fileOffset": offset,
        "functionSize": size, "functionSha256": spec["functionSha256"],
        "originalSha256": whole_sha, "sha256": hashlib.sha256(output).hexdigest(),
        "length": len(output), "patchLength": len(replacement),
        "beforeHex": original[offset:offset + len(replacement)].hex(),
        "afterHex": replacement.hex(), "otherBytesUnchanged": True,
        "behavior": "Return before forwarding report data to MSDKSystem/JNI",
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
