from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts import LocalAccounts
from game import LocalGame
from local_mailbox import DEFAULT_EXPIRE_DAYS, MAX_MAILS, LocalMailbox, send_mail
import test_systems  # module import: keeps its TestCase out of this module

DAY = 86400


class LocalMailboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account, _ = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.account)
        self.now = 1_800_000_000
        self.mailbox = LocalMailbox(self.game, clock=lambda: self.now)
        # send_mail reads the mailbox clock through game.mailbox when wired.
        self.game.mailbox = self.mailbox

    def tearDown(self) -> None:
        self._temp.cleanup()

    # helpers -------------------------------------------------------------
    def call(self, method: str, path: str, body=None):
        raw = None if body is None else json.dumps(body).encode()
        return self.mailbox.handle_http(method, path, raw, self.profile)

    def listing(self) -> dict:
        status, payload = self.call("GET", "/fci/mail/")
        self.assertEqual(status, 200)
        return payload

    def coins(self) -> int:
        return next(item["count"] for item in self.game._profile(self.account)["resources"]
                    if item["type"] == 7 and item["id"] == 0)

    def state(self) -> dict:
        return self.game.systems._state(self.profile)

    # routes --------------------------------------------------------------
    def test_list_is_plain_scmails_with_future_expiry(self):
        payload = self.listing()
        self.assertEqual(set(payload), {"mails", "clearAll", "delMails"})
        self.assertIs(payload["clearAll"], False)
        self.assertEqual(payload["delMails"], [])
        mail = payload["mails"][0]
        # MailUtil.MailExpireCheck: expireTime - serverUtc > 1.
        self.assertGreater(mail["expireTime"] - self.now, 1)
        self.assertEqual(mail["attachments"], [{"baseRes": {"type": 7, "id": 0, "count": 100, "param": 0}}])
        for key, value in {"id": 1, "read": False, "attachmentsGot": False, "important": False,
                           "version": "", "senderName": "Local Sandbox", "createTime": self.now}.items():
            self.assertEqual(mail[key], value, key)
        # The upgrade of the seeded record is persisted, not re-rolled.
        self.now += 10
        self.assertEqual(self.listing()["mails"][0]["expireTime"], mail["expireTime"])
        self.assertEqual(self.state()["mails"][0]["expireTime"], mail["expireTime"])

    def test_older_stored_records_stay_readable(self):
        state = self.state()
        state["mails"] = [{"id": 4, "title": "old", "content": "c", "read": True}]
        self.game.systems._save(self.profile, state)
        mail = self.listing()["mails"][0]
        self.assertEqual((mail["id"], mail["title"], mail["attachments"], mail["attachmentsGot"]), (4, "old", [], False))
        self.assertGreater(mail["expireTime"] - self.now, 1)
        self.assertEqual(send_mail(self.game, self.profile, "t", "c")["id"], 5)

    def test_patch_read_marks_read_only(self):
        status, reply = self.call("PATCH", "/fci/mail/1", {"read": True})
        self.assertEqual(status, 200)
        self.assertEqual((reply["resChange"], reply["delMails"], reply["attachmentsGot"]), ([], [], []))
        self.assertTrue(reply["result"]["read"])
        mail = self.listing()["mails"][0]
        self.assertTrue(mail["read"])
        self.assertFalse(mail["attachmentsGot"])

    def test_patch_claim_one_grants_exactly_once(self):
        before = self.coins()
        status, reply = self.call("PATCH", "/fci/mail/1/", {"attachmentsGot": True})
        self.assertEqual(status, 200)
        self.assertEqual(reply["attachmentsGot"], [1])
        self.assertEqual(reply["delMails"], [])
        self.assertEqual(len(reply["resChange"]), 1)
        res = reply["resChange"][0]["baseRes"]
        self.assertEqual((res["type"], res["id"], res["countDelta"], res["count"]), (7, 0, 100, before + 100))
        self.assertTrue(reply["result"]["attachmentsGot"] and reply["result"]["read"])
        self.assertEqual(self.coins(), before + 100)
        status, again = self.call("PATCH", "/fci/mail/1/", {"attachmentsGot": True})
        self.assertEqual((status, again["resChange"], again["attachmentsGot"]), (200, [], [1]))
        self.assertEqual(self.coins(), before + 100)

    def test_claim_all_merges_rewards_and_items_reach_the_bag(self):
        a = send_mail(self.game, self.profile, "a", "c", [{"type": 7, "id": 0, "count": 50}])["id"]
        b = send_mail(self.game, self.profile, "b", "c", [{"baseRes": {"type": 7, "id": 0, "count": 25}},
                                                          {"type": 11, "id": 10001, "count": 2}])["id"]
        plain = send_mail(self.game, self.profile, "plain", "c")["id"]
        before = self.coins()
        status, reply = self.call("PATCH", "/fci/mail/", {"attachmentsGot": True})
        self.assertEqual(status, 200)
        self.assertNotIn("result", reply)
        self.assertEqual(sorted(reply["attachmentsGot"]), [1, a, b])
        coins = [entry["baseRes"] for entry in reply["resChange"] if entry["baseRes"]["type"] == 7]
        self.assertEqual(len(coins), 1)
        self.assertEqual(coins[0]["countDelta"], 175)
        self.assertEqual(self.coins(), before + 175)
        self.assertEqual(self.state()["items"]["10001"]["count"], 2)
        mails = {mail["id"]: mail for mail in self.listing()["mails"]}
        self.assertTrue(all(mails[i]["attachmentsGot"] for i in (1, a, b)))
        self.assertFalse(mails[plain]["read"])
        status, again = self.call("PATCH", "/fci/mail/", {"attachmentsGot": True})
        self.assertEqual((again["resChange"], again["attachmentsGot"]), ([], []))
        self.assertEqual(self.coins(), before + 175)

    def test_delete_one_and_batch(self):
        status, reply = self.call("DELETE", "/fci/mail/1")
        self.assertEqual((status, reply), (200, {"delMails": [1]}))
        self.assertEqual(self.listing()["mails"], [])
        self.assertEqual(self.call("DELETE", "/fci/mail/1")[0], 200)  # idempotent
        ids = [send_mail(self.game, self.profile, str(i), "c")["id"] for i in range(3)]
        reward = send_mail(self.game, self.profile, "r", "c", [{"type": 7, "id": 0, "count": 1}])["id"]
        status, reply = self.call("DELETE", "/fci/mail/", {"ids": ids[:2]})
        self.assertEqual(reply["delMails"], ids[:2])
        self.call("PATCH", f"/fci/mail/{ids[2]}", {"read": True})
        self.call("PATCH", f"/fci/mail/{reward}", {"read": True})
        # Without ids only finished mail goes (read, nothing left to claim).
        status, reply = self.call("DELETE", "/fci/mail/")
        self.assertEqual(reply["delMails"], [ids[2]])
        self.assertEqual([mail["id"] for mail in self.listing()["mails"]], [reward])

    def test_auto_delete_route_and_behaviour(self):
        status, reply = self.call("GET", "/fci/mail-auto-delete/")
        self.assertEqual((status, reply), (200, {"autoDelete": False, "delMails": []}))
        self.assertFalse(self.listing()["mails"][0]["read"])  # not swallowed by /fci/mail
        done = send_mail(self.game, self.profile, "done", "c")["id"]
        note = send_mail(self.game, self.profile, "note", "c")["id"]
        self.call("PATCH", f"/fci/mail/{done}", {"read": True})
        status, reply = self.call("POST", "/fci/mail-auto-delete/", {"autoDelete": True})
        self.assertEqual((status, reply), (200, {"autoDelete": True, "delMails": [done]}))
        self.assertEqual(self.call("GET", "/fci/mail-auto-delete")[1]["autoDelete"], True)
        status, reply = self.call("PATCH", f"/fci/mail/{note}", {"read": True})
        self.assertEqual(reply["delMails"], [note])
        status, reply = self.call("PATCH", "/fci/mail/1", {"attachmentsGot": True})
        self.assertEqual((reply["attachmentsGot"], reply["delMails"]), ([1], [1]))
        self.assertEqual(len(reply["resChange"]), 1)
        self.assertEqual(self.listing()["mails"], [])
        status, reply = self.call("POST", "/fci/mail-auto-delete/", {"autoDelete": False})
        self.assertEqual(reply, {"autoDelete": False, "delMails": []})
        self.assertFalse(self.state()["mail_auto_delete"])
        self.assertEqual(self.call("DELETE", "/fci/mail-auto-delete/")[0], 405)

    # delivery helper -----------------------------------------------------
    def test_send_mail_record_shape_ids_and_notifier(self):
        pushed = []
        self.mailbox.notifier = lambda profile, payload: pushed.append((profile["account_id"], payload))
        record = send_mail(self.game, self.profile, "补偿", "内容", [{"type": 7, "id": 0, "count": 5, "param": 0}],
                           expire_days=7, sender_name="系统", important=True)
        self.assertEqual(record["id"], 2)
        self.assertEqual(record["expireTime"], self.now + 7 * DAY)
        stored = next(mail for mail in self.state()["mails"] if mail["id"] == 2)
        for key in ("title", "content", "senderName", "attachments", "read", "attachmentsGot"):
            self.assertEqual(stored[key], record[key], key)
        self.assertEqual(stored["attachments"], [{"type": 7, "id": 0, "count": 5, "param": 0}])
        self.assertEqual(pushed[0][0], self.profile["account_id"])
        self.assertEqual(pushed[0][1]["mails"][0]["id"], 2)
        self.assertTrue(pushed[0][1]["mails"][0]["important"])
        listed = {mail["id"]: mail for mail in self.listing()["mails"]}
        self.assertEqual(listed[2]["senderName"], "系统")
        self.call("DELETE", "/fci/mail/2")
        self.assertEqual(send_mail(self.game, self.profile, "t", "c")["id"], 3)  # ids never reused
        self.assertEqual(send_mail(self.game, self.profile, "t", "c", expire_days=None)["expireTime"] - self.now, 3650 * DAY)
        self.assertEqual(send_mail(self.game, self.profile, "t", "c")["expireTime"] - self.now, DEFAULT_EXPIRE_DAYS * DAY)
        with self.assertRaises(ValueError):
            send_mail(self.game, self.profile, "t", "c", expire_days=0)

    def test_mailbox_is_capped_like_the_client(self):
        for i in range(MAX_MAILS + 2):
            send_mail(self.game, self.profile, str(i), "c")
        ids = [mail["id"] for mail in self.listing()["mails"]]
        self.assertEqual(len(ids), MAX_MAILS)
        self.assertEqual(ids[0], 4)  # lowest ids (1..3) trimmed first

    def test_expired_mail_is_pruned_and_cannot_be_claimed(self):
        mail_id = send_mail(self.game, self.profile, "x", "c", [{"type": 7, "id": 0, "count": 9}], expire_days=1)["id"]
        before = self.coins()
        self.now += 2 * DAY
        self.assertNotIn(mail_id, [mail["id"] for mail in self.listing()["mails"]])
        status, reply = self.call("PATCH", f"/fci/mail/{mail_id}", {"attachmentsGot": True})
        self.assertEqual((status, reply["resChange"], reply["delMails"]), (200, [], [mail_id]))
        self.assertEqual(self.coins(), before)

    def test_scheduled_mail_is_listed_but_not_claimable_early(self):
        start = self.now + DAY
        mail_id = send_mail(self.game, self.profile, "day 2", "c", [{"type": 7, "id": 0, "count": 3}],
                            expire_days=2, start_time=start)["id"]
        listed = {mail["id"]: mail for mail in self.listing()["mails"]}[mail_id]
        self.assertEqual((listed["startTime"], listed["expireTime"]), (start, start + 2 * DAY))
        before = self.coins()
        self.assertEqual(self.call("PATCH", f"/fci/mail/{mail_id}", {"attachmentsGot": True})[1]["resChange"], [])
        self.assertEqual(self.call("PATCH", "/fci/mail/", {"attachmentsGot": True})[1]["attachmentsGot"], [1])
        self.now = start
        reply = self.call("PATCH", f"/fci/mail/{mail_id}", {"attachmentsGot": True})[1]
        self.assertEqual(reply["resChange"][0]["baseRes"]["countDelta"], 3)
        self.assertEqual(self.coins(), before + 100 + 3)

    # compatibility -------------------------------------------------------
    def test_historical_post_routes_keep_typed_replies(self):
        before = self.coins()
        status, claimed = self.call("POST", "/fci/mail/obtain/", {"id": 1})
        self.assertEqual(status, 200)
        self.assertEqual(claimed.DESCRIPTOR.full_name, "ApiMail.result")
        self.assertTrue(claimed.result.attachments_got)
        self.assertEqual(claimed.res_change[0].base_res.count_delta, 100)
        self.assertEqual(len(self.call("POST", "/fci/mail/obtain/", {"id": 1})[1].res_change), 0)
        self.assertEqual(self.coins(), before + 100)
        status, detail = self.call("GET", "/fci/mail/1/")
        self.assertTrue(detail.result.attachments_got)
        self.assertEqual(self.call("POST", "/fci/mail/read/", {"id": 1})[1].DESCRIPTOR.full_name, "ApiMail.result")
        self.assertEqual(self.call("POST", "/fci/mail/read/", {"id": 99})[0], 404)
        status, deleted = self.call("POST", "/fci/mail/delete/", {"id": 1})
        self.assertEqual(list(deleted.del_mails), [1])

    def test_unrelated_routes_are_left_alone(self):
        for path in ("/fci/mailcompose/", "/fci/task/", "/fci/mails/"):
            self.assertIsNone(self.call("GET", path), path)
        self.assertEqual(self.call("POST", "/fci/mail/", None)[1].DESCRIPTOR.full_name, "ApiMail.result")


if __name__ == "__main__":
    unittest.main()
