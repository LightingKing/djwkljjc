"""Inventory fixtures followed by the actual native novel collection flow."""
import math

from google.protobuf import message_factory

import actors
from achievements import NOVELS
from grants import grant_rewards
from systems import ACTOR_EXP_ITEMS, ACTOR_LEVELS, ACTOR_STAR_PROMOTIONS


def prepare_novel_collection(test, novel_id):
    """Reach the original star requirement through level/upgrade HTTP routes.

    Only starting inventory is granted; collection/cover/task state is never
    written by this helper. The caller performs the native POST to collect.
    """
    game, account = test.game, test.account

    def request(method, path, body=None, content_type="application/json"):
        status, result = game.handle_http(method, path, body, content_type, test.auth)
        test.assertEqual(status, 200, result)
        return result

    def records():
        profile = game._profile(account)
        best = {}
        for record in actors.ordered(profile, game.systems._state(profile)):
            aid = record["id"]
            if aid in NOVELS[novel_id]["actors"] and (aid not in best or record["star"] > best[aid]["star"]):
                best[aid] = record
        return best

    owned = records()
    grant_rewards(game, game._profile(account), [
        {"type": 15, "id": aid, "count": 1} for aid in NOVELS[novel_id]["actors"] if aid not in owned])
    for _ in range(30):
        detail = request("GET", f"/fci/CollectionsNovel/{novel_id}")
        if detail["novels"][0]["canGetReward"]:
            return detail
        actor = min(records().values(), key=lambda row: (row["star"], row["uid"]))
        promotion = ACTOR_STAR_PROMOTIONS[(actor["id"], actor["star"] + 1)]
        if actor["level"] < promotion["levelLimit"]:
            needed = sum(ACTOR_LEVELS[level] for level in range(actor["level"] + 1, promotion["levelLimit"] + 1)) - actor["exp"]
            count = math.ceil(needed / ACTOR_EXP_ITEMS[10004])
            grant_rewards(game, game._profile(account), [{"type": 11, "id": 10004, "count": count}])
            cls = message_factory.GetMessageClass(game._pool.FindMessageTypeByName("ApiActorLevelUp"))
            body = cls(food_res=[{"type": 11, "id": 10004, "count": count}]).SerializeToString()
            request("POST", f'/fci/actor/{actor["uid"]}/levelupgrade/', body, "application/wireformat")
        grant_rewards(game, game._profile(account), promotion["starCost"])
        request("POST", f'/fci/actor/{actor["uid"]}/starupgrade/', b"null")
    test.fail(f"Novel {novel_id} did not reach its original collection threshold")
