"""Fail-closed identity checks shared by build, package and local update.

The Team ID is public publisher metadata, not a credential. Forks must explicitly
change the checked-in policy; no environment/CLI switch weakens production checks.
"""
import json
from pathlib import Path
import plistlib
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / 'packaging/signing-policy.json').read_text())
BUNDLE_ID = POLICY['bundleID']
TEAM_ID = POLICY['teamID']
DEVELOPMENT_ID = POLICY['developmentBundleID']


def command(*args):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise ValueError('Signature command failed: ' + error.stderr.strip()) from error


def select_identity(requested=None):
    if requested == '-':
        raise ValueError('Ad-hoc signing is prohibited for installable builds')
    result = command('/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning')
    choices = [(digest, name) for digest, name in
               re.findall(r'\b([A-Fa-f0-9]{40})\s+"([^\"]+)"', result.stdout)
               if name.startswith('Developer ID Application:') and name.endswith('(' + TEAM_ID + ')')]
    if requested:
        choices = [(digest, name) for digest, name in choices if requested in (digest, name)]
    if len(choices) != 1:
        raise ValueError('Expected one valid Developer ID Application identity for team ' + TEAM_ID +
                         '; stopped without signing fallback')
    return choices[0][0]


def requirement(identifier=None):
    parts = ['anchor apple generic',
             'certificate 1[field.1.2.840.113635.100.6.2.6] exists',
             'certificate leaf[field.1.2.840.113635.100.6.1.13] exists',
             'certificate leaf[subject.OU] = "' + TEAM_ID + '"']
    if identifier:
        parts.insert(0, 'identifier "' + identifier + '"')
    return ' and '.join(parts)


def designated_requirement(app):
    result = command('/usr/bin/codesign', '-dr', '-', str(app))
    match = re.search(r'^designated => (.+)$', result.stdout + result.stderr, re.M)
    if not match:
        raise ValueError('Missing designated requirement')
    return match.group(1)


def canonical_requirement(text):
    # codesign renders "exists" as a comment; quoting of simple IDs varies.
    match = re.search(r'^designated => (.+)$', text, re.M)
    text = match.group(1) if match else text
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'\s+|"|\bexists\b', '', text)


def info(app):
    return plistlib.loads((Path(app) / 'Contents/Info.plist').read_bytes())


def no_symlink_components(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError('Symlink output/installation paths are not permitted')
    return path.resolve()


def build_output(root, output, development=False):
    root = Path(root).resolve()
    output = no_symlink_components(output)
    build = no_symlink_components(root / '.build')
    if build not in output.parents or output.suffix != '.app' or any(p.suffix == '.app' for p in output.parents):
        raise ValueError('Build only into a fresh .build/*.app candidate; never overwrite an installation')
    expected = 'Terminal Pilot Development.app' if development else 'Terminal Pilot.app'
    if output.name != expected:
        raise ValueError('Expected candidate name: ' + expected)
    if output.exists():
        raise ValueError('Candidate already exists; choose a fresh build directory')
    return output


def validate_details(details, identifier=None):
    values = dict(line.split('=', 1) for line in details.splitlines() if '=' in line)
    if values.get('TeamIdentifier') != TEAM_ID or 'Signature=adhoc' in details:
        raise ValueError('Signature is not from the pinned Developer ID team ' + TEAM_ID)
    if 'Authority=Developer ID Application:' not in details:
        raise ValueError('Developer ID Application certificate required')
    if identifier and values.get('Identifier') != identifier:
        raise ValueError('Unexpected signing identifier')
    if not re.search(r'flags=.*\bruntime\b', details) or 'Timestamp=' not in details:
        raise ValueError('Hardened runtime and secure timestamp required')


def verify(app, development=False):
    app = Path(app).resolve()
    metadata = info(app)
    expected_id = DEVELOPMENT_ID if development else BUNDLE_ID
    if metadata.get('CFBundleIdentifier') != expected_id:
        raise ValueError('Unexpected bundle ID; development builds cannot replace production')
    if development:
        if metadata.get('PilotBuildPurpose') != 'isolated-development':
            raise ValueError('Development marker required')
        command('/usr/bin/codesign', '--verify', '--deep', '--strict', str(app))
        return metadata
    command('/usr/bin/codesign', '--verify', '--deep', '--strict', str(app))
    # --deep with -R also applies the outer identifier to nested code.
    command('/usr/bin/codesign', '--verify', '--strict', '-R', '=' + requirement(BUNDLE_ID), str(app))
    validate_details(command('/usr/bin/codesign', '-dv', '--verbose=4', str(app)).stderr, BUNDLE_ID)
    if canonical_requirement(designated_requirement(app)) != canonical_requirement(requirement(BUNDLE_ID)):
        raise ValueError('Designated requirement must use the stable publisher identity, not a build hash')
    # Resources contain native libraries not all covered by codesign --deep.
    from bundle_runtime import macho_files
    for path in macho_files(app):
        if path == app / 'Contents/MacOS/TerminalPilot':
            continue
        command('/usr/bin/codesign', '--verify', '--strict', '-R', '=' + requirement(), str(path))
        validate_details(command('/usr/bin/codesign', '-dv', '--verbose=4', str(path)).stderr)
    return metadata


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.app)
        print('Developer ID policy verified:', result['CFBundleIdentifier'], TEAM_ID, result['CFBundleVersion'])
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit('Signing policy rejected candidate: ' + str(error))
