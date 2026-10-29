"""Runtime discovery for Finder launches and portable app bundles; no shell startup execution."""
import json
import os
from pathlib import Path
import re
import shutil


def store_path():
    return Path(os.environ.get('TERMINAL_PILOT_STORE', Path.home() / 'Library/Application Support/Terminal Pilot'))


def find_tool(name):
    try:
        configured = json.loads((store_path() / 'tools.json').read_text()).get(name)
    except (OSError, ValueError, AttributeError):
        configured = None
    if configured:
        return configured if Path(configured).is_absolute() and os.access(configured, os.X_OK) else None
    if name == 'node':
        bundled = Path(__file__).resolve().parent / 'runtime/node/bin/node'
        if bundled.is_file() and os.access(bundled, os.X_OK): return str(bundled)
    found = shutil.which(name)
    if found: return found
    if os.name == 'nt' and name == 'tailscale':
        candidate = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale/tailscale.exe'
        if candidate.is_file(): return str(candidate)
    home = Path.home()
    directories = [home / p for p in ('.local/bin', '.volta/bin', '.npm-global/bin', '.cargo/bin')]
    directories += [Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
    nvm = home / '.nvm/versions/node'
    if nvm.is_dir():
        directories += [p / 'bin' for p in sorted(nvm.iterdir(), key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.name)), reverse=True)]
    for directory in directories:
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK): return str(candidate)
    if name == 'tailscale':
        app = Path('/Applications/Tailscale.app/Contents/MacOS/Tailscale')
        if app.is_file() and os.access(app, os.X_OK): return str(app)
    return None
