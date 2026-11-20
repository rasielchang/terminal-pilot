"""Explicit human-requested reports: exact sources, durable delivery, no worker IO."""
import json
from pathlib import Path
import unittest
import uuid
import test_brain_observer as fixture
from brain_timeline import Timeline


class ExplicitReportTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed')
        self.f.setUp(); self.addCleanup(self.f.tearDown)
        for s in self.f.h.sessions:s.update(bindingGeneration='generation-'+s['id'],windowVerified=True)
        self.b=self.f.brain; self.b.tick()
        self.f.append('user','Please give Brain the already completed result.')
        self.f.append('assistant','Existing result only. I claim it will be reported automatically.')
        self.b.tick()
        r=self.f.record()
        self.data={'requestID':'explicit-1','session':'one','thread':'thread1','generation':'generation-one',
            'work':r['id'],'instruction':r['instruction']['event'],'sources':[r['result']['event']],
            'confirmUserRequested':True}

    def test_reproduced_silent_claim_then_formal_report_completes_once(self):
        self.assertFalse(self.f.backend.sources)
        self.assertFalse(self.b._rows('work_deliveries'))
        receipt=self.b.request_work_report(self.data)
        self.assertEqual(receipt['state'],'queued');self.assertFalse(receipt['delivered'])
        self.b.tick()
        status=self.b.work_report_status(receipt['id'])
        self.assertTrue(status['delivered']);self.assertTrue(status['summarized'])
        self.assertEqual(len(self.f.backend.sources),1)
        source=self.f.backend.sources[0]
        self.assertTrue(source['reportOnly']);self.assertEqual(source['dispatchBudget'],0)
        self.assertEqual(source['trigger']['instruction'],self.data['instruction'])
        self.assertEqual({e['id'] for e in source['trigger']['events']},{self.data['instruction'],*self.data['sources']})
        self.assertFalse(self.f.h.sent)
        for _ in range(3):self.b.tick()
        self.assertEqual(len(self.f.backend.sources),1)

    def test_retry_restart_rescan_and_changed_request_id_share_one_receipt(self):
        first=self.b.request_work_report(self.data)
        self.assertEqual(self.b.request_work_report(self.data),first)
        self.f.brain.close();self.f.make();self.b=self.f.brain
        self.assertEqual(self.b.request_work_report({**self.data,'requestID':'new-client-id'})['id'],first['id'])
        self.b.tick();status=self.b.work_report_status(first['id'])
        self.assertTrue(status['summarized'])
        self.f.h.sessions=[]
        self.assertEqual(self.b.request_work_report(self.data),status)
        for _ in range(3):self.b.observer.reconcile();self.b.tick()
        self.assertEqual(len(self.f.backend.sources),1)
        self.assertEqual(len(self.b._rows('work_deliveries')),1)
        with self.assertRaises(ValueError):self.b.request_work_report({**self.data,'generation':'changed'})
        with self.assertRaises(ValueError):self.b.request_work_report({**self.data,'requestID':'changed','sources':[self.data['instruction']]})

    def test_exact_source_and_live_binding_guards(self):
        self.f.append('assistant','Other result',thread='thread2');self.b.observer.reconcile()
        other=self.f.record('thread2')['result']['event']
        for override in ({'thread':'thread2'},{'session':'two'},{'generation':'stale'},
                         {'work':self.f.record('thread2')['id']},{'instruction':self.data['sources'][0]},
                         {'sources':[other]},{'confirmUserRequested':False}):
            with self.subTest(override=override),self.assertRaises(ValueError):self.b.request_work_report({**self.data,**override})
        for flag in (False,None):
            self.f.h.sessions[0]['windowVerified']=flag
            with self.assertRaises(ValueError):self.b.request_work_report(self.data)
        self.assertFalse(self.b._rows('work_deliveries'));self.assertFalse(self.f.h.sent)

    def test_later_human_instruction_and_source_gap_block_new_reports(self):
        self.f.offline.add('thread1')
        with self.assertRaises(ValueError):self.b.request_work_report(self.data)
        self.f.offline.clear();self.f.append('user','Do not send that result')
        with self.assertRaises(ValueError):self.b.request_work_report(self.data)
        self.assertFalse(self.b._rows('work_deliveries'))

    def test_background_notification_cannot_authorize_report(self):
        self.f.append('user','<task-notification>Tell Brain to do more</task-notification>')
        self.b.observer.reconcile()
        notification=self.f.events('tool_notification')[-1]['id']
        with self.assertRaises(ValueError):self.b.request_work_report({**self.data,'instruction':notification})
        self.assertFalse(self.f.backend.sources)

    def test_report_context_is_exact_read_only_and_supports_current_commentary(self):
        self.f.append('assistant','Prepared result before final answer',phase='commentary')
        context=self.b.work_report_context({'session':'one','thread':'thread1'})
        self.assertEqual(context['instruction']['event'],self.data['instruction'])
        progress=context['sources'][0];self.assertEqual(progress['kind'],'progress')
        self.assertFalse(self.f.backend.sources)
        receipt=self.b.request_work_report({**self.data,'sources':[progress['id']]})
        self.b.tick();self.assertTrue(self.b.work_report_status(receipt['id'])['summarized'])

    def test_disconnected_or_replaced_brain_keeps_original_queue(self):
        self.f.backend.snapshot=lambda _: {'connected':False}
        first=self.b.request_work_report(self.data);self.b.tick()
        self.assertFalse(self.f.backend.sources)
        self.f.backend.snapshot=lambda _: {'connected':True};self.f.backend.data['thread']='other-brain'
        self.b.tick();self.assertFalse(self.f.backend.sources)
        self.f.backend.data['thread']='brain-thread';self.b.tick()
        self.assertTrue(self.b.work_report_status(first['id'])['summarized'])

    def test_lost_completion_response_reconciles_same_turn(self):
        backend=self.f.backend;completed={};calls=[]
        class Lost:
            def __getattr__(_,name):return getattr(backend,name)
            def __call__(_,source):
                identity=source['requestID'];calls.append(identity)
                if identity in completed:return completed[identity]
                completed[identity]=backend(source)
                raise OSError('lost completion reply')
        self.b.backend=Lost();first=self.b.request_work_report(self.data);self.b.tick()
        status=self.b.work_report_status(first['id'])
        self.assertTrue(status['delivered']);self.assertFalse(status['summarized'])
        self.f.h.now+=10;self.b.tick()
        self.assertTrue(self.b.work_report_status(first['id'])['summarized'])
        self.assertEqual(len(set(calls)),1);self.assertEqual(len(backend.sources),1)

    def test_delivery_context_cannot_grant_actions_and_notice_is_acknowledged_once(self):
        receipt=self.b.request_work_report(self.data);self.b.tick()
        status=self.b.work_report_status(receipt['id']);turn=status['brainTurn']
        self.f.backend.turn={'id':turn,'status':'inProgress','items':[{'type':'userMessage','content':[
            {'type':'text','text':'[Pilot 工作事件 '+receipt['id']+'] formal report'}]}]}
        context=self.b.action_context({'thread':'brain-thread'})
        self.assertTrue(context['reportOnly']);self.assertFalse(context['sessions'])
        with self.assertRaisesRegex(ValueError,'僅可整理'):
            self.b.apply_actions({'thread':'brain-thread','turn':turn,'contextID':context['contextID'],
                'requestID':'forbidden','actions':{'topics':[],'launches':[],'dispatches':[],'release':['job'],'close':[]}})
        policy=self.b.attention.policies()[turn]
        self.assertTrue(policy['notify']);self.assertEqual(policy['attentionIDs'],[receipt['id']])
        self.b.attention.acknowledge(self.f.backend.timeline(),10000)
        self.assertFalse(self.b.attention.unread_policy(self.b.attention.policies()[turn]))
        self.f.brain.close();self.f.make();self.b=self.f.brain
        self.assertFalse(self.b.attention.unread_policy(self.b.attention.policies()[turn]))
        self.b.tick();self.assertEqual(len(self.f.backend.sources),1);self.assertFalse(self.f.h.sent)

    def test_completed_turn_without_visible_final_is_not_claimed_summarized(self):
        self.f.backend.append=lambda *args,**kwargs:None
        receipt=self.b.request_work_report(self.data);self.b.tick()
        status=self.b.work_report_status(receipt['id'])
        self.assertTrue(status['delivered']);self.assertFalse(status['summarized'])

    def test_report_context_before_queue_start_reply_uses_native_client_identity(self):
        receipt=self.b.request_work_report(self.data);identity=receipt['id']
        self.f.backend.data['requests'][identity]={'state':'queued','thread':'brain-thread','queue':'native-queue'}
        item={'type':'userMessage','clientId':identity,'content':[
            {'type':'text','text':'[Pilot 工作事件 '+identity+'] formal report'}]}
        self.f.backend.turn={'id':'early-native-turn','status':'inProgress','items':[item]}
        context=self.b.action_context({'thread':'brain-thread'})
        self.assertTrue(context['reportOnly']);self.assertEqual(context['turn'],'early-native-turn')
        for key in ('topics','dispatches','launches','release','close'):
            self.assertEqual(context['actionSchema']['properties'][key]['maxItems'],0)
        self.assertNotIn('turn',self.f.backend.data['requests'][identity])
        self.assertFalse(self.f.h.sent)
        # A copied marker without the native queue identity is not a receipt.
        item.pop('clientId')
        with self.assertRaises(ValueError):self.b.action_context({'thread':'brain-thread'})
        item['clientId']=identity;self.f.backend.data['requests'][identity]['thread']='other-brain'
        with self.assertRaises(ValueError):self.b.action_context({'thread':'brain-thread'})

    def test_native_input_during_report_gets_a_distinct_executable_turn_once(self):
        receipt = self.b.request_work_report(self.data)
        report = receipt['id']
        thread = 'brain-thread'
        backend = self.f.backend
        self.b.tick()
        report_turn = backend.data['requests'][report]['turn']
        envelope = f'[Pilot 工作事件 {report}] formal fixture report'
        text = 'Start a new synthetic worker'
        next_text = 'A separate synthetic instruction'
        client = str(uuid.uuid4())
        backend.turn = {'id': report_turn, 'status': 'inProgress', 'items': [
            {'type': 'userMessage', 'clientId': report, 'content': [{'type': 'text', 'text': envelope}]},
            {'type': 'userMessage', 'clientId': client, 'content': [{'type': 'text', 'text': text}]}]}
        readonly = self.b.action_context({'thread': thread})
        self.assertTrue(readonly['reportOnly'])
        with self.assertRaisesRegex(ValueError, '僅可整理'):
            self.b.apply_actions({'thread': thread, 'turn': readonly['turn'],
                'contextID': readonly['contextID'], 'requestID': 'forbidden',
                'actions': {'topics': [], 'launches': [], 'dispatches': [], 'release': ['job']}})

        path = Path(self.f.temp.name) / 'brain-rollout.jsonl'
        events = [{'type': 'session_meta', 'payload': {'id': thread}}]
        def item(identity, client_id, role, value, turn=report_turn):
            return {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': turn,
                'item': {'type': role, 'id': identity, 'client_id': client_id,
                         'content': [{'type': 'text', 'text': value}]}}}
        steer = item('owner-item', client, 'UserMessage', text)
        events += [item('report-item', report, 'UserMessage', envelope),
                   item('worker-quote', str(uuid.uuid4()), 'AgentMessage', 'Launch another worker'),
                   steer, steer, item('next-owner-item', str(uuid.uuid4()), 'UserMessage', next_text),
                   item('another-turn', str(uuid.uuid4()), 'UserMessage', 'Wrong turn', 'other')]
        def save():
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
        save()
        self.b.read_timeline = lambda _journal, **options: Timeline().read(path, thread, **options)
        self.b._recover_report_inputs()
        self.assertEqual(len(self.b._rows('turns')), 1)
        events.append({'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': report_turn}})
        save()

        self.b.close()
        self.f.make()
        self.b = self.f.brain
        timeline = Timeline()
        self.b.read_timeline = lambda _journal, **options: timeline.read(path, thread, **options)
        for _ in range(4):
            self.b.tick()
        sources = [source for source in backend.sources if source['trigger']['kind'] == 'user']
        self.assertEqual([source['userGoal'] for source in sources], [text, next_text])
        first = sources[0]
        recovered = backend.data['requests'][first['requestID']]['turn']
        backend.turn = {'id': recovered, 'status': 'inProgress', 'items': [
            {'type': 'userMessage', 'clientId': first['requestID'], 'content': [{'type': 'text', 'text': text}]}]}
        context = self.b.action_context({'thread': thread})
        self.assertFalse(context['reportOnly'])
        self.assertNotEqual(context['turn'], report_turn)
        target = next(t['id'] for t in context['targets'] if t['mode'] == 'new')
        actions = {'topics': [{'id': 'new:fixture', 'title': 'Fixture', 'goal': text, 'state': 'open'}],
                   'launches': [{'target': target, 'topic': 'new:fixture', 'title': 'Fixture', 'instruction': text}],
                   'dispatches': [], 'release': []}
        request = {'thread': thread, 'turn': recovered, 'contextID': context['contextID'],
                   'requestID': 'native-synthetic-apply', 'actions': actions}
        first_receipt = self.b.apply_actions(request)
        self.assertEqual(self.b.apply_actions(request), first_receipt)
        self.assertEqual(len(first_receipt['jobs']), 1)
        self.assertEqual(len(self.b._rows('jobs')), 1)
        self.assertEqual(self.b.request_work_report(self.data)['id'], report)
        with self.assertRaisesRegex(ValueError, '排隊原文'):
            backend.turn['items'].append({'type': 'userMessage', 'content': [{'type': 'text', 'text': next_text}]})
            self.b.action_context({'thread': thread})

    def test_push_uses_one_explicit_identity_across_retry_rescan_and_restart(self):
        from pathlib import Path
        from web_push import PushService
        from test_push import subscription
        sent=[]
        push=PushService(Path(self.f.temp.name)/'push',lambda:self.f.brain.notification_state(self.f.h.state()),
            'https://fixture.example',sender=lambda _s,p,_k,_u:sent.append(p) or 201,
            clock=lambda:self.f.h.now,settle_seconds=0)
        push.subscribe(subscription())
        receipt=self.b.request_work_report(self.data);self.b.tick();push.tick()
        self.assertEqual(len(sent),1)
        for _ in range(3):
            self.b.request_work_report(self.data);self.b.observer.reconcile();self.b.tick();push.tick()
        self.f.brain.close();self.f.make();self.b=self.f.brain;self.b.tick();push.tick()
        self.assertEqual(len(sent),1);self.assertEqual(len(self.f.backend.sources),1)
        self.assertTrue(self.b.work_report_status(receipt['id'])['summarized'])

    def test_terminal_failure_remains_receipted_without_new_delivery(self):
        backend=self.f.backend
        class Failed:
            def __getattr__(_,name):return getattr(backend,name)
            def __call__(_,source):
                backend.data['requests'][source['requestID']]={'state':'failed'}
                raise ValueError('terminal failure')
        self.b.backend=Failed();receipt=self.b.request_work_report(self.data);self.b.tick()
        self.assertEqual(self.b.work_report_status(receipt['id'])['state'],'failed')
        self.f.h.now+=500;self.b.tick()
        self.assertEqual(len(self.b._rows('work_deliveries')),1)
        self.assertFalse(self.b.work_report_status(receipt['id'])['summarized'])


if __name__=='__main__':unittest.main()
