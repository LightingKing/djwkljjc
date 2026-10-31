from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
from game import LocalGame
import test_systems


class RolePresentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        accounts = LocalAccounts(Path(self.temp.name) / "accounts")
        self.game = LocalGame(Path(self.temp.name) / "game", accounts)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None):
        body = payload if isinstance(payload, (bytes, bytearray)) else (
            None if payload is None else json.dumps(payload).encode()
        )
        return self.game.handle_http(method, path, body, "application/json", self.auth)

    def test_eligible_present_uses_uint_id_and_native_empty_claim(self):
        day = int(self.game._now() // 86400)
        account_id = int(self.account["accountId"])
        role_id = next(role for role in range(1, 500)
                       if (account_id * 31 + day * 17 + role * 13) % 100 < 70)
        notepad = [{"roleId": role_id, "favourLevel": 9, "favour": 0}]
        with patch.object(self.game.profile_features, "role_notepads", return_value=notepad):
            status, redpoint = self.call("GET", "/fci/redpoint/data")
            self.assertEqual(status, 200)
            pending = redpoint["rolePresentRedpoint"]["presents"]
            self.assertTrue(pending)
            self.assertIsInstance(pending[0]["presentId"], int)

            status, result = self.call("GET", "/fci/RolePresent/")
            self.assertEqual(status, 200)
            self.assertEqual(result.DESCRIPTOR.full_name, "ApiRolePresent.result")
            self.assertEqual(result.presents.role_id, role_id)
            self.assertIsInstance(result.presents.present_id, int)

            # The native flow selects through ApiPresentTargetActor first and
            # then posts an empty body; the claim must read that durable value.
            status, selected = self.call("POST", "/fci/present-target-actor/",
                                         {"presentTargetActor": role_id})
            self.assertEqual(status, 200)
            status, claimed = self.call("POST", "/fci/RolePresent/", b"")
            self.assertEqual(status, 200)
            self.assertEqual(claimed.DESCRIPTOR.full_name, "ApiRolePresent.result")
            self.assertEqual(claimed.presents.present_id, role_id)
            self.assertTrue(claimed.res_change)

            status, _ = self.call("POST", "/fci/RolePresent/", b"")
            self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main()
