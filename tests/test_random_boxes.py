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
from local_mailbox import send_mail
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
        # Seed the fixture through the same mail delivery/claim flow as the
        # client.  Tests must not write an inventory row behind the API.
        mail = send_mail(
            self.game,
            self.profile,
            "Random-box test fixture",
            f"Item {item_id}",
            [{"type": 11, "id": item_id, "count": count}],
        )
        status, reply = self.game.handle_http(
            "POST",
            f"/fci/mail/{mail['id']}/obtain",
            b"{}",
            "application/json",
            self.auth,
        )
        self.assertEqual(status, 200, reply)
        self.assertTrue(reply.result.attachments_got)

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
        receipt = next(iter(self._state()["randomBoxReceipts"].values()))
        self.assertNotIn("responseHex", receipt)
        self.assertNotIn("rewards", receipt)
        # A new LocalGame reads the same receipt/state from SQLite.
        reopened = LocalGame(self.game.state_dir, self.accounts)
        profile = reopened._profile(self.account)
        state = reopened.systems._state(profile)
        self.assertEqual(state["items"]["30012"]["count"], 10)
        self.assertEqual(len(state["randomBoxReceipts"]), 1)

    def test_equipment_box_uses_real_equip_pool_and_request_retry_is_idempotent(self):
        self._give(75001, 2)
        # The APK exposes eight DropInfo names for 75001.  The server-side
        # contents are absent, so the local rule keeps all names and resolves
        # each to an explicit selector instead of silently treating the box as
        # one undifferentiated quality pool.
        tables = systems.RANDOM_BOX_RULES[75001]["dropTables"]
        self.assertEqual(
            [table["dropId"] for table in tables],
            [
                "Box_75001_15",
                "Box_75001_11_1",
                "Box_75001_11_2",
                "Box_75001_11_3",
                "Box_75001_12_1",
                "Box_75001_12_2",
                "Box_75001_12_3",
                "Box_75001_12_4",
            ],
        )
        self.assertTrue(all(systems.LocalSystems._local_equipment_pool(table["pool"], 4) for table in tables))
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
        # maxUseCount limits one request, not the total account inventory:
        # another single-box request is valid after the first one.
        status, second = self._call({"id": 54001, "count": 1})
        self.assertEqual(status, 200, second)
        self.assertEqual(self._state()["items"]["54001"]["count"], 0)

    def test_activity_boxes_grant_original_shop_good_scene_and_currency(self):
        # ActivityShopGoods 54000 rows 1..6 are the source of the six boxes;
        # opening one grants the matching signboard (ResSignboard=30) and
        # 150 迷子 (ResPlayerMoney=5), not ascension materials.
        expected = {54001: 1503, 54002: 11703, 54003: 106,
                    54004: 1103, 54005: 10103, 54006: 502}
        for box_id, signboard_id in expected.items():
            self._give(box_id)
            status, reply = self._call({"id": box_id, "count": 1})
            self.assertEqual(status, 200, reply)
            changes = {(row.type, row.id): row.count_delta for row in self._bases(reply)}
            self.assertEqual(changes[(11, box_id)], -1)
            self.assertEqual(changes[(30, signboard_id)], 1)
            self.assertEqual(changes[(5, 0)], 150)
            status, signboards = self.game.handle_http(
                "GET", "/fci/signboard/", authorization=self.auth
            )
            self.assertEqual(status, 200)
            self.assertIn(signboard_id, signboards["unlockedSignboardId"])

    def test_activity_drop_type5_is_listed_and_has_client_use_entry(self):
        # ActivityDrop (ItemInfo.type=5) belongs in the material bag and must
        # be usable from the detail window after the hot-update source is
        # exported.  This guards the client-side presentation seam separately
        # from the reward assertion above.
        self._give(54001)
        self.assertEqual(systems.ITEM_INFO[54001]["type"], 5)
        status, listing = self.game.handle_http("GET", "/fci/item/", authorization=self.auth)
        self.assertEqual(status, 200)
        self.assertEqual(next(item["count"] for item in listing["items"] if item["id"] == 54001), 1)
        patch_source = Path(__file__).resolve().parents[1] / "client-patch" / "hot-update" / "sources" / "itemdetail-activitydrop.lua"
        source = patch_source.read_text(encoding="utf-8")
        self.assertIn("PB.enum.ItemType.ActivityDrop", source)
        self.assertIn('itemInfo.funcType == PB.enum.ItemFuncType.Box', source)

    def test_invalid_and_insufficient_boxes_do_not_create_receipts(self):
        self._give(85003, 1)
        status, error = self._call({"id": 85003, "count": 0})
        self.assertEqual((status, error["error"]), (400, "invalid_count"))
        status, error = self._call({"id": 85003, "count": 2})
        self.assertEqual((status, error["error"]), (409, "insufficient_item"))
        self.assertEqual(self._state().get("randomBoxReceipts", {}), {})

    def test_85006_uses_local_batch_cap_when_client_row_omits_one(self):
        self._give(85006, 11)
        status, error = self._call({"id": 85006, "count": 11})
        self.assertEqual((status, error["error"]), (400, "count_exceeds_max_use"))
        self.assertEqual(self._state()["items"]["85006"]["count"], 11)
        status, reply = self._call({"id": 85006, "count": 10})
        self.assertEqual(status, 200, reply)
        self.assertEqual(self._state()["items"]["85006"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
