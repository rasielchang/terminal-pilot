"""Invoked by the bundled Python in an isolated user environment; no live agent or API."""
import base64
import hashlib
import json
import os
import shlex
from pathlib import Path
import socket
import ssl
import struct
import subprocess
import sys
import threading
from unittest.mock import patch

resource, fixture = (Path(p).resolve() for p in sys.argv[1:])
sys.path.insert(0, str(resource))
import web_server, bridge, brain_codex, voice_service, runtime_paths
from codex_live import StatusProxy
from brain_unified import UnifiedBrain
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

assert AESGCM.generate_key(bit_length=256)
assert ssl.create_default_context().get_ca_certs(), 'Bundled HTTPS has no trusted roots'
assert runtime_paths.find_tool('node').startswith(str(resource.parent))
assert runtime_paths.find_tool('codex') is None, 'Clean user unexpectedly found Codex'
assert not (resource / '__pycache__').exists()
# Execute the actual advertised CLI in a fresh agent-like environment. The
# agent does not inherit the desktop service's bytecode environment settings.
brain = UnifiedBrain(Path.home() / 'tool-probe', lambda: {'sessions': []},
                     lambda _: [], lambda _: None, timeline=lambda *_args, **_kwargs: {})
try:
    command = next(line.removeprefix('工具入口：') for line in brain.tool_instructions.splitlines()
                   if line.startswith('工具入口：'))
    tool_env = {k: v for k, v in os.environ.items()
                if k not in {'PYTHONDONTWRITEBYTECODE', 'PYTHONPYCACHEPREFIX', 'CODEX_THREAD_ID'}}
    subprocess.run([*shlex.split(command), '--help'], env=tool_env,
                   stdout=subprocess.DEVNULL, timeout=10, check=True)
finally:
    brain.close()
assert not list(resource.rglob('*.pyc')), 'Brain CLI changed the signed bundle with bytecode caches'
server, child = socket.socketpair(); server.settimeout(8)
failures, seen = [], []


def read(n):
    data = b''
    while len(data) < n:
        part = server.recv(n - len(data))
        if not part: raise EOFError()
        data += part
    return data


def serve():
    try:
        header = b''
        while not header.endswith(b'\r\n\r\n'): header += read(1)
        fields = dict(line.split(b': ', 1) for line in header.split(b'\r\n')[1:] if b': ' in line)
        accept = base64.b64encode(hashlib.sha1(fields[b'Sec-WebSocket-Key'] + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
        server.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accept + b'\r\n\r\n')
        for _ in range(3):
            first, second = read(2); assert second & 128
            size = second & 127
            if size == 126: size = struct.unpack('!H', read(2))[0]
            if size == 127: size = struct.unpack('!Q', read(8))[0]
            mask, body = read(4), read(size)
            request = json.loads(bytes(b ^ mask[i % 4] for i, b in enumerate(body)))
            seen.append(request['method'])
            if request['method'] == 'initialized': continue
            result = {} if request['method'] == 'initialize' else {'thread': {'id': 'portable-test', 'status': {'type': 'idle'}}}
            data = json.dumps({'id': request['id'], 'result': result}).encode()
            server.sendall(bytes([129, len(data)]) + data)
    except BaseException as error: failures.append(error)


thread = threading.Thread(target=serve); thread.start()
original = subprocess.Popen


def connected(command, **kwargs):
    kwargs['env'] = {**kwargs['env'], 'PILOT_TEST_FD': str(child.fileno())}
    return original([command[0], str(fixture), *command[1:]], pass_fds=(child.fileno(),), **kwargs)


try:
    with patch('codex_live.subprocess.Popen', connected):
        proxy = StatusProxy('/synthetic/codex', Path.home(), socket='/synthetic/socket')
    try: assert proxy.status('portable-test') == 'idle'
    finally: proxy.close()
finally:
    child.close(); server.close(); thread.join(10)
assert not failures, failures
assert seen == ['initialize', 'initialized', 'thread/read'], seen
print('Bundled service imports, TLS trust roots and actual Node/ws transport passed.')
