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

        delivered = service.red_point(account, {"mail": 1})
        self.assertEqual(delivered, 2)
        self.assertEqual(first.messages, [(7, "ScRedPointData", b'{"mail":1}')])
        self.assertEqual(second.messages, [(11, "ScRedPointData", b'{"mail":1}')])

        self.assertTrue(service.unregister(first))
        self.assertEqual(service.connection_count("local-open-id"), 1)
        self.assertFalse(service.unregister(first))

    def test_event_helpers_use_local_json_payloads(self):
        connection = _FakeConnection(4)
        service = PushService()
        service.register(connection, "player-1")
        self.assertEqual(service.mail("player-1", {"id": 3}), 1)
        self.assertEqual(service.payment("player-1", {"goods": "sandbox"}), 1)
        self.assertEqual(service.chat("player-1", {"text": "hello"}), 1)
        self.assertEqual([entry[1] for entry in connection.messages], ["ScMail", "ScPurchaseDelevery", "ScChat"])
        self.assertEqual(json.loads(connection.messages[0][2]), {"id": 3})

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
