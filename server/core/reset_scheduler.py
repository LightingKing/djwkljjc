"""Settle calendar changes for connected players, then invalidate client data.

Business modules keep their own durable reset markers. The dispatch marker
is saved after settlement, so a crash can repeat a notification but cannot
grant the same day's tickets twice. Offline players use the same lazy reset
paths when they next request their state.
"""
from __future__ import annotations

from contextlib import closing
import logging
import threading

from .reset import policy_for

log = logging.getLogger(__name__)


class ResetScheduler:
    def __init__(self, game, services, *, interval_seconds=1.0):
        self.game = game
        self.services = tuple(services)
        self.interval_seconds = max(0.05, min(float(interval_seconds), 60.0))
        self._stop = threading.Event()
        self._thread = None
        self._tick_lock = threading.Lock()
        self._social_checked_at = {}
        self.last_error = None
        with self.game._lock, closing(game._connect()) as connection, connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS local_reset_dispatch (
                account_id INTEGER PRIMARY KEY, daily INTEGER NOT NULL,
                signin INTEGER NOT NULL, weekly INTEGER NOT NULL)''')

    def tick(self) -> int:
        with self._tick_lock:
            account_ids = sorted({value for service in self.services for value in service.account_ids()})
            self._social_checked_at = {key: value for key, value in self._social_checked_at.items()
                                       if key in account_ids}
            processed = 0
            self.last_error = None
            for account_id in account_ids:
                try:
                    processed += bool(self._settle(account_id))
                    self._tick_social(account_id)
                except Exception:
                    self.last_error = 'reset_failed'
                    log.exception('calendar settlement failed for account %s', account_id)
            return processed

    def _tick_social(self, account_id):
        """Poll connected accounts at the bot interval, not every second."""
        social = self.game.social
        now = self.game._now()
        previous = self._social_checked_at.get(account_id)
        if previous is not None and now - previous < social.WORLD_BOT_INTERVAL:
            return
        with self.game._lock:
            social.tick(self.game._profile({'accountId': account_id}), refresh_applications=False)
        self._social_checked_at[account_id] = now

    def _settle(self, account_id):
        game = self.game
        now = game._now()
        policy = policy_for(game)
        current = (policy.day_index(now), policy.day_index(now, signin=True), policy.week_index(now))
        with game._lock:
            with closing(game._connect()) as connection:
                row = connection.execute('SELECT daily,signin,weekly FROM local_reset_dispatch WHERE account_id=?',
                                         (account_id,)).fetchone()
            previous = tuple(row) if row is not None else (None, None, None)
            due = tuple(old is None or new > old for new, old in zip(current, previous))
            if not any(due):
                return False
            profile = game._profile({'accountId': account_id})
            # Guild aid settles Sunday at 06:00, independently of the Monday
            # weekly marker. Its own persisted boundary makes every daily or
            # signin dispatch safe, including offline catch-up.
            game.guild.player_data(profile)
            items = game.activity_modes.refresh_daily_items(profile)
            race = getattr(game.realtime_pvp, 'race', None)
            if race is not None:
                items += race.refresh_daily_items(profile)
            maze = getattr(game, 'maze', None)
            if maze is not None:
                # Maze uses the shared daily/Monday 06:00 policy and owns its item
                # push, so do not add its changes to the notification list.
                maze.refresh_daily_items(profile)
            task_payload = None
            if due[0]:
                tournament = getattr(game, 'tournament', None)
                if tournament is not None:
                    tournament.refresh_daily_record(profile)
                state = game.tasks._state(profile)
                game.tasks._sync(profile, state)
                game.tasks._mark_login(state)
                game.tasks._sync(profile, state)
                game.tasks._save(profile, state)
                task_payload = game.tasks._payload(profile, state)
                events = game.events._state(profile)
                if game.events._continuous_tick(events):
                    game.events._save(profile, events)
                modes = game.activity_modes._state(profile)
                game.activity_modes._material_state(modes)
                game.activity_modes._tower_state(profile, modes)
                game.activity_modes._save(profile, modes)
                settle_month_card = getattr(game.mall, 'settle_month_card', None)
                if settle_month_card is not None:
                    settle_month_card(profile)
            with closing(game._connect()) as connection, connection:
                high_water = tuple(new if old is None else max(new, old) for new, old in zip(current, previous))
                connection.execute('INSERT OR REPLACE INTO local_reset_dispatch(account_id,daily,signin,weekly) VALUES (?,?,?,?)',
                                   (account_id, *high_water))

        # Every notification follows persisted state, outside the game lock.
        if task_payload is not None:
            for service in self.services:
                service.tasks_change_for_account_id(account_id, task_payload)
        push = getattr(game, 'base_push_notifier', None)
        if push is not None:
            if due[1]:
                push(profile, 32)
            if due[0]:
                for push_type in (21, 42, 71, 76, 1):
                    push(profile, push_type)
            if due[2]:
                for push_type in (88, 74):
                    push(profile, push_type)
            item_ids = sorted({int(change['baseRes']['id']) for change in items if 'baseRes' in change})
            for item_id in item_ids:
                push(profile, 3, (item_id,))
        return True

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()

        def run():
            while not self._stop.is_set():
                self.tick()
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(target=run, name='calendar-reset', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
