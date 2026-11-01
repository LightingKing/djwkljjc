import json
from http.client import HTTPConnection
from pathlib import Path
import tempfile
import threading
import unittest

from server.core.client_errors import client_error_reply
from server import FixtureServer, JsonlLogger


class ClientErrorA4Tests(unittest.TestCase):
    def assert_notify(self, status, payload, expected, out_status=509):
        self.assertEqual(client_error_reply(status, payload),
                         (out_status, {"notify": {"code": expected}}))

    def test_purchase_and_activity_errors_use_their_business_text(self):
        self.assert_notify(409, {"error": "purchase_limit_reached"}, "Error_BuyLimit")
        self.assert_notify(409, {"error": "mall_good_limit_reached"}, "Error_BuyLimit")
        self.assert_notify(409, {"error": "activity_closed_today"}, "Error_ActivityNotStart")
        self.assert_notify(409, {"error": "dungeon_done_times"}, "Error_DungeonDoneTimes")
        self.assert_notify(409, {"error": "insufficient_resource"}, "Error_ItemNumNotEnough")

    def test_password_and_unknown_account_are_distinct_login_notifications(self):
        self.assert_notify(401, {"error": "invalid_credentials"}, "Error_PasswordDoNotMatch")
        self.assert_notify(404, {"error": "unknown_account"}, "Error_AccountNotExist")

    def test_expired_access_token_stays_401_for_relogin_patch(self):
        status, payload = client_error_reply(401, {"error": "invalid_access_token"})
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"notify": {"code": "Error_KickReason_NeedLoginAgain"}})

    def test_opaque_bad_request_remains_safe_and_structured(self):
        self.assert_notify(400, {"error": "invalid_request"}, "Error_RequestFailed")
        # Preserve the existing generic fallback for malformed routes without
        # inventing a business message that the client might mistranslate.
        self.assert_notify(400, {"error": "opaque_failure"}, "Window_SaveFail")

    def test_guild_role_present_and_unknown_task_errors_use_client_keys(self):
        self.assert_notify(403, {"error": "guild_permission_denied"}, "Error_GuildHaveNoPermission")
        self.assert_notify(404, {"error": "member_not_found"}, "Error_GuildNotFound")
        self.assert_notify(409, {"error": "role_present_daily_limit"}, "Error_PresentNotFound")
        self.assert_notify(404, {"error": "unknown_task_route"}, "Error_TaskNotFound")

    def test_real_http_egress_keeps_business_notify_and_token_401(self):
        class FakeGame:
            def handle_http(self, method, path, body, content_type, authorization):
                if path.startswith("/fci/mall-trade"):
                    return 409, {"error": "purchase_limit_reached"}
                return 401, {"error": "invalid_access_token"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = FixtureServer(("127.0.0.1", 0), root, JsonlLogger(root / "http.jsonl"))
            server.game = FakeGame()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("POST", "/fci/mall-trade", body=b"{}")
                response = connection.getresponse()
                self.assertEqual(response.status, 509)
                self.assertEqual(json.loads(response.read()), {"notify": {"code": "Error_BuyLimit"}})
                connection.request("GET", "/fci/baseinfo", headers={"Authorization": "Token expired"})
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                self.assertEqual(json.loads(response.read()), {"notify": {"code": "Error_KickReason_NeedLoginAgain"}})
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
