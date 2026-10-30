"""Recompute player level/EXP from battle history under the client-table rule.

Profiles created before the PlayerLevelInitConfig fix stored a cumulative EXP
total earned from placeholder rewards (100 per story battle, 1,000 per level).
This replays every won battle in ``local_dungeons`` with the current rule
(player EXP = energy spent, client level thresholds, level rewards) and writes
the resulting level and in-level EXP back.  A timestamped copy of the database
is written next to it first.

Usage:
  python tools/migrate_player_exp.py --db ../../analysis/ljjc-local/state/local.sqlite3 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dungeon import NONFIGHT_STORY_IDS, LocalDungeons  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.dry_run:
        backup = args.db.with_name(f"{args.db.name}.before-exp-migration-{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(args.db, backup)
        print(f"backup: {backup}")

    connection = sqlite3.connect(str(args.db))
    connection.row_factory = sqlite3.Row
    profiles = connection.execute("SELECT account_id, level, resources_json FROM game_profiles").fetchall()
    for profile in profiles:
        account_id = int(profile["account_id"])
        battles = connection.execute(
            "SELECT dungeon_id FROM local_dungeons WHERE account_id = ? AND status = 'balanced' ORDER BY rowid",
            (account_id,),
        ).fetchall()
        resources = [
            item for item in json.loads(profile["resources_json"] or "[]")
            if isinstance(item, dict) and int(item.get("type", 0)) != 1
        ]
        replay = {"account_id": account_id, "level": 1, "resources": resources}
        for battle in battles:
            dungeon_id = int(battle["dungeon_id"])
            reward = (
                LocalDungeons._nonfight_exp_reward(dungeon_id)
                if dungeon_id in NONFIGHT_STORY_IDS
                else LocalDungeons._battle_exp_reward(dungeon_id)
            )
            LocalDungeons._apply_exp_delta(replay, reward)
        level = int(replay["level"])
        exp = next((int(r["count"]) for r in replay["resources"] if int(r.get("type", 0)) == 1), 0)
        old_exp = next(
            (int(r.get("count", 0)) for r in json.loads(profile["resources_json"] or "[]")
             if isinstance(r, dict) and int(r.get("type", 0)) == 1),
            0,
        )
        print(f"account {account_id}: battles={len(battles)} level {profile['level']}->{level} "
              f"exp {old_exp}->{exp}")
        if not args.dry_run and (level != int(profile["level"]) or exp != old_exp):
            connection.execute(
                "UPDATE game_profiles SET level = ?, resources_json = ?, updated_at = ? WHERE account_id = ?",
                (level, json.dumps(replay["resources"], separators=(",", ":")), int(time.time()), account_id),
            )
    if not args.dry_run:
        connection.commit()
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
