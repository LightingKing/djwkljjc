from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actors as actor_store
from accounts import LocalAccounts
from achievements import ACHIEVEMENTS, LocalAchievements, achievement_type, trophy
from equipment import grant_equipment
from game import LocalGame


def _guest(accounts: LocalAccounts):
    """Same guest bootstrap as tests/test_systems.py LocalSystemsTests._guest."""

    first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    token = "AccountToken " + first[1]["accountToken"]
    entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    second_token = "AccountToken " + second[1]["accountToken"]
    auth_body = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
    auth = accounts.handle("POST", "/Auth/", auth_body, "application/wireformat", second_token)
    return accounts.verify_access_token(auth[1]["tokenAccess"]), "Token " + auth[1]["tokenAccess"]


class AchievementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", accounts)
        self.account, _ = _guest(accounts)
        self.profile = dict(self.game._profile(self.account))
        self.now = [int(datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc).timestamp())]
        self.ach = LocalAchievements(self.game, clock=lambda: self.now[0])

    def tearDown(self) -> None:
        self._temp.cleanup()

    # ------------------------------------------------------------ helpers
    def _call(self, method: str, path: str, body: dict | None = None):
        raw = None if body is None else json.dumps(body).encode()
        result = self.ach.handle_http(method, path, raw, self.profile)
        self.assertIsNotNone(result, path)
        json.dumps(result[1])  # plain JSON only
        return result

    def _list(self) -> dict[int, dict]:
        status, rows = self._call("GET", "/fci/achievement/")
        self.assertEqual(status, 200)
        self.assertIsInstance(rows, list)  # HandleAchievementData uses ipairs
        return {row["id"]: row for row in rows}

    def _systems(self):
        state = self.game.systems._state(self.profile)
        actor_store.instances(self.profile, state)
        return state

    def _save(self, state) -> None:
        self.game.systems._save(self.profile, state)

    def _add_actor(self, actor_id: int, **fields) -> dict:
        state = self._systems()
        record = actor_store.add(self.profile, state, actor_id)
        record.update(fields)
        self._save(state)
        return record

    def _dungeon_rows(self, count: int, *, dungeon_id: int = 100004, status: str = "balanced",
                      mode: str = "story", uids: list[int] | None = None) -> None:
        with closing(self.game._connect()) as connection, connection:
            start = connection.execute("SELECT COALESCE(MAX(dungeon_uid), 0) FROM local_dungeons").fetchone()[0]
            for offset in range(count):
                connection.execute(
                    "INSERT INTO local_dungeons(dungeon_uid, account_id, player_id, dungeon_id, dungeon_type, "
                    "actor_uids_json, status, created_at, mode) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
                    (start + offset + 1, int(self.profile["account_id"]), "p", dungeon_id,
                     json.dumps(uids or [1]), status, self.now[0], mode),
                )

    @staticmethod
    def _deltas(changes: list) -> dict:
        return {(c["baseRes"]["type"], c["baseRes"]["id"]): c["baseRes"]["countDelta"] for c in changes if "baseRes" in c}

    # -------------------------------------------------------------- tests
    def test_list_shape_matches_handleachievementdata(self) -> None:
        rows = self._list()
        self.assertEqual(set(rows), {101, 201, 301, 401, 402, 405, 501, 601, 701, 801, 901, 1001, 1101, 1201})
        for row in rows.values():
            self.assertEqual(set(row), {"id", "progress", "isDone", "rewardGot", "doneTime"})
            self.assertIn(row["id"], ACHIEVEMENTS)
        self.assertNotIn(102, rows)  # chain successor waits for 101
        self.assertFalse(any(ACHIEVEMENTS[i]["hide"] for i in rows))
        self.assertEqual(len(ACHIEVEMENTS), 123)
        self.assertEqual(self.ach.redpoint_fields(self.profile), {"achievementSummaryGet": {"canReward": False}})

    def test_star_progress_claim_points_and_successor(self) -> None:
        state = self._systems()
        first = actor_store.ordered(self.profile, state)[0]
        first["star"] = 3
        self._save(state)
        rows = self._list()
        self.assertTrue(rows[201]["isDone"])        # one character at star >= 2
        self.assertTrue(rows[202]["isDone"])        # successor listed, also star >= 3
        self.assertEqual(rows[201]["doneTime"], self.now[0])
        self.assertFalse(rows[203]["isDone"])
        self.assertEqual(rows[203]["progress"], 0)
        self.assertNotIn(204, rows)
        self.assertTrue(self.ach.redpoint_fields(self.profile)["achievementSummaryGet"]["canReward"])

        status, result = self._call("POST", "/fci/achievement/201/reward/")
        self.assertEqual(status, 200)
        deltas = self._deltas(result["resChange"])
        self.assertEqual(deltas[(5, 0)], 50)        # ResPlayerMoney
        self.assertEqual(deltas[(21, 0)], 10)       # ResAchievementPoint
        listed = {row["id"]: row for row in result["newAchievements"]}
        self.assertTrue(listed[201]["rewardGot"])
        self.assertIn(203, listed)
        self.assertEqual(self.ach.achievement_point(self.profile), 10)
        self.assertEqual(self._call("POST", "/fci/achievement/201/reward/")[0], 409)   # twice
        self.assertEqual(self._call("POST", "/fci/achievement/203/reward/")[0], 409)   # not done
        self.assertEqual(self._call("POST", "/fci/achievement/204/reward/")[0], 409)   # not listed
        self.assertEqual(self._call("POST", "/fci/achievement/99999/reward/")[0], 404)
        self.assertEqual(self._call("GET", "/fci/achievement/201/reward/")[0], 405)

        # Consuming the character never undoes earned progress.
        state = self._systems()
        state["actorInstances"][str(first["uid"])]["star"] = 1
        self._save(state)
        self.assertTrue(self._list()[202]["isDone"])

    def test_trophy_follows_achievementpointtarget(self) -> None:
        self.assertEqual(trophy(0)["trophy"], 1)
        self.assertEqual((trophy(120)["trophy"], trophy(120)["level"]), (1, 3))
        self.assertEqual((trophy(300)["trophy"], trophy(300)["level"]), (2, 1))
        self.assertEqual(trophy(99999)["trophy"], 5)

    def test_dungeon_wins_tower_floors_energy_and_team_member(self) -> None:
        self._dungeon_rows(49)
        self._dungeon_rows(3, status="failed")
        rows = self._list()
        self.assertEqual(rows[101]["progress"], 49)
        self.assertFalse(rows[101]["isDone"])
        self._dungeon_rows(1)
        rows = self._list()
        self.assertTrue(rows[101]["isDone"])
        self.assertEqual(rows[102]["progress"], 50)
        # PowerCostM counts the stage cost of every started run (100004 costs 3).
        self.assertEqual(rows[801]["progress"], 53 * 3)
        # Tower floors (1001 chain) count won tower runs only.
        self._dungeon_rows(10, dungeon_id=1, mode="tower")
        self.assertTrue(self._list()[1001]["isDone"])
        # Hidden 19: wins with character 2128 in the team.
        member = self._add_actor(2128)
        self._dungeon_rows(5, uids=[1, member["uid"]])
        self.assertEqual(self.ach.refresh(self.profile), [])
        self.assertEqual(self.ach._state(self.profile)["achievements"]["19"]["progress"], 5)

    def test_gacha_and_equipment_progress(self) -> None:
        state = self._systems()
        state["gachaPools"] = {"1": {"total": 15}, "2": {"total": 5}, "5": {"total": 100}}
        self._save(state)
        rows = self._list()
        self.assertTrue(rows[701]["isDone"])        # 20 draws in pools 1+2 (chips pool 5 excluded)
        self.assertEqual(rows[702]["progress"], 20)

        grant_equipment(self.game, self.profile, 10100)   # quality 2
        rows = self._list()
        self.assertTrue(rows[601]["isDone"])
        self.assertEqual(rows[602]["progress"], 0)  # needs quality >= 3
        status, equips = self._call("GET", "/fci/CollectionsEquip")
        self.assertEqual(status, 200)
        self.assertEqual(equips, {"equips": [{"equipId": 10100}]})
        # Decomposed equipment stays collected.
        state = self._systems()
        state["equips"] = {}
        self._save(state)
        self.assertEqual(self._call("GET", "/fci/CollectionsEquip")[1], {"equips": [{"equipId": 10100}]})
        self.assertTrue(self._list()[601]["isDone"])

    def test_record_counters_hidden_rows_and_notifier(self) -> None:
        pushed = []
        self.ach.notifier = lambda profile, ids: pushed.append(list(ids))
        self.assertEqual(achievement_type("DeployDoneM"), 36)
        self.assertEqual(achievement_type("AchievementType_BattleLinkP0MTimes"), 15)
        self.assertEqual(self.ach.record(self.profile, "DeployDoneM", 15), [1101])
        self.assertEqual(pushed, [[1101]])
        rows = self._list()
        self.assertTrue(rows[1101]["isDone"])
        self.assertEqual(rows[1102]["progress"], 15)
        # Specific-param counters feed hidden rows; the generic counter feeds p0 = 0 rows.
        done = self.ach.record(self.profile, "BattleLinkP0MTimes", 50, param=10004)
        self.assertIn(3, done)
        rows = self._list()
        self.assertTrue(rows[3]["isDone"])         # hidden row listed once done
        self.assertEqual(rows[901]["progress"], 50)
        self.assertNotIn(4, rows)                   # other hidden rows stay hidden
        self.ach.record(self.profile, "ResetGreenTalentContinuousMTimes", value=50)
        self.assertTrue(self._list()[10]["isDone"])
        self.assertEqual(self.ach.record(self.profile, "NoSuchGoal"), [])

    def test_medal_and_skin_rewards_reach_their_stores(self) -> None:
        self._add_actor(3001, quality=4)            # hidden 13: ActorPnQualityP1M [4, 3001]
        self.assertTrue(self._list()[13]["isDone"])
        status, result = self._call("POST", "/fci/achievement/13/reward/")
        self.assertEqual(status, 200)
        self.assertEqual(self._deltas(result["resChange"])[(8, 10001)], 1)
        self.assertIn("10001", self._systems()["medals"])
        self.ach.record(self.profile, "BattleLinkP0MTimes", 50, param=10063)   # hidden 18 -> skin 101006
        status, result = self._call("POST", "/fci/achievement/18/reward/")
        self.assertEqual(status, 200)
        self.assertEqual(self._deltas(result["resChange"])[(29, 101006)], 1)
        unlocked = self._systems()["unlockedSkins"]
        self.assertIn(101006, unlocked["1010"])
        self.assertIn(101006, unlocked["2010"])

    def test_collections_actor_lists_every_character_ever_owned(self) -> None:
        status, result = self._call("GET", "/fci/CollectionsActor")
        self.assertEqual(status, 200)
        self.assertEqual([row["id"] for row in result["actors"]], [1001])
        self.assertEqual(result["actors"][0]["addTime"], self.now[0])
        record = self._add_actor(2117)
        self._add_actor(2117)                      # copies collapse to one id
        state = self._systems()
        actor_store.remove(self.profile, state, record["uid"])
        self._save(state)
        ids = [row["id"] for row in self._call("GET", "/fci/CollectionsActor")[1]["actors"]]
        self.assertEqual(ids, [1001, 2117])
        self.assertEqual(self._call("POST", "/fci/CollectionsActor", {"id": 1002})[0], 405)

    def test_novel_unlock_score_claim_and_cover(self) -> None:
        novels = self._call("GET", "/fci/CollectionsNovel")[1]["novels"]
        self.assertNotIn(2, [n["novelId"] for n in novels])
        self.assertEqual(self._call("GET", "/fci/CollectionsNovel/2")[1]["novels"], [])
        record = self._add_actor(2117, star=2)     # novel 2: actor 2117, score 3
        status, detail = self._call("GET", "/fci/CollectionsNovel/2")
        self.assertEqual(status, 200)
        self.assertEqual(detail["novels"], [{"novelId": 2, "curRewardNum": 0, "canGetReward": False, "curCover": 2}])
        self.assertEqual([(a["actorId"], a["star"]) for a in detail["novelDetail"]["actorInfo"]], [(2117, 2)])
        self.assertEqual(self._call("POST", "/fci/CollectionsNovel/2")[0], 409)
        state = self._systems()
        state["actorInstances"][str(record["uid"])]["star"] = 3
        self._save(state)
        listed = {n["novelId"]: n for n in self._call("GET", "/fci/CollectionsNovel")[1]["novels"]}
        self.assertTrue(listed[2]["canGetReward"])
        status, claim = self._call("POST", "/fci/CollectionsNovel/2")
        self.assertEqual(status, 200)
        self.assertEqual(self._deltas(claim["resChange"])[(22, 2)], 1)          # ResNovelCover 2
        self.assertEqual(claim["novels"], [{"novelId": 2, "curRewardNum": 1, "canGetReward": False, "curCover": 2}])
        self.assertEqual(self._call("POST", "/fci/CollectionsNovel/2")[0], 409)
        self.assertEqual(self._call("GET", "/fci/NovelCover/2")[1], {"coverInfo": [{"coverId": 2}]})
        self.assertEqual(self._call("PUT", "/fci/CollectionsNovel/2", {"newCover": 2})[0], 200)
        self.assertEqual(self._call("PUT", "/fci/CollectionsNovel/2", {"newCover": 7})[0], 409)
        self.assertEqual(self._call("GET", "/fci/CollectionsNovel/999")[0], 404)   # not an active novel

    def test_unrelated_routes_fall_through(self) -> None:
        for path in ("/fci/task/", "/fci/achievement/101/", "/fci/collection/", "/fci/novel-cover/"):
            self.assertIsNone(self.ach.handle_http("GET", path, None, self.profile), path)


if __name__ == "__main__":
    unittest.main()
