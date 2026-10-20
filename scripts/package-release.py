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

ROOT = Path(__file__).resolve().parents[1]


def run(*command): subprocess.run(command, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=ROOT / '.build/preview/Terminal Pilot.app')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/release')
    parser.add_argument('--notary-profile', help='Existing notarytool Keychain profile; credentials are never arguments here')
    parser.add_argument('--unsigned-preview', action='store_true', help='Local developer preview only; named unnotarized')
    parser.add_argument('--zip-only', action='store_true', help='Skip DMG creation (ZIP still supports drag-to-Applications installation)')
    args = parser.parse_args()
    app, output = args.app.resolve(), args.output.resolve()
    if args.unsigned_preview == bool(args.notary_profile):
        parser.error('Choose a notary profile for public distribution, or explicitly request an unsigned developer preview')
    runtime = app / 'Contents/Resources/runtime.json'
    if json.loads(runtime.read_text()): raise SystemExit('Refusing to distribute a machine-specific development build')
    run('codesign', '--verify', '--deep', '--strict', str(app))
    run(sys.executable, str(ROOT / 'scripts/verify-portable.py'), str(app))
    if args.notary_profile:
        signature = subprocess.run(['codesign', '-dv', '--verbose=4', str(app)], capture_output=True, text=True, check=True).stderr
        if 'Authority=Developer ID Application:' not in signature or 'runtime' not in signature:
            raise SystemExit('Public distribution needs a Developer ID Application signature with hardened runtime')
    output.mkdir(parents=True, exist_ok=True)
    version = plistlib.loads((app / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString']
    name = f'Terminal-Pilot-{version}-preview.1-macos-arm64' + ('-unnotarized' if args.unsigned_preview else '')
    final_zip = output / (name + '.zip')
    if final_zip.exists(): raise SystemExit('Release output already exists; choose a new output directory')
    with tempfile.TemporaryDirectory(prefix='pilot-package-') as temp:
        stage = Path(temp) / 'Terminal Pilot'; stage.mkdir()
        staged_app = stage / app.name
        shutil.copytree(app, staged_app, symlinks=True)
        if args.notary_profile:
            submission = Path(temp) / 'submission.zip'
            run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(staged_app), str(submission))
            run('xcrun', 'notarytool', 'submit', str(submission), '--keychain-profile', args.notary_profile, '--wait')
            run('xcrun', 'stapler', 'staple', str(staged_app))
            run('xcrun', 'stapler', 'validate', str(staged_app))
            run('spctl', '--assess', '--type', 'execute', '--verbose=2', str(staged_app))
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE.txt')
        (stage / '安裝說明.txt').write_text('將 Terminal Pilot.app 拖入「應用程式」後開啟。\n首次允許控制 Terminal；右下角齒輪可設定工具與語音。\n更新前請 ⌘Q 結束舊版，等待退出後再替換；專案與配對資料會保留。\n' +
            ('此套件尚未經 Apple 公證，僅供本機開發驗證；請勿當作一般使用者正式安裝版。\n' if args.unsigned_preview else '此套件已通過 Developer ID 簽章與 Apple 公證。\n'))
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
