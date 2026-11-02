"""Bounded restart-safe source scanner; does not retain whole histories in memory."""
import hashlib
import json
from pathlib import Path
from brain_timeline import visible, claude_visible


def read_page(path, thread, tool, reader=None, *, max_records=1000, max_messages=100):
    path = Path(path)
    with path.open('rb') as stream:
        stat = path.stat()
        key = hashlib.sha256(str((stat.st_dev,stat.st_ino,getattr(stat,'st_birthtime_ns',getattr(stat,'st_birthtime',0)))).encode()).hexdigest()[:24]
        state = dict(reader or {})
        offset = state.get('offset',0)
        reset = state.get('file') != key or offset > stat.st_size
        if not reset and offset:
            stream.seek(max(0,offset-128))
            reset = hashlib.sha256(stream.read(min(offset,128))).hexdigest() != state.get('tail')
        epoch = state.get('revision') if not reset else key+':'+str(stat.st_mtime_ns)
        if tool == 'codex':
            stream.seek(0)
            meta = json.loads(stream.readline(256000)).get('payload',{})
            if meta.get('id',meta.get('session_id')) != thread or meta.get('parent_thread_id') or isinstance(meta.get('source'),dict):
                raise ValueError('原來源不是指定的主對話。')
            start = stream.tell()
        else: start = 0
        if reset: offset = start; state = {'turn':'','recent':[]}
        stream.seek(offset)
        messages, scanned = [], 0
        recent = list(state.get('recent',[]))
        previous = state.get('previous')
        for _ in range(max_records):
            before = stream.tell()
            line = stream.readline(8_000_001)
            if len(line) > 8_000_000: raise ValueError('來源單筆紀錄超過讀取上限，需人工確認。')
            if not line or not line.endswith(b'\n'):
                stream.seek(before); break
            offset = stream.tell(); scanned += len(line)
            try: record = json.loads(line)
            except (ValueError,UnicodeError):
                # Do not skip a malformed complete source line and silently claim full coverage.
                raise ValueError('來源紀錄無法解析，未略過此缺口。') from None
            if not isinstance(record,dict): raise ValueError('來源格式無效。')
            p = record.get('payload',{})
            if isinstance(p,dict) and (record.get('type')=='turn_context' or
                    record.get('type')=='event_msg' and p.get('type')=='task_started'):
                state['turn'] = p.get('turn_id','')
            item = claude_visible(record,before,thread) if tool=='claude' else visible(record,before,thread,state.get('turn',''))
            if item:
                fingerprint = hashlib.sha256(json.dumps([item.get(k) for k in ('role','text','turn','phase','time')],sort_keys=True).encode()).hexdigest()
                duplicate = item['id'] in recent or (previous and previous['representation']!=item['representation'] and previous['fingerprint']==fingerprint)
                if not duplicate:
                    messages.append(item)
                    previous={'representation':item['representation'],'fingerprint':fingerprint}
                    recent=(recent+[item['id']])[-128:]
            if len(messages)>=max_messages or scanned>=1_000_000: break
        stream.seek(max(0,offset-128))
        tail = hashlib.sha256(stream.read(min(offset,128))).hexdigest()
        return {'thread':thread,'revision':epoch,'messages':messages,'latestCursor':stat.st_size,
                'hasMore':offset<stat.st_size,'scannedBytes':scanned,
                'reader':{'file':key,'revision':epoch,'offset':offset,'turn':state.get('turn',''),
                          'tail':tail,'recent':recent,'previous':previous}}
