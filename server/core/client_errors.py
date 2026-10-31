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
    # All keys below are used by the shipped Lua, and need no format arguments.
    if reason.startswith(("Error_", "WindowLogin_")):
        code = reason
    elif any(word in reason for word in ("password", "credentials", "account_token", "access_token", "unknown_entry")):
        code = "WindowLogin_LoginFailed"
    elif "activity" in reason and any(word in reason for word in ("closed", "inactive", "not_open", "not_started")):
        code = "Error_ActivityNotStart"
    elif "insufficient_item" in reason or "ticket" in reason:
        code = "Window_ItemNotEnough"
    elif any(word in reason for word in ("insufficient", "not_enough", "unaffordable")):
        code = "Error_CostResourceNotEnough"
    elif "locked" in reason:
        code = "Error_DungeonLocked"
    elif any(word in reason for word in ("limit", "attempts", "times")):
        code = "Error_DungeonDoneTimes"
    elif status >= 500:
        code = "Error_DbError"
    else:
        code = "Window_SaveFail"
    return (507 if status >= 500 else 509), {"notify": {"code": code}}
