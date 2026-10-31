"""Native asynchronous arena contract and idempotency tests."""
from contextlib import closing
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

from google.protobuf import json_format, message_factory

from accounts import LocalAccounts
from game import LocalGame
from tournament import LocalTournament, TOURNAMENT_ID
from local_mailbox import send_mail
from server import FixtureServer, JsonlLogger
import test_systems

try:
    from lupa.lua53 import LuaRuntime
except ImportError:
    LuaRuntime = None


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

    def saved_rows(self):
        with closing(self.game._connect()) as conn:
            return tuple(conn.iterdump())

    def game_call(self, method, path, payload=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        return self.game.handle_http(method, path, raw, "application/wireformat" if isinstance(payload, bytes) else "application/json", self.auth)

    def test_summary_info_cost_and_wire_enter_are_native_contracts(self):
        status, summary = self.call("GET", "/fci/tournament/summary/")
        self.assertEqual(status, 200)
        self.assertEqual(summary["activityInfo"][0]["activity"]["category"], 3)
        self.assertEqual(summary["activityInfo"][0]["activity"]["type"], 5)
        # The activity advertised to the client must drive the native URL;
        # hardcoding the module constant used to hide the 5001/50001 mismatch.
        status, activities = self.game_call("GET", "/fci/activity/")
        activity = next(row for row in activities if row["type"] == 5)
        self.assertEqual(activity["activityId"], TOURNAMENT_ID)
        status, info = self.game_call("GET", f"/fci/tournament/info/5/{activity['activityId']}/")
        self.assertEqual((status, info["tournamentInfo"][0]["tournamentId"]), (200, TOURNAMENT_ID))
        for name, payload in (("ApiTournamentSummary.result", summary), ("ApiTournamentInfo.result", info),
                              ("ApiTournamentCost.get", self.call("GET", f"/fci/tournament/cost/5/{TOURNAMENT_ID}/")[1])):
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
            json_format.ParseDict(payload, cls())
        self.assertEqual(self.call("GET", f"/fci/tournament/cost/5/{TOURNAMENT_ID}/")[1]["cost"]["count"], 0)
        status, result = self.call("POST", "/fci/tournament/enter/", self.wire("ApiTournamentEnter", tournament_id=TOURNAMENT_ID, activity_type=5))
        self.assertEqual((status, result), (200, {"enterResult": True, "resChange": []}))
        self.assertEqual(self.call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")[1]["winNum"], 0)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiTournamentEnter.post"))
        self.assertEqual(json_format.ParseDict(result, cls()), cls.FromString(json_format.ParseDict(result, cls()).SerializeToString()))

    def test_real_run_balance_repeat_and_win_reward_are_exactly_once(self):
        self.call("POST", "/fci/tournament/enter/", {"tournamentId": TOURNAMENT_ID, "activityType": 5})
        target = self.call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")[1]["asyncPvpTargetInfo"][0]
        status, entered = self.call("POST", "/fci/tournament/dungeonenter/", {"tournamentId": TOURNAMENT_ID, "activityType": 5, "target_uid": target["uid"]})
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        # SceneFight.ProcessGameEnd sends bare FightActiveData, never the
        # ApiTournamentDungeonActive wrapper with a nested request field.
        active = self.wire("FightActiveData", dungeonType=3, stageID=4001, gameRound=3, winTeam=1)
        self.assertEqual(self.game_call("PATCH", f"/fci/tournamentdungeon/{uid}/active/", active)[0], 200)
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
        before = self.saved_rows()
        for method, path, payload in [
            ("POST", "/fci/tournament/dungeonenter/", {"tournamentId": TOURNAMENT_ID, "activityType": 5, "target_uid": target["uid"]}),
            ("POST", "/fci/asyncpvpmatch/", {"index": 0}),
            ("PATCH", f"/fci/tournamentdungeon/{uid}/active/", active),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.game_call(method, path, payload)[0], 409)
                self.assertEqual(self.saved_rows(), before)

    def test_native_loss_retry_and_next_opponent_need_no_ticket_or_energy(self):
        self.assertEqual(self.game_call("POST", "/fci/tournament/enter/", self.wire(
            "ApiTournamentEnter", tournament_id=TOURNAMENT_ID, activity_type=5))[0], 200)
        initial_resources = self.game._profile(self.account)["resources"]
        targets = self.game_call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")[1]["asyncPvpTargetInfo"]
        self.assertEqual(self.call("GET", "/fci/asyncpvprecord/")[1]["leftRewardsWinNum"], [])
        body = self.wire("ApiTournamentDungeonEnter", tournament_id=TOURNAMENT_ID, activity_type=5,
                         target_uid=targets[0]["uid"])
        status, entered = self.game_call("POST", "/fci/tournament/dungeonenter/", body)
        self.assertEqual(status, 200)
        self.assertEqual(self.game_call("POST", "/fci/tournament/dungeonenter/", body)[1], entered)
        active_path = f"/fci/tournamentdungeon/{entered['dungeonUid']}/active/"
        before = self.saved_rows()
        for method, path, payload, status in [
            ("POST", "/fci/tournament/dungeonbalance/", {"activityType": 5}, 409),
            ("PATCH", active_path, b"null", 400),
            ("PATCH", active_path, self.wire("FightActiveData", dungeonType=3, stageID=4001), 400),
            ("PATCH", active_path, self.wire("FightActiveData", dungeonType=1, stageID=4001, winTeam=1), 400),
            ("PATCH", active_path, self.wire("FightActiveData", dungeonType=3, stageID=1, winTeam=1), 400),
        ]:
            with self.subTest(path=path, payload=payload):
                self.assertEqual(self.game_call(method, path, payload)[0], status)
                self.assertEqual(self.saved_rows(), before)
        self.assertEqual(self.game_call("PATCH", active_path, self.wire(
            "FightActiveData", dungeonType=3, stageID=4001, gameRound=4, winTeam=2))[0], 200)
        status, balance = self.game_call("POST", "/fci/tournament/dungeonbalance/", {"activityType": 5})
        self.assertEqual((status, balance["balanceInfo"]["win"], balance["winNum"], balance["dieNum"]), (200, False, 0, 1))
        self.assertEqual(self.game._profile(self.account)["resources"], initial_resources)
        next_body = self.wire("ApiTournamentDungeonEnter", tournament_id=TOURNAMENT_ID, activity_type=5,
                              target_uid=targets[1]["uid"])
        status, next_run = self.game_call("POST", "/fci/tournament/dungeonenter/", next_body)
        self.assertEqual(status, 200)
        self.assertNotEqual(entered["dungeonUid"], next_run["dungeonUid"])
        self.assertEqual(self.game_call("POST", "/fci/tournament/dungeonquit/", {"activityType": 5})[0], 200)
        before = self.saved_rows()
        self.assertEqual(self.game_call("POST", "/fci/tournament/dungeonbalance/", {"activityType": 5})[0], 409)
        self.assertEqual(self.saved_rows(), before)

    def test_refresh_is_zero_based_consumes_item_and_caps_at_three(self):
        mail = send_mail(self.game, self.profile, "Arena test fixture", "Refresh tickets", [{"type": 11, "id": 80001, "count": 3}])
        self.assertEqual(self.game_call("PATCH", f"/fci/mail/{mail['id']}", {"attachmentsGot": True})[0], 200)
        original = self.call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")[1]["asyncPvpTargetInfo"][0]
        for index in range(3):
            status, result = self.call("POST", "/fci/asyncpvpmatch/", self.wire("ApiAsyncPvpMatch", index=index))
            self.assertEqual((status, result["index"]), (200, index))
            if index == 0:
                self.assertNotEqual(result["targetActor"]["uid"], original["uid"])
                self.assertNotEqual([a["id"] for a in result["targetActor"]["targetBattleActors"]],
                                    [a["id"] for a in original["targetBattleActors"]])
        self.assertEqual(self.call("POST", "/fci/asyncpvpmatch/", {"index": 0})[0], 409)
        self.assertEqual(self.call("GET", "/fci/asyncpvprecord/")[1]["canRefresh"], False)

    def test_targets_supply_the_actual_battle_actor_attributes_and_skills(self):
        targets = self.call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")[1]["asyncPvpTargetInfo"]
        for target in targets:
            status, detail = self.game_call("GET", f"/fci/asyncpvptargetdetail/{target['uid']}")
            self.assertEqual(status, 200)
            actors = detail.target_battle_actors
            self.assertEqual(len(actors), 6)
            self.assertEqual(len({a.id for a in actors if a.id}), 3)
            for actor in actors:
                if not actor.id:
                    self.assertEqual(actor.uid, 0)
                    continue
                self.assertTrue(actor.skills)
                self.assertTrue(any(attr.type == 1 and attr.value > 0 for attr in actor.attrs))
                self.assertTrue(all(skill.id // 1000 == actor.id for skill in actor.skills))

    def test_http_target_detail_is_the_direct_native_fight_callback_object(self):
        # TournamentActorGroup.RequestTargetDetail uses response.uid and
        # response.targetBattleActors directly. A result wrapper leaves the
        # second FightActorInfo.actors nil and crashes TLog.CollectBeginInfo.
        target = self.game_call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")[1]["asyncPvpTargetInfo"][0]
        server = FixtureServer(("127.0.0.1", 0), self.root, JsonlLogger(self.root / "arena-http.jsonl"))
        server.accounts, server.game = self.accounts, self.game
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as connection:
                connection.request("GET", f"/fci/asyncpvptargetdetail/{target['uid']}",
                                   headers={"Authorization": self.auth, "Accept": "application/wireformat"})
                response = connection.getresponse()
                raw = response.read()
                self.assertEqual(response.status, 200)
            envelope_type = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("RestfulResponse"))
            envelope = envelope_type.FromString(raw)
            callback_type = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(envelope.message.type))
            callback = json_format.MessageToDict(callback_type.FromString(envelope.message.data),
                                                always_print_fields_with_no_presence=True)
            self.assertEqual(callback.get("uid"), target["uid"])
            self.assertNotIn("result", callback)
            # The native Lua builds this second team verbatim, then TLog
            # iterates #team.actors. Verify the final decoded HTTP shape.
            enemy_team = {"playerId": callback["uid"], "actors": callback["targetBattleActors"]}
            self.assertEqual(len(enemy_team["actors"]), 6)
            self.assertEqual([a["id"] for a in enemy_team["actors"]], [a["id"] for a in target["targetBattleActors"]])
            self.assertTrue(all(a["uid"] and a["attrs"] and a["skills"] for a in enemy_team["actors"] if a["id"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    @unittest.skipIf(LuaRuntime is None, "native Lua test requires hot-update/requirements.txt")
    def test_native_build_actor_from_data_keeps_all_three_enemy_units(self):
        source_root = Path(__file__).resolve().parents[3] / "analysis/lua-decompiled"
        if not source_root.is_dir():
            self.skipTest("original decompiled client Lua is unavailable")
        source = (source_root / "logic__initgamelogic.lua").read_text(encoding="utf-8")
        native_function = re.search(r"^function buildActorFromData\(.*?^end$", source, re.M | re.S).group()
        runtime = LuaRuntime()
        runtime.execute("""
            PB={enum={DungeonType={None=0}}}
            function require(_) return PB end
            warnings={}
            function warning(_,text) warnings[#warnings+1]=text end
            function setActorDataImpl(actor,id,seat,team)
              actor.actorInfo.id=id; actor.seat=seat; actor.team=team
            end
            function setActorTableImpl() error('unexpected test-only actor path') end
        """)
        runtime.globals().DEFINE = runtime.execute((source_root / "logic__define.lua").read_text(encoding="utf-8"))
        runtime.execute(native_function)
        target = self.game_call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")[1]["asyncPvpTargetInfo"][1]
        status, detail = self.game_call("GET", f"/fci/asyncpvptargetdetail/{target['uid']}")
        self.assertEqual(status, 200)
        decoded = json_format.MessageToDict(type(detail).FromString(detail.SerializeToString()),
                                           always_print_fields_with_no_presence=True)
        actors = runtime.table()
        for seat, actor in enumerate(decoded["targetBattleActors"], 1):
            # The native STActor list becomes one RoundActorInfo per seat;
            # run the original Lua's count validation, not a Python replica.
            actors[seat] = runtime.table_from({"seat": seat, "actorInfo": runtime.table_from(actor)})
        player = runtime.table_from({"playerId": decoded["uid"], "actorList": actors})
        team = runtime.table_from({"team": 2, "playerList": runtime.table_from([player])})
        teams = runtime.table_from([team])
        hp = runtime.table_from([runtime.table_from([0] * 6), runtime.table_from([0] * 6)])
        runtime.globals().buildActorFromData(teams, hp, 3, 0)
        remaining = teams[1].playerList[1].actorList
        self.assertEqual(len(remaining), 6)
        self.assertEqual([remaining[i].actorInfo.id for i in range(1, 7)],
                         [actor["id"] for actor in decoded["targetBattleActors"]])
        self.assertEqual(sum(remaining[i].actorInfo.id != 0 for i in range(1, 7)), 3)
        self.assertEqual(len(runtime.globals().warnings), 0)

    def test_legacy_three_seat_targets_migrate_without_resetting_record_or_run(self):
        self.call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")
        # Explicit pre-fix save fixture: migration must preserve the existing
        # win ledger and target identities, including the pending second fight.
        with closing(self.game._connect()) as conn, conn:
            row = conn.execute("SELECT state_json FROM local_tournament_state WHERE account_id=?",
                               (self.profile["account_id"],)).fetchone()
            old = json.loads(row["state_json"])
            for target in old["targets"]:
                target["targetBattleActors"] = target["targetBattleActors"][:3]
            old["wins"], old["claimed"], old["targets"][0]["isWin"] = 1, [1], True
            old["run"] = {"uid": 1700000000001, "target_uid": old["targets"][1]["uid"], "active": True,
                          "snapshot": None, "balanced": False, "win": False, "dungeon_id": 4001}
            conn.execute("UPDATE local_tournament_state SET state_json=? WHERE account_id=?",
                         (json.dumps(old), self.profile["account_id"]))
        status, entered = self.game_call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")
        self.assertEqual((status, entered["winNum"]), (200, 1))
        for before, after in zip(old["targets"], entered["asyncPvpTargetInfo"]):
            self.assertEqual(after["uid"], before["uid"])
            self.assertEqual(after["isWin"], before["isWin"])
            self.assertEqual(after["targetBattleActors"][:3], before["targetBattleActors"])
            self.assertEqual([a["id"] for a in after["targetBattleActors"][3:]], [0, 0, 0])
        with closing(self.game._connect()) as conn:
            saved = json.loads(conn.execute("SELECT state_json FROM local_tournament_state WHERE account_id=?",
                                           (self.profile["account_id"],)).fetchone()["state_json"])
        self.assertEqual(saved["claimed"], [1])
        self.assertEqual(saved["run"], old["run"])

    def test_invalid_native_routes_and_requests_do_not_write(self):
        self.call("GET", f"/fci/tournament/enter/5/{TOURNAMENT_ID}")
        before = self.saved_rows()
        for method, route, payload, status in [
            ("GET", "/fci/tournament/info/5/99999/", None, 404),
            ("GET", "/fci/tournament/enter/5/99999/", None, 404),
            ("POST", "/fci/tournament/enter/", {"activityType": 5, "tournamentId": 99999}, 404),
            ("POST", "/fci/tournament/dungeonenter/", {"activityType": 5, "tournamentId": TOURNAMENT_ID, "target_uid": "missing"}, 404),
            ("PATCH", "/fci/tournamentdungeon/123/active/", self.wire("FightActiveData", winTeam=1), 404),
            ("POST", "/fci/tournament/dungeonbalance/", {"activityType": 5}, 404),
            ("POST", "/fci/tournament/winreward/", {"activityType": 5, "tournamentId": TOURNAMENT_ID, "winNum": 1}, 409),
            ("POST", "/fci/asyncpvpmatch/", {"index": 99}, 400),
            ("GET", "/fci/asyncpvptargetdetail/missing", None, 404),
            ("POST", "/fci/tournament/enter/", b"\xff", 400),
            ("POST", "/fci/tournament/summary/", b"null", 405),
        ]:
            with self.subTest(route=route, payload=payload):
                self.assertEqual(self.game_call(method, route, payload)[0], status)
                self.assertEqual(self.saved_rows(), before)
        with closing(self.game.dungeons._connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM local_dungeons").fetchone()[0], 0)

    def test_other_modes_and_local_pve_are_not_claimed_without_writes(self):
        before = self.saved_rows()
        for route, payload in [
            ("/fci/tournament/enter/", {"stage": 1}),
            ("/fci/tournament/info/", None),
            ("/fci/tournament/123/balance", {}),
            ("/fci/tournament/info/18/18001", None),
            ("/fci/tournament/enter/", {"activityType": 18, "tournamentId": 18001}),
            ("/fci/tournament/enter/", self.wire("ApiTournamentEnter", tournament_id=18001, activity_type=18)),
            ("/fci/BurstLinkPVPInfo/", None),
            ("/fci/RacePVPInfo/", None),
            ("/fci/asyncpvp/match", {}),
        ]:
            with self.subTest(route=route):
                self.assertIsNone(self.call("GET", route, payload))
                self.assertEqual(self.saved_rows(), before)


if __name__ == "__main__":
    unittest.main()
