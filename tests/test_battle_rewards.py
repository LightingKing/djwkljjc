"""Won stages grant character EXP and the client tables' stage drops."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
from dungeon import (  # noqa: E402
    ACTOR_LEVELS,
    DUNGEON_CONFIG,
    REPEAT_DROP_CHANCE,
    STAGE_MATERIAL_POOL,
    _roll_actor_level,
)
from game import LocalGame  # noqa: E402
import test_dungeon  # noqa: E402


class _FixedRandom:
    """random.Random stand-in: random() always returns ``value``."""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def choice(self, values):
        return values[0]

    def randrange(self, *args):
        return 0


def _gains(balance):
    gains = {}
    for change in balance["resChange"]:
        if "baseRes" in change:
            key = (change["baseRes"]["type"], change["baseRes"]["id"])
            gains[key] = gains.get(key, 0) + change["baseRes"]["countDelta"]
    return gains


class BattleRewardTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts)
        self.account = test_dungeon.DungeonTests._guest_account(self.accounts)
        with closing(self.accounts._connect()) as connection:
            token = connection.execute(
                "SELECT access_token FROM accounts WHERE account_id = ?", (self.account["accountId"],)
            ).fetchone()[0]
        self.auth = "Token " + token
        self.game._profile(self.account)  # create the profile row
        # Deterministic drops: every displayed repeat entry drops, the
        # equipment roll picks the pool's first item.
        self.game.dungeons._drop_rng = _FixedRandom(0.0)
        self.game.dungeons._equip_rng = _FixedRandom(0.0)

    def tearDown(self):
        self._temp.cleanup()

    def _refill(self, energy=120):
        with closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT resources_json FROM game_profiles WHERE account_id = ?", (self.account["accountId"],)
            ).fetchone()
            resources = [item for item in json.loads(row[0]) if int(item.get("type", 0)) != 6]
            resources.append({"type": 6, "id": 0, "param": 0, "count": energy})
            connection.execute(
                "UPDATE game_profiles SET resources_json = ? WHERE account_id = ?",
                (json.dumps(resources), self.account["accountId"]),
            )

    def _win(self, dungeon_id, **entry):
        self._refill()
        request = {"dungeonId": dungeon_id, **entry}
        status, payload = self.game.handle_http(
            "POST", "/fci/dungeon/", json.dumps(request).encode(), "application/json", self.auth
        )
        self.assertEqual(status, 200, payload)
        uid = payload["dungeonUid"]
        active = self.game._types["FightActiveData"]()
        active.dungeonType = 1
        active.stageID = dungeon_id
        active.gameRound = 1
        active.winTeam = 1
        self.game.handle_http("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(),
                              "application/wireformat", self.auth)
        status, result = self.game.handle_http("POST", f"/fci/dungeon/{uid}/balance", b"{}",
                                               "application/json", self.auth)
        self.assertEqual(status, 200, result)
        return result["balanceInfo"]

    def _items(self):
        profile = self.game._profile(self.account)
        return self.game.systems._state(profile).get("items", {})

    def test_actor_level_table(self):
        self.assertEqual([ACTOR_LEVELS[lv] for lv in (2, 3, 4)], [60, 90, 120])
        # 130 EXP from level 1: 60 to Lv2, 70 left (< 90).
        self.assertEqual(_roll_actor_level(1, 130), (2, 70))
        # ItemInfo.sources 207 (ChapterNormal): the character EXP materials.
        self.assertEqual(STAGE_MATERIAL_POOL, (2001, 2002, 2003, 2004, 2006))
        self.assertEqual(REPEAT_DROP_CHANCE, 0.5)

    def test_story_win_grants_actor_exp_and_coins(self):
        balance = self._win(2012)
        # DungeonReward[2012].expActor == 130 for the starter actor (uid 1, slot 0).
        self.assertEqual(balance["expChange"], [
            {"actorUid": 1, "level": 1, "exp": 0, "levelNew": 2, "expNew": 70, "addExp": 130}
        ])
        gains = _gains(balance)
        # First clear: exactly dropDisplayFirst (1000 coins; player EXP 1 is
        # the energy-based EXP entry).  2012 has no item drop group.
        self.assertEqual(gains[(7, 0)], 1000)
        self.assertEqual(gains[(1, 0)], 1)
        self.assertFalse(any(key[0] == 11 for key in gains))

        # Repeat clear: the per-run group Dungeon_2012_7 = a tenth of 1000.
        balance = self._win(2012)
        self.assertEqual(_gains(balance)[(7, 0)], 100)
        # The player is still Lv2, and characters are capped at the
        # player's level: the bar fills to just below Lv3 (90 - 1).
        self.assertEqual(balance["expChange"][0]["level"], 2)
        self.assertEqual(balance["expChange"][0]["levelNew"], 2)
        self.assertEqual(balance["expChange"][0]["expNew"], 89)
        # Progress reaches the login bootstrap (actor instance level).
        import actors as actor_store
        profile = self.game._profile(self.account)
        state = self.game.systems._state(profile)
        self.assertEqual(actor_store.get(profile, state, 1)["level"], 2)

    def test_first_clear_grants_every_drop_display_first_entry(self):
        # Stage 8: 1270 coins + star-up material 30001 (+ 6 player EXP), its
        # undisplayed Dungeon_8_11_1 group pays one EXP material, and the
        # chapter equipment group always drops on a first clear.
        balance = self._win(8)
        gains = _gains(balance)
        self.assertEqual(gains[(7, 0)], 1270)
        self.assertEqual(gains[(11, 30001)], 1)
        self.assertEqual(gains[(11, STAGE_MATERIAL_POOL[(8 - 1) % len(STAGE_MATERIAL_POOL)])], 1)
        self.assertEqual(gains[(1, 0)], 6)
        self.assertEqual(len([change for change in balance["resChange"] if "equip" in change]), 1)
        self.assertTrue(balance["firstFinish"])

    def test_first_clear_tickets_chips_and_money(self):
        # Story 2061: ten 60001 tickets.
        self.assertEqual(_gains(self._win(2061))[(11, 60001)], 10)
        # Side stage 5102: chip 20007 + 2x 30007, plus its two per-run coin
        # groups Dungeon_31_7 (2 x 1690 // 10) listed in dropIdFirst.
        gains = _gains(self._win(5102))
        self.assertEqual((gains[(11, 20007)], gains[(11, 30007)], gains[(7, 0)]), (1, 2, 338))
        # Training 5001 and challenge 11011: 30 ResPlayerMoney on first clear.
        self.assertEqual(_gains(self._win(5001))[(5, 0)], 30)
        self.assertEqual(_gains(self._win(11011))[(5, 0)], 30)
        items = self._items()
        self.assertEqual(items["60001"]["count"], 10)
        self.assertEqual(items["20007"]["count"], 1)

    def test_repeat_clear_rolls_the_displayed_drops(self):
        self._win(8)
        before = self._items().get("30001", {}).get("count", 0)
        balance = self._win(8)
        self.assertFalse(balance["firstFinish"])
        gains = _gains(balance)
        # Dungeon_8_7: 1270 // 10 coins; 30001 from dropDisplay (chance hit).
        self.assertEqual(gains[(7, 0)], 127)
        self.assertEqual(gains[(11, 30001)], 1)
        self.assertEqual(self._items()["30001"]["count"], before + 1)
        # A miss drops nothing displayed; coins and the EXP material stay.
        self.game.dungeons._drop_rng = _FixedRandom(0.99)
        gains = _gains(self._win(8))
        self.assertNotIn((11, 30001), gains)
        self.assertEqual(gains[(7, 0)], 127)
        # Training/challenge stages have no repeat drop groups (dropId []).
        self._win(5001)
        self.assertEqual({key: value for key, value in _gains(self._win(5001)).items() if value}, {})

    def test_multi_power_scales_cost_exp_and_drops(self):
        # Clear stage 2 with all three targets (no statistics -> all done).
        self._win(2)
        recorded = []
        original_record = self.game.tasks.record
        self.game.tasks.record = lambda profile, goal, amount=1, **kw: recorded.append((goal, amount)) or original_record(profile, goal, amount, **kw)
        self._refill(120)
        status, entered = self.game.handle_http(
            "POST", "/fci/dungeon/", json.dumps({"dungeonId": 2, "multiPower": 3}).encode(),
            "application/json", self.auth,
        )
        self.assertEqual(status, 200)
        # Entry pays cost x 3.
        self.assertEqual([c["baseRes"]["countDelta"] for c in entered["resChange"]], [-3])
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](stageID=2, gameRound=1, winTeam=1)
        self.game.handle_http("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(),
                              "application/wireformat", self.auth)
        status, result = self.game.handle_http("POST", f"/fci/dungeon/{uid}/balance", b"{}",
                                               "application/json", self.auth)
        self.assertEqual(status, 200)
        balance = result["balanceInfo"]
        changes = [c["baseRes"] for c in balance["resChange"] if "baseRes" in c]
        # costWin 5 x 3 paid on the win; player EXP = energy spent (6 x 3).
        self.assertIn(-15, [c["countDelta"] for c in changes if c["type"] == 6])
        self.assertEqual(next(c["countDelta"] for c in changes if c["type"] == 1), 18)
        row = DUNGEON_CONFIG.reward_row(2)
        self.assertEqual(balance["expChange"][0]["addExp"], row["expActor"] * 3)
        self.assertEqual(_gains(balance)[(7, 0)], (1040 // 10) * 3)
        self.assertEqual(result["multiPower"], 3)
        self.assertIn(("MultiPowerMTimes", 1), recorded)

    def test_multi_power_needs_the_stage_stars(self):
        self._refill(120)
        # Not cleared yet: multiPower falls back to 1.
        status, entered = self.game.handle_http(
            "POST", "/fci/dungeon/", json.dumps({"dungeonId": 3, "multiPower": 3}).encode(),
            "application/json", self.auth,
        )
        self.assertEqual([c["baseRes"]["countDelta"] for c in entered["resChange"]], [-1])

    def test_every_star_material_source_stage_can_drop_it(self):
        """ItemInfo.sources -> Goto DungeonStory(<stage>) all display the material.

        Goto 1000+n opens stage n for n < 1000 (the 51xx side stages use
        Goto 6xxx); the Goto row's param is the stage id either way.
        """

        items = {row["id"]: row for row in json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "client-tables" / "ItemInfo.json").read_text(encoding="utf-8")
        )["rows"] if row.get("id")}
        goto = {row.get("id"): row for row in json.loads(
            (Path(__file__).resolve().parents[1] / "data" / "client-tables" / "Goto.json").read_text(encoding="utf-8")
        )["rows"] if row.get("id")}
        checked = 0
        for item_id in range(30001, 30025):
            for source in (items.get(item_id) or {}).get("sources") or []:
                target = goto.get(source)
                if not target or target["windowName"] != "DungeonStory":
                    continue
                stage = int(float(target["params"][0]))
                if stage < 1000:
                    self.assertEqual(source, 1000 + stage)
                row = DUNGEON_CONFIG.reward_row(stage)
                displayed = {(e.get("type"), e.get("id")) for e in (row.get("dropDisplay") or []) + (row.get("dropDisplayOutside") or [])}
                self.assertIn((11, item_id), displayed, (item_id, stage))
                self.assertTrue(row.get("dropId"), stage)
                checked += 1
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main()
