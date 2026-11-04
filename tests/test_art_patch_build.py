"""Real Unity bundles with synthetic pixels; no unapproved character art is packed."""
import copy
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from external_assets import external_path, require_file

ROOT = Path(__file__).resolve().parents[1]
HOT = ROOT / 'client-patch/hot-update'
sys.path.insert(0, str(HOT))
spec = importlib.util.spec_from_file_location('art_patch_builder', HOT / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ArtPatchBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.art = self.root / 'private-art'
        self.art.mkdir()
        self.bundle_root = external_path('LJJC_PATCH_ROOT', 'analysis/ljjc_manifest_out/assets/Patch')
        self.template = 'assetbundle/texture/actorheadrectangle/actor_head_1502a'
        raw = require_file(self.bundle_root / self.template, 'LJJC_PATCH_ROOT').read_bytes()
        im = Image.new('RGBA', (256, 256), (50, 180, 90, 255))
        im.paste((0, 0, 0, 0), (0, 0, 64, 256))
        im.paste((200, 80, 30, 128), (64, 0, 128, 256))
        im.save(self.art / 'synthetic.png')
        self.png_hash = hashlib.sha256((self.art / 'synthetic.png').read_bytes()).hexdigest()
        self.origins = {'schemaVersion': 1, 'assets': [{
            'id': 'synthetic', 'path': 'synthetic.png', 'sha256': self.png_hash,
            'width': 256, 'height': 256, 'mode': 'RGBA',
            'origin': {'kind': 'generated', 'prompt': 'Python Pillow unit test pattern (not character art)', 'references': []},
            'generatedAt': '2026-11-04T00:00:00Z',
            'approval': {'status': 'approved', 'sha256': self.png_hash,
                         'confirmedBy': 'user', 'confirmedAt': '2026-11-04T00:00:00Z',
                         'evidence': 'Synthetic test fixture, never a real user artwork approval.'},
        }]}
        self.target = 'assetbundle/texture/actorheadrectangle/actor_head_999991a'
        self.config = {'appVersion': '2.0.4.59162.0', 'patchVersion': 23, 'modules': [],
                       'artOrigins': 'art-origins.json', 'textures': [{
                           'artId': 'synthetic', 'template': self.template, 'target': self.target,
                           'templateSha256': hashlib.sha256(raw).hexdigest(),
                           'width': 256, 'height': 256, 'format': 47, 'alphaSource': 'rgba'}]}

    def build(self, suffix='a'):
        (self.source / 'art-origins.json').write_text(json.dumps(self.origins), encoding='utf-8')
        config = self.source / 'modules.json'
        config.write_text(json.dumps(self.config), encoding='utf-8')
        return builder.build_candidate(config, self.bundle_root, self.root / suffix,
                                       'http://127.0.0.1:19080', 'synthetic.zip', art_root=self.art)

    def test_real_texture_build_is_repeatable_and_records_every_input(self):
        before = (self.bundle_root / self.template).read_bytes()
        first, second = self.build(), self.build('b')
        self.assertEqual(first['zipSha256'], second['zipSha256'])
        self.assertEqual((self.root / 'a/synthetic.zip').read_bytes(), (self.root / 'b/synthetic.zip').read_bytes())
        self.assertEqual((self.bundle_root / self.template).read_bytes(), before)
        record = first['textures'][0]
        self.assertEqual(record['sourceSha256'], self.png_hash)
        self.assertEqual(record['templateSha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(record['target'], self.target)
        self.assertTrue(first['validation']['textureRoundTrip'])
        self.assertTrue(first['validation']['artApproval'])
        with zipfile.ZipFile(self.root / 'a/synthetic.zip') as z:
            self.assertEqual(z.namelist(), [self.target])
            self.assertEqual(hashlib.sha256(z.read(self.target)).hexdigest(), record['bundleSha256'])

    def test_unapproved_or_changed_image_produces_no_candidate(self):
        self.origins['assets'][0]['approval']['status'] = 'pending'
        with self.assertRaisesRegex(ValueError, 'approv'):
            self.build()
        self.assertFalse((self.root / 'a').exists())
        self.origins['assets'][0]['approval']['status'] = 'approved'
        Image.new('RGBA', (256, 256), (255, 255, 255, 0)).save(self.art / 'synthetic.png')
        with self.assertRaisesRegex(ValueError, 'SHA256 changed since review'):
            self.build()
        self.assertFalse((self.root / 'a').exists())

    def test_resource_path_and_target_collisions_are_rejected(self):
        for mutation in ('duplicate', 'escape-template', 'escape-origin', 'wrong-prefix'):
            config, origins = copy.deepcopy(self.config), copy.deepcopy(self.origins)
            with self.subTest(mutation=mutation):
                if mutation == 'duplicate': self.config['textures'] *= 2
                if mutation == 'escape-template': self.config['textures'][0]['template'] = '../escape'
                if mutation == 'escape-origin': self.config['artOrigins'] = '../escape.json'
                if mutation == 'wrong-prefix': self.config['textures'][0]['target'] = 'assetbundle/data/lua/xlsx/test'
                with self.assertRaises(ValueError): self.build()
                self.assertFalse((self.root / 'a').exists())
            self.config, self.origins = config, origins

    def test_wrong_template_hash_and_wrong_image_size_fail_closed(self):
        self.config['textures'][0]['templateSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'digest|hash'): self.build()
        self.config['textures'][0]['templateSha256'] = hashlib.sha256((self.bundle_root / self.template).read_bytes()).hexdigest()
        self.config['textures'][0]['width'] = 128
        with self.assertRaisesRegex(ValueError, 'size|dimension'): self.build()
        self.assertFalse((self.root / 'a').exists())

    def test_verifier_detects_added_zip_entry_and_wrong_source(self):
        from tools.verify_hot_update_assets import verify_candidate
        self.build()
        result = verify_candidate(self.source / 'modules.json', self.root / 'a', self.bundle_root, self.art)
        self.assertTrue(result['verified'])
        original_png = (self.art / 'synthetic.png').read_bytes()
        Image.new('RGBA', (256, 256), (100, 0, 0, 0)).save(self.art / 'synthetic.png')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            verify_candidate(self.source / 'modules.json', self.root / 'a', self.bundle_root, self.art)
        (self.art / 'synthetic.png').write_bytes(original_png)
        with zipfile.ZipFile(self.root / 'a/synthetic.zip', 'a') as z: z.writestr('unexpected.txt', b'untracked')
        with self.assertRaises(ValueError):
            verify_candidate(self.source / 'modules.json', self.root / 'a', self.bundle_root, self.art)

    def test_verifier_checks_texture_candidate_when_staged(self):
        from tools.verify_hot_update_assets import verify_candidate
        self.build()
        candidate = self.root / 'a'
        fixture_root = self.root / 'fixtures'
        fixture_root.mkdir()
        shutil.copyfile(candidate / 'synthetic.zip', fixture_root / 'synthetic.zip')
        result = verify_candidate(
            self.source / 'modules.json', candidate, self.bundle_root, self.art,
            staged_manifest=candidate / 'patch-manifest.json', fixture_root=fixture_root)
        self.assertTrue(result['verified'])
        self.assertTrue(result['staged'])
        self.assertEqual(result['textureCount'], 1)


if __name__ == '__main__': unittest.main()
