#!/usr/bin/env python3
"""Run a deterministic smoke of the local M4-M6 compatibility subset.

This is deliberately a local evidence tool.  It creates a temporary sandbox
account and exercises handlers that already exist in ``LocalGame``; it never
contacts an official endpoint, uses a real payment provider, or changes the
checked-in APK.  The smoke is a breadth check, not a claim that the historical
server or all historical content has been recovered.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from google.protobuf import message_factory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import equipment  # noqa: E402
from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402


def _guest(accounts: LocalAccounts) -> tuple[dict[str, Any], str]:
    first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    account_token = "AccountToken " + first[1]["accountToken"]
    entry = accounts.handle(
        "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", account_token
    )[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", account_token)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    auth_body = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
    auth = accounts.handle(
        "POST", "/Auth/", auth_body, "application/wireformat",
        "AccountToken " + second[1]["accountToken"],
    )
    account = accounts.verify_access_token(auth[1]["tokenAccess"])
    if account is None:
        raise AssertionError("local guest account was not persisted")
    with closing(accounts._connect()) as connection:
        token = connection.execute(
            "SELECT access_token FROM accounts WHERE account_id = ?",
            (account["accountId"],),
        ).fetchone()[0]
    return account, "Token " + token


def _marker(value: Any) -> str:
    descriptor = getattr(value, "DESCRIPTOR", None)
    if descriptor is not None:
        return str(descriptor.full_name)
    if isinstance(value, dict):
        return "dict:" + ",".join(sorted(str(key) for key in value)[:4])
    return type(value).__name__


def run_smoke() -> dict[str, Any]:
    """Exercise the local playable surface and return a JSON-safe report."""

    checks: list[dict[str, Any]] = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ljjc-playability-") as temp:
        root = Path(temp)
        accounts = LocalAccounts(root / "accounts")
        game = LocalGame(root / "game", accounts)
        account, auth = _guest(accounts)
        race_evidence: dict[str, Any] = {}

        def call(method: str, path: str, payload: Any = None):
            body = payload if isinstance(payload, bytes) else None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
            content_type = "application/wireformat" if isinstance(payload, bytes) else "application/json"
            return game.handle_http(method, path, body, content_type, auth)

        def step(name: str, action: Callable[[], Any]) -> None:
            began = time.monotonic()
            try:
                value = action()
                checks.append({
                    "name": name,
                    "status": "passed",
                    "durationMs": round((time.monotonic() - began) * 1000, 2),
                    "marker": _marker(value),
                })
            except Exception as exc:  # pragma: no cover - exercised by CI failures
                checks.append({
                    "name": name,
                    "status": "failed",
                    "durationMs": round((time.monotonic() - began) * 1000, 2),
                    "error": f"{type(exc).__name__}: {exc}",
                })

        def require(status: int, expected: int = 200, path: str = "") -> None:
            if status != expected:
                raise AssertionError(f"{path or 'request'} returned {status}, expected {expected}")

        def auth_and_profile():
            status, payload = call("GET", "/fci/baseinfo/")
            require(status, path="baseinfo")
            if isinstance(payload, dict):
                # Plain base-info object (stored as fci/baseinfo/ by the client).
                if "level" not in payload or "name" not in payload:
                    raise AssertionError("baseinfo result is empty")
            elif getattr(payload, "DESCRIPTOR", None) is None:
                raise AssertionError("baseinfo result is empty")
            return payload

        def mainline():
            status, entered = call("POST", "/fci/dungeon/", {"dungeonId": 2011})
            require(status, path="dungeon enter")
            uid = int(entered["dungeonUid"])
            status, balanced = call("POST", f"/fci/dungeon/{uid}/balance", {})
            require(status, path="dungeon balance")
            if not balanced["balanceInfo"]["balanced"]:
                raise AssertionError("tutorial dungeon did not reach terminal state")
            status, repeated = call("POST", f"/fci/dungeon/{uid}/balance", {})
            require(status, path="dungeon balance retry")
            if repeated != balanced:
                raise AssertionError("dungeon balance retry changed the result")
            status, chapter = call("GET", "/fci/dungeon/chapter")
            require(status, path="chapter snapshot")
            return chapter

        def player_growth():
            status, actor = call("GET", "/fci/actor/1001/")
            require(status, path="actor read")
            status, level = call("POST", "/fci/actor/1001/levelup/", {"id": 1})
            require(status, path="actor levelup")
            # Enhance needs a real owned item plus food: grant equipment 20000
            # and one enhance material 40001 (a never-granted uid is 404).
            profile = game._profile(account)
            equip_uid = int(equipment.grant_equipment(game, profile, 20000)["equip"]["uid"])
            state = game.systems._state(profile)
            state.setdefault("items", {})["40001"] = {"id": 40001, "count": 1}
            game.systems._save(profile, state)
            status, equip = call("GET", f"/fci/equip/{equip_uid}/")
            require(status, path="equipment read")
            status, enhanced = call(
                "POST", f"/fci/equip/{equip_uid}/enhance/",
                {"foodRes": [{"type": 11, "id": 40001, "count": 1}]},
            )
            require(status, path="equipment enhance")
            status, profile = call("GET", "/fci/baseinfo/")
            require(status, path="growth profile")
            return {"actor": actor, "level": level, "equip": equip, "enhanced": enhanced, "profile": profile}

        def economy():
            status, shops = call("GET", "/fci/Shop")
            require(status, path="shop list")
            # fci/Shop/<id>/Goods/ is a plain list of the shop's real catalog;
            # the client trades by echoing one good back as ``info``.
            status, goods = call("GET", "/fci/Shop/1/Goods/")
            require(status, path="shop goods")
            if not isinstance(goods, list) or not goods:
                raise AssertionError("shop 1 goods list is empty")
            status, trade = call("POST", "/fci/ShopTrade/", {"shopId": 1, "info": goods[0]})
            require(status, path="shop trade")
            status, voucher = call("POST", "/fci/recharge/voucher/", {"rechargeId": 51})
            require(status, path="sandbox voucher")
            status, duplicate = call("POST", "/fci/recharge/voucher/", {"rechargeId": 51})
            require(status, expected=409, path="sandbox voucher retry")
            if duplicate.get("error") != "recharge_reward_already_claimed":
                raise AssertionError("voucher retry was not an explicit idempotent conflict")
            status, touch = call("POST", "/fci/purchasetouch", {"productId": "local.sandbox.60", "count": 1})
            require(status, path="sandbox purchase order")
            token = str(touch.trade_data)
            status, delivered = call("POST", "/fci/purchasedelevery", {"tradeData": token})
            require(status, path="sandbox purchase delivery")
            return {"shops": shops, "goods": goods, "trade": trade, "voucher": voucher, "delivered": delivered}

        def events():
            status, activity = call("GET", "/fci/activity/")
            require(status, path="activity")
            status, first_signin = call("POST", "/fci/signin")
            require(status, path="signin")
            status, repeat_signin = call("POST", "/fci/signin")
            require(status, path="signin retry")
            if first_signin.days != repeat_signin.days:
                raise AssertionError("signin retry changed the day counter")
            status, tasks = call("GET", "/fci/task/")
            require(status, path="task list")
            status, target = call("POST", "/fci/task/target-done-add/", {"targetId": 42})
            require(status, path="task progress")
            status, mail = call("GET", "/fci/mail/")
            require(status, path="mail list")
            status, obtained = call("POST", "/fci/mail/obtain/", {"id": 1})
            require(status, path="mail obtain")
            status, obtained_again = call("POST", "/fci/mail/obtain/", {"id": 1})
            require(status, path="mail obtain retry")
            if len(obtained_again.res_change) != 0:
                raise AssertionError("mail retry granted the attachment twice")
            status, gacha = call("POST", "/fci/gachatrade/", {"count": 1})
            require(status, path="sandbox gacha")
            return {"activity": activity, "tasks": tasks, "mail": mail, "gacha": gacha}

        def social_and_async_pvp():
            status, friends = call("GET", "/fci/friend/list")
            require(status, path="friend list")
            if not friends.get("friends"):
                raise AssertionError("robot friend list is empty")
            status, applied = call("POST", "/fci/friend/apply", {"playerId": "local-bot-003"})
            require(status, path="friend apply")
            status, chat = call("GET", "/fci/chat")
            require(status, path="chat list")
            status, sent = call("POST", "/fci/chat", {"text": "local smoke"})
            require(status, path="chat send")
            status, match = call("POST", "/fci/asyncpvp/match", {})
            require(status, path="async pvp match")
            status, settled = call("POST", "/fci/asyncpvp/settle", {"win": True})
            require(status, path="async pvp settle")
            status, ranking = call("GET", "/fci/leaderboard")
            require(status, path="leaderboard")
            return {"friends": friends, "chat": chat, "match": match, "settled": settled, "ranking": ranking}

        def guild():
            status, joined = call("POST", "/fci/guild/join/", {})
            require(status, path="guild join")
            status, donated = call("POST", "/fci/guild/donate/", {"amount": 100})
            require(status, path="guild donate")
            status, summary = call("GET", "/fci/guild/summary")
            require(status, path="guild summary")
            return {"joined": joined, "donated": donated, "summary": summary}

        def realtime():
            # This disposable smoke account must satisfy the real Lv26 gate.
            with closing(game._connect()) as connection, connection:
                connection.execute("UPDATE game_profiles SET level=MAX(level,26) WHERE account_id=?", (account["accountId"],))
            status, info = call("GET", "/fci/RacePVPInfo")
            require(status, path="race info")
            profile = game._profile(account)
            if len(info["players"]) != 20 or sum(p["playerId"] == profile["player_id"] for p in info["players"]) != 1:
                raise AssertionError("race room must contain self plus 19 robots")
            request_type = message_factory.GetMessageClass(game._pool.FindMessageTypeByName("ApiRacePVPDungeon"))
            request = request_type(dungeon_id=info["dungeonId"], select_actor_group_id=1).SerializeToString()
            tickets_before = game.systems._state(profile)["items"]["80004"]["count"]
            status, created = call("POST", "/fci/racepvp-dungeon/", request)
            require(status, path="race enter")
            uid = created["dungeonUid"]
            status, retried = call("POST", "/fci/racepvp-dungeon/", request)
            require(status, path="race enter retry")
            tickets_after = game.systems._state(profile)["items"]["80004"]["count"]
            if retried["dungeonUid"] != uid or tickets_after != tickets_before - 1:
                raise AssertionError("race enter retry changed run or spent another ticket")
            active = game._types["FightActiveData"](dungeonType=7, stageID=info["dungeonId"], gameRound=8, winTeam=1)
            active.teamList.add(team=1).statistic.totalDamage = 1000
            active.teamList.add(team=2).statistic.totalDamage = 25000
            status, _ = call("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString())
            require(status, path="race active snapshot")
            status, settled = call("POST", f"/fci/racepvp-dungeon/{uid}/balance/", {})
            require(status, path="race balance")
            if settled != {"curBattleScore": 2500, "highBattleScore": 2500}:
                raise AssertionError("race score did not use enemy received damage / 10")
            status, repeat = call("POST", f"/fci/racepvp-dungeon/{uid}/balance/", {})
            require(status, path="race balance retry")
            if repeat != settled:
                raise AssertionError("race settlement retry changed persisted result")
            status, _ = call("DELETE", f"/fci/racepvp-dungeon/{uid}")
            require(status, path="race leave")
            race_evidence.update(uid=uid, balance=settled, tickets=tickets_after, playerId=profile["player_id"])
            return settled

        step("auth_and_profile", auth_and_profile)
        step("mainline_dungeon_and_idempotent_balance", mainline)
        step("player_growth_and_equipment", player_growth)
        step("shop_and_sandbox_payment", economy)
        step("events_tasks_mail_gacha", events)
        step("friends_chat_async_pvp_leaderboard", social_and_async_pvp)
        step("guild_robot_compatibility", guild)
        step("realtime_race_pvp", realtime)

        def persistence():
            restarted = LocalGame(root / "game", accounts)
            status, base = restarted.handle_http("GET", "/fci/baseinfo/", authorization=auth)
            require(status, path="restart baseinfo")
            status, chat = restarted.handle_http("GET", "/fci/chat", authorization=auth)
            require(status, path="restart chat")
            status, guild_summary = restarted.handle_http("GET", "/fci/guild/summary", authorization=auth)
            require(status, path="restart guild")
            status, race = restarted.handle_http("GET", "/fci/RacePVPInfo", authorization=auth)
            require(status, path="restart race")
            if not chat["messages"] or chat["messages"][-1]["text"] != "local smoke":
                raise AssertionError("chat state did not survive restart")
            if not guild_summary.get("guild"):
                raise AssertionError("guild state did not survive restart")
            own = next((p for p in race["players"] if p["playerId"] == race_evidence.get("playerId")), None)
            if len(race["players"]) != 20 or own is None or own["battleScore"] != 2500:
                raise AssertionError("race room score did not survive restart")
            status, repeated = restarted.handle_http("POST", f"/fci/racepvp-dungeon/{race_evidence['uid']}/balance/",
                                                       b"{}", "application/json", auth)
            require(status, path="restart race balance retry")
            if repeated != race_evidence["balance"]:
                raise AssertionError("race balance did not survive restart")
            profile = restarted._profile(account)
            if restarted.systems._state(profile)["items"]["80004"]["count"] != race_evidence["tickets"]:
                raise AssertionError("race ticket spend did not survive restart")
            return {"base": base, "chat": chat, "guild": guild_summary, "race": race}

        step("restart_persistence", persistence)

    passed = all(item["status"] == "passed" for item in checks)
    return {
        "schemaVersion": 1,
        "passed": passed,
        "scope": "local_m4_m6_compatibility_subset",
        "checks": checks,
        "checkCount": len(checks),
        "passedChecks": sum(item["status"] == "passed" for item in checks),
        "durationMs": round((time.monotonic() - started) * 1000, 2),
        "boundaries": [
            "Uses local guest accounts and sandbox payment only.",
            "Does not prove official content, real-player networking, or Android client G1/TPShell compatibility.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    args = parser.parse_args(argv)
    report = run_smoke()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_smoke", "main"]
