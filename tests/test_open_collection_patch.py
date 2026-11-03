"""The collection candidate must retain every released module and exact provenance."""
import hashlib
import json
from pathlib import Path
import unittest

PATCH = Path(__file__).resolve().parents[1] / 'client-patch/hot-update'


class OpenCollectionPatchTests(unittest.TestCase):
    def test_v22_preserves_existing_targets_and_hashes_every_source(self):
        base_path = PATCH / 'modules-v21.json'
        base = json.loads(base_path.read_text(encoding='utf-8'))
        candidate = json.loads((PATCH / 'modules-v22.json').read_text(encoding='utf-8'))
        origins = json.loads((PATCH / 'source-origins-v22.json').read_text(encoding='utf-8'))
        self.assertEqual(candidate['patchVersion'], 22)
        before = {row['target']: row for row in base['modules']}
        after = {row['target']: row for row in candidate['modules']}
        self.assertEqual(len(after), len(candidate['modules']))
        self.assertEqual(set(before) - set(after), set())
        for target, old in before.items():
            if target in ('assetbundle/data/lua/common/actorutil', 'assetbundle/data/lua/common/windowutil',
                          'assetbundle/data/lua/window/welfare2/wlfdecrepittransport'):
                self.assertEqual(after[target]['originalSha256'], old['originalSha256'])
            else:
                self.assertEqual(after[target], old)
        self.assertIn('assetbundle/data/lua/xlsx/actorskin', after)
        self.assertIn('assetbundle/data/lua/xlsx/collectionsscenes', after)
        self.assertEqual(origins['baseModules'], 'modules-v21.json')
        self.assertEqual(origins['baseModulesSha256'], hashlib.sha256(base_path.read_bytes()).hexdigest())
        self.assertEqual(len(origins['modules']), len(after))
        for module, origin in zip(candidate['modules'], origins['modules']):
            self.assertEqual(module['target'], origin['target'])
            source = (PATCH / module['source']).read_bytes()
            self.assertNotIn(b'\r\n', source)
            self.assertEqual(hashlib.sha256(source).hexdigest(), origin['patchedSha256'])
