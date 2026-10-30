from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
from game import LocalGame


def guest(accounts: LocalAccounts):
    session = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    session_token = "AccountToken " + session[1]["accountToken"]
    entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", session_token)[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", session_token)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    token = "AccountToken " + second[1]["accountToken"]
    raw = entry.encode()
    auth = b"\x0a" + bytes((len(raw),)) + raw + b"\x12\x05guest"
    result = accounts.handle("POST", "/Auth/", auth, "application/wireformat", token)
    return accounts.verify_access_token(result[1]["tokenAccess"]), "Token " + result[1]["tokenAccess"]


class LocalProfileFeaturesTests(unittest.TestCase):
    def test_player_profile_mutations_persist_and_are_typed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account, auth = guest(accounts)

            status, base = game.handle_http("GET", "/fci/baseinfo/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(base["level"], 1)

            status, picture = game.handle_http("POST", "/fci/socialpicture/", json.dumps({"socialPicture": "local://avatar", "sex": 1}), authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(picture["socialPicture"], "local://avatar")

            status, background = game.handle_http("POST", "/fci/home-back-ground/", b'{"current":3,"isRandom":true}', authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(background["result"]["current"], 3)
            self.assertTrue(background["result"]["isRandom"])

            status, signboard = game.handle_http("POST", "/fci/signboard/", b'{"signboard":101}', authorization=auth)
            self.assertEqual(status, 200)
            restarted = LocalGame(root / "game", accounts)
            status, again = restarted.handle_http("GET", "/fci/baseinfo/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(again["socialPicture"], "local://avatar")
            self.assertEqual(again["homeBackGround"]["current"], 3)

    def test_notice_flags_and_web_vote_are_local_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _account, auth = guest(accounts)
            status, read = game.handle_http("POST", "/fci/notice/read-status/", b'{"noticeId":8,"index":1}', authorization=auth)
            self.assertEqual((status, read.DESCRIPTOR.full_name), (200, "ApiPlayerNoticeReadStatus.result"))
            self.assertEqual(read.notices[0].read_status, 1)
            status, share = game.handle_http("POST", "/fci/notice/share-status/", b'{"noticeId":8,"index":1}', authorization=auth)
            self.assertEqual((status, share.DESCRIPTOR.full_name), (200, "ApiPlayerNoticeShareStatus.result"))
            status, vote = game.handle_http("GET", "/fci/webvote/", authorization=auth)
            self.assertEqual(status, 200)
            before = vote["voteTicketNum"]
            status, posted = game.handle_http("POST", "/fci/webvote/", b'{"roleId":1001}', authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(posted["voteTicketNum"], before - 1)
            status, summary = game.handle_http("GET", "/fci/webvote/summary/", authorization=auth)
            self.assertEqual(status, 200)
            role = next(item for item in summary["roleList"] if item["roleId"] == 1001)
            self.assertEqual(role["voteCount"], 1)

    def test_remaining_minigames_and_share_persist_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _account, auth = guest(accounts)
            for path, body in (
                ("/fci/jigsawpuzzle/lighten/", {"cellId": 1}),
                ("/fci/postcard/lighten/", {"cellId": 2}),
                ("/fci/clawcrane/", {"count": 1}),
                ("/fci/share/", {"actorId": 1001}),
            ):
                status, result = game.handle_http("POST", path, json.dumps(body), authorization=auth)
                self.assertEqual(status, 200, path)
                self.assertIsInstance(result, dict)
            status, repeated = game.handle_http("POST", "/fci/share/", b'{"actorId":1001}', authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(repeated["resChange"], [])
            restarted = LocalGame(root / "game", accounts)
            status, jigsaw = restarted.handle_http("GET", "/fci/jigsawpuzzle/", authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(jigsaw["lightenCell"], [1])

    def test_share_reward_resets_on_next_server_week(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = [1_700_000_000]
            accounts = LocalAccounts(root / "accounts", clock=lambda: now[0])
            game = LocalGame(root / "game", accounts, clock=lambda: now[0])
            _account, auth = guest(accounts)

            status, first = game.handle_http("POST", "/fci/share/", b'{"actorId":1001}', authorization=auth)
            self.assertEqual(status, 200)
            self.assertTrue(first["weeklyRewarded"])
            self.assertTrue(first["resChange"])

            status, repeat = game.handle_http("POST", "/fci/share/", b'{"actorId":1001}', authorization=auth)
            self.assertEqual(status, 200)
            self.assertEqual(repeat["resChange"], [])

            now[0] += 7 * 86400
            restarted = LocalGame(root / "game", accounts, clock=lambda: now[0])
            status, next_week = restarted.profile_features.handle_http(
                "POST", "/fci/share/", b'{"actorId":1002}', restarted._profile(_account)
            )
            self.assertEqual(status, 200)
            self.assertTrue(next_week["resChange"])
            self.assertEqual(next_week["actorRewardGot"], 1002)


if __name__ == "__main__":
    unittest.main()
