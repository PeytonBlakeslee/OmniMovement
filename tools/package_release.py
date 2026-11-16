"""Package this repository's source and matching Win32 backend, without a game session."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'tools/release-config.json').read_text())

# Player runtime files only. Native backend and required licenses are added below.
PLAYER_SOURCE_FILES = frozenset({
    '__init__.py',
    '_settings_migration.py',
    'coyote.py',
    'fire_pose.py',
    'interruptions.py',
    'native_hipfire.py',
    'precision.py',
    'precision_native_adapter.py',
    'pyproject.toml',
    'recovery_budget.py',
    'reversal.py',
    'travel_input.py',
    'README.md',
    'LICENSE-NOTICE.txt',
})
SOURCE_ONLY_FILES = frozenset({})


def sha(data):
    return hashlib.sha256(data).hexdigest()

def _require(condition, message):
    if not condition:
        raise ValueError(message)


def audit_pe(data):
    _require(data[:2] == b'MZ', 'Not a PE file')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    _require(data[pe:pe + 4] == b'PE\0\0', 'Missing PE signature')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    _require(machine == 0x14c, 'Expected Windows x86')
    optional = pe + 24
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    _require(struct.unpack_from('<H', data, optional)[0] == 0x10b, 'Expected PE32 optional header')
    sections = []
    for n in range(count):
        virtual_size, rva, size, offset = struct.unpack_from('<IIII', data, optional + optional_size + n * 40 + 8)
        sections.append((rva, max(virtual_size, size), offset))
    def at(rva):
        for base, size, offset in sections:
            if base <= rva < base + size:
                return offset + rva - base
        raise ValueError('Unmapped PE RVA')
    def cstr(offset):
        return data[offset:data.index(b'\0', offset)].decode('ascii')
    directory = at(struct.unpack_from('<I', data, optional + 96)[0])
    names_count = struct.unpack_from('<I', data, directory + 24)[0]
    names = at(struct.unpack_from('<I', data, directory + 32)[0])
    exports = [cstr(at(struct.unpack_from('<I', data, names + 4 * i)[0])) for i in range(names_count)]
    _require('PyInit_' + CONFIG['module'] in exports, 'Missing PyInit export')
    imports = []
    pos = at(struct.unpack_from('<I', data, optional + 104)[0])
    while any(data[pos:pos + 20]):
        imports.append(cstr(at(struct.unpack_from('<I', data, pos + 12)[0])))
        pos += 20
    _require('unrealsdk.dll' in [s.lower() for s in imports], 'Missing unrealsdk.dll import')
    _require('python314.dll' in [s.lower() for s in imports], 'Missing python314.dll import')
    for marker in CONFIG['api_markers']:
        _require(marker.encode() in data, 'Missing native marker: ' + marker)
    _require(CONFIG['backend_version'].encode() + b'\0' in data, 'Missing native backend version')
    return {'machine': 'PE i386', 'exports': exports, 'imports': imports}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--source-commit', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY', ''))
    parser.add_argument('--run-id', default=os.environ.get('GITHUB_RUN_ID', ''))
    args = parser.parse_args()
    commit = args.source_commit or subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    source = ROOT / 'mod' / CONFIG['folder']
    version = tomllib.loads((source / 'pyproject.toml').read_text())['project']['version']
    # mods_base.build_mod's installed default parser accepts numeric components only.
    _require(len(version.split('.')) == 3 and all(part.isdecimal() for part in version.split('.')),
             'SDK mod versions must be three numeric components (candidate status belongs in validation notes)')
    native = args.native.read_bytes()
    pe = audit_pe(native)
    args.output.mkdir(parents=True, exist_ok=True)
    stage = args.output / 'staging' / CONFIG['folder']
    if stage.exists():
        raise SystemExit('Use an empty output directory; existing staging is never overwritten.')
    source_branch = os.environ.get('GITHUB_REF_NAME') or subprocess.check_output(
        ['git', 'branch', '--show-current'], cwd=ROOT, text=True).strip()
    if source_branch in ('main', 'release-candidate'):
        # Keep the repository homepage version-neutral; verify the installed guide.
        player_readme = source / 'README.md'
        if version not in player_readme.read_text(encoding='utf-8'):
            raise ValueError(f'Player documentation version mismatch: {player_readme}')
        # Fail closed on new player files; known repo-only docs are not installed.
        for item in source.rglob('*'):
            if item.is_symlink():
                raise ValueError(f'Symlink in player source: {item}')
            if item.is_file():
                rel = item.relative_to(source).as_posix()
                if item.suffix in ('.pyc', '.pyo') or '__pycache__' in item.parts:
                    continue
                if rel not in PLAYER_SOURCE_FILES and rel not in SOURCE_ONLY_FILES:
                    raise ValueError(f'Unreviewed player file: {rel}')
        stage.mkdir(parents=True)
        for relative in sorted(PLAYER_SOURCE_FILES):
            src = source / relative
            if not src.is_file():
                raise ValueError(f'Required player source missing: {relative}')
            shutil.copy2(src, stage / relative)
    else:
        shutil.copytree(source, stage, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'reports', 'VALIDATION.md'))
    shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', stage / 'THIRD_PARTY_NOTICES.md')
    shutil.copytree(ROOT / 'licenses', stage / 'licenses')
    # Keep developer validation captures in the testing/source tree, not the player ZIP.
    (stage / (CONFIG['module'] + '.pyd')).write_bytes(native)
    info = {
        'mod_version': version, 'native_backend': CONFIG['backend_version'],
        'source_commit': commit, 'repository': args.repository,
        'workflow_run': f'https://github.com/{args.repository}/actions/runs/{args.run_id}' if args.repository and args.run_id else None,
        'binary_sha256': sha(native), 'architecture': 'PE i386', 'python': '3.14.3 win32',
        'pyunrealsdk_commit': 'c72c5558473bfa1f1cbc9c9113efb2a02251e656',
        'unrealsdk_commit': 'b1852aa440277afcce8a13566b5487aeae5a7ae9',
        'game_acceptance': CONFIG.get('game_acceptance', 'Preserved historical release evidence; no new game test for repository migration.'),
        'pe_audit': pe,
    }
    # Build provenance stays beside the release archives, never in sdk_mods.
    package = args.output / f"{CONFIG['product']}-v{version}-Win32.zip"
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(stage.parent).as_posix())
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    source_zip = args.output / f"{CONFIG['product']}-Source-v{version}.zip"
    with zipfile.ZipFile(source_zip, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(filter(None, tracked)):
            archive.write(ROOT / name, f"{CONFIG['product']}-Source/{name}")
    manifest = {}
    for path in (package, source_zip):
        with zipfile.ZipFile(path) as archive:
            _require(archive.testzip() is None, 'Damaged ZIP member')
            _require(len(set(archive.namelist())) == len(archive.namelist()), 'Duplicate ZIP member')
            _require(all(not Path(n).is_absolute() and '..' not in Path(n).parts for n in archive.namelist()), 'Unsafe ZIP path')
        manifest[path.name] = {'sha256': sha(path.read_bytes()), 'bytes': path.stat().st_size}
    (args.output / 'PACKAGE-HASHES.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (args.output / 'BUILD_INFO.json').write_text(json.dumps(info, indent=2) + '\n')
    print(json.dumps({'result': 'PASS', 'version': version, 'packages': manifest}, indent=2))

if __name__ == '__main__':
    main()
