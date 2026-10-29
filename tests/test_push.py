import json
import os
from pathlib import Path
import stat
import ssl
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import web_push as push
import tailscale_web as tailscale
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature


def subscription():
    key = ec.generate_private_key(ec.SECP256R1())
    return {"endpoint": "https://web.push.apple.com/Qtest",
            "keys": {"p256dh": push.b64(push.public_bytes(key)), "auth": push.b64(b"a" * 16)}}


class EncryptionTests(unittest.TestCase):
    def test_push_tls_uses_native_windows_trust_without_global_ssl_patch(self):
        import types
        from unittest.mock import Mock
        native = Mock()
        original = ssl.SSLContext
        with patch.object(push.os, 'name', 'nt'), patch.dict(sys.modules, {'truststore': types.SimpleNamespace(SSLContext=native)}):
            self.assertIs(push.push_tls_context(), native.return_value)
        native.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        self.assertIs(ssl.SSLContext, original)
        with patch.object(push.os, 'name', 'posix'):
            context = push.push_tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_transport_keeps_redirect_blocking_and_sanitizes_tls_failure(self):
        from urllib.error import URLError
        sub = subscription()
        key = ec.generate_private_key(ec.SECP256R1())
        payload = {'tag': 'synthetic-test'}
        with patch('web_push.build_opener') as opener:
            opener.return_value.open.side_effect = URLError(ssl.SSLCertVerificationError('sensitive endpoint'))
            self.assertEqual(push.deliver(sub, payload, key, 'https://test.example'), -1)
            handlers = opener.call_args.args
            self.assertIsInstance(handlers[0], push.NoRedirect)
            self.assertIsInstance(handlers[1], push.HTTPSHandler)
        with patch('web_push.push_tls_context', side_effect=ImportError('missing')):
            self.assertEqual(push.deliver(sub, payload, key, 'https://test.example'), -2)

    def test_rfc8291_published_vector(self):
        # RFC 8291 Appendix A's published independent vector, not an encode/decode roundtrip.
        sub = {"keys": {"p256dh": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                        "auth": "BTBZMqHH6r4Tts7J_aSIgg"}}
        private = ec.derive_private_key(int.from_bytes(push.unb64("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw")), ec.SECP256R1())
        result = push.encrypt(b"When I grow up, I want to be a watermelon", sub,
                              sender=private, salt=push.unb64("DGv6ra1nlYgDCS1FRnbzlw"))
        self.assertEqual(push.b64(result[:86]), "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8")
        self.assertEqual(push.b64(result[86:]), "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ")

    def test_vapid_claims_and_es256_signature(self):
        key = ec.generate_private_key(ec.SECP256R1())
        value = push.authorization(key, "https://web.push.apple.com/path", "https://mac.example.ts.net:8443", now=1000)
        jwt = value.split("t=", 1)[1].split(",", 1)[0]
        header, payload, signature = jwt.split(".")
        self.assertEqual(json.loads(push.unb64(payload)), {"aud": "https://web.push.apple.com", "exp": 4600, "sub": "https://mac.example.ts.net:8443"})
        signature = push.unb64(signature)
        self.assertEqual(len(signature), 64)
        key.public_key().verify(encode_dss_signature(int.from_bytes(signature[:32]), int.from_bytes(signature[32:])),
                                (header + "." + payload).encode(), ec.ECDSA(hashes.SHA256()))

    def test_subscription_rejects_ssrf_credentials_bad_keys_and_redirects(self):
        sub = subscription()
        for endpoint in ["http://web.push.apple.com/x", "https://127.0.0.1/x", "https://web.push.apple.com.evil.test/x",
                         "https://user@web.push.apple.com/x", "https://web.push.apple.com:8443/x", "https://web.push.apple.com/x#y"]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                push.validate_subscription({**sub, "endpoint": endpoint})
        with self.assertRaises(ValueError):
            push.validate_subscription({**sub, "keys": {"auth": "aa", "p256dh": "bad"}})
        self.assertIsNone(push.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.test"))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sent, self.code = [], 201
        self.sessions = [{"id": "a", "noticeID": "old", "unread": True, "phase": "complete", "title": "專案", "status": "回覆完成 · 待查看"}]
        self.service = push.PushService(self.temp.name, lambda: {"sessions": self.sessions}, "https://mac.example.ts.net:8443", sender=self.send, settle_seconds=0)
        self.sub = subscription()
        self.identity = push.endpoint_id(self.sub["endpoint"])
        self.service.subscribe(self.sub)

    def tearDown(self):
        self.temp.cleanup()

    def send(self, sub, payload, key, subject):
        self.sent.append(payload)
        return self.code

    def test_only_new_unread_and_once_per_device_across_restart(self):
        self.service.tick()
        self.assertEqual(self.sent, [])
        self.sessions[0]["noticeID"] = "new"
        self.service.tick(); self.service.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["count"], 1)
        self.assertNotIn("directory", self.sent[0])
        restarted = push.PushService(self.temp.name, self.service.state, self.service.subject, sender=self.send, settle_seconds=0)
        self.assertEqual(restarted.public_key, self.service.public_key)
        restarted.tick()
        self.assertEqual(len(self.sent), 1)
        self.sessions[0].update(noticeID="running", unread=False, phase="running")
        restarted.tick()
        self.assertEqual(len(self.sent), 1)
        self.sessions[0].update(noticeID="next", unread=True, phase="complete")
        restarted.tick()
        self.assertEqual(len(self.sent), 2)

    @unittest.skipIf(os.name == 'nt', 'POSIX modes do not verify Windows ACLs')
    def test_push_files_have_private_posix_mode(self):
        for path in Path(self.temp.name).iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_desktop_ack_between_poll_and_delivery_cancels_push(self):
        self.service.state = lambda: {"sessions": [{**self.sessions[0], "noticeID": "new", "unread": True}]}
        calls = 0
        def state():
            nonlocal calls
            calls += 1
            return {"sessions": [{**self.sessions[0], "noticeID": "new", "unread": calls == 1}]}
        self.service.state = state
        self.service.tick()
        self.assertEqual(self.sent, [])

    def test_backoff_retries_only_still_unread_and_purges_expired(self):
        self.sessions[0]["noticeID"] = "new"
        self.code = 503
        self.service.tick(); self.service.tick()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("503", self.service.status(self.identity)["error"])
        self.service.next_attempt.clear()
        self.sessions[0]["unread"] = False
        self.service.tick()
        self.assertEqual(len(self.sent), 1)
        self.sessions[0]["unread"] = True
        self.code = 410
        self.service.tick()
        self.assertFalse(self.service.status(self.identity)["subscribed"])

    def test_explicit_unsubscribe_and_reset_revoke_delivery(self):
        self.service.unsubscribe(self.identity)
        self.sessions[0]["noticeID"] = "new"
        self.service.tick()
        self.assertEqual(self.sent, [])
        self.service.subscribe(self.sub)
        reset = push.PushService(self.temp.name, self.service.state, self.service.subject, sender=self.send, reset=True)
        self.assertFalse(reset.devices)
        self.assertEqual(reset.public_key, self.service.public_key)

    def test_delayed_test_is_visible_provider_acceptance_and_rate_limited(self):
        with patch.object(self.service.stop_event, "wait") as wait:
            self.assertEqual(self.service.test(self.identity), {"accepted": True})
            wait.assert_called_once_with(5)
        self.assertEqual(self.sent[0]["tag"], "pilot-test")
        self.assertEqual(self.sent[0]["session"], "")
        with self.assertRaises(ValueError):
            self.service.test(self.identity)

    def test_auto_review_unknown_permissions_and_progress_never_notify(self):
        for phase, reason, reviewer in [("running", "completion", "user"),
                                         ("waiting", "permission", "auto_review"),
                                         ("waiting", "permission", "guardian_subagent"),
                                         ("waiting", "permission", ""),
                                         ("waiting", "", ""), ("connected", "", "")]:
            with self.subTest(phase=phase, reviewer=reviewer):
                self.sessions[0].update(phase=phase, noticeID="new", noticeReason=reason, approvalReviewer=reviewer)
                self.service.tick()
                self.assertEqual(self.sent, [])

    def test_only_stable_attention_survives_grace_and_rechecks_before_send(self):
        now = [0]
        self.service.clock = lambda: now[0]
        self.service.settle_seconds = 8
        self.sessions[0].update(noticeID="permission-1", phase="waiting", noticeReason="permission", approvalReviewer="user")
        self.service.tick()
        now[0] = 7
        self.service.tick()
        self.assertEqual(self.sent, [])
        # Cached approval / a subsequent tool resumes before a human is needed.
        self.sessions[0].update(phase="running", unread=False)
        now[0] = 8
        self.service.tick()
        self.assertEqual(self.sent, [])
        # A genuinely pending question persists and emits once.
        self.sessions[0].update(phase="waiting", unread=True, noticeID="question", noticeReason="user_input")
        self.service.tick()
        now[0] = 16
        self.service.tick(); self.service.tick()
        self.assertEqual(len(self.sent), 1)
        # A fresh notice must wait its own full grace period.
        self.sessions[0].update(phase="complete", noticeID="done")
        now[0] = 17
        self.service.tick()
        now[0] = 24
        self.service.tick()
        self.assertEqual(len(self.sent), 1)
        now[0] = 25
        self.service.tick()
        self.assertEqual(len(self.sent), 2)

    def test_user_approval_question_completion_error_are_actionable(self):
        for index, fields in enumerate([
            {"phase": "waiting", "noticeReason": "permission", "approvalReviewer": "user"},
            {"phase": "waiting", "noticeReason": "user_input"},
            {"phase": "complete"}, {"phase": "error"},
        ]):
            self.sessions[0] = {"id": "a", "noticeID": str(index), "unread": True, **fields}
            self.service.tick()
        self.assertEqual(len(self.sent), 4)


class TailscaleTests(unittest.TestCase):
    def test_dedicated_port_preserves_other_services_and_confirms_private_proxy(self):
        with tempfile.TemporaryDirectory() as store:
            status = {"BackendState": "Running", "Self": {"DNSName": "mac.example.ts.net."}}
            unrelated = {"TCP": {"443": {"HTTPS": True}}, "Web": {"mac.example.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9000"}}}}}
            final = {"Web": {"mac.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:1234"}}}}}
            with patch.object(tailscale, "run_cli", side_effect=[json.dumps(status), json.dumps(unrelated), "ok", json.dumps(final)]) as run:
                self.assertEqual(tailscale.configure(store, 1234), "https://mac.example.ts.net:8443")
                self.assertEqual(run.call_args_list[2].args[0], ["serve", "--bg", "--yes", "--https=8443", "http://127.0.0.1:1234"])

    def test_foreign_port_and_public_funnel_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as store:
            status = {"BackendState": "Running", "Self": {"DNSName": "mac.example.ts.net."}}
            for config in [{"TCP": {"8443": {"HTTPS": True}}}, {"AllowFunnel": {"mac.example.ts.net:8443": True}}]:
                with patch.object(tailscale, "run_cli", side_effect=[json.dumps(status), json.dumps(config)]) as run:
                    with self.assertRaises(ValueError):
                        tailscale.configure(store, 1234)
                    self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
