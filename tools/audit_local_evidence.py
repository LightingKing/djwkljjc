#!/usr/bin/env python3
"""Audit the local acceptance evidence without contacting a remote service.

The repository has two deliberately different contract checks:

* ``analysis/route-coverage.json`` describes the handlers registered by the
  local game server (implementation coverage).
* ``dev/contract-smoke-report.json`` is produced by the isolated contract
  harness and checks that every known route fails safely when no handler is
  registered (route-safety coverage).

Those reports must not be merged into one recovery percentage.  This script
keeps both views explicit and verifies that their counts agree with the
current contract table.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON evidence: {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON evidence must be an object: {path}")
    return value


def _contract_count(path: Path) -> tuple[int, tuple[str, ...]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read contract table: {path}") from exc
    interfaces = document.get("interfaces", []) if isinstance(document, Mapping) else []
    names = tuple(
        str(item.get("interface"))
        for item in interfaces
        if isinstance(item, Mapping) and item.get("interface")
    )
    return len(names), names


def audit(
    *,
    contracts: Path,
    route_coverage: Path,
    contract_report: Path,
) -> dict[str, Any]:
    """Return a deterministic report for the two local evidence sources."""

    total, interface_names = _contract_count(contracts)
    coverage = _read_json(route_coverage)
    smoke = _read_json(contract_report)

    rows = coverage.get("interfaces", [])
    if not isinstance(rows, list):
        rows = []
    coverage_names = tuple(
        str(row.get("interface"))
        for row in rows
        if isinstance(row, Mapping) and row.get("interface")
    )
    implemented = sum(
        1 for row in rows
        if isinstance(row, Mapping) and row.get("status") == "implemented"
    )
    unimplemented = sum(
        1 for row in rows
        if isinstance(row, Mapping) and row.get("status") == "unimplemented"
    )
    duplicate_coverage = sorted({name for name in coverage_names if coverage_names.count(name) > 1})
    missing_coverage = sorted(set(interface_names) - set(coverage_names))
    extra_coverage = sorted(set(coverage_names) - set(interface_names))
    declared_total = coverage.get("totalInterfaces")
    declared_implemented = coverage.get("implementedInterfaces")
    declared_unimplemented = coverage.get("unimplementedInterfaces")

    smoke_rows = smoke.get("results", [])
    if not isinstance(smoke_rows, list):
        smoke_rows = []
    smoke_failed = [
        row for row in smoke_rows
        if not isinstance(row, Mapping) or row.get("passed") is not True
    ]
    unsafe_501 = []
    for row in smoke_rows:
        if not isinstance(row, Mapping) or row.get("status") != 501:
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping) or payload.get("error") != "route_not_implemented":
            unsafe_501.append(row.get("interface"))
    smoke_total = smoke.get("total")
    smoke_passed = smoke.get("passed")

    checks = {
        "contractCount": total > 0,
        "routeCoverageCount": declared_total == total == len(coverage_names),
        "routeCoverageNames": not duplicate_coverage and not missing_coverage and not extra_coverage,
        "routeCoverageStatusCounts": declared_implemented == implemented and declared_unimplemented == unimplemented,
        "contractSmokeCount": smoke_total == total == len(smoke_rows),
        "contractSmokePassed": smoke_passed == total and not smoke_failed,
        "contractSmoke501PayloadsSafe": not unsafe_501,
    }
    return {
        "schemaVersion": 1,
        "passed": all(checks.values()),
        "checks": checks,
        "contracts": {
            "path": str(contracts),
            "total": total,
            "interfaces": list(interface_names),
        },
        "implementationCoverage": {
            "path": str(route_coverage),
            "declaredTotal": declared_total,
            "declaredImplemented": declared_implemented,
            "declaredUnimplemented": declared_unimplemented,
            "implemented": implemented,
            "unimplemented": unimplemented,
            "coverage": coverage.get("coverage"),
            "missingInterfaces": missing_coverage,
            "extraInterfaces": extra_coverage,
            "duplicateInterfaces": duplicate_coverage,
        },
        "contractSmoke": {
            "path": str(contract_report),
            "mode": "unregistered_route_safety",
            "total": smoke_total,
            "passed": smoke_passed,
            "failedInterfaces": [
                row.get("interface") if isinstance(row, Mapping) else None
                for row in smoke_failed
            ],
            "unsafe501Interfaces": unsafe_501,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", type=Path, default=Path("contracts/api.yaml"))
    parser.add_argument("--coverage", type=Path, default=Path("analysis/route-coverage.json"))
    parser.add_argument("--contract-report", type=Path, default=Path("dev/contract-smoke-report.json"))
    parser.add_argument("--output", type=Path, default=Path("analysis/local-evidence-audit.json"))
    args = parser.parse_args(argv)
    payload = audit(
        contracts=args.contracts,
        route_coverage=args.coverage,
        contract_report=args.contract_report,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": payload["passed"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["audit", "main"]
