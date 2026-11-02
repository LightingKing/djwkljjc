"""Native actor upgrade/entry requests must retain growth and combat talents."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from google.protobuf import message_factory

from accounts import LocalAccounts
from game import LocalGame
import test_systems


class FightActorTalentSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.accounts = LocalAccounts(self.root / "accounts")
        self.game = LocalGame(self.root / "game", self.accounts)
        self.account, self.token = test_systems.LocalSystemsTests._guest(self.accounts)

    def call(self, method, path, payload):
        wire = hasattr(payload, "SerializeToString")
        status, result = self.game.handle_http(
            method, path,
            payload.SerializeToString() if wire else json.dumps(payload).encode(),
            "application/wireformat" if wire else "application/json", self.token,
        )
        self.assertEqual(status, 200, (path, result))
        return result

    def login_actor(self):
        messages = dict(self.game.login_messages(self.account))
        actors = self.game._types["DBGamePlayer.Actors"]()
        actors.ParseFromString(messages["DBGamePlayer.Actors"])
        return actors.player_actors[0]

    def enter(self):
        request = self.game._types["ApiDungeon"]()
        request.dungeon_id = 1
        request.select_actor_group_id = 1
        return self.call("POST", "/fci/dungeon/", request)["baseInfo"]["actors"][0]

    @staticmethod
    def talents(actor):
        return [{"talentId": t.talent_id, "talentValue": t.talent_value}
                for t in actor.talent_info]

    def test_native_upgrade_and_reentry_keep_current_growth_and_talents(self):
        self.call("POST", "/fci/baseresource/level/", {"level": 30})
        self.call("POST", "/fci/item/", {"itemId": 10001, "count": 5})
        login = self.login_actor()
        expected_talents = self.talents(login)
        self.assertTrue(expected_talents)
        before = self.enter()

        request = message_factory.GetMessageClass(
            self.game._pool.FindMessageTypeByName("ApiActorLevelUp"))()
        food = request.food_res.add()
        food.type, food.id, food.count = 11, 10001, 5
        result = self.call("POST", "/fci/actor/1/levelupgrade/", request)
        upgraded = next(change.actor for change in result.res_change if change.HasField("actor"))
        after = self.enter()  # Re-enter the same active dungeon after upgrading.

        self.assertEqual((login.level, before["level"], upgraded.level, after["level"]), (1, 1, 5, 5))
        self.assertEqual({a.type: a.value for a in login.attrs}[3], 100)
        self.assertEqual({a.type: a.value for a in upgraded.attrs}[3], 130)
        self.assertEqual({a["type"]: a["value"] for a in after["attrs"]},
                         {a.type: a.value for a in upgraded.attrs})
        self.assertEqual((after["uid"], after["id"], after["defaultIndex"]), (1, 1001, 0))
        self.assertEqual(self.talents(upgraded), expected_talents)
        self.assertEqual(before["talentInfo"], expected_talents)
        self.assertEqual(after["talentInfo"], expected_talents)

        self.game = LocalGame(self.root / "game", self.accounts)
        self.assertEqual(self.talents(self.login_actor()), expected_talents)
        resumed = self.enter()
        self.assertEqual(resumed["talentInfo"], expected_talents)
        self.assertEqual(resumed["attrs"], after["attrs"])

    def test_original_lua_builds_dynamic_talents_from_entry_payload(self):
        # Exercise the original initialization function, not a Python copy of it.
        source = (Path(__file__).resolve().parents[3] / "analysis" / "lua-decompiled"
                  / "logic__initgamelogic.lua")
        if not source.exists():
            self.skipTest("original decompiled client Lua is not present")
        from lupa.lua53 import LuaRuntime

        expected = self.talents(self.login_actor())
        self.assertTrue(expected)
        entered = self.enter()
        lua = LuaRuntime()
        lua.execute("function require(_) return {enum={AttrType={Hp=1,Max=26}}} end")
        lua.execute(source.read_text(encoding="utf-8"))

        def to_lua(value):
            if isinstance(value, dict):
                return lua.table_from({key: to_lua(item) for key, item in value.items()})
            if isinstance(value, list):
                return lua.table_from([to_lua(item) for item in value])
            return value

        actor = lua.table_from({"actorInfo": to_lua(entered)})
        lua.globals().setActorTalent(actor)
        self.assertEqual(len(actor.dynamicTalent), len(expected))
        for index, talent in enumerate(expected, 1):
            actual = actor.dynamicTalent[index]
            self.assertEqual((actual.talentId, actual.talentValue, actual.talentFlag, actual.talentIndex),
                             (talent["talentId"], talent["talentValue"], 1, index))


if __name__ == "__main__":
    unittest.main()
