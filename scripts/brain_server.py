"""Single-user, loopback-only Pilot assistant host, independent of macOS."""
import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time
import re
from datetime import datetime, timezone

from brain_service import BrainService
from brain_stdio import BackgroundBrain, StdioProxy
from pilot_http import WebServer, Handler
from web_push import private_write, PushService, endpoint_id
from voice_service import VoiceService


class BackgroundHandler(Handler):
    def do_POST(self):
        if self.path != '/api/service/stop': return super().do_POST()
        if not self.trusted_request() or not self.authenticated(): return
        self.reply(200, {'stopping': True})
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class Application:
    def __init__(self, store, binary, codex_home, root, name):
        self.assets = Path(__file__).resolve().parents[1] / 'web'
        self.stylesheet = (self.assets / 'style.css').read_bytes()
        self.style_hash = hashlib.sha256(self.stylesheet).hexdigest()
        self.stylesheet_url = f'/assets/{self.style_hash}/style.css'
        self.index_html = (self.assets / 'index.html').read_bytes().replace(
            b'href="/style.css"', f'href="{self.stylesheet_url}"'.encode())
        self.index_html = self.index_html.replace(b'<body>', b'<body data-service="brain">')
        for old, new in {
            '連上這台 Mac，查看與回覆 Codex、Claude Code 工作。': '連上本機 Brain，與你的個人助理對話。',
            '輸入 Terminal Pilot「網頁」中的配對碼': '輸入背景服務顯示的配對碼',
            '不用安裝 App。電腦上的 Terminal Pilot 需保持開啟。': '背景服務持續執行；關閉瀏覽器後工作仍會繼續。',
            '沿用 Mac 的 Terminal 與原本的 CLI<br>無需 tmux，支援新工作與接續對話。': '這裡列出此服務管理的 Codex 對話。<br>選取工作查看原始訊息；回到個人助理繼續交代。',
        }.items(): self.index_html = self.index_html.replace(old.encode(), new.encode())
        auth = Path(store) / 'background-auth.json'
        if not auth.exists(): private_write(auth, json.dumps({'token': secrets.token_urlsafe(32)}).encode())
        self.token = json.loads(auth.read_text(encoding='utf-8'))['token']
        self.hosts, self.https_origin = {'127.0.0.1', 'localhost'}, ''
        self.code, self.code_created = f'{secrets.randbelow(1000000):06d}', time.monotonic()
        self.pair_lock, self.pair_attempts = threading.Lock(), deque()
        proxy = StdioProxy(binary, codex_home, root)
        self.backend = BackgroundBrain(store, proxy, root, name)
        self.brain = BrainService(store, self.state, self.thread, self.unsupported,
                                  backend=self.backend, memory_root=root)
        self.voice = VoiceService(self.thread)
        self.push = None
        self.push_thread = None

    def start_push(self, store, origin):
        if self.push is not None: return
        self.push = PushService(store, lambda: self.brain.notification_state(self.backend.state()), origin)
        self.push_thread = threading.Thread(target=self.push.run, name='pilot-push', daemon=True)
        self.push_thread.start()

    def stop_push(self):
        if self.push is not None:
            self.push.stop_event.set()
            self.push_thread.join(timeout=20)

    def push_request(self, route, data):
        if self.push is None: raise ValueError('請先啟用此背景服務的 Tailscale HTTPS 通知。')
        if route == '/api/push/config': return {'publicKey': self.push.public_key}
        if route == '/api/push/subscribe': return self.push.subscribe(data.get('subscription'))
        endpoint = data.get('endpoint')
        if not isinstance(endpoint, str) or len(endpoint) > 4096: raise ValueError('缺少通知訂閱。')
        identity = endpoint_id(endpoint)
        if route == '/api/push/status': return self.push.status(identity)
        if route == '/api/push/unsubscribe': return self.push.unsubscribe(identity)
        if route == '/api/push/test': return self.push.test(identity)
        raise ValueError('找不到這個通知操作。')

    def state(self):
        native = self.backend.state()
        notifications = self.brain.notification_state(native)['sessions'] if getattr(self, 'brain', None) else []
        return {**native, 'httpsURL': self.https_origin, 'notifications': notifications}

    def thread(self, identity):
        session = next((s for s in self.backend.state()['sessions'] if s['id'] == identity), None)
        if not session:
            raise ValueError('找不到這段對話。')
        messages = []
        turns = self.backend._turns(session)
        for turn in reversed(turns):
            stamp = turn.get('startedAt')
            timestamp = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp else None
            for index, item in enumerate(turn.get('items', [])):
                kind = item.get('type')
                if kind == 'userMessage':
                    text = '\n'.join(p.get('text', '') for p in item.get('content', []) if p.get('type') == 'text')
                elif kind == 'agentMessage': text = item.get('text', '')
                else: continue
                messages.append({'id': item.get('id') or f"{turn['id']}:{index}",
                                 'role': 'user' if kind == 'userMessage' else 'assistant',
                                 'text': text, 'time': timestamp, 'phase': item.get('phase', '')})
        return {'session': session, 'messages': messages[-150:], 'source': 'codex'}

    def pair(self, code):
        with self.pair_lock:
            now = time.monotonic()
            while self.pair_attempts and now - self.pair_attempts[0] > 60: self.pair_attempts.popleft()
            if len(self.pair_attempts) >= 5: raise ValueError('嘗試過多，請稍後再試。')
            self.pair_attempts.append(now)
            if now - self.code_created > 600 or not isinstance(code, str) or not secrets.compare_digest(code, self.code):
                raise ValueError('配對碼無效或已過期，請重新啟動服務取得新碼。')
            return {'token': self.token}

    def approve(self, data):
        if set(data) != {'session', 'binding', 'approval', 'requestID', 'action', 'content'}:
            raise ValueError('核准欄位不正確。')
        current = self.backend.current
        if not current or data['session'] != current['id'] or data['binding'] != current['binding']:
            raise ValueError('對話已變更，請重新讀取核准卡片。')
        return self.backend.proxy.decide(current['codexThreadID'], data['approval'], data['requestID'], data['action'], data['content'])

    def style_health(self, data):
        return {'recorded': True}

    def unsupported(self, *args, **kwargs):
        raise ValueError('此單人服務目前只提供 Brain 助理。')

    send = launch = create_directory = close_terminal = upgrade_approval = unsupported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--brain-root', required=True, type=Path)
    parser.add_argument('--codex-home', required=True, type=Path)
    parser.add_argument('--codex-binary', required=True, type=Path)
    parser.add_argument('--name', default='使用者')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--https-origin', default='')
    parser.add_argument('--proxy-port', type=int, default=8766)
    args = parser.parse_args()
    if args.https_origin and not re.fullmatch(r'https://[a-z0-9.-]+\.ts\.net:8443', args.https_origin):
        parser.error('HTTPS origin must be the exact Tailscale DNS name on port 8443')
    if args.https_origin and args.proxy_port == args.port and args.port != 0:
        parser.error('Proxy must use a separate loopback port')
    for path in (args.brain_root, args.codex_home):
        if not path.is_dir(): parser.error(f'Directory missing: {path}')
    args.store.mkdir(parents=True, exist_ok=True)
    # Held OS lock prevents two workers from sharing a journal, including after crashes.
    lock = (args.store / 'background.lock').open('a+b')
    if os.name == 'nt':
        import msvcrt
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    app = Application(args.store, args.codex_binary, args.codex_home, args.brain_root, args.name)
    server = WebServer(('127.0.0.1', args.port), app)
    server.RequestHandlerClass = BackgroundHandler
    proxy_server = None
    if args.https_origin:
        app.https_origin = args.https_origin
        proxy_server = WebServer(('127.0.0.1', args.proxy_port), app, proxy=True)
        proxy_server.RequestHandlerClass = BackgroundHandler
        app.start_push(args.store, args.https_origin)
        threading.Thread(target=proxy_server.serve_forever, daemon=True).start()
    private_write(args.store / 'background-ready.json', json.dumps({
        'pid': os.getpid(), 'port': server.server_port, 'codexPID': app.backend.proxy.process.pid,
        'httpsURL': app.https_origin, 'proxyPort': proxy_server.server_port if proxy_server else None,
        'sandbox': app.backend.permissions['sandbox'], 'writableRoots': app.backend.permissions['roots']}).encode())
    app.brain.start()
    print(f'Pilot Brain: http://127.0.0.1:{server.server_port} | pairing code: {app.code}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        app.stop_push()
        if proxy_server:
            proxy_server.shutdown(); proxy_server.server_close()
        server.server_close(); app.brain.close(); app.backend.close(); lock.close()
        (args.store / 'background-ready.json').unlink(missing_ok=True)


if __name__ == '__main__': main()
