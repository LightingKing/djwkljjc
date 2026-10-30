"""Challenge-menu modes: material / gold / tower / deploy / triggered events."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import actors as actor_store  # noqa: E402
from accounts import LocalAccounts  # noqa: E402
from activity_modes import LocalActivityModes, UID_BASE  # noqa: E402
from game import LocalGame  # noqa: E402
import test_systems  # noqa: E402

MONDAY_NOON = int(datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc).timestamp())


class Clock:
    def __init__(self, now: int) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


class ModeTestBase(unittest.TestCase):
    level = 60

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.clock = Clock(MONDAY_NOON)
        self.accounts = LocalAccounts(root / "accounts")
        self.game = LocalGame(root / "game", self.accounts, clock=self.clock)
        self.modes = LocalActivityModes(self.game)
        self.account, self.auth = test_systems.LocalSystemsTests._guest(self.accounts)
        self.set_level(self.level)
        self.add_resource(6, 0, 500)

    def tearDown(self):
        self._temp.cleanup()

    # -- helpers
    def profile(self):
        return self.game._profile(self.account)

    def call(self, method, path, payload=None):
        body = payload if isinstance(payload, (bytes, type(None))) else json.dumps(payload).encode()
        return self.modes.handle_http(method, path, body, self.profile())

    def ok(self, method, path, payload=None):
        result = self.call(method, path, payload)
        self.assertIsNotNone(result, path)
        status, reply = result
        self.assertEqual(status, 200, reply)
        self.assertFalse(isinstance(reply, dict) and "error" in reply, reply)
        return reply

    def set_level(self, level):
        with closing(self.game._connect()) as connection, connection:
            connection.execute("UPDATE game_profiles SET level = ? WHERE account_id = ?",
                               (level, int(self.profile()["account_id"])))

    def add_resource(self, rtype, rid, count):
        ok, _, _ = self.game.economy._change_resources(self.profile(), [{"type": rtype, "id": rid, "param": 0,
                                                                         "count": count}])
        self.assertTrue(ok)

    def resource(self, rtype, rid=0):
        return sum(int(r.get("count", 0)) for r in self.profile()["resources"]
                   if int(r.get("type", 0)) == rtype and int(r.get("id", 0)) == rid)

    def item(self, item_id):
        entry = self.game.systems._state(self.profile()).get("items", {}).get(str(item_id)) or {}
        return int(entry.get("count", 0))

    def set_item(self, item_id, count):
        profile = self.profile()
        state = self.game.systems._state(profile)
        state.setdefault("items", {})[str(item_id)] = {"count": count}
        self.game.systems._save(profile, state)

    def own(self, *actor_ids):
        profile = self.profile()
        state = self.game.systems._state(profile)
        uids = [actor_store.add(profile, state, actor_id)["uid"] for actor_id in actor_ids]
        self.game.systems._save(profile, state)
        return uids

    def fight(self, path_prefix, uid, win=True, kills=0, own_hp=None, enemy_hp=None, energy=(0, 0)):
        own = {"team": 1, "statistic": {"gameRound": 3}, "energySp": energy[0], "energyMp": energy[1]}
        enemy = {"team": 2, "statistic": {"deadInfo": [{"id": i, "seat": i + 1, "kind": 1} for i in range(kills)]}}
        if own_hp is not None:
            own["statistic"]["recordHpList"] = own_hp
        if enemy_hp is not None:
            enemy["statistic"]["recordHpList"] = enemy_hp
        active = {"winTeam": 1 if win else 2, "teamList": [own, enemy]}
        self.ok("PATCH", f"{path_prefix}{uid}/active/", active)


class MaterialDungeonTests(ModeTestBase):
    def enter(self, dungeon_id=52101, multi=1, activity_id=2001):
        return self.call("POST", "/fci/activity-dungeon/", {
            "selectActorGroupId": 1, "dungeonId": dungeon_id, "activityType": 2,
            "activityId": activity_id, "multiPower": multi})

    def test_summary_and_record_are_never_null(self):
        self.assertEqual(self.ok("GET", "/fci/activity-dungeon/summary/"), {"selectActorGroupId": 1})
        self.assertEqual(self.ok("GET", "/fci/activity-dungeon/record/"), [])

    def test_win_charges_energy_grants_drops_exp_and_records(self):
        energy = self.resource(6)
        status, entered = self.enter()
        self.assertEqual(status, 200, entered)
        uid = entered["dungeonUid"]
        self.assertGreater(uid, UID_BASE)
        self.assertEqual(entered["baseInfo"]["dungeonType"], 5)
        self.assertEqual(entered["baseInfo"]["actors"][0]["id"], 1001)
        self.assertEqual(self.resource(6), energy - 1)  # DungeonReward.cost
        # WeekActivity PATCHes fci/dungeon/{uid}/active/ (scene__scenefight).
        self.fight("/fci/dungeon/", uid, win=True)
        result = self.ok("POST", f"/fci/activity-dungeon/{uid}/balance/", {})
        self.assertTrue(result["win"] and result["balanced"] and result["firstFinish"])
        self.assertEqual(self.resource(6), energy - 12)  # + costWin 11
        self.assertEqual(result["profits"], [])
        self.assertNotIn("eventInfo", result)
        changes = result["resChange"]
        self.assertEqual(changes[0]["baseRes"]["type"], 1)
        self.assertEqual(changes[0]["baseRes"]["countDelta"], 12)
        gold = [c for c in changes if c.get("baseRes", {}).get("type") == 7]
        self.assertEqual(gold[0]["baseRes"]["countDelta"], 270)
        equips = sorted(c["equip"]["id"] for c in changes if "equip" in c)
        self.assertEqual(equips, [10100, 11101])
        self.assertTrue(all(c["equip"]["countDelta"] == 1 for c in changes if "equip" in c))
        self.assertEqual(result["expChange"][0]["actorUid"], 1)
        self.assertEqual(result["expChange"][0]["addExp"], 350)
        record = self.ok("GET", "/fci/activity-dungeon/record/")
        self.assertEqual(record[0]["dungeonId"], 52101)
        self.assertTrue(record[0]["finished"])
        self.assertEqual(len(record[0]["targetsDone"]), len(result["targetsDone"]))
        # Balance is idempotent; quitting afterwards is acknowledged.
        self.assertEqual(self.ok("POST", f"/fci/activity-dungeon/{uid}/balance/", {}), result)
        self.assertEqual(self.ok("DELETE", f"/fci/activity-dungeon/{uid}"), {})

    def test_multi_power_needs_stars_and_scales_cost_and_drops(self):
        status, entered = self.enter(multi=3)
        self.assertEqual(status, 200)
        uid = entered["dungeonUid"]
        self.fight("/fci/dungeon/", uid, kills=1)
        self.ok("POST", f"/fci/activity-dungeon/{uid}/balance/", {})  # no stats: all 3 targets
        energy = self.resource(6)
        status, entered = self.enter(multi=3)
        uid = entered["dungeonUid"]
        self.assertEqual(self.resource(6), energy - 3)
        self.fight("/fci/dungeon/", uid, kills=1)
        result = self.ok("POST", f"/fci/activity-dungeon/{uid}/balance/", {})
        self.assertFalse(result["firstFinish"])
        self.assertEqual(self.resource(6), energy - 36)
        gold = sum(c["baseRes"]["countDelta"] for c in result["resChange"] if c.get("baseRes", {}).get("type") == 7)
        self.assertEqual(gold, 810)
        self.assertEqual(sum(1 for c in result["resChange"] if "equip" in c), 6)

    def test_loss_pays_no_win_cost_and_records_nothing(self):
        energy = self.resource(6)
        uid = self.enter()[1]["dungeonUid"]
        self.fight("/fci/dungeon/", uid, win=False)
        result = self.ok("POST", f"/fci/activity-dungeon/{uid}/balance/", {})
        self.assertFalse(result["win"])
        self.assertEqual(self.resource(6), energy - 1)
        self.assertEqual(self.ok("GET", "/fci/activity-dungeon/record/"), [])

    def test_weekday_level_and_predungeon_gates(self):
        self.clock.now += 86400  # Tuesday: 2001 (Mon/Thu/Sun) is closed
        status, reply = self.enter()
        self.assertEqual(status, 409)
        self.assertEqual(reply["error"], "activity_closed_today")
        self.assertEqual(self.enter(52201, activity_id=2002)[0], 200)
        self.clock.now -= 86400
        self.assertEqual(self.enter(52102)[1]["error"], "pre_dungeon_not_finished")
        self.set_level(5)
        self.assertEqual(self.enter()[1]["error"], "level_too_low")

    def test_restart_delete_and_wireformat_entry(self):
        message = self.game._pool.FindMessageTypeByName("ApiActivityDungeon")
        from google.protobuf import message_factory

        cls = message_factory.GetMessageClass(message)
        wire = cls(select_actor_group_id=1, dungeon_id=52101, activity_type=2, activity_id=2001, multi_power=1)
        status, entered = self.call("POST", "/fci/activity-dungeon/", wire.SerializeToString())
        self.assertEqual(status, 200, entered)
        uid = entered["dungeonUid"]
        # DU.Restart deletes through fci/dungeon/{uid}; foreign uids fall through.
        self.assertEqual(self.ok("DELETE", f"/fci/dungeon/{uid}"), {})
        self.assertIsNone(self.call("DELETE", "/fci/dungeon/1793332283000"))
        result = self.ok("POST", f"/fci/activity-dungeon/{uid}/balance/", {})
        self.assertFalse(result["win"])


class GoldDungeonTests(ModeTestBase):
    level = 20

    def test_daily_ticket_refresh_caps_and_never_lowers(self):
        changes = self.modes.refresh_daily_items(self.profile())
        self.assertIn({"baseRes": {"type": 11, "id": 80002, "param": 0, "count": 2, "countDelta": 2}}, changes)
        self.assertEqual(self.item(80006), 5)
        self.assertEqual(self.modes.refresh_daily_items(self.profile()), [])
        self.clock.now += 86400
        self.modes.refresh_daily_items(self.profile())
        self.assertEqual(self.item(80002), 4)
        self.clock.now += 86400
        self.modes.refresh_daily_items(self.profile())
        self.assertEqual(self.item(80002), 4)  # refreshLimit 4
        self.set_item(80002, 9)
        self.clock.now += 86400
        self.modes.refresh_daily_items(self.profile())
        self.assertEqual(self.item(80002), 9)

    def test_gold_run_pays_base_and_kill_gold_first_and_spends_a_ticket(self):
        entered = self.ok("POST", "/fci/gold-dungeon/", {"selectActorGroupId": 1})
        self.assertEqual(entered["baseInfo"]["dungeonId"], 20001)  # GoldDungeonLevel[20]
        self.assertEqual(entered["baseInfo"]["dungeonType"], 9)
        self.assertEqual(entered["resChange"][0]["baseRes"]["id"], 80002)
        uid = entered["dungeonUid"]
        gold = self.resource(7)
        self.fight("/fci/dungeon/", uid, win=False, kills=3)
        result = self.ok("POST", f"/fci/gold-dungeon/{uid}/balance/", {})
        first, second = (c["baseRes"] for c in result["resChange"][:2])
        self.assertEqual((first["type"], first["countDelta"]), (7, 12500))
        self.assertEqual((second["type"], second["countDelta"]), (7, 3 * 2500))
        self.assertEqual(result["resChange"][2]["baseRes"], {"type": 11, "id": 80002, "param": 0, "count": 1,
                                                             "countDelta": -1})
        self.assertEqual(self.resource(7), gold + 20000)
        self.assertEqual(self.ok("GET", "/fci/gold-dungeon/record/"), {"lastGoldDungeonLevel": 20})

    def test_zero_kills_still_lists_two_gold_entries(self):
        uid = self.ok("POST", "/fci/gold-dungeon/", {"selectActorGroupId": 1})["dungeonUid"]
        result = self.ok("POST", f"/fci/gold-dungeon/{uid}/balance/", {})
        deltas = [c["baseRes"]["countDelta"] for c in result["resChange"][:2]]
        self.assertEqual(deltas, [12500, 0])

    def test_fight_again_race_and_tickets_gate(self):
        old = self.ok("POST", "/fci/gold-dungeon/", {"selectActorGroupId": 1})["dungeonUid"]
        new = self.ok("POST", "/fci/gold-dungeon/", {"selectActorGroupId": 1})["dungeonUid"]
        self.assertNotEqual(old, new)
        self.assertEqual(self.ok("DELETE", f"/fci/gold-dungeon/{old}"), {})
        self.assertEqual(self.ok("DELETE", f"/fci/gold-dungeon/{old}"), {})
        self.assertEqual(self.ok("DELETE", "/fci/gold-dungeon/123"), {})
        self.ok("POST", f"/fci/gold-dungeon/{new}/balance/", {})
        self.set_item(80002, 0)
        status, reply = self.call("POST", "/fci/gold-dungeon/", {"selectActorGroupId": 1})
        self.assertEqual((status, reply["error"]), (409, "not_enough_gold_ticket"))

    def test_locked_below_level_18(self):
        self.set_level(17)
        status, reply = self.call("POST", "/fci/gold-dungeon/", {"selectActorGroupId": 1})
        self.assertEqual((status, reply["error"]), (409, "gold_dungeon_locked"))


class TowerTests(ModeTestBase):
    level = 30

    def setUp(self):
        super().setUp()
        self.uids = [1] + self.own(1002, 1011)  # uids 1..3

    def put_groups(self):
        groups = {"1": {"members": [{"actorUid": 1, "index": 0}, {"actorUid": 2, "index": 1}]},
                  "2": {"members": [{"actorUid": 3, "index": 0}]}}
        reply = self.ok("PUT", "/fci/tower/actorgroup/", {"actorGroups": groups})
        self.assertEqual(sorted(reply), ["1", "2", "3"])
        self.assertEqual(len(reply["1"]["members"]), 6)
        return reply

    def enter(self, group=1):
        return self.call("POST", "/fci/tower-dungeon/", {"dungeonId": 10001, "selectActorGroupId": group,
                                                          "towerFloor": 1})

    def test_summary_shape_and_actor_groups(self):
        summary = self.ok("GET", "/fci/tower/summary/")
        self.assertEqual(summary, {"locked": False, "towerFloor": 0, "towerFloorRecord": 0, "selectActorGroupId": 0,
                                   "wipeFloorFree": 0, "wipeFloorPay": 0, "rewardGot": []})
        groups = self.ok("GET", "/fci/tower/actorgroup/")
        self.assertEqual(groups["1"]["members"][0], {"actorUid": 1, "index": 0})  # story formation
        groups = self.put_groups()
        self.assertEqual([m["actorUid"] for m in groups["2"]["members"]], [3, 0, 0, 0, 0, 0])
        # Wireformat ApiTowerActorGroup (TowerActorGroup reorder PUT).
        from google.protobuf import message_factory

        cls = message_factory.GetMessageClass(self.game._pool.FindMessageTypeByName("ApiTowerActorGroup"))
        wire = cls()
        wire.actor_groups[1].members.add(actor_uid=2, index=0)
        wire.actor_groups[1].members.add(actor_uid=1, index=3)
        groups = self.ok("PUT", "/fci/tower/actorgroup/", wire.SerializeToString())
        self.assertEqual([m["actorUid"] for m in groups["1"]["members"]], [2, 0, 0, 1, 0, 0])

    def test_floor_win_locks_groups_advances_and_pays(self):
        self.put_groups()
        status, entered = self.enter()
        self.assertEqual(status, 200, entered)
        self.assertEqual(entered["enemyRecord"], {"hp": [], "card": []})
        self.assertEqual(entered["baseInfo"]["dungeonType"], 4)
        self.assertEqual([a.get("id", 0) for a in entered["baseInfo"]["actors"]], [1001, 1002, 0, 0, 0, 0])
        summary = self.ok("GET", "/fci/tower/summary/")
        self.assertTrue(summary["locked"])
        confirm = self.ok("GET", "/fci/tower/actorgroup-confirm/")
        member = confirm["1"]["members"][0]
        self.assertEqual((member["actorUid"], member["dead"]), (1, False))
        self.assertGreater(member["hp"], 0)
        self.assertNotIn("actor", confirm["1"]["members"][2])
        uid = entered["dungeonUid"]
        self.fight("/fci/tower-dungeon/", uid, win=True, own_hp=[50, 0, 0, 0, 0, 0], energy=(3, 2))
        result = self.ok("POST", f"/fci/tower-dungeon/{uid}/balance", {})
        self.assertTrue(result["win"] and result["firstFinish"])
        kinds = sorted((c["baseRes"]["type"], c["baseRes"]["countDelta"]) for c in result["resChange"])
        self.assertEqual(kinds, [(7, 100), (11, 1), (19, 1)])
        summary = self.ok("GET", "/fci/tower/summary/")
        self.assertEqual((summary["towerFloor"], summary["towerFloorRecord"]), (2, 1))
        group = self.ok("GET", "/fci/tower/actorgroup-confirm/")["1"]
        self.assertEqual([(m["hp"], m["dead"]) for m in group["members"][:2]], [(50, False), (0, True)])
        self.assertEqual((group["energySp"], group["energyMp"]), (3, 2))
        self.assertEqual(self.ok("DELETE", f"/fci/tower-dungeon/{uid}"), {})

    def test_loss_keeps_enemy_hp_and_dead_group_cannot_fight(self):
        self.put_groups()
        uid = self.enter()[1]["dungeonUid"]
        self.fight("/fci/tower-dungeon/", uid, win=False, own_hp=[0, 0, 0, 0, 0, 0], enemy_hp=[10, 0, 30, 0, 0, 0])
        result = self.ok("POST", f"/fci/tower-dungeon/{uid}/balance", {})
        self.assertFalse(result["win"])
        status, reply = self.enter(1)
        self.assertEqual((status, reply["error"]), (409, "tower_group_unavailable"))
        status, entered = self.enter(2)
        self.assertEqual(status, 200)
        self.assertEqual(entered["enemyRecord"]["hp"], [10, 0, 30, 0, 0, 0])

    def test_daily_reset_wipe_and_floor_rewards(self):
        self.put_groups()
        profile = self.profile()
        state = self.modes._state(profile)
        state["tower"].update(day=self.modes._day(), towerFloorRecord=8)
        self.modes._save(profile, state)
        summary = self.ok("GET", "/fci/tower/summary/")
        self.assertEqual((summary["wipeFloorFree"], summary["wipeFloorPay"]), (3, 6))
        reward = self.ok("POST", "/fci/tower/floor-reward/", {"floorReward": 4})
        self.assertEqual(reward["rewardGot"], [4])
        self.assertTrue(reward["resChange"])
        self.assertEqual(self.ok("POST", "/fci/tower/floor-reward/", {"floorReward": 4})["resChange"], [])
        self.assertEqual(self.call("POST", "/fci/tower/floor-reward/", {"floorReward": 12})[0], 409)
        self.add_resource(5, 0, 10)
        money = self.resource(5)
        wiped = self.ok("POST", "/fci/tower/wipe/", {"wipeToFloor": 6})
        self.assertEqual(wiped["towerFloor"], 7)
        self.assertEqual(self.resource(5), money - 10)
        gold = sum(c["baseRes"]["countDelta"] for c in wiped["resChange"] if c["baseRes"]["type"] == 7)
        self.assertEqual(gold, 600)
        entered = self.enter()[1]
        self.assertEqual(entered["baseInfo"]["dungeonId"], 10007)
        self.clock.now += 86400
        summary = self.ok("GET", "/fci/tower/summary/")
        self.assertEqual((summary["locked"], summary["towerFloor"], summary["towerFloorRecord"]), (False, 0, 8))

    def test_locked_reorder_moves_snapshots(self):
        self.put_groups()
        self.enter()
        self.ok("PUT", "/fci/tower/actorgroup-confirm/",
                {"actorGroups": {"1": {"members": [{"actorUid": 2, "index": 0}, {"actorUid": 1, "index": 4}]}}})
        members = self.ok("GET", "/fci/tower/actorgroup-confirm/")["1"]["members"]
        self.assertEqual([m["actorUid"] for m in members], [2, 0, 0, 0, 1, 0])
        self.assertEqual(members[0]["actor"]["id"], 1002)


class DeployTests(ModeTestBase):
    level = 30

    def setUp(self):
        super().setUp()
        self.uids = [1] + self.own(1002, 1011, 2108, 1004, 1007)
        self.modes.rng.seed(7)

    def missions(self):
        return self.ok("GET", "/fci/deploy/mission/")

    def pick(self, missions):
        roles = self.modes._owned_roles(self.profile())
        return next(m["id"] for m in missions["missionInfo"] if self.modes._mission_feasible(m["id"], roles))

    def test_mission_list_shape_and_daily_cap(self):
        missions = self.missions()
        self.assertEqual(missions["accomplishNum"], 0)
        self.assertEqual(len(missions["missionInfo"]), 8)
        first = missions["missionInfo"][0]
        self.assertEqual(set(first), {"id", "roleid", "status", "startTimestamp", "endTimestamp"})
        self.assertEqual(self.missions(), missions)  # stable within the day

    def test_attend_speedup_reward_and_refresh(self):
        mission_id = self.pick(self.missions())
        attended = self.ok("POST", "/fci/deploy/deploymissionattend/", {"id": mission_id, "actorUid": self.uids})
        self.assertTrue(attended["attendResult"])
        started = next(m for m in self.missions()["missionInfo"] if m["id"] == mission_id)
        self.assertEqual((started["status"], started["startTimestamp"]), (1, MONDAY_NOON))
        self.assertTrue(started["roleid"])
        self.set_item(51002, 1)
        sped = self.ok("POST", "/fci/deploy/deploymissionspeedup/",
                       {"id": mission_id, "cost": {"type": 11, "id": 51002, "param": 7200, "count": 1}})
        self.assertEqual(sped["resChange"][0]["baseRes"]["countDelta"], -1)
        if started["endTimestamp"] - 7200 - MONDAY_NOON > 2:
            self.assertEqual(self.call("POST", "/fci/deploy/deploymissionreward/", {"id": mission_id})[0], 409)
        self.clock.now += 8 * 3600
        reward = self.ok("POST", "/fci/deploy/deploymissionreward/", {"id": mission_id})
        self.assertTrue(reward["rewardResult"])
        self.assertTrue(any(c.get("baseRes", {}).get("type") == 7 for c in reward["resChange"]))
        missions = self.missions()
        self.assertEqual(missions["accomplishNum"], 1)
        self.assertNotIn(mission_id, [m["id"] for m in missions["missionInfo"]])
        # Refresh bought through economy resbuy (type 10) re-offers missions.
        self.assertIn(10, self.game.economy.resbuy_hooks)
        self.game.economy.resbuy_hooks[10](self.profile(), 0)
        self.assertEqual(len(self.missions()["missionInfo"]), 8)

    def test_concurrency_cap_discard_and_daily_reset_keeps_started(self):
        missions = self.missions()["missionInfo"]
        roles = self.modes._owned_roles(self.profile())
        started = []
        for mission in missions:
            if not self.modes._mission_feasible(mission["id"], roles):
                continue
            free = [uid for uid in self.uids if not any(uid in s[1] for s in started)]
            result = self.call("POST", "/fci/deploy/deploymissionattend/", {"id": mission["id"], "actorUid": free[:1]})
            if result[0] == 200:
                started.append((mission["id"], free[:1]))
            if len(started) == 2:
                break
        self.assertEqual(len(started), 2)  # DeployCount[30] = 3 allows a third, roles permitting
        self.ok("POST", "/fci/deploy/deploymissiondiscard/", {"id": started[1][0]})
        after = {m["id"]: m for m in self.missions()["missionInfo"]}
        self.assertEqual(after[started[1][0]]["startTimestamp"], 0)
        self.clock.now += 86400
        reset = self.missions()
        self.assertEqual(reset["accomplishNum"], 0)
        self.assertIn(started[0][0], [m["id"] for m in reset["missionInfo"] if m["startTimestamp"]])


class TriggeredEventTests(ModeTestBase):
    level = 40

    def add_event(self, event_id=8001):
        profile = self.profile()
        state = self.modes._state(profile)
        event = self.modes._new_event(profile, self.modes._triggered_state(state), event_id)
        self.modes._save(profile, state)
        return event

    def test_list_shape_and_ticket_search(self):
        listed = self.ok("GET", "/fci/triggered-event/")
        self.assertEqual(listed, {"eventInfo": [], "dailyTriggeredEventFinish": 0, "displayTriggeredEventTicket": False})
        self.assertEqual(self.item(80006), 5)  # daily fight tickets refreshed
        searched = self.ok("POST", "/fci/triggered-event/", {"ticketId": 80009})
        self.assertEqual((searched["eventInfo"], searched["resChange"]), ([], []))  # no ticket: nothing
        self.set_item(80009, 1)
        self.assertTrue(self.ok("GET", "/fci/triggered-event/")["displayTriggeredEventTicket"])
        searched = self.ok("POST", "/fci/triggered-event/", {"ticketId": 80009})
        self.assertEqual(searched["resChange"][0]["baseRes"]["countDelta"], -1)
        event = searched["eventInfo"][0]
        self.assertIn(event["eventId"], (8005, 8105, 8205))  # active pool 140 at Lv40
        self.assertEqual((event["eventType"], event["lastTime"]), (1, 10800))
        self.clock.now += 10800
        self.assertEqual(self.ok("GET", "/fci/triggered-event/")["eventInfo"], [])  # expired

    def test_fight_win_spends_ticket_pays_and_closes_event(self):
        event = self.add_event(8001)
        entered = self.ok("POST", "/fci/triggered-dungeon/", {"eventKey": event["eventKey"], "selectActorGroupId": 1,
                                                              "activityType": 31})
        self.assertEqual((entered["baseInfo"]["dungeonId"], entered["baseInfo"]["dungeonType"]), (8001, 13))
        uid = entered["dungeonUid"]
        self.fight("/fci/triggered-dungeon/", uid, win=True)
        result = self.ok("POST", f"/fci/triggered-dungeon/{uid}/balance/", {})
        self.assertTrue(result["win"] and result["firstFinish"])
        self.assertEqual(self.item(80006), 4)
        gold = [c["baseRes"]["countDelta"] for c in result["resChange"] if c["baseRes"]["type"] == 7]
        self.assertEqual(gold, [27000])
        listed = self.ok("GET", "/fci/triggered-event/")
        self.assertEqual((listed["eventInfo"], listed["dailyTriggeredEventFinish"]), ([], 1))

    def test_loss_keeps_event_and_daily_finish_limit(self):
        event = self.add_event(8101)
        uid = self.ok("POST", "/fci/triggered-dungeon/", {"eventKey": event["eventKey"],
                                                          "selectActorGroupId": 1})["dungeonUid"]
        self.fight("/fci/triggered-dungeon/", uid, win=False)
        self.assertFalse(self.ok("POST", f"/fci/triggered-dungeon/{uid}/balance/", {})["win"])
        self.assertEqual(self.item(80006), 5)
        self.assertEqual(len(self.ok("GET", "/fci/triggered-event/")["eventInfo"]), 1)
        profile = self.profile()
        state = self.modes._state(profile)
        state["triggered"]["finish"] = 5
        self.modes._save(profile, state)
        status, reply = self.call("POST", "/fci/triggered-dungeon/", {"eventKey": event["eventKey"]})
        self.assertEqual((status, reply["error"]), (409, "triggered_event_daily_limit"))
        self.assertEqual(self.ok("DELETE", f"/fci/triggered-dungeon/{uid}"), {})

    def test_material_win_can_trigger_a_passive_event(self):
        self.modes.rng.seed(1)
        self.modes._roll_pool = lambda pool: 8003  # force the 2xx pool roll
        uid = self.ok("POST", "/fci/activity-dungeon/", {"selectActorGroupId": 1, "dungeonId": 52101,
                                                          "activityId": 2001, "multiPower": 1})["dungeonUid"]
        self.fight("/fci/dungeon/", uid, win=True)
        result = self.ok("POST", f"/fci/activity-dungeon/{uid}/balance/", {})
        self.assertEqual(result["eventInfo"]["eventId"], 8003)
        self.assertEqual(self.ok("GET", "/fci/triggered-event/")["eventInfo"][0]["eventKey"],
                         result["eventInfo"]["eventKey"])


if __name__ == "__main__":
    unittest.main()
