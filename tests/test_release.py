import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import runtime_paths
import voice_keychain
import voice_service
import bundle_runtime
import public_source


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = self.home / 'store'; self.store.mkdir()

    def executable(self, name):
        p = self.home / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#!/bin/sh\nexit 0\n'); p.chmod(0o700)
        return str(p)

    def test_discovery_from_finder_and_explicit_override(self):
        codex = self.executable('.local/bin/codex')
        with patch.object(Path, 'home', return_value=self.home), patch.dict(os.environ, {'PATH': '/usr/bin:/bin', 'TERMINAL_PILOT_STORE': str(self.store)}):
            self.assertEqual(runtime_paths.find_tool('codex'), codex)
            chosen = self.executable('custom install/codex')
            (self.store / 'tools.json').write_text(json.dumps({'codex': chosen}))
            self.assertEqual(runtime_paths.find_tool('codex'), chosen)
            Path(chosen).unlink()
            self.assertIsNone(runtime_paths.find_tool('codex'), 'A removed explicit tool must not silently switch to another install')

    def test_nvm_numeric_version_order(self):
        self.executable('.nvm/versions/node/v9.0.0/bin/claude')
        latest = self.executable('.nvm/versions/node/v22.0.0/bin/claude')
        with patch.object(Path, 'home', return_value=self.home), patch.dict(os.environ, {'PATH': '/usr/bin:/bin', 'TERMINAL_PILOT_STORE': str(self.store)}):
            self.assertEqual(runtime_paths.find_tool('claude'), latest)

    @unittest.skipUnless(os.name == 'nt', 'Windows installation discovery')
    def test_windows_tailscale_installation_preserves_tool_override_priority(self):
        executable = Path(self.executable('Program Files/Tailscale/tailscale.exe'))
        with patch.dict(os.environ, {'ProgramFiles': str(self.home / 'Program Files'),
                                     'TERMINAL_PILOT_STORE': str(self.store)}), \
             patch.object(runtime_paths.shutil, 'which', return_value=None):
            self.assertEqual(runtime_paths.find_tool('tailscale'), str(executable))
            (self.store / 'tools.json').write_text(json.dumps({'tailscale': str(self.home / 'missing.exe')}))
            self.assertIsNone(runtime_paths.find_tool('tailscale'))

    def test_malformed_optional_settings_do_not_break_other_tools(self):
        codex = self.executable('.local/bin/codex')
        (self.store / 'tools.json').write_text('not-json')
        with patch.object(Path, 'home', return_value=self.home), patch.dict(os.environ, {'PATH': '/usr/bin:/bin', 'TERMINAL_PILOT_STORE': str(self.store)}):
            self.assertEqual(runtime_paths.find_tool('codex'), codex)

    def test_keychain_reuses_service_and_never_echoes_errors(self):
        with patch.object(voice_keychain.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'synthetic-key\n', b'')) as run:
            self.assertEqual(voice_keychain.read_key(), 'synthetic-key')
            self.assertEqual(run.call_args.args[0], ['/usr/bin/security', 'find-generic-password', '-s', 'openai-platform-api', '-w'])
        with patch.object(voice_keychain.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, b'synthetic-secret', b'private diagnostic')):
            with self.assertRaises(ValueError) as caught: voice_keychain.read_key()
            self.assertNotIn('synthetic-secret', str(caught.exception))
            self.assertNotIn('private diagnostic', str(caught.exception))

    def test_worker_uses_only_memory_credential_without_external_runner(self):
        with patch.object(voice_service, 'read_key', return_value='synthetic-key'), \
             patch.object(voice_service.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{"available":true}', b'')) as run:
            self.assertEqual(voice_service.run_worker({'operation': 'status'}), {'available': True})
            command = run.call_args.args[0]
            self.assertNotIn('synthetic-key', ' '.join(command))
            self.assertNotIn('keychain-run', ' '.join(command))
            self.assertEqual(run.call_args.kwargs['env']['OPENAI_API_KEY'], 'synthetic-key')
            self.assertEqual(json.loads(run.call_args.kwargs['input']), {'operation': 'status'})

    def test_cached_artifacts_are_verified_before_use(self):
        p = self.home / 'artifact'; p.write_bytes(b'official synthetic bytes')
        item = {'filename': p.name, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
        with patch.object(bundle_runtime.urllib.request, 'urlopen') as request:
            self.assertEqual(bundle_runtime.download(item, self.home), p)
            request.assert_not_called()
        p.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'Checksum mismatch'): bundle_runtime.download(item, self.home)

    def test_export_boundary_excludes_personal_state_and_builds(self):
        for name in ['README.md', 'Sources/App.swift', 'tests/test_example.py', '.github/workflows/ci.yml',
                     '.local/private.md', '.build/secret', 'dist/app', 'work/real-conversation.html',
                     'scripts/.env', 'scripts/runtime.json', 'web/__pycache__/state.pyc']:
            path = self.home / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('synthetic')
        exported = {p.relative_to(self.home).as_posix() for p in public_source.files(self.home)}
        self.assertEqual(exported, {'README.md', 'Sources/App.swift', 'tests/test_example.py', '.github/workflows/ci.yml'})

    def test_public_scan_reports_locations_without_secret_values(self):
        spec = importlib.util.spec_from_file_location('public_audit', ROOT / 'scripts/check-public.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for name in ['LICENSE', 'README.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md']:
            (self.home / name).write_text('synthetic')
        secret = 'sk-' + 'x' * 40
        (self.home / 'README.md').write_text(secret)
        _, failures = module.audit(self.home)
        self.assertEqual(failures, [('README.md', 1, 'provider credential')])
        self.assertNotIn(secret, repr(failures))

    def test_public_scan_covers_unlisted_windows_directories(self):
        spec = importlib.util.spec_from_file_location('public_audit', ROOT / 'scripts/check-public.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for name in ['LICENSE', 'README.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md']:
            (self.home / name).write_text('synthetic')
        path = self.home / 'new-windows-source/client.py'
        path.parent.mkdir()
        secret = 'ghp_' + 'x' * 40
        path.write_text(secret)
        candidates, failures = module.audit(self.home)
        self.assertIn(path, candidates)
        self.assertTrue(any(reason == 'provider credential' for _, _, reason in failures))
        self.assertTrue(any('outside public source boundary' in reason for _, _, reason in failures))
        self.assertNotIn(secret, repr(failures))

    def test_staged_scan_reads_index_instead_of_clean_working_copy(self):
        spec = importlib.util.spec_from_file_location('public_audit', ROOT / 'scripts/check-public.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for name in ['LICENSE', 'README.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md']:
            (self.home / name).write_text('synthetic')
        path = self.home / 'scripts/client.py'
        path.parent.mkdir()
        path.write_text('safe working copy')
        secret = ('sk-' + 'x' * 40).encode()
        with patch.object(module.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'scripts/client.py\0', b'')), \
             patch.object(module.subprocess, 'check_output', return_value=secret):
            _, failures = module.audit(self.home, staged=True)
        self.assertEqual([reason for _, _, reason in failures], ['provider credential'])
        self.assertNotIn(secret.decode(), repr(failures))


if __name__ == '__main__': unittest.main()
