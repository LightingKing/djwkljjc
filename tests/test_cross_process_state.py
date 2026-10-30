from __future__ import annotations

import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from accounts import LocalAccounts
from game import LocalGame


def _send_chat_worker(root: str, authorization: str, index: int, result_queue) -> None:
    """Send one chat message from a fresh worker process.

    The worker deliberately opens its own ``LocalGame`` instance.  This
    models a second local HTTP worker, which a process-local ``RLock`` cannot
    serialize.
    """

    try:
        root_path = Path(root)
        accounts = LocalAccounts(root_path / "accounts")
        game = LocalGame(root_path / "game", accounts)
        body = json.dumps({"text": f"cross-process-{index}"}).encode("utf-8")
        status, payload = game.handle_http(
            "POST", "/fci/chat", body, "application/json", authorization
        )
        result_queue.put((index, status, payload if status != 200 else None))
    except Exception as exc:  # pragma: no cover - surfaced by the parent assertion
        result_queue.put((index, "exception", repr(exc)))


def _start_race_worker(root: str, authorization: str, index: int, result_queue) -> None:
    """Create the same native Race run from an independent process."""

    try:
        root_path = Path(root)
        accounts = LocalAccounts(root_path / "accounts")
        game = LocalGame(root_path / "game", accounts)
        status, room = game.handle_http("GET", "/fci/RacePVPInfo", authorization=authorization)
        if status != 200:
            result_queue.put((index, status, room))
            return
        from google.protobuf import message_factory
        request_type = message_factory.GetMessageClass(game._pool.FindMessageTypeByName("ApiRacePVPDungeon"))
        body = request_type(dungeon_id=room["dungeonId"], select_actor_group_id=1).SerializeToString()
        status, payload = game.handle_http("POST", "/fci/racepvp-dungeon/", body, "application/wireformat", authorization)
        result_queue.put((index, status, payload))
    except Exception as exc:  # pragma: no cover - surfaced by the parent assertion
        result_queue.put((index, "exception", repr(exc)))


class CrossProcessStateTests(unittest.TestCase):
    @staticmethod
    def guest(accounts: LocalAccounts):
        first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        token = "AccountToken " + first[1]["accountToken"]
        entry = accounts.handle(
            "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token
        )[1]["entry"]["entry"]
        accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
        second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
        auth = accounts.handle(
            "POST",
            "/Auth/",
            b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest",
            "application/wireformat",
            "AccountToken " + second[1]["accountToken"],
        )
        return "Token " + auth[1]["tokenAccess"]

    def test_social_json_state_is_serialized_across_worker_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            authorization = self.guest(accounts)

            # Initialize all compatibility tables before workers start so the
            # test exercises the read-modify-write window itself.
            status, _ = game.handle_http(
                "GET", "/fci/chat", authorization=authorization
            )
            self.assertEqual(status, 200)

            worker_count = 8
            context = multiprocessing.get_context("spawn")
            result_queue = context.Queue()
            workers = [
                context.Process(
                    target=_send_chat_worker,
                    args=(str(root), authorization, index, result_queue),
                )
                for index in range(worker_count)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(30)
                self.assertFalse(worker.is_alive(), "worker did not finish")
                self.assertEqual(worker.exitcode, 0)

            results = [result_queue.get(timeout=5) for _ in workers]
            failures = [row for row in results if row[1] != 200]
            self.assertEqual(failures, [], results)

            status, payload = game.handle_http(
                "GET", "/fci/chat", authorization=authorization
            )
            self.assertEqual(status, 200)
            texts = {str(row["text"]) for row in payload["messages"]}
            self.assertTrue(
                {f"cross-process-{index}" for index in range(worker_count)} <= texts,
                payload,
            )

    def test_realtime_match_creation_is_idempotent_across_worker_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts, initial_level=26)
            authorization = self.guest(accounts)
            status, room = game.handle_http("GET", "/fci/RacePVPInfo", authorization=authorization)
            self.assertEqual(status, 200)

            context = multiprocessing.get_context("spawn")
            result_queue = context.Queue()
            worker_count = 2
            workers = [
                context.Process(
                    target=_start_race_worker,
                    args=(str(root), authorization, index, result_queue),
                )
                for index in range(worker_count)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(30)
                self.assertFalse(worker.is_alive(), "worker did not finish")
                self.assertEqual(worker.exitcode, 0)

            results = [result_queue.get(timeout=5) for _ in workers]
            self.assertTrue(all(row[1] == 200 for row in results), results)
            match_ids = {row[2]["dungeonUid"] for row in results}
            self.assertEqual(len(match_ids), 1, results)
            status, summary = game.handle_http(
                "GET", "/fci/RacePVPInfo", authorization=authorization
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(summary["players"]), 20)
            account, error = game._authorized(authorization)
            self.assertIsNone(error)
            profile = game._profile(account)
            self.assertEqual(game.systems._state(profile)["items"]["80004"]["count"], 5)
            self.assertEqual(game.realtime_pvp.race._load(profile)["activeUid"], next(iter(match_ids)))


if __name__ == "__main__":
    unittest.main()
