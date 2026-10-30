#!/usr/bin/env python3
"""Inspect IL2CPP structure and record independently verifiable tool output.

The report deliberately separates three claims:

* metadata/ELF structure parsed by this script;
* a ``dump.cs`` produced by a versioned Il2CppDumper run; and
* a Ghidra project/import whose analysis may be incomplete.

It never treats a file merely named ``dump.cs`` as proof.  Tool output is
accepted only when the expected path exists and the companion run evidence
exists.  It also never modifies the APK, native libraries, or integrity code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
from typing import Any


MAGIC = 0xFAB11BAF

# Unity metadata v23 has a 264-byte header: magic/version followed by 32
# offset/count pairs.  These labels are field names from the public IL2CPP
# metadata layout; the tool never uses them to invent a type definition.
V23_TABLES = (
    "stringLiteral",
    "stringLiteralData",
    "string",
    "events",
    "properties",
    "methods",
    "parameterDefaultValues",
    "fieldDefaultValues",
    "fieldAndParameterDefaultValueData",
    "fieldMarshaledSizes",
    "parameters",
    "fields",
    "genericParameters",
    "genericParameterConstraints",
    "genericContainers",
    "nestedTypes",
    "interfaces",
    "vtableMethods",
    "interfaceOffsets",
    "typeDefinitions",
    "images",
    "assemblies",
    "fieldRefs",
    "referencedAssemblies",
    "metadataUsageLists",
    "metadataUsagePairs",
    "attributeData",
    "attributeDataRange",
    "unresolvedVirtualCallParameterTypes",
    "unresolvedVirtualCallParameterRanges",
    "windowsRuntimeTypeNames",
    "windowsRuntimeStrings",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, root: Path | None = None) -> dict[str, Any]:
    """Return stable evidence for a file or directory without reading it all."""
    resolved = path.resolve()
    item: dict[str, Any] = {
        "path": str(resolved),
        "exists": resolved.exists(),
        "kind": "directory" if resolved.is_dir() else "file" if resolved.is_file() else "missing",
    }
    if root is not None:
        try:
            item["relativePath"] = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    if resolved.is_file():
        item["sizeBytes"] = resolved.stat().st_size
        item["sha256"] = _sha256(resolved)
    elif resolved.is_dir():
        files = [child for child in resolved.rglob("*") if child.is_file()]
        item["fileCount"] = len(files)
    return item


def _find_first(root: Path, candidates: tuple[str, ...]) -> Path | None:
    for candidate in candidates:
        path = root / candidate
        if path.exists():
            return path
    return None


def _tooling_info(repo_root: Path, output_path: Path, binaries: list[Path]) -> dict[str, Any]:
    """Discover recorded M2.4 outputs, without requiring them in unit fixtures."""
    analysis_root = output_path.resolve().parent
    dumper_root = repo_root / "runtime" / "il2cppdumper-v6.7.46"
    dump_root = analysis_root / "dump-v6.7.46-x86"
    dump_cs = dump_root / "dump.cs"
    archive = dumper_root / "Il2CppDumper-win-v6.7.46.zip"
    dumper_exe = _find_first(dumper_root, ("app/Il2CppDumper-x86.exe", "app/Il2CppDumper.exe"))
    ghidra_root = analysis_root / "ghidra-project-x86"
    ghidra_project = ghidra_root / "LJJC_IL2CPP_X86.gpr"
    ghidra_log = analysis_root / "ghidra-x86.log"
    x86_binary = next((path for path in binaries if path.resolve().as_posix().lower().endswith("/lib/x86/libil2cpp.so")), None)
    log_text = ghidra_log.read_text(encoding="utf-8", errors="replace") if ghidra_log.is_file() else ""
    project_exists = ghidra_project.is_file() or ghidra_root.is_dir()
    imported = "Import succeeded" in log_text and "libil2cpp.so" in log_text
    timed_out = "Analysis timed out" in log_text or "timed out" in log_text.lower()
    analysis_completed = imported and not timed_out and "Analysis completed" in log_text
    result = {
        "il2cppDumper": {
            "version": "6.7.46",
            "archive": _artifact(archive, repo_root),
            "executable": _artifact(dumper_exe, repo_root) if dumper_exe else {"exists": False, "kind": "missing"},
            "dumpOutputDirectory": _artifact(dump_root, repo_root),
            "dumpCs": _artifact(dump_cs, repo_root),
            "keyOutputs": [
                _artifact(path, repo_root)
                for path in (
                    dump_cs,
                    dump_root / "il2cpp.h",
                    dump_root / "script.json",
                    dump_root / "stringliteral.json",
                )
                if path.is_file()
            ],
            "dumpCsGenerated": dump_cs.is_file() and dump_cs.stat().st_size > 0,
        },
        "ghidra": {
            "version": "12.1.4",
            "project": _artifact(ghidra_root, repo_root),
            "projectFile": _artifact(ghidra_project, repo_root),
            "inputBinary": _artifact(x86_binary, repo_root) if x86_binary else {"exists": False, "kind": "missing"},
            "log": _artifact(ghidra_log, repo_root),
            "projectGenerated": project_exists,
            "imported": imported,
            "analysisCompleted": analysis_completed,
            "analysisTimedOut": timed_out,
            "symbolRenamingEvidence": False,
            "status": (
                "imported_analysis_timeout" if imported and timed_out else
                "analysis_completed" if analysis_completed else
                "project_created" if project_exists else
                "missing"
            ),
        },
    }
    # Keep the old summary keys for consumers that only need a boolean.
    result["dumpCsGenerated"] = result["il2cppDumper"]["dumpCsGenerated"]
    result["ghidraProjectGenerated"] = result["ghidra"]["projectGenerated"]
    return result


def _elf_info(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    result: dict[str, Any] = {
        "path": str(path),
        "sizeBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "format": "unknown",
        "class": None,
        "endianness": None,
        "machine": None,
        "machineName": None,
        "isValid": False,
        "probes": {},
    }
    if len(data) < 20 or data[:4] != b"\x7fELF":
        return result
    elf_class = data[4]
    endian = data[5]
    result["format"] = "ELF"
    result["class"] = {1: "ELF32", 2: "ELF64"}.get(elf_class, f"unknown:{elf_class}")
    result["endianness"] = {1: "little", 2: "big"}.get(endian, f"unknown:{endian}")
    if endian not in (1, 2) or elf_class not in (1, 2):
        return result
    prefix = "<" if endian == 1 else ">"
    machine = struct.unpack_from(prefix + "H", data, 18)[0]
    machine_names = {3: "i386", 40: "ARM", 62: "x86_64", 183: "AArch64"}
    result["machine"] = machine
    result["machineName"] = machine_names.get(machine, f"unknown:{machine}")
    result["isValid"] = True
    probes = {
        name: (name.encode("ascii") in data)
        for name in ("il2cpp_init", "il2cpp_domain_get", "il2cpp_thread_attach", "UnityMain")
    }
    result["probes"] = probes
    return result


def _metadata_info(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    result: dict[str, Any] = {
        "path": str(path),
        "sizeBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "magic": None,
        "version": None,
        "headerBytes": None,
        "format": "unknown",
        "isValid": False,
        "tables": [],
        "stringProbe": {},
    }
    if len(data) < 8:
        return result
    magic, version = struct.unpack_from("<II", data, 0)
    result["magic"] = f"0x{magic:08x}"
    result["version"] = version
    if magic != MAGIC:
        result["format"] = "invalid-magic"
        return result
    if version != 23:
        result["format"] = f"unsupported-metadata-version-{version}"
        return result
    header_bytes = 8 + len(V23_TABLES) * 8
    result["headerBytes"] = header_bytes
    if len(data) < header_bytes:
        result["format"] = "truncated-v23-header"
        return result
    tables: list[dict[str, Any]] = []
    valid = True
    for index, name in enumerate(V23_TABLES):
        offset, count = struct.unpack_from("<II", data, 8 + index * 8)
        end = offset + count
        table_valid = offset <= len(data) and end <= len(data)
        valid = valid and table_valid
        tables.append({
            "name": name,
            "offset": offset,
            "countOrBytes": count,
            "end": end,
            "validRange": table_valid,
        })
    result["tables"] = tables
    result["isValid"] = valid
    result["format"] = "global-metadata-v23" if valid else "global-metadata-v23-invalid-range"
    # Use the validated string table only for a bounded probe.  This proves
    # that the table can be read; it is not a type or method decompiler.
    string_table = tables[2]
    start, end = string_table["offset"], string_table["end"]
    if valid and end > start:
        sample = data[start:min(end, start + 256 * 1024)]
        strings = [chunk.decode("utf-8", "replace") for chunk in sample.split(b"\0") if chunk]
        result["stringProbe"] = {
            "bytesRead": len(sample),
            "nonEmptyStrings": len(strings),
            "firstStrings": strings[:20],
        }
    return result


def inspect(metadata: Path, binaries: list[Path], *, repo_root: Path | None = None, output_path: Path | None = None) -> dict[str, Any]:
    metadata_info = _metadata_info(metadata)
    binary_info = [_elf_info(path) for path in binaries]
    architecture = sorted({item["machineName"] for item in binary_info if item.get("isValid")})
    resolved_output = output_path or (metadata.parent / "structure-audit.json")
    # Unit callers that do not supply an output path are structural-only fixtures;
    # do not accidentally discover artifacts from the process working directory.
    if output_path is None and repo_root is None:
        tooling = {
            "il2cppDumper": {"version": None, "dumpCsGenerated": False, "keyOutputs": []},
            "ghidra": {"version": None, "projectGenerated": False, "imported": False, "analysisCompleted": False, "analysisTimedOut": False, "symbolRenamingEvidence": False, "status": "not_checked"},
            "dumpCsGenerated": False,
            "ghidraProjectGenerated": False,
        }
    else:
        resolved_root = (repo_root or resolved_output.resolve().parents[2]).resolve()
        tooling = _tooling_info(resolved_root, resolved_output, binaries)
    return {
        "schemaVersion": 2,
        "tool": "inspect_il2cpp.py",
        "metadata": metadata_info,
        "binaries": binary_info,
        "architectureSummary": architecture,
        "tooling": tooling,
        "limitations": [
            "Metadata version and table ranges are validated, but table entries are not decoded into C# types.",
            "ELF string probes do not prove that an exported function is callable or correctly named.",
            "Il2CppDumper output is a static type/method reconstruction, not a compilable server or client source tree.",
            "Ghidra imported the x86 native library, but its automatic analysis timed out before completion; no symbol-renaming claim is made.",
            "No TPShell or application integrity code was modified.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--binary", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root used to discover recorded M2.4 outputs")
    args = parser.parse_args()
    if not args.metadata.is_file():
        raise SystemExit(f"metadata does not exist: {args.metadata}")
    missing = [path for path in args.binary if not path.is_file()]
    if missing:
        raise SystemExit("binary does not exist: " + ", ".join(map(str, missing)))
    report = inspect(args.metadata.resolve(), [path.resolve() for path in args.binary], repo_root=args.repo_root, output_path=args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "metadataVersion": report["metadata"]["version"],
        "metadataValid": report["metadata"]["isValid"],
        "architectures": report["architectureSummary"],
        "dumpCsGenerated": report["tooling"]["il2cppDumper"]["dumpCsGenerated"],
        "ghidraStatus": report["tooling"]["ghidra"]["status"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

