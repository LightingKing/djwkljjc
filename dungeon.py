"""Configuration-driven local dungeon state machine.

Chapters and stages come from the complete client tables (ChapterInfo: 35
story/normal/training/challenge chapters, DungeonReward: 683 stages).  Every
chapter stage is playable through the terminal ``FightActiveData`` protocol
(notFight scenes settle directly); unlocking, stars (DungeonReward.target),
energy (cost/costWin), first-clear rewards (dropDisplayFirst), repeat drops,
chapter star boxes (ChapterReward), the star exchange and the support aura
follow the client's rules.  No remote or retired service is contacted.
"""

from __future__ import annotations

import json
import math
import random
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import yaml

from google.protobuf import json_format

from server.core.content import DungeonConfig, load_dungeon_config
from server.core.atomic import track_mapping


Response = Tuple[int, Any]
TUTORIAL_DUNGEON_ID = 2011
FIRST_STORY_BATTLE_ID = 2012
SECOND_STORY_BATTLE_ID = 2013
DUNGEON_CONFIG: DungeonConfig = load_dungeon_config()


def _load_stage_metadata() -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Load the small verified ``DungeonMonster`` sample without inventing rows.

    The checked-in table is explicitly partial (four observed stages out of
    916 historical rows).  Keeping its completeness marker next to the
    returned metadata lets local tools and the client inspect the enemy
    lineup where evidence exists while making missing stages explicit.
    """

    source = Path(__file__).resolve().parent / "data" / "config" / "DungeonMonster.json"
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, {
            "source": "data/config/DungeonMonster.json",
            "completeness": "unavailable",
            "observedCount": 0,
            "rowCount": 0,
        }
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    by_id: dict[int, dict[str, Any]] = {}
    if isinstance(rows, list):
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            try:
                dungeon_id = int(raw.get("dungeonID"))
            except (TypeError, ValueError):
                continue
            # Copy only fields used by the local stage inspection seam.  This
            # prevents arbitrary config keys from becoming response fields.
            def _ints(value: Any) -> list[int]:
                if not isinstance(value, list):
                    return []
                result: list[int] = []
                for item in value:
                    try:
                        result.append(int(item))
                    except (TypeError, ValueError):
                        continue
                return result

            entry: dict[str, Any] = {
                "dungeonId": dungeon_id,
                "stageId": str(raw.get("stageID", "")),
                "level": _ints(raw.get("level", [])),
                "monsterId": _ints(raw.get("monsterId", [])),
                "winParam": _ints(raw.get("winParam", [])),
            }
            if raw.get("aiFilename") is not None:
                entry["aiFilename"] = str(raw.get("aiFilename"))
            if raw.get("musicID") is not None:
                entry["musicId"] = str(raw.get("musicID"))
            by_id[dungeon_id] = entry
    return by_id, {
        "source": "data/config/DungeonMonster.json",
        "completeness": str(payload.get("completeness", "partial")) if isinstance(payload, Mapping) else "partial",
        "observedCount": int(payload.get("observedCount", len(by_id))) if isinstance(payload, Mapping) else len(by_id),
        "rowCount": len(by_id),
    }


_STAGE_METADATA, _STAGE_METADATA_PROVENANCE = _load_stage_metadata()


def _load_story_metadata() -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Load the captured ``StoryDungeon`` enemy rows as read-only evidence.

    The runtime export reports 119 observed rows but only contains four
    records.  These rows are useful for inspecting story enemy IDs and level
    arrays, but they do not establish story rewards, unlock rules, or a
    complete battle implementation.  Keep those boundaries explicit in the
    response rather than treating the sample as a full story table.
    """

    source = Path(__file__).resolve().parent / "data" / "config" / "StoryDungeon.json"
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}, {
            "source": "data/config/StoryDungeon.json",
            "completeness": "unavailable",
            "observedCount": 0,
            "rowCount": 0,
        }
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    by_id: dict[int, dict[str, Any]] = {}

    def _ints(value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        result: list[int] = []
        for item in value:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                continue
        return result

    if isinstance(rows, list):
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            try:
                dungeon_id = int(raw.get("dungeonId"))
            except (TypeError, ValueError):
                continue
            entry: dict[str, Any] = {
                "dungeonId": dungeon_id,
                "level": _ints(raw.get("level", [])),
                "monsterId": _ints(raw.get("monsterId", [])),
                "levelUpIndex": _ints(raw.get("levelUpIndex", [])),
            }
            if raw.get("guide") is not None:
                try:
                    entry["guide"] = int(raw.get("guide"))
                except (TypeError, ValueError):
                    pass
            by_id[dungeon_id] = entry
    return by_id, {
        "source": "data/config/StoryDungeon.json",
        "completeness": str(payload.get("completeness", "partial")) if isinstance(payload, Mapping) else "partial",
        "observedCount": int(payload.get("observedCount", len(by_id))) if isinstance(payload, Mapping) else len(by_id),
        "rowCount": len(by_id),
    }


_STORY_METADATA, _STORY_METADATA_PROVENANCE = _load_story_metadata()


def _story_metadata(dungeon_id: int) -> dict[str, Any]:
    """Return captured story enemy metadata without claiming full content."""

    dungeon_id = int(dungeon_id)
    row = _STORY_METADATA.get(dungeon_id)
    if row is None:
        return {
            "status": "unavailable",
            "dungeonId": dungeon_id,
            "source": _STORY_METADATA_PROVENANCE["source"],
            "completeness": _STORY_METADATA_PROVENANCE["completeness"],
        }
    return {
        "status": "configured",
        **row,
        "source": _STORY_METADATA_PROVENANCE["source"],
        "completeness": _STORY_METADATA_PROVENANCE["completeness"],
    }


def _stage_metadata(dungeon_id: int) -> dict[str, Any]:
    """Return evidence-bounded metadata for a stage.

    Missing IDs deliberately return ``status=unavailable``.  They remain
    playable through the deterministic local battle seam, but no enemy roster
    or level is fabricated for them.
    """

    dungeon_id = int(dungeon_id)
    row = _STAGE_METADATA.get(dungeon_id)
    if row is None:
        return {
            "status": "unavailable",
            "dungeonId": dungeon_id,
            "source": _STAGE_METADATA_PROVENANCE["source"],
            "completeness": _STAGE_METADATA_PROVENANCE["completeness"],
        }
    return {
        "status": "configured",
        **row,
        "source": _STAGE_METADATA_PROVENANCE["source"],
        "completeness": _STAGE_METADATA_PROVENANCE["completeness"],
    }


# Chapter-based modes come from the complete client ChapterInfo table
# (data/config/ChapterInfo.json, regenerated from xlsx__chapterinfo.lua):
# ChapterType 1 = story/plot chapters 1001-1012 (2011, 2012, ... and the
# 52xx/53xx side scenes), 2 = normal chapters 1-12 (stages 1-261 plus the
# 51xx/59xx side stages), 6 = training chapters 2001-2003 (5001-5006) and
# 11 = challenge chapters 3001-3008 (11011 ...).  Every stage of those rows is
# playable; DungeonReward.type carries the matching DungeonType.
CHAPTER_TYPE_STORY = 1
CHAPTER_TYPE_NORMAL = 2
CHAPTER_TYPE_TRAINING = 6
CHAPTER_TYPE_CHALLENGE = 11
CHAPTER_MODE_TYPES = (CHAPTER_TYPE_NORMAL, CHAPTER_TYPE_STORY, CHAPTER_TYPE_TRAINING, CHAPTER_TYPE_CHALLENGE)
DUNGEON_TYPE_STORY = 1
DUNGEON_TYPE_NORMAL = 2
DUNGEON_TYPE_TRAINING = 6
DUNGEON_TYPE_CHALLENGE = 11
STORY_CHAPTER_ID = 1001


def _int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in value or []:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _chapter_sort_key(row: Mapping[str, Any]) -> tuple[int, int, int]:
    # Normal chapters first, then story, training and challenge chapters,
    # each in chapterIndex order (the order the client lists them).
    chapter_type = int(row.get("type", 0) or 0)
    order = CHAPTER_MODE_TYPES.index(chapter_type) if chapter_type in CHAPTER_MODE_TYPES else len(CHAPTER_MODE_TYPES)
    return order, int(row.get("chapterIndex", 0) or 0), int(row.get("chapterId", 0) or 0)


CHAPTER_ROWS: tuple[Mapping[str, Any], ...] = tuple(sorted(
    (
        row for row in DUNGEON_CONFIG.chapter_rows
        if int(row.get("type", 0) or 0) in CHAPTER_MODE_TYPES and row.get("dungeonId") and row.get("chapterId")
    ),
    key=_chapter_sort_key,
))
CHAPTERS_BY_ID: dict[int, Mapping[str, Any]] = {int(row["chapterId"]): row for row in CHAPTER_ROWS}
CHAPTER_OF_DUNGEON: dict[int, int] = {}
for _row in CHAPTER_ROWS:
    for _dungeon_id in _int_list(_row.get("dungeonId")):
        CHAPTER_OF_DUNGEON.setdefault(_dungeon_id, int(_row["chapterId"]))
CHAPTER_DUNGEON_IDS: tuple[int, ...] = tuple(CHAPTER_OF_DUNGEON)


def _stage_ids_of_type(chapter_type: int) -> set[int]:
    return {
        dungeon_id for dungeon_id, chapter_id in CHAPTER_OF_DUNGEON.items()
        if int(CHAPTERS_BY_ID[chapter_id].get("type", 0) or 0) == chapter_type
    }


STORY_BATTLE_IDS = {FIRST_STORY_BATTLE_ID, SECOND_STORY_BATTLE_ID}
STORY_DUNGEON_IDS = _stage_ids_of_type(CHAPTER_TYPE_STORY) or {TUTORIAL_DUNGEON_ID, *STORY_BATTLE_IDS}
TRAINING_DUNGEON_IDS = _stage_ids_of_type(CHAPTER_TYPE_TRAINING)
CHALLENGE_DUNGEON_IDS = _stage_ids_of_type(CHAPTER_TYPE_CHALLENGE)
NORMAL_DUNGEON_IDS = DUNGEON_CONFIG.supported_normal_ids
NORMAL_DUNGEON_ID_SET = set(NORMAL_DUNGEON_IDS)
# ``supported_normal_ids`` remains the first chapter for callers that use it
# as a compatibility marker; gameplay uses every normal chapter stage.
ALL_NORMAL_DUNGEON_IDS = DUNGEON_CONFIG.all_normal_ids or NORMAL_DUNGEON_IDS
ALL_NORMAL_DUNGEON_ID_SET = set(ALL_NORMAL_DUNGEON_IDS)
PLAYABLE_DUNGEON_IDS = set(CHAPTER_DUNGEON_IDS) | STORY_DUNGEON_IDS | ALL_NORMAL_DUNGEON_ID_SET
# DungeonReward.notFight scenes (2011, 2021, 5201 ...) complete without a
# battle; every other chapter stage settles through FightActiveData.
NONFIGHT_STORY_IDS = {
    int(row.get("dungeonId"))
    for row in DUNGEON_CONFIG.reward_rows
    if row.get("notFight") and row.get("dungeonId") is not None and int(row.get("dungeonId")) in PLAYABLE_DUNGEON_IDS
}
FIGHT_DUNGEON_IDS = PLAYABLE_DUNGEON_IDS - NONFIGHT_STORY_IDS


def _dungeon_type(dungeon_id: int) -> int:
    """DungeonType of a chapter stage (DungeonReward.type, else its chapter)."""

    row = DUNGEON_CONFIG.reward_row(dungeon_id)
    value = int((row or {}).get("type", 0) or 0)
    if value in CHAPTER_MODE_TYPES:
        return value
    chapter = CHAPTERS_BY_ID.get(CHAPTER_OF_DUNGEON.get(int(dungeon_id), 0))
    if chapter is not None:
        return int(chapter.get("type", DUNGEON_TYPE_STORY) or DUNGEON_TYPE_STORY)
    return DUNGEON_TYPE_NORMAL if int(dungeon_id) in ALL_NORMAL_DUNGEON_ID_SET else DUNGEON_TYPE_STORY


def _stage_prerequisite(stage_id: int) -> int | None:
    """DungeonReward.preDungeonId, skipping ids that are not chapter stages."""

    pre = STAGE_PREREQUISITE.get(int(stage_id))
    seen: set[int] = set()
    while pre is not None and pre not in PLAYABLE_DUNGEON_IDS and pre not in seen:
        seen.add(pre)
        pre = STAGE_PREREQUISITE.get(pre)
    return pre


def _stage_target_ids(dungeon_id: int) -> list[int]:
    """Star target ids of a stage (DungeonReward.target[].id, 0-3 entries)."""

    row = DUNGEON_CONFIG.reward_row(dungeon_id) or {}
    return [
        int(target.get("id", 0) or 0)
        for target in row.get("target", []) or []
        if isinstance(target, Mapping) and int(target.get("id", 0) or 0) > 0
    ]
# The recovered DungeonReward rows do not contain a player EXP field.  Keep
# the small compatibility values in the reviewed local-defaults document so
# the gameplay code has no hidden test rewards.  The loader below validates
# the shape and falls back to a zero-reward policy if the document is absent
# or malformed; it never treats actor EXP as player EXP.
_DUNGEON_POLICY_FALLBACK: dict[str, Any] = {
    "tutorial_player_exp": {"2011": 10},
    "story_player_exp": {"2012": 100, "2013": 100},
    "normal_player_exp": 20,
    "provenance": "local-sandbox-no-player-exp-field-in-recovered-DungeonReward",
}


def _load_local_dungeon_policy() -> dict[str, Any]:
    source = Path(__file__).resolve().parent / "data" / "local-defaults.json"
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
        policy = payload.get("dungeon_policy") if isinstance(payload, Mapping) else None
        if not isinstance(policy, Mapping):
            return dict(_DUNGEON_POLICY_FALLBACK)
        tutorial = policy.get("tutorial_player_exp", {})
        story = policy.get("story_player_exp", {})
        if not isinstance(tutorial, Mapping) or not isinstance(story, Mapping):
            return dict(_DUNGEON_POLICY_FALLBACK)
        normal = max(0, int(policy.get("normal_player_exp", 0)))
        return {
            "tutorial_player_exp": {
                str(int(key)): max(0, int(value))
                for key, value in tutorial.items()
            },
            "story_player_exp": {
                str(int(key)): max(0, int(value))
                for key, value in story.items()
            },
            "normal_player_exp": normal,
            "provenance": str(policy.get("provenance", _DUNGEON_POLICY_FALLBACK["provenance"])),
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return dict(_DUNGEON_POLICY_FALLBACK)


_LOCAL_DUNGEON_POLICY = _load_local_dungeon_policy()
# Legacy flat rule, used only when the client level table is unavailable.
PLAYER_EXP_PER_LEVEL = 1000

# Complete config tables exported from the original client by
# tools/export_client_tables.py.  (data/config/PlayerLevelInitConfig is still
# a partial runtime capture; ChapterInfo/DungeonReward there are regenerated
# from these exports.)
_CLIENT_TABLE_DIR = Path(__file__).resolve().parent / "data" / "client-tables"


def _client_table_rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_CLIENT_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


# PlayerLevelInitConfig[level].exp is the EXP needed to advance from
# ``level - 1`` to ``level``.  The client stores ResPlayerExp as progress
# within the current level and sums these per-level values itself
# (DungeonBalanceBase.InitPlayerInfo), so the server must use the same model.
PLAYER_LEVELS: dict[int, dict[str, Any]] = {
    int(row["level"]): row for row in _client_table_rows("PlayerLevelInitConfig") if "level" in row
}
MAX_PLAYER_LEVEL = max(PLAYER_LEVELS) if PLAYER_LEVELS else 0


def _load_stage_rules() -> tuple[dict[int, int], dict[int, int]]:
    """Return (preDungeonId by stage, energy spent by a won stage).

    ``preDungeonId`` is the stage that must be cleared before this one opens
    (2011 -> 2012 -> 2013 -> 1 -> 2 -> ... -> 8 -> 2021 ...).  Energy is the
    entry ``cost`` plus the on-win ``costWin`` of resource type 6.
    """

    prerequisite: dict[int, int] = {}
    energy: dict[int, int] = {}
    for row in _client_table_rows("DungeonReward"):
        try:
            dungeon_id = int(row.get("dungeonId", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not dungeon_id:
            continue
        pre = int(row.get("preDungeonId", 0) or 0)
        if pre:
            prerequisite[dungeon_id] = pre
        spent = 0
        for key in ("cost", "costWin"):
            for item in row.get(key, []) or []:
                if isinstance(item, Mapping) and int(item.get("type", 0) or 0) == 6:
                    spent += int(item.get("count", 0) or 0)
        energy[dungeon_id] = spent
    return prerequisite, energy


STAGE_PREREQUISITE, STAGE_ENERGY_COST = _load_stage_rules()

# Full client DungeonReward rows (expActor/expAssistant, dropDisplayFirst ...).
STAGE_REWARD_ROWS: dict[int, dict[str, Any]] = {
    int(row["dungeonId"]): row for row in _client_table_rows("DungeonReward") if row.get("dungeonId")
}
# ActorLevelup[level].exp = character EXP needed to reach ``level``.
ACTOR_LEVELS: dict[int, int] = {
    int(row["level"]): int(row.get("exp", 0) or 0)
    for row in _client_table_rows("ActorLevelup") if "level" in row
}
MAX_ACTOR_LEVEL = 60  # xlsx/Misc.maxActorLevel
# Materials whose ItemInfo.sources include Goto 207 (ChapterNormal), i.e. the
# items normal stages drop.  Which stage drops which item and how many lived
# in server-only drop groups (Dungeon_<stage>_11_<n>) that were not recovered.
STAGE_MATERIAL_POOL: tuple[int, ...] = tuple(sorted(
    int(row["id"]) for row in _client_table_rows("ItemInfo")
    if row.get("id") and 207 in (row.get("sources") or [])
))
# ``ProfitDetail`` guild skills target item function classes rather than
# individual item ids (for example, funcType 7 is a star-up material and
# funcType 2 is an equipment EXP potion).  Keep this projection local to the
# reward path so a missing/partial ItemInfo export simply leaves the skill
# inoperative for that item.
ITEM_FUNC_TYPES: dict[int, int] = {
    int(row["id"]): int(row.get("funcType", 0) or 0)
    for row in _client_table_rows("ItemInfo")
    if row.get("id")
}
# Drops.  The real per-stage drop groups (Dungeon_<n>_<type>_<m>) were
# server-only; the client tables only carry what the stage window shows:
#   - dropDisplayFirst: the first-clear rewards ("首通获得"), granted in full;
#   - dropDisplay / dropDisplayOutside: the possible repeat drops (star-up
#     materials 300xx, actor chips 20xxx ...).  ItemInfo.sources of every
#     star-up material lists exactly these stages (Goto 1000+n = DungeonStory
#     n), so rolling these lists keeps every material obtainable.
# Local rules (documented, not recovered): each displayed repeat entry drops
# its displayed count with REPEAT_DROP_CHANCE per multiPower unit, only on
# stages that have server drop groups (non-empty DungeonReward.dropId: the
# training/challenge stages have none, so they pay their first clear only).
REPEAT_DROP_CHANCE = 0.5
# The per-run coin group ``Dungeon_<n>_7`` pays a tenth of stage n's
# first-clear coins (local rule; the group contents were server-only).
REPEAT_COIN_DIVISOR = 10
RES_PLAYER_EXP = 1
RES_PLAYER_MONEY = 5
RES_PLAYER_GOLD = 7
RES_ITEM = 11
RES_EQUIP = 12
MAX_MULTI_POWER = 3
_COIN_GROUP_TOKEN = re.compile(r"Dungeon_(\d+)_7")
_ITEM_GROUP_TOKEN = re.compile(r"Dungeon_(\d+)_11_1")

# ChapterReward: star boxes per chapter (star 0 = chapter finished).
CHAPTER_REWARDS: dict[int, dict[int, list[dict[str, Any]]]] = {}
for _reward_row in _client_table_rows("ChapterReward"):
    try:
        _chapter = int(_reward_row.get("chapterId", 0) or 0)
        _star = int(_reward_row.get("star", 0) or 0)
    except (TypeError, ValueError):
        continue
    if _chapter:
        CHAPTER_REWARDS.setdefault(_chapter, {})[_star] = [
            dict(item) for item in _reward_row.get("reward", []) or [] if isinstance(item, Mapping)
        ]


def _misc_value(name: str, default: int) -> int:
    for row in _client_table_rows("Misc")[:1]:
        try:
            return int(row.get(name, default) or default)
        except (TypeError, ValueError):
            return default
    return default


# Window ChapterStarExchange: every star not yet exchanged pays
# Misc.oneStarRewardCount (3).  The label is LabelMoneyCount, i.e.
# ResPlayerMoney (type 5, the currency the chapter-finish box pays).
ONE_STAR_REWARD_COUNT = _misc_value("oneStarRewardCount", 3)
STAR_EXCHANGE_RESOURCE_TYPE = RES_PLAYER_MONEY

# Support (助战) aura: ActorQualityInfo[(actorId, quality)] effect attrs that
# AU.AuraAttrAppend adds to each of the player's own fighters.
AURA_QUALITY_INFO: dict[tuple[int, int], dict[str, Any]] = {
    (int(row["actorId"]), int(row.get("quality", 0) or 0)): row
    for row in _client_table_rows("ActorQualityInfo") if row.get("actorId")
}
ACTOR_CONFIG: dict[int, dict[str, Any]] = {
    int(row["id"]): row for row in _client_table_rows("ActorConfig") if row.get("id")
}
# ActorUtil RateType: a *Rate attr scales its base attr when the base exists.
_AURA_RATE_TYPE = {2: 1, 7: 3, 9: 5, 24: 23, 10: 6}


def _roll_actor_level(level: int, exp: int, *, cap: int = MAX_ACTOR_LEVEL) -> tuple[int, int]:
    """Roll character EXP into levels up to ``cap``.

    At the cap the bar may fill up to just below the next threshold; any
    further EXP is dropped (matching the client's CanEatFood rule).
    """
    level = max(1, int(level))
    exp = max(0, int(exp))
    cap = max(1, min(MAX_ACTOR_LEVEL, int(cap)))
    while level < cap:
        need = max(1, ACTOR_LEVELS.get(level + 1, 0))
        if level + 1 not in ACTOR_LEVELS or exp < need:
            break
        exp -= need
        level += 1
    if level >= cap and level + 1 in ACTOR_LEVELS:
        exp = min(exp, max(0, ACTOR_LEVELS[level + 1] - 1))
    return level, exp


def _exp_to_reach(level: int) -> int:
    row = PLAYER_LEVELS.get(int(level))
    if row is None:
        return 0
    # The client substitutes 1 for a zero threshold; mirror it.
    return max(1, int(row.get("exp", 0) or 0))


def _roll_player_level(level: int, exp: int) -> tuple[int, int]:
    """Advance ``level`` while in-level ``exp`` covers the next threshold."""

    level = max(1, int(level))
    exp = max(0, int(exp))
    if not PLAYER_LEVELS:
        return max(level, 1 + exp // PLAYER_EXP_PER_LEVEL), exp
    while level < MAX_PLAYER_LEVEL:
        need = _exp_to_reach(level + 1)
        if need <= 0 or exp < need:
            break
        exp -= need
        level += 1
    return level, exp

# The retired service's special-dungeon tables are not available.  The local
# compatibility rules live in ``data/server-only/special-dungeons.yaml`` so
# the IDs/rewards can be reviewed and changed without editing gameplay code.
# The fallback below is intentionally kept for clean checkouts where the data
# file is absent; it is the same reviewed local policy and never claims to be
# recovered official data.
_SPECIAL_DUNGEON_FALLBACK: dict[str, dict[str, Any]] = {
    "gold": {
        "id": 900001,
        "mode": "gold",
        "activity_id": "gold",
        "dungeon_type": 3,
        "energy_cost": 1,
        "reward": [{"type": 7, "id": 0, "param": 0, "count": 5000}],
        "label": "金币本",
    },
    "tower": {
        "id": 900101,
        "mode": "tower",
        "activity_id": "tower",
        "dungeon_type": 4,
        "energy_cost": 0,
        "reward": [{"type": 7, "id": 0, "param": 0, "count": 1000}],
        "label": "爬塔",
    },
    "maze": {
        "id": 900201,
        "mode": "maze",
        "activity_id": "maze",
        "dungeon_type": 5,
        "energy_cost": 1,
        "reward": [
            {"type": 7, "id": 0, "param": 0, "count": 1000},
            {"type": 11, "id": 910001, "param": 0, "count": 1},
        ],
        "label": "迷宫",
    },
    "tournament": {
        "id": 900301,
        "mode": "tournament",
        "activity_id": "tournament",
        "dungeon_type": 6,
        "energy_cost": 0,
        "reward": [
            {"type": 7, "id": 0, "param": 0, "count": 1500},
            {"type": 11, "id": 920001, "param": 0, "count": 1},
        ],
        "label": "锦标赛 PvE",
    },
}

_SPECIAL_DUNGEON_TYPES = {"gold": 3, "tower": 4, "maze": 5, "tournament": 6}


def load_special_dungeons(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """Load local special-dungeon policy from the checked-in YAML file.

    The file intentionally contains only local rules (not an inferred copy of
    the retired backend).  Invalid or incomplete input falls back to the
    reviewed in-code defaults, while preserving the same response shape used
    by the existing handlers.
    """

    source = Path(path) if path is not None else Path(__file__).resolve().parent / "data" / "server-only" / "special-dungeons.yaml"
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        stages = raw.get("stages") if isinstance(raw, Mapping) else None
    except (OSError, UnicodeError, yaml.YAMLError):
        stages = None
    if not isinstance(stages, Mapping):
        return {mode: dict(spec) for mode, spec in _SPECIAL_DUNGEON_FALLBACK.items()}

    loaded: dict[str, dict[str, Any]] = {}
    for mode, fallback in _SPECIAL_DUNGEON_FALLBACK.items():
        configured = stages.get(mode)
        if not isinstance(configured, Mapping):
            loaded[mode] = dict(fallback)
            continue
        try:
            dungeon_id = int(configured.get("id", fallback["id"]))
            energy_cost = max(0, int(configured.get("energy_cost", fallback["energy_cost"])))
        except (TypeError, ValueError):
            loaded[mode] = dict(fallback)
            continue
        reward_key = "reward_per_floor" if mode == "tower" else "reward"
        reward_raw = configured.get(reward_key, fallback.get("reward", []))
        rewards: list[dict[str, int]] = []
        if isinstance(reward_raw, list):
            for item in reward_raw:
                if not isinstance(item, Mapping):
                    continue
                try:
                    rewards.append({
                        "type": int(item.get("type", 0)),
                        "id": int(item.get("id", 0)),
                        "param": int(item.get("param", 0)),
                        "count": max(0, int(item.get("count", 0))),
                    })
                except (TypeError, ValueError):
                    continue
        if not rewards:
            rewards = [dict(item) for item in fallback.get("reward", [])]
        loaded[mode] = {
            "id": dungeon_id,
            "mode": mode,
            "activity_id": str(configured.get("activity_id", fallback.get("activity_id", mode))),
            "dungeon_type": int(fallback.get("dungeon_type", _SPECIAL_DUNGEON_TYPES[mode])),
            "energy_cost": energy_cost,
            "reward": rewards,
            "label": str(configured.get("label", fallback.get("label", mode))),
            "max_floor": max(1, int(configured.get("floors", configured.get("layers", configured.get("stages", 1))) or 1)),
            "source": str(source),
            "provenance": str(raw.get("provenance", "local-sandbox") if isinstance(raw, Mapping) else "local-sandbox"),
        }
    return loaded


SPECIAL_DUNGEONS: dict[str, dict[str, Any]] = load_special_dungeons()
SPECIAL_ID_TO_MODE = {int(item["id"]): mode for mode, item in SPECIAL_DUNGEONS.items()}
SPECIAL_MODE_ALIASES = {
    "gold": "gold", "golddungeon": "gold", "gold_dungeon": "gold", "金币": "gold", "金币本": "gold",
    "tower": "tower", "towerdungeon": "tower", "爬塔": "tower",
    "maze": "maze", "mazedungeon": "maze", "迷宫": "maze",
    "tournament": "tournament", "tournamentpve": "tournament", "锦标赛": "tournament",
    # ApiDungeon.activityType is an enum in the original client.  The local
    # JSON seam sometimes carries that numeric value instead of a mode name.
    "3": "gold", "4": "tower", "5": "maze", "6": "tournament",
}

# Team PVE (零境探险队) uses the client-owned TeamDungeonGroup table.  The
# original 2.0.4 table contains seven groups with eight level stages each;
# keep that mapping here so the HTTP compatibility seam can work even when a
# server-only table is not available.  Rewards and unlock levels come from
# the exported DungeonReward rows (7011-7078).  The multiplayer websocket
# service was retired, so the local route creates two deterministic AI
# teammates and runs the same FightActiveData/balance flow as a normal
# dungeon.
TEAM_DUNGEON_GROUPS: dict[int, list[int]] = {
    group: [7000 + group * 10 + level for level in range(1, 9)]
    for group in range(1, 8)
}
TEAM_DUNGEON_ACTIVITY_ID = 23001
TEAM_DUNGEON_LEVEL_LIMIT = 30
TEAM_DUNGEON_TICKET_ID = 80005
TEAM_DUNGEON_TICKET_TYPE = 11
TEAM_DUNGEON_DUNGEON_TYPE = 10


def _chapter_ids(row: Mapping[str, Any]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(_int_list(row.get("dungeonId"))))


def _chapter_key_ids(row: Mapping[str, Any]) -> tuple[int, ...]:
    """ChapterInfo.keyDungeonId: the stages that finish a chapter."""

    return tuple(dict.fromkeys(_int_list(row.get("keyDungeonId")))) or _chapter_ids(row)


def _chapter_row_for_dungeon(dungeon_id: int) -> Mapping[str, Any] | None:
    return CHAPTERS_BY_ID.get(CHAPTER_OF_DUNGEON.get(int(dungeon_id), 0))


def _chapter_row_by_id(chapter_id: int) -> Mapping[str, Any] | None:
    try:
        return CHAPTERS_BY_ID.get(int(chapter_id))
    except (TypeError, ValueError):
        return None


def _dungeon_record(
    dungeon_id: int,
    *,
    unlocked: bool = True,
    finished: bool = False,
    stars: int = 0,
    targets_done: Optional[list[int]] = None,
    difficulty: str = "normal",
) -> dict[str, Any]:
    """Return the descriptor-shaped local record for an unlocked dungeon.

    The IDs are grounded in the captured ``ChapterInfo``/``DungeonReward``
    samples: 2011 is the non-fight story tutorial and 2012/2013 are its
    following story entries.  This is local progression state, not a claim
    about an original account's server record.
    """

    return {
        "dungeonId": int(dungeon_id),
        "unlocked": bool(unlocked),
        "finished": bool(finished and unlocked),
        "new": bool(unlocked and not finished),
        # Only fields present in DbDungeonRecords.DungeonRecord are emitted
        # here: this object is parsed by the client's typed protobuf path.
        # Star/difficulty metadata remains in the local SQLite row and is
        # reflected in chapter summary totals and balance JSON.
        "targetsDone": list(targets_done or []),
        "unlockEndTime": 0,
        "randomPosition": 0,
        "unlockTime": 0,
        "multiPower": 0,
        "doneTimes": 0,
        "buyNum": 0,
        "lastBuyTime": 0,
        "loseNum": 0,
    }


def _chapter_record(
    chapter_id: int,
    *,
    finished: bool = False,
    total_star: int = 0,
    unlocked: bool = True,
    star_reward_got: Optional[list[int]] = None,
) -> dict[str, Any]:
    return {
        "chapterId": int(chapter_id),
        "unlocked": bool(unlocked),
        "finished": bool(finished),
        "new": bool(unlocked and not finished),
        "starRewardGot": list(star_reward_got or []),
        "totalStar": max(0, int(total_star)),
        "unlockTime": 0,
    }


def _effect_attr_append(attrs: list[dict[str, Any]], append: Any) -> None:
    """ActorUtil.EffectAttrAppend: add to the same type, scale a base by a
    *Rate type, or insert a new attr."""

    if not isinstance(append, Mapping) or append.get("type") is None:
        return
    attr_type = int(append.get("type", 0) or 0)
    value = int(append.get("value", 0) or 0)
    for attr in attrs:
        if int(attr.get("type", 0) or 0) == attr_type:
            attr["value"] = int(attr.get("value", 0) or 0) + value
            return
    base_type = _AURA_RATE_TYPE.get(attr_type)
    if base_type is not None:
        for attr in attrs:
            if int(attr.get("type", 0) or 0) == base_type:
                attr["value"] = math.floor(int(attr.get("value", 0) or 0) * (value / 10000 + 1))
                return
    attrs.append({"type": attr_type, "isRatio": bool(append.get("isRatio", False)), "value": value})


def _apply_aura(actors: list[dict[str, Any]], aura_actor_id: int, aura_quality: int) -> list[dict[str, Any]]:
    """AU.AuraAttrAppend(actors, auraActorId, auraActorQuality) for STActor JSON.

    The support character itself never joins the fight: the client only
    appends ActorQualityInfo effect attrs (effectAttrs plus the role/actor/
    book-targeted ones) and its specialAttrId to the player's own fighters.
    """

    info = AURA_QUALITY_INFO.get((int(aura_actor_id), int(aura_quality)))
    if info is None:
        return actors
    result: list[dict[str, Any]] = []
    for actor in actors:
        if not isinstance(actor, Mapping) or not actor.get("id"):
            result.append(actor)
            continue
        copy = json.loads(json.dumps(actor))
        attrs = [dict(attr) for attr in copy.get("attrs", []) or [] if isinstance(attr, Mapping)]
        for append in info.get("effectAttrs", []) or []:
            _effect_attr_append(attrs, append)
        config = ACTOR_CONFIG.get(int(copy["id"]), {})
        for key, value in (("effectRoleIdAttr", config.get("role")), ("effectActorIdAttr", copy.get("id")),
                           ("effectBookIdAttr", config.get("book"))):
            targeted = info.get(key)
            if isinstance(targeted, Mapping) and value is not None and int(value) in _int_list(targeted.get("ids")):
                _effect_attr_append(attrs, targeted.get("attr"))
        copy["attrs"] = attrs
        special = int(info.get("specialAttrId", 0) or 0)
        if special and special not in _int_list(copy.get("specialAttrIds")):
            copy["specialAttrIds"] = _int_list(copy.get("specialAttrIds")) + [special]
        result.append(copy)
    return result


def _route(path: str) -> str:
    value = urlsplit(path or "").path or "/"
    if value != "/":
        value = value.rstrip("/") + "/"
    return value


def _body_object(body: bytes | bytearray | str | None) -> Mapping[str, Any]:
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if not raw:
        return {}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


try:
    from . import actors as actor_store
    from . import equipment
    from .grants import grant_rewards
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store
    import equipment
    from grants import grant_rewards

class LocalDungeons:
    """Persistent local dungeon records attached to a :class:`LocalGame`."""

    def __init__(self, game: Any, clock: Optional[callable] = None) -> None:
        self.game = game
        self.db_path = Path(game.db_path)
        self._clock = clock or (lambda: int(time.time()))
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_dungeons (
                    dungeon_uid INTEGER PRIMARY KEY,
                    account_id INTEGER NOT NULL,
                    player_id TEXT NOT NULL,
                    dungeon_id INTEGER NOT NULL,
                    dungeon_type INTEGER NOT NULL,
                    actor_uids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    closed_at INTEGER,
                    balance_json TEXT
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(local_dungeons)")
            }
            if "active_data" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN active_data BLOB")
            if "active_updated_at" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN active_updated_at INTEGER")
            if "balance_json" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN balance_json TEXT")
            # Special local modes reuse the same idempotent dungeon state
            # machine.  The columns are nullable so historical story rows
            # remain byte-for-byte compatible with the older schema.
            if "mode" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN mode TEXT NOT NULL DEFAULT 'story'")
            if "activity_id" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN activity_id TEXT")
            if "floor" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN floor INTEGER")
            if "node" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN node INTEGER")
            if "terminal_outcome" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN terminal_outcome INTEGER")
            if "difficulty" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'normal'")
            if "stars" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN stars INTEGER NOT NULL DEFAULT 0")
            if "targets_json" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN targets_json TEXT NOT NULL DEFAULT '[]'")
            # ApiDungeon.multi_power (1-3) and the chosen support aura are
            # kept per run so a replayed POST and the balance use them.
            if "multi_power" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN multi_power INTEGER NOT NULL DEFAULT 1")
            if "aura_json" not in columns:
                connection.execute("ALTER TABLE local_dungeons ADD COLUMN aura_json TEXT")
            # Stars already traded in the chapter star exchange
            # (DBGamePlayer.BaseInfo.exchange_star in the original).
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_star_exchange (
                    account_id INTEGER PRIMARY KEY,
                    exchanged INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            # Chapter first-clear/star/chest claims are kept separately from
            # battle rows.  The unique key makes a repeated HTTP request
            # idempotent across service restarts.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_dungeon_claims (
                    account_id INTEGER NOT NULL,
                    chapter_id INTEGER NOT NULL,
                    claim_key TEXT NOT NULL,
                    claimed_at INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (account_id, chapter_id, claim_key)
                )
                """
            )

    def _new_uid(self, connection: sqlite3.Connection) -> int:
        current = connection.execute("SELECT MAX(dungeon_uid) FROM local_dungeons").fetchone()[0]
        # Keep UIDs deterministic and clearly local, while avoiding a time-only
        # collision if multiple test clients enter simultaneously.
        return max(int(current or 1000000000) + 1, int(self._clock() * 1000))

    @staticmethod
    def _actor_dict(actor_id: int, uid: int) -> dict[str, Any]:
        # Actor 1001/Role 1 and the model resource names are observed in the
        # config-samples capture. Attribute values are explicitly local-test
        # values; skills/talents remain empty until their tables are exported.
        return {
            "id": int(actor_id),
            "uid": int(uid),
            "curExp": 0,
            "level": 1,
            "star": 1,
            "quality": 1,
            "bodyEquips": {},
            "skills": [
                {"id": skill_id, "level": 1, "isRead": False}
                for skill_id in (1001001, 1001002, 1001003, 1001004, 1001005, 1001006)
            ],
            "attrs": [
                {"type": 1, "value": 1000, "isRatio": False},
                {"type": 3, "value": 100, "isRatio": False},
                {"type": 5, "value": 50, "isRatio": False},
                {"type": 6, "value": 50, "isRatio": False},
                {"type": 13, "value": 0, "isRatio": False},
                {"type": 14, "value": 150, "isRatio": False},
                {"type": 25, "value": 100, "isRatio": False},
            ],
            "equipAttrs": [],
            "specialAttrIds": [],
            "isNew": True,
            "isNewCard": True,
            "talentInfo": [],
            "isLock": False,
            "groupTag": {},
            "lastCalcAttrTime": 0,
            "talentResetPos": 0,
            "talentResetInfo": [],
            "countDelta": 0,
            "actorSkin": 0,
            "defaultIndex": max(0, int(uid) - 1),
            "isNormal": True,
        }

    @staticmethod
    def _wire_varint(data: bytes, wanted: int) -> Optional[int]:
        """Varint field ``wanted`` of a protobuf wire message (0 if absent,
        None when the bytes are not a valid message)."""

        try:
            from google.protobuf.internal.decoder import _DecodeVarint
        except ImportError:  # pragma: no cover
            return None
        position = 0
        try:
            while position < len(data):
                key, position = _DecodeVarint(data, position)
                field, wire = key >> 3, key & 7
                if wire == 0:
                    value, position = _DecodeVarint(data, position)
                    if field == wanted:
                        return int(value)
                elif wire == 2:
                    size, position = _DecodeVarint(data, position)
                    position += size
                elif wire == 5:
                    position += 4
                elif wire == 1:
                    position += 8
                else:
                    return None
        except Exception:
            return None
        return 0 if position == len(data) else None

    @staticmethod
    def _wire_group_id(body: Any) -> int:
        """``selectActorGroupId`` of a wireformat ApiDungeon request (field 1)."""
        if not isinstance(body, (bytes, bytearray)) or not body or body[:1] in (b"{", b"["):
            return 0
        try:
            from google.protobuf.internal.decoder import _DecodeVarint
        except ImportError:  # pragma: no cover
            return 0
        data, position = bytes(body), 0
        try:
            while position < len(data):
                key, position = _DecodeVarint(data, position)
                field, wire = key >> 3, key & 7
                if wire == 0:
                    value, position = _DecodeVarint(data, position)
                    if field == 1:
                        return int(value)
                elif wire == 2:
                    size, position = _DecodeVarint(data, position)
                    position += size
                elif wire == 5:
                    position += 4
                elif wire == 1:
                    position += 8
                else:
                    return 0
        except Exception:
            return 0
        return 0

    def _fight_team(self, profile: Mapping[str, Any], group_id: Any = None) -> tuple[list[int], list[int]]:
        """``(actor_ids, actor_uids)`` for the six slots of the chosen group.

        ActorGroup/DungeonPrepare PUT the groups and then POST the dungeon with
        ``selectActorGroupId``; WU.SyncActorAttr then fights with exactly the
        returned ``baseInfo.actors[index + 1]``.  Empty slots are 0.
        """
        systems = getattr(self.game, "systems", None)
        if systems is None:
            actor_ids = self._profile_actor_ids(profile)
            return actor_ids, list(range(1, len(actor_ids) + 1))
        state = systems._state(profile)
        groups = systems._normalise_actor_groups(state.get("actorGroups")) or systems._default_actor_groups()
        try:
            selected = int(group_id or 0)
        except (TypeError, ValueError):
            selected = 0
        if str(selected) not in groups:
            selected = int((state.get("selectedGroupIds") or {}).get("storymode", 1) or 1)
        members = (groups.get(str(selected)) or groups.get("1") or {}).get("members", [])
        actor_ids, actor_uids = [0] * 6, [0] * 6
        for member in members:
            index = int(member.get("index", -1))
            record = actor_store.get(profile, state, member.get("actorUid", 0))
            if 0 <= index < 6 and record is not None:
                actor_ids[index], actor_uids[index] = int(record["id"]), int(record["uid"])
        if not any(actor_uids):
            first = next(iter(actor_store.ordered(profile, state)), None)
            if first is not None:
                actor_ids[0], actor_uids[0] = int(first["id"]), int(first["uid"])
        return actor_ids, actor_uids

    def _fight_actor(self, profile: Mapping[str, Any], actor_id: int, uid: int) -> dict[str, Any]:
        """One fighter as STActor JSON from its instance (``{}`` = empty slot)."""
        if int(uid or 0) <= 0 or int(actor_id or 0) <= 0:
            return {}
        systems = getattr(self.game, "systems", None)
        record = actor_store.get(profile, systems._state(profile), uid) if systems is not None else None
        if record is None or not hasattr(self.game, "_append_local_actor"):
            return self._actor_dict(actor_id, uid)
        from google.protobuf import json_format

        container = self.game._types["DBGamePlayer.Actors"]()
        self.game._append_local_actor(
            container, int(record["id"]), int(record["uid"]), int(record["uid"]) - 1,
            level=int(record.get("level", 1) or 1), star=int(record.get("star", 1) or 1),
            quality=int(record.get("quality") or actor_store.base_quality(int(record["id"]))),
            exp=int(record.get("exp", 0) or 0), skill_levels=record.get("skills"),
        )
        actor = container.player_actors[-1]
        actor.is_new = False
        actor.is_new_card = False
        try:
            # Fight with the worn 藏品's stats and suit effects.
            equipment.apply_worn_equipment(actor, profile, systems._state(profile))
        except Exception:
            pass
        try:
            return json_format.MessageToDict(actor, always_print_fields_with_no_presence=True)
        except TypeError:  # older protobuf
            return json_format.MessageToDict(actor, including_default_value_fields=True)

    def _profile_actor_ids(self, profile: Mapping[str, Any]) -> list[int]:
        actor_ids = profile.get("actor_ids")
        if isinstance(actor_ids, list) and actor_ids:
            return [int(value) for value in actor_ids]
        actor_id = profile.get("actor_id")
        return [int(actor_id)] if actor_id is not None else []

    def _base_info(
        self,
        profile: Mapping[str, Any],
        dungeon_id: int,
        actor_ids: list[int],
        actor_uids: list[int],
        *,
        dungeon_type: int = DUNGEON_TYPE_STORY,
        create_time: Optional[int] = None,
        activity_id: str | int = 0,
        floor: int = 0,
        node: int = 0,
        difficulty: str = "normal",
        aura: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        actors = [self._fight_actor(profile, actor_id, uid) for actor_id, uid in zip(actor_ids, actor_uids)]
        aura = aura if isinstance(aura, Mapping) else {}
        aura_actor_id = int(aura.get("actorId", 0) or 0)
        aura_quality = int(aura.get("quality", 0) or 0)
        if aura_actor_id > 0:
            # WU.SyncActorAttr replaces the client's FightActorInfo (where
            # ActorGroupBase.AppendAuraAttrs put the aura) with these actors,
            # so the support's 光环 must already be applied here.
            actors = _apply_aura(actors, aura_actor_id, aura_quality)
        return {
            "dungeonId": int(dungeon_id),
            "actorUid": actor_uids,
            "createTime": int(self._clock()) if create_time is None else int(create_time),
            "quitTime": 0,
            "activityType": int(dungeon_type) if int(dungeon_type) >= 3 else 0,
            "activityId": activity_id,
            "towerFloor": int(floor),
            "floor": int(floor),
            "mazeNode": int(node),
            "actorId": actor_ids,
            "auraPlayerId": str(aura.get("playerId", "") or ""),
            "auraActorId": aura_actor_id,
            "auraStranger": bool(aura.get("stranger", False)),
            "auraActorQuality": aura_quality,
            "dungeonType": int(dungeon_type),
            "actors": actors,
            "eventKey": "",
            "medalId": 0,
            "reduceInfo": [],
            "isChallengeMode": str(difficulty).lower() in {"hard", "challenge", "difficult"},
            "difficulty": str(difficulty or "normal"),
            "fightBuffs": [],
            "npcCnt": 0,
            # Raw JSON clients may inspect this optional local field.  The
            # canonical protobuf envelope ignores it, while local tools can
            # distinguish captured enemy data from playable placeholder
            # stages whose historical rows were not recovered.
            "stageMetadata": _stage_metadata(dungeon_id),
        }

    @staticmethod
    def _special_mode(value: Any) -> Optional[str]:
        """Normalize a local special-mode name supplied by a JSON client."""

        if value is None:
            return None
        key = str(value).strip().lower()
        return SPECIAL_MODE_ALIASES.get(key)

    def _special_spec(
        self,
        dungeon_id: int,
        request: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any], int, int] | None:
        """Resolve a local special run to ``(mode, spec, floor, node)``.

        Tower floors use a contiguous local range (900101 onward) and maze /
        tournament accept a small ``floor``/``stage`` parameter.  This is a
        compatibility seam for the local UI; it does not infer hidden
        official IDs from the incomplete APK exports.
        """

        request = request or {}
        mode = self._special_mode(request.get("mode", request.get("activity", request.get("activityType"))))
        try:
            did = int(dungeon_id)
        except (TypeError, ValueError):
            return None
        if mode is None:
            mode = SPECIAL_ID_TO_MODE.get(did)
            if mode is None:
                if int(SPECIAL_DUNGEONS["tower"]["id"]) <= did < int(SPECIAL_DUNGEONS["tower"]["id"]) + int(SPECIAL_DUNGEONS["tower"].get("max_floor", 10)):
                    mode = "tower"
                elif int(SPECIAL_DUNGEONS["maze"]["id"]) <= did < int(SPECIAL_DUNGEONS["maze"]["id"]) + int(SPECIAL_DUNGEONS["maze"].get("max_floor", 10)):
                    mode = "maze"
                elif int(SPECIAL_DUNGEONS["tournament"]["id"]) <= did < int(SPECIAL_DUNGEONS["tournament"]["id"]) + int(SPECIAL_DUNGEONS["tournament"].get("max_floor", 10)):
                    mode = "tournament"
        floor_value = request.get("floor", request.get("towerFloor", request.get("stage", 1)))
        node_value = request.get("node", request.get("mazeNode", 1))
        try:
            floor = max(1, int(floor_value or 1))
            node = max(1, int(node_value or 1))
        except (TypeError, ValueError):
            return None
        if mode == "tower":
            # A supplied id wins; otherwise derive it from the requested
            # floor.  The local policy controls the maximum floor.
            max_floor = max(1, int(SPECIAL_DUNGEONS["tower"].get("max_floor", 10)))
            if did != int(SPECIAL_DUNGEONS["tower"]["id"]):
                floor = did - int(SPECIAL_DUNGEONS["tower"]["id"]) + 1
            floor = max(1, min(max_floor, floor))
            if did == SPECIAL_DUNGEONS["tower"]["id"] and floor != 1:
                did = int(SPECIAL_DUNGEONS["tower"]["id"]) + floor - 1
            if did < int(SPECIAL_DUNGEONS["tower"]["id"]) or did >= int(SPECIAL_DUNGEONS["tower"]["id"]) + max_floor:
                return None
            spec = dict(SPECIAL_DUNGEONS["tower"])
            spec["id"] = did
            spec["reward"] = [{"type": 7, "id": 0, "param": 0, "count": 1000 * floor}]
        elif mode in {"maze", "tournament"}:
            spec = dict(SPECIAL_DUNGEONS[mode])
            max_floor = max(1, int(spec.get("max_floor", 10)))
            if did != int(spec["id"]):
                # Accept the common stage/floor convention used by local
                # callers while keeping the exposed IDs deterministic.
                if did < int(spec["id"]) or did >= int(spec["id"]) + max_floor:
                    return None
                floor = did - int(spec["id"]) + 1
                spec["id"] = did
            elif mode in {"maze", "tournament"} and did != int(spec["id"]):
                floor = did - int(spec["id"]) + 1
            if mode == "tournament":
                spec["reward"] = [{"type": 7, "id": 0, "param": 0, "count": 1500 * floor}, {"type": 11, "id": 920001, "param": 0, "count": 1}]
        elif mode == "gold":
            if did != int(SPECIAL_DUNGEONS["gold"]["id"]):
                return None
            spec = dict(SPECIAL_DUNGEONS["gold"])
        else:
            return None
        return mode, spec, floor, node

    def _resource_changes(
        self,
        profile: Mapping[str, Any],
        changes: list[Mapping[str, Any]],
    ) -> tuple[bool, list[dict[str, int]], str | None]:
        """Atomically apply local resource deltas to ``game_profiles``."""

        resources = [dict(item) for item in profile.get("resources", []) if isinstance(item, Mapping)]
        index = {
            (int(item.get("type", 0)), int(item.get("id", 0)), int(item.get("param", 0))): i
            for i, item in enumerate(resources)
        }
        normalized: list[tuple[int, int, int, int]] = []
        for raw in changes:
            try:
                normalized.append((int(raw.get("type", 0)), int(raw.get("id", 0)), int(raw.get("param", 0)), int(raw.get("count", 0))))
            except (TypeError, ValueError):
                return False, [], "invalid_resource_change"
        required: dict[tuple[int, int, int], int] = {}
        for rtype, rid, param, delta in normalized:
            if delta < 0:
                key = (rtype, rid, param)
                required[key] = required.get(key, 0) + (-delta)
        for key, amount in required.items():
            current = int(resources[index[key]].get("count", 0)) if key in index else 0
            if current < amount:
                return False, [], "insufficient_resource"
        deltas: list[dict[str, int]] = []
        for rtype, rid, param, delta in normalized:
            key = (rtype, rid, param)
            if key not in index:
                index[key] = len(resources)
                resources.append({"type": rtype, "id": rid, "param": param, "count": 0})
            item = resources[index[key]]
            item["count"] = max(0, int(item.get("count", 0)) + delta)
            deltas.append({"type": rtype, "id": rid, "param": param, "count": int(item["count"]), "countDelta": delta})
        connect = getattr(self.game, "_connect", self._connect)
        with self._lock, closing(connect()) as connection, connection:
            updated = connection.execute(
                "UPDATE game_profiles SET resources_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(resources, separators=(",", ":")), int(self._clock()), int(profile["account_id"])),
            )
            if updated.rowcount != 1:
                return False, [], "profile_not_found"
        if isinstance(profile, dict):
            track_mapping(self.game, profile, "resources")
            profile["resources"] = resources
        return True, deltas, None

    def _special_progress(self, profile: Mapping[str, Any], mode: str) -> int:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(floor), 0) FROM local_dungeons WHERE account_id = ? AND mode = ? AND status = 'balanced'",
                (int(profile["account_id"]), mode),
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def _enter_special(
        self,
        profile: Mapping[str, Any],
        mode: str,
        spec: Mapping[str, Any],
        floor: int,
        node: int,
    ) -> Response:
        actor_ids, actor_uids = self._fight_team(profile)
        if not any(actor_uids):
            return 409, {"error": "profile_has_no_actors"}
        dungeon_id = int(spec["id"])
        with self._lock, closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT dungeon_uid, actor_uids_json, created_at, floor, node FROM local_dungeons WHERE account_id = ? AND dungeon_id = ? AND status = 'active' LIMIT 1",
                (int(profile["account_id"]), dungeon_id),
            ).fetchone()
            if existing is not None:
                stored_uids = [int(value) for value in json.loads(existing["actor_uids_json"] or "[]")]
                if len(stored_uids) == 6:
                    actor_uids = stored_uids
                    actor_ids = [int((actor_store.get(profile, self.game.systems._state(profile), uid) or {}).get("id", 0))
                                 for uid in actor_uids] if getattr(self.game, "systems", None) is not None else actor_ids
                return 200, {
                    "dungeonUid": int(existing["dungeon_uid"]),
                    "baseInfo": self._base_info(profile, dungeon_id, actor_ids, actor_uids, dungeon_type=int(spec["dungeon_type"]), create_time=int(existing["created_at"]), activity_id=str(spec["activity_id"]), floor=int(existing["floor"] or floor), node=int(existing["node"] or node)),
                    "resChange": [], "mode": mode, "floor": int(existing["floor"] or floor), "node": int(existing["node"] or node), "sandbox": True,
                    "policySource": spec.get("source", "local-sandbox"),
                    "provenance": spec.get("provenance", "local-sandbox"),
                }
            if mode == "tower" and floor > self._special_progress(profile, mode) + 1:
                return 409, {"error": "tower_floor_locked", "floor": floor, "unlockedFloor": self._special_progress(profile, mode) + 1}
            cost = int(spec.get("energy_cost", 0) or 0)
            if cost:
                ok, resource_change, error = self._resource_changes(profile, [{"type": 6, "id": 0, "param": 0, "count": -cost}])
                if not ok:
                    return 409, {"error": error or "insufficient_energy", "energyCost": cost}
            actor_ids, actor_uids = self._fight_team(profile)
            dungeon_uid = self._new_uid(connection)
            # Use one timestamp for both the persisted row and the response.
            # The client retries POST /fci/dungeon/ and expects the first
            # response's createTime to match the replayed active instance;
            # calling the clock again while building the response can cross a
            # second boundary and break that idempotency contract.
            created_at = int(self._clock())
            connection.execute(
                "INSERT INTO local_dungeons (dungeon_uid, account_id, player_id, dungeon_id, dungeon_type, actor_uids_json, status, created_at, closed_at, mode, activity_id, floor, node) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, ?, ?)",
                (dungeon_uid, int(profile["account_id"]), str(profile["player_id"]), dungeon_id, int(spec["dungeon_type"]), json.dumps(actor_uids, separators=(",", ":")), created_at, mode, str(spec["activity_id"]), floor, node),
            )
        return 200, {
            "dungeonUid": dungeon_uid,
            "baseInfo": self._base_info(profile, dungeon_id, actor_ids, actor_uids, dungeon_type=int(spec["dungeon_type"]), activity_id=str(spec["activity_id"]), floor=floor, node=node),
            "resChange": resource_change if cost else [], "mode": mode, "floor": floor, "node": node, "energyCost": cost, "sandbox": True,
            "policySource": spec.get("source", "local-sandbox"),
            "provenance": spec.get("provenance", "local-sandbox"),
        }

    def _special_info(self, profile: Mapping[str, Any], mode: str) -> Response:
        spec = SPECIAL_DUNGEONS[mode]
        progress = self._special_progress(profile, mode)
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT dungeon_uid, dungeon_id, status, floor, node, created_at, closed_at FROM local_dungeons WHERE account_id = ? AND mode = ? ORDER BY dungeon_uid DESC LIMIT 20",
                (int(profile["account_id"]), mode),
            ).fetchall()
        return 200, {
            "mode": mode,
            "activityId": str(spec["activity_id"]),
            "sandbox": True,
            "energyCost": int(spec.get("energy_cost", 0) or 0),
            "currentFloor": progress,
            "unlockedFloor": progress + 1,
            "maxFloor": int(spec.get("max_floor", 10 if mode in {"tower", "maze", "tournament"} else 1)),
            "policySource": spec.get("source", "local-sandbox"),
            "provenance": spec.get("provenance", "local-sandbox"),
            "runs": [
                {"dungeonUid": int(row["dungeon_uid"]), "dungeonId": int(row["dungeon_id"]), "status": row["status"], "floor": int(row["floor"] or 1), "node": int(row["node"] or 1), "createTime": int(row["created_at"]), "quitTime": int(row["closed_at"] or 0)}
                for row in rows
            ],
        }

    # ------------------------------------------------------------------ team PVE
    def _team_ticket_count(self, profile: Mapping[str, Any]) -> int:
        modes = getattr(self.game, "activity_modes", None)
        if modes is not None and hasattr(modes, "_item_count"):
            try:
                return int(modes._item_count(profile, TEAM_DUNGEON_TICKET_ID))
            except Exception:
                pass
        try:
            state = self.game.systems._state(profile)
            item = (state.get("items") or {}).get(str(TEAM_DUNGEON_TICKET_ID)) or {}
            return max(0, int(item.get("count", 0) or 0))
        except Exception:
            return 0

    def _team_change_ticket(self, profile: Mapping[str, Any], delta: int) -> Optional[dict[str, Any]]:
        modes = getattr(self.game, "activity_modes", None)
        if modes is not None and hasattr(modes, "_change_item"):
            try:
                # The compatibility helper returns None on a debit error.
                # Roll back any partial save before converting that error.
                with self.game.atomic_mutation():
                    return modes._change_item(profile, TEAM_DUNGEON_TICKET_ID, int(delta))
            except Exception:
                return None
        return None

    @staticmethod
    def _team_stage_group(dungeon_id: int) -> tuple[int, int] | None:
        value = int(dungeon_id)
        for group, stages in TEAM_DUNGEON_GROUPS.items():
            if value in stages:
                return group, stages.index(value) + 1
        return None

    def _team_ai_members(self, profile: Mapping[str, Any], dungeon_uid: int) -> list[dict[str, Any]]:
        """Return two stable bot members for a local team run."""

        names = ("本地协战·黑雪姬", "本地协战·桐人")
        actors = (1002, 1003, 1004, 1007, 1011, 2108)
        account = int(profile.get("account_id", 0) or 0)
        result: list[dict[str, Any]] = []
        for index in range(2):
            first = actors[(account + dungeon_uid + index * 2) % len(actors)]
            second = actors[(account + dungeon_uid + index * 2 + 1) % len(actors)]
            result.append({
                "playerId": f"local-team-bot-{dungeon_uid}-{index + 1}",
                "name": names[index],
                "level": max(30, int(profile.get("level", TEAM_DUNGEON_LEVEL_LIMIT) or TEAM_DUNGEON_LEVEL_LIMIT)),
                "actors": [{"id": first, "uid": 0}, {"id": second, "uid": 0}],
                "isRobot": True,
                "ready": True,
            })
        return result

    def _team_room(self, profile: Mapping[str, Any], dungeon_uid: int, dungeon_id: int, actor_ids: list[int], actor_uids: list[int], *, status: str = "ready") -> dict[str, Any]:
        player_id = str(profile.get("player_id", "local"))
        own_actors = [{"id": int(actor_id), "uid": int(uid)} for actor_id, uid in zip(actor_ids, actor_uids) if int(actor_id or 0) > 0 and int(uid or 0) > 0]
        bots = self._team_ai_members(profile, dungeon_uid)
        members = [{"playerId": player_id, "name": str(profile.get("name", "本地玩家")), "isRobot": False, "ready": True, "actors": own_actors}, *bots]
        # DungeonTeamPrepare reads the native ``actorInfo`` map, while the
        # JSON compatibility callers use ``members``/``aiTeammates``.  Keep
        # both views in sync so the single-player patch can reuse the native
        # room window and balance screen.
        actor_info = {}
        for member in members:
            actor_rows = []
            for actor in member.get("actors", []):
                actor_id = int(actor.get("id", 0) or 0)
                actor_uid = int(actor.get("uid", 0) or 0)
                if member.get("isRobot"):
                    actor_rows.append(self._actor_dict(actor_id, actor_uid))
                else:
                    actor_rows.append(self._fight_actor(profile, actor_id, actor_uid))
            actor_info[str(member["playerId"])] = {
                "playerInfo": {
                    "playerId": str(member["playerId"]),
                    "name": str(member.get("name", "")),
                    "level": int(member.get("level", profile.get("level", TEAM_DUNGEON_LEVEL_LIMIT)) or TEAM_DUNGEON_LEVEL_LIMIT),
                },
                "actors": actor_rows,
            }
        return {
            "roomId": int(dungeon_uid),
            "dungeonUid": int(dungeon_uid),
            "dungeonId": int(dungeon_id),
            "roomLevelLimit": int((STAGE_REWARD_ROWS.get(dungeon_id) or {}).get("difficultLevelLow", TEAM_DUNGEON_LEVEL_LIMIT) or TEAM_DUNGEON_LEVEL_LIMIT),
            "masterId": player_id,
            "memberList": [str(row["playerId"]) for row in members],
            "memberReadyState": {str(row["playerId"]): bool(row.get("ready", True)) for row in members},
            "actorInfo": actor_info,
            "allowFastJoin": False,
            "members": members,
            "aiTeammates": bots,
            "status": status,
            "localAi": True,
        }

    def _team_groups(self, profile: Mapping[str, Any]) -> Response:
        # The original endpoint returns an activityId -> TeamDungeonGroup id
        # map (the client looks up TeamDungeonGroup locally).
        groups = {str(TEAM_DUNGEON_ACTIVITY_ID): 1}
        return 200, {
            "result": groups,
            "groups": [
                {"groupId": group, "dungeonIds": list(stages), "bgPicture": f"Dungeon_team_bg_{group}"}
                for group, stages in TEAM_DUNGEON_GROUPS.items()
            ],
            "activityId": TEAM_DUNGEON_ACTIVITY_ID,
            "levelLimit": TEAM_DUNGEON_LEVEL_LIMIT,
            "ticket": {"type": TEAM_DUNGEON_TICKET_TYPE, "id": TEAM_DUNGEON_TICKET_ID, "count": 1},
            "tickets": self._team_ticket_count(profile),
            "localAi": True,
        }

    def _team_enter(self, profile: Mapping[str, Any], request: Mapping[str, Any]) -> Response:
        try:
            player_level = int(profile.get("level", 1) or 1)
        except (TypeError, ValueError):
            player_level = 1
        if player_level < TEAM_DUNGEON_LEVEL_LIMIT:
            return 409, {"error": "team_dungeon_level_locked", "levelLimit": TEAM_DUNGEON_LEVEL_LIMIT}
        try:
            group = int(request.get("dungeonGroup", request.get("groupId", 1)) or 1)
            level = int(request.get("level", request.get("difficulty", 1)) or 1)
        except (TypeError, ValueError):
            return 400, {"error": "invalid_team_dungeon_level"}
        dungeon_id_raw = request.get("dungeonId", request.get("dungeon_id"))
        try:
            dungeon_id = int(dungeon_id_raw) if dungeon_id_raw is not None else int(TEAM_DUNGEON_GROUPS.get(group, [])[max(0, level - 1)])
        except (TypeError, ValueError, IndexError):
            return 400, {"error": "invalid_team_dungeon_id"}
        stage = self._team_stage_group(dungeon_id)
        if stage is None:
            return 404, {"error": "team_dungeon_not_found", "dungeonId": dungeon_id}
        group, level = stage
        if level > 1 and player_level < int((STAGE_REWARD_ROWS.get(dungeon_id) or {}).get("difficultLevelLow", level) or level):
            return 409, {"error": "team_dungeon_level_locked", "level": level}
        actor_ids, actor_uids = self._fight_team(profile, request.get("selectActorGroupId", request.get("select_actor_group_id")))
        if not any(actor_uids):
            return 409, {"error": "profile_has_no_actors"}
        with self.game.atomic_mutation(), self._lock, closing(self.game._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT dungeon_uid, actor_uids_json, created_at, status FROM local_dungeons WHERE account_id = ? AND dungeon_id = ? AND mode = 'team' AND status = 'active' LIMIT 1",
                (int(profile["account_id"]), dungeon_id),
            ).fetchone()
            if existing is not None:
                stored = [int(value) for value in json.loads(existing["actor_uids_json"] or "[]")]
                if len(stored) == 6:
                    actor_uids = stored
                    state = self.game.systems._state(profile)
                    actor_ids = [int((actor_store.get(profile, state, uid) or {}).get("id", 0)) for uid in actor_uids]
                uid = int(existing["dungeon_uid"])
                return 200, {
                    "dungeonUid": uid, "dungeonId": dungeon_id, "mode": "team", "groupId": group, "level": level,
                    "baseInfo": self._base_info(profile, dungeon_id, actor_ids, actor_uids, dungeon_type=TEAM_DUNGEON_DUNGEON_TYPE, activity_id=TEAM_DUNGEON_ACTIVITY_ID),
                    "roomInfo": self._team_room(profile, uid, dungeon_id, actor_ids, actor_uids), "resChange": [], "retry": True, "localAi": True,
                }
            if self._team_ticket_count(profile) < 1:
                return 409, {"error": "team_dungeon_ticket_not_enough", "ticketId": TEAM_DUNGEON_TICKET_ID, "tickets": 0}
            ticket_change = self._team_change_ticket(profile, -1)
            if ticket_change is None:
                return 409, {"error": "team_dungeon_ticket_not_enough", "ticketId": TEAM_DUNGEON_TICKET_ID}
            uid = self._new_uid(connection)
            created = int(self._clock())
            connection.execute(
                "INSERT INTO local_dungeons (dungeon_uid, account_id, player_id, dungeon_id, dungeon_type, actor_uids_json, status, created_at, closed_at, mode, activity_id, floor, node) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, 'team', ?, ?, ?)",
                (uid, int(profile["account_id"]), str(profile["player_id"]), dungeon_id, TEAM_DUNGEON_DUNGEON_TYPE,
                 json.dumps(actor_uids, separators=(",", ":")), created, str(TEAM_DUNGEON_ACTIVITY_ID), level, 0),
            )
            return 200, {
                "dungeonUid": uid, "dungeonId": dungeon_id, "mode": "team", "groupId": group, "level": level,
                "baseInfo": self._base_info(profile, dungeon_id, actor_ids, actor_uids, dungeon_type=TEAM_DUNGEON_DUNGEON_TYPE, activity_id=TEAM_DUNGEON_ACTIVITY_ID, create_time=created),
                "roomInfo": self._team_room(profile, uid, dungeon_id, actor_ids, actor_uids),
                "resChange": [ticket_change], "ticket": {"type": TEAM_DUNGEON_TICKET_TYPE, "id": TEAM_DUNGEON_TICKET_ID, "countDelta": -1},
                "tickets": self._team_ticket_count(profile), "localAi": True,
            }

    def _team_balance(self, profile: Mapping[str, Any], dungeon_uid: int, body: bytes | bytearray | str | Mapping[str, Any] | None) -> Response:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT dungeon_id, status, active_data, balance_json, closed_at, actor_uids_json FROM local_dungeons WHERE dungeon_uid = ? AND account_id = ? AND mode = 'team'",
                (dungeon_uid, int(profile["account_id"])),
            ).fetchone()
        if row is None:
            return 404, {"error": "team_dungeon_not_found"}
        if row["status"] != "active":
            if row["balance_json"]:
                try:
                    cached = json.loads(row["balance_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    cached = None
                if isinstance(cached, dict):
                    return 200, cached
            return 409, {"error": "team_dungeon_not_active", "status": row["status"]}
        active = self._decode_active_bytes(bytes(row["active_data"])) if row["active_data"] else None
        request = _body_object(body)
        won = bool(active is not None and int(active.winTeam) == 1)
        if active is None and request.get("win") is not None:
            won = bool(request.get("win"))
        if active is None and not request.get("win"):
            return 409, {"error": "battle_not_finished", "message": "FightActiveData PATCH is required before balance"}
        dungeon_id = int(row["dungeon_id"])
        rewards = [dict(item) for item in (STAGE_REWARD_ROWS.get(dungeon_id) or {}).get("dropDisplay", []) if isinstance(item, Mapping)] if won else []
        reward_changes: list[dict[str, Any]] = []
        if won:
            modes = getattr(self.game, "activity_modes", None)
            if modes is not None and hasattr(modes, "_grant"):
                reward_changes = modes._grant(profile, rewards, dungeon_type=TEAM_DUNGEON_DUNGEON_TYPE)
            else:
                ok, reward_changes, error = self._resource_changes(profile, rewards)
                if not ok:
                    return 409, {"error": error or "resource_update_failed"}
        balance_time = int(self._clock())
        if not self._claim_terminal(profile, dungeon_uid, won=won, balance_time=balance_time):
            return 409, {"error": "team_dungeon_not_active"}
        try:
            actor_uids = [int(value) for value in json.loads(row["actor_uids_json"] or "[]")]
        except (TypeError, ValueError):
            actor_uids = []
        actor_ids = []
        try:
            state = self.game.systems._state(profile)
            actor_ids = [int((actor_store.get(profile, state, uid) or {}).get("id", 0)) for uid in actor_uids]
        except Exception:
            actor_ids = [0 for _ in actor_uids]
        payload = {
            "dungeonUid": dungeon_uid, "dungeonId": dungeon_id, "mode": "team", "localAi": True,
            "balanced": True, "balanceTime": balance_time, "win": won, "firstFinish": won,
            "profits": reward_changes, "resChange": reward_changes, "expChange": [],
            "playerDamages": [{"playerId": str(profile.get("player_id", "local")), "damage": 1000 if won else 300},
                               *[{"playerId": bot["playerId"], "damage": 700 if won else 200} for bot in self._team_ai_members(profile, dungeon_uid)]],
            "roomInfo": self._team_room(profile, dungeon_uid, dungeon_id, actor_ids, actor_uids, status="finished"),
            "balanceInfo": {"balanced": True, "win": won, "firstFinish": won, "balanceTime": balance_time, "profits": reward_changes, "resChange": reward_changes},
        }
        self._store_balance(profile, dungeon_uid, payload)
        return 200, payload

    def _team_http(self, method: str, route: str, body: bytes | bytearray | str | None, profile: Mapping[str, Any]) -> Optional[Response]:
        route = route.rstrip("/").casefold()
        if "/teamdungeongroups" in route:
            return self._team_groups(profile) if method == "GET" else (405, {"error": "method_not_allowed"})
        if "/teamdungeonurl" in route:
            return (200, {"url": "ws://127.0.0.1:19102/fci/teamdungeon/", "localAi": True}) if method == "GET" else (405, {"error": "method_not_allowed"})
        if "/teamdungeon" not in route:
            return None
        if route.endswith("/info") or route.endswith("/summary") or route == "/fci/teamdungeon":
            if method == "GET":
                return self._team_groups(profile)
            if method == "POST":
                return self._team_enter(profile, _body_object(body))
        match = re.search(r"/teamdungeon/(?:dungeon|room|match|run/)?(\d+)", route)
        uid_text = match.group(1) if match else None
        if route.endswith("/enter") or route.endswith("/start") or route.endswith("/create") or route.endswith("/join"):
            return self._team_enter(profile, _body_object(body)) if method == "POST" else (405, {"error": "method_not_allowed"})
        if uid_text and route.endswith("/active"):
            return self._active(profile, int(uid_text), body) if method == "PATCH" else (405, {"error": "method_not_allowed"})
        if uid_text and route.endswith("/balance"):
            return self._team_balance(profile, int(uid_text), body) if method == "POST" else (405, {"error": "method_not_allowed"})
        if uid_text and method == "GET":
            with self._lock, closing(self._connect()) as connection:
                row = connection.execute("SELECT dungeon_uid, dungeon_id, actor_uids_json, status FROM local_dungeons WHERE dungeon_uid = ? AND account_id = ? AND mode = 'team'", (int(uid_text), int(profile["account_id"]))).fetchone()
            if row is None:
                return 404, {"error": "team_dungeon_not_found"}
            actor_uids = [int(value) for value in json.loads(row["actor_uids_json"] or "[]")]
            return 200, {"dungeonUid": int(row["dungeon_uid"]), "dungeonId": int(row["dungeon_id"]), "status": row["status"], "actorUid": actor_uids, "roomInfo": self._team_room(profile, int(row["dungeon_uid"]), int(row["dungeon_id"]), [], actor_uids, status=str(row["status"])) , "localAi": True}
        return None

    def _progress(
        self,
        profile: Optional[Mapping[str, Any]],
        finished_dungeon_ids: Optional[set[int]] = None,
        finished_dungeon_difficulties: Optional[Mapping[int, str]] = None,
    ) -> dict[str, Any]:
        """Terminal progress: finished stages, done star targets, multiPower.

        A stage's record keeps the union of the targets done over all its won
        runs (FightPause/DungeonPrepare read ``targetsDone`` as "ever
        achieved"); its stars are ``#targetsDone``.  Rows balanced before
        targets were evaluated only stored a star count, which is mapped to
        the stage's first target ids so old progress keeps its stars.
        """

        finished = {int(value) for value in (finished_dungeon_ids or set())}
        finished.update(int(value) for value in (finished_dungeon_difficulties or {}))
        targets: dict[int, set[int]] = {}
        multi_power: dict[int, int] = {}
        if profile is not None and profile.get("account_id") is not None:
            with self._lock, closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT dungeon_id, stars, targets_json, multi_power FROM local_dungeons "
                    "WHERE account_id = ? AND status = 'balanced' ORDER BY dungeon_uid",
                    (int(profile["account_id"]),),
                ).fetchall()
            for row in rows:
                did = int(row["dungeon_id"])
                if did not in PLAYABLE_DUNGEON_IDS:
                    continue
                finished.add(did)
                valid = _stage_target_ids(did)
                try:
                    raw = json.loads(row["targets_json"] or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw = []
                done = (
                    {int(value) for value in raw if str(value).isdigit() and int(value) in valid}
                    if isinstance(raw, list) else set()
                )
                if not done and int(row["stars"] or 0) > 0:
                    done = set(valid[: int(row["stars"] or 0)])
                targets.setdefault(did, set()).update(done)
                multi_power[did] = max(1, int(row["multi_power"] or 1))
        return {"finished": finished, "targets": targets, "multiPower": multi_power}

    @staticmethod
    def _chapter_state(progress: Mapping[str, Any]) -> dict[str, Any]:
        """Chapter/stage gates from ChapterInfo and DungeonReward.

        - A chapter is finished once its keyDungeonId stages are cleared.
        - It unlocks when its preChapterId chapter is finished (story chapter
          100n and training chapter 200n name normal chapter n-1, challenge
          chapters a normal chapter); a challenge chapter also needs
          ``totalChallengeStar >= dungeonTypeStar`` (DU.ShowChallengeChapter-
          UnlockHint), any chapter ``preChapterStar`` normal stars when set.
        - A stage unlocks in an unlocked chapter once its preDungeonId is
          cleared.  Anything already cleared stays open (and keeps its
          chapter visible) for progress recorded under older rules.
        """

        finished: set[int] = set(progress.get("finished", set()))
        targets: Mapping[int, set[int]] = progress.get("targets", {})
        stars = {did: len(targets.get(did, ())) for did in CHAPTER_DUNGEON_IDS}
        chapter_stars = {
            cid: sum(stars.get(did, 0) for did in _chapter_ids(row)) for cid, row in CHAPTERS_BY_ID.items()
        }

        def type_stars(chapter_type: int) -> int:
            return sum(
                stars.get(did, 0) for did, cid in CHAPTER_OF_DUNGEON.items()
                if int(CHAPTERS_BY_ID[cid].get("type", 0) or 0) == chapter_type
            )

        totals = {
            "all": sum(stars.values()),
            "normal": type_stars(CHAPTER_TYPE_NORMAL),
            "challenge": type_stars(CHAPTER_TYPE_CHALLENGE),
        }
        finished_chapters = {
            cid for cid, row in CHAPTERS_BY_ID.items() if set(_chapter_key_ids(row)).issubset(finished)
        }
        chapter_unlocked: dict[int, bool] = {}
        for cid, row in CHAPTERS_BY_ID.items():
            pre = int(row.get("preChapterId", 0) or 0)
            ok = pre <= 0 or pre in finished_chapters
            if int(row.get("type", 0) or 0) == CHAPTER_TYPE_CHALLENGE:
                ok = ok and totals["challenge"] >= int(row.get("dungeonTypeStar", 0) or 0)
            pre_star = int(row.get("preChapterStar", 0) or 0)
            if pre_star:
                ok = ok and totals["normal"] >= pre_star
            chapter_unlocked[cid] = ok or any(did in finished for did in _chapter_ids(row))
        dungeon_unlocked: dict[int, bool] = {}
        for did, cid in CHAPTER_OF_DUNGEON.items():
            pre = _stage_prerequisite(did)
            dungeon_unlocked[did] = did in finished or (
                chapter_unlocked[cid] and (pre is None or pre in finished)
            )
        return {
            "finished": finished,
            "stars": stars,
            "chapterStars": chapter_stars,
            "totals": totals,
            "finishedChapters": finished_chapters,
            "chapterUnlocked": chapter_unlocked,
            "dungeonUnlocked": dungeon_unlocked,
        }

    def _claimed_stars(self, profile: Optional[Mapping[str, Any]]) -> dict[int, set[int]]:
        """ChapterReward stars already claimed, per chapter.

        Claims are stored as ``star:<ChapterReward.star>``.  Keys of the old
        flat-coin sandbox rewards ("first_clear", "chest", "star:1..3") never
        match a real ChapterReward row and are ignored.
        """

        if profile is None or profile.get("account_id") is None:
            return {}
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT chapter_id, claim_key FROM local_dungeon_claims WHERE account_id = ?",
                (int(profile["account_id"]),),
            ).fetchall()
        claimed: dict[int, set[int]] = {}
        for row in rows:
            key = str(row["claim_key"])
            if not key.startswith("star:") or not key[5:].isdigit():
                continue
            chapter_id, star = int(row["chapter_id"]), int(key[5:])
            if star in CHAPTER_REWARDS.get(chapter_id, {}):
                claimed.setdefault(chapter_id, set()).add(star)
        return claimed

    def _snapshot_parts(
        self,
        profile: Optional[Mapping[str, Any]],
        finished_dungeon_ids: Optional[set[int]] = None,
        finished_dungeon_difficulties: Optional[Mapping[int, str]] = None,
    ) -> dict[str, Any]:
        """Every chapter and stage record (locked ones included)."""

        progress = self._progress(profile, finished_dungeon_ids, finished_dungeon_difficulties)
        state = self._chapter_state(progress)
        claims = self._claimed_stars(profile)
        finished = state["finished"]
        dungeons: dict[int, dict[str, Any]] = {}
        for did in CHAPTER_DUNGEON_IDS:
            record = _dungeon_record(
                did,
                unlocked=bool(state["dungeonUnlocked"][did]),
                finished=did in finished,
                targets_done=sorted(progress["targets"].get(did, ())),
            )
            if did in finished:
                record["multiPower"] = int(progress["multiPower"].get(did, 1))
            dungeons[did] = record
        chapters = {
            cid: _chapter_record(
                cid,
                finished=cid in state["finishedChapters"],
                total_star=state["chapterStars"][cid],
                unlocked=bool(state["chapterUnlocked"][cid]),
                star_reward_got=sorted(claims.get(cid, ())),
            )
            for cid in CHAPTERS_BY_ID
        }
        return {"progress": progress, "state": state, "dungeons": dungeons, "chapters": chapters}

    @staticmethod
    def _chapter_info(parts: Mapping[str, Any], chapter_id: int) -> dict[str, Any]:
        """ApiChapterInfo.get shape for one chapter (its opened stages only).

        A stage record exists only once the stage is open: DU.IsDungeonUnlocked
        tests ``recordDungeons[id] ~= nil`` and TutorialManager.IsAvaliable
        starts the 2012/2013/training(5001)/5102 tutorials on that presence.
        """

        row = CHAPTERS_BY_ID[int(chapter_id)]
        return {
            "recordChapter": dict(parts["chapters"][int(chapter_id)]),
            "recordDungeons": {
                str(did): dict(parts["dungeons"][did])
                for did in _chapter_ids(row) if parts["dungeons"][did]["unlocked"]
            },
        }

    def _changed_chapter_infos(
        self,
        before: Optional[Mapping[str, Any]],
        after: Mapping[str, Any],
        dungeon_id: int,
    ) -> list[dict[str, Any]]:
        """Balance ``chapterInfo``: the stage's chapter plus every chapter
        whose record or stages changed (a newly opened next stage/chapter).

        SceneFight stores each entry as fci/dungeon/chapter/<id> and sets
        ``NoNewChapterUnlocked = #chapterInfo <= 1``, which keeps the chapter
        window on the page the player came from when nothing else opened.
        """

        own = CHAPTER_OF_DUNGEON.get(int(dungeon_id))
        order = ([own] if own is not None else []) + [cid for cid in CHAPTERS_BY_ID if cid != own]
        result: list[dict[str, Any]] = []
        for cid in order:
            if cid != own and not after["state"]["chapterUnlocked"][cid]:
                continue
            info = self._chapter_info(after, cid)
            if cid == own or before is None or info != self._chapter_info(before, cid):
                result.append(info)
        return result

    def _snapshot_from_parts(self, parts: Mapping[str, Any], *, compact: bool) -> dict[str, Any]:
        state = parts["state"]
        unlocked_chapters = [cid for cid in CHAPTERS_BY_ID if state["chapterUnlocked"][cid]]
        visible = unlocked_chapters if compact else list(CHAPTERS_BY_ID)
        finished = state["finished"]
        unlocked = state["dungeonUnlocked"]
        dungeon_ids = list(dict.fromkeys(
            did for cid in visible for did in _chapter_ids(CHAPTERS_BY_ID[cid])
            if not compact or unlocked.get(did)
        ))

        def counts(ids: set[int]) -> tuple[int, int]:
            return (
                sum(1 for did in ids if unlocked.get(did)),
                sum(1 for did in ids if did in finished),
            )

        chapter_stage_ids = set(CHAPTER_DUNGEON_IDS)
        story_unlocked, story_finished = counts(STORY_DUNGEON_IDS & chapter_stage_ids)
        normal_unlocked, normal_finished = counts(ALL_NORMAL_DUNGEON_ID_SET & chapter_stage_ids)
        training_unlocked, training_finished = counts(TRAINING_DUNGEON_IDS)
        challenge_unlocked, challenge_finished = counts(CHALLENGE_DUNGEON_IDS)
        unlocked_normal = [
            cid for cid in unlocked_chapters
            if int(CHAPTERS_BY_ID[cid].get("type", 0) or 0) == CHAPTER_TYPE_NORMAL
        ]
        summary = {
            "currentChapterId": unlocked_normal[-1] if unlocked_normal else STORY_CHAPTER_ID,
            "unlockedChapterId": unlocked_chapters,
            "finishedChapterId": [cid for cid in CHAPTERS_BY_ID if cid in state["finishedChapters"]],
            "newChapterId": [cid for cid in unlocked_chapters if cid not in state["finishedChapters"]],
            "newDungeonId": [did for did in dungeon_ids if unlocked.get(did) and did not in finished],
            "totalStar": state["totals"]["all"],
            "totalNormalStar": state["totals"]["normal"],
            "totalChallengeStar": state["totals"]["challenge"],
            "unlockedStoryDungeonsCnt": story_unlocked,
            "finishedStoryDungeonsCnt": story_finished,
            "unlockedNormalDungeonsCnt": normal_unlocked,
            "finishedNormalDungeonsCnt": normal_finished,
            "unlockedTrainingDungeonsCnt": training_unlocked,
            "finishedTrainingDungeonsCnt": training_finished,
            "unlockedChallengeDungeonsCnt": challenge_unlocked,
            "finishedChallengeDungeonsCnt": challenge_finished,
        }
        return {
            "record": {
                "playerId": "",
                "chapterRecord": {str(cid): dict(parts["chapters"][cid]) for cid in visible},
                "dungeonRecord": {str(did): dict(parts["dungeons"][did]) for did in dungeon_ids},
                "unlockAllChapter": 0,
            },
            "summary": summary,
        }

    def chapter_snapshot(
        self,
        profile: Optional[Mapping[str, Any]] = None,
        finished_dungeon_ids: Optional[set[int]] = None,
        *,
        compact: bool = True,
        finished_dungeon_difficulties: Optional[Mapping[int, str]] = None,
    ) -> dict[str, Any]:
        """``ApiChapterInfo.result`` for every chapter the player reached.

        ``summary.unlockedChapterId`` lists every unlocked chapter of all four
        chapter types (Window_ChapterNormal pages through the unlocked normal
        chapters; DU.IsChapterUnlocked also accepts a chapter's story/training
        alias).  ``compact=True`` (login/PushService, the client view) carries
        the unlocked chapters and their opened stages only, like the original
        (see ``_chapter_info``); ``compact=False`` is a local inspection view
        that adds every locked chapter and stage with ``unlocked: false``.
        """

        parts = self._snapshot_parts(profile, finished_dungeon_ids, finished_dungeon_difficulties)
        return self._snapshot_from_parts(parts, compact=compact)

    def chapter_summary(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        return self.chapter_snapshot(profile, compact=True)["summary"]

    def chapter_detail(self, profile: Mapping[str, Any], chapter_id: int) -> dict[str, Any]:
        """Return the typed ``ApiChapterInfo.get`` payload for one chapter.

        The client reads ``fci/dungeon/chapter/<id>`` directly while binding
        chapter red points; a nil detail makes the UI retry.  The reply holds
        the chapter record (also for a locked chapter) plus the opened stage
        records.  An id that is not a ChapterInfo row gets a default record
        with the requested id.
        """

        parts = self._snapshot_parts(profile)
        compact = self._snapshot_from_parts(parts, compact=True)
        record_chapter = parts["chapters"].get(int(chapter_id))
        record_dungeons = dict(compact["record"]["dungeonRecord"])
        return {
            "recordChapter": dict(record_chapter) if record_chapter is not None else _chapter_record(int(chapter_id)),
            "recordDungeons": record_dungeons,
        }

    def _grant(self, profile: Mapping[str, Any], rewards: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Grant STResource rows: equipment via equipment.py, the rest via grants."""

        changes: list[dict[str, Any]] = []
        plain: list[dict[str, Any]] = []
        for reward in rewards:
            if not isinstance(reward, Mapping):
                continue
            rtype = int(reward.get("type", 0) or 0)
            rid = int(reward.get("id", 0) or 0)
            count = int(reward.get("count", 0) or 0)
            if count <= 0:
                continue
            if rtype == RES_EQUIP:
                if getattr(self.game, "systems", None) is None or rid not in equipment.EQUIP_INFO:
                    continue
                for _ in range(count):
                    changes.append(equipment.grant_equipment(
                        self.game, profile, rid, rng=getattr(self, "_equip_rng", None)
                    ))
            else:
                plain.append({"type": rtype, "id": rid, "param": int(reward.get("param", 0) or 0), "count": count})
        if plain:
            changes.extend(grant_rewards(self.game, profile, plain))
        return changes

    def _requested_star_reward(self, body: bytes | bytearray | str | None) -> Optional[int]:
        """``starReward`` of a chapter box claim (JSON or ApiChapterReward wire).

        The client posts ``{starReward = ChapterReward.star}`` (0 = the
        chapter-finish box).  The legacy local seam ``{"kind": "first_clear"}``
        / ``{"kind": "star", "threshold": n}`` is still accepted.
        """

        request = _body_object(body)
        raw_body = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not request and raw_body and raw_body[:1] not in (b"{", b"["):
            # ApiChapterReward { int32 star_reward = 1; }
            value = self._wire_varint(raw_body, 1)
            return None if value is None else max(0, value)
        for key in ("starReward", "star_reward", "star"):
            if key in request:
                try:
                    return max(0, int(request.get(key) or 0))
                except (TypeError, ValueError):
                    return None
        kind = str(request.get("kind", request.get("rewardType", request.get("type", ""))) or "").strip().lower()
        if kind in {"", "first", "first_clear", "firstclear", "first-clear", "clear", "finish"}:
            return 0
        if kind == "star":
            try:
                return max(0, int(request.get("threshold", request.get("stars", 0)) or 0))
            except (TypeError, ValueError):
                return None
        return None

    def _claim_chapter_reward(
        self,
        profile: Mapping[str, Any],
        chapter_id: int,
        body: bytes | bytearray | str | None,
        *,
        read_only: bool = False,
    ) -> Response:
        """POST fci/dungeon/chapter/<id>/reward/ {starReward} (a chapter box).

        WidgetChapterRewardSlot shows a box claimable when ``star == 0`` and
        the chapter is finished, or ``star <= totalStar``; it then applies
        ``resChange`` and replaces ``recordChapter.starRewardGot`` with the
        reply's ``starRewardGot``.  Rewards are the ChapterReward row's.
        """

        row = _chapter_row_by_id(chapter_id)
        if row is None:
            return 404, {"error": "chapter_not_found", "chapterId": int(chapter_id)}
        canonical_id = int(row["chapterId"])
        star = self._requested_star_reward(body)
        if star is None:
            return 400, {"error": "invalid_star_reward", "chapterId": canonical_id}
        rewards = CHAPTER_REWARDS.get(canonical_id, {})
        if star not in rewards:
            return 404, {
                "error": "chapter_reward_not_found", "chapterId": canonical_id,
                "starReward": star, "available": sorted(rewards),
            }
        parts = self._snapshot_parts(profile)
        record = parts["chapters"][canonical_id]
        claimed = set(record["starRewardGot"])
        eligible = bool(record["finished"]) if star == 0 else int(record["totalStar"]) >= star
        claim_key = f"star:{star}"
        if read_only:
            return 200, {
                "chapterId": canonical_id,
                "starReward": star,
                "reward": [dict(item) for item in rewards[star]],
                "claimed": star in claimed,
                "eligible": eligible,
                "starRewardGot": sorted(claimed),
                "totalStar": int(record["totalStar"]),
                "finished": bool(record["finished"]),
            }
        if star in claimed:
            with self._lock, closing(self._connect()) as connection:
                cached = connection.execute(
                    "SELECT payload_json FROM local_dungeon_claims WHERE account_id = ? AND chapter_id = ? AND claim_key = ?",
                    (int(profile["account_id"]), canonical_id, claim_key),
                ).fetchone()
            try:
                payload = json.loads(cached["payload_json"]) if cached else None
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            if not isinstance(payload, dict):
                payload = {"chapterId": canonical_id, "starReward": star, "resChange": []}
            payload["alreadyClaimed"] = True
            payload["starRewardGot"] = sorted(claimed)
            return 200, payload
        if not eligible:
            return 409, {
                "error": "chapter_not_finished" if star == 0 else "star_reward_locked",
                "chapterId": canonical_id,
                "starReward": star,
                "totalStar": int(record["totalStar"]),
            }
        with self._lock, closing(self._connect()) as connection, connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO local_dungeon_claims "
                "(account_id, chapter_id, claim_key, claimed_at, payload_json) VALUES (?, ?, ?, ?, '{}')",
                (int(profile["account_id"]), canonical_id, claim_key, int(self._clock())),
            )
            if inserted.rowcount != 1:
                return 409, {"error": "already_claimed", "chapterId": canonical_id, "starReward": star}
        changes = self._grant(profile, rewards[star])
        payload = {
            "starRewardGot": sorted(claimed | {star}),
            "resChange": changes,
            "chapterId": canonical_id,
            "requestedChapterId": int(chapter_id),
            "starReward": star,
            "claimed": True,
            "alreadyClaimed": False,
            "totalStar": int(record["totalStar"]),
        }
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_dungeon_claims SET payload_json = ? WHERE account_id = ? AND chapter_id = ? AND claim_key = ?",
                (json.dumps(payload, separators=(",", ":")), int(profile["account_id"]), canonical_id, claim_key),
            )
        return 200, payload

    def _star_exchange(self, profile: Mapping[str, Any], method: str, count_text: Optional[str]) -> Response:
        """GET fci/starexchange -> {starNum}; POST fci/starexchange/<n>.

        Window ChapterStarExchange shows ``starNum`` stars (chapter stars not
        exchanged yet) worth ``starNum * Misc.oneStarRewardCount`` and posts
        the whole amount; the reply's ``resChange`` is applied and the reply
        itself becomes the new fci/starexchange.
        """

        total = int(self._snapshot_parts(profile)["state"]["totals"]["all"])
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT exchanged FROM local_star_exchange WHERE account_id = ?", (int(profile["account_id"]),)
            ).fetchone()
        exchanged = int(row["exchanged"]) if row is not None else 0
        remaining = max(0, total - exchanged)
        base = {"totalStar": total, "oneStarRewardCount": ONE_STAR_REWARD_COUNT}
        if count_text is None:
            if method != "GET":
                return 405, {"error": "method_not_allowed"}
            return 200, {"starNum": remaining, "exchanged": exchanged, **base}
        if method != "POST":
            return 405, {"error": "method_not_allowed"}
        try:
            requested = int(count_text)
        except (TypeError, ValueError):
            return 400, {"error": "invalid_star_count"}
        count = min(max(0, requested), remaining)
        if count <= 0:
            return 409, {"error": "no_star_to_exchange", "starNum": remaining, "exchanged": exchanged, **base}
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO local_star_exchange (account_id, exchanged, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(account_id) DO UPDATE SET exchanged = exchanged + excluded.exchanged, "
                "updated_at = excluded.updated_at",
                (int(profile["account_id"]), count, int(self._clock())),
            )
        changes = self._grant(profile, [{
            "type": STAR_EXCHANGE_RESOURCE_TYPE, "id": 0, "param": 0, "count": count * ONE_STAR_REWARD_COUNT,
        }])
        return 200, {"starNum": remaining - count, "exchanged": exchanged + count, "resChange": changes, **base}

    def _parse_enter_request(self, body: bytes | bytearray | str | None) -> dict[str, Any]:
        """POST fci/dungeon/ parameters from JSON or an ApiDungeon wire body.

        ActorGroup posts ``{dungeonId, selectActorGroupId, multiPower, price,
        auraPlayerId, auraActorId, auraStranger}``; Common_DungeonUtil sends
        the same as a raw ApiDungeon protobuf (fields 1-10), story entries
        use the JSON ``{dungeonId}`` seam.
        """

        request = dict(_body_object(body))
        if request or body is None:
            return request
        message_type = getattr(self.game, "_types", {}).get("ApiDungeon")
        if message_type is None:
            return request
        try:
            raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
            if not raw or raw[:1] in (b"{", b"["):
                return request
            message = message_type()
            message.ParseFromString(raw)
        except Exception:
            return request
        request = {
            "dungeonId": int(getattr(message, "dungeon_id", 0)),
            "selectActorGroupId": int(getattr(message, "select_actor_group_id", 0)),
            "auraPlayerId": str(getattr(message, "aura_player_id", "") or ""),
            "auraActorId": int(getattr(message, "aura_actor_id", 0)),
            "auraStranger": bool(getattr(message, "aura_stranger", False)),
            "auraActorQuality": int(getattr(message, "aura_actor_quality", 0)),
            "multiPower": int(getattr(message, "multi_power", 0)),
        }
        if bool(getattr(message, "is_challenge_mode", False)):
            # Preserve the wire request's challenge flag for the local
            # baseInfo/difficulty marker.
            request["difficulty"] = "hard"
        return request

    def _assistant_quality(self, profile: Mapping[str, Any], player_id: str, actor_id: int) -> int:
        """auraActorQuality of a support picked from fci/dungeon-assistants/.

        ActorGroupBase.AppendAuraAttrs posts only the player/actor ids (the
        client applied ``aura.quality`` locally), so the quality is looked up
        in the same assistant list the support window showed.
        """

        social = getattr(self.game, "social", None)
        handler = getattr(social, "handle_http", None)
        if handler is None or not player_id:
            return 0
        try:
            status, rows = handler("POST", "/fci/dungeon-assistants/", b"{}", profile)
        except Exception:
            return 0
        if status != 200 or not isinstance(rows, list):
            return 0
        for row in rows:
            if not isinstance(row, Mapping) or str(row.get("playerId", "")) != str(player_id):
                continue
            if actor_id and int(row.get("auraActorId", 0) or 0) not in (0, int(actor_id)):
                continue
            try:
                return max(0, int(row.get("auraActorQuality", 0) or 0))
            except (TypeError, ValueError):
                return 0
        return 0

    def _resolve_aura(self, profile: Mapping[str, Any], request: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """The support (助战) chosen for this run, or None.

        The support only lends its 光环: ActorQualityInfo(auraActorId,
        auraActorQuality) effect attrs on the player's own fighters.  The
        support character never takes a formation slot (FightActorInfo holds
        only the player's six slots and BaseInfo has no helper STActor).
        """

        def pick(*keys: str) -> Any:
            for key in keys:
                if key in request:
                    return request.get(key)
            return None

        try:
            actor_id = int(pick("auraActorId", "aura_actor_id") or 0)
            quality = int(pick("auraActorQuality", "aura_actor_quality") or 0)
        except (TypeError, ValueError):
            return None
        if actor_id <= 0:
            return None
        player_id = str(pick("auraPlayerId", "aura_player_id") or "")
        stranger = bool(pick("auraStranger", "aura_stranger") or False)
        if quality <= 0:
            quality = self._assistant_quality(profile, player_id, actor_id)
        if quality <= 0:
            # Unknown support: the lowest quality row of that actor.
            qualities = sorted(q for (aid, q) in AURA_QUALITY_INFO if aid == actor_id)
            quality = qualities[0] if qualities else 0
        return {"playerId": player_id, "actorId": actor_id, "stranger": stranger, "quality": quality}

    @staticmethod
    def _stage_costs(dungeon_id: int, key: str, multi_power: int) -> list[dict[str, int]]:
        """DungeonReward.cost / costWin entries scaled by multiPower (deltas)."""

        row = DUNGEON_CONFIG.reward_row(dungeon_id) or STAGE_REWARD_ROWS.get(int(dungeon_id)) or {}
        result: list[dict[str, int]] = []
        for raw in row.get(key, []) or []:
            if not isinstance(raw, Mapping):
                continue
            try:
                count = max(0, int(raw.get("count", 0) or 0)) * max(1, int(multi_power))
                resource_type = int(raw.get("type", 0) or 0)
                resource_id = int(raw.get("id", 0) or 0)
                param = int(raw.get("param", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if count and resource_type > 0 and resource_id >= 0 and param >= 0:
                result.append({"type": resource_type, "id": resource_id, "param": param, "count": -count})
        return result

    def _clamp_multi_power(self, profile: Mapping[str, Any], dungeon_id: int, requested: Any) -> int:
        """multiPower 1-3, allowed up to the stage's stars once it is cleared.

        ActorGroupBase.SetMultiPowerStatus enables choice i+1 only when
        ``i < (finished and #targetsDone or 1)``.
        """

        try:
            value = int(requested or 1)
        except (TypeError, ValueError):
            value = 1
        value = max(1, min(MAX_MULTI_POWER, value))
        if value == 1:
            return 1
        progress = self._progress(profile)
        if int(dungeon_id) not in progress["finished"]:
            return 1
        stars = len(progress["targets"].get(int(dungeon_id), ()))
        return max(1, min(value, stars))

    @staticmethod
    def _wrap_res_changes(deltas: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """DBH.ResChange only applies entries wrapped as ``{"baseRes": ...}``."""

        return [{"baseRes": {
            "type": int(delta["type"]), "id": int(delta.get("id", 0) or 0), "param": int(delta.get("param", 0) or 0),
            "count": int(delta["count"]), "countDelta": int(delta["countDelta"]),
        }} for delta in deltas]

    def _enter(self, profile: Mapping[str, Any], body: bytes | bytearray | str | None) -> Response:
        request = self._parse_enter_request(body)
        raw_dungeon_id = request.get("dungeonId", request.get("dungeon_id"))
        if raw_dungeon_id is None:
            requested_mode = self._special_mode(request.get("mode", request.get("activity", request.get("activityType"))))
            if requested_mode is not None:
                raw_dungeon_id = SPECIAL_DUNGEONS[requested_mode]["id"]
            else:
                raw_dungeon_id = TUTORIAL_DUNGEON_ID
        try:
            dungeon_id = int(raw_dungeon_id)
        except (TypeError, ValueError):
            return 400, {"error": "invalid_dungeon_id"}
        special = self._special_spec(dungeon_id, request)
        if special is not None:
            mode, spec, floor, node = special
            return self._enter_special(profile, mode, spec, floor, node)
        if dungeon_id not in PLAYABLE_DUNGEON_IDS:
            return 501, {"error": "dungeon_not_implemented", "dungeonId": dungeon_id}
        if request.get("enforceUnlock", request.get("strictUnlock", False)):
            chapter_row = _chapter_row_for_dungeon(dungeon_id)
            if chapter_row is not None:
                chapter_id = int(chapter_row.get("chapterId", 0) or 0)
                state = self._chapter_state(self._progress(profile))
                if not state["chapterUnlocked"].get(chapter_id, True):
                    return 409, {
                        "error": "chapter_locked",
                        "chapterId": chapter_id,
                        "dungeonId": dungeon_id,
                        "message": "finish the previous chapter key dungeon first",
                    }
        difficulty_value = request.get("difficulty", request.get("modeDifficulty", None))
        if isinstance(difficulty_value, bool):
            difficulty = "hard" if difficulty_value else "normal"
        else:
            difficulty = str(difficulty_value or "normal").strip().lower()
        if difficulty in {"2", "1", "hardmode", "difficult"}:
            difficulty = "hard"
        elif difficulty not in {"normal", "hard"}:
            # "challenge" was a local marker for 5xxx ids; challenge chapter
            # stages are ordinary runs of DungeonType 11.
            difficulty = "normal"
        dungeon_type = _dungeon_type(dungeon_id)
        group_ref = request.get("selectActorGroupId", request.get("select_actor_group_id"))
        if group_ref is None:
            group_ref = self._wire_group_id(body)
        actor_ids, actor_uids = self._fight_team(profile, group_ref)
        if not any(actor_uids):
            return 409, {"error": "profile_has_no_actors", "message": "local profile actor bootstrap is empty"}
        aura = self._resolve_aura(profile, request)
        multi_power = self._clamp_multi_power(profile, dungeon_id, request.get("multiPower", request.get("multi_power")))
        with self._lock, closing(self._connect()) as connection, connection:
            existing = connection.execute(
                "SELECT dungeon_uid, dungeon_id, actor_uids_json, created_at, difficulty, aura_json FROM local_dungeons "
                "WHERE account_id = ? AND dungeon_id = ? AND difficulty IN (?, ?) AND status = 'active' LIMIT 1",
                (int(profile["account_id"]), dungeon_id, difficulty,
                 "challenge" if difficulty == "normal" else difficulty),
            ).fetchone()
            if existing is not None:
                # POST /fci/dungeon/ is retried by the client after a
                # disconnect.  Replaying the existing active instance is
                # idempotent: same UID/creation time, no second entry cost.
                if aura is None and existing["aura_json"]:
                    try:
                        aura = json.loads(existing["aura_json"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        aura = None
                connection.execute(
                    "UPDATE local_dungeons SET actor_uids_json = ?, aura_json = ? WHERE dungeon_uid = ?",
                    (json.dumps(actor_uids, separators=(",", ":")),
                     json.dumps(aura, separators=(",", ":")) if aura else None,
                     int(existing["dungeon_uid"])),
                )
                return 200, {
                    "dungeonUid": int(existing["dungeon_uid"]),
                    "baseInfo": self._base_info(
                        profile,
                        int(existing["dungeon_id"]),
                        actor_ids,
                        actor_uids,
                        dungeon_type=dungeon_type,
                        create_time=int(existing["created_at"]),
                        difficulty=str(existing["difficulty"] or difficulty),
                        aura=aura,
                    ),
                    "resChange": [],
                }
            # Entry pays DungeonReward.cost x multiPower; a win later pays
            # costWin x multiPower.  The client only lets the player fight
            # with cost + costWin available (ActorGroupBase price), so check
            # the full price here.
            entry_cost = self._stage_costs(dungeon_id, "cost", multi_power)
            win_cost = self._stage_costs(dungeon_id, "costWin", multi_power)
            required: dict[tuple[int, int, int], int] = {}
            for item in entry_cost + win_cost:
                key = (item["type"], item["id"], item["param"])
                required[key] = required.get(key, 0) - item["count"]
            balances = {
                (int(item.get("type", 0)), int(item.get("id", 0)), int(item.get("param", 0))): int(item.get("count", 0))
                for item in profile.get("resources", []) if isinstance(item, Mapping)
            }
            for key, amount in required.items():
                if balances.get(key, 0) < amount:
                    return 409, {
                        "error": "insufficient_resource",
                        "dungeonId": dungeon_id,
                        "required": [{"type": k[0], "id": k[1], "param": k[2], "count": v} for k, v in required.items()],
                        "resChange": [],
                    }
            resource_change: list[dict[str, Any]] = []
            if entry_cost:
                ok, deltas, error = self._resource_changes(profile, entry_cost)
                if not ok:
                    return 409, {"error": error or "insufficient_resource", "dungeonId": dungeon_id, "resChange": []}
                resource_change = self._wrap_res_changes(deltas)
            dungeon_uid = self._new_uid(connection)
            # Use one timestamp for both the persisted row and the response.
            # A retry can cross a second boundary, so calling the clock again
            # while building the response would break the idempotency contract.
            created_at = int(self._clock())
            connection.execute(
                """
                INSERT INTO local_dungeons
                (dungeon_uid, account_id, player_id, dungeon_id, dungeon_type,
                 actor_uids_json, status, created_at, closed_at, difficulty,
                 multi_power, aura_json)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, NULL, ?, ?, ?)
                """,
                (
                    dungeon_uid,
                    int(profile["account_id"]),
                    str(profile["player_id"]),
                    dungeon_id,
                    dungeon_type,
                    json.dumps(actor_uids, separators=(",", ":")),
                    created_at,
                    difficulty,
                    multi_power,
                    json.dumps(aura, separators=(",", ":")) if aura else None,
                ),
            )
        if aura and aura.get("playerId"):
            # Once per battle that used a support (a replayed POST of the same
            # run does not count again): relation points + friend cooldown.
            recorder = getattr(getattr(self.game, "social", None), "record_assist_used", None)
            if callable(recorder):
                try:
                    recorder(profile, aura["playerId"])
                except Exception:
                    pass
        return 200, {
            "dungeonUid": dungeon_uid,
            "baseInfo": self._base_info(
                profile, dungeon_id, actor_ids, actor_uids, dungeon_type=dungeon_type,
                difficulty=difficulty, create_time=created_at, aura=aura,
            ),
            "resChange": resource_change,
        }

    def _close(self, profile: Mapping[str, Any], dungeon_uid: int) -> Response:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT account_id, status FROM local_dungeons WHERE dungeon_uid = ?",
                (dungeon_uid,),
            ).fetchone()
            if row is None or int(row["account_id"]) != int(profile["account_id"]):
                return 404, {"error": "dungeon_not_found"}
            if row["status"] == "active":
                connection.execute(
                    "UPDATE local_dungeons SET status = 'closed', closed_at = ? WHERE dungeon_uid = ?",
                    (int(self._clock()), dungeon_uid),
                )
            elif row["status"] not in {"closed", "balanced", "failed"}:
                return 409, {"error": "dungeon_not_active"}
            # Closing a terminal/previously closed record is idempotent.  It
            # never reopens the UID or clears its persisted active state.
        return 200, {}

    @staticmethod
    def _player_level_exp(profile: Mapping[str, Any]) -> tuple[int, int]:
        level = max(1, int(profile.get("level", 1)))
        exp = 0
        for resource in profile.get("resources", []):
            if isinstance(resource, Mapping) and int(resource.get("type", 0)) == 1:
                exp = int(resource.get("count", 0))
                break
        return level, max(0, exp)

    @staticmethod
    def _apply_exp_delta(
        profile: Mapping[str, Any], exp_delta: int
    ) -> tuple[int, int, int, int]:
        """Apply a local EXP delta to an in-memory profile value."""

        level_before, exp_before = LocalDungeons._player_level_exp(profile)
        delta = max(0, int(exp_delta))
        if delta == 0:
            return level_before, exp_before, level_before, exp_before
        resources = [
            dict(item)
            for item in profile.get("resources", [])
            if isinstance(item, Mapping)
        ]
        exp_resource = None
        for resource in resources:
            try:
                if int(resource.get("type", 0)) == 1:
                    exp_resource = resource
                    break
            except (TypeError, ValueError):
                continue
        if exp_resource is None:
            exp_resource = {"type": 1, "id": 0, "count": exp_before, "param": 0}
            resources.append(exp_resource)
        level_after, exp_after = _roll_player_level(level_before, exp_before + delta)
        exp_resource["type"] = 1
        exp_resource.setdefault("id", 0)
        exp_resource.setdefault("param", 0)
        exp_resource["count"] = exp_after
        if level_after > level_before and PLAYER_LEVELS:
            # Level rewards (PlayerLevelInitConfig.levelReward, energy for the
            # early levels) are granted for every level gained.
            for reached in range(level_before + 1, level_after + 1):
                for reward in PLAYER_LEVELS.get(reached, {}).get("levelReward", []) or []:
                    if not isinstance(reward, Mapping):
                        continue
                    r_type = int(reward.get("type", 0) or 0)
                    r_id = int(reward.get("id", 0) or 0)
                    r_count = int(reward.get("count", 0) or 0)
                    if r_type <= 0 or r_count <= 0:
                        continue
                    target = next(
                        (item for item in resources
                         if int(item.get("type", -1)) == r_type and int(item.get("id", 0) or 0) == r_id),
                        None,
                    )
                    if target is None:
                        target = {"type": r_type, "id": r_id, "count": 0, "param": 0}
                        resources.append(target)
                    current = int(target.get("count", 0) or 0)
                    granted = current + r_count
                    # incInfo.limit is the hard cap for that resource at the
                    # new level; never reduce a balance already above it.
                    for inc in PLAYER_LEVELS.get(level_after, {}).get("incInfo", []) or []:
                        if isinstance(inc, Mapping) and int(inc.get("type", 0) or 0) == r_type and inc.get("limit"):
                            granted = max(current, min(granted, int(inc["limit"])))
                    target["count"] = granted
        if isinstance(profile, dict):
            profile["level"] = level_after
            profile["resources"] = resources
        return level_before, exp_before, level_after, exp_after

    def _grant_exp(self, profile: Mapping[str, Any], exp_delta: int) -> tuple[int, int, int, int]:
        """Persist a configured local EXP reward and return before/after values.

        Player EXP is resource type ``1`` in the extracted descriptor.  Keep
        the existing resource order and fields intact, adding/updating only
        that resource (plus level rewards).  Levels follow the client's
        PlayerLevelInitConfig thresholds; the stored EXP is the remainder
        within the current level.
        """

        updated_profile = dict(profile)
        level_before, exp_before, level_after, exp_after = self._apply_exp_delta(
            updated_profile, exp_delta
        )
        # A level-up can leave the in-level remainder equal to the old value
        # (0 -> Lv2 with 0 left), so compare both level and EXP.
        if (level_after, exp_after) == (level_before, exp_before):
            return level_before, exp_before, level_after, exp_after
        resources = updated_profile["resources"]
        connect = getattr(self.game, "_connect", self._connect)
        with self._lock, closing(connect()) as connection, connection:
            connection.execute(
                "UPDATE game_profiles SET level = ?, resources_json = ?, updated_at = ? WHERE account_id = ?",
                (
                    level_after,
                    json.dumps(resources, separators=(",", ":")),
                    int(self._clock()),
                    int(profile["account_id"]),
                ),
            )
        if isinstance(profile, dict):
            track_mapping(self.game, profile, "level", "resources")
            profile.update(level=level_after, resources=resources)
        return level_before, exp_before, level_after, exp_after

    @staticmethod
    def _coin_groups(tokens: Any) -> int:
        """Coins of the per-run groups ``Dungeon_<n>_7`` in a drop id list.

        Local rule: a group pays a tenth of stage n's first-clear coins (the
        group contents were server-only).  First-clear groups
        ``Dungeon_<n>_7_101`` are the displayed first-clear coins instead.
        """

        coins = 0
        for token in tokens or []:
            match = _COIN_GROUP_TOKEN.fullmatch(str(token))
            if match is None:
                continue
            source = STAGE_REWARD_ROWS.get(int(match.group(1))) or {}
            first = sum(
                int(item.get("count", 0) or 0)
                for item in source.get("dropDisplayFirst", []) or []
                if isinstance(item, Mapping) and int(item.get("type", 0) or 0) == RES_PLAYER_GOLD
            )
            coins += first // REPEAT_COIN_DIVISOR
        return coins

    @staticmethod
    def _has_exp_material_group(tokens: Any) -> bool:
        """A normal stage's undisplayed item group ``Dungeon_<n>_11_1``.

        For stages 1-261 that group is not shown in any drop list; the items
        whose ItemInfo.sources name ChapterNormal (Goto 207) are the five
        character EXP materials of STAGE_MATERIAL_POOL.  The 51xx/59xx side
        stages' ``_11_1`` groups are their displayed chips/materials.
        """

        for token in tokens or []:
            match = _ITEM_GROUP_TOKEN.fullmatch(str(token))
            if match is not None and int(match.group(1)) < 1000:
                return True
        return False

    def _grant_battle_rewards(
        self, profile: Mapping[str, Any], dungeon_id: int, *, first_finish: bool,
        team_uids: Optional[list[int]] = None, multi_power: int = 1,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Grant a won stage's character EXP and drops.

        Returns ``(resChange entries, expChange entries)`` for DungeonBalance.
        - Character EXP: DungeonReward.expActor to main slots (index 0-2) and
          expAssistant to assist slots (3-5) of the team that fought, x
          multiPower, levelled with ActorLevelup (cap: player level, Misc 60).
        - First clear: every DungeonReward.dropDisplayFirst entry (coins,
          ResPlayerMoney, star-up materials, chips, tickets ...) except player
          EXP (type 1, paid by _grant_exp from the energy spent), plus the
          per-run coin groups listed in dropIdFirst.
        - Repeat clear (x multiPower): the per-run coin groups of dropId and
          each dropDisplay/dropDisplayOutside entry with REPEAT_DROP_CHANCE.
        - Every win of a normal stage with a ``Dungeon_<n>_11_1`` group: one
          EXP material (x multiPower).  Equipment: equipment.py's chapter
          pool (100% first clear, 25% per repeat run).
        """

        row = STAGE_REWARD_ROWS.get(int(dungeon_id)) or DUNGEON_CONFIG.reward_row(dungeon_id)
        systems = getattr(self.game, "systems", None)
        if row is None or systems is None:
            return [], []
        multi_power = max(1, int(multi_power or 1))
        runs = 1 if first_finish else multi_power
        rng = getattr(self, "_drop_rng", None) or random
        res_changes: list[dict[str, Any]] = []
        exp_changes: list[dict[str, Any]] = []
        state = systems._state(profile)
        profit_rules = self._guild_profit_rules(profile)
        dungeon_type = _dungeon_type(dungeon_id)

        # Character EXP for the team that fought (slot index = position in
        # the stored team; older rows fall back to the saved group).
        if team_uids and len(team_uids) == 6:
            members = [{"actorUid": uid, "index": index} for index, uid in enumerate(team_uids)]
        else:
            groups = systems._normalise_actor_groups(state.get("actorGroups")) or systems._default_actor_groups()
            selected = int((state.get("selectedGroupIds") or {}).get("storymode", 1) or 1)
            members = (groups.get(str(selected)) or groups.get("1") or {}).get("members", [])
        for member in members:
            uid = int(member.get("actorUid", 0) or 0)
            actor = actor_store.get(profile, state, uid) if uid > 0 else None
            if actor is None:
                continue
            gain = int(row.get("expActor" if int(member.get("index", 0)) < 3 else "expAssistant", 0) or 0) * runs
            gain = self._profit_scaled(
                gain, profit_rules, dungeon_type=dungeon_type, profit_type=3, rng=rng,
            )
            if gain <= 0:
                continue
            level_before = max(1, int(actor.get("level", 1) or 1))
            exp_before = max(0, int(actor.get("exp", 0) or 0))
            # The client caps character level at the player's level.
            cap = max(1, min(MAX_ACTOR_LEVEL, int(profile.get("level", 1) or 1)))
            level_after, exp_after = _roll_actor_level(level_before, exp_before + gain, cap=cap)
            actor["level"], actor["exp"] = level_after, exp_after
            exp_changes.append({
                "actorUid": uid, "level": level_before, "exp": exp_before,
                "levelNew": level_after, "expNew": exp_after, "addExp": gain,
            })
        # Equipment (equipment.py: chapter-tier pool, 100% first clear / 25%
        # per repeat run).
        for _ in range(runs):
            equip_id = equipment.roll_stage_equipment(int(dungeon_id), first_finish, getattr(self, "_equip_rng", None))
            if equip_id:
                equip_count = self._profit_scaled(
                    1, profit_rules, dungeon_type=dungeon_type, profit_type=8,
                    res_type=RES_EQUIP, rng=rng,
                )
                for _ in range(equip_count):
                    res_changes.append(equipment.grant_equipment(self.game, profile, equip_id, state=state))
        systems._save(profile, state)

        drops: dict[tuple[int, int], dict[str, int]] = {}

        def add(rtype: int, rid: int, count: int) -> None:
            if rtype <= 0 or count <= 0 or rtype == RES_PLAYER_EXP:
                return
            entry = drops.setdefault((rtype, rid), {"type": rtype, "id": rid, "param": 0, "count": 0})
            entry["count"] += count

        if first_finish:
            for item in row.get("dropDisplayFirst", []) or []:
                if isinstance(item, Mapping):
                    add(int(item.get("type", 0) or 0), int(item.get("id", 0) or 0), int(item.get("count", 0) or 0))
            add(RES_PLAYER_GOLD, 0, self._coin_groups(row.get("dropIdFirst")))
            tokens = row.get("dropIdFirst") or row.get("dropId")
        else:
            add(RES_PLAYER_GOLD, 0, self._coin_groups(row.get("dropId")) * runs)
            tokens = row.get("dropId")
            if row.get("dropId"):
                seen: set[tuple[int, int]] = set()
                for item in list(row.get("dropDisplay", []) or []) + list(row.get("dropDisplayOutside", []) or []):
                    if not isinstance(item, Mapping):
                        continue
                    key = (int(item.get("type", 0) or 0), int(item.get("id", 0) or 0))
                    if key in seen or key[0] in (RES_PLAYER_EXP, RES_PLAYER_GOLD):
                        continue
                    seen.add(key)
                    for _ in range(runs):
                        if rng.random() < REPEAT_DROP_CHANCE:
                            add(key[0], key[1], int(item.get("count", 0) or 0))
        if STAGE_MATERIAL_POOL and self._has_exp_material_group(tokens):
            add(RES_ITEM, STAGE_MATERIAL_POOL[(int(dungeon_id) - 1) % len(STAGE_MATERIAL_POOL)], runs)
        for entry in drops.values():
            item_id = int(entry.get("id", 0) or 0)
            entry["count"] = self._profit_scaled(
                int(entry.get("count", 0) or 0), profit_rules,
                dungeon_type=dungeon_type, profit_type=8, res_type=RES_ITEM,
                func_type=ITEM_FUNC_TYPES.get(item_id, 0), rng=rng,
            )
        rewards = list(drops.values())
        welfare = getattr(self.game, "welfare", None)
        if welfare is not None:
            rewards = welfare.replace_chip_rewards(profile, dungeon_id, rewards)
        res_changes.extend(self._grant(profile, rewards))
        if welfare is not None:
            res_changes.extend(welfare.grant_battle_materials(
                profile, dungeon_id, dungeon_type=dungeon_type, runs=runs))
        return res_changes, exp_changes

    def _guild_profit_rules(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Read active guild ProfitDetail rows without making rewards fragile.

        Guild state is optional for old saves and isolated dungeon tests.  A
        missing module, an expired skill, or a malformed row must leave the
        canonical reward calculation unchanged.
        """

        guild = getattr(self.game, "guild", None)
        reader = getattr(guild, "active_profit_rules", None)
        if not callable(reader):
            return []
        try:
            rows = reader(profile)
        except Exception:
            return []
        if not isinstance(rows, (list, tuple)):
            return []
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    @staticmethod
    def _profit_scaled(
        base: int,
        rules: list[Mapping[str, Any]],
        *,
        dungeon_type: int,
        profit_type: int = 0,
        res_type: int = 0,
        func_type: int = 0,
        rng: Any = None,
    ) -> int:
        """Apply active ratio ProfitDetail rows to one reward quantity.

        ProfitDetail ratios use the same 10,000 denominator as the guild
        donation settlement table.  Fractional quantities are carried as a
        deterministic extra unit when the supplied RNG hits the remainder;
        this preserves integer resource ledgers while avoiding systematic
        truncation of a 15%/20% skill on one-unit drops.
        """

        quantity = max(0, int(base or 0))
        if quantity <= 0:
            return quantity
        for rule in rules:
            allowed = rule.get("dungeonType", rule.get("dungeon_type", []))
            if isinstance(allowed, (int, str)):
                allowed = [allowed]
            try:
                if allowed and int(dungeon_type) not in {int(value) for value in allowed}:
                    continue
            except (TypeError, ValueError):
                continue
            try:
                rule_type = int(rule.get("type", 0) or 0)
                target_res = int(rule.get("resType", rule.get("res_type", 0)) or 0)
                target_func = int(rule.get("funcType", rule.get("func_type", 0)) or 0)
                add = int(rule.get("add", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if profit_type and rule_type != int(profit_type):
                continue
            if target_res and target_res != int(res_type):
                continue
            if target_func and target_func != int(func_type):
                continue
            if add <= 0:
                continue
            if bool(rule.get("isRatio", rule.get("is_ratio", False))):
                numerator = quantity * add
                whole, remainder = divmod(numerator, 10000)
                if remainder and rng is not None and float(rng.random()) < (remainder / 10000.0):
                    whole += 1
                quantity += whole
            else:
                quantity += add
        return quantity

    @staticmethod
    def _team_statistics(active: Any) -> tuple[Any, Any]:
        """(own, enemy) TeamStatistic of a FightActiveData, or (None, None).

        teamList[].team names the side (1 = the player, FightSelfTeam);
        without it the first entry is the player's (the gold balance reads the
        enemy dead list from teamList[2]).
        """

        if active is None:
            return None, None
        teams = [team for team in getattr(active, "teamList", []) if team.HasField("statistic")]
        if not teams:
            return None, None
        own = next((team for team in teams if int(team.team) == 1), None)
        enemy = next((team for team in teams if int(team.team) == 2), None)
        if own is None or enemy is None:
            own, enemy = teams[0], (teams[1] if len(teams) > 1 else None)
        return own.statistic, (enemy.statistic if enemy is not None else None)

    @staticmethod
    def _target_value(target_type: int, params: list[int], own: Any, enemy: Any) -> int:
        """Logic/Target/Target<Type>.Update(statisticSelf, statisticEnemy)."""

        enemy_dead = list(getattr(enemy, "deadInfo", []) or []) if enemy is not None else []
        enemy_init = int(getattr(enemy, "initCount", 0) or 0) if enemy is not None else 0
        extra = params[2] if len(params) > 2 else 0
        if target_type == 1:  # LinkSkill
            return len(own.linkIdList)
        if target_type == 2:  # Round
            return int(own.gameRound)
        if target_type == 3:  # DeadActor
            return len(own.deadInfo)
        if target_type == 4:  # TotalBeHurt
            return int(own.totalDamage)
        if target_type == 5:  # BigSkill
            return len(own.bigSkillList)
        if target_type == 6:  # CardNum
            return sum(1 for value in own.cardNumList if (int(value) % 10000) // 10 == extra)
        if target_type == 7:  # KillWinType
            if enemy_dead and enemy_init == len(enemy_dead):
                return int(enemy_dead[-1].kind)
            return -1
        if target_type == 8:  # KillBySkill
            return sum(1 for dead in enemy_dead if int(dead.kind) == extra)
        if target_type == 9:  # KillBySequence
            if enemy_dead:
                if extra == 1:
                    return int(enemy_dead[0].seat)
                if extra == 2 and enemy_init == len(enemy_dead):
                    return int(enemy_dead[-1].seat)
            return 0
        if target_type == 10:  # ClearBuff
            source = enemy if extra == 2 else own
            return sum(1 for value in getattr(source, "buffKindList", []) or [] if int(value) != 0)
        raise ValueError(target_type)

    @staticmethod
    def _compare(value: int, compare: int, operation: int) -> bool:
        """FightHelp.compareValue with the PB Operation enum."""

        if operation == 0:
            return value == compare
        if operation == 1:
            return value != compare
        if operation == 2:
            return value < compare
        if operation == 3:
            return value <= compare
        if operation == 4:
            return value > compare
        if operation == 5:
            return value >= compare
        return False

    def _targets_done(
        self,
        dungeon_id: int,
        active: Any,
        body: bytes | bytearray | str | Mapping[str, Any] | None,
        *,
        won: bool,
    ) -> list[int]:
        """Star targets completed by a run (balance ``targetsDone``).

        Evaluated exactly like Logic/Target/TargetLogic.checkTarget over the
        TeamStatistic in the FightActiveData the client PATCHed.  When the
        fight data carries no statistics (local tools), a win completes every
        target.  A JSON body may still name the targets explicitly
        (``targetsDone`` / ``stars``; the client itself posts ``{}``).
        """

        if not won:
            return []
        targets = [
            target for target in (DUNGEON_CONFIG.reward_row(dungeon_id) or {}).get("target", []) or []
            if isinstance(target, Mapping) and int(target.get("id", 0) or 0) > 0
        ]
        valid = [int(target["id"]) for target in targets]
        request = body if isinstance(body, Mapping) else _body_object(body)
        raw = request.get("targetsDone", request.get("targets"))
        if isinstance(raw, (str, int)):
            raw = [raw]
        if isinstance(raw, list):
            chosen: list[int] = []
            for item in raw:
                try:
                    target_id = int(item.get("id")) if isinstance(item, Mapping) else int(item)
                except (TypeError, ValueError):
                    continue
                if target_id in valid and target_id not in chosen:
                    chosen.append(target_id)
            return chosen
        raw_stars = request.get("stars", request.get("star", request.get("starCount")))
        if raw_stars is not None:
            try:
                return valid[: max(0, min(len(valid), int(raw_stars)))]
            except (TypeError, ValueError):
                pass
        own, enemy = self._team_statistics(active)
        if own is None:
            return valid
        done: list[int] = []
        for target in targets:
            params = _int_list(target.get("param"))
            if len(params) < 2:
                continue
            try:
                value = self._target_value(int(target.get("type", 0) or 0), params, own, enemy)
            except (ValueError, AttributeError, TypeError):
                continue
            if self._compare(value, params[0], params[1]):
                done.append(int(target["id"]))
        return done

    @staticmethod
    def _battle_stats(active: Any) -> dict[str, Any]:
        """Task counters from the fight statistics (tasks.on_battle_win)."""

        own, enemy = LocalDungeons._team_statistics(active)
        if own is None:
            return {}
        actor_seats = {}
        teams = list(getattr(active, "teamList", []) or []) if active is not None else []
        own_team = next((team for team in teams if _int(getattr(team, "team", 0)) == 1), None)
        if own_team is not None:
            for player in getattr(own_team, "playerList", []) or []:
                for actor in getattr(player, "actorList", []) or []:
                    info = getattr(actor, "actorInfo", None)
                    actor_id = _int(getattr(info, "id", 0)) if info is not None else 0
                    seat = _int(getattr(actor, "baseSeat", 0) or getattr(actor, "seat", 0))
                    if actor_id and seat:
                        actor_seats[actor_id] = seat
        def plain_dead(values):
            return [{"id": _int(getattr(v, "id", 0)), "seat": _int(getattr(v, "seat", 0)),
                     "kind": _int(getattr(v, "kind", 0))} for v in (values or [])]
        return {
            "linkSkills": len(own.linkIdList),
            "bigSkills": len(own.bigSkillList),
            "kills": len(enemy.deadInfo) if enemy is not None else 0,
            "rounds": int(own.gameRound),
            "recordHpList": list(getattr(own, "recordHpList", []) or []),
            "ownDeadInfo": plain_dead(getattr(own, "deadInfo", [])),
            "enemyDeadInfo": plain_dead(getattr(enemy, "deadInfo", [])) if enemy is not None else [],
            "ownBuffKindList": list(getattr(own, "buffKindList", []) or []),
            "enemyBuffKindList": list(getattr(enemy, "buffKindList", []) or []) if enemy is not None else [],
            "actorSeats": actor_seats,
        }

    @staticmethod
    def _story_battle_exp_reward(dungeon_id: int) -> int:
        rewards = _LOCAL_DUNGEON_POLICY.get("story_player_exp", {})
        try:
            return max(0, int(rewards.get(str(int(dungeon_id)), 0)))
        except (AttributeError, TypeError, ValueError):
            return 0

    @staticmethod
    def _nonfight_exp_reward(dungeon_id: int) -> int:
        # Non-fight story stages cost no energy, so they grant no player EXP.
        if int(dungeon_id) in STAGE_ENERGY_COST:
            return STAGE_ENERGY_COST[int(dungeon_id)]
        rewards = _LOCAL_DUNGEON_POLICY.get("tutorial_player_exp", {})
        try:
            return max(0, int(rewards.get(str(int(dungeon_id)), 0)))
        except (AttributeError, TypeError, ValueError):
            return 0

    @staticmethod
    def _configured_player_exp(dungeon_id: int) -> int | None:
        """Read an explicit player EXP field when a recovered row has one.

        The checked-in DungeonReward sample exposes actor EXP and display drops,
        but no unambiguous player EXP field.  Those values are deliberately not
        guessed from actor EXP; callers use the reviewed local fallback below.
        """
        row = DUNGEON_CONFIG.reward_row(dungeon_id)
        if not row:
            return None
        for key in ("expPlayer", "playerExp", "exp_player"):
            try:
                value = int(row.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    @staticmethod
    def _battle_exp_reward(dungeon_id: int) -> int:
        """Return configured player EXP or a documented local fallback.

        DungeonReward has no player EXP field.  Player EXP is the energy the
        stage consumes (entry cost + on-win cost): this matches the client's
        level table exactly (a 6-energy normal stage, 6 EXP per early level,
        Lv2 at 1 EXP after the 1-energy first story battle).
        """

        configured = LocalDungeons._configured_player_exp(dungeon_id)
        if configured is not None:
            return configured
        if int(dungeon_id) in STAGE_ENERGY_COST:
            return STAGE_ENERGY_COST[int(dungeon_id)]
        story_reward = LocalDungeons._story_battle_exp_reward(dungeon_id)
        if story_reward:
            return story_reward
        if int(dungeon_id) in ALL_NORMAL_DUNGEON_ID_SET:
            try:
                return max(0, int(_LOCAL_DUNGEON_POLICY.get("normal_player_exp", 0)))
            except (AttributeError, TypeError, ValueError):
                return 0
        return 0

    def _balance_info(
        self,
        profile: Mapping[str, Any],
        *,
        won: bool,
        balance_time: int,
        first_finish: bool,
        exp_delta: int = 0,
        level_before: Optional[int] = None,
        exp_before: Optional[int] = None,
        stars: int = 0,
        targets_done: Optional[list[int]] = None,
        difficulty: str = "normal",
    ) -> dict[str, Any]:
        """Return every field read by DungeonBalance Lua.

        The response includes a player EXP resource record.  ``exp_delta``
        comes from the reviewed local policy only when the recovered row has
        no unambiguous player EXP field; callers pass the profile after
        persistence so the count and level match the next login bootstrap.
        """

        level, exp = self._player_level_exp(profile)
        if level_before is None:
            level_before = level
        if exp_before is None:
            exp_before = max(0, exp - max(0, int(exp_delta)))
        # The client derives "EXP before battle" as count - countDelta and then
        # subtracts the thresholds of the levels gained itself, so ``count``
        # is the pre-rollover value (before + delta).  Without a level-up this
        # equals the stored in-level EXP.
        resource_change = {
            "baseRes": {
                "type": 1,
                "id": 0,
                "count": int(exp_before) + max(0, int(exp_delta)) if exp_delta else exp,
                "param": 0,
                "countDelta": int(exp_delta),
            }
        }
        return {
            "balanced": True,
            "balanceTime": int(balance_time),
            "targetsDone": list(targets_done or []),
            "resChange": [resource_change],
            "expChange": [],
            "favourChange": [],
            "playerLevel": level,
            "playerLevelBeforeBalance": int(level_before),
            "win": bool(won),
            "firstFinish": bool(first_finish),
            "profits": [],
            "loseNum": 0,
            "expReturn": [],
            "rewardProvenance": str(_LOCAL_DUNGEON_POLICY.get("provenance", "local-sandbox")),
            # These keys are consumed by the local JSON UI.  The typed
            # chapter protobuf intentionally omits them because the captured
            # descriptor has no star field on DungeonRecord.
            "stars": max(0, min(3, int(stars))),
            "star": max(0, min(3, int(stars))),
            "difficulty": str(difficulty or "normal"),
        }

    def _terminal_balance_fallback(
        self,
        profile: Mapping[str, Any],
        row: sqlite3.Row,
    ) -> Optional[Response]:
        """Reconstruct a deterministic response for pre-cache terminal rows."""

        if row["status"] not in {"balanced", "failed"}:
            return None
        won = row["status"] == "balanced"
        if int(row["dungeon_id"]) in FIGHT_DUNGEON_IDS and row["active_data"]:
            active = self._decode_active_bytes(bytes(row["active_data"]))
            won = bool(active is not None and int(active.winTeam) == 1)
        parts = self._snapshot_parts(profile)
        balance_info = self._balance_info(
            profile,
            won=won,
            balance_time=int(row["closed_at"] or self._clock()),
            first_finish=False,
        )
        payload = {
            "chapterSummaryInfo": self._snapshot_from_parts(parts, compact=True)["summary"],
            "chapterInfo": self._changed_chapter_infos(None, parts, int(row["dungeon_id"]))[:1],
            "balanceInfo": balance_info,
            "resChange": list(balance_info["resChange"]),
        }
        return 200, payload

    def _claim_terminal(
        self,
        profile: Mapping[str, Any],
        dungeon_uid: int,
        *,
        won: bool,
        balance_time: int,
        stars: int = 0,
        targets_done: Optional[list[int]] = None,
    ) -> bool:
        """Mark an active run terminal before paying anything (idempotency)."""

        with self._lock, closing(self._connect()) as connection, connection:
            updated = connection.execute(
                "UPDATE local_dungeons SET status = ?, closed_at = ?, terminal_outcome = ?, stars = ?, targets_json = ? "
                "WHERE dungeon_uid = ? AND account_id = ? AND status = 'active'",
                (
                    "balanced" if won else "failed",
                    balance_time,
                    1 if won else 2,
                    stars if won else 0,
                    json.dumps(list(targets_done or []), separators=(",", ":")),
                    dungeon_uid,
                    int(profile["account_id"]),
                ),
            )
        return updated.rowcount == 1

    def _store_balance(self, profile: Mapping[str, Any], dungeon_uid: int, payload: Mapping[str, Any]) -> None:
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_dungeons SET balance_json = ? WHERE dungeon_uid = ? AND account_id = ?",
                (json.dumps(payload, separators=(",", ":")), dungeon_uid, int(profile["account_id"])),
            )

    def _previous_finish(self, profile: Mapping[str, Any], dungeon_id: int, difficulty: str, exclude_uid: int) -> bool:
        difficulties = (difficulty, "challenge") if difficulty == "normal" else (difficulty, difficulty)
        with self._lock, closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM local_dungeons WHERE account_id = ? AND dungeon_id = ? AND difficulty IN (?, ?) "
                "AND status = 'balanced' AND dungeon_uid != ? LIMIT 1",
                (int(profile["account_id"]), int(dungeon_id), difficulties[0], difficulties[1], int(exclude_uid)),
            ).fetchone() is not None

    def _balance(
        self,
        profile: Mapping[str, Any],
        dungeon_uid: int,
        body: bytes | bytearray | str | Mapping[str, Any] | None = None,
    ) -> Response:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT dungeon_id, status, active_data, balance_json, closed_at, mode, activity_id, floor, node, difficulty, stars, targets_json, actor_uids_json, multi_power FROM local_dungeons WHERE dungeon_uid = ? AND account_id = ?",
                (dungeon_uid, int(profile["account_id"])),
            ).fetchone()
        if row is None:
            return 404, {"error": "dungeon_not_found"}
        if row["mode"] == "team":
            # Canonical /fci/dungeon/<uid>/balance is also accepted by the
            # local client shim; use the Team PVE reward path rather than the
            # generic special-dungeon energy path.
            return self._team_balance(profile, dungeon_uid, body)
        if row["status"] != "active":
            if row["balance_json"]:
                try:
                    cached = json.loads(row["balance_json"])
                except (TypeError, ValueError):
                    cached = None
                if isinstance(cached, dict):
                    return 200, cached
            fallback = self._terminal_balance_fallback(profile, row)
            if fallback is not None:
                return fallback
            return 409, {"error": "dungeon_not_active", "status": row["status"]}
        if row["mode"] and row["mode"] != "story":
            # Special modes use the same terminal FightActiveData seam when
            # the client has a battle result.  A JSON ``{"win": true}``
            # body is also accepted by local tools for simple deterministic
            # smoke tests; it never affects story stages.
            active = self._decode_active_bytes(bytes(row["active_data"])) if row["active_data"] else None
            won = bool(active is not None and int(active.winTeam) == 1)
            if not row["active_data"]:
                won = True
            if active is not None and int(active.winTeam) == 0:
                return 409, {"error": "battle_not_finished"}
            balance_time = int(self._clock())
            request = _body_object(body)
            # This call is intentionally kept body-free because _balance's
            # public seam historically receives only the uid.  A missing
            # active payload means an explicit local auto-win.
            mode = str(row["mode"])
            spec = SPECIAL_DUNGEONS.get(mode, SPECIAL_DUNGEONS["gold"])
            floor = int(row["floor"] or 1)
            node = int(row["node"] or 1)
            rewards = [dict(item) for item in spec.get("reward", [])]
            if mode == "tower":
                for item in rewards:
                    if int(item.get("type", 0)) == 7:
                        item["count"] = 1000 * floor
            if not won:
                rewards = []
            ok, reward_changes, error = self._resource_changes(profile, rewards)
            if not ok:
                return 409, {"error": error or "resource_update_failed"}
            payload = {
                "balanced": True,
                "balanceTime": balance_time,
                "win": won,
                "firstFinish": won and self._special_progress(profile, mode) < floor,
                "mode": mode,
                "activityId": row["activity_id"] or mode,
                "floor": floor,
                "node": node,
                "sandbox": True,
                "policySource": spec.get("source", "local-sandbox"),
                "provenance": spec.get("provenance", "local-sandbox"),
                "profits": reward_changes,
                "resChange": reward_changes,
                "balanceInfo": {
                    "balanced": True, "win": won, "firstFinish": won and self._special_progress(profile, mode) < floor,
                    "balanceTime": balance_time, "profits": reward_changes, "resChange": reward_changes,
                },
            }
            with self._lock, closing(self._connect()) as connection, connection:
                updated = connection.execute(
                    "UPDATE local_dungeons SET status = ?, closed_at = ?, terminal_outcome = ?, balance_json = ? WHERE dungeon_uid = ? AND account_id = ? AND status = 'active'",
                    ("balanced" if won else "failed", balance_time, 1 if won else 2, json.dumps(payload, separators=(",", ":")), dungeon_uid, int(profile["account_id"])),
                )
                if updated.rowcount != 1:
                    return 409, {"error": "dungeon_not_active"}
            return 200, payload
        dungeon_id = int(row["dungeon_id"])
        if dungeon_id in FIGHT_DUNGEON_IDS:
            active_data = row["active_data"]
            if not active_data:
                return 409, {
                    "error": "battle_not_finished",
                    "message": "FightActiveData PATCH is required before balance",
                }
            active = self._decode_active_bytes(bytes(active_data))
            if active is None:
                return 409, {"error": "invalid_fight_active_data"}
            # FightActiveData.winTeam is the client's actual terminal outcome.
            # A zero value means the fight is still in progress; do not infer a
            # win from a request count or from the configured monster row.
            win_team = int(active.winTeam)
            if win_team == 0:
                return 409, {"error": "battle_not_finished"}
            won = win_team == 1
            targets_done = self._targets_done(dungeon_id, active, body, won=won)
            stars = len(targets_done)
            difficulty = str(row["difficulty"] or "normal")
            multi_power = max(1, int(row["multi_power"] or 1))
            balance_time = int(self._clock())
            previous_finish = self._previous_finish(profile, dungeon_id, difficulty, dungeon_uid)
            before = self._snapshot_parts(profile)
            if not self._claim_terminal(profile, dungeon_uid, won=won, balance_time=balance_time,
                                        stars=stars, targets_done=targets_done):
                return 409, {"error": "dungeon_not_active"}
            energy_changes: list[dict[str, Any]] = []
            if won:
                # Player EXP equals the energy the run spent (cost + costWin).
                exp_reward = self._battle_exp_reward(dungeon_id) * multi_power
                level_before, exp_before, _, _ = self._grant_exp(profile, exp_reward)
                exp_delta = exp_reward
                energy_changes = self._pay_win_cost(profile, dungeon_id, multi_power)
            else:
                level_before, exp_before = self._player_level_exp(profile)
                exp_delta = 0
            balance_info = self._balance_info(
                profile,
                won=won,
                balance_time=balance_time,
                first_finish=won and not previous_finish,
                exp_delta=exp_delta,
                level_before=level_before,
                exp_before=exp_before,
                stars=stars,
                targets_done=targets_done,
                difficulty=difficulty,
            )
            if won:
                try:
                    team_uids = [int(value) for value in json.loads(row["actor_uids_json"] or "[]")]
                except (TypeError, ValueError, IndexError, KeyError):
                    team_uids = None
                reward_changes, exp_changes = self._grant_battle_rewards(
                    profile, dungeon_id, first_finish=not previous_finish, team_uids=team_uids,
                    multi_power=multi_power,
                )
                balance_info["resChange"].extend(energy_changes)
                balance_info["resChange"].extend(reward_changes)
                balance_info["expChange"] = exp_changes
                tasks = getattr(self.game, "tasks", None)
                if tasks is not None:
                    tasks.on_battle_win(
                        profile, dungeon_id,
                        STAGE_ENERGY_COST.get(dungeon_id, 0) * multi_power,
                        mode=self._task_mode(dungeon_id, difficulty),
                        stats=self._battle_stats(active),
                        actor_uids=team_uids or (),
                        dungeon_type=_dungeon_type(dungeon_id),
                    )
                    if multi_power > 1 and hasattr(tasks, "record"):
                        # TaskType_MultiPowerMTimes (main task 1113).
                        tasks.record(profile, "MultiPowerMTimes", 1)
                    tasks.on_level(profile)
            after = self._snapshot_parts(profile)
            payload = {
                "dungeonId": dungeon_id,
                "stars": stars,
                "star": stars,
                "difficulty": difficulty,
                "multiPower": multi_power,
                "chapterSummaryInfo": self._snapshot_from_parts(after, compact=True)["summary"],
                "chapterInfo": self._changed_chapter_infos(before, after, dungeon_id),
                "balanceInfo": balance_info,
                "resChange": list(balance_info["resChange"]),
            }
            self._store_balance(profile, dungeon_uid, payload)
            return 200, payload
        if dungeon_id not in NONFIGHT_STORY_IDS:
            return 501, {"error": "dungeon_balance_not_implemented"}
        # These rows are marked notFight in DungeonReward.  Return the
        # response containers the Lua exit path reads without claiming a
        # fabricated combat result.  They cost no energy, so no player EXP.
        nonfight_reward = self._nonfight_exp_reward(dungeon_id)
        balance_time = int(self._clock())
        previous_finish = self._previous_finish(profile, dungeon_id, "normal", dungeon_uid)
        before = self._snapshot_parts(profile)
        if not self._claim_terminal(profile, dungeon_uid, won=True, balance_time=balance_time):
            return 409, {"error": "dungeon_not_active"}
        if nonfight_reward:
            level_before, exp_before, _, _ = self._grant_exp(profile, nonfight_reward)
        else:
            level_before, exp_before = self._player_level_exp(profile)
        balance_info = self._balance_info(
            profile,
            won=True,
            balance_time=balance_time,
            first_finish=not previous_finish,
            exp_delta=nonfight_reward,
            level_before=level_before,
            exp_before=exp_before,
        )
        if not previous_finish:
            # A scene with first-clear rewards (none in the 2.0.4 tables, but
            # the data decides) pays them like a battle.
            reward_changes, _ = self._grant_battle_rewards(profile, dungeon_id, first_finish=True)
            balance_info["resChange"].extend(reward_changes)
        after = self._snapshot_parts(profile)
        payload = {
            "dungeonId": dungeon_id,
            "sandbox": True,
            "chapterSummaryInfo": self._snapshot_from_parts(after, compact=True)["summary"],
            "chapterInfo": self._changed_chapter_infos(before, after, dungeon_id),
            "balanceInfo": balance_info,
            "resChange": list(balance_info["resChange"]),
        }
        self._store_balance(profile, dungeon_uid, payload)
        return 200, payload

    def _pay_win_cost(self, profile: Mapping[str, Any], dungeon_id: int, multi_power: int) -> list[dict[str, Any]]:
        """Deduct DungeonReward.costWin x multiPower on a win (wrapped deltas).

        The entry already checked that cost + costWin was available; energy
        spent elsewhere meanwhile is clamped rather than refusing the win.
        """

        costs = self._stage_costs(dungeon_id, "costWin", multi_power)
        if not costs:
            return []
        balances = {
            (int(item.get("type", 0)), int(item.get("id", 0)), int(item.get("param", 0))): int(item.get("count", 0))
            for item in profile.get("resources", []) if isinstance(item, Mapping)
        }
        payable = []
        for item in costs:
            available = balances.get((item["type"], item["id"], item["param"]), 0)
            amount = min(-item["count"], max(0, available))
            if amount > 0:
                payable.append({**item, "count": -amount})
        if not payable:
            return []
        ok, deltas, _ = self._resource_changes(profile, payable)
        return self._wrap_res_changes(deltas) if ok else []

    @staticmethod
    def _task_mode(dungeon_id: int, difficulty: str) -> str:
        """tasks.on_battle_win ``mode`` for a chapter stage.

        Story chapters report "story", the rest "normal" ("hard" for the
        hard track): main tasks such as BALANCE_P0_DUNGEON_M_TIMES [11012]
        name challenge/training stages, and tasks.py counts those goals only
        for story-like modes.
        """

        if str(difficulty or "normal") == "hard":
            return "hard"
        return "story" if _dungeon_type(dungeon_id) == DUNGEON_TYPE_STORY else "normal"

    def _sweep(
        self,
        profile: Mapping[str, Any],
        dungeon_id: int,
        body: bytes | bytearray | str | None,
    ) -> Response:
        """Run a bounded local sweep after a difficulty-specific clear.

        No official sweep contract or drop table survived in the checked-in
        evidence.  The endpoint therefore grants only the reviewed local EXP
        fallback and advertises ``sandbox``/``provenance`` in every response.
        """

        request = _body_object(body)
        difficulty_value = request.get("difficulty", request.get("modeDifficulty", None))
        difficulty = str(difficulty_value or "normal").strip().lower()
        if difficulty in {"1", "2", "hardmode", "difficult"}:
            difficulty = "hard"
        if difficulty not in {"normal", "hard", "challenge"}:
            return 400, {"error": "invalid_difficulty", "allowed": ["normal", "hard", "challenge"]}
        if difficulty == "challenge":
            difficulty = "normal"
        try:
            count = max(1, min(10, int(request.get("count", request.get("times", 1)))))
        except (TypeError, ValueError):
            count = 1
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT MAX(stars) AS stars FROM local_dungeons WHERE account_id = ? AND dungeon_id = ? AND difficulty IN (?, ?) AND status = 'balanced'",
                (int(profile["account_id"]), int(dungeon_id), difficulty, "challenge" if difficulty == "normal" else difficulty),
            ).fetchone()
        if row is None or row["stars"] is None:
            return 409, {
                "error": "sweep_locked",
                "message": "clear this dungeon at the requested difficulty first",
                "dungeonId": int(dungeon_id),
                "difficulty": difficulty,
                "sandbox": True,
            }
        exp_delta = self._battle_exp_reward(int(dungeon_id)) * count
        if exp_delta:
            level_before, exp_before, _, _ = self._grant_exp(profile, exp_delta)
        else:
            level_before, exp_before = self._player_level_exp(profile)
        level_after, exp_after = self._player_level_exp(profile)
        changes = []
        if exp_delta:
            changes = [{"type": 1, "id": 0, "param": 0, "count": exp_after, "countDelta": exp_delta}]
        return 200, {
            "sandbox": True,
            "provenance": "local-sandbox-no-official-sweep-table",
            "dungeonId": int(dungeon_id),
            "difficulty": difficulty,
            "count": count,
            "stars": int(row["stars"] or 0),
            "firstFinish": False,
            "resChange": changes,
            "rewards": changes,
            "balanceInfo": {
                "balanced": True,
                "win": True,
                "sweep": True,
                "count": count,
                "playerLevel": level_after,
                "playerLevelBeforeBalance": level_before,
                "resChange": changes,
                "expBefore": exp_before,
                "expAfter": exp_after,
            },
        }

    def _decode_active_bytes(self, raw: bytes):
        """Decode a canonical FightActiveData protobuf payload."""

        message_type = getattr(self.game, "_types", {}).get("FightActiveData")
        if message_type is None:
            return None
        message = message_type()
        try:
            message.ParseFromString(raw)
        except (TypeError, ValueError):
            return None
        return message

    def _parse_active(self, body: bytes | bytearray | str | Mapping[str, Any] | None):
        """Accept the observed JSON test seam or raw FightActiveData bytes."""

        message_type = getattr(self.game, "_types", {}).get("FightActiveData")
        if message_type is None:
            return None
        message = message_type()
        if isinstance(body, Mapping):
            try:
                json_format.ParseDict(dict(body), message)
            except (TypeError, ValueError):
                return None
            return message
        if isinstance(body, str):
            try:
                body = body.encode("utf-8")
            except UnicodeEncodeError:
                return None
        if body is None:
            return None
        raw = bytes(body)
        if not raw:
            return None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = None
        if isinstance(value, Mapping):
            try:
                json_format.ParseDict(dict(value), message)
            except (TypeError, ValueError):
                return None
            return message
        return self._decode_active_bytes(raw)

    def _active(self, profile: Mapping[str, Any], dungeon_uid: int, body: bytes | bytearray | str | None) -> Response:
        with self._lock, closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT dungeon_id, status, mode FROM local_dungeons WHERE dungeon_uid = ? AND account_id = ?",
                (dungeon_uid, int(profile["account_id"])),
            ).fetchone()
            if row is None:
                return 404, {"error": "dungeon_not_found"}
            if row["mode"] == "story" and int(row["dungeon_id"]) not in PLAYABLE_DUNGEON_IDS:
                return 501, {"error": "dungeon_active_not_implemented"}
            if row["status"] != "active":
                return 409, {"error": "dungeon_not_active"}
            message = self._parse_active(body)
            if message is None:
                return 400, {"error": "invalid_fight_active_data"}
            encoded = message.SerializeToString()
            connection.execute(
                "UPDATE local_dungeons SET active_data = ?, active_updated_at = ? WHERE dungeon_uid = ? AND account_id = ?",
                (encoded, int(self._clock()), dungeon_uid, int(profile["account_id"])),
            )
        # The client ignores the body of the active PATCH callback.  An empty
        # object acknowledges persistence without claiming a battle result.
        return 200, {}

    def handle_http(
        self,
        method: str,
        path: str,
        body: bytes | bytearray | str | None,
        profile: Mapping[str, Any],
    ) -> Optional[Response]:
        route = _route(path)
        method = method.upper()
        team_result = self._team_http(method, route, body, profile)
        if team_result is not None:
            return team_result
        # The original client uses feature-specific GoldDungeon*/Tower*/Maze*
        # and Tournament* resources.  Their retired descriptors are not
        # available, so expose a small JSON compatibility seam alongside the
        # canonical /fci/dungeon/ state machine.  All mutations still use the
        # same authenticated profile and persisted rows.
        special_route_mode: Optional[str] = None
        special_route_prefix: Optional[str] = None
        for prefix, mode in (
            ("/fci/gold-dungeon", "gold"), ("/fci/golddungeon", "gold"),
            ("/fci/tower", "tower"), ("/fci/maze", "maze"),
            ("/fci/tournament", "tournament"),
        ):
            if route.lower().startswith(prefix + "/") or route.lower() == prefix + "/":
                special_route_mode = mode
                special_route_prefix = prefix
                break
        if special_route_mode is not None:
            suffix = route[len(special_route_prefix or "") :].strip("/").lower()
            if suffix.endswith("/balance"):
                uid_text = suffix[: -len("/balance")].strip("/")
                if uid_text.isdigit():
                    return self._balance(profile, int(uid_text), body)
            if suffix.endswith("/active"):
                uid_text = suffix[: -len("/active")].strip("/")
                if uid_text.isdigit():
                    return self._active(profile, int(uid_text), body) if method == "PATCH" else (405, {"error": "method_not_allowed"})
            if suffix.isdigit() and method == "GET":
                # Reuse the canonical run-record response by dispatching via
                # the normal dungeon resource path.
                return self.handle_http("GET", f"/fci/dungeon/{suffix}", body, profile)
            if suffix in {"", "info", "status", "list"} and method == "GET":
                return self._special_info(profile, special_route_mode)
            if method == "POST" and suffix in {"", "enter", "start", "open"}:
                request = dict(_body_object(body))
                request.setdefault("mode", special_route_mode)
                return self._enter(profile, json.dumps(request, separators=(",", ":")))
            return 405, {"error": "method_not_allowed"}
        for exchange_prefix in ("/fci/starexchange/", "/fci/star-exchange/"):
            if route.lower().startswith(exchange_prefix):
                # Window ChapterStarExchange: GET fci/starexchange and
                # POST fci/starexchange/<starNum>.
                exchange_suffix = route[len(exchange_prefix):].strip("/")
                if exchange_suffix and not exchange_suffix.isdigit():
                    return 404, {"error": "star_exchange_not_found"}
                return self._star_exchange(profile, method, exchange_suffix or None)
        if route == "/fci/dungeon/chapter-summary/":
            # WidgetChapterRewardEntry BindRemote(fci/dungeon/chapter-summary).
            if method != "GET":
                return 405, {"error": "method_not_allowed"}
            return 200, self.chapter_summary(profile)
        if route == "/fci/dungeon/":
            return self._enter(profile, body) if method == "POST" else (405, {"error": "method_not_allowed"})
        if route == "/fci/dungeon/chapter/":
            if method != "GET":
                return 405, {"error": "method_not_allowed"}
            # The login bootstrap keeps the historical compact graph, while
            # the explicit chapter resource exposes every locally configured
            # chapter for the full offline map.
            return 200, self.chapter_snapshot(profile, compact=False)
        if route.startswith("/fci/dungeon/stage/"):
            stage_suffix = route[len("/fci/dungeon/stage/") :].strip("/")
            if not stage_suffix.isdigit():
                return 404, {"error": "stage_not_found"}
            if method != "GET":
                return 405, {"error": "method_not_allowed"}
            # This is a local inspection seam, backed only by the partial
            # DungeonMonster export.  It is intentionally separate from the
            # historical protobuf contract so missing rows stay explicit.
            return 200, _stage_metadata(int(stage_suffix))
        if route.startswith("/fci/dungeon/story/"):
            story_suffix = route[len("/fci/dungeon/story/") :].strip("/")
            if not story_suffix.isdigit():
                return 404, {"error": "story_not_found"}
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            # StoryDungeon is a partial captured table.  This inspection seam
            # exposes only the observed enemy rows and never fabricates story
            # rewards, unlocks, or missing battle records.
            return 200, _story_metadata(int(story_suffix))
        chapter_prefix = "/fci/dungeon/chapter/"
        if route.startswith(chapter_prefix):
            chapter_suffix = route[len(chapter_prefix) :].strip("/")
            if chapter_suffix.lower() in {"full", "all"}:
                if method != "GET":
                    return 405, {"error": "method_not_allowed"}
                return 200, self.chapter_snapshot(profile, compact=False)
            chapter_parts = chapter_suffix.split("/") if chapter_suffix else []
            if not chapter_parts or not chapter_parts[0].isdigit():
                return None
            chapter_id = int(chapter_parts[0])
            if len(chapter_parts) > 1 and chapter_parts[1].lower() in {"reward", "rewards", "claim", "chest"}:
                if method not in {"GET", "POST"}:
                    return 405, {"error": "method_not_allowed"}
                claim_body = body
                if chapter_parts[1].lower() == "chest":
                    request = dict(_body_object(body))
                    request.setdefault("kind", "chest")
                    claim_body = json.dumps(request, separators=(",", ":"))
                return self._claim_chapter_reward(profile, chapter_id, claim_body, read_only=method == "GET")
            if method != "GET":
                return 405, {"error": "method_not_allowed"}
            return 200, self.chapter_detail(profile, chapter_id)
        prefix = "/fci/dungeon/"
        if not route.startswith(prefix):
            return None
        suffix = route[len(prefix) :].strip("/")
        if suffix.endswith("/sweep"):
            dungeon_text = suffix[: -len("/sweep")].strip("/")
            if not dungeon_text.isdigit():
                return None
            if method != "POST":
                return 405, {"error": "method_not_allowed"}
            return self._sweep(profile, int(dungeon_text), body)
        if suffix.endswith("/balance"):
            uid_text = suffix[: -len("/balance")].strip("/")
            if not uid_text.isdigit():
                return None
            return self._balance(profile, int(uid_text), body)
        if suffix.endswith("/active"):
            uid_text = suffix[: -len("/active")].strip("/")
            if not uid_text.isdigit():
                return None
            return self._active(profile, int(uid_text), body) if method == "PATCH" else (405, {"error": "method_not_allowed"})
        if not suffix.isdigit():
            return None
        dungeon_uid = int(suffix)
        if method == "DELETE":
            return self._close(profile, dungeon_uid)
        if method == "GET":
            with self._lock, closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT dungeon_id, actor_uids_json, status, created_at, closed_at, mode, activity_id, floor, node FROM local_dungeons WHERE dungeon_uid = ? AND account_id = ?",
                    (dungeon_uid, int(profile["account_id"])),
                ).fetchone()
            if row is None:
                return 404, {"error": "dungeon_not_found"}
            return 200, {
                "dungeonUid": dungeon_uid,
                "dungeonId": row["dungeon_id"],
                "actorUid": json.loads(row["actor_uids_json"]),
                "status": row["status"],
                "createTime": row["created_at"],
                "quitTime": row["closed_at"] or 0,
                "mode": row["mode"] or "story",
                "activityId": row["activity_id"] or 0,
                "floor": int(row["floor"] or 0),
                "node": int(row["node"] or 0),
                "stageMetadata": _stage_metadata(int(row["dungeon_id"])),
            }
        return 405, {"error": "method_not_allowed"}


__all__ = [
    "LocalDungeons",
    "TUTORIAL_DUNGEON_ID",
    "SPECIAL_DUNGEONS",
    "NORMAL_DUNGEON_IDS",
    "ALL_NORMAL_DUNGEON_IDS",
    "NONFIGHT_STORY_IDS",
    "PLAYABLE_DUNGEON_IDS",
]
