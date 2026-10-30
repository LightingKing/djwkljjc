"""Storage compatibility helpers for the local service."""

from .compat import (
    ensure_unified_store,
    resolve_unified_store_path,
    shared_store_lock,
)

__all__ = [
    "ensure_unified_store",
    "resolve_unified_store_path",
    "shared_store_lock",
]
