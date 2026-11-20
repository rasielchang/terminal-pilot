"""Read-through view of canonical Brain cards. No scheduling, dispatch or inference."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from pathlib import Path
import time
from urllib.parse import urlsplit

DONE = {'completed', 'completed_closed', 'research_completed'}
STOPPED = {'ended_by_user', 'engineering_closed_by_user', 'cancelled', 'canceled', 'stopped', 'tracking_stopped', 'merged'}
EXCLUDED_TRACKING = {'excluded_until_user_reopens', 'stopped', 'cancelled', 'canceled'}
EXTERNAL = {'waiting_external', 'waiting_external_uat', 'waiting_external_validation'}
DEFERRED = {'deferred', 'deferred_until_wow_work', 'deferred_until_hardware_check', 'retirement_completed_followup_scheduled', 'phase_completed_first_schedule_pending'}
RUNNING = {'in_progress', 'opening', 'running'}
READY = {'pending', 'todo', 'ready', 'not_started', 'recurring'}
WAITING = {'waiting', 'waiting_external', 'waiting_external_uat', 'waiting_external_validation', 'paused', 'deferred', 'blocked', 'pending'}
USER = {'needs_user', 'waiting_on_user', 'waiting_iphone_acceptance', 'waiting_user_use', 'test_delivery_prepared_awaiting_release_approval'}


def stamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool): return value
    if isinstance(value, str):
        try: return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, OSError, OverflowError): pass
    return None


def day(value):
    # A calendar date is not UTC midnight (which can become yesterday locally).
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try: return datetime.fromisoformat(value).date().isoformat()
        except ValueError: return ''
    value = stamp(value)
    return datetime.fromtimestamp(value, timezone.utc).astimezone().date().isoformat() if value is not None else ''


def text(value, limit=600):
    return value[:limit] if isinstance(value, str) else ''


def link(value):
    if not isinstance(value, str): return ''
    try: parsed = urlsplit(value)
    except ValueError: return ""
    return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else ''


def tracking_group(card, today):
    """Display grouping from structured fields only; never a dispatch decision."""
    policy = card.get('notification_policy') if isinstance(card.get('notification_policy'), dict) else {}
    if card['status'] == 'recurring' or policy.get('frequency') in ('daily', 'weekly', 'monthly'): return 'recurring'
    agenda = card.get('agenda') if isinstance(card.get('agenda'), dict) else {}
    scheduling = card.get('scheduling') if isinstance(card.get('scheduling'), dict) else {}
    planned = card.get('planned_week') if isinstance(card.get('planned_week'), dict) else {}
    scheduled = card.get('remind_on') or card.get('start_on') or scheduling.get('start_on') or planned.get('start')
    if card['status'] in DEFERRED | {'paused'} or day(scheduled) or day(agenda.get('date')) > today:
        return 'scheduled'
    if card['status'] in EXTERNAL: return 'external'
    return 'other'


def excluded(card):
    # Legacy paused/user-owned cards are excluded by the canonical work-state
    # index. Explicit active tracking can retain a paused, still-tracked matter.
    tracking = card.get('tracking')
    state = tracking.get('state') if isinstance(tracking, dict) else tracking
    state = state if isinstance(state, str) else None
    return (card['status'] in STOPPED or state in EXCLUDED_TRACKING or
            (state != 'active' and (card['status'] == 'paused' or card.get('tracking_owner') == 'user')))


def postponed(card, today):
    agenda = card.get('agenda') if isinstance(card.get('agenda'), dict) else {}
    scheduling = card.get('scheduling') if isinstance(card.get('scheduling'), dict) else {}
    planned = card.get('planned_week') if isinstance(card.get('planned_week'), dict) else {}
    return (card['status'] in DEFERRED | {'paused'} or any(day(value) > today for value in (
        agenda.get('date'), card.get('remind_on'), card.get('start_on'),
        scheduling.get('start_on'), planned.get('start'))))


class TodayCards:
    def __init__(self, root, clock=time.time):
        self.root, self.clock, self.cache = Path(root), clock, {}

    @staticmethod
    def validate_reference(identity):
        if (not isinstance(identity, str) or len(identity) > 400 or
                not re.fullmatch(r'projects/[^/\\\x00-\x1f]+/work-items/[^/\\\x00-\x1f]+\.json', identity) or
                any(part in {'.', '..'} for part in identity.split('/'))):
            raise ValueError('工作卡引用識別不正確。')
        return identity

    def reference(self, identity):
        """Read only this exact card afresh; never substitute cached/private text."""
        self.validate_reference(identity)
        result = {'canonicalID': identity, 'title': '', 'availability': 'unavailable', 'contextOnly': True}
        path = self.root / identity
        try:
            if any(p.is_symlink() for p in (path, path.parent, path.parent.parent, path.parent.parent.parent)):
                raise ValueError('unsafe_card')
            if not path.resolve().is_relative_to(self.root.resolve()) or path.stat().st_size > 512_000:
                raise ValueError('unsafe_card')
            card = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(card, dict) or not isinstance(card.get('status'), str): raise ValueError('invalid_card')
        except FileNotFoundError:
            return {**result, 'availability': 'missing'}
        except (OSError, ValueError):
            return result
        topic, job = card.get('topic') or card.get('pilot_topic'), card.get('job') or card.get('pilot_job')
        return {**result, 'availability': 'available', 'title': text(card.get('title'), 160),
                'status': text(card['status'], 100), 'updatedAt': stamp(card.get('updated_at')),
                'topic': topic if isinstance(topic, str) else '',
                'job': job if isinstance(job, str) else ''}

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

    def closeout_asana(self, record, session):
        """Resolve the existing canonical work card, never a task mentioned in prose."""
        cards, errors = self.read()
        if errors:
            raise ValueError('工作卡無法完整核對；請確認原工作與 Asana 的唯一對應。')
        matches = [(key, card) for key, (card, _) in cards.items() if self.matches(card, record)]
        if len(matches) != 1:
            raise ValueError('原工作沒有唯一的既有工作卡；請核對 Asana 對應。')
        key, card = matches[0]
        resume = card.get('resume') if isinstance(card.get('resume'), dict) else {}
        session_ref = card.get('session') or card.get('pilot_session')
        if (session_ref and session_ref != session or
                any(thread and thread != record['target']['thread'] for thread in
                    (card.get('thread'), resume.get('thread_id'), resume.get('session_id'))) or
                resume.get('tool') not in (None, '', record['target']['tool'])):
            raise ValueError('既有工作卡與此原對話或 session 的對應不一致。')
        task, project = self.asana_ids(card)
        if not task: raise ValueError('原工作卡沒有一致的既有 Asana 任務與專案；請核對對應。')
        if any(other_key != key and self.asana_ids(other)[0] == task for other_key, (other, _) in cards.items()):
            raise ValueError('同一 Asana 任務出現在多張工作卡；請核對對應。')
        return {'card': key, 'task': task, 'project': project}

    @staticmethod
    def asana_ids(card):
        asana = card.get('asana') if isinstance(card.get('asana'), dict) else {}
        tasks, projects = set(), set()
        gid = r'[1-9][0-9]{0,29}'
        for url in (card.get('asana_url'), asana.get('url'), asana.get('permalink_url')):
            if not url: continue
            if not isinstance(url, str): return '', ''
            match = (re.fullmatch(r'https://app\.asana\.com/0/(0|' + gid + r')/(' + gid + r')(?:/f)?/?(?:\?[^#]*)?', url) or
                     re.fullmatch(r'https://app\.asana\.com/1/' + gid + r'(?:/project/(' + gid + r'))?/task/(' + gid + r')/?(?:\?[^#]*)?', url))
            if not match: return '', ''
            project, task = match.groups()
            tasks.add(task)
            if project and project != '0': projects.add(project)
        for key, ids in (('task_gid', tasks), ('project_gid', projects)):
            value = asana.get(key)
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(gid, value): return '', ''
                ids.add(value)
        if len(tasks) != 1 or len(projects) > 1: return '', ''
        return tasks.pop(), projects.pop() if projects else ''

    def snapshot(self, sessions=(), records=(), jobs=()):
        now = self.clock()
        today = datetime.fromtimestamp(now, timezone.utc).astimezone().date()
        cards, errors = self.read()
        groups = {key: [] for key in ('needs_user', 'in_progress', 'pending', 'waiting', 'completed')}
        tracking = {key: [] for key in ('external', 'scheduled', 'recurring', 'other')}
        deadlines, unscheduled = [], 0
        for path, (card, unavailable) in cards.items():
            status = card['status']
            inactive = excluded(card)
            agenda = card.get('agenda') if isinstance(card.get('agenda'), dict) else {}
            deferred = postponed(card, today.isoformat())
            selected = agenda.get('date') == today.isoformat() and not deferred
            previous = bool(day(agenda.get('date')) and agenda['date'] < today.isoformat())
            completion = next((day(card.get(k)) for k in ('completed_at', 'completed_on', 'completed_date', 'completion_date') if day(card.get(k))), '')
            verified = next((stamp(card.get(k)) for k in ('updated_at', 'reviewed_at', 'last_verified') if stamp(card.get(k)) is not None), None)
            wait = card.get('waiting_on_user')
            wait = wait if isinstance(wait, dict) else {}
            answered = 'waiting_on_user' in card and (not wait or wait.get('status') in {'resolved', 'answered', 'cancelled', 'superseded'})
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
            asana = card.get('asana') if isinstance(card.get('asana'), dict) else {}
            due = card.get('due_at') or card.get('due_on') or asana.get('due_at') or asana.get('due_on')
            blocked = status in EXTERNAL | {'blocked'} or bool(text(card.get('blocker')))
            # A current, explicit blocked deadline is important, not actionable.
            # Old deadlines and later decisions never schedule work by themselves.
            due_blocked = bool(blocked and day(due) == today.isoformat() and not (
                deferred or answered or newer_input or expired or unavailable))
            if status in DONE: group = 'completed' if completion == today.isoformat() else None
            elif inactive or not (selected or due_blocked): group = None
            elif newer_input or expired: group = 'waiting'
            elif blocked: group = 'waiting'
            elif waiting_user: group = 'needs_user'
            elif status in RUNNING: group = 'in_progress'
            elif status in READY: group = 'pending'
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
            if approval and selected and status not in DONE and not inactive:
                group = 'needs_user'
            job_id = card.get('job') or card.get('pilot_job')
            job = next((j for j in jobs if j['id'] == job_id and j.get('canOpen')), None)
            if target is None and job: target = {'job': job_id}
            source = card.get('source_event') or card.get('result_source') or card.get('user_source') or card.get('status_source') or card.get('source')
            asana_url = link(card.get('asana_url') or asana.get('url') or asana.get('permalink_url'))
            activity = ('completed' if status in DONE else 'approval' if approval else
                        'review' if newer_input or expired or (answered and status in USER) else
                        'blocked' if blocked else 'needs_user' if waiting_user else
                        'in_progress' if status in RUNNING else 'pending' if status in READY else 'review')
            row = {'id': path, 'project': path.split('/')[1], 'title': text(card.get('title'), 160),
                   'status': status, 'result': text(card.get('result'), 240), 'next': text(card.get('next_action') or card.get('next'), 240),
                   'due': text(due), 'validUntil': stamp(expires), 'updatedAt': verified, 'source': text(source, 240), 'reference': asana_url,
                   'referenceCheckedAt': text(asana.get('verified_on') or card.get('last_asana_sync'), 80),
                   'rank': agenda.get('rank') if type(agenda.get('rank')) is int else 999,
                   'reason': text(agenda.get('reason'), 160), 'target': target,
                   'priority': ('optional' if agenda.get('priority') == 'optional' else 'required') if selected else 'important' if due_blocked else '',
                   'activity': activity,
                   'stale': stale or source_gap, 'unconfirmed': changed_source or newer_input or expired or status not in DONE | STOPPED | RUNNING | READY | WAITING | USER | DEFERRED,
                   'waitingFor': text((card.get('blocker') or card.get('waiting_on')) if blocked else (wait.get('action') if waiting_user else None) or card.get('waiting_on'), 240),
                   'releaseCondition': text(wait.get('release_condition') if waiting_user else None, 240),
                   'note': '原專員有正式核准待處理' if approval and status not in DONE and not inactive else '原對話有較新回覆，等待正式核對' if newer_input else '尚未排入今天' if previous and not selected and status not in DONE else '',
                   'completionDate': completion, 'scheduledToday': selected,
                   'trackingGroup': tracking_group(card, today.isoformat()),
                   'followUpOn': text(card.get('remind_on') or card.get('start_on') or (agenda.get('date') if not selected and not previous else None), 80),
                   'schedule': text(card.get('schedule'), 160)}
            if status in DONE:
                row.update(waitingFor='', releaseCondition='')
            elif answered and status in USER:
                row.update(waitingFor='', releaseCondition='', next='原等待已解除，依最新決定核對後續', note='已回覆／解除，不再催辦原動作')
            if expired and status not in DONE | STOPPED and not approval:
                row.update(waitingFor='', releaseCondition='核對期限與最新決定', next='原動作已過期，待重新核對', note='原動作已過期，待重新核對')
            if newer_input and status not in DONE | STOPPED:
                row.update(waitingFor='', releaseCondition='核對較新回覆並更新原工作卡', next='核對原對話的新回覆')
            if status not in DONE and not inactive and not deferred and not answered and not expired and not newer_input and day(due) and day(due) <= (today + timedelta(days=7)).isoformat():
                deadlines.append({k: row[k] for k in ('id', 'title', 'due', 'reference', 'referenceCheckedAt')})
            if group: groups[group].append(row)
            elif status not in DONE and not inactive:
                tracking[row['trackingGroup']].append(row)
                unscheduled += 1
        for rows in (*groups.values(), *tracking.values()): rows.sort(key=lambda r: (r['rank'] if r['scheduledToday'] else 999, r['followUpOn'] or r['due'] or '9999', r['id']))
        deadlines.sort(key=lambda r: (r['due'], r['id']))
        counts = {'today': sum(len(rows) for key, rows in groups.items() if key != 'completed'),
                  'tracking': sum(map(len, tracking.values())),
                  'completed': len(groups['completed'])}
        return {'date': today.isoformat(), 'readAt': now, 'source': 'projects/*/work-items/*.json',
                'groups': groups, 'tracking': tracking, 'counts': counts, 'deadlines': deadlines, 'notScheduled': unscheduled, 'errors': errors,
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
        updated.update(updated_at=datetime.fromtimestamp(self.clock(), timezone.utc).astimezone().isoformat(),
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
