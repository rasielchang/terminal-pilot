"""Read-only, incremental projection of visible events in one exact Codex rollout.

The rollout is the authority. This index contains no model/tool output, cannot
execute actions, and is rebuilt after replacement/truncation. Cursors are stable
byte offsets scoped to a thread; incomplete writes are retried on the next read.
"""
from collections import OrderedDict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import threading


def content(parts):
    if isinstance(parts, str): return parts
    if not isinstance(parts,list):return ''
    return '\n'.join(p.get('text', '') for p in parts or [] if isinstance(p, dict)
                     and p.get('type', '').lower() in {'text', 'input_text', 'output_text'})


def visible(record, offset, thread, turn):
    payload = record.get('payload', {})
    if record.get('type') != 'event_msg' or not isinstance(payload, dict): return None
    kind = payload.get('type')
    item = payload.get('item', {})
    if kind == 'item_completed':
        if not isinstance(item,dict):return None
        role = {'UserMessage': 'user', 'AgentMessage': 'assistant'}.get(item.get('type'))
        text, identity, phase = content(item.get('content')), item.get('id'), item.get('phase')
    elif kind in {'user_message', 'agent_message'}:
        role = 'user' if kind == 'user_message' else 'assistant'
        text, identity, phase = payload.get('message', ''), payload.get('id'), payload.get('phase')
    elif kind in {'turn_aborted', 'error'}:
        role, identity, phase = 'system', None, 'error'
        text = '這一回合已中斷，尚未完成。' if kind == 'turn_aborted' else '這一回合發生錯誤，請查看原對話及連線狀態。'
    else: return None
    if not role or not isinstance(text, str) or not text.strip(): return None
    # Historic planner prompts contain internal context. Only their original
    # human input and natural replies belong in the visible conversation.
    if role == 'user' and text.startswith('[Pilot Brain 回合 '):
        if '\n本輪使用者原文：\n' not in text: return None
        text = text.split('\n本輪使用者原文：\n', 1)[1]
    if role == 'user' and text.startswith('[Pilot 工作事件 '): return None
    if role == 'assistant' and text.lstrip().startswith('{'):
        try:
            old = json.loads(text)
            if isinstance(old, dict) and {'reply', 'dispatches', 'release'} <= old.keys():
                text = '\n\n'.join([old['reply'], *[t.get('reply', '') for t in old.get('topics', []) if t.get('reply')]])
        except (ValueError, TypeError): pass
    stamp = record.get('timestamp', '')
    try: created = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
    except (ValueError, AttributeError): created = 0
    identity = hashlib.sha256((thread + ':' + (payload.get('turn_id') or turn) + ':' + (identity or str(offset))).encode()).hexdigest()[:32]
    return {'id': identity, 'cursor': offset + 1, 'thread': thread, 'turn': payload.get('turn_id') or turn,
            'role': role, 'text': text, 'phase': phase or '', 'kind': phase or 'reply',
            'time': stamp, 'created': created, 'representation': kind}


def claude_visible(record, offset, thread):
    if record.get('sessionId') != thread or record.get('isSidechain') or record.get('isMeta') or record.get('isCompactSummary'): return None
    role = record.get('type'); message = record.get('message', {})
    if role not in {'user','assistant'} or not isinstance(message,dict) or message.get('role') != role: return None
    text = content(message.get('content'))
    if not text.strip() or (role=='user' and text.lstrip().startswith(('<local-command-caveat>','<command-name>','<local-command-stdout>'))): return None
    if not record.get('uuid'): return None
    return {'id':record['uuid'],'cursor':offset+1,'thread':thread,'turn':record.get('requestId',''),
            'role':role,'text':text,'time':record.get('timestamp',''),'phase':'','representation':'claude'}


class Timeline:
    def __init__(self):
        self.lock = threading.RLock()
        self.files = OrderedDict()

    def read(self, path, thread, *, before=None, after=None, limit=100, tool="codex"):
        if not isinstance(thread, str) or not thread: raise ValueError('缺少精確對話。')
        if before is not None and (type(before) is not int or before <= 0): raise ValueError('歷史游標無效。')
        if after is not None and (type(after) is not int or after < 0): raise ValueError('增量游標無效。')
        path = Path(path)
        with self.lock, path.open('rb') as stream:
            stat = path.stat(); key = (str(path), thread); identity = (stat.st_dev, stat.st_ino)
            cache = self.files.get(key)
            if not cache or cache['identity'] != identity or cache['offset'] > stat.st_size:
                if tool == 'codex':
                    meta = json.loads(stream.readline()).get('payload', {})
                    if meta.get('id', meta.get('session_id')) != thread or meta.get('parent_thread_id') or isinstance(meta.get('source'), dict):
                        raise ValueError('歷史不是指定的主對話。')
                cache = {'identity': identity, 'offset': stream.tell(), 'items': [], 'ids': set(), 'turn': '', 'revision': hashlib.sha256((str(identity)+str(stat.st_mtime_ns if cache else getattr(stat,'st_birthtime',0))).encode()).hexdigest()[:16]}
            stream.seek(cache['offset'])
            while True:
                offset = stream.tell(); line = stream.readline()
                if not line or not line.endswith(b'\n'): break
                cache['offset'] = stream.tell()
                try: record = json.loads(line)
                except (ValueError, UnicodeError): continue
                if not isinstance(record,dict):continue
                p = record.get('payload', {})
                if isinstance(p, dict):
                    if record.get('type') == 'turn_context': cache['turn'] = p.get('turn_id', cache['turn'])
                    if record.get('type') == 'event_msg' and p.get('type') == 'task_started': cache['turn'] = p.get('turn_id', '')
                item = claude_visible(record,offset,thread) if tool=='claude' else visible(record, offset, thread, cache['turn'])
                if not item or item['id'] in cache['ids']: continue
                # Some Codex versions emit both legacy and item_completed UI
                # events for the same message. Collapse only adjacent copies in
                # the same turn; repeated human input in another turn is real.
                previous = cache['items'][-1] if cache['items'] else None
                if previous and previous['representation'] != item['representation'] and all(previous[k] == item[k] for k in ('role', 'text', 'turn', 'phase', 'time')):
                    continue
                cache['items'].append(item); cache['ids'].add(item['id'])
            self.files[key] = cache; self.files.move_to_end(key)
            while len(self.files) > 16: self.files.popitem(last=False)
            items = cache['items']; eligible = [m for m in items if (before is None or m['cursor'] < before) and (after is None or m['cursor'] > after)]
            page = eligible[:limit] if after is not None else eligible[-limit:]
            return {'messages': page, 'hasMore': len(eligible) > limit,
                    'before': page[0]['cursor'] if page else before,
                    'cursor': page[-1]['cursor'] if page else after or 0,
                    'latestCursor': items[-1]['cursor'] if items else 0, 'thread': thread, 'revision':cache['revision']}
