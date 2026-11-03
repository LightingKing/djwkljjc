"""TLS certificate inspection used by the local service health endpoints.

The runtime deliberately uses the Python standard library here.  Certificate
private keys are never read; only the public certificate's ``notAfter`` field
is decoded so startup diagnostics cannot accidentally expose key material.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import ssl
from typing import Any


CERTIFICATE_WARNING_DAYS = 30


def inspect_certificate_expiry(
    certificate_path: str | Path,
    *,
    now: datetime | None = None,
    warning_days: int = CERTIFICATE_WARNING_DAYS,
) -> dict[str, Any]:
    """Return safe, JSON-ready expiry information for a PEM certificate.

    ``ssl._ssl._test_decode_cert`` is the standard library's PEM decoder and
    does not require loading a private key.  A malformed or unreadable file is
    reported as ``status=error`` so startup can warn without hiding the
    problem behind a generic ``/health`` response.
    """

    path = Path(certificate_path)
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)
    try:
        decoded = ssl._ssl._test_decode_cert(str(path))
        raw_not_after = str(decoded["notAfter"])
        # OpenSSL's decoder emits e.g. ``Jan 25 05:22:20 2027 GMT``.
        not_after = datetime.strptime(raw_not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return {
            "status": "error",
            "path": str(path),
            "error": type(exc).__name__,
            "checkedAt": checked_at.isoformat(),
        }

    remaining = not_after - checked_at
    warning = remaining < timedelta(days=max(0, int(warning_days)))
    status = "expired" if remaining.total_seconds() < 0 else ("warning" if warning else "ok")
    return {
        "status": status,
        "path": str(path),
        "notAfter": not_after.isoformat(),
        "checkedAt": checked_at.isoformat(),
        "daysRemaining": remaining.total_seconds() / 86400.0,
        "warning": warning,
    }
