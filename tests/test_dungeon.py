from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from accounts import LocalAccounts
from game import LocalGame


class DungeonTests(unittest.TestCase):
    @staticmethod
    def _guest_account(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        authz = "AccountToken " + first[1]["accountToken"]
        entry_result = accounts.handle(
            "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", authz
        )
        entry = entry_result[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        result = accounts.handle(
            "POST",
            "/Auth/",
            b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest",
            "application/wireformat",
            "AccountToken " + second[1]["accountToken"],
        )
        return accounts.verify_access_token(result[1]["tokenAccess"])

    def test_tutorial_entry_returns_persistent_uid_and_profile_actors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            auth = "Token " + token

            status, payload = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":2011}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertGreater(payload["dungeonUid"], 1_000_000_000)
            self.assertEqual(payload["baseInfo"]["dungeonId"], 2011)
            self.assertEqual(payload["baseInfo"]["dungeonType"], 1)
            self.assertEqual(payload["baseInfo"]["actorId"], [1001, 0, 0, 0, 0, 0])  # six formation slots
            self.assertEqual(payload["baseInfo"]["actorUid"], [1, 0, 0, 0, 0, 0])  # six formation slots
            self.assertEqual(payload["baseInfo"]["actors"][0]["id"], 1001)
            self.assertEqual(payload["resChange"], [])

            uid = payload["dungeonUid"]
            status, record = game.handle_http(
                "GET", f"/fci/dungeon/{uid}", b"", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(record["status"], "active")
            self.assertEqual(record["actorUid"], [1, 0, 0, 0, 0, 0])
            status, balance = game.handle_http(
                "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(balance["balanceInfo"]["balanced"])
            self.assertTrue(balance["balanceInfo"]["win"])
            self.assertEqual(balance["balanceInfo"]["playerLevel"], 1)
            self.assertEqual(balance["balanceInfo"]["playerLevelBeforeBalance"], 1)
            # Non-fight tutorial stage 2011 consumes no energy -> no player EXP.
            self.assertEqual(balance["balanceInfo"]["resChange"][0]["baseRes"]["countDelta"], 0)
            self.assertEqual(balance["balanceInfo"]["resChange"][0]["baseRes"]["count"], 0)
            # Normal chapter 1, story chapter 1001 and training chapter 2001
            # have no preChapterId; chapter 2 waits for chapter 1's key stage.
            self.assertEqual(balance["chapterSummaryInfo"]["unlockedChapterId"], [1, 1001, 2001])
            self.assertEqual(balance["chapterSummaryInfo"]["finishedChapterId"], [])
            # Only the stage's own chapter changed (2011 done, 2012 opened).
            self.assertEqual([info["recordChapter"]["chapterId"] for info in balance["chapterInfo"]], [1001])
            self.assertEqual(set(balance["chapterInfo"][0]["recordDungeons"]), {"2011", "2012"})
            self.assertEqual(
                game.handle_http("DELETE", f"/fci/dungeon/{uid}", b"", "application/json", auth),
                (200, {}),
            )
            status, record = game.handle_http(
                "GET", f"/fci/dungeon/{uid}", b"", "application/json", auth
            )
            self.assertEqual((status, record["status"]), (200, "balanced"))

    def test_only_tutorial_dungeon_is_implemented_and_non_fci_stays_fixture_owned(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            auth = "Token " + token
            status, payload = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":9999}', "application/json", auth
            )
            self.assertEqual((status, payload["error"]), (501, "dungeon_not_implemented"))
            status, payload = game.handle_http(
                "POST", "/fci/dungeon/1000000001/balance", b"{}", "application/json", auth
            )
            self.assertEqual((status, payload["error"]), (404, "dungeon_not_found"))
            self.assertIsNone(game.handle_http("GET", "/patch-version.json", b"", None, None))

    def test_story_2012_requires_real_fight_active_data_before_balance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            auth = "Token " + token

            status, payload = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":2012}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload["baseInfo"]["dungeonId"], 2012)
            uid = payload["dungeonUid"]
            status, replay = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":2012}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(replay["dungeonUid"], uid)
            self.assertEqual(replay["baseInfo"]["createTime"], payload["baseInfo"]["createTime"])
            self.assertEqual(replay["baseInfo"]["actorUid"], payload["baseInfo"]["actorUid"])

            active = game._types["FightActiveData"]()
            active.dungeonType = 1
            active.stageID = 2012
            active.gameRound = 1
            active.winTeam = 0
            status, ack = game.handle_http(
                "PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(), "application/wireformat", auth
            )
            self.assertEqual((status, ack), (200, {}))
            status, result = game.handle_http(
                "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", auth
            )
            self.assertEqual((status, result["error"]), (409, "battle_not_finished"))

            # Only the reported terminal protobuf state permits balancing.
            active.winTeam = 1
            self.assertEqual(game.handle_http(
                "PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(), "application/wireformat", auth
            ), (200, {}))
            decoded_active = game._types["FightActiveData"]()
            decoded_active.ParseFromString(active.SerializeToString())
            self.assertEqual(decoded_active.winTeam, 1)
            with closing(game._connect()) as connection:
                stored_active = connection.execute(
                    "SELECT active_data FROM local_dungeons WHERE dungeon_uid = ?", (uid,)
                ).fetchone()[0]
            stored_decoded = game._types["FightActiveData"]()
            stored_decoded.ParseFromString(bytes(stored_active))
            self.assertEqual(stored_decoded.winTeam, 1)
            status, result = game.handle_http(
                "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(result["balanceInfo"]["balanced"])
            self.assertTrue(result["balanceInfo"]["win"])
            # 2012 consumes 1 energy -> 1 player EXP; Lv2 needs exactly 1
            # (client PlayerLevelInitConfig).  count is pre-rollover (0 + 1).
            self.assertEqual(result["balanceInfo"]["playerLevel"], 2)
            self.assertEqual(result["balanceInfo"]["playerLevelBeforeBalance"], 1)
            self.assertEqual(result["balanceInfo"]["resChange"][0]["baseRes"]["countDelta"], 1)
            self.assertEqual(result["balanceInfo"]["resChange"][0]["baseRes"]["count"], 1)
            self.assertTrue(result["chapterInfo"][0]["recordDungeons"]["2012"]["finished"])
            with closing(game._connect()) as connection:
                resources = connection.execute(
                    "SELECT resources_json FROM game_profiles WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            # Stored EXP is the remainder within the new level (1 - 1 = 0).
            self.assertIn({"type": 1, "id": 0, "count": 0, "param": 0}, json.loads(resources))
            status, chapter = game.handle_http(
                "GET", "/fci/dungeon/chapter", b"", "application/wireformat", auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(chapter.record.dungeon_record[2012].finished)
            self.assertEqual(chapter.summary.finished_story_dungeons_cnt, 1)
            status, repeat = game.handle_http(
                "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(repeat, result)
            status, repeat = game.handle_http(
                "PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(), "application/wireformat", auth
            )
            self.assertEqual((status, repeat["error"]), (409, "dungeon_not_active"))

    def test_story_2013_uses_the_same_terminal_active_protocol(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            auth = "Token " + token

            status, entered = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":2013}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(entered["baseInfo"]["dungeonId"], 2013)
            uid = entered["dungeonUid"]
            active = game._types["FightActiveData"]()
            active.dungeonType = 1
            active.stageID = 2013
            active.gameRound = 3
            active.winTeam = 1
            self.assertEqual(
                game.handle_http(
                    "PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(), "application/wireformat", auth
                ),
                (200, {}),
            )
            status, result = game.handle_http(
                "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(result["balanceInfo"]["win"])
            self.assertEqual(result["balanceInfo"]["resChange"][0]["baseRes"]["countDelta"], 1)
            self.assertTrue(result["chapterInfo"][0]["recordDungeons"]["2013"]["finished"])
            status, chapter = game.handle_http(
                "GET", "/fci/dungeon/chapter", b"", "application/wireformat", auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(chapter.record.dungeon_record[2013].finished)

    def test_normal_entry_accepts_api_dungeon_wireformat(self):
        """Normal chapter entries use ApiDungeon rather than the story JSON seam."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            auth = "Token " + token

            request = game._types["ApiDungeon"]()
            request.dungeon_id = 1
            request.select_actor_group_id = 0
            status, payload = game.handle_http(
                "POST",
                "/fci/dungeon/",
                request.SerializeToString(),
                "application/wireformat",
                auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload["baseInfo"]["dungeonId"], 1)
            self.assertEqual(payload["baseInfo"]["dungeonType"], 2)
            self.assertEqual(payload["baseInfo"]["actorUid"], [1, 0, 0, 0, 0, 0])  # six formation slots

            uid = payload["dungeonUid"]
            active = game._types["FightActiveData"]()
            active.dungeonType = 2
            active.stageID = 1
            active.gameRound = 1
            active.winTeam = 1
            self.assertEqual(
                game.handle_http(
                    "PATCH",
                    f"/fci/dungeon/{uid}/active/",
                    active.SerializeToString(),
                    "application/wireformat",
                    auth,
                ),
                (200, {}),
            )
            status, result = game.handle_http(
                "POST", f"/fci/dungeon/{uid}/balance", b"{}", "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(result["balanceInfo"]["win"])
            self.assertTrue(result["chapterInfo"][0]["recordDungeons"]["1"]["finished"])

    def test_finished_story_and_normal_records_survive_service_restart(self):
        """Chapter progress is derived from persisted local dungeon rows."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            auth = "Token " + token

            for dungeon_id, dungeon_type in ((2012, 1), (2013, 1), (1, 2)):
                status, entered = game.handle_http(
                    "POST",
                    "/fci/dungeon/",
                    json.dumps({"dungeonId": dungeon_id}).encode(),
                    "application/json",
                    auth,
                )
                self.assertEqual(status, 200)
                active = game._types["FightActiveData"]()
                active.dungeonType = dungeon_type
                active.stageID = dungeon_id
                active.gameRound = 1
                active.winTeam = 1
                self.assertEqual(
                    game.handle_http(
                        "PATCH",
                        f"/fci/dungeon/{entered['dungeonUid']}/active/",
                        active.SerializeToString(),
                        "application/wireformat",
                        auth,
                    ),
                    (200, {}),
                )
                status, result = game.handle_http(
                    "POST",
                    f"/fci/dungeon/{entered['dungeonUid']}/balance",
                    b"{}",
                    "application/json",
                    auth,
                )
                self.assertEqual(status, 200)
                self.assertTrue(result["balanceInfo"]["win"])

            # Reconstruct the service object against the same account/game DB.
            restarted = LocalGame(root / "game", accounts)
            status, chapter = restarted.handle_http(
                "GET", "/fci/dungeon/chapter", b"", "application/wireformat", auth
            )
            self.assertEqual(status, 200)
            self.assertTrue(chapter.record.dungeon_record[2012].finished)
            self.assertTrue(chapter.record.dungeon_record[2013].finished)
            self.assertTrue(chapter.record.dungeon_record[1].finished)
            self.assertEqual(chapter.summary.finished_story_dungeons_cnt, 2)
            self.assertEqual(chapter.summary.finished_normal_dungeons_cnt, 1)
            self.assertNotIn(2012, chapter.summary.new_dungeon_id)
            self.assertNotIn(2013, chapter.summary.new_dungeon_id)
            self.assertNotIn(1, chapter.summary.new_dungeon_id)

    def test_startup_does_not_rewrite_legacy_zero_exp_balances(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = self._guest_account(accounts)
            profile = game._profile(account)
            old_balance = {
                "chapterSummaryInfo": {},
                "chapterInfo": [],
                "balanceInfo": {
                    "balanced": True,
                    "balanceTime": 1,
                    "resChange": [
                        {"baseRes": {"type": 1, "id": 0, "count": 0, "param": 0, "countDelta": 0}}
                    ],
                    "playerLevel": 1,
                    "playerLevelBeforeBalance": 1,
                    "win": True,
                },
                "resChange": [],
            }
            with closing(game._connect()) as connection, connection:
                connection.executemany(
                    """
                    INSERT INTO local_dungeons
                    (dungeon_uid, account_id, player_id, dungeon_id, dungeon_type,
                     actor_uids_json, status, created_at, closed_at, active_data,
                     active_updated_at, balance_json)
                    VALUES (?, ?, ?, ?, 1, ?, 'balanced', 1, 2, NULL, NULL, ?)
                    """,
                    [
                        (2000000001, account["accountId"], profile["player_id"], 2011, "[1]", json.dumps(old_balance)),
                        (2000000002, account["accountId"], profile["player_id"], 2012, "[1]", json.dumps(old_balance)),
                    ],
                )

            # Re-opening the service must not invent a reward for an old
            # cached response.  New rewards are granted only while settling a
            # current battle from the configured local policy.
            LocalGame(root / "game", accounts)
            with closing(game._connect()) as connection:
                resources = json.loads(
                    connection.execute(
                        "SELECT resources_json FROM game_profiles WHERE account_id = ?",
                        (account["accountId"],),
                    ).fetchone()[0]
                )
                caches = [
                    json.loads(row[0])
                    for row in connection.execute(
                        "SELECT balance_json FROM local_dungeons WHERE account_id = ? ORDER BY dungeon_uid",
                        (account["accountId"],),
                    ).fetchall()
                ]
            self.assertFalse(any(int(item.get("type", 0)) == 1 for item in resources))
            self.assertEqual(caches[0]["balanceInfo"]["resChange"][0]["baseRes"]["countDelta"], 0)
            self.assertEqual(caches[0]["balanceInfo"]["resChange"][0]["baseRes"]["count"], 0)
            self.assertEqual(caches[1]["balanceInfo"]["resChange"][0]["baseRes"]["countDelta"], 0)
            self.assertEqual(caches[1]["balanceInfo"]["resChange"][0]["baseRes"]["count"], 0)

            # A second service start sees positive countDelta and is a no-op.
            LocalGame(root / "game", accounts)
            with closing(game._connect()) as connection:
                stored = connection.execute(
                    "SELECT resources_json FROM game_profiles WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            self.assertFalse(any(int(item.get("type", 0)) == 1 for item in json.loads(stored)))


if __name__ == "__main__":
    unittest.main()
