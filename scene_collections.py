"""Original collection gates plus the explicit once-per-account local gift."""
import json
from pathlib import Path

try:
    from .local_mailbox import send_mail
except ImportError:
    from local_mailbox import send_mail

_ROOT = Path(__file__).resolve().parent
SCENES = {int(r.get('id', 0)): r for r in json.loads(
    (_ROOT / 'data/client-tables/CollectionsScenes.json').read_text(encoding='utf-8'))['rows']}
# D/local: original acquisition not recovered. 31014 keeps the original puzzle.
LOCAL_GIFT_SCENES = (31002, 31003, 31004, 31006, 31010, 31011, 31012, 31015, 31016)
DELIVERY_KEY = 'sceneCollectionGift20261104'


def deliver_local_scene_gift(game, profile):
    """Mail plus durable delivery marker commit together, including under retries."""
    with game.atomic_mutation():
        state = game.systems._state(profile)
        if state.get(DELIVERY_KEY):
            return
        mail = send_mail(
            game, profile, '本地收藏开放：场景',
            '这封本地规则邮件提供原获取途径尚未恢复的九个场景物品，每账号仅一次。'
            '任务场景仍按原任务解锁；拼图场景31014仍通过拼图获得。',
            [{'type': 11, 'id': scene_id, 'count': 1} for scene_id in LOCAL_GIFT_SCENES],
            expire_days=None, important=True,
        )
        # send_mail persisted its own fresh copy; never overwrite it with the pre-send state.
        state = game.systems._state(profile)
        state[DELIVERY_KEY] = {'mailId': mail['id'], 'sceneIds': list(LOCAL_GIFT_SCENES)}
        game.systems._save(profile, state)


def scene_unlocked(game, profile, scene_id):
    """Native scene UI claims task items before setting a non-default background."""
    row = SCENES.get(int(scene_id))
    if row is None:
        return False
    if int(row.get('unlockType', 0)) == 0:
        return True
    item = game.systems._state(profile).get('items', {}).get(str(scene_id), {})
    return int(item.get('count', 0)) > 0
