"""Bind persisted game events to the original client's typed push messages.

HTTP and TLS listeners share one game but have separate socket registries.
The weak set avoids duplicate delivery and retaining closed listeners.
"""
from __future__ import annotations

from collections.abc import Mapping
import weakref

from google.protobuf import json_format, message_factory


def encode_mails(pool, payload: Mapping) -> bytes:
    message = message_factory.GetMessageClass(pool.FindMessageTypeByName('ScMails'))()
    json_format.ParseDict(dict(payload), message)
    return message.SerializeToString()


def wire_game_notifications(game, service) -> None:
    """Attach one listener, idempotently, without changing persisted state."""
    if game is None:
        return
    services = getattr(game, '_notification_services', None)
    if services is None:
        services = weakref.WeakSet()
        game._notification_services = services

        def base(profile, push_type, params=()):
            account_id = profile.get('account_id')
            # Materialize once: a generator must reach both listeners intact.
            params = tuple(params)
            return sum(s.base_push_for_account_id(account_id, push_type, params)
                       for s in tuple(services))

        def mail(profile, payload):
            data = encode_mails(game._pool, payload)
            delivered = 0
            for target in tuple(services):
                key = target.key_for_account_id(profile.get('account_id'))
                if key is not None:
                    delivered += target.publish(key, 'ScMails', data)
            return delivered

        def achievement(profile, ids):
            ids = tuple(dict.fromkeys(int(value) for value in ids))
            if not ids:
                return 0
            delivered = base(profile, 19)
            for achievement_id in ids:
                delivered += base(profile, 20, (achievement_id,))
            return delivered

        def social(profile, message_type, payload):
            if message_type not in {'WorldMessage', 'ScWorldChatSubChannel', 'ActorCultivationInfoMessage'}:
                raise ValueError('unsupported social push')
            cls = message_factory.GetMessageClass(game._pool.FindMessageTypeByName(message_type))
            data = json_format.ParseDict(dict(payload), cls()).SerializeToString()
            delivered = 0
            for target in tuple(services):
                key = target.key_for_account_id(profile.get('account_id'))
                if key is not None:
                    delivered += target.publish(key, message_type, data)
            return delivered

        def deferred(callback):
            def notify(*args, **kwargs):
                return game.after_commit(lambda: callback(*args, **kwargs))
            return notify

        game.base_push_notifier = deferred(base)
        game.base_push_notifier.push_services = services
        game.social_message_notifier = deferred(social)
        if getattr(game, 'mailbox', None) is not None:
            game.mailbox.notifier = deferred(mail)
        if getattr(game, 'achievements', None) is not None:
            game.achievements.notifier = deferred(achievement)
        if getattr(game, 'cultivation', None) is not None:
            game.cultivation.notifier = deferred(lambda profile, payload: social(
                profile, 'ActorCultivationInfoMessage', payload))
            def cultivation_tasks(profile, partial):
                with game._lock:
                    payload = game.tasks.snapshot(profile)
                return sum(target.tasks_change_for_account_id(profile['account_id'], payload)
                           for target in tuple(services))
            game.cultivation.task_notifier = deferred(cultivation_tasks)
    services.add(service)
