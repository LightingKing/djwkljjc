"""Round-four P0-18 guild administration contracts.

These tests use the same native HTTP/protobuf harness as ``test_guild_client``
and deliberately exercise the routes as the client does.
"""
from __future__ import annotations

import unittest

import test_guild_client as _guild_harness


class GuildP018ContractTests(unittest.TestCase):
    def setUp(self):
        # Reuse the established fixture without inheriting its entire test
        # class (which would also run all legacy guild tests here).
        _guild_harness.GuildClientTests.setUp(self)
        self.native = _guild_harness.GuildClientTests.native.__get__(self)
        self.new_player = _guild_harness.GuildClientTests.new_player.__get__(self)

    def tearDown(self):
        _guild_harness.GuildClientTests.tearDown(self)

    def _create_guild(self):
        status, result = self.native(
            "POST", "/fci/guild/", {"name": "P018公会", "icon": "1"},
            message="ApiGuild",
        )
        self.assertEqual(status, 200, result)
        return result["baseInfo"]["id"]

    def _add_member(self, gid, *, pending=False):
        profile, auth = self.new_player()
        pid = profile["player_id"]
        if pending:
            self.assertEqual(
                self.native(
                    "PUT", f"/fci/guild/{gid}/pending-members/",
                    {"playerId": pid}, auth=auth,
                    message="ApiGuildMemberPending",
                )[0],
                200,
            )
            return profile, auth
        self.assertEqual(
            self.native(
                "PUT", f"/fci/guild/{gid}/invited-members/",
                {"playerId": pid}, message="ApiGuildMemberInvite",
            )[0],
            200,
        )
        self.assertEqual(
            self.native(
                "PUT", f"/fci/guild/{gid}/members/",
                {"playerId": pid}, auth=auth, message="ApiGuildMembers",
            )[0],
            200,
        )
        return profile, auth

    def test_only_president_can_transfer_or_change_vice_and_three_vice_limit(self):
        gid = self._create_guild()
        first, first_auth = self._add_member(gid)
        second, second_auth = self._add_member(gid)
        third, third_auth = self._add_member(gid)
        fourth, fourth_auth = self._add_member(gid)

        for profile in (first, second, third):
            status, result = self.native(
                "PUT", f"/fci/guild/{gid}/vice-president/",
                {"playerId": profile["player_id"]},
                message="ApiGuildVicePresident",
            )
            self.assertEqual(status, 200, result)
        status, result = self.native(
            "PUT", f"/fci/guild/{gid}/vice-president/",
            {"playerId": fourth["player_id"]}, message="ApiGuildVicePresident",
        )
        self.assertNotEqual(status, 200, result)

        # A vice-president cannot promote, demote, or transfer the guild.
        self.assertEqual(
            self.native(
                "POST", f"/fci/guild/{gid}/president/",
                {"newPresident": fourth["player_id"]}, auth=first_auth,
                message="ApiGuildPresident",
            )[0],
            403,
        )
        self.assertEqual(
            self.native(
                "PUT", f"/fci/guild/{gid}/vice-president/",
                {"playerId": fourth["player_id"]}, auth=first_auth,
                message="ApiGuildVicePresident",
            )[0],
            403,
        )
        self.assertEqual(
            self.native(
                "DELETE", f"/fci/guild/{gid}/vice-president/{first['player_id']}",
                auth=first_auth, message="ApiGuildVicePresident",
            )[0],
            403,
        )

    def test_vice_can_kick_only_ordinary_member_and_approval_pushes_29(self):
        gid = self._create_guild()
        vice, vice_auth = self._add_member(gid)
        other_vice, _ = self._add_member(gid)
        ordinary, _ = self._add_member(gid)
        for profile in (vice, other_vice):
            self.assertEqual(
                self.native(
                    "PUT", f"/fci/guild/{gid}/vice-president/",
                    {"playerId": profile["player_id"]},
                    message="ApiGuildVicePresident",
                )[0],
                200,
            )

        # The vice-president may remove a regular member.
        self.assertEqual(
            self.native(
                "DELETE", f"/fci/guild/{gid}/members/{ordinary['player_id']}",
                auth=vice_auth, message="ApiGuildMembers",
            )[0],
            200,
        )
        # A vice-president may not remove another vice-president.
        status, result = self.native(
            "DELETE", f"/fci/guild/{gid}/members/{other_vice['player_id']}",
            auth=vice_auth, message="ApiGuildMembers",
        )
        self.assertNotEqual(status, 200, result)

        pending, pending_auth = self._add_member(gid, pending=True)
        self.pushes.clear()
        status, result = self.native(
            "PUT", f"/fci/guild/{gid}/members/",
            {"playerId": pending["player_id"]}, message="ApiGuildMembers",
        )
        self.assertEqual(status, 200, result)
        self.assertTrue(
            any(account_id == self.profile["account_id"] and kind == 29
                for account_id, kind, _ in self.pushes),
            self.pushes,
        )


if __name__ == "__main__":
    unittest.main()
