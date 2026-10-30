"""Friend points, friend removal/blacklist, avatar/signboard unlocks, medals and RoleNotePad.

Shapes follow the original client: Window_Friend (fci/relationpoint/),
Common_FriendUtil (fci/blacklist/), Window_PlayerOption (DELETE
fci/friendlist/<id>), Window_MySpacePlayerHeadBox (fci/headphoto/),
Manager_PushService.MedalChange (fci/medal/<id>), Window_RoleNotepad
(fci/RoleNotePad/[<roleId>]) and Window_RoleNotepadDetailBoard (fci/Signboard/).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
from grants import grant_rewards  # noqa: E402
from profile_features import table_rows  # noqa: E402
import test_systems  # noqa: E402


def _ts(*parts: int) -> int:
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp())


class FriendsProfileTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.now = [_ts(2026, 10, 30, 8, 0)]
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = self._game()
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)

    def tearDown(self):
        self._temp.cleanup()

    def _game(self) -> LocalGame:
        return LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])

    def call(self, method, path, payload=None, game=None):
        body = payload if isinstance(payload, (bytes, type(None))) else json.dumps(payload).encode()
        return (game or self.game).handle_http(method, path, body, "application/json", self.auth)

    def profile(self):
        return self.game._profile(self.account)

    def grant(self, *rewards):
        return grant_rewards(self.game, self.profile(), [dict(zip(("type", "id", "count"), item)) for item in rewards])

    # ------------------------------------------------------------ friend points
    def test_supports_used_fill_the_point_pool_and_claim_respects_the_daily_cap(self):
        social = self.game.social
        self.assertEqual(self.call("GET", "/fci/relationpoint/"),
                         (200, {"todayTotalGet": 0, "auraCanRecvCnt": 0, "canRecvCnt": 0, "pointPool": 0}))
        # Misc.auraRelationPointAddFriends / AddStranger.
        self.assertEqual(social.record_assist_used(self.profile(), "local-bot-001"),
                         {"playerId": "local-bot-001", "points": 25, "stranger": False})
        self.assertEqual(social.record_assist_used(self.profile(), "local-bot-010")["points"], 20)
        # The friend cools down for Misc.auraCooldown (4h); unknown ids are ignored.
        self.assertEqual(social.record_assist_used(self.profile(), "local-bot-001")["points"], 0)
        self.assertEqual(social.record_assist_used(self.profile(), "nobody")["points"], 0)
        self.assertEqual(social.record_assist_used(self.profile(), "")["points"], 0)
        status, rows = self.call("POST", "/fci/dungeon-assistants", {"token": 4})
        friend = next(row for row in rows if row["playerId"] == "local-bot-001")
        self.assertEqual((friend["stranger"], friend["lastUseTime"]), (False, self.now[0]))
        self.assertTrue(all(row["lastUseTime"] == 0 for row in rows if row["stranger"]))
        self.assertEqual(self.call("GET", "/fci/relationpoint/")[1],
                         {"todayTotalGet": 0, "auraCanRecvCnt": 0, "canRecvCnt": 2, "pointPool": 45})
        self.assertEqual(social.redpoint_fields(self.profile()), {"relationPointRedpoint": True})

        status, claimed = self.call("POST", "/fci/relationpoint/")
        self.assertEqual((status, claimed["getCntThisTime"]), (200, 45))
        point = claimed["resChange"][0]["baseRes"]
        self.assertEqual((point["type"], point["countDelta"], point["count"]), (10, 45, 45))
        self.assertEqual(self.call("GET", "/fci/relationpoint/")[1],
                         {"todayTotalGet": 45, "auraCanRecvCnt": 0, "canRecvCnt": 0, "pointPool": 0})
        self.assertEqual(self.call("POST", "/fci/relationpoint/")[1]["getCntThisTime"], 0)
        self.assertEqual(social.redpoint_fields(self.profile()), {"relationPointRedpoint": False})

        # After the cooldown the friend pays again; Misc.relationPointDailyLimit
        # (400) caps a day's claims and the rest stays pooled.
        self.now[0] += 4 * 3600
        for _ in range(20):
            social.record_assist_used(self.profile(), "local-bot-012")
        self.assertEqual(self.call("GET", "/fci/relationpoint/")[1]["pointPool"], 400)
        self.assertEqual(self.call("POST", "/fci/relationpoint/")[1]["getCntThisTime"], 355)
        state = self.call("GET", "/fci/relationpoint/")[1]
        self.assertEqual((state["todayTotalGet"], state["pointPool"]), (400, 45))
        self.assertEqual(social.redpoint_fields(self.profile()), {"relationPointRedpoint": False})
        self.now[0] += 86400  # next 06:00 reset
        restarted = self._game()
        self.assertEqual(self.call("GET", "/fci/relationpoint/", game=restarted)[1]["todayTotalGet"], 0)
        self.assertEqual(self.call("POST", "/fci/relationpoint/", game=restarted)[1]["getCntThisTime"], 45)

    def test_bots_borrow_the_players_support_once_one_is_set(self):
        social = self.game.social
        self.call("GET", "/fci/relationpoint/")
        self.now[0] += 3 * 86400
        # No support character (auraActorUid) -> nobody can borrow it, and
        # setting one later does not pay for the days before.
        self.assertEqual(self.call("GET", "/fci/relationpoint/")[1]["auraCanRecvCnt"], 0)
        self.now[0] += 2 * 86400
        self.assertEqual(self.call("PATCH", "/fci/playerpageinfo/", {"auraActorUid": 1})[0], 200)
        start = self.now[0]
        self.assertEqual(self.call("GET", "/fci/relationpoint/")[1]["auraCanRecvCnt"], 0)
        snapshot = json.loads(json.dumps(social._state(self.profile())))
        self.now[0] = start + 5 * 86400
        state = self.call("GET", "/fci/relationpoint/")[1]
        self.assertGreater(state["auraCanRecvCnt"], 0)
        self.assertLessEqual(state["auraCanRecvCnt"], 3 * 6)
        self.assertGreaterEqual(state["pointPool"], 20 * state["auraCanRecvCnt"])
        self.assertLessEqual(state["pointPool"], 25 * state["auraCanRecvCnt"])
        self.assertEqual(self.call("GET", "/fci/relationpoint/", game=self._game())[1], state)
        # Uses happen at fixed seeded times: polling more often changes nothing.
        social._save(self.profile(), snapshot)
        for hours in (20, 49, 77, 120):
            self.now[0] = start + hours * 3600
            self.call("GET", "/fci/relationpoint/")
        self.assertEqual(self.call("GET", "/fci/relationpoint/")[1], state)
        # A month away only catches up a bounded number of days.
        self.now[0] += 30 * 86400
        later = self.call("GET", "/fci/relationpoint/")[1]
        self.assertLessEqual(later["auraCanRecvCnt"] - state["auraCanRecvCnt"], 3 * 8)

    # --------------------------------------------------------- friends / block
    def test_removed_friend_stays_removed_after_restart(self):
        ids = [row["playerId"] for row in self.call("GET", "/fci/friendlist/")[1]]
        self.assertIn("local-bot-001", ids)
        self.assertEqual(self.call("DELETE", "/fci/friendlist/local-bot-001"), (200, {}))
        restarted = self._game()
        ids = [row["playerId"] for row in self.call("GET", "/fci/friendlist/", game=restarted)[1]]
        self.assertNotIn("local-bot-001", ids)
        rows = self.call("POST", "/fci/dungeon-assistants", {"token": 1}, game=restarted)[1]
        self.assertTrue(all(row["stranger"] for row in rows if row["playerId"] == "local-bot-001"))
        recommend = [row["playerId"] for row in self.call("GET", "/fci/recommendrelation/", game=restarted)[1]]
        self.assertIn("local-bot-001", recommend)

    def test_block_and_unblock(self):
        self.assertEqual(self.call("GET", "/fci/blacklist/"), (200, []))
        status, blocked = self.call("POST", "/fci/blacklist/", {"targetId": "local-bot-002"})
        self.assertEqual((status, blocked["playerId"], blocked["isInRelation"]), (200, "local-bot-002", False))
        self.assertEqual(self.call("POST", "/fci/blacklist/", {"targetId": "nobody"})[0], 404)
        restarted = self._game()
        self.assertEqual([row["playerId"] for row in self.call("GET", "/fci/blacklist/", game=restarted)[1]], ["local-bot-002"])
        # Blocking ends the friendship and hides the player everywhere.
        self.assertNotIn("local-bot-002", [row["playerId"] for row in self.call("GET", "/fci/friendlist/", game=restarted)[1]])
        self.assertNotIn("local-bot-002", [row["playerId"] for row in self.call("GET", "/fci/recommendrelation/", game=restarted)[1]])
        self.assertEqual(self.call("POST", "/fci/applyfriend/", {"targetId": "local-bot-002"}, game=restarted)[0], 409)
        self.assertEqual(self.game.social.record_assist_used(self.profile(), "local-bot-002")["points"], 0)
        self.assertEqual(self.call("DELETE", "/fci/blacklist/local-bot-002", game=restarted), (200, {}))
        self.assertEqual(self.call("GET", "/fci/blacklist/", game=restarted), (200, []))
        self.assertEqual(self.call("POST", "/fci/applyfriend/", {"targetId": "local-bot-002"}, game=restarted)[0], 200)

    def test_bot_avatars_are_real_head_photos(self):
        valid = {int(row["id"]) for row in table_rows("PlayerHeadPhoto")}
        rows = (self.call("GET", "/fci/friendlist/")[1] + self.call("GET", "/fci/recommendrelation/")[1]
                + self.call("POST", "/fci/dungeon-assistants", {"token": 1})[1])
        self.assertTrue(rows)
        self.assertTrue(all(row["headPhoto"] in valid for row in rows))

    # ------------------------------------------------------------- avatars
    def test_head_photos_follow_owned_roles_and_grants(self):
        self.assertEqual(self.call("GET", "/fci/headphoto/")[1], [{"id": 1, "gotTime": self.now[0], "isNew": False}])
        self.assertEqual(self.call("PATCH", "/fci/playerpageinfo/", {"headPhoto": 2})[0], 409)
        self.grant((15, 1002, 1))  # role 2 -> PlayerHeadPhoto 2 (getWay 1)
        photos = [row["id"] for row in self.call("GET", "/fci/headphoto/")[1]]
        self.assertEqual(photos, [1, 2])  # 502 (getWay 2) needs a grant
        self.grant((2, 502, 1))
        self.assertEqual([row["id"] for row in self.call("GET", "/fci/headphoto/")[1]], [1, 2, 502])
        status, page = self.call("PATCH", "/fci/playerpageinfo/", {"headPhoto": 502})
        self.assertEqual((status, page["headPhoto"]), (200, 502))
        self.assertEqual(self.call("PATCH", "/fci/playerpageinfo/", {"headPhoto": 130})[0], 409)
        self.assertEqual(self.call("GET", "/fci/playerpageinfo/")[1]["headPhoto"], 502)
        # -1 (platform picture) is kept as 0: head_photo is uint32 in replies.
        self.assertEqual(self.call("PATCH", "/fci/playerpageinfo/", {"headPhoto": -1})[1]["headPhoto"], 0)
        self.assertEqual(self.call("GET", "/fci/baseinfo/")[1]["headPhoto"], 0)

    def test_medal_routes_return_a_list_and_single_objects(self):
        profile = self.profile()
        state = self.game.systems._state(profile)
        state["medals"] = {"7": {"id": 7, "gotTime": 123, "isNew": True}, "3": {"id": 3, "gotTime": 99, "isNew": False}}
        self.game.systems._save(profile, state)
        self.assertEqual(self.call("GET", "/fci/medal/7"), (200, {"id": 7, "gotTime": 123, "isNew": True}))
        self.assertEqual(self.call("GET", "/fci/medal/8"), (200, {}))
        self.assertEqual([row["id"] for row in self.call("GET", "/fci/medal/")[1]], [3, 7])

    # ------------------------------------------------------------ RoleNotePad
    def test_one_notepad_per_owned_role(self):
        status, notepads = self.call("GET", "/fci/RoleNotePad/")
        self.assertEqual(status, 200)
        self.assertEqual([row["roleId"] for row in notepads], [1])  # actor 1001 is role 1
        first = notepads[0]
        self.assertEqual((first["favourLevel"], first["favour"]), (0, 0))
        self.assertEqual([event["type"] for event in first["notepadEvents"]], [1, 4])  # GetActor, GetHeadPhoto
        self.assertTrue(all(json.loads(event["text"]) is not None for event in first["notepadEvents"]))
        # Characters map to ActorConfig.role; roles without a notepad
        # (favourActor false, or absent from Role) get none.
        self.grant((15, 1002, 1), (15, 2007, 1), (15, 1007, 1), (15, 3001, 1), (15, 7008, 1))
        roles = [row["roleId"] for row in self.call("GET", "/fci/RoleNotePad/")[1]]
        self.assertEqual(roles, [1, 2, 7])
        valid_roles = {int(row["id"]) for row in table_rows("Role")}
        self.assertTrue(set(roles) <= valid_roles)
        status, one = self.call("GET", "/fci/RoleNotePad/7")
        self.assertEqual((status, one["roleId"]), (200, 7))
        self.assertEqual(self.call("GET", "/fci/RoleNotePad/3")[0], 404)
        # A legacy notepad keyed by a character id is dropped.
        features = self.game.profile_features
        profile = self.profile()
        state = features._state(profile)
        state["notepad"]["1001"] = {"roleId": 1001, "favour": 0, "favourLevel": 1}
        features._save(profile, state)
        self.assertNotIn(1001, [row["roleId"] for row in self.call("GET", "/fci/RoleNotePad/", game=self._game())[1]])

    def test_notepad_batch_matches_native_request_positions(self):
        self.grant((15, 1002, 1), (15, 1007, 1))
        # DataBindingManager pairs response[index] with requestList[index - 1].
        # A subset must not include the other owned roles, and duplicates keep
        # their positions even when the request order differs from role order.
        for method, query, expected in (
            ("GET", "perPage=20&uids=7", [7]),
            ("GET", "perPage=20&uids=7%2C1", [7, 1]),
            ("GET", "uids=7,1,7", [7, 1, 7]),
            ("GET", "uids=7&uids=1,2", [7, 1, 2]),
            ("HEAD", "uids=2,1", [2, 1]),
        ):
            with self.subTest(method=method, query=query):
                status, records = self.call(method, "/fci/RoleNotePad/?" + query)
                self.assertEqual(status, 200)
                self.assertEqual([row["roleId"] for row in records], expected)
                for role_id, record in zip(expected, records):
                    self.assertEqual(record, self.call("GET", f"/fci/RoleNotePad/{role_id}")[1])
        status, summary = self.call("GET", "/fci/RoleNotePad?perPage=20&fields=roleId,favourLevel")
        self.assertEqual((status, [row["roleId"] for row in summary]), (200, [1, 2, 7]))

    def test_notepad_batch_rejects_missing_or_invalid_ids_without_shifting_slots(self):
        self.grant((15, 1007, 1))
        for uids in ("3,1", "7,3,1", "1,1001"):
            with self.subTest(uids=uids):
                status, error = self.call("GET", "/fci/RoleNotePad/?uids=" + uids)
                self.assertEqual(status, 404)
                self.assertEqual(error["error"], "role_notepad_not_found")
                self.assertEqual(error["roleId"], 1001 if "1001" in uids else 3)
        for uids in ("", "1,,7", "1,", "nope", "0", "-1", "1.0"):
            with self.subTest(uids=uids):
                status, error = self.call("GET", "/fci/RoleNotePad/?uids=" + uids)
                self.assertEqual((status, error), (400, {"error": "invalid_role_notepad_uids"}))

    def test_favour_levels_follow_the_favour_level_table(self):
        features = self.game.profile_features
        record = features.add_role_favour(self.profile(), 1, 60)  # 15 to reach 1, 50 more for 2
        self.assertEqual((record["favourLevel"], record["favour"]), (1, 45))
        record = features.add_role_favour(self.profile(), 1, 5)
        self.assertEqual((record["favourLevel"], record["favour"]), (2, 0))
        levels = [json.loads(event["text"])["favour_level"] for event in record["notepadEvents"] if event["type"] == 2]
        self.assertEqual(levels, [1, 2])
        self.assertEqual(features.add_role_favour(self.profile(), 1, 10 ** 6)["favourLevel"], 10)
        self.assertIsNone(features.add_role_favour(self.profile(), 2, 10))  # role not owned
        self.assertEqual(self.call("GET", "/fci/RoleNotePad/1")[1]["favourLevel"], 10)

    # ------------------------------------------------------------- signboards
    def test_signboard_unlocks_follow_the_signboard_rules(self):
        features = self.game.profile_features
        self.assertEqual(self.call("GET", "/fci/Signboard/")[1]["unlockedSignboardId"], [101])
        self.assertEqual(self.call("PUT", "/fci/Signboard/", {"signboard": 102})[0], 409)
        features.add_role_favour(self.profile(), 1, 15)  # FavourLevel 1 -> board 102
        status, chosen = self.call("PUT", "/fci/Signboard/", {"signboard": 102})
        self.assertEqual((status, chosen.signboard), (200, 102))
        stored = self.call("GET", "/fci/Signboard/", game=self._game())[1]
        self.assertEqual((stored["signboard"], stored["unlockedSignboardId"]), (102, [101, 102]))
        # 104 (ActorEverGotAndFavourLevel, actor 1401) is a special board: the
        # client trusts the server list, so it needs the character as well.
        features.add_role_favour(self.profile(), 1, 50)
        self.assertEqual(self.call("PUT", "/fci/Signboard/", {"signboard": 104})[0], 409)
        self.grant((15, 1401, 1))
        self.assertEqual(self.call("PUT", "/fci/Signboard/", {"signboard": 104})[0], 200)
        self.assertIn(104, self.call("GET", "/fci/Signboard/")[1]["unlockedSignboardId"])
        # 106 is an ActorSignboardGift board: only a ResSignboard grant unlocks it.
        self.assertEqual(self.call("PUT", "/fci/Signboard/", {"signboard": 106})[0], 409)
        self.grant((30, 106, 1))
        self.assertEqual(self.call("PUT", "/fci/Signboard/", {"signboard": 106})[0], 200)
        # Boards of roles the player never met stay locked.
        self.assertEqual(self.call("PUT", "/fci/Signboard/", {"signboard": 1501})[0], 409)
        # Ordinary type-3 boards are shown unlocked by favour alone in the
        # client, so the server accepts them, but lists them only once the
        # board's character was owned.
        self.grant((15, 1007, 1))
        features.add_role_favour(self.profile(), 7, 65)  # role 7 level 2
        self.assertEqual(self.call("PUT", "/fci/Signboard/", {"signboard": 703})[0], 200)
        unlocked = self.call("GET", "/fci/Signboard/")[1]["unlockedSignboardId"]
        self.assertIn(702, unlocked)
        self.assertNotIn(703, unlocked)


if __name__ == "__main__":
    unittest.main()
