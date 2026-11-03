"""Native activity entries for the explicitly authorized local reopening.

Client ActivityManager keys entries by activityId; autumn checks 38 and 39
together. These are entry contracts, separate from each mode's battle tests.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import json
import tempfile
import unittest

from accounts import LocalAccounts
from game import LocalGame
import test_systems as systems_tests


class OpenAllScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [1793660400]
        # The clock deliberately advances a year; keep this schedule fixture's
        # access token valid instead of testing authentication expiry here.
        self.accounts = LocalAccounts(self.root / 'accounts', clock=lambda: self.now[0], access_ttl_seconds=400 * 86400)
        self.game = LocalGame(self.root / 'game', self.accounts, clock=lambda: self.now[0])
        self.account, self.auth = systems_tests.LocalSystemsTests._guest(self.accounts)

    def entries(self):
        status, rows = self.game.handle_http('GET', '/fci/activity/', authorization=self.auth)
        self.assertEqual(status, 200)
        return rows

    def test_all_requested_activity_families_have_native_entries(self):
        rows = self.entries()
        expected = {27, 39, 40, 38, 14, 19, 13, 54, 51, 45, 49, 50, 42}
        self.assertEqual(expected - {row['type'] for row in rows}, set())
        ids = [row['activityId'] for row in rows]
        self.assertEqual(len(ids), len(set(ids)), 'ActivityManager requires globally unique ids')
        self.assertTrue({2, 3, 4, 5, 10, 17, 18, 20, 23, 25, 30, 46} <= {row['type'] for row in rows})

    def test_autumn_crane_and_stage_entries_remain_available_together(self):
        for days in (0, 1, 6, 27, 28, 365):
            self.now[0] = 1793660400 + days * 86400
            rows = [row for row in self.entries() if row['type'] in (38, 39, 40)]
            self.assertEqual({row['type'] for row in rows}, {38, 39, 40})
            self.assertEqual({row['activityId'] for row in rows if row['type'] == 39}, {39000, 39001, 39002, 39003})
            self.assertEqual({row['activityId'] for row in rows if row['type'] == 39 and row['category'] == 1}, {39001, 39002, 39003})
            self.assertEqual({row['activityId'] for row in rows if row['type'] == 40}, {40001, 40002})
            for row in rows:
                self.assertLessEqual(row['timestampStart'], self.now[0])
                self.assertTrue(row['timestampEnd'] == 0 or self.now[0] < row['timestampEnd'])

    def test_carnival_uses_client_category_and_unavailable_ads_stay_closed(self):
        rows = self.entries()
        carnival = [row for row in rows if row['type'] == 51]
        self.assertTrue(carnival)
        self.assertEqual({row['activityId']: row['category'] for row in carnival}, {51001: 4, 51002: 5, 51003: 5})
        christmas = [row for row in rows if row['type'] == 54]
        self.assertEqual({row['activityId'] for row in christmas if row['category'] == 5}, set(range(54001, 54007)))
        self.assertEqual([row['activityId'] for row in christmas if row['category'] == 4], [54000])
        ads = {row['activityId'] for row in rows if row['type'] == 19}
        self.assertTrue(ads)
        self.assertEqual(ads & set(range(19022, 19031)), set())

    def test_challenge_rotates_all_original_stage_sets_without_duplicate_tabs(self):
        anchor = int(datetime(2026, 11, 2, 6, tzinfo=timezone(timedelta(hours=8))).timestamp())
        table = json.loads((Path(__file__).resolve().parents[1] / 'data/client-tables/ChallengeModeDungeon.json').read_text(encoding='utf-8'))['rows']
        by_id = {row['activityId']: row['dungeonId'] for row in table}
        seen = set()
        for week in range(10):
            self.now[0] = anchor + week * 7 * 86400
            activities = [row for row in self.entries() if row['type'] == 45]
            self.assertEqual(len(activities), 1)
            self.assertEqual(activities[0]['activityId'], 45001 + week)
            self.assertLessEqual(len(by_id[activities[0]['activityId']]), 4)
            seen.update(by_id[activities[0]['activityId']])
        self.assertEqual(seen, {did for row in table for did in row['dungeonId']})
        self.assertEqual(len(seen), 30)


if __name__ == '__main__':
    unittest.main()
