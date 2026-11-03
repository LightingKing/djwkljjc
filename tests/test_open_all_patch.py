"""Cumulative reopening patch inputs and independently auditable sources."""
import hashlib
import json
from pathlib import Path
import unittest

PATCH = Path(__file__).resolve().parents[1] / 'client-patch/hot-update'


class OpenAllPatchTests(unittest.TestCase):
    def test_v21_retains_v20_and_adds_only_reviewed_local_branches(self):
        base = json.loads((PATCH / 'modules-v20.json').read_text(encoding='utf-8'))
        current = json.loads((PATCH / 'modules-v21.json').read_text(encoding='utf-8'))
        origins = json.loads((PATCH / 'source-origins-v21.json').read_text(encoding='utf-8'))
        self.assertEqual(current['patchVersion'], 21)
        self.assertEqual(current['modules'][:len(base['modules'])], base['modules'])
        self.assertEqual({row['source'] for row in current['modules'][len(base['modules']):]}, {
            'sources/gacha-open-all.lua', 'sources/gachanormal-open-all.lua',
            'sources/gachaeventautumn-open-all.lua'})
        self.assertEqual(origins['baseModulesSha256'], hashlib.sha256((PATCH / 'modules-v20.json').read_bytes()).hexdigest())
        self.assertEqual(len(current['modules']), len(origins['modules']))
        for module, origin in zip(current['modules'], origins['modules']):
            self.assertEqual(module['target'], origin['target'])
            self.assertEqual(module['source'], origin['destination'])
            source = (PATCH / module['source']).read_bytes()
            self.assertNotIn(b'\r\n', source)
            self.assertEqual(hashlib.sha256(source).hexdigest(), origin['patchedSha256'])
