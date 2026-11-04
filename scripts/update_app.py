#!/usr/bin/env python3
"""Check or replace a stopped local installation; never quit/open apps or touch store data."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import signing_policy as policy


def preflight(candidate, installed, backup):
    candidate = policy.no_symlink_components(candidate)
    installed = policy.no_symlink_components(installed)
    backup = policy.no_symlink_components(backup)
    if any(a == b or a in b.parents or b in a.parents
           for a, b in [(candidate, installed), (candidate, backup), (installed, backup)]):
        raise ValueError('Candidate, installed app and backup must be separate paths')
    if backup.exists():
        raise ValueError('Backup must not exist; never overwrite recovery data')
    if not backup.parent.is_dir() or installed.parent.stat().st_dev != backup.parent.stat().st_dev:
        raise ValueError('Backup parent must exist on the same filesystem as installation')
    new = policy.verify(candidate)
    if new.get('PilotBuildPurpose') != 'production':
        raise ValueError('Only production candidates may be installed')
    if json.loads((candidate / 'Contents/Resources/runtime.json').read_text()) != {}:
        raise ValueError('Updates require a self-contained portable runtime')
    old = policy.info(installed)
    if old.get('CFBundleIdentifier') != policy.BUNDLE_ID:
        raise ValueError('Installed bundle identity differs')
    if int(new['CFBundleVersion']) < int(old['CFBundleVersion']):
        raise ValueError('Refusing a build downgrade')
    policy.command('/usr/bin/codesign', '--verify', '--deep', '--strict', str(installed))
    details = policy.command('/usr/bin/codesign', '-dv', '--verbose=4', str(installed)).stderr
    migrating = 'Signature=adhoc' in details
    if not migrating:
        policy.verify(installed)
        for source, target in [(installed, candidate), (candidate, installed)]:
            req = policy.designated_requirement(source)
            policy.command('/usr/bin/codesign', '--verify', '-R', '=' + req, str(target))
    import runtime_compat
    compatibility = runtime_compat.preflight_dependencies(installed, candidate)
    return {**compatibility, 'fromBuild': old['CFBundleVersion'], 'toBuild': new['CFBundleVersion'],
            'teamID': policy.TEAM_ID, 'migratingFromAdhoc': migrating}


def require_stopped(installed, candidate=None):
    import runtime_compat
    preserved = []
    result = policy.command('/bin/ps', '-axo', 'pid=,comm=')
    for line in result.stdout.splitlines():
        row = line.strip().split(None, 1)
        if len(row) != 2:
            continue
        executable = row[1]
        if executable.endswith('/TerminalPilot'):
            raise ValueError('Pilot manager is still running; no replacement performed')
        if Path(executable).is_relative_to(installed):
            if candidate is None or not runtime_compat.is_independent_codex_wrapper(int(row[0]), executable, installed):
                raise ValueError('Pilot service or unknown bundled process is still running; no replacement performed')
            preserved.append(int(row[0]))
    if preserved:
        runtime_compat.verify_dependencies(installed, candidate)
    return preserved


@contextmanager
def service_lock(store):
    import fcntl
    # Do not create/replace the live service lock or any user state.
    with (store / 'web-service.lock').open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def install(candidate, installed, backup, store):
    receipt = preflight(candidate, installed, backup)  # BEFORE locks, writes or app operations
    with service_lock(store):
        require_stopped(installed, candidate)
        with tempfile.TemporaryDirectory(prefix='.pilot-update-', dir=installed.parent) as temp:
            staged = Path(temp) / 'Terminal Pilot.app'
            shutil.copytree(candidate, staged, symlinks=True)
            policy.verify(staged)
            require_stopped(installed, candidate)
            if backup.exists():
                raise ValueError('Backup appeared during staging; stopped')
            installed.rename(backup)
            try:
                staged.rename(installed)
                policy.verify(installed)
            except Exception:
                if installed.exists():
                    installed.rename(Path(temp) / 'failed-candidate.app')
                backup.rename(installed)
                raise
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'install'])
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--installed', required=True, type=Path)
    parser.add_argument('--backup', required=True, type=Path)
    parser.add_argument('--store', type=Path, default=Path.home() / 'Library/Application Support/Terminal Pilot')
    args = parser.parse_args()
    try:
        paths = [policy.no_symlink_components(p) for p in (args.candidate, args.installed, args.backup)]
        result = (preflight(*paths) if args.action == 'check' else install(*paths, args.store))
        print(json.dumps({'action': args.action, **result}))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('Update refused: ' + str(error))


if __name__ == '__main__':
    main()
