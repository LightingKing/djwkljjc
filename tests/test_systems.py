from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import equipment
from accounts import LocalAccounts
from game import LocalGame
from local_mailbox import send_mail


class LocalSystemsTests(unittest.TestCase):
    @staticmethod
    def _guest(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle(
            "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token
        )[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        second_token = "AccountToken " + second[1]["accountToken"]
        auth_body = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
        auth = accounts.handle("POST", "/Auth/", auth_body, "application/wireformat", second_token)
        return accounts.verify_access_token(auth[1]["tokenAccess"]), "Token " + auth[1]["tokenAccess"]

    def test_actor_gacha_equipment_mail_relation_and_pvp_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)
            # Enhance needs a real owned item plus food: grant equipment 20000
            # and one enhance material 40001 (uid 20001 never existed -> 404).
            profile = game._profile(account)
            equip_uid = equipment.grant_equipment(game, profile, 20000)["equip"]["uid"]
            state = game.systems._state(profile)
            state.setdefault("items", {})["40001"] = {"id": 40001, "count": 1}
            game.systems._save(profile, state)
            enhance_path = f"/fci/equip/{equip_uid}/enhance/"
            bodies = {enhance_path: {"foodRes": [{"type": 11, "id": 40001, "count": 1}]},
                      "/fci/gacha/": {"id": 1, "gachaFree": True}}  # the daily free draw
            cases = [
                ("POST", "/fci/gacha/", "ApiGachaTrade.result"),
                ("GET", "/fci/actor/1001/", "ApiActor.result"),
                ("POST", "/fci/actor/1001/levelup/", "ApiActorLevelUp.post"),
                ("GET", "/fci/equip/20001/", "ApiEquip.result"),
                ("POST", enhance_path, "ApiEquipEnhance.result"),
                ("POST", "/fci/mail/read/", "ApiMail.result"),
                ("POST", "/fci/relation/applyfriend/", "ApiRelationApplyFriend.result"),
                ("GET", "/fci/asyncpvprecord/", "ApiAsyncPvpRecordInfo.result"),
            ]
            # Pools are a plain JSON list (Window_GachaNormal finds id == 1).
            status, pools = game.handle_http("GET", "/fci/gachainfo/", None, "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(pools[0]["id"], 1)
            # MailUtil.OnMails reads mails/clearAll/delMails at the top level;
            # Window_Friend binds the friend list as a list of summaries.
            status, mails = game.handle_http("GET", "/fci/mail/", None, "application/json", auth)
            self.assertEqual(status, 200)
            self.assertIn("mails", mails)
            status, friends = game.handle_http("GET", "/fci/friendlist/", None, "application/json", auth)
            self.assertEqual(status, 200)
            self.assertIsInstance(friends, list)
            for method, path, expected in cases:
                body = None if method == "GET" else json.dumps(bodies.get(path, {"id": 1})).encode()
                status, result = game.handle_http(method, path, body, "application/json", auth)
                self.assertEqual(status, 200, path)
                self.assertEqual(result.DESCRIPTOR.full_name, expected, path)
                if path == "/fci/relation/applyfriend/":
                    self.assertEqual(result.result.player_id, "local-bot-001")
                    self.assertTrue(result.result.name)
            # Native async matching is open locally.  Deliver the refresh
            # ticket through the same mailbox path used by the client, then
            # claim it before refreshing; tests must not seed inventory state
            # behind the API.
            mail = send_mail(
                game,
                profile,
                "Arena test fixture",
                "Refresh ticket",
                [{"type": 11, "id": 80001, "count": 1}],
            )
            status, claimed = game.handle_http(
                "POST",
                f"/fci/mail/{mail['id']}/obtain",
                b"{}",
                "application/json",
                auth,
            )
            self.assertEqual(status, 200)
            self.assertTrue(claimed.result.attachments_got)
            status, refreshed = game.handle_http(
                "POST", "/fci/asyncpvpmatch/", b'{"index":1}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(refreshed.DESCRIPTOR.full_name, "ApiAsyncPvpMatch.result")
            self.assertEqual(refreshed.index, 1)
            self.assertEqual(game.systems._state(profile)["items"]["80001"]["count"], 0)
            # The enhance consumed its food and fed EXP into the real item.
            state = game.systems._state(profile)
            self.assertEqual(state["items"]["40001"]["count"], 0)
            self.assertGreater(state["equips"][str(equip_uid)]["exp"], 0)

            game_again = LocalGame(root / "game", accounts)
            status, actor = game_again.handle_http("GET", "/fci/actor/1001/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(actor.result.level, 2)

    def test_task_rows_include_safe_end_time_for_city_update(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)
            status, result = game.handle_http(
                "GET", "/fci/task/", authorization=auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(result["tasks"])
            task = result["tasks"][0]
            # endTime is always present; 0 means "no expiry" (TaskUtil only
            # expires tasks with endTime ~= 0, the slot hides its countdown).
            self.assertIn("endTime", task)
            self.assertTrue(task["endTime"] == 0 or task["endTime"] > game._now())
            # TaskUtil.Categorize sets timeout itself and fills ``res`` from
            # its own TaskInfo by id; dailyRefresh/actorCultivationVipOnly are
            # optional sort keys.  The server sends the progress fields.
            for key in ("id", "progress", "isDone", "rewardGot"):
                self.assertIn(key, task)

            # The city callback must be a harmless, idempotent POST.
            status, empty = game.handle_http(
                "POST",
                "/fci/task/target-done-add/",
                b'{"targetId":42}',
                authorization=auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(empty, {})
            status, result = game.handle_http("GET", "/fci/task/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(result["taskTargetDone"], [42])

            # A fresh LocalGame instance reads the normalized shape from the
            # existing SQLite state rather than recreating a sparse row.
            game_again = LocalGame(root / "game", accounts)
            status, persisted = game_again.handle_http("GET", "/fci/task/", authorization=auth)
            self.assertEqual(status, 200)
            persisted_task = persisted["tasks"][0]
            self.assertEqual(persisted_task["id"], task["id"])
            self.assertIn("endTime", persisted_task)

    def test_mail_attachment_claim_is_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)

            status, listing = game.handle_http("GET", "/fci/mail/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(len(listing["mails"]), 1)
            self.assertEqual(listing["mails"][0]["attachments"][0]["baseRes"]["count"], 100)
            self.assertFalse(listing["mails"][0]["attachmentsGot"])

            before = next(item["count"] for item in game._profile(account)["resources"]
                          if item["type"] == 7 and item["id"] == 0)
            status, claimed = game.handle_http(
                "POST", "/fci/mail/obtain/", b'{"id":1}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(claimed.result.id, 1)
            self.assertTrue(claimed.result.attachments_got)
            self.assertEqual(len(claimed.res_change), 1)
            self.assertEqual(claimed.res_change[0].base_res.count_delta, 100)

            after = next(item["count"] for item in game._profile(account)["resources"]
                         if item["type"] == 7 and item["id"] == 0)
            self.assertEqual(after - before, 100)

            # Repeating the same request is a successful no-op and cannot
            # duplicate the grant, which is required for client retries.
            status, repeated = game.handle_http(
                "POST", "/fci/mail/obtain/", b'{"id":1}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(repeated.res_change), 0)
            after_repeat = next(item["count"] for item in game._profile(account)["resources"]
                                if item["type"] == 7 and item["id"] == 0)
            self.assertEqual(after_repeat, after)

            restarted = LocalGame(root / "game", accounts)
            status, detail = restarted.handle_http(
                "GET", "/fci/mail/1/", authorization=auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(detail.result.attachments_got)

    def test_local_guild_donation_skill_and_records_are_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)
            status, joined = game.handle_http("POST", "/fci/guild/join/", b"{}", "application/json", auth)
            self.assertEqual(status, 200); self.assertTrue(joined["ok"])
            status, donated = game.handle_http("POST", "/fci/guild/donate/", b'{"amount":100}', "application/json", auth)
            self.assertEqual(status, 200); self.assertEqual(donated["contribution"], 100)
            status, skill = game.handle_http("POST", "/fci/guild/skill/", b'{"skillId":2}', "application/json", auth)
            self.assertEqual(status, 200); self.assertEqual(skill["level"], 1)
            restarted = LocalGame(root / "game", accounts)
            status, records = restarted.handle_http("GET", "/fci/guild/records/", authorization=auth)
            self.assertEqual(status, 200); self.assertEqual(records["records"][0]["type"], "donate")

    def test_local_guild_robot_roster_vote_support_and_dungeon_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)

            status, joined = game.handle_http("POST", "/fci/guild/join/", b"{}", "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(joined["members"], 6)

            status, roster = game.handle_http("GET", "/fci/guild/members/list", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(len(roster["members"]), 6)
            self.assertEqual(roster["members"][0]["role"], "president")
            self.assertTrue(any(member["isBot"] for member in roster["members"]))

            status, kicked = game.handle_http("POST", "/fci/guild/member/kick", b'{"playerId":"local-bot-001"}', "application/json", auth)
            self.assertEqual(status, 200)
            status, invited = game.handle_http("POST", "/fci/guild/member/invite", b"{}", "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(invited["playerId"], "local-bot-001")
            self.assertTrue(invited["accepted"])

            status, started = game.handle_http("POST", "/fci/guild/vote/start", b'{"playerId":"local-bot-002"}', "application/json", auth)
            self.assertEqual(status, 200)
            vote_id = started["vote"]["id"]
            status, cast = game.handle_http("POST", "/fci/guild/vote/cast", json.dumps({"voteId": vote_id, "agree": True}).encode(), "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(cast["vote"]["status"], "passed")
            status, transferred = game.handle_http("POST", "/fci/guild/transfer", b'{"playerId":"local-bot-002"}', "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(transferred["president"], "local-bot-002")

            status, requested = game.handle_http("POST", "/fci/guild/support/request", b'{"actorId":1001}', "application/json", auth)
            self.assertEqual(status, 200)
            request_id = requested["request"]["id"]
            self.assertEqual(requested["offer"]["status"], "offered")
            status, claimed = game.handle_http("POST", "/fci/guild/support/claim", json.dumps({"requestId": request_id}).encode(), "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(claimed["request"]["status"], "claimed")
            status, repeat = game.handle_http("POST", "/fci/guild/support/claim", json.dumps({"requestId": request_id}).encode(), "application/json", auth)
            self.assertEqual(status, 200)
            self.assertTrue(repeat["alreadyClaimed"])

            # The client uses both the historical support names and the
            # assist aliases depending on the build/configuration.
            status, assist = game.handle_http("POST", "/fci/guild/assist/request", b'{"actorId":1002}', "application/json", auth)
            self.assertEqual(status, 200)
            assist_id = assist["request"]["id"]
            status, assist_claim = game.handle_http("POST", "/fci/guild/assist/claim", json.dumps({"requestId": assist_id}).encode(), "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(assist_claim["request"]["status"], "claimed")

            status, cleared = game.handle_http("POST", "/fci/guild/dungeon/settle", b'{"stage":2,"score":250}', "application/json", auth)
            self.assertEqual((status, cleared["error"]), (404, "invalid_request"))
            status, first_keyed = game.handle_http("POST", "/fci/guild/dungeon/settle", b'{"stage":3,"score":40,"requestId":"guild-run-1"}', "application/json", auth)
            self.assertEqual(status, 404)
            status, repeated_keyed = game.handle_http("POST", "/fci/guild/dungeon/settle", b'{"stage":3,"score":40,"requestId":"guild-run-1"}', "application/json", auth)
            self.assertEqual((status, repeated_keyed), (404, first_keyed))
            restarted = LocalGame(root / "game", accounts)
            status, detail = restarted.handle_http("GET", "/fci/guild/summary", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(detail["guild"]["president"], "local-bot-002")
            self.assertNotIn("dungeon_clear", [record["type"] for record in detail["records"]])
            status, records = restarted.handle_http("GET", "/fci/guild-dungeon-record/", authorization=auth)
            self.assertEqual((status, records["error"]), (403, "guild_not_member"))

    def test_player_profile_level_background_collections_and_signboard_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._guest(accounts)

            status, base = game.handle_http("GET", "/fci/baseinfo/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(base["level"], 1)
            self.assertTrue(base["sandbox"])

            status, changed = game.handle_http(
                "POST", "/fci/socialpicture/",
                b'{"socialPicture":"local://avatar/1","sex":1}',
                "application/json", auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(changed["socialPicture"], "local://avatar/1")

            status, changed = game.handle_http(
                "PATCH", "/fci/home-back-ground/",
                b'{"homeBackGround":42,"isRandom":true}',
                "application/json", auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(changed["result"]["current"], 42)
            self.assertTrue(changed["result"]["isRandom"])

            status, changed = game.handle_http(
                "POST", "/fci/present-target-actor/",
                b'{"presentTargetActor":1002}',
                "application/json", auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(changed["result"]["presentTargetActor"], 1002)

            status, level = game.handle_http(
                "PATCH", "/fci/baseresource/level/", b'{"level":8}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(level["result"], 8)

            status, signboard = game.handle_http(
                "PATCH", "/fci/signboard/", b'{"signboard":202,"unlockedSignboardId":[202]}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(signboard.signboard, 202)

            status, collection = game.handle_http(
                "POST", "/fci/collections/actor/", b'{"category":"actor","id":1002}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual([item.id for item in collection.actors], [1002])

            status, cover = game.handle_http(
                "POST", "/fci/novel-cover/", b'{"coverId":7}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual([item.novel_id for item in cover.novels], [7])

            status, note = game.handle_http(
                "POST", "/fci/role-notepad/", b'{"actorId":1001,"text":"local note"}', "application/json", auth
            )
            self.assertEqual(status, 200)
            # Notepads are keyed by Role id: character 1001 belongs to Role 1.
            self.assertEqual(note.role_notepad.role_id, 1)

            restarted = LocalGame(root / "game", accounts)
            status, base = restarted.handle_http("GET", "/fci/baseinfo/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(base["level"], 8)
            self.assertEqual(base["homeBackGround"]["current"], 42)
            status, stored_signboard = restarted.handle_http("GET", "/fci/signboard/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(stored_signboard["signboard"], 202)
            status, stored_collections = restarted.handle_http("GET", "/fci/collections/actor/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual([item.id for item in stored_collections.actors], [1002])


if __name__ == "__main__":
    unittest.main()
