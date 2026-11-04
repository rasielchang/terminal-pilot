import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import urlencode
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from attachment_store import AttachmentStore, MAX_ATTACHMENT, filename, read_local
from test_web import WebTests
from test_brain_unified import UnifiedTests

RAW = ('\ufeff會員ID,顯示名稱,Email\r\n1,"合成,會員",sample@example.invalid\r\n').encode('utf-8')

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=AttachmentStore(self.temp.name)
        self.owner={'topic':'topic-1','target':'conversation:'+'a'*32,'brainThread':'brain'}
        self.data={'requestID':'synthetic-1','name':'測試會員.csv','mime':'text/csv','data':base64.b64encode(RAW).decode()}
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_exact_bytes_hash_private_mode_idempotency_and_restart(self):
        value=self.store.register(self.data,self.owner)
        self.assertEqual(self.store.register(self.data,self.owner),value)
        self.assertEqual(self.store.read(value['id'],self.owner)[1],RAW)
        self.assertEqual(value['sha256'],hashlib.sha256(RAW).hexdigest())
        self.assertEqual(os.stat(self.store.root/(value['id']+'.csv')).st_mode&0o777,0o600)
        self.store.close();self.store=AttachmentStore(self.temp.name)
        self.assertEqual(self.store.read(value['id'],self.owner)[1],RAW)
        for changes in ({'name':'other.csv'},{'data':base64.b64encode(b'changed').decode()}):
            with self.assertRaises(ValueError):self.store.register({**self.data,**changes},self.owner)
    def test_scope_and_traversal_tampering_are_rejected(self):
        value=self.store.register(self.data,self.owner)
        for field in self.owner:
            with self.assertRaises(PermissionError):self.store.read(value['id'],{**self.owner,field:'other'})
        for identity in ('../index.sqlite3','/etc/passwd','a'*33,'a'*32):
            with self.assertRaises(ValueError):self.store.read(identity,self.owner)
        path=self.store.root/(value['id']+'.csv');path.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.store.read(value['id'],self.owner)
        path.unlink();path.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):self.store.read(value['id'],self.owner)
    def test_filename_mime_size_path_and_local_special_files(self):
        for name in ('../export.csv','/tmp/export.csv','a\\b.csv','a\r\n.csv','.hidden.csv','x.html','x\u202e.csv','x'*181+'.csv'):
            with self.assertRaises(ValueError):filename(name)
        for update in ({'mime':'text/html'},{'data':'%%% '},{'data':base64.b64encode(b'\xff').decode()}, {'path':'/etc/passwd'},
                       {'data':base64.b64encode(b'x'*(MAX_ATTACHMENT+1)).decode()}):
            with self.assertRaises(ValueError):self.store.register({**self.data,**update},self.owner)
        link=Path(self.temp.name)/'link.csv';link.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):read_local(link)
        if hasattr(os,'mkfifo'):
            fifo=Path(self.temp.name)/'fifo';os.mkfifo(fifo)
            with self.assertRaises(ValueError):read_local(fifo)

class AttachmentHTTPTests(WebTests):
    # Reuse HTTP harness, not its inherited test suite.
    def test_authenticated_download_headers_bytes_and_failure_boundaries(self):
        self.store=AttachmentStore(self.home);self.addCleanup(self.store.close)
        owner={'topic':'topic-1','target':'conversation:'+'a'*32,'brainThread':'brain'}
        item=self.store.register({'requestID':'download','name':'會員.csv','mime':'text/csv','data':base64.b64encode(RAW).decode()},owner)
        def read(data):return self.store.read(data['id'],{**owner,'topic':data['topic'],'target':data['target']})
        self.app.brain=type('Brain',(),{'read_attachment':staticmethod(read),'close':lambda _:None})()
        route='/api/attachments?'+urlencode({k:item[k] for k in ('id','topic','target')})
        self.assertEqual(self.request(route,auth=False)[0],401)
        self.assertEqual(self.request(route,headers={'Authorization':'Bearer wrong'})[0],401)
        self.assertEqual(self.request(route,headers={'Origin':'https://evil.invalid'})[0],403)
        code,headers,raw=self.request(route)
        self.assertEqual(code,200);self.assertEqual(raw,RAW)
        self.assertIn('Content-Disposition: attachment;',headers);self.assertIn('filename*=UTF-8',headers)
        self.assertIn('Cache-Control: no-store',headers);self.assertIn('X-Content-Type-Options: nosniff',headers)
        self.assertEqual(self.request(route.replace('topic-1','topic-2'))[0],403)
        self.assertEqual(self.request(route+'&id=duplicate')[0],409)

class BrainAttachmentTests(UnifiedTests):
    def test_formal_register_validates_current_turn_and_bound_target(self):
        c=self.context();target=self.targets[0]['id']
        data={'thread':'brain-thread','contextID':c['contextID'],'turn':c['turn'],'requestID':'file-1',
              'topic':'fixture:one','target':target,'name':'synthetic.csv','mime':'text/csv','data':base64.b64encode(RAW).decode()}
        value=self.brain.register_attachment(data)
        self.assertEqual(self.brain.register_attachment(data),value)
        query={k:value['attachment'][k] for k in ('id','topic','target')}
        self.assertEqual(self.brain.read_attachment(query)[1],RAW)
        self.assertIn('/api/attachments?',value['link'])
        with self.assertRaises(PermissionError):self.brain.register_attachment({**data,'target':self.targets[1]['id']})
        with self.assertRaises(PermissionError):self.brain.register_attachment({**data,'thread':'other'})
        self.backend.turn['status']='completed'
        with self.assertRaises(ValueError):self.brain.register_attachment(data)
        self.assertEqual(len(self.brain.state()['attachments']),1)

# Only run the added cases; shared harness base classes are imported above.
def load_tests(loader, tests, pattern):
    suite=unittest.TestSuite(loader.loadTestsFromTestCase(StoreTests))
    suite.addTest(AttachmentHTTPTests('test_authenticated_download_headers_bytes_and_failure_boundaries'))
    suite.addTest(BrainAttachmentTests('test_formal_register_validates_current_turn_and_bound_target'))
    return suite
