import copy
import json
import os
from pathlib import Path
import sys
import threading
import tempfile
import unittest
from unittest.mock import patch
from collections import OrderedDict, deque

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from codex_approvals import ApprovalProxy, presentation, decision_result, form_fields, validate_form
from codex_live import CodexLiveStatus, StatusRequestError
from codex_managed import run_managed, valid_socket


def client():
    proxy = object.__new__(ApprovalProxy)
    proxy.approval_lock = threading.RLock()
    proxy.requests, proxy.items, proxy.receipts = OrderedDict(), {}, OrderedDict()
    proxy.subscribed, proxy.lifecycle, proxy.closed = {'thread'}, deque(maxlen=256), False
    proxy.writes = []
    def write(value):
        proxy.writes.append(value)
        proxy.event({'method':'serverRequest/resolved', 'params':{'threadId':'thread', 'requestId':value['id']}})
    proxy._write = write
    return proxy


def request(proxy, identity=7, **params):
    proxy.event({'id':identity, 'method':'mcpServer/elicitation/request', 'params':{
        'threadId':'thread', 'turnId':'turn', 'serverName':'synthetic', 'mode':'form',
        'message':'Allow this synthetic test?', 'requestedSchema':{'type':'object','properties':{}}, **params}})
    return next(reversed(proxy.requests))


class ApprovalTests(unittest.TestCase):
    def test_explicit_decision_resolves_exact_request_and_retries_are_idempotent(self):
        proxy = client(); handle = request(proxy)
        self.assertEqual(proxy.writes, [])  # Receiving/rendering never approves.
        result = proxy.decide('thread', handle, 'phone-1', 'accept', {})
        self.assertEqual(result, {'submitted':True, 'resolved':True, 'uncertain':False})
        self.assertEqual(proxy.writes, [{'id':7, 'result':{'action':'accept','content':{}}}])
        self.assertEqual(proxy.decide('thread', handle, 'phone-1', 'accept', {}), result)
        self.assertEqual(len(proxy.writes), 1)
        self.assertEqual(proxy.approvals('thread'), [])
        with self.assertRaises(ValueError): proxy.decide('thread', handle, 'phone-1', 'decline', {})
        with self.assertRaises(ValueError): proxy.decide('thread', handle, 'phone-2', 'accept', {})

    def test_desktop_first_or_wrong_thread_cannot_be_approved_from_phone(self):
        proxy = client(); handle = request(proxy)
        with self.assertRaises(ValueError): proxy.decide('other', handle, 'a', 'accept', {})
        proxy.event({'method':'serverRequest/resolved','params':{'threadId':'thread','requestId':7}})
        with self.assertRaises(ValueError): proxy.decide('thread', handle, 'b', 'accept', {})
        self.assertEqual(proxy.writes, [])

    def test_pending_replay_and_separate_request_identity(self):
        proxy = client(); handle = request(proxy)
        self.assertEqual(request(proxy), handle)
        second = request(proxy, identity=8)
        self.assertNotEqual(handle, second)
        proxy.decide('thread', handle, 'a', 'decline', None)
        self.assertEqual([r['id'] for r in proxy.approvals('thread')], [second])
        proxy.decide('thread', second, 'b', 'cancel', None)
        self.assertEqual(proxy.writes[-1], {'id':8,'result':{'action':'cancel','content':None}})

    def test_ambiguous_delivery_never_retries(self):
        proxy = client(); handle = request(proxy)
        calls = []
        def fail(value): calls.append(value); raise OSError('disconnect')
        proxy._write = fail
        result = proxy.decide('thread', handle, 'a', 'accept', {})
        self.assertTrue(result['uncertain'])
        proxy.decide('thread', handle, 'a', 'accept', {})
        with self.assertRaises(ValueError): proxy.decide('thread', handle, 'b', 'accept', {})
        self.assertEqual(len(calls), 1)

    def test_no_cross_thread_requests_or_automatic_tool_answers(self):
        proxy = client()
        proxy.event({'id':7,'method':'mcpServer/elicitation/request','params':{'threadId':'other'}})
        self.assertEqual(proxy.requests, {})
        self.assertEqual(proxy.writes, [])

    def test_unknown_request_is_ignored_and_completed_turn_clears_pending(self):
        proxy = client()
        proxy.event({'id':1,'method':'attestation/generate','params':{'threadId':'thread'}})
        self.assertEqual(proxy.requests, {})
        handle = request(proxy)
        proxy.event({'method':'turn/completed','params':{'threadId':'thread','turn':{'id':'turn','status':'completed'}}})
        self.assertEqual(proxy.approvals('thread'), [])
        with self.assertRaises(ValueError): proxy.decide('thread', handle, 'a', 'accept', {})
        self.assertEqual(proxy.drain_lifecycle('thread'), [('thread','turn/completed','turn','completed')])
        self.assertEqual(proxy.writes, [])

    def test_reconnect_invalidates_old_handles(self):
        first, second = client(), client()
        old = request(first); new = request(second)
        first.closed = True
        self.assertNotEqual(old, new)
        with self.assertRaises(ValueError): first.decide('thread',old,'a','accept',{})
        with self.assertRaises(ValueError): second.decide('thread',old,'a','accept',{})
        self.assertEqual(first.approvals('thread'), [])

    def test_subscribe_only_loaded_thread_without_policy_overrides(self):
        proxy = client(); proxy.subscribed.clear(); calls = []
        def call(method, params):
            calls.append((method, copy.deepcopy(params)))
            if method == 'thread/loaded/list': return {'data':['thread']}
            return {'thread':{'id':'thread','status':{'type':'idle'}}}
        proxy.call = call
        self.assertEqual(proxy.status('thread'), 'idle')
        self.assertIn(('thread/resume', {'threadId':'thread','excludeTurns':True}), calls)
        calls.clear()
        self.assertEqual(proxy.status('old-history'), 'notLoaded')
        self.assertEqual(calls, [('thread/loaded/list', {})])

    def test_new_unpersisted_thread_retries_subscription_after_first_input(self):
        proxy = client(); proxy.subscribed.clear(); persisted = [False]; joins = []
        def call(method, params):
            if method == 'thread/loaded/list': return {'data':['thread']}
            if method == 'thread/resume':
                joins.append(params)
                if not persisted[0]: raise StatusRequestError(-32600, 'no rollout found for thread id thread')
            return {'thread':{'id':'thread','status':{'type':'idle'}}}
        proxy.call = call
        self.assertEqual(proxy.status('thread'), 'idle')
        self.assertNotIn('thread', proxy.subscribed)
        persisted[0] = True
        self.assertEqual(proxy.status('thread'), 'idle')
        self.assertIn('thread', proxy.subscribed)
        self.assertEqual(len(joins), 2)

    def test_waiting_identity_survives_reconnection_and_partial_resolution(self):
        first, second = client(), client()
        a = request(first, identity=7); request(second, identity=7)
        self.assertEqual(first.waiting_keys('thread'), second.waiting_keys('thread'))
        key = first.waiting_keys('thread')[0]
        request(first, identity=8)
        self.assertEqual(len(first.waiting_keys('thread')), 2)
        first.decide('thread', a, 'test', 'decline', None)
        self.assertNotIn(key, first.waiting_keys('thread'))
        self.assertEqual(len(first.waiting_keys('thread')), 1)

    def test_command_options_and_permissions_cannot_be_expanded_by_browser(self):
        params = {'command':'touch /tmp/synthetic', 'availableDecisions':['decline','cancel']}
        method = 'item/commandExecution/requestApproval'
        record = {'method':method, 'params':params, 'view':presentation(method,params)}
        with self.assertRaises(ValueError): decision_result(record,'accept',{})
        self.assertEqual(decision_result(record,'decline',{}), {'decision':'decline'})
        params = {'permissions':{'network':{'enabled':True}}}; method = 'item/permissions/requestApproval'
        record = {'method':method,'params':params,'view':presentation(method,params)}
        self.assertEqual(decision_result(record,'accept',{'fileSystem':{'write':['/']}}),
                         {'permissions':params['permissions'],'scope':'turn'})
        self.assertEqual(decision_result(record,'decline',None), {'permissions':{},'scope':'turn'})

    def test_file_changes_require_actual_review_content_to_accept(self):
        method = 'item/fileChange/requestApproval'
        self.assertNotIn('accept', presentation(method, {})['actions'])
        item = {'changes':[{'path':'/tmp/test','diff':'+test'}]}
        view = presentation(method, {}, item)
        self.assertIn('accept', view['actions'])
        self.assertIn('+test', view['context'][0]['text'])

    def test_form_schema_validation_and_no_silent_unknown_constraints(self):
        fields = form_fields({'type':'object','required':['permission','count','enabled'], 'properties':{
            'permission':{'type':'string','oneOf':[{'const':'once','title':'Allow once'},{'const':'deny','title':'Deny'}]},
            'count':{'type':'integer','minimum':1,'maximum':3}, 'enabled':{'type':'boolean'},
            'tags':{'type':'array','items':{'type':'string','enum':['a','b']},'maxItems':1}}})
        self.assertEqual(validate_form(fields, {'permission':'once','count':2,'enabled':False})['count'],2)
        for wrong in ({'permission':'always','count':2,'enabled':False}, {'permission':'once','count':True,'enabled':False},
                      {'permission':'once','count':5,'enabled':False}, {'permission':'once','count':2},
                      {'permission':'once','count':2,'enabled':True,'tags':['a','b']}):
            with self.assertRaises(ValueError): validate_form(fields, wrong)
        for schema in ({'type':'object','properties':{'a':{'type':'object'}}},
                       {'type':'object','properties':{'a':{'type':'string','pattern':'secret'}}}):
            view = presentation('mcpServer/elicitation/request', {'mode':'openai/form','requestedSchema':schema})
            self.assertNotIn('accept',view['actions']); self.assertIn('decline',view['actions'])

    def test_question_response_uses_exact_ids_and_original_labels(self):
        params = {'questions':[{'id':'q1','question':'Choice','options':[{'label':'Accept','description':'test'}]},
                               {'id':'q2','question':'Text','isOther':True}]}
        method = 'item/tool/requestUserInput'
        record = {'method':method,'params':params,'view':presentation(method,params)}
        self.assertEqual(decision_result(record,'answer',{'q1':'Accept','q2':'literal text'}),
                         {'answers':{'q1':{'answers':['Accept']},'q2':{'answers':['literal text']}}})
        for wrong in ({'q1':'invented','q2':'x'}, {'q1':'Accept'}, {'q1':'Accept','q2':'x','extra':'y'}):
            with self.assertRaises(ValueError): decision_result(record,'answer',wrong)

    def test_binding_and_freshness_required_for_decisions(self):
        session = {'id':'s','binding':'tty1','tool':'codex','codexThreadID':'thread','codexSocket':'/tmp/test.sock','agentPID':1}
        clock = [0]; proxy = client(); handle = request(proxy)
        live = CodexLiveStatus('codex','/home',lambda:{},lambda *_:None,clock=lambda:clock[0])
        key = live.identity(session); live.cache[key]=(0,'approval');live.clients[live.scope(key)]=proxy
        data = {'binding':'tty2','approval':handle,'requestID':'a','action':'accept','content':{}}
        with self.assertRaises(ValueError): live.decide(session,data)
        data['binding']='tty1'; clock[0]=13
        with self.assertRaises(ValueError): live.decide(session,data)
        clock[0]=1; self.assertTrue(live.decide(session,data)['resolved'])

    @unittest.skipIf(os.name == 'nt', 'macOS managed socket ownership uses /tmp and Unix permissions')
    def test_socket_validation_rejects_files_symlinks_and_untrusted_locations(self):
        with tempfile.TemporaryDirectory(prefix='tp-codex-',dir='/tmp') as directory:
            path=Path(directory)/'server.sock';path.touch()
            self.assertFalse(valid_socket(str(path)))
            self.assertFalse(valid_socket('/etc/passwd'))
            self.assertFalse(valid_socket(''))

    @unittest.skipIf(os.name == 'nt', 'macOS managed TUI launcher requires POSIX sockets and signals')
    def test_managed_launcher_preserves_tui_arguments_and_cleans_owned_children(self):
        class Child:
            def __init__(self, args, **kwargs): self.args=args;self.kwargs=kwargs;self.code=None;children.append(self)
            def poll(self): return self.code
            def wait(self, timeout=None): self.code=0;return 0
            def terminate(self): self.code=0
        children, registration = [], []
        with patch('codex_managed.subprocess.Popen', Child), patch('codex_managed.valid_socket',return_value=True):
            self.assertEqual(run_managed('/fake/codex',['-c','notify=[]'],['resume',"literal ' $(text)"],
                                         {'PATH':'/fake'},registration.append),0)
        backend,tui=children
        self.assertEqual(backend.args[1:5],['-c','notify=[]','app-server','--listen'])
        self.assertTrue(backend.kwargs['start_new_session'])
        self.assertEqual(tui.args, ['/fake/codex','--remote','unix://'+registration[0],
                                    '--cd',os.getcwd(),'resume',"literal ' $(text)"])
        self.assertFalse(Path(registration[0]).parent.exists())
        self.assertIsNotNone(backend.poll())

    @unittest.skipIf(os.name == 'nt', 'macOS managed TUI launcher requires POSIX sockets and signals')
    def test_remote_resume_scope_follows_project_for_every_launch_mode(self):
        class Child:
            def __init__(self, args, **kwargs): self.args=args;self.code=None;children.append(self)
            def poll(self): return self.code
            def wait(self, timeout=None): self.code=0;return 0
            def terminate(self): self.code=0
        # Paths with spaces/metacharacters must remain a single literal argument.
        # Only the explicit cross-project picker may contain --all.
        for directory in ('/projects/one', "/projects/other ' $(literal)"):
            for extra in ([], ['resume'], ['resume','--last'], ['resume','--all'], ['resume','saved-id']):
                with self.subTest(directory=directory, extra=extra):
                    children, registration = [], []
                    with patch('codex_managed.os.getcwd', return_value=directory), \
                         patch('codex_managed.subprocess.Popen', Child), \
                         patch('codex_managed.valid_socket', return_value=True):
                        run_managed('/fake/codex', [], extra, {}, registration.append)
                    self.assertEqual(children[1].args[3:], ['--cd',directory,*extra])
                    self.assertEqual('--all' in children[1].args, '--all' in extra)


if __name__ == '__main__': unittest.main()
