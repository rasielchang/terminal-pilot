"""Fixed closeout operations; no shell, arbitrary URL, approval or task creation."""
import json
import os
from urllib.request import Request, build_opener, ProxyHandler
from brain_service import ACTIVE, running, waiting


class ContinuationActions:
    def __init__(self, owner, asana=None):
        self.o = owner
        self.asana = asana or asana_request

    def __call__(self, grant, action, check):
        scope = action['scope']
        if scope['kind'] == 'asana_complete_existing':
            return complete_asana(scope, self.asana, check)
        if scope['kind'] != 'close_work': raise ValueError('不支援的接續動作。')
        _, session = check()
        if running(session) or waiting(session) or any(j['session'] == session['id'] and j['state'] in ACTIVE for j in self.o._rows('jobs')):
            raise ValueError('工作仍在執行、等待正式核准或尚有交辦；保留視窗。')
        if not self.o.close_work: raise ValueError('此平台沒有精確關窗入口。')
        preview = self.o.close_work({'session': grant['session'], 'binding': grant['binding']}, preview=True)
        if preview.get('bindings') != [grant['binding']]: raise ValueError('視窗包含其他工作；沒有關閉。')
        _, session = check()
        if running(session) or waiting(session): raise ValueError('關窗前工作狀態已改變；沒有關閉。')
        result = self.o.close_work({'requestID': action['id'], 'session': grant['session'],
            'binding': grant['binding'], 'bindings': [grant['binding']]})
        return {'verified': result.get('closed') is True, 'native': result,
                'session': grant['session'], 'thread': grant['workerThread'], 'generation': grant['generation']}


def asana_request(method, path, data=None):
    # No credential guessing, copying or worker-selected credential/endpoint.
    # Existing host credential loaders may provide ASANA_TOKEN in memory.
    token = os.environ.get('ASANA_TOKEN')
    if not token: raise ValueError('此服務未接上既有 Asana 憑證載入器；沒有寫入。')
    request = Request('https://app.asana.com/api/1.0' + path, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        data=None if data is None else json.dumps({'data': data}).encode())
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            return json.load(response)['data']
    except OSError:
        raise OSError('Asana 回應失敗或送達不確定；沒有自動重送。') from None


def complete_asana(scope, api, check):
    task, project, section = (scope[k] for k in ('task', 'project', 'section'))
    fields = 'gid,name,notes,completed,parent.gid,due_on,due_at,assignee.gid,tags.gid,memberships.project.gid,memberships.section.gid'
    path = '/tasks/' + task + '?opt_fields=' + fields
    before = api('GET', path)
    destination = api('GET', '/sections/' + section + '?opt_fields=gid,name,project.gid')
    if before.get('parent') or not any(m['project']['gid'] == project for m in before['memberships']):
        raise ValueError('Asana 不是此既有專案的頂層任務；沒有寫入。')
    if destination.get('project', {}).get('gid') != project or destination.get('name', '').replace(' ', '') != scope['month'] + '完成':
        raise ValueError('Asana 既有月份 section 不符合授權；不建立新 section。')
    def in_section(value):
        return any(m['project']['gid'] == project and (m.get('section') or {}).get('gid') == section for m in value['memberships'])
    if not in_section(before):
        check(); api('POST', '/sections/' + section + '/addTask', {'task': task})
    if not before.get('completed'):
        check(); api('PUT', '/tasks/' + task, {'completed': True})
    after = api('GET', path)
    preserved = all(after.get(k) == before.get(k) for k in ('name', 'notes', 'parent', 'due_on', 'due_at', 'assignee', 'tags'))
    preserved &= {m['project']['gid'] for m in before['memberships']} == {m['project']['gid'] for m in after['memberships']}
    preserved &= all(m in after['memberships'] for m in before['memberships'] if m['project']['gid'] != project)
    if not after.get('completed') or not in_section(after) or not preserved:
        raise ValueError('Asana 寫入後驗證未通過；保留收據，不重試。')
    return {'verified': True, 'task': task, 'project': project, 'section': section, 'month': scope['month'],
            'before': before, 'after': after}
