"""Native asynchronous arena contract and idempotency tests."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from google.protobuf import json_format, message_factory

from accounts import LocalAccounts
from game import LocalGame
from tournament import LocalTournament, TOURNAMENT_ID
import test_systems


class TournamentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = LocalGame(self.root / "game", self.accounts, initial_level=23,
                              initial_resources=[{"type": 7, "id": 0, "param": 0, "count": 200000}])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.module = LocalTournament(self.game)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        return self.module.handle_http(method, path, raw, self.profile)

    def wire(self, name, **fields):
        return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))(**fields).SerializeToString()

    def test_summary_info_cost_and_wire_enter_are_native_contracts(self):
        status, summary = self.call("GET", "/fci/tournament/summary/")
        self.assertEqual(status, 200)
        self.assertEqual(summary["activityInfo"][0]["activity"]["category"], 3)
        self.assertEqual(summary["activityInfo"][0]["activity"]["type"], 5)
        status, info = self.call("GET", "/fci/tournament/info/5/50001/")
        self.assertEqual((status, info["tournamentInfo"][0]["tournamentId"]), (200, TOURNAMENT_ID))
        for name, payload in (("ApiTournamentSummary.result", summary), ("ApiTournamentInfo.result", info),
                              ("ApiTournamentCost.get", self.call("GET", "/fci/tournament/cost/5/50001/")[1])):
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
            json_format.ParseDict(payload, cls())
        self.assertEqual(self.call("GET", "/fci/tournament/cost/5/50001/")[1]["cost"]["count"], 0)
        status, result = self.call("POST", "/fci/tournament/enter/", self.wire("ApiTournamentEnter", tournament_id=TOURNAMENT_ID, activity_type=5))
        self.assertEqual((status, result), (200, {"enterResult": True, "resChange": []}))
        self.assertEqual(self.call("GET", "/fci/tournament/enter/5/50001")[1]["winNum"], 0)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiTournamentEnter.post"))
        self.assertEqual(json_format.ParseDict(result, cls()), cls.FromString(json_format.ParseDict(result, cls()).SerializeToString()))

    def test_real_run_balance_repeat_and_win_reward_are_exactly_once(self):
        self.call("POST", "/fci/tournament/enter/", {"tournamentId": TOURNAMENT_ID, "activityType": 5})
        target = self.call("GET", "/fci/tournament/enter/5/50001")[1]["asyncPvpTargetInfo"][0]
        status, entered = self.call("POST", "/fci/tournament/dungeonenter/", {"tournamentId": TOURNAMENT_ID, "activityType": 5, "target_uid": target["uid"]})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        self.assertEqual(self.call("PATCH", f"/fci/tournamentdungeon/{uid}/active/", {"winTeam": 1})[0], 200)
        status, balance = self.call("POST", "/fci/tournament/dungeonbalance/", {"activityType": 5})
        self.assertEqual((status, balance["balanceInfo"]["win"], balance["winNum"]), (200, True, 1))
        balance_cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiTournamentDungeonBalance.balance"))
        json_format.ParseDict(balance, balance_cls())
        self.assertEqual(self.call("POST", "/fci/tournament/dungeonbalance/", {"activityType": 5})[1], balance)
        status, reward = self.call("POST", "/fci/tournament/winreward/", {"tournamentId": TOURNAMENT_ID, "activityType": 5, "winNum": 1})
        self.assertEqual(status, 200)
        self.assertTrue(reward["resChange"])
        task_rows = {row["id"]: row for row in self.game.tasks.snapshot(self.profile)["tasks"]}
        self.assertEqual(task_rows[12]["progress"], 1)
        self.assertEqual(self.call("POST", "/fci/tournament/winreward/", {"tournamentId": TOURNAMENT_ID, "activityType": 5, "winNum": 1})[0], 409)
        record = self.call("GET", "/fci/asyncpvprecord/")[1]
        self.assertEqual(record["todayMaxWinNum"], 1)
        self.assertNotIn(1, record["leftRewardsWinNum"])

    def test_refresh_is_zero_based_consumes_item_and_caps_at_three(self):
        bag = self.game.systems._state(self.profile)
        bag.setdefault("items", {})["80001"] = {"count": 3}
        self.game.systems._save(self.profile, bag)
        original = self.call("GET", "/fci/tournament/enter/5/50001")[1]["asyncPvpTargetInfo"][0]["uid"]
        for index in range(3):
            status, result = self.call("POST", "/fci/asyncpvpmatch/", {"index": index})
            self.assertEqual((status, result["index"]), (200, index))
            if index == 0:
                self.assertNotEqual(result["targetActor"]["uid"], original)
        self.assertEqual(self.call("POST", "/fci/asyncpvpmatch/", {"index": 0})[0], 409)
        self.assertEqual(self.call("GET", "/fci/asyncpvprecord/")[1]["canRefresh"], False)


if __name__ == "__main__":
    unittest.main()
