"""Real guild routes, descriptor contracts and SQLite failure/retry behavior."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.protobuf import json_format, message_factory
from accounts import LocalAccounts
from game import LocalGame
from grants import grant_rewards
from guild import LocalGuild, RULES
from profile_features import actor_role, role_head_photo
import test_systems


class GuildClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [int(datetime(2026, 10, 30, 4, tzinfo=timezone.utc).timestamp())]
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0],
                              initial_level=23, initial_actor_ids=[1001, 1002],
                              initial_resources=[{"type": 7, "id": 0, "param": 0, "count": 200000}])
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.guild = LocalGuild(self.game)
        self.pushes = []
        self.game.base_push_notifier = lambda profile, kind, params=(): self.pushes.append((profile["account_id"], kind, tuple(params)))

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, body=None, module=None, profile=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        return (module or self.guild).handle_http(method, path, raw, profile or self.profile)

    def join(self, gid=1, profile=None):
        target = profile or self.profile
        status, result = self.call("PUT", f"/fci/guild/{gid}/pending-members/", {"playerId": target["player_id"]}, profile=target)
        self.assertEqual(status, 200, result)

    def native(self, method, path, body=None, auth=None, message=None):
        if message:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(message))
            raw = json_format.ParseDict(body or {}, cls()).SerializeToString()
            content_type = "application/wireformat"
        else:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            content_type = "application/json"
        return self.game.handle_http(method, path, raw, content_type, auth or self.auth)

    def new_player(self):
        account, auth = test_systems.LocalSystemsTests._guest(self.accounts)
        return self.game._profile(account), auth

    def balance(self, kind, rid=0):
        if kind == 11:
            return self.game.systems._state(self.profile)["items"].get(str(rid), {}).get("count", 0)
        fresh = self.game._profile(self.account)
        return sum(r["count"] for r in fresh["resources"] if r["type"] == kind and r["id"] == rid)

    def assert_message(self, name, payload):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        msg = json_format.ParseDict(payload, cls())
        decoded = cls.FromString(msg.SerializeToString())
        self.assertEqual(decoded, msg)

    def earn_guild_score(self, gid, rounds=1):
        """Only inventory is a fixture; popularity comes from native sends."""
        for round_index in range(rounds):
            if round_index:
                self.now[0] = self.native("GET", f"/fci/guild/{gid}/donate/")[1]["nextBalanceTime"]
            info = self.native("GET", f"/fci/guild/{gid}/donate/")[1]
            self.assertEqual(len(info["donates"]), 5)
            for wish in info["donates"].values():
                res = dict(wish["donateRes"], count=10)
                grant_rewards(self.game, self.profile, [res])
                for _ in range(10):
                    status, result = self.native("POST", f"/fci/guild/{gid}/donate/send/",
                        {"playerIdRequest": wish["requestPlayerId"], "res": dict(res, count=1)},
                        message="ApiGuildDonateSend")
                    self.assertEqual(status, 200, result)

    def test_self_created_guild_has_finite_native_wishes_and_visible_bot_profiles(self):
        status, created = self.native("POST", "/fci/guild/", {"name": "互助来源", "icon": "1"}, message="ApiGuild")
        self.assertEqual(status, 200)
        gid = created["baseInfo"]["id"]
        self.assertEqual(created["baseInfo"]["membersCount"], 6)
        self.earn_guild_score(gid)
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/")[1]["baseInfo"]["score"], 500)
        wishes = self.native("GET", f"/fci/guild/{gid}/donate/")[1]
        for target, wish in wishes["donates"].items():
            self.assertEqual(sum(wish["donatesCount"].values()), 10)
            status, summary = self.native("GET", f"/fci/playersummary/{target}")
            self.assertEqual((status, summary["playerId"]), (200, target))
            self.assert_message("PlayerSummaryInfo", summary)
            self.assertEqual(self.native("POST", f"/fci/guild/{gid}/donate/send/",
                {"playerIdRequest": target, "res": dict(wish["donateRes"], count=1)},
                message="ApiGuildDonateSend"), (409, {"error": "donation_request_full"}))
        fresh = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.assertEqual(fresh.guild.player_data(fresh._profile(self.account))["guildScoreRecord"], 500)
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/donate/")[1], wishes)
        self.now[0] = wishes["nextBalanceTime"]
        renewed = self.native("GET", f"/fci/guild/{gid}/donate/")[1]
        self.assertEqual(len(renewed["donates"]), 5)
        self.assertTrue(all(not row["donatesCount"] for row in renewed["donates"].values()))

    def test_native_rename_icon_fees_validation_retry_and_rollback(self):
        gid = self.native("POST", "/fci/guild/", {"name": "改名收费", "icon": "1"})[1]["baseInfo"]["id"]
        path = f"/fci/guild/{gid}/"
        before = self.native("GET", path)[1]
        self.assertEqual(self.native("PATCH", path, {"name": "未够人气"}, message="ApiGuild"),
                         (409, {"error": "guild_score_insufficient"}))
        self.assertEqual(self.native("GET", path)[1], before)
        self.earn_guild_score(gid, rounds=4)
        gold = self.balance(7)
        before = self.native("GET", path)[1]
        for change in ({"name": "本地互助社"}, {"icon": "7"}, {"name": "改名不提交", "icon": "invalid"}):
            self.assertIn(self.native("PATCH", path, change, message="ApiGuild")[0], (400, 409))
            self.assertEqual(self.native("GET", path)[1], before)
        with patch.object(self.game.guild, "_save", side_effect=RuntimeError("rename save failed")):
            with self.assertRaisesRegex(RuntimeError, "rename save failed"):
                self.native("PATCH", path, {"name": "失败改名"}, message="ApiGuild")
        self.assertEqual(self.native("GET", path)[1], before)
        status, renamed = self.native("PATCH", path, {"name": "有效新名"}, message="ApiGuild")
        self.assertEqual((status, renamed["baseInfo"]["score"]), (200, 1000))
        self.assertEqual(self.native("PATCH", path, {"name": "有效新名"}, message="ApiGuild")[1], renamed)
        status, changed = self.native("PATCH", path, {"icon": "3"}, message="ApiGuild")
        self.assertEqual((status, changed["baseInfo"]["score"], changed["baseInfo"]["icon"]), (200, 0, "3"))
        self.assertEqual(self.balance(7), gold)

    def test_native_group_unlink_null_and_scope(self):
        gid = self.native("POST", "/fci/guild/", {"name": "本地解绑", "icon": "1"})[1]["baseInfo"]["id"]
        self.native("PATCH", f"/fci/guild/{gid}/socialgroup/", {"qqGroupId": "12345"}, message="ApiGuildSocialGroup")
        self.assertEqual(self.native("POST", "/fci/guild/1/group/unlink/", b"null")[0], 403)
        self.assertEqual(self.native("POST", f"/fci/guild/{gid}/group/unlink/", b"null"), (200, {"ret": 0, "errorcode": 0}))
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/")[1]["baseInfo"]["qqGroup"], "")

    def test_native_vote_auto_start_one_ballot_and_succession(self):
        gid = self.native("POST", "/fci/guild/", {"name": "投票接任", "icon": "1"})[1]["baseInfo"]["id"]
        member, auth = self.new_player()
        pid = member["player_id"]
        self.native("PUT", f"/fci/guild/{gid}/invited-members/", {"playerId": pid}, message="ApiGuildMemberInvite")
        self.native("PUT", f"/fci/guild/{gid}/members/", {"playerId": pid}, auth=auth, message="ApiGuildMembers")
        path = f"/fci/guild/{gid}/vote/"
        self.now[0] += RULES["voteStartPresidentLeave"] - 1
        self.assertFalse(self.native("GET", path, auth=auth)[1]["inVote"])
        self.now[0] += 1
        status, vote = self.native("GET", path, auth=auth)
        self.assertEqual(status, 200)
        self.assertTrue(vote["inVote"])
        self.assertEqual(vote["endTime"], self.now[0] + RULES["voteLastTime"])
        self.assert_message("DbGuildData.VoteInfo", vote)
        status, vote = self.native("POST", path, {"voteSupport": False}, auth=auth, message="ApiGuildVotePresident")
        self.assertEqual((status, vote["playersKick"], vote["playersSupport"]), (200, [pid], []))
        self.assertEqual(self.native("POST", path, {"voteSupport": False}, auth=auth, message="ApiGuildVotePresident")[1], vote)
        self.assertEqual(self.native("POST", path, {"voteSupport": True}, auth=auth, message="ApiGuildVotePresident")[0], 409)
        self.now[0] = vote["endTime"]
        self.assertFalse(self.native("GET", path, auth=auth)[1]["inVote"])
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/", auth=auth)[1]["baseInfo"]["president"], pid)
        self.assertEqual(self.native("GET", "/fci/guild-player/", auth=auth)[1]["type"], 2)
        self.assertTrue(any(kind == 57 for _, kind, _ in self.pushes))

    def test_native_vote_failed_cooldown_and_president_login_resets_inactivity(self):
        self.join()
        path = "/fci/guild/1/vote/"
        self.now[0] += RULES["voteStartPresidentLeave"]
        vote = self.native("GET", path)[1]
        self.assertTrue(vote["inVote"])
        self.native("POST", path, {"voteSupport": True}, message="ApiGuildVotePresident")
        self.now[0] = vote["endTime"]
        self.assertFalse(self.native("GET", path)[1]["inVote"])
        self.now[0] += RULES["voteCoolDown"] - 1
        self.assertFalse(self.native("GET", path)[1]["inVote"])
        self.now[0] += 1
        self.assertTrue(self.native("GET", path)[1]["inVote"])

        owner, owner_auth = self.new_player()
        gid = self.native("POST", "/fci/guild/", {"name": "在线会长", "icon": "1"}, auth=owner_auth)[1]["baseInfo"]["id"]
        member, auth = self.new_player()
        self.native("PUT", f"/fci/guild/{gid}/invited-members/", {"playerId": member["player_id"]}, auth=owner_auth)
        self.native("PUT", f"/fci/guild/{gid}/members/", {"playerId": member["player_id"]}, auth=auth)
        self.now[0] += RULES["voteStartPresidentLeave"]
        # Login uses this path even when the player never opens the guild UI.
        self.game.guild.player_data(owner)
        self.assertFalse(self.native("GET", f"/fci/guild/{gid}/vote/", auth=auth)[1]["inVote"])

    def test_guild_bot_lookup_does_not_accept_unsolicited_friend_approval(self):
        created = self.native("POST", "/fci/guild/", {"name": "机器人资料", "icon": "1"})[1]
        gid = created["baseInfo"]["id"]
        targets = [member["playerId"] for member in created["members"] if member["playerId"] != self.profile["player_id"]]
        target = targets[0]
        before = self.native("GET", "/fci/friendlist")[1]
        self.native("POST", "/fci/applylist", {"targetList": [target], "isAgree": True}, message="ApiRelationApplyList")
        self.assertEqual(self.native("GET", "/fci/friendlist")[1], before)
        self.assertEqual(self.native("POST", "/fci/applyfriend", {"targetId": target}, message="ApiRelationApplyFriend")[0], 200)
        for pid in targets:
            self.assertEqual(self.native("DELETE", f"/fci/guild/{gid}/members/{pid}")[0], 200)
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/")[1]["baseInfo"]["membersCount"], 1)
        self.assertEqual(self.native("POST", f"/fci/guild/{gid}/members/quit/", {"playerId": self.profile["player_id"]}, message="ApiGuildMembersQuit")[0], 200)
        self.assertEqual(self.native("GET", f"/fci/playersummary/{target}")[1]["playerId"], target)
        self.assertIn(target, [row["playerId"] for row in self.native("GET", "/fci/friendlist")[1]])

    def test_legacy_self_created_guild_migrates_without_refilling_existing_wish(self):
        created = self.native("POST", "/fci/guild/", {"name": "旧公会迁移", "icon": "1"})[1]
        gid = created["baseInfo"]["id"]
        self.native("POST", f"/fci/guild/{gid}/donate/request/", {"res": {"type": 11, "id": 20001}}, message="ApiGuildDonateRequest")
        # Migration-only fixture reproduces the released one-member guild.
        with closing(self.game._connect()) as conn, conn:
            state = self.guild._load(conn, self.now[0])
            guild = state["guilds"][str(gid)]
            wish = guild["donateInfo"]["donates"][self.profile["player_id"]]
            guild["members"] = {self.profile["player_id"]: guild["members"][self.profile["player_id"]]}
            guild["botIds"] = []
            guild.pop("localBotVersion")
            guild.pop("localBots")
            guild["donateInfo"]["donates"] = {self.profile["player_id"]: wish}
            self.guild._save(conn, state, self.now[0])
        info = self.native("GET", f"/fci/guild/{gid}/donate/")[1]
        self.assertEqual(len(info["donates"]), 6)
        self.assertEqual(info["donates"][self.profile["player_id"]], wish)
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/donate/")[1], info)

    def test_routes_membership_and_descriptor_roundtrip_persist(self):
        status, recommendations = self.call("POST", "/fci/guild-recommend/")
        self.assertEqual(status, 200)
        self.assertEqual(len(recommendations), 3)
        for row in recommendations:
            self.assert_message("DbGuildData.BaseInfo", row)
        query = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiGuildSearch"))(id_or_name="1")
        self.assertEqual(self.call("POST", "/fci/guild-search/", query.SerializeToString())[1], [recommendations[0]])
        self.assertIsNone(self.call("GET", "/fci/guild/unimplemented/"))
        self.join()
        state = self.guild.player_data(self.profile)
        self.assertEqual(state["guildId"], 1)
        self.assertEqual(state["joinTime"], self.now[0])
        self.assert_message("DBGamePlayer.GuildData", state)
        status, detail = self.call("GET", "/fci/guild/1/")
        self.assertEqual(status, 200)
        self.assert_message("ApiGuild.result", detail)
        self.assertEqual(detail["baseInfo"]["membersCount"], 6)
        self.assertEqual(sum(m["playerId"] == self.profile["player_id"] for m in detail["members"]), 1)
        reboot = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        restored = LocalGuild(reboot)
        self.assertEqual(restored.player_data(self.profile)["guildId"], 1)
        self.assertTrue(any(kind == 25 for _, kind, _ in self.pushes))
        self.assertEqual(self.call("GET", "/fci/guild/1/record/")[1], [])
        self.assertEqual(self.call("GET", "/fci/guild/1/skill/")[1], detail["skills"])
        self.assert_message("DbGuildData.VoteInfo", self.call("GET", "/fci/guild/1/vote/")[1])

    def test_native_null_recommend_and_touch_keep_route_field_validation(self):
        def native(method, path, raw):
            return self.game.handle_http(method, path, raw, "application/json", self.auth)
        expected = native("POST", "/fci/guild-recommend/", b"{}")
        self.assertEqual(expected[0], 200)
        self.assertEqual(native("POST", "/fci/guild-recommend/", b"null"), expected)
        self.assertEqual(native("POST", "/fci/guild-recommend/", b""), expected)
        self.join()
        self.assertEqual(native("POST", "/fci/guild/1/donate/touch/", b"null"),
                         (409, {"error": "no_received_donation"}))
        self.call("POST", "/fci/guild/1/donate/request/", {"res": {"type": 11, "id": 20001}})
        status, touched = native("POST", "/fci/guild/1/donate/touch/", b"null")
        self.assertEqual(status, 200)
        self.assertGreater(touched["loveScore"], 0)
        self.assert_message("DbGuildData.DonateSingleInfo", touched)
        self.assertEqual(native("POST", "/fci/guild/1/donate/touch/", b"{}"), (200, touched))
        self.assertEqual(native("POST", "/fci/guild/1/donate/request/", b"null"),
                         (400, {"error": "invalid_shard"}))
        for raw in (b"[]", b"false", b"1", b'"text"'):
            with self.subTest(raw=raw):
                self.assertEqual(native("POST", "/fci/guild-recommend/", raw),
                                 (400, {"error": "invalid_request"}))

    def test_four_native_skill_slots_expose_profit_contract_and_activation(self):
        status, detail = self.call("POST", "/fci/guild/", {"name": "技能测试", "icon": "1"})
        self.assertEqual(status, 200)
        gid = detail["baseInfo"]["id"]
        skills = self.call("GET", f"/fci/guild/{gid}/skill/")[1]
        self.assertEqual([row["id"] for row in skills], [101, 102, 103, 104])
        self.assertEqual([row["add"] for row in skills], [1500, 2000, 1500, 2000])
        self.assertEqual([row["type"] for row in skills], [3, 8, 8, 8])
        for row in skills:
            self.assert_message("ApiGuildSkill.result", row)
            self.assertEqual(row["time"], 43200)
            self.assertEqual((row["startTime"], row["endTime"], row["maxUseCount"], row["scoreCost"]), (0, 0, 1, 500))
        self.earn_guild_score(gid)
        request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiGuildSkill"))(id=101, use_count=1)
        status, result = self.call("POST", f"/fci/guild/{gid}/skill/", request.SerializeToString())
        self.assertEqual(status, 200)
        self.assertEqual(result["guildSkillInfo"]["id"], 101)
        self.assertEqual(result["guildSkillInfo"]["endTime"] - result["guildSkillInfo"]["startTime"], 43200)
        self.assertEqual(result["baseInfo"]["score"], 0)
        self.assertEqual(result["profit"]["id"], 101)
        self.assert_message("ApiGuildSkill.post", result)
        self.assertEqual(self.guild.active_profit_rules(self.profile)[0]["dungeonType"], [2])
        self.assertEqual(self.call("POST", f"/fci/guild/{gid}/skill/", request.SerializeToString()),
                         (409, {"error": "guild_skill_opened_before"}))
        self.now[0] += 43200
        self.assertEqual(self.call("GET", f"/fci/guild/{gid}/skill/")[1][0]["startTime"], 0)
        self.assertEqual(self.guild.active_profits(self.profile), [])

    def test_saved_guild_repairs_photos_and_adds_wish_without_discarding_donations(self):
        self.join()
        with closing(self.game._connect()) as conn, conn:
            state = self.guild._load(conn, self.now[0])
            guild = state["guilds"]["1"]
            for member in guild["members"].values():
                member["headPhoto"] = 1001
            old = guild["donateInfo"]["donates"]["local-bot-003"]
            old["donateRes"]["id"] = 20003
            old["donatesCount"][self.profile["player_id"]] = 1
            self.guild._save(conn, state, self.now[0])
        appearance = self.game.profile_features._state(self.profile)
        appearance.update(headPhoto=0, socialPicture="local://avatar")
        self.game.profile_features._save(self.profile, appearance)
        restored = LocalGuild(LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0]))
        detail = self.call("GET", "/fci/guild/1/", module=restored)[1]
        bots = {bot["playerId"]: bot for bot in self.game.social.bots.all()}
        for member in detail["members"]:
            if member["playerId"] in bots:
                self.assertEqual(member["headPhoto"], role_head_photo(actor_role(bots[member["playerId"]]["actors"][0])))
            else:
                self.assertEqual((member["headPhoto"], member["socialPicture"]), (0, "local://avatar"))
        requests = detail["donateInfo"]["donates"]
        self.assertEqual(requests["local-bot-003"]["donatesCount"], {self.profile["player_id"]: 1})
        self.assertTrue({20002, 20014} <= {r["donateRes"]["id"] for r in requests.values()})
        self.assertEqual(self.call("GET", "/fci/guild/1/", module=restored)[1], detail)

    def test_quit_retry_and_second_quit_function_cooldown(self):
        self.join()
        request = {"playerId": self.profile["player_id"]}
        first = self.call("POST", "/fci/guild/1/members/quit/", request)[1]
        self.assertEqual(first["quitCount"], 1)
        self.assertEqual(first["limitEndTime"], 0)
        self.assertEqual(self.call("POST", "/fci/guild/1/members/quit/", request)[1], first)
        self.join()
        second = self.call("POST", "/fci/guild/1/members/quit/", request)[1]
        self.assertEqual(second["limitEndTime"], self.now[0] + 86400)
        self.join(2)
        self.assertEqual(self.call("POST", "/fci/guild/2/donate/request/", {"res": {"type": 11, "id": 20001}})[1]["error"], "guild_function_cooldown")
        self.now[0] += 86400
        self.assertEqual(self.call("POST", "/fci/guild/2/donate/request/", {"res": {"type": 11, "id": 20001}})[0], 200)

    def test_creation_cost_rollback_and_repeat(self):
        before = self.balance(7)
        body = {"name": "我的公会", "icon": "2", "billboard": "互助"}
        with patch.object(self.guild, "_save", side_effect=RuntimeError("disk failure")):
            with self.assertRaisesRegex(RuntimeError, "disk failure"):
                self.call("POST", "/fci/guild/", body)
        self.assertEqual(self.balance(7), before)
        self.assertEqual(self.guild.player_data(self.profile)["guildId"], 0)
        self.assertEqual(self.pushes, [])
        status, result = self.call("POST", "/fci/guild/", body)
        self.assertEqual(status, 200)
        self.assert_message("ApiGuild.result", result)
        self.assertEqual(self.balance(7), before - 100000)
        self.assertEqual(self.call("POST", "/fci/guild/", body)[0], 200)
        self.assertEqual(self.balance(7), before - 100000)

    def test_manager_routes_update_invite_promote_kick_and_social_group(self):
        status, created = self.native("POST", "/fci/guild/", {"name": "管理测试", "icon": "2"})
        self.assertEqual(status, 200)
        gid = created["baseInfo"]["id"]
        member, auth = self.new_player()
        pid = member["player_id"]
        status, detail = self.call("PATCH", f"/fci/guild/{gid}/", {"billboard": "欢迎"})
        self.assertEqual((status, detail["baseInfo"]["billboard"], detail["baseInfo"]["icon"]), (200, "欢迎", "2"))
        status, invited = self.native("PUT", f"/fci/guild/{gid}/invited-members/", {"playerId": pid}, message="ApiGuildMemberInvite")
        self.assertEqual((status, invited["playerId"]), (200, pid))
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/invited-members/")[1][0]["playerId"], pid)
        self.assertEqual(self.native("PUT", f"/fci/guild/{gid}/members/", {"playerId": pid}, auth=auth)[0], 200)
        status, promoted = self.native("PUT", f"/fci/guild/{gid}/vice-president/", {"playerId": pid}, message="ApiGuildVicePresident")
        self.assertEqual((status, promoted["vicePresident"]), (200, [pid]))
        self.assert_message("DbGuildData.BaseInfo", promoted)
        self.assertEqual(self.native("GET", "/fci/guild-player/", auth=auth)[1]["type"], 1)
        status, demoted = self.native("DELETE", f"/fci/guild/{gid}/vice-president/{pid}")
        self.assertEqual((status, demoted["vicePresident"]), (200, []))
        self.assert_message("DbGuildData.BaseInfo", demoted)
        self.assertEqual(self.native("GET", "/fci/guild-player/", auth=auth)[1]["type"], 0)
        status, kicked = self.native("DELETE", f"/fci/guild/{gid}/members/{pid}")
        self.assertEqual((status, kicked["membersCount"]), (200, created["baseInfo"]["membersCount"]))
        self.assert_message("DbGuildData.BaseInfo", kicked)
        self.assertEqual(self.native("GET", "/fci/guild-player/", auth=auth)[1]["guildId"], 0)
        status, social = self.call("PATCH", f"/fci/guild/{gid}/socialgroup/", {"qqGroupId": "12345"})
        self.assertEqual((status, social["qqGroupId"]), (200, "12345"))

    def test_native_invitation_acceptance_weekly_settlement_recovers_legacy_account_ids(self):
        gid = self.native("POST", "/fci/guild/", {"name": "邀请结算", "icon": "1"})[1]["baseInfo"]["id"]
        recipient, auth = self.new_player()
        pid = recipient["player_id"]
        self.assertEqual(self.native("PUT", f"/fci/guild/{gid}/invited-members/", {"playerId": pid})[0], 200)
        with closing(self.game._connect()) as conn:
            state = self.guild._load(conn, self.now[0])
        self.assertEqual(state["players"][pid]["accountId"], recipient["account_id"])
        self.assertEqual(self.native("GET", "/fci/guild-player/invited/", auth=auth)[1][0]["id"], gid)
        self.assertEqual(self.native("PUT", f"/fci/guild/{gid}/members/", {"playerId": pid}, auth=auth,
                                     message="ApiGuildMembers")[0], 200)
        self.assertEqual(self.native("POST", f"/fci/guild/{gid}/donate/request/", {"res": {"type": 11, "id": 20001}}, auth=auth)[0], 200)
        grant_rewards(self.game, self.profile, [{"type": 11, "id": 20001, "count": 1}])
        self.assertEqual(self.native("POST", f"/fci/guild/{gid}/donate/send/",
                                     {"playerIdRequest": pid, "res": {"type": 11, "id": 20001, "count": 1}},
                                     message="ApiGuildDonateSend")[0], 200)
        with closing(self.game._connect()) as conn, conn:
            state = self.guild._load(conn, self.now[0])
            # Only the migration fixture edits persisted state: recreate the
            # released invitation bug, including a bot and an unknown account.
            state["players"][pid]["accountId"] = 0
            state["players"]["local-bot-001"] = {"accountId": 0, "data": self.guild._player_default()}
            state["players"]["missing-player"] = {"accountId": 0, "data": self.guild._player_default()}
            bot_request = state["guilds"]["1"]["donateInfo"]["donates"]["local-bot-001"]
            bot_request["donatesCount"][self.profile["player_id"]] = 1
            self.guild._save(conn, state, self.now[0])
            boundary = state["guilds"][str(gid)]["donateInfo"]["nextBalanceTime"]
        self.now[0] = boundary
        self.assertEqual(self.native("GET", "/fci/guild-player/")[0], 200)
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/")[0], 200)
        saved = self.game.systems._state(recipient)["items"]["20001"]["count"]
        self.assertEqual(saved, 4)  # One real donation plus three finite robot donations.
        self.assertEqual(self.native("GET", "/fci/guild-player/", auth=auth)[1]["lastGuildDonateBalanceTime"], boundary)
        self.assertEqual(self.game.systems._state(recipient)["items"]["20001"]["count"], saved)
        with closing(self.game._connect()) as conn:
            state = self.guild._load(conn, self.now[0])
        self.assertEqual(state["players"][pid]["accountId"], recipient["account_id"])
        self.assertNotIn("local-bot-001", state["players"])
        self.assertNotIn("missing-player", state["players"])

    def test_native_application_approval_transfer_and_invitation_decline(self):
        gid = self.native("POST", "/fci/guild/", {"name": "审批转让", "icon": "1"})[1]["baseInfo"]["id"]
        member, auth = self.new_player()
        pid = member["player_id"]
        self.assertEqual(self.native("PUT", f"/fci/guild/{gid}/invited-members/", {"playerId": pid})[0], 200)
        self.assertEqual(self.native("DELETE", f"/fci/guild-player/invited/{gid}", auth=auth)[0], 200)
        self.assertEqual(self.native("GET", "/fci/guild-player/invited/", auth=auth)[1], [])
        self.assertEqual(self.native("GET", f"/fci/guild/{gid}/invited-members/")[1], [])
        self.assertEqual(self.native("PUT", f"/fci/guild/{gid}/pending-members/", {"playerId": pid}, auth=auth,
                                     message="ApiGuildMemberPending")[0], 200)
        self.assertEqual(self.native("GET", "/fci/guild-player/", auth=auth)[1]["guildId"], 0)
        status, approved = self.native("PUT", f"/fci/guild/{gid}/members/", {"playerId": pid}, message="ApiGuildMembers")
        self.assertEqual(status, 200)
        self.assertEqual(approved["membersCount"], 7)
        self.assert_message("DbGuildData.BaseInfo", approved)
        status, transferred = self.native("POST", f"/fci/guild/{gid}/president/", {"newPresident": pid}, message="ApiGuildPresident")
        self.assertEqual(status, 200)
        self.assertEqual(transferred["president"], pid)
        self.assert_message("DbGuildData.BaseInfo", transferred)
        self.assertEqual(self.native("GET", "/fci/guild-player/", auth=auth)[1]["type"], 2)
        self.assertEqual(self.native("PATCH", f"/fci/guild/{gid}/", {"billboard": "旧会长不能改"})[0], 403)

    def test_foreign_guild_management_rejected_without_state_or_asset_changes(self):
        self.native("POST", "/fci/guild/", {"name": "本会权限", "icon": "1"})
        before = self.native("GET", "/fci/guild/1/")[1]
        gold = self.balance(7)
        for method, path, body in [
            ("PATCH", "/fci/guild/1/", {"billboard": "越权"}),
            ("DELETE", "/fci/guild/1/members/local-bot-002", None),
            ("PUT", "/fci/guild/1/vice-president/", {"playerId": "local-bot-002"}),
            ("POST", "/fci/guild/1/president/", {"newPresident": self.profile["player_id"]}),
            ("PUT", "/fci/guild/1/invited-members/", {"playerId": self.profile["player_id"]}),
            ("DELETE", "/fci/guild/1/pending-members/", None),
            ("PATCH", "/fci/guild/1/socialgroup/", {"qqGroupId": "123"}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.native(method, path, body)[0], 403)
        self.assertEqual(self.native("GET", "/fci/guild/1/")[1], before)
        self.assertEqual(self.balance(7), gold)

    def test_legacy_missing_bot_and_orphaned_donation_do_not_block_weekly_settlement(self):
        self.join()
        with closing(self.game._connect()) as conn, conn:
            state = self.guild._load(conn, self.now[0])
            guild = state["guilds"]["1"]
            # Reproduce a previously saved unauthorized kick, which removed
            # the member but left its ID in the next week's bot generator.
            del guild["members"]["local-bot-002"]
            missing = self.guild._donation("deleted-player", (11, 20001), self.now[0])
            missing["donatesCount"][self.profile["player_id"]] = 1
            guild["donateInfo"]["donates"]["deleted-player"] = missing
            state["players"]["deleted-player"] = {"accountId": 0, "data": self.guild._player_default()}
            boundary = guild["donateInfo"]["nextBalanceTime"]
            self.guild._save(conn, state, self.now[0])
        before = self.balance(11, 20001)
        self.now[0] = boundary
        self.assertEqual(self.native("GET", "/fci/guild-player/")[0], 200)
        self.assertEqual(self.native("GET", "/fci/guild/1/donate/")[0], 200)
        with closing(self.game._connect()) as conn:
            state = self.guild._load(conn, self.now[0])
        self.assertNotIn("local-bot-002", state["guilds"]["1"]["botIds"])
        self.assertNotIn("deleted-player", state["players"])
        self.assertEqual(state["orphanedDonations"][f"1:{boundary}:deleted-player"]["request"], missing)
        self.assertEqual(self.balance(11, 20001), before)

    def test_invite_existing_bot_or_unknown_player_does_not_create_phantom_accounts(self):
        gid = self.native("POST", "/fci/guild/", {"name": "邀请校验", "icon": "1"})[1]["baseInfo"]["id"]
        gold = self.balance(7)
        for pid, status in (("local-bot-001", 409), ("nonexistent-player", 404)):
            self.assertEqual(self.native("PUT", f"/fci/guild/{gid}/invited-members/", {"playerId": pid})[0], status)
        with closing(self.game._connect()) as conn:
            state = self.guild._load(conn, self.now[0])
        self.assertEqual(set(state["players"]), {self.profile["player_id"]})
        self.assertEqual(state["guilds"][str(gid)]["invitedMembers"], {})
        self.assertEqual(self.balance(7), gold)

    def test_request_rejects_currency_unknown_or_uncollected_shards(self):
        self.join()
        for res, status in (({"type": 7, "count": 100}, 400), ({"type": 11, "id": 99999}, 400),
                            ({"type": 11, "id": 20003}, 403), ({"type": 11, "id": 20001, "count": -1}, 400)):
            self.assertEqual(self.call("POST", "/fci/guild/1/donate/request/", {"res": res})[0], status)
        self.assertEqual(self.call("PUT", "/fci/guild/1/pending-members/", {"playerId": "another"})[0], 403)
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/request/", b"{broken")[0], 400)
        self.assertNotIn(self.profile["player_id"], self.call("GET", "/fci/guild/1/donate/")[1]["donates"])

    def test_real_shard_spend_refunds_and_durable_send_receipt(self):
        self.join()
        target = self.call("GET", "/fci/guild/1/donate/")[1]["donates"]["local-bot-001"]
        body = {"playerIdRequest": target["requestPlayerId"], "res": {"type": 11, "id": 20001, "count": 1}, "requestId": "donation-one"}
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/send/", body)[1]["error"], "insufficient_shards")
        grant_rewards(self.game, self.profile, [{"type": 11, "id": 20001, "count": 2}])
        before = self.balance(7)
        status, result = self.call("POST", "/fci/guild/1/donate/send/", body)
        self.assertEqual(status, 200)
        self.assert_message("ApiGuildDonateSend.post", result)
        self.assertEqual(self.balance(11, 20001), 1)
        self.assertEqual(self.balance(17), 10)
        self.assertEqual(self.balance(7), before + 20000)
        restored = LocalGuild(LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0]))
        status, replay = self.call("POST", "/fci/guild/1/donate/send/", body, module=restored)
        self.assertEqual(status, 200)
        self.assertTrue(all(row["baseRes"]["countDelta"] == 0 for row in replay["resChange"]))
        self.assertEqual(self.balance(11, 20001), 1)
        self.assertEqual(self.balance(17), 10)
        conflict = dict(body, res={"type": 11, "id": 20002, "count": 1})
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/send/", conflict)[1]["error"], "request_id_conflict")
        # Native requests have no token: a second click intentionally donates
        # a second shard; do not suppress valid clicks by body fingerprint.
        del body["requestId"]
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/send/", body)[0], 200)
        self.assertEqual(self.balance(11, 20001), 0)

    def test_donation_failure_rolls_back_bag_wallet_and_record(self):
        self.join()
        grant_rewards(self.game, self.profile, [{"type": 11, "id": 20001, "count": 1}])
        before = self.balance(7)
        self.pushes.clear()
        body = {"playerIdRequest": "local-bot-001", "res": {"type": 11, "id": 20001, "count": 1}, "requestId": "rollback"}
        with patch.object(self.guild, "_save", side_effect=RuntimeError("simulated crash")):
            with self.assertRaises(RuntimeError):
                self.call("POST", "/fci/guild/1/donate/send/", body)
        self.assertEqual(self.balance(11, 20001), 1)
        self.assertEqual(self.balance(7), before)
        self.assertEqual(self.balance(17), 0)
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/send/", body)[0], 200)
        self.assertEqual(self.balance(11, 20001), 0)

    def test_concurrent_last_shard_only_one_send_succeeds(self):
        self.join()
        grant_rewards(self.game, self.profile, [{"type": 11, "id": 20001, "count": 1}])
        other = LocalGuild(LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0]))
        def send(pair):
            module, token = pair
            return self.call("POST", "/fci/guild/1/donate/send/", {"playerIdRequest": "local-bot-001",
                "res": {"type": 11, "id": 20001, "count": 1}, "requestId": token}, module=module)[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(send, [(self.guild, "one"), (other, "two")]))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.balance(11, 20001), 0)
        self.assertEqual(self.balance(17), 10)

    def test_native_protobuf_send_and_request_capacity(self):
        self.join()
        grant_rewards(self.game, self.profile, [{"type": 11, "id": 20001, "count": 11}])
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiGuildDonateSend"))
        request = cls(player_id_request="local-bot-001")
        request.res.type, request.res.id, request.res.count = 11, 20001, 1
        for _ in range(10):
            status, result = self.call("POST", "/fci/guild/1/donate/send/", request.SerializeToString())
            self.assertEqual(status, 200, result)
        status, result = self.call("POST", "/fci/guild/1/donate/send/", request.SerializeToString())
        self.assertEqual((status, result["error"]), (409, "donation_request_full"))
        self.assertEqual(self.balance(11, 20001), 1)
        self.assertEqual(self.balance(17), 100)

    def test_touch_resets_at_local_midnight_not_task_reset(self):
        self.join()
        self.now[0] = int(datetime(2026, 10, 30, 15, 59, tzinfo=timezone.utc).timestamp())
        self.call("POST", "/fci/guild/1/donate/request/", {"res": {"type": 11, "id": 20001}})
        first = self.call("POST", "/fci/guild/1/donate/touch/")[1]
        self.now[0] += 60
        second = self.call("POST", "/fci/guild/1/donate/touch/")[1]
        self.assertGreater(second["lastTouchTime"], first["lastTouchTime"])
        self.now[0] += 6 * 3600
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/touch/")[1], second)

    def test_robot_aid_touch_and_sunday_settlement_once_across_restart(self):
        self.join()
        body = {"res": {"type": 11, "id": 20001}}
        status, first = self.call("POST", "/fci/guild/1/donate/request/", body)
        self.assertEqual(status, 200)
        self.assert_message("DbGuildData.DonateSingleInfo", first)
        self.assertEqual(sum(first["donatesCount"].values()), 3)
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/request/", body)[1], first)
        self.assertEqual(self.balance(11, 20001), 0)
        touched = self.call("POST", "/fci/guild/1/donate/touch/")[1]
        self.assertGreater(touched["loveScore"], 0)
        self.assertEqual(self.call("POST", "/fci/guild/1/donate/touch/")[1], touched)
        boundary = self.call("GET", "/fci/guild/1/donate/")[1]["nextBalanceTime"]
        local = self.game.reset_policy.local_datetime(boundary)
        self.assertEqual((local.isoweekday(), local.hour), (7, 6))
        self.now[0] = boundary - 1
        self.guild.player_data(self.profile)
        self.assertEqual(self.balance(11, 20001), 0)
        self.now[0] = boundary
        self.pushes.clear()
        self.guild.player_data(self.profile)
        received = self.balance(11, 20001)
        self.assertGreaterEqual(received, 3)
        self.assertLessEqual(received, 6)
        self.assertEqual([kind for _, kind, _ in self.pushes], [3, 25, 58])
        pushes_after_settlement = list(self.pushes)
        restored = LocalGuild(LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0]))
        self.assertEqual(restored.player_data(self.profile)["lastGuildDonateBalanceTime"], boundary)
        self.assertEqual(self.balance(11, 20001), received)
        self.now[0] -= 86400
        restored.player_data(self.profile)
        self.now[0] += 86400
        restored.player_data(self.profile)
        self.assertEqual(self.balance(11, 20001), received)
        self.assertTrue(any(kind == 3 and params == ("20001",) for _, kind, params in self.pushes))
        self.assertEqual(self.pushes, pushes_after_settlement)

    def test_weekly_push_refreshes_members_without_requests_and_guildless_recipients(self):
        self.join()
        self.call("POST", "/fci/guild/1/donate/request/", {"res": {"type": 11, "id": 20001}})
        boundary = self.call("GET", "/fci/guild/1/donate/")[1]["nextBalanceTime"]
        self.call("POST", "/fci/guild/1/members/quit/", {"playerId": self.profile["player_id"]})
        account, _ = test_systems.LocalSystemsTests._guest(self.accounts)
        member = self.game._profile(account)
        self.join(profile=member)
        self.pushes.clear()
        self.now[0] = boundary
        self.guild.player_data(member)
        self.assertEqual([kind for aid, kind, _ in self.pushes if aid == self.profile["account_id"]], [3, 25])
        self.assertEqual([kind for aid, kind, _ in self.pushes if aid == member["account_id"]], [25, 58])
        self.assertGreaterEqual(self.balance(11, 20001), 3)
        self.assertEqual(self.guild.player_data(self.profile)["lastGuildDonateBalanceTime"], boundary)


if __name__ == "__main__":
    unittest.main()
