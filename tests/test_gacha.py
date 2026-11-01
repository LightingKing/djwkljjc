"""Gacha pools and draws follow the client GachaType/GachaActorPool tables."""

from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
import actors as actor_store  # noqa: E402
from game import LocalGame  # noqa: E402
from local_mailbox import send_mail  # noqa: E402
from server.core.activity_schedule import ActivitySchedule  # noqa: E402
from systems import GACHA_POOL, LEAFA_ACTOR_ID  # noqa: E402
import test_systems  # noqa: E402


class FixedRollRandom(random.Random):
    """Control probability rolls while retaining real choice/sample methods."""

    def __init__(self, fraction=0.5):
        super().__init__(27)
        self.fraction = fraction

    def randrange(self, start, stop=None, step=1):
        if stop is None:
            start, stop = 0, start
        return start + int(len(range(start, stop, step)) * self.fraction) * step


class ActorRollRandom(FixedRollRandom):
    def __init__(self, actor_id):
        super().__init__(0.999)
        self.actor_id = actor_id

    def choices(self, population, weights=None, *, cum_weights=None, k=1):
        if self.actor_id in population:
            return [self.actor_id] * k
        return super().choices(population, weights, cum_weights=cum_weights, k=k)


class GachaTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.now = 1793577600  # 2026-11-02 08:00 Beijing; no wall-clock dependency.
        self.accounts = LocalAccounts(self.root / "accounts", clock=lambda: self.now)
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now)
        _, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.profile = self.game._profile(self.accounts.verify_access_token(self.auth.split()[1]))

    def tearDown(self):
        self._temp.cleanup()

    def call(self, method, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        return self.game.handle_http(method, path, body, "application/json", self.auth)

    def test_pool_list_is_plain_and_free_draw_is_available(self):
        status, pools = self.call("GET", "/fci/gachainfo/")
        self.assertEqual(status, 200)
        self.assertIsInstance(pools, list)
        self.assertEqual([pool["id"] for pool in pools], [1, 2, 5, 6])
        normal = pools[0]
        # IsFreeGacha requires 0 < nextFreeGachaTime <= now.
        self.assertGreater(normal["nextFreeGachaTime"], 0)
        self.assertLessEqual(normal["nextFreeGachaTime"], self.game.systems._now())
        self.assertEqual(normal["nextGetPurpleCount"], 10)

    def _give_tickets(self, count, item_id=60001):
        mail = send_mail(self.game, self.profile, "Isolated gacha test", "Draw tickets",
                         [{"type": 11, "id": item_id, "count": count}])
        status, result = self.call("PATCH", f"/fci/mail/{mail['id']}", {"attachmentsGot": True})
        self.assertEqual(status, 200, result)

    def draw(self, count, pool_id=1, activity_id=0):
        # Native Window_Gacha.DoGacha uses this protobuf and route.  No
        # fabricated costs, actors or pre-seeded pity counters.
        request = self.game.systems._message("ApiGachaTrade")
        request.id, request.count, request.activity_id = pool_id, count, activity_id
        return self.game.handle_http("POST", "/fci/gachatrade/", request.SerializeToString(),
                                     "application/wireformat", self.auth)

    def assert_delivery_matches(self, result):
        delivered = [change.actor for change in result.info.res_change if change.HasField("actor")]
        self.assertEqual([(x.id, x.quality) for x in result.actor_record], [(x.id, x.quality) for x in delivered])
        state = self.game.systems._state(self.profile)
        history = state["gacha_records"][-len(delivered):]
        self.assertEqual([(x["id"], x["quality"]) for x in history], [(x.id, x.quality) for x in delivered])
        for actor in delivered:
            saved = actor_store.get(self.profile, state, actor.uid)
            self.assertEqual(saved["quality"], actor.quality)
            self.assertEqual(len(saved["talents"]), actor.quality)
        return delivered

    def test_first_ten_pull_guarantees_leafa_and_delivers_actors(self):
        self.assertEqual(len(GACHA_POOL), 56)
        # The tutorial's 10 tickets (stage 2061 first clear) pay the first ten-pull.
        self._give_tickets(10)
        status, result = self.call("POST", "/fci/gachatrade/", {"id": 1, "count": 10})
        self.assertEqual(status, 200)
        self.assertEqual(result.info.id, 1)
        drawn = [record.id for record in result.actor_record]
        self.assertEqual(len(drawn), 10)
        self.assertEqual(len(set(drawn)), 10)
        self.assertIn(LEAFA_ACTOR_ID, drawn)
        delivered = [change.actor for change in result.info.res_change if change.HasField("actor")]
        self.assertEqual(sorted(actor.id for actor in delivered), sorted(drawn))
        self.assertEqual(len({actor.uid for actor in delivered}), 10)
        self.assertNotIn(1, {actor.uid for actor in delivered})  # uid 1 is the starter
        # The new actors reach the login bootstrap.
        actor_ids = self.game.local_actor_ids(self.game._profile(self.accounts.verify_access_token(self.auth.split()[1])))
        for actor_id in drawn:
            self.assertIn(actor_id, actor_ids)

    def test_daily_free_draw_then_costs_are_enforced(self):
        # The daily free draw is one character, never a free ten-pull.
        status, result = self.call("POST", "/fci/gachatrade/", {"id": 1, "gachaFree": True})
        self.assertEqual(status, 200)
        self.assertEqual(len(result.actor_record), 1)
        self.assertGreater(result.next_free_gacha_time, self.game.systems._now())
        status, again = self.call("POST", "/fci/gachatrade/", {"id": 1, "gachaFree": True})
        self.assertEqual((status, again["error"]), (409, "gacha_not_free"))
        # No tickets and no 迷子: a paid draw is refused.
        status, paid = self.call("POST", "/fci/gachatrade/", {"id": 1, "cost": [{"type": 5, "id": 0, "count": 150}]})
        self.assertEqual(status, 409)

    def test_quality_rolls_and_delivery_are_consistent(self):
        self._give_tickets(4)
        # ActorStarRandom way 5 boundaries: 7920 / 1500 / 500 / 80.
        for fraction, expected in ((0.5, 1), (0.85, 2), (0.96, 3), (0.999, 4)):
            with self.subTest(quality=expected):
                self.game.systems._rng = FixedRollRandom(fraction)
                status, result = self.draw(1)
                self.assertEqual(status, 200, result)
                self.assertEqual(result.actor_record[0].quality, expected)
                self.assert_delivery_matches(result)

    def test_currency_and_tickets_roll_back_if_actor_delivery_fails(self):
        mail = send_mail(self.game, self.profile, "Isolated gacha rollback", "Draw currency",
                         [{"type": 5, "count": 150}])
        self.assertEqual(self.call("PATCH", f"/fci/mail/{mail['id']}", {"attachmentsGot": True})[0], 200)
        self._give_tickets(9)
        self.assertEqual(self.call("GET", "/fci/gachainfo/")[0], 200)

        def snapshot():
            with closing(self.game._connect()) as conn:
                return tuple(conn.iterdump())

        before = snapshot()
        for failure_point in ("_save", "_st_actor"):
            with self.subTest(failure=failure_point):
                with patch.object(self.game.systems, failure_point, side_effect=RuntimeError("delivery failed")):
                    with self.assertRaisesRegex(RuntimeError, "delivery failed"):
                        self.draw(10)
                self.assertEqual(snapshot(), before)
        status, result = self.draw(10)
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result.actor_record), 10)
        self.assert_delivery_matches(result)

    def test_actor_with_missing_quality_row_uses_pool_weights(self):
        self._give_tickets(2)
        for actor_id in (1002, 2113):
            self.game.systems._rng = ActorRollRandom(actor_id)
            status, result = self.draw(1)
            self.assertEqual(status, 200, result)
            self.assertEqual((result.actor_record[0].id, result.actor_record[0].quality), (actor_id, 4))
            self.assert_delivery_matches(result)

    def test_purple_and_orange_pity_survive_restart_and_match_countdown(self):
        self._give_tickets(118)
        self.game.systems._rng = FixedRollRandom()
        for _ in range(9):
            status, result = self.draw(1)
            self.assertEqual(status, 200, result)
            self.assertEqual(result.actor_record[0].quality, 1)
        self.assertEqual(result.next_get_purple_count, 1)
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now)
        self.game.systems._rng = FixedRollRandom()
        self.assertEqual(self.call("GET", "/fci/gachainfo/")[1][0]["nextGetPurpleCount"], 1)
        status, result = self.draw(1)
        self.assertEqual(status, 200, result)
        self.assertEqual(result.actor_record[0].quality, 3)
        self.assertEqual(result.next_get_purple_count, 10)
        self.assertEqual(result.activity_info.next_get_purple_count, 10)
        self.assert_delivery_matches(result)
        for _ in range(10):
            status, result = self.draw(10)
            self.assertEqual(status, 200, result)
            self.assertEqual([x.quality for x in result.actor_record].count(3), 1)
        for _ in range(8):
            status, result = self.draw(1)
            self.assertEqual(status, 200, result)
        self.assertEqual(result.actor_record[-1].quality, 4)
        self.assertEqual(result.next_get_purple_count, 10)
        self.assert_delivery_matches(result)

    def test_all_original_limited_activities_open_and_independent(self):
        status, activities = self.call("GET", "/fci/activity/")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(x["activityId"] for x in activities if x["type"] == 4), [4001, 4002, 4003])
        self._give_tickets(273, 60002)
        self.game.systems._rng = FixedRollRandom()
        for activity_id, actor_id in ((4001, 1401), (4002, 1502), (4003, 1514)):
            with self.subTest(activity=activity_id):
                for _ in range(9):
                    status, result = self.draw(10, 2, activity_id)
                    self.assertEqual(status, 200, result)
                    self.assertNotIn(actor_id, [x.id for x in result.actor_record])
                self.assertEqual(result.activity_info.cur_drop_activity_actor, 90)
                self.assertEqual(result.activity_info.get_activity_actor_count, 0)
                status, result = self.draw(1, 2, activity_id)
                self.assertEqual(status, 200, result)
                self.assertEqual((result.actor_record[0].id, result.actor_record[0].quality), (actor_id, 3))
                self.assertEqual(result.activity_info.activity_id, activity_id)
                self.assertEqual(result.activity_info.cur_drop_activity_actor, 91)
                self.assertEqual(result.activity_info.pre_drop_activity_actor, 90)
                self.assertEqual(result.activity_info.get_activity_actor_count, 1)
                self.assert_delivery_matches(result)
        self.game = LocalGame(self.root / "game", self.accounts, clock=lambda: self.now)
        info = self.call("GET", "/fci/gachainfo/")[1][1]["activityInfo"]
        self.assertEqual([x["activityId"] for x in info], [4001, 4002, 4003])
        self.assertTrue(all(x["getActivityActorCount"] == 1 and x["curDropActivityActor"] == 91 for x in info))

    def test_invalid_or_closed_limited_activity_does_not_spend(self):
        self._give_tickets(5, 60002)
        for activity in (0, 4999):
            status, result = self.draw(1, 2, activity)
            self.assertEqual((status, result["error"]), (409, "activity_not_open"))
        source = self.root / "closed.yaml"
        source.write_text("cycle:\n  phases: []\n", encoding="utf-8")
        self.game.activity_schedule = ActivitySchedule(source, clock=lambda: self.now)
        self.assertEqual(self.call("GET", "/fci/gachainfo/")[1][1]["activityInfo"], [])
        status, result = self.draw(1, 2, 4001)
        self.assertEqual((status, result["error"]), (409, "activity_not_open"))
        state = self.game.systems._state(self.profile)
        self.assertEqual(state["items"]["60002"]["count"], 5)
        self.assertEqual(state["gacha_records"], [])

    def test_early_limited_copy_finishes_once_only_floor(self):
        self._give_tickets(2, 60002)
        self.game.systems._rng = FixedRollRandom(0.005)
        for obtained in (1, 2):
            status, result = self.draw(1, 2, 4001)
            self.assertEqual(status, 200, result)
            self.assertEqual((result.actor_record[0].id, result.actor_record[0].quality), (1401, 3))
            self.assertEqual(result.activity_info.cur_drop_activity_actor, 91)
            self.assertEqual(result.activity_info.get_activity_actor_count, obtained)
            self.assert_delivery_matches(result)

    def test_veteran_first_ten_still_delivers_ten(self):
        mail = send_mail(self.game, self.profile, "Isolated actor fixture", "Original pool actors",
                         [{"type": 15, "id": actor, "param": 1001, "count": 1} for actor, _ in GACHA_POOL])
        self.assertEqual(self.call("PATCH", f"/fci/mail/{mail['id']}", {"attachmentsGot": True})[0], 200)
        self._give_tickets(10)
        status, result = self.draw(10)
        self.assertEqual(status, 200, result)
        self.assertEqual(len(result.actor_record), 10)
        self.assertIn(LEAFA_ACTOR_ID, [x.id for x in result.actor_record])
        self.assert_delivery_matches(result)


if __name__ == "__main__":
    unittest.main()
