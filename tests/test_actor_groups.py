"""Dungeon team presets (fci/ActorGroup/) and selected preset per mode."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts  # noqa: E402
from game import LocalGame  # noqa: E402
import test_systems  # noqa: E402


class ActorGroupTests(unittest.TestCase):
    def test_default_presets_have_starter_actor_and_six_slots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _, auth = test_systems.LocalSystemsTests._guest(accounts)
            status, groups = game.handle_http("GET", "/fci/ActorGroup/", None, "application/json", auth)
            self.assertEqual(status, 200)
            # Misc.maxActorGroup == 5 unlocked presets.
            self.assertEqual(sorted(groups), ["1", "2", "3", "4", "5"])
            members = groups["1"]["members"]
            self.assertEqual([m["index"] for m in members], [0, 1, 2, 3, 4, 5])
            self.assertEqual(members[0]["actorUid"], 1)
            self.assertTrue(all(m["actorUid"] == 0 for m in members[1:]))
            status, selected = game.handle_http("GET", "/fci/SelectedGroupId/StoryMode", None, "application/json", auth)
            self.assertEqual((status, selected), (200, {"selectActorGroupId": 1}))

    def test_saved_team_and_selection_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            _, auth = test_systems.LocalSystemsTests._guest(accounts)
            team = {"actorGroups": {"2": {"members": [{"actorUid": 1, "index": 1}, {"actorUid": 0, "index": 4}]}}}
            status, groups = game.handle_http("PUT", "/fci/ActorGroup/", json.dumps(team).encode(), "application/json", auth)
            self.assertEqual(status, 200)
            self.assertEqual(groups["2"]["members"][1], {"actorUid": 1, "index": 1})
            self.assertEqual(groups["2"]["members"][0], {"actorUid": 0, "index": 0})
            self.assertEqual(groups["1"]["members"][0]["actorUid"], 1)
            status, _ = game.handle_http("PATCH", "/fci/SelectedGroupId/StoryMode",
                                         json.dumps({"selectActorGroupId": 2}).encode(), "application/json", auth)
            self.assertEqual(status, 200)

            again = LocalGame(root / "game", accounts)
            _, groups = again.handle_http("GET", "/fci/ActorGroup/", None, "application/json", auth)
            self.assertEqual(groups["2"]["members"][1]["actorUid"], 1)
            _, selected = again.handle_http("GET", "/fci/SelectedGroupId/StoryMode", None, "application/json", auth)
            self.assertEqual(selected, {"selectActorGroupId": 2})
            # Other modes keep their own selection.
            _, other = again.handle_http("GET", "/fci/SelectedGroupId/gold-dungeon", None, "application/json", auth)
            self.assertEqual(other, {"selectActorGroupId": 1})


if __name__ == "__main__":
    unittest.main()
