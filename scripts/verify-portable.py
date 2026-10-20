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
            linked = subprocess.check_output(['/usr/bin/otool', '-L', str(binary)], text=True)
            for line in linked.splitlines()[1:]:
                dependency = line.strip().split(' (', 1)[0]
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
