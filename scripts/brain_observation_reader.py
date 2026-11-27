"""Bounded restart-safe source scanner; does not retain whole histories in memory."""
import hashlib
import json
import shlex
import sys
from pathlib import Path
from brain_timeline import visible, claude_main_record, claude_visible


def report_poll(part):
    """Only a direct, read-only invocation of this installation's report CLI."""
    command = part.get('input')
    command = command.get('command') if isinstance(command, dict) else None
    if part.get('name') != 'Bash' or not isinstance(command, str) or '\n' in command:
        return None
    try:
        argv = shlex.split(command)
        if argv[:1] == ['sleep'] and len(argv) > 3 and argv[2] == '&&':
            if not 0 <= float(argv[1]) <= 600:
                return None
            argv = argv[3:]
        if (len(argv) == 6 and argv[1] == '-B' and argv[3:5] == ['report-status', '--report']
                and Path(argv[0]).resolve() == Path(sys.executable).resolve()
                and Path(argv[2]).resolve() == Path(__file__).with_name('brain_tools.py').resolve()):
            return argv[5]
    except (ValueError, OSError, RuntimeError):
        pass
    return None


def claude_tool_activity(record, thread, cursor):
    if record.get('type') != 'assistant' or not record.get('uuid') or not claude_main_record(record, thread):
        return None
    message = record.get('message')
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return None
    identity = message.get('id')
    parts = message.get('content')
    if not isinstance(identity, str) or not identity or not isinstance(parts, list):
        return None
    tools = [part for part in parts if isinstance(part, dict) and part.get('type') == 'tool_use']
    if tools:
        # A mixed message can contain actual work. Store the minimum metadata
        # needed to rule it out, including when its blocks span scanner pages.
        polls = [report_poll(part) for part in tools]
        return {'messageID': identity, 'cursor': cursor,
                'reportPoll': polls[0] if all(poll == polls[0] for poll in polls) else None}
    return None


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
        # Metadata timestamps may be unchanged for rapid rewrites (notably on
        # Windows). A verified tail mismatch still advances a persisted epoch.
        serial = state.get('epochSerial',0) + int(reset)
        epoch = state.get('revision') if not reset else key+':'+str(stat.st_mtime_ns)+':'+str(serial)
        if tool == 'codex':
            stream.seek(0)
            meta = json.loads(stream.readline(256000)).get('payload',{})
            if meta.get('id',meta.get('session_id')) != thread or meta.get('parent_thread_id') or isinstance(meta.get('source'),dict):
                raise ValueError('原來源不是指定的主對話。')
            start = stream.tell()
        else: start = 0
        if reset: offset = start; state = {'turn':'','recent':[]}
        stream.seek(offset)
        messages, activity, scanned = [], [], 0
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
            if tool == 'claude':
                marker = claude_tool_activity(record, thread, before + 1)
                if marker:
                    activity.append(marker)
                item = claude_visible(record, before, thread)
            else:
                item = visible(record, before, thread, state.get('turn', ''))
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
        return {'thread':thread,'revision':epoch,'reset':reset,'messages':messages,'latestCursor':stat.st_size,
                'hasMore':offset<stat.st_size,'scannedBytes':scanned,'claudeToolActivity':activity,
                'reader':{'file':key,'revision':epoch,'epochSerial':serial,'offset':offset,'turn':state.get('turn',''),
                          'tail':tail,'recent':recent,'previous':previous}}
