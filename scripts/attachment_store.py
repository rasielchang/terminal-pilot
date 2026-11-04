"""Explicit immutable private CSV copies. HTTP never accepts a source path."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import threading
import time
import unicodedata
import uuid

MAX_ATTACHMENT = 5 * 1024 * 1024
MAX_ATTACHMENT_BODY = 4 * ((MAX_ATTACHMENT + 2) // 3) + 8192


def filename(value):
    if (not isinstance(value,str) or not 1 <= len(value.encode('utf-8')) <= 180 or
        not value.lower().endswith('.csv') or value.startswith('.') or value != value.strip() or
        any(c in '/\\:' or unicodedata.category(c).startswith('C') for c in value)):
        raise ValueError('附件名稱須為安全的 CSV 檔名（最多180 bytes）。')
    return value


def read_local(path):
    """CLI-only explicit local read, no symlinks, devices, FIFOs or unlimited read."""
    path = Path(path).expanduser().absolute()
    # macOS system aliases are canonical roots, not caller-controlled links.
    if sys.platform == 'darwin' and path.parts[1:2] in [('tmp',),('var',)]:
        path = Path('/private')/path.relative_to('/')
    if any(p.is_symlink() for p in [path,*path.parents]): raise ValueError('附件來源不能是符號連結。')
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode): raise ValueError('附件須為一般檔案。')
    fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0))
    with os.fdopen(fd,'rb') as stream:
        info = os.fstat(stream.fileno())
        if (info.st_dev,info.st_ino) != (before.st_dev,before.st_ino) or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_ATTACHMENT: raise ValueError('附件須為1 byte至5MB的一般檔案。')
        raw = stream.read(MAX_ATTACHMENT+1)
    if not 0 < len(raw) <= MAX_ATTACHMENT: raise ValueError('附件超過5MB。')
    return raw


class AttachmentStore:
    def __init__(self, store):
        self.root = Path(store)/'attachments'
        if self.root.is_symlink(): raise ValueError('附件儲存位置不可為連結。')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root,0o700)
        self.lock = threading.RLock()
        index = self.root/'index.sqlite3'
        if index.is_symlink(): raise ValueError('附件索引不可為連結。')
        self.db = sqlite3.connect(index,check_same_thread=False)
        os.chmod(index,0o600)
        self.db.executescript('PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY, request TEXT UNIQUE, fingerprint TEXT, data TEXT);')
    def close(self):
        with self.lock: self.db.close()
    def register(self, data, owner):
        if set(data) != {'requestID','name','mime','data'}: raise ValueError('附件只接受明確檔名、MIME與檔案內容，不接受路徑。')
        request = data['requestID']; name = filename(data['name'])
        if not isinstance(request,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request): raise ValueError('附件請求識別無效。')
        if data['mime'] != 'text/csv': raise ValueError('目前只支援 text/csv。')
        encoded = data['data']
        if not isinstance(encoded,str) or len(encoded) > MAX_ATTACHMENT_BODY-8192: raise ValueError('附件最多5MB。')
        try: raw = base64.b64decode(encoded,validate=True); text = raw.decode('utf-8-sig')
        except (ValueError,UnicodeError): raise ValueError('附件須為 UTF-8 CSV。') from None
        if not 0 < len(raw) <= MAX_ATTACHMENT or '\0' in text: raise ValueError('附件大小或內容無效。')
        sha = hashlib.sha256(raw).hexdigest()
        fingerprint = hashlib.sha256(json.dumps([owner,name,data['mime'],sha],sort_keys=True).encode()).hexdigest()
        with self.lock, self.db:
            old = self.db.execute('SELECT fingerprint,data FROM attachments WHERE request=?',(request,)).fetchone()
            if old:
                if old[0] != fingerprint: raise ValueError('附件 requestID 已用於不同內容或工作。')
                return json.loads(old[1])
            used = self.db.execute('SELECT data FROM attachments').fetchall()
            if sum(json.loads(r[0])['bytes'] for r in used) + len(raw) > 100*1024*1024: raise ValueError('私人附件已達100MB上限。')
            identity = uuid.uuid4().hex
            path = self.root/(identity+'.csv')
            fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(fd,'wb') as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                value = {'id':identity,'name':name,'mime':'text/csv','bytes':len(raw),'sha256':sha,'created':time.time(),**owner}
                self.db.execute('INSERT INTO attachments VALUES(?,?,?,?)',(identity,request,fingerprint,json.dumps(value)))
            except BaseException:
                path.unlink(missing_ok=True); raise
            return value
    def metadata(self, identity):
        if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity): raise ValueError('附件識別無效。')
        with self.lock: row = self.db.execute('SELECT data FROM attachments WHERE id=?',(identity,)).fetchone()
        if not row: raise ValueError('找不到已註冊附件。')
        return json.loads(row[0])
    def read(self, identity, owner):
        value = self.metadata(identity)
        if any(value.get(k) != v for k,v in owner.items()): raise PermissionError('附件不屬於指定工作。')
        raw = read_local(self.root/(identity+'.csv'))
        if len(raw) != value['bytes'] or hashlib.sha256(raw).hexdigest() != value['sha256']: raise ValueError('附件完整性驗證失敗。')
        return value, raw
    def catalog(self, thread):
        with self.lock: rows = self.db.execute('SELECT data FROM attachments ORDER BY rowid DESC').fetchall()
        return [v for r in rows if (v:=json.loads(r[0])).get('brainThread')==thread][:100]
