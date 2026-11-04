"""No real agent, Terminal, home history or provider. Replays exact source pages."""
import copy
import json
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from brain_unified import UnifiedBrain
from brain_targets import conversation_target
from test_brain import Harness
from test_brain_unified import Backend
import test_brain_unified as unified_fixture


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.h, self.backend = Harness(), Backend()
        for s in self.h.sessions: s['directory'] = '/fixture/shared'
        self.targets = [conversation_target(s) for s in self.h.sessions]
        self.pages = {t['thread']: [] for t in self.targets}
        self.revision, self.offline, self.page_size = 'file-1', set(), 100
        self.make()
        unified_fixture.seed_continuations(self.brain,self.h.sessions)

    def make(self):
        self.brain = UnifiedBrain(self.temp.name, self.h.state, self.h.thread, self.h.send,
            backend=self.backend, timeline=self.backend.timeline, clock=lambda: self.h.now,
            launch=lambda _: self.fail('observer cannot launch'), targets=lambda: copy.deepcopy(self.targets),
            observe_page=self.page)

    def tearDown(self):
        self.brain.close(); self.temp.cleanup()

    def page(self, target, after=0, reader=None):
        if target['thread'] in self.offline: raise OSError('offline fixture')
        messages = self.pages[target['thread']]
        rows = [m for m in messages if m['cursor'] > after]
        return {'thread':target['thread'], 'revision':self.revision, 'messages':rows[:self.page_size],
                'hasMore':len(rows)>self.page_size, 'latestCursor':messages[-1]['cursor'] if messages else 0}

    def append(self, role, text, thread='thread1', phase='final_answer'):
        rows = self.pages[thread]; n = len(rows)+1
        self.h.now += 1
        rows.append({'id':str(n),'thread':thread,'turn':'turn-'+str(n),'cursor':n,
                     'created':self.h.now,'role':role,'text':text,'phase':phase})

    def events(self, kind=None):
        rows = [json.loads(r[0]) for r in self.brain.db.execute('SELECT data FROM work_events ORDER BY revision')]
        return [r for r in rows if kind is None or r['kind']==kind]

    def record(self, thread='thread1'):
        return next(r for r in self.brain.observer.records() if r['target']['thread']==thread)

    def test_direct_work_without_jobs_and_after_delivery_stays_observed(self):
        self.brain.observer.reconcile()
        self.append('user','Approved within the original scope')
        self.append('assistant','Code tested, not deployed')
        self.brain.observer.reconcile()
        self.assertEqual(self.record()['instruction']['text'],'Approved within the original scope')
        self.assertEqual(self.record()['result']['text'],'Code tested, not deployed')
        self.append('user','Pause the next step'); self.brain.observer.reconcile()
        self.assertEqual(len(self.events('instruction')),2)
        self.assertFalse(self.h.sent); self.assertFalse(self.brain._rows('jobs'))

    def test_restart_duplicate_and_same_text_distinct_events(self):
        self.brain.observer.reconcile()
        self.append('user','Same words'); self.append('user','Same words')
        self.brain.observer.reconcile(); count=len(self.events())
        self.brain.close(); self.make(); self.brain.observer.reconcile()
        self.assertEqual(len(self.events()),count)
        self.assertEqual(len(self.events('instruction')),2)
        self.assertFalse(self.h.sent)

    def test_gap_recovers_exact_cursor_without_erasing_last_confirmed_summary(self):
        self.brain.observer.reconcile(); self.append('user','Latest decision'); self.brain.observer.reconcile()
        stamp=self.record()['lastVerifiedAt']; self.offline.add('thread1')
        self.h.now+=10; self.brain.observer.reconcile()
        self.assertEqual(self.record()['coverage'],'source_unavailable')
        self.assertEqual(self.record()['lastVerifiedAt'],stamp)
        self.assertEqual(self.record()['instruction']['text'],'Latest decision')
        self.append('assistant','Recovered result'); self.offline.clear(); self.brain.observer.reconcile()
        self.assertEqual(self.record()['coverage'],'current')
        self.assertEqual(self.record()['result']['text'],'Recovered result')

    def test_replace_source_reindexes_without_duplicate_delivery(self):
        self.brain.observer.reconcile(); self.append('user','Old'); self.brain.observer.reconcile()
        self.revision='replacement'; self.append('user','New'); self.brain.observer.reconcile()
        self.assertEqual(len(self.events('instruction')),2)
        self.assertEqual(self.record()['instruction']['text'],'New')
        self.assertEqual(len([e for e in self.events('source_reset') if e['work']==self.record()['id']]),1)

    def test_paged_bootstrap_is_silent_and_future_appends_are_not(self):
        self.page_size=2
        for _ in range(5): self.append('user','Historic')
        self.h.now+=1
        self.brain.observer.reconcile(); self.assertEqual(self.record()['coverage'],'catching_up')
        self.brain.observer.reconcile(); self.brain.observer.reconcile()
        self.assertFalse(any(e['notify'] for e in self.events('instruction')))
        self.append('user','New after bootstrap'); self.brain.observer.reconcile()
        self.assertTrue(self.events('instruction')[-1]['notify'])

    def test_rewritten_short_source_without_timestamp_keeps_future_events(self):
        for _ in range(5):self.append('user','Historic')
        self.h.now+=1;self.brain.observer.reconcile()
        self.pages['thread1']=[{'id':'replacement','cursor':1,'thread':'thread1','turn':'replacement',
            'role':'user','text':'New decision','created':0}]
        self.revision='replacement';self.brain.observer.reconcile()
        self.assertTrue(self.events('instruction')[-1]['notify'])
        self.pages['thread1'].append({'id':'next','cursor':2,'thread':'thread1','turn':'next',
            'role':'user','text':'Next decision','created':0})
        self.brain.observer.reconcile();self.assertTrue(self.events('instruction')[-1]['notify'])
        count=len(self.events());self.brain.observer.reconcile();self.assertEqual(len(self.events()),count)

    def test_different_waits_old_runtime_and_missing_transcript(self):
        s=self.h.sessions[0]; self.offline.add('thread1')
        s.update(phase='waiting',updatedAt=20,eventSequence=1,noticeID='a',runtime={'approvals':[{'id':'a'}]})
        self.brain.observer.reconcile(); first=self.record()['waiting']['id']
        s.update(phase='running',updatedAt=21,eventSequence=2,runtime={}); self.brain.observer.reconcile()
        self.assertIsNone(self.record()['waiting'])
        s.update(phase='waiting',updatedAt=22,eventSequence=3,noticeID='b',runtime={'approvals':[{'id':'b'}]})
        self.brain.observer.reconcile(); self.assertNotEqual(first,self.record()['waiting']['id'])
        s.update(phase='running',updatedAt=23,eventSequence=4,runtime={}); self.brain.observer.reconcile()
        s.update(phase='waiting',updatedAt=20,eventSequence=1,runtime={'approvals':[{'id':'a'}]})
        self.brain.observer.reconcile(); self.assertIsNone(self.record()['waiting'])

    def test_closed_work_reads_registered_history_but_done_topic_retires(self):
        self.brain.observer.reconcile(); self.h.sessions=[]
        self.append('assistant','Result after window close'); self.brain.observer.reconcile()
        self.assertEqual(self.record()['result']['text'],'Result after window close')
        with self.brain.db:
            self.brain._save('topics',{'id':'task','state':'done'})
            self.brain._save('jobs',{'id':'job','target':self.targets[0],'topic':'task'})
        self.brain.observer.reconcile(); self.assertFalse(self.record()['active'])

    def test_same_directory_different_threads_and_ambiguous_bindings(self):
        self.brain.observer.reconcile(); self.append('user','A'); self.append('user','B',thread='thread2')
        self.brain.observer.reconcile()
        self.assertEqual(self.record()['instruction']['text'],'A')
        self.assertEqual(self.record('thread2')['instruction']['text'],'B')
        self.h.sessions.append({**self.h.sessions[0],'id':'duplicate','binding':'another'})
        self.brain.observer.reconcile(); self.assertEqual(self.record()['coverage'],'current')
        self.assertEqual(self.record()['runtimeCoverage'],'ambiguous_binding')
        self.assertFalse(self.h.sent)

    def test_same_thread_in_different_agent_homes_is_not_one_work(self):
        self.targets[0]['agentHome']='/fixture/home-a'
        self.targets.append({**self.targets[0],'agentHome':'/fixture/home-b'})
        self.h.sessions=[]
        self.brain.observer.reconcile()
        matches=[r for r in self.brain.observer.records() if r['target']['thread']=='thread1']
        self.assertEqual({r['target']['agentHome'] for r in matches},{'/fixture/home-a','/fixture/home-b'})
        self.assertEqual(len({r['id'] for r in matches}),2)

    def test_failed_snapshot_cannot_clear_wait_and_auto_review_is_not_human_wait(self):
        s=self.h.sessions[0]; s.update(phase='waiting',updatedAt=20,eventSequence=1,approvalReviewer='user')
        self.brain.observer.reconcile()
        s.update(phase='running',updatedAt=21,eventSequence=2)
        self.brain.observer.reconcile({**self.h.state(),'snapshotReliable':False})
        self.assertIsNotNone(self.record()['waiting'])
        s.update(phase='waiting',updatedAt=22,eventSequence=3,approvalReviewer='auto_review')
        self.brain.observer.reconcile(); self.assertIsNone(self.record()['waiting'])


class ManualAndCloseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = unified_fixture.UnifiedTests('test_web_and_direct_share_only_thread_events')
        self.fixture.setUp()
    def tearDown(self): self.fixture.tearDown()
    def __getattr__(self, name):
        if name == 'fixture': raise AttributeError(name)
        return getattr(self.fixture, name)

    def test_manual_error_is_never_completed(self):
        self.action(self.plan()); self.brain.tick(); self.brain.manual_send('one')
        self.h.messages['one'] += [{'role':'user','text':'update'}, {'role':'assistant','text':'Partial reply'}]
        self.h.sessions[0]['phase']='error'; self.brain.tick()
        self.assertEqual(self.jobs()[0]['state'],'blocked')

    def test_manual_wait_recovery_and_second_wait_are_distinct(self):
        self.action(self.plan()); self.brain.tick(); self.brain.manual_send('one')
        for phase in ('waiting','running','waiting'):
            self.h.sessions[0]['phase']=phase; self.brain.tick()
        events=[s for s in self.backend.sources if s['trigger'].get('outcome')=='waiting']
        self.assertEqual(len(events),0,'Wait cards never need a model paraphrase')
        self.assertEqual(self.jobs()[0]['state'],'waiting')

    def test_direct_busy_checkout_blocks_queued_dispatch(self):
        self.h.sessions[1].update(directory=self.h.sessions[0]['directory'],phase='running')
        self.action(self.plan()); self.brain.tick(); self.assertFalse(self.h.sent)

    def test_close_unknown_only_reconciles_exact_generation(self):
        self.h.sessions[0]['bindingGeneration']='tty1@instance'
        self.action({'topics':[],'dispatches':[],'launches':[],'release':[],'close':['one']})
        with self.brain.db:
            record=self.brain._rows('closings')[0]; record['state']='uncertain'; self.brain._save('closings',record)
        reads=[]
        def proof(record):
            reads.append(record['generation'])
            return {'reliable':True,'absent':True,'generation':record['generation']}
        self.brain.closure_status=proof
        self.brain.tick(); self.assertEqual(self.brain._rows('closings')[0]['state'],'closed')
        self.assertEqual(reads,['tty1@instance']); self.assertFalse(self.closed)

    def test_migration_does_not_reannounce_legacy_closed_receipt(self):
        self.action({'topics':[],'dispatches':[],'launches':[],'release':[],'close':['one']})
        with self.brain.db:
            record=self.brain._rows('closings')[0]; record['state']='closed'; self.brain._save('closings',record)
            self.brain._save('turns',{'id':'event:'+record['id'],'state':'done','goal':record['goal'],
                                    'trigger':{'kind':'result','outcome':'closed'}})
        before=len(self.brain._rows('turns')); self.brain.tick()
        self.assertEqual(len(self.brain._rows('turns')),before); self.assertFalse(self.closed)


if __name__ == '__main__': unittest.main()
