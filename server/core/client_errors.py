"""Native Restifizer rejection replies (Manager/RestifizerManager.lua).

The original ``error`` branch resets the game even with a valid code.  509
notify displays a hint; 507 notify displays a dialog without a reset callback.
Keep the internal status/reason for diagnostics and adapt only at HTTP egress.
"""
from urllib.parse import urlsplit


def is_client_route(path: str) -> bool:
    route = urlsplit(path).path.lower().rstrip("/")
    return route.startswith(("/fci/", "/match/", "/auth/", "/accountentry/", "/password/")) or route in {
        "/auth", "/account", "/accountsession", "/entryreg", "/existentry", "/exist-entry",
        "/password", "/passwordreset", "/accountguestbind", "/accountentry", "/newentry",
        "/verifycode", "/passwordverifycode", "/currententrymodifyverifycode", "/newentrymodifyverifycode",
        "/exist-entry-verify-code", "/captcha", "/extrainfo", "/extrainfobyauth", "/clickgame",
    }


def client_error_reply(status: int, payload: dict) -> tuple[int, dict]:
    if status in {507, 509} and isinstance(payload.get("notify"), dict) and payload["notify"].get("code"):
        return status, payload
    reason = payload.get("error", "request_failed")
    if isinstance(reason, dict):
        reason = reason.get("code", "request_failed")
    reason = str(reason)
    lower = reason.casefold()
    # Authentication has two different client contracts.  An expired access
    # token must remain HTTP 401 so a later client patch can clear the cache and
    # return to login; account/password rejection is a normal 509 notify.
    if status == 401 and lower in {
        "invalid_access_token", "expired_access_token", "invalid_account_token",
        "expired_account_token", "missing_account_token", "account_token_required",
    }:
        return 401, {"notify": {"code": "Error_KickReason_NeedLoginAgain"}}
    if lower in {"invalid_credentials", "password_incorrect", "wrong_password"}:
        return 509, {"notify": {"code": "Error_PasswordDoNotMatch"}}
    if lower in {"unknown_account", "unknown_entry", "account_not_found", "account_not_exist"}:
        return 509, {"notify": {"code": "Error_AccountNotExist"}}
    if lower in {"purchase_limit_reached", "mall_good_limit_reached", "shop_good_limit_reached",
                 "buy_limit_reached", "pending_product_unavailable", "purchase_product_mismatch"}:
        return 509, {"notify": {"code": "Error_BuyLimit"}}
    if lower in {"activity_closed_today", "mall_activity_closed", "race_cycle_closed",
                 "activity_not_open", "activity_not_started", "activity_inactive"}:
        return 509, {"notify": {"code": "Error_ActivityNotStart"}}
    if lower in {"level_too_low", "player_level_too_low", "gold_dungeon_locked", "race_level_locked"}:
        return 509, {"notify": {"code": "Error_PlayerLevelNotReach"}}
    if lower in {"dungeon_done_times", "deploy_count_limit", "deploy_accomplish_limit",
                 "triggered_event_daily_limit", "attempts_exhausted"}:
        return 509, {"notify": {"code": "Error_DungeonDoneTimes"}}
    if lower in {"insufficient_resource", "insufficient_energy", "not_enough_gold_ticket",
                 "not_enough_triggered_ticket", "race_ticket_exhausted", "ticket_exhausted",
                 "gacha_chips_not_enough", "team_dungeon_ticket_not_enough"}:
        return 509, {"notify": {"code": "Error_ItemNumNotEnough"}}
    if lower in {"role_present_not_available", "role_present_daily_limit",
                 "role_present_already_claimed"}:
        return 509, {"notify": {"code": "Error_PresentNotFound"}}
    if lower in {"guild_forbidden", "guild_permission_denied", "guild_no_permission",
                 "guild_have_no_permission", "only_guild_admin", "not_guild_member",
                 "guild_not_member"} or (status == 403 and "guild" in lower):
        return 509, {"notify": {"code": "Error_GuildHaveNoPermission"}}
    if lower in {"guild_not_found", "local_bot_not_found", "member_not_found",
                 "vote_not_found", "support_request_not_found", "guild_target_not_found"} or (
                 status == 404 and ("guild" in lower or "member" in lower or "vote" in lower or "support" in lower)):
        return 509, {"notify": {"code": "Error_GuildNotFound"}}
    if lower in {"unknown_task_route", "unknown_active_point_type", "unknown_active_point",
                 "unknown_week_active_point", "unknown_extra_active_point", "task_unknown"}:
        return 509, {"notify": {"code": "Error_TaskNotFound"}}
    if lower in {"dungeon_not_active", "race_dungeon_closed", "battle_not_finished"}:
        return 509, {"notify": {"code": "Error_DungeonNotActive"}}
    if lower in {"dungeon_balance_not_implemented", "resource_update_failed", "invalid_request"}:
        return 509, {"notify": {"code": "Error_RequestFailed"}}
    # All keys below are used by the shipped Lua, and need no format arguments.
    if reason.startswith(("Error_", "WindowLogin_")):
        code = reason
    elif any(word in lower for word in ("password", "credentials")):
        code = "Error_PasswordDoNotMatch"
    elif any(word in lower for word in ("account_token", "access_token", "unknown_entry")):
        code = "Error_KickReason_NeedLoginAgain" if status == 401 else "Error_AccountNotExist"
    elif "activity" in lower and any(word in lower for word in ("closed", "inactive", "not_open", "not_started")):
        code = "Error_ActivityNotStart"
    elif "insufficient_item" in lower or "ticket" in lower:
        code = "Error_ItemNumNotEnough"
    elif any(word in lower for word in ("insufficient", "not_enough", "unaffordable")):
        code = "Error_CostResourceNotEnough"
    elif "locked" in lower:
        code = "Error_DungeonLocked"
    elif any(word in lower for word in ("done_times", "attempts")):
        code = "Error_DungeonDoneTimes"
    elif status >= 500:
        code = "Error_DbError"
    else:
        code = "Window_SaveFail"
    return (507 if status >= 500 else 509), {"notify": {"code": code}}
