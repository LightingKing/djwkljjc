"""Native 2.0.4 Maze loop with transactional, restart-safe local rules.

MazeInfo costs/limits and Buff 2 are from xlsx__mazeinfo / xlsx__mazebuff.
The five-cell route, existing dungeon IDs 3/5/7, prize 101 -> item 60001,
80007 daily +1/cap3 and 80008 Monday weekly +3/cap3 are local rules: the
original map/drop/supply tables are unavailable. Maze buffs are applied by
the native client, never applied a second time to server actor attributes.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory
from google.protobuf.message import DecodeError
from server.core.reset import policy_for

try:
    from . import actors as actor_store
    from .grants import grant_rewards
except ImportError:  # pragma: no cover
    import actors as actor_store
    from grants import grant_rewards

Body = bytes | bytearray | str | Mapping[str, Any] | None
Response = tuple[int, Any]
ACTOR_KIND = {int(r['id']): int(r['kind']) for r in json.loads(
    (Path(__file__).parent / 'data/client-tables/ActorConfig.json').read_text(encoding='utf-8'))['rows']}
MAZE_CONFIG = {
    1: {'unlockLevel': 30, 'maxActorCount': 8, 'maxFloor': 3, 'ticket': 80007, 'cost': 1},
    2: {'unlockLevel': 45, 'maxActorCount': 12, 'maxFloor': 5, 'ticket': 80007, 'cost': 2},
    3: {'unlockLevel': 60, 'maxActorCount': 15, 'maxFloor': 5, 'ticket': 80007, 'cost': 3},
    4: {'unlockLevel': 60, 'maxActorCount': 18, 'maxFloor': 9, 'ticket': 80008, 'cost': 3},
}
PATH = (1, 2, 3, 6, 9)
DUNGEONS = (3, 5, 7)
ROUTES = {
    'summary': ('GET',), 'enter': ('POST',), 'actor': ('GET', 'POST'),
    'fightactorgroup': ('GET', 'POST'), 'move': ('POST',), 'receiveprize': ('POST',),
}
REQUESTS = {'enter': 'ApiMazeEnter', 'actor': 'ApiMazeActor',
            'fightactorgroup': 'ApiMazeFightActorGroup', 'move': 'ApiMazeMove'}


class MazeError(Exception):
    def __init__(self, error: str, status: int = 409):
        self.error, self.status = error, status
        super().__init__(error)


def _dict(message: Any) -> dict[str, Any]:
    return json_format.MessageToDict(message, always_print_fields_with_no_presence=True)


class LocalMaze:
    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._clock = clock or game._now

    def _message(self, name: str):
        return message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()

    def _typed(self, name: str, fields: Mapping[str, Any]):
        return json_format.ParseDict(dict(fields), self._message(name))

    def _request(self, body: Body, name: str | None = None, *, empty: bool = False):
        """Decode native bare protobuf or JSON; malformed data never means a win."""
        value = body
        if isinstance(body, (bytes, bytearray, str)):
            raw = body.encode('utf-8') if isinstance(body, str) else bytes(body)
            try:
                value = json.loads(raw.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                if not name:
                    raise MazeError('maze_invalid_request', 400)
                message = self._message(name)
                try:
                    message.ParseFromString(raw)
                except DecodeError as exc:
                    raise MazeError('maze_invalid_request', 400) from exc
                if not message.ListFields():
                    raise MazeError('maze_invalid_request', 400)
                return message
        if value is None and empty:
            value = {}
        if not isinstance(value, Mapping):
            raise MazeError('maze_invalid_request', 400)
        if not name:
            return dict(value)
        try:
            return self._typed(name, value)
        except (ValueError, TypeError, json_format.ParseError) as exc:
            raise MazeError('maze_invalid_request', 400) from exc

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {'version': 2, 'mazeId': 0, 'floor': 0, 'playerCellIndex': 1,
                'completed': [1], 'selectedActors': [], 'pool': {}, 'group': [0] * 6,
                'isMazeOver': False, 'received': False, 'prizeId': [], 'buffs': [],
                'triggerCellIndex': 0, 'currentBattle': 0, 'battles': {},
                'lastDungeonUid': 0, 'energyMp': -1, 'energySp': -1, 'enemyHp': [], 'supply': {}}

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        state = self.game.profile_features._state(profile)
        old = state.get('maze')
        if not isinstance(old, dict) or old.get('version') != 2:
            maze = self._empty_state()
            if isinstance(old, dict):
                # v1 had invalid dungeon 34001, unearned prize101 and no
                # battle evidence. Reset only Maze, preserve every other ledger,
                # refund the single ticket v1 actually charged exactly once.
                refund = bool(old.get('mazeId')) and not old.get('received')
                maze['migration'] = {'fromVersion': old.get('version', 1),
                                     'at': int(self._clock()), 'refundedTicket': 1 if refund else 0,
                                     'oldMazeId': old.get('mazeId', 0), 'reason': 'invalid_v1_run'}
                if refund:
                    changes = grant_rewards(self.game, profile, [{'type': 11, 'id': 80007, 'count': 1}])
                    self._notify(profile, changes)
            state['maze'] = maze
            self._save(profile, state)
        return state

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        self.game.profile_features._save(profile, state)

    def _notify(self, profile: Mapping[str, Any], changes: list[dict[str, Any]]) -> None:
        ids = tuple(dict.fromkeys(c['baseRes']['id'] for c in changes if c.get('baseRes', {}).get('type') == 11))
        notifier = getattr(self.game, 'base_push_notifier', None)
        if ids and notifier:
            self.game.after_commit(lambda: notifier(profile, 3, ids))

    def _refresh(self, profile: Mapping[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
        # Shared Beijing 06:00 / Monday 06:00 boundaries; accrue only to cap.
        policy, now = policy_for(self.game), int(self._clock())
        day, week = policy.day_index(now), policy.week_index(now)
        supply = state['maze']['supply']
        bag = self.game.systems._state(profile)
        rewards = []
        changed = False
        for rid, level, period, amount in ((80007, 30, day, 1), (80008, 60, week, 3)):
            if int(profile.get('level', 1)) < level:
                continue
            last = supply.get(str(rid))
            periods = 1 if last is None else max(0, period - int(last))
            if not periods:
                continue
            count = int(bag.get('items', {}).get(str(rid), {}).get('count', 0))
            delta = max(0, min(periods * amount, 3 - count))
            if delta:
                rewards.append({'type': 11, 'id': rid, 'count': delta})
            supply[str(rid)] = period
            changed = True
        changes = grant_rewards(self.game, profile, rewards)
        if changed:
            self._save(profile, state)
        self._notify(profile, changes)
        return changes

    def refresh_daily_items(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Login/reset hook; caller can include returned changes in its response."""
        with self.game.atomic_mutation():
            return self._refresh(profile, self._state(profile))

    @staticmethod
    def _cells(maze: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
        cells = {}
        dungeon = DUNGEONS[(max(1, maze['floor']) - 1) % len(DUNGEONS)]
        for index, node in enumerate(PATH):
            cells[node] = {'nodeIndex': node, 'preCellIndex': PATH[index - 1] if index else 0,
                           'nextCellIndex': PATH[index + 1] if index + 1 < len(PATH) else 0,
                           'branchIndex': 0, 'isComplete': node in maze['completed'],
                           'eventType': 2 if node == 2 else 1 if node == 3 else 3,
                           'eventId': 2 if node == 2 else dungeon if node == 3 else 101,
                           'eventParam': '', 'preConditionId': 0}
        return cells

    def _info(self, maze: Mapping[str, Any], *, full: bool = False) -> dict[str, Any]:
        result = {'curMazeFloor': maze['floor'], 'playerCellIndex': maze['playerCellIndex'],
                  'isMazeOver': maze['isMazeOver'], 'curBuff': deepcopy(maze['buffs']),
                  'prizeId': list(maze['prizeId']), 'mazeMapDimension': 3 if full else 0,
                  'mazeMapBuffId': 0}
        if full:
            result['mazeCells'] = self._cells(maze)
        return result

    def _actors(self, profile: Mapping[str, Any], maze: Mapping[str, Any]) -> list[dict[str, Any]]:
        if maze['selectedActors']:
            return [deepcopy(maze['pool'][str(uid)]) for uid in maze['selectedActors']]
        bag = self.game.systems._state(profile)
        result = []
        for record in actor_store.ordered(profile, bag):
            actor = self.game.systems._st_actor_json(record, profile, bag, 0)
            hp = next((int(a['value']) for a in actor['attrs'] if a['type'] == 1), 1)
            result.append({'actor': actor, 'hp': max(1, hp), 'dead': False})
        return result

    @staticmethod
    def _active(maze: Mapping[str, Any], *, selected: bool = True) -> None:
        if not maze['mazeId'] or maze['isMazeOver']:
            raise MazeError('maze_not_active')
        if selected and not maze['selectedActors']:
            raise MazeError('maze_actor_selection_required')

    @staticmethod
    def _idle(maze: Mapping[str, Any]) -> None:
        if maze['currentBattle']:
            raise MazeError('maze_dungeon_active')

    @staticmethod
    def _kind(entry: Mapping[str, Any]) -> int:
        return ACTOR_KIND.get(int(entry['actor']['id']), 0)

    def _group(self, maze: Mapping[str, Any]):
        group = {'members': [{'index': i, 'actorUid': uid} for i, uid in enumerate(maze['group'])]}
        for name in ('energySp', 'energyMp'):
            if maze[name] >= 0:
                group[name] = maze[name]
        return self._typed('ApiMazeFightActorGroup.result', {'fightGroup': {1: group}})

    def _validate_group(self, maze: Mapping[str, Any], group: list[int], *, fight: bool = False) -> None:
        occupied = [uid for uid in group if uid]
        if len(group) != 6 or len(occupied) != len(set(occupied)):
            raise MazeError('maze_invalid_fight_group')
        for index, uid in enumerate(group):
            if not uid:
                continue
            entry = maze['pool'].get(str(uid))
            if entry is None or self._kind(entry) != (1 if index < 3 else 2):
                raise MazeError('maze_invalid_fight_group')
            if index >= 3 and not group[index - 3]:
                raise MazeError('maze_support_without_main')
            # The native editor POSTs unchanged slots when backing out, even
            # after a defeat. Preserve dead slots there; Fight must reject them.
            if fight and (entry['dead'] or entry['hp'] <= 0):
                raise MazeError('maze_actor_dead')
        if fight and not any(group[:3]):
            raise MazeError('maze_main_actor_required')

    @staticmethod
    def _walk(maze: dict[str, Any], node: int) -> None:
        maze['playerCellIndex'] = node
        if node not in maze['completed']:
            maze['completed'].append(node)
        maze['buffs'] = [{**buff, 'leftRound': buff['leftRound'] - 1}
                         for buff in maze['buffs'] if buff['leftRound'] > 1]

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = (urlsplit(path or '').path or '/').rstrip('/').lower()
        method = method.upper()
        action = route.removeprefix('/fci/maze/') if route.startswith('/fci/maze/') else ''
        actor_match = re.fullmatch(r'/fci/mazeactor/(\d+)', route)
        battle_match = re.fullmatch(r'/fci/maze-dungeon(?:/(\d+)(?:/(active|balance))?)?', route)
        if action not in ROUTES and not actor_match and not battle_match:
            return None
        allowed = ROUTES[action] if action in ROUTES else ('GET',) if actor_match else (
            ('POST',) if not battle_match[1] else ('PATCH',) if battle_match[2] == 'active' else
            ('POST',) if battle_match[2] == 'balance' else ('DELETE',))
        if method not in allowed:
            return 405, {'error': 'method_not_allowed'}
        try:
            with self.game.atomic_mutation():
                state = self._state(profile)
                maze = state['maze']
                if actor_match:
                    uid = int(actor_match[1])
                    entry = next((a for a in self._actors(profile, maze) if int(a['actor']['uid']) == uid), None)
                    if entry is None:
                        raise MazeError('maze_actor_not_found', 404)
                    return 200, self._typed('DbMaze.SelectActor', entry)
                if battle_match:
                    return self._battle(method, battle_match, body, profile, state)
                request = self._request(body, REQUESTS[action]) if method == 'POST' and action in REQUESTS else None
                if action == 'summary':
                    self._refresh(profile, state)
                    return 200, self._typed('ApiMazeSummary.result', {'mazeId': maze['mazeId'],
                        'isSelectActor': bool(maze['selectedActors']), 'mazeInfo': self._info(maze, full=True)})
                if action == 'enter':
                    config = MAZE_CONFIG.get(request.maze_id)
                    if config is None or request.maze_floor != 1:
                        raise MazeError('maze_invalid_entry', 400)
                    if int(profile.get('level', 1)) < config['unlockLevel']:
                        raise MazeError('maze_level_locked')
                    if maze['mazeId'] and not maze['received']:
                        raise MazeError('maze_reward_unclaimed' if maze['isMazeOver'] else 'maze_already_active')
                    self._refresh(profile, state)
                    bag = self.game.systems._state(profile)
                    ticket = bag.setdefault('items', {}).setdefault(str(config['ticket']), {'count': 0})
                    if ticket['count'] < config['cost']:
                        raise MazeError('maze_ticket_not_enough')
                    ticket['count'] -= config['cost']
                    self.game.systems._save(profile, bag)
                    changes = [{'baseRes': {'type': 11, 'id': config['ticket'], 'param': 0,
                                            'count': ticket['count'], 'countDelta': -config['cost']}}]
                    new = self._empty_state()
                    for key in ('supply', 'lastDungeonUid', 'migration'):
                        if key in maze:
                            new[key] = maze[key]
                    new.update(mazeId=request.maze_id, floor=1)
                    state['maze'] = new
                    self._save(profile, state)
                    self._notify(profile, changes)
                    return 200, self._typed('ApiMazeEnter.result', {'mazeInfo': self._info(new, full=True), 'resChange': changes})
                if action == 'actor':
                    if method == 'GET':
                        return 200, self._typed('ApiMazeActor.result', {'selectActors': self._actors(profile, maze)})
                    self._active(maze, selected=False)
                    selected = list(request.actor_uids)
                    if maze['selectedActors']:
                        if selected == maze['selectedActors']:
                            return 200, {}
                        raise MazeError('maze_actor_pool_locked')
                    owned = {int(a['actor']['uid']): a for a in self._actors(profile, maze)}
                    if (not selected or len(selected) > MAZE_CONFIG[maze['mazeId']]['maxActorCount']
                            or len(selected) != len(set(selected)) or any(uid not in owned for uid in selected)):
                        raise MazeError('maze_invalid_actor_selection')
                    entries = [owned[uid] for uid in selected]
                    if len({a['actor']['id'] for a in entries}) != len(entries) or not any(self._kind(a) == 1 for a in entries):
                        raise MazeError('maze_invalid_actor_selection')
                    maze['selectedActors'] = selected
                    maze['pool'] = {str(uid): owned[uid] for uid in selected}
                    main = [uid for uid in selected if self._kind(owned[uid]) == 1][:3]
                    sub = [uid for uid in selected if self._kind(owned[uid]) == 2][:len(main)]
                    maze['group'] = main + [0] * (3 - len(main)) + sub + [0] * (3 - len(sub))
                    self._save(profile, state)
                    return 200, {}
                if action == 'fightactorgroup':
                    if method == 'POST':
                        self._active(maze)
                        self._idle(maze)
                        members = list(request.members)
                        if len(members) != 6 or {m.index for m in members} != set(range(6)):
                            raise MazeError('maze_invalid_fight_group')
                        group = [m.actor_uid for m in sorted(members, key=lambda m: m.index)]
                        self._validate_group(maze, group)
                        maze['group'] = group
                        self._save(profile, state)
                    return 200, self._group(maze)
                if action == 'move':
                    self._active(maze)
                    self._idle(maze)
                    start, end = request.start_cell_index, request.end_cell_index
                    if start != maze['playerCellIndex'] or start not in PATH or end not in PATH or start == end:
                        raise MazeError('maze_move_not_adjacent')
                    first, last = PATH.index(start), PATH.index(end)
                    step = 1 if first < last else -1
                    way = [PATH[i] for i in range(first, last + step, step)]
                    if any(node not in maze['completed'] for node in way[1:-1]):
                        raise MazeError('maze_move_not_adjacent')
                    fresh = end not in maze['completed']
                    new_buff, full = 0, False
                    if end == 3 and fresh:
                        for node in way[1:-1]:
                            self._walk(maze, node)
                        maze['triggerCellIndex'] = end
                        way = way[:-1]  # Native UpdateRoad marks every returned node complete.
                    else:
                        for node in way[1:]:
                            self._walk(maze, node)
                        maze['triggerCellIndex'] = 0
                        if end == 2 and fresh:
                            new_buff = 2
                            maze['buffs'] = [{'buffId': 2, 'leftRound': 5,
                                              'actorUid': [uid for uid in maze['group'] if uid]}]
                        elif end == 6 and fresh:
                            maze['prizeId'].append(101)
                        elif end == 9:
                            if maze['floor'] == MAZE_CONFIG[maze['mazeId']]['maxFloor']:
                                maze['isMazeOver'] = True
                            else:
                                maze.update(floor=maze['floor'] + 1, playerCellIndex=1, completed=[1],
                                            triggerCellIndex=0, enemyHp=[])
                                full = True
                    self._save(profile, state)
                    return 200, self._typed('ApiMazeMove.result', {'way': way, 'resChange': [], 'influenceBuff': [],
                        'newBuff': new_buff, 'mazeInfo': self._info(maze, full=full)})
                if action == 'receiveprize':
                    if self._request(body, empty=True):
                        raise MazeError('maze_invalid_request', 400)
                    if maze['received']:
                        return 200, self._typed('ApiMazeReceivePrize.result', {'resChange': []})
                    if not maze['mazeId'] or not maze['isMazeOver']:
                        raise MazeError('maze_not_over')
                    changes = grant_rewards(self.game, profile, [{'type': 11, 'id': 60001, 'count': len(maze['prizeId'])}])
                    maze.update(received=True, mazeId=0)
                    self._save(profile, state)
                    self._notify(profile, changes)
                    return 200, self._typed('ApiMazeReceivePrize.result', {'resChange': changes})
        except MazeError as exc:
            return exc.status, {'error': exc.error}

    def _battle(self, method: str, match: Any, body: Body, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        maze = state['maze']
        if not match[1]:
            self._active(maze)
            payload = self._request(body)
            cell = payload.get('cellIndex')
            if type(cell) is not int or set(payload) != {'cellIndex'}:
                raise MazeError('maze_invalid_request', 400)
            if cell != maze['triggerCellIndex'] or cell != 3 or maze['playerCellIndex'] != 2:
                raise MazeError('maze_fight_not_triggered')
            if maze['currentBattle']:
                return 200, deepcopy(maze['battles'][str(maze['currentBattle'])]['enter'])
            self._validate_group(maze, maze['group'], fight=True)
            uid = max(maze['lastDungeonUid'] + 1, 7_000_000_000_000 + int(self._clock()) * 1000)
            dungeon_id = DUNGEONS[(maze['floor'] - 1) % len(DUNGEONS)]
            entered = {'dungeonUid': uid, 'baseInfo': {'dungeonId': dungeon_id, 'useServerActor': False},
                       'myRecord': [{'actorUid': actor_uid, 'hp': maze['pool'][str(actor_uid)]['hp']}
                                    for actor_uid in maze['group'] if actor_uid],
                       'energyMp': maze['energyMp'], 'energySp': maze['energySp']}
            if maze['enemyHp']:
                entered['enemyRecord'] = {'hp': list(maze['enemyHp']), 'card': []}
            maze['lastDungeonUid'] = maze['currentBattle'] = uid
            maze['battles'][str(uid)] = {'enter': entered, 'group': list(maze['group']), 'state': 'started',
                                        'floor': maze['floor'], 'cell': cell}
            self._save(profile, state)
            return 200, deepcopy(entered)
        uid = int(match[1])
        run = maze['battles'].get(str(uid))
        if run is None:
            raise MazeError('maze_dungeon_not_found', 404)
        if match[2] == 'active':
            active = self._request(body, 'FightActiveData')
            if (active.dungeonType != 12 or active.stageID != run['enter']['baseInfo']['dungeonId']
                    or active.winTeam not in (1, 2)):
                raise MazeError('maze_invalid_battle_result', 400)
            teams = list(active.teamList)
            if len(teams) != 2 or {t.team for t in teams} != {1, 2} or not all(t.HasField('statistic') for t in teams):
                raise MazeError('maze_battle_statistics_required', 400)
            own = next(t for t in teams if t.team == 1)
            needed = max(i for i, actor_uid in enumerate(run['group']) if actor_uid) + 1
            if (not needed <= len(own.statistic.recordHpList) <= 6 or own.energySp < 0 or own.energyMp < 0
                    or any(hp < 0 for t in teams for hp in t.statistic.recordHpList)
                    or any(not 1 <= len(t.statistic.recordHpList) <= 6 for t in teams)):
                raise MazeError('maze_invalid_battle_statistics', 400)
            saved = _dict(active)
            if run.get('active') == saved and run['state'] in ('active', 'settled'):
                return 200, {}
            if run['state'] != 'started' or maze['currentBattle'] != uid:
                raise MazeError('maze_battle_result_already_saved')
            run.update(active=saved, state='active')
            self._save(profile, state)
            return 200, {}
        if match[2] == 'balance':
            if self._request(body, empty=True):
                raise MazeError('maze_invalid_request', 400)
            if run['state'] == 'settled':
                return 200, deepcopy(run['balance'])
            if run['state'] != 'active' or maze['currentBattle'] != uid:
                raise MazeError('maze_battle_result_required')
            active = run['active']
            won = active['winTeam'] == 1
            own = next(t for t in active['teamList'] if t['team'] == 1)
            enemy = next(t for t in active['teamList'] if t['team'] == 2)
            hp_list = own['statistic']['recordHpList']
            for index, actor_uid in enumerate(run['group']):
                if actor_uid:
                    entry = maze['pool'][str(actor_uid)]
                    entry['hp'] = hp_list[index]
                    entry['dead'] = hp_list[index] == 0
            maze['energyMp'], maze['energySp'] = own['energyMp'], own['energySp']
            maze['enemyHp'] = [] if won else list(enemy['statistic']['recordHpList'])
            maze['currentBattle'] = 0
            way = [maze['playerCellIndex']]
            if won:
                self._walk(maze, run['cell'])
                maze['triggerCellIndex'] = 0
                way.append(run['cell'])
            if not any(self._kind(entry) == 1 and not entry['dead'] for entry in maze['pool'].values()):
                maze['isMazeOver'] = True
            result = self.game.activity_modes._result(profile, won=won)
            result.update(result={'win': won, 'resChange': []}, isMazeOver=maze['isMazeOver'],
                          autoMove={'way': way, 'mazeInfo': self._info(maze), 'resChange': [],
                                    'newBuff': 0, 'influenceBuff': []})
            run.update(state='settled', balance=result)
            self._save(profile, state)
            return 200, deepcopy(result)
        # Continue DELETEs a settled run too: do not erase completed progress.
        if run['state'] in ('settled', 'cancelled'):
            return 200, {}
        if run['state'] == 'active':
            raise MazeError('maze_battle_balance_required')
        if maze['currentBattle'] != uid:
            raise MazeError('maze_dungeon_not_active')
        run['state'] = 'cancelled'
        maze['currentBattle'] = 0
        self._save(profile, state)
        return 200, {}


__all__ = ['LocalMaze']
