import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import signing_policy as policy
import bundle_runtime
import update_app
import runtime_compat

GOOD = ('Identifier=' + policy.BUNDLE_ID + '\nTeamIdentifier=' + policy.TEAM_ID +
        '\nAuthority=Developer ID Application: Publisher (' + policy.TEAM_ID + ')\n'
        'CodeDirectory v=20500 size=500 flags=0x10000(runtime) hashes=8+7 location=embedded\n'
        'Timestamp=Nov 4, 2026 at 12:00:00 PM\n')
IDENTITY = 'A' * 40


def completed(stdout='', stderr=''):
    return subprocess.CompletedProcess([], 0, stdout, stderr)


class SigningPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.app = self.root / '.build/candidate/Terminal Pilot.app'
        (self.app / 'Contents/Resources').mkdir(parents=True)
        self.metadata = {'CFBundleIdentifier': policy.BUNDLE_ID, 'CFBundleVersion': '54',
                         'PilotBuildPurpose': 'production'}
        self.write_info()
        (self.app / 'Contents/Resources/runtime.json').write_text('{}')

    def write_info(self):
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps(self.metadata))

    def test_missing_wrong_team_ambiguous_identity_never_falls_back(self):
        for text in ['', IDENTITY + ' "Apple Development: Publisher (' + policy.TEAM_ID + ')"',
                     IDENTITY + ' "Developer ID Application: Publisher (OTHERTEAM)"',
                     '\n'.join([IDENTITY + ' "Developer ID Application: Publisher (' + policy.TEAM_ID + ')"'] * 2)]:
            with self.subTest(text=text), patch.object(policy, 'command', return_value=completed(text)):
                with self.assertRaises(ValueError): policy.select_identity()
        with patch.object(policy, 'command') as run:
            with self.assertRaises(ValueError): policy.select_identity('-')
            run.assert_not_called()

    def test_existing_identity_is_selected_without_reading_private_key(self):
        with patch.object(policy, 'command', return_value=completed(
                IDENTITY + ' "Developer ID Application: Publisher (' + policy.TEAM_ID + ')"')) as run:
            self.assertEqual(policy.select_identity(), IDENTITY)
            run.assert_called_once_with('/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning')

    def test_wrong_identity_adhoc_missing_runtime_or_timestamp_rejected(self):
        for text in [GOOD.replace(policy.TEAM_ID, 'OTHERTEAM'), GOOD + 'Signature=adhoc\n',
                     GOOD.replace('Developer ID Application:', 'Apple Development:'),
                     GOOD.replace('runtime', 'adhoc'), GOOD.replace('Timestamp=', 'Signed Time='),
                     GOOD.replace('Identifier=' + policy.BUNDLE_ID, 'Identifier=wrong')]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                policy.validate_details(text, policy.BUNDLE_ID)
        policy.validate_details(GOOD, policy.BUNDLE_ID)

    def test_build_rejects_installed_existing_and_symlink_paths_before_writing(self):
        with self.assertRaises(ValueError): policy.build_output(self.root, self.app)
        with self.assertRaises(ValueError): policy.build_output(self.root, self.root / 'dist/Terminal Pilot.app')
        outside = self.root / 'outside'; outside.mkdir()
        try:
            (self.root / '.build/link').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Symlink creation unavailable')
        with self.assertRaises(ValueError): policy.build_output(self.root, self.root / '.build/link/Terminal Pilot.app')
        self.assertFalse((outside / 'Terminal Pilot.app').exists())

    def test_development_has_separate_bundle_and_filename(self):
        with self.assertRaises(ValueError): policy.build_output(self.root, self.root / '.build/dev/Terminal Pilot.app', True)
        with patch.object(policy, 'command') as run:
            with self.assertRaises(ValueError): policy.verify(self.app, development=True)
            run.assert_not_called()
        self.metadata.update(CFBundleIdentifier=policy.DEVELOPMENT_ID, PilotBuildPurpose='isolated-development')
        self.write_info()
        with patch.object(policy, 'command') as run:
            with self.assertRaises(ValueError): policy.verify(self.app)
            run.assert_not_called()

    def test_sign_helper_cannot_silently_use_adhoc(self):
        with patch.object(bundle_runtime.subprocess, 'run') as run:
            with self.assertRaises(ValueError): bundle_runtime.sign(self.app, '-')
            with self.assertRaises(ValueError): bundle_runtime.sign(self.app, '-', development=True)
            run.assert_not_called()

    def test_nested_invalid_binary_stops_verification(self):
        def command(*args):
            if args[-1].endswith('helper') and '--verify' in args:
                raise subprocess.CalledProcessError(1, args)
            if '-dv' in args: return completed(stderr=GOOD)
            if '-dr' in args: return completed(stderr='designated => ' + policy.requirement(policy.BUNDLE_ID))
            return completed()
        with patch.object(policy, 'command', side_effect=command), \
             patch.object(bundle_runtime, 'macho_files', return_value=[self.app / 'Contents/Resources/helper']):
            with self.assertRaises(subprocess.CalledProcessError): policy.verify(self.app)

    def test_verified_production_checks_inline_requirements_separately_from_deep(self):
        calls = []
        def command(*args):
            calls.append(args)
            if '-dv' in args: return completed(stderr=GOOD)
            if '-dr' in args:
                return completed('designated => ' + policy.requirement(policy.BUNDLE_ID) + '\n',
                                 'Executable=/temporary app/Contents/MacOS/TerminalPilot\n')
            return completed()
        with patch.object(policy, 'command', side_effect=command), \
             patch.object(bundle_runtime, 'macho_files', return_value=[]):
            self.assertEqual(policy.verify(self.app), self.metadata)
        constrained = [c for c in calls if '-R' in c]
        self.assertTrue(constrained)
        for c in constrained:
            self.assertTrue(c[c.index('-R') + 1].startswith('='))
            self.assertNotIn('--deep', c)

    def test_actual_build_cli_rejects_adhoc_without_creating_output(self):
        target = policy.ROOT / '.build/signing-rejection-proof/Terminal Pilot.app'
        self.assertFalse(target.exists())
        result = subprocess.run([sys.executable, '-B', str(policy.ROOT / 'scripts/build.py'),
                                 '--identity', '-', '--output', str(target)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Ad-hoc signing is prohibited', result.stderr)
        self.assertFalse(target.exists())

    def test_development_packaging_cannot_relabel_a_production_bundle(self):
        spec = importlib.util.spec_from_file_location('package_release', policy.ROOT / 'scripts/package-release.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(sys, 'argv', ['package-release.py', '--app', str(self.app), '--unsigned-preview']), \
             patch.object(module, 'run') as run:
            with self.assertRaises(ValueError): module.main()
            run.assert_not_called()

    def test_hash_bound_designated_requirement_rejected(self):
        def command(*args):
            if '-dv' in args: return completed(stderr=GOOD)
            if '-dr' in args: return completed(stderr='designated => cdhash H"deadbeef"')
            return completed()
        with patch.object(policy, 'command', side_effect=command):
            with self.assertRaisesRegex(ValueError, 'stable publisher'): policy.verify(self.app)

    def test_update_rejects_before_lock_copy_or_process_action(self):
        installed = self.root / 'Terminal Pilot.app'; installed.mkdir()
        with patch.object(policy, 'verify', side_effect=ValueError('ad-hoc rejected')), \
             patch.object(update_app, 'service_lock') as lock, \
             patch.object(update_app.shutil, 'copytree') as copy, \
             patch.object(update_app, 'require_stopped') as stopped:
            with self.assertRaises(ValueError): update_app.install(self.app, installed, self.root / 'backup.app', self.root)
            lock.assert_not_called(); copy.assert_not_called(); stopped.assert_not_called()

    def test_live_parent_or_orphan_child_prevents_replacement(self):
        for process in ['/Applications/Other Pilot.app/Contents/MacOS/TerminalPilot',
                        str(self.app / 'Contents/Resources/runtime/python/bin/python3')]:
            with patch.object(policy, 'command', return_value=completed('10 ' + process)):
                with self.assertRaisesRegex(ValueError, 'still running'): update_app.require_stopped(self.app)

    def test_only_independent_terminal_wrapper_with_equal_dependencies_can_survive(self):
        output = '10 ' + str(self.app / 'Contents/Resources/runtime/python/bin/python3')
        with patch.object(policy, 'command', return_value=completed(output)), \
             patch.object(runtime_compat, 'is_independent_codex_wrapper', return_value=True), \
             patch.object(runtime_compat, 'verify_dependencies', return_value={'files': 5}) as verify:
            self.assertEqual(update_app.require_stopped(self.app, self.root / 'candidate.app'), [10])
            verify.assert_called_once_with(self.app, self.root / 'candidate.app')
        with patch.object(policy, 'command', return_value=completed(output)), \
             patch.object(runtime_compat, 'is_independent_codex_wrapper', return_value=False):
            with self.assertRaises(ValueError): update_app.require_stopped(self.app, self.root / 'candidate.app')
        with patch.object(policy, 'command', return_value=completed(output)), \
             patch.object(runtime_compat, 'is_independent_codex_wrapper', return_value=True), \
             patch.object(runtime_compat, 'verify_dependencies', side_effect=ValueError('runtime changed')):
            with self.assertRaises(ValueError): update_app.require_stopped(self.app, self.root / 'candidate.app')

    def test_wrapper_argv_and_terminal_ancestry_are_both_required(self):
        python = self.app / 'Contents/Resources/runtime/python/bin/python3'
        launch = str(python) + ' -E -s -B ' + str(self.app / 'Contents/Resources/bridge.py') + ' launch codex --session synthetic'
        for args, parent, expected in [(launch, True, True), (launch, False, False),
                                       (launch.replace('bridge.py', 'web_server.py'), True, False),
                                       (launch.replace('launch codex', 'hook'), True, False)]:
            with patch.object(policy, 'command', return_value=completed(args)), \
                 patch.object(runtime_compat, 'terminal_ancestor', return_value=parent):
                self.assertEqual(runtime_compat.is_independent_codex_wrapper(10, str(python), self.app), expected)

    def test_native_comparison_ignores_only_valid_signature_extent(self):
        def binary(signature):
            header = struct.pack('<IIIIIIII', 0xfeedfacf, 0x0100000c, 0, 2, 2, 88, 0, 0)
            segment = struct.pack('<II16sQQQQIIII', 0x19, 72, b'__LINKEDIT', 0,
                                  4096, 120, 16 + len(signature), 1, 1, 0, 0)
            sig = struct.pack('<IIII', 0x1d, 16, 136, len(signature))
            return header + segment + sig + b'unchanged native' + signature
        a = self.root / 'native-a'; b = self.root / 'native-b'
        a.write_bytes(binary(b'old-signature'));b.write_bytes(binary(b'new-official-signature'))
        self.assertEqual(runtime_compat.unsigned_digest(a), runtime_compat.unsigned_digest(b))
        changed = bytearray(b.read_bytes());changed[125] ^= 1;b.write_bytes(changed)
        self.assertNotEqual(runtime_compat.unsigned_digest(a), runtime_compat.unsigned_digest(b))
        b.write_bytes(binary(b'new') + b'extra')
        with self.assertRaises(ValueError): runtime_compat.unsigned_digest(b)
        b.write_bytes(b'unknown native layout')
        with self.assertRaises(ValueError): runtime_compat.unsigned_digest(b)

    def test_runtime_preflight_checks_wrappers_before_manager_quit(self):
        python = self.app / 'Contents/Resources/runtime/python/bin/python3'
        with patch.object(policy, 'command', return_value=completed('10 ' + str(python))), \
             patch.object(runtime_compat, 'is_independent_codex_wrapper', return_value=True), \
             patch.object(runtime_compat, 'verify_dependencies', side_effect=ValueError('incompatible runtime')):
            with self.assertRaisesRegex(ValueError, 'incompatible'):
                runtime_compat.preflight_dependencies(self.app, self.root / 'new.app')

    def test_changed_wrapper_or_runtime_bytes_are_never_ignored(self):
        other = self.root / 'other.app'
        for app in [self.app, other]:
            resource = app / 'Contents/Resources';(resource / 'runtime').mkdir(parents=True, exist_ok=True)
            for name in ['bridge.py', 'codex_managed.py', 'runtime_paths.py', 'runtime/module.py']:
                (resource / name).write_text('same')
        self.assertEqual(runtime_compat.verify_dependencies(self.app, other)['files'], 1)
        (other / 'Contents/Resources/runtime/module.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'runtime bytes changed'): runtime_compat.verify_dependencies(self.app, other)
        (other / 'Contents/Resources/bridge.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'wrapper code changed'): runtime_compat.verify_dependencies(self.app, other)

    def test_update_revalidates_staged_bytes_before_renaming(self):
        from contextlib import nullcontext
        installed = self.root / 'installed.app'; installed.mkdir()
        (installed / 'original').write_text('keep')
        with patch.object(update_app, 'preflight', return_value={}), \
             patch.object(update_app, 'service_lock', return_value=nullcontext()), \
             patch.object(update_app, 'require_stopped'), \
             patch.object(policy, 'verify', side_effect=ValueError('tampered stage')):
            with self.assertRaises(ValueError): update_app.install(self.app, installed, self.root / 'backup.app', self.root)
        self.assertEqual((installed / 'original').read_text(), 'keep')
        self.assertFalse((self.root / 'backup.app').exists())

    def test_install_failure_restores_app_without_touching_store(self):
        from contextlib import nullcontext
        installed = self.root / 'installed.app'; installed.mkdir()
        (installed / 'original').write_text('keep')
        store = self.root / 'store'; store.mkdir(); (store / 'data').write_text('latest')
        with patch.object(update_app, 'preflight', return_value={}), \
             patch.object(update_app, 'service_lock', return_value=nullcontext()), \
             patch.object(update_app, 'require_stopped'), \
             patch.object(policy, 'verify', side_effect=[{}, ValueError('post-copy failure')]):
            with self.assertRaises(ValueError): update_app.install(self.app, installed, self.root / 'backup.app', store)
        self.assertEqual((installed / 'original').read_text(), 'keep')
        self.assertEqual((store / 'data').read_text(), 'latest')

    def test_packager_rejects_adhoc_before_portable_probe_or_notary(self):
        spec = importlib.util.spec_from_file_location('package_release', policy.ROOT / 'scripts/package-release.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with patch.object(sys, 'argv', ['package-release.py', '--app', str(self.app), '--notary-profile', 'test']), \
             patch.object(policy, 'verify', side_effect=ValueError('adhoc')), patch.object(module, 'run') as run:
            with self.assertRaises(ValueError): module.main()
            run.assert_not_called()


if __name__ == '__main__': unittest.main()
