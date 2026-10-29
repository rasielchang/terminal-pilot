"""Exact HTTPS proxy origin and Serve route ownership boundaries."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pilot_http import Handler
from tailscale_web import configure

ORIGIN = 'https://pilot.example.ts.net:8443'
AUTHORITY = 'pilot.example.ts.net:8443'


class HTTPSBoundaryTests(unittest.TestCase):
    def trusted(self, proxy=True, client='127.0.0.1', host=AUTHORITY, origin=ORIGIN):
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(proxy=proxy, server_port=8766,
            application=SimpleNamespace(hosts={'127.0.0.1', 'localhost'}, https_origin=ORIGIN))
        handler.client_address = (client, 123)
        handler.headers = {'Host': host, 'Origin': origin}
        handler.reply = lambda *args: None
        return handler.trusted_request()

    def test_exact_https_origin_works_only_on_loopback_proxy_listener(self):
        self.assertTrue(self.trusted())
        self.assertFalse(self.trusted(proxy=False))
        self.assertFalse(self.trusted(client='100.64.0.2'))

    def test_other_hosts_and_origins_are_rejected(self):
        self.assertFalse(self.trusted(host='attacker.test:8443'))
        self.assertFalse(self.trusted(origin='https://attacker.test'))
        self.assertFalse(self.trusted(origin='http://' + AUTHORITY))

    def test_localhost_listener_still_works(self):
        self.assertTrue(self.trusted(proxy=False, host='127.0.0.1:8766', origin='http://127.0.0.1:8766'))

    def configure(self, config):
        calls = []
        def cli(args):
            calls.append(args)
            if args == ['status', '--json']:
                return json.dumps({'BackendState': 'Running', 'Self': {'DNSName': 'pilot.example.ts.net.'}})
            return json.dumps(config)
        with tempfile.TemporaryDirectory() as store, patch('tailscale_web.run_cli', cli):
            with self.assertRaises(ValueError): configure(store, 8766)
        self.assertFalse(any('--bg' in args for args in calls))

    def test_existing_unowned_route_is_never_overwritten(self):
        self.configure({'TCP': {'8443': {'HTTPS': True}}, 'Web': {
            AUTHORITY: {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:9999'}}}}})

    def test_funnel_port_is_never_reused(self):
        self.configure({'AllowFunnel': {AUTHORITY: True}})

    def test_new_route_uses_only_dedicated_port_without_reset(self):
        calls = []
        def cli(args):
            calls.append(args)
            if args == ['status', '--json']:
                return json.dumps({'BackendState': 'Running', 'Self': {'DNSName': 'pilot.example.ts.net.'}})
            if args == ['serve', 'status', '--json']:
                if len(calls) < 4: return '{}'
                return json.dumps({'Web': {AUTHORITY: {'Handlers': {'/': {'Proxy': 'http://127.0.0.1:8766'}}}}})
            return ''
        with tempfile.TemporaryDirectory() as store, patch('tailscale_web.run_cli', cli):
            self.assertEqual(configure(store, 8766), ORIGIN)
            self.assertEqual(json.loads((Path(store) / 'https-target.json').read_text())['origin'], ORIGIN)
        self.assertIn(['serve', '--bg', '--yes', '--https=8443', 'http://127.0.0.1:8766'], calls)
        self.assertFalse(any('reset' in args or 'off' in args for args in calls))


if __name__ == '__main__': unittest.main()
