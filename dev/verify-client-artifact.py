#!/usr/bin/env python3
"""Collect reproducible evidence for a patched LJJC APK.

The checker is deliberately read-only with respect to the APK and the
installed application data.  It computes the artifact digest, verifies the
APK signature, reads package metadata through Android build tools, and can
perform non-destructive cold starts on an already-installed package.  It
never installs, uninstalls, clears data, or changes TPShell/integrity code.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable, Sequence


class VerificationError(RuntimeError):
    """A local checker input or tool invocation is invalid."""


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    output: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "outputSha256": hashlib.sha256(self.output.encode("utf-8", "replace")).hexdigest(),
        }


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _which(candidates: Iterable[str]) -> Path | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return Path(found)
    return None


def find_android_tool(kind: str, explicit: Path | None = None, *, sdk_root: Path | None = None) -> Path | None:
    """Resolve a build tool without assuming one particular SDK revision."""

    if explicit:
        return explicit
    env_name = {"adb": "ADB", "apksigner": "APKSIGNER", "aapt2": "AAPT2", "aapt": "AAPT"}[kind]
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured)
    root = sdk_root or Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or r"C:\Users\Administrator\AppData\Local\Android\Sdk")
    if kind == "adb":
        candidates = [root / "platform-tools" / name for name in ("adb.exe", "adb")]
    else:
        build_root = root / "build-tools"
        revisions = sorted((p for p in build_root.iterdir() if p.is_dir()), reverse=True) if build_root.is_dir() else []
        names = {
            "apksigner": ("apksigner.bat", "apksigner"),
            "aapt2": ("aapt2.exe", "aapt2"),
            "aapt": ("aapt.exe", "aapt"),
        }[kind]
        candidates = [revision / name for revision in revisions for name in names]
    from_sdk = next((candidate for candidate in candidates if candidate.is_file()), None)
    if from_sdk:
        return from_sdk
    if kind == "adb":
        return _which(("adb", "adb.exe"))
    return _which((kind, f"{kind}.bat", f"{kind}.exe"))


def _run(command: Sequence[str | Path], *, timeout: float = 60.0) -> CommandResult:
    executable = Path(str(command[0]))
    argv = [str(item) for item in command]
    # Windows batch entry points need cmd.exe, but no shell interpolation is
    # used for user-provided paths or arguments.
    if os.name == "nt" and executable.suffix.casefold() in {".bat", ".cmd"}:
        argv = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", *argv]
    try:
        completed = subprocess.run(
            argv,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError(f"command failed to run: {' '.join(argv)}: {exc}") from exc
    return CommandResult(tuple(str(item) for item in command), completed.returncode, completed.stdout)


def parse_badging(output: str) -> dict[str, str | None]:
    """Parse the stable fields emitted by ``aapt2 dump badging``."""

    package_match = re.search(
        r"^package:\s+name='([^']+)'\s+versionCode='([^']*)'\s+versionName='([^']*)'",
        output,
        re.MULTILINE,
    )
    target_match = re.search(r"targetSdkVersion:'([^']+)'", output)
    if not package_match:
        return {"package": None, "versionCode": None, "versionName": None, "targetSdk": target_match.group(1) if target_match else None}
    return {
        "package": package_match.group(1),
        "versionCode": package_match.group(2),
        "versionName": package_match.group(3),
        "targetSdk": target_match.group(1) if target_match else None,
    }


def parse_apksigner(output: str) -> dict[str, Any]:
    schemes: dict[str, bool] = {}
    for match in re.finditer(r"Verified using (v[123](?:\.\d+)?) scheme(?:\s+\([^)]*\))?:\s*(true|false)", output, re.IGNORECASE):
        schemes[match.group(1).lower()] = match.group(2).lower() == "true"
    signer_match = re.search(r"^Number of signers:\s*(\d+)", output, re.MULTILINE | re.IGNORECASE)
    signer_count = int(signer_match.group(1)) if signer_match else len(re.findall(r"^Signer #\d+ certificate", output, re.MULTILINE))
    verified = bool(re.search(r"^(?:Verified|Verifies)$", output, re.MULTILINE)) and "DOES NOT VERIFY" not in output
    return {"verified": verified, "schemes": schemes, "signerCount": signer_count}


def _output_digest(output: str) -> str:
    return hashlib.sha256(output.encode("utf-8", "replace")).hexdigest()


def _run_adb(adb: Path, serial: str, args: Sequence[str], *, timeout: float = 30.0) -> CommandResult:
    return _run([adb, "-s", serial, *args], timeout=timeout)


def _installed_apk_path(pm_output: str) -> str | None:
    match = re.search(r"(?:^|\n)package:(\S+)", pm_output)
    return match.group(1) if match else None


def _sha256sum(output: str) -> str | None:
    match = re.search(r"\b([0-9a-fA-F]{64})\b", output)
    return match.group(1).lower() if match else None


def _cold_start(
    *,
    adb: Path,
    serial: str,
    package: str,
    runs: int,
    settle_seconds: float,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    crash_pattern = re.compile(r"FATAL EXCEPTION|has died|Process .* crashed", re.IGNORECASE)
    relevant_pattern = re.compile(r"FATAL EXCEPTION|has died|Process .* crashed|TPShell|tpshell", re.IGNORECASE)
    for index in range(1, runs + 1):
        started = datetime.now(timezone.utc).isoformat()
        force_stop = _run_adb(adb, serial, ["shell", "am", "force-stop", package])
        monkey = _run_adb(adb, serial, ["shell", "monkey", "-p", package, "1"])
        if settle_seconds:
            time.sleep(settle_seconds)
        pid = _run_adb(adb, serial, ["shell", "pidof", package])
        activity = _run_adb(adb, serial, ["shell", "dumpsys", "activity", "activities"])
        logcat = _run_adb(adb, serial, ["logcat", "-d", "-t", "400"])
        activity_text = activity.output
        relevant_log_lines = [line for line in logcat.output.splitlines() if relevant_pattern.search(line)]
        crash_log_lines = [line for line in relevant_log_lines if crash_pattern.search(line)]
        alive = bool(pid.output.strip())
        focused = bool(re.search(
            rf"(?:mResumedActivity|ResumedActivity).*{re.escape(package)}",
            activity_text,
            re.IGNORECASE,
        ))
        # ``monkey``'s return code is the launch command status.  Android does
        # not expose a portable child exit code through adb, so processAlive
        # and the focused activity are recorded separately.
        records.append({
            "run": index,
            "startedAt": started,
            "forceStopExitCode": force_stop.returncode,
            "monkeyExitCode": monkey.returncode,
            # adb does not expose an Activity process's native exit status in
            # a portable way.  Keep the field explicit instead of inferring a
            # code from a missing pid; monkeyExitCode is the launch command's
            # actual exit code and processAlive/focusedPackage are the state
            # evidence after settling.
            "processExitCode": None,
            "processAlive": alive,
            "pid": pid.output.strip(),
            "focusedPackage": focused,
            "activityOutputSha256": _output_digest(activity_text),
            "logcatOutputSha256": _output_digest(logcat.output),
            "matchedLogLines": relevant_log_lines[:100],
            "crashLogLines": crash_log_lines[:100],
            "tpshellObserved": any("tpshell" in line.lower() for line in relevant_log_lines),
            "passed": monkey.returncode == 0 and (alive or focused) and not crash_log_lines,
        })
    return records


def verify_artifact(
    *,
    apk: Path,
    expected_sha256: str | None = None,
    expected_package: str | None = None,
    expected_target_sdk: int | None = None,
    apksigner: Path | None = None,
    aapt2: Path | None = None,
    aapt: Path | None = None,
    sdk_root: Path | None = None,
    adb: Path | None = None,
    serial: str | None = None,
    cold_start_runs: int = 0,
    settle_seconds: float = 3.0,
) -> dict[str, Any]:
    apk = apk.resolve()
    if not apk.is_file() or apk.suffix.casefold() != ".apk":
        raise VerificationError(f"APK does not exist or is not an APK file: {apk}")
    if cold_start_runs < 0 or cold_start_runs > 20:
        raise VerificationError("cold_start_runs must be between 0 and 20")
    digest = sha256_file(apk)
    expected = expected_sha256.lower() if expected_sha256 else None
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "apk": str(apk),
        "sha256": {"actual": digest, "expected": expected, "matches": (digest == expected) if expected else None},
        "signature": {"tool": None, "command": None, "exitCode": None, "verified": False, "schemes": {}, "signerCount": 0},
        "metadata": {"tool": None, "command": None, "exitCode": None, "package": None, "versionCode": None, "versionName": None, "targetSdk": None},
        "coldStart": {"requestedRuns": cold_start_runs, "serial": serial, "package": expected_package, "records": [], "passed": None},
        "passed": False,
    }
    if expected and digest != expected:
        result["error"] = "SHA-256 mismatch"
        return result

    # An explicitly supplied path is authoritative.  A typo must be reported
    # as unavailable instead of silently falling back to another SDK revision.
    signer_tool = (apksigner if apksigner and apksigner.is_file() else None) if apksigner is not None else find_android_tool("apksigner", sdk_root=sdk_root)
    if signer_tool:
        signed = _run([signer_tool, "verify", "--verbose", apk])
        parsed = parse_apksigner(signed.output)
        result["signature"] = {"tool": str(signer_tool), **signed.as_dict(), "exitCode": signed.returncode, **parsed}
    else:
        result["signature"]["error"] = "apksigner not found"

    if aapt2 is not None:
        metadata_tool = aapt2 if aapt2.is_file() else None
    elif aapt is not None:
        metadata_tool = aapt if aapt.is_file() else None
    else:
        metadata_tool = find_android_tool("aapt2", sdk_root=sdk_root)
        if not metadata_tool:
            metadata_tool = find_android_tool("aapt", sdk_root=sdk_root)
    if metadata_tool:
        metadata_command = [metadata_tool, "dump", "badging", apk]
        badging = _run(metadata_command)
        result["metadata"] = {"tool": str(metadata_tool), **badging.as_dict(), "exitCode": badging.returncode, **parse_badging(badging.output)}
    else:
        result["metadata"]["error"] = "aapt2/aapt not found"

    metadata = result["metadata"]
    metadata_ok = bool(metadata.get("package") and metadata.get("versionCode") is not None and metadata.get("versionName") is not None)
    package_ok = expected_package is None or metadata.get("package") == expected_package
    target_ok = expected_target_sdk is None or str(metadata.get("targetSdk")) == str(expected_target_sdk)
    signature_ok = result["signature"].get("exitCode") == 0 and bool(result["signature"].get("verified"))

    if cold_start_runs:
        if not serial or not expected_package:
            raise VerificationError("--serial and --package are required when --cold-start-runs is non-zero")
        adb_tool = adb or find_android_tool("adb", sdk_root=sdk_root)
        if not adb_tool:
            raise VerificationError("adb not found; pass --adb or set ADB")
        installed = _run_adb(adb_tool, serial, ["shell", "pm", "path", expected_package])
        remote_apk = _installed_apk_path(installed.output)
        installed_check: dict[str, Any] = {
            **installed.as_dict(),
            "exitCode": installed.returncode,
            "installed": installed.returncode == 0 and remote_apk is not None,
            "remoteApk": remote_apk,
        }
        if remote_apk:
            installed_digest = _run_adb(adb_tool, serial, ["shell", "sha256sum", remote_apk])
            installed_check["digestCommand"] = installed_digest.as_dict()
            installed_check["installedSha256"] = _sha256sum(installed_digest.output)
            installed_check["artifactMatches"] = installed_check["installedSha256"] == digest
        else:
            installed_check["artifactMatches"] = False
        result["coldStart"]["installedCheck"] = installed_check
        if not installed_check["installed"]:
            result["coldStart"]["error"] = "package is not installed; checker will not install it"
            result["coldStart"]["passed"] = False
        elif not installed_check["artifactMatches"]:
            result["coldStart"]["error"] = "installed package digest does not match the APK under test; cold start was skipped"
            result["coldStart"]["passed"] = False
        else:
            records = _cold_start(adb=adb_tool, serial=serial, package=expected_package, runs=cold_start_runs, settle_seconds=settle_seconds)
            result["coldStart"]["records"] = records
            result["coldStart"]["passed"] = len(records) == cold_start_runs and all(item["passed"] for item in records)

    result["passed"] = bool(signature_ok and metadata_ok and package_ok and target_ok and (result["coldStart"]["passed"] in {None, True}))
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-package", "--package", dest="expected_package")
    parser.add_argument("--expected-target-sdk", type=int)
    parser.add_argument("--sdk-root", type=Path)
    parser.add_argument("--apksigner", type=Path)
    parser.add_argument("--aapt2", type=Path)
    parser.add_argument("--aapt", type=Path)
    parser.add_argument("--adb", type=Path)
    parser.add_argument("--serial")
    parser.add_argument("--cold-start-runs", type=int, default=0)
    parser.add_argument("--settle-seconds", type=float, default=3.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_artifact(
            apk=args.apk,
            expected_sha256=args.expected_sha256,
            expected_package=args.expected_package,
            expected_target_sdk=args.expected_target_sdk,
            apksigner=args.apksigner,
            aapt2=args.aapt2,
            aapt=args.aapt,
            sdk_root=args.sdk_root,
            adb=args.adb,
            serial=args.serial,
            cold_start_runs=args.cold_start_runs,
            settle_seconds=args.settle_seconds,
        )
    except VerificationError as exc:
        print(f"verify-client-artifact: error: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
