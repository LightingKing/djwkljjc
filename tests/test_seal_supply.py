"""Independent local Seal supply without raising the APK's candy drop cap."""
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
from test_welfare_maze_local import guest


class SealSupplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [int(datetime(2026, 10, 30, 4, tzinfo=timezone.utc).timestamp())]
        self.accounts = LocalAccounts(self.root / "accounts", clock=lambda: self.now[0])
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0],
                              initial_level=60, initial_resources=[{"type": 6, "count": 20000}])
        self.account, self.auth = guest(self.accounts)

    def native(self, method, path, payload=None, message=None):
        if message:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(message))
            body = json_format.ParseDict(payload, cls()).SerializeToString()
            content_type = "application/wireformat"
        else:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            content_type = "application/json"
        status, result = self.game.handle_http(method, path, body, content_type, self.auth)
        self.assertEqual(status, 200, result)
        return json_format.MessageToDict(result, always_print_fields_with_no_presence=True) if isinstance(result, Message) else result

    def profile(self):
        return self.game._profile(self.account)

    def item(self, rid):
        return self.game.systems._state(self.profile()).get("items", {}).get(str(rid), {}).get("count", 0)

    def supply_mails(self):
        return [mail for mail in self.game.systems._state(self.profile()).get("mails", [])
                if mail["title"] == "印章活动补给"]

    def claim_supply(self):
        seal = self.native("GET", "/fci/seal/")
        self.assertEqual(len(seal["cellInfo"]), 25)
        self.assertTrue(all(row["info"]["cost"]["id"] == 1016 and row["info"]["cost"]["count"] == 1
                            for row in seal["cellInfo"]))
        mails = self.supply_mails()
        self.assertEqual(len(mails), 1)
        before = self.item(1016)
        self.native("PATCH", f'/fci/mail/{mails[0]["id"]}', {"attachmentsGot": True})
        self.assertEqual(self.item(1016), before + 25)
        self.native("PATCH", f'/fci/mail/{mails[0]["id"]}', {"attachmentsGot": True})
        self.assertEqual(self.item(1016), before + 25)

    def earn_battle_candy(self):
        for _ in range(66):
            entry = self.native("POST", "/fci/activity-dungeon/",
                                {"dungeonId": 52201, "activityId": 2002, "activityType": 2,
                                 "selectActorGroupId": 1, "multiPower": 3}, "ApiActivityDungeon")
            uid = entry["dungeonUid"]
            active = self.game._types["FightActiveData"]()
            active.dungeonType, active.stageID, active.winTeam, active.gameRound = entry["baseInfo"]["dungeonType"], 52201, 1, 1
            status, result = self.game.handle_http("PATCH", f"/fci/dungeon/{uid}/active/",
                                                  active.SerializeToString(), "application/wireformat", self.auth)
            self.assertEqual(status, 200, result)
            self.native("POST", f"/fci/activity-dungeon/{uid}/balance/", b"{}")
        earned = self.game.welfare._state(self.profile())["welfare"]["earnedMaterials"]
        self.assertEqual(earned["1016"], 750)
        before = self.item(1016)
        self.native("POST", f"/fci/activity-dungeon/{uid}/balance/", b"{}")
        self.assertEqual(self.item(1016), before)

    def fill_seal(self):
        for cell in range(1, 26):
            self.native("POST", f"/fci/seal/fill/{cell}/", b"null")
        self.native("POST", "/fci/seal/reward/12/", b"null")
        self.assertEqual(self.native("POST", "/fci/seal/reward/12/", b"null")["resChange"], [])

    def fill_jigsaw(self):
        for cell in range(1, 31):
            self.native("POST", "/fci/JigsawPuzzle/Lighten/", {"cellId": cell}, "ApiJigsawPuzzleLighten")
        self.assertEqual(self.item(31014), 1)
        for threshold in (5, 15, 30):
            self.native("POST", "/fci/JigsawPuzzle/ReceivePrize/", {"lightNum": threshold}, "ApiJigsawPuzzleReceive")

    def test_seal_then_jigsaw_finish_from_mail_and_capped_battles(self):
        self.claim_supply()
        self.fill_seal()
        self.assertEqual(self.item(1016), 0)
        self.earn_battle_candy()
        self.assertEqual(self.item(1016), 750)
        self.fill_jigsaw()
        self.assertEqual(self.item(1016), 0)

    def test_jigsaw_then_seal_finish_from_capped_battles_and_mail(self):
        self.earn_battle_candy()
        self.fill_jigsaw()
        self.assertEqual(self.item(1016), 0)
        self.claim_supply()
        self.fill_seal()
        self.assertEqual(self.item(1016), 0)
        self.assertEqual(self.game.welfare._state(self.profile())["welfare"]["earnedMaterials"]["1016"], 750)

    def test_supply_once_per_cycle_across_restart_and_clock_rollback(self):
        self.native("GET", "/fci/seal/")
        first = self.supply_mails()[0]["id"]
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now[0])
        self.native("GET", "/fci/seal/")
        self.assertEqual([mail["id"] for mail in self.supply_mails()], [first])
        self.now[0] = self.game.welfare.cycle_window()["end"]
        self.native("GET", "/fci/seal/")
        second = self.supply_mails()[-1]["id"]
        self.assertGreater(second, first)
        self.now[0] -= 86400
        self.native("GET", "/fci/seal/")
        self.assertEqual([mail["id"] for mail in self.supply_mails()], [second])
        self.assertEqual(self.item(1016), 0)  # mail attachment requires an explicit claim

    def test_supply_mail_expires_at_cycle_end_before_next_cycle_claim(self):
        cycle_end = self.game.welfare.cycle_window()["end"]
        self.now[0] = cycle_end - 3600
        self.native("GET", "/fci/seal/")
        old = self.supply_mails()[0]
        self.assertEqual(old["expireTime"], cycle_end)
        self.now[0] = cycle_end + 1
        result = self.native("PATCH", f'/fci/mail/{old["id"]}', {"attachmentsGot": True})
        self.assertEqual(result["resChange"], [])
        self.assertEqual(self.item(1016), 0)
        self.native("GET", "/fci/seal/")
        current = self.supply_mails()
        self.assertEqual(len(current), 1)
        self.assertGreater(current[0]["id"], old["id"])

    def test_failed_supply_marker_rolls_back_mail_and_push(self):
        notified = []
        self.game.mailbox.notifier = lambda profile, payload: notified.append(payload)
        save = self.game.welfare._save

        def fail_marker(profile, state):
            if state["welfare"]["seal"].get("supplyMailId"):
                raise RuntimeError("seal supply save failed")
            return save(profile, state)

        with patch.object(self.game.welfare, "_save", side_effect=fail_marker):
            with self.assertRaisesRegex(RuntimeError, "seal supply save failed"):
                self.native("GET", "/fci/seal/")
        self.assertEqual(self.supply_mails(), [])
        self.assertEqual(notified, [])
        self.native("GET", "/fci/seal/")
        self.assertEqual(len(self.supply_mails()), 1)
        self.assertEqual(len(notified), 1)


if __name__ == "__main__":
    unittest.main()
