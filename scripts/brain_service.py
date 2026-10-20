"""Durable coordination over Pilot's existing, precisely bound agent adapters.

SQLite is the dispatch journal, never a replacement for Terminal/agent state.
No terminal input is retried after a potentially delivered write.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from brain_provider import validate_plan
from brain_codex import BrainPending

ACTIVE = {"queued", "dispatching", "sent", "running", "observing", "waiting", "uncertain"}
FINAL = {"completed", "blocked", "manual"}
LABELS = {"queued": "等待工作空下來", "dispatching": "正在交辦", "sent": "等待 agent 接收",
          "running": "正在處理", "observing": "等待完成確認", "waiting": "需要你的決定", "uncertain": "送出結果待確認",
          "completed": "已收到結果", "blocked": "需要你處理", "manual": "已交回手動處理"}


def conversation_id(s):
    return s.get("codexThreadID") or s.get("claudeSessionID") or ""


def running(s):
    return s.get("phase") in {"running", "starting"} or s.get("runtime", {}).get("state") == "running" or s.get("turnProgress", {}).get("state") == "active"


def waiting(s):
    return bool(s.get("runtime", {}).get("approvals")) or s.get("phase") == "waiting"


def human_wait(s):
    return waiting(s) and (s.get("runtime", {}).get("approvals") or
        s.get("approvalReviewer") not in {"auto_review", "guardian_subagent"})


def read_memory(root):
    """Explicit small Markdown surface; no logs, credentials, recursive crawl or symlinks."""
    root = Path(root).expanduser()
    if root.is_symlink() or not root.is_dir(): return []
    paths = [root / "AGENTS.md", root / "projects/brain/operating-rules.md", root / "reminders.md"]
    projects = root / "projects"
    if projects.is_dir() and not projects.is_symlink():
        for project in sorted(projects.iterdir())[:40]:
            if project.is_dir() and not project.is_symlink() and not project.name.startswith("_"):
                paths.extend(project / name for name in ("brief.md", "status.md"))
    files, remaining = [], 20000
    for path in paths:
        if remaining <= 0: break
        ancestors = list(path.parents)[:len(path.relative_to(root).parts)]
        if any(p.is_symlink() for p in [path, *ancestors]) or not path.is_file(): continue
        try:
            with path.open() as stream: text = stream.read(min(4000, remaining))
        except (OSError, UnicodeError): continue
        files.append({"source": str(path.relative_to(root)), "text": text, "partial": path.stat().st_size > len(text.encode())})
        remaining -= len(text)
    return files


class BrainService:
    def __init__(self, store, state, thread, send, *, planner=None, backend=None, memory_root=None, clock=time.time):
        self.get_state, self.get_thread, self.send = state, thread, send
        self.backend = backend
        self.planner, self.clock = planner or backend, clock
        self.memory_root = Path(memory_root or Path.home() / "brain")
        self.lock, self.wake, self.stop_event = threading.RLock(), threading.Event(), threading.Event()
        self.worker = None
        path = Path(store) / "brain.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600); os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, role TEXT NOT NULL, text TEXT NOT NULL,
            kind TEXT NOT NULL, created REAL NOT NULL, request TEXT UNIQUE);
          CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS topics(id TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
        """)
        columns = {r[1] for r in self.db.execute('PRAGMA table_info(messages)')}
        if 'topic' not in columns: self.db.execute("ALTER TABLE messages ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
        if 'links' not in columns: self.db.execute("ALTER TABLE messages ADD COLUMN links TEXT NOT NULL DEFAULT '[]'")
        self.db.execute('CREATE INDEX IF NOT EXISTS messages_topic ON messages(topic,id)')
        # A killed planner cannot have committed a partial batch. Retrying model
        # inference is safe; retrying a terminal write is not.
        with self.lock, self.db:
            for turn in self._rows("turns"):
                if turn["state"] == "planning":
                    turn["state"] = "queued"; self._save("turns", turn)
            for job in self._rows("jobs"):
                if job["state"] == "dispatching":
                    job["state"] = "uncertain"; self._save("jobs", job)
            if backend and not self._get('codexMigration'):
                for turn in self._rows('turns'):
                    if turn['state'] == 'failed' and turn['trigger'].get('kind') == 'result':
                        trigger = turn['trigger']
                        self._message(f"「{trigger.get('title', '工作')}」先前已回報；以下是 agent 原始結果，尚未由 Brain 整合：\n\n{trigger.get('result', '')}", kind='result')
                self._set('codexMigration', True)
            if not self._get('topicsMigration'):
                grouped = {}
                for job in self._rows('jobs'):
                    if job['goal'] not in grouped:
                        row = self.db.execute('SELECT text FROM messages WHERE id=?', (job['goal'],)).fetchone()
                        goal = row[0] if row else job['instruction']
                        topic = {'id': str(uuid.uuid4()), 'title': goal[:44], 'goal': goal, 'state': 'open',
                                 'created': job['created'], 'updated': job['created']}
                        grouped[job['goal']] = topic
                        self._save('topics', topic)
                        self._message(goal, role='user', topic=topic['id'])
                        self.db.execute('UPDATE messages SET links=? WHERE id=?', (json.dumps([topic['id']]), job['goal']))
                    job['topic'] = grouped[job['goal']]['id']; self._save('jobs', job)
                    if job.get('result'): self._message(job['result'], kind='archive', topic=job['topic'])
                for turn in self._rows('turns'):
                    if turn['goal'] in grouped: turn['topic'] = grouped[turn['goal']]['id']; self._save('turns', turn)
                self._set('topicsMigration', True)

    def _rows(self, table):
        return [json.loads(row[0]) for row in self.db.execute(f"SELECT data FROM {table} ORDER BY rowid")]

    def _save(self, table, value):
        self.db.execute(f"INSERT OR REPLACE INTO {table}(id,data) VALUES(?,?)", (value["id"], json.dumps(value, ensure_ascii=False)))

    def _get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def _message(self, text, *, role="assistant", kind="reply", request=None, topic='', links=None):
        cursor = self.db.execute("INSERT INTO messages(role,text,kind,created,request,topic,links) VALUES(?,?,?,?,?,?,?)",
                                 (role, text, kind, self.clock(), request, topic, json.dumps(links or [])))
        if topic:
            value = self._topic(topic)
            value['updated'] = self.clock(); self._save('topics', value)
        return cursor.lastrowid

    def _topic(self, identity):
        if not isinstance(identity, str) or len(identity) > 80: raise ValueError('事情識別碼不正確。')
        if not identity: return {'id': '', 'title': '主對話', 'goal': ''}
        row = self.db.execute('SELECT data FROM topics WHERE id=?', (identity,)).fetchone()
        if not row: raise ValueError('找不到這件事，請回主對話重新選擇。')
        return json.loads(row[0])

    def create_topic(self, data):
        if set(data) != {'requestID', 'title'} or not isinstance(data['title'], str) or not 0 < len(data['title'].strip()) <= 100:
            raise ValueError('請為這件事輸入 1 至 100 字的名稱。')
        with self.lock, self.db:
            fingerprint, old = self._receipt(data)
            if old: return old
            value = {'id': str(uuid.uuid4()), 'title': data['title'].strip(), 'goal': '', 'state': 'open',
                     'created': self.clock(), 'updated': self.clock()}
            self._save('topics', value)
            return self._record(data, fingerprint, {'topic': value})

    def _report(self, text, *, topic='', kind='result'):
        identity = self._message(text, kind=kind, topic=topic)
        if topic and kind in {'result', 'error', 'question'}:
            label = {'result': '有新的結果', 'error': '遇到問題', 'question': '需要你的決定'}[kind]
            self._message(f"「{self._topic(topic)['title']}」{label}。", kind='update', links=[topic])
        return identity

    def _receipt(self, data):
        request = data.get("requestID")
        if not isinstance(request, str) or not 0 < len(request) <= 80: raise ValueError("缺少請求識別碼。")
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        row = self.db.execute("SELECT fingerprint,result FROM receipts WHERE id=?", (request,)).fetchone()
        if row and row[0] != fingerprint: raise ValueError("識別碼已用於另一個操作。")
        return fingerprint, json.loads(row[1]) if row else None

    def _record(self, data, fingerprint, result):
        self.db.execute("INSERT INTO receipts VALUES(?,?,?)", (data["requestID"], fingerprint, json.dumps(result)))
        return result

    def submit(self, data):
        if not {'requestID', 'text'} <= set(data) or set(data) - {'requestID', 'text', 'topic', 'source'}: raise ValueError("Brain 訊息欄位不正確。")
        text = data["text"]
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 8000 or any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in text):
            raise ValueError("請輸入 1 至 8,000 字的交代。")
        with self.lock, self.db:
            fingerprint, old = self._receipt(data)
            if old: return old
            topic = self._topic(data.get('topic', ''))
            source = None
            if 'source' in data:
                source = next((j for j in self._rows('jobs') if j['id'] == data['source']), None)
                if not source or (topic['id'] and source.get('topic') != topic['id']): raise ValueError('引用的回報不屬於目前這件事。')
            if sum(t["state"] in {"queued", "planning", "codex_wait"} for t in self._rows("turns")) >= 8:
                raise ValueError("Brain 還在整理前面的交代，請稍候。")
            message = self._message(text.strip(), role="user", request=data["requestID"], topic=topic['id'])
            if topic['id'] and not topic['goal']:
                topic['goal'] = text.strip(); self._save('topics', topic)
            self._set("enabled", True)
            self._save("turns", {"id": str(uuid.uuid4()), "goal": message, "topic": topic['id'], "source": source['id'] if source else '', "state": "queued", "trigger": {"kind": "user"}})
            result = self._record(data, fingerprint, {"accepted": True, "messageID": message})
        self.wake.set()
        return result

    def open_backend(self, data):
        if set(data) != {'requestID'} or self.backend is None:
            raise ValueError('Brain Codex 尚未設定。')
        with self.lock:
            fingerprint, old = self._receipt(data)
            if old: return old
        try: result = self.backend.open()
        except BrainPending: return {'opened': False, 'starting': True}
        with self.lock, self.db:
            _, old = self._receipt(data)
            return old or self._record(data, fingerprint, result)

    def control(self, data):
        action = data.get("action")
        if action not in {"pause", "resume", "takeover"} or set(data) != ({"requestID", "action", "job"} if action == "takeover" else {"requestID", "action"}):
            raise ValueError("Brain 控制操作不正確。")
        with self.lock, self.db:
            fingerprint, old = self._receipt(data)
            if old: return old
            if action == "takeover":
                job = next((j for j in self._rows("jobs") if j["id"] == data["job"]), None)
                if not job: raise ValueError("找不到這筆交辦。")
                self._manual(job["session"], "你已接手處理，Brain 不再對這個工作自動續派。")
            else:
                self._set("paused", action == "pause")
                self._message("已暫停新的交辦。正在執行的 agent 仍會繼續，我會保留收到的結果。" if action == "pause" else "已恢復交辦；送出結果不明的指令不會重送。", kind="system")
            result = self._record(data, fingerprint, {"ok": True})
        self.wake.set()
        return result

    def _manual(self, session, reason):
        changed, scopes = False, set()
        for job in self._rows("jobs"):
            if job["session"] == session and job["state"] not in {"manual", "blocked"}:
                self._set("manual:" + session, self.clock())
            if job["session"] == session and job["state"] in ACTIVE:
                job.update(state="manual", detail=reason, finished=self.clock())
                self._save("jobs", job); changed = True; scopes.add(job.get('topic', ''))
        if changed:
            for topic in scopes: self._message(reason, kind="system", topic=topic)
            # Fence in-flight and future result plans from reclaiming this session.
            self._set("manual:" + session, self.clock())

    def manual_send(self, session):
        with self.lock, self.db: self._manual(session, "你已從工作管理回覆，這個工作交回手動處理。")

    def acknowledge(self, data):
        if set(data) not in ({"through"}, {"through", "topic"}) or type(data["through"]) is not int or data["through"] < 0:
            raise ValueError("已讀識別碼不正確。")
        with self.lock, self.db:
            topic = self._topic(data.get('topic', ''))['id']
            last = self.db.execute("SELECT COALESCE(MAX(id),0) FROM messages WHERE topic=?", (topic,)).fetchone()[0]
            key = 'read:' + topic if topic else 'read'
            self._set(key, max(self._get(key, 0), min(last, data["through"])))
        return {"ok": True}

    def _topics(self, jobs, turns):
        values = []
        for topic in self._rows('topics'):
            latest = self.db.execute("SELECT id,text,role FROM messages WHERE topic=? ORDER BY id DESC LIMIT 1", (topic['id'],)).fetchone()
            notice = self.db.execute("SELECT MAX(id) FROM messages WHERE topic=? AND role='assistant' AND kind IN ('result','error','question')", (topic['id'],)).fetchone()[0] or 0
            active = [j for j in jobs if j.get('topic') == topic['id'] and j['state'] in ACTIVE]
            planning = any(t.get('topic') == topic['id'] and t['state'] in {'queued', 'planning', 'codex_wait'} for t in turns)
            values.append({**topic, 'preview': latest['text'][:160] if latest else '',
                           'unread': notice > self._get('read:' + topic['id'], 0), 'working': planning or bool(active),
                           'needsDecision': any(j['state'] in {'waiting', 'uncertain'} for j in active)})
        return sorted(values, key=lambda t: (t['unread'], t['state'] != 'done', t['updated']), reverse=True)

    def state(self, topic='', before=None):
        if before is not None and (type(before) is not int or before <= 0): raise ValueError('訊息游標不正確。')
        sessions = self.get_state().get("sessions", [])
        with self.lock:
            scope = self._topic(topic)
            rows = self.db.execute("SELECT id,role,text,kind,created,topic,links FROM messages WHERE topic=? AND id<? ORDER BY id DESC LIMIT 151", (topic, before or 9223372036854775807)).fetchall()
            messages = [{**dict(row), 'links': json.loads(row['links'])} for row in rows[:150]][::-1]
            all_jobs, turns = self._rows('jobs'), self._rows('turns')
            jobs = [{k: v for k, v in j.items() if k not in {"wire", "baseline", "thread"}}
                    for j in all_jobs if not topic or j.get('topic') == topic][-80:]
            for job in jobs: job["label"] = LABELS[job["state"]]
            relevant = {j['session'] for j in all_jobs if j.get('topic') == topic and j['state'] in ACTIVE}
            if self.backend and any(t.get('topic', '') == topic and t['state'] == 'codex_wait' for t in turns): relevant.add(self.backend.session_id)
            decisions = [{"session": s, "requests": s.get("runtime", {}).get("approvals", [])}
                         for s in sessions if human_wait(s) and s.get("alive", True) and (not topic or s['id'] in relevant)]
            return {"messages": messages, "hasMore": len(rows) > 150, "topic": scope,
                    "topics": self._topics(all_jobs, turns), "jobs": jobs, "decisions": decisions,
                    "backend": self.backend.snapshot(sessions) if self.backend else None,
                    "paused": self._get("paused", False),
                    "working": any(t.get('topic', '') == topic and t["state"] in {"queued", "planning", "codex_wait"} for t in turns),
                    "unreadThrough": self._get('read:' + topic if topic else 'read', 0),
                    "memory": {"available": self.memory_root.is_dir(), "path": str(self.memory_root)},
                    "error": self._get("connectionError", "")}

    def source(self, identity):
        with self.lock:
            job = next((j for j in self._rows('jobs') if j['id'] == identity), None)
            if not job: raise ValueError('找不到這筆交辦來源。')
            result = {'job': job['id'], 'title': job['title'], 'topic': job.get('topic', ''),
                      'instruction': job['instruction'], 'result': job.get('result', ''), 'messages': [], 'available': False}
        try:
            current = next((s for s in self.get_state().get('sessions', []) if s['id'] == job['session']), None)
            if (not current or not current.get('alive', True) or current.get('binding') != job['binding']
                    or conversation_id(current) != job['thread']): return result
            thread = self.get_thread(job['session'])
            s = thread['session']
            if s.get('binding') != job['binding'] or conversation_id(s) != job['thread'] or not s.get('alive', True): return result
            result.update(messages=thread['messages'], available=True, session=job['session'])
        except (ValueError, OSError): pass
        return result

    def notification_state(self, native):
        """Scope acknowledgments independently, preserving native/manager notices."""
        with self.lock:
            jobs = self._rows("jobs")
            owned = {s["id"] for s in native.get("sessions", []) if any(j["session"] == s["id"] and
                     (j["state"] in ACTIVE or (j["state"] == "completed" and j.get("sourceNotice") == s.get("noticeID"))) for j in jobs)}
            if self.backend: owned.add(self.backend.session_id)
            sessions = [{**s, "unread": False} if s["id"] in owned else s for s in native.get("sessions", [])]
            for topic in ['', *[t['id'] for t in self._rows('topics')]]:
                latest = self.db.execute("SELECT id,created FROM messages WHERE topic=? AND role='assistant' AND kind IN ('result','error','question') ORDER BY id DESC LIMIT 1", (topic,)).fetchone()
                if latest:
                    sessions.append({"id": 'brain:' + topic if topic else 'brain', "title": self._topic(topic)['title'] if topic else 'Brain',
                                     "status": "有結果或決定需要你查看", "phase": "complete",
                                     "unread": latest[0] > self._get('read:' + topic if topic else 'read', 0),
                                     "noticeID": str(latest[0]), "noticeAt": latest[1]})
        return {**native, "sessions": sessions}

    def _context(self, turn, state):
        with self.lock:
            scope = self._topic(turn.get('topic', ''))
            goal_row = self.db.execute("SELECT text,created FROM messages WHERE id=?", (turn["goal"],)).fetchone()
            goal, turn["userCreated"] = goal_row
            if scope['id'] and turn['trigger']['kind'] == 'result': goal = scope['goal']
            conversation = [{"role": r[0], "text": r[1][:3000]} for r in self.db.execute("SELECT role,text FROM messages WHERE topic=? ORDER BY id DESC LIMIT 16", (scope['id'],))][::-1]
            jobs = self._rows("jobs")
            scoped_jobs = [j for j in jobs if not scope['id'] or j.get('topic') == scope['id']]
            topics = [scope] if scope['id'] else [{k: t[k][:1800] for k in ('id', 'title', 'goal', 'state', 'preview')} for t in self._topics(jobs, self._rows('turns'))[:80]]
            budget = max(0, 6 - sum(j["goal"] == turn["goal"] for j in jobs))
        candidates = [s for s in state.get("sessions", []) if s.get("tool") in {"codex", "claude"}
                      and (not self.backend or s['id'] != self.backend.session_id)
                      and s.get("binding") and s.get("alive", True) and s.get("phase") != "ended"]
        candidates.sort(key=lambda s: s.get("updatedAt", 0), reverse=True)
        contexts = []
        turn["baselines"] = {}
        for s in candidates[:24]:
            row = {k: s.get(k) for k in ("id", "title", "directory", "tool", "phase", "status")}
            try:
                thread = self.get_thread(s["id"])
                if thread["session"].get("binding") != s["binding"]: continue
                row["recent"] = [{"role": m["role"], "text": m["text"][:900], "phase": m.get("phase", "")} for m in thread["messages"][-2:]]
                user = next((m.get("text", "") for m in reversed(thread["messages"]) if m.get("role") == "user"), "")
                turn["baselines"][s["id"]] = hashlib.sha256(user.encode()).hexdigest()
            except (ValueError, OSError): row["recent"] = []
            contexts.append(row)
        source = {"requestID": turn['id'], "scope": scope, "topics": topics, "userGoal": goal, "conversation": conversation, "trigger": turn["trigger"], "dispatchBudget": budget,
                "releasableJobs": [j['id'] for j in scoped_jobs[-20:] if j['state'] in ACTIVE] if turn['trigger']['kind'] == 'user' else [],
                "sessions": contexts, "jobs": [{k: j.get(k, "")[:1800] if isinstance(j.get(k), str) else j.get(k) for k in ("id", "goal", "topic", "session", "title", "instruction", "state", "result", "detail")} for j in scoped_jobs[-20:]],
                "busySessions": [j['session'] for j in jobs if j['state'] in ACTIVE],
                "memory": read_memory(self.memory_root)}
        if turn.get('source'):
            ref = next((j for j in jobs if j['id'] == turn['source']), None)
            if ref:
                source['reference'] = {k: ref.get(k, '')[:12000] for k in ('id', 'title', 'instruction', 'result')}
                source['reference']['partial'] = len(ref.get('result', '')) > 12000
        # Bound UTF-8 bytes, not just character count. Keep the actual user goal
        # and triggering result intact while reducing ancillary excerpts first.
        for size in (1200, 600, 300, 120):
            if len(json.dumps(source, ensure_ascii=False).encode()) <= 210000: break
            source["contextPartial"] = True
            for topic in source['topics']:
                if topic['id'] != source['scope']['id']: topic['goal'] = topic['goal'][:size]
            for message in source["conversation"]: message["text"] = message["text"][:size]
            for context in source["sessions"]:
                for message in context["recent"]: message["text"] = message["text"][:size]
            for job in source["jobs"]:
                for key in ("instruction", "result", "detail"):
                    if isinstance(job.get(key), str): job[key] = job[key][:size]
            for memory in source["memory"]:
                if len(memory["text"]) > size: memory.update(text=memory["text"][:size], partial=True)
        return source

    def _topic_plan(self, plan, turn, source):
        scope = turn.get('topic', '')
        allowed = {t['id'] for t in source.get('topics', [])}
        updates, mapping = [], {}
        for entry in plan.get('topics', []):
            identity = entry['id']
            if scope and (identity != scope or entry['reply']):
                raise ValueError('這次討論只會更新目前這件事，跨事情安排請回主對話。')
            if identity.startswith('new:'):
                if scope or turn['trigger']['kind'] != 'user': raise ValueError('工作回報不能自行新增事情。')
                value = {'id': str(uuid.uuid4()), 'created': self.clock()}
            else:
                if identity not in allowed: raise ValueError('事情不在這次討論範圍內，沒有變更工作。')
                value = self._topic(identity)
            mapping[identity] = value['id']
            updates.append({**value, **{k: entry[k] for k in ('title', 'goal', 'state')}, 'updated': self.clock()})
        for action in plan['dispatches']:
            identity = action.get('topic', scope)
            if scope and identity != scope: raise ValueError('交辦不能送到另一件事。')
            if identity and identity not in allowed and identity not in mapping: raise ValueError('交辦缺少有效的事情來源。')
            if 'topics' in plan and not identity: raise ValueError('交辦必須先連到一件事情。')
        return updates, mapping

    def _plan_one(self, state):
        with self.lock, self.db:
            queued = [t for t in self._rows("turns") if t["state"] in {"queued", "codex_wait"}]
            turn = next((t for t in queued if t['state'] == 'codex_wait'), None)
            turn = turn or next((t for t in queued if t["trigger"]["kind"] == "user"), queued[0] if queued else None)
            if not turn: return
            turn.update(state="planning", started=self.clock()); self._save("turns", turn)
        try:
            if not self.planner: raise ValueError('Brain Codex 尚未連接。')
            source = turn.get('context') or self._context(turn, state)
            if self.backend and 'context' not in turn:
                turn.update(context=source, snapshot=state)
                with self.lock, self.db: self._save('turns', turn)
            state = turn.get('snapshot', state)
            plan = validate_plan(self.planner(source))
            # Entire batch validated against both the planning snapshot and fresh state.
            fresh = {s["id"]: s for s in self.get_state().get("sessions", [])}
            with self.lock, self.db:
                jobs = self._rows("jobs")
                topic_updates, topic_ids = self._topic_plan(plan, turn, source)
                budget = max(0, 6 - sum(j["goal"] == turn["goal"] for j in jobs))
                if len(plan["dispatches"]) > budget: raise ValueError("這次交辦已到自動續派上限，請確認結果後再交代下一步。")
                staged = []
                releases = [j for j in jobs if j["id"] in plan["release"]]
                if len(releases) != len(set(plan["release"])) or (releases and turn["trigger"]["kind"] != "user"):
                    raise ValueError("Brain 無法確認撤回交辦的來源，沒有變更工作。")
                if any(j['id'] not in source['releasableJobs'] or j['state'] not in ACTIVE for j in releases):
                    raise ValueError('要撤回的交辦狀態已改變，沒有變更工作。')
                if any(j["session"] == a["session"] for j in releases for a in plan["dispatches"]):
                    raise ValueError("同一個工作不能同時交回手動與自動派送。")
                for action in plan["dispatches"]:
                    if self.backend and action['session'] == self.backend.session_id:
                        raise ValueError('Brain 不會向自己重複派送工作。')
                    s = fresh.get(action["session"])
                    original = next((s for s in state.get("sessions", []) if s["id"] == action["session"]), None)
                    if (not s or not original or s.get("binding") != original.get("binding") or not s.get("binding")
                            or conversation_id(s) != conversation_id(original) or s.get("tool") not in {"codex", "claude"}
                            or not s.get("alive", True) or s.get("phase") in {"ended", "error"}):
                        raise ValueError("規劃時的工作已改變，Brain 沒有送出；請重新交代要處理的專案。")
                    if s["id"] not in turn["baselines"]: raise ValueError("未能確認這個工作目前的對話，沒有送出交辦。")
                    if any(j["session"] == s["id"] and j["state"] in ACTIVE for j in jobs):
                        raise ValueError("這個工作已有尚未完成的交辦，Brain 沒有重複送出。")
                    if any(j["goal"] == turn["goal"] and j["session"] == s["id"] and j["instruction"] == action["instruction"] for j in jobs):
                        raise ValueError("Brain 提出了相同的交辦，已阻止重複執行。")
                    manual_at = self._get("manual:" + s["id"], 0)
                    if manual_at and (turn["trigger"]["kind"] != "user" or manual_at >= turn["userCreated"]):
                        raise ValueError("你已手動接手這個工作，Brain 沒有自動續派。")
                    identity = str(uuid.uuid4())
                    wire = f"[Brain 交辦 {identity}]\n{action['instruction'].strip()}\n\n請在這個對話完成交辦並回報實際結果、驗證及阻礙；沿用既有核准規則。"
                    topic_ref = action.get('topic', turn.get('topic', ''))
                    staged.append({"id": identity, "goal": turn["goal"], "topic": topic_ids.get(topic_ref, topic_ref), "session": s["id"], "binding": s["binding"],
                                   "thread": conversation_id(s), "title": s.get("title", "工作"), "instruction": action["instruction"],
                                   "wire": wire, "state": "queued", "created": self.clock(), "detail": "", "baseline": turn["baselines"][s["id"]]})
                for topic in topic_updates:
                    if topic['state'] == 'done' and any(j.get('topic') == topic['id'] and j['state'] in ACTIVE and j['id'] not in plan['release'] for j in jobs + staged):
                        raise ValueError('這件事還有未完成的交辦，不能標示為完成。')
                for topic in topic_updates: self._save('topics', topic)
                for entry in plan.get('topics', []):
                    if entry['reply'].strip(): self._message(entry['reply'], topic=topic_ids[entry['id']])
                links = list(dict.fromkeys([*topic_ids.values(), *[j['topic'] for j in staged if j['topic']]]))
                if not turn.get('topic') and links:
                    self.db.execute('UPDATE messages SET links=? WHERE id=?', (json.dumps(links), turn['goal']))
                for job in staged: self._save("jobs", job)
                for job in releases: self._manual(job["session"], f"「{job['title']}」已依你的交代停止後續派送；目前執行中的 agent 仍會繼續。")
                if turn.get('topic'):
                    self._report(plan['reply'], topic=turn['topic'], kind='reply' if staged else 'result')
                else: self._message(plan["reply"], kind="reply" if staged else "result", links=links)
                turn["state"] = "done"; turn.pop('context', None); turn.pop('snapshot', None); self._save("turns", turn)
        except BrainPending:
            with self.lock, self.db:
                turn['state'] = 'codex_wait'; self._save('turns', turn)
        except Exception as error:
            # Only controlled validation/provider errors are user visible.
            reason = str(error) if type(error) is ValueError else "Brain 整理失敗，這次沒有新增交辦。請稍後再試。"
            with self.lock, self.db:
                turn["state"] = "failed"; turn.pop('context', None); turn.pop('snapshot', None); self._save("turns", turn)
                if turn['trigger'].get('kind') == 'result':
                    trigger = turn['trigger']
                    self._report(f"「{trigger.get('title', '工作')}」已回報；以下是 agent 原始結果，後續規劃尚未完成：\n\n{trigger.get('result', '')}", kind='result', topic=turn.get('topic', ''))
                self._report(reason, kind="error", topic=turn.get('topic', ''))

    def _finish(self, job, state, text):
        job.update(state=state, detail=text, finished=self.clock())
        self._save("jobs", job)
        if state == "completed":
            job["result"] = text; self._save("jobs", job)
            self._save("turns", {"id": "result:" + job["id"], "goal": job["goal"], "topic": job.get('topic', ''), "state": "queued",
                                  "trigger": {"kind": "result", "job": job["id"], "title": job["title"], "result": text[:12000], "partial": len(text) > 12000}})
        else: self._report(f"「{job['title']}」：{text}", kind="error" if state == "blocked" else "system", topic=job.get('topic', ''))

    def _observe_job(self, job):
        try: thread = self.get_thread(job["session"])
        except (ValueError, OSError): return  # Absence is confirmed by the state snapshot in tick.
        s, messages = thread["session"], thread["messages"]
        with self.lock, self.db:
            current = next(j for j in self._rows("jobs") if j["id"] == job["id"])
            if current["state"] not in ACTIVE: return
            job = current
            if s.get("binding") != job["binding"] or (job["thread"] and conversation_id(s) != job["thread"]):
                self._finish(job, "blocked", "對話或視窗已改變，請重新交代。沒有送到新視窗。")
                return
            if s.get("phase") == "error":
                self._finish(job, "blocked", "Agent 回報錯誤，請查看原始工作；沒有把目前內容當作完成結果。")
                return
            receipts = [i for i, m in enumerate(messages) if m.get("role") == "user" and m.get("text", "").strip() == job["wire"].strip()]
            start = receipts[-1] if receipts else None
            if start is not None:
                following = messages[start + 1:]
                if any(m.get("role") == "user" for m in following):
                    self._manual(job["session"], "偵測到你在原對話接著回覆，這個工作已交回手動處理。")
                    return
                replies = [m for m in following if m.get("role") == "assistant" and m.get("phase") != "commentary"]
                # Claude transcript messages have no final/commentary phase.
                # Require a NEW completion notice rather than interpreting an
                # intermediate assistant paragraph as completion on legacy hooks.
                complete = s.get("tool") != "claude" or (s.get("phase") == "complete" and bool(s.get("noticeID")) and s.get("noticeID") != job.get("noticeBefore"))
                if not running(s) and not waiting(s) and replies and complete:
                    job["sourceNotice"] = s.get("noticeID")
                    self._finish(job, "completed", replies[-1]["text"])
                    return
                job["state"] = "waiting" if waiting(s) else "running" if running(s) else "observing"
                self._save("jobs", job)
                return
            if job["state"] != "queued":
                if self.clock() - job.get("sentAt", job["created"]) > 45 and job["state"] != "uncertain":
                    job["state"] = "uncertain"
                    job["detail"] = "尚未在對話找到交辦，請查看原工作；Brain 不會重送。"
                    self._save("jobs", job); self._report(f"「{job['title']}」{job['detail']}", kind="error", topic=job.get('topic', ''))
                return
            if self._get("paused", False) or self.stop_event.is_set(): return
            # Interpret new user steering (including cancellation) before any
            # queued terminal write, even if the worker just became idle.
            if any(t["state"] in {"queued", "planning", "codex_wait"} and t["trigger"]["kind"] == "user" for t in self._rows("turns")):
                return
            user = next((m.get("text", "") for m in reversed(messages) if m.get("role") == "user"), "")
            if hashlib.sha256(user.encode()).hexdigest() != job.get("baseline"):
                self._manual(job["session"], "排隊期間原對話有新的使用者回覆，Brain 已停止自動交辦，交回手動處理。")
                return
            if running(s) or waiting(s): return
            if not s.get("canSend") or s.get("phase") in {"ended", "error"}:
                self._finish(job, "blocked", "目前無法接收交辦，請在工作管理確認 agent。")
                return
            # Commit BEFORE crossing the side-effect boundary, while holding the
            # same lock as pause/manual takeover. A crash never returns to queued.
            job.update(state="dispatching", sentAt=self.clock(), noticeBefore=s.get("noticeID"))
            self._save("jobs", job); self.db.commit()
            try:
                result = self.send({"session": job["session"], "binding": job["binding"], "text": job["wire"], "requestID": "brain-" + job["id"]})
                job["state"] = "sent" if result.get("sent") else "uncertain"
            except (ValueError, OSError): job["state"] = "uncertain"
            if job["state"] == "uncertain":
                job["detail"] = "交辦送出結果不明，先確認原對話；沒有自動重送。"
                self._report(f"「{job['title']}」{job['detail']}", kind="error", topic=job.get('topic', ''))
            self._save("jobs", job)

    def tick(self):
        state = self.get_state()
        with self.lock, self.db:
            self._set("connectionError", "")
            jobs = [j for j in self._rows("jobs") if j["state"] in ACTIVE]
            sessions = {s["id"]: s for s in state.get("sessions", [])}
            for job in jobs:
                if job["session"] not in sessions or sessions[job["session"]].get("alive") is False or sessions[job["session"]].get("phase") == "ended":
                    self._finish(job, "blocked", "原工作已關閉，交辦已停止追蹤；沒有自動開啟其他視窗。")
            # Only newly arriving human decisions produce a Brain message. They
            # never trigger autonomous work or clear the source notification.
            if self._get("enabled", False):
                seen = self._get("notices", {})
                owned = {j["session"] for j in jobs}
                if self.backend: owned.add(self.backend.session_id)
                for s in sessions.values():
                    identity = s.get("noticeID")
                    confirmed = bool(s.get("runtime", {}).get("approvals")) or s.get("noticeReason") == "user_input" or s.get("approvalReviewer") == "user"
                    if identity and seen.get(s["id"]) != identity and s["id"] in owned and human_wait(s) and confirmed:
                        topic = next((j.get('topic', '') for j in jobs if j['session'] == s['id']), '')
                        if self.backend and s['id'] == self.backend.session_id:
                            topic = next((t.get('topic', '') for t in self._rows('turns') if t['state'] == 'codex_wait'), '')
                        self._report(f"「{s.get('title', '工作')}」需要你的決定，請處理下方的原始確認內容。", kind="question", topic=topic)
                    if identity: seen[s["id"]] = identity
                self._set("notices", seen)
        for job in jobs: self._observe_job(job)
        self._plan_one(state)

    def start(self):
        if self.worker: return
        def run():
            while not self.stop_event.is_set():
                try: self.tick()
                except Exception:
                    with self.lock, self.db: self._set("connectionError", "暫時無法確認工作狀態，保留交辦；連線恢復後繼續確認。")
                self.wake.wait(2.5); self.wake.clear()
        self.worker = threading.Thread(target=run, name="pilot-brain", daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set(); self.wake.set()
        if self.worker: self.worker.join(timeout=1)

    def close(self):
        self.stop()
        if not self.worker or not self.worker.is_alive(): self.db.close()
