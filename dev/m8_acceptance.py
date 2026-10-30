#!/usr/bin/env python3
"""Produce repeatable M8.3/M8.4 evidence for the offline server.

The roadmap deliberately separates service evidence from the blocked Android
client gate.  This helper therefore checks only the local stability tests,
contract smoke run, route/evidence boundary audit, delivery archive integrity
and (optionally) a temporary server health check.  It never opens the original
APK or contacts a retired remote service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable
from urllib.request import urlopen
from zipfile import ZipFile


_SENSITIVE_SUFFIXES = {
    ".apk", ".aab", ".apks", ".jks", ".keystore", ".p12", ".pem", ".key",
    ".sqlite3", ".db", ".log", ".jsonl", ".png", ".jpg", ".jpeg",
}
_SENSITIVE_SEGMENTS = {
    ".git", ".pytest_cache", "__pycache__", ".venv", "venv", "runtime",
    "state", "backups", "logs", "screenshots", "artifacts", "delivery", "analysis", "tmp",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_sensitive(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return path.suffix.lower() in _SENSITIVE_SUFFIXES or any(
        segment in _SENSITIVE_SEGMENTS for segment in path.parts
    )


def inspect_archive(archive: str | Path) -> dict[str, Any]:
    """Validate archive exclusions and manifest hashes without extracting it."""

    path = Path(archive).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with ZipFile(path) as bundle:
        names = [item.filename for item in bundle.infolist() if not item.is_dir()]
        sensitive = sorted(name for name in names if _is_sensitive(name))
        manifest_ok = False
        manifest_error: str | None = None
        try:
            manifest = json.loads(bundle.read("DELIVERY-MANIFEST.json").decode("utf-8-sig"))
            listed = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(listed, list):
                raise ValueError("manifest.files must be a list")
            archive_files = set(names) - {"DELIVERY-MANIFEST.json"}
            manifest_files = {str(item.get("path")) for item in listed if isinstance(item, dict)}
            if archive_files != manifest_files:
                raise ValueError("manifest file set differs from archive")
            for item in listed:
                if not isinstance(item, dict):
                    raise ValueError("manifest entry is not an object")
                name = str(item.get("path"))
                expected = str(item.get("sha256"))
                actual = hashlib.sha256(bundle.read(name)).hexdigest()
                if actual != expected:
                    raise ValueError(f"manifest hash mismatch: {name}")
            manifest_ok = True
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            manifest_error = str(exc)
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "entries": len(names),
        "sensitiveEntries": sensitive,
        "manifestValid": manifest_ok,
        "manifestError": manifest_error,
        "passed": not sensitive and manifest_ok,
    }


def _run(command: list[str], root: Path) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (completed.stdout + completed.stderr).strip()
    return {
        "command": command,
        "exitCode": completed.returncode,
        "durationSeconds": round(time.monotonic() - started, 3),
        "outputTail": output[-4000:],
        "passed": completed.returncode == 0,
    }


def run_regression(root: Path) -> dict[str, Any]:
    """Run the stability/backup/log tests and contract smoke probe."""

    # The report is deliberately written below runtime/ so it cannot enter a
    # later delivery archive.  A fresh checkout may not have that directory
    # yet, so create it here rather than relying on start-server.cmd.
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    checks = [
        _run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_long_term_stability.py",
                "tests/test_clock.py",
                "tests/test_activity_schedule.py",
                "tests/test_backup.py",
                "tests/test_crash_logs.py",
                "tests/test_cross_process_state.py",
                "tests/test_local_evidence_audit.py",
                "tests/test_safe_contract_completion.py",
            ],
            root,
        ),
        _run(
            [sys.executable, "tools/contract_harness.py", "--report", str(root / "runtime" / "m8-contract-report.json")],
            root,
        ),
        _run(
            [
                sys.executable,
                "tools/audit_local_evidence.py",
                "--output",
                str(root / "runtime" / "m8-local-evidence-audit.json"),
            ],
            root,
        ),
        _run(
            [
                sys.executable,
                "dev/local_playability_smoke.py",
                "--output",
                str(root / "runtime" / "local-playability-smoke.json"),
            ],
            root,
        ),
    ]
    return {"checks": checks, "passed": all(item["passed"] for item in checks)}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_delivery_health(archive: Path) -> dict[str, Any]:
    """Extract the package into a disposable directory and probe health."""

    root = Path(tempfile.mkdtemp(prefix="ljjc-m8-delivery-"))
    process: subprocess.Popen[str] | None = None
    try:
        fixture = root / "fixtures"
        state = root / "state"
        backups = root / "backups"
        logs = root / "logs"
        for directory in (fixture, state, backups, logs):
            directory.mkdir(parents=True, exist_ok=True)
        with ZipFile(archive) as bundle:
            bundle.extractall(root)
        (fixture / "pr2new_djwk.json").write_text('{"version":"local","entries":[]}', encoding="utf-8")
        port = _free_port()
        process = subprocess.Popen(
            [
                sys.executable,
                str(root / "server.py"),
                "--fixture-root",
                str(fixture),
                "--state-dir",
                str(state),
                "--unified-store-path",
                str(state / "local.sqlite3"),
                "--backup-dir",
                str(backups),
                "--log",
                str(logs / "requests.jsonl"),
                "--bind-host",
                "127.0.0.1",
                "--http-port",
                str(port),
                "--no-tls",
                "--no-auto-backup",
            ],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        health: dict[str, Any] | None = None
        for _ in range(60):
            if process.poll() is not None:
                stderr = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"server exited: {stderr[-2000:]}")
            try:
                with urlopen(f"http://127.0.0.1:{port}/__local__/health", timeout=1.5) as response:
                    health = json.load(response)
                if health.get("status") == "ok":
                    break
            except Exception:
                time.sleep(0.25)
        if not health or health.get("status") != "ok":
            raise RuntimeError("health probe timed out")
        with urlopen(f"http://127.0.0.1:{port}/__local__/coverage", timeout=2) as response:
            coverage = json.load(response)
        return {
            "status": health.get("status"),
            "coverageInterfaces": len(coverage.get("interfaces", [])),
            "passed": True,
        }
    except Exception as exc:  # report data, not an instruction
        return {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(root, ignore_errors=True)


def build_report(
    root: str | Path,
    *,
    archive: str | Path | None = None,
    run_tests: bool = False,
    health_check: bool = False,
) -> dict[str, Any]:
    checkout = Path(root).resolve()
    bundle = Path(archive).resolve() if archive is not None else checkout / "delivery" / "ljjc-offline-server.zip"
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "root": str(checkout),
        "archive": None,
        "regression": None,
        "deliveryHealth": None,
    }
    try:
        report["archive"] = inspect_archive(bundle)
    except Exception as exc:
        report["archive"] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    if run_tests:
        report["regression"] = run_regression(checkout)
    if health_check and report["archive"].get("passed"):
        report["deliveryHealth"] = run_delivery_health(bundle)
    checks = [report["archive"]]
    if report["regression"] is not None:
        checks.append(report["regression"])
    if report["deliveryHealth"] is not None:
        checks.append(report["deliveryHealth"])
    report["passed"] = all(bool(item and item.get("passed")) for item in checks)
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = build_report(args.root, archive=args.archive, run_tests=args.run_tests, health_check=args.health_check)
    output = args.output or (Path(args.root).resolve() / "analysis" / "m8-acceptance-report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_report", "inspect_archive", "run_delivery_health", "run_regression"]
