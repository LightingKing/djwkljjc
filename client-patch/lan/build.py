"""Build a separately signed LAN client from a verified, read-only original APK.

Only a small temporary APK containing the DEX is sent through apktool. Game
assets and native libraries are copied from the original archive, unchanged
unless named in the audited replacement map. No installed application is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from patch_bootstrap import ENTRY, patch_bundle
from patch_manifest import patch_manifest
from patch_sdk_log import PATCHES as SDK_LOG_PATCHES, patch_library as patch_sdk_library
from patch_sdk_noop import PATCHES as SDK_NOOP_PATCHES, patch_library as patch_sdk_noop
from patch_sdk_bridge import PATCHES as SDK_BRIDGE_PATCHES, patch_library as patch_sdk_bridge
from patch_sdk_report import PATCHES as SDK_REPORT_PATCHES, patch_library as patch_sdk_report
from patch_tprt_package import PATCHES as TPRT_PACKAGE_PATCHES, patch_library as patch_tprt_package

ORIGINAL_APK_SHA256 = "b997dbac9ff1764b46809486d83e4a65c6d511cae17fee6f8063ef598d66a776"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv: list[str | Path], *, env: dict | None = None) -> None:
    args = [str(part) for part in argv]
    if os.name == "nt" and Path(args[0]).suffix.lower() in {".bat", ".cmd"}:
        args = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", *args]
    subprocess.run(args, check=True, env=env)


def build(args) -> dict:
    original = args.apk.resolve()
    if sha256(original) != ORIGINAL_APK_SHA256:
        raise ValueError("original APK SHA256 mismatch")
    output = args.output.resolve()
    work = args.work.resolve()
    if output == original or work == original.parent:
        raise ValueError("output and work must be separate from the original")
    if work.exists() and any(work.iterdir()):
        raise ValueError("use a new, empty work directory")
    if output.exists():
        raise ValueError("refusing to overwrite a prior APK")
    work.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    patch_report = json.loads((args.patch / "build-report.json").read_text("utf-8"))
    replacements: dict[str, bytes] = {}
    sdk_log_reports = []
    sdk_noop_reports = []
    sdk_bridge_reports = []
    sdk_report_reports = []
    tprt_reports = []
    for module in patch_report["modules"]:
        relative = module["target"]
        path = (args.patch / "bundles" / relative).resolve()
        if not path.is_relative_to((args.patch / "bundles").resolve()):
            raise ValueError("unsafe patch path")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != module["bundleSha256"]:
            raise ValueError(f"patch bundle digest mismatch: {relative}")
        replacements["assets/Patch/" + relative] = raw

    thin = work / "dex-input.apk"
    with zipfile.ZipFile(original) as source:
        manifest, manifest_report = patch_manifest(
            source.read("AndroidManifest.xml"), package_name=args.package,
            version_name="2.0.4", label="零境交错·内网版", target_sdk=24,
        )
        replacements["AndroidManifest.xml"] = manifest
        bootstrap, bootstrap_report = patch_bundle(
            source.read(ENTRY), args.base_url.rstrip("/") + "/pr2new_djwk.json"
        )
        replacements[ENTRY] = bootstrap
        # The offline client still reaches the original MSDK log bridge before
        # any game network request.  Patch only the audited entry points; the
        # helper verifies the complete original library digest and function
        # bytes before emitting a same-size replacement.
        for abi in SDK_LOG_PATCHES:
            name = f"lib/{abi}/libMsdkAdapter.so"
            original_library = source.read(name)
            patched, sdk_report = patch_sdk_library(original_library, abi)
            # The second diagnostic patch is also checked against the
            # original digest, then applied at its own audited offset so the
            # two same-size edits compose without weakening either guard.
            noop_source, noop_report = patch_sdk_noop(original_library, abi)
            noop_offset = SDK_NOOP_PATCHES[abi]["offset"]
            noop_bytes = SDK_NOOP_PATCHES[abi]["return"]
            # `patch_sdk_noop` returns a separately verified one-edit copy;
            # take only its audited byte range and apply it to the BuglyLog
            # copy so the first edit remains present.
            if noop_source[:noop_offset] != original_library[:noop_offset] or (
                    noop_source[noop_offset + len(noop_bytes):] !=
                    original_library[noop_offset + len(noop_bytes):]):
                raise ValueError(f"unexpected SetPermission patch span: {abi}")
            patched = patched[:noop_offset] + noop_bytes + patched[noop_offset + len(noop_bytes):]
            replacements[name] = patched
            sdk_log_reports.append(sdk_report)
            sdk_noop_reports.append(noop_report)
            bridge_source, bridge_report = patch_sdk_bridge(original_library, abi)
            bridge_offset = SDK_BRIDGE_PATCHES[abi]["offset"]
            bridge_bytes = SDK_BRIDGE_PATCHES[abi]["return"]
            if bridge_source[:bridge_offset] != original_library[:bridge_offset] or (
                    bridge_source[bridge_offset + len(bridge_bytes):] !=
                    original_library[bridge_offset + len(bridge_bytes):]):
                raise ValueError(f"unexpected setBridge patch span: {abi}")
            patched = patched[:bridge_offset] + bridge_bytes + patched[bridge_offset + len(bridge_bytes):]
            replacements[name] = patched
            sdk_bridge_reports.append(bridge_report)
            report_source, report_item = patch_sdk_report(original_library, abi)
            report_offset = SDK_REPORT_PATCHES[abi]["offset"]
            report_bytes = SDK_REPORT_PATCHES[abi]["return"]
            if report_source[:report_offset] != original_library[:report_offset] or (
                    report_source[report_offset + len(report_bytes):] !=
                    original_library[report_offset + len(report_bytes):]):
                raise ValueError(f"unexpected ReportUnityData patch span: {abi}")
            patched = patched[:report_offset] + report_bytes + patched[report_offset + len(report_bytes):]
            replacements[name] = patched
            sdk_report_reports.append(report_item)
        for abi in TPRT_PACKAGE_PATCHES:
            name = f"lib/{abi}/libtprt.so"
            patched, tprt_item = patch_tprt_package(source.read(name), abi, package=args.package.encode("ascii"))
            replacements[name] = patched
            tprt_reports.append(tprt_item)
        with zipfile.ZipFile(thin, "w") as small:
            for name in ("AndroidManifest.xml", "resources.arsc", "classes.dex"):
                small.writestr(name, source.read(name))

    decoded = work / "decoded"
    run([args.java, "-jar", args.apktool, "d", "-r", thin, "-o", decoded])
    application = decoded / "smali/com/actgames/fci/tx/TXApplication.smali"
    application_source = application.read_text("utf-8")
    old_parent = "Lcom/tencent/tpshell/TPShellApplication;"
    if application_source.count(old_parent) != 3:
        raise ValueError("unexpected TXApplication baseline")
    application.write_text(application_source.replace(old_parent, "Landroid/app/Application;"), "utf-8")
    shim = Path(__file__).resolve().parents[1] / "patches/offline-smali/com/actgames/fci/AppDelegater.smali"
    shutil.copyfile(shim, decoded / "smali/com/actgames/fci/AppDelegater.smali")
    thin_output = work / "dex-output.apk"
    run([args.java, "-jar", args.apktool, "b", decoded, "-o", thin_output])
    with zipfile.ZipFile(thin_output) as compiled:
        replacements["classes.dex"] = compiled.read("classes.dex")

    unsigned = work / "unsigned.apk"
    changed = []
    removed = []
    native_hashes = {}
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(unsigned, "w", allowZip64=False) as target:
        names = set(source.namelist())
        if not set(replacements).issubset(names):
            raise ValueError("replacement entry missing from source")
        for info in source.infolist():
            name = info.filename
            if name.upper().startswith("META-INF/"):
                removed.append(name)
                continue
            # Strip old alignment metadata; zipalign supplies the final offsets.
            entry = zipfile.ZipInfo(name, info.date_time)
            entry.compress_type = info.compress_type
            entry.external_attr = info.external_attr
            if name in replacements:
                raw = replacements[name]
                target.writestr(entry, raw)
                changed.append({"entry": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
            else:
                with source.open(info) as reader, target.open(entry, "w") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
                if name.startswith("lib/") and name.endswith(".so") and name not in replacements:
                    with source.open(info) as native:
                        native_hashes[name] = hashlib.file_digest(native, "sha256").hexdigest()

    run([args.zipalign, "-p", "4", unsigned, output])
    signer_env = dict(os.environ)
    signer_env["JAVA_HOME"] = str(args.java.resolve().parents[1])
    run([args.apksigner, "sign", "--ks", args.keystore, "--ks-key-alias", args.alias,
         "--ks-pass", "file:" + str(args.password_file.resolve()), output], env=signer_env)
    run([args.apksigner, "verify", "--verbose", "--print-certs", output], env=signer_env)
    with zipfile.ZipFile(output) as result:
        for name, expected in replacements.items():
            if result.read(name) != expected:
                raise ValueError(f"packaged replacement changed: {name}")
        for name, expected in native_hashes.items():
            with result.open(name) as native:
                if hashlib.file_digest(native, "sha256").hexdigest() != expected:
                    raise ValueError(f"native library changed: {name}")
    report = {"status": "built-not-device-verified", "input": str(original),
              "inputSha256": ORIGINAL_APK_SHA256, "output": str(output),
              "outputSha256": sha256(output), "bytes": output.stat().st_size,
              "baseUrl": args.base_url, "manifest": manifest_report,
              "bootstrap": bootstrap_report, "replacements": changed,
              "removedEntries": removed,
              "dexChanges": ["TXApplication inherits android.app.Application; lifecycle callbacks preserved",
                             "AppDelegater uses local SDK lifecycle shim"],
              "toolsSha256": {name: sha256(getattr(args, name)) for name in ("java", "apktool", "zipalign", "apksigner")},
              "patchVersion": patch_report["patchVersion"],
              "nativeLibraries": {"allByteIdentical": False,
                                  "patched": sdk_log_reports + sdk_noop_reports + sdk_bridge_reports + sdk_report_reports + tprt_reports,
                                  "unchangedSha256": native_hashes},
              "coldStart": "not-yet-tested"}
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("apk", "output", "work", "patch", "java", "apktool", "zipalign", "apksigner", "keystore", "password-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--package", default="local.ljjc.lan")
    parser.add_argument("--alias", default="ljjc-lan")
    result = build(parser.parse_args())
    print(json.dumps({key: result[key] for key in ("output", "outputSha256", "bytes", "status")}, indent=2))


if __name__ == "__main__":
    main()
