"""Real client board fields, durable applications and ChatChannel rules."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from google.protobuf import json_format, message_factory

from accounts import LocalAccounts
from game import LocalGame
import test_systems


class SocialClientProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = [1793339999]  # A fixed UTC instant; all comparisons use the reset policy.
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = self.make_game()
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.base = []
        self.messages = []
        self.game.base_push_notifier = lambda p, kind, args=(): self.base.append((kind, args))
        self.game.social_message_notifier = self.record_message

    def tearDown(self):
        self.temp.cleanup()

    def make_game(self):
        return LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])

    def call(self, method, path, payload=None, *, game=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        return (game or self.game).handle_http(method, path, body, "application/json", self.auth)

    def wire(self, name, **fields):
        return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))(**fields).SerializeToString()

    def record_message(self, profile, name, data):
        message = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()
        json_format.ParseDict(data, message).SerializeToString()
        self.messages.append((name, dict(data)))

    def level(self, value):
        with closing(self.game._connect()) as conn, conn:
            conn.execute("UPDATE game_profiles SET level=? WHERE account_id=?", (value, self.profile["account_id"]))
        self.profile = self.game._profile(self.account)

    def test_board_client_fields_reply_delete_and_restart(self):
        path = "/fci/player-message/local-bot-001"
        status, entry = self.call("POST", path, self.wire("ApiPlayerMessage", content="hello", to="local-bot-002"))
        self.assertEqual(status, 200)
        self.assertEqual(set(entry), {"id", "sender", "senderName", "content", "to", "toName", "createTime"})
        self.assertEqual(entry["sender"], self.profile["player_id"])
        self.assertEqual(entry["toName"], self.game.social.bots.get("local-bot-002")["name"])
        self.assertEqual(entry["createTime"], self.now[0])
        restarted = self.make_game()
        self.assertEqual(self.call("GET", path, game=restarted), (200, [entry]))
        self.assertEqual(self.call("DELETE", path + "/" + str(entry["id"]), game=restarted), (200, {}))
        status, after = self.call("POST", path, {"content": "after delete"}, game=restarted)
        self.assertEqual(status, 200)
        self.assertGreater(after["id"], entry["id"])

    def test_board_old_rows_duplicates_retention_and_permissions(self):
        social = self.game.social
        state = social._state(self.profile)
        state["boards"] = {"local-bot-001": [
            {"id": 51, "playerId": "local-bot-002", "name": "old name", "content": "keep", "time": 17},
            {"id": 51, "playerId": self.profile["player_id"], "name": "me", "content": "duplicate", "time": 18}]}
        social._save(self.profile, state)
        path = "/fci/player-message/local-bot-001"
        status, rows = self.call("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(rows[0]["senderName"], "old name")
        self.assertEqual(rows[0]["createTime"], 17)
        self.assertEqual(len({row["id"] for row in rows}), 2)
        self.assertEqual(self.call("DELETE", path + "/51")[0], 403)
        previous = max(row["id"] for row in rows)
        for index in range(55):
            status, row = self.call("POST", path, {"content": str(index)})
            self.assertEqual(status, 200)
            self.assertGreater(row["id"], previous)
            previous = row["id"]
        self.assertEqual(len(self.call("GET", path)[1]), 50)
        self.assertEqual(self.call("DELETE", path + "/99999")[0], 404)
        self.assertEqual(self.call("POST", path, {"content": "x" * 129})[0], 400)

    def test_board_pagination_does_not_repeat_short_final_page(self):
        path = "/fci/player-message/local-bot-001"
        for index in range(6):
            self.assertEqual(self.call("POST", path, {"content": str(index)})[0], 200)

        status, first = self.call("GET", path + "?page=1&perPage=5")
        self.assertEqual((status, len(first)), (200, 5))
        status, second = self.call("GET", path + "?page=2&perPage=5")
        self.assertEqual((status, len(second)), (200, 1))
        status, third = self.call("GET", path + "?page=3&perPage=5")
        self.assertEqual((status, third), (200, []))

    def test_applications_persist_accept_reject_and_notify_real_parameters(self):
        status, pending = self.call("GET", "/fci/applylist/")
        self.assertEqual(status, 200)
        self.assertEqual(len(pending), 2)
        self.assertEqual(self.base, [(33, ())])
        ids = [row["playerId"] for row in pending]
        restarted = self.make_game()
        self.assertEqual([row["playerId"] for row in self.call("GET", "/fci/applylist", game=restarted)[1]], ids)
        self.assertEqual(self.call("POST", "/fci/applylist", self.wire("ApiRelationApplyList", target_id=ids[0], is_agree=True))[0], 200)
        self.assertIn((34, (ids[0],)), self.base)
        self.assertIn((35, (ids[0],)), self.base)
        self.assertEqual(self.call("PATCH", "/fci/applylist", {"targetList": [ids[1]], "isAgree": False})[0], 200)
        self.assertEqual(self.call("GET", "/fci/applylist"), (200, []))
        self.assertEqual(self.call("GET", "/fci/applylist", game=self.make_game()), (200, []))
        friend_ids = [row["playerId"] for row in self.call("GET", "/fci/friendlist")[1]]
        self.assertIn(ids[0], friend_ids)
        self.assertNotIn(ids[1], friend_ids)

    def test_applications_exclude_friends_blocklist_limit_and_clock_rollback(self):
        self.call("GET", "/fci/applylist")
        self.call("POST", "/fci/blacklist", {"targetId": "local-bot-003"})
        self.now[0] += 86400
        rows = self.call("GET", "/fci/applylist")[1]
        self.assertFalse({"local-bot-001", "local-bot-002", "local-bot-003"} & {row["playerId"] for row in rows})
        ids = [row["playerId"] for row in rows]
        self.call("PATCH", "/fci/applylist", {"targetList": ids, "isAgree": False})
        self.now[0] -= 86400
        self.assertEqual(self.call("GET", "/fci/applylist"), (200, []))
        self.now[0] += 2 * 86400
        self.call("PATCH", "/fci/applylist", {"friendApplyLevelLimit": 99})
        self.now[0] += 86400
        self.call("PATCH", "/fci/applylist", {"targetList": [row["playerId"] for row in self.call("GET", "/fci/applylist")[1]], "isAgree": False})
        self.assertEqual(self.call("GET", "/fci/applylist"), (200, []))

    def test_chat_real_channels_level_cooldown_subchannel_and_current_push(self):
        payload = self.wire("ApiChat", channel=1, content="world", world_sub_channel=1)
        self.assertEqual(self.call("POST", "/fci/chat", payload)[0], 403)
        self.level(12)
        self.assertEqual(self.call("POST", "/fci/chat", payload), (200, {}))
        self.assertEqual(self.messages[-1][0], "WorldMessage")
        self.assertEqual(self.messages[-1][1]["time"], self.now[0])
        self.assertEqual(self.messages[-1][1]["subChannelId"], 1)
        self.assertNotEqual(self.messages[-1][1]["senderId"], self.profile["player_id"])
        self.assertEqual(self.call("POST", "/fci/chat", payload)[0], 429)
        self.now[0] += 9
        self.assertEqual(self.call("POST", "/fci/chat", payload)[0], 429)
        self.now[0] += 1
        self.assertEqual(self.call("POST", "/fci/chat", payload)[0], 200)
        self.assertEqual(self.game.social.set_world_subchannel(self.profile, 4), (200, {"channel": 4, "totalChannel": 10}))
        self.assertEqual(self.messages[-2], ("ScWorldChatSubChannel", {"channel": 4, "totalChannel": 10}))
        self.assertEqual(self.messages[-1][1]["subChannelId"], 4)
        self.assertEqual(self.call("POST", "/fci/chat", payload)[0], 409)
        self.assertEqual(self.game.social.set_world_subchannel(self.profile, 11)[0], 400)
        self.assertEqual(self.call("POST", "/fci/chat", {"channel": 2, "content": "invalid"})[0], 400)

    def test_private_reply_base24_and_persisted_cooldown(self):
        self.level(12)
        payload = {"channel": 3, "content": "hello", "targetId": "local-bot-001"}
        self.assertEqual(self.call("POST", "/fci/chat", payload), (200, {}))
        self.assertEqual(self.base[-1], (24, ("local-bot-001", self.game.social.bots.get("local-bot-001")["name"], "收到，一起加油！", str(self.now[0]))))
        self.assertEqual(self.call("POST", "/fci/chat", payload, game=self.make_game())[0], 429)
        self.now[0] += 1
        self.assertEqual(self.call("POST", "/fci/chat", payload)[0], 200)
        self.assertEqual(self.call("POST", "/fci/chat", dict(payload, targetId=self.profile["player_id"]))[0], 400)
        self.call("POST", "/fci/blacklist", {"targetId": "local-bot-001"})
        self.assertEqual(self.call("POST", "/fci/chat", payload)[0], 409)
        self.assertEqual(self.call("POST", "/fci/chat", {"channel": 5, "content": "guild"})[0], 409)

    def test_online_tick_login_restart_and_rate(self):
        social = self.game.social
        social.tick(self.profile, connected=True)
        self.assertEqual([name for name, _ in self.messages], ["ScWorldChatSubChannel", "WorldMessage"])
        self.assertEqual(self.messages[-1][1]["time"], self.now[0])
        social.tick(self.profile)
        self.assertEqual(len(self.messages), 2)
        self.now[0] += 59
        social.tick(self.profile)
        self.assertEqual(len(self.messages), 2)
        self.now[0] += 1
        social.tick(self.profile)
        self.assertEqual(len(self.messages), 3)
        state = social._state(self.profile)
        self.assertEqual(self.make_game().social._state(self.profile)["worldBotAt"], state["worldBotAt"])
        self.now[0] -= 100
        social.tick(self.profile)
        self.assertEqual(len(self.messages), 3)

    def test_guild_channel_uses_current_membership_and_base30(self):
        self.level(23)
        status, _ = self.call("PUT", "/fci/guild/1/pending-members/", {"playerId": self.profile["player_id"]})
        self.assertEqual(status, 200)
        self.base.clear()
        self.assertEqual(self.call("POST", "/fci/chat", self.wire("ApiChat", channel=5, content="guild hello")), (200, {}))
        self.assertEqual(self.base[-1][0], 30)
        self.assertEqual(len(self.base[-1][1]), 4)
        self.assertEqual(self.base[-1][1][-1], str(self.now[0]))
        self.assertEqual(self.call("POST", "/fci/chat", {"channel": 5, "content": "again"})[0], 429)
        self.now[0] += 1
        self.assertEqual(self.call("POST", "/fci/chat", {"channel": 5, "content": "again"})[0], 200)


if __name__ == "__main__":
    unittest.main()
