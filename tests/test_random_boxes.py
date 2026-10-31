"""D1 random-box behaviour through the same HTTP seams as the client."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import systems
from accounts import LocalAccounts
from game import LocalGame
from google.protobuf import message_factory
from test_systems import LocalSystemsTests


class RandomBoxRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, self.auth = LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _state(self):
        return self.game.systems._state(self.profile)

    def _give(self, item_id: int, count: int = 1) -> None:
        state = self._state()
        state.setdefault("items", {})[str(item_id)] = {"id": item_id, "count": count, "gotTime": 1, "isNew": True}
        self.game.systems._save(self.profile, state)

    def _call(self, payload):
        return self.game.handle_http("POST", "/fci/itemuse/", json.dumps(payload).encode(), "application/json", self.auth)

    def _bases(self, reply):
        return [change.base_res for change in reply.res_change if change.HasField("base_res")]

    def test_fixed_ascension_box_consumes_and_persists_rewards(self):
        self._give(85003)
        status, reply = self._call({"id": 85003, "count": 1})
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply.DESCRIPTOR.full_name, "ApiPlayerItemUse.result")
        changes = {(row.type, row.id): row.count_delta for row in self._bases(reply)}
        self.assertEqual(changes[(11, 85003)], -1)
        self.assertEqual(changes[(11, 30011)], 10)
        self.assertEqual(changes[(11, 30012)], 10)
        self.assertEqual(self._state()["items"]["85003"]["count"], 0)
        self.assertEqual(self._state()["items"]["30011"]["count"], 10)
        # A new LocalGame reads the same receipt/state from SQLite.
        reopened = LocalGame(self.game.state_dir, self.accounts)
        profile = reopened._profile(self.account)
        state = reopened.systems._state(profile)
        self.assertEqual(state["items"]["30012"]["count"], 10)
        self.assertEqual(len(state["randomBoxReceipts"]), 1)

    def test_equipment_box_uses_real_equip_pool_and_request_retry_is_idempotent(self):
        self._give(75001, 2)
        request = {"id": 75001, "count": 1, "requestId": "box-retry-1"}
        status, first = self._call(request)
        self.assertEqual(status, 200, first)
        equips = [change.equip for change in first.res_change if change.HasField("equip")]
        self.assertEqual(len(equips), 1)
        self.assertIn(equips[0].id, systems.equip_store.EQUIP_POOLS[4])
        state = self._state()
        self.assertEqual(state["items"]["75001"]["count"], 1)
        equip_count = len(state["equips"])
        status, duplicate = self._call(request)
        self.assertEqual(status, 200, duplicate)
        self.assertEqual(duplicate.SerializeToString(), first.SerializeToString())
        self.assertEqual(self._state()["items"]["75001"]["count"], 1)
        self.assertEqual(len(self._state()["equips"]), equip_count)
        conflict = {"id": 75001, "count": 1, "requestId": "box-retry-1"}
        # Different item under an existing key must not silently grant twice.
        self._give(75002, 1)
        status, error = self._call({**conflict, "id": 75002})
        self.assertEqual((status, error["error"]), (409, "idempotency_key_reused"))

    def test_wireformat_box_request_and_limits(self):
        self._give(54001, 2)
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiPlayerItemUse"))
        request = cls(id=54001, count=1)
        status, reply = self.game.handle_http("POST", "/fci/itemuse/", request.SerializeToString(), "application/wireformat", self.auth)
        self.assertEqual(status, 200, reply)
        self.assertTrue(any(row.id == 54001 and row.count_delta == -1 for row in self._bases(reply)))
        status, error = self._call({"id": 54001, "count": 2})
        self.assertEqual((status, error["error"]), (400, "count_exceeds_max_use"))
        self.assertEqual(self._state()["items"]["54001"]["count"], 1)

    def test_invalid_and_insufficient_boxes_do_not_create_receipts(self):
        self._give(85003, 1)
        status, error = self._call({"id": 85003, "count": 0})
        self.assertEqual((status, error["error"]), (400, "invalid_count"))
        status, error = self._call({"id": 85003, "count": 2})
        self.assertEqual((status, error["error"]), (409, "insufficient_item"))
        self.assertEqual(self._state().get("randomBoxReceipts", {}), {})


if __name__ == "__main__":
    unittest.main()
