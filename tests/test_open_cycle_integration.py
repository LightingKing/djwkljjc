"""Authentication and login trigger earned cycle mail without manual maintenance."""
import unittest
import test_hidden_challenge as harness


class OpenCycleIntegrationTests(unittest.TestCase):
    def setUp(self):
        harness.HiddenChallengeTests.setUp(self)
        self.addCleanup(self.temp.cleanup)
        self.module = self.game.hidden_challenge
        uids = self.game.dungeons._fight_team(self.profile, 1)[1]
        self.module.begin_run(self.profile, 901, 21, uids, {})
        self.module.record_win(self.profile, 901)
        self.now = 1793570400 + 604800

    def earned_mails(self):
        return [m for m in self.game.systems._state(self.profile)['mails']
                if m['title'] == '练度竞赛赛季排名奖励']

    def test_native_activity_read_delivers_due_rank_mail_once(self):
        for _ in range(2):
            status, _ = self.game.handle_http('GET', '/fci/activity/', authorization=self.auth)
            self.assertEqual(status, 200)
            self.assertEqual(len(self.earned_mails()), 1)
        self.assertEqual(self.earned_mails()[0]['attachments'], [{'type': 8, 'id': 70001, 'count': 1, 'param': 0}])

    def test_login_bootstrap_delivers_due_rank_mail_once(self):
        for _ in range(2):
            self.game.login_messages(self.account)
            self.assertEqual(len(self.earned_mails()), 1)
