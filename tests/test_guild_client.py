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
from guild import LocalGuild
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

    def test_four_native_skill_slots_are_inactive_and_activation_never_charges(self):
        status, detail = self.call("POST", "/fci/guild/", {"name": "技能测试", "icon": "1"})
        self.assertEqual(status, 200)
        gid = detail["baseInfo"]["id"]
        skills = self.call("GET", f"/fci/guild/{gid}/skill/")[1]
        self.assertEqual([row["id"] for row in skills], [101, 102, 103, 104])
        self.assertEqual([row["add"] for row in skills], [1500, 2000, 1500, 2000])
        self.assertEqual([row["type"] for row in skills], [3, 8, 8, 8])
        for row in skills:
            self.assert_message("ApiGuildSkill.result", row)
            self.assertEqual(row["time"], 18000)
            self.assertEqual((row["startTime"], row["endTime"], row["maxUseCount"], row["scoreCost"]), (0, 0, 0, 0))
        before = self.game._profile(self.account)["resources"]
        request = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiGuildSkill"))(id=101, use_count=1)
        status, result = self.call("POST", f"/fci/guild/{gid}/skill/", request.SerializeToString())
        self.assertEqual((status, result["error"]), (403, "guild_skill_activation_unavailable"))
        self.assertEqual(self.game._profile(self.account)["resources"], before)
        self.assertEqual(self.call("GET", f"/fci/guild/{gid}/")[1], dict(detail, resChange=[]))

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
