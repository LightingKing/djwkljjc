"""Original event-dungeon, SemiFixed, BuffShop and autumn claw contracts.

Tables are unmodified APK exports. Constant availability and the deterministic
drop-display/individual claw stock policy are local rules, documented separately.
All mutations, grants and receipts share LocalGame.atomic_mutation().
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from activity_modes import DUNGEON_REWARD, MISC, _costs, _int, _res, _team_statistics
from grants import grant_rewards
from server.core.reset import policy_for, period_due


def _rows(name):
    return json.loads((Path(__file__).parent / "data/client-tables" / f"{name}.json").read_text(encoding="utf8"))["rows"]


EVENTS = {int(r["activityId"]): r for r in _rows("EventDungeon")}
STARS = {(int(r["activityId"]), int(r["star"])): r for r in _rows("EventReward")}
SEMI = {int(r["dungeonId"]): r for r in _rows("SemiFixedDungeon")}
NPCS = {int(r["npcId"]): r for r in _rows("DungeonNPC")}
BUFF_SHOPS = {int(r["activityType"]): r["buffGoods"] for r in _rows("ActivityBuffShop")}
BUFF_GOODS = {(int(r["goodId"]), int(r["grade"])): r for r in _rows("ActivityBuffGoods")}
CLAW = {int(r["id"]): r for r in _rows("ClawCrane")}
CLAW_STOCK = _rows("ClawCraneStock")
CLAW_FORCE = _rows("ClawCraneForceCount")
ACTOR_SKILLS = _rows("ActorSkill")
ACTOR_KINDS = {int(r["id"]): int(r.get("kind", 0)) for r in _rows("ActorConfig")}
DROP_CAPS = {int(r["id"]): int(r["dropMaxCount"]) for r in _rows("ItemInfo") if r.get("dropMaxCount")}


class LocalHiddenEventDungeons:
    def __init__(self, game):
        self.game = game
        self.modes = game.activity_modes
        with game._lock, closing(game._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS hidden_event_state "
                         "(account_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL, updated_at INTEGER NOT NULL)")

    def _state(self, profile):
        with closing(self.game._connect()) as conn:
            row = conn.execute("SELECT state_json FROM hidden_event_state WHERE account_id=?", (profile["account_id"],)).fetchone()
        state = json.loads(row[0]) if row else {}
        for key in ("records", "stars", "buffs", "groups", "claw"):
            state.setdefault(key, {})
        day = policy_for(self.game).day_index(self.game._now())
        if period_due(state.get("day"), day):
            for r in state["records"].values():
                r["doneTimes"] = 0
            state["day"] = day
        return state

    def _save(self, profile, state):
        with closing(self.game._connect()) as conn, conn:
            conn.execute("INSERT INTO hidden_event_state VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE "
                         "SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                         (profile["account_id"], json.dumps(state, separators=(",", ":")), self.game._now()))

    def _open(self, kind, aid=None, profile=None):
        status, entries = self.game.events._activity_list("GET")
        now = int(self.game._now())
        weekday = policy_for(self.game).local_datetime(now).isoweekday()
        return status == 200 and any(
            _int(e.get("type")) == kind and (aid is None or _int(e.get("activityId")) == aid)
            and _int(e.get("timestampStart")) <= now
            and (not _int(e.get("timestampEnd")) or now < _int(e["timestampEnd"]))
            and (not _int(e.get("timestampClose")) or now < _int(e["timestampClose"]))
            and (not e.get("weekdays") or weekday in e["weekdays"])
            and (profile is None or _int(profile.get("level"), 1) >= _int(e.get("levelLimit")))
            for e in entries)

    def _record(self, profile, state, did):
        row = DUNGEON_REWARD.get(did, {})
        old = state["records"].get(str(did), {})
        pre = _int(row.get("preDungeonId"))
        unlocked = int(profile.get("level", 1)) >= _int(row.get("unlockLevel")) and (
            not pre or state["records"].get(str(pre), {}).get("finished", False))
        return {"dungeonId": did, "unlocked": unlocked, "finished": bool(old.get("finished")),
                "targetsDone": old.get("targetsDone", []), "multiPower": old.get("multiPower", 1),
                "doneTimes": old.get("doneTimes", 0), "finishCondition": unlocked, "new": not bool(old.get("finished"))}

    def _summary(self, profile, state, aid):
        return {"rewardGot": state["stars"].get(str(aid), []), "totalStar": sum(
            len(state["records"].get(str(did), {}).get("targetsDone", [])) for did in EVENTS[aid]["dungeonId"])}

    def claimable_activity_ids(self, profile):
        state = self._state(profile)
        return sorted({aid for aid, star in STARS if self._open(EVENTS[aid]["activityType"], aid, profile)
                       and self._summary(profile, state, aid)["totalStar"] >= star
                       and star not in state["stars"].get(str(aid), [])})

    def redpoint_fields(self, profile):
        state = self._state(profile)
        return {"eventDungeonStarRewardGet": {"rewardSummary": {
            str(aid): self._summary(profile, state, aid) for aid in EVENTS}}}

    def _group(self, profile, state, did):
        if str(did) in state["groups"]:
            return state["groups"][str(did)]
        _, uids = self.modes._fight_team(profile, 1)
        return {"groupInfo": {"members": [{"index": i, "isNpc": bool(npc),
                  "actorUid": 0 if npc else (uids[i] if i < len(uids) else 0)}
                 for i, npc in enumerate(SEMI[did]["npcId"]) ]}}

    def _fighters(self, profile, state, did, group):
        if did not in SEMI:
            ids, uids = self.modes._fight_team(profile, group)
            return ids, uids, [self.modes._fight_actor(profile, aid, uid) for aid, uid in zip(ids, uids)], 0
        members = self._group(profile, state, did)["groupInfo"]["members"]
        ids, uids, actors, count = [], [], [], 0
        system_state = self.game.systems._state(profile)
        import actors as actor_store
        for member, npc_id in zip(members, SEMI[did]["npcId"]):
            uid = _int(member.get("actorUid"))
            if member.get("isNpc") and npc_id:
                npc = NPCS[npc_id]
                aid, uid = npc["actorId"], 0
                actor = self.game.dungeons._actor_dict(aid, npc_id)
                actor.update(uid=0, level=npc["level"], star=npc["star"], quality=npc["quality"],
                             attrs=npc["attrs"], talentInfo=npc.get("talents", []))
                levels = npc.get("skillsLevel", [])
                actor["skills"] = [{"id": r["skillId"], "level": levels[r["displayIndex"] - 1], "isRead": True}
                    for r in ACTOR_SKILLS if r.get("actorId") == aid and r.get("cmdStr")
                    and 1 <= _int(r.get("displayIndex")) <= len(levels)]
                count += 1
            else:
                owned = actor_store.get(profile, system_state, uid) if uid else None
                aid = int(owned["id"]) if owned else 0
                actor = self.modes._fight_actor(profile, aid, uid)
            ids.append(aid); uids.append(uid); actors.append(actor)
        return ids, uids, actors, count

    def _enter(self, profile, body, semi=False):
        req = self.modes._request(body, "ApiSemiFixedDungeon" if semi else "ApiEventDungeon")
        aid, kind, did = (_int(req.get(k)) for k in ("activityId", "activityType", "dungeonId"))
        event = EVENTS.get(aid)
        if not event or int(event["activityType"]) != kind or did not in event["dungeonId"] or semi != (did in SEMI):
            return 400, {"error": "invalid_request"}
        if not self._open(kind, aid, profile):
            return 409, {"error": "activity_not_open"}
        row, state = DUNGEON_REWARD[did], self._state(profile)
        record = self._record(profile, state, did)
        if not record["unlocked"]:
            return 409, {"error": "level_too_low" if _int(row.get("unlockLevel")) > int(profile["level"]) else "pre_dungeon_not_finished"}
        if _int(row.get("enterTimes")) and record["doneTimes"] >= row["enterTimes"]:
            return 409, {"error": "dungeon_done_times"}
        multi = max(1, min(_int(req.get("multiPower"), 1), max(1, len(record["targetsDone"])), 3))
        costs, win_costs = _costs(row, "cost", multi), _costs(row, "costWin", multi)
        if not self.modes._can_pay(profile, costs + win_costs):
            return 409, {"error": "insufficient_resource"}
        ids, uids, actors, npc_count = self._fighters(profile, state, did, req.get("selectActorGroupId", 1))
        if not any(ids[:3]):
            return 409, {"error": "invalid_request"}
        # Native POST has no nonce. An unchanged unreported entry is a retry;
        # explicit DELETE or a completed report is required for a new charge.
        with closing(self.game._connect()) as conn:
            old = conn.execute("SELECT dungeon_uid, extra_json FROM activity_mode_runs WHERE account_id=? "
                               "AND mode=? AND status='active' AND active_data IS NULL ORDER BY dungeon_uid DESC LIMIT 1",
                               (profile["account_id"], "hidden_semi" if semi else "hidden_event")).fetchone()
        signature = [did, aid, multi, uids, ids]
        if old:
            extra = json.loads(old["extra_json"])
            if extra.get("signature") == signature:
                return 200, extra["entry"]
        paid = self.modes._pay(profile, costs)
        if paid is None:
            return 409, {"error": "insufficient_resource"}
        uid, now = self.modes._insert_run(profile, "hidden_semi" if semi else "hidden_event", did,
            activity_id=aid, multi_power=multi, group_id=_int(req.get("selectActorGroupId"), 1), actor_uids=uids)
        aura = self.modes._aura(profile, req)
        base = self.modes._base_info(profile, "material", did, ids, uids, now, activity_id=aid, actors=actors, aura=aura)
        base.update(activityType=kind, dungeonType=14 if semi else 5, npcCnt=npc_count, reduceInfo={},
                    fightBuffs=[BUFF_GOODS[(int(g), grade)]["buffId"] for g, grade in state["buffs"].get(str(kind), {}).items() if grade])
        entry = {"dungeonUid": uid, "baseInfo": base, "resChange": paid}
        with closing(self.game._connect()) as conn, conn:
            conn.execute("UPDATE activity_mode_runs SET extra_json=? WHERE dungeon_uid=?",
                         (json.dumps({"signature": signature, "entry": entry, "activityType": kind, "npcCnt": npc_count}), uid))
        self._save(profile, state)
        return 200, entry

    def _balance(self, profile, run):
        if run.get("balance_json"):
            return 200, json.loads(run["balance_json"])
        if run["status"] != "active":
            return 409, {"error": "dungeon_not_active"}
        active = self.modes._decode_active(run.get("active_data"))
        if active is None or int(active.winTeam) not in (1, 2):
            return 409, {"error": "invalid_request"}
        won = int(active.winTeam) == 1
        did, aid, multi = run["dungeon_id"], run["activity_id"], run["multi_power"]
        row, extra = DUNGEON_REWARD[did], run["extra"]
        changes = self.modes._pay(profile, _costs(row, "costWin", multi)) if won else []
        if changes is None:
            return 409, {"error": "insufficient_resource"}
        self.modes._claim(profile, run["dungeon_uid"], won)
        state = self._state(profile)
        old = state["records"].get(str(did), {})
        first = won and not old.get("finished")
        targets = self.modes._targets_done(did, active, won)
        actor_exp, before = [], int(profile["level"])
        if won:
            # Original DropInfo server tables are absent. Match existing local
            # material dungeons: deterministic dropDisplay + first-clear list.
            drops = [{**_res(r), "count": _int(r.get("count")) * multi} for r in row.get("dropDisplay", [])]
            if first:
                drops.extend(_res(r) for r in row.get("dropDisplayFirst", []))
            # SemiFixedDungeon.addtions is per selected original NPC.
            for addition in SEMI.get(did, {}).get("addtions", []):
                value = int(extra.get("npcCnt", 0)) * _int(addition.get("value"))
                if value:
                    res = _res(addition["res"])
                    if addition.get("isRatio"):
                        value = sum(r["count"] for r in drops if (r["type"], r["id"]) == (res["type"], res["id"])) * value // 1000
                    drops.append({**res, "count": value * multi})
            exp = sum(r["count"] for r in drops if r["type"] == 1)
            before, exp_change = self.modes._grant_player_exp(profile, exp)
            changes.append(exp_change)
            # ItemUtil.RemoveExtraRewards uses lifetime historyGetCount, not
            # current bag count. Spending must not re-enable capped drops.
            sys_state = self.game.systems._state(profile)
            capped = {}
            for reward in drops:
                rid = reward["id"]
                if reward["type"] == 11 and rid in DROP_CAPS:
                    history = _int(sys_state.get("items", {}).get(str(rid), {}).get("historyGetCount"))
                    reward["count"] = min(reward["count"], max(0, DROP_CAPS[rid] - history - capped.get(rid, 0)))
                    capped[rid] = capped.get(rid, 0) + reward["count"]
            capped_rewards = [r for r in drops if r["type"] == 11 and r["id"] in DROP_CAPS and r["count"] > 0]
            changes += self.modes._grant(profile, [r for r in drops if not (r["type"] == 11 and r["id"] in DROP_CAPS)],
                                         dungeon_type=14 if did in SEMI else 5)
            changes += grant_rewards(self.game, profile, capped_rewards) if capped_rewards else []
            if any(capped.values()):
                sys_state = self.game.systems._state(profile)
                for rid, count in capped.items():
                    if count:
                        entry = sys_state["items"][str(rid)]
                        entry["historyGetCount"] = _int(entry.get("historyGetCount")) + count
                self.game.systems._save(profile, sys_state)
            changes += self.game.welfare.grant_battle_materials(profile, did, dungeon_type=14 if did in SEMI else 5, runs=multi)
            actor_exp = self.modes._grant_actor_exp(profile, did, run["actor_uids"], multi)
            old.update(dungeonId=did, finished=True, targetsDone=sorted(set(old.get("targetsDone", [])) | set(targets)),
                       multiPower=multi, doneTimes=_int(old.get("doneTimes")) + 1)
            state["records"][str(did)] = old
            stats = self.modes._battle_stats(active)
            _, _, own_stat, _ = _team_statistics(active)
            if own_stat is not None:
                stats.update(statisticsPresent=True, assistSkills=_int(own_stat.assistCount))
            energy = sum(-r["count"] for r in _costs(row, "cost", multi) + _costs(row, "costWin", multi) if r["type"] == 6)
            self.game.tasks.on_battle_win(profile, did, energy, mode="activity", actor_uids=run["actor_uids"],
                stats={k: v for k, v in stats.items() if k != "cultivationStats"}, cultivation_stats=stats.get("cultivationStats"),
                dungeon_type=14 if did in SEMI else 5, activity_type=extra["activityType"], activity_id=aid)
        self._save(profile, state)
        result = self.modes._result(profile, won=won, res_change=changes, exp_changes=actor_exp,
                                   targets=targets, first=first, level_before=before)
        self.modes._store_balance(profile, run["dungeon_uid"], result)
        return 200, result

    def _star(self, profile, state, aid, body, schema="ApiEventDungeonStarReward"):
        if not self._open(EVENTS[aid]["activityType"], aid, profile):
            return 409, {"error": "activity_not_open"}
        req = self.modes._request(body, schema)
        star = _int(req.get("star"))
        row = STARS.get((aid, star))
        if row is None:
            return 400, {"error": "invalid_request"}
        if star in state["stars"].get(str(aid), []):
            return 200, {"resChange": []}
        if self._summary(profile, state, aid)["totalStar"] < star:
            return 409, {"error": "invalid_request"}
        changes = grant_rewards(self.game, profile, row["reward"])
        state["stars"].setdefault(str(aid), []).append(star)
        self._save(profile, state)
        return 200, {"resChange": changes}

    def _buff(self, method, kind, body, profile):
        if kind not in BUFF_SHOPS:
            return 404, {"error": "invalid_request"}
        state = self._state(profile)
        own = state["buffs"].setdefault(str(kind), {})
        if method == "GET":
            return 200, {"shopInfo": [{"goodsId": gid, "grade": own.get(str(gid), 0)} for gid in BUFF_SHOPS[kind]]}
        req = self.modes._request(body, "ApiActivityBuffShop")
        gid, grade = _int(req.get("goodsId")), _int(req.get("grade"))
        row = BUFF_GOODS.get((gid, grade))
        if method != "POST" or gid not in BUFF_SHOPS[kind] or not row or grade < 1:
            return 400, {"error": "invalid_request"}
        if own.get(str(gid), 0) >= grade:
            return 200, {"resChange": []}
        if grade != own.get(str(gid), 0) + 1:
            return 409, {"error": "invalid_request"}
        if not self._open(kind, profile=profile):
            return 409, {"error": "activity_not_open"}
        changes = self.modes._pay(profile, _costs(row, "cost"))
        if changes is None:
            return 409, {"error": "insufficient_resource"}
        own[str(gid)] = grade
        self._save(profile, state)
        return 200, {"resChange": changes}

    def _claw(self, action, method, body, profile, query_nonce=""):
        state = self._state(profile)
        claw = state["claw"]
        now = int(self.game._now())
        day = policy_for(self.game).day_index(now)
        start_day = claw.setdefault("startDay", day)
        event_day = min(28, max(1, day - start_day + 1))
        claw.setdefault("used", {})
        claw.setdefault("receipts", {})
        stock = {gid: sum(_int(r["stock"]) for r in CLAW_STOCK if r["id"] == gid and r["day"] <= event_day)
                 for gid, r in CLAW.items() if r.get("isCountLimit")}
        limits = [{"id": gid, "stock": count, "leftCount": max(0, count - claw["used"].get(str(gid), 0))}
                  for gid, count in stock.items()]
        if method == "GET" and action == "clawcraneprize":
            self._save(profile, state)
            return 200, {"limitInfo": limits}
        if method == "GET" and action == "clawcranerecord":
            return 200, {"peripheralRecord": claw.get("peripheralRecord", [])}
        if method != "POST" or action != "clawcranebingo":
            return 405, {"error": "invalid_request"}
        if not self._open(38, profile=profile) or not self._open(39, profile=profile):
            return 409, {"error": "activity_not_open"}
        req = self.modes._request(body)
        nonce = query_nonce or str(req.get("requestId", ""))
        if len(nonce) > 128:
            return 400, {"error": "invalid_request"}
        if not nonce:
            return 409, {"error": "request_id_required"}
        if nonce and nonce in claw["receipts"]:
            return 200, claw["receipts"][nonce]
        # The local Lua branch persists a pending operation in the original
        # GameDataCache before POST, then supplies this nonce in the query.
        # A retry can arrive after any delay or either process's restart.
        costs = [{**_res(MISC["clawCraneCost"]), "count": -_int(MISC["clawCraneCost"]["count"])}]
        changes = self.modes._pay(profile, costs)
        if changes is None:
            return 409, {"error": "insufficient_resource"}
        sequence = claw.get("sequence", 0) + 1
        roll = int.from_bytes(hashlib.sha256(f"claw:{profile['account_id']}:{sequence}".encode()).digest()[:8], "big") % int(MISC["clawCraneTotalProbability"])
        eligible = [r for gid, r in CLAW.items() if (not r.get("isUnique") or not claw["used"].get(str(gid)))
                    and (gid not in stock or claw["used"].get(str(gid), 0) < stock[gid])]
        force = [r for r in CLAW_FORCE if r["day"] == event_day and sequence - claw.get("lastForced", 0) >= r["forceDropCount"]
                 and any(v["id"] == r["id"] for v in eligible)]
        chosen = CLAW[force[0]["id"]] if force else None
        if chosen is not None:
            claw["lastForced"] = sequence
        else:
            for row in CLAW.values():
                roll -= _int(row.get("probability"))
                if roll < 0:
                    chosen = row if row in eligible else None
                    break
        # Unavailable limited prizes keep their probability as no prize; never
        # redistribute it into a more valuable available prize.
        if chosen is None:
            result = {"id": 0, "resChange": changes}
        else:
            gid = chosen["id"]
            changes += grant_rewards(self.game, profile, [chosen["res"]])
            claw["used"][str(gid)] = claw["used"].get(str(gid), 0) + 1
            result = {"id": gid, "resChange": changes}
        claw.update(sequence=sequence)
        if nonce:
            claw["receipts"][nonce] = result
        self._save(profile, state)
        return 200, result

    def handle_http(self, method, path, body, profile):
        parts = [p for p in urlsplit(path).path.lower().split("/") if p]
        if len(parts) < 2 or parts[0] != "fci":
            return None
        action, sub, method = parts[1], parts[2:], method.upper()
        if action not in {"event-dungeon", "semifixed-dungeon", "buffshop", "clawcraneprize", "clawcranerecord", "clawcranebingo", "activity"}:
            return None
        if action == "activity" and (len(sub) != 3 or sub[0] not in {"record", "star-reward"}):
            return None
        if action == "activity" and sub[1].isdigit() and int(sub[1]) not in (27, 39, 40, 51, 54):
            return None
        with self.game.atomic_mutation():
            if action.startswith("clawcrane"):
                query = parse_qs(urlsplit(path).query)
                return self._claw(action, method, body, profile, query.get("localRequestId", [""])[0])
            if action == "buffshop" and len(sub) == 1 and sub[0].isdigit():
                return self._buff(method, int(sub[0]), body, profile)
            state = self._state(profile)
            if action == "activity":
                if not all(s.isdigit() for s in sub[1:]):
                    return 400, {"error": "invalid_request"}
                kind, aid = map(int, sub[1:])
                if aid not in EVENTS or EVENTS[aid]["activityType"] != kind:
                    return 404, {"error": "invalid_request"}
                if sub[0] == "record" and method == "GET":
                    return 200, {"records": [self._record(profile, state, did) for did in EVENTS[aid]["dungeonId"]]}
                if sub[0] == "star-reward":
                    if method not in ("GET", "POST"):
                        return 405, {"error": "method_not_allowed"}
                    return (200, {"rewardSummary": {str(aid): self._summary(profile, state, aid)}}) if method == "GET" else self._star(profile, state, aid, body, "ApiActivityStarReward")
            if action == "event-dungeon" and sub == ["record"] and method == "GET":
                records = []
                for e in EVENTS.values():
                    if e["activityType"] in (27, 39, 40):
                        for did in e["dungeonId"]:
                            record = self._record(profile, state, did)
                            records.append({**{k: record[k] for k in ("dungeonId", "finished", "targetsDone", "multiPower", "doneTimes")},
                                            "activityId": e["activityId"], "activityType": e["activityType"]})
                return 200, records
            if action == "event-dungeon" and sub == ["star-reward"]:
                if method == "GET":
                    return 200, self.redpoint_fields(profile)["eventDungeonStarRewardGet"]
                if method != "POST":
                    return 405, {"error": "method_not_allowed"}
                req = self.modes._request(body, "ApiEventDungeonStarReward")
                aid = _int(req.get("activityId"))
                return self._star(profile, state, aid, body) if aid in EVENTS else (400, {"error": "invalid_request"})
            if action == "semifixed-dungeon" and len(sub) == 2 and sub[0] == "actorgroup" and sub[1].isdigit():
                did = int(sub[1])
                if did not in SEMI:
                    return 404, {"error": "invalid_request"}
                if method == "GET":
                    return 200, self._group(profile, state, did)
                req = self.modes._request(body, "ApiSemiFixedDungeonActorGroup")
                group = req.get("groupInfo")
                members = group.get("members", []) if isinstance(group, dict) else []
                if (method != "PUT" or not isinstance(members, list) or len(members) != 6
                        or any(not isinstance(m, dict) for m in members)
                        or [m.get("index", 0) for m in members] != list(range(6))):
                    return 400, {"error": "invalid_request"}
                import actors as actor_store
                owned = self.game.systems._state(profile)
                seen = set()
                for slot, (m, npc) in enumerate(zip(members, SEMI[did]["npcId"])):
                    uid = _int(m.get("actorUid"))
                    actor = actor_store.get(profile, owned, uid) if uid else None
                    if (m.get("isNpc") and (not npc or uid)) or (uid and (uid in seen or actor is None)):
                        return 400, {"error": "invalid_request"}
                    if actor and ACTOR_KINDS.get(_int(actor["id"])) != (1 if slot < 3 else 2):
                        return 400, {"error": "invalid_request"}
                    if uid:
                        seen.add(uid)
                state["groups"][str(did)] = {"groupInfo": {"members": [
                    {"index": m.get("index", 0), "isNpc": bool(m.get("isNpc")), "actorUid": _int(m.get("actorUid"))}
                    for m in members]}}
                self._save(profile, state)
                return 200, {}
            if not sub and method == "POST" and action in {"event-dungeon", "semifixed-dungeon"}:
                return self._enter(profile, body, action == "semifixed-dungeon")
            if sub and sub[0].isdigit() and action in {"event-dungeon", "semifixed-dungeon"}:
                uid = int(sub[0])
                run = self.modes._run(profile, uid)
                expected = "hidden_semi" if action == "semifixed-dungeon" else "hidden_event"
                if not run or run["mode"] != expected:
                    return 409, {"error": "dungeon_not_active"}
                if len(sub) == 1 and method == "DELETE":
                    return self.modes._close(profile, uid)
                if sub[1:] == ["active"] and method == "PATCH":
                    active = self.modes._parse_active(body)
                    if active is None or int(active.winTeam) not in (1, 2):
                        schema = "ApiSemiFixedDungeonActive" if action == "semifixed-dungeon" else "ApiEventDungeonActive"
                        wrapped = self.modes._request(body, schema)
                        active = self.modes._parse_active(wrapped.get("request"))
                    if run["status"] != "active":
                        return 409, {"error": "dungeon_not_active"}
                    if active is None or int(active.winTeam) not in (1, 2):
                        return 400, {"error": "invalid_request"}
                    # First terminal report is immutable across retries.
                    if run.get("active_data") and bytes(run["active_data"]) != active.SerializeToString():
                        return 409, {"error": "invalid_request"}
                    return self.modes._store_active(profile, uid, active.SerializeToString())
                if sub[1:] == ["balance"] and method == "POST":
                    return self._balance(profile, run)
            return 405, {"error": "invalid_request"}
