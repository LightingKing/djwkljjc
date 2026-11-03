from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "client-patch" / "hot-update"
sys.path.insert(0, str(PATCH))


def _builder():
    spec = importlib.util.spec_from_file_location("hot_update_build_d3", PATCH / "build.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TeamDungeonPatchContractTests(unittest.TestCase):
    def test_fragment_targets_native_team_and_burst_windows(self):
        import json

        fragment = json.loads((PATCH / "modules-v10-d3.fragment.json").read_text(encoding="utf-8"))
        self.assertEqual(fragment["patchVersion"], 10)
        self.assertEqual(
            [(row["source"], row["target"]) for row in fragment["modules"]],
            [
                ("sources/dungeonteam-local.lua", "assetbundle/data/lua/window/dungeonteam/dungeonteam"),
                ("sources/dungeonteamprepare-local.lua", "assetbundle/data/lua/window/dungeonteamprepare/dungeonteamprepare"),
                ("sources/burstlinkactorgroup-v10.lua", "assetbundle/data/lua/window/burstlinkactorgroup/burstlinkactorgroup"),
            ],
        )

    def test_local_lua_sources_compile_and_use_http_single_player_flow(self):
        builder = _builder()
        for filename in ("dungeonteam-local.lua", "dungeonteamprepare-local.lua", "burstlinkactorgroup-v10.lua"):
            source = (PATCH / "sources" / filename).read_bytes()
            standard = builder.compile_source(source, b"@d3-local")
            builder.verify_lua(standard)
        entry = (PATCH / "sources" / "dungeonteam-local.lua").read_text(encoding="utf-8")
        prepare = (PATCH / "sources" / "dungeonteamprepare-local.lua").read_text(encoding="utf-8")
        self.assertIn('this:GameRequest("fci/teamdungeon/enter/"):Post', prepare)
        self.assertIn('this:GameRequest("fci/teamdungeon/" .. tostring(m_roomInfo.dungeonUid or m_roomInfo.roomId) .. "/start/"):Post', prepare)
        self.assertIn('this:SetData("DungeonTeam/LocalAi", result.localAi == true)', entry)
        self.assertIn('WU.EnterGameLevel(message.dungeonUid, message.baseInfo, m_dungeonId, PB.enum.DungeonType.TeamPve', prepare)
        self.assertIn('m_localAi = this:GetData("DungeonTeam/LocalAi") == true', prepare)
        self.assertIn('this:SetData("TeamPveLocal", true)', prepare)
        self.assertIn('function SendTeamSocket(name, payload)', prepare)


if __name__ == "__main__":
    unittest.main()
