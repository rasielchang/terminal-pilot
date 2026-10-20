"""Explicit, request-scoped Codex decisions. Payloads live in memory only.

The browser receives an opaque handle, never a writable JSON-RPC method or id.
No decision is inferred from reading, focusing, timing out, or reconnecting.
"""
from __future__ import annotations

from collections import OrderedDict, deque
import copy
import hashlib
import json
import math
import threading
import uuid

from codex_live import StatusProxy, StatusRequestError

APPROVAL_METHODS = {
    'item/commandExecution/requestApproval', 'item/fileChange/requestApproval',
    'item/permissions/requestApproval', 'mcpServer/elicitation/request',
    'item/tool/requestUserInput', 'tool/requestUserInput',
}


def form_fields(schema):
    """Only offer forms whose constraints we can render and validate completely."""
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise ValueError('這種表單尚需在電腦處理。')
    if set(schema) - {'$schema', 'type', 'properties', 'required', 'title', 'description', 'additionalProperties'}:
        raise ValueError('這種表單尚需在電腦處理。')
    props = schema.get('properties', {})
    required = schema.get('required') or []
    if not isinstance(props, dict) or len(props) > 32 or not isinstance(required, list) or set(required) - props.keys():
        raise ValueError('授權表單格式不正確。')
    fields = []
    for name, spec in props.items():
        if not isinstance(spec, dict) or set(spec) - {
            'type', 'title', 'description', 'default', 'enum', 'enumNames', 'oneOf',
            'minimum', 'maximum', 'minLength', 'maxLength', 'format', 'items', 'minItems', 'maxItems'}:
            raise ValueError('這種表單尚需在電腦處理。')
        kind = spec.get('type')
        if kind not in {'string', 'boolean', 'number', 'integer', 'array'}:
            raise ValueError('這種表單尚需在電腦處理。')
        field = {**spec, 'name': name, 'required': name in required}
        source = spec.get('items', {}) if kind == 'array' else spec
        if kind == 'array' and (not isinstance(source, dict) or set(source) - {'type', 'enum', 'anyOf'}):
            raise ValueError('這種多選表單尚需在電腦處理。')
        choices = source.get('oneOf', source.get('anyOf'))
        if choices is not None:
            if not isinstance(choices, list) or not choices or any(
                not isinstance(c, dict) or set(c) - {'const', 'title'} or not isinstance(c.get('const'), str) for c in choices):
                raise ValueError('這種選項尚需在電腦處理。')
            field['options'] = [{'value': c['const'], 'label': c.get('title') or c['const']} for c in choices]
        elif 'enum' in source:
            values, labels = source['enum'], source.get('enumNames') or []
            if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
                raise ValueError('這種選項尚需在電腦處理。')
            field['options'] = [{'value': v, 'label': labels[i] if i < len(labels) else v} for i, v in enumerate(values)]
        if kind == 'array' and not field.get('options'):
            raise ValueError('這種多選表單尚需在電腦處理。')
        if spec.get('format') not in {None, 'email', 'uri', 'date', 'date-time'}:
            raise ValueError('這種輸入格式尚需在電腦處理。')
        fields.append(field)
    return fields


def validate_form(fields, content):
    if not isinstance(content, dict) or set(content) - {f['name'] for f in fields}:
        raise ValueError('表單欄位不正確。')
    for field in fields:
        name, kind = field['name'], field['type']
        if name not in content:
            if field['required']: raise ValueError('請完成所有必填欄位。')
            continue
        value = content[name]
        valid = (isinstance(value, str) if kind == 'string' else type(value) is bool if kind == 'boolean'
                 else type(value) is int if kind == 'integer' else type(value) in {int, float} and math.isfinite(value)
                 if kind == 'number' else isinstance(value, list) and all(isinstance(v, str) for v in value))
        if not valid: raise ValueError('表單資料型別不正確。')
        for key, measure, lower in [('minLength', len(value) if kind == 'string' else None, True),
                                    ('maxLength', len(value) if kind == 'string' else None, False),
                                    ('minItems', len(value) if kind == 'array' else None, True),
                                    ('maxItems', len(value) if kind == 'array' else None, False),
                                    ('minimum', value if kind in {'number', 'integer'} else None, True),
                                    ('maximum', value if kind in {'number', 'integer'} else None, False)]:
            bound = field.get(key)
            if bound is not None and measure is not None and (measure < bound if lower else measure > bound):
                raise ValueError('輸入超出表單允許範圍。')
        if field.get('options'):
            allowed = {o['value'] for o in field['options']}
            values = value if kind == 'array' else [value]
            if any(v not in allowed for v in values) or len(values) != len(set(values)):
                raise ValueError('請選擇原要求提供的選項。')
        if kind == 'string' and field.get('format'):
            from datetime import date, datetime
            from urllib.parse import urlsplit
            try:
                fmt = field['format']
                if fmt == 'email' and ('@' not in value or any(c.isspace() for c in value)): raise ValueError()
                if fmt == 'uri' and not urlsplit(value).scheme: raise ValueError()
                if fmt == 'date': date.fromisoformat(value)
                if fmt == 'date-time': datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError: raise ValueError('輸入格式不符合授權表單。') from None
    return content


def presentation(method, params, item=None):
    view = {'kind': 'approval', 'title': '需要你核准', 'detail': params.get('reason') or '',
            'actions': ['accept', 'decline', 'cancel'], 'fields': [], 'context': []}
    for key, label in [('command', '指令'), ('cwd', '工作目錄'), ('networkApprovalContext', '網路存取'),
                       ('additionalPermissions', '額外權限'), ('permissions', '請求權限'), ('grantRoot', '寫入範圍')]:
        if params.get(key) is not None:
            value = params[key]
            view['context'].append({'label': label, 'text': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)})
    if method == 'item/commandExecution/requestApproval':
        view['title'] = '允許這次網路存取？' if params.get('networkApprovalContext') else '允許這次指令？'
        if params.get('availableDecisions') is not None:
            view['actions'] = [a for a in view['actions'] if a in params['availableDecisions']]
    elif method == 'item/fileChange/requestApproval':
        view['title'] = '允許這次檔案修改？'
        if item and item.get('changes'):
            view['context'].append({'label': '檔案修改', 'text': json.dumps(item['changes'], ensure_ascii=False, indent=2)})
        else:
            view['actions'].remove('accept')
            view['unsupported'] = '尚未取得這筆修改內容，請在電腦檢視後核准；手機仍可拒絕。'
    elif method == 'item/permissions/requestApproval':
        view.update(title='授予本輪所列權限？', actions=['accept', 'decline'])
    elif method == 'mcpServer/elicitation/request':
        view.update(title='工具請求你的確認', detail=params.get('message') or '', server=params.get('serverName') or '')
        mode = params.get('mode')
        if mode == 'url':
            from urllib.parse import urlsplit
            url = params.get('url', '')
            if urlsplit(url).scheme in {'https', 'http'}:
                view['url'] = url
            view['unsupported'] = '請開啟原工具的確認頁面，完成後按「已完成確認」。'
            view['kind'] = 'url'
        elif mode in {'form', 'openai/form', 'openaiForm'}:
            try: view['fields'] = form_fields(params.get('requestedSchema'))
            except (ValueError, TypeError) as error:
                view['actions'].remove('accept'); view['unsupported'] = str(error)
        else:
            view['actions'].remove('accept'); view['unsupported'] = '此工具的確認格式需在電腦處理。'
    elif method in {'item/tool/requestUserInput', 'tool/requestUserInput'}:
        questions = params.get('questions', [])
        if not isinstance(questions, list) or not 1 <= len(questions) <= 32:
            raise ValueError('不支援的問題格式。')
        view.update(kind='question', title='請回答這個工作', actions=['answer'], questions=questions)
    else:
        raise ValueError('不支援的授權請求。')
    return view


def decision_result(record, action, content):
    view, method, params = record['view'], record['method'], record['params']
    if action not in view['actions']:
        raise ValueError('這筆要求不接受這個選項。')
    if method == 'mcpServer/elicitation/request':
        if action == 'accept':
            content = validate_form(view['fields'], content) if view['kind'] != 'url' else None
        else: content = None
        return {'action': action, 'content': content}
    if method == 'item/permissions/requestApproval':
        # Copy only the original request. No browser-supplied permission expansion.
        return {'permissions': copy.deepcopy(params['permissions']) if action == 'accept' else {}, 'scope': 'turn'}
    if view['kind'] == 'question':
        questions = view['questions']
        if not isinstance(content, dict) or set(content) != {q['id'] for q in questions}:
            raise ValueError('請回答這筆要求的所有問題。')
        result = {}
        for question in questions:
            answer = content[question['id']]
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
                raise ValueError('請輸入答案或選擇選項。')
            options = question.get('options') or []
            if options and not question.get('isOther') and answer not in [o['label'] for o in options]:
                raise ValueError('請選擇原要求提供的選項。')
            result[question['id']] = {'answers': [answer]}
        return {'answers': result}
    return {'decision': action}


class ApprovalProxy(StatusProxy):
    methods = StatusProxy.methods | {'thread/loaded/list', 'thread/resume', 'thread/items/list',
                                     'thread/turns/list', 'turn/start', 'hooks/list'}

    def __init__(self, binary, home, socket):
        self.approval_lock = threading.RLock()
        self.requests, self.items, self.receipts = OrderedDict(), OrderedDict(), OrderedDict()
        self.subscribed = set()
        self.lifecycle = deque(maxlen=256)
        super().__init__(binary, home, socket=socket, capabilities={
            'experimentalApi': True, 'mcpServerOpenaiFormElicitation': True})

    def loaded(self):
        result = self.call('thread/loaded/list', {})
        return result.get('data', [])

    def discover(self):
        roots = []
        for identity in self.loaded():
            thread = self.call('thread/read', {'threadId': identity, 'includeTurns': False}).get('thread', {})
            source = thread.get('source')
            if thread.get('id') == identity and isinstance(source, str) and source in {'cli', 'appServer', 'vscode'}:
                roots.append(identity)
        return roots[0] if len(roots) == 1 else None

    def status(self, thread_id):
        if thread_id not in self.subscribed:
            if thread_id not in self.loaded():
                return 'notLoaded'  # Never load a historical or unrelated thread as a side effect.
            with self.approval_lock: self.subscribed.add(thread_id)
            try:
                response = self.call('thread/resume', {'threadId': thread_id, 'excludeTurns': True})
                if response.get('thread', {}).get('id') != thread_id: raise ValueError('Codex thread mismatch')
            except Exception as error:
                with self.approval_lock: self.subscribed.discard(thread_id)
                # A brand-new TUI thread has no rollout until its first input.
                # Read its state now; join/replay on the next poll after persistence.
                if not isinstance(error, StatusRequestError) or not str(error.detail or '').startswith('no rollout found for thread id '):
                    raise
        self.hydrate_file_reviews(thread_id)
        return super().status(thread_id)

    def hydrate_file_reviews(self, thread):
        with self.approval_lock:
            missing = [(key, r['params']) for key, r in self.requests.items()
                       if r['thread'] == thread and r['state'] == 'pending'
                       and r['method'] == 'item/fileChange/requestApproval' and r['view'].get('unsupported')]
        for key, params in missing:
            if not params.get('turnId'): continue
            try:
                page = self.call('thread/items/list', {'threadId':thread, 'turnId':params['turnId'],
                                                     'sortDirection':'desc', 'limit':50})
            except StatusRequestError: continue
            for entry in page.get('data', []):
                item = entry.get('item') or {}
                if item.get('id') == params.get('itemId') and item.get('type') == 'fileChange':
                    view = presentation('item/fileChange/requestApproval', params, item)
                    with self.approval_lock:
                        if self.requests[key]['state'] == 'pending': self.requests[key]['view'] = view
                    break

    def event(self, value):
        method, params = value.get('method'), value.get('params') or {}
        if not isinstance(params, dict): return
        thread = params.get('threadId')
        with self.approval_lock:
            if thread not in self.subscribed: return
            if method == 'serverRequest/resolved':
                for record in self.requests.values():
                    if record['thread'] == thread and record['rpcID'] == params.get('requestId'):
                        record['state'] = 'resolved'
                        record['resolved'].set()
                return
            if method in {'turn/started', 'turn/completed'}:
                turn = params.get('turn') or {}
                self.lifecycle.append((thread, method, turn.get('id'), turn.get('status')))
                if method == 'turn/completed':
                    for record in self.requests.values():
                        if record['thread'] == thread and record['params'].get('turnId') == turn.get('id'):
                            record['state'] = 'resolved'; record['resolved'].set()
                return
            if method == 'item/started':
                item = params.get('item') or {}
                if item.get('type') == 'fileChange':
                    self.items[(thread, item.get('id'))] = item
                    while len(self.items) > 32: self.items.pop(next(iter(self.items)))
                return
            if method not in APPROVAL_METHODS or 'id' not in value: return
            # A replay with the same RPC id is the same request, not a second consent.
            if any(r['rpcID'] == value['id'] and r['thread'] == thread for r in self.requests.values()): return
            try: view = presentation(method, params, self.items.get((thread, params.get('itemId'))))
            except (ValueError, TypeError, KeyError): return
            handle = str(uuid.uuid4())
            self.requests[handle] = {'rpcID': value['id'], 'thread': thread, 'method': method,
                'params': params, 'view': view, 'state': 'pending', 'resolved': threading.Event()}
            # Evict only resolved entries; never turn a pending decision into a different one.
            for key in list(self.requests):
                if len(self.requests) <= 128: break
                if self.requests[key]['state'] == 'resolved': self.requests.pop(key)

    def approvals(self, thread):
        with self.approval_lock:
            if self.closed: return []
            return [{**copy.deepcopy(r['view']), 'id': key, 'state': r['state']}
                    for key, r in self.requests.items() if r['thread'] == thread and r['state'] != 'resolved']

    def waiting_keys(self, thread):
        with self.approval_lock:
            return [hashlib.sha256(json.dumps([r['thread'], r['rpcID'], r['params'].get('turnId')]).encode()).hexdigest()
                    for r in self.requests.values() if r['thread'] == thread and r['state'] != 'resolved']

    def decide(self, thread, handle, request_id, action, content):
        fingerprint = json.dumps([thread, handle, action, content], sort_keys=True, ensure_ascii=False)
        with self.approval_lock:
            previous = self.receipts.get(request_id)
            if previous:
                if previous[0] != fingerprint: raise ValueError('此送出識別碼已用於另一筆決定。')
                return previous[1]
            record = self.requests.get(handle)
            if self.closed or not record or record['thread'] != thread or record['state'] != 'pending':
                raise ValueError('這筆要求已處理或連線已更換，請查看最新狀態。')
            response = decision_result(record, action, content)
            receipt = {'submitted': False, 'resolved': False, 'uncertain': True}
            self.receipts[request_id] = (fingerprint, receipt)
            while len(self.receipts) > 256: self.receipts.popitem(last=False)
            record['state'] = 'submitting'
            try:
                self._write({'id': record['rpcID'], 'result': response})
                receipt.update(submitted=True, uncertain=False)
            except (OSError, ValueError):
                # Delivery may have happened. Never resend or expose an active button.
                record['state'] = 'uncertain'
                return receipt.copy()
        record['resolved'].wait(2)
        with self.approval_lock:
            receipt['resolved'] = record['state'] == 'resolved'
            return receipt.copy()

    def drain_lifecycle(self, thread):
        with self.approval_lock:
            events = [e for e in self.lifecycle if e[0] == thread]
            self.lifecycle = deque((e for e in self.lifecycle if e[0] != thread), maxlen=256)
            return events
