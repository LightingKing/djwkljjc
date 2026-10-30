"""Story/stage progression against the original client tables.

Chapters (ChapterInfo), stage stars (DungeonReward.target evaluated on the
FightActiveData statistics), energy (cost/costWin), chapter star boxes
(ChapterReward), the chapter star exchange and the support (助战) aura.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
from dungeon import AURA_QUALITY_INFO, CHAPTER_REWARDS, ONE_STAR_REWARD_COUNT  # noqa: E402
from game import LocalGame  # noqa: E402
import test_dungeon  # noqa: E402


class StageProgressTests(unittest.TestCase):
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
        self.profile = self.game._profile(self.account)
        self._uid = 3_000_000_000

    def tearDown(self):
        self._temp.cleanup()

    # ----------------------------------------------------------- helpers
    def call(self, method, path, body=b"", content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        return self.game.handle_http(method, path, body, content_type, self.auth)

    def _energy(self, count=None):
        with closing(self.game._connect()) as connection, connection:
            resources = json.loads(connection.execute(
                "SELECT resources_json FROM game_profiles WHERE account_id = ?", (self.account["accountId"],)
            ).fetchone()[0])
            if count is None:
                return next((int(item["count"]) for item in resources if int(item["type"]) == 6), 0)
            resources = [item for item in resources if int(item["type"]) != 6]
            resources.append({"type": 6, "id": 0, "param": 0, "count": count})
            connection.execute(
                "UPDATE game_profiles SET resources_json = ? WHERE account_id = ?",
                (json.dumps(resources), self.account["accountId"]),
            )
        return count

    def _cleared(self, *dungeon_ids, targets=None):
        """Record earlier clears directly (all targets done unless given)."""

        import dungeon

        with closing(self.game._connect()) as connection, connection:
            for dungeon_id in dungeon_ids:
                self._uid += 1
                done = dungeon._stage_target_ids(dungeon_id) if targets is None else targets
                connection.execute(
                    "INSERT INTO local_dungeons (dungeon_uid, account_id, player_id, dungeon_id, dungeon_type, "
                    "actor_uids_json, status, created_at, closed_at, difficulty, stars, targets_json) "
                    "VALUES (?, ?, ?, ?, 2, '[1,0,0,0,0,0]', 'balanced', 1, 2, 'normal', ?, ?)",
                    (self._uid, self.account["accountId"], self.profile["player_id"], dungeon_id,
                     len(done), json.dumps(done)),
                )

    def _fight(self, dungeon_id, active=None, *, entry=None, win=True):
        self._energy(120)
        status, entered = self.call("POST", "/fci/dungeon/", {"dungeonId": dungeon_id, **(entry or {})})
        self.assertEqual(status, 200, entered)
        if active is None:
            active = self.game._types["FightActiveData"](stageID=dungeon_id, gameRound=1, winTeam=1 if win else 2)
        uid = entered["dungeonUid"]
        self.assertEqual(self.call("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(),
                                   "application/wireformat"), (200, {}))
        status, result = self.call("POST", f"/fci/dungeon/{uid}/balance", b"{}")
        self.assertEqual(status, 200, result)
        return entered, result

    def _active_with_stats(self, dungeon_id, *, rounds, own_dead, enemy_seats, enemy_init=None):
        active = self.game._types["FightActiveData"](stageID=dungeon_id, winTeam=1)
        own = active.teamList.add()
        own.team = 1
        own.statistic.gameRound = rounds
        for index in range(own_dead):
            own.statistic.deadInfo.add(id=index + 1, seat=index + 1, kind=0)
        enemy = active.teamList.add()
        enemy.team = 2
        enemy.statistic.initCount = enemy_init if enemy_init is not None else len(enemy_seats)
        for index, seat in enumerate(enemy_seats):
            enemy.statistic.deadInfo.add(id=100 + index, seat=seat, kind=1)
        return active

    # ---------------------------------------------------------- chapters
    def test_new_account_chapter_view_matches_the_client_rules(self):
        status, chapter = self.call("GET", "/fci/dungeon/chapter", b"", "application/wireformat")
        self.assertEqual(status, 200)
        # Chapters without preChapterId: normal 1, story 1001, training 2001.
        self.assertEqual(list(chapter.summary.unlocked_chapter_id), [1, 1001, 2001])
        self.assertEqual(set(chapter.record.chapter_record), {1, 1001, 2001})
        # A stage record exists only once the stage is open (DU.IsDungeonUnlocked,
        # TutorialManager.IsAvaliable(2/3/12/13)).
        self.assertEqual(set(chapter.record.dungeon_record), {2011})
        self.assertEqual(list(chapter.summary.new_dungeon_id), [2011])
        status, summary = self.call("GET", "/fci/dungeon/chapter-summary")
        self.assertEqual((status, summary["unlockedChapterId"]), (200, [1, 1001, 2001]))

    def test_clearing_a_chapter_key_stage_opens_the_next_chapters(self):
        self._cleared(2011, 2012, 2013, 1, 2, 3, 4, 5, 6, 7)
        _, result = self._fight(8)
        summary = result["chapterSummaryInfo"]
        self.assertEqual(summary["unlockedChapterId"], [1, 2, 1001, 1002, 2001, 2002, 3001])
        self.assertEqual(summary["finishedChapterId"], [1, 1001])
        infos = {info["recordChapter"]["chapterId"]: info for info in result["chapterInfo"]}
        # The stage's chapter first, then every chapter that changed: story
        # 1002 (2021 opened), chapters 2/2002 (unlocked, no stage open yet)
        # and challenge 3001 (11011-11014 have no preDungeonId).
        self.assertEqual(result["chapterInfo"][0]["recordChapter"]["chapterId"], 1)
        self.assertEqual(set(infos), {1, 2, 1002, 2002, 3001})
        self.assertIn("2021", infos[1002]["recordDungeons"])
        self.assertEqual(infos[2]["recordDungeons"], {})
        self.assertEqual(set(infos[3001]["recordDungeons"]), {"11011", "11012", "11013", "11014"})
        self.assertTrue(infos[1]["recordChapter"]["finished"])
        # Login/bootstrap (game.py compact view) now has chapter 2+ pages.
        status, chapter = self.call("GET", "/fci/dungeon/chapter", b"", "application/wireformat")
        self.assertEqual(list(chapter.summary.unlocked_chapter_id), [1, 2, 1001, 1002, 2001, 2002, 3001])
        self.assertIn(2, chapter.record.chapter_record)
        self.assertEqual(chapter.summary.unlocked_challenge_dungeons_cnt, 4)
        # Training 5001 (pre 4) and 5002 (pre 6) are open.
        self.assertTrue({5001, 5002} <= set(chapter.record.dungeon_record))
        self.assertEqual(chapter.summary.unlocked_training_dungeons_cnt, 2)

    def test_repeat_win_without_changes_reports_only_its_chapter(self):
        self._cleared(2011, 2012, 2013)
        self._fight(1)
        _, result = self._fight(1)
        self.assertEqual([info["recordChapter"]["chapterId"] for info in result["chapterInfo"]], [1])

    def test_every_later_stage_kind_is_playable(self):
        # Beyond chapter 6, plot 2123, training 5006, challenge 11083.
        for dungeon_id, dungeon_type in ((261, 2), (2123, 1), (5006, 6), (11083, 11), (5912, 2)):
            entered, result = self._fight(dungeon_id)
            self.assertEqual(entered["baseInfo"]["dungeonType"], dungeon_type)
            self.assertTrue(result["balanceInfo"]["win"])

    # ------------------------------------------------------------- stars
    def test_star_targets_are_evaluated_from_the_fight_statistics(self):
        # Stage 10: Round <= 6, DeadActor <= 1, first enemy killed in seat 3.
        active = self._active_with_stats(10, rounds=4, own_dead=1, enemy_seats=[2, 3])
        _, result = self._fight(10, active)
        self.assertEqual(result["balanceInfo"]["targetsDone"], [1, 2])
        self.assertEqual(result["stars"], 2)
        # The snapshot is built after the update: no one-fight lag.
        chapter2 = next(info for info in result["chapterInfo"] if info["recordChapter"]["chapterId"] == 2)
        self.assertEqual(chapter2["recordChapter"]["totalStar"], 2)
        self.assertEqual(chapter2["recordDungeons"]["10"]["targetsDone"], [1, 2])
        self.assertEqual(result["chapterSummaryInfo"]["totalNormalStar"], 2)
        # Too slow and two dead, but the seat-3 kill: the record keeps the union.
        active = self._active_with_stats(10, rounds=9, own_dead=2, enemy_seats=[3, 1])
        _, result = self._fight(10, active)
        self.assertEqual(result["balanceInfo"]["targetsDone"], [3])
        chapter2 = next(info for info in result["chapterInfo"] if info["recordChapter"]["chapterId"] == 2)
        self.assertEqual(chapter2["recordDungeons"]["10"]["targetsDone"], [1, 2, 3])
        self.assertEqual(result["chapterSummaryInfo"]["totalStar"], 3)

    def test_win_without_statistics_completes_every_target_and_loss_none(self):
        _, result = self._fight(2)
        self.assertEqual(result["balanceInfo"]["targetsDone"], [1, 2, 3])
        _, lost = self._fight(4, win=False)
        self.assertFalse(lost["balanceInfo"]["win"])
        self.assertEqual(lost["balanceInfo"]["targetsDone"], [])
        # Stages without targets (DU.IsShowStar false) never count stars.
        _, result = self._fight(1)
        self.assertEqual(result["stars"], 0)

    # ------------------------------------------------------------ energy
    def test_entry_pays_cost_and_a_win_pays_cost_win(self):
        self._energy(120)
        status, entered = self.call("POST", "/fci/dungeon/", {"dungeonId": 1})
        self.assertEqual(entered["resChange"], [{"baseRes": {"type": 6, "id": 0, "param": 0, "count": 119, "countDelta": -1}}])
        uid = entered["dungeonUid"]
        active = self.game._types["FightActiveData"](stageID=1, gameRound=1, winTeam=1)
        self.call("PATCH", f"/fci/dungeon/{uid}/active/", active.SerializeToString(), "application/wireformat")
        status, result = self.call("POST", f"/fci/dungeon/{uid}/balance", b"{}")
        energy = [c["baseRes"] for c in result["balanceInfo"]["resChange"] if "baseRes" in c and c["baseRes"]["type"] == 6]
        self.assertEqual([item["countDelta"] for item in energy], [-5])
        self.assertEqual(self._energy(), energy[-1]["count"])
        # A lost fight keeps costWin.
        _, entered = self._fight(1, win=False)
        self.assertEqual(self._energy(), 119)
        # The full price (cost + costWin) must be available to enter.
        self._energy(5)
        status, refused = self.call("POST", "/fci/dungeon/", {"dungeonId": 2})
        self.assertEqual((status, refused["error"]), (409, "insufficient_resource"))
        self.assertEqual(self._energy(), 5)

    # ------------------------------------------------------- chapter boxes
    def test_chapter_star_boxes_follow_chapter_reward(self):
        self.assertEqual(sorted(CHAPTER_REWARDS[1]), [0, 6, 12])
        self._cleared(2011, 2012, 2013, 1, 2, 3, 4, 5, 6, 7)
        status, locked = self.call("POST", "/fci/dungeon/chapter/1/reward/", {"starReward": 0})
        self.assertEqual((status, locked["error"]), (409, "chapter_not_finished"))
        self._cleared(8, targets=[])
        # star 0: chapter finished -> 120 ResPlayerMoney.
        status, claim = self.call("POST", "/fci/dungeon/chapter/1/reward/", {"starReward": 0})
        self.assertEqual(status, 200, claim)
        self.assertEqual(claim["starRewardGot"], [0])
        self.assertEqual([c["baseRes"]["countDelta"] for c in claim["resChange"] if c["baseRes"]["type"] == 5], [120])
        status, again = self.call("POST", "/fci/dungeon/chapter/1/reward/", {"starReward": 0})
        self.assertTrue(again["alreadyClaimed"])
        # Stages 2-7 carry 9 stars (8 was recorded without targets).
        status, chapter = self.call("GET", "/fci/dungeon/chapter/1", b"", "application/wireformat")
        self.assertEqual(chapter.record_chapter.total_star, 9)
        status, early = self.call("POST", "/fci/dungeon/chapter/1/reward/", {"starReward": 12})
        self.assertEqual((status, early["error"]), (409, "star_reward_locked"))
        # star 6 via the ApiChapterReward wire body: 8000 coins + 25 x 10001.
        wire = b""  # ApiChapterReward{star_reward: 6}
        status, claim = self.call("POST", "/fci/dungeon/chapter/1/reward/", wire, "application/wireformat")
        self.assertEqual(status, 200, claim)
        self.assertEqual(claim["starRewardGot"], [0, 6])
        gains = {(c["baseRes"]["type"], c["baseRes"]["id"]): c["baseRes"]["countDelta"] for c in claim["resChange"]}
        self.assertEqual(gains, {(7, 0): 8000, (11, 10001): 25})
        status, chapter = self.call("GET", "/fci/dungeon/chapter/1", b"", "application/wireformat")
        self.assertEqual(list(chapter.record_chapter.star_reward_got), [0, 6])
        status, missing = self.call("POST", "/fci/dungeon/chapter/1/reward/", {"starReward": 7})
        self.assertEqual((status, missing["error"]), (404, "chapter_reward_not_found"))

    # ------------------------------------------------------ star exchange
    def test_star_exchange_trades_unexchanged_stars_once(self):
        self._cleared(2, 4)  # 6 stars
        status, info = self.call("GET", "/fci/starexchange")
        self.assertEqual((status, info["starNum"]), (200, 6))
        status, done = self.call("POST", "/fci/starexchange/6", b"")
        self.assertEqual(status, 200, done)
        self.assertEqual(done["starNum"], 0)
        self.assertEqual([c["baseRes"]["countDelta"] for c in done["resChange"]], [6 * ONE_STAR_REWARD_COUNT])
        self.assertEqual(done["resChange"][0]["baseRes"]["type"], 5)
        status, none_left = self.call("POST", "/fci/starexchange/6", b"")
        self.assertEqual((status, none_left["error"]), (409, "no_star_to_exchange"))
        self._cleared(6)  # 3 new stars
        status, info = self.call("GET", "/fci/starexchange")
        self.assertEqual(info["starNum"], 3)

    # ------------------------------------------------------- support aura
    def test_support_aura_is_echoed_and_applied_to_own_fighters(self):
        status, assistants = self.call("POST", "/fci/dungeon-assistants/", {"token": 1})
        support = assistants[0]
        used = []
        original = self.game.social.record_assist_used
        self.game.social.record_assist_used = lambda profile, player_id: used.append(player_id) or original(profile, player_id)
        self._energy(120)
        status, plain = self.call("POST", "/fci/dungeon/", {"dungeonId": 2011})
        self.assertEqual(plain["baseInfo"]["auraActorId"], 0)
        request = {"dungeonId": 2012, "auraPlayerId": support["playerId"], "auraActorId": support["auraActorId"],
                   "auraStranger": support["stranger"]}
        status, entered = self.call("POST", "/fci/dungeon/", request)
        self.assertEqual(status, 200)
        base = entered["baseInfo"]
        self.assertEqual(
            (base["auraPlayerId"], base["auraActorId"], base["auraStranger"], base["auraActorQuality"]),
            (support["playerId"], support["auraActorId"], support["stranger"], support["auraActorQuality"]),
        )
        # The support does not fight: the six slots are the player's own.
        self.assertEqual(base["actorId"], plain["baseInfo"]["actorId"])
        effect = AURA_QUALITY_INFO[(support["auraActorId"], support["auraActorQuality"])]["effectAttrs"][0]
        before = {a["type"]: a["value"] for a in plain["baseInfo"]["actors"][0]["attrs"]}
        after = {a["type"]: a["value"] for a in base["actors"][0]["attrs"]}
        rate_base = {2: 1, 7: 3, 9: 5, 24: 23, 10: 6}
        if effect["type"] in before:
            self.assertEqual(after[effect["type"]], before[effect["type"]] + effect["value"])
        elif rate_base.get(effect["type"]) in before:
            base_type = rate_base[effect["type"]]
            self.assertEqual(after[base_type], math.floor(before[base_type] * (effect["value"] / 10000 + 1)))
        else:
            self.assertEqual(after[effect["type"]], effect["value"])
        self.assertEqual(used, [support["playerId"]])
        # A replayed POST keeps the aura and does not count the support again.
        status, replay = self.call("POST", "/fci/dungeon/", {"dungeonId": 2012})
        self.assertEqual(replay["baseInfo"]["auraActorId"], support["auraActorId"])
        self.assertEqual(used, [support["playerId"]])

    def test_support_fields_in_the_api_dungeon_wire_body(self):
        status, assistants = self.call("POST", "/fci/dungeon-assistants/", {"token": 1})
        support = assistants[-1]
        self._energy(120)
        message = self.game._types["ApiDungeon"](
            dungeon_id=1, select_actor_group_id=1, aura_player_id=support["playerId"],
            aura_actor_id=support["auraActorId"], aura_stranger=support["stranger"],
        )
        status, entered = self.call("POST", "/fci/dungeon/", message.SerializeToString(), "application/wireformat")
        self.assertEqual(status, 200)
        self.assertEqual(entered["baseInfo"]["auraActorQuality"], support["auraActorQuality"])
        self.assertEqual(entered["baseInfo"]["auraPlayerId"], support["playerId"])

    # ---------------------------------------------------------- task hook
    def test_task_hook_receives_mode_energy_and_battle_stats(self):
        calls = []
        original = self.game.tasks.on_battle_win

        def spy(profile, dungeon_id, energy_spent=0, **kwargs):
            calls.append((dungeon_id, energy_spent, kwargs))
            return original(profile, dungeon_id, energy_spent, **kwargs)

        self.game.tasks.on_battle_win = spy
        active = self._active_with_stats(10, rounds=3, own_dead=0, enemy_seats=[3, 1])
        active.teamList[0].statistic.linkIdList.extend([1, 2])
        active.teamList[0].statistic.bigSkillList.extend([7])
        self._fight(10, active)
        self._fight(11012)
        self._fight(2012)
        self.assertEqual(calls[0][0:2], (10, 6))
        self.assertEqual(calls[0][2]["mode"], "normal")
        self.assertEqual(calls[0][2]["stats"], {"linkSkills": 2, "bigSkills": 1, "kills": 2, "rounds": 3})
        # Challenge stages count for main tasks such as BALANCE [11012].
        self.assertEqual((calls[1][0], calls[1][2]["mode"]), (11012, "normal"))
        self.assertEqual((calls[2][0], calls[2][2]["mode"]), (2012, "story"))


if __name__ == "__main__":
    unittest.main()
