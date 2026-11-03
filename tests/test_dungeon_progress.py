from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from accounts import LocalAccounts
from game import LocalGame


class DungeonProgressTests(unittest.TestCase):
    @staticmethod
    def _account(accounts: LocalAccounts) -> tuple[dict, str]:
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        auth = "AccountToken " + second[1]["accountToken"]
        payload = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
        result = accounts.handle("POST", "/Auth/", payload, "application/wireformat", auth)[1]
        account = accounts.verify_access_token(result["tokenAccess"])
        with closing(accounts._connect()) as connection:
            access = connection.execute("SELECT access_token FROM accounts WHERE account_id = ?", (account["accountId"],)).fetchone()[0]
        return account, "Token " + access

    @staticmethod
    def _finish(game: LocalGame, auth: str, dungeon_id: int, *, difficulty: str = "normal") -> tuple[int, dict]:
        status, entered = game.handle_http(
            "POST", "/fci/dungeon/", json.dumps({"dungeonId": dungeon_id, "difficulty": difficulty}).encode(), "application/json", auth
        )
        assert status == 200
        active = game._types["FightActiveData"]()
        active.dungeonType = 2
        active.stageID = dungeon_id
        active.gameRound = 1
        active.winTeam = 1
        assert game.handle_http("PATCH", f"/fci/dungeon/{entered['dungeonUid']}/active", active.SerializeToString(), "application/wireformat", auth) == (200, {})
        return entered["dungeonUid"], game.handle_http("POST", f"/fci/dungeon/{entered['dungeonUid']}/balance", b"{}", "application/json", auth)

    def test_captured_key_dungeon_gate_is_visible_and_strict_entry_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _, auth = self._account(accounts)
            status, payload = game.handle_http("POST", "/fci/dungeon/", b'{"dungeonId":9,"enforceUnlock":true}', "application/json", auth)
            self.assertEqual((status, payload["error"]), (409, "chapter_locked"))
            self._finish(game, auth, 8)
            status, chapter = game.handle_http("GET", "/fci/dungeon/chapter/2", b"", "application/wireformat", auth)
            self.assertEqual((status, chapter.record_chapter.unlocked), (200, True))

    def test_normal_and_hard_runs_have_separate_active_rows_and_first_clear_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _, auth = self._account(accounts)
            normal_uid, normal = self._finish(game, auth, 1)
            hard_uid, hard = self._finish(game, auth, 1, difficulty="hard")
            self.assertNotEqual(normal_uid, hard_uid)
            self.assertTrue(normal[1]["balanceInfo"]["firstFinish"])
            self.assertTrue(hard[1]["balanceInfo"]["firstFinish"])
            with closing(game._connect()) as connection:
                rows = connection.execute("SELECT difficulty, status FROM local_dungeons WHERE dungeon_uid IN (?, ?) ORDER BY difficulty", (normal_uid, hard_uid)).fetchall()
            self.assertEqual([(row["difficulty"], row["status"]) for row in rows], [("hard", "balanced"), ("normal", "balanced")])

    def test_recovered_story_entry_cost_is_charged_once(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = self._account(accounts)
            before = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 6)
            status, first = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":2012}', "application/json", auth
            )
            self.assertEqual(status, 200)
            # DBH.ResChange applies only wrapped {"baseRes": ...} entries.
            self.assertEqual(
                [item["baseRes"] for item in first["resChange"]],
                [{"type": 6, "id": 0, "param": 0, "count": before - 1, "countDelta": -1}],
            )
            after = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 6)
            self.assertEqual(after, before - 1)
            status, replay = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":2012}', "application/json", auth
            )
            self.assertEqual(status, 200)
            self.assertEqual(replay["dungeonUid"], first["dungeonUid"])
            self.assertEqual(replay["resChange"], [])

    def test_duplicate_resource_costs_are_checked_as_one_atomic_batch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, _auth = self._account(accounts)
            profile = game._profile(account)
            before = next(item["count"] for item in profile["resources"] if item["type"] == 6)
            ok, changes, error = game.dungeons._resource_changes(
                profile,
                [
                    {"type": 6, "id": 0, "param": 0, "count": -(before // 2)},
                    {"type": 6, "id": 0, "param": 0, "count": -(before // 2 + 1)},
                ],
            )
            self.assertEqual((ok, changes, error), (False, [], "insufficient_resource"))
            after = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 6)
            self.assertEqual(after, before)

    def test_sweep_and_chapter_reward_are_gated_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _, auth = self._account(accounts)
            status, locked = game.handle_http("POST", "/fci/dungeon/1/sweep", b"{}", "application/json", auth)
            self.assertEqual((status, locked["error"]), (409, "sweep_locked"))
            self._finish(game, auth, 1)
            status, swept = game.handle_http("POST", "/fci/dungeon/1/sweep", b'{"count":2}', "application/json", auth)
            self.assertEqual((status, swept["sandbox"]), (200, True))
            self._finish(game, auth, 2)
            self._finish(game, auth, 3)
            self._finish(game, auth, 4)
            self._finish(game, auth, 5)
            self._finish(game, auth, 6)
            self._finish(game, auth, 7)
            self._finish(game, auth, 8)
            status, claim = game.handle_http("POST", "/fci/dungeon/chapter/1/reward", b'{"kind":"first_clear"}', "application/json", auth)
            self.assertEqual((status, claim["claimed"]), (200, True))
            status, repeat = game.handle_http("POST", "/fci/dungeon/chapter/1/reward", b'{"kind":"first_clear"}', "application/json", auth)
            self.assertEqual((status, repeat["alreadyClaimed"]), (200, True))

    def test_observed_nonfight_story_transitions_complete_without_fake_battle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _, auth = self._account(accounts)
            for dungeon_id in (2021, 2022):
                status, entered = game.handle_http(
                    "POST", "/fci/dungeon/", json.dumps({"dungeonId": dungeon_id}).encode(), "application/json", auth
                )
                self.assertEqual(status, 200)
                self.assertEqual(entered["baseInfo"]["dungeonId"], dungeon_id)
                status, balance = game.handle_http(
                    "POST", f"/fci/dungeon/{entered['dungeonUid']}/balance", b"{}", "application/json", auth
                )
                self.assertEqual(status, 200)
                self.assertEqual(balance["dungeonId"], dungeon_id)
                self.assertEqual(balance["balanceInfo"]["resChange"][0]["baseRes"]["countDelta"], 0)

    def test_challenge_entry_checks_chapter_unlock_and_null_active_is_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _, auth = self._account(accounts)
            status, entered_challenge = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":11011,"difficulty":"challenge"}',
                "application/json", auth,
            )
            self.assertEqual(status, 200)
            self.assertEqual(entered_challenge["baseInfo"]["dungeonId"], 11011)
            # A malformed/null active payload is a client error, never an
            # uncaught protobuf DecodeError (HTTP 500).
            status, entered = game.handle_http(
                "POST", "/fci/dungeon/", b'{"dungeonId":1}', "application/json", auth,
            )
            self.assertEqual(status, 200)
            uid = entered["dungeonUid"]
            for body in (b"null", b"{broken", b"\xff"):
                status, payload = game.handle_http(
                    "PATCH", f"/fci/dungeon/{uid}/active", body, "application/wireformat", auth,
                )
                self.assertEqual((status, payload["error"]), (400, "invalid_fight_active_data"))
            status, chapter = game.handle_http(
                "GET", "/fci/dungeon/chapter/1", b"", "application/wireformat", auth,
            )
            self.assertEqual(status, 200)
            self.assertTrue(set(chapter.record_dungeons).issubset({1, 2, 3, 4, 5, 6, 7, 8}))


if __name__ == "__main__":
    unittest.main()
