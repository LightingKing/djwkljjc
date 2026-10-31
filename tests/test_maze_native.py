"""Maze native request replay. Gameplay progress is driven only by HTTP replies.

The sole direct state fixture is the explicitly versioned legacy migration test.
Battle active payloads mirror SceneFight and FightHelp.recordTeamHp, including
bare FightActiveData, six seat HP, dungeonType12 and the returned stage ID.
"""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.protobuf import json_format, message_factory

from accounts import LocalAccounts
from game import LocalGame
from grants import grant_rewards
from maze import MAZE_CONFIG
from test_welfare_maze_local import guest


class MazeNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = int(datetime(2026, 11, 1, 21, 59, tzinfo=timezone.utc).timestamp())
        self.accounts = LocalAccounts(self.root / 'accounts')
        self.game = LocalGame(self.root / 'game', self.accounts, initial_level=60,
                              initial_actor_ids=[1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009, 1010, 2001, 2002],
                              clock=lambda: self.now)
        self.account, self.auth = guest(self.accounts)
        self.profile = self.game._profile(self.account)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, method, path, payload=None, status=200):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
        code, result = self.game.handle_http(method, path, raw,
            'application/wireformat' if isinstance(payload, bytes) else 'application/json', self.auth)
        self.assertEqual(code, status, (path, result))
        # Exercise the response the protobuf serializer actually sends; map
        # keys remain uint32 after decoding, unlike JSON dict lookalikes.
        if hasattr(result, 'SerializeToString'):
            result = type(result).FromString(result.SerializeToString())
        return result

    def wire(self, name, **fields):
        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))
        return json_format.ParseDict(fields, cls()).SerializeToString()

    def saved_rows(self):
        with closing(self.game._connect()) as conn:
            return tuple(conn.iterdump())

    def item(self, item_id):
        return self.game.systems._state(self.profile).get('items', {}).get(str(item_id), {}).get('count', 0)

    def restart(self):
        self.game = LocalGame(self.root / 'game', self.accounts, clock=lambda: self.now)
        self.profile = self.game._profile(self.account)

    def enter(self, maze_id=1, selected=None):
        self.call('GET', '/fci/Maze/Summary/')
        info = self.call('POST', '/fci/Maze/Enter/', self.wire('ApiMazeEnter', mazeId=maze_id, mazeFloor=1)).maze_info
        actors = self.call('GET', '/fci/Maze/Actor/').select_actors
        if selected is None:
            selected = [a.actor.uid for a in actors[:4]]
        self.call('POST', '/fci/Maze/Actor/', self.wire('ApiMazeActor', actorUids=selected))
        return info

    def group(self, uids):
        slots = list(uids) + [0] * (6 - len(uids))
        return self.call('POST', '/fci/Maze/FightActorGroup/', self.wire('ApiMazeFightActorGroup',
            members=[{'index': index, 'actorUid': uid} for index, uid in enumerate(slots)]))

    def move(self, info):
        start = info.player_cell_index
        end = info.maze_cells[start].next_cell_index
        return self.call('POST', '/fci/Maze/Move', self.wire('ApiMazeMove', startCellIndex=start, endCellIndex=end))

    def summary(self):
        return self.call('GET', '/fci/Maze/Summary/').maze_info

    def trigger(self):
        info = self.summary()
        if info.player_cell_index == 1:
            self.move(info)
        info = self.summary()
        self.assertEqual(info.player_cell_index, 2)
        fight = info.maze_cells[info.player_cell_index].next_cell_index
        moved = self.move(info)
        self.assertEqual(list(moved.way), [info.player_cell_index])
        return fight

    def begin(self):
        return self.call('POST', '/fci/maze-dungeon/', {'cellIndex': self.trigger()})

    def active(self, entered, *, win=1, hp=None, enemy_hp=None, mp=4, sp=2):
        return self.wire('FightActiveData', dungeonType=12, stageID=entered['baseInfo']['dungeonId'],
            gameRound=3, winTeam=win, teamList=[
                {'team': 1, 'energyMp': mp, 'energySp': sp, 'statistic': {'recordHpList': hp or [100, 100, 100, 100, 100, 100]}},
                {'team': 2, 'statistic': {'recordHpList': enemy_hp or [0, 0, 0, 0, 0, 0]}}])

    def finish(self, entered, **kw):
        uid = entered['dungeonUid']
        self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', self.active(entered, **kw))
        return self.call('POST', f'/fci/maze-dungeon/{uid}/balance/', {})

    def test_native_three_floor_loop_uses_numeric_maps_real_dungeons_and_exactly_once_prizes(self):
        info = self.enter()
        self.assertEqual(set(info.maze_cells), {1, 2, 3, 6, 9})
        self.assertTrue(all(type(k) is int for k in info.maze_cells))
        self.assertEqual(info.maze_cells[6].pre_cell_index, 3)
        self.assertTrue(info.maze_cells[1].is_complete)
        self.assertFalse(info.maze_cells[3].is_complete)
        self.assertEqual(list(info.prize_id), [])
        groups = self.group([1])
        self.assertEqual(set(groups.fight_group), {1})
        self.assertEqual([(m.index, m.actor_uid) for m in groups.fight_group[1].members],
                         [(0, 1), (1, 0), (2, 0), (3, 0), (4, 0), (5, 0)])
        for expected_floor, dungeon in enumerate((3, 5, 7), 1):
            info = self.summary()
            self.assertEqual(info.cur_maze_floor, expected_floor)
            buff = self.move(info)
            self.assertEqual((list(buff.way), buff.new_buff, buff.maze_info.maze_map_dimension), ([1, 2], 2, 0))
            self.assertEqual(buff.maze_info.cur_buff[0].left_round, 5)
            entered = self.begin()
            self.assertEqual(entered['baseInfo'], {'dungeonId': dungeon, 'useServerActor': False})
            self.assertNotIn('enemyRecord', entered)
            self.assertEqual(self.call('POST', '/fci/maze-dungeon/', {'cellIndex': 3}), entered)
            uid = entered['dungeonUid']
            active = self.active(entered)
            self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', active)
            self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', active)
            result = self.call('POST', f'/fci/maze-dungeon/{uid}/balance/', {})
            self.assertTrue(result['result']['win'])
            self.assertEqual(result['autoMove']['mazeInfo']['playerCellIndex'], 3)
            self.assertEqual(result['autoMove']['mazeInfo']['curMazeFloor'], expected_floor)
            self.assertEqual(result['autoMove']['mazeInfo']['curBuff'][0]['leftRound'], 4)
            rows = self.saved_rows()
            self.assertEqual(self.call('POST', f'/fci/maze-dungeon/{uid}/balance/', {}), result)
            self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', active)
            self.call('DELETE', f'/fci/maze-dungeon/{uid}')
            self.assertEqual(self.saved_rows(), rows)
            treasure = self.move(self.summary())
            self.assertEqual(len(treasure.maze_info.prize_id), expected_floor)
            self.assertFalse(treasure.res_change)
            exit_move = self.move(self.summary())
            self.assertEqual(exit_move.maze_info.is_maze_over, expected_floor == 3)
            self.assertEqual(exit_move.maze_info.maze_map_dimension, 0 if expected_floor == 3 else 3)
            if expected_floor < 3:
                self.assertEqual((exit_move.maze_info.player_cell_index, exit_move.maze_info.cur_maze_floor), (1, expected_floor + 1))
        before = self.item(60001)
        claimed = self.call('POST', '/fci/Maze/ReceivePrize/', b'null')
        self.assertEqual(sum(r.base_res.count_delta for r in claimed.res_change), 3)
        self.assertEqual(self.item(60001), before + 3)
        self.assertFalse(self.call('POST', '/fci/Maze/ReceivePrize/', {}).res_change)
        self.restart()
        self.assertEqual(self.call('GET', '/fci/Maze/Summary/').maze_id, 0)
        self.assertFalse(self.call('POST', '/fci/Maze/ReceivePrize/', {}).res_change)
        # A new run never accepts a prior run UID, even at the same timestamp.
        self.now += 86400
        self.enter()
        before = self.saved_rows()
        self.call('POST', f'/fci/maze-dungeon/{uid}/balance/', {}, status=404)
        self.assertEqual(self.saved_rows(), before)

    def test_cancel_prompt_and_started_battle_can_retry_without_completing_cell(self):
        self.enter()
        first = self.trigger()
        rows = self.saved_rows()
        self.assertEqual(self.trigger(), first)
        self.assertEqual(self.saved_rows(), rows)
        entered = self.call('POST', '/fci/maze-dungeon/', {'cellIndex': first})
        self.call('DELETE', f"/fci/maze-dungeon/{entered['dungeonUid']}")
        self.call('DELETE', f"/fci/maze-dungeon/{entered['dungeonUid']}")
        self.assertFalse(self.summary().maze_cells[first].is_complete)
        next_run = self.begin()
        self.assertNotEqual(next_run['dungeonUid'], entered['dungeonUid'])
        rows = self.saved_rows()
        self.call('POST', f"/fci/maze-dungeon/{entered['dungeonUid']}/balance/", {}, status=409)
        self.assertEqual(self.saved_rows(), rows)

    def test_completed_cells_allow_native_backtracking_without_repeating_events(self):
        self.enter(selected=[1])
        self.finish(self.begin())
        self.move(self.summary())
        self.assertEqual(list(self.summary().prize_id), [101])
        back = self.call('POST', '/fci/Maze/Move', {'startCellIndex': 6, 'endCellIndex': 1})
        self.assertEqual(list(back.way), [6, 3, 2, 1])
        self.assertFalse(back.maze_info.cur_buff)  # Actual walked cells expire Buff 2.
        again = self.call('POST', '/fci/Maze/Move', {'startCellIndex': 1, 'endCellIndex': 6})
        self.assertEqual(list(again.way), [1, 2, 3, 6])
        self.assertEqual(list(again.maze_info.prize_id), [101])
        self.assertEqual(again.new_buff, 0)
        self.assertFalse(again.maze_info.cur_buff)

    def test_nine_floor_native_difficulty_and_concurrent_settlement_claim(self):
        self.enter(4, selected=[1])
        for floor in range(1, 10):
            self.assertEqual(self.summary().cur_maze_floor, floor)
            entered = self.begin()
            uid = entered['dungeonUid']
            self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', self.active(entered))
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(lambda _: self.call('POST', f'/fci/maze-dungeon/{uid}/balance/', {}), range(24)))
            self.assertTrue(all(r == results[0] for r in results))
            self.move(self.summary())
            moved = self.move(self.summary())
            self.assertEqual(moved.maze_info.is_maze_over, floor == 9)
        before = self.item(60001)
        with ThreadPoolExecutor(max_workers=8) as workers:
            claimed = list(workers.map(lambda _: self.call('POST', '/fci/Maze/ReceivePrize/', {}), range(24)))
        self.assertEqual(sum(len(r.res_change) for r in claimed), 1)
        self.assertEqual(self.item(60001), before + 9)

    def test_loss_preserves_hp_energy_enemy_and_reserve_actor_across_restart(self):
        self.enter(selected=[1, 2, 3, 4])
        self.group([1])
        entered = self.begin()
        result = self.finish(entered, win=2, hp=[0, 0, 0, 0, 0, 0], enemy_hp=[42, 0, 19, 0, 0, 0], mp=7, sp=3)
        self.assertFalse(result['result']['win'])
        self.assertFalse(result['isMazeOver'])
        self.assertEqual(result['autoMove']['mazeInfo']['playerCellIndex'], 2)
        self.restart()
        pool = self.call('GET', '/fci/Maze/Actor/').select_actors
        self.assertEqual([a.actor.uid for a in pool], [1, 2, 3, 4])
        self.assertEqual((pool[0].hp, pool[0].dead), (0, True))
        self.assertGreater(pool[3].hp, 0)
        group = self.call('GET', '/fci/Maze/FightActorGroup/').fight_group[1]
        self.assertEqual((group.energyMp, group.energySp), (7, 3))
        self.group([1])  # Native editor's unchanged/back POST keeps dead slots.
        self.assertTrue(self.call('GET', '/fci/mazeactor/1').dead)
        self.call('POST', '/fci/maze-dungeon/', {'cellIndex': 3}, status=409)
        self.group([4])
        retry = self.begin()
        self.assertEqual(retry['enemyRecord']['hp'], [42, 0, 19, 0, 0, 0])
        self.assertEqual((retry['energyMp'], retry['energySp']), (7, 3))
        self.finish(retry)
        self.assertTrue(self.summary().maze_cells[3].is_complete)
        self.assertTrue(self.call('GET', '/fci/mazeactor/1').dead)

    def test_all_main_actors_dead_ends_run_and_allows_accumulated_prize(self):
        self.enter(selected=[1])
        self.finish(self.begin())
        self.move(self.summary())
        self.move(self.summary())
        entered = self.begin()
        result = self.finish(entered, win=2, hp=[0, 0, 0, 0, 0, 0])
        self.assertTrue(result['isMazeOver'])
        reward = self.call('POST', '/fci/Maze/ReceivePrize/', {})
        self.assertEqual(reward.res_change[0].base_res.count_delta, 1)
        self.assertFalse(self.call('POST', '/fci/Maze/ReceivePrize/', {}).res_change)

    def test_actor_pool_includes_uid_above_eight_then_freezes_and_group_allows_empty_slots(self):
        self.call('GET', '/fci/Maze/Summary/')
        self.call('POST', '/fci/Maze/Enter/', {'mazeId': 1, 'mazeFloor': 1})
        pool = self.call('GET', '/fci/Maze/Actor/').select_actors
        self.assertEqual(len(pool), 12)
        self.assertTrue(all(a.actor.attrs and a.actor.skills for a in pool))
        self.call('POST', '/fci/Maze/Actor/', {'actorUids': [9, 10, 11]})
        group = self.group([9, 0, 0, 11]).fight_group[1]
        self.assertEqual([m.actor_uid for m in group.members], [9, 0, 0, 11, 0, 0])
        self.assertEqual(self.call('GET', '/fci/mazeactor/9').actor.id, 1009)
        self.call('GET', '/fci/mazeactor/1', status=404)
        rows = self.saved_rows()
        for uids in ([1], [9, 10], [9, 10, 11, 12]):
            self.call('POST', '/fci/Maze/Actor/', {'actorUids': uids}, status=409)
            self.assertEqual(self.saved_rows(), rows)
        self.group([])  # Empty native six-slot editor state is legal.
        self.trigger()
        self.call('POST', '/fci/maze-dungeon/', {'cellIndex': 3}, status=409)

    def test_bad_selection_duplicate_character_and_invalid_slot_indices_do_not_write(self):
        self.call('GET', '/fci/Maze/Summary/')
        # Fixture creates a second instance through the real grant system.
        grant_rewards(self.game, self.profile, [{'type': 15, 'id': 1001, 'count': 1}])
        self.call('POST', '/fci/Maze/Enter/', {'mazeId': 1, 'mazeFloor': 1})
        pool = self.call('GET', '/fci/Maze/Actor/').select_actors
        duplicate = next(a.actor.uid for a in pool if a.actor.id == 1001 and a.actor.uid != 1)
        rows = self.saved_rows()
        for selected in ([], [1, 1], [1, duplicate], [999], [11, 12], list(range(1, 10))):
            self.call('POST', '/fci/Maze/Actor/', {'actorUids': selected}, status=409)
            self.assertEqual(self.saved_rows(), rows)
        self.call('POST', '/fci/Maze/Actor/', {'actorUids': [1, 2, 11]})
        rows = self.saved_rows()
        for members in (
            [{'index': 0, 'actorUid': 1}],
            [{'index': 0, 'actorUid': 1}] * 6,
            [{'index': i, 'actorUid': u} for i, u in enumerate([1, 1, 0, 0, 0, 0])],
            [{'index': i, 'actorUid': u} for i, u in enumerate([11, 0, 0, 0, 0, 0])],
            [{'index': i, 'actorUid': u} for i, u in enumerate([1, 0, 0, 0, 11, 0])],
            [{'index': i, 'actorUid': u} for i, u in enumerate([1, 3, 0, 0, 0, 0])],
        ):
            self.call('POST', '/fci/Maze/FightActorGroup/', {'members': members}, status=409)
            self.assertEqual(self.saved_rows(), rows)

    def test_bad_battle_payloads_uid_type_and_stage_never_advance_or_write(self):
        self.enter()
        entered = self.begin()
        uid = entered['dungeonUid']
        rows = self.saved_rows()
        bad = [b'null', b'{}', b'\xff', b'{broken', self.wire('FightActiveData', winTeam=1),
               self.wire('FightActiveData', dungeonType=12, stageID=34001, winTeam=1),
               self.wire('FightActiveData', dungeonType=1, stageID=3, winTeam=1),
               self.wire('FightActiveData', dungeonType=12, stageID=3, winTeam=1),
               self.active(entered, win=0), self.active(entered, hp=[-1] * 6), self.active(entered, hp=[1])]
        for payload in bad:
            self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', payload, status=400)
            self.assertEqual(self.saved_rows(), rows)
        for method, path, data, status in [
            ('POST', f'/fci/maze-dungeon/{uid}/balance/', {}, 409),
            ('POST', f'/fci/maze-dungeon/{uid}/balance/', b'broken', 400),
            ('PATCH', f'/fci/maze-dungeon/{uid+1}/active/', self.active(entered), 404),
            ('DELETE', f'/fci/maze-dungeon/{uid+1}', None, 404),
            ('POST', '/fci/Maze/ReceivePrize/', {}, 409),
            ('POST', '/fci/Maze/Move', {'startCellIndex': 2, 'endCellIndex': 3}, 409),
        ]:
            self.call(method, path, data, status=status)
            self.assertEqual(self.saved_rows(), rows)
        self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', self.active(entered))
        rows = self.saved_rows()
        self.call('DELETE', f'/fci/maze-dungeon/{uid}', status=409)
        self.call('PATCH', f'/fci/maze-dungeon/{uid}/active/', self.active(entered, win=2), status=409)
        self.assertEqual(self.saved_rows(), rows)
        self.restart()
        self.assertTrue(self.call('POST', f'/fci/maze-dungeon/{uid}/balance/', {})['win'])

    def test_ticket_supply_beijing_day_monday_week_caps_and_restart(self):
        self.call('GET', '/fci/Maze/Summary/')
        self.assertEqual((self.item(80007), self.item(80008)), (1, 3))
        rows = self.saved_rows()
        self.game.maze.refresh_daily_items(self.profile)
        self.assertEqual(self.saved_rows(), rows)
        self.enter(4)
        self.assertEqual(self.item(80008), 0)
        self.now += 120  # Monday 05:59 -> 06:01 Beijing.
        self.game.maze.refresh_daily_items(self.profile)
        self.assertEqual((self.item(80007), self.item(80008)), (2, 3))
        self.restart()
        self.call('GET', '/fci/Maze/Summary/')
        self.assertEqual((self.item(80007), self.item(80008)), (2, 3))
        self.now += 20 * 86400
        self.game.maze.refresh_daily_items(self.profile)
        self.assertEqual((self.item(80007), self.item(80008)), (3, 3))
        # Clock rollback cannot trigger the same supply period twice.
        self.now -= 86400
        self.assertEqual(self.game.maze.refresh_daily_items(self.profile), [])

    def test_four_native_configs_costs_limits_and_level_gate(self):
        for maze_id, config in MAZE_CONFIG.items():
            with self.subTest(maze_id=maze_id), tempfile.TemporaryDirectory() as tmp:
                game = LocalGame(Path(tmp) / 'g', self.accounts, initial_level=config['unlockLevel'], clock=lambda: self.now)
                account, auth = guest(self.accounts)
                profile = game._profile(account)
                grant_rewards(game, profile, [{'type': 11, 'id': config['ticket'], 'count': 3}])
                status, entered = game.handle_http('POST', '/fci/Maze/Enter/',
                    json.dumps({'mazeId': maze_id, 'mazeFloor': 1}).encode(), authorization=auth)
                self.assertEqual(status, 200)
                self.assertEqual(entered.res_change[0].base_res.count_delta, -config['cost'])
                self.assertEqual(entered.maze_info.cur_maze_floor, 1)
            with self.subTest(locked=maze_id), tempfile.TemporaryDirectory() as tmp:
                game = LocalGame(Path(tmp) / 'g', self.accounts, initial_level=config['unlockLevel'] - 1, clock=lambda: self.now)
                account, auth = guest(self.accounts)
                game._profile(account)
                with closing(game._connect()) as conn:
                    rows = tuple(conn.iterdump())
                # Isolate this first-request rollback from the dispatcher's
                # unrelated eager event/social default-state initialization.
                status, error = game.maze.handle_http('POST', '/fci/Maze/Enter/',
                    json.dumps({'mazeId': maze_id, 'mazeFloor': 1}).encode(), game._profile(account))
                self.assertEqual((status, error['error']), (409, 'maze_level_locked'))
                with closing(game._connect()) as conn:
                    self.assertEqual(tuple(conn.iterdump()), rows)
        self.call('GET', '/fci/Maze/Summary/')
        rows = self.saved_rows()
        self.call('POST', '/fci/Maze/Enter/', {'mazeId': 3, 'mazeFloor': 1}, status=409)
        self.assertEqual(self.saved_rows(), rows)

    def test_legacy_invalid_run_refunds_once_preserving_other_features_and_assets(self):
        state = self.game.profile_features._state(self.profile)
        state['untouched-test-feature'] = {'marker': 17}
        state['maze'] = {'mazeId': 1, 'floor': 2, 'dungeonUid': 123, 'received': False, 'prizeId': [101]}
        self.game.profile_features._save(self.profile, state)
        before = self.item(80007)
        summary = self.call('GET', '/fci/Maze/Summary/')
        self.assertEqual(summary.maze_id, 0)
        self.assertEqual(self.item(80007), before + 2)  # One refund + first daily supply.
        state = self.game.profile_features._state(self.profile)
        self.assertEqual(state['untouched-test-feature'], {'marker': 17})
        self.assertEqual(state['maze']['migration']['refundedTicket'], 1)
        rows = self.saved_rows()
        self.restart()
        self.call('GET', '/fci/Maze/Summary/')
        self.assertEqual(self.saved_rows(), rows)
        self.call('POST', '/fci/Maze/ReceivePrize/', {}, status=409)

    def test_ticket_and_reward_atomic_rollback_and_no_failed_push(self):
        self.call('GET', '/fci/Maze/Summary/')
        pushes = []
        self.game.base_push_notifier = lambda *args: pushes.append(args)
        rows = self.saved_rows()
        with patch.object(self.game.maze, '_save', side_effect=RuntimeError('injected ledger failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected ledger failure'):
                self.call('POST', '/fci/Maze/Enter/', {'mazeId': 1, 'mazeFloor': 1})
        self.assertEqual(self.saved_rows(), rows)
        self.assertEqual(pushes, [])
        self.enter(selected=[1])
        self.finish(self.begin())
        self.move(self.summary())
        self.move(self.summary())
        self.finish(self.begin(), win=2, hp=[0] * 6)
        rows = self.saved_rows()
        pushes.clear()
        with patch.object(self.game.maze, '_save', side_effect=RuntimeError('injected ledger failure')):
            with self.assertRaises(RuntimeError):
                self.call('POST', '/fci/Maze/ReceivePrize/', {})
        self.assertEqual(self.saved_rows(), rows)
        self.assertEqual(pushes, [])
        self.assertTrue(self.call('POST', '/fci/Maze/ReceivePrize/', {}).res_change)
        self.assertEqual(len(pushes), 1)

    def test_unrelated_routes_and_invalid_requests_do_not_create_maze_state(self):
        rows = self.saved_rows()
        self.assertIsNone(self.game.maze.handle_http('GET', '/fci/unrelated/', None, self.profile))
        self.assertEqual(self.saved_rows(), rows)
        for method, path, payload, status in [
            ('POST', '/fci/Maze/Enter/', b'null', 400),
            ('POST', '/fci/Maze/Enter/', b'\xff', 400),
            ('POST', '/fci/Maze/Enter/', {'mazeId': 1}, 400),
            ('POST', '/fci/Maze/Enter/', {'mazeId': 0, 'mazeFloor': 1}, 400),
            ('POST', '/fci/Maze/Enter/', {'mazeId': 1, 'mazeFloor': 2}, 400),
            ('POST', '/fci/Maze/Summary/', None, 405),
            ('POST', '/fci/maze-dungeon/', {'cellIndex': 3}, 409),
        ]:
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
            result_status, _ = self.game.maze.handle_http(method, path, raw, self.profile)
            self.assertEqual(result_status, status)
            self.assertEqual(self.saved_rows(), rows)


if __name__ == '__main__':
    unittest.main()
