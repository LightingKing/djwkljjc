from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import sqlite3
import json

from accounts import LocalAccounts
from game import LocalGame


class GameTests(unittest.TestCase):
    def _accounts_and_game(self, root: Path):
        now = [1_700_000_000]
        clock = lambda: now[0]
        accounts = LocalAccounts(root / "accounts", clock=clock)
        # Keep the fixture's first profile empty so migration behavior is
        # tested independently from the production local-test defaults.
        game = LocalGame(
            root / "game",
            accounts,
            clock=clock,
            scene_id=0,
            initial_actor_id=None,
            initial_actor_ids=[],
            initial_resources=[],
        )
        return accounts, game, now

    @staticmethod
    def _guest_account(accounts: LocalAccounts):
        def auth_body(entry: str) -> bytes:
            value = entry.encode("utf-8")
            return b"\x0a" + bytes((len(value),)) + value + b"\x12\x05guest"

        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        authz = "AccountToken " + first[1]["accountToken"]
        entry_result = accounts.handle(
            "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", authz
        )
        entry = entry_result[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", authz)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        second_authz = "AccountToken " + second[1]["accountToken"]
        result = accounts.handle(
            "POST", "/Auth/", auth_body(entry), "application/wireformat", second_authz
        )
        return accounts.verify_access_token(result[1]["tokenAccess"])

    def test_login_messages_round_trip_real_schema_and_persist_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts, game, _ = self._accounts_and_game(root)
            account_record = self._guest_account(accounts)
            self.assertIsNotNone(account_record)

            messages = game.login_messages(account_record)
            self.assertEqual(
                [name for name, _ in messages],
                [
                    "ScLoginBegin",
                    "ScLoginTutorial",
                    "DBGamePlayer.Actors",
                    "DBGamePlayer.EquipPackage",
                    "DBGamePlayer.ItemPackage",
                    "ScLoginEnd",
                ],
            )
            # Re-decode through the same descriptor-backed classes.  This
            # proves the server does not emit hand-written arbitrary bytes.
            pool = game._pool
            begin = game._types["ScLoginBegin"]()
            begin.ParseFromString(messages[0][1])
            self.assertTrue(begin.player_id)
            actors = game._types["DBGamePlayer.Actors"]()
            actors.ParseFromString(messages[2][1])
            self.assertEqual(list(actors.unlocked_skins), [0])
            end = game._types["ScLoginEnd"]()
            end.ParseFromString(messages[-1][1])
            self.assertEqual(end.base_info.level, 1)
            self.assertEqual(end.base_info.home_back_ground.current, 0)

            # A new LocalGame instance sees the same SQLite profile/player ID.
            game_again = LocalGame(root / "game", accounts, clock=lambda: 1_700_000_001)
            again = game_again.login_messages(account_record)
            begin_again = game_again._types["ScLoginBegin"]()
            begin_again.ParseFromString(again[0][1])
            self.assertEqual(begin_again.player_id, begin.player_id)

    def test_corrupt_profile_json_is_repaired_before_login(self):
        """A damaged local save must not make the next login crash."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts, game, _ = self._accounts_and_game(root)
            account_record = self._guest_account(accounts)
            game.login_messages(account_record)
            with closing(sqlite3.connect(root / "game" / "game.sqlite3")) as connection:
                connection.execute(
                    """
                    UPDATE game_profiles
                    SET resources_json = ?, actor_ids_json = ?,
                        tutorials_json = ?, auto_fight_json = ?
                    WHERE account_id = ?
                    """,
                    (
                        json.dumps(["bad", {"type": 6, "id": 0, "count": -5}, {"type": 7, "id": 0, "count": "2"}]),
                        json.dumps(["bad", 0, "1001"]),
                        json.dumps({"1": "bad", "2": 1}),
                        json.dumps({"banedSkills": {"1001": "bad", "1002": 1, "1003": 2}}),
                        account_record["accountId"],
                    ),
                )
                connection.commit()

            restarted = LocalGame(root / "game", accounts, clock=lambda: 1_700_000_001)
            profile = restarted._profile(account_record)
            self.assertEqual(
                profile["resources"],
                [
                    {"type": 6, "id": 0, "count": 0},
                    {"type": 7, "id": 0, "count": 2},
                ],
            )
            self.assertEqual(profile["actor_ids"], [1001])
            self.assertEqual(profile["tutorials"]["1"], 0)
            self.assertEqual(profile["tutorials"]["2"], 1)
            self.assertEqual(profile["auto_fight"]["banedSkills"], {"1002": 1})
            messages = restarted.login_messages(account_record)
            self.assertEqual(messages[-1][0], "ScLoginEnd")

    def test_http_routes_require_access_and_return_minimal_profile_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts, game, _ = self._accounts_and_game(Path(temp))
            self.assertEqual(game.handle_http("GET", "/fci/applylist/", authorization=None)[0], 401)
            account_record = self._guest_account(accounts)
            auth = "AccountToken " + account_record["openId"]
            # Use the access token from the account row via a fresh Auth flow;
            # the helper intentionally returns only the verified record.
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account_record["accountId"],),
                ).fetchone()[0]
            auth = "AccountToken " + token
            self.assertIsNone(game.handle_http("GET", "/unknown", authorization=auth))
            self.assertEqual(game.handle_http("POST", "/fci/login/", authorization=auth), (200, {}))
            status, applications = game.handle_http("GET", "/fci/applylist/", authorization=auth)
            self.assertEqual(status, 200)
            application_ids = [row["playerId"] for row in applications]
            self.assertEqual(len(application_ids), 2)
            self.assertEqual(len(set(application_ids)), len(application_ids))
            self.assertTrue(all(pid.startswith("local-bot-") for pid in application_ids))
            self.assertTrue(all(row["name"] and row["level"] >= 1 for row in applications))
            self.assertEqual(game.handle_http("GET", "/fci/applylist/", authorization=auth.replace("AccountToken", "Token")),
                             (200, applications))
            self.assertEqual(game.handle_http("POST", "/fci/login/", authorization=auth), (200, {}))
            self.assertEqual(game.handle_http("GET", "/fci/applylist/", authorization=auth), (200, applications))
            self.assertEqual(
                game.handle_http("GET", "/fci/Signboard/", authorization=auth),
                (200, {"signboard": 101, "unlockedSignboardId": [101], "checkSignboardTime": 0}),
            )
            status, guild = game.handle_http("GET", "/fci/guild-player/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(guild["guildId"], 0)
            self.assertEqual(guild["rentedActors"], {})
            self.assertEqual(guild["donateLimitEndTime"], 0)
            self.assertEqual(guild["guildDungeonLimitEndTime"], 0)
            self.assertEqual(
                game.handle_http("GET", "/fci/auto-fight-conf/", authorization=auth),
                (200, {"banedSkills": {}}),
            )
            auto = game._types["ApiAutoFightConf"]()
            auto.configure_skills["1001001"] = 1
            status, saved = game.handle_http(
                "POST",
                "/fci/auto-fight-conf/",
                auto.SerializeToString(),
                "application/wireformat",
                auth,
            )
            self.assertEqual((status, saved), (200, {"banedSkills": {"1001001": 1}}))
            self.assertEqual(
                game.handle_http("GET", "/fci/auto-fight-conf/", authorization=auth),
                (200, {"banedSkills": {"1001001": 1}}),
            )
            status, dungeon = game.handle_http("GET", "/fci/dungeon/chapter", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(dungeon.DESCRIPTOR.full_name, "ApiChapterInfo.result")
            # The chapter screen includes the captured first six normal
            # chapters now.  Keep the original tutorial/story IDs as a
            # compatibility requirement while allowing data-driven additions.
            # Training chapter 2001 is open from the start, as in the client.
            self.assertTrue({1, 1001, 2001} <= set(dungeon.record.chapter_record))
            # The client treats any dungeon record as "stage open", so a new
            # account carries only its open stage: the story tutorial 2011.
            self.assertEqual(set(dungeon.record.dungeon_record), {2011})
            self.assertEqual(list(dungeon.summary.unlocked_chapter_id), [1, 1001, 2001])

            # The city red-point bindings also request per-chapter detail
            # resources.  They must be typed ApiChapterInfo.get messages;
            # returning the summary only leaves fci/dungeon/chapter/<id> nil.
            status, detail = game.handle_http(
                "GET", "/fci/dungeon/chapter/24", authorization=auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(detail.DESCRIPTOR.full_name, "ApiChapterInfo.get")
            self.assertEqual(detail.record_chapter.chapter_id, 24)
            self.assertEqual(set(detail.record_dungeons), set())

    def test_tutorial_bootstrap_has_all_local_status_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts, game, _ = self._accounts_and_game(Path(temp))
            account_record = self._guest_account(accounts)
            tutorial = game._types["ScLoginTutorial"]()
            tutorial.ParseFromString(dict(game.login_messages(account_record))["ScLoginTutorial"])
            # Every id of the client's complete Tutorial table, in its
            # sequence order (data/client-tables/Tutorial.json, 55 rows).
            client_rows = json.loads(
                (Path(__file__).resolve().parents[1] / "data" / "client-tables" / "Tutorial.json").read_text(encoding="utf-8")
            )["rows"]
            expected = [row["id"] for row in sorted(client_rows, key=lambda row: (row.get("sequence", row["id"]), row["id"]))]
            self.assertEqual(len(expected), 55)
            self.assertEqual([entry.id for entry in tutorial.tutorial_info], expected)
            self.assertTrue(all(entry.status == 0 for entry in tutorial.tutorial_info))
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account_record["accountId"],),
                ).fetchone()[0]
            update = game._types["ApiTutorial"]()
            update.status = 1
            status, payload = game.handle_http(
                "PUT",
                "/fci/tutorial/1",
                update.SerializeToString(),
                "application/wireformat",
                "Token " + token,
            )
            self.assertEqual((status, payload), (200, {}))
            self.assertEqual(game._profile(account_record)["tutorials"]["1"], 1)

    def test_tutorial_completion_follows_configured_sequence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts, game, _ = self._accounts_and_game(root)
            account_record = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account_record["accountId"],),
                ).fetchone()[0]
            update = game._types["ApiTutorial"]()
            update.status = 1
            status, payload = game.handle_http(
                "PUT", "/fci/tutorial/2", update.SerializeToString(),
                "application/wireformat", "Token " + token,
            )
            # Any tutorial can be completed: the client orders them itself.
            self.assertEqual((status, payload), (200, {}))
            self.assertEqual(
                game.handle_http(
                    "PUT", "/fci/tutorial/1", update.SerializeToString(),
                    "application/wireformat", "Token " + token,
                ),
                (200, {}),
            )
            self.assertEqual(
                game.handle_http(
                    "PUT", "/fci/tutorial/2", update.SerializeToString(),
                    "application/wireformat", "Token " + token,
                ),
                (200, {}),
            )

    def test_tutorial_step_progress_is_persistent_and_resettable(self):
        """The client posts each reached TutorialStep before final PUT."""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts, game, _ = self._accounts_and_game(root)
            account_record = self._guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account_record["accountId"],),
                ).fetchone()[0]
            auth = "Token " + token

            self.assertEqual(
                game.handle_http("POST", "/fci/tutorial/1/12", b"", "application/wireformat", auth),
                (200, {}),
            )
            # Out-of-order/replayed step posts cannot regress the cursor.
            self.assertEqual(
                game.handle_http("POST", "/fci/tutorial/1/4", b"", "application/wireformat", auth),
                (200, {}),
            )
            restarted = LocalGame(root / "game", accounts, clock=lambda: 1_700_000_001)
            profile = restarted._profile(account_record)
            self.assertEqual(profile["tutorial_steps"]["1"], 12)
            listed = restarted.handle_http("GET", "/fci/tutorial/", authorization=auth)
            self.assertEqual(listed[0], 200)
            self.assertEqual(next(item for item in listed[1] if item["id"] == 1)["step"], 12)

            update = restarted._types["ApiTutorial"]()
            update.status = 1
            update.step_force_finish = True
            self.assertEqual(
                restarted.handle_http("PUT", "/fci/tutorial/1", update.SerializeToString(), "application/wireformat", auth),
                (200, {}),
            )
            update.status = 0
            update.step_force_finish = False
            self.assertEqual(
                restarted.handle_http("PUT", "/fci/tutorial/1", update.SerializeToString(), "application/wireformat", auth),
                (200, {}),
            )
            self.assertEqual(restarted._profile(account_record)["tutorial_steps"]["1"], 0)

    def test_verified_defaults_migrate_only_empty_profiles_and_keep_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts, game, _ = self._accounts_and_game(root)
            account_record = self._guest_account(accounts)
            # Create the historical empty bootstrap profile first.
            game.login_messages(account_record)

            migrated = LocalGame(
                root / "game",
                accounts,
                initial_actor_id=101,
                initial_resources=[{"type": 1, "id": 100, "count": 50}],
            )
            profile = migrated._profile(account_record)
            self.assertEqual(profile["actor_id"], 101)
            self.assertEqual(profile["resources"], [{"type": 1, "id": 100, "count": 50}])
            backup = root / "game" / "game.sqlite3.preconfig-migration.bak"
            self.assertTrue(backup.exists())
            with closing(sqlite3.connect(backup)) as connection:
                old = connection.execute(
                    "SELECT actor_id, resources_json FROM game_profiles WHERE account_id = ?",
                    (account_record["accountId"],),
                ).fetchone()
            self.assertEqual(old, (None, "[]"))

            # A second migration does not overwrite a non-empty profile and
            # gets its own backup name only if a real change is required.
            unchanged = LocalGame(
                root / "game",
                accounts,
                initial_actor_id=202,
                initial_resources=[{"type": 2, "id": 200, "count": 1}],
            )
            self.assertEqual(unchanged._profile(account_record)["actor_id"], 101)
            self.assertEqual(unchanged._profile(account_record)["resources"], [{"type": 1, "id": 100, "count": 50}])

    def test_production_defaults_are_local_sample_values_and_support_multiple_actors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts, initial_actor_ids=[1001, 1002])
            account_record = self._guest_account(accounts)
            profile = game._profile(account_record)
            self.assertEqual(profile["actor_ids"], [1001, 1002])
            self.assertEqual(profile["signboard"]["signboard"], 101)
            messages = game.login_messages(account_record)
            actors = game._types["DBGamePlayer.Actors"]()
            actors.ParseFromString(messages[2][1])
            self.assertEqual([actor.id for actor in actors.player_actors], [1001, 1002])
            self.assertEqual(dict(actors.actor_id_map), {1001: 1, 1002: 2})
            self.assertEqual(list(actors.player_actors[0].attrs)[0].type, 1)
            observed_attrs = {attr.type: (attr.value, attr.is_ratio) for attr in actors.player_actors[0].attrs}
            self.assertEqual(observed_attrs[1], (253, False))
            self.assertEqual(observed_attrs[13], (100, True))
            self.assertEqual(observed_attrs[14], (1000, True))
            # Skills come from the client ActorSkill table (non-passive rows
            # not locked behind a higher star); the captured six are included.
            skill_ids = [skill.id for skill in actors.player_actors[0].skills]
            self.assertEqual(skill_ids[:6], [1001001, 1001002, 1001003, 1001004, 1001005, 1001006])
            self.assertNotIn(1001900, skill_ids)  # unlocks with a higher star


if __name__ == "__main__":
    unittest.main()
