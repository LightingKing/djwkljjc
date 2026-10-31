from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
from game import LocalGame
from grants import grant_rewards
from maze import LocalMaze
from welfare_features import LocalWelfareFeatures


def guest(accounts: LocalAccounts):
    session = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    session_token = "AccountToken " + session[1]["accountToken"]
    entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", session_token)[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", session_token)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    token = "AccountToken " + second[1]["accountToken"]
    raw = entry.encode(); auth = b"\x0a" + bytes((len(raw),)) + raw + b"\x12\x05guest"
    result = accounts.handle("POST", "/Auth/", auth, "application/wireformat", token)
    return accounts.verify_access_token(result[1]["tokenAccess"]), "Token " + result[1]["tokenAccess"]


class WelfareMazeTests(unittest.TestCase):
    def test_jigsaw_and_postcard_use_real_costs_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = LocalAccounts(Path(temp) / "accounts"); game = LocalGame(Path(temp) / "game", accounts); account, auth = guest(accounts)
            profile = game._profile(account)
            grant_rewards(game, profile, [{"type": 11, "id": 1016, "count": 25}, {"type": 11, "id": 10001, "count": 1}])
            welfare = LocalWelfareFeatures(game)
            status, first = welfare.handle_http("GET", "/fci/JigsawPuzzle/", None, profile)
            self.assertEqual((status, first["isFirstEnter"], first["lightenCell"]), (200, True, []))
            status, result = welfare.handle_http("POST", "/fci/JigsawPuzzle/lighten/", b'{"cellId":1}', profile)
            self.assertEqual(status, 200); self.assertTrue(result["resChange"])
            status, repeated = welfare.handle_http("POST", "/fci/JigsawPuzzle/lighten/", b'{"cellId":1}', profile)
            self.assertEqual((status, repeated["resChange"]), (200, []))
            status, postcard = welfare.handle_http("GET", "/fci/Postcard/", None, profile)
            self.assertEqual((status, len(postcard["postcardInfo"]), len(postcard["postcardInfo"][0]["postcardCell"])), (200, 4, 18))

    def test_seal_and_chip_exchange_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = LocalAccounts(Path(temp) / "accounts"); game = LocalGame(Path(temp) / "game", accounts); account, _auth = guest(accounts)
            profile = game._profile(account); grant_rewards(game, profile, [{"type": 11, "id": 1016, "count": 1}])
            welfare = LocalWelfareFeatures(game)
            status, result = welfare.handle_http("POST", "/fci/seal/fill/1/", None, profile)
            self.assertEqual(status, 200)
            self.assertEqual(int(result.cell_id) if hasattr(result, "cell_id") else int(result["cellId"]), 1)
            chips = [20002, 20116, 20014, 20128, 20107, 20108, 20012, 20006]
            status, changed = welfare.handle_http("POST", "/fci/chipexchange/", json.dumps({"chips": chips}).encode(), profile)
            changed_info = changed["chipExchangeInfo"]
            self.assertEqual((status, changed_info["modifyCount"], changed_info["settlePosInfo"]), (200, 1, chips))
            status, saved = welfare.handle_http("GET", "/fci/chipexchange/", None, profile)
            saved_info = saved["chipExchangeInfo"]
            self.assertEqual((status, saved_info["modifyCount"], saved_info["exchangePosInfo"]), (200, 1, chips))

    def test_maze_three_floor_loop_and_reward_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = LocalAccounts(Path(temp) / "accounts"); game = LocalGame(Path(temp) / "game", accounts); account, auth = guest(accounts)
            profile = game._profile(account); grant_rewards(game, profile, [{"type": 11, "id": 80007, "count": 1}])
            status, entered = game.handle_http("POST", "/fci/Maze/Enter/", b'{"mazeId":1,"mazeFloor":1}', authorization=auth)
            self.assertEqual((status, entered["mazeInfo"]["curMazeFloor"]), (200, 1))
            # A jump to the exit is rejected; the client must follow the
            # server-provided nextCellIndex before starting a fight.
            status, _ = game.handle_http("POST", "/fci/Maze/Move/", b'{"endCellIndex":9}', authorization=auth)
            self.assertEqual(status, 409)
            for _ in range(20):
                state = game.profile_features._state(profile)["maze"]
                if state["isMazeOver"]:
                    break
                idx = game.profile_features._state(profile)["maze"]["playerCellIndex"]
                status, started = game.handle_http("POST", "/fci/maze-dungeon/", json.dumps({"cellIndex": idx}).encode(), authorization=auth)
                self.assertEqual(status, 200)
                uid = started["dungeonUid"]
                status, settled = game.handle_http("POST", f"/fci/maze-dungeon/{uid}/balance/", b"{}", authorization=auth)
                self.assertEqual((status, settled["result"]["win"]), (200, True))
            state = game.profile_features._state(profile)["maze"]
            self.assertTrue(state["isMazeOver"]); self.assertEqual(state["floor"], 3)
            before = game.systems._state(profile).get("items", {}).get("60001", {}).get("count", 0)
            status, claimed = game.handle_http("POST", "/fci/Maze/ReceivePrize/", b"null", authorization=auth)
            self.assertEqual(status, 200); self.assertTrue(claimed["resChange"])
            after = game.systems._state(profile)["items"]["60001"]["count"]
            self.assertEqual(after, before + 1)
            status, repeated = game.handle_http("POST", "/fci/Maze/ReceivePrize/", b"{}", authorization=auth)
            self.assertEqual((status, repeated["resChange"]), (200, []))
            restarted = LocalGame(Path(temp) / "game", accounts)
            status, summary = restarted.handle_http("GET", "/fci/Maze/Summary/", authorization=auth)
            self.assertEqual((status, summary["mazeId"]), (200, 0))

    def test_maze_actor_filter_and_member_poll_use_client_shapes(self):
        with tempfile.TemporaryDirectory() as temp:
            accounts = LocalAccounts(Path(temp) / "accounts"); game = LocalGame(Path(temp) / "game", accounts); account, auth = guest(accounts)
            profile = game._profile(account); grant_rewards(game, profile, [{"type": 11, "id": 80007, "count": 1}])
            status, _entered = game.handle_http("POST", "/fci/Maze/Enter/", b'{"mazeId":1}', authorization=auth)
            self.assertEqual(status, 200)
            status, actors = game.handle_http("GET", "/fci/Maze/Actor/", authorization=auth)
            self.assertEqual(status, 200); self.assertTrue(actors["selectActors"])
            uid = actors["selectActors"][0]["actor"]["uid"]
            status, saved = game.handle_http("POST", "/fci/Maze/Actor/", json.dumps({"actorUids": [uid]}).encode(), authorization=auth)
            self.assertEqual((status, saved), (200, {}))
            status, group = game.handle_http("GET", "/fci/Maze/FightActorGroup/", authorization=auth)
            self.assertEqual((status, group["fightGroup"][0]["members"][0]["actorUid"]), (200, uid))
            status, member = game.handle_http("GET", f"/fci/mazeactor/{uid}", authorization=auth)
            self.assertEqual((status, member["actor"]["uid"], member["dead"]), (200, uid, False))


if __name__ == "__main__":
    unittest.main()
