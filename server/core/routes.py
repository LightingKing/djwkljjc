"""Contract-driven route registry for the staged M3 server.

The legacy HTTP handler is intentionally left untouched while this registry
provides the common route table used by new feature modules.  It can load the
current ``contracts/api.yaml`` and gives every known-but-unimplemented route a
stable 501 response plus counters for the coverage report.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
import re
from threading import RLock
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

import yaml


class RouteContractError(ValueError):
    """Raised when a contract file or route registration is malformed."""


@dataclass(frozen=True)
class RouteContract:
    interface: str
    route: str
    view: str | None = None
    file: str | None = None
    description: str | None = None
    request_fields: tuple[Mapping[str, Any], ...] = ()
    response_fields: Mapping[str, Any] = field(default_factory=dict)
    implementation_status: str = "observed_unverified"
    source: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RouteContract":
        interface = str(value.get("interface", "")).strip()
        route = str(value.get("route", "")).strip()
        if not interface or not route.startswith("/"):
            raise RouteContractError("each interface needs a name and absolute route")
        request_fields = value.get("requestFields") or ()
        response_fields = value.get("responseFields") or {}
        if not isinstance(request_fields, (list, tuple)):
            raise RouteContractError(f"{interface}: requestFields must be a list")
        if not isinstance(response_fields, Mapping):
            raise RouteContractError(f"{interface}: responseFields must be a mapping")
        return cls(
            interface=interface,
            route=route,
            view=value.get("view"),
            file=value.get("file"),
            description=value.get("description"),
            request_fields=tuple(request_fields),
            response_fields=dict(response_fields),
            implementation_status=str(value.get("implementationStatus", "observed_unverified")),
            source=value.get("source"),
        )


@dataclass(frozen=True)
class RouteResponse:
    status: int
    payload: Any
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass
class _RouteEntry:
    contract: RouteContract | None
    method: str
    route: str
    handler: Callable[[Any, Mapping[str, str]], Any] | None = None
    pattern: re.Pattern[str] = field(init=False, repr=False)
    parameter_names: tuple[str, ...] = field(init=False, repr=False)
    calls: int = 0
    errors: int = 0

    def __post_init__(self) -> None:
        self.method = self.method.upper()
        self.pattern, self.parameter_names = _compile_route(self.route)


_PARAM_RE = re.compile(r"<(?:(?P<converter>string|int|path):)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)>")


def _normalise_path(path: str) -> str:
    parsed = urlsplit(str(path))
    value = parsed.path or "/"
    if not value.startswith("/"):
        value = "/" + value
    if len(value) > 1:
        value = value.rstrip("/")
    return value or "/"


def _route_variants(route: str) -> tuple[str, ...]:
    """Expand comma-separated aliases emitted by the contract extractor."""
    variants = tuple(_normalise_path(part.strip()) for part in str(route).split(",") if part.strip())
    return variants or ("/",)


def _compile_route(route: str) -> tuple[re.Pattern[str], tuple[str, ...]]:
    """Compile a Flask-style contract route into a safe full-match regex.

    Capture groups are positional instead of named because the descriptor
    table contains a few legacy routes that repeat the same placeholder name.
    ``parameter_names`` preserves the names expected by handlers.
    """
    normalised = _normalise_path(route)
    output: list[str] = []
    names: list[str] = []
    cursor = 0
    for match in _PARAM_RE.finditer(normalised):
        output.append(re.escape(normalised[cursor:match.start()]))
        converter = match.group("converter") or "string"
        if converter == "int":
            expression = r"([0-9]+)"
        elif converter == "path":
            expression = r"(.+)"
        else:
            expression = r"([^/]+)"
        output.append(expression)
        names.append(match.group("name"))
        cursor = match.end()
    output.append(re.escape(normalised[cursor:]))
    return re.compile("^" + "".join(output) + "$") , tuple(names)


def _normalise_response(value: Any) -> RouteResponse:
    if isinstance(value, RouteResponse):
        return value
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], int):
        return RouteResponse(int(value[0]), value[1])
    return RouteResponse(200, value)


class RouteRegistry:
    """Load contracts, register handlers, and dispatch known routes.

    A registry is deliberately independent from the legacy ``server.py``
    handler. New API modules can use it directly or adapt its ``dispatch``
    result to their HTTP/protobuf encoder.
    """

    def __init__(self, contracts: Iterable[RouteContract] = ()) -> None:
        self._contracts: dict[str, RouteContract] = {}
        self._entries: list[_RouteEntry] = []
        self._lock = RLock()
        self._unimplemented_calls: Counter[str] = Counter()
        self._not_found_calls = 0
        for contract in contracts:
            self.add_contract(contract)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "RouteRegistry":
        source = Path(path)
        try:
            raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RouteContractError(f"cannot read contract file: {source}") from exc
        except yaml.YAMLError as exc:
            raise RouteContractError(f"invalid contract YAML: {source}") from exc
        if not isinstance(raw, Mapping) or not isinstance(raw.get("interfaces"), list):
            raise RouteContractError("contract file must contain an interfaces list")
        try:
            contracts = [RouteContract.from_mapping(item) for item in raw["interfaces"]]
        except (TypeError, AttributeError) as exc:
            raise RouteContractError("interface entries must be mappings") from exc
        return cls(contracts)

    @property
    def contracts(self) -> tuple[RouteContract, ...]:
        return tuple(self._contracts.values())

    def add_contract(self, contract: RouteContract) -> None:
        if contract.interface in self._contracts:
            raise RouteContractError(f"duplicate interface: {contract.interface}")
        self._contracts[contract.interface] = contract

    def contract(self, interface: str) -> RouteContract:
        try:
            return self._contracts[interface]
        except KeyError as exc:
            raise RouteContractError(f"unknown interface: {interface}") from exc

    def register(
        self,
        interface: str,
        handler: Callable[[Any, Mapping[str, str]], Any],
        *,
        method: str = "POST",
        replace: bool = False,
    ) -> None:
        contract = self.contract(interface)
        for index, route in enumerate(_route_variants(contract.route)):
            self.register_path(route, handler, method=method, interface=interface,
                               replace=replace if index == 0 else True)

    def register_path(
        self,
        route: str,
        handler: Callable[[Any, Mapping[str, str]], Any],
        *,
        method: str = "POST",
        interface: str | None = None,
        replace: bool = False,
    ) -> None:
        if not callable(handler):
            raise RouteContractError("route handler must be callable")
        contract = self._contracts.get(interface) if interface else None
        if interface and contract is None:
            raise RouteContractError(f"unknown interface: {interface}")
        paths = _route_variants(route)
        method = method.upper()
        with self._lock:
            for path in paths:
                existing = [entry for entry in self._entries if entry.route == path and entry.method == method]
                if existing and not replace:
                    raise RouteContractError(f"route already registered: {method} {path}")
                if existing:
                    self._entries = [entry for entry in self._entries if not (entry.route == path and entry.method == method)]
                self._entries.append(_RouteEntry(contract, method, path, handler))

    def dispatch(self, method: str, path: str, request: Any = None) -> RouteResponse:
        method = str(method).upper()
        normalised = _normalise_path(path)
        with self._lock:
            # Prefer a registered handler, then fall back to the first matching
            # contract so duplicate descriptor routes remain deterministic.
            for entry in self._entries:
                if entry.method != method:
                    continue
                match = entry.pattern.fullmatch(normalised)
                if not match:
                    continue
                entry.calls += 1
                params = {name: value for name, value in zip(entry.parameter_names, match.groups())}
                try:
                    return _normalise_response(entry.handler(request, params)) if entry.handler else self._not_implemented(entry.contract)
                except Exception:
                    entry.errors += 1
                    return RouteResponse(500, {"error": "route_handler_error", "interface": entry.contract.interface if entry.contract else None})
            matches = [contract for contract in self._contracts.values()
                       if any(_compile_route(route)[0].fullmatch(normalised)
                              for route in _route_variants(contract.route))]
            if matches:
                contract = matches[0]
                self._unimplemented_calls[contract.interface] += 1
                return self._not_implemented(contract)
            self._not_found_calls += 1
            return RouteResponse(404, {"error": "route_not_found", "method": method, "path": normalised})

    def has_handler(self, method: str, path: str) -> bool:
        """Return whether a concrete handler is registered for a request."""

        method = str(method).upper()
        normalised = _normalise_path(path)
        with self._lock:
            return any(
                entry.method == method and entry.handler is not None and entry.pattern.fullmatch(normalised)
                for entry in self._entries
            )

    def _not_implemented(self, contract: RouteContract | None) -> RouteResponse:
        payload: dict[str, Any] = {"error": "route_not_implemented"}
        if contract is not None:
            payload.update({"interface": contract.interface, "path": contract.route})
        return RouteResponse(501, payload)

    def coverage_report(self) -> dict[str, Any]:
        with self._lock:
            registered = {entry.contract.interface for entry in self._entries if entry.contract is not None and entry.handler is not None}
            interfaces = []
            for contract in self._contracts.values():
                entries = [entry for entry in self._entries if entry.contract is contract]
                calls = sum(entry.calls for entry in entries)
                errors = sum(entry.errors for entry in entries)
                interfaces.append({
                    "interface": contract.interface,
                    "route": contract.route,
                    "status": "implemented" if contract.interface in registered else "unimplemented",
                    "calls": calls,
                    "errors": errors,
                })
            total = len(self._contracts)
            implemented = len(registered)
            return {
                "totalInterfaces": total,
                "implementedInterfaces": implemented,
                "unimplementedInterfaces": total - implemented,
                "coverage": (implemented / total) if total else 1.0,
                "registeredRoutes": len(self._entries),
                "unimplementedCalls": dict(self._unimplemented_calls),
                "notFoundCalls": self._not_found_calls,
                "interfaces": interfaces,
            }

    def route_table(self) -> list[dict[str, Any]]:
        """Return a serialisable route table for diagnostics/admin pages."""
        with self._lock:
            return [
                {
                    "method": entry.method,
                    "route": entry.route,
                    "interface": entry.contract.interface if entry.contract else None,
                    "implemented": entry.handler is not None,
                }
                for entry in self._entries
            ]


__all__ = ["RouteContract", "RouteContractError", "RouteRegistry", "RouteResponse"]



