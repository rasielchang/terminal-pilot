"""Start/status/stop the local Windows assistant without a visible Terminal."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    parser.add_argument('--store', type=Path, default=ROOT / '.runtime/windows-brain')
    parser.add_argument('--brain-root', type=Path)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    parser.add_argument('--codex-binary', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--name', default='使用者')
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--tailscale', action='store_true', help='Configure private HTTPS Serve on port 8443')
    parser.add_argument('--proxy-port', type=int, default=8766)
    args = parser.parse_args()
    ready = args.store / 'background-ready.json'
    token_path = args.store / 'background-auth.json'
    def running():
        if not ready.exists() or not token_path.exists(): return None
        info = json.loads(ready.read_text(encoding='utf-8'))
        token = json.loads(token_path.read_text(encoding='utf-8'))['token']
        try:
            req = Request(f"http://127.0.0.1:{info['port']}/api/state", headers={'Authorization': 'Bearer ' + token})
            with urlopen(req, timeout=2) as response: json.load(response)
            return info, token
        except OSError: return None
    current = running()
    if args.action == 'start' and args.restart and current:
        info, token = current
        req = Request(f"http://127.0.0.1:{info['port']}/api/service/stop", data=b'{}',
                      headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urlopen(req, timeout=10) as response: json.load(response)
        for _ in range(100):
            if not ready.exists(): break
            time.sleep(.1)
        if ready.exists(): raise RuntimeError('Previous service has not finished shutting down')
        current = None
    if args.action == 'status':
        print(f"http://127.0.0.1:{current[0]['port']}" if current else 'Stopped')
        if current and current[0].get('httpsURL'): print(current[0]['httpsURL'])
        return
    if args.action == 'stop':
        if current:
            info, token = current
            req = Request(f"http://127.0.0.1:{info['port']}/api/service/stop", data=b'{}',
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            with urlopen(req, timeout=10) as response: json.load(response)
        print('Stopped')
        return
    if current:
        if args.tailscale and not current[0].get('httpsURL'):
            parser.error('Use start --restart --tailscale to enable HTTPS on the running host')
        print(f"Already running: http://127.0.0.1:{current[0]['port']}")
        if current[0].get('httpsURL'): print(current[0]['httpsURL'])
        return
    binary = args.codex_binary
    if not binary:
        shim = shutil.which('codex.cmd')
        if shim:
            candidates = list((Path(shim).parent / 'node_modules/@openai/codex/node_modules').glob(
                '@openai/codex-win32-*/vendor/*/bin/codex.exe'))
            if len(candidates) == 1: binary = candidates[0]
    if not binary or not binary.is_file(): parser.error('Specify --codex-binary with the installed codex.exe path')
    root = args.brain_root or args.store / 'workspace'
    root.mkdir(parents=True, exist_ok=True)
    rules = root / 'AGENTS.md'
    if not rules.exists():
        rules.write_text('你是 Company Brain 個人助理。使用台灣繁體中文。只使用此工作區及使用者明確提供的資料。\n'
                         '公司資料來源尚未設定，找不到資料時明說，不編造政策或聲稱已連接公司系統。\n', encoding='utf-8')
    args.store.mkdir(parents=True, exist_ok=True)
    if args.tailscale:
        sys.path.insert(0, str(PILOT / 'scripts'))
        from tailscale_web import configure
        configure(args.store, args.proxy_port)
    https_args = []
    route_file = args.store / 'https-target.json'
    if route_file.exists():
        route = json.loads(route_file.read_text(encoding='utf-8'))
        target = urlsplit(route['target'])
        if target.scheme != 'http' or target.hostname != '127.0.0.1' or not target.port:
            parser.error('Invalid saved Tailscale loopback target')
        https_args = ['--https-origin', route['origin'], '--proxy-port', str(target.port)]
    # Retain a failed launch's diagnostics; never include credentials in output.
    with (args.store / 'service.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-X', 'utf8', str(PILOT / 'scripts/brain_server.py'),
            '--store', str(args.store.resolve()), '--brain-root', str(root.resolve()),
            '--codex-home', str(args.codex_home), '--codex-binary', str(binary),
            '--port', str(args.port), '--name', args.name, *https_args], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0)
    for _ in range(100):
        current = running()
        if current:
            print(f"http://127.0.0.1:{current[0]['port']}")
            if current[0].get('httpsURL'): print(current[0]['httpsURL'])
            print('Pairing code: ' + (args.store / 'service.log').read_text(encoding='utf-8').split('pairing code: ')[-1].splitlines()[0])
            return
        if process.poll() is not None: break
        time.sleep(.2)
    raise RuntimeError(f'Service did not become ready; see {args.store / "service.log"}')


if __name__ == '__main__': main()
