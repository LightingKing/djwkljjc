"""Contract smoke tests and deterministic local traffic replay.

The contract table is an observation of the retired client's HTTP surface.  It
is useful even when most handlers are not implemented yet: every known route
can be probed and must return the explicit ``501 route_not_implemented``
response rather than hanging, leaking an exception, or silently becoming a
404.  This module keeps that probe data deterministic so it can run in CI and
can also be used as a small JSONL replay harness for captured requests.

The harness intentionally dispatches through :class:`RouteRegistry`; it does
not make network requests and it never mutates the source APK or a live game
account.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

# When invoked as ``python tools/contract_harness.py`` Python puts ``tools``
# (rather than the repository root) on sys.path.  Add the explicit project
# root only for this local script invocation; importing as a module needs no
# special handling.
try:
    from server.core.routes import RouteContract, RouteRegistry, RouteResponse
except ModuleNotFoundError as exc:  # pragma: no cover - only script invocation
    if exc.name != "server":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from server.core.routes import RouteContract, RouteRegistry, RouteResponse


_PARAM_RE = re.compile(
    r"<(?:(?P<converter>string|int|path):)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>"
)


class ReplayFormatError(ValueError):
    """Raised when a JSONL replay record is malformed."""


@dataclass(frozen=True)
class ContractCase:
    """One deterministic request generated from a contract entry."""

    interface: str
    method: str
    path: str
    request: Mapping[str, Any]
    expected_status: int = 501


@dataclass(frozen=True)
class ProbeResult:
    """The observable result for one smoke or replay case."""

    interface: str | None
    method: str
    path: str
    status: int
    expected_status: int | None
    passed: bool
    payload: Any


def _route_variants(route: str) -> tuple[str, ...]:
    variants = tuple(part.strip() for part in str(route).split(",") if part.strip())
    return variants or ("/",)


def _sample_value(field: Mapping[str, Any], interface: str) -> Any:
    """Return a stable, harmless value for a descriptor field."""

    field_type = str(field.get("type", "string")).lower()
    number = int(field.get("number", 0) or 0)
    name = str(field.get("name", f"field_{number}"))
    if field_type in {"bool", "boolean"}:
        return False
    if field_type in {"int32", "sint32", "sfixed32", "uint32", "fixed32", "enum", "int64", "sint64", "sfixed64", "uint64", "fixed64"}:
        return 0
    if field_type in {"float", "double"}:
        return 0.0
    if field_type in {"bytes", "byte_string"}:
        # JSON replay stays text-only.  An empty value is valid for optional
        # bytes and avoids ever putting a credential or APK data in a fixture.
        return ""
    if field_type == "message":
        return {}
    return f"sample-{interface.lower()}-{name}-{number}"


def sample_request(contract: RouteContract) -> dict[str, Any]:
    """Build a deterministic request object from ``requestFields``."""

    result: dict[str, Any] = {}
    for field in contract.request_fields:
        if not isinstance(field, Mapping):
            continue
        name = str(field.get("name", "")).strip()
        if name:
            result[name] = _sample_value(field, contract.interface)
    return result


def sample_path(contract: RouteContract, variant_index: int = 0) -> str:
    """Choose one route alias and fill path parameters with safe samples."""

    variants = _route_variants(contract.route)
    route = variants[variant_index % len(variants)]

    def replace(match: re.Match[str]) -> str:
        converter = match.group("converter") or "string"
        name = match.group("name")
        if converter == "int":
            return "0"
        if converter == "path":
            return "sample-path"
        # Keep values free of slashes because they are path segments.
        return f"sample-{name}"

    return _PARAM_RE.sub(replace, route)


def cases_from_contracts(
    contracts: Iterable[RouteContract],
    *,
    method: str = "POST",
    expected_status: int = 501,
) -> tuple[ContractCase, ...]:
    """Create one canonical probe case per interface.

    Only the first route alias is selected.  Some legacy descriptor entries
    intentionally share a route (for example two ``/test/`` interfaces); one
    case per interface keeps coverage accounting stable without pretending
    that a colliding route can identify both interfaces at runtime.
    """

    result: list[ContractCase] = []
    for contract in contracts:
        result.append(
            ContractCase(
                interface=contract.interface,
                method=str(method).upper(),
                path=sample_path(contract),
                request=sample_request(contract),
                expected_status=int(expected_status),
            )
        )
    return tuple(result)


def _payload_is_safe(response: RouteResponse) -> bool:
    """Reject stack traces and implementation details in probe responses."""

    if response.status != 501:
        return True
    if not isinstance(response.payload, Mapping):
        return False
    if response.payload.get("error") != "route_not_implemented":
        return False
    text = json.dumps(response.payload, ensure_ascii=False).lower()
    return "traceback" not in text and "exception" not in text


def run_cases(registry: RouteRegistry, cases: Iterable[ContractCase]) -> tuple[ProbeResult, ...]:
    """Dispatch cases and return results without raising handler exceptions."""

    results: list[ProbeResult] = []
    for case in cases:
        response = registry.dispatch(case.method, case.path, dict(case.request))
        passed = response.status == case.expected_status and _payload_is_safe(response)
        results.append(
            ProbeResult(
                interface=case.interface,
                method=case.method,
                path=case.path,
                status=response.status,
                expected_status=case.expected_status,
                passed=passed,
                payload=response.payload,
            )
        )
    return tuple(results)


def run_contract_smoke(
    registry: RouteRegistry,
    *,
    method: str = "POST",
    expected_status: int = 501,
) -> tuple[ProbeResult, ...]:
    """Probe every contract exactly once using deterministic test data."""

    return run_cases(
        registry,
        cases_from_contracts(registry.contracts, method=method, expected_status=expected_status),
    )


def write_replay(path: str | Path, cases: Iterable[ContractCase]) -> Path:
    """Write cases as UTF-8 JSONL and return the destination path."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for case in cases:
            stream.write(json.dumps(asdict(case), ensure_ascii=False, sort_keys=True) + "\n")
    return destination


def read_replay(path: str | Path) -> tuple[ContractCase, ...]:
    """Read and validate deterministic JSONL replay cases."""

    source = Path(path)
    cases: list[ContractCase] = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReplayFormatError(f"cannot read replay file: {source}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayFormatError(f"line {line_number}: invalid JSON") from exc
        if not isinstance(raw, Mapping):
            raise ReplayFormatError(f"line {line_number}: record must be an object")
        interface = str(raw.get("interface", "replay")).strip() or "replay"
        method = str(raw.get("method", "POST")).upper().strip()
        path_value = str(raw.get("path", "")).strip()
        request = raw.get("request", {})
        expected_status = raw.get("expected_status", raw.get("expectedStatus", 501))
        if not method or not path_value.startswith("/") or not isinstance(request, Mapping):
            raise ReplayFormatError(f"line {line_number}: method/path/request are invalid")
        try:
            expected_status = int(expected_status)
        except (TypeError, ValueError) as exc:
            raise ReplayFormatError(f"line {line_number}: expected_status must be an integer") from exc
        cases.append(
            ContractCase(interface, method, path_value, dict(request), expected_status)
        )
    return tuple(cases)


def run_replay(registry: RouteRegistry, path: str | Path) -> tuple[ProbeResult, ...]:
    """Replay JSONL records through a registry."""

    return run_cases(registry, read_replay(path))


def report(results: Iterable[ProbeResult]) -> dict[str, Any]:
    """Summarise smoke/replay results for CI and the management panel."""

    rows = list(results)
    passed = sum(1 for row in rows if row.passed)
    return {
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "ok": passed == len(rows),
        "results": [asdict(row) for row in rows],
    }


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts", default="contracts/api.yaml", help="contract YAML")
    parser.add_argument("--replay", help="JSONL replay file")
    parser.add_argument("--write-sample-replay", help="write generated contract probes to JSONL")
    parser.add_argument("--report", help="write a JSON report")
    args = parser.parse_args(argv)

    registry = RouteRegistry.from_yaml(args.contracts)
    if args.replay:
        results = run_replay(registry, args.replay)
    else:
        cases = cases_from_contracts(registry.contracts)
        if args.write_sample_replay:
            write_replay(args.write_sample_replay, cases)
        results = run_cases(registry, cases)
    payload = report(results)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        Path(args.report).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if payload["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - exercised through CLI smoke
    raise SystemExit(_main())


__all__ = [
    "ContractCase",
    "ProbeResult",
    "ReplayFormatError",
    "cases_from_contracts",
    "read_replay",
    "report",
    "run_cases",
    "run_contract_smoke",
    "run_replay",
    "sample_path",
    "sample_request",
    "write_replay",
]
