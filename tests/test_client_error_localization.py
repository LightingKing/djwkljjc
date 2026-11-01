"""Guard the server's native error keys against client contract drift.

The APK's localization asset is a protected Unity TextAsset, so the stable
offline contract we can verify in this repository is the shipped
``ErrorMessageBox`` enum plus the decompiled Lua string table references.  A
new server error key must be present in one of those client-owned sources;
otherwise Restifizer would display the raw key.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

from google.protobuf import descriptor_pb2

from server.core.client_errors import client_error_reply


ROOT = Path(__file__).resolve().parents[1]


def _error_message_box_keys() -> set[str]:
    files = descriptor_pb2.FileDescriptorSet()
    files.ParseFromString((ROOT / "data" / "descriptor_set.pb").read_bytes())
    keys: set[str] = set()
    for file in files.file:
        for enum in file.enum_type:
            if enum.name == "ErrorMessageBox":
                keys.update(value.name for value in enum.value)
        for message in file.message_type:
            for enum in message.enum_type:
                if enum.name == "ErrorMessageBox":
                    keys.update(value.name for value in enum.value)
    return keys


def _lua_string_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT.parent.parent / "analysis" / "lua-decompiled").rglob("*.lua"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        keys.update(re.findall(r"(?:WU\.GetString|ShowHintText)\(\s*['\"]([^'\"]+)", text))
    return keys


class ClientErrorLocalizationTests(unittest.TestCase):
    def test_all_server_error_keys_are_client_owned(self):
        enum_keys = _error_message_box_keys()
        lua_keys = _lua_string_keys()
        # Exercise every branch that emits a fixed code.  This deliberately
        # uses the public adapter rather than copying its implementation.
        cases = (
            (409, "purchase_limit_reached"), (409, "activity_closed_today"),
            (409, "dungeon_done_times"), (409, "insufficient_resource"),
            (409, "dungeon_not_active"), (400, "invalid_request"),
            (400, "opaque_failure"), (401, "invalid_access_token"),
            (401, "invalid_credentials"), (404, "unknown_account"),
            (409, "level_too_low"),
            (400, "actor_skill_not_found"), (409, "actor_skill_star_limit"),
            (409, "cultivation_vip_required"), (409, "purchase_token_expired"),
        )
        emitted = {
            client_error_reply(status, {"error": reason})[1]["notify"]["code"]
            for status, reason in cases
        }
        missing = sorted(code for code in emitted if code not in enum_keys and code not in lua_keys)
        self.assertEqual(missing, [], f"server emitted keys absent from client contracts: {missing}")

    def test_item_not_enough_contract_is_the_v9_enum_key(self):
        status, payload = client_error_reply(409, {"error": "insufficient_resource"})
        self.assertEqual(status, 509)
        self.assertEqual(payload["notify"]["code"], "Error_ItemNumNotEnough")
        self.assertIn("Error_ItemNumNotEnough", _error_message_box_keys())
        self.assertIn("Window_ItemNotEnough", _lua_string_keys())


if __name__ == "__main__":
    unittest.main()
