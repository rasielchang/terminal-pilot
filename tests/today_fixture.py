"""Loopback-only real HTTP/UI fixture. All identities and cards are synthetic.

Run: python tests/today_fixture.py <new-empty-temporary-directory>
With --discussions, the real Brain queue runs against a synthetic backend and
writes delivery evidence. No model calls, Asana calls or real native bridge.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from web_server import WebApplication
from pilot_http import WebServer
from brain_unified import UnifiedBrain
from test_brain_unified import Backend
from test_web import FakeNative


def fixture(root, discussions=False):
    root.mkdir(parents=True, exist_ok=False)
    native=FakeNative()
    native.sessions=[{'id':'sample-a','binding':'fixture-a','bindingGeneration':'generation-a','title':'設計驗收',
        'codexThreadID':'sample-thread-a','tool':'codex','directory':'/synthetic/project','alive':True,'canSend':True,'phase':'complete'},
        {'id':'sample-b','binding':'fixture-b','bindingGeneration':'generation-b','title':'介面調整',
        'codexThreadID':'sample-thread-b','tool':'codex','directory':'/synthetic/project','alive':True,'canSend':True,'phase':'running'}]
    app=WebApplication(native,Path(__file__).resolve().parents[1]/'web',root/'codex')
    class RecordingBackend(Backend):
        def __call__(self, source):
            result = super().__call__(source)
            evidence = {'thread':self.data['thread'], 'deliveries':[
                {'text':s['userGoal'], 'reference':s.get('referenceTopic'), 'scope':s['scope']['id']} for s in self.sources],
                'jobs':len(app.brain._rows('jobs')), 'topics':len(app.brain._rows('topics')),
                'nativeActions':[method for method, _ in native.calls if method not in {'list','attention'}]}
            pending = root/'delivery.tmp';pending.write_text(json.dumps(evidence, ensure_ascii=False));pending.replace(root/'delivery.json')
            return result
    backend=RecordingBackend();backend.append('user','請安排今天的示範工作');backend.append('assistant','今天先核對設計，再看介面調整。','chat-turn','final_answer')
    app.brain=UnifiedBrain(root/'store',app.state,app.thread,app.send,backend=backend,timeline=backend.timeline,memory_root=root/'brain')
    app.thread=lambda sid,**kwargs: {'session':next(s for s in native.sessions if s['id']==sid),'messages':[{'id':'message-'+sid,'role':'assistant','text':'精確專員：'+sid}], 'hasMore':False}
    now=datetime.now().astimezone();date=now.date().isoformat()
    directory=root/'brain/projects/demo/work-items';directory.mkdir(parents=True)
    cards={
      'design':{'title':'確認首頁設計','status':'needs_user','result':'手機與桌面預覽已備妥。','next_action':'查看設計並回覆需要調整的地方。','thread':'sample-thread-a','session':'sample-a','waiting_on_user':{'action':'確認首頁設計','source_at':now.timestamp(),'release_condition':'本人在原專員回覆'},'due_on':date},
      'interface':{'title':'介面調整與回歸驗證','status':'in_progress','result':'核心流程已接通，正在驗證不同螢幕。','next_action':'專員完成驗證後回報。','thread':'sample-thread-b','session':'sample-b'},
      'waiting':{'title':'等待示範素材','status':'waiting_external','result':'需求已確認。','next_action':'收到素材後接續。','waiting_on':'設計窗口提供素材'},
      'done':{'title':'整理今日草稿','status':'completed','result':'草稿已確認完成。','next_action':'今天無需再處理。','completed_at':now.isoformat(),'thread':'closed-thread','session':'closed-session'},
      'small':{'title':'補上一個獨立小項','status':'pending','agenda':{},'result':'小項已記錄。','next_action':'下次討論時確認。','thread':'sample-thread-b','session':'sample-b'},
      'external':{'title':'等待示範窗口回覆','status':'waiting_external','agenda':{},'result':'已送出樣本。','next_action':'收到回覆後核對。','waiting_on':'示範窗口','asana_url':'https://app.asana.com/0/example/example'},
      'scheduled':{'title':'指定日期再核對','status':'deferred','agenda':{},'remind_on':(now+timedelta(days=3)).date().isoformat(),'result':'已保留資料。','next_action':'指定日核對。'},
      'weekly':{'title':'每週檢查示範資料','status':'recurring','agenda':{},'schedule':'每週一次','result':'本週已核對。','next_action':'下週依原安排追蹤。'},
      'future':{'title':'未來安排的示範工作','status':'pending','agenda':{'date':(now+timedelta(days=2)).date().isoformat(),'rank':1},'result':'安排已保存。','next_action':'指定日再處理。'},
      'stopped':{'title':'不應復活的停止事項','status':'pending','tracking':'excluded_until_user_reopens','agenda':{}},
      'cancelled':{'title':'不應復活的取消事項','status':'cancelled','agenda':{}},
      'old':{'title':'不應復活的歷史完成','status':'completed','completed_on':(now-timedelta(days=1)).date().isoformat(),'agenda':{}},
    }
    for index,(name,card) in enumerate(cards.items()):
        (directory/(name+'.json')).write_text(json.dumps({'updated_at':now.isoformat(),'user_source':'合成驗收資料','agenda':{'date':date,'rank':index,'reason':'隔離測試安排'},**card},ensure_ascii=False))
    server=WebServer(('127.0.0.1',0),app)
    if discussions: app.brain.start()
    config={'url':'http://127.0.0.1:'+str(server.server_port),'token':app.token,'root':str(root)}
    (root/'fixture.json').write_text(json.dumps(config));(root/'fixture.json').chmod(0o600)
    print('Synthetic fixture ready on port '+str(server.server_port),flush=True)
    try: server.serve_forever()
    finally: server.server_close();app.brain.close()

if __name__=='__main__': fixture(Path(sys.argv[1]), '--discussions' in sys.argv[2:])
