"""Evidence-bounded responses for the original client's entering requests.

``Manager/RestifizerManager.lua:DoEnteringRequests`` starts these GETs after
``ScLoginEnd``.  This module is deliberately independent of account and game
authentication: the parent server decides who may call it and passes the
already selected local profile.  A route not listed here, or a complex route
without a supplied profile value, returns ``None`` so it remains visible to
the parent fixture router instead of receiving a guessed object.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit


Response = Tuple[int, Any]


def _route(path: str) -> str:
    value = urlsplit(path or "").path or "/"
    if value != "/":
        value = value.rstrip("/") + "/"
    # The redpoint request in the Lua source deliberately has no trailing /
    # and is normalized separately below.
    if value == "/fci/redpoint/data/":
        return "/fci/redpoint/data"
    return value


def _profile_values(profile: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(profile, Mapping):
        return {}
    values = profile.get("entering")
    return values if isinstance(values, Mapping) else profile


def _override(values: Mapping[str, Any], route: str) -> tuple[bool, Any]:
    """Find an explicitly supplied response without treating absent data as null."""

    keys = (route, route.rstrip("/"), route[5:] if route.startswith("/fci/") else route)
    for key in keys:
        if key in values:
            return True, values[key]
    return False, None


# These defaults are only structures directly established by the Lua reads or
# descriptor fields.  Complex player state intentionally has no fabricated
# default and must be supplied through profile["entering"].
_SAFE_DEFAULTS: Dict[str, Any] = {
    "/fci/friendlist/": [],
    "/fci/blacklist/": [],
    "/fci/activity/": {},
    "/fci/ActorGroup/": {},
    # The query form is consumed as a collection by the data-binding layer;
    # ApiRoleNotePad.result.roleNotepad is the element schema.
    "/fci/RoleNotePad/": [],
    # ApiAutoFightConf.result.banedSkills is a string -> uint32 map.
    "/fci/auto-fight-conf/": {"banedSkills": {}},
    "/fci/mail/": [],
    "/fci/recentcontact/": [],
    # ApiTempEntranceSwitch.get.switchInfo is repeated; an empty list is
    # explicitly safe because OnTempEntranceSwitch loops over it.
    "/fci/temp-entrance-switch/": {"switchInfo": []},
    # ApiEquipCustomMatch.result.equipCustomMatch is a repeated map.
    "/fci/equip/custom-match/": {"equipCustomMatch": {}},
    # ApiAsyncPvpRecordInfo.result fields and their proto3 defaults.
    "/fci/asyncpvprecord/": {
        "todayMaxWinNum": 0,
        "leftRewardsWinNum": [],
        "nextRefreshSyncpvpRecordTime": 0,
        "canRefresh": False,
    },
    # ApiPlayerName.get.notFirstRename is read as a single boolean.
    "/fci/name/": {"notFirstRename": False},
    # These are all optional bootstrap reads observed after ScLoginEnd.  Their
    # empty values follow the repeated/map fields in the descriptor and the
    # corresponding Lua loops, without creating a guild invitation or actor.
    "/fci/guild-player/invited/": [],
    "/fci/server-update-info/": {"timeInfo": {}},
    "/fci/actor-cultivation/": [],
    "/fci/BurstLinkPvpInfo/": {
        "reward": [],
        "resChange": [],
        "dungeonId": 0,
    },
    # Stateless fallback only: in LocalGame, client_compat routes this GET to
    # LocalEconomy, which serves the real power recovery state (resChange +
    # lastResIncTime) from PlayerLevelInitConfig.incInfo.
    "/fci/baseresource/update/": {"resChange": [], "lastResIncTime": {}},
    # ApiGachaInfo.get.gachaInfo is repeated; Window_City's normalRed loops
    # over this list and checks id/nextFreeGachaTime when present.
    "/fci/gachainfo/": {"gachaInfo": []},
    # ApiConditionReward.get: a fresh local guest has neither a qualification
    # nor a claimed manual-box reward.  manualBoxReward is optional and is
    # intentionally omitted rather than inventing an STResource.
    "/fci/conditionreward/": {
        "hasRewardQualification": False,
        "isRewardGot": False,
    },
    # Common_TaskUtil.InitData reads these fields before categorising.  This
    # is the smallest clear-all response that makes every one of those reads
    # deterministic without inventing a TaskInfo row.
    "/fci/task/": {
        "tasks": [],
        "delTasks": [],
        "disableCategory": [],
        "activePoint": 0,
        "activePointRewardGot": [],
        "taskTargetDone": [],
        "clearAll": True,
    },
    # ApiDeployMission.get: mission_info is repeated and accomplish_num is a
    # uint32.  No mission row is fabricated.
    "/fci/deploy/mission/": {"missionInfo": [], "accomplishNum": 0},
    # ApiDanmuSwitch.result.result is repeated DanmuSwitch.  Returning no
    # switch rows is the evidence-bounded local "danmu disabled" state: the
    # client has no resource for which either switch_get or switch_send can be
    # enabled.  DanmuSwitch itself is {resId, switchGet, switchSend}; no row
    # is invented here.
    "/fci/danmu-switch/": {"result": []},
    # ApiDanmu.post.post is repeated DbDanmu.  The local server does not
    # distribute or persist remote bullet comments, so the POST acknowledgement
    # is the same schema-shaped empty repeated field.
    "/fci/danmu/": {"post": []},
    # ApiAQLog.result.result is an int32 acknowledgement.  This route only
    # accepts the client's local telemetry and returns the proto3 default; it
    # does not forward the report or claim that an anti-cheat check succeeded.
    "/fci/aq/log/": {"result": 0},
    # PushService periodically sends the local TSS payload to this endpoint.
    # The fixture server never forwards or evaluates it, but an empty
    # successful RestfulResponse keeps the original client out of its retry
    # and error path while it remains on loopback.
    "/fci/tss/anti/": {},
}


_COMPLEX_ROUTES = {
    "/fci/Signboard/",
    "/fci/redpoint/data",
}


# Unlike the entering bootstrap GETs, this endpoint is polled during story
# playback and is a write-shaped ApiDanmu.post request.  Keep POST narrowly
# scoped so an accidental method mismatch cannot turn an unconfirmed route
# into a successful response.
_POST_ONLY_ROUTES = {"/fci/danmu/", "/fci/aq/log/", "/fci/tss/anti/"}


# ApiRedPointData.result fields used unconditionally by
# Manager_RestifizerManager.OnRedPointData are represented explicitly here:
# rolePresentRedpoint.presents and achievementSummaryGet.canReward.  The
# remaining entries are either stored for later windows or are only inspected
# when an activity exists.  Their empty values come from the descriptor's
# repeated/map/scalar defaults.
_REDPOINT_DEFAULT: Dict[str, Any] = {
    "redpoint": {"redpointType": {}},
    "achievementSummaryGet": {"canReward": False},
    "rolePresentRedpoint": {"presents": []},
    "medalRedpoint": {"medal": []},
    "gachaInfoGet": {"gachaInfo": []},
    "shopRedpoint": {"shopInfo": []},
    "relationPointRedpoint": False,
}


def _signboard_from_profile(profile: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Build the exact fields consumed by OnSignboard from starter evidence."""

    explicit = profile.get("signboard")
    if isinstance(explicit, Mapping):
        # Preserve only the response fields observed in OnSignboard.
        result = {
            key: explicit[key]
            for key in ("signboard", "unlockedSignboardId", "checkSignboardTime")
            if key in explicit
        }
        return result if "signboard" in result else None

    starter = profile.get("starter_config")
    if not isinstance(starter, Mapping):
        starter = profile.get("starterConfig")
    if not isinstance(starter, Mapping):
        return None
    tables = starter.get("tables")
    if not isinstance(tables, Mapping):
        return None
    rows = tables.get("PlayerInitConfig")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
        return None
    init_id = rows[0].get("initSignboardId")
    if init_id is None:
        return None
    unlocked = profile.get("unlockedSignboardId", [init_id])
    if not isinstance(unlocked, list):
        unlocked = [unlocked]
    return {
        "signboard": int(init_id),
        "unlockedSignboardId": [int(value) for value in unlocked],
        "checkSignboardTime": int(profile.get("checkSignboardTime", 0)),
    }


def handle_http(method: str, path: str, profile: Mapping[str, Any] | None = None) -> Optional[Response]:
    """Return ``(status, payload)`` for a known entering GET, else ``None``.

    ``profile`` may contain an ``entering`` mapping keyed by the full route
    (for example ``"/fci/task/"``) to provide a verified schema-shaped
    response.  It may also contain starter-config data used for Signboard:
    ``starter_config.tables.PlayerInitConfig[0].initSignboardId``.
    """

    route = _route(path)
    if route == "/fci/redpoint/data/":
        route = "/fci/redpoint/data"
    known = set(_SAFE_DEFAULTS) | _COMPLEX_ROUTES
    if route not in known:
        return None
    method_name = method.upper()
    if route in _POST_ONLY_ROUTES:
        if method_name != "POST":
            return 405, {"error": "method_not_allowed", "message": "POST required"}
    elif method_name != "GET":
        return 405, {"error": "method_not_allowed", "message": "GET required"}

    values = _profile_values(profile)
    found, value = _override(values, route)
    if found:
        return 200, value

    if route == "/fci/Signboard/":
        value = _signboard_from_profile(profile if isinstance(profile, Mapping) else {})
        return None if value is None else (200, value)

    if route == "/fci/redpoint/data":
        return 200, _REDPOINT_DEFAULT

    if route in _COMPLEX_ROUTES:
        return None
    return 200, _SAFE_DEFAULTS[route]


__all__ = ["handle_http"]
