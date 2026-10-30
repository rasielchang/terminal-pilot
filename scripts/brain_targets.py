"""Conversation identity and bounded Pilot-owned metadata, never agent selection by cwd."""
import hashlib
import json
from pathlib import Path


def target_id(tool, directory, thread):
    return 'conversation:' + hashlib.sha256(json.dumps([tool, directory, thread]).encode()).hexdigest()[:32]


def conversation_target(s):
    if not isinstance(s, dict): return None
    tool, directory = s.get('tool'), s.get('directory') or s.get('cwd')
    thread = s.get('codexThreadID') if tool == 'codex' else s.get('claudeSessionID')
    if (tool not in {'codex', 'claude'} or not isinstance(directory, str) or not 0 < len(directory) <= 4096
            or not isinstance(thread, str) or not 0 < len(thread) <= 256): return None
    return {'id': target_id(tool, directory, thread), 'tool': tool, 'directory': directory,
            'thread': thread, 'mode': 'specific', 'title': s.get('title') or s.get('label') or Path(directory).name,
            'updated': s.get('updatedAt', 0)}


def stored_targets(store):
    """Only our own event metadata; no recursive agent history or credentials."""
    directory = Path(store) / 'sessions'
    if directory.is_symlink() or not directory.is_dir(): return []
    values = []
    for path in directory.glob('*.json'):
        if path.is_symlink() or path.stat().st_size > 128_000: continue
        try:
            value = conversation_target(json.loads(path.read_text(encoding='utf-8')))
            if value: values.append(value)
        except (OSError, ValueError, TypeError): continue
    return values
