import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from brain_timeline import Timeline

class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'rollout.jsonl'
        self.add('session_meta',{'id':'thread','source':'cli'})
        self.reader=Timeline()
    def tearDown(self):self.temp.cleanup()
    def add(self,kind,payload):
        with self.path.open('a') as f:f.write(json.dumps({'type':kind,'payload':payload,'timestamp':'2026-01-01T00:00:00Z'})+'\n')
    def event(self,kind,**kw):self.add('event_msg',{'type':kind,**kw})
    def test_all_history_pagination_incremental_and_partial_append(self):
        for i in range(230):
            self.event('task_started',turn_id='turn'+str(i))
            self.event('user_message',message='request'+str(i))
        page=self.reader.read(self.path,'thread');self.assertEqual(len(page['messages']),100);self.assertTrue(page['hasMore'])
        old=self.reader.read(self.path,'thread',before=page['before']);older=self.reader.read(self.path,'thread',before=old['before'])
        self.assertEqual([m['text'] for m in older['messages']+old['messages']+page['messages']],['request'+str(i) for i in range(230)])
        cursor=page['cursor'];self.assertFalse(self.reader.read(self.path,'thread',after=cursor)['messages'])
        line=json.dumps({'type':'event_msg','payload':{'type':'agent_message','message':'complete','phase':'final_answer'}})
        with self.path.open('a') as f:f.write(line[:20])
        self.assertFalse(self.reader.read(self.path,'thread',after=cursor)['messages'])
        with self.path.open('a') as f:f.write(line[20:]+'\n')
        new=self.reader.read(self.path,'thread',after=cursor)
        self.assertEqual([m['text'] for m in new['messages']],['complete'])
        restarted=Timeline().read(self.path,'thread',after=cursor)
        self.assertEqual(restarted,new)
    def test_only_displayable_events_no_internal_reasoning_or_credentials(self):
        with self.path.open('a') as stream:stream.write('null\n42\n[]\n')
        self.event('item_completed',item=None)
        self.add('response_item',{'role':'developer','content':[{'type':'input_text','text':'internal secret'}]})
        self.event('agent_reasoning',text='hidden reasoning')
        self.event('exec_command_end',aggregated_output='credential')
        self.event('agent_message',message='正在查明',phase='commentary')
        self.event('agent_message',message='自然回答',phase='final_answer')
        self.event('turn_aborted',turn_id='failed')
        result=self.reader.read(self.path,'thread')
        self.assertEqual([m['text'] for m in result['messages'][:2]],['正在查明','自然回答'])
        self.assertEqual(result['messages'][-1]['phase'],'error')
        self.assertNotIn('credential',json.dumps(result))
    def test_duplicate_representation_not_duplicate_human_turn(self):
        self.event('task_started',turn_id='one');self.event('user_message',message='好')
        self.event('item_completed',item={'type':'UserMessage','id':'item1','content':[{'type':'text','text':'好'}]})
        self.event('task_started',turn_id='two');self.event('user_message',message='好')
        self.assertEqual(len(self.reader.read(self.path,'thread')['messages']),2)
    def test_legacy_planner_projects_natural_text_without_actions(self):
        self.event('user_message',message='[Pilot Brain 回合 old]\nPRIVATE CONTEXT\n本輪使用者原文：\n本人問題')
        self.event('agent_message',message=json.dumps({'reply':'自然回答','dispatches':[{'instruction':'DO NOT EXECUTE'}],'release':[]}),phase='final_answer')
        self.assertEqual([m['text'] for m in self.reader.read(self.path,'thread')['messages']],['本人問題','自然回答'])
    def test_wrong_thread_rejected_and_truncation_rebuilds(self):
        self.event('user_message',message='original')
        with self.assertRaises(ValueError):self.reader.read(self.path,'another')
        self.reader.read(self.path,'thread');self.path.write_text('')
        self.add('session_meta',{'id':'thread','source':'cli'});self.event('user_message',message='new')
        self.assertEqual([m['text'] for m in self.reader.read(self.path,'thread')['messages']],['new'])

    def test_same_item_id_in_distinct_turns_preserves_both(self):
        for turn in ('first','second'):
            self.event('task_started',turn_id=turn)
            self.event('agent_message',id='same-upstream-id',message='結果',phase='final_answer')
        rows=self.reader.read(self.path,'thread')['messages']
        self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['id'],rows[1]['id'])

    def test_claude_full_pages_hide_tools_and_sidechains(self):
        self.path.write_text('')
        with self.path.open('a') as f:
            for i in range(220):
                f.write(json.dumps({'sessionId':'claude-thread','uuid':str(i),'type':'assistant','message':{'role':'assistant','content':[{'type':'text','text':str(i)},{'type':'tool_use','input':{'secret':'hidden'}}]}})+'\n')
            f.write(json.dumps({'sessionId':'claude-thread','uuid':'child','type':'assistant','isSidechain':True,'message':{'role':'assistant','content':'hidden'}})+'\n')
        page=self.reader.read(self.path,'claude-thread',tool='claude')
        before=self.reader.read(self.path,'claude-thread',tool='claude',before=page['before'])
        self.assertEqual(len(page['messages']),100);self.assertEqual(before['messages'][-1]['text'],'119')
        self.assertNotIn('hidden',json.dumps(page));self.assertNotIn('tool_use',json.dumps(page))

if __name__=='__main__':unittest.main()
