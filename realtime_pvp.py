"""Local RacePVP cycles and the legacy BurstLink simulation.

Race uses the real client HTTP battle lifecycle and extracted config tables.
Its local policy is explicit: one player plus 19 deterministic robot records
per cycle, rotating stages 3001-3004, and six non-accumulating cycle tickets.
The official schedule is Monday/Thursday 06:00 through Wednesday/Saturday
24:00, with eight cycles per season. The local season starts with the first
settled battle; no historical official season epoch was recovered. Robot
scores are fixed per cycle. Battle score is enemy TeamStatistic.totalDamage
// 10, matching Window_FightRound.AddPtVal. Cycle and season rewards use the
original RacePVPSeg and RacePVPSegReward tables.

The retired service used a WebSocket/FightService for these modes.  The
offline build cannot recover that service, so BurstLink still exposes the same
player-facing lifecycle as a small authenticated JSON state machine:

``info -> match -> round* -> balance``

Every match is seeded from the local account and match number.  Robot turns,
rank order, rewards and retry responses are therefore stable across process
restarts.  No external service, real player, or anti-cheat component is
contacted.  The route is intentionally separate from :mod:`social` so async
PVP and these round based modes cannot share or corrupt one another's state.
"""

from __future__ import annotations

from server.core.reset import policy_for, period_due

import hashlib
import json
import random
import re
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from google.protobuf import json_format, message_factory

try:  # Package import when local-server is installed as a package.
    from .storage.compat import shared_process_lock
    from .economy import _client_rows
    from .grants import grant_rewards
    from .local_mailbox import send_mail
except ImportError:  # pragma: no cover - direct PYTHONPATH execution
    from storage.compat import shared_process_lock
    from economy import _client_rows
    from grants import grant_rewards
    from local_mailbox import send_mail


Body = bytes | bytearray | str | None
Response = tuple[int, Any]


def _int(value: Any, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


BURST_ACTOR_KIND = {_int(row.get("id")): _int(row.get("kind"))
                    for row in _client_rows("ActorConfig")
                    if _int(row.get("id")) and _int(row.get("kind"))}


class RaceError(Exception):
    def __init__(self, error, status=409):
        self.error, self.status = error, status


class LocalRacePvp:
    """Persistent client-shaped room, ticket, run and reward state."""

    # activity_modes owns 9e12; ordinary dungeons use current epoch ms (~1e12).
    UID_BASE = 8_000_000_000_000
    TICKET_ID = 80004
    ACTIVITY_TYPE = 20
    # RacePVPStatus from descriptor_set.pb. Local matching/settlement happen
    # in the same transaction; the playable room is published in Fight.
    NONE, MATCH, FIGHT, SETTLE = 0, 1, 2, 3
    MISC = _client_rows("Misc")[0]
    PLAYER_COUNT = int(MISC["racePVPGroupMaxPlayer"])
    MAX_ROUNDS = int(MISC["racePVPRoundNum"])
    CYCLE_TICKETS = int(MISC["racePVPTicketNum"])
    SEASON_CYCLES = 8
    # Native create has no nonce. A fresh request with no uploaded battle is
    # replayable for transport/concurrent retries; a later entry starts anew.
    CREATE_RETRY_SECONDS = 30
    SEGMENTS = sorted(_client_rows("RacePVPSeg"), key=lambda row: row["score"])
    SEASON_REWARDS = {int(row["seg"]): row["rewards"] for row in _client_rows("RacePVPSegReward")}
    RANK_SCORES = {int(row["rank"]): int(row.get("score", 0)) for row in _client_rows("RacePVPRankScore")}
    SCORE_REWARDS = {int(row["battleScore"]): row["rewards"] for row in _client_rows("RacePVPBattleScoreReward")}
    HEAD_PHOTOS = frozenset(int(row["id"]) for row in _client_rows("PlayerHeadPhoto"))
    STAGES = {int(row["dungeonId"]): row for row in _client_rows("DungeonReward")
              if _int(row.get("type")) == 7 and 3001 <= _int(row.get("dungeonId")) <= 3004}
    ROUTES = {"/fci/racepvpinfo", "/fci/racepvpsummary", "/fci/racepvpbattlescorereward",
              "/fci/racepvpscore", "/fci/racepvp/info", "/fci/racepvp-dungeon"}

    def __init__(self, game, clock=None):
        self.game, self._clock = game, clock or game._now
        with game._lock, closing(game._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS local_race_pvp_state "
                         "(account_id INTEGER PRIMARY KEY,state_json TEXT NOT NULL,updated_at INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS local_race_pvp_runs "
                         "(dungeon_uid INTEGER PRIMARY KEY,account_id INTEGER NOT NULL,run_json TEXT NOT NULL)")

    def _now(self):
        return int(self._clock())

    @staticmethod
    def _copy(value):
        return json.loads(json.dumps(value))

    def _message(self, name, data=None):
        message = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(name))()
        if data is not None:
            json_format.ParseDict(data, message)
        return message

    @staticmethod
    def _dict(message):
        return json_format.MessageToDict(message, always_print_fields_with_no_presence=True)

    def _request(self, body, name):
        if isinstance(body, Mapping):
            return dict(body)
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return {}
        try:
            if raw.lstrip()[:1] in (b"{", b"["):
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("object required")
                return data
            message = self._message(name)
            message.ParseFromString(raw)
            return self._dict(message)
        except Exception as exc:
            raise RaceError("invalid_race_request", 400) from exc

    def _notify(self, profile, kind, params=()):
        def notify():
            callback = getattr(self.game, "base_push_notifier", None)
            if callable(callback):
                callback(profile, kind, params)
        self.game.after_commit(notify)

    def _save(self, profile, state):
        with closing(self.game._connect()) as conn, conn:
            conn.execute("INSERT INTO local_race_pvp_state(account_id,state_json,updated_at) VALUES(?,?,?) "
                         "ON CONFLICT(account_id) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at",
                         (profile["account_id"], json.dumps(state, ensure_ascii=False), self._now()))

    def _load(self, profile):
        with closing(self.game._connect()) as conn:
            row = conn.execute("SELECT state_json FROM local_race_pvp_state WHERE account_id=?", (profile["account_id"],)).fetchone()
        if row:
            return json.loads(row["state_json"])
        score = int(self.SEGMENTS[0]["score"])
        return {"week": None, "round": 0, "score": score, "oldScore": score, "day": None,
                "players": [], "rewardRecved": [], "activeUid": None, "isNew": True,
                "settlements": []}

    def _run(self, profile, uid):
        with closing(self.game._connect()) as conn:
            row = conn.execute("SELECT run_json FROM local_race_pvp_runs WHERE dungeon_uid=? AND account_id=?",
                               (uid, profile["account_id"])).fetchone()
        return json.loads(row["run_json"]) if row else None

    def _save_run(self, profile, run):
        with closing(self.game._connect()) as conn, conn:
            conn.execute("INSERT INTO local_race_pvp_runs(dungeon_uid,account_id,run_json) VALUES(?,?,?) "
                         "ON CONFLICT(dungeon_uid) DO UPDATE SET run_json=excluded.run_json",
                         (run["dungeonUid"], profile["account_id"], json.dumps(run, ensure_ascii=False)))

    def _ticket(self, profile, delta=0, minimum=None, *, notify=True, set_count=None):
        bag = self.game.systems._state(profile)
        item = bag.setdefault("items", {}).setdefault(str(self.TICKET_ID), {"count": 0})
        old = _int(item.get("count"))
        count = set_count if set_count is not None else (max(old, minimum) if minimum is not None else old + delta)
        if count < 0:
            raise RaceError("race_ticket_exhausted")
        if count != old:
            item["count"] = count
            if count > old:
                item["gotTime"] = self._now()
            self.game.systems._save(profile, bag)
            if notify:
                self._notify(profile, 3, (self.TICKET_ID,))
        return {"baseRes": {"type": 11, "id": self.TICKET_ID, "count": count, "param": 0, "countDelta": count - old}}

    def _actors(self, profile, group=None):
        ids, uids = self.game.dungeons._fight_team(profile, group)
        return ids, uids, [self.game.dungeons._fight_actor(profile, aid, uid) for aid, uid in zip(ids, uids)]

    def _room(self, profile, state, week):
        _, _, actors = self._actors(profile)
        players = [{"playerId": str(profile["player_id"]), "name": profile["name"], "battleScore": 0,
                    "timestamp": week, "score": state["score"], "level": profile["level"],
                    "headPhoto": 0, "socialPicture": "", "battleActors": actors}]
        for index in range(1, self.PLAYER_COUNT):
            bot = self.game.social.bots.all()[(index - 1) % len(self.game.social.bots.all())]
            container = self.game._types["DBGamePlayer.Actors"]()
            for pos, aid in enumerate(bot["actors"], 1):
                self.game._append_local_actor(container, aid, pos, pos - 1, level=bot["level"])
            # These are explicitly local robot records, not recovered scores.
            score = 2000 + LocalRealtimePvp._digest_int(profile["account_id"], week, index, "race-score", modulo=31001)
            players.append({"playerId": f"local-race-bot-{index:02d}", "name": bot["name"],
                            "battleScore": score, "timestamp": week, "score": state["score"],
                            "level": bot["level"], "headPhoto": self.game.profile_features.bot_head_photo(bot), "socialPicture": "",
                            "battleActors": [self._dict(actor) for actor in container.player_actors]})
        return players

    def _refresh_appearance(self, profile, players):
        features = self.game.profile_features
        own = features._state(profile)
        for player in players:
            if player["playerId"] == str(profile["player_id"]):
                photo = features._head_photo_value(own)
                # Zero is the native platform-picture sentinel. A positive
                # value must index PlayerHeadPhoto, never ActorConfig.
                player["headPhoto"] = photo if photo == 0 or photo in self.HEAD_PHOTOS else 1
                player["socialPicture"] = str(own.get("socialPicture", ""))
            elif _int(player.get("headPhoto")) not in self.HEAD_PHOTOS:
                # Repair existing weekly rooms from the old actor-id avatar
                # implementation without recreating scores, runs or rewards.
                actors = [actor["id"] for actor in player.get("battleActors", []) if _int(actor.get("id")) > 0]
                player["headPhoto"] = features.bot_head_photo({"actors": actors})

    @staticmethod
    def _ranked(players):
        return sorted(players, key=lambda row: (-row["battleScore"], row["timestamp"], row["playerId"]))

    def _cycle_at(self, now):
        local = policy_for(self.game).local_datetime(now)
        monday = (local - timedelta(days=local.weekday())).replace(hour=6, minute=0, second=0, microsecond=0)
        if local < monday:
            monday -= timedelta(days=7)
        slot = int(local >= monday + timedelta(days=3))
        index = ((monday.date() - date(1970, 1, 5)).days // 7) * 2 + slot
        return self._cycle_by_index(index)

    def _cycle_by_index(self, index):
        monday = datetime.combine(date(1970, 1, 5) + timedelta(weeks=index // 2),
                                  datetime.min.time(), policy_for(self.game).timezone).replace(hour=6)
        start = monday + timedelta(days=3 if index % 2 else 0)
        return {"index": index, "start": int(start.timestamp()),
                "end": int((start + timedelta(days=2, hours=18)).timestamp()),
                "nextStart": int((start + timedelta(days=4 if index % 2 else 3)).timestamp())}

    def _segment(self, score):
        return max((row for row in self.SEGMENTS if row["score"] <= score), key=lambda row: row["score"])

    def _mail(self, profile, title, rewards, *, start_time=None):
        if not rewards:
            return None
        return send_mail(self.game, profile, title, "残梦之境结算奖励，请领取附件。", rewards,
                         sender_name="残梦之境", start_time=start_time, now=self._now())["id"]

    def _compensate_run(self, profile, state, run, high_score):
        if not run.get("compensated"):
            current = self._cycle_at(self._now())
            start = self._now() if self._now() < current["end"] else current["nextStart"]
            run["compensationMailId"] = self._mail(profile, "残梦之境跨周期门票补偿",
                                                   [{"type": 11, "id": self.TICKET_ID, "count": 1}], start_time=start)
            state.setdefault("refundMails", []).append(run["compensationMailId"])
            run.update(compensated=True, expired=True, expiredHighScore=high_score)
            self._save_run(profile, run)

    def _collect_refunds(self, profile, state, *, can_use_tickets):
        """Keep a claimed refund through a closed-period ticket clear.

        Mail attachments are ordinary items. A separate durable credit is
        needed when the user claims an older refund during a later closure,
        or before the new cycle's base tickets have been initialized.
        """
        if not state.get("refundMails"):
            return
        mails = {mail["id"]: mail for mail in self.game.systems._state(profile).get("mails", [])}
        claimed = set(state.get("claimedRefundMails", []))
        remaining = []
        for mail_id in state["refundMails"]:
            mail = mails.get(mail_id)
            if mail_id in claimed or (mail is not None and mail.get("attachmentsGot")):
                if not can_use_tickets:
                    state["pendingTicketRefunds"] = _int(state.get("pendingTicketRefunds")) + 1
            elif mail is not None:
                remaining.append(mail_id)
        state["refundMails"] = remaining
        state["claimedRefundMails"] = []

    def record_mail_claims(self, profile, mail_ids):
        """Mailbox hook before auto-delete, joined to its grant transaction."""
        state = self._load(profile)
        claimed = set(map(int, mail_ids)) & set(state.get("refundMails", []))
        if claimed:
            state["claimedRefundMails"] = sorted(set(state.get("claimedRefundMails", [])) | claimed)
            self._save(profile, state)

    def _settle_cycle(self, profile, state):
        if state.get("cycleSettled"):
            return
        run = self._run(profile, state["activeUid"]) if state.get("activeUid") else None
        if run and run["status"] == "active":
            # The native client uploads on finish/surrender. A received final
            # snapshot is scored in its original room before that room closes.
            if run.get("active"):
                try:
                    self._battle_score(run)
                except RaceError:
                    self._compensate_run(profile, state, run, self._own(profile, state)["battleScore"])
                else:
                    self._balance(profile, state, run)
            else:
                self._compensate_run(profile, state, run, self._own(profile, state)["battleScore"])
        if not state.get("enrolled"):
            # Ticket initialization/room browsing is not battle participation.
            # An unfinished charged run above still receives its refund.
            state.update(cycleSettled=True, activeUid=None, isNew=True)
            return
        ranked = self._ranked(state["players"])
        own = self._own(profile, state)
        rank = next(i for i, row in enumerate(ranked, 1) if row["playerId"] == profile["player_id"]) if own["battleScore"] else self.PLAYER_COUNT
        old = state["score"]
        segment = self._segment(old)
        floor = segment["score"] if segment.get("protect") else self.SEGMENTS[0]["score"]
        state["score"] = max(floor, old + self.RANK_SCORES[rank])
        state["oldScore"] = old
        pending = sorted(threshold for threshold in self.SCORE_REWARDS
                         if threshold <= own["battleScore"] and threshold not in state["rewardRecved"])
        rewards = list(self._segment(state["score"])["rewards"])
        rewards += [reward for threshold in pending for reward in self.SCORE_REWARDS[threshold]]
        mail_id = self._mail(profile, "残梦之境周期结算", rewards)
        state["rewardRecved"].extend(pending)
        state["settlements"].append({"cycle": state["cycle"], "week": state["week"], "rank": rank,
                                     "before": old, "after": state["score"], "mailId": mail_id,
                                     "scoreRewardsMailed": pending})
        state["settlements"] = state["settlements"][-64:]
        state.update(cycleSettled=True, activeUid=None, isNew=True)

    def _settle_season(self, profile, state, now):
        origin = state.get("seasonOrigin")
        participated = state.get("lastParticipatedCycle")
        if origin is None or participated is None:
            return
        season = (participated - origin) // self.SEASON_CYCLES
        end = self._cycle_by_index(origin + (season + 1) * self.SEASON_CYCLES - 1)["end"]
        if now < end or state.get("seasonPaid", -1) >= season:
            return
        segment = self._segment(state["score"])
        mail_id = self._mail(profile, "残梦之境赛季结算", self.SEASON_REWARDS[segment["seg"]])
        state.setdefault("seasonSettlements", []).append({"season": season, "score": state["score"],
                                                          "segId": segment["id"], "mailId": mail_id})
        state["seasonSettlements"] = state["seasonSettlements"][-16:]
        state.update(seasonPaid=season, score=int(self.SEGMENTS[0]["score"]), isNew=True)

    def _migrate_participation(self, profile, state):
        """Repair old login-created participation using durable battle evidence.

        Keep past score/mail payouts and their season anchor intact. Only the
        eligibility flags change; no already granted player asset is removed.
        """
        if state.get("participationVersion") == 1:
            return
        with closing(self.game._connect()) as conn:
            rows = conn.execute("SELECT run_json FROM local_race_pvp_runs WHERE account_id=?",
                                (profile["account_id"],)).fetchall()
        cycles = []
        for row in rows:
            run = json.loads(row["run_json"])
            if (run.get("balance") is None or run.get("expired") or run.get("compensated")
                    or not self._has_result(run)):
                continue
            if run.get("cycle") is None and run.get("week") is not None:
                # Older weekly rooms only migrated the current room's runs.
                # Preserve real battles from previous weeks as well.
                when = _int(run.get("createdAt")) or _int(run["week"])
                cycle = self._cycle_at(when)
                run.update(cycle=cycle["index"], cycleEnd=cycle["end"])
                self._save_run(profile, run)
            if run.get("cycle") is not None:
                cycles.append(run["cycle"])
        state["enrolled"] = state["cycle"] in cycles
        state["lastParticipatedCycle"] = max(cycles) if cycles else None
        if not cycles and not state.get("seasonSettlements"):
            state["seasonOrigin"] = None
        elif cycles and state.get("seasonOrigin") is None:
            state["seasonOrigin"] = min(cycles)
        elif cycles and not state.get("seasonSettlements"):
            state["seasonOrigin"] = min(state["seasonOrigin"], min(cycles))
        state["participationVersion"] = 1

    def _refresh(self, profile, state, *, notify=True):
        changes = []
        now = max(self._now(), _int(state.get("lastSeenAt")))
        current = self._cycle_at(now)
        unlocked = _int(profile.get("level")) >= 26
        if "cycle" not in state:
            # A one-time upgrade preserves an existing room, its rewards and
            # runs. Current legacy weekly rooms become the current cycle.
            legacy_week = state.get("week")
            same_week = legacy_week is not None and policy_for(self.game).period_start(now, "week") == legacy_week
            initial = current if legacy_week is None or same_week else self._cycle_at(legacy_week)
            state.update(cycle=initial["index"], cycleEnd=initial["end"], nextStart=initial["nextStart"],
                         week=initial["start"], cycleSettled=False, enrolled=False,
                         seasonOrigin=initial["index"] - max(0, _int(state.get("round"), 1) - 1) if legacy_week is not None else None,
                         schemaVersion=2)
            state["round"] = max(1, _int(state.get("round")))
            if not state["players"]:
                state["dungeonId"] = sorted(self.STAGES)[initial["index"] % len(self.STAGES)]
                state["players"] = self._room(profile, state, initial["start"])
            if legacy_week is not None:
                with closing(self.game._connect()) as conn:
                    rows = conn.execute("SELECT run_json FROM local_race_pvp_runs WHERE account_id=?", (profile["account_id"],)).fetchall()
                for row in rows:
                    run = json.loads(row["run_json"])
                    if "cycle" not in run and run.get("week") == legacy_week:
                        run.update(cycle=initial["index"], cycleEnd=initial["end"])
                        self._save_run(profile, run)
                # Preserve tickets already spent in the migrated cycle.
                if unlocked:
                    state["ticketCycle"] = initial["index"]
        self._migrate_participation(profile, state)
        if now >= state["cycleEnd"]:
            self._settle_cycle(profile, state)
            self._settle_season(profile, state, now)
        if current["index"] > state["cycle"]:
            state.update(cycle=current["index"], cycleEnd=current["end"], nextStart=current["nextStart"],
                         week=current["start"], cycleSettled=False, enrolled=False,
                         rewardRecved=[], activeUid=None, isNew=True)
            state["dungeonId"] = sorted(self.STAGES)[current["index"] % len(self.STAGES)]
            state["players"] = self._room(profile, state, current["start"])
        is_open = now < state["cycleEnd"] and unlocked and not state.get("cycleSettled")
        tickets_initialized = state.get("ticketCycle") == state["cycle"]
        self._collect_refunds(profile, state, can_use_tickets=is_open and tickets_initialized)
        if is_open:
            pending = _int(state.get("pendingTicketRefunds"))
            if not tickets_initialized:
                changes.append(self._ticket(profile, set_count=self.CYCLE_TICKETS + pending, notify=notify))
                state["ticketCycle"] = state["cycle"]
                state["pendingTicketRefunds"] = 0
            elif pending:
                changes.append(self._ticket(profile, delta=pending, notify=notify))
                state["pendingTicketRefunds"] = 0
        else:
            changes.append(self._ticket(profile, set_count=0, notify=notify))
        if state.get("seasonOrigin") is not None:
            state["round"] = (state["cycle"] - state["seasonOrigin"]) % self.SEASON_CYCLES + 1
        state.update(status=self.FIGHT if is_open else self.MATCH,
                     endTime=state["cycleEnd"] if is_open else state["nextStart"], lastSeenAt=now)
        self._refresh_appearance(profile, state["players"])
        self._save(profile, state)
        return [change for change in changes if change["baseRes"]["countDelta"]]

    def refresh_daily_items(self, profile):
        """Return persisted ticket deltas; the scheduler publishes them once.

        Login already sends the full ItemPackage. Direct Race HTTP requests
        use _refresh's normal notification path instead of this hook.
        """
        with self.game.atomic_mutation():
            return self._refresh(profile, self._load(profile), notify=False)

    def summary(self, profile):
        """Canonical summary for login/redpoint aggregation."""
        with self.game.atomic_mutation():
            state = self._load(profile)
            self._refresh(profile, state)
            return {"rewardTag": self._reward_tag(profile, state)}

    def _info(self, state):
        return {"dungeonId": state["dungeonId"], "players": self._ranked(state["players"]),
                "oldScore": state["oldScore"], "score": state["score"], "isNew": state["isNew"],
                "endTime": state["endTime"], "state": state["status"], "round": state["round"]}

    def _own(self, profile, state):
        return next(row for row in state["players"] if row["playerId"] == profile["player_id"])

    def _reward_tag(self, profile, state):
        if state["status"] != self.FIGHT:
            return False
        score = self._own(profile, state)["battleScore"]
        return any(threshold <= score and threshold not in state["rewardRecved"] for threshold in self.SCORE_REWARDS)

    def _grant(self, profile, rewards):
        changes = grant_rewards(self.game, profile, rewards)
        bag = self.game.systems._state(profile)
        for row in changes:
            res = row.get("baseRes", {})
            if res.get("type") == 11:
                bag["items"][str(res["id"])]["gotTime"] = self._now()
                self._notify(profile, 3, (res["id"],))
        self.game.systems._save(profile, bag)
        return changes

    def _create(self, profile, state, body):
        request = self._request(body, "ApiRacePVPDungeon")
        stage = _int(request.get("dungeonId"))
        if stage != state["dungeonId"] or stage not in self.STAGES:
            raise RaceError("race_dungeon_not_current", 400)
        if _int(profile.get("level")) < 26:
            raise RaceError("race_level_locked", 403)
        if state["status"] != self.FIGHT:
            raise RaceError("race_cycle_closed")
        ids, uids, actors = self._actors(profile, request.get("selectActorGroupId"))
        active = self._run(profile, state["activeUid"]) if state["activeUid"] else None
        recovered = False
        if active and active["status"] == "active":
            same_team = (active["baseInfo"]["actorId"] == ids and active["baseInfo"]["actorUid"] == uids
                         and active["baseInfo"]["actors"] == actors)
            if same_team and not active.get("active") and 0 <= self._now() - active["createdAt"] <= self.CREATE_RETRY_SECONDS:
                return 200, {"dungeonUid": active["dungeonUid"], "baseInfo": active["baseInfo"], "resChange": [self._ticket(profile)]}
            if self._has_result(active):
                # Race only uploads active on finish/surrender. A lost balance
                # reply must not make the next native create discard its score.
                self._balance(profile, state, active)
                recovered = True
            else:
                active.update(status="closed", closedAt=self._now(), closeReason="new_entry")
                self._save_run(profile, active)
        if not any(uids):
            raise RaceError("profile_has_no_actors")
        cost = self.STAGES[stage]["cost"]
        if len(cost) != 1 or (cost[0]["type"], cost[0]["id"], cost[0]["count"]) != (11, self.TICKET_ID, 1):
            raise RaceError("unsupported_race_cost")
        if recovered and self._ticket(profile)["baseRes"]["count"] < 1:
            # Commit the old paid-for result even if no ticket remains for a
            # new battle. Raising here would roll that recovery back as well.
            return 409, {"error": "race_ticket_exhausted"}
        change = self._ticket(profile, -1)
        now = self._now()
        with closing(self.game._connect()) as conn:
            uid = max(_int(conn.execute("SELECT MAX(dungeon_uid) FROM local_race_pvp_runs").fetchone()[0]), self.UID_BASE) + 1
        # Only fields declared by DbDungeon.BaseInfo are returned.
        base = {"dungeonId": stage, "dungeonType": 7, "activityType": self.ACTIVITY_TYPE, "activityId": 0,
                "actorUid": uids, "actorId": ids, "actors": actors, "createTime": now, "quitTime": 0,
                "auraPlayerId": "", "auraActorId": 0, "auraStranger": False, "auraActorQuality": 0,
                "towerFloor": 0, "eventKey": "", "medalId": 0, "reduceInfo": {}, "isChallengeMode": False,
                "fightBuffs": [], "npcCnt": 0}
        run = {"dungeonUid": uid, "week": state["week"], "cycle": state["cycle"], "cycleEnd": state["cycleEnd"],
               "status": "active", "baseInfo": base,
               "active": None, "balance": None, "createdAt": now}
        self._save_run(profile, run)
        state["activeUid"] = uid
        self._save(profile, state)
        return 200, {"dungeonUid": uid, "baseInfo": base, "resChange": [change]}

    def _active(self, profile, run, body):
        data = self._request(body, "FightActiveData")
        try:
            message = self._message("FightActiveData", data)
        except Exception as exc:
            raise RaceError("invalid_race_active", 400) from exc
        if message.dungeonType != 7 or message.stageID != run["baseInfo"]["dungeonId"]:
            raise RaceError("race_active_stage_mismatch", 400)
        if not 0 <= message.gameRound <= self.MAX_ROUNDS:
            raise RaceError("invalid_race_round", 400)
        data = self._dict(message)
        previous = run.get("active")
        if run["status"] != "active":
            if data == previous:
                return 200, {}
            raise RaceError("race_dungeon_closed")
        if previous and _int(previous.get("gameRound")) > message.gameRound:
            return 200, {}  # late snapshot cannot rewind the battle
        if previous and _int(previous.get("winTeam")) in (1, 2) and previous != data:
            raise RaceError("race_battle_already_finished")
        run["active"] = data
        self._save_run(profile, run)
        return 200, {}

    @staticmethod
    def _battle_score(run):
        active = run.get("active")
        if not active:
            raise RaceError("race_fight_snapshot_required")
        # totalDamage measures damage RECEIVED by the team, not damage dealt.
        enemy = [team for team in active.get("teamList", []) if _int(team.get("team")) == 2]
        if len(enemy) != 1 or "statistic" not in enemy[0]:
            raise RaceError("race_enemy_statistics_required", 400)
        damage = _int(enemy[0]["statistic"].get("totalDamage"))
        if damage < 0:
            raise RaceError("invalid_race_damage", 400)
        return damage // 10

    def _has_result(self, run):
        active = run.get("active") or {}
        if (_int(active.get("dungeonType")) != 7
                or _int(active.get("stageID")) != _int(run.get("baseInfo", {}).get("dungeonId"))):
            return False
        try:
            self._battle_score(run)
        except RaceError:
            return False
        # Both normal end and LeaveRacePvp(3) upload a final snapshot. Surrender
        # can have winTeam=0 and zero damage; neither excludes participation.
        return True

    def _balance(self, profile, state, run):
        if run["balance"] is not None:
            return 200, self._copy(run["balance"])
        if run["status"] != "active":
            raise RaceError("race_dungeon_closed")
        score = self._battle_score(run)
        if run.get("expired") or run.get("cycle") != state["cycle"] or state.get("cycleSettled"):
            # A delayed finish cannot rewrite a published old ranking or
            # contaminate the next room. Refund the charged ticket once and
            # still satisfy the client's normal balance/DELETE flow.
            self._compensate_run(profile, state, run, _int(run.get("expiredHighScore")))
            high_score = max(score, _int(run.get("expiredHighScore")))
        else:
            state["enrolled"] = True
            state["lastParticipatedCycle"] = state["cycle"]
            if state.get("seasonOrigin") is None:
                state["seasonOrigin"] = state["cycle"]
                state["round"] = 1
            own = self._own(profile, state)
            if score > own["battleScore"]:
                own.update(battleScore=score, timestamp=self._now(), battleActors=run["baseInfo"]["actors"])
            high_score = own["battleScore"]
        # Common/DungeonUtil also balances a player's early surrender; the
        # persisted real damage is valid then even when winTeam is still 0.
        result = {"curBattleScore": score, "highBattleScore": high_score}
        run.update(status="balanced", balance=result, balancedAt=self._now())
        self._save_run(profile, run)
        self._save(profile, state)
        return 200, result

    def _claim_reward(self, profile, state, body):
        request = self._request(body, "ApiRacePVPBattleScoreReward")
        threshold = _int(request.get("battleScore"))
        if threshold not in self.SCORE_REWARDS:
            raise RaceError("race_reward_not_found", 404)
        if threshold in state["rewardRecved"]:
            return 200, {"resChange": []}
        if self._own(profile, state)["battleScore"] < threshold:
            raise RaceError("race_reward_not_earned")
        changes = self._grant(profile, self.SCORE_REWARDS[threshold])
        state["rewardRecved"].append(threshold)
        self._save(profile, state)
        return 200, {"resChange": changes}

    def handle_http(self, method, path, body, profile):
        route = urlsplit(path).path.rstrip("/").casefold()
        own_match = re.fullmatch(r"/fci/racepvp-dungeon/(\d+)(?:/(active|balance))?", route)
        shared_match = re.fullmatch(r"/fci/dungeon/(\d+)(?:/(active|balance))?", route)
        match = own_match or shared_match
        if route not in self.ROUTES and not match:
            return None
        if shared_match and self._run(profile, int(shared_match[1])) is None:
            return None
        try:
            with self.game.atomic_mutation():
                state = self._load(profile)
                self._refresh(profile, state)
                method = method.upper()
                if route in ("/fci/racepvpinfo", "/fci/racepvp/info") and method == "GET":
                    result = self._info(state)
                    state["isNew"] = False
                    state["oldScore"] = state["score"]
                    self._save(profile, state)
                    return 200, result
                if route == "/fci/racepvpsummary" and method == "GET":
                    return 200, {"rewardTag": self._reward_tag(profile, state)}
                if route == "/fci/racepvpscore" and method == "GET":
                    segment = max((r for r in self.SEGMENTS if r["score"] <= state["score"]), key=lambda r: r["score"])
                    return 200, {"segId": segment["id"], "score": state["score"]}
                if route == "/fci/racepvpbattlescorereward":
                    if method == "GET":
                        return 200, {"rewardRecved": list(state["rewardRecved"])}
                    if method == "POST":
                        return self._claim_reward(profile, state, body)
                if route == "/fci/racepvp-dungeon" and method == "POST":
                    return self._create(profile, state, body)
                if match:
                    run = self._run(profile, int(match[1]))
                    if run is None:
                        raise RaceError("race_dungeon_not_found", 404)
                    action = match[2]
                    if action == "active" and method in ("PATCH", "PUT", "POST"):
                        return self._active(profile, run, body)
                    if action == "active" and method == "GET":
                        return 200, run.get("active") or {}
                    if action == "balance" and method == "POST":
                        return self._balance(profile, state, run)
                    if action is None and method == "DELETE":
                        if run["status"] == "active":
                            run.update(status="closed", closedAt=self._now())
                            self._save_run(profile, run)
                        if state["activeUid"] == run["dungeonUid"]:
                            state["activeUid"] = None
                            self._save(profile, state)
                        return 200, {}
                return 405, {"error": "method_not_allowed"}
        except RaceError as exc:
            return exc.status, {"error": exc.error}


def _json(body: Body) -> dict[str, Any]:
    if body is None:
        return {}
    raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


class LocalRealtimePvp:
    """Persistent, deterministic local implementation for real-time modes."""

    RACE_ROUNDS = 8
    RACE_PLAYERS = LocalRacePvp.PLAYER_COUNT
    RACE_TICKETS = 6
    BURST_ROUNDS = 5
    BURST_PLAYERS = 3
    BURST_TICKETS = 10
    BURST_WEEKLY_GRANT = 2
    # Client 2.0.4's native BurstLink constants.  The local matcher publishes
    # a deterministic robot room and then enters the normal local FightLua
    # scene.  80003 is the extracted DailyRefresh type-2 ticket (two per
    # Beijing week, capped at ten); the type-18 RefreshPrice row is the
    # client's one-ticket payment before matching.
    BURST_ACTIVITY_TYPE = 18
    BURST_ACTIVITY_ID = 18001
    # No BurstLink-specific map survived extraction.  4001 is the recovered
    # playable async arena map; dungeonType=8 keeps the BurstLink combat rules
    # while allowing the stock local scene to load its DungeonMonster data.
    BURST_DUNGEON_ID = 4001
    BURST_TICKET_ID = 80003
    BURST_MAX_WINS = 5
    BURST_MAX_LOSSES = 3

    def __init__(self, game: Any, *, clock=None) -> None:
        self.game = game
        self._lock = game._lock
        self._clock = clock or game._now
        self.race = LocalRacePvp(game, clock=self._clock)
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_realtime_pvp_state (
                    account_id INTEGER PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )

    def _now(self) -> int:
        return int(self._clock())

    def _day_key(self) -> str:
        return policy_for(self.game).day_key(self._now())

    def _next_reset(self) -> int:
        return policy_for(self.game).next_boundary(self._now())

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "race": {"day": "", "tickets": LocalRealtimePvp.RACE_TICKETS,
                     "rating": 1000, "wins": 0, "losses": 0,
                     "nextMatch": 1, "matches": [], "active": None},
            "burst": {"day": "", "tickets": 0, "inventoryTickets": None,
                      "wins": 0, "losses": 0, "nextMatch": 1,
                      "matches": [], "active": None},
        }

    def _state(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        account_id = int(profile["account_id"])
        with self._lock, closing(self.game._connect()) as connection, connection:
            row = connection.execute(
                "SELECT state_json FROM local_realtime_pvp_state WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                state = self._default_state()
                connection.execute(
                    "INSERT INTO local_realtime_pvp_state(account_id,state_json,updated_at) VALUES (?,?,?)",
                    (account_id, json.dumps(state, separators=(",", ":")), self._now()),
                )
            else:
                try:
                    state = json.loads(row["state_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    state = self._default_state()
            if not isinstance(state, dict):
                state = self._default_state()
            defaults = self._default_state()
            for mode, default in defaults.items():
                value = state.get(mode)
                if not isinstance(value, dict):
                    state[mode] = dict(default)
                    continue
                for key, fallback in default.items():
                    value.setdefault(key, fallback)
            self._refresh_tickets(state, profile)
            return state

    def _burst_inventory_count(self, profile: Mapping[str, Any]) -> int:
        """Read the canonical 80003 inventory after weekly refresh settlement."""
        try:
            state = self.game.systems._state(profile)
            return max(0, _int((state.get("items") or {}).get(str(self.BURST_TICKET_ID), {}).get("count")))
        except Exception:
            return 0

    def _refresh_tickets(self, state: dict[str, Any], profile: Mapping[str, Any] | None = None) -> None:
        day = self._day_key()
        bucket = state["race"]
        if period_due(bucket.get("day"), day):
            bucket["day"] = day
            bucket["tickets"] = self.RACE_TICKETS
        if profile is None:
            return
        burst = state["burst"]
        current = self._burst_inventory_count(profile)
        previous = burst.get("inventoryTickets")
        if previous is None:
            burst["tickets"] = min(self.BURST_TICKETS, current)
        elif current != _int(previous):
            burst["tickets"] = max(0, min(self.BURST_TICKETS,
                                           _int(burst.get("tickets")) + current - _int(previous)))
        burst["inventoryTickets"] = current

    def _save(self, profile: Mapping[str, Any], state: Mapping[str, Any]) -> None:
        with self._lock, closing(self.game._connect()) as connection, connection:
            connection.execute(
                "UPDATE local_realtime_pvp_state SET state_json = ?, updated_at = ? WHERE account_id = ?",
                (json.dumps(dict(state), ensure_ascii=False, separators=(",", ":")), self._now(), int(profile["account_id"])),
            )

    @staticmethod
    def _route(path: str) -> str:
        return urlsplit(path or "").path.rstrip("/").casefold() or "/"

    @staticmethod
    def _digest_int(*values: object, modulo: int = 10_000) -> int:
        raw = "|".join(str(value) for value in values).encode("utf-8")
        return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big") % modulo

    def _bot(self, account_id: int, match_no: int, index: int, *, burst: bool = False) -> dict[str, Any]:
        names = ("御坂训练员", "黑雪姬", "桐人练习生", "亚丝娜助手", "司波机器人")
        player_id = f"local-{'burst' if burst else 'race'}-bot-{index:02d}"
        return {
            "playerId": player_id,
            "name": names[(account_id + match_no + index) % len(names)],
            "level": 1 + self._digest_int(account_id, match_no, index, "level", modulo=45),
            "actorIds": [1001 + self._digest_int(account_id, match_no, index, n, modulo=8) for n in range(3)],
            "isRobot": True,
            "score": 0,
        }

    def _player(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        actor_ids = [int(value) for value in profile.get("actor_ids", []) if str(value).isdigit()]
        if not actor_ids and profile.get("actor_id") is not None:
            actor_ids = [int(profile["actor_id"])]
        return {
            "playerId": str(profile.get("player_id", "local")),
            "name": str(profile.get("name", "本地玩家")) or "本地玩家",
            "level": int(profile.get("level", 1)),
            "actorIds": actor_ids[:3] or [1001],
            "isRobot": False,
            "score": 0,
        }

    # ----------------------------------------------------------- native BurstLink
    # The following helpers mirror the names and JSON fields of the recovered
    # ApiBurstLink* messages.  They intentionally share the existing
    # local_realtime_pvp_state row with the compact compatibility state machine
    # above, keeping retries/restarts idempotent without a second ledger.
    @staticmethod
    def _native_default() -> dict[str, Any]:
        return {"main": [], "assistant": [], "groups": {"1": {"members": []}},
                "active": None, "lastWin": False, "lastBalanced": False,
                "received": False, "randSeed": 0, "status": 0, "paid": False}

    def _native(self, state: dict[str, Any]) -> dict[str, Any]:
        value = state["burst"].get("native")
        if not isinstance(value, dict):
            value = self._native_default()
            state["burst"]["native"] = value
        defaults = self._native_default()
        for key, fallback in defaults.items():
            # ``active`` intentionally defaults to ``None`` and becomes a
            # dictionary after matching.  ``isinstance(active, type(None))``
            # would erase that persisted run every time the state was read.
            # For nullable fields, validate only that the key exists; all
            # non-null values are consumed through the guarded checks below.
            if key not in value or (fallback is not None and not isinstance(value[key], type(fallback))):
                value[key] = fallback.copy() if isinstance(fallback, (dict, list)) else fallback
        return value

    def _native_actors(self, profile: Mapping[str, Any]) -> list[dict[str, Any]]:
        # BurstLink's three-choice cards are temporary level-1 actors from the
        # BurstLink pool, not the currently selected story group (and not a
        # restriction to characters already owned by the account).  Preserve
        # owned instance UIDs where available, then allocate stable synthetic
        # UIDs for the remaining client-table actors.  The selected cards are
        # still represented by ordinary STActor records, so the local scene
        # can consume their id/uid pair without a remote FightService.
        try:
            from . import actors as actor_store
        except ImportError:  # pragma: no cover - direct module import
            import actors as actor_store
        state = self.game.systems._state(profile)
        records = actor_store.ordered(profile, state)
        seen_ids = set()
        actors = []
        for record in records:
            actor = self.game.dungeons._fight_actor(profile, int(record["id"]), int(record["uid"]))
            if actor:
                actors.append(actor)
                seen_ids.add(int(record["id"]))
        for actor_id in sorted(actor_store.BASE_QUALITY):
            if actor_id in seen_ids:
                continue
            uid = 1_000_000 + int(actor_id)
            actor = self.game.dungeons._fight_actor(profile, int(actor_id), uid)
            if actor:
                actors.append(actor)
        if not actors:
            actor = self.game.dungeons._fight_actor(profile, int(profile.get("actor_id") or 1001), 1)
            if actor:
                actors.append(actor)
        return actors

    def _native_request(self, body: Body, message_name: str) -> dict[str, Any]:
        """Decode JSON or the exact protobuf request emitted by the client."""
        if isinstance(body, Mapping):
            return dict(body)
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body or b"")
        if not raw:
            return {}
        if raw.lstrip()[:1] in (b"{", b"["):
            try:
                value = json.loads(raw.decode("utf-8"))
                return dict(value) if isinstance(value, Mapping) else {}
            except (UnicodeDecodeError, ValueError):
                return {}
        try:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName(message_name))
            message = cls(); message.ParseFromString(raw)
            return json_format.MessageToDict(message)
        except Exception:
            return {}

    @staticmethod
    def _uid(actor: Mapping[str, Any]) -> int:
        return _int(actor.get("uid", actor.get("actorUid", 0)))

    def _native_info(self, profile: Mapping[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        bucket = state["burst"]
        native = self._native(state)
        active = native.get("active") if isinstance(native.get("active"), dict) else None
        return {
            # Compatibility keys retained for the existing local dashboard;
            # the native client ignores unknown JSON fields.
            "mode": "burst",
            "tickets": int(bucket.get("tickets", 0)),
            "maxTickets": int(self.BURST_TICKETS),
            "playerId": str(profile.get("player_id", "local")),
            "isRobot": False,
            "expireSeconds": 0,
            "baseInfo": {
                "createTime": int(active.get("createdAt", self._now()) if active else self._now()),
                "quitTime": 0,
                "level": int(profile.get("level", 1) or 1),
                "name": str(profile.get("name", "本地玩家")),
                "winNum": int(bucket.get("wins", 0)),
                "failNum": int(bucket.get("losses", 0)),
            },
            "balanceInfo": {"balanced": bool(native.get("lastBalanced")), "isWin": bool(native.get("lastWin"))},
            "actorsInfo": {
                "curMainActor": [_int(v) for v in native.get("main", [])],
                "curAssistantActor": [_int(v) for v in native.get("assistant", [])],
                "battleActorGroup": [_int(v) for v in native.get("main", []) + native.get("assistant", [])],
                "playerActors": [],
            },
            "matchInfo": {},
            "dungeonId": int(self.BURST_DUNGEON_ID),
            "cost": {"type": 11, "id": self.BURST_TICKET_ID, "count": 1},
            "reward": self._native_rewards(),
            "resChange": [],
        }

    @staticmethod
    def _native_rewards() -> list[dict[str, Any]]:
        # No BurstLink reward table survived extraction.  Keep a visible,
        # deterministic cumulative table using the local gold resource; the
        # policy is recorded as confidence D in docs/本地规则清单.md.
        return [{"id": i, "reward": [{"type": 7, "id": 0, "count": 100 * i, "param": 0}],
                 "nextReward": []} for i in range(1, 6)]

    def _native_rand_actor(self, profile: Mapping[str, Any], native: dict[str, Any], actor_type: int = 0) -> dict[str, Any]:
        pool = self._native_actors(profile)
        if actor_type in {1, 2}:
            wanted_kind = 1 if actor_type == 1 else 2
            typed = [actor for actor in pool if BURST_ACTOR_KIND.get(_int(actor.get("id"))) == wanted_kind]
            if typed:
                pool = typed
        selected = {_int(v) for v in native.get("main", []) + native.get("assistant", [])}
        choices = [actor for actor in pool if self._uid(actor) not in selected]
        if not choices:
            choices = pool
        # Stable pseudo-random order changes when the account/match/slot
        # changes, while retries of the same GET return the same three cards.
        seed = native.get("randSeed", 0) or 0
        choices = sorted(choices, key=lambda actor: self._digest_int(
            profile.get("account_id"), seed, actor_type, self._uid(actor), modulo=2**31 - 1))
        return {
            "randActor": choices[:3],
            "selectActor": [actor for actor in pool if self._uid(actor) in selected],
            "curMainActor": [_int(v) for v in native.get("main", [])],
            "curAssistantActor": [_int(v) for v in native.get("assistant", [])],
        }

    def _native_group(self, profile: Mapping[str, Any], state: dict[str, Any], payload: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        native = self._native(state)
        groups = payload.get("actorGroups") if isinstance(payload, Mapping) else None
        if isinstance(groups, Mapping):
            clean = {}
            for key, group in groups.items():
                if not isinstance(group, Mapping):
                    continue
                members = []
                for member in group.get("members") or []:
                    if not isinstance(member, Mapping):
                        continue
                    members.append({"index": _int(member.get("index")), "actorUid": _int(member.get("actorUid")),
                                    "actorId": _int(member.get("actorId"))})
                clean[str(key)] = {"members": members}
            if clean:
                native["groups"] = clean
                members = clean.get("1", next(iter(clean.values()))) .get("members", [])
                native["main"] = [_int(m.get("actorUid")) for m in members if _int(m.get("index")) < 3 and _int(m.get("actorUid"))]
                native["assistant"] = [_int(m.get("actorUid")) for m in members if _int(m.get("index")) >= 3 and _int(m.get("actorUid"))]
        return native["groups"]

    def _validate_native_group(self, profile: Mapping[str, Any], payload: Optional[Mapping[str, Any]]) -> str | None:
        """Validate the six native BurstLink slots before persisting them.

        The actor-selection UI sends actor UIDs, while older local callers
        could submit arbitrary IDs and later receive a room that did not
        correspond to the account.  Keep empty slots valid while editing, but
        reject unknown/duplicate UIDs and mismatched actor IDs at the HTTP
        boundary.
        """
        groups = payload.get("actorGroups") if isinstance(payload, Mapping) else None
        if not isinstance(groups, Mapping):
            return None
        owned = {}
        for actor in self._native_actors(profile):
            uid = self._uid(actor)
            if uid:
                owned[uid] = _int(actor.get("id"))
        seen_uid: set[int] = set()
        seen_index: set[int] = set()
        members_to_validate: list[tuple[int, Mapping[str, Any], int]] = []
        for group in groups.values():
            if not isinstance(group, Mapping):
                continue
            for member in group.get("members") or []:
                if not isinstance(member, Mapping):
                    continue
                index = _int(member.get("index"), -1)
                if index < 0 or index >= 6:
                    return "burstlink_actor_index_invalid"
                if index in seen_index:
                    return "burstlink_actor_index_duplicate"
                seen_index.add(index)
                uid = _int(member.get("actorUid"))
                if not uid:
                    continue
                if uid not in owned:
                    return "burstlink_actor_not_owned"
                if uid in seen_uid:
                    return "burstlink_actor_duplicate"
                seen_uid.add(uid)
                members_to_validate.append((index, member, uid))
        # Report duplicate/ownership errors consistently before checking
        # actor-kind placement.  A repeated UID can otherwise be rejected on
        # the first slot's kind and hide the more actionable duplicate error.
        for index, member, uid in members_to_validate:
                actor_id = _int(member.get("actorId"))
                if actor_id and actor_id != owned[uid]:
                    return "burstlink_actor_mismatch"
                expected_kind = 1 if index < 3 else 2
                actual_kind = BURST_ACTOR_KIND.get(owned[uid])
                if actual_kind and actual_kind != expected_kind:
                    return "burstlink_actor_kind_mismatch"
        return None

    def _publish_native_match(self, profile: Mapping[str, Any], dungeon: Mapping[str, Any]) -> None:
        """Deliver ScMatchNtf when a WebSocket session is available.

        The local HTTP tests and offline clients can use the REST balance route
        directly; a connected original client also gets the same typed push
        message used by the native window.  Missing push descriptors/sessions
        are intentionally harmless.
        """
        services = getattr(getattr(self.game, "base_push_notifier", None), "push_services", ())
        try:
            cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ScMatchNtf"))
            payload = json_format.ParseDict({"fightsvrUrl": "", "dungeonInfo": dict(dungeon)}, cls(),
                                             ignore_unknown_fields=True)
            data = payload.SerializeToString()
        except Exception:
            return
        for service in tuple(services or ()):
            try:
                key = service.key_for_account_id(profile.get("account_id"))
                if key is not None:
                    service.publish(key, "ScMatchNtf", data)
            except Exception:
                continue

    def _consume_native_ticket(self, profile: Mapping[str, Any], state: dict[str, Any]) -> list[dict[str, Any]] | None:
        """Consume the 80003 item paid by the native BurstLink join flow."""
        bucket = state["burst"]
        current = self._burst_inventory_count(profile)
        if current <= 0 or _int(bucket.get("tickets")) <= 0:
            return None
        change = self.game.activity_modes._change_item(profile, self.BURST_TICKET_ID, -1)
        if change is None:
            return None
        bucket["tickets"] = max(0, _int(bucket.get("tickets")) - 1)
        bucket["inventoryTickets"] = current - 1
        return [change]

    def _native_match(self, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        native = self._native(state)
        bucket = state["burst"]
        active = native.get("active")
        if isinstance(active, dict) and not active.get("balanced"):
            # A lost HTTP response or a reconnect can repeat the native
            # matcher request.  Re-send the same local room so the client can
            # enter the existing Fight scene instead of opening a spinner.
            dungeon = active.get("dungeon") if isinstance(active.get("dungeon"), Mapping) else {}
            return 200, {"matchTime": 0, "maxWaitTime": 1, "resChange": [],
                         "localFight": True, "fightsvrUrl": "", "dungeonInfo": dungeon}
        if int(bucket.get("tickets", 0)) <= 0:
            return 409, {"error": "ticket_exhausted", "mode": "burst", "nextResetAt": self._next_reset()}
        if not native.get("main"):
            native["main"] = [self._uid(actor) for actor in self._native_actors(profile)[:3]]
        if not native.get("assistant"):
            native["assistant"] = [self._uid(actor) for actor in self._native_actors(profile)[3:6]]
        match_no = int(bucket.get("nativeMatch", 0)) + 1
        uid = 7_000_000_000_000 + int(profile["account_id"]) * 1000 + match_no
        actors = self._native_actors(profile)
        own = [actor for actor in actors if self._uid(actor) in set(native["main"] + native["assistant"])]
        if not own:
            own = actors[:3]
        bot = self._bot(int(profile["account_id"]), match_no, 1, burst=True)
        bot_actors = [{"id": int(actor_id), "uid": 0, "level": int(bot["level"]), "quality": 1, "star": 1,
                       "attrs": []} for actor_id in bot["actorIds"]]
        dungeon = {"dungeonUid": int(uid), "dungeonId": int(self.BURST_DUNGEON_ID), "dungeonType": 8,
                   "expireSeconds": 3600, "state": 1,
                   "teams": [{"members": [{"playerId": str(profile.get("player_id", "local")),
                                             "name": str(profile.get("name", "本地玩家")),
                                             "level": int(profile.get("level", 1) or 1), "actors": own}]},
                              {"members": [{"playerId": bot["playerId"], "name": bot["name"],
                                             "level": int(bot["level"]), "actors": bot_actors}]}]}
        native["active"] = {"uid": int(uid), "createdAt": self._now(), "balanced": False,
                             "dungeon": dungeon, "win": None, "snapshot": None}
        native["lastBalanced"] = False
        # The real client normally pays the type-18 RefreshPrice row before
        # reaching the match URL.  Direct protocol callers (and recovery
        # tools) may enter straight through /match; consume the canonical
        # item-bag ticket only when the inventory did not move since the last
        # state read.  Type 11 lives in LocalSystems, so passing it to the
        # currency ledger would incorrectly return ``insufficient_resource``.
        changes = []
        if not self._native(state).get("paid"):
            changes = self._consume_native_ticket(profile, state)
            if changes is None:
                return 409, {"error": "ticket_exhausted", "mode": "burst", "nextResetAt": self._next_reset()}
        native["paid"] = False
        bucket["nativeMatch"] = match_no
        self._save(profile, state)
        self._publish_native_match(profile, dungeon)
        return 200, {"matchTime": 0, "maxWaitTime": 1, "resChange": changes,
                      "localFight": True, "fightsvrUrl": "", "dungeonInfo": dungeon}

    def _native_balance(self, profile: Mapping[str, Any], state: dict[str, Any], body: Body) -> Response:
        native = self._native(state)
        active = native.get("active")
        if not isinstance(active, dict):
            return 409, {"error": "burstlink_dungeon_not_found"}
        if active.get("balanced"):
            return 200, {"isWin": bool(active.get("win")), "winNum": int(state["burst"].get("wins", 0)),
                          "failNum": int(state["burst"].get("losses", 0)), "rewards": []}
        # The native balance request is an empty JSON object.  Its result is
        # deliberately derived only from the terminal FightActiveData that
        # was uploaded to the matching dungeon UID; accepting a guessed
        # ``{"win": true}`` here would recreate the old free-win path.
        self._native_request(body, "ApiBurstLinkDunBalance")
        snapshot = active.get("snapshot")
        if not isinstance(snapshot, Mapping):
            return 409, {"error": "burstlink_battle_result_required"}
        win_team = _int(snapshot.get("winTeam"))
        if win_team not in (1, 2):
            return 409, {"error": "burstlink_battle_result_required"}
        won = win_team == 1
        active["balanced"] = True
        active["win"] = won
        native["lastWin"] = won
        native["lastBalanced"] = True
        bucket = state["burst"]
        if won:
            bucket["wins"] = int(bucket.get("wins", 0)) + 1
        else:
            bucket["losses"] = int(bucket.get("losses", 0)) + 1
        native["active"] = active
        self._save(profile, state)
        return 200, {"isWin": won, "winNum": int(bucket["wins"]), "failNum": int(bucket["losses"]), "rewards": []}

    def _native_claim(self, profile: Mapping[str, Any], state: dict[str, Any]) -> Response:
        native = self._native(state)
        bucket = state["burst"]
        if int(bucket.get("wins", 0)) < self.BURST_MAX_WINS and int(bucket.get("losses", 0)) < self.BURST_MAX_LOSSES:
            return 409, {"error": "burstlink_not_finished"}
        if native.get("received"):
            return 200, {"resChange": []}
        total = max(0, min(self.BURST_MAX_WINS, int(bucket.get("wins", 0)))) * 100
        changes: list[dict[str, Any]] = []
        if total:
            ok, changes, error = self.game.economy._change_resources(profile, [{"type": 7, "id": 0, "count": total}])
            if not ok:
                return 409, {"error": error or "resource_update_failed"}
        native["received"] = True
        self._save(profile, state)
        return 200, {"resChange": changes}

    def _summary(self, mode: str, bucket: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "mode": mode,
            "tickets": int(bucket.get("tickets", 0)),
            "maxTickets": self.RACE_TICKETS if mode == "race" else self.BURST_TICKETS,
            "nextResetAt": self._next_reset(),
            "rating": int(bucket.get("rating", 1000)),
            "wins": int(bucket.get("wins", 0)),
            "losses": int(bucket.get("losses", 0)),
            "activeMatchId": (bucket.get("active") or {}).get("matchId") if isinstance(bucket.get("active"), dict) else None,
            "matches": list(bucket.get("matches", []))[-20:],
        }

    def _info(self, mode: str, state: dict[str, Any]) -> Response:
        return 200, self._summary(mode, state[mode])

    def _new_match(self, mode: str, profile: Mapping[str, Any], state: dict[str, Any], payload: Mapping[str, Any]) -> Response:
        bucket = state[mode]
        self._refresh_tickets(state)
        active = bucket.get("active")
        if isinstance(active, dict) and active.get("status") in {"matching", "running"}:
            return 200, {"ok": True, "created": False, "match": active, "retry": True}
        if int(bucket.get("tickets", 0)) <= 0:
            return 409, {"error": "ticket_exhausted", "mode": mode, "nextResetAt": self._next_reset()}
        match_no = int(bucket.get("nextMatch", 1))
        bucket["nextMatch"] = match_no + 1
        account_id = int(profile["account_id"])
        burst = mode == "burst"
        player_count = self.BURST_PLAYERS if burst else self.RACE_PLAYERS
        participants = [self._player(profile)] + [self._bot(account_id, match_no, i, burst=burst) for i in range(1, player_count)]
        match_id = f"local-{mode}-{account_id}-{match_no:04d}"
        match = {
            "matchId": match_id,
            "mode": mode,
            "status": "running",
            "round": 0,
            "rounds": self.BURST_ROUNDS if burst else self.RACE_ROUNDS,
            "participants": participants,
            "actions": [],
            "createdAt": self._now(),
            "seed": self._digest_int(account_id, match_no, mode, modulo=2**31 - 1),
            "result": None,
            "settled": False,
        }
        bucket["tickets"] = int(bucket.get("tickets", 0)) - 1
        bucket["active"] = match
        self._save(profile, state)
        return 200, {"ok": True, "created": True, "match": match, "tickets": bucket["tickets"]}

    @staticmethod
    def _action_points(action: str, actor_count: int, round_no: int) -> int:
        values = {"attack": 12, "skill": 16, "defend": 8, "support": 10, "wait": 5, "auto": 9}
        return 100 + values.get(action.casefold(), 9) + actor_count * 2 + round_no

    def _advance(self, mode: str, profile: Mapping[str, Any], state: dict[str, Any], match: dict[str, Any], payload: Mapping[str, Any]) -> Response:
        expected = int(match.get("round", 0)) + 1
        try:
            round_no = int(payload.get("round", expected))
        except (TypeError, ValueError):
            return 400, {"error": "invalid_round", "expectedRound": expected}
        if round_no < expected:
            previous = next((row for row in match.get("actions", []) if int(row.get("round", -1)) == round_no), None)
            return 200, {"ok": True, "duplicate": True, "round": previous or round_no, "match": match}
        if round_no > expected:
            return 409, {"error": "round_out_of_order", "expectedRound": expected, "currentRound": match.get("round", 0)}
        player = match["participants"][0]
        action = str(payload.get("action", payload.get("skill", "auto"))).strip().casefold() or "auto"
        if action not in {"attack", "skill", "defend", "support", "wait", "auto"}:
            return 400, {"error": "invalid_action", "allowed": ["attack", "skill", "defend", "support", "wait", "auto"]}
        player_score = self._action_points(action, len(player.get("actorIds", [])), round_no)
        scores = [{"playerId": player["playerId"], "action": action, "score": player_score, "isSelf": True}]
        for index, bot in enumerate(match["participants"][1:], 1):
            bot_action = ("attack", "skill", "defend", "support")[self._digest_int(match["seed"], round_no, index, modulo=4)]
            bot_score = self._action_points(bot_action, len(bot.get("actorIds", [])), round_no) + self._digest_int(match["seed"], round_no, index, "bonus", modulo=8)
            scores.append({"playerId": bot["playerId"], "action": bot_action, "score": bot_score, "isSelf": False})
        for item in match["participants"]:
            found = next((row for row in scores if row["playerId"] == item["playerId"]), None)
            if found is not None:
                item["score"] = int(item.get("score", 0)) + int(found["score"])
        match["round"] = round_no
        match["actions"].append({"round": round_no, "scores": scores})
        if round_no >= int(match["rounds"]):
            match["status"] = "finished"
            ordered = sorted(match["participants"], key=lambda item: (-int(item.get("score", 0)), str(item["playerId"])))
            for rank, item in enumerate(ordered, 1):
                item["rank"] = rank
            player_rank = next(item["rank"] for item in ordered if item["playerId"] == player["playerId"])
            match["result"] = {"rank": player_rank, "winner": player_rank == 1, "scores": [{"playerId": item["playerId"], "score": item["score"], "rank": item["rank"]} for item in ordered]}
        self._save(profile, state)
        return 200, {"ok": True, "duplicate": False, "round": round_no, "scores": scores, "match": match}

    def _settle(self, mode: str, profile: Mapping[str, Any], state: dict[str, Any], match: dict[str, Any]) -> Response:
        if match.get("status") != "finished":
            while int(match.get("round", 0)) < int(match.get("rounds", 0)):
                status, _ = self._advance(mode, profile, state, match, {"round": int(match.get("round", 0)) + 1, "action": "auto"})
                if status != 200:
                    return status, {"error": "auto_finish_failed"}
        if match.get("settled"):
            return 200, {"ok": True, "duplicate": True, "settlement": match.get("settlement", {})}
        result = match.get("result") or {}
        won = bool(result.get("winner", False))
        bucket = state[mode]
        if won:
            bucket["wins"] = int(bucket.get("wins", 0)) + 1
        else:
            bucket["losses"] = int(bucket.get("losses", 0)) + 1
        if mode == "race":
            bucket["rating"] = max(0, int(bucket.get("rating", 1000)) + (25 if won else -15))
        reward = {"type": 7, "id": 0, "param": 0, "count": 100 if won else 40}
        changes: list[dict[str, Any]] = []
        try:
            ok, changes, error = self.game.economy._change_resources(profile, [reward])
        except (AttributeError, TypeError, ValueError):
            ok, error = False, "resource_ledger_unavailable"
        if not ok:
            return 409, {"error": error or "resource_update_failed"}
        settlement = {"win": won, "rank": int(result.get("rank", 0)), "reward": reward, "resChange": changes, "rating": int(bucket.get("rating", 1000))}
        match["settled"] = True
        match["settlement"] = settlement
        bucket.setdefault("matches", []).append({"matchId": match["matchId"], **settlement, "finishedAt": self._now()})
        bucket["matches"] = bucket["matches"][-50:]
        bucket["active"] = None
        self._save(profile, state)
        return 200, {"ok": True, "duplicate": False, "settlement": settlement}

    def _match_route(self, route: str) -> tuple[str, str | None, str | None]:
        mode = "burst" if "burstlink" in route or "burst-link" in route else "race"
        action = None
        if any(token in route for token in ("/balance", "/settle", "/result", "/finish")):
            action = "settle"
        elif any(token in route for token in ("/round", "/turn", "/action", "/battle")):
            action = "round"
        elif any(token in route for token in ("/match", "/join", "/start", "/matching")):
            action = "match"
        match_id = None
        match_match = re.search(r"/(?:match|room|dungeon)/([^/]+)", route)
        if match_match:
            candidate = match_match.group(1)
            if candidate not in {"info", "list", "record", "rank", "score", "summary"}:
                match_id = candidate
        return mode, action, match_id

    def handle_http(self, method: str, path: str, body: Body, profile: Mapping[str, Any]) -> Optional[Response]:
        route = self._route(path)
        if "burstlink" in route or route.startswith("/match/"):
            # The login bootstrap normally settles DailyRefresh first.  The
            # direct HTTP seam is also used by native replay tests, so settle
            # the same weekly 80003 row before reading native state.  Keep it
            # outside the realtime lock to avoid nested SQLite transactions.
            try:
                self.game.activity_modes.refresh_daily_items(profile)
            except Exception:
                pass
        race_result = self.race.handle_http(method, path, body, profile)
        if race_result is not None:
            return race_result
        # Native 2.0.4 BurstLink requests.  The client uses the match server
        # URL for matching and the FCI URL for the player/settlement resources;
        # both are handled by this deterministic local AI implementation.
        if route in {"/fci/burstlinkpvpinfo", "/fci/burstlinkpvpinfo/"}:
            with shared_process_lock(self.game.db_path), self._lock:
                state = self._state(profile)
                method_upper = method.upper()
                if method_upper == "GET":
                    return 200, self._native_info(profile, state)
                if method_upper == "POST":
                    native = self._native(state)
                    if native.get("paid"):
                        return 200, {"resChange": [], **self._native_info(profile, state)}
                    changes = self._consume_native_ticket(profile, state)
                    if changes is None:
                        return 409, {"error": "ticket_exhausted", "mode": "burst", "nextResetAt": self._next_reset()}
                    native["paid"] = True
                    self._save(profile, state)
                    # `_native_info` contains the compatibility descriptor and
                    # deliberately defaults resChange to an empty list.  Put
                    # the actual ticket delta after that expansion so the
                    # native POST reply cannot silently discard the payment.
                    return 200, {**self._native_info(profile, state), "resChange": changes}
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
        if route in {"/fci/burstlinkpvprandactorinfo", "/fci/burstlinkpvprandactorinfo/"} or route.startswith("/fci/burstlinkpvprandactorinfo/"):
            with shared_process_lock(self.game.db_path), self._lock:
                state = self._state(profile)
                native = self._native(state)
                method_upper = method.upper()
                if method_upper == "GET":
                    return 200, self._native_rand_actor(profile, native)
                if method_upper == "POST":
                    return 200, self._native_rand_actor(profile, native, _int(route.rsplit("/", 1)[-1]) if route.rsplit("/", 1)[-1].isdigit() else 0)
                if method_upper == "PUT":
                    request = self._native_request(body, "ApiBurstLinkPVPRandActor")
                    actor_uid = _int(request.get("actorUid", request.get("uid")))
                    actor_type = _int(route.rsplit("/", 1)[-1]) if route.rsplit("/", 1)[-1].isdigit() else 0
                    owned = {_int(self._uid(actor)) for actor in self._native_actors(profile)}
                    if actor_uid <= 0 or actor_uid not in owned:
                        return 409, {"error": "burstlink_actor_not_owned"}
                    target = native["main"] if actor_type in {0, 1} else native["assistant"]
                    other = native["assistant"] if target is native["main"] else native["main"]
                    if actor_uid in other:
                        return 409, {"error": "burstlink_actor_already_selected"}
                    if actor_uid not in target and len(target) >= 3:
                        return 409, {"error": "burstlink_actor_group_full"}
                    if actor_uid and actor_uid not in target:
                        target.append(actor_uid)
                    native["randSeed"] = int(native.get("randSeed", 0)) + 1
                    self._save(profile, state)
                    return 200, self._native_rand_actor(profile, native, actor_type)
                return 405, {"error": "method_not_allowed"}
        if route in {"/fci/burstlinkpvpactor", "/fci/burstlinkpvpactor/"}:
            if method.upper() == "GET":
                return 200, {"actor": self._native_actors(profile)}
            return 405, {"error": "method_not_allowed", "message": "GET required"}
        if route in {"/fci/burstlinkpvpactorgroup", "/fci/burstlinkpvpactorgroup/"}:
            with shared_process_lock(self.game.db_path), self._lock:
                state = self._state(profile)
                if method.upper() == "GET":
                    return 200, self._native(state)["groups"]
                if method.upper() in {"PUT", "POST"}:
                    request = self._native_request(body, "ApiBurstLinkPvpBattleGroup")
                    error = self._validate_native_group(profile, request)
                    if error:
                        return 409, {"error": error}
                    groups = self._native_group(profile, state, request)
                    self._save(profile, state)
                    return 200, {"result": {"actorGroups": groups}}
                return 405, {"error": "method_not_allowed"}
        active_match = re.fullmatch(r"/fci/dungeon/(\d+)/active", route)
        if active_match:
            uid = int(active_match.group(1))
            with shared_process_lock(self.game.db_path), self._lock:
                state = self._state(profile)
                native = self._native(state)
                active = native.get("active")
                if not isinstance(active, dict) or _int(active.get("uid")) != uid:
                    # Let Dungeon/TeamPve handle its own shared active seam;
                    # this handler owns only the persisted native Burst room.
                    return None
                payload = self._native_request(body, "FightActiveData")
                if _int(payload.get("dungeonType")) != 8 or _int(payload.get("stageID")) != self.BURST_DUNGEON_ID:
                    return 400, {"error": "burstlink_active_type_mismatch"}
                if _int(payload.get("winTeam")) not in (1, 2):
                    return 400, {"error": "burstlink_battle_result_required"}
                active["snapshot"] = payload
                self._save(profile, state)
                return 200, {}
        if route in {"/fci/burstlinkdunbalance", "/fci/burstlinkdunbalance/"}:
            if method.upper() != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            with shared_process_lock(self.game.db_path), self._lock:
                state = self._state(profile)
                return self._native_balance(profile, state, body)
        if route in {"/fci/burstlinkpvpbalance", "/fci/burstlinkpvpbalance/"}:
            if method.upper() != "POST":
                return 405, {"error": "method_not_allowed", "message": "POST required"}
            with shared_process_lock(self.game.db_path), self._lock:
                state = self._state(profile)
                return self._native_claim(profile, state)
        if route in {"/match/burstlinkpvpmatch", "/match/burstlinkpvpmatch/"}:
            with shared_process_lock(self.game.db_path), self._lock:
                state = self._state(profile)
                if method.upper() == "POST":
                    return self._native_match(profile, state)
                if method.upper() == "PUT":
                    native = self._native(state)
                    native["active"] = None
                    self._save(profile, state)
                    return 200, {}
                return 405, {"error": "method_not_allowed", "message": "POST or PUT required"}
        # Preserve BurstLink's old simulation without allowing the obsolete
        # action-string Race routes to create a second reward/ticket economy.
        if any(token in route for token in ("racepvp", "race-pvp")):
            if any(token in route for token in ("/match", "/join", "/round", "/start", "/settle")):
                return 410, {"error": "race_requires_client_dungeon_flow"}
            return None
        if not any(token in route for token in ("burstlink", "burst-link")):
            return None
        # ``_state`` followed by a JSON update is one logical transaction.
        # The thread lock alone cannot protect a second local worker process.
        with shared_process_lock(self.game.db_path), self._lock:
            # The bootstrap descriptor calls this exact capitalized endpoint.
            if route == "/fci/burstlinkpvpinfo":
                mode = "burst"
                return self._info(mode, self._state(profile))
            state = self._state(profile)
            mode, action, match_id = self._match_route(route)
            bucket = state[mode]
            if method.upper() == "GET" and action is None:
                return self._info(mode, state)
            if method.upper() == "GET" and action == "match":
                active = bucket.get("active")
                if isinstance(active, dict) and (match_id is None or active.get("matchId") == match_id):
                    return 200, {"ok": True, "match": active, "summary": self._summary(mode, bucket)}
                return 404, {"error": "match_not_found", "matchId": match_id}
            if method.upper() == "GET" and any(token in route for token in ("/record", "/rank", "/score", "/summary")):
                return 200, self._summary(mode, bucket)
            if method.upper() != "POST":
                return 405, {"error": "method_not_allowed", "message": "GET or POST required"}
            payload = _json(body)
            if action == "match":
                return self._new_match(mode, profile, state, payload)
            active = bucket.get("active")
            if not isinstance(active, dict):
                if action == "settle" and match_id:
                    previous = next((row for row in bucket.get("matches", []) if row.get("matchId") == match_id), None)
                    if isinstance(previous, dict):
                        settlement = {key: value for key, value in previous.items() if key not in {"matchId", "finishedAt"}}
                        return 200, {"ok": True, "duplicate": True, "settlement": settlement}
                return 409, {"error": "no_active_match", "mode": mode}
            if match_id and active.get("matchId") != match_id:
                return 404, {"error": "match_not_found", "matchId": match_id}
            if action == "round":
                return self._advance(mode, profile, state, active, payload)
            if action == "settle":
                return self._settle(mode, profile, state, active)
            # A bare POST to the mode route is treated as match search, which
            # mirrors the client's first click on RacePVP/BurstLink join.
            return self._new_match(mode, profile, state, payload)


__all__ = ["LocalRealtimePvp", "LocalRacePvp"]
