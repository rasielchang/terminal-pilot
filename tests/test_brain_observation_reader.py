import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from brain_observation_reader import read_page


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'history.jsonl'
        self.path.write_text(json.dumps({'type':'session_meta','payload':{'id':'thread'}})+'\n')
    def tearDown(self): self.temp.cleanup()
    def append(self, n):
        with self.path.open('a') as f:
            f.write(json.dumps({'type':'event_msg','payload':{'type':'user_message','id':str(n),'turn_id':str(n),'message':'same words'}})+'\n')
    def test_bounded_pages_restart_and_partial_tail(self):
        for n in range(350): self.append(n)
        state=None; ids=[]; pages=0
        while True:
            page=read_page(self.path,'thread','codex',state,max_messages=50)
            ids.extend(m['id'] for m in page['messages']); pages+=1; state=json.loads(json.dumps(page['reader']))
            self.assertLessEqual(len(page['messages']),50)
            if not page['hasMore']: break
        self.assertEqual(len(ids),len(set(ids))); self.assertEqual(len(ids),350); self.assertEqual(pages,7)
        self.assertEqual(read_page(self.path,'thread','codex',state)['scannedBytes'],0)
        with self.path.open('a') as f: f.write('{"type":')
        partial=read_page(self.path,'thread','codex',state)
        self.assertTrue(partial['hasMore']); self.assertEqual(partial['reader']['offset'],state['offset'])
        with self.path.open('a') as f: f.write('"event_msg","payload":{"type":"user_message","message":"new","id":"new"}}\n')
        self.assertEqual(read_page(self.path,'thread','codex',partial['reader'])['messages'][0]['text'],'new')
    def test_replacement_and_wrong_thread_are_explicit(self):
        self.append(1); old=read_page(self.path,'thread','codex')
        self.path.write_text(json.dumps({'type':'session_meta','payload':{'id':'thread'}})+'\n')
        self.append(2); new=read_page(self.path,'thread','codex',old['reader'])
        self.assertNotEqual(new['revision'],old['revision']); self.assertEqual(len(new['messages']),1)
        with self.assertRaises(ValueError): read_page(self.path,'other','codex')
    def test_malformed_complete_line_does_not_skip_gap(self):
        self.append(1)
        with self.path.open('a') as f: f.write('broken json\n')
        with self.assertRaises(ValueError): read_page(self.path,'thread','codex')
    def test_rewrite_with_identical_metadata_advances_epoch_and_resumes(self):
        self.append(1); old=read_page(self.path,'thread','codex'); stamp=self.path.stat()
        self.path.write_text(json.dumps({'type':'session_meta','payload':{'id':'thread'}})+'\n')
        self.append(2)
        os.utime(self.path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
        new=read_page(self.path,'thread','codex',old['reader'])
        self.assertTrue(new['reset']);self.assertNotEqual(new['revision'],old['revision'])
        self.assertEqual(new['reader']['epochSerial'],old['reader']['epochSerial']+1)
        self.assertEqual(len(new['messages']),1)
        resumed=read_page(self.path,'thread','codex',new['reader'])
        self.assertFalse(resumed['reset']);self.assertEqual(resumed['revision'],new['revision'])
        self.assertFalse(resumed['messages'])
    def test_claude_separates_threads_and_sidechains(self):
        self.path.write_text('')
        with self.path.open('a') as f:
            for thread,side in [('thread',False),('other',False),('thread',True)]:
                f.write(json.dumps({'sessionId':thread,'type':'user','uuid':thread+str(side),'isSidechain':side,
                                   'message':{'role':'user','content':'hello'}})+'\n')
        page=read_page(self.path,'thread','claude');self.assertEqual(len(page['messages']),1)

if __name__=='__main__': unittest.main()
