import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.core.push_service import PushService, PushServiceError  # noqa: E402


class _FakeConnection:
    def __init__(self, sequence=7):
        self.last_client_sequence = sequence
        self.messages = []

    def send_sc(self, ack, message_type, data):
        self.messages.append((ack, message_type, bytes(data)))


class PushServiceTests(unittest.TestCase):
    def test_register_publish_and_unregister(self):
        first = _FakeConnection()
        second = _FakeConnection(11)
        service = PushService()
        account = {"openId": "local-open-id"}
        self.assertEqual(service.register(first, account), "local-open-id")
        self.assertEqual(service.register(second, account), "local-open-id")
        self.assertEqual(service.connection_count(account), 2)

        delivered = service.publish(account, "BasePushMessage", b"\x08\x01")
        self.assertEqual(delivered, 2)
        self.assertEqual(first.messages, [(7, "BasePushMessage", b"\x08\x01")])
        self.assertEqual(second.messages, [(11, "BasePushMessage", b"\x08\x01")])

        self.assertTrue(service.unregister(first))
        self.assertEqual(service.connection_count("local-open-id"), 1)
        self.assertFalse(service.unregister(first))

    def test_failed_session_is_removed_and_unknown_account_is_noop(self):
        class Broken(_FakeConnection):
            def send_sc(self, *_args):
                raise BrokenPipeError("closed")

        service = PushService()
        connection = Broken()
        service.register(connection, "player-2")
        self.assertEqual(service.publish("player-2", "ScMail", b"x"), 0)
        self.assertEqual(service.connection_count("player-2"), 0)
        self.assertEqual(service.publish("missing", "ScMail", b"x"), 0)

    def test_rejects_invalid_selector_and_payload(self):
        service = PushService()
        with self.assertRaises(PushServiceError):
            service.register(_FakeConnection(), {})
        with self.assertRaises(PushServiceError):
            service.publish("p", "", b"x")
        with self.assertRaises(PushServiceError):
            service.publish("p", "ScMail", "text")


if __name__ == "__main__":
    unittest.main()
