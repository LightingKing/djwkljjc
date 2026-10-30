import sys
import unittest
from pathlib import Path


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from entering import handle_http  # noqa: E402


class EnteringRoutesTests(unittest.TestCase):
    def test_known_safe_defaults_have_confirmed_shapes(self):
        cases = {
            "/fci/friendlist/": [],
            "/fci/blacklist/": [],
            "/fci/activity/": {},
            "/fci/mail/": [],
            "/fci/recentcontact/": [],
            "/fci/auto-fight-conf/": {"banedSkills": {}},
            "/fci/temp-entrance-switch/": {"switchInfo": [{"type": 107, "switch": 1,
                "isWindowEntrance": False, "startTime": "", "stopTime": "", "tips": ""}]},
            "/fci/equip/custom-match/": {"equipCustomMatch": {}},
            "/fci/asyncpvprecord/": {
                "todayMaxWinNum": 0,
                "leftRewardsWinNum": [],
                "nextRefreshSyncpvpRecordTime": 0,
                "canRefresh": False,
            },
            "/fci/name/": {"notFirstRename": False},
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(handle_http("GET", path), (200, expected))

    def test_task_and_deploy_defaults_are_schema_shaped(self):
        self.assertEqual(
            handle_http("GET", "/fci/task/"),
            (
                200,
                {
                    "tasks": [],
                    "delTasks": [],
                    "disableCategory": [],
                    "activePoint": 0,
                    "activePointRewardGot": [],
                    "taskTargetDone": [],
                    "clearAll": True,
                },
            ),
        )
        self.assertEqual(
            handle_http("GET", "/fci/deploy/mission/"),
            (200, {"missionInfo": [], "accomplishNum": 0}),
        )

    def test_danmu_switch_is_closed_and_story_posts_are_acknowledged_empty(self):
        # ApiDanmuSwitch.result.result is repeated DanmuSwitch.  No rows means
        # no resource can enable either switch_get or switch_send locally.
        self.assertEqual(
            handle_http("GET", "/fci/danmu-switch"),
            (200, {"result": []}),
        )
        # ApiDanmu.post.post is repeated DbDanmu; local play has no remote
        # comment store, so the write-shaped response is an empty list.
        self.assertEqual(
            handle_http("POST", "/fci/danmu/"),
            (200, {"post": []}),
        )

    def test_danmu_route_methods_stay_narrow(self):
        self.assertEqual(
            handle_http("GET", "/fci/danmu/"),
            (405, {"error": "method_not_allowed", "message": "POST required"}),
        )
        self.assertEqual(
            handle_http("POST", "/fci/danmu-switch/"),
            (405, {"error": "method_not_allowed", "message": "GET required"}),
        )

    def test_aq_log_is_local_ack_only(self):
        # ApiAQLog.result.result is int32.  The local acknowledgement uses
        # proto3's default value and does not assert an anti-cheat conclusion.
        self.assertEqual(
            handle_http("POST", "/fci/aq/log"),
            (200, {"result": 0}),
        )
        self.assertEqual(
            handle_http("GET", "/fci/aq/log"),
            (405, {"error": "method_not_allowed", "message": "POST required"}),
        )

    def test_tss_anti_is_local_post_ack_only(self):
        """TSS telemetry must not retry against the retired service."""

        self.assertEqual(handle_http("POST", "/fci/tss/anti/"), (200, {}))
        self.assertEqual(
            handle_http("GET", "/fci/tss/anti/"),
            (405, {"error": "method_not_allowed", "message": "POST required"}),
        )

    def test_post_login_optional_routes_use_descriptor_defaults(self):
        cases = {
            "/fci/guild-player/invited/": [],
            "/fci/server-update-info/": {"timeInfo": {}},
            "/fci/actor-cultivation/": [],
            "/fci/BurstLinkPvpInfo/": {
                "reward": [],
                "resChange": [],
                "dungeonId": 0,
            },
            "/fci/baseresource/update/": {
                "resChange": [],
                "lastResIncTime": {},
            },
            "/fci/gachainfo/": {"gachaInfo": []},
            "/fci/conditionreward": {
                "hasRewardQualification": False,
                "isRewardGot": False,
            },
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(handle_http("GET", path), (200, expected))

    def test_redpoint_default_contains_only_evidenced_reads(self):
        self.assertEqual(
            handle_http("GET", "/fci/redpoint/data"),
            (
                200,
                {
                    "redpoint": {"redpointType": {}},
                    "achievementSummaryGet": {"canReward": False},
                    "rolePresentRedpoint": {"presents": []},
                    "medalRedpoint": {"medal": []},
                    "gachaInfoGet": {"gachaInfo": []},
                    "shopRedpoint": {"shopInfo": []},
                    "relationPointRedpoint": False,
                },
            ),
        )

    def test_signboard_uses_starter_config_without_fabricating_id(self):
        profile = {
            "starter_config": {
                "tables": {"PlayerInitConfig": [{"initSignboardId": 101}]}
            }
        }
        self.assertEqual(
            handle_http("GET", "/fci/Signboard/", profile),
            (
                200,
                {
                    "signboard": 101,
                    "unlockedSignboardId": [101],
                    "checkSignboardTime": 0,
                },
            ),
        )
        self.assertIsNone(handle_http("GET", "/fci/Signboard/"))

    def test_explicit_profile_values_can_supply_complex_or_override_routes(self):
        redpoint = {"medalRedpoint": {"medal": True}}
        task = {"clearAll": False, "tasks": [{"id": 7}]}
        profile = {"entering": {"/fci/redpoint/data": redpoint, "/fci/task/": task}}
        self.assertEqual(handle_http("GET", "/fci/redpoint/data/", profile), (200, redpoint))
        self.assertEqual(handle_http("GET", "/fci/task/?page=1", profile), (200, task))

    def test_unknown_route_and_method_are_not_silently_successful(self):
        self.assertIsNone(handle_http("GET", "/fci/not-confirmed/"))
        self.assertEqual(
            handle_http("POST", "/fci/mail/"),
            (405, {"error": "method_not_allowed", "message": "GET required"}),
        )


if __name__ == "__main__":
    unittest.main()
