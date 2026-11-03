"""Small, persistent local implementations for non-economy game systems.

The retired service exposed a large number of actor, equipment, card, mail,
relation and PVP endpoints.  This module keeps those seams usable for a local
guest without pretending to reproduce server data: state is deliberately
small, deterministic and stored in the existing game SQLite database.
"""

from __future__ import annotations

from server.core.reset import policy_for, period_due

import json
import hashlib
import random
from google.protobuf import json_format
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

from google.protobuf import message_factory
from google.protobuf.message import DecodeError


Body = bytes | bytearray | str | None
Response = tuple[int, Any]


_MISC_CONFIG_PATH = Path(__file__).parent / "data" / "config" / "Misc.json"


def _load_name_constraints() -> tuple[int, int]:
    """Load observed local name/tutorial constraints from the APK table."""

    try:
        payload = json.loads(_MISC_CONFIG_PATH.read_text(encoding="utf-8"))
        rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
        row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], Mapping) else {}
        max_len = int(row.get("maxNameLen", 0))
        tutorial_level = int(row.get("modifyNameTutorialLevel", 0))
        return (max_len if max_len > 0 else 16, tutorial_level if tutorial_level > 0 else 6)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 16, 6


_MAX_NAME_LENGTH, _MODIFY_NAME_TUTORIAL_LEVEL = _load_name_constraints()


_CLIENT_TABLE_DIR = Path(__file__).resolve().parent / "data" / "client-tables"


def _client_table_rows(name: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads((_CLIENT_TABLE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, Mapping) else []
    return [row for row in rows if isinstance(row, dict)]


# Client GachaType (pools 1 normal, 2 periodic, 5 chips, 6 friendship) and
# GachaActorPool (actor weights), exported by tools/export_client_tables.py.
GACHA_TYPES: dict[int, dict[str, Any]] = {int(row["id"]): row for row in _client_table_rows("GachaType") if row.get("id")}
GACHA_LIMITED: dict[int, dict[str, Any]] = {
    int(row["activityId"]): row for row in _client_table_rows("ActivityGachaLimited") if row.get("activityId")
}
GACHA_NORMAL_UP: dict[int, dict[str, Any]] = {
    int(row["activityId"]): row for row in _client_table_rows("ActivityGachaNormalUp") if row.get("activityId")
}
GACHA_POOL: list[tuple[int, int]] = [
    (int(row["actorId"]), int(row.get("weight", 0) or 0))
    for row in _client_table_rows("GachaActorPool")
    if row.get("actorId") and int(row.get("weight", 0) or 0) > 0
]
LEAFA_ACTOR_ID = 2108  # ActorName_2108 (Leafa), guaranteed in the first ten-pull

try:
    from . import actors as actor_store
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import actors as actor_store
try:
    from . import equipment as equip_store
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    import equipment as equip_store
try:
    from .dungeon import ACTOR_LEVELS, MAX_ACTOR_LEVEL, _roll_actor_level
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from dungeon import ACTOR_LEVELS, MAX_ACTOR_LEVEL, _roll_actor_level

# Actors are addressed by the client as ``fci/actor/<uid>/...`` where uid is
# the 1-based login-bootstrap position.  Real ActorConfig ids start at 1001, so
# any path number >= this bound is accepted as a legacy actor id instead.
_MIN_ACTOR_ID = 1000
_RES_ITEM = 11  # ResourceType.ResItem
# ItemInfo rows with funcType ItemFuncType.ActorExp (1); param[0] is the
# character EXP one item grants (10001=100, 10002=300, 10003=800, 10004=1500).
ACTOR_EXP_ITEMS: dict[int, int] = {
    int(row["id"]): int((row.get("param") or [0])[0] or 0)
    for row in _client_table_rows("ItemInfo")
    if row.get("id") and int(row.get("funcType", 0) or 0) == 1 and row.get("param")
}
# ActorAttrStarPromotion[(actorId, star)]: levelLimit and starCost (items) to
# reach ``star``; the client validates exactly these before starupgrade.
ACTOR_STAR_PROMOTIONS: dict[tuple[int, int], dict[str, Any]] = {
    (int(row["actorId"]), int(row["star"])): row
    for row in _client_table_rows("ActorAttrStarPromotion")
    if row.get("actorId") and row.get("star")
}
ACTOR_SKILL_ROWS = {int(row["skillId"]): row for row in _client_table_rows("ActorSkill") if row.get("skillId")}
ACTOR_SKILL_LIMITS = {(int(row["actorId"]), int(row["star"])): row.get("maxLevel", [])
                      for row in _client_table_rows("ActorSkillMaxLevel")}
SKILL_LEVEL_COSTS = {(int(row["costIndex"]), int(row.get("level", 0))): row.get("cost", [])
                    for row in _client_table_rows("SkillLevelup")}


def _misc_int(key: str, default: int) -> int:
    try:
        payload = json.loads(_MISC_CONFIG_PATH.read_text(encoding="utf-8"))
        row = (payload.get("rows") or [{}])[0]
        value = int(row.get(key, 0) or 0)
    except (OSError, TypeError, ValueError, AttributeError, IndexError, json.JSONDecodeError):
        return default
    return value if value > 0 else default


MAX_ACTOR_STAR = _misc_int("maxStarLevel", 6)

# -- character / bag / profile cultivation tables (client 2.0.4) -------------
# All exported by tools/export_client_tables.py from analysis/lua-decompiled.
_RES_PLAYER_MONEY = 5        # 迷子 total (bound + paid)
_RES_PLAYER_GOLD = 7
_RES_EQUIP = 12
_RES_ACTOR = 15
_RES_GACHA_POINT = 23
_RES_PLAYER_PAY_MONEY = 27   # paid part of the 迷子 total
_RES_ACTOR_SKIN = 29
_ITEM_TYPE_ACTOR_CHIP = 3
_ITEM_FUNC_EQUIP_EXP = 2
_ITEM_FUNC_BOX = 6
_ITEM_FUNC_FAVOUR = 9
_ITEM_FUNC_MANUAL_BOX = 12
_RENAME_CARD_ITEM = 80010     # Window_Account_Rename: fci/item/80010
_RENAME_PRICE_TYPE = 10007    # RefreshPriceType.ChgName
_GENERIC_CHIP_ITEM = 90001    # TalentResetCost.costSub

_MISC_ROW: dict[str, Any] = (_client_table_rows("Misc") or [{}])[0]
ITEM_INFO: dict[int, dict[str, Any]] = {int(row["id"]): row for row in _client_table_rows("ItemInfo") if row.get("id")}
# ActorDecompose[(actor id, quality)].decomposeRes (AU.GetDecomposeResult).
ACTOR_DECOMPOSE: dict[tuple[int, int], list[dict[str, Any]]] = {
    (int(row["id"]), int(row.get("quality", 0) or 0)): [res for res in row.get("decomposeRes") or [] if isinstance(res, dict)]
    for row in _client_table_rows("ActorDecompose") if row.get("id")
}
ACTOR_LEVEL_ROWS: dict[int, dict[str, Any]] = {
    int(row["level"]): row for row in _client_table_rows("ActorLevelup") if row.get("level")
}
# ManualBox rows per box id in table order (IU.GetManualBox); the client
# sends the 0-based position of the chosen row as ``index``.
MANUAL_BOXES: dict[int, list[dict[str, Any]]] = {}
for _row in _client_table_rows("ManualBox"):
    if _row.get("id") and isinstance(_row.get("inBox"), dict):
        MANUAL_BOXES.setdefault(int(_row["id"]), []).append(_row)

# The original DropInfo/Box tables were server-only and were not present in
# the 2.0.4 client export.  Keep the replacement pools explicit and versioned
# so a later recovery can replace them without changing the item-use contract.
# ``weight`` is in a 10,000 point domain; a fixed row has an implicit 10,000
# weight.  The client only renders the resulting STResourceDynamic rows, so
# all rewards here are existing ItemInfo/EquipInfo resources.
RANDOM_BOX_RULES: dict[int, dict[str, Any]] = {
    # Local reconstruction of the public international-server description:
    # ascension boxes grant two same-tier materials, ten of each.
    85003: {"mode": "fixed", "dropId": "Box_85003", "rewards": [
        {"type": _RES_ITEM, "id": 30011, "count": 10},
        {"type": _RES_ITEM, "id": 30012, "count": 10},
    ]},
    85004: {"mode": "fixed", "dropId": "Box_85004", "rewards": [
        {"type": _RES_ITEM, "id": 30016, "count": 10},
        {"type": _RES_ITEM, "id": 30017, "count": 10},
    ]},
    85006: {"mode": "fixed", "dropId": "Box_85006", "rewards": [
        {"type": _RES_ITEM, "id": 30021, "count": 10},
        {"type": _RES_ITEM, "id": 30022, "count": 10},
    ]},
    # The client marks 75001/75002 as quality-4 boxes.  The APK exposes the
    # eight 75001 DropInfo names but not their server-side contents or rates.
    # Keep every recovered name in an explicit local table.  ``family_*``
    # groups the three visible quality-4 equipment families (1xx/2xx/3xx),
    # while ``pos_*`` groups the four equipment positions.  These selectors
    # are a documented local fallback, not a claim that the CN DropInfo was
    # recovered.  75002 only exposes the shared ``_15`` table and therefore
    # uses the full quality-4 fallback pool.
    75001: {"mode": "equipment", "dropId": "Box_75001_15", "quality": 4, "dropTables": [
        {"dropId": "Box_75001_15", "weight": 1, "pool": "quality_4"},
        {"dropId": "Box_75001_11_1", "weight": 1, "pool": "family_1xx"},
        {"dropId": "Box_75001_11_2", "weight": 1, "pool": "family_2xx"},
        {"dropId": "Box_75001_11_3", "weight": 1, "pool": "family_3xx"},
        {"dropId": "Box_75001_12_1", "weight": 1, "pool": "pos_0"},
        {"dropId": "Box_75001_12_2", "weight": 1, "pool": "pos_1"},
        {"dropId": "Box_75001_12_3", "weight": 1, "pool": "pos_2"},
        {"dropId": "Box_75001_12_4", "weight": 1, "pool": "pos_3"},
    ]},
    75002: {"mode": "equipment", "dropId": "Box_75001_15", "quality": 4, "dropTables": [
        {"dropId": "Box_75001_15", "weight": 1, "pool": "quality_4"},
    ]},
    # Activity 54000's six ActivityDrop boxes are the reward rows from the
    # original ActivityShopGoods table (shopGoodId 1..6): a signboard/scene
    # resource and 150 bound 迷子.  The DropInfo itself was server-only, but
    # the goods and resource ids are present in xlsx__activityshopgoods.lua.
    # ItemInfo.maxUseCount=1 is the client-side maximum count for one use
    # request (the item detail window passes this to its number editor); it is
    # not an account-wide ownership limit.  A malformed batch count is rejected
    # below while repeated single opens remain valid when the caller owns more.
    54001: {"mode": "fixed", "dropId": "Box_54001_1", "rewards": [
        {"type": 30, "id": 1503, "count": 1}, {"type": _RES_PLAYER_MONEY, "id": 0, "count": 150},
    ]},
    54002: {"mode": "fixed", "dropId": "Box_54002_1", "rewards": [
        {"type": 30, "id": 11703, "count": 1}, {"type": _RES_PLAYER_MONEY, "id": 0, "count": 150},
    ]},
    54003: {"mode": "fixed", "dropId": "Box_54003_1", "rewards": [
        {"type": 30, "id": 106, "count": 1}, {"type": _RES_PLAYER_MONEY, "id": 0, "count": 150},
    ]},
    54004: {"mode": "fixed", "dropId": "Box_54004_1", "rewards": [
        {"type": 30, "id": 1103, "count": 1}, {"type": _RES_PLAYER_MONEY, "id": 0, "count": 150},
    ]},
    54005: {"mode": "fixed", "dropId": "Box_54005_1", "rewards": [
        {"type": 30, "id": 10103, "count": 1}, {"type": _RES_PLAYER_MONEY, "id": 0, "count": 150},
    ]},
    54006: {"mode": "fixed", "dropId": "Box_54006_1", "rewards": [
        {"type": 30, "id": 502, "count": 1}, {"type": _RES_PLAYER_MONEY, "id": 0, "count": 150},
    ]},
}

# 85006 is the only sibling ascension box whose recovered ItemInfo row omits
# maxUseCount.  Keep the client-visible batch cap consistent with 85003/85004
# (10) until the server-only DropInfo is recovered; this prevents an unlimited
# single request from producing an unbounded reward response.
RANDOM_BOX_MAX_USE_OVERRIDES: dict[int, int] = {85006: 10}
RESOURCE_COMPOSE: dict[tuple[int, int], dict[str, Any]] = {
    (int(row["targetRes"].get("type", 0) or 0), int(row["targetRes"].get("id", 0) or 0)): row
    for row in _client_table_rows("ResourceCompose") if isinstance(row.get("targetRes"), dict)
}
FAVOUR_GIFT_ITEMS: dict[int, set[int]] = {
    int(row["roleId"]): {int(item) for item in row.get("item") or []}
    for row in _client_table_rows("RoleFavourGiftList") if row.get("roleId")
}
TALENT_RESET_COST: dict[int, dict[str, Any]] = {
    int(row["actorId"]): row for row in _client_table_rows("TalentResetCost") if row.get("actorId")
}
SPECIAL_ATTR: dict[int, dict[str, Any]] = {
    int(row["index"]): row for row in _client_table_rows("SpecialAttrInfo") if row.get("index")
}
ACTOR_TALENTS: dict[int, dict[str, Any]] = {int(row["id"]): row for row in _client_table_rows("ActorTalent") if row.get("id")}
ORDINARY_TALENTS: list[int] = sorted(tid for tid, row in ACTOR_TALENTS.items() if not row.get("spectial"))
ACTOR_CONFIG: dict[int, dict[str, Any]] = {int(row["id"]): row for row in _client_table_rows("ActorConfig") if row.get("id")}
DIFF_ACTOR_INHERIT = _client_table_rows("DiffActorInherit")
ACTOR_QUALITY_INFO: dict[tuple[int, int], dict[str, Any]] = {
    (int(row["actorId"]), int(row.get("quality", 0) or 0)): row
    for row in _client_table_rows("ActorQualityInfo") if row.get("actorId")
}
# ActorStarRandom[(way, actorId)]: talent count (= quality) and star rates.
ACTOR_STAR_RANDOM: dict[tuple[int, int], dict[str, Any]] = {
    (int(row.get("way", 0) or 0), int(row["actorId"])): row
    for row in _client_table_rows("ActorStarRandom") if row.get("actorId")
}
ACTOR_SKIN_OWNER: dict[int, int] = {
    int(row["id"]): int(row.get("actorId", 0) or 0) for row in _client_table_rows("ActorSkin") if row.get("id")
}
GACHA_CHIP_CHANCE: dict[tuple[int, int], int] = {
    (int(row["id"]), int(row.get("remainder", 0) or 0)): int(row.get("chance", 0) or 0)
    for row in _client_table_rows("GachaActorChipChance") if row.get("id")
}
REFRESH_PRICES: dict[int, list[dict[str, Any]]] = {}
for _row in _client_table_rows("RefreshPrice"):
    if _row.get("type"):
        REFRESH_PRICES.setdefault(int(_row["type"]), []).append(_row)
MAX_ROLE_FAVOUR_LEVEL = int(_MISC_ROW.get("maxRoleFavourLevel", 10) or 10)
# FavourLevel[level].favour: the favour needed to reach ``level``.
FAVOUR_LEVEL_COST: dict[int, int] = {
    int(row["level"]): int(row.get("favour", 0) or 0) for row in _client_table_rows("FavourLevel") if row.get("level")
}
# FavourIncreaseRule ruleType 1 (gift): favour a role may gain from gifts per day.
FAVOUR_GIFT_DAILY_LIMIT = next((int(row.get("dailyIncreaseLimit", 0) or 0) for row in _client_table_rows("FavourIncreaseRule")
                                if int(row.get("ruleType", 0) or 0) == 1), 0)
MAX_RESET_TALENT_COUNT = int(_MISC_ROW.get("maxResetTalentCount", 10) or 10)
GACHA_MAX_COUNT_ONCE = int(_MISC_ROW.get("gachaMaxCountOnce", 100) or 100)
ACTOR_DECOMPOSE_TALENT_COEF = float(_MISC_ROW.get("actorDecomposeTalentCoef", 0.016) or 0.016)


def _greedy_exp_items(exp: float, values: Mapping[int, int]) -> list[tuple[int, int]]:
    """AU.DecomposeToExpItems: largest item first, floor at each step."""

    result = []
    remaining = max(0.0, float(exp))
    for item_id, value in sorted(values.items(), key=lambda kv: (-kv[1], kv[0])):
        if value <= 0:
            continue
        count = int(remaining // value)
        if count:
            result.append((item_id, count))
            remaining -= count * value
    return result


def talent_score(talents: list[Mapping[str, Any]]) -> int:
    """AU.ActorTalentScore over ``[{talentId, talentValue}]``."""

    total = 0.0
    for talent in talents:
        info = SPECIAL_ATTR.get(_int(talent.get("talentId")))
        if info is None:
            continue
        score = float(info.get("score", 0) or 0)
        max_level = _int(info.get("maxLevel"), 1)
        if max_level > 1:
            score += (_int(talent.get("talentValue"), 1) - 1) / (max_level - 1) * (float(info.get("scoreMax", 0) or 0) - score)
        total += score
    return int(total // 1)


def actor_talents(record: Mapping[str, Any]) -> list[dict[str, int]]:
    """Stored ``STActor.talentInfo`` of a character instance (pos = index + 1)."""

    return [{"talentId": _int(t.get("talentId")), "talentValue": max(1, _int(t.get("talentValue"), 1))}
            for t in record.get("talents") or [] if isinstance(t, Mapping) and _int(t.get("talentId"))]


def _talent_candidates(actor_id: int) -> list[int]:
    exclusive = [_int(t) for t in (ACTOR_CONFIG.get(_int(actor_id), {}).get("exclusiveTalentId") or []) if _int(t) in ACTOR_TALENTS]
    return ORDINARY_TALENTS + exclusive


def _roll_talent(actor_id: int, rng: Any, exclude: set[int] = frozenset(), value: Optional[int] = None) -> dict[str, int]:
    """Local rule (TalentTypeRandom/TalentValueRandom were server-only):
    a uniform talent id among ActorTalent ordinary + the actor's exclusive
    ones, value uniform in 1..SpecialAttrInfo.maxLevel."""

    pool = [tid for tid in _talent_candidates(actor_id) if tid not in exclude] or _talent_candidates(actor_id) or [2]
    talent_id = rng.choice(pool)
    if value is None:
        value = rng.randint(1, max(1, _int(SPECIAL_ATTR.get(talent_id, {}).get("maxLevel"), 1)))
    return {"talentId": int(talent_id), "talentValue": int(value)}


def ensure_talents(record: dict[str, Any], rng: Any = None) -> bool:
    """Give a character without stored talents ``quality`` of them (value 1).

    The talent count is the quality (ActorStarRandom.talentCountRandoms ->
    AU.CheckActorStarRandom; batch decompose expects 5 points per talent at
    value 1).  Returns True when the record changed (caller saves).
    """

    if isinstance(record.get("talents"), list) and actor_talents(record):
        return False
    rng = rng or random
    actor_id = _int(record.get("id"))
    count = max(1, min(4, _int(record.get("quality")) or actor_store.base_quality(actor_id) or 1))
    talents: list[dict[str, int]] = []
    for _ in range(count):
        talents.append(_roll_talent(actor_id, rng, {t["talentId"] for t in talents}, value=1))
    record["talents"] = talents
    return True



def _route(path: str) -> str:
    value = urlsplit(path or "").path or "/"
    return value.rstrip("/") or "/"


def _json(body: Body) -> dict[str, Any]:
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class LocalSystems:
    """Authenticated local actor/card/mail/relation/PVP compatibility layer."""

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_system_state (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _now(self) -> int:
        return int(self._clock())

    def _message(self, name: str):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        return cls()

    def _request(self, body: Body, name: str):
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return None
        try:
            msg = self._message(name)
            msg.ParseFromString(raw)
            return msg
        except Exception:
            return None

    @staticmethod
    def _default_state(profile: Mapping[str, Any]) -> dict[str, Any]:
        actor_ids = profile.get("actor_ids", [])
        if not isinstance(actor_ids, list) or not actor_ids:
            actor_ids = [profile.get("actor_id")] if profile.get("actor_id") else [1001]
        actors = {
            str(_int(actor_id)): {
                "id": _int(actor_id), "level": 1, "star": 1, "exp": 0,
                "quality": 1, "locked": False, "skin": 0,
                "skills": {}, "favourLevel": 1, "favourExp": 0,
            }
            for actor_id in actor_ids
            if _int(actor_id)
        }
        if not actors:
            actors = {"1001": {"id": 1001, "level": 1, "star": 1, "exp": 0,
                                "quality": 1, "locked": False, "skin": 0,
                                "skills": {}, "favourLevel": 1, "favourExp": 0}}
        return {
            "actors": actors,
            "equips": {},
            "items": {},
            "medals": {},
            "seals": {"cells": {}, "rewards": {}, "newOpenFlag": False},
            "cultivation": {},
            "gacha_count": 0,
            "gacha_records": [],
            "mails": [
                {
                    "id": 1,
                    "title": "本地测试邮件",
                    "content": "这是本地沙盒邮件，不来自官方服务器。",
                    "senderName": "Local Sandbox",
                    # A real attachment makes the local mail window exercise
                    # the same obtain -> resource refresh path as the game.
                    # It is deliberately a small sandbox grant and is never
                    # backed by a remote payment or official account.
                    "attachments": [{"type": 7, "id": 0, "count": 100, "param": 0}],
                    "read": False,
                    "attachmentsGot": False,
                }
            ],
            "friends": [],
            # Task rows are expanded by ``_normalise_tasks`` before they are
            # sent to the client.  Keep this compact seed for old databases
            # and make the normalizer the single source of schema defaults.
            "tasks": {"1": {"progress": 0, "maxProgress": 1, "rewardGot": False, "endTime": 0}},
            "taskTargetDone": [],
            "guild": None,
            # Player profile presentation state.  The retired playeraction
            # descriptor is damaged in the recovered APK, so these values
            # use a small JSON compatibility seam until a valid descriptor
            # is recovered.  They are still persisted per local account.
            "player_profile": {
                "socialPicture": "",
                "sex": 0,
                "headPhoto": 0,
                "fightMedal": 0,
                "presentTargetActor": 0,
                "friendApplyLevelLimit": 0,
                "homeBackGround": {"current": 0, "isRandom": False},
                "collections": {"actors": [], "equips": [], "novels": []},
                "novelCovers": [],
                "roleNotePad": {},
                "emojis": [],
            },
        }

    @staticmethod
    def _task_reward(value: Any) -> list[dict[str, int]]:
        """Return a JSON-safe local reward list with the STResource shape."""

        raw = value if isinstance(value, list) else []
        result: list[dict[str, int]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            result.append(
                {
                    "type": _int(item.get("type")),
                    "id": _int(item.get("id")),
                    "count": _int(item.get("count")),
                    "param": _int(item.get("param")),
                }
            )
        return result or [{"type": 7, "id": 0, "count": 100, "param": 0}]

    def _normalise_task(self, key: Any, value: Any, now: int) -> dict[str, Any]:
        """Expand a stored task into the fields read by Common/TaskUtil.

        The original client dereferences ``task.res`` and several optional
        booleans before it renders the city task panel.  Historical local
        accounts only stored progress/reward state, so every read upgrades the
        row to this schema and persists it through ``_state``.
        """

        raw = dict(value) if isinstance(value, Mapping) else {}
        task_id = _int(raw.get("id"), _int(key, 1)) or 1
        max_progress = max(1, _int(raw.get("maxProgress", raw.get("max_progress", 1)), 1))
        progress = max(0, _int(raw.get("progress"), 0))
        reward_got = bool(raw.get("rewardGot", raw.get("reward_got", False)))
        is_done = bool(raw.get("isDone", raw.get("is_done", progress >= max_progress)))
        if progress >= max_progress:
            is_done = True
        # A finite future deadline keeps arithmetic in the native Lua client
        # defined while still behaving like a long-lived local task.
        end_time = _int(raw.get("endTime", raw.get("end_time", 0)), 0)
        if end_time <= now:
            end_time = now + 365 * 86400
        start_time = _int(raw.get("startTime", raw.get("start_time", now)), now)
        if start_time <= 0:
            start_time = now

        raw_res = raw.get("res")
        res = dict(raw_res) if isinstance(raw_res, Mapping) else {}
        reward = self._task_reward(res.get("reward", raw.get("reward")))
        # TaskInfo.xlsx's proto3 defaults are included explicitly.  This is
        # useful for Lua code that tests a field for nil rather than relying
        # on protobuf's implicit zero value.
        res_defaults: dict[str, Any] = {
            "id": task_id,
            "priority": 0,
            "goalType": 0,
            "maxProgress": max_progress,
            "taskParam": [],
            "reward": reward,
            "activePoint": 0,
            "preTaskId": 0,
            "gotoId": 0,
            "category": 1,
            "addFollowTask": False,
            "recordBeforeGot": False,
            "refreshAfterGot": False,
            "deleteAfterReward": False,
            "syncByServer": False,
            "life": 0,
            "series": 0,
            "checkWhenRefresh": False,
            "outstanding": False,
            "isActivity": False,
            "extraActivePointType": 0,
            "extraActivePoint": 0,
            "actorCultivationActivityId": 0,
            "actorCultivationRewardProgress": 0,
            "dailyRefresh": bool(raw.get("dailyRefresh", False)),
        }
        for field, default in res_defaults.items():
            res.setdefault(field, default)
        # Ensure values carried over from old JSON are scalar/list types the
        # client can safely consume.
        res["id"] = _int(res.get("id"), task_id)
        res["priority"] = _int(res.get("priority"))
        res["goalType"] = _int(res.get("goalType"))
        res["maxProgress"] = max_progress
        res["taskParam"] = [
            _int(item) for item in res.get("taskParam", [])
        ] if isinstance(res.get("taskParam"), list) else []
        res["reward"] = reward
        res["category"] = max(1, _int(res.get("category"), 1))

        task = dict(raw)
        task.update(
            {
                "id": task_id,
                "progress": progress,
                "maxProgress": max_progress,
                "rewardGot": reward_got,
                "isDone": is_done,
                "startTime": start_time,
                "endTime": end_time,
                "dailyRefresh": bool(raw.get("dailyRefresh", res.get("dailyRefresh", False))),
                "actorCultivationVipOnly": bool(raw.get("actorCultivationVipOnly", False)),
                "timeout": bool(raw.get("timeout", False)),
                "res": res,
                # Keep the legacy top-level reward/status fields for tooling
                # that used the early local JSON seam.
                "reward": reward,
                "status": 1 if is_done else 0,
            }
        )
        return task

    def _normalise_tasks(self, state: dict[str, Any]) -> bool:
        tasks = state.get("tasks")
        if not isinstance(tasks, Mapping):
            tasks = {"1": {"progress": 0, "maxProgress": 1, "rewardGot": False}}
        now = self._now()
        normalised: dict[str, dict[str, Any]] = {}
        for key, value in tasks.items():
            task = self._normalise_task(key, value, now)
            normalised[str(task["id"])] = task
        if not normalised:
            task = self._normalise_task("1", {}, now)
            normalised["1"] = task
        changed = normalised != tasks
        state["tasks"] = normalised
        return changed

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM local_system_state WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                state = self._default_state(profile)
                self._normalise_tasks(state)
                for record in actor_store.ordered(profile, state):
                    ensure_talents(record, self._rng_for())
                connection.execute(
                    "INSERT OR IGNORE INTO local_system_state(account_id,state_json,updated_at) VALUES(?,?,?)",
                    (account_id, json.dumps(state, separators=(",", ":")), self._now()),
                )
                return state
            try:
                state = json.loads(row["state_json"] or "{}")
            except (TypeError, ValueError):
                state = {}
            defaults = self._default_state(profile)
            if not isinstance(state, dict):
                state = {}
            for key, value in defaults.items():
                state.setdefault(key, value)
            changed = self._normalise_tasks(state)
            migrated = not state.get("actorInstances")
            for record in actor_store.ordered(profile, state):
                changed = ensure_talents(record, self._rng_for()) or changed
            changed = migrated or changed
            if changed:
                connection.execute(
                    "UPDATE local_system_state SET state_json = ?, updated_at = ? WHERE account_id = ?",
                    (json.dumps(state, separators=(",", ":")), self._now(), account_id),
                )
            return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_system_state SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    def _owned_actor_ids(self, profile: Mapping[str, Any], state: Optional[Mapping[str, Any]] = None) -> list[int]:
        """Actor id per uid, positional (``ids[uid - 1]``; 0 = removed uid)."""
        if not isinstance(state, dict):
            return [int(value) for value in self.game.local_actor_ids(profile)]
        records = actor_store.ordered(profile, state)
        ids = [0] * (max((int(record["uid"]) for record in records), default=0))
        for record in records:
            ids[int(record["uid"]) - 1] = int(record["id"])
        return ids

    def _resolve_actor_ref(self, profile: Mapping[str, Any], state: Optional[Mapping[str, Any]], ref: Any) -> Optional[int]:
        """Map a client actor reference (uid; >= 1000 = legacy id) to an actor id."""
        if not isinstance(state, dict):
            state = self._state(profile)
        record = actor_store.resolve(profile, state, ref)
        if record is not None:
            return int(record["id"])
        ref = _int(ref)
        return ref if ref >= _MIN_ACTOR_ID else None

    def _actor_uid(self, profile: Mapping[str, Any], state: Optional[Mapping[str, Any]], actor_id: Any) -> int:
        """The first uid holding ``actor_id`` (legacy: the id itself)."""
        if not isinstance(state, dict):
            state = self._state(profile)
        return actor_store.first_uid(profile, state, actor_id) or _int(actor_id)

    def _actor_message(self, actor: Mapping[str, Any], uid: int = 0):
        result = self._message("ApiActor.result")
        result.result.id = _int(actor.get("id"))
        result.result.uid = _int(uid) or _int(actor.get("id"))
        result.result.level = _int(actor.get("level"), 1)
        result.result.star = _int(actor.get("star"), 1)
        result.result.cur_exp = _int(actor.get("exp"))
        result.result.quality = _int(actor.get("quality"), 1)
        result.result.is_lock = bool(actor.get("locked", actor.get("isLock", False)))
        result.result.actor_skin = _int(actor.get("skin"))
        skills = actor.get("skills", {})
        if isinstance(skills, Mapping):
            for key, value in skills.items():
                skill = result.result.skills.add()
                skill.id = _int(key)
                if isinstance(value, Mapping):
                    skill.level = max(1, _int(value.get("level"), 1))
                    skill.is_read = bool(value.get("read", value.get("isRead", False)))
                else:
                    skill.level = max(1, _int(value, 1))
        return result

    def _resource_count(self, profile: Mapping[str, Any], resource: Mapping[str, Any]) -> int:
        """Read one resource balance from the game's canonical profile JSON."""
        rtype, rid, param = _int(resource.get("type")), _int(resource.get("id")), _int(resource.get("param"))
        for item in profile.get("resources", []):
            if isinstance(item, Mapping) and (_int(item.get("type")), _int(item.get("id")), _int(item.get("param"))) == (rtype, rid, param):
                return _int(item.get("count"))
        return 0

    def _change_resources(self, profile: Mapping[str, Any], changes: list[Mapping[str, Any]]) -> tuple[bool, list[dict[str, int]], str | None]:
        """Use LocalEconomy's canonical atomic resource mutation seam."""
        return self.game.economy._change_resources(profile, changes)

    def _dynamic(self, changes: list[Mapping[str, Any]]) -> list[dict[str, int]]:
        return [{"type": _int(x.get("type")), "id": _int(x.get("id")), "param": _int(x.get("param")),
                 "count": _int(x.get("count")), "countDelta": _int(x.get("countDelta"))} for x in changes]

    @staticmethod
    def _request_values(body: Body) -> dict[str, Any]:
        value = _json(body)
        # A few clients wrap JSON payloads under ``data``/``request``.
        for key in ("data", "request", "param"):
            if isinstance(value.get(key), Mapping):
                merged = dict(value)
                merged.update(value[key])
                value = merged
        return value

    @staticmethod
    def _first_int(value: Mapping[str, Any], *keys: str, default: int = 0) -> int:
        for key in keys:
            if key in value:
                return _int(value.get(key), default)
        return default

    def _actor(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        legacy_upgrade = any(lower.endswith(suffix) for suffix in ("/levelup", "/level-up", "/starlevelup", "/star-level-up"))
        if not (lower == "/fci/actor" or lower.startswith("/fci/actor/") or legacy_upgrade):
            return None
        parts = [part for part in route.split("/") if part]
        actor_ref = _int(_json(body).get("id"))
        if not actor_ref:
            msg = self._request(body, "ApiActor")
            actor_ref = _int(getattr(msg, "id", 0)) if msg is not None else 0
        from_path = len(parts) >= 3 and parts[1].lower() == "actor" and parts[2].isdigit()
        if from_path:
            actor_ref = int(parts[2])
        # The client addresses actors by bootstrap uid; >= 1000 is a legacy id.
        instance = actor_store.resolve(profile, state, actor_ref) if actor_ref else None
        if instance is None and from_path:
            return 404, {"error": "actor_not_found", "uid": actor_ref}
        actor = self._actor_for(state, (instance or {}).get("uid") or actor_ref or 1001, profile)
        actor_id = _int(actor.get("id"), 1001)
        uid = _int(actor.get("uid")) or self._actor_uid(profile, state, actor_id)
        if lower in {"/fci/actor", "/fci/actor/"} and method == "GET":
            return 200, self._actor_message(actor, uid)
        if lower.endswith("/levelupgrade"):
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._actor_level_upgrade(body, profile, state, actor)
        if lower.endswith("/starupgrade"):
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._actor_star_upgrade(profile, state, actor)
        if lower.endswith("/levelup") or lower.endswith("/level-up"):
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = self._request(body, "ApiActorLevelUp")
            payload = self._request_values(body)
            delta = max(1, _int(payload.get("levels", payload.get("level", 1))))
            if request is not None and request.food_res:
                delta = max(delta, len(request.food_res))
            current = max(1, _int(actor.get("level"), 1)); target = min(60, current + delta)
            if target == current:
                return 409, {"error": "actor_level_max"}
            # The extracted ActorLevel table is incomplete.  These explicitly
            # local sandbox costs scale with the requested level and are kept
            # in the canonical resource ledger (type 7 = gold).
            cost = sum(100 * level for level in range(current, target))
            ok, changes, error = self._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": -cost}])
            if not ok:
                return 409, {"error": error or "insufficient_resource", "required": cost}
            actor["level"] = target
            actor["exp"] = _int(actor.get("exp"))
            self._save(profile, state)
            result = self._message("ApiActorLevelUp.post")
            for change in self._dynamic(changes):
                entry = result.res_change.add(); entry.base_res.type = change["type"]; entry.base_res.id = change["id"]; entry.base_res.param = change["param"]; entry.base_res.count = change["count"]; entry.base_res.count_delta = change["countDelta"]
            return 200, result
        if lower.endswith("/starlevelup") or lower.endswith("/star-level-up"):
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            current = max(1, _int(actor.get("star"), 1)); target = min(6, current + 1)
            if target == current:
                return 409, {"error": "actor_star_max"}
            # Local actor shards use resource type 15 and the actor id.  This
            # makes the operation deterministic without inventing official
            # star tables that were not recovered from the APK.
            shard_cost = 2 * target
            shard_resource = {"type": 15, "id": actor_id, "param": 0, "count": -shard_cost}
            # Fresh local guests do not start with actor chips.  Allow the
            # first upgrade to use the starter gold balance as a deterministic
            # sandbox fallback; once chips exist they are consumed first.
            if self._resource_count(profile, {"type": 15, "id": actor_id, "param": 0}) >= shard_cost:
                payment = shard_resource
            else:
                payment = {"type": 7, "id": 0, "param": 0, "count": -(200 * target)}
            ok, changes, error = self._change_resources(profile, [payment])
            if not ok:
                return 409, {"error": error or "insufficient_resource", "required": shard_cost}
            actor["star"] = target
            self._save(profile, state)
            result = self._message("ApiActorStarLevelUp.post")
            for change in self._dynamic(changes):
                entry = result.res_change.add(); entry.base_res.type = change["type"]; entry.base_res.id = change["id"]; entry.base_res.param = change["param"]; entry.base_res.count = change["count"]; entry.base_res.count_delta = change["countDelta"]
            return 200, result
        if method in {"PATCH", "POST"} and lower.startswith("/fci/actor/"):
            request = self._request_values(body)
            if "skin" in request or "actorSkin" in request:
                from content_opening import allowed_skin_actors
                skin = self._first_int(request, "skin", "actorSkin", default=0)
                if skin and (skin not in state.get("unlockedSkins", {}).get(str(actor_id), [])
                             or actor_id not in allowed_skin_actors(skin)):
                    return 409, {"error": "invalid_skin"}
            # PATCH is a local administrative seam; preserve the existing
            # compatibility behavior while still normalizing new fields.
            actor["level"] = max(1, min(60, _int(request.get("level", actor.get("level", 1)))))
            actor["star"] = max(1, min(6, _int(request.get("star", actor.get("star", 1)))))
            if "locked" in request or "isLock" in request: actor["locked"] = bool(request.get("locked", request.get("isLock")))
            if "skin" in request or "actorSkin" in request: actor["skin"] = self._first_int(request, "skin", "actorSkin", default=0)
            self._save(profile, state)
            return 200, self._actor_message(actor, uid)
        if method == "GET" and (lower.startswith("/fci/actor/") or lower == "/fci/actor"):
            return 200, self._actor_message(actor, uid)
        return None

    @staticmethod
    def _actor_can_gain_exp(level: int, exp: int, level_limit: int) -> bool:
        """WidgetActorLevelup.CanEatFood: below the limit, or at it with a non-full bar."""
        need = ACTOR_LEVELS.get(level + 1)
        return level < level_limit or (level == level_limit and need is not None and exp < need)

    def _actor_upgrade_result(self, name: str, changes: list[Mapping[str, Any]], actor: Mapping[str, Any],
                              profile: Mapping[str, Any], state: Mapping[str, Any]):
        """``{resChange:[{baseRes}..., {actor: STActor}]}`` as DBH.ResChange consumes it."""
        result = self._message(name)
        for change in self._dynamic(changes):
            entry = result.res_change.add().base_res
            entry.type = change["type"]; entry.id = change["id"]; entry.param = change["param"]
            entry.count = change["count"]; entry.count_delta = change["countDelta"]
        result.res_change.add().actor.CopyFrom(self._st_actor(actor, profile, state))
        return result

    def _actor_level_upgrade(self, body: Body, profile: Mapping[str, Any], state: dict[str, Any], actor: dict[str, Any]) -> Response:
        """``POST fci/actor/<uid>/levelupgrade/ {foodRes:[{type:11,id,count}]}``.

        WidgetActorLevelup.DoLevelup passes ``result.resChange`` to
        DBH.ResChange: item ``baseRes`` entries update ``fci/item/<id>`` and
        the ``actor`` entry replaces ``fci/actor/<uid>``.  EXP per item is
        ItemInfo.param[0]; levels follow ActorLevelup and stop at
        min(maxActorLevel, player level), where the bar may fill up to the
        next threshold (overflow is wasted, as the client warns).
        """
        payload = self._request_values(body)
        food = payload.get("foodRes", payload.get("food_res"))
        if not isinstance(food, list):
            request = self._request(body, "ApiActorLevelUp")
            food = [{"type": res.type, "id": res.id, "count": res.count} for res in request.food_res] if request is not None else []
        wanted: dict[int, int] = {}
        for entry in food:
            if not isinstance(entry, Mapping):
                return 400, {"error": "invalid_food_res"}
            rtype, item_id, count = _int(entry.get("type"), _RES_ITEM), _int(entry.get("id")), _int(entry.get("count"))
            if rtype != _RES_ITEM or item_id not in ACTOR_EXP_ITEMS or count <= 0:
                return 400, {"error": "invalid_food_res", "type": rtype, "id": item_id}
            wanted[item_id] = wanted.get(item_id, 0) + count
        if not wanted:
            return 400, {"error": "invalid_food_res"}
        items = state.setdefault("items", {})
        for item_id, count in wanted.items():
            value = items.get(str(item_id))
            owned = _int(value.get("count")) if isinstance(value, Mapping) else 0
            if owned < count:
                return 409, {"error": "insufficient_item", "id": item_id, "required": count, "owned": owned}
        level = max(1, _int(actor.get("level"), 1))
        exp = max(0, _int(actor.get("exp")))
        level_limit = max(1, min(MAX_ACTOR_LEVEL, _int(profile.get("level"), 1)))
        if not self._actor_can_gain_exp(level, exp, level_limit):
            return 409, {"error": "actor_level_limit", "levelLimit": level_limit}
        gain = sum(ACTOR_EXP_ITEMS[item_id] * count for item_id, count in wanted.items())
        level_after, exp_after = _roll_actor_level(level, exp + gain)
        if level_after > level_limit:
            level_after, exp_after = level_limit, ACTOR_LEVELS.get(level_limit + 1, 0)
        elif level_after == level_limit and level_limit + 1 in ACTOR_LEVELS:
            exp_after = min(exp_after, ACTOR_LEVELS[level_limit + 1])
        changes: list[dict[str, int]] = []
        for item_id, count in wanted.items():
            value = items[str(item_id)]
            value["count"] = _int(value.get("count")) - count
            changes.append({"type": _RES_ITEM, "id": item_id, "param": 0, "count": value["count"], "countDelta": -count})
        actor["level"], actor["exp"] = level_after, exp_after
        self._save(profile, state)
        if level_after > level:
            self._record_task(profile, "TaskType_ActorLevelUpMTimes", level_after - level)
            self._record_task(profile, "TaskType_ActorP0LevelUpM", 0, param=_int(actor.get("id")), value=level_after)
        return 200, self._actor_upgrade_result("ApiActorLevelUp.post", changes, actor, profile, state)

    def _actor_star_upgrade(self, profile: Mapping[str, Any], state: dict[str, Any], actor: dict[str, Any]) -> Response:
        """``POST fci/actor/<uid>/starupgrade/ {}``.

        WidgetActorStarLevelup validates ActorAttrStarPromotion[id, star+1]
        (levelLimit <= actor level, Misc.maxStarLevel) and shows its
        ``starCost`` from ``fci/item/<id>`` (type 11 star materials, not
        actor shards).  The callback opens ActorStarLevelupResult with the
        ``actor`` entry of ``result.resChange`` and applies DBH.ResChange.
        """
        actor_id = _int(actor.get("id"))
        star = max(1, _int(actor.get("star"), 1))
        if star >= MAX_ACTOR_STAR:
            return 409, {"error": "actor_star_max"}
        row = ACTOR_STAR_PROMOTIONS.get((actor_id, star + 1))
        if row is None:
            return 409, {"error": "actor_star_config_missing", "actorId": actor_id, "star": star + 1}
        level_limit = _int(row.get("levelLimit"))
        if max(1, _int(actor.get("level"), 1)) < level_limit:
            return 409, {"error": "actor_level_too_low", "requiredLevel": level_limit}
        item_costs: dict[int, int] = {}
        ledger_costs: list[dict[str, int]] = []
        for cost in row.get("starCost") or []:
            if not isinstance(cost, Mapping) or _int(cost.get("count")) <= 0:
                continue
            if _int(cost.get("type")) == _RES_ITEM:
                item_costs[_int(cost.get("id"))] = item_costs.get(_int(cost.get("id")), 0) + _int(cost.get("count"))
            else:
                ledger_costs.append({"type": _int(cost.get("type")), "id": _int(cost.get("id")), "param": 0, "count": -_int(cost.get("count"))})
        items = state.setdefault("items", {})
        for item_id, count in item_costs.items():
            value = items.get(str(item_id))
            owned = _int(value.get("count")) if isinstance(value, Mapping) else 0
            if owned < count:
                return 409, {"error": "insufficient_item", "id": item_id, "required": count, "owned": owned}
        changes: list[dict[str, int]] = []
        if ledger_costs:
            ok, ledger_changes, error = self._change_resources(profile, ledger_costs)
            if not ok:
                return 409, {"error": error or "insufficient_resource"}
            changes.extend(ledger_changes)
        for item_id, count in item_costs.items():
            value = items[str(item_id)]
            value["count"] = _int(value.get("count")) - count
            changes.append({"type": _RES_ITEM, "id": item_id, "param": 0, "count": value["count"], "countDelta": -count})
        actor["star"] = star + 1
        self._save(profile, state)
        self._record_task(profile, "TaskType_StarLevelupMTimes")
        reached = sum(1 for record in actor_store.ordered(profile, state) if _int(record.get("star"), 1) >= star + 1)
        self._record_task(profile, "TaskType_ActorStarP0LevelUpM", 0, param=star + 1, value=reached)
        return 200, self._actor_upgrade_result("ApiActorStarLevelUp.post", changes, actor, profile, state)

    def _equip(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        # equip/custom-match is a separate resource (ApiEquipCustomMatch:
        # RestifizerManager iterates result.equipCustomMatch); leave it to the
        # entering defaults instead of answering with ApiEquip.result.
        if not lower.startswith("/fci/equip") or lower.rstrip("/") == "/fci/equip/custom-match":
            return None
        request = self._request_values(body)
        msg = self._request(body, "ApiEquip")
        # fci/equip/<uid>/...: the path names the item (EquipDetail, levelup).
        path_uid = next((_int(part) for part in [p for p in lower.split("/") if p][2:] if part.isdigit()), 0)
        fallback = path_uid or (getattr(msg, "id", 20001) if msg else 20001)
        equip_id = _int(request.get("id", request.get("uid", fallback)), 20001)
        equips = state.setdefault("equips", {})
        equip = equips.setdefault(str(equip_id), {"id": equip_id, "uid": equip_id, "level": 1, "exp": 0, "locked": False, "actorUid": 0, "plan": 0})
        equip.setdefault("locked", False); equip.setdefault("actorUid", 0); equip.setdefault("plan", 0)
        if method == "GET":
            # Package/list aliases return all owned equipment in a compact
            # JSON projection for clients without a dedicated descriptor.
            if lower in {"/fci/equip", "/fci/equip/", "/fci/equippackage", "/fci/equippackage/"}:
                return 200, {"equips": [dict(value) for value in equips.values()]}
            result = self._message("ApiEquip.result")
            if equip_store.is_real(equip):
                result.result.CopyFrom(self._st_equip(equip))
                return 200, result
            result.result.id = _int(equip["id"])
            result.result.uid = _int(equip["uid"])
            result.result.level = _int(equip.get("level"), 1)
            result.result.cur_exp = _int(equip.get("exp"))
            result.result.actor_uid = _int(equip.get("actorUid"))
            result.result.is_lock = bool(equip.get("locked"))
            return 200, result
        if method in {"PATCH", "POST"} and not (lower.endswith("/enhance") or lower.endswith("/enhance/")):
            # Never persist a placeholder for an unrecognised sub-route: the
            # client's fci/equip/on|off|on-all used to land here and stored a
            # bogus uid 20001 row (see _equip_carry).
            if not equip_store.is_real(equip) and not any(part.isdigit() for part in lower.split("/")):
                return 404, {"error": "equip_route_not_found", "route": route}
            equip["level"] = max(1, min(100, _int(request.get("level", equip.get("level", 1))))); self._save(profile, state)
            result = self._message("ApiEquip.result"); result.result.id = _int(equip["id"]); result.result.uid = _int(equip["uid"]); result.result.level = _int(equip["level"]); return 200, result
        # fci/equip/<uid>/enhance/ is answered by _equip_upgrade (dispatched
        # from _equipment_maintenance, which runs first).
        return 405, {"error": "method_not_allowed", "message": "GET or POST required"}

    def _actor_for(self, state: dict[str, Any], actor_id: int, profile: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """The stored record for a client reference.

        With ``profile`` the reference is a uid (or a legacy id >= 1000 meaning
        its first copy) and the uid-keyed instance is returned, so edits
        persist per copy.  An unowned reference gets a detached record that is
        never saved.  Without ``profile`` the legacy id-keyed map is used.
        """
        ref = _int(actor_id)
        if profile is not None:
            record = actor_store.resolve(profile, state, ref)
            if record is None:
                detached = actor_store.new_record(ref if ref >= _MIN_ACTOR_ID else 1001, 0)
                detached["id"] = ref if ref >= _MIN_ACTOR_ID else (ref or 1001)
                return detached
        else:
            ref = ref or 1001
            record = {"id": ref, "level": 1, "star": 1, "exp": 0, "quality": 1, "locked": False, "skin": 0,
                      "skills": {}, "favourLevel": 1, "favourExp": 0}
            if ref < _MIN_ACTOR_ID:
                return record
            record = state.setdefault("actors", {}).setdefault(str(ref), record)
        for key, value in (("level", 1), ("star", 1), ("exp", 0), ("quality", 1), ("locked", False), ("skin", 0),
                           ("skills", {}), ("favourLevel", 1), ("favourExp", 0)):
            record.setdefault(key, value)
        return record

    def _st_actor(self, actor: Mapping[str, Any], profile: Optional[Mapping[str, Any]] = None,
                  state: Optional[Mapping[str, Any]] = None):
        """Project one local actor into the shared ``STActor`` wire type.

        With ``profile`` the projection starts from the same record the login
        bootstrap sends (``game._append_local_actor``: attrs, quality, base
        skills) at the bootstrap uid, because DBH.UpdateActors replaces
        ``fci/actor/<uid>`` with it wholesale.
        """
        actor_id = _int(actor.get("id"))
        result = self._message("STActor")
        based = False
        uid = actor_id
        if profile is not None:
            uid = _int(actor.get("uid")) or self._actor_uid(profile, state, actor_id)
            try:
                container = self.game._types["DBGamePlayer.Actors"]()
                self.game._append_local_actor(
                    container, actor_id, uid, max(0, uid - 1),
                    level=max(1, _int(actor.get("level"), 1)), star=max(1, _int(actor.get("star"), 1)),
                    quality=_int(actor.get("quality")) or None, exp=max(0, _int(actor.get("exp"))),
                )
                result.CopyFrom(container.player_actors[-1])
                result.is_new = False
                result.is_new_card = False
                based = True
            except Exception:
                result = self._message("STActor")
        result.id = actor_id
        result.uid = uid
        result.level = max(1, _int(actor.get("level"), 1))
        result.star = max(1, _int(actor.get("star"), 1))
        result.cur_exp = max(0, _int(actor.get("exp")))
        if not based:
            result.quality = max(1, _int(actor.get("quality"), 1))
        result.is_lock = bool(actor.get("locked", actor.get("isLock", False)))
        result.actor_skin = max(0, _int(actor.get("skin")))
        known = {skill.id: skill for skill in result.skills}
        for key, value in (actor.get("skills") or {}).items():
            skill = known.get(_int(key)) or result.skills.add()
            skill.id = _int(key)
            if isinstance(value, Mapping):
                skill.level = max(1, _int(value.get("level"), 1))
                skill.is_read = bool(value.get("read", value.get("isRead", False)))
            else:
                skill.level = max(1, _int(value, 1))
        # Talents (STActor.talentInfo, pos = index + 1) and a pending reroll.
        del result.talent_info[:]
        for talent in actor_talents(actor):
            entry = result.talent_info.add()
            entry.talent_id = talent["talentId"]
            entry.talent_value = talent["talentValue"]
        del result.talent_reset_info[:]
        pending = [t for t in actor.get("talentResetInfo") or [] if isinstance(t, Mapping)]
        result.talent_reset_pos = _int(actor.get("talentResetPos")) if pending else 0
        for talent in pending:
            entry = result.talent_reset_info.add()
            entry.talent_id = _int(talent.get("talentId"))
            entry.talent_value = _int(talent.get("talentValue"))
        if profile is not None:
            # Carried equipment (bodyEquips/equipAttrs/specialAttrIds): every
            # reply that replaces fci/actor/<uid> must keep the 藏品 slots.
            try:
                equip_store.apply_worn_equipment(result, profile, state if isinstance(state, dict) else self._state(profile))
            except Exception:
                pass
        return result

    def login_actor_extras(self, profile: Mapping[str, Any], state: dict[str, Any],
                           record: dict[str, Any], message: Any) -> bool:
        """Login-bootstrap hook for one ``DBGamePlayer.Actors.player_actors`` entry.

        Sets is_new / is_new_card from the persisted NEW flag (cleared by
        ``PATCH fci/new-flag`` type 15) and fills talentInfo /
        talentResetPos / talentResetInfo, seeding talents on first use (see
        ensure_talents).  Returns True when ``state`` changed: save it once
        after the loop.
        """

        changed = ensure_talents(record, self._rng_for())
        message.is_new = message.is_new_card = equip_store.actor_is_new(record)
        del message.talent_info[:]
        for talent in actor_talents(record):
            entry = message.talent_info.add()
            entry.talent_id, entry.talent_value = talent["talentId"], talent["talentValue"]
        del message.talent_reset_info[:]
        pending = [t for t in record.get("talentResetInfo") or [] if isinstance(t, Mapping)]
        message.talent_reset_pos = _int(record.get("talentResetPos")) if pending else 0
        for talent in pending:
            entry = message.talent_reset_info.add()
            entry.talent_id, entry.talent_value = _int(talent.get("talentId")), _int(talent.get("talentValue"))
        return changed

    def _st_equip(self, equip: Mapping[str, Any]):
        """Project one local equipment row into the shared ``STEquip`` type.

        Always the full record (owner, lock, plan, random attributes):
        DBH.UpdateEquips replaces the client's ``fci/equip/<uid>`` wholesale.
        """
        return equip_store.fill_st_equip(self._message("STEquip"), equip)

    def _actor_maintenance(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        if not (lower.startswith("/fci/actor") or "unlockedactorskins" in lower or "unlocked-actor-skins" in lower or "setactorlock" in lower or "actorlock" in lower or "favour" in lower or "present" in lower):
            return None
        payload = self._request_values(body)
        parts = [part for part in lower.split("/") if part]
        actor_ref = self._first_int(payload, "actorId", "actor_id", "id", "uid", default=1001)
        path_ref = None
        for index, part in enumerate(parts):
            if part.isdigit() and index > 1:
                path_ref = _int(part, actor_ref); break
        if path_ref is not None:
            actor_ref = path_ref
        # ``fci/actor/<uid>/...``: uid is the bootstrap position (>= 1000 = legacy id).
        instance = actor_store.resolve(profile, state, actor_ref)
        if instance is None and path_ref is not None and lower.startswith("/fci/actor/"):
            return 404, {"error": "actor_not_found", "uid": path_ref}
        actor = self._actor_for(state, (instance or {}).get("uid") or actor_ref, profile)
        actor_id = _int(actor.get("id"), actor_ref)

        # ``ActorLike`` was a small player-action endpoint in the retired
        # service, but its descriptor was not recovered with the rest of
        # playeraction.proto.  Keep the interaction usable through a stable
        # JSON compatibility seam.  Likes are account-local and idempotent:
        # retrying the same POST does not inflate the count, while DELETE (or
        # an explicit ``liked=false``) removes the local like.  The aggregate
        # count is persisted so the actor page remains consistent after a
        # restart.
        if any(token in lower for token in ("actorlike", "actor-like", "rolelike", "role-like")):
            likes = state.setdefault("actorLikes", {})
            if not isinstance(likes, dict):
                likes = {}
                state["actorLikes"] = likes
            key = str(actor_id)
            entry = likes.setdefault(key, {"count": 0, "liked": False})
            if not isinstance(entry, dict):
                entry = {"count": 0, "liked": False}
                likes[key] = entry
            entry["count"] = max(0, _int(entry.get("count")))
            entry["liked"] = bool(entry.get("liked", False))
            if method == "GET":
                try:
                    result = self._message("ApiActorLike.get")
                    result.like_num = entry["count"]
                    result.is_like = entry["liked"]
                    return 200, result
                except Exception:
                    return 200, {"actorId": actor_id, "likeCount": entry["count"], "liked": entry["liked"], "sandbox": True}
            if method not in {"POST", "DELETE"}:
                return 405, {"error": "method_not_allowed", "message": "POST or DELETE required"}
            payload = self._request_values(body)
            request = self._request(body, "ApiActorLike")
            if request is not None and request.is_cancel:
                payload["liked"] = False
            remove = method == "DELETE" or payload.get("liked") is False or payload.get("isLike") is False
            if remove:
                if entry["liked"]:
                    entry["count"] = max(0, entry["count"] - 1)
                entry["liked"] = False
            elif not entry["liked"]:
                entry["count"] += 1
                entry["liked"] = True
            self._save(profile, state)
            try:
                result = self._message("ApiActorLike.post")
                result.result = True
                result.like_num = entry["count"]
                result.is_like = entry["liked"]
                return 200, result
            except Exception:
                return 200, {"actorId": actor_id, "likeCount": entry["count"], "liked": entry["liked"], "sandbox": True}

        if any(token in lower for token in ("setactorlock", "actor-lock", "actorlock")):
            if method == "GET":
                return 200, {"actorId": actor_id, "isLock": bool(actor.get("locked"))}
            if method not in {"POST", "PATCH"}: return 405, {"error": "method_not_allowed", "message": "POST required"}
            lock = bool(payload.get("isLock", payload.get("locked", payload.get("lock", not actor.get("locked")))))
            actor["locked"] = lock; self._save(profile, state)
            try:
                result = self._message("ApiSetActorLock.result"); result.result.CopyFrom(self._actor_message(actor, _int(actor.get("uid")) or self._actor_uid(profile, state, actor_id)).result); return 200, result
            except Exception: return 200, {"actorId": actor_id, "isLock": lock}

        # ``ActorFavourFull`` (no 2.0.4 client caller): a local one-tap seam
        # that fills the favour of the character's Role (profile_features
        # owns favour per Role; FavourLevel costs, Misc.maxRoleFavourLevel).
        if "actorfavourfull" in lower or "actor-favour-full" in lower or "favourfull" in lower:
            role_id = _int(ACTOR_CONFIG.get(actor_id, {}).get("role"))
            features = getattr(self.game, "profile_features", None)
            notepad = features.role_notepad(profile, role_id) if features is not None and role_id else None
            if notepad is None:
                return 404, {"error": "role_not_owned", "actorId": actor_id}
            if method == "POST":
                level = _int(notepad.get("favourLevel"))
                missing = sum(FAVOUR_LEVEL_COST.get(each, 0) for each in range(level + 1, MAX_ROLE_FAVOUR_LEVEL + 1))
                if missing > _int(notepad.get("favour")):
                    notepad = features.add_role_favour(profile, role_id, missing - _int(notepad.get("favour"))) or notepad
            elif method != "GET":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            result = self._message("ApiActorFavourFull.result")
            result.result = int(_int(notepad.get("favourLevel")) >= MAX_ROLE_FAVOUR_LEVEL)
            return 200, result

        if "decompose" in lower and ("actor" in lower or "role" in lower):
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            if actor.get("locked"): return 409, {"error": "actor_locked"}
            # Keep the starter actor owned; decomposition removes only actors
            # acquired after the first slot and refunds local actor shards.
            if len(actor_store.instances(profile, state)) <= 1:
                return 409, {"error": "starter_actor_protected"}
            if _int(actor.get("uid")):
                actor_store.remove(profile, state, actor["uid"])
            refund = {"type": 15, "id": actor_id, "param": 0, "count": 1}
            ok, changes, error = self._change_resources(profile, [refund])
            if not ok: return 409, {"error": error or "resource_update_failed"}
            self._save(profile, state)
            try:
                result = self._message("ApiActorDecompose.post")
                for change in self._dynamic(changes):
                    item = result.res_change.add(); item.base_res.type = change["type"]; item.base_res.id = change["id"]; item.base_res.param = change["param"]; item.base_res.count = change["count"]; item.base_res.count_delta = change["countDelta"]
                return 200, result
            except Exception: return 200, {"resChange": self._dynamic(changes)}

        if "skill" in lower and ("learn" in lower or "learning" in lower or lower.endswith("/skill")):
            skill_id = self._first_int(payload, "skillId", "skill_id", default=0)
            skills = actor.setdefault("skills", {})
            if method == "GET":
                return 200, {"actorId": actor_id, "skills": [{"id": _int(k), **(v if isinstance(v, Mapping) else {"level": _int(v, 1), "read": False})} for k, v in skills.items()]}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            if not payload and isinstance(body, (bytes, bytearray)):
                try:
                    request = self._message("ApiActorSkillLearnning")
                    request.ParseFromString(bytes(body))
                    skill_id = int(request.skill_id)
                except (ValueError, TypeError, AttributeError, DecodeError):
                    return 400, {"error": "invalid_skill_request"}
            config = ACTOR_SKILL_ROWS.get(skill_id, {})
            display = _int(config.get("displayIndex"))
            if (_int(config.get("actorId")) != actor_id or display <= 0
                    or config.get("isLink") or not config.get("costIndex")):
                return 400, {"error": "actor_skill_not_found"}
            # The UI shows unstored skills at level 1 and posts this SAME route
            # for every upgrade. Shadow variants share one displayed level.
            group = [row for row in ACTOR_SKILL_ROWS.values()
                     if _int(row.get("actorId")) == actor_id and _int(row.get("displayIndex")) == display
                     and not row.get("isLink")]
            default = group[0]
            if default.get("isPassive"):
                unlock_star = next((int(row["star"]) for row in ACTOR_STAR_PROMOTIONS.values()
                                    if _int(row.get("unlockSkillId")) == _int(default["skillId"])), 0)
                if _int(actor.get("star"), 1) < unlock_star:
                    return 409, {"error": "actor_skill_star_limit"}
            value = skills.get(str(default["skillId"]), {})
            current = max(1, _int(value.get("level"), 1) if isinstance(value, Mapping) else _int(value, 1))
            limits = ACTOR_SKILL_LIMITS.get((actor_id, _int(actor.get("star"), 1)), [])
            if display > len(limits) or current >= _int(limits[display - 1]):
                return 409, {"error": "actor_skill_star_limit"}
            costs = SKILL_LEVEL_COSTS.get((_int(default.get("costIndex")), current + 1))
            if costs is None:
                return 409, {"error": "actor_skill_level_max"}
            item_costs, resource_costs = {}, []
            for cost in costs:
                amount = _int(cost.get("count"))
                if amount <= 0:
                    continue
                if _int(cost.get("type")) == _RES_ITEM:
                    rid = _int(cost.get("id"))
                    item_costs[rid] = item_costs.get(rid, 0) + amount
                else:
                    resource_costs.append(dict(cost, count=-amount))
            if any(self._items_owned(state, rid) < count for rid, count in item_costs.items()):
                return 409, {"error": "insufficient_item"}
            # Payment, inventory, skill state and the complete response are one
            # unit: storage/projection failure must not charge a failed click.
            with self.game.atomic_mutation():
                ok, changes, error = self._change_resources(profile, resource_costs)
                if not ok:
                    return 409, {"error": error or "insufficient_resource"}
                changes = self._dynamic(changes) + self._items_take(state, item_costs)
                for row in group:
                    skills[str(row["skillId"])] = {"level": current + 1, "read": False}
                self._save(profile, state)
                self._record_task(profile, "TaskType_ActorSkillLevelUpMTimes")
                result = self._message("ApiActorSkillLearnning.post")
                result.new_skill.id, result.new_skill.level = skill_id, current + 1
                for change in changes:
                    item = result.res_change.add().base_res
                    item.type, item.id, item.param = change["type"], change["id"], change["param"]
                    item.count, item.count_delta = change["count"], change["countDelta"]
                # Actorskilldetail consumes only resChange, not newSkill. Return
                # the updated full actor so the next click uses the new level.
                result.res_change.add().actor.CopyFrom(self._st_actor(actor, profile, state))
                return 200, result

        if "skill" in lower and "read" in lower:
            skill_id = self._first_int(payload, "skillId", "skill_id", "id", default=0)
            skill = actor.setdefault("skills", {}).setdefault(str(skill_id), {"level": 1, "read": False})
            skill["read"] = bool(payload.get("isRead", payload.get("read", True)))
            self._save(profile, state)
            try:
                result = self._message("ApiActorSkillRead.result"); result.is_read = bool(skill["read"]); return 200, result
            except Exception: return 200, {"isRead": bool(skill["read"])}

        if "unlockedactorskins" in lower or "unlocked-actor-skins" in lower:
            unlocked = state.setdefault("unlockedSkins", {})
            values = [int(item) for item in unlocked.get(str(actor_id), []) if _int(item) > 0]
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            try:
                result = self._message("ApiUnlockedActorSkins.result")
                result.unlocked_skins.extend(values)
                return 200, result
            except Exception:
                return 200, {"actorId": actor_id, "unlockedSkins": values, "sandbox": True}

        if "skin" in lower:
            if method == "GET":
                try:
                    result = self._message("ApiActorSkin.result")
                    result.actor_skin = _int(actor.get("skin"))
                    return 200, result
                except Exception:
                    return 200, {"actorId": actor_id, "skin": _int(actor.get("skin"))}
            if method not in {"POST", "PATCH"}: return 405, {"error": "method_not_allowed", "message": "POST required"}
            native = self._request(body, "ApiActorSkin")
            skin = self._first_int(payload, "skin", "skinId", "actorSkin",
                                   default=_int(native.actor_skin) if native is not None else 0)
            if skin < 0:
                return 400, {"error": "invalid_skin"}
            from content_opening import allowed_skin_actors
            owned = state.get("unlockedSkins", {}).get(str(actor_id), [])
            if skin and (skin not in owned or actor_id not in allowed_skin_actors(skin)):
                return 409, {"error": "invalid_skin"}
            actor["skin"] = skin
            self._save(profile, state)
            try:
                result = self._message("ApiActorSkin.result")
                result.actor_skin = skin
                return 200, result
            except Exception:
                return 200, {"actorId": actor_id, "skin": skin, "sandbox": True}

        if "favour" in lower or "favor" in lower or "present" in lower:
            # Legacy local favour seam (the client's own route is PATCH
            # fci/FavourGift/<roleId>, see _favour_gift): favour belongs to the
            # character's Role notepad (profile_features.add_role_favour).
            role_id = _int(ACTOR_CONFIG.get(actor_id, {}).get("role"))
            features = getattr(self.game, "profile_features", None)
            notepad = features.role_notepad(profile, role_id) if features is not None and role_id else None
            if notepad is None:
                return 404, {"error": "role_not_owned", "actorId": actor_id}
            if method == "GET":
                return 200, {"roleNotepad": notepad}
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            gift_id = self._first_int(payload, "itemId", "giftId", "id", default=0)
            gift_count = max(1, self._first_int(payload, "count", "num", default=1))
            if _int(ITEM_INFO.get(gift_id, {}).get("funcType")) != _ITEM_FUNC_FAVOUR:
                return 400, {"error": "invalid_favour_item", "id": gift_id}
            return self._favour_gift(role_id, json.dumps({"usedItemList": [{"type": _RES_ITEM, "id": gift_id, "count": gift_count}]}).encode(),
                                     profile, state)
        return None

    # -- carrying equipment (藏品) and plans (搭配) ---------------------------
    #
    # Client requests (decompiled Lua) and the reply fields each callback reads:
    #   ActorEquipCompare.RequestEquip    POST fci/equip/on/ {actorUid, equipUid}
    #       -> DBH.UpdateActors{newActor, oldActor}; DBH.UpdateEquips{newEquip, oldEquip}
    #   ActorEquipCompare.RequestUnequip  POST fci/equip/off/ {actorUid, bodyPos}
    #       -> DBH.UpdateActors(newActor); DBH.UpdateEquips(newEquip)
    #   WidgetActorEquip.OnToggleAllClick POST fci/equip/off/1 {actorUid}
    #       -> DBH.UpdateEquips(offEquips); DBH.UpdateActors(newActor)
    #                                     POST fci/equip/on-all/ {actorUid, equipUids}
    #       -> DBH.UpdateEquips(newEquips); DBH.UpdateActors(newActor)
    #   EquipMatch.LoadEquipMatchImpl     POST fci/equip/apply-match {actorUid, matchIndex}
    #       -> DBH.UpdateEquips(changeEquips); DBH.UpdateActors(changeActors)
    #   EquipMatch.SaveEquipMatchImpl     POST fci/equip/custom-match {equipUids, matchIndex}
    #       -> DBH.UpdateEquips(changeEquips); SetData("Equip/Match/"..i) per equipCustomMatch
    #   RestifizerManager (entering)      GET  fci/equip/custom-match -> equipCustomMatch
    #   EquipMatchRename                  POST fci/equip/modify-match-name/ {matchIndex, matchName} -> matchName
    #   EquipDetail.SetLock               POST fci/equip/<uid>/setlock/ {isLock}
    #       -> DBH.UpdateEquips(equip); SetData("Equip/Match/"..i) per equipCustomMatch
    # Replies are the typed Api*.result messages: the client decodes them with
    # lua-protobuf (use_default_values), which keeps uint32 map keys numeric
    # (matchEquipUids[pos]) and string keys as strings (bodyEquips[tostring(pos)]).
    # ApiEquipOn.result always carries oldActor/oldEquip (the previous owner /
    # displaced item, else the same records again) so neither table entry is
    # ever a defaulted empty record.

    def _equip_carry_action(self, rest: list[str]) -> Optional[str]:
        if rest in (["on"], ["equipon"]) or (len(rest) == 2 and rest[0].isdigit() and rest[1] == "equipon"):
            return "on"
        if rest in (["on-all"], ["onall"], ["equiponall"]):
            return "on-all"
        if rest == ["off"] or rest == ["equipoff"] or (len(rest) == 2 and rest[0].isdigit() and rest[1] == "equipoff"):
            return "off"
        if len(rest) == 2 and rest[0] == "off":
            return "off-all"
        if rest in (["apply-match"], ["applymatch"]):
            return "apply-match"
        if rest in (["custom-match"], ["custommatch"]):
            return "custom-match"
        if rest in (["modify-match-name"], ["modifymatchname"]):
            return "modify-match-name"
        if len(rest) == 2 and rest[0].isdigit() and rest[1] in ("setlock", "setequiplock"):
            return "setlock"
        return None

    def _equip_carry(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        parts = [part for part in route.lower().split("/") if part]
        if parts[:2] != ["fci", "equip"]:
            return None
        rest = parts[2:]
        action = self._equip_carry_action(rest)
        if action is None:
            return None
        payload = self._request_values(body)
        equips = state.setdefault("equips", {})
        legacy = any(token in rest for token in ("equipon", "equipoff", "onall", "equiponall", "setequiplock"))
        path_uid = _int(rest[0]) if rest and rest[0].isdigit() else 0
        equip_uid = self._first_int(payload, "equipUid", "equip_uid", default=path_uid)
        if action in {"on", "setlock"} or (action == "off" and equip_uid and "bodyPos" not in payload):
            # Legacy local aliases kept placeholder rows for unknown uids;
            # leave those to the historical branch below.
            if (legacy or action == "setlock") and not equip_store.is_real(equips.get(str(equip_uid))):
                return None
        if action == "custom-match" and method == "GET":
            current = equip_store.plans(state)
            if not current:
                return 200, {"equipCustomMatch": {}}
            result = self._message("ApiEquipCustomMatch.result")
            equip_store.fill_custom_match(result.equip_custom_match, current)
            return 200, result
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        equip_store.prune_placeholders(state)
        if action == "custom-match":
            return self._equip_save_match(payload, profile, state)
        if action == "modify-match-name":
            return self._equip_rename_match(payload, profile, state)
        if action == "setlock":
            return self._equip_set_lock(payload, equips[str(equip_uid)], profile, state)
        actor = actor_store.resolve(profile, state, self._first_int(payload, "actorUid", "actor_uid", "actorId", default=0))
        if actor is None:
            return 404, {"error": "actor_not_found", "uid": self._first_int(payload, "actorUid", "actor_uid", "actorId", default=0)}
        if action == "on":
            return self._equip_on(equip_uid, actor, profile, state)
        if action == "off":
            return self._equip_off(payload, equip_uid, actor, profile, state)
        if action == "off-all":
            return self._equip_off_all(actor, profile, state)
        if action == "on-all":
            return self._equip_on_all(payload, actor, profile, state)
        return self._equip_apply_match(payload, actor, profile, state)

    def _equip_put(self, profile: Mapping[str, Any], state: dict[str, Any], actor_uid: int,
                   record: dict[str, Any]) -> tuple[Optional[dict[str, Any]], int]:
        """Carry ``record`` on ``actor_uid``; return (displaced row, previous owner uid)."""

        previous = equip_store.owner_uid(profile, state, record.get("actorUid"))
        displaced = equip_store.worn_equips(profile, state, actor_uid).get(equip_store.equip_pos(record.get("id")))
        if displaced is not None and _int(displaced.get("uid")) == _int(record.get("uid")):
            displaced = None
        if displaced is not None:
            displaced["actorUid"] = 0
        record["actorUid"] = actor_uid
        record["isNew"] = False
        return displaced, (previous if previous != actor_uid else 0)

    def _equip_actor_level(self, actor: Mapping[str, Any]) -> int:
        return max(1, _int(actor.get("level"), 1))

    def _equip_on(self, equip_uid: int, actor: dict[str, Any], profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        record = state["equips"].get(str(equip_uid))
        if not equip_store.is_real(record):
            return 404, {"error": "equip_not_found", "uid": equip_uid}
        if equip_store.require_level(record.get("id")) > self._equip_actor_level(actor):
            return 409, {"error": "equip_level_limit", "requireLevel": equip_store.require_level(record.get("id"))}
        actor_uid = _int(actor.get("uid"))
        displaced, previous = self._equip_put(profile, state, actor_uid, record)
        self._save(profile, state)
        self._record_task(profile, "TaskType_EquipOnMTimes")
        result = self._message("ApiEquipOn.result")
        result.new_actor.CopyFrom(self._st_actor(actor, profile, state))
        result.new_equip.CopyFrom(self._st_equip(record))
        old_actor = actor_store.get(profile, state, previous) if previous else None
        result.old_actor.CopyFrom(self._st_actor(old_actor, profile, state) if old_actor is not None else result.new_actor)
        result.old_equip.CopyFrom(self._st_equip(displaced) if displaced is not None else result.new_equip)
        return 200, result

    def _equip_off(self, payload: Mapping[str, Any], equip_uid: int, actor: dict[str, Any],
                   profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        actor_uid = _int(actor.get("uid"))
        worn = equip_store.worn_equips(profile, state, actor_uid)
        if "bodyPos" in payload or "body_pos" in payload or not equip_uid:
            record = worn.get(self._first_int(payload, "bodyPos", "body_pos", default=0))
        else:
            record = state["equips"].get(str(equip_uid))
            if not equip_store.is_real(record):
                return 404, {"error": "equip_not_found", "uid": equip_uid}
            owner = equip_store.owner_uid(profile, state, record.get("actorUid"))
            if owner and owner != actor_uid:
                actor = actor_store.get(profile, state, owner) or actor
        if record is not None:
            record["actorUid"] = 0
            self._save(profile, state)
        result = self._message("ApiEquipOff.result")
        result.new_actor.CopyFrom(self._st_actor(actor, profile, state))
        if record is not None:
            result.new_equip.CopyFrom(self._st_equip(record))
            result.off_equips.add().CopyFrom(result.new_equip)
        return 200, result

    def _equip_off_all(self, actor: dict[str, Any], profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        removed = [record for _pos, record in sorted(equip_store.worn_equips(profile, state, _int(actor.get("uid"))).items())]
        for record in removed:
            record["actorUid"] = 0
        if removed:
            self._save(profile, state)
        result = self._message("ApiEquipOff.result")
        result.new_actor.CopyFrom(self._st_actor(actor, profile, state))
        for record in removed:
            result.off_equips.add().CopyFrom(self._st_equip(record))
        if removed:
            result.new_equip.CopyFrom(result.off_equips[0])
        return 200, result

    def _equip_on_all(self, payload: Mapping[str, Any], actor: dict[str, Any], profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """One-key carry.

        The client (WidgetActorEquip.OnToggleAllClick) only offers it while
        nothing is carried and sends, per slot, the first candidate in its
        list order (quality, level, score, uid; all descending) that is in
        the bag and within the character level.  Those uids are carried as
        sent; without a list the server makes the same choice for every
        empty slot.
        """

        actor_uid = _int(actor.get("uid"))
        level = self._equip_actor_level(actor)
        equips = state["equips"]
        owners = equip_store.effective_owners(profile, state)
        chosen: dict[int, dict[str, Any]] = {}
        selected = payload.get("equipUids", payload.get("equip_uids"))
        if isinstance(selected, Mapping):  # a Lua array encoded as an object
            selected = [value for _key, value in sorted(selected.items(), key=lambda item: _int(item[0]))]
        if isinstance(selected, list) and selected:
            for uid in selected:
                record = equips.get(str(_int(uid)))
                if not equip_store.is_real(record) or owners.get(_int(uid), 0) not in (0, actor_uid):
                    continue
                if equip_store.require_level(record.get("id")) > level:
                    continue
                chosen.setdefault(equip_store.equip_pos(record.get("id")), record)
        else:
            worn = equip_store.worn_equips(profile, state, actor_uid)
            candidates = sorted(
                (record for record in equips.values()
                 if equip_store.is_real(record) and owners.get(_int(record.get("uid")), 0) == 0
                 and equip_store.require_level(record.get("id")) <= level),
                key=equip_store.carry_sort_key,
            )
            for record in candidates:
                pos = equip_store.equip_pos(record.get("id"))
                if pos not in worn and pos not in chosen:
                    chosen[pos] = record
        changed: list[dict[str, Any]] = []
        for _pos, record in sorted(chosen.items()):
            displaced, _previous = self._equip_put(profile, state, actor_uid, record)
            changed.append(record)
            if displaced is not None:
                changed.append(displaced)
        if changed:
            self._save(profile, state)
            self._record_task(profile, "TaskType_EquipOnMTimes", len(chosen))
        result = self._message("ApiEquipOnAll.result")
        result.new_actor.CopyFrom(self._st_actor(actor, profile, state))
        for record in changed:
            result.new_equips.add().CopyFrom(self._st_equip(record))
        return 200, result

    def _equip_match_index(self, payload: Mapping[str, Any]) -> int:
        return self._first_int(payload, "matchIndex", "match_index", "planId", "plan_id", "matchId", default=0)

    def _equip_apply_match(self, payload: Mapping[str, Any], actor: dict[str, Any], profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        index = self._equip_match_index(payload)
        plan = equip_store.plans(state).get(index)
        if plan is None or not plan["equipUids"]:
            return 404, {"error": "match_not_found", "matchIndex": index}
        actor_uid = _int(actor.get("uid"))
        level = self._equip_actor_level(actor)
        changed: dict[int, dict[str, Any]] = {}
        other_actors: list[int] = []
        for _pos, uid in sorted(plan["equipUids"].items()):
            record = state["equips"].get(str(uid))
            if not equip_store.is_real(record) or equip_store.require_level(record.get("id")) > level:
                continue
            displaced, previous = self._equip_put(profile, state, actor_uid, record)
            changed[_int(record.get("uid"))] = record
            if displaced is not None:
                changed[_int(displaced.get("uid"))] = displaced
            if previous and previous not in other_actors:
                other_actors.append(previous)
        if changed:
            self._save(profile, state)
        result = self._message("ApiEquipApplyMatch.result")
        result.change_actors.add().CopyFrom(self._st_actor(actor, profile, state))
        for uid in other_actors:
            other = actor_store.get(profile, state, uid)
            if other is not None:
                result.change_actors.add().CopyFrom(self._st_actor(other, profile, state))
        for record in changed.values():
            result.change_equips.add().CopyFrom(self._st_equip(record))
        return 200, result

    def _equip_save_match(self, payload: Mapping[str, Any], profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        index = self._equip_match_index(payload) or 1
        if not 1 <= index <= equip_store.MAX_MATCH_COUNT:
            return 400, {"error": "invalid_match_index", "matchIndex": index, "max": equip_store.MAX_MATCH_COUNT}
        selected = payload.get("equipUids", payload.get("equip_uids", payload.get("uids", [])))
        if isinstance(selected, Mapping):
            selected = list(selected.values())
        if not isinstance(selected, list):
            selected = []
        name = payload.get("matchName", payload.get("name"))
        changed = {_int(record.get("uid")): record
                   for record in equip_store.save_plan(state, index, selected, None if name is None else str(name))}
        current = equip_store.plans(state)
        for uid in current[index]["equipUids"].values():
            changed.setdefault(uid, state["equips"][str(uid)])
        self._save(profile, state)
        result = self._message("ApiEquipCustomMatch.result")
        equip_store.fill_custom_match(result.equip_custom_match, current)
        for record in changed.values():
            if equip_store.is_real(record):
                result.change_equips.add().CopyFrom(self._st_equip(record))
        return 200, result

    def _equip_rename_match(self, payload: Mapping[str, Any], profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        index = self._equip_match_index(payload) or 1
        if not 1 <= index <= equip_store.MAX_MATCH_COUNT:
            return 400, {"error": "invalid_match_index", "matchIndex": index, "max": equip_store.MAX_MATCH_COUNT}
        name = str(payload.get("matchName", payload.get("name", ""))).strip()[:32]
        current = equip_store.plans(state)
        current.setdefault(index, {"matchName": "", "equipUids": {}})["matchName"] = name
        equip_store.store_plans(state, current)
        self._save(profile, state)
        result = self._message("ApiEquipModifyMatchName.result")
        result.match_name = name
        return 200, result

    def _equip_set_lock(self, payload: Mapping[str, Any], record: dict[str, Any], profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """EquipDetail.SetLock: an item in a plan is taken out of it (the
        client asks "remove from match" first and sends isLock=false)."""

        if _int(record.get("matchIndex")) or any(
                _int(record.get("uid")) in plan["equipUids"].values() for plan in equip_store.plans(state).values()):
            equip_store.remove_from_plans(state, record.get("uid"))
        record["locked"] = bool(payload.get("isLock", payload.get("locked", payload.get("lock", not record.get("locked")))))
        self._save(profile, state)
        result = self._message("ApiSetEquipLock.result")
        result.equip.CopyFrom(self._st_equip(record))
        equip_store.fill_custom_match(result.equip_custom_match, equip_store.plans(state))
        return 200, result

    # -- 藏品 enhance / decompose -------------------------------------------
    # Rules and client evidence: equipment.py "Enhance (藏品强化), decompose".
    # Replies are ApiEquipEnhance.result / ApiEquipDecompose.result
    # {resChange: [STResourceDynamic]}; both callbacks run DBH.ResChange on
    # result.resChange (item counts absolute, equip countDelta -1 = removed).

    def _equip_upgrade(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        parts = [part for part in route.lower().split("/") if part]
        if parts[:2] != ["fci", "equip"]:
            return None
        rest = parts[2:]
        enhance = len(rest) == 2 and rest[0].isdigit() and rest[1] == "enhance"
        decompose = bool(rest) and rest[-1] in ("decompose", "dismantle") and (
            len(rest) == 1 or (len(rest) == 2 and rest[0].isdigit()))
        if not (enhance or decompose):
            return None
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        if enhance:
            return self._equip_enhance(_int(rest[0]), body, profile, state)
        return self._equip_decompose(_int(rest[0]) if len(rest) == 2 else 0, body, profile, state)

    def _equip_res_result(self, name: str, entries: list[Mapping[str, Any]]):
        """Typed ``{resChange}``: ``{"baseRes": {...}}``, ``{"equip": record, "countDelta": n}``
        or ``{"actor": STActor}`` (an update, countDelta 0)."""

        result = self._message(name)
        for entry in entries:
            dynamic = result.res_change.add()
            if "equip" in entry:
                equip_store.fill_st_equip(dynamic.equip, entry["equip"])
                dynamic.equip.count_delta = _int(entry.get("countDelta"))
                continue
            if "actor" in entry:
                dynamic.actor.CopyFrom(entry["actor"])
                dynamic.actor.count_delta = 0
                continue
            change = entry["baseRes"]
            dynamic.base_res.type = _int(change.get("type")); dynamic.base_res.id = _int(change.get("id"))
            dynamic.base_res.param = _int(change.get("param")); dynamic.base_res.count = _int(change.get("count"))
            dynamic.base_res.count_delta = _int(change.get("countDelta"))
        return result

    def _record_task(self, profile: Mapping[str, Any], goal: int | str, amount: int = 1, **kwargs: Any) -> None:
        """Advance TaskInfo goals after a successful action; never fails the reply."""

        tasks = getattr(self.game, "tasks", None)
        if tasks is None:
            return
        try:
            tasks.record(profile, goal, amount, **kwargs)
        except Exception:
            pass

    def _equip_enhance(self, equip_uid: int, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """``POST fci/equip/<uid>/enhance/ {foodRes:[{type,id,count,param}]}``."""

        payload = self._request_values(body)
        food = payload.get("foodRes", payload.get("food_res"))
        if not isinstance(food, list):
            request = self._request(body, "ApiEquipEnhance")
            food = [{"type": res.type, "id": res.id, "count": res.count, "param": res.param}
                    for res in request.food_res] if request is not None else []
        equips = state.setdefault("equips", {})
        record = equips.get(str(equip_uid))
        if not equip_store.is_real(record):
            return 404, {"error": "equip_not_found", "uid": equip_uid}
        player_level = max(1, _int(profile.get("level"), 1))
        level = max(1, _int(record.get("level"), 1))
        limit = equip_store.level_limit(record.get("id"), player_level)
        if level >= limit:
            # EU.ValidateEquipLevelup: Window_EquipLevelLimitedByAccountLevel
            # before Window_EquipLevelUpLimit.
            error = "equip_level_max" if level >= equip_store.max_level(record.get("id")) else "equip_level_limited_by_account_level"
            return 409, {"error": error, "levelLimit": limit}
        item_food: dict[int, int] = {}
        equip_food: list[dict[str, Any]] = []
        for entry in food:
            if not isinstance(entry, Mapping):
                return 400, {"error": "invalid_food_res"}
            rtype = _int(entry.get("type"))
            if rtype == equip_store.RES_ITEM:
                item_id, count = _int(entry.get("id")), _int(entry.get("count"))
                if item_id not in equip_store.EQUIP_EXP_ITEMS or count <= 0:
                    return 400, {"error": "invalid_food_res", "type": rtype, "id": item_id}
                item_food[item_id] = item_food.get(item_id, 0) + count
            elif rtype == equip_store.RES_EQUIP:
                uid = _int(entry.get("param"))
                if uid <= 0 or uid == equip_uid or any(_int(food_row.get("uid")) == uid for food_row in equip_food):
                    return 400, {"error": "invalid_food_res", "type": rtype, "uid": uid}
                blocker = equip_store.food_blocker(profile, state, equips.get(str(uid)))
                if blocker is not None:
                    return (404 if blocker == "equip_not_found" else 409), {"error": blocker, "uid": uid}
                equip_food.append(equips[str(uid)])
            else:
                return 400, {"error": "invalid_food_res", "type": rtype}
        if not item_food and not equip_food:
            return 400, {"error": "material_not_selected"}
        items = state.setdefault("items", {})
        for item_id, count in item_food.items():
            value = items.get(str(item_id))
            owned = _int(value.get("count")) if isinstance(value, Mapping) else 0
            if owned < count:
                return 409, {"error": "insufficient_item", "id": item_id, "required": count, "owned": owned}
        plan = equip_store.plan_enhance(record, item_food, equip_food, player_level)
        gold_changes: list[Mapping[str, Any]] = []
        if plan["gold"]:
            # Validated last so a refused payment leaves everything untouched.
            ok, gold_changes, error = self._change_resources(profile, [dict(equip_store.EXP_TO_RESOURCE, param=0, count=-plan["gold"])])
            if not ok:
                return 409, {"error": error or "insufficient_resource", "required": plan["gold"]}
        entries: list[dict[str, Any]] = []
        for item_id, count in item_food.items():
            value = items[str(item_id)]
            value["count"] = _int(value.get("count")) - count
            entries.append({"baseRes": {"type": equip_store.RES_ITEM, "id": item_id, "param": 0,
                                        "count": value["count"], "countDelta": -count}})
        entries.extend({"baseRes": change} for change in self._dynamic(gold_changes))
        for food_row in equip_food:
            equips.pop(str(_int(food_row.get("uid"))), None)
            entries.append({"equip": food_row, "countDelta": -1})
        entries.extend({"baseRes": change}
                       for change in equip_store.add_items(state, dict(plan["returnItems"]), self._now()))
        target = plan["targetLevel"]
        if target > level:
            record["randomAttrs"] = list(record.get("randomAttrs") or []) + equip_store.roll_level_attrs(
                record.get("id"), level + 1, target, getattr(self, "_rng", None))
        record["level"], record["exp"] = target, plan["curExp"]
        entries.append({"equip": record, "countDelta": 0})
        wearer = actor_store.get(profile, state, equip_store.owner_uid(profile, state, record.get("actorUid")) or -1)
        if wearer is not None and target > level:
            # The carrier's equipAttrs/attrs follow the new level; DBH.ResChange
            # replaces fci/actor/<uid> with this update (countDelta 0).
            entries.append({"actor": self._st_actor(wearer, profile, state)})
        self._save(profile, state)
        self._record_task(profile, "TaskType_EquipEnhanceMTimes")
        for threshold in range(level + 1, target + 1):
            reached = sum(1 for row in equips.values() if equip_store.is_real(row) and _int(row.get("level"), 1) >= threshold)
            self._record_task(profile, "TaskType_EquipEnhanceP0LevelMCount", 0, param=threshold, value=reached)
        return 200, self._equip_res_result("ApiEquipEnhance.result", entries)

    def _equip_decompose(self, path_uid: int, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """``POST fci/equip/decompose/ {equipUids:[uid...]}`` (Window_Bag)."""

        payload = self._request_values(body)
        raw = payload.get("equipUids", payload.get("equip_uids"))
        if not isinstance(raw, list):
            request = self._request(body, "ApiEquipDecompose")
            raw = list(request.equip_uids) if request is not None else []
        uids: list[int] = []
        for value in list(raw) + ([path_uid] if path_uid else []):
            if _int(value) > 0 and _int(value) not in uids:
                uids.append(_int(value))
        if not uids:
            return 400, {"error": "no_equip_selected"}
        equips = state.setdefault("equips", {})
        records = []
        for uid in uids:
            blocker = equip_store.food_blocker(profile, state, equips.get(str(uid)))
            if blocker is not None:
                return (404 if blocker == "equip_not_found" else 409), {"error": blocker, "uid": uid}
            records.append(equips[str(uid)])
        exp_items, gold = equip_store.decompose_yield(records)
        entries: list[dict[str, Any]] = [{"equip": record, "countDelta": -1} for record in records]
        if gold:
            ok, gold_changes, error = self._change_resources(profile, [dict(equip_store.EXP_TO_RESOURCE, param=0, count=gold)])
            if not ok:
                return 409, {"error": error or "resource_update_failed"}
            entries.extend({"baseRes": change} for change in self._dynamic(gold_changes))
        for uid in uids:
            equips.pop(str(uid), None)
        entries.extend({"baseRes": change} for change in equip_store.add_items(state, exp_items, self._now()))
        self._save(profile, state)
        return 200, self._equip_res_result("ApiEquipDecompose.result", entries)

    def _equipment_maintenance(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        carried = self._equip_carry(method, route, body, profile, state)
        if carried is not None:
            return carried
        upgraded = self._equip_upgrade(method, route, body, profile, state)
        if upgraded is not None:
            return upgraded
        lower = route.lower()
        if not lower.startswith("/fci/equip") and "setequiplock" not in lower and "equiplock" not in lower:
            return None
        if not any(token in lower for token in ("lock", "equipon", "equipoff", "onall", "decompose", "dismantle", "plan", "custommatch", "modifymatchname", "modify-match-name")):
            return None
        payload = self._request_values(body); parts = [part for part in lower.split("/") if part]
        equip_id = self._first_int(payload, "equipUid", "equip_uid", "uid", "id", default=20001)
        for index, part in enumerate(parts):
            if part.isdigit() and index > 1: equip_id = _int(part, equip_id); break
        equips = state.setdefault("equips", {})
        equip = equips.setdefault(str(equip_id), {"id": equip_id, "uid": equip_id, "level": 1, "exp": 0, "locked": False, "actorUid": 0, "plan": 0})
        if "lock" in lower:
            if method == "GET": return 200, {"uid": equip_id, "isLock": bool(equip.get("locked"))}
            if method not in {"POST", "PATCH"}: return 405, {"error": "method_not_allowed", "message": "POST required"}
            equip["locked"] = bool(payload.get("isLock", payload.get("locked", payload.get("lock", not equip.get("locked")))))
            self._save(profile, state)
            try:
                result = self._message("ApiSetEquipLock.result"); result.equip.uid = equip_id; result.equip.id = _int(equip.get("id")); result.equip.level = _int(equip.get("level"), 1); result.equip.is_lock = bool(equip["locked"]); return 200, result
            except Exception: return 200, {"uid": equip_id, "isLock": bool(equip["locked"])}

        # Equipment loadouts are stored per account.  The protobuf map uses a
        # numeric key and a DBGamePlayer.EquipCustomMatch value; accept both
        # that wire shape and the compact JSON shape used by the local tools.
        if "custommatch" in lower or "custom-match" in lower:
            plans = state.setdefault("equipPlans", {})
            if method == "GET":
                try:
                    result = self._message("ApiEquipCustomMatch.result")
                    for key, raw in plans.items():
                        plan = result.equip_custom_match[_int(key, 1)]
                        if isinstance(raw, Mapping):
                            plan.match_name = str(raw.get("matchName", raw.get("name", f"方案{key}")))[:32]
                            selected = raw.get("equipUids", raw.get("equip_uids", []))
                        else:
                            selected = []
                        if isinstance(selected, list):
                            for uid in selected:
                                plan.match_equip_uids[_int(uid, 0)] = _int(uid, 0)
                    return 200, result
                except Exception:
                    return 200, {"plans": plans, "sandbox": True}
            if method != "POST" and method != "PATCH":
                return 405, {"error": "method_not_allowed", "message": "POST or PATCH required"}
            plan_id = self._first_int(payload, "planId", "plan_id", "matchId", "match_id", default=1)
            selected = payload.get("equipUids", payload.get("equip_uids", payload.get("uids", [])))
            if not isinstance(selected, list):
                selected = [equip_id]
            selected = sorted({_int(uid) for uid in selected if _int(uid) > 0})
            plans[str(plan_id)] = {"matchName": str(payload.get("matchName", payload.get("name", f"方案{plan_id}")))[:32], "equipUids": selected}
            self._save(profile, state)
            try:
                result = self._message("ApiEquipCustomMatch.result")
                plan = result.equip_custom_match[plan_id]
                plan.match_name = plans[str(plan_id)]["matchName"]
                for uid in selected:
                    plan.match_equip_uids[uid] = uid
                for uid in selected:
                    if str(uid) in equips:
                        result.change_equips.add().CopyFrom(self._st_equip(equips[str(uid)]))
                return 200, result
            except Exception:
                return 200, {"planId": plan_id, "plan": plans[str(plan_id)], "sandbox": True}

        if "modifymatchname" in lower or "modify-match-name" in lower:
            if method != "POST" and method != "PATCH":
                return 405, {"error": "method_not_allowed", "message": "POST or PATCH required"}
            plan_id = self._first_int(payload, "planId", "plan_id", "matchId", "match_id", default=1)
            plans = state.setdefault("equipPlans", {})
            value = plans.setdefault(str(plan_id), {"matchName": f"方案{plan_id}", "equipUids": []})
            value["matchName"] = str(payload.get("matchName", payload.get("name", value.get("matchName", f"方案{plan_id}"))))[:32]
            self._save(profile, state)
            try:
                result = self._message("ApiEquipModifyMatchName.result")
                result.match_name = value["matchName"]
                return 200, result
            except Exception:
                return 200, {"planId": plan_id, "matchName": value["matchName"], "sandbox": True}
        if "equipon" in lower or "onall" in lower or lower.endswith("/on") or lower.endswith("/off") or "equip-off" in lower or "equipoff" in lower:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            actor_uid = self._first_int(payload, "actorUid", "actor_uid", "actorId", default=1001)
            if "off" in lower:
                equip["actorUid"] = 0
            else:
                equip["actorUid"] = actor_uid
            self._save(profile, state)
            if "onall" in lower:
                actor_uid = self._first_int(payload, "actorUid", "actor_uid", "actorId", default=1001)
                selected = payload.get("equipUids", payload.get("equip_uids", []))
                if not isinstance(selected, list): selected = [equip_id]
                for selected_uid in selected:
                    value = equips.setdefault(str(_int(selected_uid)), {"id": _int(selected_uid), "uid": _int(selected_uid), "level": 1, "exp": 0, "locked": False, "actorUid": 0, "plan": 0})
                    value["actorUid"] = actor_uid
                self._save(profile, state)
                try:
                    result = self._message("ApiEquipOnAll.result")
                    actor = self._actor_for(state, actor_uid, profile)
                    result.new_actor.CopyFrom(self._st_actor(actor, profile, state))
                    for selected_uid in selected:
                        result.new_equips.add().CopyFrom(self._st_equip(equips[str(_int(selected_uid))]))
                    return 200, result
                except Exception:
                    return 200, {"actorUid": actor_uid, "equipUids": [_int(x) for x in selected], "sandbox": True}
            try:
                response_name = "ApiEquipOff.result" if "off" in lower else "ApiEquipOn.result"
                result = self._message(response_name)
                actor = self._actor_for(state, actor_uid, profile)
                st_actor = self._st_actor(actor, profile, state)
                if "off" in lower:
                    result.new_actor.CopyFrom(st_actor); result.new_equip.CopyFrom(self._st_equip(equip)); result.off_equips.add().CopyFrom(self._st_equip(equip))
                else:
                    result.new_actor.CopyFrom(st_actor); result.new_equip.CopyFrom(self._st_equip(equip))
                    result.old_actor.CopyFrom(st_actor); result.old_equip.CopyFrom(self._st_equip(equip))
                return 200, result
            except Exception:
                return 200, {"uid": equip_id, "actorUid": _int(equip.get("actorUid")), "sandbox": True}
        if "plan" in lower or "custommatch" in lower:
            if method == "GET": return 200, {"plans": state.setdefault("equipPlans", {})}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            plan_id = str(self._first_int(payload, "planId", "plan_id", default=1)); plans = state.setdefault("equipPlans", {})
            plans[plan_id] = dict(payload); self._save(profile, state); return 200, {"planId": _int(plan_id), "plan": plans[plan_id]}
        return None

    def _item_resource(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        query = parse_qs(urlsplit(route).query, keep_blank_values=True)
        lower = _route(route).lower(); payload = self._request_values(body)
        item_route = lower in {"/fci/item", "/fci/item/", "/fci/item/use", "/fci/item/sell"} or any(token in lower for token in ("playeritem", "itemuse", "item-use", "itemsell", "item/sell", "/item/"))
        resource_route = any(token in lower for token in ("baseresource", "base-resource", "resourcecompose", "resource-compose", "/resource/compose", "resource/newflag", "resource-new-flag", "resourcefull"))
        if not (item_route or resource_route): return None
        # The client's own routes (ItemDetail/AutoSell, ItemDetail/ManualBox*,
        # StarCompose) follow the client tables; the /fci/item/use|sell and
        # /fci/resource/compose aliases below stay local tool seams.
        client_route = {"/fci/itemsell": self._item_sell, "/fci/itemuse": self._item_use,
                        "/fci/resourcecompose": self._resource_compose}.get(lower.rstrip("/"))
        if client_route is not None:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return client_route(body, profile, state)
        items = state.setdefault("items", {})

        if method in {"GET", "HEAD"} and (lower.rstrip("/") == "/fci/item" or lower.startswith("/fci/item/")):
            def item_view(item_id):
                value = items.get(str(item_id), {})
                # A consumed stack still has a binding: return zero so ItemChange
                # can clear it instead of leaving the stale client count intact.
                return {"id": item_id, "count": _int(value.get("count")),
                        "gotTime": _int(value.get("gotTime")), "isNew": bool(value.get("isNew", False))}
            tail = lower.rstrip("/").rsplit("/", 1)[-1]
            if tail != "item":
                if not tail.isdecimal() or int(tail) <= 0:
                    return 400, {"error": "invalid_item_uid"}
                return 200, item_view(int(tail))
            if "uids" in query:
                values = [value.strip() for group in query["uids"] for value in group.split(",")]
                if any(not value.isdecimal() or int(value) <= 0 for value in values):
                    return 400, {"error": "invalid_item_uids"}
                return 200, [item_view(int(value)) for value in values]

        # The real client uses a typed single-item endpoint for the item
        # detail popup.  Keep the historical list endpoint as JSON below, but
        # answer ``/playeritem/<id>`` with the recovered STItem envelope.
        if lower.startswith("/fci/playeritem/") and lower.rstrip("/").rsplit("/", 1)[-1].isdigit() and method == "GET":
            item_id = _int(lower.rstrip("/").rsplit("/", 1)[-1])
            value = items.get(str(item_id), {})
            try:
                result = self._message("ApiPlayerItem.result")
                result.result.id = item_id
                result.result.count = _int(value.get("count"))
                result.result.got_time = _int(value.get("gotTime"))
                result.result.is_new = bool(value.get("isNew", False))
                return 200, result
            except Exception:
                return 200, {"id": item_id, "count": _int(value.get("count")), "sandbox": True}

        if item_route:
            item_id = self._first_int(payload, "itemId", "item_id", "id", default=0)
            count = max(1, self._first_int(payload, "count", "num", default=1))
            if ("playeritem" in lower or lower in {"/fci/item", "/fci/item/"}) and method == "GET":
                result = []
                for key, value in items.items():
                    if _int(value.get("count")) > 0: result.append({"id": _int(key), "count": _int(value.get("count")), "gotTime": _int(value.get("gotTime")), "isNew": bool(value.get("isNew", True))})
                return 200, {"items": result}
            if "sell" in lower:
                if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
                current = _int(items.get(str(item_id), {}).get("count"))
                if current < count: return 409, {"error": "insufficient_item"}
                items[str(item_id)]["count"] = current - count
                ok, changes, error = self._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": 10 * count}])
                if not ok: return 409, {"error": error or "resource_update_failed"}
                self._save(profile, state)
                try:
                    result = self._message("ApiItemSell.result")
                    for change in self._dynamic(changes):
                        entry = result.res_change.add(); entry.base_res.type = change["type"]; entry.base_res.id = change["id"]; entry.base_res.param = change["param"]; entry.base_res.count = change["count"]; entry.base_res.count_delta = change["countDelta"]
                    return 200, result
                except Exception: return 200, {"resChange": self._dynamic(changes)}
            if "use" in lower:
                if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
                current = _int(items.get(str(item_id), {}).get("count"))
                if current < count: return 409, {"error": "insufficient_item"}
                items[str(item_id)]["count"] = current - count
                # Local consumables grant actor EXP and a small gold reward.
                actor = self._actor_for(state, self._first_int(payload, "actorId", "actor_id", default=1001), profile)
                actor["exp"] = _int(actor.get("exp")) + 50 * count
                ok, changes, error = self._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": 5 * count}])
                if not ok: return 409, {"error": error or "resource_update_failed"}
                self._save(profile, state)
                try:
                    result = self._message("ApiPlayerItemUse.result")
                    for change in self._dynamic(changes):
                        entry = result.res_change.add(); entry.base_res.type = change["type"]; entry.base_res.id = change["id"]; entry.base_res.param = change["param"]; entry.base_res.count = change["count"]; entry.base_res.count_delta = change["countDelta"]
                    return 200, result
                except Exception: return 200, {"resChange": self._dynamic(changes), "actorExp": actor["exp"]}
            # POST to the item endpoint is a local item grant used by tests
            # and by the sandbox shop.  It is explicit and bounded.
            if method == "POST" and item_id:
                entry = items.setdefault(str(item_id), {"id": item_id, "count": 0, "gotTime": self._now(), "isNew": True})
                entry["count"] = _int(entry.get("count")) + count; entry["gotTime"] = self._now(); self._save(profile, state)
                return 200, {"item": dict(entry)}
            return 405, {"error": "method_not_allowed", "message": "GET or POST required"}

        # Base resources are exposed both as typed protobuf where descriptors
        # exist and as JSON for partial builds.  They share the profile ledger.
        if lower in {"/fci/playerbaseresource", "/fci/playerbaseresource/"} and method == "GET":
            try:
                result = self._message("ApiPlayerBaseResource.result")
                for item in profile.get("resources", []):
                    if not isinstance(item, Mapping):
                        continue
                    entry = result.result.add(); entry.type = _int(item.get("type")); entry.id = _int(item.get("id")); entry.count = _int(item.get("count")); entry.param = _int(item.get("param"))
                return 200, result
            except Exception:
                return 200, {"resources": list(profile.get("resources", [])), "sandbox": True}
        if lower in {"/fci/playerbaseresource/full", "/fci/playerbaseresource/full/"} and method == "GET":
            try:
                result = self._message("ApiPlayerBaseResourceFull.result")
                for item in profile.get("resources", []):
                    if not isinstance(item, Mapping):
                        continue
                    entry = result.result.add(); entry.type = _int(item.get("type")); entry.id = _int(item.get("id")); entry.count = _int(item.get("count")); entry.param = _int(item.get("param"))
                return 200, result
            except Exception:
                return 200, {"resources": list(profile.get("resources", [])), "sandbox": True}
        if lower in {"/fci/baseresource", "/fci/baseresource/", "/fci/base-resource", "/fci/base-resource/", "/fci/resource", "/fci/resource/"} and method == "GET":
            return 200, self.game._resource_message(profile)
        if "newflag" in lower or "new-flag" in lower:
            if method == "GET": return 200, {"isNew": bool(state.get("resourceNewFlag", False)), "resInfo": []}
            if method not in {"POST", "PATCH"}: return 405, {"error": "method_not_allowed", "message": "PATCH required"}
            state["resourceNewFlag"] = bool(payload.get("isNew", payload.get("patch", False))); self._save(profile, state)
            try:
                result = self._message("ApiResourceNewFlag.patch")
                result.patch = bool(state["resourceNewFlag"])
                return 200, result
            except Exception:
                return 200, {"isNew": bool(state["resourceNewFlag"])}
        if "compose" in lower:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            rtype = self._first_int(payload, "type", default=6); rid = self._first_int(payload, "id", default=0); amount = max(1, self._first_int(payload, "count", "num", default=1))
            # One local recipe: three low-tier resources become one higher
            # tier resource.  The recipe is deterministic and atomic.
            source = {"type": rtype, "id": rid, "param": 0, "count": -3 * amount}; target = {"type": rtype, "id": rid + 1, "param": 0, "count": amount}
            ok, changes, error = self._change_resources(profile, [source, target])
            if not ok: return 409, {"error": error or "insufficient_resource"}
            self._save(profile, state)
            try:
                result = self._message("ApiResourceCompose.result")
                for change in self._dynamic(changes):
                    entry = result.res_change.add(); entry.base_res.type = change["type"]; entry.base_res.id = change["id"]; entry.base_res.param = change["param"]; entry.base_res.count = change["count"]; entry.base_res.count_delta = change["countDelta"]
                return 200, result
            except Exception: return 200, {"resChange": self._dynamic(changes)}
        if method == "POST" and lower in {"/fci/baseresource", "/fci/baseresource/", "/fci/resource", "/fci/resource/"}:
            rtype = self._first_int(payload, "type", default=7); rid = self._first_int(payload, "id", default=0); amount = self._first_int(payload, "count", default=0)
            if not amount: return 400, {"error": "invalid_resource_count"}
            ok, changes, error = self._change_resources(profile, [{"type": rtype, "id": rid, "param": 0, "count": amount}])
            if not ok: return 409, {"error": error or "resource_update_failed"}
            return 200, {"resChange": self._dynamic(changes)}
        return None

    def _medal_seal_cultivation(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower(); payload = self._request_values(body)
        if "chip" in lower and "exchange" in lower:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            chip_id = self._first_int(payload, "chipId", "chip_id", "id", default=0)
            amount = max(1, self._first_int(payload, "count", "num", default=1))
            actor_id = self._first_int(payload, "actorId", "actor_id", default=chip_id)
            # Actor chips are represented by resource type 15.  Exchanging
            # them for local gold keeps the operation useful even when the
            # original activity tables are unavailable.
            ok, changes, error = self._change_resources(profile, [
                {"type": 15, "id": actor_id, "param": 0, "count": -amount},
                {"type": 7, "id": 0, "param": 0, "count": 100 * amount},
            ])
            if not ok: return 409, {"error": error or "insufficient_resource"}
            self._save(profile, state)
            return 200, {"chipId": chip_id, "count": amount, "resChange": self._dynamic(changes), "sandbox": True}
        if "medal" in lower:
            medals = state.setdefault("medals", {})
            medal_id = self._first_int(payload, "id", "medalId", default=1)
            if method == "GET": return 200, {"medals": [dict(v) for v in medals.values()]}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            medals[str(medal_id)] = {"id": medal_id, "gotTime": self._now(), "isNew": True}; self._save(profile, state)
            try:
                result = self._message("ApiMedal.result"); result.result.id = medal_id; result.result.got_time = self._now(); result.result.is_new = True; return 200, result
            except Exception: return 200, {"medal": medals[str(medal_id)]}
        if "seal" in lower:
            seals = state.setdefault("seals", {"cells": {}, "rewards": {}, "newOpenFlag": False})
            if method == "GET":
                try:
                    result = self._message("ApiSeal.result")
                    for value in seals.get("cells", {}).values():
                        cell = result.cell_info.add(); cell.info.cell_id = _int(value.get("cellId")); cell.fill = bool(value.get("filled", False))
                    for value in seals.get("rewards", {}).values():
                        reward = result.reward_info.add(); reward.info.reward_id = _int(value.get("id")); reward.got = bool(value.get("claimed", False))
                    return 200, result
                except Exception:
                    return 200, {"cellInfo": list(seals.get("cells", {}).values()), "rewardInfo": list(seals.get("rewards", {}).values()), "sandbox": True}
            if "flag" in lower:
                seals["newOpenFlag"] = bool(payload.get("newOpenFlag", payload.get("isNew", False))); self._save(profile, state)
                try:
                    result = self._message("ApiSealFlag.result"); result.new_open_flag = bool(seals["newOpenFlag"]); return 200, result
                except Exception: return 200, {"newOpenFlag": seals["newOpenFlag"]}
            cell_id = self._first_int(payload, "cellId", "cell_id", "id", default=1)
            if "reward" in lower:
                reward_id = self._first_int(payload, "rewardId", "reward_id", "id", default=1)
                key = str(reward_id)
                if seals.setdefault("rewards", {}).get(key, {}).get("claimed"): return 409, {"error": "seal_reward_already_claimed"}
                ok, changes, error = self._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": 100}])
                if not ok: return 409, {"error": error or "resource_update_failed"}
                seals["rewards"][key] = {"id": reward_id, "claimed": True}; self._save(profile, state)
                try:
                    result = self._message("ApiSealReward.result"); result.reward_id = reward_id
                    for change in self._dynamic(changes):
                        item = result.res_change.add(); item.base_res.type = change["type"]; item.base_res.id = change["id"]; item.base_res.param = change["param"]; item.base_res.count = change["count"]; item.base_res.count_delta = change["countDelta"]
                    return 200, result
                except Exception: return 200, {"rewardId": reward_id, "resChange": self._dynamic(changes)}
            if "fill" in lower:
                if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
                seals.setdefault("cells", {})[str(cell_id)] = {"cellId": cell_id, "filled": True}; self._save(profile, state)
                try:
                    result = self._message("ApiSealFill.result"); result.cell_id = cell_id; return 200, result
                except Exception: return 200, {"cellId": cell_id}
            return 200, {"cellInfo": list(seals.get("cells", {}).values()), "rewardInfo": list(seals.get("rewards", {}).values())}
        if "cultivation" in lower:
            cultivation = state.setdefault("cultivation", {})
            if method == "GET" and lower.rstrip("/") == "/fci/actor-cultivation":
                # Collection read: Welfare2.ShowActorCultivationAble iterates
                # the records (``v.actorId``), so return a list - only the
                # cultivations the player actually took part in (POSTed).
                return 200, [dict(info) for info in cultivation.values() if isinstance(info, Mapping) and info.get("joined")]
            actor_id = self._first_int(payload, "actorId", "actor_id", default=1001); key = str(actor_id)
            info = cultivation.setdefault(key, {"actorId": actor_id, "activityId": 1, "progress": 0, "progressDaily": 0, "progressReward": 0, "progressRewardVip": 0, "vip": False, "startTime": self._now(), "endTime": self._now() + 86400 * 30, "new": False, "showEnd": False})
            if method == "GET": return 200, dict(info)
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            info["joined"] = True
            info["progress"] = max(0, _int(info.get("progress")) + self._first_int(payload, "progress", default=1)); info["progressDaily"] = _int(info.get("progressDaily")) + 1
            if "vip" in lower: info["vip"] = True
            if "showend" in lower: info["showEnd"] = True
            self._save(profile, state)
            try:
                name = "ApiActorCultivation.result"
                for marker, response_name in (("newflag", "ApiActorCultivationNewFlag.result"), ("showend", "ApiActorCultivationShowEnd.result"), ("done", "ApiActorCultivationDone.result"), ("upgradevip", "ApiActorCultivationUpgradeVip.result"), ("taskreward", "ApiActorCultivationTaskReward.result"), ("progressreward", "ApiActorCultivationProgressReward.result")):
                    if marker in lower: name = response_name; break
                result = self._message(name)
                field = "result" if hasattr(result, "result") else "info"
                getattr(result, field).CopyFrom(self._cultivation_message(info)); return 200, result
            except Exception: return 200, dict(info)
        return None

    def _cultivation_message(self, info: Mapping[str, Any]):
        result = self._message("ActorCultivationInfo")
        for source, target in (("activityId", "activity_id"), ("actorId", "actor_id"), ("progress", "progress"), ("progressDaily", "progress_daily"), ("progressReward", "progress_reward"), ("progressRewardVip", "progress_reward_vip"), ("vip", "vip"), ("startTime", "start_time"), ("endTime", "end_time"), ("new", "new"), ("showEnd", "show_end")):
            if hasattr(result, target): setattr(result, target, info.get(source, 0))
        return result

    def _gacha_pool(self, state: dict[str, Any], pool_id: int, now: int) -> dict[str, Any]:
        """Per-pool gacha counters; the daily count resets at 06:00 Beijing."""

        pools = state.setdefault("gachaPools", {})
        pool = pools.setdefault(str(pool_id), {"total": 0, "today": 0, "day": 0, "nextFree": 0, "firstTenDone": False})
        day = policy_for(self.game).day_index(now)
        if period_due(pool.get("day"), day):
            pool["day"], pool["today"] = day, 0
        config = GACHA_TYPES.get(pool_id, {})
        if config.get("gachaFreeInterval") and not int(pool.get("nextFree", 0) or 0):
            # A new account's daily free draw is available at once; the client
            # only treats 0 < nextFreeGachaTime <= now as free (IsFreeGacha).
            pool["nextFree"] = int(now) - 1
        self._gacha_init_pity(pool, config, [row for row in state.get("gacha_records", [])
                                           if _int(row.get("pool")) == pool_id])
        return pool

    @staticmethod
    def _gacha_init_pity(counter: dict[str, Any], config: Mapping[str, Any], history: list) -> None:
        # Upgrade existing all-green histories without resetting earned pity.
        for key, quality, limit in (("purpleMisses", 3, _int(config.get("actorDropCount"))),
                                    ("orangeMisses", 4, _int(config.get("notGetOrangeMax")))):
            if key in counter or not limit:
                continue
            misses = max(0, _int(counter.get("total")))
            for distance, row in enumerate(reversed(history)):
                if _int(row.get("quality")) >= quality:
                    misses = distance
                    break
            counter[key] = min(misses, limit - 1)

    def _gacha_open_activities(self, profile: Mapping[str, Any], now: int, pool_id: int = 2) -> list[int]:
        status, rows = self.game.events._activity_list("GET")
        if status != 200:
            return []
        weekday = policy_for(self.game).local_datetime(now).isoweekday()
        table = GACHA_NORMAL_UP if pool_id == 1 else GACHA_LIMITED
        activity_type = 14 if pool_id == 1 else 4
        return sorted({_int(row.get("activityId")) for row in rows
                       if _int(row.get("type")) == activity_type and _int(row.get("activityId")) in table
                       and _int(row.get("timestampStart")) <= now
                       and (not _int(row.get("timestampEnd")) or now < _int(row.get("timestampEnd")))
                       and (not row.get("weekdays") or weekday in row["weekdays"])
                       and _int(profile.get("level"), 1) >= _int(row.get("levelLimit"))})

    @staticmethod
    def _gacha_activity_row(activity_id: int, counter: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, int]:
        interval = _int(config.get("actorDropCount"))
        return {"activityId": activity_id,
                "nextGetPurpleCount": max(1, interval - _int(counter.get("purpleMisses"))) if interval else 0,
                "curDropActivityActor": _int(counter.get("curDropActivityActor")),
                "preDropActivityActor": _int(counter.get("preDropActivityActor")),
                "getActivityActorCount": _int(counter.get("getActivityActorCount"))}

    def _gacha_info_row(self, pool_id: int, pool: dict[str, Any], activity_ids: list[int] = ()) -> dict[str, Any]:
        config = GACHA_TYPES.get(pool_id, {})
        if pool_id in (1, 2):
            activities = pool.setdefault("activities", {})
            activity_info = [self._gacha_activity_row(aid, activities.setdefault(str(aid), {}), config)
                             for aid in activity_ids]
            if pool_id == 1:
                activity_info.append(self._gacha_activity_row(0, pool, config))
        else:
            activity_info = [self._gacha_activity_row(0, pool, config)] if pool_id == 1 else []
        return {
            "id": pool_id,
            "gachaCountToday": int(pool.get("today", 0)),
            "nextFreeGachaTime": int(pool.get("nextFree", 0) or 0) if config.get("gachaFreeInterval") else 0,
            "nextGetPurpleCount": self._gacha_activity_row(0, pool, config)["nextGetPurpleCount"],
            "activityInfo": activity_info,
            "actorRecord": [],
        }

    def _gacha(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        parsed = urlsplit(route)
        route = parsed.path.rstrip("/")
        if not route.lower().startswith(("/fci/gacha", "/fci/card/gacha")):
            return None
        # Currency, tickets, characters and pity must commit together even if
        # saving state or projecting the native response fails.
        with self.game.atomic_mutation():
            payload = self._request_values(body)
            query_id = parse_qs(parsed.query, keep_blank_values=True).get("localRequestId", [None])[0]
            request_id = payload.get("requestId", query_id) if method == "POST" else None
            if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
                return 400, {"error": "invalid_request"}
            native = self._request(body, "ApiGachaTrade") if request_id and not payload else None
            request_data = payload or (json_format.MessageToDict(native) if native is not None else {})
            signature = json.dumps({k: v for k, v in request_data.items() if k != "requestId"}, sort_keys=True,
                                   separators=(",", ":"))
            receipt = state.get("gachaRequestReceipts", {}).get(request_id) if request_id else None
            if receipt:
                if receipt["request"] != signature:
                    return 409, {"error": "invalid_request"}
                return 200, json_format.ParseDict(receipt["result"], self._message("ApiGachaTrade.result"))
            response = self._gacha_transaction(method, route, body, profile, state)
            if request_id and response is not None and response[0] == 200 and hasattr(response[1], "actor_record"):
                state.setdefault("gachaRequestReceipts", {})[request_id] = {
                    "request": signature, "result": json_format.MessageToDict(response[1])}
                self._save(profile, state)
            return response

    def _gacha_transaction(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """Gacha pools (fci/gachainfo/) and draws (fci/gachatrade/).

        Pools, costs, the daily free interval, the 10-draw purple counter and
        actor weights come from the client's GachaType/GachaActorPool tables.
        Window_GachaNormal searches the stored fci/gachainfo/ value for
        ``id == 1`` directly, so pools are returned as a plain JSON list.
        The first normal-pool trade is the newcomer ten-pull (GachaType 1
        dropIdForce FirstGacha_01..10, forced tutorial step 5); 2018 guides
        record that it guarantees Leafa (actor 2108).  Its other nine actors
        were server-only and are drawn here as distinct pool actors.
        Every draw creates a distinct uid.  Quality uses ActorStarRandom way 5;
        recovered purple/orange parameters enforce the displayed hard pity.
        """

        lower = route.lower()
        now = self._now()
        if lower in {"/fci/gachainfo", "/fci/gachainfo/"}:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            rows = [self._gacha_info_row(pool_id, self._gacha_pool(state, pool_id, now),
                                       self._gacha_open_activities(profile, now, pool_id))
                    for pool_id in sorted(GACHA_TYPES)]
            self._save(profile, state)
            return 200, rows
        if not (lower.startswith("/fci/gacha") or lower.startswith("/fci/card/gacha")):
            return None
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        payload = self._request_values(body)
        msg = self._request(body, "ApiGachaTrade")
        # Window_Gacha always sends the selected pool id.  Falling back to
        # pool 1 on a malformed request can silently charge the normal pool
        # when the caller intended a limited/bookmark pool, so reject it.
        if "id" not in payload and (msg is None or not _int(getattr(msg, "id", 0))):
            return 400, {"error": "gacha_id_required"}
        pool_id = _int(payload.get("id", getattr(msg, "id", 0) if msg else 0), 0)
        config = GACHA_TYPES.get(pool_id)
        if config is None or not GACHA_POOL:
            return 404, {"error": "unknown_gacha", "id": pool_id}
        pool = self._gacha_pool(state, pool_id, now)
        activity_id = _int(payload.get("activityId", getattr(msg, "activity_id", 0) if msg else 0))
        if pool_id == 2 or (pool_id == 1 and activity_id):
            if activity_id not in self._gacha_open_activities(profile, now, pool_id):
                return 409, {"error": "activity_not_open"}
            counter = pool.setdefault("activities", {}).setdefault(str(activity_id), {})
            self._gacha_init_pity(counter, config, [])
        elif activity_id:
            return 400, {"error": "invalid_gacha_activity"}
        else:
            counter = pool
        # Window_Gacha.DoGacha: pools 1/2 send {id, activityId?, count,
        # cinematic, gachaFree} and never a cost (the server splits tickets
        # then GachaType.costSub 迷子); GachaChips (5) sends the chosen chips
        # as ``cost``.  gachaFree is IsFreeGacha() of pool 1 and also rides
        # along on other pools' requests, so it only counts for a pool with
        # gachaFreeInterval, and only for the count-0 free draw.
        flag_free = bool(payload.get("gachaFree", getattr(msg, "gacha_free", False) if msg else False))
        requested = _int(payload.get("count", getattr(msg, "count", 0) if msg else 0))
        costs = payload.get("cost", [])
        if isinstance(costs, Mapping):
            costs = [costs]
        costs = [item for item in (costs or []) if isinstance(item, Mapping)]
        if not costs and msg is not None and msg.cost:
            costs = [{"type": c.type, "id": c.id, "count": c.count} for c in msg.cost]
        cost_main = config.get("cost") if isinstance(config.get("cost"), Mapping) else {}
        cost_sub = config.get("costSub") if isinstance(config.get("costSub"), Mapping) else {}
        changes: list[dict[str, Any]] = []
        # Newcomer guarantee (GachaType 1 dropIdForce): the account's first
        # paid ten-pull on the normal pool includes Leafa and distinct
        # characters.  It is paid like any ten-pull (the tutorial grants the
        # 10 tickets via stage 2061's first clear); single/free draws are
        # never turned into a free ten-pull.
        first_ten = pool_id == 1 and not activity_id and not pool.get("firstTenDone") and requested == 10
        free = flag_free and bool(config.get("gachaFreeInterval")) and requested <= 0 and not costs
        items = state.setdefault("items", {})
        item_costs: dict[int, int] = {}
        ledger: list[dict[str, int]] = []
        if free:
            if int(pool.get("nextFree", 0) or 0) > now:
                return 409, {"error": "gacha_not_free"}
            times = 1
        elif pool_id == 5 or (costs and not cost_main):
            # GachaChips: every safeCount chips is one character; a remainder
            # is a GachaActorChipChance roll (Window_GachaChips.UpdateConsume).
            safe = max(1, _int(config.get("safeCount"), 10))
            total = 0
            for item in costs:
                item_id, count = _int(item.get("id")), max(0, _int(item.get("count")))
                if _int(item.get("type"), _RES_ITEM) != _RES_ITEM or _int(ITEM_INFO.get(item_id, {}).get("type")) != _ITEM_TYPE_ACTOR_CHIP:
                    return 400, {"error": "invalid_gacha_cost", "id": item_id}
                item_costs[item_id] = item_costs.get(item_id, 0) + count
                total += count
            if total < safe:
                return 409, {"error": "gacha_chips_not_enough", "safeCount": safe}
            times = total // safe
            chance = GACHA_CHIP_CHANCE.get((pool_id, total % safe), 0)
            chip_rng = random.Random((_int(profile.get("account_id")) * 31) ^ (int(pool.get("total", 0)) * 131) ^ total)
            if chance and chip_rng.randrange(10000) < chance:
                times += 1
        elif costs:
            # Explicit cost (local tools / older seam): draws the cost pays for.
            times = 0
            for item in costs:
                ctype, cid, count = _int(item.get("type")), _int(item.get("id")), max(0, _int(item.get("count")))
                for option in (cost_main, cost_sub):
                    if option and ctype == _int(option.get("type")) and (ctype != _RES_ITEM or cid == _int(option.get("id"))):
                        unit = max(1, _int(option.get("count"), 1))
                        draws = count // unit
                        times += draws
                        if ctype == _RES_ITEM:
                            item_costs[cid] = item_costs.get(cid, 0) + draws * unit
                        else:
                            ledger.append({"type": ctype, "id": cid, "param": 0, "count": draws * unit})
                        break
            times = max(0, min(GACHA_MAX_COUNT_ONCE, times))
            if times <= 0:
                return 409, {"error": "insufficient_resource"}
        else:
            times = max(1, min(GACHA_MAX_COUNT_ONCE, requested))
            unit = max(1, _int(cost_main.get("count"), 1))
            if _int(cost_main.get("type")) == _RES_ITEM:
                ticket = _int(cost_main.get("id"))
                tickets = min(times, self._items_owned(state, ticket) // unit)
                if tickets:
                    item_costs[ticket] = tickets * unit
                rest = times - tickets
            elif cost_main:
                ledger.append({"type": _int(cost_main.get("type")), "id": _int(cost_main.get("id")), "param": 0, "count": times * unit})
                rest = 0
            else:
                rest = times
            if rest:
                if not cost_sub:
                    return 409, {"error": "insufficient_item", "itemId": _int(cost_main.get("id"))}
                ledger.append({"type": _int(cost_sub.get("type")), "id": _int(cost_sub.get("id")), "param": 0,
                               "count": rest * max(1, _int(cost_sub.get("count"), 1))})
        if not first_ten and int(pool.get("today", 0)) + times > _int(config.get("maxCountPerDay"), 9999):
            return 409, {"error": "gacha_daily_limit"}
        for item_id, count in item_costs.items():
            owned = self._items_owned(state, item_id)
            if owned < count:
                return 409, {"error": "insufficient_item", "itemId": item_id, "required": count, "owned": owned}
        if ledger:
            spend: list[dict[str, int]] = []
            for pay in ledger:
                if pay["type"] == _RES_PLAYER_MONEY:
                    spend.extend(self._money_changes(profile, pay["count"]))
                else:
                    spend.append(dict(pay, count=-pay["count"]))
            ok, paid, error = self._change_resources(profile, spend)
            if not ok:
                return 409, {"error": error or "insufficient_resource"}
            changes.extend(self._dynamic(paid))
        changes.extend(self._items_take(state, item_costs))

        rng = getattr(self, "_rng", None) or random.Random(
            (_int(profile.get("account_id")) * 1_000_003) ^ (int(pool.get("total", 0)) * 7919) ^ pool_id)
        owned = {int(record["id"]) for record in actor_store.ordered(profile, state)}
        drawn: list[int] = []
        if first_ten:
            # Leafa is guaranteed; the rest are distinct actors not yet owned.
            drawn.append(LEAFA_ACTOR_ID)
            candidates = [(aid, w) for aid, w in GACHA_POOL if aid not in owned and aid != LEAFA_ACTOR_ID]
            while len(drawn) < times and candidates:
                pick = rng.choices([aid for aid, _ in candidates], weights=[w for _, w in candidates])[0]
                drawn.append(pick)
                candidates = [(aid, w) for aid, w in candidates if aid != pick]
        # Veteran accounts may already own the whole pool before their first
        # ten-pull.  Always deliver every paid draw, including duplicate uids.
        if len(drawn) < times:
            drawn.extend(rng.choices([aid for aid, _ in GACHA_POOL], weights=[w for _, w in GACHA_POOL], k=times - len(drawn)))

        # Every draw is a separate character instance (copies are allowed;
        # the client keys characters by uid and offers decomposition).
        limited = (GACHA_LIMITED if pool_id == 2 else GACHA_NORMAL_UP).get(activity_id, {}) if pool_id in (1, 2) else {}
        featured = (limited.get("dropResInfo") or [{}])[0]
        featured_id = _int(featured.get("dropRes", {}).get("id")) or _int(featured.get("dropActorId"))
        featured_limit = _int(limited.get("dropActorCount"))
        counter["preDropActivityActor"] = _int(counter.get("curDropActivityActor"))
        new_instances = []
        for actor_id in drawn:
            way = 6 if pool_id == 6 else 5
            # Some original actors omit way 5/6 even though the exported
            # local actor pool includes them.  Apply the same pool's original
            # quality weights from actor 1001 rather than silently stay green.
            selected = {"id": actor_id if (way, actor_id) in ACTOR_STAR_RANDOM else 1001, "quality": 1}
            self._apply_star_random(selected, way, rng)
            selected["id"] = actor_id
            purple_limit = _int(config.get("actorDropCount"))
            orange_limit = _int(config.get("notGetOrangeMax"))
            if purple_limit and _int(counter.get("purpleMisses")) + 1 >= purple_limit and selected["quality"] < 3:
                self._apply_star_random(selected, _int(config.get("purpleActorParam"), 101), rng)
            if orange_limit and _int(counter.get("orangeMisses")) + 1 >= orange_limit:
                self._apply_star_random(selected, _int(config.get("orangeActorParam"), 102), rng)
            if featured_id:
                progress = _int(counter.get("curDropActivityActor"))
                # The native UI switches from countdown to "already obtained"
                # after the first featured actor.  Its 91-pull floor is once
                # per activity, and later copies use the original probability.
                probability = (_int(config.get("limitOrangeProbability")) if selected["quality"] == 4 and pool_id == 2
                               else _int(featured.get("dropActorProbability")))
                force_featured = not _int(counter.get("getActivityActorCount")) and progress + 1 >= featured_limit
                if force_featured or rng.randrange(10000) < probability:
                    selected["id"] = featured_id
                    if selected["quality"] < 3:
                        self._apply_star_random(selected, _int(featured.get("dropRes", {}).get("param"), 101), rng)
                    counter["getActivityActorCount"] = _int(counter.get("getActivityActorCount")) + 1
                    counter["curDropActivityActor"] = featured_limit
                else:
                    counter["curDropActivityActor"] = min(featured_limit, progress + 1)
            quality = _int(selected["quality"], 1)
            # Resolve quality before new_actor seeds quality-many talents.
            new_instances.append(self.new_actor(profile, state, selected["id"], way=1000 + quality))
            counter["purpleMisses"] = 0 if quality >= 3 else _int(counter.get("purpleMisses")) + 1
            counter["orangeMisses"] = 0 if quality >= 4 else _int(counter.get("orangeMisses")) + 1
            if counter is not pool:
                counter["total"] = _int(counter.get("total")) + 1
        first_order = int(pool.get("total", 0)) + 1
        records = state.setdefault("gacha_records", [])
        for order, actor in enumerate(new_instances, start=first_order):
            records.append({"id": actor["id"], "quality": actor["quality"], "time": now,
                            "pool": pool_id, "activityId": activity_id, "order": order})
        pool["total"] = int(pool.get("total", 0)) + len(drawn)
        pool["today"] = int(pool.get("today", 0)) + len(drawn)
        if first_ten:
            pool["firstTenDone"] = True
        elif free:
            pool["nextFree"] = policy_for(self.game).next_boundary(now)
        # Original scalar, local applicability policy: normal/limited draws
        # earn points while the permanent point-shop activity is open.
        from gacha_score_shop import active_activities
        add_gacha_score = (_int(_MISC_ROW.get("gachaPointGenRation")) * len(drawn)
                           if pool_id in (1, 2) and active_activities(self.game, profile, 13) else 0)
        if add_gacha_score:
            ok, earned, error = self._change_resources(profile, [{"type": _RES_GACHA_POINT, "id": 0,
                                                                 "count": add_gacha_score, "param": 0}])
            if not ok:
                raise RuntimeError(error or "gacha point grant failed")
            changes.extend(self._dynamic(earned))
        self._save(profile, state)
        self._record_task(profile, "TaskType_GachaP0MTimes", len(drawn), param=pool_id)

        result = self._message("ApiGachaTrade.result")
        result.add_gacha_score = add_gacha_score
        result.info.id = pool_id
        result.info.time = now
        result.info.count = len(drawn)
        result.info.trade_id = int(pool["total"])
        for change in changes:
            entry = result.info.res_change.add()
            entry.base_res.type = _int(change.get("type"))
            entry.base_res.id = _int(change.get("id"))
            entry.base_res.param = _int(change.get("param"))
            entry.base_res.count = _int(change.get("count"))
            entry.base_res.count_delta = _int(change.get("countDelta"))
        if new_instances:
            for record in new_instances:
                entry = result.info.res_change.add().actor
                entry.CopyFrom(self._st_actor(record, profile, state))
                # DBH.UpdateActors adds a character only for countDelta > 0.
                entry.count_delta = 1
                entry.is_new = entry.is_new_card = True
        for order, actor in enumerate(new_instances, start=first_order):
            record = result.actor_record.add()
            record.id = actor["id"]
            record.quality = actor["quality"]
            record.time_order = order
        info = self._gacha_info_row(pool_id, pool)
        result.gacha_count_today = info["gachaCountToday"]
        result.next_free_gacha_time = info["nextFreeGachaTime"]
        activity = self._gacha_activity_row(activity_id, counter, config)
        result.next_get_purple_count = activity["nextGetPurpleCount"]
        result.activity_info.activity_id = activity_id
        result.activity_info.next_get_purple_count = activity["nextGetPurpleCount"]
        result.activity_info.cur_drop_activity_actor = activity["curDropActivityActor"]
        result.activity_info.pre_drop_activity_actor = activity["preDropActivityActor"]
        result.activity_info.get_activity_actor_count = activity["getActivityActorCount"]
        return 200, result

    def _mail(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        if not lower.startswith("/fci/mail"):
            return None
        mails = state.setdefault("mails", [])
        # Upgrade old local profiles created before mail attachments were
        # modelled.  Keep this migration deterministic and account-local.
        for value in mails:
            if isinstance(value, dict):
                value.setdefault("attachments", [])
                value.setdefault("attachmentsGot", False)
        if lower in {"/fci/mail", "/fci/mail/"} and method == "GET":
            result = self._message("ApiMail.get")
            result.get.clear_all = False
            for value in mails:
                mail = result.get.mails.add()
                mail.id = _int(value.get("id")); mail.title = str(value.get("title", "")); mail.content = str(value.get("content", "")); mail.sender_name = str(value.get("senderName", "Local Sandbox")); mail.create_time = self._now(); mail.start_time = self._now(); mail.end_time = self._now() + 86400 * 365; mail.read = bool(value.get("read")); mail.attachments_got = bool(value.get("attachmentsGot"))
                for attachment in value.get("attachments", []):
                    if isinstance(attachment, Mapping):
                        entry = mail.attachments.add()
                        entry.base_res.type = _int(attachment.get("type"))
                        entry.base_res.id = _int(attachment.get("id"))
                        entry.base_res.count = _int(attachment.get("count"))
                        entry.base_res.param = _int(attachment.get("param"))
            return 200, result
        request = _json(body)
        mail_id = _int(request.get("id", request.get("mailId", 1)), 1)
        if method == "GET":
            value = next((item for item in mails if _int(item.get("id")) == mail_id), None)
            result = self._message("ApiMail.result"); result.result.id = mail_id
            if isinstance(value, Mapping):
                result.result.title = str(value.get("title", "")); result.result.content = str(value.get("content", "")); result.result.sender_name = str(value.get("senderName", "Local Sandbox")); result.result.read = bool(value.get("read")); result.result.attachments_got = bool(value.get("attachmentsGot"))
                for attachment in value.get("attachments", []):
                    if isinstance(attachment, Mapping):
                        entry = result.result.attachments.add()
                        entry.base_res.type = _int(attachment.get("type"))
                        entry.base_res.id = _int(attachment.get("id"))
                        entry.base_res.count = _int(attachment.get("count"))
                        entry.base_res.param = _int(attachment.get("param"))
            return 200, result
        if method != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
        value = next((item for item in mails if _int(item.get("id")) == mail_id), None)
        if value is None:
            return 404, {"error": "mail_not_found", "mailId": mail_id}
        value.setdefault("attachments", [])
        value.setdefault("attachmentsGot", False)
        changes: list[dict[str, int]] = []
        if lower.endswith("/read"):
            value["read"] = True
        elif lower.endswith("/obtain") or lower.endswith("/collect"):
            # Claim each message exactly once.  Resource changes go through
            # the canonical ledger so balances and login bootstrap stay in
            # sync across restarts.
            if not value.get("attachmentsGot"):
                attachments = [item for item in value.get("attachments", []) if isinstance(item, Mapping)]
                if attachments:
                    ok, changes, error = self._change_resources(profile, attachments)
                    if not ok:
                        return 409, {"error": error or "resource_update_failed"}
                value["attachmentsGot"] = True
            value["read"] = True
        elif lower.endswith("/delete") or lower.endswith("/auto-delete"):
            # Deleting a mail is local metadata only; unclaimed attachments
            # remain unclaimed and cannot be obtained after deletion.
            mails[:] = [item for item in mails if _int(item.get("id")) != mail_id]
        else:
            value["read"] = True
        self._save(profile, state)
        if lower.endswith("/delete") or lower.endswith("/auto-delete"):
            result = self._message("ApiMailAutoDelete.result"); result.auto_delete = True; result.del_mails.append(mail_id); return 200, result
        result = self._message("ApiMail.result"); result.result.id = mail_id; result.result.read = bool(value.get("read")); result.result.attachments_got = bool(value.get("attachmentsGot"))
        for change in changes:
            entry = result.res_change.add()
            entry.base_res.type = _int(change.get("type"))
            entry.base_res.id = _int(change.get("id"))
            entry.base_res.count = _int(change.get("count"))
            entry.base_res.param = _int(change.get("param"))
            entry.base_res.count_delta = _int(change.get("countDelta"))
        return 200, result

    def _relation(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        if not (lower.startswith("/fci/relation") or lower.startswith("/fci/friend")):
            return None
        friends = state.setdefault("friends", [])
        if method == "GET":
            result = self._message("ApiRelationFriendList.get")
            for friend in friends:
                item = result.result.add(); item.player_id = str(friend.get("playerId")); item.name = str(friend.get("name", "Local Friend")); item.level = _int(friend.get("level"), 1); item.is_in_relation = True
            return 200, result
        request = _json(body); msg = self._request(body, "ApiRelationApplyFriend")
        target = str(request.get("targetId", request.get("target_id", getattr(msg, "target_id", "local-bot-001") if msg else "local-bot-001")))
        # The JSON social surface and the typed protobuf surface should show
        # the same deterministic bot identity.  This keeps an old client
        # build that still posts ``ApiRelationApplyFriend`` in sync with the
        # local friend/recommend panels.
        bot_name = "Local Friend"
        bot_level = 1
        try:
            from social import LocalBotPool

            bot = LocalBotPool(seed=20261028, count=24, clock=self._clock).get(target)
            if bot is not None:
                bot_name = str(bot.get("name", bot_name))
                bot_level = _int(bot.get("level"), bot_level)
        except Exception:  # pragma: no cover - isolated descriptor tests
            pass
        if not any(str(x.get("playerId")) == target for x in friends):
            friends.append({"playerId": target, "name": bot_name, "level": bot_level})
        self._save(profile, state)
        result = self._message("ApiRelationApplyFriend.result"); result.result.player_id = target; result.result.name = bot_name; result.result.level = bot_level; result.result.is_in_relation = True
        return 200, result

    def _pvp(self, method: str, route: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        lower = route.lower()
        if lower in {"/fci/asyncpvprecord", "/fci/asyncpvprecord/"}:
            if method != "GET": return 405, {"error": "method_not_allowed", "message": "GET required"}
            return 200, self._message("ApiAsyncPvpRecordInfo.result")
        if lower.startswith("/fci/asyncpvp") or lower.startswith("/fci/match_mirror"):
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            result = self._message("ApiAsyncPvpMatch.result"); result.index = 0; result.target_actor.name = "Local Training Opponent"; result.target_actor.level = 1; result.target_actor.player_id = "local-bot"; return 200, result
        return None

    def _guild_bot_members(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return a deterministic roster used by the local guild.

        The retired service is unavailable, so a guild needs a stable set of
        members for the member, vote and support panels to render.  Reuse the
        same fixed-seed bot pool as social/PVP when it is available; the
        fallback keeps this module usable in isolated unit tests.
        """
        try:
            from social import LocalBotPool

            bots = LocalBotPool(seed=20261028, count=24, clock=self._clock).all()[:5]
        except Exception:  # pragma: no cover - defensive import fallback
            bots = [
                {"playerId": "local-bot-001", "name": "御坂训练员1", "level": 12, "avatar": 1002, "score": 1000, "online": True, "actors": [1001, 1002, 1003]},
                {"playerId": "local-bot-002", "name": "黑雪姬2", "level": 18, "avatar": 1003, "score": 1040, "online": False, "actors": [1002, 1003, 1004]},
                {"playerId": "local-bot-003", "name": "桐人练习生3", "level": 24, "avatar": 1004, "score": 1120, "online": True, "actors": [1001, 1004, 1005]},
                {"playerId": "local-bot-004", "name": "亚丝娜助手4", "level": 30, "avatar": 1005, "score": 1190, "online": False, "actors": [1003, 1005, 1006]},
                {"playerId": "local-bot-005", "name": "司波机器人5", "level": 36, "avatar": 1006, "score": 1250, "online": True, "actors": [1001, 1005, 1007]},
            ]
        return [dict(item, actors=list(item.get("actors", []))) for item in bots]

    def _guild_member_rows(self, guild: Mapping[str, Any], profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Build a JSON-safe roster while preserving persisted contribution."""
        self_id = str(profile.get("player_id", "local"))
        self_name = str(profile.get("name", "Local Guest"))
        self_level = _int(profile.get("level"), 1)
        president = str(guild.get("president", self_id))
        raw_members = guild.get("members", [])
        by_id = {
            str(item.get("playerId")): dict(item)
            for item in raw_members
            if isinstance(item, Mapping) and item.get("playerId")
        }
        bots = {str(bot["playerId"]): bot for bot in self._guild_bot_members(profile)}
        if self_id not in by_id:
            by_id[self_id] = {"playerId": self_id, "name": self_name, "level": self_level, "avatar": _int(profile.get("head_photo")), "online": True, "contribution": 0}
        rows: list[dict[str, Any]] = []
        for player_id, item in by_id.items():
            bot = bots.get(player_id, {})
            row = {
                "playerId": player_id,
                "name": str(item.get("name", bot.get("name", "Local Member"))),
                "level": _int(item.get("level", bot.get("level", 1)), 1),
                "avatar": _int(item.get("avatar", bot.get("avatar", 0))),
                "online": bool(item.get("online", bot.get("online", False))),
                "contribution": _int(item.get("contribution", 0)),
                "role": "president" if player_id == president else str(item.get("role", "member")),
                "isBot": player_id.startswith("local-bot-"),
                "actors": list(item.get("actors", bot.get("actors", [])) or []),
            }
            rows.append(row)
        rows.sort(key=lambda row: (0 if row["role"] == "president" else 1, row["playerId"]))
        return rows

    def _guild_normalise(self, guild: dict[str, Any], profile: Mapping[str, Any]) -> dict[str, Any]:
        """Upgrade old one-member guild JSON to the current local schema."""
        self_id = str(profile.get("player_id", "local"))
        guild.setdefault("id", 1)
        guild.setdefault("name", "Local Sandbox Guild")
        guild.setdefault("level", 1)
        guild.setdefault("exp", 0)
        guild.setdefault("contribution", 0)
        guild.setdefault("skills", {})
        guild.setdefault("records", [])
        guild.setdefault("president", self_id)
        guild.setdefault("vicePresidents", [])
        guild.setdefault("pendingMembers", [])
        guild.setdefault("votes", {})
        guild.setdefault("support", {"requests": [], "offers": []})
        guild.setdefault("dungeon", {"stage": 1, "score": 0, "attempts": 0, "history": []})
        if not isinstance(guild.get("members"), list) or not guild["members"]:
            guild["members"] = [
                {"playerId": self_id, "name": str(profile.get("name", "Local Guest")), "level": _int(profile.get("level"), 1), "online": True, "contribution": 0}
            ]
            guild["members"].extend(
                {"playerId": str(bot["playerId"]), "name": str(bot["name"]), "level": _int(bot.get("level"), 1), "avatar": _int(bot.get("avatar")), "online": bool(bot.get("online")), "contribution": 0, "actors": list(bot.get("actors", []))}
                for bot in self._guild_bot_members(profile)
            )
        if not isinstance(guild.get("records"), list):
            guild["records"] = []
        if not isinstance(guild.get("votes"), dict):
            guild["votes"] = {}
        support = guild.get("support")
        if not isinstance(support, dict):
            guild["support"] = {"requests": [], "offers": []}
        else:
            support.setdefault("requests", []); support.setdefault("offers", [])
        dungeon = guild.get("dungeon")
        if not isinstance(dungeon, dict):
            guild["dungeon"] = {"stage": 1, "score": 0, "attempts": 0, "history": []}
        else:
            dungeon.setdefault("stage", 1); dungeon.setdefault("score", 0); dungeon.setdefault("attempts", 0); dungeon.setdefault("history", [])
        return guild

    @staticmethod
    def _guild_record(guild: dict[str, Any], record: Mapping[str, Any]) -> None:
        rows = guild.setdefault("records", [])
        if not isinstance(rows, list):
            rows = []
            guild["records"] = rows
        rows.append(dict(record))
        del rows[:-100]

    def _guild(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """Provide a persistent local guild with deterministic robot members.

        This is a local compatibility surface.  It gives the client a complete
        single-guild loop (roster, invitations, president vote, support,
        donations, skills, dungeon records and leave) without contacting the
        retired service or claiming that cross-player coordination is restored.
        """
        lower = route.lower()
        if not (lower.startswith("/fci/guild") or lower.startswith("/fci/guild-dungeon-record")):
            return None
        # The historical sandbox accepted a caller-supplied score as a win.
        # Native guild-dungeon combat is a separate, unimplemented mode; never
        # expose that fake settlement or emit progress-reward push 87 for it.
        if "/dungeon" in lower or lower.startswith("/fci/guild-dungeon"):
            return 404, {"error": "guild_dungeon_not_implemented"}
        guild = state.get("guild")
        if lower in {"/fci/guild/recommend", "/fci/guild/recommend/", "/fci/guild/search", "/fci/guild/search/"}:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            name = "Local Sandbox Guild"
            result = self._message("ApiGuildRecommend.result" if "recommend" in lower else "ApiGuildSearch.result")
            result.result.id = 1; result.result.name = name; result.result.level = 1; result.result.exp = 0; result.result.president = "local"; result.result.president_name = "Local Guest"; result.result.members_count = 6
            return 200, result
        if lower in {"/fci/guild/create", "/fci/guild/create/"}:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body); name = str(request.get("name", "Local Sandbox Guild"))[:32] or "Local Sandbox Guild"
            state["guild"] = self._guild_normalise({"id": 1, "name": name, "level": 1}, profile)
            self._save(profile, state)
            result = self._message("ApiGuild.result"); result.base_info.id = 1; result.base_info.name = name; result.base_info.level = 1; result.base_info.president = str(profile.get("player_id", "local")); result.base_info.president_name = str(profile.get("name", "Local Guest")); result.base_info.members_count = len(state["guild"]["members"])
            return 200, result
        if lower in {"/fci/guild/members", "/fci/guild/members/"}:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            guild = self._guild_normalise(guild or {}, profile)
            state["guild"] = guild
            self._save(profile, state)
            result = self._message("ApiGuildMembers.get"); result.get.player_id = str(profile.get("player_id", "local")); result.get.name = str(profile.get("name", "Local Guest")); result.get.level = _int(profile.get("level"), 1); result.get.type = 1; return 200, result
        if lower in {"/fci/guild/exit", "/fci/guild/exit/"}:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            state["guild"] = None; self._save(profile, state); return 200, {}
        # The retired guild service is unavailable, so these operations use a
        # single persisted local guild and the same resource ledger as the
        # rest of the sandbox.  They keep the guild screens navigable without
        # pretending to reproduce cross-player coordination.
        if lower in {"/fci/guild/join", "/fci/guild/join/"}:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            state["guild"] = self._guild_normalise({"id": 1, "name": "Local Sandbox Guild", "level": 1, "contribution": 0, "skills": {}, "records": []}, profile)
            self._save(profile, state)
            return 200, {"ok": True, "guildId": 1, "name": state["guild"]["name"], "members": len(state["guild"]["members"]), "sandbox": True}
        if not isinstance(guild, dict):
            guild = {"id": 1, "name": "Local Sandbox Guild", "level": 1, "contribution": 0, "skills": {}, "records": []}
            state["guild"] = guild
        guild = self._guild_normalise(guild, profile)

        # JSON roster and guild summary endpoints are intentionally explicit so
        # local tooling can inspect all deterministic members even though the
        # damaged protobuf descriptor exposes only one ``get`` member field.
        if lower in {"/fci/guild/summary", "/fci/guild/info", "/fci/guild/members/list", "/fci/guild/member-list", "/fci/guild/roster"}:
            if method != "GET": return 405, {"error": "method_not_allowed", "message": "GET required"}
            self._save(profile, state)
            rows = self._guild_member_rows(guild, profile)
            return 200, {"guild": {"id": _int(guild.get("id"), 1), "name": str(guild.get("name")), "level": _int(guild.get("level"), 1), "exp": _int(guild.get("exp")), "president": str(guild.get("president")), "members": len(rows)}, "members": rows, "records": list(guild.get("records", [])), "sandbox": True}

        if lower in {"/fci/guild/member/invite", "/fci/guild/invite"}:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body)
            target = str(request.get("playerId", request.get("targetId", ""))).strip()
            bots = {str(bot["playerId"]): bot for bot in self._guild_bot_members(profile)}
            if not target: target = next((key for key in bots if key not in {str(m.get("playerId")) for m in guild["members"]}), "")
            if target not in bots: return 404, {"error": "local_bot_not_found", "playerId": target}
            if any(str(item.get("playerId")) == target for item in guild["members"]):
                return 200, {"ok": True, "alreadyMember": True, "playerId": target, "sandbox": True}
            bot = bots[target]
            guild["pendingMembers"] = [item for item in guild.get("pendingMembers", []) if str(item.get("playerId")) != target]
            guild["members"].append({"playerId": target, "name": str(bot["name"]), "level": _int(bot.get("level"), 1), "avatar": _int(bot.get("avatar")), "online": bool(bot.get("online")), "contribution": 0, "actors": list(bot.get("actors", []))})
            self._guild_record(guild, {"type": "member_join", "playerId": target, "name": str(bot["name"]), "time": self._now(), "actor": str(profile.get("player_id", "local"))})
            self._save(profile, state)
            member = next((row for row in self._guild_member_rows(guild, profile) if row["playerId"] == target), None)
            return 200, {"ok": True, "accepted": True, "playerId": target, "member": member, "sandbox": True}

        if lower in {"/fci/guild/member/kick", "/fci/guild/kick", "/fci/guild/member/remove"}:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            target = str(_json(body).get("playerId", _json(body).get("targetId", ""))).strip()
            if not target: return 400, {"error": "player_id_required"}
            if target == str(guild.get("president")): return 409, {"error": "president_cannot_be_removed"}
            before = len(guild["members"]); guild["members"] = [item for item in guild["members"] if str(item.get("playerId")) != target]
            if len(guild["members"]) == before: return 404, {"error": "member_not_found", "playerId": target}
            self._guild_record(guild, {"type": "member_leave", "playerId": target, "time": self._now(), "actor": str(profile.get("player_id", "local"))})
            self._save(profile, state)
            return 200, {"ok": True, "removed": target, "members": len(guild["members"]), "sandbox": True}

        if lower in {"/fci/guild/vote", "/fci/guild/vote/status", "/fci/guild/president-vote"} and method == "GET":
            active = next((value for value in guild.get("votes", {}).values() if isinstance(value, Mapping) and value.get("status") in {"open", "passed"}), None)
            return 200, {"vote": dict(active) if active else None, "votes": list(guild.get("votes", {}).values()), "president": str(guild.get("president")), "sandbox": True}

        if lower in {"/fci/guild/vote/start", "/fci/guild/president-vote/start", "/fci/guild/transfer/start"}:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body); target = str(request.get("playerId", request.get("targetId", ""))).strip()
            member_ids = {str(item.get("playerId")) for item in guild["members"]}
            if not target: target = next((item for item in member_ids if item.startswith("local-bot-")), "")
            if target not in member_ids: return 404, {"error": "member_not_found", "playerId": target}
            vote_id = str(max([_int(key) for key in guild.get("votes", {}) if str(key).isdigit()] or [0]) + 1)
            vote = {"id": int(vote_id), "candidate": target, "startedBy": str(profile.get("player_id", "local")), "startedAt": self._now(), "endsAt": self._now() + 86400, "status": "open", "votes": {}}
            guild.setdefault("votes", {})[vote_id] = vote
            self._guild_record(guild, {"type": "vote_start", "voteId": int(vote_id), "candidate": target, "time": self._now()})
            self._save(profile, state)
            return 200, {"ok": True, "vote": vote, "sandbox": True}

        if lower in {"/fci/guild/vote/cast", "/fci/guild/president-vote/cast", "/fci/guild/vote/resolve"}:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body); vote_id = str(request.get("voteId", request.get("id", ""))).strip()
            vote = guild.get("votes", {}).get(vote_id)
            if not isinstance(vote, dict): return 404, {"error": "vote_not_found"}
            choice = bool(request.get("agree", request.get("yes", request.get("approve", True))))
            votes = vote.setdefault("votes", {}); votes[str(profile.get("player_id", "local"))] = choice
            # Local bots vote deterministically.  A majority makes the screen
            # useful immediately while preserving an auditable vote record.
            for member in guild["members"]:
                player_id = str(member.get("playerId", ""))
                if player_id.startswith("local-bot-"):
                    votes.setdefault(player_id, True)
            yes = sum(1 for value in votes.values() if bool(value)); total = len(guild["members"])
            vote["yes"] = yes; vote["no"] = len(votes) - yes; vote["status"] = "passed" if yes > total // 2 else "rejected" if (total - yes) > total // 2 else "open"
            if vote["status"] == "passed" and bool(request.get("transfer", False)):
                guild["president"] = str(vote["candidate"])
            self._guild_record(guild, {"type": "vote_cast", "voteId": _int(vote_id), "choice": choice, "status": vote["status"], "time": self._now()})
            self._save(profile, state)
            return 200, {"ok": True, "vote": vote, "president": str(guild.get("president")), "sandbox": True}

        if lower in {"/fci/guild/transfer", "/fci/guild/president-transfer"}:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body); target = str(request.get("playerId", request.get("targetId", ""))).strip()
            if not any(str(item.get("playerId")) == target for item in guild["members"]): return 404, {"error": "member_not_found"}
            vote = next((item for item in guild.get("votes", {}).values() if isinstance(item, Mapping) and item.get("candidate") == target and item.get("status") == "passed"), None)
            if vote is None: return 409, {"error": "president_vote_required"}
            guild["president"] = target
            self._guild_record(guild, {"type": "president_transfer", "target": target, "time": self._now()})
            self._save(profile, state)
            return 200, {"ok": True, "president": target, "sandbox": True}

        if lower in {
            "/fci/guild/support",
            "/fci/guild/support/list",
            "/fci/guild/support/requests",
            "/fci/guild/assist",
            "/fci/guild/assist/list",
            "/fci/guild/assist/requests",
        } and method == "GET":
            support = guild["support"]
            return 200, {"requests": list(support.get("requests", [])), "offers": list(support.get("offers", [])), "sandbox": True}

        if lower in {
            "/fci/guild/support/request",
            "/fci/guild/support/create",
            "/fci/guild/assist/request",
            "/fci/guild/assist/create",
        }:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body); actor_id = _int(request.get("actorId", request.get("id", 1001)), 1001); count = max(1, min(_int(request.get("count", 1), 1), 3))
            request_id = len(guild["support"].get("requests", [])) + 1
            item = {"id": request_id, "playerId": str(profile.get("player_id", "local")), "actorId": actor_id, "count": count, "status": "open", "createdAt": self._now()}
            guild["support"].setdefault("requests", []).append(item)
            # The first bot offers a deterministic support response.
            bot = self._guild_bot_members(profile)[0]
            offer = {"id": request_id, "requestId": request_id, "playerId": str(bot["playerId"]), "actorId": actor_id, "count": count, "status": "offered", "createdAt": self._now()}
            guild["support"].setdefault("offers", []).append(offer)
            self._guild_record(guild, {"type": "support_request", "requestId": request_id, "actorId": actor_id, "time": self._now()})
            self._save(profile, state)
            return 200, {"ok": True, "request": item, "offer": offer, "sandbox": True}

        if lower in {
            "/fci/guild/support/claim",
            "/fci/guild/support/obtain",
            "/fci/guild/assist/claim",
            "/fci/guild/assist/obtain",
        }:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body); request_id = _int(request.get("requestId", request.get("id", 0)))
            found = next((item for item in guild["support"].get("requests", []) if _int(item.get("id")) == request_id), None)
            if found is None: return 404, {"error": "support_request_not_found"}
            if found.get("status") == "claimed": return 200, {"ok": True, "alreadyClaimed": True, "request": found, "sandbox": True}
            found["status"] = "claimed"; found["claimedAt"] = self._now()
            for offer in guild["support"].get("offers", []):
                if _int(offer.get("requestId")) == request_id: offer["status"] = "claimed"
            self._guild_record(guild, {"type": "support_claim", "requestId": request_id, "time": self._now()})
            self._save(profile, state)
            return 200, {"ok": True, "request": found, "actorId": _int(found.get("actorId")), "sandbox": True}

        if lower in {"/fci/guild/dungeon", "/fci/guild/dungeon/enter", "/fci/guild/dungeon/settle", "/fci/guild-dungeon/", "/fci/guild-dungeon"}:
            dungeon = guild["dungeon"]
            if method == "GET":
                return 200, {"guildId": _int(guild.get("id"), 1), "stage": _int(dungeon.get("stage"), 1), "score": _int(dungeon.get("score")), "attempts": _int(dungeon.get("attempts")), "history": list(dungeon.get("history", [])), "sandbox": True}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            request = _json(body)
            request_key = str(request.get("requestId", request.get("request_id", request.get("idempotencyKey", ""))) or "").strip()[:128]
            if request_key:
                previous = next(
                    (item for item in guild["dungeon"].get("history", [])
                     if isinstance(item, Mapping) and str(item.get("requestId", "")) == request_key),
                    None,
                )
                if isinstance(previous, Mapping):
                    return 200, {"ok": True, "duplicate": True, "result": dict(previous), "sandbox": True}
            stage = max(1, min(_int(request.get("stage", dungeon.get("stage", 1)), 1), 100))
            score = max(0, min(_int(request.get("score", 100), 100), 100000))
            dungeon["stage"] = max(_int(dungeon.get("stage"), 1), stage)
            dungeon["score"] = _int(dungeon.get("score")) + score
            dungeon["attempts"] = _int(dungeon.get("attempts")) + 1
            result = {"stage": stage, "score": score, "totalScore": _int(dungeon.get("score")), "attempt": _int(dungeon.get("attempts")), "finished": True, "time": self._now()}
            if request_key:
                result["requestId"] = request_key
            dungeon.setdefault("history", []).append(result); del dungeon["history"][:-50]
            self._guild_record(guild, {"type": "dungeon_clear", "stage": stage, "score": score, "time": self._now()})
            self._save(profile, state)
            return 200, {"ok": True, "result": result, "sandbox": True}

        if lower in {"/fci/guild-dungeon-record", "/fci/guild-dungeon-record/"}:
            if method != "GET": return 405, {"error": "method_not_allowed", "message": "GET required"}
            return 200, {"dungeonRecords": list(guild["dungeon"].get("history", [])), "score": _int(guild["dungeon"].get("score")), "sandbox": True}
        if "donat" in lower:
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            amount = max(1, _int(_json(body).get("amount", _json(body).get("count", 100)), 100))
            amount = min(amount, 10000)
            ok, changes, error = self._change_resources(profile, [{"type": 7, "id": 0, "param": 0, "count": -amount}])
            if not ok: return 409, {"error": error or "insufficient_resource"}
            guild["contribution"] = _int(guild.get("contribution")) + amount
            guild["exp"] = _int(guild.get("exp")) + amount
            guild["records"].append({"type": "donate", "amount": amount, "time": self._now()}); del guild["records"][:-50]
            self._save(profile, state)
            return 200, {"ok": True, "contribution": guild["contribution"], "resChange": self._dynamic(changes), "sandbox": True}
        if "skill" in lower:
            skill_id = _int(_json(body).get("skillId", _json(body).get("id", 1)), 1)
            skills = guild["skills"]
            if method == "GET": return 200, {"skills": [{"id": _int(key), "level": _int(value, 1)} for key, value in skills.items()]}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            skills[str(skill_id)] = min(10, _int(skills.get(str(skill_id)), 0) + 1); self._save(profile, state)
            return 200, {"ok": True, "skillId": skill_id, "level": skills[str(skill_id)], "sandbox": True}
        if "rent" in lower or "lease" in lower:
            if method == "GET": return 200, {"actors": [{"id": 1001, "uid": 1001, "name": "Local Guild Support", "level": 1}]}
            if method != "POST": return 405, {"error": "method_not_allowed", "message": "POST required"}
            return 200, {"ok": True, "actorId": 1001, "sandbox": True}
        if "record" in lower or "social-group" in lower:
            if method != "GET": return 405, {"error": "method_not_allowed", "message": "GET required"}
            return 200, {"records": list(guild["records"]), "group": {"id": 1, "name": guild["name"], "members": len(guild.get("members", []))}}
        if "dungeon" in lower:
            if method not in {"GET", "POST"}: return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            return 200, {"guildId": 1, "stage": _int(guild["dungeon"].get("stage"), 1), "score": _int(guild["dungeon"].get("score")), "finished": method == "POST", "sandbox": True}
        return None

    def _name(self, method: str, route: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        """``GET fci/name/`` -> {notFirstRename}; ``PATCH fci/name {name}``.

        Window_Account_Rename: the first rename is free (tutorial 16 waits for
        ``not notFirstRename``), later ones use one rename card (item 80010)
        when owned, else RefreshPrice ChgName (10007: 200 迷子).  The body does
        not say how to pay; the callback reads ``response.name`` and runs
        DBH.ResChange on ``response.resChange``.
        """

        if route.lower() not in {"/fci/name", "/fci/name/"}:
            return None
        state = self._state(profile)
        if method == "GET":
            return 200, {"notFirstRename": bool(state.get("notFirstRename", False))}
        if method not in {"PATCH", "POST"}:
            return 405, {"error": "method_not_allowed", "message": "PATCH required"}
        request = _json(body)
        name = str(request.get("name", request.get("playerName", ""))).strip()
        if not name or any(char in name for char in '/?#"'):
            return 400, {"error": "invalid_name"}
        # Misc.maxNameLen is an observed client rule (7 for this build).
        # Reject overlong values instead of silently truncating them, which
        # could make the client and server disagree about the saved name.
        if len(name) > _MAX_NAME_LENGTH:
            return 400, {
                "error": "name_too_long",
                "maxNameLen": _MAX_NAME_LENGTH,
            }
        if name == str(profile.get("name") or ""):
            return 409, {"error": "name_unchanged"}
        with self._lock, closing(self.game._connect()) as connection, connection:
            duplicate = connection.execute(
                "SELECT 1 FROM game_profiles WHERE name = ? AND account_id <> ? LIMIT 1",
                (name, int(profile["account_id"])),
            ).fetchone()
        if duplicate is not None:
            return 409, {"error": "name_taken"}
        changes: list[dict[str, Any]] = []
        if state.get("notFirstRename"):
            if self._items_owned(state, _RENAME_CARD_ITEM) >= 1:
                changes = self._items_take(state, {_RENAME_CARD_ITEM: 1})
            else:
                price = next((row.get("cost") for row in REFRESH_PRICES.get(_RENAME_PRICE_TYPE, []) if isinstance(row.get("cost"), Mapping)), None)                     or {"type": _RES_PLAYER_MONEY, "count": 200}
                ptype, pcount = _int(price.get("type")), _int(price.get("count"))
                spend = self._money_changes(profile, pcount) if ptype == _RES_PLAYER_MONEY else                     [{"type": ptype, "id": _int(price.get("id")), "param": 0, "count": -pcount}]
                ok, deltas, error = self._change_resources(profile, spend)
                if not ok:
                    return 409, {"error": error or "insufficient_resource", "required": pcount}
                changes = self._dynamic(deltas)
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute("UPDATE game_profiles SET name = ?, updated_at = ? WHERE account_id = ?", (name, self._now(), int(profile["account_id"])))
        state["notFirstRename"] = True
        self._save(profile, state)
        return 200, {"name": name, "resChange": self._dynamic_entries(changes)}

    def _player_profile(self, method: str, route: str, body: Body,
                        profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """Persist the player-facing profile and collection controls.

        ``playeraction.proto`` is one of the recovered files whose syntax
        field is corrupted, so its response messages cannot be safely loaded
        into the descriptor pool.  These routes deliberately return JSON with
        the same camelCase field names used by the Restifizer JSON fallback.
        No remote identity, avatar CDN, or payment service is contacted.
        """
        lower = route.lower()
        # ``LocalProfileFeatures`` owns the recovered protobuf-backed
        # profile/collection/notepad routes.  Keep only the seams that module
        # cannot safely expose because their playeraction descriptors are
        # absent, avoiding a broad JSON handler from shadowing typed replies.
        profile_routes = (
            "/fci/present-target-actor", "/fci/baseresource/level",
            "/fci/signboard", "/fci/emoji", "/fci/emojis",
        )
        if not any(lower == item or lower.startswith(item + "/") for item in profile_routes):
            return None
        # ProfileFeatures owns signboard mutations and typed replies; the
        # entering/bootstrap layer leaves only its GET projection unhandled.
        if lower.startswith("/fci/signboard") and method != "GET":
            return None

        player = state.setdefault("player_profile", {})
        if not isinstance(player, dict):
            player = {}
            state["player_profile"] = player
        defaults = self._default_state(profile)["player_profile"]
        for key, value in defaults.items():
            if key not in player:
                player[key] = json.loads(json.dumps(value))
        payload = self._request_values(body)

        if lower in {"/fci/baseinfo", "/fci/baseinfo/"}:
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            background = dict(player.get("homeBackGround", {}))
            return 200, {"result": {
                "level": _int(profile.get("level"), 1),
                "name": str(profile.get("name") or "Local Guest"),
                "lastLoginTime": self._now(),
                "socialPicture": str(player.get("socialPicture", "")),
                "headPhoto": _int(player.get("headPhoto")) or 1,
                "fightMedal": _int(player.get("fightMedal")),
                "sex": _int(player.get("sex")),
                "homeBackGround": {"current": _int(background.get("current")), "isRandom": bool(background.get("isRandom"))},
                "friendApplyLevelLimit": _int(player.get("friendApplyLevelLimit")),
                "sandbox": True,
            }}

        if lower in {"/fci/socialpicture", "/fci/socialpicture/"}:
            if method == "GET":
                return 200, {"result": {"sex": _int(player.get("sex")), "socialPicture": str(player.get("socialPicture", ""))}}
            if method not in {"POST", "PATCH"}:
                return 405, {"error": "method_not_allowed", "message": "POST or PATCH required"}
            if "socialPicture" in payload or "social_picture" in payload:
                player["socialPicture"] = str(payload.get("socialPicture", payload.get("social_picture", "")))[:512]
            if "sex" in payload:
                player["sex"] = max(0, min(2, _int(payload.get("sex"))))
            self._save(profile, state)
            return 200, {"result": {"sex": _int(player.get("sex")), "socialPicture": str(player.get("socialPicture", ""))}}

        if lower in {"/fci/home-back-ground", "/fci/home-back-ground/"}:
            if method == "GET":
                return 200, {"result": dict(player["homeBackGround"])}
            if method not in {"POST", "PATCH"}:
                return 405, {"error": "method_not_allowed", "message": "POST or PATCH required"}
            current = self._first_int(payload, "homeBackGround", "home_back_ground", "current", default=_int(player["homeBackGround"].get("current")))
            is_random = bool(payload.get("isRandom", payload.get("is_random", player["homeBackGround"].get("isRandom", False))))
            player["homeBackGround"] = {"current": max(0, current), "isRandom": is_random}
            self._save(profile, state)
            return 200, {"result": dict(player["homeBackGround"])}

        if lower in {"/fci/present-target-actor", "/fci/present-target-actor/"}:
            if method == "GET":
                return 200, {"result": {"presentTargetActor": _int(player.get("presentTargetActor"))}}
            if method not in {"POST", "PATCH"}:
                return 405, {"error": "method_not_allowed", "message": "POST or PATCH required"}
            player["presentTargetActor"] = max(0, self._first_int(payload, "presentTargetActor", "present_target_actor", "actorId", default=0))
            self._save(profile, state)
            return 200, {"result": {"presentTargetActor": _int(player.get("presentTargetActor"))}}

        if lower in {"/fci/baseresource/level", "/fci/baseresource/level/"}:
            if method == "GET":
                return 200, {"result": _int(profile.get("level"), 1)}
            if method not in {"POST", "PATCH"}:
                return 405, {"error": "method_not_allowed", "message": "POST or PATCH required"}
            level = max(1, min(60, self._first_int(payload, "level", default=_int(profile.get("level"), 1))))
            with self._lock, closing(self.game._connect()) as connection, connection:
                connection.execute("UPDATE game_profiles SET level = ?, updated_at = ? WHERE account_id = ?", (level, self._now(), int(profile["account_id"])))
            return 200, {"result": level, "sandbox": True}

        if lower in {"/fci/signboard", "/fci/signboard/"}:
            if method == "GET":
                return 200, dict(profile.get("signboard") or {"signboard": 101, "unlockedSignboardId": [101], "checkSignboardTime": 0})
            if method not in {"POST", "PATCH"}:
                return 405, {"error": "method_not_allowed", "message": "POST or PATCH required"}
            current = self._first_int(payload, "signboard", "signboardId", "id", default=_int((profile.get("signboard") or {}).get("signboard"), 101))
            unlocked = payload.get("unlockedSignboardId", payload.get("unlocked_signboard_id", [current]))
            if not isinstance(unlocked, list):
                unlocked = [current]
            signboard = {"signboard": max(0, current), "unlockedSignboardId": sorted({_int(item) for item in unlocked if _int(item) >= 0}), "checkSignboardTime": self._now()}
            with self._lock, closing(self.game._connect()) as connection, connection:
                connection.execute("UPDATE game_profiles SET signboard_json = ?, updated_at = ? WHERE account_id = ?", (json.dumps(signboard, separators=(",", ":")), self._now(), int(profile["account_id"])))
            try:
                result = self._message("ApiSignboard.result")
                result.signboard = signboard["signboard"]
                result.check_signboard_time = signboard["checkSignboardTime"]
                result.unlocked_signboard_id.extend(signboard["unlockedSignboardId"])
                return 200, result
            except Exception:
                return 200, signboard

        if lower.startswith(("/fci/collection", "/fci/collections")):
            collections = player.setdefault("collections", {"actors": [], "equips": [], "novels": []})
            if method == "GET":
                if lower.startswith("/fci/collections/actor"):
                    try:
                        result = self._message("ApiCollectionsActor.result")
                        for value in collections.get("actors", []):
                            row = result.actors.add(); row.id = _int(value); row.add_time = self._now()
                        return 200, result
                    except Exception:
                        pass
                return 200, {"collections": collections, "sandbox": True}
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            category = str(payload.get("category", payload.get("type", "actors"))).lower()
            category = {"actor": "actors", "equip": "equips", "novel": "novels"}.get(category, category)
            if category not in collections:
                return 400, {"error": "invalid_collection_category"}
            item_id = self._first_int(payload, "id", "itemId", "actorId", "equipId", "novelId", default=0)
            if item_id <= 0:
                return 400, {"error": "invalid_collection_id"}
            values = {int(item) for item in collections[category] if str(item).isdigit()}
            values.add(item_id); collections[category] = sorted(values)
            self._save(profile, state)
            if category == "actors":
                try:
                    result = self._message("ApiCollectionsActor.result")
                    for value in collections[category]:
                        row = result.actors.add(); row.id = value; row.add_time = self._now()
                    return 200, result
                except Exception:
                    pass
            if category == "equips":
                try:
                    result = self._message("ApiCollectionsEquip.result")
                    for value in collections[category]:
                        result.equips.add().equip_id = value
                    return 200, result
                except Exception:
                    pass
            return 200, {"collections": collections, "added": item_id, "sandbox": True}

        if lower.startswith(("/fci/novel-cover", "/fci/novelcover")):
            if method == "GET":
                return 200, {"novelCovers": list(player.get("novelCovers", [])), "sandbox": True}
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            cover_id = self._first_int(payload, "id", "coverId", "novelId", default=0)
            if cover_id <= 0:
                return 400, {"error": "invalid_cover_id"}
            covers = {int(item) for item in player.get("novelCovers", []) if str(item).isdigit()}; covers.add(cover_id)
            player["novelCovers"] = sorted(covers); self._save(profile, state)
            try:
                result = self._message("ApiCollectionsNovel.result")
                for value in player["novelCovers"]:
                    row = result.novels.add(); row.novel_id = value; row.cur_cover = value; row.cur_reward_num = 0; row.can_get_reward = False
                return 200, result
            except Exception:
                return 200, {"novelCovers": player["novelCovers"], "added": cover_id, "sandbox": True}

        if lower.startswith(("/fci/role-notepad", "/fci/rolenotepad")):
            note_id = self._first_int(payload, "id", "actorId", "roleId", default=0)
            if method == "GET":
                return 200, {"roleNotePad": dict(player.get("roleNotePad", {})), "sandbox": True}
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            if note_id <= 0:
                return 400, {"error": "invalid_role_id"}
            player.setdefault("roleNotePad", {})[str(note_id)] = {"id": note_id, "updatedAt": self._now(), "text": str(payload.get("text", ""))[:2000]}
            self._save(profile, state)
            try:
                result = self._message("ApiRoleNotePad.result")
                info = result.role_notepad
                info.role_id = note_id
                info.favour = 0
                info.favour_level = 1
                info.unlock_time = self._now()
                info.present_state = 0
                info.read_tag = _int(payload.get("readTag", payload.get("read_tag", 0)))
                return 200, result
            except Exception:
                return 200, {"roleNotePad": dict(player["roleNotePad"]), "sandbox": True}

        if lower.startswith(("/fci/emoji", "/fci/emojis")):
            if method == "GET":
                return 200, {"emojis": list(player.get("emojis", [])), "sandbox": True}
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            emoji_id = self._first_int(payload, "id", "emojiId", default=0)
            if emoji_id <= 0:
                return 400, {"error": "invalid_emoji_id"}
            values = {int(item) for item in player.get("emojis", []) if str(item).isdigit()}; values.add(emoji_id)
            player["emojis"] = sorted(values); self._save(profile, state)
            return 200, {"emojis": player["emojis"], "added": emoji_id, "sandbox": True}

        return None

    def _task(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        lower = route.lower()
        if not lower.startswith("/fci/task"):
            return None
        # ``TaskFirstCheck`` posts this endpoint from the city update loop.
        # Treat it as an idempotent local telemetry update; it must not be
        # mistaken for the reward-claim endpoint below.
        if lower in {"/fci/task/target-done-add", "/fci/task/target-done-add/"}:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            request = _json(body)
            target_id = _int(request.get("targetId", request.get("target_id", 0)))
            done = state.setdefault("taskTargetDone", [])
            if not isinstance(done, list):
                done = []
                state["taskTargetDone"] = done
            if target_id and target_id not in done:
                done.append(target_id)
                self._save(profile, state)
            # Empty JSON is valid for Restifizer's untyped POST callback and
            # keeps the client from trying to parse a nonexistent result.
            return 200, {}

        self._normalise_tasks(state)
        tasks = state["tasks"]
        if lower in {"/fci/task", "/fci/task/"} and method == "GET":
            rows = []
            for key, value in tasks.items():
                # Values are normalized above, but keep this projection
                # explicit so malformed historical state cannot leak through
                # to the wire response.
                task = self._normalise_task(key, value, self._now())
                rows.append(task)
            return 200, {
                "tasks": rows,
                "delTasks": [],
                "disableCategory": [],
                "activePoint": 0,
                "activePointRewardGot": [],
                "taskTargetDone": list(state.get("taskTargetDone", [])),
                "clearAll": True,
            }
        parts = [part for part in lower.split("/") if part]
        if len(parts) == 4 and parts[:3] == ["fci", "task", "extra-activepoint"] and parts[3].isdigit():
            if method != "GET":
                return 405, {"error": "method_not_allowed", "message": "GET required"}
            rows = [row for row in _client_table_rows("TaskExtraActivePointReward")
                    if _int(row.get("activePointType")) == _int(parts[3])]
            if not rows:
                return 404, {"error": "unknown_active_point_type"}
            return 200, {"extraActivePoint": 0, "progress": [
                {"targetActivePoint": _int(row.get("activePoint")), "rewardGot": False,
                 "reward": list(row.get("reward") or [])}
                for row in sorted(rows, key=lambda row: _int(row.get("activePoint")))]}
        # Concrete task claims are handled by LocalTasks. Unknown paths must
        # never synthesize a task or a reward.
        return 404, {"error": "unknown_task_route"}

    # xlsx/Misc.maxActorGroup: number of dungeon team presets.  The client
    # treats the number of groups returned as the number of unlocked tabs.
    _ACTOR_GROUP_COUNT = 5
    # Common_GroupUtil: member index 0-2 are main slots, 3-5 assist slots.
    _ACTOR_GROUP_SLOTS = 6

    @classmethod
    def _default_actor_groups(cls) -> dict[str, Any]:
        groups: dict[str, Any] = {}
        for group_id in range(1, cls._ACTOR_GROUP_COUNT + 1):
            members = [{"actorUid": 0, "index": index} for index in range(cls._ACTOR_GROUP_SLOTS)]
            # The first owned actor has uid 1 in the login bootstrap.
            members[0]["actorUid"] = 1
            groups[str(group_id)] = {"members": members}
        return groups

    @classmethod
    def _normalise_actor_groups(cls, raw: Any) -> dict[str, Any]:
        groups: dict[str, Any] = {}
        if not isinstance(raw, Mapping):
            return groups
        for key, group in raw.items():
            group_id = _int(key)
            if not 1 <= group_id <= cls._ACTOR_GROUP_COUNT or not isinstance(group, Mapping):
                continue
            slots = {index: 0 for index in range(cls._ACTOR_GROUP_SLOTS)}
            members = group.get("members", [])
            if isinstance(members, Mapping):
                members = list(members.values())
            for member in members if isinstance(members, list) else []:
                if not isinstance(member, Mapping):
                    continue
                index = _int(member.get("index"), -1)
                if 0 <= index < cls._ACTOR_GROUP_SLOTS:
                    slots[index] = max(0, _int(member.get("actorUid")))
            groups[str(group_id)] = {
                "members": [{"actorUid": uid, "index": index} for index, uid in sorted(slots.items())]
            }
        return groups

    def _actor_group(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """Persist dungeon team presets and the selected preset per mode.

        Window_ActorGroup binds ``fci/SelectedGroupId/<mode>`` first and only
        then loads ``fci/ActorGroup/`` (an object keyed by group id), reading
        ``groups[tostring(selected)].members``; ActorSelect saves with
        ``PUT fci/ActorGroup/ {actorGroups}`` and ``PATCH fci/SelectedGroupId``.
        """

        lower = route.lower().rstrip("/")
        if lower == "/fci/actorgroup":
            groups = self._normalise_actor_groups(state.get("actorGroups")) or self._default_actor_groups()
            if method in {"PUT", "POST", "PATCH"}:
                payload = self._request_values(body)
                raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
                if not payload and raw and not raw.lstrip().startswith((b"{", b"[")):
                    from google.protobuf import json_format
                    request = self._request(raw, "ApiActorGroup")
                    if request is None:
                        return 400, {"error": "invalid_actor_group_request"}
                    payload = json_format.MessageToDict(request)
                incoming = self._normalise_actor_groups(payload.get("actorGroups", payload))
                groups.update(incoming)
                state["actorGroups"] = groups
                self._save(profile, state)
            elif method != "GET":
                return 405, {"error": "method_not_allowed"}
            return 200, groups
        if lower.startswith("/fci/selectedgroupid"):
            mode = lower[len("/fci/selectedgroupid"):].strip("/") or "default"
            selected = state.setdefault("selectedGroupIds", {})
            if method in {"PUT", "POST", "PATCH"}:
                value = _int(self._request_values(body).get("selectActorGroupId"), 1)
                selected[mode] = value if 1 <= value <= self._ACTOR_GROUP_COUNT else 1
                self._save(profile, state)
            elif method != "GET":
                return 405, {"error": "method_not_allowed"}
            return 200, {"selectActorGroupId": _int(selected.get(mode), 1) or 1}
        return None

    # -- character / bag / profile cultivation --------------------------------
    #
    # Client requests (decompiled Lua, client 2.0.4) and what each callback reads:
    #   Window_ActorDecompose      POST fci/actor/decompose/ {uids}
    #       -> DBH.ResChange(result.resChange): actor countDelta -1, chips/EXP items
    #   Widget_RoleIntimateUp      PATCH fci/FavourGift/<roleId> {usedItemList:[{type,id,count}]}
    #       -> DBH.ResChange(result.resChange); DBH.RoleNotepadChange(result.roleNotepad)
    #   Widget_ActorTalents        GET fci/talent/reset/<actorUid> -> {talentPos, talentResetInfo}
    #   Window_ActorTalentReset    POST fci/talent/reset/ {actorUid, talentPos}
    #       -> {talentPos, talentResetInfo (session rolls, newest last), resChange}
    #   Window_ActorTalentResetResult POST fci/talent/select/ {actorUid, select, selectTalentPos?}
    #       -> DBH.UpdateActors(result.actor); ActorQualityUp if result.upgradeTalentInfo
    #   Window_ItemDetail / AutoSell  POST fci/itemsell[/] ProtobufT ApiItemSell {itemsell:[{id,count}]}
    #   Window_ItemDetail / ManualBox*  POST fci/itemuse/ {id, count, index}
    #   Window_StarCompose         POST fci/resourcecompose/ {type, id, count}
    #   Window_Account_Rename      PATCH fci/name {name} -> response.name, response.resChange

    def _cultivation(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        parts = [part for part in route.lower().split("/") if part]
        if parts[:1] != ["fci"] or len(parts) < 2:
            return None
        ordinary_inherit = (len(parts) == 4 and parts[1] == "actor" and parts[2].isdigit()
                            and parts[3] in {"inheriting", "actorinheriting", "actor-inheriting"})
        diff_inherit = parts[1:] == ["diffactorinherit"]
        if ordinary_inherit or diff_inherit:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._actor_inherit(body, profile, _int(parts[2]) if ordinary_inherit else None)
        if parts[1:] == ["actor", "decompose"]:
            if method != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            return self._actor_decompose(body, profile, state)
        if parts[1] == "favourgift" and len(parts) == 3 and parts[2].isdigit():
            if method not in {"PATCH", "POST"}:
                return 405, {"error": "method_not_allowed", "message": "PATCH required"}
            return self._favour_gift(_int(parts[2]), body, profile, state)
        if parts[1] == "talent" and len(parts) >= 3:
            if parts[2] == "reset" and len(parts) == 4 and parts[3].isdigit():
                if method != "GET":
                    return 405, {"error": "method_not_allowed", "message": "GET required"}
                return self._talent_session(_int(parts[3]), profile, state)
            if parts[2:] == ["reset"]:
                if method != "POST":
                    return 405, {"error": "method_not_allowed", "message": "POST required"}
                return self._talent_reset(body, profile, state)
            if parts[2:] == ["select"]:
                if method != "POST":
                    return 405, {"error": "method_not_allowed", "message": "POST required"}
                return self._talent_select(body, profile, state)
        return None

    def _actor_consumption_error(self, profile: Mapping[str, Any], state: dict[str, Any],
                                 record: Mapping[str, Any]) -> Optional[str]:
        uid, actor_id = _int(record.get("uid")), _int(record.get("id"))
        if record.get("locked", record.get("isLock", False)):
            return "actor_locked"
        groups = self._normalise_actor_groups(state.get("actorGroups")) or self._default_actor_groups()
        for group in groups.values():
            if any(_int(member.get("actorUid")) == uid for member in group.get("members", [])):
                return "actor_in_use"
        features = getattr(self.game, "profile_features", None)
        if features is not None and _int(features._state(profile).get("auraActorUid")) == uid:
            return "actor_in_use"
        modes = getattr(self.game, "activity_modes", None)
        if modes is not None:
            with closing(self.game._connect()) as connection:
                runs = connection.execute(
                    "SELECT actor_uids_json FROM activity_mode_runs WHERE account_id = ? AND status = 'active'",
                    (int(profile["account_id"]),)).fetchall()
            if any(uid in [_int(value) for value in json.loads(row["actor_uids_json"])] for row in runs):
                return "actor_in_use"
            role = _int(ACTOR_CONFIG.get(actor_id, {}).get("role"))
            missions = modes._state(profile).get("deploy", {}).get("missions", [])
            if any(_int(m.get("startTimestamp")) and role in [_int(v) for v in m.get("roleid", [])]
                   for m in missions):
                return "actor_in_use"
        if sum(_int(actor.get("id")) == actor_id for actor in actor_store.ordered(profile, state)) == 1:
            if any(info.get("joined") and _int(info.get("actorId")) == actor_id
                   for info in (state.get("cultivation") or {}).values() if isinstance(info, Mapping)):
                return "actor_in_use"
        return None

    def _diff_inherit_allowed(self, source: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
        schedule = getattr(self.game, "activity_schedule", None)
        if not DIFF_ACTOR_INHERIT or schedule is None:
            return False
        active = {_int(row.get("activityId")) for row in schedule.snapshot(self._now()).get("active", [])}
        return any(_int(row.get("actorId")) == _int(source.get("id"))
                   and _int(target.get("id")) in [_int(value) for value in row.get("targetId", [])]
                   # The client whitelist row (activity 33001) still has to be
                   # enabled by the server schedule.  Keeping the activity id
                   # in the exported table is not itself an activation flag.
                   and _int(row.get("activityId")) in active
                   for row in DIFF_ACTOR_INHERIT)

    @staticmethod
    def _inherit_talents(left: Mapping[str, Any], right: Mapping[str, Any], use_new: bool,
                         survivor_id: int) -> list[dict[str, int]]:
        chosen, other = (right, left) if use_new else (left, right)
        talents = actor_talents(chosen)
        if _int(chosen.get("quality")) < _int(other.get("quality")):
            wanted = len(actor_talents(other))
            for talent in actor_talents(other):
                if len(talents) >= wanted:
                    break
                info = ACTOR_TALENTS.get(talent["talentId"], {})
                groups = {ACTOR_TALENTS.get(t["talentId"], {}).get("groupType", t["talentId"]) for t in talents}
                has_special = any(ACTOR_TALENTS.get(t["talentId"], {}).get("spectial") for t in talents)
                if info.get("groupType", talent["talentId"]) not in groups and not (has_special and info.get("spectial")):
                    talents.append(talent)
        exclusive = ACTOR_CONFIG.get(survivor_id, {}).get("exclusiveTalentId", [])
        for talent in talents:
            info = ACTOR_TALENTS.get(talent["talentId"], {})
            if info.get("spectial") and talent["talentId"] not in exclusive and exclusive:
                talent["talentId"] = _int(exclusive[0])
        return talents

    @staticmethod
    def _inherit_skills(left: Mapping[str, Any], right: Mapping[str, Any], survivor_id: int) -> dict[str, Any]:
        slots: dict[int, int] = {}
        rows = _client_table_rows("ActorSkill")
        for actor in (left, right):
            for row in rows:
                slot = _int(row.get("displayIndex"))
                if _int(row.get("actorId")) != _int(actor.get("id")) or not slot:
                    continue
                value = (actor.get("skills") or {}).get(str(_int(row.get("skillId"))), 1)
                level = _int(value.get("level"), 1) if isinstance(value, Mapping) else _int(value, 1)
                slots[slot] = max(slots.get(slot, 1), level)
        return {str(_int(row.get("skillId"))): slots[_int(row.get("displayIndex"))]
                for row in rows if _int(row.get("actorId")) == survivor_id and _int(row.get("displayIndex")) in slots}

    def _actor_inherit(self, body: Body, profile: Mapping[str, Any], source_uid: Optional[int]) -> Response:
        is_diff = source_uid is None
        request_name = "ApiDiffActorInherit" if is_diff else "ApiActorInheriting"
        payload = self._request_values(body)
        request = self._request(body, request_name)
        if request is not None:
            target_uid = _int(request.target_actor_uid)
            use_new = bool(request.use_new_talent)
            if is_diff:
                source_uid = _int(request.actor_uid)
        else:
            target_uid = self._first_int(payload, "targetActorUid", "target_actor_uid", default=0)
            use_new = payload.get("useNewTalent", payload.get("use_new_talent", False))
            if not isinstance(use_new, bool):
                return 400, {"error": "invalid_use_new_talent"}
            if is_diff:
                source_uid = self._first_int(payload, "actorUid", "actor_uid", default=0)
        if not source_uid or not target_uid:
            return 400, {"error": "actor_uid_required"}
        if source_uid == target_uid:
            return 409, {"error": "actor_inherit_same_target"}
        with self.game.atomic_mutation():
            state = self._state(profile)
            source = actor_store.get(profile, state, source_uid)
            target = actor_store.get(profile, state, target_uid)
            if source is None or target is None:
                return 404, {"error": "actor_not_found", "uid": source_uid if source is None else target_uid}
            if is_diff:
                if not self._diff_inherit_allowed(source, target):
                    return 409, {"error": "actor_inherit_activity_unavailable"}
            elif _int(source.get("id")) != _int(target.get("id")):
                return 409, {"error": "actor_inherit_different_actor"}
            survivor, material = (target, source) if is_diff else (source, target)
            error = self._actor_consumption_error(profile, state, material)
            if error:
                return 409, {"error": error, "uid": _int(material.get("uid"))}
            survivor_id, survivor_uid = _int(survivor.get("id")), _int(survivor.get("uid"))
            removed_uid = _int(material.get("uid"))
            quality = min(_int(source.get("quality")), _int(target.get("quality")))
            rows = ACTOR_DECOMPOSE.get((_int(material.get("id")), quality))
            if rows is None:
                return 409, {"error": "actor_decompose_config_missing"}
            score = min(talent_score(actor_talents(source)), talent_score(actor_talents(target)))
            rewards = [dict(row, count=int((_int(row.get("count")) + score * ACTOR_DECOMPOSE_TALENT_COEF) // 1)
                            if row.get("isChips") else _int(row.get("count"))) for row in rows]
            talents = self._inherit_talents(source, target, use_new, survivor_id)
            skills = self._inherit_skills(source, target, survivor_id)
            worn_left = equip_store.worn_equips(profile, state, source_uid)
            worn_right = equip_store.worn_equips(profile, state, target_uid)
            score_left = sum(int(equip_store.equip_score(row)) for row in worn_left.values())
            score_right = sum(int(equip_store.equip_score(row)) for row in worn_right.values())
            kept = {_int(row.get("uid")) for row in (worn_left if score_left >= score_right else worn_right).values()}
            equipment_changes = {}
            for equip in (state.get("equips") or {}).values():
                if equip_store.owner_uid(profile, state, equip.get("actorUid")) in {source_uid, target_uid}:
                    uid = _int(equip.get("uid"))
                    equip["actorUid"] = survivor_uid if uid in kept else 0
                    equipment_changes[uid] = equip["actorUid"]
            best_exp = max(((_int(actor.get("level"), 1), _int(actor.get("exp"))) for actor in (source, target)))
            for key in ("level", "star", "quality", "cultivationTotal"):
                survivor[key] = max(_int(source.get(key)), _int(target.get(key)))
            survivor["exp"] = best_exp[1]
            survivor["talents"], survivor["skills"] = talents, skills
            survivor.pop("talentResetInfo", None)
            survivor.pop("talentResetPos", None)
            actor_store.remove(profile, state, removed_uid)
            result = self._message(f"{request_name}.post")
            error = self._grant_contents(profile, state, rewards, result)
            if error:
                raise RuntimeError(f"inheritance reward failed: {error}")
            result.res_change.add().actor.CopyFrom(self._st_actor(survivor, profile, state))
            deleted = result.res_change.add().actor
            deleted.uid, deleted.id, deleted.count_delta = removed_uid, _int(material.get("id")), -1
            result.equip_change.update(equipment_changes)
            self._save(profile, state)
            return 200, result

    def _rng_for(self) -> Any:
        return getattr(self, "_rng", None) or random

    def _items_take(self, state: dict[str, Any], counts: Mapping[int, int]) -> list[dict[str, int]]:
        """Remove already-validated bag items; return absolute baseRes deltas."""

        items = state.setdefault("items", {})
        changes = []
        for item_id, count in counts.items():
            if count <= 0:
                continue
            entry = items[str(item_id)]
            entry["count"] = _int(entry.get("count")) - count
            changes.append({"type": _RES_ITEM, "id": int(item_id), "param": 0, "count": entry["count"], "countDelta": -count})
        return changes

    def _items_owned(self, state: Mapping[str, Any], item_id: int) -> int:
        value = (state.get("items") or {}).get(str(_int(item_id)))
        return _int(value.get("count")) if isinstance(value, Mapping) else 0

    def _money_changes(self, profile: Mapping[str, Any], price: int) -> list[dict[str, int]]:
        """The economy ledger spends free currency first and adjusts paid balance."""
        return [{"type": _RES_PLAYER_MONEY, "id": 0, "param": 0, "count": -price}]

    def _dynamic_entries(self, changes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [{"baseRes": change} for change in self._dynamic(changes)]

    def _st_actor_json(self, record: Mapping[str, Any], profile: Mapping[str, Any], state: Mapping[str, Any],
                       count_delta: int) -> dict[str, Any]:
        from google.protobuf import json_format

        message = self._st_actor(record, profile, state)
        message.count_delta = int(count_delta)
        if count_delta > 0:
            message.is_new = message.is_new_card = True
        try:
            return json_format.MessageToDict(message, always_print_fields_with_no_presence=True)
        except TypeError:  # older protobuf
            return json_format.MessageToDict(message, including_default_value_fields=True)

    def _grant_contents(self, profile: Mapping[str, Any], state: dict[str, Any], rewards: list[Mapping[str, Any]],
                        message: Any) -> Optional[str]:
        """Grant STResource rows into ``message.res_change`` (typed).

        11 items -> bag, 15 characters -> new instances (ActorStarRandom way =
        param), 12 equipment -> new rows, 29 skins -> unlocked skins, the rest
        -> resource ledger.  Returns an error string when the ledger refuses.
        """

        items: dict[int, int] = {}
        ledger: list[dict[str, int]] = []
        new_actors: list[dict[str, Any]] = []
        new_equips: list[dict[str, Any]] = []
        skin_changes: list[dict[str, int]] = []
        rng = self._rng_for()
        for reward in rewards:
            rtype, rid, count = _int(reward.get("type")), _int(reward.get("id")), _int(reward.get("count"))
            if count <= 0:
                continue
            if rtype == _RES_ITEM:
                items[rid] = items.get(rid, 0) + count
            elif rtype == _RES_ACTOR:
                for _ in range(count):
                    record = self.new_actor(profile, state, rid, _int(reward.get("param")))
                    new_actors.append(record)
            elif rtype == _RES_EQUIP:
                for _ in range(count):
                    granted = equip_store.grant_equipment(self.game, profile, rid, state=state, rng=rng)
                    new_equips.append(state["equips"][str(granted["equip"]["uid"])])
            elif rtype == _RES_ACTOR_SKIN:
                owner = ACTOR_SKIN_OWNER.get(rid, 0)
                owned = state.setdefault("unlockedSkins", {}).setdefault(str(owner), [])
                if rid not in owned:
                    owned.append(rid); owned.sort()
                skin_changes.append({"type": rtype, "id": rid, "param": 0, "count": 1, "countDelta": 1})
            else:
                ledger.append({"type": rtype, "id": rid, "param": _int(reward.get("param")), "count": count})
        if ledger:
            ok, deltas, error = self._change_resources(profile, ledger)
            if not ok:
                return error or "resource_update_failed"
            skin_changes.extend(self._dynamic(deltas))
        for change in equip_store.add_items(state, items, self._now()) + skin_changes:
            entry = message.res_change.add().base_res
            entry.type = change["type"]; entry.id = change["id"]; entry.param = change["param"]
            entry.count = change["count"]; entry.count_delta = change["countDelta"]
        for record in new_equips:
            dynamic = message.res_change.add()
            equip_store.fill_st_equip(dynamic.equip, record)
            dynamic.equip.count_delta = 1
        for record in new_actors:
            dynamic = message.res_change.add()
            dynamic.actor.CopyFrom(self._st_actor(record, profile, state))
            dynamic.actor.count_delta = 1
            dynamic.actor.is_new = dynamic.actor.is_new_card = True
        return None

    def new_actor(self, profile: Mapping[str, Any], state: dict[str, Any], actor_id: int,
                  way: int = 0) -> dict[str, Any]:
        """Finalize quality before seeding the persisted, immediately visible talents."""
        record = actor_store.add(profile, state, actor_id)
        self._apply_star_random(record, way, self._rng_for())
        ensure_talents(record, self._rng_for())
        record["isNew"] = record["isNewCard"] = True
        return record

    @staticmethod
    def _apply_star_random(record: dict[str, Any], way: int, rng: Any) -> None:
        """ActorStarRandom[(way, actorId)]: roll the talent count (quality) and star."""

        row = ACTOR_STAR_RANDOM.get((_int(way), _int(record.get("id"))))
        if row is None:
            return

        def pick(entries: Any, key: str) -> int:
            weighted = [(_int(e.get(key)), _int(e.get("rate"))) for e in entries or [] if isinstance(e, Mapping) and _int(e.get("rate")) > 0]
            if not weighted:
                return 0
            roll = rng.randrange(sum(rate for _, rate in weighted))
            for value, rate in weighted:
                roll -= rate
                if roll < 0:
                    return value
            return weighted[-1][0]

        quality = pick(row.get("talentCountRandoms"), "count")
        star = pick(row.get("starRandoms"), "star")
        if quality:
            record["quality"] = max(1, min(4, quality))
        if star:
            record["star"] = max(1, star)

    # (a) 分解角色 ---------------------------------------------------------------
    def _actor_decompose(self, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """ActorDecompose[(id, quality)].decomposeRes (chips +
        floor(talentScore * Misc.actorDecomposeTalentCoef)) plus the character
        EXP (ActorLevelup sum, less expLostRate) as ActorExp books."""

        payload = self._request_values(body)
        raw = payload.get("uids")
        if not isinstance(raw, list):
            request = self._request(body, "ApiActorDecompose")
            raw = list(request.uids) if request is not None else []
        uids: list[int] = []
        for value in raw:
            if _int(value) > 0 and _int(value) not in uids:
                uids.append(_int(value))
        if not uids:
            return 400, {"error": "no_actor_selected"}
        records = []
        for uid in uids:
            record = actor_store.get(profile, state, uid)
            if record is None:
                return 404, {"error": "actor_not_found", "uid": uid}
            if record.get("locked", record.get("isLock", False)):
                return 409, {"error": "actor_locked", "uid": uid}
            records.append(record)
        if len(records) >= len(actor_store.instances(profile, state)):
            return 409, {"error": "last_actor_protected"}
        items: dict[int, int] = {}
        ledger: list[dict[str, int]] = []
        for record in records:
            actor_id = _int(record.get("id"))
            quality = _int(record.get("quality")) or actor_store.base_quality(actor_id)
            rows = ACTOR_DECOMPOSE.get((actor_id, quality))
            if rows is None:
                return 409, {"error": "actor_decompose_config_missing", "id": actor_id, "quality": quality}
            score = talent_score(actor_talents(record))
            for res in rows:
                count = int((_int(res.get("count")) + score * ACTOR_DECOMPOSE_TALENT_COEF) // 1) if res.get("isChips") else _int(res.get("count"))
                if count <= 0:
                    continue
                if _int(res.get("type")) == _RES_ITEM:
                    items[_int(res.get("id"))] = items.get(_int(res.get("id")), 0) + count
                else:
                    ledger.append({"type": _int(res.get("type")), "id": _int(res.get("id")), "param": 0, "count": count})
            level = max(1, _int(record.get("level"), 1))
            exp = float(max(0, _int(record.get("exp"))))
            for each in range(1, level + 1):
                exp += _int(ACTOR_LEVEL_ROWS.get(each, {}).get("exp"))
            exp *= 1 - _int(ACTOR_LEVEL_ROWS.get(level, {}).get("expLostRate")) / 10000
            for item_id, count in _greedy_exp_items(exp, ACTOR_EXP_ITEMS):
                items[item_id] = items.get(item_id, 0) + count
        result = self._message("ApiActorDecompose.post")
        if ledger:
            ok, deltas, error = self._change_resources(profile, ledger)
            if not ok:
                return 409, {"error": error or "resource_update_failed"}
        else:
            deltas = []
        removed = set(uids)
        for record in records:
            entry = result.res_change.add().actor
            entry.id = _int(record.get("id")); entry.uid = _int(record.get("uid")); entry.count_delta = -1
            actor_store.remove(profile, state, record.get("uid"))
        for equip in (state.get("equips") or {}).values():
            if isinstance(equip, dict) and _int(equip.get("actorUid")) in removed:
                equip["actorUid"] = 0  # DBH.UpdateActors frees bodyEquips the same way
        groups = state.get("actorGroups")
        if isinstance(groups, Mapping):
            for group in groups.values():
                for member in (group.get("members") or []) if isinstance(group, Mapping) else []:
                    if isinstance(member, dict) and _int(member.get("actorUid")) in removed:
                        member["actorUid"] = 0
        for change in equip_store.add_items(state, items, self._now()) + self._dynamic(deltas):
            entry = result.res_change.add().base_res
            entry.type = change["type"]; entry.id = change["id"]; entry.param = change["param"]
            entry.count = change["count"]; entry.count_delta = change["countDelta"]
        self._save(profile, state)
        return 200, result

    # (e) 好感度礼物 ---------------------------------------------------------------
    def _favour_gift(self, role_id: int, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """Favour = ItemInfo.param[1] x count (ItemFuncType.Favour, the items
        RoleFavourGiftList allows), capped per day by FavourIncreaseRule
        ruleType 1; levels, FavourLvUp events and signboard unlocks are
        profile_features.add_role_favour's.  Plain JSON {roleNotepad, resChange}."""

        payload = self._request_values(body)
        used = payload.get("usedItemList", payload.get("used_item_list"))
        if not isinstance(used, list):
            request = self._request(body, "ApiFavourGift")
            used = [{"id": entry.id, "count": entry.count} for entry in request.used_item_list] if request is not None else []
        wanted: dict[int, int] = {}
        for entry in used:
            if not isinstance(entry, Mapping):
                return 400, {"error": "invalid_used_item_list"}
            item_id, count = _int(entry.get("id")), _int(entry.get("count"))
            info = ITEM_INFO.get(item_id, {})
            allowed = FAVOUR_GIFT_ITEMS.get(role_id)
            if count <= 0 or _int(info.get("funcType")) != _ITEM_FUNC_FAVOUR or (allowed is not None and item_id not in allowed):
                return 400, {"error": "invalid_favour_item", "id": item_id}
            wanted[item_id] = wanted.get(item_id, 0) + count
        if not wanted:
            return 400, {"error": "invalid_used_item_list"}
        features = getattr(self.game, "profile_features", None)
        notepad = features.role_notepad(profile, role_id) if features is not None else None
        if notepad is None:
            return 404, {"error": "role_not_owned", "roleId": role_id}
        if _int(notepad.get("favourLevel")) >= MAX_ROLE_FAVOUR_LEVEL:
            return 409, {"error": "favour_level_max"}
        for item_id, count in wanted.items():
            owned = self._items_owned(state, item_id)
            if owned < count:
                return 409, {"error": "insufficient_item", "id": item_id, "required": count, "owned": owned}
        amount = sum(_int((ITEM_INFO[item_id].get("param") or [0])[0]) * count for item_id, count in wanted.items())
        day = policy_for(self.game).day_index(self._now())
        daily = state.get("favourGiftDaily") if isinstance(state.get("favourGiftDaily"), dict) else {}
        if period_due(daily.get("day"), day):
            daily = {"day": day, "roles": {}}
        gained = _int(daily["roles"].get(str(role_id)))
        if FAVOUR_GIFT_DAILY_LIMIT and gained + amount > FAVOUR_GIFT_DAILY_LIMIT:
            return 409, {"error": "favour_daily_limit", "remaining": max(0, FAVOUR_GIFT_DAILY_LIMIT - gained)}
        daily["roles"][str(role_id)] = gained + amount
        state["favourGiftDaily"] = daily
        changes = self._items_take(state, wanted)
        self._save(profile, state)
        notepad = features.add_role_favour(profile, role_id, amount) or notepad
        self._record_task(profile, "TaskType_RoleP0FavourLevelM", 0, param=role_id, value=_int(notepad.get("favourLevel")))
        return 200, {"resChange": self._dynamic_entries(changes), "roleNotepad": notepad}

    # (f) 天赋重置 ---------------------------------------------------------------
    def _talent_actor(self, profile: Mapping[str, Any], state: dict[str, Any], uid: int) -> Optional[dict[str, Any]]:
        record = actor_store.get(profile, state, uid)
        if record is not None and ensure_talents(record, self._rng_for()):
            self._save(profile, state)
        return record

    @staticmethod
    def _talent_session_json(record: Mapping[str, Any]) -> dict[str, Any]:
        rolls = [{"talentId": _int(t.get("talentId")), "talentValue": _int(t.get("talentValue"))}
                 for t in record.get("talentResetInfo") or [] if isinstance(t, Mapping)]
        return {"talentPos": _int(record.get("talentResetPos")) if rolls else 0, "talentResetInfo": rolls}

    def _talent_session(self, uid: int, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        record = self._talent_actor(profile, state, uid)
        if record is None:
            return 404, {"error": "actor_not_found", "uid": uid}
        return 200, self._talent_session_json(record)

    def _talent_reset(self, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """One reroll of talent ``talentPos`` (1-based), paid with
        TalentResetCost.cost (the actor's chip) or else costSub (generic chip);
        at most Misc.maxResetTalentCount rolls per session."""

        payload = self._request_values(body)
        uid, pos = _int(payload.get("actorUid")), _int(payload.get("talentPos"))
        record = self._talent_actor(profile, state, uid)
        if record is None:
            return 404, {"error": "actor_not_found", "uid": uid}
        talents = actor_talents(record)
        if not 1 <= pos <= len(talents):
            return 400, {"error": "invalid_talent_pos", "talentPos": pos}
        session = list(record.get("talentResetInfo") or [])
        if session and _int(record.get("talentResetPos")) != pos:
            return 409, {"error": "talent_reset_pending", "talentPos": _int(record.get("talentResetPos"))}
        if len(session) >= MAX_RESET_TALENT_COUNT:
            return 409, {"error": "talent_reset_limit", "max": MAX_RESET_TALENT_COUNT}
        cost_row = TALENT_RESET_COST.get(_int(record.get("id")), {})
        payment = None
        for option in (cost_row.get("cost"), cost_row.get("costSub") or {"type": _RES_ITEM, "id": _GENERIC_CHIP_ITEM, "count": 1}):
            if isinstance(option, Mapping) and _int(option.get("count")) > 0 and \
                    self._items_owned(state, _int(option.get("id"))) >= _int(option.get("count")):
                payment = {_int(option.get("id")): _int(option.get("count"))}
                break
        if payment is None:
            return 409, {"error": "insufficient_item", "message": "Talent_ResetCostNotEnough"}
        changes = self._items_take(state, payment)
        others = {t["talentId"] for index, t in enumerate(talents) if index != pos - 1}
        session.append(_roll_talent(_int(record.get("id")), self._rng_for(), others))
        record["talentResetPos"], record["talentResetInfo"] = pos, session
        record["talentResetTimes"] = _int(record.get("talentResetTimes")) + 1
        self._save(profile, state)
        # Category-102 task 60010 is the daily talent-reset objective.  Keep
        # the generic task table in sync with the cultivation-specific hook.
        self._record_task(profile, "TaskType_TalenResetMTimes")
        cultivation = getattr(self.game, "cultivation", None)
        if cultivation is not None:
            cultivation.record(profile, 78, param=_int(record.get("id")))
        reply = self._talent_session_json(record)
        reply["resChange"] = self._dynamic_entries(changes)
        return 200, reply

    def _talent_select(self, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """Keep the old talent or take roll ``selectTalentPos``; the session ends.

        Quality-up (local reading of ActorQualityInfo): chance
        talentResetUpgradeRate/10000 per finished session, guaranteed once
        talentResetUpgradeFloor rerolls were paid since the last upgrade; it
        adds one talent (quality = talent count) and is reported as
        ``upgradeTalentInfo`` (omitted otherwise: the client tests truthiness).
        """

        payload = self._request_values(body)
        uid = _int(payload.get("actorUid"))
        record = self._talent_actor(profile, state, uid)
        if record is None:
            return 404, {"error": "actor_not_found", "uid": uid}
        session = [t for t in record.get("talentResetInfo") or [] if isinstance(t, Mapping)]
        pos = _int(record.get("talentResetPos"))
        if not session or pos <= 0:
            return 409, {"error": "no_talent_reset_pending"}
        talents = actor_talents(record)
        if payload.get("select"):
            index = _int(payload.get("selectTalentPos"))
            if not 1 <= index <= len(session) or not 1 <= pos <= len(talents):
                return 400, {"error": "invalid_select_talent_pos", "selectTalentPos": index}
            chosen = session[index - 1]
            talents[pos - 1] = {"talentId": _int(chosen.get("talentId")), "talentValue": max(1, _int(chosen.get("talentValue"), 1))}
        record["talents"] = talents
        record["talentResetPos"], record["talentResetInfo"] = 0, []
        upgrade = None
        quality = _int(record.get("quality")) or actor_store.base_quality(_int(record.get("id")))
        row = ACTOR_QUALITY_INFO.get((_int(record.get("id")), quality), {})
        rate, floor = _int(row.get("talentResetUpgradeRate")), _int(row.get("talentResetUpgradeFloor"))
        if quality < 4 and (rate or floor):
            rng = self._rng_for()
            if (floor and _int(record.get("talentResetTimes")) >= floor) or (rate and rng.randrange(10000) < rate):
                upgrade = _roll_talent(_int(record.get("id")), rng, {t["talentId"] for t in talents}, value=1)
                talents.append(upgrade)
                record["talents"] = talents
                record["quality"] = quality + 1
                record["talentResetTimes"] = 0
        self._save(profile, state)
        reply: dict[str, Any] = {"actor": self._st_actor_json(record, profile, state, 0)}
        if upgrade is not None:
            reply["upgradeTalentInfo"] = upgrade
        return 200, reply

    # (c) 背包: sell / use / compose -------------------------------------------
    def _item_sell(self, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """ItemInfo.sellPrice is per unit; saleable items (and activity-bound
        ones, which ItemDetail offers once their activity closed)."""

        payload = self._request_values(body)
        entries = payload.get("itemsell", payload.get("itemSell"))
        if not isinstance(entries, list):
            if "id" in payload:
                entries = [{"id": payload.get("id"), "count": payload.get("count", 1)}]
            else:
                request = self._request(body, "ApiItemSell")
                entries = [{"id": e.id, "count": e.count} for e in request.itemsell] if request is not None else []
        wanted: dict[int, int] = {}
        for entry in entries:
            if not isinstance(entry, Mapping):
                return 400, {"error": "invalid_item_sell"}
            item_id, count = _int(entry.get("id")), _int(entry.get("count"))
            info = ITEM_INFO.get(item_id)
            if info is None:
                return 404, {"error": "item_not_found", "id": item_id}
            if count <= 0:
                return 400, {"error": "invalid_item_sell", "id": item_id}
            price = info.get("sellPrice")
            if not (info.get("saleable") or info.get("bindActivityType")) or not isinstance(price, Mapping) or _int(price.get("count")) <= 0:
                return 409, {"error": "item_not_for_sale", "id": item_id}
            wanted[item_id] = wanted.get(item_id, 0) + count
        if not wanted:
            return 400, {"error": "invalid_item_sell"}
        for item_id, count in wanted.items():
            owned = self._items_owned(state, item_id)
            if owned < count:
                return 409, {"error": "insufficient_item", "id": item_id, "required": count, "owned": owned}
        income: dict[tuple[int, int], int] = {}
        for item_id, count in wanted.items():
            price = ITEM_INFO[item_id]["sellPrice"]
            key = (_int(price.get("type")), _int(price.get("id")))
            income[key] = income.get(key, 0) + _int(price.get("count")) * count
        ok, deltas, error = self._change_resources(profile, [{"type": t, "id": i, "param": 0, "count": c} for (t, i), c in income.items()])
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        changes = self._items_take(state, wanted) + self._dynamic(deltas)
        self._save(profile, state)
        result = self._message("ApiItemSell.result")
        for change in changes:
            entry = result.res_change.add().base_res
            entry.type = change["type"]; entry.id = change["id"]; entry.param = change["param"]
            entry.count = change["count"]; entry.count_delta = change["countDelta"]
        return 200, result

    @staticmethod
    def _box_request_key(payload: Mapping[str, Any]) -> str:
        """Read an optional client retry key without making ordinary calls idempotent.

        The stock 2.0.4 client sends only ``id`` and ``count``.  It has no
        request-id field, so synthesising a key from those values would make
        two intentional consecutive openings look like a retry.  Newer local
        tooling may send any of these common spellings to get exactly-once
        behaviour across a timeout/reconnect.
        """

        for key in ("requestId", "request_id", "idempotencyKey", "idempotency_key"):
            value = payload.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()[:128]
        return ""

    @staticmethod
    def _box_rng(profile: Mapping[str, Any], item_id: int, sequence: int, request_key: str = "") -> random.Random:
        """Return a stable per-account/per-box stream for local rewards."""

        account_id = _int(profile.get("account_id"))
        seed_material = f"ljjc-random-box-v1:{account_id}:{item_id}:{sequence}:{request_key}".encode()
        seed = int.from_bytes(hashlib.blake2b(seed_material, digest_size=16).digest(), "big")
        return random.Random(seed)

    @staticmethod
    def _local_equipment_pool(selector: Any, quality: int) -> list[int]:
        """Resolve a documented local selector to concrete EquipInfo ids.

        The 2.0.4 APK contains the 75001 ``dropIds`` strings but no DropInfo
        rows.  Keeping selector resolution here makes the fallback explicit
        and auditable, while allowing the server table to be replaced later
        without changing the item-use route.
        """

        pool = [int(value) for value in equip_store.EQUIP_POOLS.get(quality, [])]
        token = str(selector or "quality_%d" % quality)
        if token == "quality_%d" % quality:
            return pool
        if token.startswith("family_") and token.endswith("xx"):
            prefix = token[len("family_"):-2]
            return [value for value in pool if str(value).startswith(prefix)]
        if token.startswith("pos_"):
            position = _int(token[4:], -1)
            if position < 0:
                return []
            return [
                value for value in pool
                if _int((equip_store.EQUIP_INFO.get(value) or {}).get("pos"), 0) == position
            ]
        return []

    def _random_box_rewards(self, profile: Mapping[str, Any], item_id: int, count: int,
                            sequence: int, request_key: str = "") -> list[dict[str, int]]:
        """Roll the D1 local pool once per box and return STResource rows."""

        info = ITEM_INFO.get(item_id, {})
        rule = RANDOM_BOX_RULES.get(item_id)
        if rule is None:
            # Future funcType=6 rows still get a safe local implementation:
            # quality-matched star material, or equipment for broadType=1.
            quality = max(1, _int(info.get("quality"), 1))
            if _int(info.get("broadType")) == 1:
                rule = {"mode": "equipment", "quality": quality, "weight": 10000}
            else:
                pool = [
                    _int(row.get("id")) for row in ITEM_INFO.values()
                    if _int(row.get("type")) == 1 and _int(row.get("funcType")) == 7
                    and _int(row.get("quality"), 0) == quality
                ]
                rule = {"mode": "item_pool", "pool": sorted(item for item in pool if item > 0), "weight": 10000}

        rng = self._box_rng(profile, item_id, sequence, request_key)
        rewards: list[dict[str, int]] = []
        mode = str(rule.get("mode", "fixed"))
        if mode == "fixed":
            fixed = [dict(reward) for reward in rule.get("rewards", []) if isinstance(reward, Mapping)]
            for _ in range(count):
                rewards.extend(fixed)
            return rewards
        if mode == "equipment":
            quality = _int(rule.get("quality"))
            drop_tables = [
                table for table in rule.get("dropTables", [])
                if isinstance(table, Mapping)
                and _int(table.get("weight"), 0) > 0
                and self._local_equipment_pool(table.get("pool"), quality)
            ]
            for _ in range(count):
                if drop_tables:
                    total_weight = sum(_int(table.get("weight"), 0) for table in drop_tables)
                    pick = rng.randrange(total_weight)
                    chosen = drop_tables[-1]
                    for table in drop_tables:
                        pick -= _int(table.get("weight"), 0)
                        if pick < 0:
                            chosen = table
                            break
                    pool = self._local_equipment_pool(chosen.get("pool"), quality)
                else:
                    pool = list(equip_store.EQUIP_POOLS.get(quality, []))
                if not pool:
                    return []
                rewards.append({"type": _RES_EQUIP, "id": int(rng.choice(pool)), "count": 1})
            return rewards
        if mode == "item_pool":
            pool = [_int(item) for item in rule.get("pool", []) if _int(item) in ITEM_INFO]
            if not pool:
                return []
            for _ in range(count):
                rewards.append({"type": _RES_ITEM, "id": int(rng.choice(pool)), "count": 1})
            return rewards
        return []

    def _box_reply_from_receipt(self, receipt: Mapping[str, Any]) -> Optional[Any]:
        encoded = str(receipt.get("responseHex", ""))
        if not encoded:
            return None
        try:
            result = self._message("ApiPlayerItemUse.result")
            result.ParseFromString(bytes.fromhex(encoded))
            return result
        except Exception:
            return None

    def _item_use(self, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """Use ManualBox (12) or a local, durable random box (6).

        Random-box DropInfo was server-only in the recovered APK.  D1 uses the
        explicit ``RANDOM_BOX_RULES`` table above, rolls with a stable
        account/item/sequence seed, and stores the typed response as a receipt
        when the caller supplies a retry key.  The stock client has no retry
        key, so consecutive ordinary openings remain independent.
        """

        payload = self._request_values(body)
        if "id" not in payload:
            request = self._request(body, "ApiPlayerItemUse")
            if request is not None:
                payload = {"id": request.id, "count": request.count, "index": request.index}
        item_id, count, index = _int(payload.get("id")), _int(payload.get("count"), 1), _int(payload.get("index"))
        info = ITEM_INFO.get(item_id)
        if info is None:
            return 404, {"error": "item_not_found", "id": item_id}
        if count <= 0:
            return 400, {"error": "invalid_count"}
        func = _int(info.get("funcType"))
        if func == _ITEM_FUNC_BOX:
            max_use = _int(info.get("maxUseCount")) or _int(RANDOM_BOX_MAX_USE_OVERRIDES.get(item_id))
            if max_use > 0 and count > max_use:
                return 400, {"error": "count_exceeds_max_use", "id": item_id, "maxUseCount": max_use}
        owned = self._items_owned(state, item_id)
        if owned < count:
            return 409, {"error": "insufficient_item", "id": item_id, "required": count, "owned": owned}
        if func == _ITEM_FUNC_MANUAL_BOX:
            rows = MANUAL_BOXES.get(item_id) or []
            if not 0 <= index < len(rows):
                return 400, {"error": "invalid_manual_box_index", "index": index}
            content = dict(rows[index]["inBox"])
            content["count"] = _int(content.get("count"), 1) * count
            rewards = [content]
        elif func == _ITEM_FUNC_BOX:
            request_key = self._box_request_key(payload)
            receipts = state.setdefault("randomBoxReceipts", {})
            if not isinstance(receipts, dict):
                receipts = {}
                state["randomBoxReceipts"] = receipts
            if request_key:
                previous = receipts.get(request_key)
                if isinstance(previous, Mapping):
                    same_request = (_int(previous.get("itemId")) == item_id
                                    and _int(previous.get("count")) == count
                                    and _int(previous.get("index")) == index)
                    if not same_request:
                        return 409, {"error": "idempotency_key_reused", "requestId": request_key}
                    duplicate = self._box_reply_from_receipt(previous)
                    if duplicate is not None:
                        return 200, duplicate
            sequence = _int(state.get("randomBoxUseSeq")) + 1
            state["randomBoxUseSeq"] = sequence
            rewards = self._random_box_rewards(profile, item_id, count, sequence, request_key)
            if not rewards:
                state["randomBoxUseSeq"] = sequence - 1
                return 409, {"error": "box_drop_table_unavailable", "id": item_id,
                             "message": "No local reward pool is configured for this box"}
        else:
            return 409, {"error": "item_not_usable", "id": item_id, "funcType": func}
        result = self._message("ApiPlayerItemUse.result")
        change = self._items_take(state, {item_id: count})[0]
        entry = result.res_change.add().base_res
        entry.type = change["type"]; entry.id = change["id"]; entry.param = change["param"]
        entry.count = change["count"]; entry.count_delta = change["countDelta"]
        error = self._grant_contents(profile, state, rewards, result)
        if error is not None:
            state["items"][str(item_id)]["count"] = _int(state["items"][str(item_id)].get("count")) + count
            if func == _ITEM_FUNC_BOX:
                state["randomBoxUseSeq"] = max(0, _int(state.get("randomBoxUseSeq")) - 1)
            return 409, {"error": error}
        if func == _ITEM_FUNC_BOX:
            request_key = self._box_request_key(payload)
            # Every successful opening has a durable audit row.  Explicit
            # request keys additionally make timeout retries exactly-once.
            receipt_id = request_key or f"seq:{_int(state.get('randomBoxUseSeq'))}"
            receipts = state.setdefault("randomBoxReceipts", {})
            if isinstance(receipts, dict):
                # Keep the normal (stock-client) audit row compact.  Only a
                # caller-supplied retry key needs a serialized response for
                # exactly-once replay; sequence rows retain metadata useful
                # for diagnostics without duplicating the full protobuf and
                # reward list on every opening.
                receipt = {
                    "requestId": request_key,
                    "receiptId": receipt_id,
                    "sequence": _int(state.get("randomBoxUseSeq")),
                    "itemId": item_id,
                    "count": count,
                    "index": index,
                    "createdAt": self._now(),
                }
                if request_key:
                    receipt["responseHex"] = result.SerializeToString().hex()
                receipts[receipt_id] = receipt
                # Keep the state bounded while retaining the most recent
                # explicit keys for practical retry windows.
                if len(receipts) > 512:
                    for old_key in sorted(receipts, key=lambda key: _int(receipts[key].get("sequence")) if isinstance(receipts[key], Mapping) else 0)[:-512]:
                        receipts.pop(old_key, None)
        self._save(profile, state)
        return 200, result

    def _resource_compose(self, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        """ResourceCompose: ``count`` x targetRes from materialRes; a short
        item material is composed from its own recipe first (StarCompose
        SetMaterials shows the whole chain)."""

        payload = self._request_values(body)
        if "id" not in payload:
            request = self._request(body, "ApiResourceCompose")
            if request is not None:
                payload = {"type": request.type, "id": request.id, "count": request.count}
        rtype, rid, count = _int(payload.get("type"), _RES_ITEM), _int(payload.get("id")), _int(payload.get("count"), 1)
        if count <= 0:
            return 400, {"error": "invalid_count"}
        if (rtype, rid) not in RESOURCE_COMPOSE:
            return 404, {"error": "resource_compose_not_found", "type": rtype, "id": rid}
        stock: dict[int, int] = {}
        item_delta: dict[int, int] = {}
        currency: dict[tuple[int, int], int] = {}

        def make(target_type: int, target_id: int, amount: int, depth: int = 0) -> bool:
            row = RESOURCE_COMPOSE.get((target_type, target_id))
            if row is None or depth > 8:
                return False
            per = max(1, _int(row["targetRes"].get("count"), 1))
            batches = -(-amount // per)
            for material in row.get("materialRes") or []:
                mtype, mid, need = _int(material.get("type")), _int(material.get("id")), _int(material.get("count")) * batches
                if mtype != _RES_ITEM:
                    currency[(mtype, mid)] = currency.get((mtype, mid), 0) + need
                    continue
                have = stock.setdefault(mid, self._items_owned(state, mid))
                if have < need and not make(mtype, mid, need - have, depth + 1):
                    return False
                stock[mid] -= need
                item_delta[mid] = item_delta.get(mid, 0) - need
            if target_type == _RES_ITEM:
                stock[target_id] = stock.setdefault(target_id, self._items_owned(state, target_id)) + batches * per
                item_delta[target_id] = item_delta.get(target_id, 0) + batches * per
            else:
                currency[(target_type, target_id)] = currency.get((target_type, target_id), 0) - batches * per
            return True

        if not make(rtype, rid, count):
            return 409, {"error": "resource_compose_failed", "code": 243}
        ledger = [{"type": t, "id": i, "param": 0, "count": -c} for (t, i), c in currency.items() if c]
        deltas: list[Mapping[str, Any]] = []
        if ledger:
            ok, deltas, error = self._change_resources(profile, ledger)
            if not ok:
                return 409, {"error": error or "insufficient_resource"}
        items = state.setdefault("items", {})
        result = self._message("ApiResourceCompose.result")
        for item_id, delta in sorted(item_delta.items(), key=lambda kv: (kv[0] != rid, kv[0])):
            if not delta:
                continue
            value = items.setdefault(str(item_id), {"id": item_id, "count": 0})
            value["count"] = _int(value.get("count")) + delta
            if delta > 0:
                value["gotTime"], value["isNew"] = self._now(), True
            entry = result.res_change.add().base_res
            entry.type = _RES_ITEM; entry.id = item_id; entry.count = value["count"]; entry.count_delta = delta
        for change in self._dynamic(deltas):
            entry = result.res_change.add().base_res
            entry.type = change["type"]; entry.id = change["id"]; entry.param = change["param"]
            entry.count = change["count"]; entry.count_delta = change["countDelta"]
        self._save(profile, state)
        return 200, result

    def _flags_and_profit(self, method: str, route: str, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Optional[Response]:
        """``PATCH fci/new-flag`` and ``GET fci/profit/``.

        Common_SortUtil.ClearNewFlag patches ProtobufT("ApiResourceNewFlag",
        {resInfo:[{idOrUid, type}], isNew=false}) and clears its local NEW
        markers in the callback, ignoring the body.  The flags are persisted
        (equipment.set_new_flags: characters type 15 by uid, equipment 12 by
        uid, items 11 and medals 8 by id) so the badges stay cleared after a
        re-login.  Window_ProfitBuff binds fci/profit/ as a list of active
        profit buffs; no buff is active locally.
        """

        lower = route.lower().rstrip("/")
        if lower == "/fci/new-flag":
            if method not in {"PATCH", "POST", "PUT"}:
                return 405, {"error": "method_not_allowed"}
            payload = self._request_values(body)
            res_info = payload.get("resInfo", payload.get("res_info"))
            is_new = bool(payload.get("isNew", payload.get("is_new", False)))
            if not isinstance(res_info, list):
                request = self._request(body, "ApiResourceNewFlag")
                res_info = [{"idOrUid": info.id_or_uid, "type": info.type}
                            for info in request.res_info] if request is not None else []
                is_new = bool(request.is_new) if request is not None else False
            entries = [entry for entry in res_info if isinstance(entry, Mapping)]
            if entries and equip_store.set_new_flags(profile, state, entries, is_new):
                self._save(profile, state)
            return 200, {}
        if lower == "/fci/profit":
            if method != "GET":
                return 405, {"error": "method_not_allowed"}
            guild = getattr(self.game, "guild", None)
            if guild is not None:
                try:
                    return 200, guild.active_profits(profile)
                except Exception:
                    # A cosmetic profit refresh must not turn a committed
                    # guild operation into a client-resetting 500.
                    return 200, []
            return 200, []
        return None

    def _handle_http_unlocked(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        method = method.upper(); route = _route(path)
        state = self._state(profile)
        # More specific maintenance routes must run before the historical
        # broad actor/equipment handlers (which intentionally accept generic
        # PATCH payloads for compatibility).
        for handler in (self._actor_group, self._flags_and_profit, self._cultivation, self._player_profile, self._actor_maintenance, self._equipment_maintenance, self._item_resource, self._medal_seal_cultivation,
                        self._gacha, self._actor, self._equip, self._mail, self._relation, self._guild):
            result = handler(method, path if handler in (self._item_resource, self._gacha) else route, body, profile, state)
            if result is not None:
                return result
        result = self._task(method, route, body, profile, state)
        if result is not None:
            return result
        result = self._name(method, route, body, profile)
        if result is not None:
            return result
        result = self._pvp(method, route, body, profile)
        if result is not None:
            return result
        return None


    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        """Serialize actor/equipment/mail/social mutations per profile."""
        with self._lock:
            return self._handle_http_unlocked(method, path, body, profile)



__all__ = ["LocalSystems"]
