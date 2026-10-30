"""Gacha pools and draws follow the client GachaType/GachaActorPool tables."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
from systems import GACHA_POOL, LEAFA_ACTOR_ID  # noqa: E402
import test_systems  # noqa: E402


class GachaTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        _, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)

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

    def _give_tickets(self, count):
        profile = self.game._profile(self.accounts.verify_access_token(self.auth.split()[1]))
        state = self.game.systems._state(profile)
        state.setdefault("items", {})["60001"] = {"id": 60001, "count": count}
        self.game.systems._save(profile, state)

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


if __name__ == "__main__":
    unittest.main()
