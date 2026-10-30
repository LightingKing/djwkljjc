from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from accounts import LocalAccounts
from game import LocalGame
from server import FixtureServer, JsonlLogger


class SafeContractCompletionTests(unittest.TestCase):
    def test_remaining_unimplemented_contracts_are_explicitly_dangerous(self):
        """Keep the 501 boundary reviewable instead of silently widening it.

        The remaining unimplemented descriptors are administrative or external
        identity operations.  They must stay visible as 501 responses while
        local gameplay routes continue to be implemented normally.
        """
        expected = {
            "ApiGmPassword", "ApiGmAccountEntry", "ApiAuthByLdap", "ApiBbrrImport",
            "ApiWhiteOpenId", "ApiUploadWhiteOpenId", "ApiDelWhiteOpenId",
            "ApiDelAllWhiteOpenId", "ApiShutDown", "ApiKickAll", "ApiChangeServerTime",
            "ApiGmRemovePlayer", "ApiBanPlayer", "ApiPlayerFuncBan", "ApiKickPlayer",
            "ApiGmCachePlayerClean",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            accounts = LocalAccounts(state)
            game = LocalGame(state, accounts)
            server = FixtureServer(
                ("127.0.0.1", 0), root, JsonlLogger(root / "requests.jsonl"),
                accounts=accounts, game=game, state_dir=state,
            )
            try:
                coverage = server.route_registry.coverage_report()
                unimplemented = {
                    row["interface"] for row in coverage["interfaces"]
                    if row["status"] == "unimplemented"
                }
                self.assertEqual(unimplemented, expected)
                for row in coverage["interfaces"]:
                    if row["interface"] not in expected:
                        continue
                    route = row["route"].split(",", 1)[0]
                    route = route.replace("<string:openid>", "local-openid")
                    route = route.replace("<string:playerId>", "local-player")
                    # ``dispatch_contract`` intentionally returns ``None`` for
                    # routes without concrete handlers.  Ask the registry
                    # directly so the contract's explicit fallback response
                    # is part of this boundary test.
                    response = server.route_registry.dispatch(
                        "POST", route,
                        {"method": "POST", "path": route, "body": b"{}"},
                    )
                    self.assertEqual(response.status, 501, row["interface"])
                    self.assertEqual(response.payload.get("error"), "route_not_implemented")
            finally:
                server.server_close()

    def test_bbrr_old_bind_is_local_token_scoped_compatibility_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / "state"
            accounts = LocalAccounts(state)
            game = LocalGame(state, accounts)
            server = FixtureServer(
                ("127.0.0.1", 0), root, JsonlLogger(root / "requests.jsonl"),
                accounts=accounts, game=game, state_dir=state,
            )
            try:
                coverage = server.route_registry.coverage_report()
                # Other roadmap work may add more local handlers; this test
                # only requires the safe BBrr seam to be present.
                self.assertGreaterEqual(coverage["implementedInterfaces"], 68)

                status, session = accounts.handle(
                    "POST", "/AccountSession/", b"\x1a\x08password", "application/wireformat"
                )
                self.assertEqual(status, 200)
                account_token = "AccountToken " + session["accountToken"]
                credentials = json.dumps({
                    "entryType": "password",
                    "username": "bbrr-local",
                    "password": "local-password",
                })
                status, _ = accounts.handle(
                    "POST", "/NewEntry/", credentials, "application/json", account_token
                )
                self.assertEqual(status, 200)
                status, login = accounts.handle("POST", "/Password/", credentials, "application/json")
                self.assertEqual(status, 200)
                authorization = "Token " + login["tokenAccess"]

                response = server.dispatch_contract(
                    "POST",
                    "/AccountEntry/bind-bbrr-old/",
                    {
                        "method": "POST",
                        "path": "/AccountEntry/bind-bbrr-old/",
                        "body": b"",
                        "authorization": authorization,
                    },
                )
                self.assertIsNotNone(response)
                assert response is not None
                self.assertEqual(response.status, 200)
                self.assertEqual(response.payload["post"]["username"], "bbrr-local")
                self.assertTrue(response.payload["sandbox"])

                unauthorized = server.dispatch_contract(
                    "POST",
                    "/AccountEntry/bind-bbrr-old/",
                    {"method": "POST", "path": "/AccountEntry/bind-bbrr-old/", "body": b""},
                )
                self.assertIsNotNone(unauthorized)
                assert unauthorized is not None
                self.assertEqual(unauthorized.status, 401)
            finally:
                server.server_close()


if __name__ == "__main__":
    unittest.main()

