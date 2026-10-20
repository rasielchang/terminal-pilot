"""Apple Web Push delivery using RFC 8291/8292 and the installed cryptography library.

Only notification metadata is encrypted and sent; never transcripts or terminal input.
Persistent keys/subscriptions are private to this user's Pilot application store.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import struct
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        raise ValueError("通知訂閱格式不正確。")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def public_bytes(key):
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def private_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + secrets.token_hex(6))
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(data)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def endpoint_id(endpoint):
    return hashlib.sha256(endpoint.encode()).hexdigest()


def validate_subscription(value):
    if not isinstance(value, dict):
        raise ValueError("通知訂閱格式不正確。")
    endpoint = value.get("endpoint", "")
    if not isinstance(endpoint, str) or len(endpoint) > 4096:
        raise ValueError("通知位址不正確。")
    url = urlsplit(endpoint)
    # This version deliberately supports Apple's push service only. An authenticated
    # client cannot use subscriptions as an arbitrary HTTP proxy into the LAN.
    host = url.hostname or ""
    if (url.scheme != "https" or not re.fullmatch(r"(?:[a-z0-9-]+\.)*push\.apple\.com", host)
            or url.port not in (None, 443) or url.username or url.password or url.fragment
            or not url.path.startswith("/") or any(ord(c) <= 32 for c in endpoint)):
        raise ValueError("這版通知僅支援 iPhone／Safari 的 Apple 推播訂閱。")
    keys = value.get("keys", {})
    if not isinstance(keys, dict):
        raise ValueError("通知金鑰格式不正確。")
    receiver, auth = unb64(keys.get("p256dh")), unb64(keys.get("auth"))
    if len(receiver) != 65 or receiver[0] != 4 or len(auth) != 16:
        raise ValueError("通知金鑰格式不正確。")
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver)
    return {"endpoint": endpoint, "keys": {"p256dh": b64(receiver), "auth": b64(auth)}}


def encrypt(payload, subscription, *, sender=None, salt=None):
    """One aes128gcm record, including the RFC 8188 binary header."""
    receiver = unb64(subscription["keys"]["p256dh"])
    auth = unb64(subscription["keys"]["auth"])
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    salt = salt or secrets.token_bytes(16)
    sender_public = public_bytes(sender)
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + receiver + sender_public).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    if len(payload) > 3000:
        raise ValueError("通知內容過長。")
    encrypted = AESGCM(key).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(sender_public)) + sender_public + encrypted


def authorization(key, endpoint, subject, now=None):
    url = urlsplit(endpoint)
    header = b64(b'{"typ":"JWT","alg":"ES256"}')
    claims = b64(json.dumps({"aud": f"{url.scheme}://{url.netloc}", "exp": int(now or time.time()) + 3600,
                            "sub": subject}, separators=(",", ":")).encode())
    unsigned = f"{header}.{claims}"
    r, s = decode_dss_signature(key.sign(unsigned.encode(), ec.ECDSA(hashes.SHA256())))
    signature = b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={unsigned}.{signature}, k={b64(public_bytes(key))}"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def deliver(subscription, payload, key, subject):
    endpoint = validate_subscription(subscription)["endpoint"]
    body = encrypt(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), subscription)
    request = Request(endpoint, data=body, method="POST", headers={
        "Authorization": authorization(key, endpoint, subject), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": "120", "Urgency": "high",
        "Topic": b64(hashlib.sha256(payload["tag"].encode()).digest())[:32],
    })
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            return response.status
    except HTTPError as error:
        return error.code
    except (OSError, URLError):
        return 0  # Never expose endpoint URLs or subscription secrets in errors/logs.


class PushService:
    def __init__(self, store, state, subject, *, sender=deliver, reset=False, clock=time.monotonic, settle_seconds=8):
        self.store, self.state, self.subject, self.sender = Path(store), state, subject, sender
        self.lock, self.stop_event = threading.RLock(), threading.Event()
        self.next_attempt, self.last_test = {}, {}
        self.clock, self.settle_seconds, self.pending = clock, settle_seconds, {}
        self.store.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_file = self.store / "push-key.pem"
        if key_file.exists():
            self.key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            private_write(key_file, self.key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.file = self.store / "push-subscriptions.json"
        self.devices = json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() and not reset else {}
        if reset:
            self.save()

    @property
    def public_key(self):
        return b64(public_bytes(self.key))

    def save(self):
        private_write(self.file, json.dumps(self.devices, ensure_ascii=False).encode())

    @staticmethod
    def notices(sessions):
        return {s["id"]: s.get("noticeID", "") for s in sessions}

    @staticmethod
    def needs_attention(session):
        if not session.get("unread") or not session.get("noticeID"):
            return False
        if session.get("phase") in {"complete", "error"}:
            return True
        if session.get("phase") != "waiting":
            return False
        if session.get("noticeReason") == "user_input" or session.get("waitingTool") in {
                "request_user_input", "functions.request_user_input", "request_user_input_async", "functions.request_user_input_async"}:
            return True
        # PermissionRequest by itself includes automatic reviews. When the route
        # is unknown, retain the desktop indication but don't interrupt the phone.
        return session.get("noticeReason") == "permission" and session.get("approvalReviewer") == "user"

    def subscribe(self, subscription):
        subscription = validate_subscription(subscription)
        identity = endpoint_id(subscription["endpoint"])
        sessions = self.state().get("sessions", [])
        with self.lock:
            old = self.devices.get(identity)
            if old is None:
                if len(self.devices) >= 16:
                    raise ValueError("通知裝置已達上限，請先在 Mac 重設配對。")
                self.devices[identity] = {"subscription": subscription, "seen": self.notices(sessions),
                                          "lastSent": 0, "error": ""}
            else:
                old["subscription"] = subscription
            self.save()
        return {"subscribed": True}

    def unsubscribe(self, identity):
        with self.lock:
            self.devices.pop(identity, None)
            self.save()
        return {"subscribed": False}

    def status(self, identity):
        with self.lock:
            device = self.devices.get(identity, {})
            return {"subscribed": bool(device), "lastSent": device.get("lastSent", 0), "error": device.get("error", "")}

    @staticmethod
    def payload(session, count, *, test=False):
        return {"title": "Terminal Pilot 通知測試" if test else str(session.get("title") or "工作")[:80],
                "body": "收到這則通知就表示推播已送達。聲音與震動依 iPhone 設定。" if test else str(session.get("status") or "有工作需要你處理")[:160],
                "session": "" if test else session["id"], "noticeID": "" if test else session["noticeID"],
                "count": count, "tag": "pilot-test" if test else "pilot-" + session["id"],
                "sentAt": int(time.time() * 1000)}

    def send(self, identity, device, payload):
        code = self.sender(device["subscription"], payload, self.key, self.subject)
        with self.lock:
            if self.devices.get(identity) is not device:
                return False
            if code in (404, 410):
                self.devices.pop(identity)
            elif 200 <= code < 300:
                device["error"] = ""
                device["lastSent"] = time.time()
                if payload["session"]:
                    device["seen"][payload["session"]] = payload["noticeID"]
                self.next_attempt.pop(identity, None)
            else:
                device["error"] = f"Apple 推播服務回應 {code}，稍後重試。" if code else "無法連上 Apple 推播服務，稍後重試。"
                self.next_attempt[identity] = time.monotonic() + (300 if code in (400, 401, 403) else 30)
            self.save()
        return 200 <= code < 300

    def test(self, identity):
        with self.lock:
            device = self.devices.get(identity)
            if not device:
                raise ValueError("請先啟用這台 iPhone 的通知。")
            if time.monotonic() - self.last_test.get(identity, -60) < 10:
                raise ValueError("請等 10 秒再測試。")
            self.last_test[identity] = time.monotonic()
        self.stop_event.wait(5)
        with self.lock:
            if self.stop_event.is_set() or self.devices.get(identity) is not device:
                raise ValueError("通知已取消。")
        sessions = self.state().get("sessions", [])
        sent = self.send(identity, device, self.payload({}, sum(bool(s.get("unread")) for s in sessions), test=True))
        if not sent:
            raise ValueError(self.status(identity).get("error") or "訂閱已失效，請重新啟用通知。")
        return {"accepted": True}  # Provider acceptance is not proof of device delivery.

    def tick(self):
        with self.lock:
            devices = list(self.devices.items())
        if not devices:
            return
        sessions = self.state().get("sessions", [])
        now = self.clock()
        candidates = {(s["id"], s["noticeID"]): s for s in sessions if self.needs_attention(s)}
        self.pending = {key: self.pending.get(key, now) for key in candidates}
        count = sum(bool(s.get("unread")) for s in sessions)
        for identity, device in devices:
            if self.next_attempt.get(identity, 0) > time.monotonic():
                continue
            for session in sessions:
                key = (session["id"], session.get("noticeID"))
                if (key in candidates and now - self.pending[key] >= self.settle_seconds
                        and device["seen"].get(session["id"]) != session["noticeID"]):
                    # Confirm again immediately before sending; a desktop ack or
                    # resumed agent must cancel notifications queued by an earlier snapshot.
                    latest = self.state().get("sessions", [])
                    current = next((s for s in latest if s["id"] == session["id"]), {})
                    if not self.needs_attention(current) or current.get("noticeID") != session["noticeID"]:
                        continue
                    count = sum(bool(s.get("unread")) for s in latest)
                    if not self.send(identity, device, self.payload(current, count)):
                        break

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except (ValueError, OSError, TypeError):
                pass  # Parent may be unavailable during app shutdown; retry later.
            self.stop_event.wait(2)
