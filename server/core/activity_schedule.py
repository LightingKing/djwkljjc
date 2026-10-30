"""Deterministic local activity rotation for the offline service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading
from typing import Any

import yaml


class ActivitySchedule:
    """Read the checked-in local schedule and expose an admin-adjustable view."""

    def __init__(self, source: str | Path, *, state_path: str | Path | None = None, clock=None) -> None:
        self.source = Path(source).resolve()
        self.state_path = Path(state_path).resolve() if state_path is not None else None
        self.clock = clock or (lambda: datetime.now(timezone.utc).timestamp())
        self._lock = threading.RLock()
        document = yaml.safe_load(self.source.read_text(encoding="utf-8")) if self.source.is_file() else {}
        document = document if isinstance(document, dict) else {}
        cycle = document.get("cycle") if isinstance(document.get("cycle"), dict) else {}
        self.period_days = max(1, int(cycle.get("periodDays", 28)))
        anchor_text = str(cycle.get("anchor", "1970-01-01T00:00:00Z"))
        self._anchor = self._parse_datetime(anchor_text)
        phases = cycle.get("phases", [])
        if not phases and isinstance(document.get("activities"), list):
            phases = document.get("activities", [])
        self.phases = [dict(item) for item in phases if isinstance(item, dict)] if isinstance(phases, list) else []
        for item in self.phases:
            item.setdefault("enabled", True)
            item.setdefault("source", "local-config")
        self._load_state()

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        text = str(value).strip().replace("Z", "+00:00")
        current = datetime.fromisoformat(text)
        return (current if current.tzinfo is not None else current.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)

    def _now(self) -> datetime:
        value = self.clock()
        if isinstance(value, datetime):
            current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            return current.astimezone(timezone.utc)
        return datetime.fromtimestamp(float(value), timezone.utc)

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if payload.get("anchor") is not None:
                self._anchor = self._parse_datetime(str(payload.get("anchor")))
            activities = payload.get("activities")
            if isinstance(activities, list):
                self.phases = [dict(item) for item in activities if isinstance(item, dict)]
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

    @property
    def anchor(self) -> datetime:
        with self._lock:
            return self._anchor

    def set_anchor(self, value: str | datetime) -> datetime:
        current = value if isinstance(value, datetime) else self._parse_datetime(value)
        current = (current if current.tzinfo is not None else current.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            self._anchor = current
            self._save_state()
            return current

    @staticmethod
    def _activity_id(item: dict[str, Any]) -> str:
        value = item.get("id", item.get("activityId"))
        if value is None or not str(value).strip():
            raise ValueError("activity id is required")
        return str(value).strip()

    @classmethod
    def _validate_activity(cls, value: dict[str, Any]) -> dict[str, Any]:
        item = dict(value)
        item["id"] = cls._activity_id(item)
        if "dayStart" in item or "dayEnd" in item:
            try:
                start = int(item.get("dayStart", 0)); end = int(item.get("dayEnd", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("dayStart/dayEnd must be integers") from exc
            if start < 0 or end <= start:
                raise ValueError("dayEnd must be greater than dayStart")
        for key in ("start", "end"):
            if key in item and item[key] is not None:
                cls._parse_datetime(str(item[key]))
        item["enabled"] = bool(item.get("enabled", True))
        item.setdefault("source", "local-admin")
        return item

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"anchor": self._anchor.isoformat(), "activities": self.phases}, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.state_path)

    def activities(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self.phases]

    def upsert_activity(self, value: dict[str, Any]) -> dict[str, Any]:
        item = self._validate_activity(value)
        with self._lock:
            for index, current in enumerate(self.phases):
                if self._activity_id(current) == item["id"]:
                    self.phases[index] = item
                    self._save_state()
                    return dict(item)
            self.phases.append(item)
            self._save_state()
            return dict(item)

    def patch_activity(self, activity_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        key = str(activity_id).strip()
        with self._lock:
            for index, current in enumerate(self.phases):
                if self._activity_id(current) == key:
                    updated = dict(current); updated.update(changes); updated["id"] = key
                    updated = self._validate_activity(updated)
                    self.phases[index] = updated
                    self._save_state()
                    return dict(updated)
        return None

    def delete_activity(self, activity_id: str) -> bool:
        key = str(activity_id).strip()
        with self._lock:
            before = len(self.phases)
            self.phases = [item for item in self.phases if self._activity_id(item) != key]
            changed = len(self.phases) != before
            if changed:
                self._save_state()
            return changed

    def _active(self, item: dict[str, Any], now: datetime, cycle_day: int) -> bool:
        if not bool(item.get("enabled", True)):
            return False
        if item.get("start") is not None or item.get("end") is not None:
            try:
                start = self._parse_datetime(str(item.get("start"))) if item.get("start") is not None else self._anchor
                end = self._parse_datetime(str(item.get("end"))) if item.get("end") is not None else now + timedelta(seconds=1)
            except (TypeError, ValueError):
                return False
            return start <= now < end
        try:
            start = int(item.get("dayStart", 0)); end = int(item.get("dayEnd", self.period_days))
        except (TypeError, ValueError):
            return False
        return start <= cycle_day < end

    def snapshot(self, value: datetime | int | float | None = None) -> dict[str, Any]:
        now = self._now() if value is None else (
            value if isinstance(value, datetime) else datetime.fromtimestamp(float(value), timezone.utc)
        )
        now = (now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        with self._lock:
            elapsed = max(0, (now - self._anchor).total_seconds())
            day = int(elapsed // 86400) % self.period_days
            active: list[dict[str, Any]] = []
            all_activities: list[dict[str, Any]] = []
            for phase in self.phases:
                if self._active(phase, now, day):
                    item = dict(phase)
                    item["day"] = day
                    item["active"] = True
                    active.append(item)
                item = dict(phase)
                item["active"] = self._active(phase, now, day)
                all_activities.append(item)
            return {
                "now": now.isoformat(),
                "anchor": self._anchor.isoformat(),
                "periodDays": self.period_days,
                "cycleDay": day,
                "active": active,
                "activities": all_activities,
                "source": str(self.source),
            }


__all__ = ["ActivitySchedule"]
