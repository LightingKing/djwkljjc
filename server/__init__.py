"""Staged M3 server package with legacy import compatibility.

The HTTP fixture implementation historically lived in the sibling
``server.py`` module.  M3 adds this package for ``server.core``; Python gives a
package precedence over a same-named sibling module, so old callers doing
``from server import FixtureServer`` need a lazy compatibility bridge.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


_LEGACY_PATH = Path(__file__).resolve().parent.parent / "server.py"
_legacy_module: ModuleType | None = None


def _load_legacy() -> ModuleType:
    global _legacy_module
    if _legacy_module is not None:
        return _legacy_module
    spec = importlib.util.spec_from_file_location("_ljjc_legacy_server", _LEGACY_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise ImportError(f"unable to load legacy server module: {_LEGACY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _legacy_module = module
    return module


__all__ = [
    "FixtureServer",
    "FixtureHandler",
    "JsonlLogger",
    "encode_raw_json",
    "encode_response",
    "decode_request_body",
    "build_parser",
    "serve",
    "_load_server_config",
    "_is_local_address",
    "_validate_local_network_config",
]


def __getattr__(name: str):
    if name in __all__:
        return getattr(_load_legacy(), name)
    raise AttributeError(name)
