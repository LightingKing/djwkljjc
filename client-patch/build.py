#!/usr/bin/env python3
"""Deterministic, read-only-input APK patch pipeline.

The pipeline intentionally has no shell/eval step and never writes to the
source APK.  It validates the input digest, unpacks into an isolated work
directory, applies a small JSON patch manifest, rebuilds with apktool, then
aligns and signs the result with Android build tools.  ``--dry-run`` validates
the plan and prints the exact stages without requiring the external tools.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence


class PipelineError(RuntimeError):
    """A user-actionable pipeline validation or stage failure."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    path: Path | None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": str(self.path) if self.path else None,
                "exists": bool(self.path and self.path.is_file())}


@dataclass(frozen=True)
class PatchPlan:
    name: str
    version: str
    abi: str
    expected_sha256: str
    operations: tuple[dict[str, Any], ...]


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"patch operation {field} must be a non-empty relative path")
    raw = Path(value)
    if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
        raise PipelineError(f"patch operation {field} must stay inside the decoded/patch root: {value!r}")
    return raw


def _resolve_under(root: Path, relative: Path, *, field: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PipelineError(f"patch operation {field} escapes its root: {relative}") from exc
    return candidate


def load_patch_plan(path: Path, *, expected_sha256: str | None = None) -> PatchPlan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PipelineError(f"patch manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PipelineError(f"patch manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PipelineError("patch manifest root must be a JSON object")
    if raw.get("schemaVersion", 1) != 1:
        raise PipelineError(f"unsupported patch manifest schemaVersion: {raw.get('schemaVersion')!r}")
    name = raw.get("name", "ljjc-offline")
    version = raw.get("version", "unknown")
    abi = raw.get("abi", "universal")
    for field, value in (("name", name), ("version", version), ("abi", abi)):
        if not isinstance(value, str) or not value.strip():
            raise PipelineError(f"patch manifest {field} must be a non-empty string")
    digest = expected_sha256 or raw.get("expectedSha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest):
        raise PipelineError("an expected SHA-256 digest is required in --expected-sha256 or expectedSha256")
    operations = raw.get("operations", [])
    if not isinstance(operations, list):
        raise PipelineError("patch manifest operations must be an array")
    normalized: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise PipelineError(f"patch operation {index} must be an object")
        kind = operation.get("kind")
        if kind not in {"replace_file", "replace_text"}:
            raise PipelineError(f"patch operation {index} has unsupported kind: {kind!r}")
        target = _safe_relative(operation.get("target"), field=f"operations[{index}].target")
        item = dict(operation)
        item["target"] = target.as_posix()
        if kind == "replace_file":
            item["source"] = _safe_relative(operation.get("source"), field=f"operations[{index}].source").as_posix()
        else:
            find = operation.get("find")
            replacement = operation.get("replace", "")
            if not isinstance(find, str) or find == "":
                raise PipelineError(f"patch operation {index}.find must be a non-empty string")
            if not isinstance(replacement, str):
                raise PipelineError(f"patch operation {index}.replace must be a string")
            expected_count = operation.get("count", 1)
            if not isinstance(expected_count, int) or expected_count < 1:
                raise PipelineError(f"patch operation {index}.count must be a positive integer")
            item["count"] = expected_count
        normalized.append(item)
    return PatchPlan(name=name, version=version, abi=abi,
                     expected_sha256=digest.lower(), operations=tuple(normalized))


def _default_tool(name: str) -> Path | None:
    env_name = {"apktool": "APKTOOL", "zipalign": "ZIPALIGN", "apksigner": "APKSIGNER"}[name]
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured)
    sdk_env = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    sdk = Path(sdk_env) if sdk_env else Path(r"C:\Users\Administrator\AppData\Local\Android\Sdk")
    if name == "apktool":
        for candidate in (Path("apktool"), Path("apktool.bat")):
            found = shutil.which(str(candidate))
            if found:
                return Path(found)
        return None
    build_tools = sdk / "build-tools" / "37.0.0"
    candidate = build_tools / ("apksigner.bat" if name == "apksigner" else "zipalign.exe")
    return candidate if candidate.is_file() else None


def resolve_tools(paths: Mapping[str, str | Path | None] | None = None) -> tuple[ToolSpec, ...]:
    paths = paths or {}
    return tuple(
        ToolSpec(name, Path(paths[name]) if paths.get(name) else _default_tool(name))
        for name in ("apktool", "zipalign", "apksigner")
    )


def _command_text(command: Sequence[str | Path]) -> str:
    return " ".join(str(item) for item in command)


def _run_stage(stage: str, command: Sequence[str | Path]) -> None:
    executable = Path(str(command[0]))
    argv = [str(item) for item in command]
    # Windows batch entry points (apktool.bat/apksigner.bat) are not PE
    # executables. Invoke them through cmd.exe while keeping every argument
    # as a separate subprocess argument and avoiding shell interpolation.
    if os.name == "nt" and executable.suffix.casefold() in {".bat", ".cmd"}:
        argv = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", *argv]
    try:
        result = subprocess.run(argv, check=False,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PipelineError(f"{stage} could not start {_command_text(command)}: {exc}") from exc
    if result.returncode != 0:
        output = result.stdout.strip()
        detail = f"\n{output[-4000:]}" if output else ""
        raise PipelineError(f"{stage} failed with exit code {result.returncode}: {_command_text(command)}{detail}")


def apply_operations(plan: PatchPlan, *, decoded_root: Path, patch_root: Path) -> None:
    for index, operation in enumerate(plan.operations):
        target = _resolve_under(decoded_root, Path(operation["target"]), field=f"operations[{index}].target")
        kind = operation["kind"]
        if kind == "replace_file":
            source = _resolve_under(patch_root, Path(operation["source"]), field=f"operations[{index}].source")
            if not source.is_file():
                raise PipelineError(f"patch source does not exist: {source}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            continue
        if not target.is_file():
            raise PipelineError(f"replace_text target does not exist: {target}")
        try:
            original = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PipelineError(f"replace_text target is not UTF-8 text: {target}") from exc
        find = operation["find"]
        count = original.count(find)
        expected_count = int(operation["count"])
        if count != expected_count:
            raise PipelineError(f"replace_text expected {expected_count} occurrence(s) of {find!r} in {target}, found {count}")
        target.write_text(original.replace(find, operation.get("replace", ""), expected_count), encoding="utf-8", newline="")


def _validate_tools(tools: Iterable[ToolSpec]) -> None:
    missing = [spec.name for spec in tools if not spec.path or not spec.path.is_file()]
    if missing:
        paths = ", ".join(f"{spec.name}={spec.path or '<unset>'}" for spec in tools if spec.name in missing)
        raise PipelineError(f"required APK tools are missing: {paths}; pass explicit paths or set APKTOOL/ZIPALIGN/APKSIGNER")


def _output_name(plan: PatchPlan) -> str:
    safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in plan.name)
    safe_version = "".join(char if char.isalnum() or char in "._-" else "_" for char in plan.version)
    safe_abi = "".join(char if char.isalnum() or char in "._-" else "_" for char in plan.abi)
    return f"{safe_name}-{safe_version}-{safe_abi}.apk"


def build_apk(
    *,
    input_apk: Path,
    output_dir: Path,
    manifest: Path,
    tools: Mapping[str, str | Path | None] | None = None,
    expected_sha256: str | None = None,
    keystore: Path | None = None,
    key_alias: str | None = None,
    keystore_password_env: str = "LJJC_KEYSTORE_PASSWORD",
    work_dir: Path | None = None,
    dry_run: bool = False,
    keep_work: bool = False,
) -> dict[str, Any]:
    input_apk = input_apk.resolve()
    manifest = manifest.resolve()
    output_dir = output_dir.resolve()
    if not input_apk.is_file():
        raise PipelineError(f"input APK does not exist: {input_apk}")
    if input_apk.suffix.lower() != ".apk":
        raise PipelineError(f"input must be an APK file: {input_apk}")
    plan = load_patch_plan(manifest, expected_sha256=expected_sha256)
    actual_sha256 = sha256_file(input_apk)
    if actual_sha256 != plan.expected_sha256:
        raise PipelineError(f"input SHA-256 mismatch: expected {plan.expected_sha256}, got {actual_sha256}")
    tool_specs = resolve_tools(tools)
    output_path = output_dir / _output_name(plan)
    if output_path.resolve() == input_apk:
        raise PipelineError("output APK must not overwrite the input APK")
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "status": "dry-run" if dry_run else "planned",
        "input": str(input_apk),
        "inputSha256": actual_sha256,
        "manifest": str(manifest),
        "name": plan.name,
        "version": plan.version,
        "abi": plan.abi,
        "operations": list(plan.operations),
        "output": str(output_path),
        "tools": [spec.as_dict() for spec in tool_specs],
        "stages": ["verify-input", "decode-apktool", "apply-patches", "build-apktool", "zipalign", "sign", "verify-signature"],
    }
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / (output_path.stem + ".json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["report"] = str(report_path)
        return report

    _validate_tools(tool_specs)
    if not keystore or not keystore.is_file():
        raise PipelineError("a signing keystore is required for a real build; use --keystore")
    if not key_alias:
        raise PipelineError("a signing key alias is required for a real build; use --key-alias")
    if not os.environ.get(keystore_password_env):
        raise PipelineError(f"signing password is missing from environment variable {keystore_password_env}")
    output_dir.mkdir(parents=True, exist_ok=True)
    owned_work = work_dir is None
    if work_dir is None:
        work_path = Path(tempfile.mkdtemp(prefix="ljjc-apk-", dir=str(output_dir)))
    else:
        work_path = work_dir.resolve()
        if work_path.exists() and any(work_path.iterdir()):
            raise PipelineError(f"work directory must be empty: {work_path}")
        work_path.mkdir(parents=True, exist_ok=True)
    decoded = work_path / "decoded"
    unsigned = work_path / "unsigned.apk"
    aligned = work_path / "aligned.apk"
    try:
        apktool, zipalign, apksigner = (spec.path for spec in tool_specs)
        assert apktool and zipalign and apksigner
        _run_stage("apktool decode", [apktool, "d", "--force", "--output", decoded, input_apk])
        apply_operations(plan, decoded_root=decoded, patch_root=manifest.parent)
        _run_stage("apktool build", [apktool, "b", decoded, "--output", unsigned])
        _run_stage("zipalign", [zipalign, "-f", "-p", "4", unsigned, aligned])
        _run_stage("apksigner sign", [apksigner, "sign", "--ks", keystore,
                                       "--ks-key-alias", key_alias,
                                       "--ks-pass", f"env:{keystore_password_env}", aligned])
        _run_stage("apksigner verify", [apksigner, "verify", "--verbose", aligned])
        shutil.copyfile(aligned, output_path)
        report["status"] = "built"
        report["outputSha256"] = sha256_file(output_path)
        report_path = output_dir / (output_path.stem + ".json")
        report["report"] = str(report_path)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report
    finally:
        if owned_work and not keep_work:
            shutil.rmtree(work_path, ignore_errors=True)
        elif owned_work:
            report["workDir"] = str(work_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="read-only source APK")
    parser.add_argument("--manifest", type=Path, required=True, help="JSON patch manifest")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--apktool", type=Path)
    parser.add_argument("--zipalign", type=Path)
    parser.add_argument("--apksigner", type=Path)
    parser.add_argument("--keystore", type=Path)
    parser.add_argument("--key-alias")
    parser.add_argument("--keystore-password-env", default="LJJC_KEYSTORE_PASSWORD")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_apk(
            input_apk=args.input, output_dir=args.output_dir, manifest=args.manifest,
            tools={"apktool": args.apktool, "zipalign": args.zipalign, "apksigner": args.apksigner},
            expected_sha256=args.expected_sha256, keystore=args.keystore,
            key_alias=args.key_alias, keystore_password_env=args.keystore_password_env,
            work_dir=args.work_dir, dry_run=args.dry_run, keep_work=args.keep_work,
        )
    except PipelineError as exc:
        print(f"client-patch: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
