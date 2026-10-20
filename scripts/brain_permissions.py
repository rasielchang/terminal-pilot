"""Local administrator-owned policy; never configured by chat or browser payload."""
import json
from pathlib import Path


def load_permissions(store):
    path = Path(store) / 'development-permissions.json'
    if not path.exists():
        return {'sandbox': 'read-only', 'roots': []}
    value = json.loads(path.read_text(encoding='utf-8'))
    if set(value) != {'sandbox', 'writableRoots'} or value['sandbox'] != 'workspace-write':
        raise ValueError('Invalid development permissions; refusing to start')
    roots = value['writableRoots']
    if not isinstance(roots, list) or not 1 <= len(roots) <= 8:
        raise ValueError('Explicit development roots required')
    resolved = []
    for root in roots:
        if not isinstance(root, str) or not Path(root).is_absolute() or not Path(root).is_dir():
            raise ValueError('Development root must be an existing absolute directory')
        p = Path(root).resolve()
        if p == Path(p.anchor):
            raise ValueError('Whole-drive development access is not permitted')
        if str(p) not in resolved:
            resolved.append(str(p))
    return {'sandbox': 'workspace-write', 'roots': resolved}


def thread_permissions(policy):
    result = {'approvalPolicy': 'on-request', 'approvalsReviewer': 'user',
              'sandbox': policy['sandbox']}
    if policy['roots']:
        result['config'] = {'sandbox_workspace_write.writable_roots': policy['roots'],
                            'sandbox_workspace_write.network_access': False}
    return result


def turn_permissions(policy):
    if not policy['roots']:
        return {}
    return {'approvalPolicy': 'on-request', 'approvalsReviewer': 'user',
            'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': policy['roots'],
                              'networkAccess': False, 'excludeTmpdirEnvVar': False,
                              'excludeSlashTmp': False}}
