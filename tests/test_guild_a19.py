"""A19 native guild notifications, shared economy and login-redpoint contracts."""
import unittest
from unittest.mock import patch

from google.protobuf import json_format, message_factory

from game import LocalGame
from guild import GuildError
from local_mailbox import send_mail
import test_guild_client as harness


class GuildA19Tests(unittest.TestCase):
    def setUp(self):
        harness.GuildClientTests.setUp(self)
        self.native = harness.GuildClientTests.native.__get__(self)
        self.new_player = harness.GuildClientTests.new_player.__get__(self)
        self.balance = harness.GuildClientTests.balance.__get__(self)

    def tearDown(self):
        harness.GuildClientTests.tearDown(self)

    def grant(self, resources):
        mail = send_mail(self.game, self.profile, "Isolated A19 fixture", "Resources", resources)
        self.assertEqual(self.native("PATCH", f"/fci/mail/{mail['id']}", {"attachmentsGot": True})[0], 200)

    def create(self):
        status, result = self.native("POST", "/fci/guild/", {"name": "A19公会", "icon": "1"}, message="ApiGuild")
        self.assertEqual(status, 200, result)
        return result["baseInfo"]["id"]

    def test_native_invitation_refreshes_recipient_and_manager(self):
        gid = self.create()
        target, auth = self.new_player()
        self.pushes.clear()
        status, result = self.native("PUT", f"/fci/guild/{gid}/invited-members/",
            {"playerId": target["player_id"]}, message="ApiGuildMemberInvite")
        self.assertEqual(status, 200, result)
        self.assertIn((target["account_id"], 28, ()), self.pushes)
        self.assertIn((self.profile["account_id"], 53, ()), self.pushes)
        invited = self.native("GET", "/fci/guild-player/invited/", auth=auth)[1]
        self.assertEqual([row["id"] for row in invited], [gid])
        self.pushes.clear()
        self.assertEqual(self.native("DELETE", f"/fci/guild-player/invited/{gid}/", auth=auth)[0], 200)
        self.assertIn((target["account_id"], 28, ()), self.pushes)
        self.assertEqual(self.native("GET", "/fci/guild-player/invited/", auth=auth)[1], [])

    def test_record_and_system_pushes_match_persisted_native_records(self):
        gid = self.create()
        status, records = self.native("GET", f"/fci/guild/{gid}/record/")
        self.assertEqual(status, 200)
        self.assertEqual(len(records), 1)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiGuildRecord.result"))
        for record in records:
            json_format.ParseDict(record, cls())
        self.assertIn((self.profile["account_id"], 31, (records[0]["content"],)), self.pushes)
        self.assertIn((self.profile["account_id"], 54, ()), self.pushes)
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/record/")[1], records)
        # Retrying the actual create request is idempotent and creates no
        # additional journal entry or cost.
        self.assertEqual(self.create(), gid)
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/record/")[1], records)

    def test_failed_guild_save_rolls_back_resources_records_and_pushes(self):
        before = self.balance(7)
        self.pushes.clear()
        with patch.object(self.game.guild, "_save", side_effect=RuntimeError("injected guild save failure")):
            with self.assertRaisesRegex(RuntimeError, "injected guild save failure"):
                self.native("POST", "/fci/guild/", {"name": "回滚验证", "icon": "1"}, message="ApiGuild")
        self.assertEqual(self.balance(7), before)
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.native("GET", "/fci/guild-player/")[1]["guildId"], 0)

    def test_resource_adapter_uses_canonical_total_paid_coupling_and_rollback(self):
        # This is an internal transaction test, not a claim that the client
        # sends custom currency changes. Current native guild creation costs
        # gold; the shared adapter must obey economy invariants for any grant.
        self.grant([{"type": 5, "count": 100}, {"type": 27, "count": 200}])
        self.assertEqual((self.balance(5), self.balance(27)), (300, 200))

        def change(conn, state, player, now, notices):
            return self.game.guild._resources(conn, self.profile, [{"type": 5, "count": -150}], now)

        changes = self.game.guild._run(self.profile, change)
        self.assertEqual((self.balance(5), self.balance(27)), (150, 150))
        self.assertEqual({row["baseRes"]["type"]: row["baseRes"]["countDelta"] for row in changes}, {5: -150, 27: -50})
        with patch.object(self.game.guild, "_save", side_effect=RuntimeError("rollback after spend")):
            with self.assertRaisesRegex(RuntimeError, "rollback after spend"):
                self.game.guild._run(self.profile, change)
        self.assertEqual((self.balance(5), self.balance(27)), (150, 150))
        with self.assertRaises(GuildError):
            self.game.guild._run(self.profile, lambda c, s, p, n, q:
                self.game.guild._resources(c, self.profile, [{"type": 5, "count": -151}], n))
        self.assertEqual((self.balance(5), self.balance(27)), (150, 150))

    def test_legacy_guild_dungeon_cannot_fabricate_progress(self):
        before = self.game.systems._state(self.profile).get("guild")
        status,result=self.native("POST","/fci/guild/dungeon/settle",{"stage":99,"score":100000})
        self.assertEqual((status,result["error"]),(404,"invalid_request"))
        for method, route in (("POST", "/fci/guild-dungeon/"),
                              ("GET", "/fci/guild-dungeon-record/"),
                              ("GET", "/fci/guild/1/dungeon/summary/")):
            with self.subTest(route=route):
                status, result = self.native(method, route, {"stage": 99, "score": 100000})
                self.assertEqual((status, result["error"]), (403, "guild_not_member"))
        self.assertEqual(self.game.systems._state(self.profile).get("guild"), before)
        self.assertFalse(any(kind == 87 for _, kind, _ in self.pushes))

    def test_login_redpoint_uses_actual_ten_day_signin_state(self):
        status, redpoints = self.native("GET", "/fci/redpoint/data")
        self.assertEqual(status, 200)
        self.assertEqual(redpoints["activitySigninGet"], {"activityId": 32001, "day": 1, "signed": False})
        self.assertEqual(self.native("POST", "/fci/activity-signin/32001")[0], 200)
        self.assertEqual(self.native("GET", "/fci/redpoint/data")[1]["activitySigninGet"],
                         {"activityId": 32001, "day": 1, "signed": True})
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.assertTrue(self.native("GET", "/fci/redpoint/data")[1]["activitySigninGet"]["signed"])
        self.now[0] += 86400
        self.assertEqual(self.native("GET", "/fci/redpoint/data")[1]["activitySigninGet"],
                         {"activityId": 32001, "day": 2, "signed": False})


if __name__ == "__main__":
    unittest.main()
