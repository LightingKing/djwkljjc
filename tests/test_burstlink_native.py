from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest
from contextlib import closing

from google.protobuf import message_factory

from accounts import LocalAccounts
from game import LocalGame


class NativeBurstLinkTests(unittest.TestCase):
    @staticmethod
    def guest(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        auth = accounts.handle(
            "POST", "/Auth/", b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest",
            "application/wireformat", "AccountToken " + second[1]["accountToken"],
        )
        return accounts.verify_access_token(auth[1]["tokenAccess"]), "Token " + auth[1]["tokenAccess"]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts, clock=lambda: 1793332800,
                              initial_level=30, initial_actor_ids=[1001, 1002, 1003, 1004])
        self.account, self.auth = self.guest(self.accounts)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None, content_type="application/json"):
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def test_native_info_actor_selection_accepts_wire_request(self):
        status, info = self.call("GET", "/fci/BurstLinkPvpInfo/")
        self.assertEqual(status, 200)
        self.assertEqual((info["dungeonId"], info["tickets"], info["maxTickets"]), (4001, 2, 10))
        status, actors = self.call("GET", "/fci/BurstLinkPVPRandActorInfo/1/")
        self.assertEqual(status, 200)
        self.assertTrue(actors["randActor"])
        self.assertEqual(len({int(actor["uid"]) for actor in actors["randActor"]}),
                         len(actors["randActor"]))
        uid = int(actors["randActor"][0]["uid"])
        request_type = self.game._pool.FindMessageTypeByName("ApiBurstLinkPVPRandActor")
        request = message_factory.GetMessageClass(request_type)(actor_uid=uid).SerializeToString()
        status, selected = self.call("PUT", "/fci/BurstLinkPVPRandActorInfo/1/", request, "application/wireformat")
        self.assertEqual(status, 200)
        self.assertIn(uid, [int(value) for value in selected["curMainActor"]])

    def test_tournament_summary_exposes_native_burst_entry_and_join_status(self):
        status, summary = self.call("GET", "/fci/tournament/summary/")
        self.assertEqual(status, 200)
        burst = next(row for row in summary["activityInfo"] if row["activity"]["type"] == 18)
        self.assertEqual((burst["activity"]["activityId"], burst["status"]), (18001, 0))
        status, joined = self.call("POST", "/fci/BurstLinkPvpInfo/", {})
        self.assertEqual(status, 200)
        status, summary = self.call("GET", "/fci/tournament/summary/")
        burst = next(row for row in summary["activityInfo"] if row["activity"]["type"] == 18)
        self.assertEqual(burst["status"], 1)

    def test_native_match_push_shape_and_idempotent_balance(self):
        self.assertEqual(self.call("POST", "/fci/BurstLinkPvpInfo", {})[0], 200)
        status, matching = self.call("POST", "/match/BurstLinkPVPMatch", b"{}")
        self.assertEqual((status, matching["maxWaitTime"]), (200, 1))
        profile = self.game._profile(self.account)
        state = self.game.realtime_pvp._state(profile)
        dungeon = state["burst"]["native"]["active"]["dungeon"]
        self.assertEqual(dungeon["dungeonType"], 8)
        status, result = self.call("POST", f"/fci/dungeon/{dungeon['dungeonUid']}/active/",
                                   self.game._types["FightActiveData"](
                                       dungeonType=8, stageID=4001, gameRound=5, winTeam=1
                                   ).SerializeToString(), "application/wireformat")
        self.assertEqual((status, result), (200, {}))
        status, result = self.call("POST", "/fci/BurstLinkDunBalance/", b"{}")
        self.assertEqual((status, result["isWin"]), (200, True))
        status, duplicate = self.call("POST", "/fci/BurstLinkDunBalance/", b'{"win":false}')
        self.assertEqual((status, duplicate["isWin"]), (200, True))

    def test_matching_does_not_charge_again_within_registered_round(self):
        self.assertEqual(self.call("POST", "/fci/BurstLinkPvpInfo", {})[0], 200)
        before = self.game.realtime_pvp._state(self.game._profile(self.account))["burst"]["tickets"]
        status, matching = self.call("POST", "/match/BurstLinkPVPMatch", b"{}")
        self.assertEqual(status, 200)
        first_uid = matching["dungeonInfo"]["dungeonUid"]
        active = self.game._types["FightActiveData"](dungeonType=8, stageID=4001, gameRound=5, winTeam=1)
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{first_uid}/active/", active.SerializeToString(), "application/wireformat")[0], 200)
        self.assertEqual(self.call("POST", "/fci/BurstLinkDunBalance/", b"{}")[0], 200)
        status, matching = self.call("POST", "/match/BurstLinkPVPMatch", b"{}")
        self.assertEqual(status, 200)
        after = self.game.realtime_pvp._state(self.game._profile(self.account))["burst"]["tickets"]
        self.assertEqual(after, before)

    def test_claim_wraps_rewards_and_clears_round(self):
        profile = self.game._profile(self.account)
        state = self.game.realtime_pvp._state(profile)
        state["burst"]["wins"] = 5
        state["burst"]["losses"] = 0
        native = self.game.realtime_pvp._native(state)
        native["received"] = False
        native["active"] = {"uid": 1, "balanced": True}
        self.game.realtime_pvp._save(profile, state)
        status, result = self.call("POST", "/fci/BurstLinkPvpBalance/", b"{}")
        self.assertEqual(status, 200)
        self.assertTrue(result["resChange"])
        self.assertTrue(all("baseRes" in row for row in result["resChange"]))
        state = self.game.realtime_pvp._state(profile)
        self.assertIsNone(state["burst"]["native"]["active"])
        self.assertEqual((state["burst"]["wins"], state["burst"]["losses"]), (0, 0))
        status, duplicate = self.call("POST", "/fci/BurstLinkPvpBalance/", b"{}")
        self.assertEqual((status, duplicate["resChange"]), (200, []))

    def test_burstlink_requires_level_28_for_join_and_match(self):
        profile = self.game._profile(self.account)
        with closing(self.game._connect()) as connection, connection:
            connection.execute("UPDATE game_profiles SET level = 27 WHERE account_id = ?", (profile["account_id"],))
        status, result = self.call("POST", "/fci/BurstLinkPvpInfo", {})
        self.assertEqual((status, result["error"]), (403, "burstlink_level_locked"))
        status, result = self.call("POST", "/match/BurstLinkPVPMatch", b"{}")
        self.assertEqual((status, result["error"]), (403, "burstlink_level_locked"))

    def test_empty_balance_without_terminal_snapshot_is_rejected(self):
        self.assertEqual(self.call("POST", "/fci/BurstLinkPvpInfo", {})[0], 200)
        status, matching = self.call("POST", "/match/BurstLinkPVPMatch", b"{}")
        self.assertEqual(status, 200)
        status, result = self.call("POST", "/fci/BurstLinkDunBalance/", b"{}")
        self.assertEqual((status, result["error"]), (409, "burstlink_battle_result_required"))

    def test_balance_uses_fight_active_snapshot_instead_of_guessed_win(self):
        self.assertEqual(self.call("POST", "/fci/BurstLinkPvpInfo", {})[0], 200)
        status, matching = self.call("POST", "/match/BurstLinkPVPMatch", b"{}")
        self.assertEqual(status, 200)
        uid = matching["dungeonInfo"]["dungeonUid"]
        active = self.game._types["FightActiveData"](
            dungeonType=8, stageID=4001, gameRound=5, winTeam=2
        )
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{uid}/active/",
                                   active.SerializeToString(), "application/wireformat")[0], 200)
        status, result = self.call("POST", "/fci/BurstLinkDunBalance/", b'{"win":true}')
        self.assertEqual((status, result["isWin"]), (200, False))

    def test_native_group_rejects_unknown_and_duplicate_actor_uids(self):
        status, actors = self.call("GET", "/fci/BurstLinkPVPRandActorInfo/1/")
        self.assertEqual(status, 200)
        uid = int(actors["randActor"][0]["uid"])
        unknown = {"actorGroups": {"1": {"members": [{"index": 0, "actorUid": 999999}]}}}
        status, result = self.call("PUT", "/fci/BurstLinkPvpActorGroup/", unknown)
        self.assertEqual((status, result["error"]), (409, "burstlink_actor_not_owned"))
        duplicate = {"actorGroups": {"1": {"members": [
            {"index": 0, "actorUid": uid}, {"index": 1, "actorUid": uid}]}}}
        status, result = self.call("PUT", "/fci/BurstLinkPvpActorGroup/", duplicate)
        self.assertEqual((status, result["error"]), (409, "burstlink_actor_duplicate"))


if __name__ == "__main__":
    unittest.main()
