"""Verify Lua and reviewed texture inputs in an offline cumulative candidate.

All roots are explicit. --staged additionally checks the requested manifest and
fixture directory; verification never edits a service, manifest, or user state.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
HOT = ROOT / 'client-patch/hot-update'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HOT))
spec = importlib.util.spec_from_file_location('asset_candidate_builder', HOT / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _below(root, path):
    result = (root / builder.relative_path(path)).resolve(strict=True)
    if not result.is_relative_to(root.resolve()):
        raise ValueError('candidate path escaped its input tree')
    return result


def verify_candidate(config_path: Path, candidate: Path, bundle_root: Path,
                     art_root: Path | None = None, *, staged_manifest: Path | None = None,
                     fixture_root: Path | None = None) -> dict:
    config_path, candidate, bundle_root = config_path.resolve(), candidate.resolve(), bundle_root.resolve()
    source_root = config_path.parent
    config = json.loads(config_path.read_bytes())
    report = json.loads((candidate / 'build-report.json').read_bytes())
    manifest = json.loads((candidate / 'patch-manifest.json').read_bytes())
    zip_path = _below(candidate, report['zip'])
    raw = zip_path.read_bytes()
    if len(manifest['history']) != 1:
        raise ValueError('candidate must contain one cumulative patch')
    entry = manifest['history'][0]
    from server.core.patch_versions import select_patch_manifest
    select_patch_manifest(manifest, None)
    if not all((
        manifest['version'] == config['appVersion'] == report['appVersion'],
        entry['version'] == config['patchVersion'] == report['patchVersion'],
        entry['length'] == report['zipLength'] == len(raw),
        entry['checksum'] == report['zipCrc32'] == zlib.crc32(raw),
        report['zipSha256'] == digest(raw),
    )):
        raise ValueError('candidate manifest/report/ZIP mismatch')
    modules, textures = config['modules'], config.get('textures', [])
    expected = [row['target'] for row in modules + textures]
    if len(expected) != len(set(expected)):
        raise ValueError('duplicate candidate target')
    module_records = {row['target']: row for row in report['modules']}
    texture_records = {row['target']: row for row in report.get('textures', [])}
    if len(module_records) != len(report['modules']) or set(module_records) != {r['target'] for r in modules}:
        raise ValueError('module report inventory mismatch')
    if len(texture_records) != len(report.get('textures', [])) or set(texture_records) != {r['target'] for r in textures}:
        raise ValueError('texture report inventory mismatch')
    origins = {}
    if textures:
        if art_root is None:
            raise ValueError('texture verification requires private art root')
        origins_raw = _below(source_root, config['artOrigins']).read_bytes()
        if digest(origins_raw) != report['artOriginsSha256']:
            raise ValueError('art origins digest mismatch')
        document = json.loads(origins_raw)
        origins = {r['id']: r for r in document['assets']}
        if document.get('schemaVersion') != 1 or len(origins) != len(document['assets']):
            raise ValueError('invalid art origins')
    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip() is not None or sorted(archive.namelist()) != sorted(expected):
            raise ValueError('candidate ZIP content inventory mismatch')
        for module in modules:
            record = module_records[module['target']]
            source = _below(source_root, module['source']).read_bytes()
            original = _below(bundle_root, module['target']).read_bytes()
            if digest(original) != module['originalSha256'] or digest(source) != record['sourceSha256']:
                raise ValueError('Lua source/template digest mismatch')
            rebuilt, _ = builder.compile_bundle(original, source)
            content = archive.read(module['target'])
            if (rebuilt != content or digest(content) != record['bundleSha256']
                    or content != _below(candidate / 'bundles', module['target']).read_bytes()):
                raise ValueError('Lua candidate content mismatch')
        for texture in textures:
            from tools.art.bundle import clone_texture_bundle
            from tools.art.origins import verify_approved_asset
            record = texture_records[texture['target']]
            png = verify_approved_asset(origins[texture['artId']], art_root)
            original = _below(bundle_root, texture['template']).read_bytes()
            if (digest(original) != texture['templateSha256'] or digest(original) != record['templateSha256']
                    or digest(png) != record['sourceSha256']):
                raise ValueError('texture source/template digest mismatch')
            rebuilt, _ = clone_texture_bundle(original, png, texture['target'],
                expected_size=(texture['width'], texture['height']), expected_format=texture['format'],
                alpha_source=texture['alphaSource'])
            content = archive.read(texture['target'])
            if (rebuilt != content or digest(content) != record['bundleSha256']
                    or content != _below(candidate / 'bundles', texture['target']).read_bytes()):
                raise ValueError('texture candidate content mismatch')
    if (staged_manifest is None) != (fixture_root is None):
        raise ValueError('staged verification requires both manifest and fixture root')
    if staged_manifest is not None:
        if json.loads(staged_manifest.read_bytes()) != manifest:
            raise ValueError('staged manifest differs from candidate')
        for url in entry['urls']:
            if _below(fixture_root, urlsplit(url).path.lstrip('/')).read_bytes() != raw:
                raise ValueError('staged ZIP differs from candidate')
    return dict(verified=True, staged=staged_manifest is not None, version=config['patchVersion'],
                moduleCount=len(modules), textureCount=len(textures), zipSha256=digest(raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--candidate-directory', type=Path, required=True)
    parser.add_argument('--bundle-root', type=Path, required=True)
    parser.add_argument('--art-root', type=Path)
    parser.add_argument('--staged', action='store_true')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--fixtures', type=Path)
    args = parser.parse_args()
    if args.staged != (args.manifest is not None and args.fixtures is not None):
        parser.error('--staged requires --manifest and --fixtures together')
    print(json.dumps(verify_candidate(args.config, args.candidate_directory, args.bundle_root, args.art_root,
        staged_manifest=args.manifest, fixture_root=args.fixtures), indent=2))


if __name__ == '__main__': main()
