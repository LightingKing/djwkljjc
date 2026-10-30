"""The native async arena must never fall through to a guessed local PvE run."""

from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from google.protobuf import json_format, message_factory

from accounts import LocalAccounts
from game import LocalGame
from tournament import LocalTournament
import test_systems


class TournamentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = LocalGame(self.root / "game", self.accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.module = getattr(self.game, "tournament", None) or LocalTournament(self.game)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        return self.module.handle_http(method, path, body, self.profile)

    def wire(self, name, **fields):
        return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))(**fields).SerializeToString()

    def test_summary_is_legal_empty_native_response(self):
        status, result = self.call("GET", "/fci/tournament/summary/")
        self.assertEqual((status, result), (200, {"activityInfo": []}))
        message_type = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiTournamentSummary.result"))
        json_format.ParseDict(result, message_type()).SerializeToString()
        self.assertEqual(self.call("POST", "/fci/tournament/summary/")[0], 405)

    def test_native_routes_report_missing_configuration_without_writes(self):
        before = self.game._profile(self.account)["resources"]
        for method, route, payload in [
            ("GET", "/fci/tournament/info/5/50001/", None),
            ("GET", "/fci/tournament/enter/5/50001", None),
            ("POST", "/fci/tournament/enter/", {"activityType": 5, "tournamentId": 50001}),
            ("POST", "/fci/tournament/dungeonenter/", {"activityType": 5, "tournamentId": 50001, "target_uid": "bot"}),
            ("PATCH", "/fci/tournamentdungeon/123/active/", {}),
            ("POST", "/fci/tournament/dungeonbalance/", {"activityType": 5}),
            ("POST", "/fci/tournament/dungeonquit/", {"activityType": 5}),
            ("POST", "/fci/tournament/winreward/", {"activityType": 5, "tournamentId": 50001}),
            ("POST", "/fci/asyncpvpmatch/", {"index": 1}),
            ("GET", "/fci/asyncpvptargetdetail/local-bot-001", None),
        ]:
            with self.subTest(route=route):
                status, result = self.call(method, route, payload)
                self.assertEqual(status, 501)
                self.assertEqual(result["error"], "tournament_asyncpvp_not_configured")
                self.assertFalse(result["dungeonConfigured"])
                self.assertNotIn("dungeonId", result)
        self.assertEqual(self.game._profile(self.account)["resources"], before)
        with closing(self.game.dungeons._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM local_dungeons").fetchone()[0], 0)

    def test_wire_enter_is_identified_before_old_pve_handler(self):
        body = self.wire("ApiTournamentEnter", tournament_id=50001, activity_type=5)
        self.assertTrue(self.module.handles("/fci/tournament/enter/", body))
        self.assertEqual(self.call("POST", "/fci/tournament/enter/", body)[0], 501)
        other = self.wire("ApiTournamentEnter", tournament_id=18001, activity_type=18)
        self.assertFalse(self.module.handles("/fci/tournament/enter/", other))

    def test_other_modes_and_local_pve_are_not_claimed(self):
        for route, payload in [
            ("/fci/tournament/enter/", {"stage": 1}),
            ("/fci/tournament/info/", None),
            ("/fci/tournament/123/balance", {}),
            ("/fci/tournament/info/18/18001", None),
            ("/fci/tournament/enter/", {"activityType": 18, "tournamentId": 18001}),
            ("/fci/BurstLinkPVPInfo/", None),
            ("/fci/RacePVPInfo/", None),
            ("/fci/asyncpvp/match", {}),
            ("/fci/asyncpvprecord/", None),
        ]:
            with self.subTest(route=route):
                self.assertIsNone(self.call("GET", route, payload))

    def test_game_dispatch_rejects_json_and_wire_enter_before_pve(self):
        before = self.game._profile(self.account)["resources"]
        for content_type, body in [
            ("application/json", b'{"tournamentId":50001,"activityType":5}'),
            ("application/wireformat", self.wire("ApiTournamentEnter", tournament_id=50001, activity_type=5)),
        ]:
            with self.subTest(content_type=content_type):
                status, result = self.game.handle_http("POST", "/fci/tournament/enter/", body, content_type, self.auth)
                self.assertEqual(status, 501)
                self.assertEqual(result["error"], "tournament_asyncpvp_not_configured")
        for method, path, body in [
            ("GET", "/fci/tournament/info/5/50001/", None),
            ("POST", "/fci/asyncpvpmatch/", b'{"index":1}'),
            ("GET", "/fci/asyncpvptargetdetail/bot", None),
        ]:
            self.assertEqual(self.game.handle_http(method, path, body, "application/json", self.auth)[0], 501)
        self.assertEqual(self.game.handle_http("GET", "/fci/tournament/summary/", None, "application/json", self.auth),
                         (200, {"activityInfo": []}))
        self.assertEqual(self.game._profile(self.account)["resources"], before)
        with closing(self.game.dungeons._connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM local_dungeons").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
