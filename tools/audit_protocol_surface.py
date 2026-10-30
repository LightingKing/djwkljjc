#!/usr/bin/env python3
"""Audit the observed HTTP and WebSocket protocol surface.

This report deliberately keeps discovery separate from implementation.  The
IL2CPP type-name dump contains helper classes as well as protocol messages,
while the descriptor map only contains the HTTP interfaces extracted so far.
The output therefore records raw counts, evidence-backed subsets, and the
remaining gaps instead of presenting a single inflated coverage number.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable


DEFAULT_TYPE_NAMES = Path(r"I:/workspace/mobilegame/analysis/ljjc-local/bootstrap-static/il2cpp-v23-type-names.json")
DEFAULT_INTERFACE_MAP = Path(r"I:/workspace/mobilegame/analysis/ljjc-local/protocols/interface-map.json")
DEFAULT_EVIDENCE_ROOT = Path(r"I:/workspace/mobilegame/analysis/ljjc-local")

_MESSAGE_RE = re.compile(r"\b(?:Cs|Sc)[A-Za-z][A-Za-z0-9_]*\b")

# IL2CPP type names share the ``Sc`` prefix with many ordinary Unity/.NET
# classes (Scene*, Scriptable*, Scroll*, ...).  Keep the raw prefix counts for
# discovery, but exclude these known namespace collisions from the protocol
# candidate set so the gap report does not call them missing messages.
_NON_PROTOCOL_PREFIXES = (
    "Scene",
    "Scan",
    "Scaling",
    "Schema",
    "Score",
    "Screen",
    "Script",
    "Scheduler",
    "Scroll",
)
_NON_PROTOCOL_NAMES = {"CspParameters", "CspProviderFlags"}


def _is_protocol_type(name: str) -> bool:
    return (
        bool(re.match(r"^(?:Cs|Sc)[A-Z]", name))
        and name not in _NON_PROTOCOL_NAMES
        and not name.startswith(_NON_PROTOCOL_PREFIXES)
    )


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _type_names(document: Any) -> list[str]:
    values = document.get("types", []) if isinstance(document, dict) else []
    return [str(item.get("name", "")) for item in values if isinstance(item, dict) and item.get("name")]


def _interface_names(document: Any) -> list[str]:
    values = document.get("interfaces", []) if isinstance(document, dict) else []
    return [str(item.get("interface", "")) for item in values if isinstance(item, dict) and item.get("interface")]


def _evidence_files(root: Path) -> list[Path]:
    """Return files that are eligible to prove runtime protocol traffic.

    Static descriptor inventories and prose notes often contain every message
    name in the client.  Counting those files as runtime evidence makes the
    report claim observations that never happened.  Runtime captures in this
    workspace are JSON Lines (or newline-delimited JSON), so the default
    evidence boundary is deliberately limited to those formats.
    """
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jsonl", ".ndjson"}
    )


def _evidence_names(root: Path) -> tuple[set[str], list[str]]:
    names: set[str] = set()
    files: list[str] = []
    for path in _evidence_files(root):
        # The static IL2CPP type-name dump is an input to discovery, not
        # runtime evidence.  Counting it here would incorrectly mark every
        # candidate as observed on the live protocol.
        if "bootstrap-static" in path.parts or path.name in {"interface-map.json", "interfaces.json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        files.append(str(path))
        names.update(name for name in _MESSAGE_RE.findall(text) if _is_protocol_type(name))
    return names, files


def audit(type_names_path: Path, interface_map_path: Path, evidence_root: Path) -> dict[str, Any]:
    type_names = _type_names(_load_json(type_names_path))
    interface_names = _interface_names(_load_json(interface_map_path))
    api_types = sorted({name for name in type_names if name.startswith("Api")})
    cs_types_raw = sorted({name for name in type_names if name.startswith("Cs")})
    sc_types_raw = sorted({name for name in type_names if name.startswith("Sc")})
    cs_types = sorted({name for name in cs_types_raw if _is_protocol_type(name)})
    sc_types = sorted({name for name in sc_types_raw if _is_protocol_type(name)})
    # Api and ApiReflection are IL2CPP/runtime helper names, not HTTP
    # endpoints.  Keep them visible in raw counts but exclude them from the
    # conservative candidate count used for the gap report.
    api_helpers = sorted(set(api_types) & {"Api", "ApiReflection"})
    api_candidates = sorted(set(api_types) - set(api_helpers))
    websocket_candidates = sorted(set(cs_types + sc_types))
    evidence_set, evidence_files = _evidence_names(evidence_root)
    evidence = sorted(evidence_set)
    evidence_set = set(evidence)
    observed_ws = sorted(set(websocket_candidates) & evidence_set)
    return {
        "schemaVersion": 1,
        "sources": {
            "typeNames": str(type_names_path),
            "interfaceMap": str(interface_map_path),
            "runtimeEvidenceRoot": str(evidence_root),
            "runtimeEvidenceFiles": evidence_files,
        },
        "counts": {
            "il2cppTypeNames": len(type_names),
            "apiTypesRaw": len(api_types),
            "apiHelpersExcluded": len(api_helpers),
            "apiCandidates": len(api_candidates),
            "httpDescriptorInterfaces": len(interface_names),
            "csTypesRaw": len(cs_types_raw),
            "scTypesRaw": len(sc_types_raw),
            "protocolTypeCandidates": len(websocket_candidates),
            "nonProtocolPrefixCollisions": len(set(cs_types_raw + sc_types_raw) - set(websocket_candidates)),
            "websocketCandidates": len(websocket_candidates),
            "runtimeWebsocketEvidence": len(observed_ws),
        },
        "http": {
            "descriptorInterfaces": sorted(set(interface_names)),
            "apiCandidatesWithoutHttpDescriptor": sorted(set(api_candidates) - set(interface_names)),
            "descriptorInterfacesWithoutMatchingApiType": sorted(set(interface_names) - set(api_types)),
        },
        "websocket": {
            "csTypes": cs_types,
            "scTypes": sc_types,
            "rawPrefixTypes": sorted(set(cs_types_raw + sc_types_raw)),
            "excludedPrefixCollisions": sorted(set(cs_types_raw + sc_types_raw) - set(websocket_candidates)),
            "runtimeEvidence": observed_ws,
            "candidatesWithoutRuntimeEvidence": sorted(set(websocket_candidates) - evidence_set),
        },
        "status": {
            "discovery": "complete_for_supplied_artifacts",
            "httpImplementation": "tracked_by_local_route_registry",
            "websocketImplementation": "partial_local_compatibility_subset",
            "officialRecovery": "not_claimed",
        },
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type-names", type=Path, default=DEFAULT_TYPE_NAMES)
    parser.add_argument("--interface-map", type=Path, default=DEFAULT_INTERFACE_MAP)
    parser.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    for path in (args.type_names, args.interface_map, args.evidence_root):
        if not path.exists():
            raise SystemExit(f"missing audit input: {path}")
    result = audit(args.type_names, args.interface_map, args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["audit"]
