#!/usr/bin/env python3
"""Verify a relocated App using only its runtimes and a synthetic socket transport."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from bundle_runtime import macho_files

ROOT = Path(__file__).resolve().parents[1]


def loaded_libraries(commands):
    """otool -L includes LC_ID_DYLIB; only load commands are dependencies."""
    command = None
    for line in commands.splitlines():
        fields = line.strip().split()
        if fields[:1] == ['cmd']: command = fields[1]
        if fields[:1] == ['name'] and command in {
                'LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB',
                'LC_LOAD_UPWARD_DYLIB', 'LC_LAZY_LOAD_DYLIB'}:
            yield line.strip()[5:].rsplit(' (offset ', 1)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    args = parser.parse_args()
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(args.app)], check=True)
    with tempfile.TemporaryDirectory(prefix='pilot-portable-') as temp:
        temp = Path(temp)
        home = temp / 'clean-user'; home.mkdir()
        app = temp / 'Moved folder with spaces/Terminal Pilot.app'
        shutil.copytree(args.app, app, symlinks=True)
        contents = app / 'Contents'
        for binary in macho_files(contents):
            linked = subprocess.check_output(['/usr/bin/otool', '-l', str(binary)], text=True)
            for dependency in loaded_libraries(linked):
                if dependency.startswith('/') and not dependency.startswith(('/usr/lib/', '/System/Library/')):
                    raise SystemExit(f'Non-portable native dependency in {binary.relative_to(app)}: {dependency}')
        # HOME is assigned only in the child environment to simulate a fresh user.
        env = {'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8',
               'TMPDIR': str(temp), 'TERMINAL_PILOT_STORE': str(home / 'pilot-state'),
               'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        python = contents / 'Resources/runtime/python/bin/python3'
        subprocess.run([str(python), '-I', '-B', str(ROOT / 'tests/fixtures/portable_probe.py'),
                        str(contents / 'Resources'), str(ROOT / 'tests/fixtures/socket_client.cjs')],
                       cwd=temp, env=env, timeout=30, check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print('Portable checks passed: relocated paths, clean user/PATH, bundled imports, Node WebSocket handshake, sealed signature.')


if __name__ == '__main__': main()
