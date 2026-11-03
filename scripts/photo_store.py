"""Private normalized images and durable receipts; never accepts filesystem paths."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
import warnings

MAX_PHOTO = 12 * 1024 * 1024
MAX_PHOTO_BODY = MAX_PHOTO * 4 // 3 + 4096
MARKER = re.compile(r'\n?\[Pilot photos: ([a-f0-9,]+)\]')


def photo_ids(value):
    if not isinstance(value,list) or not 1 <= len(value) <= 4 or any(not isinstance(v,str) or not re.fullmatch(r'[a-f0-9]{32}',v) for v in value) or len(set(value)) != len(value):
        raise ValueError('每次可傳1至4張照片；照片識別碼不正確。')
    return value


def scope(session):
    tool=session.get('tool')
    thread=session.get('codexThreadID') if tool=='codex' else session.get('claudeSessionID')
    if tool not in {'codex','claude'} or not thread: raise ValueError('原 agent 對話尚未綁定，照片沒有改送其他工作。')
    home=session.get('codexHome') if tool=='codex' else session.get('claudeHome')
    home=str(Path(home or Path.home()/('.codex' if tool=='codex' else '.claude')).expanduser().resolve())
    return hashlib.sha256(json.dumps([tool,home or '',thread]).encode()).hexdigest()


def wire(text, ids, request=None):
    result=text.rstrip()+'\n[Pilot photos: '+','.join(photo_ids(ids))+']'
    return result+('\n[Pilot photo request: '+request+']' if request else '')


def clean(text):
    text=re.sub(r'\n?\[Pilot photos: [a-f0-9,]+\]\n.*?\[Pilot photos end\]','',text,flags=re.S)
    return re.sub(r'\n?\[Pilot photo request: [A-Za-z0-9_-]+\]','',MARKER.sub('',text)).strip()


def claude_wire(text,ids,paths,request):
    import shlex
    return wire(text,ids)+'\n請讀取以下照片的圖像內容後回覆，若無法讀取請明確告知；仍沿用原核准規則。\n'+'\n'.join(shlex.quote(p) for p in paths)+'\n[Pilot photo request: '+request+']\n[Pilot photos end]'


class PhotoStore:
    def __init__(self,store):
        self.root=Path(store)/'photos';self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=threading.RLock()
        self.db=sqlite3.connect(self.root/'index.sqlite3',check_same_thread=False)
        os.chmod(self.root/'index.sqlite3',0o600)
        self.db.executescript('''PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY, request TEXT UNIQUE, fingerprint TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS grants(id TEXT, scope TEXT, PRIMARY KEY(id,scope));
            CREATE TABLE IF NOT EXISTS sends(id TEXT PRIMARY KEY, data TEXT);
        ''')
    def close(self):
        with self.lock:self.db.close()
    def metadata(self,identity,target):
        with self.lock:
            row=self.db.execute('SELECT data FROM photos JOIN grants USING(id) WHERE id=? AND scope=?',(identity,target)).fetchone()
        if not row:raise ValueError('照片不屬於這個對話，沒有跨工作取用。')
        return json.loads(row[0])
    def paths(self,ids,target):
        return [str(self.root/(self.metadata(i,target)['id']+'.jpg')) for i in photo_ids(ids)]
    def catalog(self,target):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT data FROM photos JOIN grants USING(id) WHERE scope=? ORDER BY photos.rowid DESC LIMIT 24',(target,))]
    def grant(self,ids,source,target):
        with self.lock,self.db:
            for i in photo_ids(ids):self.metadata(i,source)
            self.db.executemany('INSERT OR IGNORE INTO grants VALUES(?,?)',[(i,target) for i in ids])
    def upload(self,data,target):
        from PIL import Image,ImageOps,UnidentifiedImageError
        request=data.get('requestID');encoded=data.get('data');name=data.get('name','照片')
        if not isinstance(request,str) or not 1<=len(request)<=80 or not isinstance(name,str) or len(name)>255:raise ValueError('照片請求格式不正確。')
        if not isinstance(encoded,str) or len(encoded)>MAX_PHOTO*4//3+4:raise ValueError('每張照片最多12MB。')
        try:raw=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError):raise ValueError('照片資料不完整，請重試上傳。') from None
        if not 0<len(raw)<=MAX_PHOTO:raise ValueError('每張照片最多12MB。')
        fingerprint=hashlib.sha256(target.encode()+raw).hexdigest()
        with self.lock,self.db:
            old=self.db.execute('SELECT fingerprint,data FROM photos WHERE request=?',(request,)).fetchone()
            if old:
                if old[0]!=fingerprint:raise ValueError('上傳識別碼已用於另一張照片或對話。')
                return json.loads(old[1])
            if b'ftyp' in raw[:16] or name.lower().endswith(('.heic','.heif')):raise ValueError('這張HEIC照片尚未轉換；請從相簿分享為JPEG或選擇截圖後重試。')
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error',Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format not in {'JPEG','PNG'} or getattr(image,'n_frames',1)!=1:raise ValueError('只支援靜態JPEG或PNG照片，不支援影片或動畫。')
                        if image.width*image.height>50_000_000 or max(image.size)>12000:raise ValueError('照片解析度過大，請縮小或選擇截圖。')
                        image.load();image=ImageOps.exif_transpose(image)
                        if 'A' in image.getbands() or 'transparency' in image.info:
                            rgba=image.convert('RGBA');background=Image.new('RGBA',rgba.size,'white');background.alpha_composite(rgba);image=background.convert('RGB')
                        else:image=image.convert('RGB')
                        image.thumbnail((3072,3072))
                        output=io.BytesIO();image.save(output,'JPEG',quality=90)
                        if output.tell()>4*1024*1024:
                            image.thumbnail((2048,2048));output=io.BytesIO();image.save(output,'JPEG',quality=85)
                        if output.tell()>4*1024*1024:raise ValueError('照片轉換後仍過大，請縮小或選擇截圖。')
                        width,height=image.size
            except (UnidentifiedImageError,OSError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise ValueError('無法讀取照片，請選擇有效JPEG或PNG。') from None
            if sum(p.stat().st_size for p in self.root.glob('*.jpg'))+len(output.getvalue())>1024**3:raise ValueError('照片儲存已達1GB，請先由電腦整理後再上傳。')
            identity=uuid.uuid4().hex;path=self.root/(identity+'.jpg')
            with path.open('xb') as f:
                os.chmod(path,0o600);f.write(output.getvalue());f.flush();os.fsync(f.fileno())
            value={'id':identity,'name':name,'mime':'image/jpeg','width':width,'height':height,'bytes':path.stat().st_size,'created':time.time()}
            self.db.execute('INSERT INTO photos VALUES(?,?,?,?)',(identity,request,fingerprint,json.dumps(value)))
            self.db.execute('INSERT INTO grants VALUES(?,?)',(identity,target))
            return value
    def decorate(self,page,target):
        for message in page.get('messages',[]):
            matches=MARKER.findall(message.get('text','')) if message.get('role')=='user' else []
            ids=[i for m in matches for i in m.split(',')]
            if not ids:continue
            try:photos=[self.metadata(i,target) for i in photo_ids(ids)]
            except ValueError:continue
            request=re.search(r'\[Pilot photo request: ([A-Za-z0-9_-]+)\]',message['text'])
            if request:message['photoRequest']=request[1]
            message['photos']=photos;message['text']=clean(message['text'])
        return page
    def receipt(self,identity):
        with self.lock:
            r=self.db.execute('SELECT data FROM sends WHERE id=?',(identity,)).fetchone()
            return json.loads(r[0]) if r else None
    def save(self,record):
        with self.lock,self.db:self.db.execute('INSERT OR REPLACE INTO sends VALUES(?,?)',(record['id'],json.dumps(record)))
