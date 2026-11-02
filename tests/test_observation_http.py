"""Authenticated HTTP consumers over the actual handler and isolated observer."""
import json
import unittest
import test_web as http_fixture
import test_brain_observer as observer_fixture


class ObservationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.web=http_fixture.WebTests('test_brain_contract_auth_durable_acceptance_and_control');self.web.setUp()
        self.work=observer_fixture.ObserverTests('test_direct_work_without_jobs_and_after_delivery_stays_observed');self.work.setUp()
        if self.web.app.brain:self.web.app.brain.close()
        self.web.app.brain=self.work.brain
    def tearDown(self):
        self.web.app.brain=None;self.web.tearDown();self.work.tearDown()
    def test_auth_query_reconcile_delta_and_source_contract(self):
        request=self.web.request
        self.assertEqual(request('/api/brain/work-state',auth=False)[0],401)
        self.assertEqual(request('/api/brain/reconcile',{},auth=False)[0],401)
        self.assertEqual(request('/api/brain/reconcile',{},headers={'Origin':'https://untrusted.invalid'})[0],403)
        code,head,raw=request('/api/brain/reconcile',{'limit':1})
        self.assertEqual(code,200);self.assertIn('no-store',head)
        data=json.loads(raw); self.assertTrue(data['reportOnly']);self.assertIn('coverage',data)
        cursor=data['revision'];self.work.append('user','New decision');self.work.brain.observer.reconcile()
        code,_,raw=request('/api/brain/work-state?after='+str(cursor)+'&limit=1')
        self.assertEqual(code,200);work=json.loads(raw)['works'][0]
        # Source IDs are scoped to work. Callers cannot supply filesystem paths.
        r=self.work.record();event=r['latest']['event']
        code,_,raw=request('/api/brain/observation-source?work='+r['id']+'&event='+event)
        self.assertEqual(code,200);self.assertEqual(json.loads(raw)['event']['text'],'New decision')
        self.assertEqual(request('/api/brain/observation-source?work=wrong&event='+event)[0],409)
        self.assertEqual(request('/api/brain/reconcile',{'directory':'/unregistered'})[0],409)
        self.assertEqual(request('/api/brain/work-state?after=-1')[0],409)
        self.assertFalse(self.work.backend.sources);self.assertFalse(self.work.h.sent)
    def test_legacy_host_explicitly_unsupported_and_invalid_summary_no_mutation(self):
        original=self.web.app.brain;self.web.app.brain=object()
        for route,data in [('/api/brain/work-state',None),('/api/brain/reconcile',{}),('/api/brain/observations',{})]:
            code,_,raw=self.web.request(route,data);self.assertEqual(code,501)
            self.assertEqual(json.loads(raw)['code'],'unsupported_operation')
        self.web.app.brain=original
        self.assertEqual(self.web.request('/api/brain/observations',{})[0],409)
        self.assertFalse(self.work.h.sent)


if __name__=='__main__':unittest.main()
