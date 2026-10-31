"""C8 native Lua routes, reward delivery and shared event-cycle boundaries."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.protobuf import json_format, message_factory
from google.protobuf.message import Message

from accounts import LocalAccounts
from game import LocalGame
from grants import grant_rewards
from test_welfare_maze_local import guest
from welfare_features import CHIP_ORDER


class WelfarePeriodsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.now = [int(datetime(2026, 10, 30, 4, tzinfo=timezone.utc).timestamp())]
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts, clock=lambda: self.now[0],
            initial_level=60, initial_resources=[{"type": 6, "id": 0, "count": 20000}])
        self.account, self.auth = guest(self.accounts)
        self.profile = self.game._profile(self.account)

    def native(self, method, path, body=None, message=None, content_type="application/json"):
        if message:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(message))
            raw = json_format.ParseDict(body or {}, cls()).SerializeToString()
            content_type = "application/wireformat"
        else:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        status, result = self.game.handle_http(method, path, raw, content_type, self.auth)
        if isinstance(result, Message):
            result = json_format.MessageToDict(result, always_print_fields_with_no_presence=True)
        return status, result

    def item(self, rid):
        return self.game.systems._state(self.profile).get("items", {}).get(str(rid), {}).get("count", 0)

    def give(self, rid, count):
        grant_rewards(self.game, self.profile, [{"type": 11, "id": rid, "count": count}])

    def battle(self, did, *, material=False, won=True, multi=1):
        if material:
            path, kind, payload = "/fci/activity-dungeon/", "ApiActivityDungeon", {
                "dungeonId": did, "activityId": 2002, "activityType": 2,
                "selectActorGroupId": 1, "multiPower": multi}
        else:
            path, kind, payload = "/fci/dungeon/", "ApiDungeon", {"dungeonId": did, "selectActorGroupId": 1, "multiPower": multi}
        status, entered = self.native("POST", path, payload, kind)
        self.assertEqual(status, 200, entered)
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"]()
        active.dungeonType = entered["baseInfo"]["dungeonType"]
        active.stageID = did
        active.winTeam = 1 if won else 2
        active.gameRound = 1
        status, result = self.native("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(), content_type="application/wireformat")
        self.assertEqual(status, 200, result)
        balance_path = f"{path}{uid}/balance/"
        status, result = self.native("POST", balance_path, b"{}")
        self.assertEqual(status, 200, result)
        return balance_path, result

    def test_seal_native_reward_indices_match_rows_columns_diagonal_and_full_board(self):
        self.give(1016, 25)
        status, seal = self.native("GET", "/fci/seal/")
        self.assertEqual(status, 200)
        self.assertEqual([(row["info"]["rewardKind"], row["info"]["rewardIndex"]) for row in seal["rewardInfo"]],
                         [(1, i) for i in range(1, 6)] + [(2, i) for i in range(1, 6)] + [(3, 1), (4, 1)])
        self.assertEqual(self.native("POST", "/fci/seal/reward/1/", b"null")[0], 409)
        for cell in (1, 6, 11, 16, 21):
            self.assertEqual(self.native("POST", f"/fci/seal/fill/{cell}/", b"null")[0], 200)
        self.assertEqual(self.native("POST", "/fci/seal/reward/6/", b"null")[0], 200)
        self.assertEqual(self.native("POST", "/fci/seal/reward/1/", b"null")[0], 409)
        for cell in range(1, 26):
            self.assertEqual(self.native("POST", f"/fci/seal/fill/{cell}/", b"null")[0], 200)
        self.assertEqual(self.item(1016), 0)
        self.assertEqual(self.native("POST", "/fci/seal/reward/12/", b"null")[0], 200)

    def test_postcard_native_path_all_four_and_zero_total_prize(self):
        self.give(2001, 72)
        self.native("POST", "/fci/Postcard/", b"{}")
        self.assertFalse(self.native("GET", "/fci/Postcard/")[1]["isFirstEnter"])
        for pid in range(1, 5):
            for cid in range(1, 19):
                status, result = self.native("POST", f"/fci/Postcard/Lighten/{pid}", {"cellId": cid}, "ApiPostcardLighten")
                self.assertEqual((status, result.get("cellId")), (200, cid))
            self.assertEqual(self.native("POST", "/fci/Postcard/ReceivePrize/", {"postcardId": pid}, "ApiPostcardReceive")[0], 200)
        self.assertEqual(self.item(2001), 0)
        status, prize = self.native("POST", "/fci/Postcard/ReceivePrize/", {"postcardId": 0}, "ApiPostcardReceive")
        self.assertEqual((status, prize["postcardId"]), (200, 0))
        self.assertTrue(all(row["prizeReceiveStatus"] for row in self.native("GET", "/fci/Postcard/")[1]["prizeInfo"]))
        self.assertEqual(self.native("POST", "/fci/Postcard/ReceivePrize/", {"postcardId": 0}, "ApiPostcardReceive")[1]["resChange"], [])

    def test_jigsaw_last_native_cell_grants_scene_once_and_failure_rolls_back(self):
        self.give(1016, 750)
        self.native("POST", "/fci/JigsawPuzzle/", b"{}")
        self.assertFalse(self.native("GET", "/fci/JigsawPuzzle/")[1]["isFirstEnter"])
        for cid in range(1, 30):
            self.assertEqual(self.native("POST", "/fci/JigsawPuzzle/Lighten/", {"cellId": cid}, "ApiJigsawPuzzleLighten")[0], 200)
        original = self.game.welfare._save
        def fail_scene(profile, state):
            if 0 in state["welfare"]["jigsaw"]["received"]:
                raise RuntimeError("scene save failed")
            return original(profile, state)
        with patch.object(self.game.welfare, "_save", side_effect=fail_scene):
            with self.assertRaisesRegex(RuntimeError, "scene save failed"):
                self.native("POST", "/fci/JigsawPuzzle/Lighten/", {"cellId": 30}, "ApiJigsawPuzzleLighten")
        self.assertEqual((self.item(1016), self.item(31014)), (25, 0))
        status, result = self.native("POST", "/fci/JigsawPuzzle/Lighten/", {"cellId": 30}, "ApiJigsawPuzzleLighten")
        self.assertEqual((status, self.item(31014), self.item(1016)), (200, 1, 0))
        self.assertTrue(any(row.get("baseRes", {}).get("id") == 31014 for row in result["resChange"]))
        self.assertIn(0, self.native("GET", "/fci/JigsawPuzzle/")[1]["receivePrize"])
        self.assertEqual(self.native("POST", "/fci/JigsawPuzzle/Lighten/", {"cellId": 30}, "ApiJigsawPuzzleLighten")[1]["resChange"], [])
        for threshold in (5, 15, 30):
            self.assertEqual(self.native("POST", "/fci/JigsawPuzzle/ReceivePrize/", {"lightNum": threshold}, "ApiJigsawPuzzleReceive")[0], 200)
        self.assertEqual(self.item(31014), 1)

    def test_chip_bonus_drops_apply_next_six_and_season_resets_same_as_activity(self):
        status, pending = self.native("POST", "/fci/chipexchange/", {"chips": CHIP_ORDER})
        self.assertEqual((status, pending["exchangePosInfo"]), (200, []))
        self.battle(5102)
        self.assertEqual((self.item(20007), self.item(CHIP_ORDER[0])), (1, 0))
        self.now[0] = self.game.reset_policy.next_boundary(self.now[0])
        self.assertEqual(self.native("GET", "/fci/chipexchange/")[1]["exchangePosInfo"], CHIP_ORDER)
        path, _ = self.battle(5110)
        self.assertEqual(self.item(CHIP_ORDER[0]), 1)
        saved = self.item(CHIP_ORDER[0])
        self.native("POST", path, b"{}")
        self.assertEqual(self.item(CHIP_ORDER[0]), saved)
        activities = self.native("GET", "/fci/activity/")[1]
        windows = [row for row in activities if row["type"] in (16, 26, 44, 48)]
        self.assertEqual(len(windows), 4)
        self.assertTrue(all(row["timestampStart"] <= self.now[0] < row["timestampEnd"] for row in windows))
        self.assertTrue(all(not row["forever"] and row["timeType"] == 3 for row in windows))
        self.now[0] = windows[0]["timestampEnd"]
        new = self.native("GET", "/fci/chipexchange/")[1]
        self.assertEqual((new["modifyCount"], new["exchangePosInfo"], new["settlePosInfo"]), (0, [], []))
        self.assertNotEqual(new["lastActivitySeason"], pending["lastActivitySeason"])
        fresh = [row for row in self.native("GET", "/fci/activity/")[1] if row["type"] == 44][0]
        self.assertEqual((fresh["timestampStart"], fresh["season"]), (self.now[0], new["lastActivitySeason"]))

    def test_material_sources_real_win_loss_retry_and_weekend_boundary(self):
        # Thursday 07:00 (before the Friday 06:00 doubling window).
        self.now[0] -= 86400
        path, _ = self.battle(1)
        self.assertEqual((self.item(1016), self.item(2001)), (1, 1))
        self.native("POST", path, b"{}")
        self.assertEqual((self.item(1016), self.item(2001)), (1, 1))
        self.battle(2, won=False)
        self.assertEqual((self.item(1016), self.item(2001)), (1, 1))
        self.now[0] = self.game.reset_policy.next_boundary(self.now[0])
        self.battle(2)
        self.assertEqual((self.item(1016), self.item(2001)), (3, 2))
        self.battle(52201, material=True)
        self.assertEqual((self.item(1016), self.item(2001)), (7, 4))

    def test_material_source_caps_survive_real_spending_and_period_resets(self):
        # Native repeated material runs reach the recorded seasonal limit.
        for _ in range(66):
            self.battle(52201, material=True, multi=3)
        self.assertEqual((self.item(1016), self.item(2001)), (775, 72))
        self.native("POST", "/fci/seal/fill/1/", b"null")
        self.battle(52201, material=True, multi=3)
        self.battle(1)  # The old generic chapter pool must not bypass 2001's cap.
        self.assertEqual((self.item(1016), self.item(2001)), (774, 72))
        end = self.game.welfare.cycle_window()["end"]
        self.now[0] = end
        self.assertFalse(self.native("GET", "/fci/seal/")[1]["cellInfo"][0]["fill"])
        self.assertTrue(self.native("GET", "/fci/Postcard/")[1]["isFirstEnter"])
        self.assertEqual(self.native("GET", "/fci/JigsawPuzzle/")[1]["lightenCell"], [])
        # The next source replenishes only headroom, keeping saved inventory.
        self.battle(1)
        self.assertEqual(self.item(1016), 775)
        self.now[0] -= 86400
        self.assertFalse(self.native("GET", "/fci/seal/")[1]["cellInfo"][0]["fill"])

    def test_chip_change_blackout_rejects_without_consuming_attempt(self):
        self.now[0] = self.game.reset_policy.next_boundary(self.now[0]) - 3600
        self.assertEqual(self.native("POST", "/fci/chipexchange/", {"chips": CHIP_ORDER})[0], 409)
        self.assertEqual(self.native("GET", "/fci/chipexchange/")[1]["modifyCount"], 0)
        self.now[0] += 3600
        self.assertEqual(self.native("POST", "/fci/chipexchange/", {"chips": CHIP_ORDER})[0], 200)
        self.now[0] = self.game.welfare.cycle_window()["end"] - 12 * 3600
        self.assertEqual(self.native("POST", "/fci/chipexchange/", {"chips": CHIP_ORDER[::-1]})[0], 409)
        self.assertEqual(self.native("GET", "/fci/chipexchange/")[1]["modifyCount"], 1)

    def test_legacy_completed_jigsaw_repairs_missing_scene_only_once(self):
        # A migration fixture recreates the released omission, not a gameplay
        # shortcut: new boards are filled through native routes above.
        state = self.game.welfare._state(self.profile)
        state["welfare"]["jigsaw"] = {"cells": list(range(1, 31)), "received": [5, 15, 30]}
        self.game.welfare._save(self.profile, state)
        self.assertEqual(self.item(31014), 0)
        self.assertIn(0, self.native("GET", "/fci/JigsawPuzzle/")[1]["receivePrize"])
        self.assertEqual(self.item(31014), 1)
        self.native("GET", "/fci/JigsawPuzzle/")
        self.assertEqual(self.item(31014), 1)


if __name__ == "__main__":
    unittest.main()
