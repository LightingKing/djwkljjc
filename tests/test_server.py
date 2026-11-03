from __future__ import annotations

import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection, HTTPSConnection

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import (FixtureHandler, FixtureServer, JsonlLogger, _is_local_address,
                    _load_server_config, _validate_local_network_config, build_parser,
                    encode_raw_json)  # noqa: E402
from accounts import LocalAccounts
import lz4.frame


class FixtureServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "pr2new_djwk.json").write_text('{"version":"test","servers":[]}', encoding="utf-8")
        clientupdate = self.root / "patch-version.json" / "clientupdate"
        clientupdate.mkdir(parents=True)
        (clientupdate / "index.json").write_text('{"patch":"test"}', encoding="utf-8")
        self.log_path = self.root / "requests.jsonl"
        self.server = FixtureServer(("127.0.0.1", 0), self.root, JsonlLogger(self.log_path))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def test_patch_manifest_filters_versions_and_rejects_incompatible_apk(self):
        manifest = {"version": "2.0.4.59162.0", "history": [
            {"version": 5, "length": 100, "checksum": 123, "urls": ["http://127.0.0.1/p.zip"]}]}
        path = self.root / "candidate.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        self.server.force_patch_path = path
        # setUp leaves a captured fixture at the legacy URL. An explicit
        # override must win there as well as at the current update URL.
        for route in ("/clientupdate", "/clientupdate/", "/patch-version.json/clientupdate", "/patch-version.json/clientupdate/"):
            for version in range(7):
                with self.subTest(route=route, version=version):
                    status, body = self.request("GET", f"{route}?version=2.0.4.59162.{version}")
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)["history"], manifest["history"] if version < 5 else [])
            status, body = self.request("GET", f"{route}?version=2.0.3.59162.0")
            self.assertEqual(status, 409)
            self.assertIn("error", json.loads(body))
            status, body = self.request("GET", f"{route}?version=broken")
            self.assertEqual(status, 400)

    def test_legacy_patch_fixture_is_preserved_without_override(self):
        self.assertIsNone(getattr(self.server, "force_patch_path", None))
        status, body = self.request("GET", "/patch-version.json/clientupdate/?version=2.0.4.59162.4")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"patch": "test"})

    def test_tls_only_is_an_explicit_listener_mode(self) -> None:
        args = build_parser().parse_args([
            "--fixture-root", str(self.root),
            "--log", str(self.log_path),
            "--tls-only",
        ])
        self.assertTrue(args.tls_only)
        invalid = build_parser().parse_args([
            "--fixture-root", str(self.root),
            "--log", str(self.log_path),
            "--tls-only", "--no-tls",
        ])
        from server import serve
        with self.assertRaises(SystemExit):
            serve(invalid)

    def test_server_config_accepts_windows_utf8_bom(self) -> None:
        config = self.root / "bom.toml"
        config.write_bytes("[server]\npublic_base_url = \"https://lan\"\n".encode("utf-8-sig"))
        loaded = _load_server_config(config)
        self.assertEqual(loaded["public_base_url"], "https://lan")

    def test_local_network_config_rejects_public_endpoints(self) -> None:
        self.assertTrue(_is_local_address("127.0.0.1"))
        self.assertTrue(_is_local_address("192.168.1.20"))
        self.assertTrue(_is_local_address("::1"))
        self.assertFalse(_is_local_address("8.8.8.8"))
        self.assertFalse(_is_local_address("example.com"))
        with self.assertRaises(SystemExit):
            _validate_local_network_config("127.0.0.1", "https://example.com:443")
        with self.assertRaises(SystemExit):
            _validate_local_network_config("8.8.8.8", "https://8.8.8.8:443")
        _validate_local_network_config("192.168.1.20", "https://192.168.1.20:19443")

    def test_malformed_host_header_falls_back_without_exception(self) -> None:
        self.server.public_base_url = "http://192.168.1.20:19080"
        self.assertEqual(
            self.server.request_base_url("[malformed"),
            "http://192.168.1.20:19080",
        )

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_fixture_get_and_head(self) -> None:
        status, body = self.request("GET", "/pr2new_djwk.json?x=1")
        self.assertEqual(status, 200)
        self.assertIn(b'"version":"test"', body)
        status, body = self.request("HEAD", "/pr2new_djwk.json")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_dynamic_bootstrap_rewrites_directory_urls(self) -> None:
        self.server.public_base_url = "http://192.168.1.20:19080"
        status, body = self.request("GET", "/pr2new_djwk.json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["entries"][0]["dirServerUrls"], ["http://192.168.1.20:19080"])
        directory = self.root / "dirsvr"
        directory.mkdir()
        (directory / "DirAccount.wireformat").write_bytes(
            encode_raw_json({"accountUrl": "http://127.0.0.1:19080", "pushUrl": "ws://127.0.0.1:19080/push"})
        )
        status, body = self.request("GET", "/dirsvr/DirAccount")
        self.assertEqual(status, 200)
        self.assertIn(b"http://192.168.1.20:19080", body)
        self.assertNotIn(b"http://127.0.0.1:19080", body)

    def test_request_host_selects_loopback_or_private_base_and_public_host_falls_back(self) -> None:
        self.server.public_base_url = "http://192.168.1.22:%d" % self.server.server_port
        for host, expected in (
            ("127.0.0.1:0", "http://192.168.1.22:%d" % self.server.server_port),
            ("127.0.0.1:%d" % self.server.server_port, "http://127.0.0.1:%d" % self.server.server_port),
            ("192.168.50.7:%d" % self.server.server_port, "http://192.168.50.7:%d" % self.server.server_port),
            ("8.8.8.8:%d" % self.server.server_port, "http://192.168.1.22:%d" % self.server.server_port),
        ):
            with self.subTest(host=host):
                status, body = self.request("GET", "/pr2new_djwk.json", headers={"Host": host})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["entries"][0]["dirServerUrls"], [expected])
                status, body = self.request("GET", "/DirAccount/", headers={"Host": host})
                self.assertEqual(status, 200)
                payload = FixtureHandler._wire_json_payload(body)
                self.assertEqual(payload["accountUrl"], expected)

    def test_request_host_rewrites_team_dungeon_url_without_fixed_loopback(self) -> None:
        from game import LocalGame
        from test_systems import LocalSystemsTests

        accounts = LocalAccounts(self.root / "state")
        game = LocalGame(self.root / "game", accounts)
        _account, auth = LocalSystemsTests._guest(accounts)
        self.server.accounts, self.server.game = accounts, game
        self.server.public_base_url = "http://192.168.1.22:%d" % self.server.server_port
        headers = {"Authorization": auth, "Host": "192.168.50.7:%d" % self.server.server_port}
        status, body = self.request("GET", "/fci/teamdungeonurl/", headers=headers)
        self.assertEqual(status, 200)
        payload = FixtureHandler._wire_json_payload(body)
        self.assertEqual(payload["url"], "ws://192.168.50.7:%d/fci/teamdungeon/" % self.server.server_port)

    def test_dynamic_bootstrap_preserves_secure_websocket_scheme(self) -> None:
        self.server.public_base_url = "https://192.168.1.20:19443"
        directory = self.root / "dirsvr"
        directory.mkdir()
        (directory / "DirAccount.wireformat").write_bytes(
            encode_raw_json({
                "accountUrl": "http://127.0.0.1:19080",
                "pushUrl": "wss://127.0.0.1:19080/push?transport=tls",
            })
        )
        status, body = self.request("GET", "/dirsvr/DirAccount")
        self.assertEqual(status, 200)
        payload = FixtureHandler._wire_json_payload(body)
        self.assertEqual(payload["accountUrl"], "https://192.168.1.20:19443")
        self.assertEqual(payload["pushUrl"], "wss://192.168.1.20:19443/push?transport=tls")

    def test_fresh_runtime_has_local_directory_and_version_fallbacks(self) -> None:
        # A clean start-server.ps1 runtime only contains the bootstrap file;
        # directory responses must still let the original client reach the
        # local guest/account seam without copying old capture artifacts.
        status, body = self.request("POST", "/dirsvr/DirAccount", body=b"client-version")
        self.assertEqual(status, 200)
        payload = FixtureHandler._wire_json_payload(body)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["accountSwitch"]["guestLoginSwitch"], True)
        self.assertTrue(payload["accountUrl"].startswith("http://127.0.0.1:"))
        status, body = self.request("GET", "/clientupdate/")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body[2:]), {})
        # A client that cached the previous updateCheckUrls base may append
        # the legacy /patch-version.json prefix on its next launch.  Keep
        # that request on the same local no-update path when no captured
        # manifest is present.  (The fixture-serving behavior itself is
        # covered by test_directory_route_uses_index... below.)
        legacy_manifest = self.root / "patch-version.json" / "clientupdate" / "index.json"
        captured_manifest = legacy_manifest.read_bytes()
        legacy_manifest.unlink()
        try:
            status, body = self.request("GET", "/patch-version.json/clientupdate/")
        finally:
            legacy_manifest.write_bytes(captured_manifest)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body[2:]), {})
        status, body = self.request("GET", "/app_version/")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body[2:]), {})
        status, body = self.request("POST", "/ClickGame", body=b"probe")
        self.assertEqual(status, 200)
        self.assertIn(b'"result":0', body)

    def test_loopback_coverage_and_route_table_diagnostics(self) -> None:
        status, body = self.request("GET", "/__local__/coverage")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["totalInterfaces"], 84)
        status, body = self.request("GET", "/__local__/route-table")
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body)["routes"], list)

    def test_local_directory_contracts_return_sandbox_config(self) -> None:
        expected = {
            "/Dir/": "dirWorld",
            "/DirAccount/": "accountUrl",
            "/WorldConfig/": "worldConfigGet",
            "/DirStat/": "dirStatGet",
            "/CommitVersion/": "commitVersionGet",
            "/FormalVersion/": "formalVersionGet",
            "/AllConfig/": "worldConfigGet",
            "/iplimit/": "ipList",
            "/test/": "test",
            "/accountswitch/": "result",
        }
        for path, key in expected.items():
            with self.subTest(path=path):
                status, body = self.request("POST", path, body=b"{}", headers={"Content-Type": "application/wireformat"})
                self.assertEqual(status, 200)
                self.assertEqual(body[0], 0x22)
                offset = 1
                while body[offset] & 0x80:
                    offset += 1
                offset += 1
                payload = json.loads(body[offset:])
                self.assertIn(key, payload)
                self.assertTrue(payload.get("sandbox", True))

    def test_local_status_contracts_return_safe_wire_responses(self) -> None:
        expected = {
            "/health/": "status",
            "/info/": "settings",
            "/info/res/": "resources",
            "/status/": "msg",
            "/myip/": "get",
            "/isopen/": "isOpen",
            "/server-update-info/": "timeInfo",
            "/close_ws/": "closed",
            "/gamecenter/": "qqvip",
            "/xinyue-vipinfo/": "ilevel",
            "/db-access/": "result",
            "/temp-entrance-switch/": "switchInfo",
        }
        for path, key in expected.items():
            with self.subTest(path=path):
                status, body = self.request("GET", path)
                self.assertEqual(status, 200)
                payload = FixtureHandler._wire_json_payload(body)
                self.assertIsInstance(payload, dict)
                self.assertIn(key, payload)
                self.assertTrue(payload.get("sandbox", True))

    def test_safe_control_contracts_are_registered(self) -> None:
        """Control-plane compatibility routes stay local and non-sensitive."""

        accounts = LocalAccounts(self.root / "state")
        from game import LocalGame

        game = LocalGame(self.root / "game", accounts)
        server = FixtureServer(
            ("127.0.0.1", 0),
            self.root,
            JsonlLogger(self.log_path),
            accounts=accounts,
            game=game,
        )
        try:
            report = server.route_registry.coverage_report()
            for interface in (
                "ApiManageAuthInfo",
                "ApiManageSession",
                "ApiAppById",
                "ApiApp",
                "ApiClickGame",
                "ApiModelDetailInfo",
                "ApiCheckPlayerData",
            ):
                self.assertEqual(
                    next(item["status"] for item in report["interfaces"] if item["interface"] == interface),
                    "implemented",
                )
            probes = {
                ("GET", "/App/local/"): "get",
                ("POST", "/ClickGame/"): "result",
                ("GET", "/models/local/key/"): "model",
                ("GET", "/checkdata/player/name/"): "data",
            }
            for (method, path), key in probes.items():
                with self.subTest(method=method, path=path):
                    response = server.dispatch_contract(method, path, {"method": method, "path": path, "body": b""})
                    self.assertIsNotNone(response)
                    assert response is not None
                    self.assertEqual(response.status, 200)
                    self.assertIn(key, response.payload)
                    self.assertTrue(response.payload.get("sandbox", True))

            # Player lookup routes are scoped to the current local access
            # token and must not become an account-enumeration primitive.
            status, session = accounts.handle(
                "POST", "/AccountSession/", b"\x1a\x08password", "application/wireformat"
            )
            self.assertEqual(status, 200)
            account_token = "AccountToken " + session["accountToken"]
            credentials = json.dumps(
                {"entryType": "password", "username": "lookup_user", "password": "lookup-pass"}
            )
            status, _ = accounts.handle("POST", "/NewEntry/", credentials, "application/json", account_token)
            self.assertEqual(status, 200)
            status, login = accounts.handle("POST", "/Password/", credentials, "application/json")
            self.assertEqual(status, 200)
            authorization = "Token " + login["tokenAccess"]
            response = server.dispatch_contract(
                "POST",
                "/playeridbyname/",
                {"method": "POST", "path": "/playeridbyname/", "body": b"{}", "authorization": authorization},
            )
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response.status, 200)
            self.assertTrue(response.payload["playerId"].startswith("local-"))
            response = server.dispatch_contract(
                "POST",
                "/openidbyplayerid/",
                {"method": "POST", "path": "/openidbyplayerid/", "body": b"{}", "authorization": authorization},
            )
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response.status, 200)
            self.assertTrue(response.payload["msdkOpenId"].startswith("local_"))
            response = server.dispatch_contract(
                "POST",
                "/playeridbyname/",
                {"method": "POST", "path": "/playeridbyname/", "body": b"{}"},
            )
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response.status, 401)
        finally:
            server.server_close()

        for path, key in (
            ("/dirtycheck/hello/", "dirty"),
            ("/real-name-verified/local_guest/", "adult"),
            ("/entry-exist/email/local_guest/", "exists"),
        ):
            with self.subTest(path=path):
                status, body = self.request("GET", path)
                self.assertEqual(status, 200)
                payload = FixtureHandler._wire_json_payload(body)
                self.assertIsInstance(payload, dict)
                self.assertIn(key, payload)
                self.assertTrue(payload.get("sandbox", True))

    def test_health_and_unknown_route(self) -> None:
        status, body = self.request("GET", "/__local__/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        status, body = self.request("GET", "/login")
        self.assertEqual(status, 501)
        self.assertEqual(json.loads(body)["error"], "route_not_implemented")

    def test_health_exposes_tls_certificate_warning(self) -> None:
        self.server.tls_certificate = {
            "status": "warning",
            "notAfter": "2027-01-25T05:22:20+00:00",
            "daysRemaining": 10.0,
            "warning": True,
        }
        status, body = self.request("GET", "/__local__/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "warning")
        self.assertTrue(payload["certificate"]["warning"])
        response = self.server.dispatch_contract(
            "GET", "/health/", {"method": "GET", "path": "/health/", "host": "127.0.0.1"}
        )
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload["status"], "warning")

    def test_unexpected_http_handler_exception_returns_notify_and_safe_audit(self) -> None:
        class ExplodingGame:
            def handle_http(self, *_args):
                raise NameError("secret body and bearer token must not be recorded")

        self.server.game = ExplodingGame()
        secret_body = b"private request body"
        status, body = self.request(
            "PUT",
            "/fci/tutorial/name?token=private-query",
            body=secret_body,
            headers={
                "Content-Length": str(len(secret_body)),
                "Authorization": "Bearer private-token",
                "Content-Type": "application/wireformat",
            },
        )
        self.assertEqual(status, 507)
        self.assertEqual(
            json.loads(body),
            {"notify": {"code": "Error_DbError"}},
        )

        deadline = time.time() + 1
        while not self.log_path.exists() and time.time() < deadline:
            time.sleep(0.01)
        records = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        error = next(item for item in records if item.get("error") == "handler_exception")
        self.assertEqual(error["method"], "PUT")
        self.assertEqual(error["path"], "/fci/tutorial/name")
        self.assertEqual(error["exception"], "NameError")
        encoded = json.dumps(error, ensure_ascii=False)
        self.assertNotIn("private request body", encoded)
        self.assertNotIn("private-token", encoded)
        self.assertNotIn("private-query", encoded)

    def test_wireformat_fixture_content_type(self) -> None:
        directory = self.root / 'dirsvr'
        directory.mkdir()
        (directory / 'DirAccount.wireformat').write_bytes(b'\x22\x02{}')
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        connection.request('POST', '/dirsvr/DirAccount', body=b'')
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Type'), 'application/wireformat')
        self.assertEqual(response.read(), b'\x22\x02{}')
        connection.close()

    def test_lz4_account_session_over_http(self) -> None:
        self.server.accounts = LocalAccounts(self.root / 'state')
        plain = b'\x0a\x05local\x12\x64' + b'x' * 100
        status, body = self.request('POST', '/AccountSession', lz4.frame.compress(plain),
            {'Content-Type':'application/wireformat','Content-Encoding':'lz4'})
        self.assertEqual(status, 200)
        self.assertEqual(body[0], 0x22)
        self.assertEqual(body[1], len(body)-2)
        self.assertTrue(json.loads(body[2:])['accountToken'].startswith('local_session_'))
        status, _ = self.request('POST', '/AccountSession', b'not-lz4', {'Content-Encoding':'lz4'})
        self.assertEqual(status, 509)
        status, body = self.request('POST', '/ClickGame/', b'not-lz4', {'Content-Encoding':'lz4'})
        self.assertEqual(status, 509)
        self.assertEqual(json.loads(body), {"notify": {"code": "Error_RequestFailed"}})

    def test_role_notepad_query_survives_real_http_dispatch(self) -> None:
        from game import LocalGame
        from grants import grant_rewards
        from test_systems import LocalSystemsTests

        accounts = LocalAccounts(self.root / "state")
        game = LocalGame(self.root / "game", accounts)
        account, auth = LocalSystemsTests._guest(accounts)
        grant_rewards(game, game._profile(account), [
            {"type": 15, "id": 1002, "count": 1},
            {"type": 15, "id": 1007, "count": 1},
        ])
        self.server.accounts, self.server.game = accounts, game
        headers = {"Authorization": auth, "Accept": "application/wireformat"}
        for path, expected in (
            ("/fci/RoleNotePad/?perPage=20&uids=7", [7]),
            ("/fci//RoleNotePad/?uids=7%2C1%2C7", [7, 1, 7]),
            ("http://127.0.0.1/fci/RoleNotePad/?uids=7&uids=2,1", [7, 2, 1]),
        ):
            with self.subTest(path=path):
                status, body = self.request("GET", path, headers=headers)
                self.assertEqual(status, 200)
                records = FixtureHandler._wire_json_payload(body)
                self.assertIsInstance(records, list)
                self.assertEqual([record["roleId"] for record in records], expected)
        status, body = self.request("GET", "/fci/RoleNotePad/?uids=7,3,1", headers=headers)
        self.assertEqual(status, 509)
        self.assertEqual(json.loads(body), {"notify": {"code": "Error_RequestFailed"}})
        status, body = self.request("GET", "/fci/RoleNotePad/?uids=", headers=headers)
        self.assertEqual(status, 509)
        self.assertEqual(json.loads(body), {"notify": {"code": "Error_RequestFailed"}})
        status, body = self.request("HEAD", "/fci/RoleNotePad/?uids=7", headers=headers)
        self.assertEqual((status, body), (200, b""))

    def test_game_http_query_preserves_routes_and_selects_activity(self) -> None:
        from google.protobuf import message_factory
        from game import LocalGame
        from test_systems import LocalSystemsTests

        accounts = LocalAccounts(self.root / "state")
        game = LocalGame(self.root / "game", accounts, clock=lambda: 1_700_000_000)
        _account, auth = LocalSystemsTests._guest(accounts)
        self.server.accounts, self.server.game = accounts, game
        headers = {"Authorization": auth, "Accept": "application/wireformat"}
        for path in ("/fci/Signboard", "/fci/task", "/fci/actor-cultivation", "/fci/auto-fight-conf",
                     "/fci/activity"):
            with self.subTest(path=path):
                expected = self.request("GET", path, headers=headers)
                self.assertEqual(expected[0], 200)
                self.assertEqual(self.request("GET", path + "?perPage=20", headers=headers), expected)
        response_type = message_factory.GetMessageClass(game._pool.FindMessageTypeByName("RestfulResponse"))
        activity_type = message_factory.GetMessageClass(game._pool.FindMessageTypeByName("ApiActivity.result"))
        for query, expected_type in (("type=46", 46), ("activityType=25", 25)):
            with self.subTest(query=query):
                status, body = self.request("GET", "/fci/activity?" + query, headers=headers)
                self.assertEqual(status, 200)
                response = response_type.FromString(body)
                self.assertEqual(response.message.type, "ApiActivity.result")
                self.assertEqual(activity_type.FromString(response.message.data).result.type, expected_type)

    def test_directory_route_uses_index_and_redacts_query_values(self) -> None:
        status, body = self.request(
            "GET",
            "/patch-version.json/clientupdate/?version=2.0.4&device_id=private-device&platform=android&token=private-token",
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["patch"], "test")
        deadline = time.time() + 1
        while not self.log_path.exists() and time.time() < deadline:
            time.sleep(0.01)
        records = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        request = next(item for item in records if item.get("path") == "/patch-version.json/clientupdate/")
        self.assertEqual(
            request["query_fields"], ["version", "device_id", "platform", "token"]
        )
        self.assertEqual(request["query_params"]["version"], "2.0.4")
        self.assertEqual(request["query_params"]["platform"], "android")
        self.assertEqual(request["query_params"]["device_id"], "redacted")
        self.assertEqual(request["query_params"]["token"], "redacted")
        encoded = json.dumps(request, ensure_ascii=False)
        self.assertNotIn("private-device", encoded)
        self.assertNotIn("private-token", encoded)

    def test_dir_account_logs_safe_headers_and_small_body_hex_only(self) -> None:
        body = b"client_version=20"
        status, _ = self.request(
            "POST",
            "/dirsvr/DirAccount",
            body=body,
            headers={
                "Content-Length": str(len(body)),
                "Content-Type": "application/octet-stream",
                "Accept": "application/json",
            },
        )
        self.assertEqual(status, 200)
        deadline = time.time() + 1
        records = []
        while time.time() < deadline:
            if self.log_path.exists():
                records = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
                if any(item.get("path") == "/dirsvr/DirAccount" for item in records):
                    break
            time.sleep(0.01)
        request = next(item for item in records if item.get("path") == "/dirsvr/DirAccount")
        self.assertEqual(request["content_type"], "application/octet-stream")
        self.assertEqual(request["accept"], "application/json")
        self.assertEqual(request["request_body_hex"], body.hex())

        status, _ = self.request(
            "POST",
            "/dirsvr/DirAccount/extra",
            body=body,
            headers={"Content-Length": str(len(body)), "Content-Type": "private-header"},
        )
        self.assertEqual(status, 501)
        deadline = time.time() + 1
        records = []
        while time.time() < deadline:
            if self.log_path.exists():
                records = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
                if any(item.get("path") == "/dirsvr/DirAccount/extra" for item in records):
                    break
            time.sleep(0.01)
        request = next(item for item in records if item.get("path") == "/dirsvr/DirAccount/extra")
        self.assertNotIn("request_body_hex", request)
        self.assertEqual(request["content_type"], "private-header")

    def test_log_does_not_record_sensitive_headers(self) -> None:
        status, _ = self.request(
            "POST", "/login", body=b"secret", headers={"Content-Length": "6", "Authorization": "Bearer secret", "Cookie": "sid=secret"}
        )
        self.assertEqual(status, 501)
        # Wait for the request record itself, not just the file: the log may
        # already exist (startup records) before this request is written.
        deadline = time.time() + 2
        records = []
        while time.time() < deadline:
            if self.log_path.exists():
                records = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
                if any(item.get("kind") == "request" for item in records):
                    break
            time.sleep(0.01)
        request = next(item for item in records if item.get("kind") == "request")
        encoded = json.dumps(request, ensure_ascii=False)
        self.assertNotIn("Bearer", encoded)
        self.assertNotIn("sid=", encoded)
        self.assertEqual(request["request_body_bytes"], 6)
        self.assertIsNotNone(request["request_body_sha256"])

    def test_path_traversal_is_not_served(self) -> None:
        status, _ = self.request("GET", "/../requests.jsonl")
        self.assertEqual(status, 501)

    def test_bad_tls_handshake_is_logged_and_health_still_works(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(minutes=5))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256())
        )
        cert_path = self.root / "test.crt"
        key_path = self.root / "test.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
        tls_log = self.root / "tls.jsonl"
        tls_server = FixtureServer(("127.0.0.1", 0), self.root, JsonlLogger(tls_log), tls=True, tls_context=context)
        context.set_servername_callback(tls_server._capture_sni)
        tls_thread = threading.Thread(target=tls_server.serve_forever, daemon=True)
        tls_thread.start()
        try:
            bad = socket.create_connection(("127.0.0.1", tls_server.server_port), timeout=2)
            bad.sendall(b"this is not TLS")
            bad.close()

            client_context = ssl._create_unverified_context()
            connection = HTTPSConnection("localhost", tls_server.server_port, timeout=2, context=client_context)
            connection.request("GET", "/__local__/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")
            connection.close()

            deadline = time.time() + 1
            while not tls_log.exists() and time.time() < deadline:
                time.sleep(0.01)
            records = [json.loads(line) for line in tls_log.read_text(encoding="utf-8").splitlines()]
            handshakes = [item for item in records if item.get("kind") == "tls_handshake"]
            self.assertTrue(any(item.get("status") == "error" for item in handshakes))
            self.assertTrue(any(item.get("status") == "ok" and item.get("tls_version") for item in handshakes))
            self.assertTrue(any(item.get("kind") == "request" and item.get("status") == 200 for item in records))
        finally:
            tls_server.shutdown()
            tls_server.server_close()
            tls_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
