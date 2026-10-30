"""Configuration-backed dungeon metadata for the local compatibility server.

``data/config/ChapterInfo.json`` and ``data/config/DungeonReward.json`` are
regenerated from the complete original client tables
(``data/client-tables/<Table>.json``, exported from
``analysis/lua-decompiled/xlsx__<table>.lua`` by
``tools/export_client_tables.py``): 35 chapter rows (story, normal, training
and challenge chapters) and 683 stage rows.  The loader still exposes the
completeness metadata so an older partial runtime capture is reported as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class DungeonConfig:
    chapter_rows: tuple[Mapping[str, Any], ...]
    reward_rows: tuple[Mapping[str, Any], ...]
    chapter_observed_count: int
    reward_observed_count: int
    chapter_source: str
    reward_source: str
    fallback_used: bool = False

    @property
    def chapter_complete(self) -> bool:
        return self.chapter_observed_count <= len(self.chapter_rows)

    @property
    def reward_complete(self) -> bool:
        return self.reward_observed_count <= len(self.reward_rows)

    def chapter_dungeon_ids(self, chapter_type: int) -> tuple[int, ...]:
        """Sorted stage ids of every ChapterInfo row of ``chapter_type``.

        ChapterType: 1 story (plot), 2 normal, 6 training, 11 challenge.
        """
        values: set[int] = set()
        for row in self.chapter_rows:
            if int(row.get("type", 0) or 0) != int(chapter_type):
                continue
            for value in row.get("dungeonId", []) or []:
                try:
                    values.add(int(value))
                except (TypeError, ValueError):
                    continue
        return tuple(sorted(values))

    @property
    def all_normal_ids(self) -> tuple[int, ...]:
        return self.chapter_dungeon_ids(2)

    @property
    def training_ids(self) -> tuple[int, ...]:
        return self.chapter_dungeon_ids(6)

    @property
    def challenge_ids(self) -> tuple[int, ...]:
        return self.chapter_dungeon_ids(11)

    @property
    def supported_normal_ids(self) -> tuple[int, ...]:
        """Return the stage ids of the first normal chapter.

        Kept as a compatibility marker for callers that want the verified
        tutorial chapter; gameplay uses every chapter (``all_normal_ids``).
        """
        normal_rows = [row for row in self.chapter_rows if int(row.get("type", 0) or 0) == 2]
        if normal_rows:
            first = min(normal_rows, key=lambda row: int(row.get("chapterIndex", row.get("chapterId", 1)) or 1))
            ids = tuple(sorted({int(value) for value in first.get("dungeonId", []) or []}))
            if ids:
                return ids
        return (1, 2, 3, 4, 5, 6, 7, 8)

    @property
    def story_ids(self) -> tuple[int, ...]:
        """Stage ids of the story (plot) chapters 1001+ (2011, 2012, ...)."""
        return self.chapter_dungeon_ids(1) or (2011, 2012, 2013)

    @cached_property
    def _reward_index(self) -> dict[int, Mapping[str, Any]]:
        index: dict[int, Mapping[str, Any]] = {}
        for row in self.reward_rows:
            try:
                index.setdefault(int(row.get("dungeonId")), row)
            except (TypeError, ValueError):
                continue
        return index

    def reward_row(self, dungeon_id: int) -> Mapping[str, Any] | None:
        try:
            return self._reward_index.get(int(dungeon_id))
        except (TypeError, ValueError):
            return None

    def provenance(self) -> dict[str, Any]:
        return {
            "chapterSource": self.chapter_source,
            "chapterObservedCount": self.chapter_observed_count,
            "chapterRowCount": len(self.chapter_rows),
            "chapterComplete": self.chapter_complete,
            "rewardSource": self.reward_source,
            "rewardObservedCount": self.reward_observed_count,
            "rewardRowCount": len(self.reward_rows),
            "rewardComplete": self.reward_complete,
            "fallbackUsed": self.fallback_used,
        }


def _read_table(path: Path) -> tuple[tuple[Mapping[str, Any], ...], int, str, bool]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("table root is not an object")
        rows = payload.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError("table rows is not a list")
        records = tuple(row for row in rows if isinstance(row, Mapping))
        observed = int(payload.get("observedCount", len(records)))
        source = str((payload.get("sourceFiles") or [str(path)])[0])
        return records, observed, source, False
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return (), 0, str(path), True


def load_dungeon_config(root: str | Path | None = None) -> DungeonConfig:
    base = Path(root) if root is not None else Path(__file__).resolve().parents[2]
    chapter_path = base / "data" / "config" / "ChapterInfo.json"
    reward_path = base / "data" / "config" / "DungeonReward.json"
    chapter_rows, chapter_count, chapter_source, chapter_fallback = _read_table(chapter_path)
    reward_rows, reward_count, reward_source, reward_fallback = _read_table(reward_path)
    return DungeonConfig(
        chapter_rows=chapter_rows,
        reward_rows=reward_rows,
        chapter_observed_count=chapter_count,
        reward_observed_count=reward_count,
        chapter_source=chapter_source,
        reward_source=reward_source,
        fallback_used=chapter_fallback or reward_fallback,
    )


__all__ = ["DungeonConfig", "load_dungeon_config"]
