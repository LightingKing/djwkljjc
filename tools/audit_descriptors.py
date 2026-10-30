#!/usr/bin/env python3
"""Audit protobuf descriptors against the recovered API contract table.

The descriptor set came from a retired client and contains a few malformed or
incomplete files.  This tool deliberately reports those gaps instead of
inventing fields.  It is useful for M2.6 acceptance and is safe to run against
the read-only APK analysis outputs.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

import yaml
from google.protobuf import (
    any_pb2,
    api_pb2,
    descriptor_pb2,
    descriptor_pool,
    duration_pb2,
    empty_pb2,
    field_mask_pb2,
    source_context_pb2,
    struct_pb2,
    timestamp_pb2,
    type_pb2,
    wrappers_pb2,
)


def _load_contracts(path: Path) -> list[Mapping[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping) or not isinstance(raw.get("interfaces"), list):
        raise ValueError("contract file must contain an interfaces list")
    return [item for item in raw["interfaces"] if isinstance(item, Mapping)]


def _load_descriptors(path: Path) -> tuple[
    list[descriptor_pb2.FileDescriptorProto],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    document = descriptor_pb2.FileDescriptorSet.FromString(path.read_bytes())
    valid: list[descriptor_pb2.FileDescriptorProto] = []
    skipped: list[dict[str, str]] = []
    repaired: list[dict[str, str]] = []
    for item in document.file:
        if isinstance(item.syntax, bytes) or item.syntax not in {"", "proto2", "proto3"}:
            # Some exports were produced through a Python protobuf helper that
            # accidentally serialized the syntax string as arbitrary bytes.
            # The descriptor body (including nested messages and field
            # numbers) remains intact.  Normalize only this metadata field so
            # the pool can resolve the recovered messages, and record the
            # repair explicitly in the report.  We never infer fields.
            original = (
                bytes(item.syntax).hex()
                if isinstance(item.syntax, bytes)
                else str(item.syntax).encode("utf-8", "backslashreplace").hex()
            )
            item.syntax = "proto3"
            repaired.append({
                "file": item.name,
                "reason": "malformed syntax field normalized to proto3; message fields were not changed",
                "originalHex": original,
            })
        valid.append(item)
    return valid, skipped, repaired


def _build_pool(files: Iterable[descriptor_pb2.FileDescriptorProto]) -> tuple[descriptor_pool.DescriptorPool, list[dict[str, str]]]:
    # Use a fresh pool so repeated audits do not depend on whichever modules a
    # caller imported earlier.  The recovered set references these standard
    # protobuf files, including timestamp.proto from account91.proto.
    pool = descriptor_pool.DescriptorPool()
    standard_modules = (
        descriptor_pb2, any_pb2, duration_pb2, empty_pb2, field_mask_pb2,
        source_context_pb2, struct_pb2, timestamp_pb2, type_pb2,
        wrappers_pb2, api_pb2,
    )
    for module in standard_modules:
        try:
            pool.AddSerializedFile(module.DESCRIPTOR.serialized_pb)
        except Exception:
            # A standard descriptor may already be provided as part of an
            # imported dependency.  The audit still has its local input set.
            pass
    pending = list(files)
    unresolved: list[dict[str, str]] = []
    while pending:
        next_pending: list[descriptor_pb2.FileDescriptorProto] = []
        progress = 0
        for file_descriptor in pending:
            try:
                pool.FindFileByName(file_descriptor.name)
                progress += 1
                continue
            except KeyError:
                pass
            try:
                pool.Add(file_descriptor)
                progress += 1
            except Exception as exc:  # descriptor errors are report data
                next_pending.append((file_descriptor, exc))
        if not next_pending:
            break
        if progress == 0:
            unresolved = [
                {
                    "file": item.name,
                    "error": str(error).encode("ascii", "replace").decode("ascii"),
                }
                for item, error in next_pending
            ]
            break
        pending = [item for item, _error in next_pending]
    return pool, unresolved


def _declared_messages(files: Iterable[descriptor_pb2.FileDescriptorProto]) -> set[str]:
    names: set[str] = set()
    for file_descriptor in files:
        prefix = f"{file_descriptor.package}." if file_descriptor.package else ""

        def visit(messages: Iterable[descriptor_pb2.DescriptorProto], parent: str) -> None:
            for message in messages:
                full_name = f"{parent}{message.name}"
                names.add(full_name)
                visit(message.nested_type, f"{full_name}.")

        visit(file_descriptor.message_type, prefix)
    return names


def _message_count(messages: Iterable[descriptor_pb2.DescriptorProto]) -> int:
    """Count top-level and nested messages without flattening their fields."""

    total = 0
    for message in messages:
        total += 1 + _message_count(message.nested_type)
    return total


def _descriptor_inventory(
    files: Iterable[descriptor_pb2.FileDescriptorProto],
    repaired_files: Iterable[Mapping[str, str]],
) -> list[dict[str, Any]]:
    repaired_names = {str(item.get("file")) for item in repaired_files}
    inventory: list[dict[str, Any]] = []
    for file_descriptor in files:
        serialized = file_descriptor.SerializeToString()
        inventory.append({
            "file": file_descriptor.name,
            "package": file_descriptor.package,
            "syntax": "proto3" if file_descriptor.syntax == "proto3" else (file_descriptor.syntax or "unspecified"),
            "syntaxStatus": "normalized_metadata" if file_descriptor.name in repaired_names else "as_extracted",
            "sha256": hashlib.sha256(serialized).hexdigest(),
            "dependencies": sorted(file_descriptor.dependency),
            "topLevelMessages": len(file_descriptor.message_type),
            "messageCountIncludingNested": _message_count(file_descriptor.message_type),
            "enumCount": len(file_descriptor.enum_type),
            "serviceCount": len(file_descriptor.service),
            "extensionCount": len(file_descriptor.extension),
        })
    return sorted(inventory, key=lambda item: str(item["file"]))


def _contract_types(interfaces: Iterable[Mapping[str, Any]]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    uses: dict[str, list[str]] = defaultdict(list)
    enum_uses: dict[str, list[str]] = defaultdict(list)
    for interface in interfaces:
        name = str(interface.get("interface", "unknown"))
        response_fields = interface.get("responseFields") or {}
        request_fields = [field for field in interface.get("requestFields") or [] if isinstance(field, Mapping)]
        for field_group in list(response_fields.values()) + [request_fields]:
            if not isinstance(field_group, list):
                continue
            for field in field_group:
                if not isinstance(field, Mapping):
                    continue
                type_name = str(field.get("type_name") or "")
                if type_name.startswith("."):
                    type_name = type_name[1:]
                if not type_name:
                    continue
                target = enum_uses if str(field.get("type", "")).lower() == "enum" else uses
                if name not in target[type_name]:
                    target[type_name].append(name)
    return dict(uses), dict(enum_uses)


_IL2CPP_CLASS_RE = re.compile(
    r"^\s*(?:public|internal|private|protected)\s+"
    r"(?:(?:sealed|static|abstract|partial)\s+)*class\s+"
    r"([A-Za-z_][A-Za-z0-9_.]*)\s*:.*\bIMessage<"
)
_IL2CPP_FIELD_NUMBER_RE = re.compile(r"^\s*public\s+const\s+int\s+(\w+)FieldNumber\s*=\s*(\d+)\s*;")
_IL2CPP_PROPERTY_RE = re.compile(
    r"^\s*public\s+([A-Za-z_][A-Za-z0-9_.<>?,\[\]]*)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\{\s*get;\s*(?:set;\s*)?\}"
)


def _normalise_il2cpp_message_name(type_name: str) -> str:
    """Map a protobuf nested name to Il2CppDumper's ``Types`` spelling."""

    value = str(type_name).lstrip(".")
    # Descriptor names may carry a package prefix while dump.cs uses the
    # generated C# type name directly.  Keep the full name for an exact match
    # and derive the suffix below when correlating evidence.
    return value


def _parse_il2cpp_dump(path: Path) -> dict[str, Any]:
    """Extract static IMessage field evidence without editing descriptors.

    Il2CppDumper's C# output preserves generated protobuf ``*FieldNumber``
    constants and public properties.  They are useful evidence for M2.6, but
    they are not a replacement descriptor: the report labels this section as
    static candidate evidence and never feeds it into the descriptor pool.
    """

    digest = hashlib.sha256()
    messages: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    depth = 0
    try:
        stream = path.open("rb")
    except OSError as exc:
        return {"path": str(path), "available": False, "error": str(exc), "messageCount": 0, "messages": []}
    with stream:
        for raw in stream:
            digest.update(raw)
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if current is None:
                match = _IL2CPP_CLASS_RE.match(line)
                if not match:
                    continue
                current = {
                    "className": match.group(1),
                    "fieldNumbers": {},
                    "properties": [],
                }
                # Class declarations in dump.cs put the opening brace on the
                # next line, so start at zero and let the next lines establish
                # the body depth.
                depth = line.count("{") - line.count("}")
                continue
            field_match = _IL2CPP_FIELD_NUMBER_RE.match(line)
            if field_match:
                field = field_match.group(1)
                current["fieldNumbers"][field] = int(field_match.group(2))
            property_match = _IL2CPP_PROPERTY_RE.match(line)
            if property_match:
                current["properties"].append({"type": property_match.group(1), "name": property_match.group(2)})
            depth += line.count("{") - line.count("}")
            if depth <= 0:
                if current["fieldNumbers"] or current["properties"]:
                    current["fieldNumbers"] = dict(sorted(current["fieldNumbers"].items(), key=lambda item: item[1]))
                    current["properties"].sort(key=lambda item: str(item["name"]))
                    messages.append(current)
                current = None
                depth = 0
    by_name = {str(item["className"]): item for item in messages}
    return {
        "path": str(path),
        "available": True,
        "sha256": digest.hexdigest(),
        "parser": "IMessageFieldEvidence-v1",
        "messageCount": len(by_name),
        "messages": [by_name[name] for name in sorted(by_name)],
    }


def _il2cpp_contract_evidence(
    uses: Mapping[str, list[str]], il2cpp: Mapping[str, Any] | None
) -> dict[str, Any]:
    if not il2cpp or not il2cpp.get("available"):
        return {"status": "unavailable", "matches": [], "unmatchedContractTypes": sorted(uses)}
    by_name = {str(item.get("className")): item for item in il2cpp.get("messages", []) if isinstance(item, Mapping)}
    matches: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for type_name in sorted(uses):
        normalized = _normalise_il2cpp_message_name(type_name)
        candidate_names = [normalized, normalized.replace(".Types.", ".")]
        prefix_match = re.search(r"(?:Api|Cs|Sc|DB|Db)[A-Z]", normalized)
        if prefix_match:
            short = normalized[prefix_match.start():]
            candidate_names.extend([short, short.replace(".Types.", ".")])
            parts = short.split(".")
            if len(parts) > 1:
                # Generated C# places every protobuf nested message below a
                # ``Types`` container.  Try all insertions for the shallow
                # names found in the contract table, while retaining the
                # original descriptor name as the exact-match candidate.
                positions = range(1, len(parts))
                for mask in range(1, 1 << len(tuple(positions))):
                    expanded = [parts[0]]
                    for index, part in enumerate(parts[1:]):
                        if mask & (1 << index):
                            expanded.append("Types")
                        expanded.append(part)
                    candidate_names.append(".".join(expanded))
        candidate = next((by_name[name] for name in candidate_names if name in by_name), None)
        if candidate is None:
            unmatched.append(type_name)
            continue
        matches.append({
            "contractType": type_name,
            "className": candidate["className"],
            "fieldNumbers": candidate.get("fieldNumbers", {}),
            "propertyCount": len(candidate.get("properties", [])),
            "usedBy": uses[type_name],
            "status": "static_candidate_only",
        })
    return {
        "status": "available_static_candidate_evidence",
        "matchedContractTypes": len(matches),
        "unmatchedContractTypes": unmatched,
        "matches": matches,
        "note": "Il2CppDumper output is evidence only; no fields are merged into descriptor_set.pb.",
    }


def _descriptor_field_evidence(
    pool: descriptor_pool.DescriptorPool,
    il2cpp_contract_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare recovered descriptor field numbers with static C# evidence.

    This is deliberately a read-only comparison.  Il2CppDumper field-number
    constants are useful for spotting an incomplete descriptor, but they are
    not sufficient to reconstruct field types or map-entry metadata.  The
    report therefore records number-set differences and never mutates the
    descriptor pool.
    """

    if il2cpp_contract_evidence.get("status") != "available_static_candidate_evidence":
        return {
            "status": "unavailable",
            "matched": 0,
            "matches": [],
            "note": "No static candidate evidence was supplied.",
        }

    rows: list[dict[str, Any]] = []
    for candidate in il2cpp_contract_evidence.get("matches", []):
        if not isinstance(candidate, Mapping):
            continue
        type_name = str(candidate.get("contractType") or "")
        if not type_name:
            continue
        try:
            descriptor = pool.FindMessageTypeByName(type_name)
        except KeyError:
            rows.append({
                "contractType": type_name,
                "status": "descriptor_unresolved",
                "descriptorFieldNumbers": [],
                "il2cppFieldNumbers": sorted(
                    int(number)
                    for number in (candidate.get("fieldNumbers") or {}).values()
                    if isinstance(number, int) or str(number).isdigit()
                ),
            })
            continue

        descriptor_fields = [
            {
                "name": field.name,
                "jsonName": field.json_name,
                "number": int(field.number),
                "type": field.type,
                "label": field.label,
            }
            for field in descriptor.fields
        ]
        descriptor_numbers = sorted(int(field["number"]) for field in descriptor_fields)
        static_numbers = sorted(
            int(number)
            for number in (candidate.get("fieldNumbers") or {}).values()
            if isinstance(number, int) or str(number).isdigit()
        )
        descriptor_set = set(descriptor_numbers)
        static_set = set(static_numbers)
        if descriptor_set == static_set:
            status = "field_numbers_match"
        elif descriptor_set - static_set and static_set - descriptor_set:
            status = "field_numbers_differ"
        elif descriptor_set - static_set:
            status = "descriptor_has_additional_fields"
        else:
            status = "static_has_additional_fields"
        rows.append({
            "contractType": type_name,
            "className": candidate.get("className"),
            "status": status,
            "descriptorFieldNumbers": descriptor_numbers,
            "il2cppFieldNumbers": static_numbers,
            "descriptorOnlyNumbers": sorted(descriptor_set - static_set),
            "il2cppOnlyNumbers": sorted(static_set - descriptor_set),
            "descriptorFields": sorted(descriptor_fields, key=lambda item: item["number"]),
        })
    return {
        "status": "available_static_candidate_evidence",
        "matched": len(rows),
        "matches": rows,
        "note": "Number-set comparison only; static evidence never supplies missing descriptor fields or types.",
    }


def audit(
    descriptor_path: Path,
    contracts_path: Path,
    il2cpp_dump_path: Path | None = None,
) -> dict[str, Any]:
    files, skipped_files, repaired_files = _load_descriptors(descriptor_path)
    pool, unresolved_files = _build_pool(files)
    inventory = _descriptor_inventory(files, repaired_files)
    declared = _declared_messages(files)
    uses, enum_uses = _contract_types(_load_contracts(contracts_path))
    il2cpp_evidence = None
    if il2cpp_dump_path is not None and il2cpp_dump_path.is_file():
        il2cpp_evidence = _parse_il2cpp_dump(il2cpp_dump_path)
    resolved: list[str] = []
    missing: list[str] = []
    declared_but_unresolved: list[str] = []
    for type_name in sorted(uses):
        try:
            pool.FindMessageTypeByName(type_name)
        except KeyError:
            missing.append(type_name)
            if type_name in declared:
                declared_but_unresolved.append(type_name)
        else:
            resolved.append(type_name)
    missing_enums: list[str] = []
    resolved_enums: list[str] = []
    for type_name in sorted(enum_uses):
        try:
            pool.FindEnumTypeByName(type_name)
        except KeyError:
            missing_enums.append(type_name)
        else:
            resolved_enums.append(type_name)
    il2cpp_contract = _il2cpp_contract_evidence(uses, il2cpp_evidence)
    return {
        "schemaVersion": 2,
        "descriptorSet": str(descriptor_path),
        "contracts": str(contracts_path),
        "fileCount": len(files),
        "skippedFiles": skipped_files,
        "repairedFiles": repaired_files,
        "unresolvedFiles": unresolved_files,
        "descriptorInventory": inventory,
        "descriptorTotals": {
            "messageCountIncludingNested": sum(item["messageCountIncludingNested"] for item in inventory),
            "enumCount": sum(item["enumCount"] for item in inventory),
            "serviceCount": sum(item["serviceCount"] for item in inventory),
            "dependencyEdges": sum(len(item["dependencies"]) for item in inventory),
        },
        "contractMessageTypes": {
            "total": len(uses),
            "resolved": len(resolved),
            "missing": len(missing),
            "resolvedNames": resolved,
            "missingNames": missing,
            "declaredButUnresolved": declared_but_unresolved,
            "usedBy": {name: uses[name] for name in sorted(uses)},
        },
        "contractEnumTypes": {
            "total": len(enum_uses),
            "resolved": len(resolved_enums),
            "missing": len(missing_enums),
            "resolvedNames": resolved_enums,
            "missingNames": missing_enums,
            "usedBy": {name: enum_uses[name] for name in sorted(enum_uses)},
        },
        "il2cppMessageEvidence": il2cpp_contract,
        "descriptorFieldEvidence": _descriptor_field_evidence(pool, il2cpp_contract),
        "il2cppDump": il2cpp_evidence,
        "notes": [
            "Missing types are reported for follow-up; no fields are fabricated.",
            "A declared-but-unresolved type is present in the recovered descriptor set but blocked by another malformed or missing dependency.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor-set", type=Path, default=Path("data/descriptor_set.pb"))
    parser.add_argument("--contracts", type=Path, default=Path("contracts/api.yaml"))
    parser.add_argument(
        "--il2cpp-dump",
        type=Path,
        default=None,
        help="optional Il2CppDumper dump.cs; emits static candidate evidence without changing descriptors",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.descriptor_set, args.contracts, args.il2cpp_dump)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **result["contractMessageTypes"], "enumTypes": result["contractEnumTypes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

