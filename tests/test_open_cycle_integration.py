"""Valid collection reads and login trigger cycle mail without unrelated writes."""
import unittest
from contextlib import closing
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

    def test_invalid_unrelated_request_does_not_settle_due_rewards(self):
        # The fixture jumps one week after profile creation; establish the
        # existing resource-recovery baseline before inspecting cycle writes.
        self.profile=self.game._profile(self.account)
        with closing(self.game._connect()) as conn:
            before=tuple(conn.iterdump())
        status,_=self.game.handle_http('GET','/fci/tournament/info/5/99999/',authorization=self.auth)
        self.assertEqual(status,404)
        self.assertEqual(self.earned_mails(),[])
        with closing(self.game._connect()) as conn:
            self.assertEqual(tuple(conn.iterdump()),before)

    def test_cycle_checks_without_participation_do_not_create_empty_ledgers(self):
        from test_systems import LocalSystemsTests
        account,_=LocalSystemsTests._guest(self.accounts)
        profile=self.game._profile(account)
        with closing(self.game._connect()) as conn:
            before=tuple(conn.iterdump())
        for _ in range(2):
            self.game.hidden_challenge.settle_due(profile)
            self.game.guild_dungeons.settle_due(profile)
            self.now+=1
        with closing(self.game._connect()) as conn:
            self.assertEqual(tuple(conn.iterdump()),before)

    def test_native_mail_list_delivers_due_rewards_before_listing(self):
        for _ in range(2):
            status,result=self.game.handle_http('GET','/fci/mail/',authorization=self.auth)
            self.assertEqual(status,200)
            self.assertEqual(len(self.earned_mails()),1)
            self.assertTrue(any(m['title']=='练度竞赛赛季排名奖励' for m in result['mails']))
