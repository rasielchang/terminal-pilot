"""Fixed closeout operations; no shell, arbitrary URL, approval or task creation."""
import json
import os
import re
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler
from brain_service import ACTIVE, running, waiting


TASK_FIELDS = 'gid,resource_subtype,name,notes,completed,parent.gid,due_on,due_at,assignee.gid,tags.gid,memberships.project.gid,memberships.section.gid'


class ContinuationActions:
    def __init__(self, owner, asana=None):
        self.o = owner
        self.asana = asana or asana_request

    def preview_close(self, grant):
        if not self.o.close_work: raise ValueError('此平台沒有精確關窗入口。')
        preview = self.o.close_work({'session': grant['session'], 'binding': grant['binding']}, preview=True)
        if preview.get('bindings') != [grant['binding']]:
            raise ValueError('原視窗包含其他工作或分頁；請先處理，保留視窗。')

    def prepare_report_closeout(self, grant, mapping, month):
        self.preview_close(grant)
        if not self.o.closure_status:
            raise ValueError('此平台無法回讀原視窗世代；保留視窗。')
        task, project = mapping['task'], mapping['project']
        before = self.asana('GET', task_path(task))
        if not project and len(before.get('memberships', [])) == 1:
            project = before['memberships'][0]['project']['gid']
        if not isinstance(project, str) or not re.fullmatch(r'[1-9][0-9]{0,29}', project):
            raise ValueError('Asana 任務沒有唯一的既有專案；請核對對應。')
        verify_task(before, task, project, exact_project=True)
        scope = {'kind': 'asana_complete_existing', 'task': task, 'project': project,
                 'section': self.month_destination(project, month), 'month': month}
        verify_section(scope, self.asana)
        return scope

    def month_destination(self, project, month):
        sections = set()
        path = '/projects/' + project + '/sections?opt_fields=gid,name&limit=100'
        for _ in range(8):
            page = self.asana('GET', path, paged=True)
            for section in page['data']:
                if month_section(section, month) and re.fullmatch(r'[0-9]{1,30}', section.get('gid', '')):
                    sections.add(section['gid'])
            offset = (page.get('next_page') or {}).get('offset')
            if not offset: break
            path = '/projects/' + project + '/sections?opt_fields=gid,name&limit=100&offset=' + quote(offset, safe='')
        else:
            raise ValueError('Asana 專案區段未能完整核對；請確認月份區段。')
        if len(sections) != 1:
            raise ValueError('請核對 Asana 專案唯一的既有 ' + month + ' 完成區段。')
        return sections.pop()

    def __call__(self, grant, action, check):
        scope = action['scope']
        report = grant.get('reportPlan')
        if scope['kind'] == 'asana_complete_existing':
            if not report:
                return complete_asana(scope, self.asana, check)

            def checked():
                record, _ = check()
                if self.o.today_cards.closeout_asana(record, grant['session']) != report['mapping']:
                    raise ValueError('Asana 工作卡對應已變更；沒有寫入。')
                self.preview_close(grant)
                return check()

            checked()
            if self.month_destination(scope['project'], scope['month']) != scope['section']:
                raise ValueError('Asana 月份區段對應已變更；請核對原任務。')
            return complete_asana(scope, self.asana, checked, action.get('progress', {}),
                                  lambda progress: self.o.continuations.checkpoint(grant['id'], action['id'], progress))
        if scope['kind'] != 'close_work': raise ValueError('不支援的接續動作。')
        if report and action.get('progress', {}).get('submitted'):
            proof = self.o.closure_status(grant)
            if (proof.get('reliable') and proof.get('absent') and proof.get('generation') == grant['generation']):
                return self.close_receipt(grant, proof, action['progress'].get('native'))
            raise OSError('原關窗已送出，等待精確世代的關閉證據；沒有重送。')
        if report:
            archive = next(a['scope'] for a in grant['actions'] if a['scope']['kind'] == 'asana_complete_existing')
            check()
            verify_section(archive, self.asana)
            archived = self.asana('GET', task_path(archive['task']))
            verify_task(archived, archive['task'], archive['project'], exact_project=True)
            if archived.get('completed') is not True or not in_asana_section(archived, archive):
                raise ValueError('Asana 歸檔狀態已變更；請核對原任務，保留視窗。')
        _, session = check()
        if running(session) or waiting(session) or any(j['session'] == session['id'] and j['state'] in ACTIVE for j in self.o._rows('jobs')):
            raise ValueError('工作仍在執行、等待正式核准或尚有交辦；保留視窗。')
        self.preview_close(grant)
        _, session = check()
        if running(session) or waiting(session): raise ValueError('關窗前工作狀態已改變；沒有關閉。')
        data = {'requestID': action['id'], 'session': grant['session'],
                'binding': grant['binding'], 'bindings': [grant['binding']]}
        if report:
            self.o.continuations.checkpoint(grant['id'], action['id'], {'submitted': True})
            data.update(generation=grant['generation'], thread=grant['workerThread'])
        result = self.o.close_work(data)
        if report:
            if result.get('closed') is not True:
                raise OSError('原關窗未取得可靠完成證據；沒有重送。')
            self.o.continuations.checkpoint(grant['id'], action['id'], {'submitted': True, 'native': result})
            proof = self.o.closure_status(grant)
            if not (proof.get('reliable') and proof.get('absent') and proof.get('generation') == grant['generation']):
                raise OSError('關窗後仍待精確世代回讀；沒有重送。')
            return self.close_receipt(grant, proof, result)
        return self.close_receipt(grant, None, result)

    @staticmethod
    def close_receipt(grant, proof, native):
        return {'verified': bool(proof or native and native.get('closed') is True), 'native': native,
                'closureProof': proof, 'session': grant['session'], 'thread': grant['workerThread'],
                'generation': grant['generation']}


def asana_request(method, path, data=None, *, paged=False):
    # Existing host credential loaders may provide ASANA_TOKEN in memory.
    token = os.environ.get('ASANA_TOKEN')
    if not token: raise ValueError('此服務未接上既有 Asana 憑證載入器；請先設定已授權的憑證。')
    request = Request('https://app.asana.com/api/1.0' + path, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        data=None if data is None else json.dumps({'data': data}).encode())
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
            body = json.load(response)
            return body if paged else body['data']
    except OSError:
        raise OSError('Asana 回應失敗；請核對原任務讀回與接續收據。') from None


def task_path(task):
    return '/tasks/' + task + '?opt_fields=' + TASK_FIELDS


def verify_task(value, task, project, *, exact_project=False):
    if (value.get('gid') != task or value.get('parent') or
            sum(m['project']['gid'] == project for m in value['memberships']) != 1):
        raise ValueError('Asana 回讀不是此既有專案的精確頂層任務；沒有寫入。')
    if exact_project and (len(value['memberships']) != 1 or value.get('resource_subtype') != 'default_task'):
        raise ValueError('Asana 任務橫跨專案或不是一般任務；請確認影響範圍。')


def month_section(value, month):
    return re.sub(r'\s+', '', value.get('name', '')) == month + '完成'


def verify_section(scope, api):
    section = scope['section']
    destination = api('GET', '/sections/' + section + '?opt_fields=gid,name,project.gid')
    if (destination.get('gid') != section or destination.get('project', {}).get('gid') != scope['project'] or
            not month_section(destination, scope['month'])):
        raise ValueError('Asana 既有月份區段與提案不一致；沒有寫入。')


def in_asana_section(value, scope):
    return any(m['project']['gid'] == scope['project'] and (m.get('section') or {}).get('gid') == scope['section']
               for m in value['memberships'])


def complete_asana(scope, api, check, progress=None, checkpoint=None):
    task, project, section = (scope[k] for k in ('task', 'project', 'section'))
    path = task_path(task)
    check()
    current = api('GET', path)
    verify_task(current, task, project, exact_project=checkpoint is not None)
    verify_section(scope, api)
    progress = dict(progress or {})
    before = progress.setdefault('before', current)

    def preserved(value):
        return (all(value.get(k) == before.get(k) for k in ('name', 'notes', 'parent', 'due_on', 'due_at', 'assignee', 'tags')) and
                {m['project']['gid'] for m in before['memberships']} == {m['project']['gid'] for m in value['memberships']} and
                all(m in value['memberships'] for m in before['memberships'] if m['project']['gid'] != project))

    def in_section(value):
        return in_asana_section(value, scope)

    for name, reached, method, route, body in (
        ('move', in_section, 'POST', '/sections/' + section + '/addTask', {'task': task}),
        ('complete', lambda v: v.get('completed') is True, 'PUT', '/tasks/' + task, {'completed': True}),
    ):
        if not preserved(current):
            raise ValueError('Asana 任務其他欄位已變更；請核對原任務。')
        if not reached(current):
            if name in progress:
                raise OSError('Asana ' + name + ' 已送出或曾讀回，現況仍待確認；沒有重送。')
            check()
            verify_section(scope, api)
            check()
            if checkpoint:
                progress[name] = {'state': 'attempting'}
                checkpoint(progress)
            api(method, route, body)
            current = api('GET', path)
            verify_task(current, task, project, exact_project=checkpoint is not None)
        if not reached(current) or not preserved(current):
            raise OSError('Asana 動作尚未取得一致讀回；請核對原任務。')
        if checkpoint:
            progress[name] = {'state': 'done', 'observed': True}
            checkpoint(progress)
    verify_section(scope, api)
    if not current.get('completed') or not in_section(current):
        raise ValueError('Asana 完成狀態與月份區段尚未同時讀回。')
    return {'verified': True, 'task': task, 'project': project, 'section': section, 'month': scope['month'],
            'before': before, 'after': current}
