"""Permanent local bookmark-point shop using the native GachaScoreShop contract.

Client data supplies the point resource and gachaPointGenRation, but no shop
inventory was recovered. Products/prices are explicitly local data. Each
product is a permanent one-time unlock, so a native request without a nonce
can be replayed safely, including across restarts.
"""
from contextlib import closing
import json
from pathlib import Path
from urllib.parse import urlsplit

from google.protobuf import json_format

import actors
from content_opening import (CHARACTER_GOOD_BASE, FUTURE_SKINS, OPEN_CHARACTERS, POLICY,
                             allowed_skin_actors, collection_products)
from grants import grant_rewards
from server.core.reset import policy_for


PRODUCTS_PATH = Path(__file__).parent / "data/server-only/gacha-score-shop.json"


def active_activities(game, profile, activity_type):
    status, rows = game.events._activity_list("GET")
    if status != 200:
        return []
    now = int(game._now())
    weekday = policy_for(game).local_datetime(now).isoweekday()
    return [r for r in rows if int(r.get("type", 0)) == activity_type
            and int(r.get("timestampStart", 0) or 0) <= now
            and (not int(r.get("timestampEnd", 0) or 0) or now < int(r["timestampEnd"]))
            and (not r.get("weekdays") or weekday in r["weekdays"])
            and int(profile.get("level", 1)) >= int(r.get("levelLimit", 0) or 0)]


class LocalGachaScoreShop:
    def __init__(self, game):
        self.game = game
        self.products = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))["goods"] + collection_products()
        with game._lock, closing(game._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS local_gacha_shop_claims ("
                         "account_id INTEGER NOT NULL,good_id INTEGER NOT NULL,claimed_at INTEGER NOT NULL,"
                         "PRIMARY KEY(account_id,good_id))")

    def _claimed(self, profile):
        with closing(self.game._connect()) as conn:
            return {int(row[0]) for row in conn.execute(
                "SELECT good_id FROM local_gacha_shop_claims WHERE account_id=?", (profile["account_id"],))}

    def redpoint_fields(self, profile):
        """The native login field uses the same pool counters as the window."""
        with self.game.atomic_mutation():
            state = self.game.systems._state(profile)
            status, rows = self.game.systems._gacha_transaction("GET", "/fci/gachainfo/", None, profile, state)
            return {"gachaInfoGet": {"gachaInfo": rows}} if status == 200 else {}

    def handle_http(self, method, path, body, profile):
        route = urlsplit(path).path.rstrip("/").lower()
        if route not in {"/fci/gacha-shop", "/fci/gachashop", "/fci/gacha-shop/trade", "/fci/gachashoptrade"}:
            return None
        trade = route.endswith(("/trade", "shoptrade"))
        if method.upper() != ("POST" if trade else "GET"):
            return 405, {"error": "method_not_allowed"}
        with self.game.atomic_mutation():
            if not active_activities(self.game, profile, 13):
                return 409, {"error": "activity_not_open"}
            claimed = self._claimed(profile)
            state = self.game.systems._state(profile)
            owned_skins = {int(skin_id) for skin_ids in
                           state.get("unlockedSkins", {}).values()
                           for skin_id in skin_ids}

            def skin_owned(product):
                return product["good"]["type"] == 29 and product["good"]["id"] in owned_skins

            if not trade:
                result = self.game.systems._message("ApiGachaShop.get")
                # Permanent inventory: do not periodically recreate claims.
                result.next_refresh_time = 2147483647
                for row in self.products:
                    if int(row["goodId"]) not in claimed and not skin_owned(row):
                        json_format.ParseDict(row, result.goods.add())
                return 200, result
            payload = self.game.systems._request_values(body)
            msg = self.game.systems._request(body, "ApiGachaShopTrade")
            good_id = int(payload.get("good_id", payload.get("goodId", msg.good_id if msg else 0)) or 0)
            product = next((p for p in self.products if p["goodId"] == good_id), None)
            if product is None:
                return 400, {"error": "invalid_request"}
            result = self.game.systems._message("ApiGachaShopTrade.post")
            if good_id in claimed:
                return 200, result
            if skin_owned(product):
                return 409, {"error": "invalid_request"}
            cost = product["cost"]
            if int(cost['type']) == 11:
                if self.game.systems._items_owned(state, cost['id']) < cost['count']:
                    return 409, {"error": "insufficient_item"}
                paid = self.game.systems._items_take(state, {cost['id']: cost['count']})
                # Persist the bag inside the same transaction before the common
                # grant helper reads it; otherwise its reload would restore cost.
                self.game.systems._save(profile, state)
            else:
                ok, paid, error = self.game.economy._change_resources(profile, [{**cost, "count": -cost["count"]}])
                if not ok:
                    return 409, {"error": error or "insufficient_resource"}
            changes = [{"baseRes": change} for change in paid]
            rewards = [product["good"]]
            actor_id = int(product['good']['id'])
            if (product['good']['type'] == 15 and actor_id in OPEN_CHARACTERS
                    and good_id == CHARACTER_GOOD_BASE + actor_id
                    and actors.first_uid(profile, state, actor_id) is not None):
                # Only the new local collection offers convert existing cards.
                # The original purple ActorDecompose rows supply shard counts.
                from systems import ACTOR_DECOMPOSE
                rewards = ACTOR_DECOMPOSE[(actor_id, POLICY['characterOffer']['duplicateQuality'])]
            skin_owners = ({actor_id: allowed_skin_actors(actor_id)}
                           if product['good']['type'] == 29 and actor_id in FUTURE_SKINS else None)
            changes += grant_rewards(self.game, profile, rewards, skin_owner_overrides=skin_owners)
            with closing(self.game._connect()) as conn, conn:
                conn.execute("INSERT INTO local_gacha_shop_claims VALUES(?,?,?)",
                             (profile["account_id"], good_id, int(self.game._now())))
            for change in changes:
                json_format.ParseDict(change, result.res_change.add())
            return 200, result
