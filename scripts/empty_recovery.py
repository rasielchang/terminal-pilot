"""Evidence for a Pilot-created Claude identity closed before its first input."""
import hashlib
import json
from pathlib import Path
import uuid


def empty_source(store, target):
    if target.get('tool') != 'claude' or target.get('mode') != 'specific': return None
    try:
        identity = str(uuid.UUID(target['thread']))
        home = Path(target.get('agentHome') or Path.home()/'.claude')
        projects = home/'projects'
        # Fail on inaccessible inventory; missing history alone is never proof.
        directories = list(projects.iterdir())
        if any((p/(identity+'.jsonl')).exists() or (p/(identity+'.jsonl')).is_symlink() for p in directories if p.is_dir()): return None
        matches = []
        for path in (Path(store)/'sessions').glob('*.json'):
            if path.is_symlink() or path.stat().st_size > 128000: continue
            raw = path.read_bytes(); value = json.loads(raw)
            if value.get('claudeSessionID') != identity or value.get('cwd') != target['directory']: continue
            transcript = Path(value.get('claudeTranscript',''))
            if (value.get('tool') != 'claude' or value.get('phase') != 'ended' or value.get('eventSequence') != 3
                    or value.get('noticeAt') != 0 or value.get('noticeID') or value.get('claudeHome') != str(home)
                    or transcript.name != identity+'.jsonl' or transcript.parent.parent != projects
                    or transcript.exists() or transcript.is_symlink()): return None
            uuid.UUID(value['id'])
            matches.append({'session':value['id'],'evidence':hashlib.sha256(raw).hexdigest()})
        return matches[0] if len(matches) == 1 else None
    except (ValueError, OSError, KeyError, TypeError): return None
