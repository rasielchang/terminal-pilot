#!/usr/bin/env python3
"""Package a verified portable app. Public distribution requires Developer ID + notarization."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import signing_policy

ROOT = Path(__file__).resolve().parents[1]


def run(*command): subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / '.build/signed/Terminal Pilot.app')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/release')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile; credentials are never arguments here')
    parser.add_argument('--unsigned-preview', action='store_true', help='Only the separate --development bundle; never installable over production')
    parser.add_argument('--zip-only', action='store_true', help='Skip DMG creation (ZIP still supports drag-to-Applications installation)')
    args = parser.parse_args()
    app, output = args.app.resolve(), args.output.resolve()
    if args.unsigned_preview == bool(args.notary_profile):
        parser.error('Choose a notary profile for public distribution, or explicitly request an unsigned developer preview')
    signing_policy.verify(app, development=args.unsigned_preview)
    runtime = app / 'Contents/Resources/runtime.json'
    if json.loads(runtime.read_text()): raise SystemExit('Refusing to distribute a machine-specific development build')
    run('codesign', '--verify', '--deep', '--strict', str(app))
    run(sys.executable, str(ROOT / 'scripts/verify-portable.py'), str(app))
    output.mkdir(parents=True, exist_ok=True)
    version = plistlib.loads((app / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']
    name = f'Terminal-Pilot-{version}-preview.1-macos-arm64' + ('-development-only' if args.unsigned_preview else '')
    final_zip = output / (name + '.zip')
    if final_zip.exists(): raise SystemExit('Release output already exists; choose a new output directory')
    with tempfile.TemporaryDirectory(prefix='pilot-package-') as temp:
        stage = Path(temp) / 'Terminal Pilot'; stage.mkdir()
        staged_app = stage / app.name
        shutil.copytree(app, staged_app, symlinks=True)
        signing_policy.verify(staged_app, development=args.unsigned_preview)
        if args.notary_profile:
            submission = Path(temp) / 'submission.zip'
            run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(staged_app), str(submission))
            run('xcrun', 'notarytool', 'submit', str(submission), '--keychain-profile', args.notary_profile, '--wait')
            run('xcrun', 'stapler', 'staple', str(staged_app))
            run('xcrun', 'stapler', 'validate', str(staged_app))
            run('spctl', '--assess', '--type', 'execute', '--verbose=2', str(staged_app))
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE.txt')
        if args.unsigned_preview:
            (stage / 'DEVELOPMENT-ONLY.txt').write_text(
                'Isolated development artifact. Different bundle ID; cannot update Terminal Pilot.\n'
                'Do not launch against real user data. No production installation is supported.\n')
        else:
            (stage / '安裝說明.txt').write_text(
                '此套件已通過固定 Developer ID 身分驗證與 Apple 公證。\n'
                '將 Terminal Pilot.app 拖入應用程式。更新前正常退出 Pilot 管理程式；保留 Terminal 與工作資料。\n')
            (stage / 'Applications').symlink_to('/Applications')
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(stage), str(final_zip))
        artifacts = [final_zip]
        if not args.zip_only:
            dmg = output / (name + '.dmg')
            run('hdiutil', 'create', '-volname', 'Terminal Pilot', '-srcfolder', str(stage), '-ov', '-format', 'UDZO', str(dmg))
            artifacts.append(dmg)
    (output / (name + '-SHA256SUMS.txt')).write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.name + '\n' for p in artifacts))
    print('Release artifacts:', *artifacts, sep='\n')


if __name__ == '__main__': main()
