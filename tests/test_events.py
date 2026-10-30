from __future__ import annotations

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from accounts import LocalAccounts
from events import LocalEvents
from game import LocalGame
from server.core.clock import ServerClock


def guest_account(accounts: LocalAccounts):
    first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    auth = "AccountToken " + first[1]["accountToken"]
    entry = accounts.handle(
        "POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", auth
    )[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", auth)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    auth2 = "AccountToken " + second[1]["accountToken"]
    value = entry.encode() if isinstance(entry, str) else entry
    request = b"\x0a" + bytes((len(value),)) + value + b"\x12\x05guest"
    result = accounts.handle("POST", "/Auth/", request, "application/wireformat", auth2)
    return accounts.verify_access_token(result[1]["tokenAccess"])


class LocalEventsTests(unittest.TestCase):
    def test_game_dispatches_activity_and_signin_routes_after_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = guest_account(accounts)
            with closing(accounts._connect()) as connection:
                token = connection.execute(
                    "SELECT access_token FROM accounts WHERE account_id = ?",
                    (account["accountId"],),
                ).fetchone()[0]
            authorization = "Token " + token

            status, activity = game.handle_http(
                "GET", "/fci/activity/", authorization=authorization
            )
            # ActivityManager iterates the stored reply as activity records.
            self.assertEqual(status, 200)
            self.assertIsInstance(activity, list)
            self.assertTrue(all("type" in row and "activityId" in row for row in activity))
            status, signin = game.handle_http(
                "GET", "/fci/signin/", authorization=authorization
            )
            self.assertEqual((status, signin.DESCRIPTOR.full_name), (200, "ApiSignin.result"))

    def test_signin_activity_and_daily_claims_persist_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = [1_700_000_000]
            accounts = LocalAccounts(root / "accounts", clock=lambda: now[0])
            game = LocalGame(root / "game", accounts, clock=lambda: now[0])
            account = guest_account(accounts)
            profile = game._profile(account)
            events = LocalEvents(game, clock=lambda: now[0])

            status, signin = events.handle_http("POST", "/fci/signin", None, profile)
            self.assertEqual(status, 200)
            self.assertEqual(signin.DESCRIPTOR.full_name, "ApiSignin.result")
            self.assertEqual(signin.days, 1)
            # Repeating on the same UTC date is idempotent.
            _, repeated = events.handle_http("POST", "/fci/signin", None, profile)
            self.assertEqual(repeated.days, 1)
            events.handle_http("POST", "/fci/signin/gift", b'{"days":1}', profile)
            events.handle_http("POST", "/fci/activity-signin/32001", None, profile)
            events.handle_http("POST", "/fci/everyday-signin", None, profile)
            resources = game._profile(account)["resources"]
            gold = next(item["count"] for item in resources if item["type"] == 7)
            self.assertGreaterEqual(gold, 20_000)

            # A fresh game/module instance sees the same state and does not
            # issue the same day's reward again.
            game2 = LocalGame(root / "game", accounts, clock=lambda: now[0])
            events2 = LocalEvents(game2, clock=lambda: now[0])
            profile2 = game2._profile(account)
            _, state = events2.handle_http("GET", "/fci/activity-signin/32001", None, profile2)
            self.assertTrue(state.signed)
            self.assertEqual(state.day, 1)
            events2.handle_http("POST", "/fci/signin", None, profile2)
            self.assertEqual(next(item["count"] for item in game2._profile(account)["resources"] if item["type"] == 7), gold)

    def test_activity_response_uses_descriptor_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = guest_account(accounts)
            events = LocalEvents(game)
            status, rows = events.handle_http("GET", "/fci/activity", None, game._profile(account))
            self.assertEqual(status, 200)
            self.assertIn(12, [row["type"] for row in rows])  # the local shop entry
            # The per-type route still returns the descriptor message.
            status, result = events.handle_http("GET", "/fci/activity?type=12", None, game._profile(account))
            self.assertEqual(result.DESCRIPTOR.full_name, "ApiActivity.result")
            self.assertEqual(result.result.activity_id, 1)
            self.assertEqual(result.result.type, 12)
            self.assertEqual(result.result.category, 4)
            self.assertTrue(result.result.forever)

            status, signin_activity = events.handle_http(
                "GET", "/fci/activity/?type=46", None, game._profile(account)
            )
            self.assertEqual(status, 200)
            self.assertEqual(signin_activity.result.type, 46)

    def test_fourteen_day_signin_simulation_uses_beijing_midnight(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # 2026-01-01 23:59 Beijing; sign-in changes at local midnight.
            now = [1767283140]
            clock = ServerClock(source=lambda: now[0])
            accounts = LocalAccounts(root / "accounts", clock=clock.now)
            game = LocalGame(root / "game", accounts, clock=clock.now)
            account = guest_account(accounts)
            profile = game._profile(account)
            events = LocalEvents(game, clock=clock.now)
            _, first = events.handle_http("POST", "/fci/signin", None, profile)
            self.assertEqual(first.days, 1)
            now[0] += 60 * 60  # cross the local midnight reset
            _, second = events.handle_http("POST", "/fci/signin", None, profile)
            self.assertEqual(second.days, 2)
            # Advance fourteen local days, claiming once per bucket; the
            # persisted counter survives a fresh LocalEvents instance.
            after = second
            for _ in range(14):
                now[0] += 86400
                events_again = LocalEvents(game, clock=clock.now)
                _, after = events_again.handle_http("POST", "/fci/signin", None, game._profile(account))
            self.assertEqual(after.days, 16)

    def test_activity_minigames_and_claims_use_typed_local_responses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = guest_account(accounts)
            profile = game._profile(account)
            cases = [
                ("GET", "/fci/gachashop/", "ApiGachaShop.get"),
                ("POST", "/fci/gachashoptrade/", "ApiGachaShopTrade.post"),
                ("GET", "/fci/deploymission/", "ApiDeployMission.get"),
                ("POST", "/fci/deploymissionreward/", "ApiDeployMissionReward.result"),
                ("GET", "/fci/conditionreward/", "ApiConditionReward.get"),
                ("GET", "/fci/achievementsummary/", "ApiAchievementSummary.get"),
                ("POST", "/fci/achievementreward/", "ApiAchievementReward.result"),
            ]
            for method, path, descriptor in cases:
                with self.subTest(path=path):
                    # Direct LocalEvents invocation avoids coupling this test
                    # to the account-token transport while still exercising
                    # the same dispatcher used by LocalGame.
                    status, result = game.events.handle_http(method, path, b'{"id":1}', profile)
                    self.assertEqual(status, 200)
                    self.assertEqual(result.DESCRIPTOR.full_name, descriptor)

    def test_gacha_count_and_activity_shop_retries_are_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = guest_account(accounts)
            profile = game._profile(account)
            before = next(item["count"] for item in profile["resources"] if item["type"] == 7)

            # Draws belong to LocalSystems (client GachaType tables, tested in
            # test_gacha.py); the events module leaves gachatrade alone and
            # never charges coins for it.
            self.assertIsNone(game.events.handle_http("POST", "/fci/gachatrade/", b'{"count":3}', profile))
            after_draw = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 7)
            self.assertEqual(after_draw, before)

            status, first = game.events.handle_http(
                "POST", "/fci/gachashoptrade/", b'{"goodId":1}', profile
            )
            self.assertEqual(status, 200)
            after_first = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 7)
            status, repeat = game.events.handle_http(
                "POST", "/fci/gachashoptrade/", b'{"goodId":1}', profile
            )
            self.assertEqual(status, 200)
            self.assertEqual(repeat.DESCRIPTOR.full_name, first.DESCRIPTOR.full_name)
            after_repeat = next(item["count"] for item in game._profile(account)["resources"] if item["type"] == 7)
            self.assertEqual(after_repeat, after_first)

    def test_subscribe_toggle_is_typed_idempotent_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = guest_account(accounts)
            profile = game._profile(account)

            status, result = game.events.handle_http(
                "POST", "/fci/subscribe", b'{"opType":1}', profile
            )
            self.assertEqual(status, 200)
            self.assertEqual(result.DESCRIPTOR.full_name, "ApiSubscribe.result")
            self.assertEqual(result.errcode, 0)
            self.assertTrue(result.is_subscribed)

            # Querying and repeating the same operation do not grant or
            # mutate any other player state.
            _, queried = game.events.handle_http(
                "POST", "/fci/subscribe", b'{"opType":0}', profile
            )
            self.assertTrue(queried.is_subscribed)
            _, repeated = game.events.handle_http(
                "POST", "/fci/subscribe", b'{"opType":1}', profile
            )
            self.assertTrue(repeated.is_subscribed)

            game2 = LocalGame(root / "game", accounts)
            _, unsubscribed = game2.events.handle_http(
                "POST", "/fci/subscribe", b'{"opType":2}', game2._profile(account)
            )
            self.assertFalse(unsubscribed.is_subscribed)
            _, persisted = game2.events.handle_http(
                "POST", "/fci/subscribe", b'{"opType":0}', game2._profile(account)
            )
            self.assertFalse(persisted.is_subscribed)

    def test_gacha_verify_and_plot_emote_are_local_and_persistent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = guest_account(accounts)
            profile = game._profile(account)

            status, verify = game.events.handle_http(
                "GET", "/fci/gachaverify/", None, profile
            )
            self.assertEqual((status, verify.DESCRIPTOR.full_name), (200, "ApiGachaVerify.result"))
            self.assertEqual(verify.total_info[1].count, 0)

            status, first = game.events.handle_http(
                "POST", "/fci/gachaplotemote/", b'{"id":5}', profile
            )
            self.assertEqual((status, first.DESCRIPTOR.full_name), (200, "ApiGachaPlotEmote.result"))
            self.assertTrue(first.is_first_plot_emote)
            status, repeat = game.events.handle_http(
                "POST", "/fci/gachaplotemote/", b'{"id":5}', profile
            )
            self.assertEqual(status, 200)
            self.assertFalse(repeat.is_first_plot_emote)

            restarted = LocalGame(root / "game", accounts)
            status, persisted = restarted.events.handle_http(
                "POST", "/fci/gachaplotemote/", b'{"id":5}', restarted._profile(account)
            )
            self.assertEqual(status, 200)
            self.assertFalse(persisted.is_first_plot_emote)

    def test_web_signin_is_persistent_idempotent_and_has_local_milestone(self):
        """Web签到 is available even when the original vote descriptor is absent."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            now = [1_700_000_000]
            accounts = LocalAccounts(root / "accounts", clock=lambda: now[0])
            game = LocalGame(root / "game", accounts, clock=lambda: now[0])
            account = guest_account(accounts)
            profile = game._profile(account)

            status, initial = game.events.handle_http("GET", "/fci/websignin", None, profile)
            self.assertEqual(status, 200)
            self.assertTrue(initial["sandbox"])
            self.assertFalse(initial["isSignIn"])
            self.assertEqual(initial["globalSignInCount"], 0)

            status, first = game.events.handle_http("POST", "/fci/websignin", None, profile)
            self.assertEqual((status, first["result"]), (200, True))
            self.assertEqual(first["rewards"], [{"type": 7, "id": 0, "count": 50}])
            # A retry in the same daily bucket must not grant a second reward.
            status, repeat = game.events.handle_http("POST", "/fci/websignin", None, profile)
            self.assertEqual((status, repeat["result"], repeat["rewards"]), (200, False, []))

            # Six more buckets reach the explicit local seven-day milestone.
            for _ in range(6):
                now[0] += 86400
                status, claimed = game.events.handle_http("POST", "/fci/websignin", None, game._profile(account))
                self.assertEqual(status, 200)
            self.assertEqual(claimed["globalSignInCount"], 7)
            self.assertEqual(claimed["rewards"], [{"type": 7, "id": 0, "count": 50}, {"type": 7, "id": 0, "count": 200}])

            restarted = LocalGame(root / "game", accounts, clock=lambda: now[0])
            persisted = restarted._profile(account)
            status, state = restarted.events.handle_http("GET", "/fci/websignin", None, persisted)
            self.assertEqual(status, 200)
            self.assertTrue(state["isSignIn"])
            self.assertEqual(state["globalSignInCount"], 7)
            self.assertTrue(state["globalTargetRewards"][0]["finished"])


if __name__ == "__main__":
    unittest.main()
