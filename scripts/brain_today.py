"""Read-through view of canonical Brain cards. No scheduling, dispatch or inference."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

DONE = {'completed', 'completed_closed'}
STOPPED = {'ended_by_user', 'engineering_closed_by_user', 'cancelled'}
RUNNING = {'in_progress', 'opening', 'running'}
WAITING = {'waiting', 'waiting_external', 'waiting_external_uat', 'waiting_external_validation', 'paused', 'deferred', 'blocked', 'pending'}
USER = {'needs_user', 'waiting_on_user', 'waiting_iphone_acceptance', 'waiting_user_use', 'test_delivery_prepared_awaiting_release_approval'}


def stamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool): return value
    if isinstance(value, str):
        try: return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except ValueError: pass
    return None


def day(value):
    value = stamp(value)
    return datetime.fromtimestamp(value).astimezone().date().isoformat() if value is not None else ''


def text(value, limit=600):
    return value[:limit] if isinstance(value, str) else ''


def link(value):
    if not isinstance(value, str): return ''
    try: parsed = urlsplit(value)
    except ValueError: return ""
    return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else ''


class TodayCards:
    def __init__(self, root, clock=time.time):
        self.root, self.clock, self.cache = Path(root), clock, {}

    def read(self):
        """Retain last known content on read failure; never persist another ledger."""
        found, errors = {}, []
        try:
            projects = self.root / 'projects'
            if not projects.is_dir(): raise OSError('cards_unavailable')
            paths = list(projects.glob('*/work-items/*.json'))
        except OSError:
            return {key: (value, True) for key, value in self.cache.items()}, ['cards_unavailable']
        for path in sorted(paths):
            key = path.relative_to(self.root).as_posix()
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()): raise ValueError('unsafe_card')
                if path.stat().st_size > 512_000: raise ValueError('oversize_card')
                value = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(value, dict) or not isinstance(value.get('status'), str): raise ValueError('invalid_card')
                found[key] = (value, False)
                self.cache[key] = value
            except (OSError, ValueError):
                errors.append(key)
                if key in self.cache: found[key] = (self.cache[key], True)
        self.cache = {key: value for key, (value, _) in found.items()}
        return found, errors

    @staticmethod
    def matches(card, record):
        if card.get('work'): return card['work'] == record['id']
        resume = card.get('resume') if isinstance(card.get('resume'), dict) else {}
        thread = card.get('thread') or resume.get('thread_id') or resume.get('session_id')
        target = record.get('target', {})
        return bool(thread and thread == target.get('thread') and (not resume.get('tool') or resume['tool'] == target.get('tool')))

    def snapshot(self, sessions=(), records=(), jobs=()):
        now = self.clock()
        today = datetime.fromtimestamp(now).astimezone().date()
        cards, errors = self.read()
        groups = {key: [] for key in ('needs_user', 'in_progress', 'waiting', 'completed')}
        deadlines, omitted = [], 0
        for path, (card, unavailable) in cards.items():
            status = card['status']
            agenda = card.get('agenda') if isinstance(card.get('agenda'), dict) else {}
            selected = agenda.get('date') == today.isoformat()
            previous = bool(day(agenda.get('date')) and agenda['date'] < today.isoformat())
            completion = next((day(card.get(k)) for k in ('completed_at', 'completed_on', 'completed_date', 'completion_date') if day(card.get(k))), '')
            verified = next((stamp(card.get(k)) for k in ('updated_at', 'reviewed_at', 'last_verified') if stamp(card.get(k)) is not None), None)
            wait = card.get('waiting_on_user')
            wait = wait if isinstance(wait, dict) else {}
            waiting_user = bool(wait and wait.get('status', 'pending') not in {'resolved', 'answered', 'cancelled', 'superseded'}) or ('waiting_on_user' not in card and status in USER)
            observed = [r for r in records if self.matches(card, r)]
            record = observed[0] if len(observed) == 1 else {}
            instruction = record.get('instruction', {})
            expires = card.get('valid_until') or wait.get('expires_at')
            expired = stamp(expires) is not None and stamp(expires) <= now
            source_at = stamp(wait.get('source_at')) or verified
            newer_input = bool(waiting_user and source_at and (stamp(instruction.get('source', {}).get('created')) or 0) > source_at)
            changed_source = bool(record and verified and (stamp(record.get('latest', {}).get('source', {}).get('created')) or 0) > verified)
            stale = unavailable or verified is None or now - verified > 86400
            source_gap = bool(record and record.get('coverage') != 'current')
            if status in DONE: group = 'completed' if completion == today.isoformat() else None
            elif status in STOPPED: group = None
            elif not selected and not previous: group = None
            elif not selected or newer_input or expired: group = 'waiting'
            elif waiting_user: group = 'needs_user'
            elif status in RUNNING: group = 'in_progress'
            else: group = 'waiting'
            exact = []
            for s in sessions:
                resume = card.get('resume') if isinstance(card.get('resume'), dict) else {}
                thread = card.get('thread') or resume.get('thread_id') or resume.get('session_id') or record.get('target', {}).get('thread')
                sid = card.get('session') or card.get('pilot_session') or record.get('session')
                if thread and thread in (s.get('codexThreadID'), s.get('claudeSessionID')) and (not sid or sid == s.get('id')):
                    exact.append(s)
            target = {'session': exact[0]['id']} if len(exact) == 1 else None
            approval = bool(target and exact[0].get('runtime', {}).get('approvals'))
            if approval and selected and status not in DONE | STOPPED:
                group = 'needs_user'
            job_id = card.get('job') or card.get('pilot_job')
            job = next((j for j in jobs if j['id'] == job_id and j.get('canOpen')), None)
            if target is None and job: target = {'job': job_id}
            source = card.get('source_event') or card.get('result_source') or card.get('user_source') or card.get('status_source') or card.get('source')
            asana = card.get('asana') if isinstance(card.get('asana'), dict) else {}
            due = card.get('due_at') or card.get('due_on')
            asana_url = link(card.get('asana_url') or asana.get('url') or asana.get('permalink_url'))
            row = {'id': path, 'project': path.split('/')[1], 'title': text(card.get('title'), 160),
                   'status': status, 'result': text(card.get('result'), 240), 'next': text(card.get('next_action') or card.get('next'), 240),
                   'due': text(due), 'validUntil': stamp(expires), 'updatedAt': verified, 'source': text(source, 240), 'reference': asana_url,
                   'referenceCheckedAt': text(asana.get('verified_on') or card.get('last_asana_sync'), 80),
                   'rank': agenda.get('rank') if type(agenda.get('rank')) is int else 999,
                   'reason': text(agenda.get('reason'), 160), 'target': target,
                   'stale': stale or source_gap, 'unconfirmed': changed_source or newer_input or expired or status not in DONE | STOPPED | RUNNING | WAITING | USER,
                   'waitingFor': text(wait.get('action') or card.get('waiting_on') or card.get('blocker'), 240),
                   'releaseCondition': text(wait.get('release_condition'), 240),
                   'note': '原專員有正式核准待處理' if approval and status not in DONE | STOPPED else '原對話有較新回覆，等待正式核對' if newer_input else '尚未排入今天' if previous and not selected and status not in DONE else '',
                   'completionDate': completion}
            if status in DONE:
                row.update(waitingFor='', releaseCondition='')
            if expired and status not in DONE | STOPPED and not approval:
                row.update(waitingFor='', releaseCondition='核對期限與最新決定', next='原動作已過期，待重新核對', note='原動作已過期，待重新核對')
            if newer_input and status not in DONE | STOPPED:
                row.update(waitingFor='', releaseCondition='核對較新回覆並更新原工作卡', next='核對原對話的新回覆')
            if status not in DONE | STOPPED and day(due) and day(due) <= (today + timedelta(days=7)).isoformat():
                deadlines.append({k: row[k] for k in ('id', 'title', 'due', 'reference', 'referenceCheckedAt')})
            if group: groups[group].append(row)
            elif status not in DONE | STOPPED: omitted += 1
        for rows in groups.values(): rows.sort(key=lambda r: (r['rank'], r['due'] or '9999', r['id']))
        deadlines.sort(key=lambda r: (r['due'], r['id']))
        return {'date': today.isoformat(), 'readAt': now, 'source': 'projects/*/work-items/*.json',
                'groups': groups, 'deadlines': deadlines, 'notScheduled': omitted, 'errors': errors,
                'revision': hashlib.sha256(json.dumps(cards, sort_keys=True).encode()).hexdigest()}

    def sync_summary(self, record, summary, waiting, evidence):
        """Only an already-validated Brain summary can update an exact original card.

        No card creation, no completion inference, no schedule/Asana/authority edits.
        A newer canonical decision wins over a summary of older source events.
        """
        import os
        import tempfile
        cards, errors = self.read()
        matches = [(path, card) for path, (card, failed) in cards.items() if not failed and self.matches(card, record)]
        if len(matches) != 1: return {'state': 'unmatched' if not matches else 'ambiguous'}
        relative, card = matches[0]
        if card['status'] in DONE | STOPPED: return {'state': 'terminal_card', 'card': relative}
        latest = max((stamp(e.get('source', {}).get('created')) or 0 for e in evidence), default=0)
        if stamp(card.get('updated_at')) is None:
            return {'state': 'unverified_card', 'card': relative}
        if not latest or stamp(card['updated_at']) > latest:
            return {'state': 'newer_card', 'card': relative}
        path = self.root / relative
        raw = path.read_bytes()
        if json.loads(raw) != card: raise ValueError('正式工作卡已有較新變更，請重新核對。')
        updated = dict(card)
        if summary['result']: updated['result'] = summary['result']
        if summary['next']: updated['next_action'] = summary['next']
        updated.update(updated_at=datetime.fromtimestamp(self.clock()).astimezone().isoformat(),
                       source_event=max(evidence, key=lambda e: stamp(e.get('source', {}).get('created')) or 0)['id'])
        if waiting == 'pending':
            updated['waiting_on_user'] = {'action': summary['blocker'], 'source_at': latest,
                'sources': [e['id'] for e in evidence], 'release_condition': '取得較新回覆後，由 Brain 核對是否解除'}
        elif waiting == 'resolved': updated['waiting_on_user'] = None
        fd, temporary = tempfile.mkstemp(prefix='.' + path.stem + '-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(updated, out, ensure_ascii=False, indent=2); out.write('\n'); out.flush(); os.fsync(out.fileno())
            if path.read_bytes() != raw: raise ValueError('正式工作卡已有較新變更，請重新核對。')
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return {'state': 'updated', 'card': relative}
