"""Paired photo API, durable image delivery and exact conversation isolation."""
import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from urllib.parse import urlencode
from PIL import Image
import test_web as fixtures
FakeNative,web=fixtures.FakeNative,fixtures.web
from photo_store import PhotoStore,scope,wire,claude_wire,clean,MAX_PHOTO
from photo_delivery import PhotoDelivery


def picture(fmt='PNG',size=(80,60)):
    b=io.BytesIO();Image.new('RGB',size,'red').save(b,fmt);return base64.b64encode(b.getvalue()).decode()

class Live:
    def __init__(self):self.calls=[];self.queue=[];self.turns=[];self.lose_add=False;self.lose_start=False
    def brain_call(self,session,method,params):
        self.calls.append((method,copy.deepcopy(params)))
        if method=='thread/queue/list':return {'data':copy.deepcopy(self.queue)}
        if method=='thread/turns/list':return {'data':self.turns}
        if method=='thread/queue/add':
            self.queue.append({'id':'queued','clientUserMessageId':params['clientUserMessageId']})
            if self.lose_add:raise OSError('lost add response')
            return {'queuedSubmission':self.queue[-1]}
        if method=='thread/queue/start':
            q=self.queue.pop(0);t={'id':'turn','status':'inProgress','items':[{'type':'userMessage','clientId':q['clientUserMessageId']}]};self.turns.append(t)
            if self.lose_start:raise OSError('lost start response')
            return {'turn':t}
        raise AssertionError(method)

class PhotoTests(unittest.TestCase):
    request=fixtures.WebTests.request
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name);self.native=FakeNative()
        self.s={'id':'one','binding':'tty-one','tool':'codex','codexThreadID':str(uuid.uuid4()),'directory':str(self.home),'phase':'complete','alive':True,'canSend':True}
        self.other={**self.s,'id':'two','binding':'tty-two','codexThreadID':str(uuid.uuid4())}
        self.native.sessions=[self.s,self.other]
        self.app=web.WebApplication(self.native,Path(__file__).resolve().parents[1]/'web',self.home,store=self.home/'store')
        self.addCleanup(self.app.brain.close)
        self.app.with_runtime=lambda s:s
        self.app.live=Live();self.server=type('Server',(),{'application':self.app,'server_port':8765})()
    def target(self,s=None):
        s=s or self.s;return {'session':s['id'],'binding':s['binding'],'thread':s.get('codexThreadID') or s.get('claudeSessionID')}
    def upload(self,**extra):
        d={**self.target(),'requestID':'upload','name':'photo.png','data':picture(),**extra}
        return self.request('/api/photos',d)
    def send(self,photo,**extra):return self.app.send({**self.target(),'text':'Describe image','photos':[photo],'requestID':'send',**extra})
    def photo(self):return json.loads(self.upload()[2])['photo']['id']
    def test_auth_origin_validation_and_only_scoped_large_body(self):
        data={**self.target(),'requestID':'u','name':'x.png','data':picture()}
        self.assertEqual(self.request('/api/photos',data,auth=False)[0],401)
        self.assertEqual(self.request('/api/photos',data,headers={'Origin':'https://other.invalid'})[0],403)
        self.assertEqual(self.upload(data='bad data')[0],409)
        self.assertEqual(self.upload(name='photo.heic')[0],409)
        self.assertEqual(self.upload(data=picture('GIF'))[0],409)
        self.assertEqual(self.upload(data=base64.b64encode(b'x'*(MAX_PHOTO+1)).decode())[0],409)
        self.assertEqual(self.request('/api/send',{**data,'text':'x'*40001})[0],409)
    def test_upload_retry_normalizes_private_file_and_restart_receipt(self):
        status,_,raw=self.upload();self.assertEqual(status,200);p=json.loads(raw)['photo']
        self.assertEqual(self.upload()[2],raw)
        self.assertEqual(self.upload(thread=self.other['codexThreadID'])[0],409)
        self.assertEqual(self.upload(data=picture(size=(20,20)))[0],409)
        store=PhotoStore(self.home/'store');self.addCleanup(store.close)
        self.assertEqual(store.upload({**self.target(),'requestID':'upload','data':picture(),'name':'photo.png'},scope(self.s)),p)
        path=Path(store.paths([p['id']],scope(self.s))[0])
        with Image.open(path) as img:self.assertEqual(img.format,'JPEG')
        self.assertNotIn('data',p)
    def test_history_read_requires_auth_exact_thread_home_and_binding(self):
        identity=self.photo();q={**self.target(),'id':identity}
        status,head,body=self.request('/api/photos?'+urlencode(q));self.assertEqual(status,200);self.assertIn('no-store',head);self.assertTrue(body.startswith(b'\xff\xd8'))
        self.assertEqual(self.request('/api/photos?'+urlencode(q),auth=False)[0],401)
        self.assertEqual(self.request('/api/photos?'+urlencode({**self.target(self.other),'id':identity}))[0],409)
        self.s['binding']='reused';self.assertEqual(self.request('/api/photos?'+urlencode(q))[0],409)
        with self.assertRaises(ValueError):self.app.photos.paths([identity],scope({**self.s,'codexHome':str(self.home/'different-home')}))
        self.assertEqual(self.request('/api/photos?'+urlencode({**self.target(),'id':'../../web-auth.json'}))[0],409)
    def test_actual_rpc_input_contains_image_and_retry_never_resends(self):
        identity=self.photo();self.assertTrue(self.send(identity)['sent']);self.assertTrue(self.send(identity)['sent'])
        adds=[p for m,p in self.app.live.calls if m=='thread/queue/add'];self.assertEqual(len(adds),1)
        self.assertEqual(adds[0]['threadId'],self.s['codexThreadID']);image=adds[0]['input'][1]
        self.assertEqual(image['type'],'localImage');self.assertTrue(Path(image['path']).is_file())
        with self.assertRaises(ValueError):self.send(identity,text='changed')
    def test_lost_add_and_start_responses_reconcile_without_replay_after_restart(self):
        identity=self.photo();self.app.live.lose_add=True
        self.assertTrue(self.send(identity)['uncertain'])
        self.app.photo_delivery=PhotoDelivery(self.app);self.app.live.lose_start=True
        self.send(identity);self.app.photo_delivery=PhotoDelivery(self.app)
        self.assertTrue(self.send(identity)['sent'])
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in self.app.live.calls),1)
        self.assertEqual(sum(m=='thread/queue/start' for m,_ in self.app.live.calls),1)
    def test_uncertain_absent_queue_and_changed_binding_do_not_retarget(self):
        identity=self.photo();self.app.live.lose_add=True;self.send(identity);self.app.live.queue=[]
        self.assertTrue(self.send(identity)['uncertain'])
        self.s['binding']='different';self.assertTrue(self.app.photo_delivery.advance(self.app.photos.receipt('send'))['uncertain'])
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in self.app.live.calls),1)
    def test_auto_consumed_queue_recovers_from_original_history_when_rpc_history_unavailable(self):
        identity=self.photo();self.app.live.lose_add=True;self.send(identity);self.app.live.queue=[]
        page={'messages':[{'role':'user','text':wire('Describe image',[identity],'send')}]}
        self.app.thread_page=lambda session:self.app.photos.decorate(page,scope(self.s))
        self.app.photo_delivery=PhotoDelivery(self.app)
        self.assertTrue(self.send(identity)['sent'])
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in self.app.live.calls),1)

    def test_no_send_to_busy_waiting_duplicate_or_unbound_threads(self):
        identity=self.photo()
        for phase in ('running','waiting'):
            self.s['phase']=phase
            with self.assertRaises(ValueError):self.send(identity)
        self.s['phase']='complete';self.native.sessions.append({**self.s,'id':'duplicate'})
        with self.assertRaises(ValueError):self.send(identity)
        self.native.sessions.pop();self.s['codexThreadID']=''
        with self.assertRaises(ValueError):self.send(identity)
        self.assertFalse(any(m=='thread/queue/add' for m,_ in self.app.live.calls))
    def test_authorized_forwarding_grants_only_exact_target_and_history_keeps_reference(self):
        identity=self.photo();self.app.photos.grant([identity],scope(self.s),scope(self.other))
        self.assertEqual(self.app.photos.paths([identity],scope(self.s)),self.app.photos.paths([identity],scope(self.other)))
        page={'messages':[{'role':'user','text':wire('Look',[identity])}]}
        result=self.app.photos.decorate(page,scope(self.other));self.assertEqual(result['messages'][0]['text'],'Look')
        self.assertEqual(result['messages'][0]['photos'][0]['id'],identity)
        self.assertNotIn('base64',json.dumps(result));self.assertNotIn(str(self.home),json.dumps(result))
    def test_claude_uses_readable_image_and_native_uncertainty_is_not_retyped(self):
        self.s.update(tool='claude',claudeSessionID=str(uuid.uuid4()));self.s.pop('codexThreadID')
        identity=self.photo();real=self.native.call
        def lost(method,params=None):
            if method=='send':self.native.calls.append((method,params));raise OSError('lost native response')
            return real(method,params)
        self.native.call=lost
        self.assertTrue(self.send(identity)['uncertain']);self.assertTrue(self.send(identity)['uncertain'])
        calls=[p for m,p in self.native.calls if m=='send'];self.assertEqual(len(calls),1)
        self.assertIn(self.app.photos.paths([identity],scope(self.s))[0],calls[0]['text'])
        self.assertEqual(clean(calls[0]['text']),'Describe image')
        self.assertEqual(self.app.photos.decorate({'messages':[{'role':'user','text':calls[0]['text']}]},scope(self.s))['messages'][0]['photos'][0]['id'],identity)
    def test_brain_acceptance_persists_ids_and_exact_thread_not_base64(self):
        backend=self.app.brain.backend;backend.data.update(thread=self.s['codexThreadID'],session=self.s['id'],binding=self.s['binding'])
        identity=self.photo();d={'requestID':'brain-photo','text':'','photos':[identity],'thread':self.s['codexThreadID']}
        r=self.request('/api/brain/message',d);self.assertEqual(r[0],200);self.assertEqual(self.request('/api/brain/message',d)[2],r[2])
        turns=self.app.brain._rows('turns');self.assertEqual(len(turns),1);self.assertEqual(turns[0]['photos'],[identity])
        self.assertEqual(self.request('/api/brain/message',{**d,'thread':self.other['codexThreadID']})[0],409)
        self.assertNotIn('base64',json.dumps(turns))


class FormalPhotoTests(unittest.TestCase):
    def test_brain_formal_forward_retains_exact_job_and_replay_fence(self):
        import test_brain_unified as unified
        f=unified.UnifiedTests();f.setUp();self.addCleanup(f.tearDown)
        store=PhotoStore(f.temp.name);f.brain.photos=store;f.brain.photo_scope=lambda:'brain-scope'
        p=store.upload({'requestID':'photo','name':'test.png','data':picture()},'brain-scope')['id']
        plan=f.plan();plan['dispatches'][0]['photos']=[p];context=f.context()
        first=f.action(plan,context=context);self.assertEqual(f.action(plan,context=context),first)
        f.brain.tick();self.assertEqual(len(f.h.sent),1)
        self.assertEqual(f.h.sent[0]['photos'],[p]);self.assertEqual(f.h.sent[0]['thread'],'thread1')
        self.assertEqual(f.h.sent[0]['photoSource'],'brain-scope')
        self.assertEqual(f.jobs()[0]['photos'],[p]);self.assertNotIn('base64',json.dumps(f.jobs()))
        foreign=store.upload({'requestID':'other','name':'test.png','data':picture()},'other-scope')['id']
        f.backend.turn['id']='next-turn';bad=f.plan(('two',));bad['dispatches'][0]['photos']=[foreign]
        with self.assertRaisesRegex(ValueError,'照片不在'):f.action(bad,request='other')
        self.assertEqual(len(f.jobs()),1)

    def test_closed_exact_launch_keeps_photo_reference_until_binding_ready(self):
        import test_brain_unified as unified
        f=unified.UnifiedTests();f.setUp();self.addCleanup(f.tearDown)
        f.brain.photos=PhotoStore(f.temp.name);f.brain.photo_scope=lambda:'brain-scope'
        p=f.brain.photos.upload({'requestID':'photo','data':picture()},'brain-scope')['id']
        f.h.sessions.pop(0);plan=f.plan();plan['dispatches']=[]
        plan['launches']=[{'target':f.targets[0]['id'],'topic':'new:one','title':'Original','instruction':'Look','photos':[p]}]
        f.action(plan);f.brain.tick();f.brain.tick()
        self.assertEqual(f.opened[0]['sessionID'],'thread1');self.assertEqual(f.h.sent[0]['photos'],[p]);self.assertEqual(len(f.h.sent),1)

    def test_brain_queue_receives_real_image_once_with_bounded_incremental_context(self):
        import test_brain_queue as queued
        from brain_context import prepare
        f=queued.QueueTests();f.setUp();self.addCleanup(f.tearDown)
        store=PhotoStore(f.temp.name);self.addCleanup(store.close)
        p=store.upload({'requestID':'photo','data':picture()},'brain')['id']
        f.backend.photos=store;f.backend.photo_scope=lambda:'brain'
        f.source.update(photos=[p],brainThread='thread',availablePhotos=store.catalog('brain'))
        f.pending();inputs=next(p['input'] for m,p in f.calls if m=='thread/queue/add')
        self.assertEqual(inputs[1]['type'],'localImage');self.assertTrue(Path(inputs[1]['path']).exists())
        first,cursor,_=prepare(f.source,None,'','thread','binding')
        later={**f.source,'requestID':'later'};later.pop('photos')
        second,_,checkpoint=prepare(later,cursor,'','thread','binding')
        self.assertFalse(checkpoint);self.assertNotIn('base64',first);self.assertNotIn('"name":',second);self.assertLess(len(second),len(first))
        f.pending();self.assertTrue(f.backend(f.source)['completed'])
        f.make();self.assertTrue(f.backend(f.source)['completed'])
        self.assertEqual(sum(m=='thread/queue/add' for m,_ in f.calls),1)

    def test_invalid_nested_ids_and_launch_type_are_value_errors(self):
        from photo_store import photo_ids
        from brain_provider import validate_plan
        with self.assertRaises(ValueError):photo_ids([[]])
        with self.assertRaises(ValueError):validate_plan({'reply':'ok','dispatches':[],'release':[],'topics':[],'launches':None})

if __name__=='__main__':unittest.main()
