#!/usr/bin/env python3
"""Configurable local compatibility service for the original LJJC client.

Provides local guest accounts, login push messages, persistent local profiles,
the currently implemented story/battle routes, and a clearly marked local
shop/payment sandbox. Startup files come from ``--fixture-root`` and the
optional ``server.toml`` controls loopback/LAN binding and bootstrap URLs;
unknown routes return 501. Official accounts and the complete original game
backend are not implemented, and no external payment provider is contacted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import ssl
import threading
import tomllib
import weakref
from typing import Any, Mapping
import lz4.frame
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, unquote, urlsplit
from accounts import LocalAccounts
from push import handle_upgrade
from game import LocalGame
from google.protobuf.message import Message as ProtobufMessage
from server.core.clock import ServerClock
from server.core.routes import RouteRegistry
from server.core.backup import DailyBackupScheduler, backup_state, list_backups, prune_backups, restore_sqlite
from server.core.crash_logs import CrashLogStore
from server.core.activity_schedule import ActivitySchedule
from server.core.push_service import PushService
from storage.compat import ensure_unified_store, resolve_unified_store_path


def _wire_bytes(field_number: int, body: bytes) -> bytes:
    length = len(body)
    prefix = bytearray([(field_number << 3) | 2])
    while length > 127:
        prefix.append((length & 127) | 128)
        length >>= 7
    prefix.append(length)
    return bytes(prefix) + body


def encode_raw_json(payload: object) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _wire_bytes(4, body)


def encode_response(payload: object) -> bytes:
    if isinstance(payload, ProtobufMessage):
        message = (_wire_bytes(2, payload.DESCRIPTOR.full_name.encode('utf-8'))
                   + _wire_bytes(3, payload.SerializeToString()))
        return _wire_bytes(1, message) + b'\x38\x01'
    return encode_raw_json(payload)


def _balance_wire_compat(payload: object) -> object:
    """Make chapter record maps numeric-indexable for the old JSON decoder.

    Restifizer's raw JSON path turns object keys into Lua strings, while the
    original protobuf path exposes the same dungeon map with numeric keys.
    DungeonBalance Lua indexes ``recordDungeons[dungeonId]`` directly.  Encode
    each all-numeric map as an array padded to its largest dungeon id so the
    decoder creates numeric array indices and the original UI can consume the
    complete chapter result.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("chapterInfo"), list):
        return payload
    result = dict(payload)
    chapters = []
    for chapter in payload["chapterInfo"]:
        if not isinstance(chapter, dict) or not isinstance(chapter.get("recordDungeons"), dict):
            chapters.append(chapter)
            continue
        records = chapter["recordDungeons"]
        numeric = {}
        for key, value in records.items():
            try:
                dungeon_id = int(key)
            except (TypeError, ValueError):
                numeric = {}
                break
            if dungeon_id < 1 or str(dungeon_id) != str(key):
                numeric = {}
                break
            numeric[dungeon_id] = value
        if not numeric:
            chapters.append(chapter)
            continue
        expanded = [None] * max(numeric)
        for dungeon_id, value in numeric.items():
            expanded[dungeon_id - 1] = value
        chapter_copy = dict(chapter)
        chapter_copy["recordDungeons"] = expanded
        chapters.append(chapter_copy)
    result["chapterInfo"] = chapters
    return result


def decode_request_body(body: bytes, encoding: str | None) -> bytes:
    if not encoding or encoding.lower() == 'identity':
        return body
    if encoding.lower() != 'lz4':
        raise ValueError('unsupported Content-Encoding')
    try:
        decoder = lz4.frame.LZ4FrameDecompressor()
        plain = decoder.decompress(body, max_length=MAX_BODY_BYTES + 1)
        if len(plain) > MAX_BODY_BYTES:
            raise ValueError('decompressed request exceeds 1 MiB')
        if not decoder.eof or decoder.unused_data:
            raise ValueError('incomplete or concatenated LZ4 frame')
        return plain
    except RuntimeError as exc:
        raise ValueError('invalid LZ4 frame') from exc


MAX_BODY_BYTES = 1024 * 1024
DIR_ACCOUNT_PATH = "/dirsvr/DirAccount"
DIR_ACCOUNT_BODY_HEX_MAX = 4096
QUERY_VALUE_ALLOWLIST = {
    "version",
    "appversion",
    "patchversion",
    "client_version",
    "device_type",
    "platform",
    "channel",
    "channel_id",
    "os",
    "game_version",
}
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
}


class JsonlLogger:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._day = datetime.now(timezone.utc).date().isoformat()

    def write(self, **fields: object) -> None:
        now = datetime.now(timezone.utc)
        record = {"ts": now.isoformat(), **fields}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        logging.info(line)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                day = now.date().isoformat()
                if day != self._day and self.path.exists():
                    archive = self.path.with_name(f"{self.path.stem}.{self._day}{self.path.suffix}")
                    if archive.exists():
                        archive = self.path.with_name(f"{self.path.stem}.{self._day}.{int(now.timestamp())}{self.path.suffix}")
                    self.path.replace(archive)
                self._day = day
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")


class FixtureServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        fixture_root: Path,
        audit_log: JsonlLogger,
        tls: bool = False,
        tls_context: ssl.SSLContext | None = None,
        accounts: LocalAccounts | None = None,
        game: LocalGame | None = None,
        public_base_url: str | None = None,
        clock: ServerClock | None = None,
        state_dir: Path | None = None,
        backup_dir: Path | None = None,
        backup_retention: int = 7,
        crash_logs: CrashLogStore | None = None,
        activity_schedule: ActivitySchedule | None = None,
    ):
        super().__init__(address, FixtureHandler)
        self.fixture_root = fixture_root.resolve()
        self.audit_log = audit_log
        self.tls = tls
        self.accounts = accounts
        self.game = game
        self.public_base_url = public_base_url.rstrip('/') if public_base_url else None
        self.clock = clock or ServerClock()
        inferred_state = state_dir
        if inferred_state is None:
            inferred_state = getattr(accounts, "state_dir", None) or getattr(game, "state_dir", None)
        self.state_dir = Path(inferred_state).resolve() if inferred_state is not None else None
        self.backup_dir = Path(backup_dir).resolve() if backup_dir is not None else (
            self.state_dir / "backups" if self.state_dir is not None else None
        )
        self.backup_retention = max(1, int(backup_retention))
        self._admin_lock = threading.RLock()
        self.crash_logs = crash_logs or (
            CrashLogStore(self.state_dir / "crash-logs", clock=self.clock.now)
            if self.state_dir is not None else None
        )
        self.daily_backup: DailyBackupScheduler | None = None
        self.activity_schedule = activity_schedule
        self.push_service = PushService(self.audit_log)
        self._wire_task_push()
        self._wire_base_push()
        self.tls_context = tls_context
        contracts_path = Path(__file__).resolve().parent / "contracts" / "api.yaml"
        try:
            self.route_registry = RouteRegistry.from_yaml(contracts_path)
        except Exception:
            # A fixture-only test or packaged runtime may not ship the
            # contract table; the legacy HTTP service remains usable and the
            # coverage endpoint reports an empty registry in that case.
            self.route_registry = RouteRegistry()
        self._register_contract_handlers()
        self._sni_lock = threading.Lock()
        self._pending_sni: dict[int, str | None] = {}

    def _wire_task_push(self) -> None:
        """Deliver task progress live as ``ScTasksChange`` (TaskUtil.InitData).

        The client reads ``/fci/task/`` once per session; afterwards it only
        learns about progress from this push.  LocalTasks calls the notifier
        with the hook's game profile; sessions are found by account id.

        ``main`` builds an HTTP and a TLS server around one LocalGame and each
        owns a PushService, so the notifier fans out to every live server's
        service; the push socket is registered on exactly one of them.
        """

        tasks = getattr(self.game, "tasks", None) if self.game is not None else None
        if tasks is None or not hasattr(tasks, "notifier"):
            return
        services = getattr(tasks.notifier, "push_services", None)
        if services is None:
            services = weakref.WeakSet()

            def notify(profile: Mapping[str, Any], payload: Mapping[str, Any]) -> int:
                account_id = profile.get("account_id") if isinstance(profile, Mapping) else None
                if account_id is None:
                    return 0
                return sum(service.tasks_change_for_account_id(account_id, payload)
                           for service in list(services))

            notify.push_services = services  # type: ignore[attr-defined]
            tasks.notifier = notify
        services.add(self.push_service)

    def _wire_base_push(self) -> None:
        """Deliver ``BasePushMessage`` (e.g. BaseInfoChange after a level-up)
        from LocalGame to the account's live push socket on any server."""

        game = self.game
        if game is None or not hasattr(game, "base_push_notifier"):
            return
        services = getattr(game.base_push_notifier, "push_services", None)
        if services is None:
            services = weakref.WeakSet()

            def notify(profile: Mapping[str, Any], push_type: int) -> int:
                account_id = profile.get("account_id") if isinstance(profile, Mapping) else None
                if account_id is None:
                    return 0
                return sum(service.base_push_for_account_id(account_id, push_type)
                           for service in list(services))

            notify.push_services = services  # type: ignore[attr-defined]
            game.base_push_notifier = notify
        services.add(self.push_service)

    def _local_directory_contract(self, route: str, request: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        """Return deterministic directory/config responses for the local world.

        These are the small control-plane messages the retired client requests
        before it reaches the account and FCI services.  They intentionally
        describe only this local sandbox; no historical version, remote world,
        or third-party account endpoint is advertised.
        """

        base_url = self.public_base_url or f"http://{self.server_address[0]}:{self.server_address[1]}"
        if base_url.endswith("/"):
            base_url = base_url.rstrip("/")
        world = {
            "worldId": 1,
            "worldName": "本地测试区",
            "accountUrl": base_url,
            "accountWebUrl": base_url,
            "pushUrl": base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/push",
            "fciUrl": base_url,
            "gmUrl": base_url,
            "matchUrl": base_url,
        }
        dir_stat = {"stat": 0, "dirOpenTime": 0}
        account_switch = {
            "qqLoginSwitch": False,
            "wxLoginSwitch": False,
            "guestLoginSwitch": True,
            "wxScanCodeLoginSwitch": False,
            "bulletinSwitch": False,
            "customService": False,
        }
        route = str(route).rstrip("/") + "/"
        if route == "/Dir/":
            return 200, {"dirStat": dir_stat, "dirWorld": {"worldInfo": world}, "sandbox": True}
        if route == "/DirAccount/":
            return 200, {"dirStat": dir_stat, "accountUrl": base_url, "accountWebUrl": base_url,
                         "accountSwitch": account_switch, "sandbox": True}
        if route == "/WorldConfig/":
            return 200, {"ret": 0, "worldConfigGet": {"formalWorldInfo": world, "commitWorldInfo": world, "guestWorldInfo": world}, "sandbox": True}
        if route == "/DirStat/":
            return 200, {"ret": 0, "dirStatGet": dir_stat, "sandbox": True}
        if route == "/CommitVersion/":
            version = {"androidCommitVersion": {"commitVersion": "local"}, "iosCommitVersion": {"commitVersion": "local"}}
            return 200, {"ret": 0, "commitVersionGet": version, "sandbox": True}
        if route == "/FormalVersion/":
            version = {"isCheckVersion": False, "androidFormalVersion": {"baseVersion": "local", "suggestVersion": "local"}, "iosFormalVersion": {"baseVersion": "local", "suggestVersion": "local"}}
            return 200, {"ret": 0, "formalVersionGet": version, "sandbox": True}
        if route == "/AllConfig/":
            return 200, {
                "ret": 0,
                "formalVersionGet": {"isCheckVersion": False, "androidFormalVersion": {"baseVersion": "local", "suggestVersion": "local"}, "iosFormalVersion": {"baseVersion": "local", "suggestVersion": "local"}},
                "commitVersionGet": {"androidCommitVersion": {"commitVersion": "local"}, "iosCommitVersion": {"commitVersion": "local"}},
                "worldConfigGet": {"formalWorldInfo": world, "commitWorldInfo": world, "guestWorldInfo": world},
                "dirStatGet": dir_stat,
                "accountSwitchGet": {"formalAccountSwitch": account_switch, "commitAccountSwitch": account_switch, "guestAccountSwitch": account_switch},
                "sandbox": True,
            }
        if route == "/iplimit/":
            return 200, {"ipList": [], "sandbox": True}
        if route == "/test/":
            return 200, {"test": "local", "sandbox": True}
        if route == "/accountswitch/":
            return 200, {"result": {"whiteIdSwitch": False, "cdkeySwitch": False, "bookingFullSwitch": False, "miziPaySwitch": False}, "sandbox": True}
        return 404, {"error": "directory_route_not_found", "path": route}

    def _local_status_contract(self, route: str, request: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        """Return safe local responses for the non-game status contracts.

        These endpoints are control-plane probes from the retired client.  A
        successful response only describes this process and never forwards a
        health check, account token, telemetry payload, or payment request to
        a remote service.
        """

        normalised = str(route).rstrip("/") or "/"
        base_url = self.public_base_url or f"http://{self.server_address[0]}:{self.server_address[1]}"
        if normalised == "/health":
            return 200, {"status": "ok", "service": "ljjc-fixture-server", "sandbox": True}
        if normalised == "/info":
            settings = {
                "service": "ljjc-fixture-server",
                "baseUrl": base_url,
                "worldId": 1,
                "sandbox": True,
            }
            return 200, {"settings": json.dumps(settings, ensure_ascii=False, separators=(",", ":")), "sandbox": True}
        if normalised == "/info/res":
            return 200, {"resources": [], "sandbox": True}
        if normalised == "/status":
            return 200, {"status": 1, "msg": "local", "sandbox": True}
        if normalised == "/myip":
            # Do not expose the host's external address; the client only uses
            # this as a diagnostic label.
            return 200, {"get": "127.0.0.1", "sandbox": True}
        if normalised == "/isopen":
            return 200, {"isOpen": True, "sandbox": True}
        if normalised == "/server-update-info":
            return 200, {"timeInfo": {}, "sandbox": True}
        if normalised == "/close_ws":
            return 200, {"closed": True, "sandbox": True}
        if normalised == "/gamecenter":
            return 200, {
                "gamecenterLoginTime": 0,
                "gamecenterClickTime": 0,
                "qqvip": 0,
                "xinyueClickTime": 0,
                "raceClickTime": 0,
                "animeMovieClickTime": 0,
                "animeMovieOctClickTime": 0,
                "sandbox": True,
            }
        if normalised == "/xinyue-vipinfo":
            return 200, {"ret": 0, "msg": "local sandbox", "ilevel": 0, "sandbox": True}
        if normalised == "/db-access":
            return 200, {"result": [], "sandbox": True}
        if normalised == "/temp-entrance-switch":
            return 200, {"switchInfo": [], "sandbox": True}
        if normalised == "/syncmidas":
            # This acknowledgement is local bookkeeping only; remote Midas
            # receipts are still rejected by the economy module.
            return 200, {"result": 0, "sandbox": True}
        if normalised == "/asmiadinfo":
            return 200, {"result": [], "sandbox": True}
        if normalised == "/ManageAuthInfo":
            # The historical endpoint exposed account-session diagnostics to
            # the retired account service.  Keep the local response free of
            # tokens and passwords while preserving the response envelope.
            return 200, {"get": {"service": "local", "sessionCount": 0, "sandbox": True}, "sandbox": True}
        if normalised == "/ManageSession":
            # LDAP is intentionally not supported.  A local session probe is
            # still useful to clients that only need a successful control
            # plane handshake.
            return 200, {"result": {"clientType": "local", "ldap": False, "sandbox": True}, "sandbox": True}
        if normalised == "/App":
            method = str(request.get("method", "GET")).upper()
            payload: dict[str, Any] = {"appId": "local", "appKeyType": "sandbox", "sandbox": True}
            if method == "GET":
                return 200, {"apps": ["local"], "sandbox": True}
            if method in {"POST", "PATCH"}:
                return 200, {"post" if method == "POST" else "patch": payload, "sandbox": True}
            return 405, {"error": "method_not_allowed", "message": "GET, POST or PATCH required"}
        if normalised.startswith("/App/"):
            app_id = normalised[len("/App/"):].strip("/") or "local"
            return 200, {"get": {"appId": app_id[:128], "appKeyType": "sandbox", "sandbox": True}, "sandbox": True}
        if normalised == "/ClickGame":
            return 200, {"result": 0, "sandbox": True}
        if normalised.startswith("/models"):
            pieces = [item for item in normalised.split("/") if item]
            if len(pieces) == 1:
                return 200, {"models": ["local"], "sandbox": True}
            response: dict[str, Any] = {"model": pieces[1][:128], "sandbox": True}
            if len(pieces) >= 3:
                response["key"] = pieces[2][:128]
                response["value"] = None
            return 200, response
        if normalised.startswith("/checkdata/"):
            # This historical debugging endpoint must never dump local
            # account or profile data.  Return only a deterministic health
            # marker for the requested model/key pair.
            return 200, {"data": "local", "sandbox": True}
        if normalised == "/dirtycheck" or normalised.startswith("/dirtycheck/"):
            return 200, {"dirty": False, "result": 0, "sandbox": True}
        if normalised.startswith("/real-name-verified/"):
            return 200, {"verified": True, "adult": True, "sandbox": True}
        if normalised.startswith("/entry-exist/") or normalised.startswith("/openid-exist/"):
            return 200, {"exists": False, "sandbox": True}
        if normalised in {"/playeridbyname", "/openidbyplayerid"}:
            if self.accounts is None or self.game is None:
                return 503, {"error": "local_player_store_unavailable", "sandbox": True}
            authorization = str(request.get("authorization") or "")
            parts = authorization.strip().split(None, 1)
            token = parts[1].strip() if len(parts) == 2 and parts[0].casefold() in {"token", "accounttoken"} else ""
            account = self.accounts.verify_access_token(token)
            if account is None:
                return 401, {"error": "invalid_access_token", "message": "local access token required"}
            profile = self.game._profile(account)
            if normalised == "/playeridbyname":
                payload = self._request_json(request)
                requested = str(payload.get("playerName", payload.get("name", ""))).strip()
                if requested and requested != str(profile.get("name", "")):
                    return 404, {"error": "player_not_found", "sandbox": True}
                return 200, {
                    "playerId": str(profile.get("player_id", "")),
                    "entry": str(account.get("entry", "")),
                    "accountId": str(account.get("accountId", "")),
                    "openId": str(account.get("openId", "")),
                    "sandbox": True,
                }
            requested_id = str(self._request_json(request).get("playerId", "")).strip()
            if requested_id and requested_id != str(profile.get("player_id", "")):
                return 404, {"error": "player_not_found", "sandbox": True}
            return 200, {
                "playerId": str(profile.get("player_id", "")),
                "msdkOpenId": str(account.get("openId", "")),
                "openId": str(account.get("openId", "")),
                "sandbox": True,
            }
        return 404, {"error": "status_route_not_found", "path": normalised}

    def _local_legacy_account_bind(self, request: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        """Adapt the retired BBrr bind probe to the current local account.

        ``AccountEntry/bind-bbrr-old`` was an account migration seam.  The
        original provider is gone, so there is no external identity to
        import.  A caller that already holds a local access token can still
        use the route as a harmless compatibility probe; it returns the
        redacted local account record and never creates, imports, or merges an
        external account.
        """

        if self.accounts is None:
            return 503, {"error": "local_account_store_unavailable", "sandbox": True}
        authorization = str(request.get("authorization") or "")
        parts = authorization.strip().split(None, 1)
        token = parts[1].strip() if len(parts) == 2 and parts[0].casefold() in {"token", "accounttoken"} else ""
        account = self.accounts.verify_access_token(token)
        if account is None:
            return 401, {"error": "invalid_access_token", "message": "local access token required"}
        redacted = {
            key: account[key]
            for key in ("accountId", "openId", "entry", "entryType", "username")
            if key in account and account[key] is not None
        }
        redacted["sandbox"] = True
        return 200, {"post": redacted, "sandbox": True}

    @staticmethod
    def _request_json(request: Mapping[str, Any]) -> dict[str, Any]:
        """Decode an optional local JSON request without raising on wire data."""

        body = request.get("body", b"")
        if isinstance(body, str):
            raw = body.encode("utf-8")
        else:
            try:
                raw = bytes(body or b"")
            except (TypeError, ValueError):
                return {}
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    def _register_contract_handlers(self) -> None:
        """Bind implemented account calls to the contract registry."""

        # The directory/config contract is deliberately served from the same
        # process as the game.  The dynamic ``/dirsvr/*`` fixture remains for
        # legacy bootstrap calls, while these canonical routes make the
        # contract table useful to local clients and diagnostics as well.
        directory_bindings = {
            "ReqDir": "/Dir/",
            "ReqWorldConfig": "/WorldConfig/",
            "ReqDirStat": "/DirStat/",
            "ReqCommitVersion": "/CommitVersion/",
            "ReqFormalVersion": "/FormalVersion/",
            "ReqAllConfig": "/AllConfig/",
            "ApiIpLimit": "/iplimit/",
            "ReqDirAccount": "/DirAccount/",
            "ApiTest": "/test/",
            "ApiAccountSwitch": "/accountswitch/",
        }
        for interface, route in directory_bindings.items():
            def directory_handler(request: Mapping[str, Any], _params: Mapping[str, str], route=route):
                return self._local_directory_contract(route, request)
            for method in ("GET", "POST"):
                try:
                    self.route_registry.register_path(route, directory_handler, method=method, interface=interface)
                except Exception:
                    continue

        # ``ApiViewTest`` is a duplicate descriptor for the legacy /test/
        # probe.  Keep ApiTest's POST binding and expose the read-only GET
        # variant under its own interface so coverage reflects both observed
        # client call shapes without changing the response.
        try:
            self.route_registry.register_path(
                "/test/",
                lambda request, _params: self._local_directory_contract("/test/", request),
                method="GET",
                interface="ApiViewTest",
                replace=True,
            )
        except Exception:
            pass

        status_bindings = {
            "ApiHealth": ("/health/", ("GET", "POST")),
            "ApiInfo": ("/info/,/Info/", ("GET", "POST")),
            "ApiInfoRes": ("/info/res/", ("GET", "POST")),
            "ApiStatus": ("/status/,/Status/", ("GET", "POST")),
            "ApiMyIp": ("/myip/", ("GET",)),
            "ApiIsOpen": ("/isopen/,/IsOpen/", ("GET",)),
            "ApiGameCenter": ("/gamecenter/", ("GET", "POST")),
            "ApiXinyueVipInfo": ("/xinyue-vipinfo/", ("GET",)),
            "ApiServerUpdateInfo": ("/server-update-info/", ("GET",)),
            "ApiCloseWebSocket": ("/close_ws/", ("GET", "POST")),
            "ApiDbAccess": ("/db-access/", ("GET",)),
            "ApiTempEntranceSwitch": ("/temp-entrance-switch/", ("GET",)),
            "ApiPlayerSyncMidasInfo": ("/syncmidas/", ("POST",)),
            "ApiPlayerASMIadInfo": ("/asmiadinfo/", ("GET", "POST")),
            "ApiManageAuthInfo": ("/ManageAuthInfo/", ("GET", "POST")),
            "ApiManageSession": ("/ManageSession/", ("POST",)),
            "ApiAppById": ("/App/<string:app_id>/", ("GET", "POST")),
            "ApiApp": ("/App/", ("GET", "POST", "PATCH")),
            "ApiClickGame": ("/ClickGame/", ("GET", "POST")),
            "ApiPlayerIdByName": ("/playeridbyname/", ("GET", "POST")),
            "ApiOpenIdByPlayerId": ("/openidbyplayerid/", ("GET", "POST")),
            "ApiModelDetailInfo": ("/models/,/models/<string:name>/,/models/<string:name>/<string:key>/", ("GET", "POST")),
            "ApiCheckPlayerData": ("/checkdata/<string:name>/<string:key>/", ("GET", "POST")),
            "ApiDirtyCheck": ("/dirtycheck/<string:content>/", ("GET", "POST")),
            "ApiRealNameVerified": ("/real-name-verified/<string:open_id>/", ("GET",)),
            "ApiCheckMailEntryExist": ("/entry-exist/email/<string:open_id>/", ("GET",)),
            "ApiCheckPhoneEntryExist": ("/entry-exist/phone/<string:open_id>/", ("GET",)),
            "ApiCheckOpenIdExist": ("/openid-exist/<string:open_id>/", ("GET",)),
        }
        for interface, (route, methods) in status_bindings.items():
            def status_handler(request: Mapping[str, Any], params: Mapping[str, str], route=route):
                # The registry passes path parameters separately; the status
                # handler only needs the route shape to choose its response.
                actual = str(request.get("path", route))
                if params:
                    for name, value in params.items():
                        actual = actual.replace("<string:" + name + ">", str(value))
                return self._local_status_contract(actual, request)
            for method in methods:
                try:
                    self.route_registry.register_path(route, status_handler, method=method, interface=interface)
                except Exception:
                    continue

        if self.accounts is None:
            return

        # Register this exact compatibility route before the generic
        # ``/AccountEntry/<string:entry>/`` binding below.  The registry keeps
        # registration order for matching, so placing the specific route
        # first prevents it from being swallowed by the generic account
        # handler (which expects a JSON body and has no BBrr migration
        # semantics).
        try:
            self.route_registry.register_path(
                "/AccountEntry/bind-bbrr-old/",
                lambda request, _params: self._local_legacy_account_bind(request),
                method="POST",
                interface="ApiAccountBbrrOldBind",
            )
        except Exception:
            pass

        bindings = {
            "ApiAccountSession": ("/AccountSession/", "/AccountSession/"),
            "ApiEntryReg": ("/EntryReg/", "/EntryReg/"),
            "ApiAccount": ("/Account/", "/Account/"),
            "ApiAuth": ("/Auth/", "/Auth/"),
            "ApiPassword": ("/Password/", "/Password/"),
            "ApiPasswordReset": ("/Password/reset/", "/PasswordReset/"),
            "ApiAccountGuestBind": ("/AccountEntry/bind/", "/AccountGuestBind/"),
        }

        for interface, (route, legacy_path) in bindings.items():
            def handler(request: Mapping[str, Any], _params: Mapping[str, str], legacy_path=legacy_path):
                return self.accounts.handle(
                    str(request.get("method", "POST")),
                    legacy_path,
                    request.get("body", b""),
                    request.get("content_type"),
                    request.get("authorization"),
                )
            try:
                self.route_registry.register_path(route, handler, method="POST", interface=interface)
            except Exception:
                # A packaged contract table may omit an interface from a newer
                # client build; keep the legacy service available in that case.
                continue

        account_compat_bindings = {
            "ApiNewEntry": ("/NewEntry/", ("POST",)),
            "ApiExistEntry": ("/exist-entry/", ("POST",)),
            "ApiVerifyCode": ("/VerifyCode/", ("POST",)),
            "ApiPasswordVerifyCode": ("/PasswordVerifyCode/", ("POST",)),
            "ApiCurrentEntryModifyVerifyCode": ("/CurrentEntryModifyVerifyCode/", ("POST",)),
            "ApiNewEntryModifyVerifyCode": ("/NewEntryModifyVerifyCode/,/exist-entry-verify-code/", ("POST",)),
            "ApiCaptcha": ("/Captcha/", ("POST",)),
            "ApiExtraInfo": ("/ExtraInfo/", ("GET",)),
            "ApiExtraInfoByAuth": ("/ExtraInfoByAuth/", ("GET",)),
            "ApiAccountEntry": ("/AccountEntry/,/AccountEntry/<string:entry>/", ("GET", "POST")),
            "ApiAccountEntryExist": ("/AccountEntry/<string:entry>/exist/", ("GET", "POST")),
            "ApiAuthInfo": ("/Auth/<string:verify_token>/", ("GET",)),
        }
        for interface, (route, methods) in account_compat_bindings.items():
            def compat_handler(request: Mapping[str, Any], _params: Mapping[str, str]):
                return self.accounts.handle(
                    str(request.get("method", "POST")),
                    str(request.get("path", route)),
                    request.get("body", b""),
                    request.get("content_type"),
                    request.get("authorization"),
                )
            for method in methods:
                try:
                    self.route_registry.register_path(route, compat_handler, method=method, interface=interface)
                except Exception:
                    continue

        if self.game is not None:
            game_bindings = {
                "ApiPlayerLogin": "/fci/login/",
                "ApiPlayerLogout": "/fci/logout/",
                "ApiPlayerBaseInfo": "/fci/baseinfo/",
                "ApiPlayerName": "/fci/name/",
                "ApiPlayerSocialPicture": "/fci/socialpicture/",
                "ApiPlayerLevel": "/fci/baseresource/level/",
                "ApiPresentTargetActor": "/fci/present-target-actor/",
                "ApiHomeBackGround": "/fci/home-back-ground/",
                "ApiChipExchange": "/fci/chipexchange/",
                "ApiGuildDungeonPlayerRecord": "/fci/guild-dungeon-record/",
            }
            for interface, route in game_bindings.items():
                def handler(request: Mapping[str, Any], _params: Mapping[str, str], route=route):
                    result = self.game.handle_http(
                        str(request.get("method", "POST")),
                        route,
                        request.get("body", b""),
                        request.get("content_type"),
                        request.get("authorization"),
                    )
                    return result if result is not None else (501, {
                        "error": "route_not_implemented", "path": route,
                    })
                try:
                    self.route_registry.register_path(route, handler, method="POST", interface=interface)
                except Exception:
                    continue

    def dispatch_contract(self, method: str, path: str, request: Mapping[str, Any]):
        """Dispatch a request through the registry when a handler is bound."""

        if not self.route_registry.has_handler(method, path):
            return None
        response = self.route_registry.dispatch(method, path, request)
        return None if response.status == 404 else response

    @staticmethod
    def _safe_sni(server_name: str | None) -> str | None:
        if not server_name:
            return None
        return str(server_name).replace("\r", "").replace("\n", "")[:255]

    def _capture_sni(self, _ssl_socket: ssl.SSLObject, server_name: str | None, _context: ssl.SSLContext) -> None:
        with self._sni_lock:
            self._pending_sni[id(_ssl_socket)] = self._safe_sni(server_name)

    def get_request(self):
        """Accept and handshake one TLS connection without wrapping the listener."""
        if not self.tls:
            return super().get_request()
        if self.tls_context is None:
            raise OSError("TLS listener has no SSL context")

        raw_socket, client_address = self.socket.accept()
        raw_socket.settimeout(10.0)
        wrapped = None
        try:
            wrapped = self.tls_context.wrap_socket(raw_socket, server_side=True, do_handshake_on_connect=False)
            wrapped.do_handshake()
            wrapped.settimeout(None)
            with self._sni_lock:
                sni = self._pending_sni.pop(id(wrapped), None)
            cipher = wrapped.cipher()
            self.audit_log.write(
                kind="tls_handshake",
                status="ok",
                tls_version=wrapped.version(),
                cipher=cipher[0] if cipher else None,
                sni=sni,
            )
            return wrapped, client_address
        except (ssl.SSLError, OSError, TimeoutError) as exc:
            key = id(wrapped) if wrapped is not None else id(raw_socket)
            with self._sni_lock:
                sni = self._pending_sni.pop(key, None)
            self.audit_log.write(kind="tls_handshake", status="error", error=type(exc).__name__,
                                 ssl_reason=getattr(exc, "reason", None), sni=sni)
            try:
                (wrapped if wrapped is not None else raw_socket).close()
            finally:
                # BaseServer catches OSError and continues accepting.
                raise OSError("TLS handshake failed") from exc


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FixtureServer

    def handle(self) -> None:
        """Treat a client closing an idle socket as a normal disconnect.

        Browsers, health probes, and emulators may tear down a keep-alive
        connection while the handler is waiting for the next request.  The
        stdlib server otherwise prints a traceback to stderr even though the
        service is healthy and no request failed.
        """
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            return

    def log_message(self, _format: str, *_args: object) -> None:
        # Request records are emitted by _handle; BaseHTTPRequestHandler's
        # default access log may include untrusted query strings.
        return

    def do_GET(self) -> None:
        self._handle(send_body=True)

    def do_HEAD(self) -> None:
        self._handle(send_body=False)

    def do_POST(self) -> None:
        self._handle(send_body=True)

    def do_PATCH(self) -> None:
        self._handle(send_body=True)

    def do_PUT(self) -> None:
        self._handle(send_body=True)

    def do_DELETE(self) -> None:
        self._handle(send_body=True)

    def _read_request_body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body exceeds 1 MiB or has invalid Content-Length")
        return self.rfile.read(length) if length else b""

    def _fixture_path(self, request_path: str) -> Path | None:
        parsed = urlsplit(request_path)
        decoded = unquote(parsed.path)
        if not decoded.startswith("/") or "\x00" in decoded:
            return None
        relative_text = decoded.lstrip("/")
        relative = Path(relative_text)
        if not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
            return None
        candidate = (self.server.fixture_root / relative).resolve()
        if decoded.endswith("/"):
            candidate = (candidate / "index.json").resolve()
        if not candidate.is_file():
            wire_candidate = Path(str(candidate) + ".wireformat").resolve()
            if wire_candidate.is_file():
                candidate = wire_candidate
        try:
            candidate.relative_to(self.server.fixture_root)
        except ValueError:
            return None
        return candidate

    @staticmethod
    def _wire_json_payload(content: bytes) -> object | None:
        """Decode the raw JSON carried by a field-4 wireformat envelope."""
        if not content or content[0] != 0x22:
            return None
        index = 1
        length = 0
        shift = 0
        while index < len(content) and shift <= 28:
            byte = content[index]
            index += 1
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        else:
            return None
        body = content[index:index + length]
        if len(body) != length:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _dynamic_fixture(self, parsed) -> tuple[bytes, str] | None:
        """Render the small directory/bootstrap fixtures from server.toml.

        The original fixture files contain loopback URLs.  Keeping their
        shape but replacing only URL values lets the same APK connect from a
        phone on the LAN without rebuilding the fixture files or embedding a
        second host-specific copy.
        """
        configured_base_url = self.server.public_base_url
        base_url = configured_base_url or f"http://{self.server.server_address[0]}:{self.server.server_port}"
        path = parsed.path.rstrip("/") or "/"
        if path == "/pr2new_djwk.json":
            if not configured_base_url:
                return None
            payload = {
                "entries": [{
                    "id": 0,
                    "locale": "zh_CN",
                    "dirServerUrls": [base_url],
                    "updateCheckUrls": [base_url],
                }],
            }
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json"

        # Version checks in the old client are harmless locally: return an
        # empty raw-json envelope so a retired CDN cannot block startup.
        # Unity's bootstrap stores the first updateCheckUrls entry and
        # appends ``clientupdate/`` to it on the next launch.  Older local
        # runs used ``/patch-version.json`` as that entry, so a client may
        # legitimately come back with ``/patch-version.json/clientupdate/``
        # even after the directory response has been corrected.  Treat this
        # legacy prefix as the same harmless local version check instead of
        # returning 501 and leaving the splash screen in a retry loop.
        version_check_paths = {
            "/clientupdate", "/clientupdate/",
            "/patch-version.json/clientupdate", "/patch-version.json/clientupdate/",
            "/app_version", "/app_version/", "/app_version/index.json",
        }
        if path in version_check_paths:
            # Preserve an explicitly supplied fixture (the test harness and
            # captured deployments may contain a real patch manifest).  The
            # compatibility response is only the fallback for a fresh local
            # runtime whose stale client URL has no matching file.
            if path.startswith("/patch-version.json/"):
                fixture = self._fixture_path(parsed.path)
                if fixture is not None and fixture.is_file():
                    return None
            # Optional hot-update override: when the server was started with
            # --force-patch, serve that file as the clientupdate PatchVersionInfo.
            # Used to drive the game's own patch channel for local Lua delivery.
            if path in {"/clientupdate", "/clientupdate/"}:
                override = getattr(self.server, "force_patch_path", None)
                try:
                    if override is not None and override.is_file():
                        # Serve as PLAIN JSON (like pr2new): ScenePatch.ProcessPatchInfo's
                        # deserializer parses protobuf-JSON directly, so the raw_json
                        # envelope (0x88 0x01 framing) would break it with U+0001.
                        return (override.read_bytes(), "application/json")
                except OSError:
                    pass
            return encode_raw_json({}), "application/wireformat"
        if path in {"/ClickGame", "/ClickGame/"}:
            return encode_raw_json({"result": 0}), "application/wireformat"
        if path not in {"/dirsvr/Dir", "/dirsvr/DirAccount"}:
            return None
        fixture = self.server.fixture_root / "dirsvr" / ("Dir.wireformat" if path.endswith("/Dir") else "DirAccount.wireformat")
        try:
            original = self._wire_json_payload(fixture.read_bytes())
        except OSError:
            original = None
        # Fresh runtime directories intentionally contain no copied capture
        # files.  Keep a small, reviewed local directory response here so the
        # original client can reach the account seam after bootstrap.  This is
        # the same shape as the checked-in analysis fixture, with all URLs
        # rewritten below to the configured local base URL.
        if not isinstance(original, dict):
            if path.endswith("/DirAccount"):
                original = {
                    "dirStat": {"stat": 0, "dirOpenTime": 0},
                    "accountUrl": base_url,
                    "accountWebUrl": base_url,
                    "accountSwitch": {
                        "qqLoginSwitch": False,
                        "wxLoginSwitch": False,
                        "guestLoginSwitch": True,
                        "wxScanCodeLoginSwitch": False,
                        "bulletinSwitch": False,
                        "customService": False,
                    },
                }
            else:
                original = {
                    "dirStat": {"stat": 0, "dirOpenTime": 0},
                    "dirWorld": {"worldInfo": {
                        "worldId": 1,
                        "worldName": "本地测试区",
                        "accountUrl": base_url,
                        "accountWebUrl": base_url,
                        "fciUrl": base_url,
                        "pushUrl": base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/push",
                        "gmUrl": base_url,
                        "matchUrl": base_url,
                    }},
                }
        if not isinstance(original, dict):
            return None

        def replace_urls(value: object) -> object:
            if isinstance(value, dict):
                return {key: replace_urls(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace_urls(item) for item in value]
            if isinstance(value, str) and (value.startswith("http://") or value.startswith("https://") or value.startswith("ws://") or value.startswith("wss://")):
                source = urlsplit(value)
                target = urlsplit(base_url)
                suffix = source.path or ""
                if source.query:
                    suffix += "?" + source.query
                if source.fragment:
                    suffix += "#" + source.fragment
                target_scheme = target.scheme.casefold() or "http"
                if source.scheme.casefold() in {"ws", "wss"}:
                    scheme = "wss" if target_scheme == "https" else "ws"
                else:
                    scheme = target_scheme
                return f"{scheme}://{target.netloc}{suffix}"
            return value

        return encode_raw_json(replace_urls(original)), "application/wireformat"

    @staticmethod
    def _query_log(parsed) -> tuple[list[str], dict[str, object]]:
        if not parsed.query:
            return [], {}
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=100)
        except ValueError:
            return [], {"_parse_error": "redacted"}
        fields: list[str] = []
        params: dict[str, object] = {}
        for raw_name, raw_value in pairs:
            name = raw_name.replace("\r", "").replace("\n", "")[:128]
            if name not in fields:
                fields.append(name)
            if name.casefold() in QUERY_VALUE_ALLOWLIST:
                value: object = raw_value.replace("\r", "").replace("\n", "")[:256]
            else:
                value = "redacted"
            if name in params:
                previous = params[name]
                if isinstance(previous, list):
                    previous.append(value)
                else:
                    params[name] = [previous, value]
            else:
                params[name] = value
        return fields, params

    @staticmethod
    def _safe_header(headers, name: str) -> str | None:
        value = headers.get(name)
        if value is None:
            return None
        return value.replace("\r", "").replace("\n", "")[:256]

    def _json_response(self, status: int, payload: dict[str, object], send_body: bool = True) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8", send_body)

    def _admin_response(self, status: int, payload: object, send_body: bool = True) -> None:
        """Serialize a loopback admin response without exposing exceptions."""

        self._last_admin_status = int(status)
        if isinstance(payload, bytes):
            self._send_bytes(status, payload, "text/html; charset=utf-8", send_body)
            return
        self._json_response(status, payload if isinstance(payload, dict) else {"result": payload}, send_body)

    def _handle_local_activity_web(self, method: str, parsed, send_body: bool) -> bool:
        """Serve a loopback-only offline activity dashboard and JSON state."""
        path = parsed.path.rstrip("/") or "/"
        if path not in {"/__local__/activity", "/__local__/activity.json"}:
            return False
        peer = str(self.client_address[0]) if self.client_address else ""
        if peer not in {"127.0.0.1", "::1"}:
            self._json_response(404, {"error": "not_found"}, send_body)
            return True
        if method not in {"GET", "HEAD"}:
            self._json_response(405, {"error": "method_not_allowed", "message": "GET required"}, send_body)
            return True
        schedule = self.server.activity_schedule
        snapshot = schedule.snapshot() if schedule is not None else {
            "now": datetime.now(timezone.utc).isoformat(), "anchor": None,
            "periodDays": None, "cycleDay": None, "active": [],
            "activities": [], "source": None,
        }
        if path == "/__local__/activity.json":
            self._json_response(200, snapshot, send_body)
            return True
        html = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>零境交错 · 本地活动</title>
<style>body{margin:0;padding:24px;background:#101522;color:#e8edf7;font-family:system-ui,"Microsoft YaHei",sans-serif}main{max-width:960px;margin:auto}.card{background:#182136;border:1px solid #2b3957;border-radius:12px;padding:16px;margin:14px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.muted{color:#9eabc2}.row{display:flex;justify-content:space-between;gap:12px;align-items:center}.pill{padding:3px 8px;border-radius:999px;background:#315f9e;font-size:12px}.off{background:#4a5364}button{border:0;border-radius:8px;padding:8px 12px;background:#3974d1;color:#fff}pre{white-space:pre-wrap;word-break:break-word;font-size:12px}</style>
<main><div class="row"><div><h1>本地活动控制台</h1><div class="muted">仅回环访问 · 无官方账号、支付或远程资源</div></div><button id="refresh">刷新</button></div>
<section id="summary" class="card">正在读取活动排期…</section><div id="activities" class="grid"></div>
<section class="card"><div class="row"><strong>调试入口</strong><a href="/__local__/admin" style="color:#8bb8ff">管理面板</a></div><p class="muted">签到、活动领取、商城和抽卡由本地游戏接口处理；此页面只展示排期状态。</p><pre id="raw"></pre></section></main>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){const summary=document.querySelector('#summary'),list=document.querySelector('#activities'),raw=document.querySelector('#raw');try{const r=await fetch('/__local__/activity.json',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();summary.innerHTML='<b>服务时间</b> '+esc(d.now)+'<div class="muted">周期 '+esc(d.periodDays)+' 天 · 当前第 '+esc(d.cycleDay)+' 天 · 活动 '+esc((d.active||[]).length)+' 个</div>';list.innerHTML=(d.activities||[]).map(a=>'<article class="card"><div class="row"><strong>'+esc(a.name||a.id||'未命名活动')+'</strong><span class="pill '+(a.active?'':'off')+'">'+(a.active?'进行中':'未开放')+'</span></div><p class="muted">类型 '+esc(a.activityType??'—')+' · ID '+esc(a.activityId??a.id??'—')+'</p><pre>'+esc(JSON.stringify(a,null,2))+'</pre></article>').join('')||'<div class="card muted">当前没有配置活动</div>';raw.textContent=JSON.stringify(d,null,2)}catch(e){summary.textContent='读取失败：'+e;list.innerHTML='';raw.textContent=''}}
document.querySelector('#refresh').addEventListener('click',load);load();
</script>""".encode("utf-8")
        self._send_bytes(200, html, "text/html; charset=utf-8", send_body)
        return True

    @staticmethod
    def _admin_json(body: bytes) -> dict[str, object]:
        if not body:
            return {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("JSON object required") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def _handle_admin(self, method: str, path: str, body: bytes, parsed, send_body: bool) -> bool:
        """Handle the local management surface.

        The caller has already checked that the peer is loopback.  This
        method returns ``True`` for every admin path, including errors, so a
        malformed admin request cannot fall through into a game fixture.
        """

        server = self.server
        if path == "/__local__/admin" and method == "GET":
            html = (
                "<!doctype html><meta charset='utf-8'><title>LJJC 本地管理</title>"
                "<h1>LJJC 本地管理</h1><p>仅回环地址可访问。</p>"
                "<ul><li><a href='/__local__/admin/status'>状态</a></li>"
                "<li><a href='/__local__/admin/accounts'>账号</a></li>"
                "<li><a href='/__local__/admin/payments'>沙盒账单</a></li>"
                "<li><a href='/__local__/admin/backups'>备份</a></li>"
                "<li><a href='/__local__/admin/crash-logs'>崩溃日志</a></li>"
                "<li><a href='/__local__/coverage'>接口覆盖率</a></li></ul>"
            ).encode("utf-8")
            self._admin_response(200, html, send_body)
            return True
        if path == "/__local__/admin" and method != "GET":
            self._admin_response(405, {"error": "method_not_allowed"}, send_body)
            return True

        if path == "/__local__/admin/status" and method == "GET":
            accounts = server.accounts.admin_list_accounts(limit=1000) if server.accounts is not None else []
            recent: list[str] = []
            log_path = getattr(server.audit_log, "path", None)
            if log_path is not None and Path(log_path).is_file():
                try:
                    recent = Path(log_path).read_text(encoding="utf-8").splitlines()[-20:]
                except OSError:
                    recent = []
            self._admin_response(200, {
                "status": "ok",
                "now": server.clock.now(),
                "clockOffsetSeconds": server.clock.offset_seconds,
                "accountCount": len(accounts),
                "stateDir": str(server.state_dir) if server.state_dir else None,
                "backupDir": str(server.backup_dir) if server.backup_dir else None,
                "crashLogCount": server.crash_logs.count() if server.crash_logs is not None else 0,
                "activitySchedule": server.activity_schedule.snapshot() if server.activity_schedule is not None else None,
                "dailyBackup": {
                    "enabled": server.daily_backup is not None,
                    "lastRun": server.daily_backup.last_run if server.daily_backup is not None else None,
                    "lastError": server.daily_backup.last_error if server.daily_backup is not None else None,
                },
                "pushSessions": server.push_service.connection_count() if hasattr(server, "push_service") else 0,
                "coverage": server.route_registry.coverage_report(),
                "recentLogLines": recent,
            }, send_body)
            return True

        if path == "/__local__/admin/accounts" and method == "GET":
            if server.accounts is None:
                self._admin_response(503, {"error": "accounts_unavailable"}, send_body)
            else:
                self._admin_response(200, {"accounts": server.accounts.admin_list_accounts()}, send_body)
            return True

        if path == "/__local__/admin/payments":
            if method != "GET":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            economy = getattr(getattr(server, "game", None), "economy", None)
            if economy is None or not hasattr(economy, "admin_billing_snapshot"):
                self._admin_response(503, {"error": "economy_unavailable", "sandbox": True}, send_body)
                return True
            limit = 200
            try:
                if parsed.query:
                    query = dict(parse_qsl(parsed.query, keep_blank_values=False, max_num_fields=20))
                    limit = max(1, min(int(query.get("limit", 200)), 1000))
            except (ValueError, TypeError):
                self._admin_response(400, {"error": "invalid_limit"}, send_body)
                return True
            self._admin_response(200, economy.admin_billing_snapshot(limit=limit), send_body)
            return True

        match = re.fullmatch(r"/__local__/admin/accounts/([0-9]+)/reset-password", path)
        if match:
            if method != "POST":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            if server.accounts is None:
                self._admin_response(503, {"error": "accounts_unavailable"}, send_body)
                return True
            try:
                payload = self._admin_json(body)
                password = payload.get("newPassword", payload.get("new_password"))
                changed = server.accounts.admin_reset_password(int(match.group(1)), password)
            except (TypeError, ValueError) as exc:
                self._admin_response(400, {"error": "invalid_password", "message": str(exc)}, send_body)
                return True
            self._admin_response(200 if changed else 404, {"ok": changed, "error": None if changed else "unknown_account"}, send_body)
            return True

        match = re.fullmatch(r"/__local__/admin/accounts/([0-9]+)/revoke-sessions", path)
        if match:
            if method != "POST":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            if server.accounts is None:
                self._admin_response(503, {"error": "accounts_unavailable"}, send_body)
                return True
            count = server.accounts.admin_revoke_sessions(int(match.group(1)))
            self._admin_response(200, {"ok": True, "revoked": count}, send_body)
            return True

        if path == "/__local__/admin/clock":
            if method == "GET":
                self._admin_response(200, {"now": server.clock.now(), "offsetSeconds": server.clock.offset_seconds}, send_body)
                return True
            if method != "POST":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            try:
                payload = self._admin_json(body)
                if "offsetSeconds" in payload:
                    server.clock.set_offset(int(payload["offsetSeconds"]))
                elif "adjustSeconds" in payload:
                    server.clock.adjust(int(payload["adjustSeconds"]))
                else:
                    raise ValueError("offsetSeconds or adjustSeconds required")
            except (TypeError, ValueError) as exc:
                self._admin_response(400, {"error": "invalid_clock_update", "message": str(exc)}, send_body)
                return True
            self._admin_response(200, {"now": server.clock.now(), "offsetSeconds": server.clock.offset_seconds}, send_body)
            return True

        if path == "/__local__/admin/backups" and method == "GET":
            backups = list_backups(server.backup_dir) if server.backup_dir else []
            self._admin_response(200, {"backups": [item.name for item in backups]}, send_body)
            return True

        if path == "/__local__/admin/backup":
            if method != "POST":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            if server.state_dir is None or server.backup_dir is None:
                self._admin_response(503, {"error": "state_unavailable"}, send_body)
                return True
            try:
                with server._admin_lock:
                    created = backup_state(server.state_dir, server.backup_dir)
                    prune_backups(server.backup_dir, server.backup_retention)
            except (OSError, ValueError) as exc:
                self._admin_response(500, {"error": "backup_failed", "message": str(exc)}, send_body)
                return True
            self._admin_response(200, {"ok": True, "backup": created.name}, send_body)
            return True

        if path == "/__local__/admin/restore":
            if method != "POST":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            if server.state_dir is None or server.backup_dir is None:
                self._admin_response(503, {"error": "state_unavailable"}, send_body)
                return True
            try:
                payload = self._admin_json(body)
                name = str(payload.get("backup", ""))
                if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", name):
                    raise ValueError("invalid backup name")
                source_root = (server.backup_dir / name).resolve()
                source_root.relative_to(server.backup_dir.resolve())
                if not source_root.is_dir():
                    raise FileNotFoundError(source_root)
                restored: list[str] = []
                with server._admin_lock:
                    for db_name in ("accounts.sqlite3", "game.sqlite3", "economy.sqlite3", "local.sqlite3"):
                        source = source_root / db_name
                        if not source.is_file():
                            continue
                        destination = server.state_dir / db_name
                        restore_sqlite(source, destination)
                        for suffix in ("-wal", "-shm"):
                            Path(str(destination) + suffix).unlink(missing_ok=True)
                        restored.append(db_name)
            except FileNotFoundError:
                self._admin_response(404, {"error": "backup_not_found"}, send_body)
                return True
            except (OSError, ValueError) as exc:
                self._admin_response(400, {"error": "restore_failed", "message": str(exc)}, send_body)
                return True
            self._admin_response(200, {"ok": True, "restored": restored, "restartRecommended": True}, send_body)
            return True

        if path == "/__local__/admin/logs" and method == "GET":
            limit = 100
            try:
                if parsed.query:
                    query = dict(parse_qsl(parsed.query, keep_blank_values=False, max_num_fields=20))
                    limit = max(1, min(int(query.get("limit", 100)), 500))
            except (ValueError, TypeError):
                self._admin_response(400, {"error": "invalid_limit"}, send_body)
                return True
            log_path = getattr(server.audit_log, "path", None)
            lines: list[str] = []
            if log_path is not None and Path(log_path).is_file():
                try:
                    lines = Path(log_path).read_text(encoding="utf-8").splitlines()[-limit:]
                except OSError:
                    lines = []
            self._admin_response(200, {"lines": lines}, send_body)
            return True

        if path == "/__local__/admin/crash-logs" and method == "GET":
            limit = 100
            try:
                if parsed.query:
                    query = dict(parse_qsl(parsed.query, keep_blank_values=False, max_num_fields=20))
                    limit = max(1, min(int(query.get("limit", 100)), 500))
            except (ValueError, TypeError):
                self._admin_response(400, {"error": "invalid_limit"}, send_body)
                return True
            reports = server.crash_logs.list_recent(limit) if server.crash_logs is not None else []
            self._admin_response(200, {"reports": reports}, send_body)
            return True

        # Activity CRUD is intentionally restricted to the loopback admin
        # surface.  The scheduler validates IDs and bounded cycle windows;
        # this endpoint only edits local phase metadata and never reaches a
        # remote service.
        if path == "/__local__/admin/activities":
            if server.activity_schedule is None:
                self._admin_response(503, {"error": "activity_schedule_unavailable"}, send_body)
                return True
            if method == "GET":
                self._admin_response(200, {"activities": server.activity_schedule.activities()}, send_body)
                return True
            if method != "POST":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            try:
                value = server.activity_schedule.upsert_activity(self._admin_json(body))
            except (TypeError, ValueError) as exc:
                self._admin_response(400, {"error": "invalid_activity", "message": str(exc)}, send_body)
                return True
            self._admin_response(200, {"ok": True, "activity": value}, send_body)
            return True

        match = re.fullmatch(r"/__local__/admin/activities/([^/]+)", path)
        if match:
            if server.activity_schedule is None:
                self._admin_response(503, {"error": "activity_schedule_unavailable"}, send_body)
                return True
            activity_id = unquote(match.group(1))
            if method == "DELETE":
                removed = server.activity_schedule.delete_activity(activity_id)
                self._admin_response(200 if removed else 404, {"ok": removed, "activityId": activity_id}, send_body)
                return True
            if method != "PATCH":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            try:
                updated = server.activity_schedule.patch_activity(activity_id, self._admin_json(body))
            except (TypeError, ValueError) as exc:
                self._admin_response(400, {"error": "invalid_activity", "message": str(exc)}, send_body)
                return True
            self._admin_response(200 if updated is not None else 404, {"ok": updated is not None, "activity": updated}, send_body)
            return True

        if path == "/__local__/admin/activity-schedule":
            if server.activity_schedule is None:
                self._admin_response(503, {"error": "activity_schedule_unavailable"}, send_body)
                return True
            if method == "GET":
                self._admin_response(200, server.activity_schedule.snapshot(), send_body)
                return True
            if method != "POST":
                self._admin_response(405, {"error": "method_not_allowed"}, send_body)
                return True
            try:
                payload = self._admin_json(body)
                anchor = payload.get("anchor", payload.get("anchorTimestamp"))
                if anchor is None:
                    raise ValueError("anchor or anchorTimestamp required")
                if isinstance(anchor, (int, float)):
                    anchor = datetime.fromtimestamp(float(anchor), timezone.utc)
                updated = server.activity_schedule.set_anchor(str(anchor) if not isinstance(anchor, datetime) else anchor)
            except (TypeError, ValueError) as exc:
                self._admin_response(400, {"error": "invalid_activity_schedule", "message": str(exc)}, send_body)
                return True
            self._admin_response(200, {"ok": True, "anchor": updated.isoformat(), "schedule": server.activity_schedule.snapshot()}, send_body)
            return True

        self._admin_response(404, {"error": "admin_route_not_found"}, send_body)
        return True

    def _publish_game_event(self, path: str, authorization: str | None, payload: object) -> None:
        """Notify an online local client after a successful mutating request."""

        if self.server.accounts is None or not authorization:
            return
        parts = authorization.strip().split(None, 1)
        if len(parts) != 2:
            return
        account = self.server.accounts.verify_access_token(parts[1].strip())
        if account is None:
            return
        route = urlsplit(path).path.rstrip("/").casefold()
        summary = {"route": route, "ok": True}
        if isinstance(payload, dict):
            # Keep push payloads bounded and JSON-safe.  The normal HTTP
            # response remains authoritative for the full typed result.
            for key in ("resourceChanges", "rewardTbl", "transactionId", "sandbox", "result"):
                if key in payload and isinstance(payload[key], (str, int, float, bool, list, dict, type(None))):
                    summary[key] = payload[key]
        try:
            if "purchasedelevery" in route:
                self.server.push_service.payment(account, summary)
            elif "/mail" in route or "/notice" in route:
                self.server.push_service.mail(account, summary)
            elif self.command in {"POST", "PATCH", "PUT", "DELETE"}:
                self.server.push_service.red_point(account, summary)
        except Exception:
            # A disconnected optional push client must not turn a successful
            # local HTTP mutation into a failed request.
            logging.debug("local push notification failed", exc_info=True)

    def _send_bytes(self, status: int, body: bytes, content_type: str, send_body: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    @staticmethod
    def _is_websocket_path(path: str) -> bool:
        return urlsplit(path or "").path.rstrip("/") == "/push"

    def _handle_unexpected_exception(self, send_body: bool, exc: BaseException) -> None:
        """Return a safe HTTP error and retain a minimal diagnostic record.

        Handler exceptions must not expose request headers, authorization
        values, or body contents.  WebSocket handling is deliberately kept
        outside this recovery path because a peer closing a WS normally may
        raise a connection exception that the WS transport owns.
        """

        method = str(getattr(self, "command", "UNKNOWN"))[:32]
        raw_path = str(getattr(self, "path", "/"))
        try:
            parsed = urlsplit(raw_path)
            path = parsed.path[:2048] or "/"
        except ValueError:
            path = "/<invalid-request-path>"
        record = {
            "kind": "request",
            "tls": bool(self.server.tls),
            "method": method,
            "path": path,
            "status": 500,
            "error": "handler_exception",
            "exception": type(exc).__name__,
        }
        try:
            self.server.audit_log.write(**record)
        except Exception:
            # A failing audit sink must not hide the original handler failure.
            logging.exception("failed to write handler exception audit record")
        try:
            self._json_response(
                500,
                {"error": "internal_server_error", "message": "request_handler_failed"},
                send_body,
            )
        except Exception:
            # The peer may already have gone away; the audit record above is
            # still useful and there is no safe response left to send.
            self.close_connection = True

    def _handle(self, send_body: bool) -> None:
        # Do not wrap the WebSocket transport in HTTP recovery.  Its normal
        # close/reset exceptions are part of the protocol's connection life
        # cycle and must remain visible to handle_upgrade.
        if self._is_websocket_path(getattr(self, "path", "")) and self.server.accounts is not None:
            self._handle_impl(send_body)
            return
        try:
            self._handle_impl(send_body)
        except Exception as exc:
            self._handle_unexpected_exception(send_body, exc)

    def _handle_impl(self, send_body: bool) -> None:
        method = self.command
        parsed = urlsplit(self.path)
        request_body = b""
        body_error: str | None = None
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                request_body = self._read_request_body()
            except ValueError as exc:
                body_error = str(exc)

        host = self.headers.get("Host", "")[:255]
        body_hash = hashlib.sha256(request_body).hexdigest() if request_body else None
        query_fields, query_params = self._query_log(parsed)
        base_record: dict[str, object] = {
            "kind": "request",
            "tls": bool(self.server.tls),
            "method": method,
            "host": host,
            "path": parsed.path,
            "query_present": bool(parsed.query),
            "query_fields": query_fields,
            "query_params": query_params,
            "content_type": self._safe_header(self.headers, "Content-Type"),
            "content_encoding": self._safe_header(self.headers, "Content-Encoding"),
            "accept": self._safe_header(self.headers, "Accept"),
            "request_body_bytes": len(request_body),
            "request_body_sha256": body_hash,
        }
        if parsed.path == DIR_ACCOUNT_PATH and len(request_body) <= DIR_ACCOUNT_BODY_HEX_MAX:
            base_record["request_body_hex"] = request_body.hex()

        if body_error is not None:
            self._json_response(413, {"error": "request_body_rejected", "detail": body_error}, send_body)
            self.server.audit_log.write(**base_record, status=413)
            return

        try:
            payload_body = decode_request_body(request_body, self.headers.get('Content-Encoding'))
        except ValueError as exc:
            self._json_response(400, {'error':'invalid_request_encoding','message':str(exc)}, send_body)
            self.server.audit_log.write(**base_record, status=400, error='invalid_request_encoding')
            return
        base_record['decoded_body_bytes'] = len(payload_body)

        # Bugly is intentionally replaced with a local, bounded diagnostic
        # sink.  Keep this endpoint outside the admin namespace so a mobile
        # client on the LAN can report a crash without loopback access.
        if parsed.path.rstrip("/").casefold() in {
            "/__local__/crash-log",
            "/fci/crash-log",
            "/bugly/crash-report",
        }:
            if method != "POST":
                self._json_response(405, {"error": "method_not_allowed", "message": "POST required"}, send_body)
                self.server.audit_log.write(**base_record, status=405, handler="local_crash_log")
                return
            if self.server.crash_logs is None:
                self._json_response(503, {"error": "crash_log_unavailable"}, send_body)
                self.server.audit_log.write(**base_record, status=503, handler="local_crash_log")
                return
            account_id = None
            authorization = self.headers.get("Authorization")
            if authorization and self.server.accounts is not None:
                parts = authorization.strip().split(None, 1)
                if len(parts) == 2:
                    account = self.server.accounts.verify_access_token(parts[1].strip())
                    if account is not None:
                        account_id = int(account.get("account_id", 0)) or None
            try:
                report = self.server.crash_logs.write(
                    payload_body,
                    content_type=self.headers.get("Content-Type"),
                    path=parsed.path,
                    account_id=account_id,
                )
            except ValueError as exc:
                self._json_response(413, {"error": "crash_report_rejected", "message": str(exc)}, send_body)
                self.server.audit_log.write(**base_record, status=413, handler="local_crash_log")
                return
            self._json_response(200, {"ok": True, "report": report}, send_body)
            self.server.audit_log.write(**base_record, status=200, handler="local_crash_log", report_id=report.get("id"))
            return

        if parsed.path == "/__local__/health":
            payload = {"status": "ok", "service": "ljjc-fixture-server", "build": "m3.7-admin", "tls": bool(self.server.tls)}
            self._json_response(200, payload, send_body)
            self.server.audit_log.write(**base_record, status=200)
            return

        if self._handle_local_activity_web(method, parsed, send_body):
            self.server.audit_log.write(**base_record, status=getattr(self, "_last_admin_status", 200), handler="local_activity_web")
            return

        if parsed.path.startswith("/__local__/admin"):
            peer = str(self.client_address[0]) if self.client_address else ""
            if peer not in {"127.0.0.1", "::1"}:
                self._json_response(404, {"error": "not_found"}, send_body)
                self.server.audit_log.write(**base_record, status=404, handler="local_admin_loopback_only")
                return
            self._handle_admin(method, parsed.path.rstrip("/") or "/__local__/admin", payload_body, parsed, send_body)
            self.server.audit_log.write(**base_record, status=getattr(self, "_last_admin_status", 200), handler="local_admin")
            return

        if parsed.path in {"/__local__/coverage", "/__local__/route-table"}:
            # Management diagnostics are intentionally loopback-only even when
            # the gameplay listener is configured on a LAN address.
            peer = str(self.client_address[0]) if self.client_address else ""
            if peer not in {"127.0.0.1", "::1"}:
                self._json_response(404, {"error": "not_found"}, send_body)
                self.server.audit_log.write(**base_record, status=404, handler="local_admin_loopback_only")
                return
            payload = (self.server.route_registry.coverage_report()
                       if parsed.path.endswith("coverage")
                       else {"routes": self.server.route_registry.route_table()})
            self._json_response(200, payload, send_body)
            self.server.audit_log.write(**base_record, status=200, handler="local_admin")
            return

        if parsed.path.rstrip('/') == '/push' and self.server.accounts is not None:
            self.protocol_version = 'HTTP/1.1'
            self.close_connection = True
            self.server.audit_log.write(**{**base_record,'kind':'websocket_upgrade_attempt'})
            def initialize_game(account, login_fields, connection):
                if self.server.game is not None:
                    for name, data in self.server.game.login_messages(account):
                        connection.send_sc(connection.last_client_sequence, name, data)
            # RFC 6455 requires client frames to be masked, but the original
            # FCICV client's push transport sends UNMASKED binary frames (its
            # CsLogin was observed rejected with "client frame is not masked",
            # which reset the client before it could enter the city).  This is
            # the legacy-client case push.handle_upgrade reserves the opt-in
            # for.  This local service is a personal loopback/LAN preservation
            # server, so accept the legacy client's unmasked frames (masked
            # frames still work); it never faces untrusted proxies.
            handle_upgrade(self, self.server.accounts, self.server.audit_log,
                           initializer=initialize_game, timeout=90,
                           push_service=self.server.push_service,
                           clock=self.server.clock.now,
                           allow_unmasked_local=True)
            return

        contract_response = self.server.dispatch_contract(method, parsed.path, {
            "method": method,
            "path": parsed.path,
            "body": payload_body,
            "content_type": self.headers.get("Content-Type"),
            "authorization": self.headers.get("Authorization"),
        })
        if contract_response is not None:
            status, payload = contract_response.status, contract_response.payload
            if status == 200:
                self._send_bytes(status, encode_response(payload), "application/wireformat", send_body)
            else:
                self._json_response(status, payload if isinstance(payload, dict) else {"result": payload}, send_body)
            if status == 200:
                self._publish_game_event(parsed.path, self.headers.get("Authorization"), payload)
            self.server.audit_log.write(**base_record, status=status, handler="contract_registry")
            return

        if self.server.accounts is not None:
            result = self.server.accounts.handle(method, parsed.path, payload_body, self.headers.get('Content-Type'), self.headers.get('Authorization'))
            if result is not None:
                status, payload = result
                if status == 200:
                    self._send_bytes(status, encode_response(payload), 'application/wireformat', send_body)
                else:
                    self._json_response(status, payload, send_body)
                self.server.audit_log.write(**base_record, status=status, handler='local_guest_accounts', handler_error=payload.get('error') if status >= 400 else None, handler_error_message=payload.get('message') if status >= 400 else None)
                return

        if self.server.game is not None and parsed.path.startswith('/fci/'):
            result = self.server.game.handle_http(method, parsed.path, payload_body,
                self.headers.get('Content-Type'), self.headers.get('Authorization'))
            if result is not None:
                status, payload = result
                if status == 200:
                    if (method == 'POST' and parsed.path.rstrip('/').endswith('/balance')):
                        payload = _balance_wire_compat(payload)
                    self._send_bytes(status, encode_response(payload), 'application/wireformat', send_body)
                else:
                    self._json_response(status, payload, send_body)
                if status == 200:
                    self._publish_game_event(parsed.path, self.headers.get('Authorization'), payload)
                self.server.audit_log.write(**base_record, status=status, handler='local_game')
                return

        dynamic = self._dynamic_fixture(parsed)
        if dynamic is not None:
            content, content_type = dynamic
            self._send_bytes(200, content, content_type, send_body)
            self.server.audit_log.write(**base_record, status=200, fixture="<dynamic>")
            return

        fixture = self._fixture_path(self.path)
        if fixture is not None and fixture.is_file():
            try:
                content = fixture.read_bytes()
            except OSError as exc:
                self._json_response(500, {"error": "fixture_read_failed"}, send_body)
                self.server.audit_log.write(**base_record, status=500, fixture=str(fixture), error=type(exc).__name__)
                return
            content_type = "application/wireformat" if fixture.suffix == ".wireformat" else (mimetypes.guess_type(fixture.name)[0] or "application/octet-stream")
            self._send_bytes(200, content, content_type, send_body)
            self.server.audit_log.write(**base_record, status=200, fixture=str(fixture.relative_to(self.server.fixture_root)))
            return

        self._json_response(501, {"error": "route_not_implemented", "method": method, "path": parsed.path}, send_body)
        self.server.audit_log.write(**base_record, status=501, error="route_not_implemented")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local LJJC fixture/diagnostic server")
    parser.add_argument("--config", type=Path, help="optional TOML server configuration")
    parser.add_argument("--fixture-root", type=Path, required=True, help="directory whose files map to URL paths")
    parser.add_argument("--log", type=Path, required=True, help="JSONL audit log path")
    parser.add_argument("--state-dir", type=Path, help="local guest database directory; defaults to fixtures sibling state")
    parser.add_argument(
        "--unified-store-path",
        type=Path,
        help="shared SQLite path for accounts, game and economy; defaults to state-dir/local.sqlite3",
    )
    parser.add_argument("--backup-dir", type=Path, help="directory for timestamped state backups")
    parser.add_argument("--no-auto-backup", action="store_true", help="do not create a state backup before startup")
    parser.add_argument("--backup-retention", type=int, default=7, help="number of startup/admin backups to retain")
    parser.add_argument("--bind-host", default="127.0.0.1", help="address for HTTP and TLS listeners")
    parser.add_argument("--http-port", type=int, default=19080)
    parser.add_argument("--tls-port", type=int, default=19102)
    parser.add_argument("--cert", type=Path, help="PEM certificate for the TLS listener")
    parser.add_argument("--key", type=Path, help="PEM private key for the TLS listener")
    parser.add_argument("--no-tls", action="store_true", help="start only HTTP, useful for unit tests")
    parser.add_argument(
        "--tls-only",
        action="store_true",
        help="bind only the TLS listener; requires --cert/--key and an https public_base_url",
    )
    parser.add_argument(
        "--legacy-tls",
        action="store_true",
        help="allow TLSv1 and OpenSSL security level 0 for old Unity clients; disabled by default",
    )
    parser.add_argument(
        "--force-patch",
        type=Path,
        help="serve this PatchVersionInfo JSON from /clientupdate/ to drive the client's hot-update channel",
    )
    return parser


def _load_server_config(path: Path | None) -> dict[str, object]:
    if path is None:
        return {}
    try:
        # Windows PowerShell 5.1's ``Set-Content -Encoding UTF8`` emits a
        # UTF-8 BOM.  Decode with ``utf-8-sig`` so a generated LAN config is
        # accepted alongside the repository's BOM-free server.toml.
        raw = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
    except FileNotFoundError as exc:
        raise SystemExit(f"server config does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"invalid server TOML {path}: {exc}") from exc
    section = raw.get("server", {})
    if not isinstance(section, dict):
        raise SystemExit("server.toml [server] must be a table")
    return dict(section)


def _config_value(config: dict[str, object], key: str, fallback: object) -> object:
    value = config.get(key, fallback)
    if isinstance(value, str):
        value = value.strip()
    return value


def _config_bool(config: dict[str, object], key: str, fallback: bool) -> bool:
    value = config.get(key, fallback)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _is_local_address(value: str) -> bool:
    """Return whether a host literal is safe for the local/LAN server.

    The offline server must never advertise or bind a public endpoint by
    accident.  Loopback, private, link-local and unspecified literals are
    useful for local tests and LAN listeners; public addresses are rejected.
    Hostnames are deliberately limited to ``localhost`` because resolving an
    arbitrary name would make the local-only guarantee depend on DNS.
    """

    host = str(value or "").strip().casefold().rstrip(".")
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_unspecified
    )


def _validate_local_network_config(bind_host: str, public_base_url: str) -> None:
    """Reject public bind/advertised endpoints before starting the server."""

    if not _is_local_address(bind_host):
        raise SystemExit(
            "bind_host must be loopback, private/link-local, wildcard, or localhost; "
            "public/DNS bind targets are refused"
        )
    try:
        parsed = urlsplit(str(public_base_url).strip())
        hostname = parsed.hostname
        parsed.port  # force validation of malformed ports
    except ValueError as exc:
        raise SystemExit(f"invalid server.public_base_url: {exc}") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not hostname:
        raise SystemExit("server.public_base_url must be an http:// or https:// local URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("server.public_base_url cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise SystemExit("server.public_base_url must not contain a path")
    if not _is_local_address(hostname) and hostname.casefold().rstrip(".") != str(bind_host).strip().casefold().rstrip("."):
        raise SystemExit("server.public_base_url must point to a local/LAN host")


def serve(args: argparse.Namespace) -> None:
    config = _load_server_config(args.config)
    fixture_root = args.fixture_root.resolve()
    if not fixture_root.is_dir():
        raise SystemExit(f"fixture root does not exist or is not a directory: {fixture_root}")
    bind_host = str(_config_value(config, "bind_host", args.bind_host))
    http_port = int(_config_value(config, "http_port", args.http_port))
    tls_port = int(_config_value(config, "tls_port", args.tls_port))
    if args.tls_only and args.no_tls:
        raise SystemExit("--tls-only cannot be combined with --no-tls")
    enable_tls = True if args.tls_only else (False if args.no_tls else _config_bool(config, "enable_tls", True))
    legacy_tls = _config_bool(config, "legacy_tls", args.legacy_tls)
    public_base_url = str(_config_value(config, "public_base_url", f"http://{bind_host}:{http_port}"))
    _validate_local_network_config(bind_host, public_base_url)
    if args.tls_only and not public_base_url.casefold().startswith("https://"):
        raise SystemExit("--tls-only requires server.public_base_url to use https://")
    audit_log = JsonlLogger(args.log.resolve())
    state_dir = args.state_dir or fixture_root.parent / 'state'
    configured_store_path = _config_value(config, "unified_store_path", None)
    command_store_path = getattr(args, "unified_store_path", None)
    requested_store_path = command_store_path if command_store_path is not None else configured_store_path
    unified_store_path = resolve_unified_store_path(state_dir, requested_store_path or (Path("local.sqlite3")))
    ensure_unified_store(unified_store_path)
    time_offset_seconds = int(_config_value(config, "time_offset_seconds", 0))
    server_clock = ServerClock(offset_seconds=time_offset_seconds)
    accounts = LocalAccounts(state_dir, clock=server_clock.now, unified_store_path=unified_store_path)
    game = LocalGame(state_dir, accounts, clock=server_clock.now, unified_store_path=unified_store_path)
    # Event claims use the extracted local daily reset (06:00 UTC) while
    # direct LocalGame tests retain their historical midnight fallback.
    game.daily_key = lambda value=None: server_clock.daily_key(value, refresh_at="06:00:00")
    schedule_source = Path(__file__).resolve().parent / "data" / "server-only" / "activity-schedule.yaml"
    activity_schedule = ActivitySchedule(
        schedule_source,
        state_path=state_dir / "activity-schedule.json",
        clock=server_clock.now,
    ) if schedule_source.is_file() else None
    if activity_schedule is not None:
        # LocalEvents reads this optional service when constructing the city
        # activity entry; keeping it as a late-bound attribute preserves the
        # legacy LocalGame constructor used by unit tests.
        game.activity_schedule = activity_schedule
    configured_backup_dir = _config_value(config, "backup_dir", None)
    backup_dir = Path(configured_backup_dir) if configured_backup_dir else (args.backup_dir or state_dir / "backups")
    if not backup_dir.is_absolute() and args.config is not None:
        backup_dir = (args.config.resolve().parent / backup_dir).resolve()
    auto_backup = _config_bool(config, "auto_backup", not args.no_auto_backup)
    if auto_backup:
        try:
            created = backup_state(state_dir, backup_dir)
            prune_backups(backup_dir, max(1, int(_config_value(config, "backup_retention", args.backup_retention))))
            logging.info("created startup state backup at %s", created)
        except FileExistsError:
            logging.info("startup state backup already exists for the current second")
        except (OSError, ValueError) as exc:
            logging.warning("startup state backup failed: %s", exc)
    http_server: FixtureServer | None = None
    if not args.tls_only:
        http_server = FixtureServer((bind_host, http_port), fixture_root, audit_log, tls=False,
                                    accounts=accounts, game=game, public_base_url=public_base_url,
                                    clock=server_clock, state_dir=state_dir, backup_dir=backup_dir,
                                    backup_retention=int(_config_value(config, "backup_retention", args.backup_retention)),
                                    activity_schedule=activity_schedule)
    tls_server: FixtureServer | None = None
    threads: list[threading.Thread] = []
    if enable_tls:
        if args.cert is None or args.key is None:
            raise SystemExit("TLS is enabled but --cert and --key were not provided")
        tls_server = FixtureServer((bind_host, tls_port), fixture_root, audit_log, tls=True,
                                   accounts=accounts, game=game, public_base_url=public_base_url,
                                   clock=server_clock, state_dir=state_dir, backup_dir=backup_dir,
                                   backup_retention=int(_config_value(config, "backup_retention", args.backup_retention)),
                                   activity_schedule=activity_schedule)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        if legacy_tls:
            context.minimum_version = ssl.TLSVersion.TLSv1
            # Old Unity clients (this game's bootstrap) speak TLS 1.0 with legacy
            # ciphers; DEFAULT omits some of them even at SECLEVEL 0, so use ALL.
            context.set_ciphers("ALL:@SECLEVEL=0")
        context.set_servername_callback(tls_server._capture_sni)
        context.load_cert_chain(certfile=args.cert, keyfile=args.key)
        tls_server.tls_context = context

    for listener in (http_server, tls_server):
        if listener is not None:
            listener.force_patch_path = args.force_patch.resolve() if args.force_patch else None

    daily_backup: DailyBackupScheduler | None = None
    if auto_backup:
        daily_backup = DailyBackupScheduler(
            state_dir,
            backup_dir,
            retention=int(_config_value(config, "backup_retention", args.backup_retention)),
            # The worker only needs to notice a new day.  A one-minute tick
            # keeps the service responsive after an admin clock adjustment.
            interval_seconds=float(_config_value(config, "backup_check_interval_seconds", 60.0)),
            clock=server_clock.now,
            lock=(http_server or tls_server)._admin_lock,
        )
        daily_backup.start()
        if http_server is not None:
            http_server.daily_backup = daily_backup
        if tls_server is not None:
            tls_server.daily_backup = daily_backup

    for server in (http_server, tls_server):
        if server is None:
            continue
        thread = threading.Thread(target=server.serve_forever, name=f"listener-{server.server_port}", daemon=True)
        thread.start()
        threads.append(thread)
        logging.info("listening on %s://%s:%d fixture_root=%s", "https" if server.tls else "http", bind_host, server.server_port, fixture_root)

    audit_log.write(kind="server_started", bind_host=bind_host, public_base_url=public_base_url,
                    http_port=None if args.tls_only else http_port,
                    tls_port=None if not enable_tls else tls_port)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logging.info("stopping")
    finally:
        if daily_backup is not None:
            daily_backup.stop()
        if http_server is not None:
            http_server.shutdown()
            http_server.server_close()
        if tls_server is not None:
            tls_server.shutdown()
            tls_server.server_close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(build_parser().parse_args())


if __name__ == "__main__":
    main()
