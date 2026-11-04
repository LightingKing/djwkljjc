"""Client task-claim refresh contract.

The task widget used to remove/reinsert only the clicked row after a claim.
That local write can race with ScTasksChange and overwrite another pending row.
The patched widget must refresh the authoritative task snapshot instead.
"""

from pathlib import Path
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from accounts import LocalAccounts
from game import LocalGame
from tasks import LocalTasks


def _guest(accounts):
    first = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    token = "AccountToken " + first[1]["accountToken"]
    entry = accounts.handle("POST", "/EntryReg/", b'{"entryType":"guest"}', "application/json", token)[1]["entry"]["entry"]
    accounts.handle("POST", "/Account/", b'{"entryType":"guest"}', "application/json", token)
    second = accounts.handle("POST", "/AccountSession/", b"", "application/wireformat")
    second_token = "AccountToken " + second[1]["accountToken"]
    auth_body = b"\x0a" + bytes((len(entry),)) + entry.encode() + b"\x12\x05guest"
    auth = accounts.handle("POST", "/Auth/", auth_body, "application/wireformat", second_token)
    return accounts.verify_access_token(auth[1]["tokenAccess"])


def _ts(*args):
    return int(datetime(*args, tzinfo=timezone(timedelta(hours=8))).timestamp())


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "client-patch" / "hot-update" / "sources" / "widgettaskslot-v24.lua"


def test_task_claim_refreshes_authoritative_snapshot_without_local_list_rewrite():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'this:GameRequest("fci/task/"):SyncInput(false):Get(TU.InitData)' in source
    assert "table.remove(taskList, index)" not in source


def test_task_claim_fix_candidate_overlays_the_published_v22_target():
    config = json.loads((ROOT / "client-patch" / "hot-update" / "modules-task-fix-v23.json").read_text(encoding="utf-8"))
    module = next(item for item in config["modules"] if item["target"].endswith("window/task/widgettaskslot"))
    assert config["patchVersion"] == 23
    assert module["source"] == "sources/widgettaskslot-v24.lua"
    assert module["originalSha256"] == "42c91a9b311a0e847798bd9066394b001c33b82e5435ee197fa90be256e82faf"


class TaskClaimServerProjectionTests(unittest.TestCase):
    def test_claim_push_keeps_another_completed_daily_task(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            accounts = LocalAccounts(root / "accounts")
            game = LocalGame(root / "game", accounts)
            account = _guest(accounts)
            profile = dict(game._profile(account))
            profile["level"] = 1
            tasks = LocalTasks(game, clock=lambda: _ts(2026, 10, 29, 12, 30))
            tasks.handle_http("GET", "/fci/task/", None, profile)
            # Five normal wins complete daily task 3; five activity wins
            # complete daily task 4. Both are finished before either claim.
            for _ in range(5):
                tasks.on_battle_win(profile, 1, 6)
                tasks.on_battle_win(profile, 50001, 0, mode="activity")
            before = tasks.snapshot(profile)
            self.assertTrue(next(row for row in before["tasks"] if row["id"] == 3)["isDone"])
            self.assertTrue(next(row for row in before["tasks"] if row["id"] == 4)["isDone"])
            calls = []
            tasks.notifier = lambda _profile, payload: calls.append(payload)
            self.assertEqual(tasks.handle_http("POST", "/fci/task/3/reward/", None, profile)[0], 200)
            pushed = calls[-1]
            other = next(row for row in pushed["tasks"] if row["id"] == 4)
            self.assertTrue(other["isDone"])
            self.assertFalse(other["rewardGot"])
            self.assertNotIn(4, pushed["delTasks"])
