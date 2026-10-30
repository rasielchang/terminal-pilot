"""Shared Pilot HTTP boundary for native and background hosts."""
import hmac
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from voice_service import MAX_VOICE_BODY
MAX_BODY = 40_000


class UnsupportedOperation(Exception):
    """A valid operation that this host does not implement (HTTP 501)."""


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, application, *, proxy=False):
        self.application = application
        self.proxy = proxy
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "TerminalPilot"

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *args):
        pass  # No URLs, text, tokens or conversation content in logs.

    def reply(self, status, value, content_type="application/json; charset=utf-8", *, cache_control="no-store"):
        body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Permissions-Policy", "microphone=(self), camera=()")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def trusted_request(self):
        app = self.server.application
        host = self.headers.get("Host", "")
        expected = {f"{name}:{self.server.server_port}" for name in app.hosts}
        origin_expected = "http://" + host
        secure_proxy = (getattr(self.server, "proxy", False) and self.client_address[0] == "127.0.0.1"
                        and app.https_origin and host == urlsplit(app.https_origin).netloc)
        if secure_proxy:
            origin_expected = app.https_origin
        if host not in expected and not secure_proxy:
            self.reply(403, {"error": "不接受這個連線位址。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != origin_expected:
            self.reply(403, {"error": "不接受跨網站請求。"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.reply(403, {"error": "不接受跨網站請求。"})
            return False
        return True

    def push_transport(self):
        app = self.server.application
        if not (getattr(self.server, "proxy", False) and self.client_address[0] == "127.0.0.1"
                and app.https_origin and self.headers.get("Host") == urlsplit(app.https_origin).netloc):
            raise ValueError("請從 Tailscale HTTPS 網址啟用通知。")

    def authenticated(self):
        expected = "Bearer " + self.server.application.token
        provided = self.headers.get("Authorization", "")
        if not hmac.compare_digest(provided.encode(), expected.encode()):
            self.reply(401, {"error": "請先使用 Terminal Pilot 的配對碼連線。"})
            return False
        return True

    def do_GET(self):
        if not self.trusted_request():
            return
        route = urlsplit(self.path)
        app = self.server.application
        if route.path == app.stylesheet_url:
            self.reply(200, app.stylesheet, "text/css; charset=utf-8",
                       cache_control="public, max-age=31536000, immutable")
            return
        static = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/reading.js": ("reading.js", "text/javascript; charset=utf-8"),
            "/approvals.js": ("approvals.js", "text/javascript; charset=utf-8"),
            "/windows.js": ("windows.js", "text/javascript; charset=utf-8"),
                  "/launch.js": ("launch.js", "text/javascript; charset=utf-8"),
                  "/assistant.js": ("assistant.js", "text/javascript; charset=utf-8"),
                  "/voice.js": ("voice.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/icon.svg": ("icon.svg", "image/svg+xml"),
                  "/pwa.js": ("pwa.js", "text/javascript; charset=utf-8"),
                  "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
                  "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
                  "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
                  "/icon-192.png": ("icon-192.png", "image/png"),
                  "/icon-512.png": ("icon-512.png", "image/png")}
        if route.path in static:
            name, mime = static[route.path]
            app = self.server.application
            body = app.index_html if name == "index.html" else app.stylesheet if name == "style.css" else (app.assets / name).read_bytes()
            self.reply(200, body, mime)
            return
        if not self.authenticated():
            return
        try:
            if route.path == "/api/state":
                result = self.server.application.state()
            elif route.path == "/api/brain":
                if app.brain is None: raise ValueError("Brain 尚未啟動。")
                query = parse_qs(route.query)
                result = app.brain.state(query.get('topic', [''])[0], int(query['before'][0]) if 'before' in query else None)
            elif route.path == '/api/brain/source':
                if app.brain is None: raise ValueError("Brain 尚未啟動。")
                result = app.brain.source(parse_qs(route.query).get('job', [''])[0])
            elif route.path == "/api/thread":
                session_id = parse_qs(route.query).get("session", [""])[0]
                result = self.server.application.thread(session_id)
            elif route.path == "/api/directories":
                directory = parse_qs(route.query).get("directory", ["~"])[0]
                result = app.directories({"directory": directory})
            elif route.path == "/api/voice/status":
                result = self.server.application.voice.status()
            elif route.path == "/api/push/config":
                self.push_transport()
                result = self.server.application.push_request(route.path, {})
            else:
                self.reply(404, {"error": "找不到這個頁面。"})
                return
            self.reply(200, result)
        except UnsupportedOperation as error:
            self.reply(501, {"error": str(error), "code": "unsupported_operation"})
        except (ValueError, OSError):
            self.reply(409, {"error": str(sys.exc_info()[1])})

    def do_POST(self):
        if not self.trusted_request():
            return
        if self.path != "/api/pair" and not self.authenticated():
            return
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("請使用 JSON 送出。")
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("不支援分段請求。")
            length = int(self.headers.get("Content-Length", "0"))
            limit = MAX_VOICE_BODY if self.path == "/api/voice/transcribe" else MAX_BODY
            if not 0 < length <= limit:
                raise ValueError("送出內容過長或為空。")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("請求格式不正確。")
            app = self.server.application
            if self.path == "/api/pair":
                result = app.pair(data.get("code"))
            elif self.path in {"/api/brain/message", "/api/brain/control", "/api/brain/ack", "/api/brain/open", "/api/brain/topics", "/api/brain/work"}:
                if app.brain is None: raise ValueError("Brain 尚未啟動。")
                operation = {"/api/brain/message": app.brain.submit, "/api/brain/control": app.brain.control,
                             "/api/brain/ack": app.brain.acknowledge, "/api/brain/open": app.brain.open_backend,
                             "/api/brain/topics": app.brain.create_topic, "/api/brain/work": app.brain.open_work}[self.path]
                result = operation(data)
            elif self.path == "/api/send":
                result = app.send(data)
            elif self.path == "/api/terminal/input":
                result = app.terminal_input(data)
            elif self.path == "/api/approval":
                result = app.approve(data)
            elif self.path == "/api/approval/upgrade":
                result = app.upgrade_approval(data)
            elif self.path == "/api/launch":
                result = app.launch(data)
            elif self.path == "/api/directories":
                result = app.create_directory(data)
            elif self.path == "/api/terminal/close-preview":
                result = app.close_terminal(data, preview=True)
            elif self.path == "/api/terminal/close":
                result = app.close_terminal(data)
            elif self.path == "/api/diagnostics/styles":
                result = app.style_health(data)
            elif self.path == "/api/voice/transcribe":
                result = app.voice.transcribe(data)
            elif self.path == "/api/voice/summary":
                result = app.voice.summarize(data)
            elif self.path == "/api/voice/speech":
                result = app.voice.speech(data)
            elif self.path == "/api/voice/demo":
                result = app.voice.demo()
            elif self.path == "/api/terminal":
                result = app.terminal_read(data)
            elif self.path == "/api/ack":
                result = app.acknowledge(data)
            elif self.path.startswith("/api/push/"):
                self.push_transport()
                result = app.push_request(self.path, data)
            else:
                self.reply(404, {"error": "找不到這個操作。"})
                return
            self.reply(200, result)
        except UnsupportedOperation as error:
            self.reply(501, {"error": str(error), "code": "unsupported_operation"})
        except (ValueError, OSError, TypeError):
            self.reply(409, {"error": str(sys.exc_info()[1])})
