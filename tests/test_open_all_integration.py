"""Login projection and native dispatcher contracts for reopened activities."""
import unittest

import test_guild_client as harness


class OpenAllIntegrationTests(unittest.TestCase):
    def setUp(self):
        harness.GuildClientTests.setUp(self)
        self.addCleanup(self.temp.cleanup)
        self.native = harness.GuildClientTests.native.__get__(self)

    def test_item_cap_history_is_returned_after_stack_is_consumed(self):
        state = self.game.systems._state(self.profile)
        state['items']['2013'] = {'count': 0, 'historyGetCount': 1060}
        self.game.systems._save(self.profile, state)
        for path in ('/fci/item/2013/', '/fci/item/?uids=2013', '/fci/item/'):
            with self.subTest(path=path):
                status, result = self.native('GET', path)
                self.assertEqual(status, 200, result)
                if isinstance(result, list):
                    row = result[0]
                elif 'items' in result:
                    row = next(row for row in result['items'] if row['id'] == 2013)
                else:
                    row = result
                self.assertEqual(row['count'], 0)
                self.assertEqual(row['historyGetCount'], 1060)

    def test_rented_guild_actor_survives_native_player_data_reload(self):
        status, created = self.native('POST', '/fci/guild/', {'name': '重登租赁', 'icon': '1'})
        self.assertEqual(status, 200, created)
        path = f"/fci/guild/{created['baseInfo']['id']}/rent-actor/"
        status, available = self.native('GET', path)
        self.assertEqual(status, 200, available)
        uid = available['rentActorInfo'][0]['actor']['uid']
        status, rented = self.native('POST', path, {'actorGuildUid': uid, 'actorKind': 1}, message='ApiGuildRentActor')
        self.assertEqual(status, 200, rented)
        status, player = self.native('GET', '/fci/guild-player/')
        self.assertEqual(status, 200, player)
        self.assertEqual(player['rentedActors'], rented['rentedActorInfo'])
        self.assertEqual(player['guildDungeonActorGroupId'], 1)
        self.assertTrue(player['actorGroupInfo']['actorGroups']['1']['members'])
