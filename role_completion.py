"""Explicit local role-data overlay; original APK exports stay byte-for-byte intact.

This candidate data does not add an actor to any shop/pool/obtainability list.
Artwork approval and isolated combat remain separate release gates.
"""
from copy import deepcopy
import json
from pathlib import Path

_POLICY = json.loads((Path(__file__).resolve().parent /
                     'data/server-only/role-completion-20261104.json').read_text(encoding='utf-8'))


def apply_table_overlay(name: str, rows: list[dict]) -> list[dict]:
    patches = _POLICY['tables'].get(name)
    if not patches:
        return rows
    result = deepcopy(rows)
    for patch in patches:
        expected_key = patch['key']
        def matches(row):
            return all(row.get(k, '' if isinstance(v, str) else 0) == v for k, v in expected_key.items())
        position = next((i for i, row in enumerate(result) if matches(row)), None)
        if position is None:
            if patch['original'] is not None:
                raise ValueError(f'{name} local overlay expected original row {expected_key}')
            result.append(deepcopy(patch['row']))
        else:
            current = result[position]
            if current == patch['row']:
                continue
            if patch['original'] is None or current != patch['original']:
                raise ValueError(f'{name} local overlay original changed: {expected_key}')
            result[position] = deepcopy(patch['row'])
    return result
