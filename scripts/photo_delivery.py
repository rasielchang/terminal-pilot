"""Exact-thread image submission with durable no-replay fences."""
import hashlib
import json
import re
import threading
import time
from photo_store import scope,wire,claude_wire


class PhotoDelivery:
    def __init__(self,app):
        self.app=app;self.store=app.photos;self.lock=threading.RLock();self.stop_event=threading.Event()
    def send(self,data,session,*,origin='manager'):
        ids=data['photos'];target=scope(session);paths=self.store.paths(ids,target)
        identity=data['requestID']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identity):raise ValueError('照片送出識別碼不正確。')
        fingerprint=hashlib.sha256(json.dumps([target,session['binding'],data['text'],ids]).encode()).hexdigest()
        # Match the text sender's lock order: Brain -> delivery, never reverse.
        if self.app.brain and origin!='brain' and not self.store.receipt(identity):
            self.app.brain.manual_send(session['id'])
        with self.lock:
            old=self.store.receipt(identity)
            if old:
                if old['fingerprint']!=fingerprint:raise ValueError('此送出識別碼已用於不同內容或對話。')
                return self.advance(old)
            if session.get('phase') in {'running','starting','waiting','ended','error'} or not session.get('canSend'):
                raise ValueError('請等原工作空下來或完成正式確認後再送照片。')
            if session['tool']=='codex':
                if not self.app.live:raise ValueError('Codex圖像通道尚未接通。')
                # Read-only readiness check before committing an ambiguous write.
                self.app.live.brain_call(session,'thread/queue/list',{'threadId':session['codexThreadID']})
            native_wire=claude_wire(data['text'],ids,paths,identity) if session['tool']=='claude' else ''
            if len(native_wire)>8000:raise ValueError('照片加上文字過長，請縮短回覆後重試。')
            record={'id':identity,'fingerprint':fingerprint,'scope':target,'session':session['id'],'binding':session['binding'],
                    'thread':session.get('codexThreadID') or session.get('claudeSessionID'),'tool':session['tool'],'wire':native_wire,'state':'adding','created':time.time(),'photos':ids}
            self.store.save(record)
            if session['tool']=='claude':
                try:
                    result=self.app.native.call('send',{'session':session['id'],'binding':session['binding'],'text':native_wire})
                    if result.get('sent'):record.update(state='sent');self.store.save(record)
                    return {**result,'photosSubmitted':bool(result.get('sent')),'imageRead':'pending' if result.get('sent') else 'unknown'}
                except (ValueError,OSError):return {'sent':False,'uncertain':True,'requestID':identity}
            try:
                result=self.app.live.brain_call(session,'thread/queue/add',{'threadId':record['thread'],
                    'clientUserMessageId':identity,'input':[{'type':'text','text':wire(data['text'],ids,identity)},*[{'type':'localImage','path':p} for p in paths]]})
                record.update(state='queued',queue=result['queuedSubmission']['id']);self.store.save(record)
            except (ValueError,OSError):return {'sent':False,'uncertain':True,'requestID':identity}
            return self.advance(record)
    def advance(self,record):
        if record['state']=='sent':return {'sent':True,'turn':record.get('turn'),'requestID':record['id']}
        sessions=self.app.native.call('list').get('sessions',[])
        session=next((s for s in sessions if s['id']==record['session']),None)
        if not session or session.get('binding')!=record['binding'] or scope(session)!=record['scope']:
            return {'sent':False,'uncertain':True,'error':'原對話綁定已變更；不改送其他工作。'}
        try:
            # A bounded original-thread read also handles Codex versions whose
            # live queue supports images but paginated RPC history is unavailable.
            page=self.app.thread_page(session['id'])
            found=next((m for m in page['messages'] if m.get('role')=='user' and m.get('photoRequest')==record['id']),None)
            if found:
                record.update(state='sent',turn=found.get('turn'));self.store.save(record)
                return {'sent':True,'turn':found.get('turn'),'requestID':record['id']}
        except (ValueError,OSError):pass
        if record['tool']=='claude':
            return {'sent':False,'uncertain':True,'requestID':record['id']}  # Native ambiguity is never retyped.
        rpc=lambda method,args:self.app.live.brain_call(session,method,{'threadId':record['thread'],**args})
        try:
            queued=rpc('thread/queue/list',{}).get('data',[])
            match=next((q for q in queued if q.get('clientUserMessageId')==record['id']),None)
            if match:
                record.update(state='queued',queue=match['id']);self.store.save(record)
                if queued[0]['id']==match['id'] and session.get('phase') not in {'running','starting','waiting'}:
                    result=rpc('thread/queue/start',{'queuedSubmissionId':match['id']})
                    record.update(state='sent',turn=result['turn']['id']);self.store.save(record)
                return {'sent':True,'queued':record['state']=='queued','requestID':record['id']}
            cursor=None
            for _ in range(8):
                page=rpc('thread/turns/list',{'limit':1,'sortDirection':'desc','itemsView':'summary',**({'cursor':cursor} if cursor else {})})
                found=next((t for t in page.get('data',[]) if any(i.get('type')=='userMessage' and i.get('clientId')==record['id'] for i in t.get('items',[]))),None)
                if found:
                    record.update(state='sent',turn=found['id']);self.store.save(record)
                    return {'sent':True,'turn':found['id'],'requestID':record['id']}
                cursor=page.get('nextCursor')
                if not cursor:break
        except (ValueError,OSError):pass
        if record['state']=='queued':
            # The durable add receipt proves acceptance even if this CLI already
            # consumed the queue and its local history has not caught up yet.
            return {'sent':True,'queued':True,'requestID':record['id']}
        return {'sent':False,'uncertain':True,'requestID':record['id']}
    def run(self):
        while not self.stop_event.wait(2):
            with self.store.lock:records=[json.loads(r[0]) for r in self.store.db.execute('SELECT data FROM sends')]
            for r in records:
                if r['state'] not in {'queued','adding'}:continue
                try:
                    with self.lock:self.advance(r)
                except (OSError,ValueError):pass
