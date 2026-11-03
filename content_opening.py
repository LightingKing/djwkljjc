"""Explicit batch-two collection policy; original tables remain unchanged."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY = json.loads((ROOT / 'data/server-only/content-open-20261104.json').read_text(encoding='utf-8'))
SKINS = {int(r['id']): r for r in json.loads((ROOT / 'data/client-tables/ActorSkin.json').read_text(encoding='utf-8'))['rows']}
OPEN_CHARACTERS = frozenset(POLICY['openCharacterIds'])
FUTURE_SKINS = frozenset(POLICY['futureSkinIds'])
CHARACTER_GOOD_BASE = 1310000
SKIN_GOOD_BASE = 29000000


def collection_products():
    """Use native goods, isolated local prices, and an explicit checked skin set."""
    offers = [{'goodId': CHARACTER_GOOD_BASE + actor_id,
               'cost': dict(POLICY['characterOffer']['cost']),
               'good': {'type': 15, 'id': actor_id, 'count': 1, 'param': POLICY['characterOffer']['grantWay']}}
              for actor_id in POLICY['openCharacterIds']]
    for skin_id in POLICY['futureSkinIds']:
        cost = SKINS[skin_id]['exchangeDefault']
        if not isinstance(cost, dict) or int(cost.get('type', 0)) != 11 or int(cost.get('count', 0)) <= 0:
            raise ValueError(f'collection skin has no original shard cost: {skin_id}')
        offers.append({'goodId': SKIN_GOOD_BASE + skin_id,
                       'cost': {**cost, 'param': int(cost.get('param', 0))},
                       'good': {'type': 29, 'id': skin_id, 'count': 1, 'param': 0}})
    return offers


def allowed_skin_actors(skin_id):
    """Mixed rows never enable missing character variants such as 2017/2134."""
    skin_id = int(skin_id)
    skin = SKINS.get(skin_id)
    if skin is None:
        return frozenset()
    if skin.get('openTime', '').startswith('2029'):
        return frozenset(POLICY['skinAllowedActorIds'].get(str(skin_id), []))
    return frozenset(skin.get('actorIds', [])) - frozenset(POLICY['excludedCharacterIds'])
