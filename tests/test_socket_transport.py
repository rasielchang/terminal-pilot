import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from codex_live import StatusProxy


class SocketTransportTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'POSIX socket transport fixture requires subprocess pass_fds')
    def test_real_websocket_upgrade_masked_frames_and_json_rpc(self):
        server, child = socket.socketpair()
        server.settimeout(5)
        seen, failures = [], []
        def read(n):
            value=b''
            while len(value)<n:
                chunk=server.recv(n-len(value))
                if not chunk: raise EOFError()
                value+=chunk
            return value
        def receive():
            first, second=read(2)
            self.assertTrue(second & 128, 'Client frames must be masked')
            size=second & 127
            if size==126: size=struct.unpack('!H',read(2))[0]
            elif size==127: size=struct.unpack('!Q',read(8))[0]
            mask, body=read(4),read(size)
            return json.loads(bytes(b ^ mask[i % 4] for i,b in enumerate(body)))
        def send(value):
            data=json.dumps(value).encode()
            header=bytes([129,len(data)]) if len(data)<126 else b'\x81\x7e'+struct.pack('!H',len(data))
            server.sendall(header+data)
        def serve():
            try:
                headers=b''
                while not headers.endswith(b'\r\n\r\n'): headers+=read(1)
                self.assertTrue(headers.startswith(b'GET / HTTP/1.1'))
                fields=dict(line.split(b': ',1) for line in headers.split(b'\r\n')[1:] if b': ' in line)
                accept=base64.b64encode(hashlib.sha1(fields[b'Sec-WebSocket-Key']+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
                server.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
                for _ in range(3):
                    request=receive();seen.append(request['method'])
                    if request['method']=='initialized': continue
                    result={} if request['method']=='initialize' else {'thread':{'id':'exact','status':{'type':'idle'}}}
                    send({'id':request['id'],'result':result})
            except Exception as error: failures.append(error)
        thread=threading.Thread(target=serve);thread.start()
        original=subprocess.Popen
        def connected(command, **kwargs):
            kwargs['env']={**kwargs['env'],'PILOT_TEST_FD':str(child.fileno())}
            return original([command[0],str(Path(__file__).parent/'fixtures/socket_client.cjs'),*command[1:]],pass_fds=(child.fileno(),),**kwargs)
        try:
            with patch('codex_live.subprocess.Popen',connected):
                proxy=StatusProxy(shutil.which('codex') or shutil.which('node'),'/synthetic',socket='/synthetic/server.sock')
            try: self.assertEqual(proxy.status('exact'),'idle')
            except Exception as error: raise AssertionError((seen,[str(e) for e in failures])) from error
            finally: proxy.close()
        finally:
            thread.join(6);server.close();child.close()
        if failures: raise failures[0]
        self.assertEqual(seen,['initialize','initialized','thread/read'])
