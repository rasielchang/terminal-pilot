"""One local app-server per Terminal work, with the real Codex TUI attached.

The Terminal wrapper owns both children. Restarting Pilot does not stop Codex.
No ports are exposed and no global Codex configuration or approval policy changes.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time


def valid_socket(value):
    if not isinstance(value, str) or not value: return False
    path = Path(value)
    try:
        root = path.parent.resolve(strict=True)
        info = root.stat()
        if not (path.name == 'server.sock' and root.parent == Path('/tmp').resolve()
                and root.name.startswith('tp-codex-') and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o700):
            return False
        # Codex deliberately places a short socket in its per-user runtime
        # directory and publishes the requested path as a symlink (macOS limit).
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            parent = resolved.parent
            owner = parent.stat()
            if (parent != Path(f'/tmp/codex-daemon-{os.getuid()}').resolve()
                    or not re.fullmatch(r'[a-f0-9]{64}', resolved.name)
                    or owner.st_uid != os.getuid() or stat.S_IMODE(owner.st_mode) & 0o022):
                return False
        socket = path.stat()
        return socket.st_uid == os.getuid() and stat.S_ISSOCK(socket.st_mode)
    except OSError:
        return False


def run_managed(binary, backend_args, extra, environment, register):
    # bridge.launch has already selected the project. Also pass the remote
    # client's explicit workspace override so resume scoping does not rely on
    # the child process cwd implicitly becoming the remote workspace.
    project_directory = os.getcwd()
    directory = Path(tempfile.mkdtemp(prefix='tp-codex-', dir='/tmp'))
    socket = directory / 'server.sock'
    endpoint = 'unix://' + str(socket)
    backend = tui = None
    old_handlers = {}
    def stop(_signum, _frame):
        if tui and tui.poll() is None: tui.terminate()
        elif backend and backend.poll() is None: backend.terminate()
    try:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            old_handlers[signum] = signal.signal(signum, stop)
        # Ctrl-C belongs to the real TUI. A callable handler is reset on exec,
        # unlike SIG_IGN, so the child still receives normal Terminal interrupts.
        old_handlers[signal.SIGINT] = signal.signal(signal.SIGINT, lambda *_: None)
        backend = subprocess.Popen([binary, *backend_args, 'app-server', '--listen', endpoint],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 15
        while not valid_socket(str(socket)):
            if backend.poll() is not None:
                raise RuntimeError('Codex 手機核准通道啟動失敗，未開啟新對話。請更新 Codex 後重試。')
            if time.monotonic() >= deadline:
                raise RuntimeError('Codex 手機核准通道啟動逾時，未開啟新對話。')
            time.sleep(.05)
        register(str(socket))
        print('手機核准通道已啟動；進入對話後自動接通，電腦仍使用原本的 Codex 操作介面。', flush=True)
        # Same foreground process group and real Terminal, no pseudo-terminal or tmux.
        tui = subprocess.Popen([binary, '--remote', endpoint, '--cd', project_directory, *extra],
                               env=environment)
        return tui.wait()
    finally:
        for child in (tui, backend):
            if child and child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=2)
        for signum, handler in old_handlers.items(): signal.signal(signum, handler)
        shutil.rmtree(directory)
