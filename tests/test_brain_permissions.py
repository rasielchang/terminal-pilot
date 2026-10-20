"""Scoped development permissions keep sandbox and user approvals enabled."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_permissions import load_permissions, thread_permissions, turn_permissions
from brain_stdio import BackgroundBrain
from test_brain_background import Proxy


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def configure(self, value):
        (self.root / 'development-permissions.json').write_text(json.dumps(value), encoding='utf-8')

    def test_default_remains_readonly(self):
        self.assertEqual(thread_permissions(load_permissions(self.root))['sandbox'], 'read-only')
        self.assertEqual(turn_permissions(load_permissions(self.root)), {})

    def test_profile_explicit_roots_and_approvals(self):
        self.configure({'sandbox':'workspace-write','writableRoots':[str(self.root)]})
        policy = load_permissions(self.root)
        self.assertEqual(thread_permissions(policy)['approvalPolicy'], 'on-request')
        turn = turn_permissions(policy)
        self.assertFalse(turn['sandboxPolicy']['networkAccess'])
        self.assertEqual(turn['sandboxPolicy']['writableRoots'], [str(self.root.resolve())])
        self.assertEqual(turn['approvalsReviewer'], 'user')

    def test_bad_policy_fails_closed(self):
        for value in [
            {'sandbox':'danger-full-access','writableRoots':[str(self.root)]},
            {'sandbox':'workspace-write','writableRoots':[]},
            {'sandbox':'workspace-write','writableRoots':[self.root.anchor]},
            {'sandbox':'workspace-write','writableRoots':['relative']},
            {'sandbox':'workspace-write','writableRoots':[str(self.root)],'approvalPolicy':'never'}
        ]:
            self.configure(value)
            with self.assertRaises(ValueError):
                load_permissions(self.root)

    def test_exact_resume_and_turn_get_same_policy(self):
        self.configure({'sandbox':'workspace-write','writableRoots':[str(self.root)]})
        proxy = Proxy()
        backend = BackgroundBrain(self.root, proxy, self.root, 'Test')
        backend.open()
        self.assertEqual(proxy.calls[0][1]['sandbox'], 'workspace-write')
        backend.rpc_call(backend.current, 'turn/start', {'threadId':'exact-thread', 'input':[]})
        self.assertEqual(proxy.calls[-1][1]['sandboxPolicy']['type'], 'workspaceWrite')
        restarted = BackgroundBrain(self.root, Proxy(), self.root, 'Test')
        restarted.open()
        self.assertEqual(restarted.proxy.calls[0][0], 'thread/resume')
        self.assertEqual(restarted.proxy.calls[0][1]['threadId'], 'exact-thread')


if __name__ == '__main__':
    unittest.main()
