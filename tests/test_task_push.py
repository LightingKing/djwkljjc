"""Live task progress push (ScTasksChange -> Common/TaskUtil.InitData)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory  # noqa: E402

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
from server.core.push_service import (  # noqa: E402
    SC_TASKS_CHANGE,
    PushService,
    PushServiceError,
    encode_tasks_change,
)
from tasks import LocalTasks  # noqa: E402
from test_tasks import _guest  # noqa: E402


def _ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def _sc_tasks_change_class():
    """Independent descriptor built from the client's compiled field numbers.

    IL2CPP dump.cs: ScTasksChange (TypeDefIndex 5479) and ApiTask.Types.result
    (TypeDefIndex 5454).  Field names use the Lua-visible camelCase names that
    TaskUtil.InitData reads.
    """

    T = descriptor_pb2.FieldDescriptorProto
    fdp = descriptor_pb2.FileDescriptorProto(name="ljjc_test_tasks_change.proto", package="ljjctest", syntax="proto3")
    result = fdp.message_type.add(name="ApiTaskResult")
    for name, number, kind in (
        ("id", 1, T.TYPE_UINT32), ("progress", 2, T.TYPE_UINT32), ("isDone", 3, T.TYPE_BOOL),
        ("rewardGot", 4, T.TYPE_BOOL), ("startTime", 6, T.TYPE_UINT32), ("endTime", 7, T.TYPE_UINT32),
        ("param", 8, T.TYPE_UINT32), ("actorCultivationActivityId", 9, T.TYPE_UINT32),
        ("actorCultivationRewardProgress", 10, T.TYPE_UINT32), ("actorCultivationVipOnly", 11, T.TYPE_BOOL),
        ("dailyRefresh", 12, T.TYPE_BOOL),
    ):
        result.field.add(name=name, number=number, type=kind, label=T.LABEL_OPTIONAL)
    change = fdp.message_type.add(name="ScTasksChange")
    change.field.add(name="clearAll", number=1, type=T.TYPE_BOOL, label=T.LABEL_OPTIONAL)
    change.field.add(name="tasks", number=2, type=T.TYPE_MESSAGE, label=T.LABEL_REPEATED,
                     type_name=".ljjctest.ApiTaskResult")
    change.field.add(name="activePoint", number=4, type=T.TYPE_UINT32, label=T.LABEL_OPTIONAL)
    for name, number in (("delTasks", 3), ("activePointRewardGot", 5), ("disableCategory", 6), ("taskTargetDone", 7)):
        change.field.add(name=name, number=number, type=T.TYPE_UINT32, label=T.LABEL_REPEATED)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fdp)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName("ljjctest.ScTasksChange"))


ScTasksChange = _sc_tasks_change_class()


def _decode(data: bytes):
    message = ScTasksChange()
    message.ParseFromString(data)
    return message


class _FakeConnection:
    def __init__(self, sequence: int = 3) -> None:
        self.last_client_sequence = sequence
        self.messages: list[tuple[int, str, bytes]] = []

    def send_sc(self, ack, message_type, data) -> None:
        self.messages.append((ack, message_type, bytes(data)))


class EncodeTasksChangeTests(unittest.TestCase):
    def test_round_trips_get_task_payload_shape(self) -> None:
        payload = {
            "tasks": [
                {"id": 3, "progress": 4, "isDone": False, "rewardGot": False, "endTime": 0},
                {"id": 1101, "progress": 5, "isDone": True, "rewardGot": True, "endTime": 0},
            ],
            "delTasks": [1102, 1103],
            "disableCategory": [3, 4, 5, 6, 7],
            "activePoint": 20,
            "activePointRewardGot": [20],
            "taskTargetDone": [],
            "clearAll": False,
        }
        message = _decode(encode_tasks_change(payload))
        self.assertFalse(message.clearAll)
        self.assertEqual([(t.id, t.progress, t.isDone, t.rewardGot, t.endTime) for t in message.tasks],
                         [(3, 4, False, False, 0), (1101, 5, True, True, 0)])
        self.assertEqual(list(message.delTasks), [1102, 1103])
        self.assertEqual(list(message.disableCategory), [3, 4, 5, 6, 7])
        self.assertEqual(message.activePoint, 20)
        self.assertEqual(list(message.activePointRewardGot), [20])
        self.assertEqual(list(message.taskTargetDone), [])

    def test_zero_valued_task_fields_are_written_explicitly(self) -> None:
        data = encode_tasks_change({"tasks": [{"id": 9}]})
        # field 2 (tasks) holds id=9, progress=0, isDone=0, rewardGot=0, endTime=0
        self.assertIn(bytes([0x12, 10, 0x08, 9, 0x10, 0, 0x18, 0, 0x20, 0, 0x38, 0]), data)
        self.assertTrue(data.startswith(b"\x08\x00"))  # clearAll=false present
        self.assertIn(b"\x20\x00", data)  # activePoint=0 present

    def test_rejects_malformed_values(self) -> None:
        with self.assertRaises(PushServiceError):
            encode_tasks_change({"tasks": [{"id": -1}]})
        with self.assertRaises(PushServiceError):
            encode_tasks_change({"delTasks": "1,2"})
        with self.assertRaises(PushServiceError):
            encode_tasks_change(["not", "a", "mapping"])  # type: ignore[arg-type]


class PushServiceTaskChangeTests(unittest.TestCase):
    def test_account_id_alias_reaches_open_id_session(self) -> None:
        service = PushService()
        connection = _FakeConnection()
        service.register(connection, {"accountId": 7, "openId": "open-7"})
        self.assertEqual(service.key_for_account_id(7), "open-7")
        self.assertEqual(service.tasks_change_for_account_id(7, {"tasks": [{"id": 3, "progress": 1}]}), 1)
        ack, message_type, data = connection.messages[-1]
        self.assertEqual((ack, message_type), (3, SC_TASKS_CHANGE))
        self.assertEqual(_decode(data).tasks[0].progress, 1)
        self.assertEqual(service.tasks_change_for_account_id(8, {"tasks": []}), 0)  # other account offline
        service.unregister(connection, {"openId": "open-7"})
        self.assertIsNone(service.key_for_account_id(7))
        self.assertEqual(service.tasks_change_for_account_id(7, {"tasks": []}), 0)


class LocalTasksNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, _ = _guest(self.accounts)
        self.profile = dict(self.game._profile(self.account))
        self.profile["level"] = 1
        self.now = [_ts(2026, 10, 29, 8, 0)]
        self.tasks = LocalTasks(self.game, clock=lambda: self.now[0])
        status, _ = self.tasks.handle_http("GET", "/fci/task/", None, self.profile)
        self.assertEqual(status, 200)
        self.calls: list[tuple[dict, dict]] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _record(self, profile, payload) -> None:
        self.calls.append((profile, payload))

    @staticmethod
    def _task(payload: dict, task_id: int) -> dict:
        return next(task for task in payload["tasks"] if task["id"] == task_id)

    def test_progress_change_invokes_notifier_with_account_and_payload(self) -> None:
        self.tasks.notifier = self._record
        changed = self.tasks.on_battle_win(self.profile, 1, 6)
        self.assertIn(3, changed)
        self.assertEqual(len(self.calls), 1)
        profile, payload = self.calls[0]
        self.assertEqual(profile["account_id"], self.profile["account_id"])
        json.dumps(payload)
        self.assertEqual(self._task(payload, 3)["progress"], 1)
        self.assertEqual(set(payload), {"tasks", "delTasks", "disableCategory", "activePoint",
                                        "activePointRewardGot", "taskTargetDone", "clearAll"})
        self.assertFalse(payload["clearAll"])  # merge: keeps special categories
        # The pushed payload equals what the next GET /fci/task/ returns.
        self.assertEqual(payload, self.tasks.snapshot(self.profile))
        decoded = _decode(encode_tasks_change(payload))
        self.assertEqual(next(t.progress for t in decoded.tasks if t.id == 3), 1)

    def test_unchanged_hook_does_not_push(self) -> None:
        self.tasks.notifier = self._record
        self.assertEqual(self.tasks.on_level(self.profile), [])
        self.assertEqual(self.calls, [])

    def test_task_claim_pushes_reward_state_and_active_point(self) -> None:
        for _ in range(5):
            self.tasks.on_battle_win(self.profile, 1, 6)
        self.tasks.notifier = self._record
        status, result = self.tasks.handle_http("POST", "/fci/task/3/reward/", None, self.profile)
        self.assertEqual(status, 200)
        self.assertEqual(len(self.calls), 1)
        payload = self.calls[0][1]
        self.assertTrue(self._task(payload, 3)["rewardGot"])
        self.assertEqual(payload["activePoint"], result["activePoint"])
        status, _ = self.tasks.handle_http("POST", "/fci/task/activepoint/reward/",
                                           json.dumps({"targetActivePoint": 20}).encode(), self.profile)
        self.assertEqual(status, 200)
        self.assertEqual(self.calls[-1][1]["activePointRewardGot"], [20])
        # A rejected claim changes nothing and pushes nothing.
        count = len(self.calls)
        self.assertEqual(self.tasks.handle_http("POST", "/fci/task/3/reward/", None, self.profile)[0], 409)
        self.assertEqual(len(self.calls), count)

    def test_no_notifier_and_failing_notifier_are_harmless(self) -> None:
        self.assertIsNone(self.tasks.notifier)
        self.assertIn(3, self.tasks.on_battle_win(self.profile, 1, 6))

        def broken(profile, payload):
            raise ConnectionError("push client gone")

        self.tasks.notifier = broken
        self.assertIn(3, self.tasks.on_battle_win(self.profile, 1, 6))
        self.assertEqual(self._task(self.tasks.snapshot(self.profile), 3)["progress"], 2)


class ServerWiringTests(unittest.TestCase):
    def test_fixture_server_wires_game_tasks_to_push_service(self) -> None:
        from server import FixtureServer, JsonlLogger

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "state")
            game = LocalGame(root / "game", accounts)
            server = FixtureServer(("127.0.0.1", 0), root, JsonlLogger(root / "audit.jsonl"),
                                   accounts=accounts, game=game)
            try:
                self.assertIsNotNone(game.tasks.notifier)
                account, _ = _guest(accounts)
                profile = dict(game._profile(account))
                game.tasks.handle_http("GET", "/fci/task/", None, profile)
                # Offline: progress still recorded, nothing delivered, no error.
                self.assertIn(3, game.tasks.on_battle_win(profile, 1, 6))
                connection = _FakeConnection()
                server.push_service.register(connection, account)  # as push.py does after CsConnect
                self.assertIn(3, game.tasks.on_battle_win(profile, 1, 6))
                self.assertEqual(len(connection.messages), 1)
                _, message_type, data = connection.messages[0]
                self.assertEqual(message_type, SC_TASKS_CHANGE)
                self.assertEqual(next(t.progress for t in _decode(data).tasks if t.id == 3), 2)
            finally:
                server.server_close()

    def test_http_and_tls_servers_sharing_a_game_both_deliver(self) -> None:
        from server import FixtureServer, JsonlLogger

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "state")
            game = LocalGame(root / "game", accounts)
            logger = JsonlLogger(root / "audit.jsonl")
            http_server = FixtureServer(("127.0.0.1", 0), root, logger, accounts=accounts, game=game)
            tls_server = FixtureServer(("127.0.0.1", 0), root, logger, tls=True, accounts=accounts, game=game)
            try:
                account, _ = _guest(accounts)
                profile = dict(game._profile(account))
                game.tasks.handle_http("GET", "/fci/task/", None, profile)
                # The push socket landed on the HTTP server (wired first).
                connection = _FakeConnection()
                http_server.push_service.register(connection, account)
                game.tasks.on_battle_win(profile, 1, 6)
                self.assertEqual([m[1] for m in connection.messages], [SC_TASKS_CHANGE])
                http_server.push_service.unregister(connection, account)
                tls_server.push_service.register(connection, account)
                game.tasks.on_battle_win(profile, 1, 6)
                self.assertEqual(len(connection.messages), 2)
            finally:
                http_server.server_close()
                tls_server.server_close()


if __name__ == "__main__":
    unittest.main()
