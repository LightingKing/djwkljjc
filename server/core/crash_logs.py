"""Local crash-report storage used as the offline Bugly replacement.

Reports are accepted only by the local service and are written below the
configured state directory.  Payloads are bounded and common credential-like
fields are redacted before they reach disk, so a client crash report cannot
turn the diagnostic endpoint into an accidental secret store.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid
from typing import Any, Mapping


_SECRET_KEY = re.compile(
    r"(?:authorization|access[_-]?token|refresh[_-]?token|session|cookie|password|passwd|secret|api[_-]?key|sign)\b",
    re.IGNORECASE,
)


def _safe_text(value: Any, *, limit: int = 16_384) -> str:
    text = str(value).replace("\x00", "")
    return text[:limit]


def _redact(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-compatible copy with secret-like keys masked."""

    if depth > 8:
        return "<depth-limit>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = _safe_text(raw_key, limit=128)
            result[key] = "<redacted>" if _SECRET_KEY.search(key) else _redact(raw_value, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_redact(item, depth=depth + 1) for item in value[:256]]
    if isinstance(value, tuple):
        return [_redact(item, depth=depth + 1) for item in value[:256]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _safe_text(value) if isinstance(value, str) else value
    return _safe_text(value)


class CrashLogStore:
    """Bounded, date-partitioned crash report store."""

    def __init__(self, root: str | Path, *, clock=None, max_bytes: int = 512 * 1024) -> None:
        self.root = Path(root).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.RLock()

    def _now(self) -> datetime:
        value = self.clock()
        if isinstance(value, datetime):
            current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return current.astimezone(timezone.utc)
        return datetime.fromtimestamp(float(value), timezone.utc)

    def write(
        self,
        body: bytes,
        *,
        content_type: str | None = None,
        path: str | None = None,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        """Persist one report and return a safe index record."""

        raw = bytes(body or b"")
        if len(raw) > self.max_bytes:
            raise ValueError("crash report exceeds size limit")
        digest = hashlib.sha256(raw).hexdigest()
        current = self._now()
        day_dir = self.root / current.strftime("%Y-%m-%d")
        report_id = f"{current.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}"
        record: dict[str, Any] = {
            "id": report_id,
            "receivedAt": current.isoformat(),
            "sha256": digest,
            "bytes": len(raw),
            "contentType": _safe_text(content_type or "application/octet-stream", limit=128),
            "path": _safe_text(path or "", limit=256),
        }
        if account_id is not None:
            record["accountId"] = int(account_id)
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded = ""
        if decoded:
            try:
                parsed = json.loads(decoded)
            except json.JSONDecodeError:
                record["text"] = _safe_text(decoded, limit=self.max_bytes)
            else:
                record["payload"] = _redact(parsed)
        else:
            # Keep binary reports searchable without duplicating an arbitrary
            # binary blob in a JSON file.
            record["binary"] = True
        with self._lock:
            day_dir.mkdir(parents=True, exist_ok=True)
            target = day_dir / f"{report_id}.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            temporary.replace(target)
        return {key: value for key, value in record.items() if key not in {"payload", "text"}}

    def list_recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return newest report index records without payload bodies."""

        limit = max(1, min(int(limit), 500))
        candidates = sorted(self.root.glob("*/*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        result: list[dict[str, Any]] = []
        with self._lock:
            for path in candidates[:limit]:
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    result.append({key: item for key, item in value.items() if key not in {"payload", "text"}})
        return result

    def count(self) -> int:
        with self._lock:
            return sum(1 for _ in self.root.glob("*/*.json")) if self.root.is_dir() else 0


__all__ = ["CrashLogStore"]
