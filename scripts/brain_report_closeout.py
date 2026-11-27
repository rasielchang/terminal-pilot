"""Convert a verified explicit report into one reviewable, finite grant."""
from datetime import datetime, timezone
import json

from brain_service import ACTIVE, running, waiting
from brain_observer import digest
from brain_context import report_input
from brain_today import STOPPED


def report_source(delivery):
    return {'report': delivery['id'], 'work': delivery['work'],
            'session': delivery['session'], 'workerThread': delivery['workerThread'],
            'generation': delivery['generation'], 'binding': delivery['binding'],
            'sourceRevision': delivery['sourceRevision'], 'original': delivery['events'][0],
            'evidence': delivery['events'][1:], 'closeout': delivery.get('closeout'),
            'continuation': delivery.get('continuation') if 'closeout' not in delivery else None}


class WorkerNotIdle(ValueError):
    pass


class ReportCloseout:
    def __init__(self, continuations):
        self.c = continuations
        self.o = continuations.o

    def active_report(self, data):
        o = self.o
        d = next((d for d in o._rows('work_deliveries') if d['id'] == data['report']), None)
        if not d or not d.get('explicit') or 'closeout' not in d:
            raise ValueError('沒有可判讀的新正式回報。')
        active = o.backend.current_turn() if o.backend else {}
        remote = o.backend.data.get('requests', {}).get(d['id'], {}) if o.backend else {}
        inputs = [i for i in active.get('items', []) if i.get('type') == 'userMessage']
        original = inputs[0] if len(inputs) == 1 else {}
        client = original.get('clientId')
        if (data['thread'] != d['thread'] or remote.get('thread') != d['thread'] or
                o.backend.data.get('thread') != d['thread'] or
                active.get('id') != data['turn'] or active.get('status') != 'inProgress' or
                remote.get('turn', data['turn']) != data['turn'] or
                (client is not None and client != d['id']) or
                (not client and remote.get('turn') != data['turn']) or
                [(p.get('type'), p.get('text')) for p in original.get('content', [])] != [('text', report_input(report_source(d)))] or
                d['state'] != 'running' or o._get('paused', False)):
            raise ValueError('收尾提案只接受正式回報的原 Brain 回合；本輪新指令須另行處理。')
        return d

    def check_brain_fence(self, d, turn):
        if d['brainFence'] is None:
            raise ValueError('回報受理時無法核對 Brain 決策來源；保留原工作。')
        if self.c.brain_fence((turn, d['id'])) != d['brainFence']:
            raise ValueError('Brain 有較晚本人決定；保留原工作。')

    def readiness(self, d):
        o = self.o
        r, s = o._report_work(d['session'], d['workerThread'])
        if (r['id'] != d['work'] or s['bindingGeneration'] != d['generation'] or s['binding'] != d['binding'] or
                r['sourceRevision'] != d['sourceRevision'] or r.get('instruction', {}).get('event') != d['instruction']):
            raise ValueError('原指令、工作綁定或來源版本已改變；請核對新狀態。')
        event = o.observation_source(d['work'], d['instruction'])['event']
        if (event != d['events'][0] or event['kind'] != 'instruction' or event.get('partial') or
                event['source'].get('provenance') not in {None, 'user'} or
                event['source'].get('thread') != d['workerThread'] or
                event['source'].get('revision') != d['sourceRevision']):
            raise ValueError('回報缺少目前原工作完整指令或來源版本不符；只保留回報。')
        if not self.c.current_instruction(event):
            raise ValueError('原本人指令已過期或來源時間無法確認；請重新核對。')
        self.delivered_result(r, d['sources'], event, d['id'])
        self.check_approval(s)
        return r

    def delivered_result(self, r, sources, instruction, report):
        evidence = self.c.evidence(r, sources)
        origin = instruction['source']
        command_turn = origin.get('turn')
        candidates = [event['source']['cursor'] for event in evidence if event['kind'] == 'result' or (
            event['kind'] == 'progress' and event['source'].get('phase') == 'commentary' and
            event['source']['cursor'] > origin['cursor'])]
        cited = max(candidates, default=-1)
        latest = r.get('result', {}).get('source', {})
        changed = latest.get('cursor', -1) > cited
        if changed and origin.get('representation') == 'claude':
            changed = latest.get('revision') != r['sourceRevision'] or self.has_new_claude_result(
                r, cited, origin['cursor'], report)
        elif changed:
            # Keep the existing Codex reporting-turn behavior.
            changed = not (command_turn and latest.get('turn') == command_turn)
        if cited < 0 or changed or r.get('error', {}).get('source', {}).get('cursor', -1) > cited:
            raise ValueError('請先核對原工作最新的交付結果與錯誤。')

    def has_new_claude_result(self, r, cited, command_cursor, report):
        # Claude can log text and tool calls as separate records in one message.
        # A status poll on this exact report accounts for that message only.
        # Check every newer result so a trailing poll cannot mask earlier work.
        rows = self.o.db.execute(
            """SELECT json_extract(data, '$.kind'), json_extract(data, '$.source')
                 FROM work_events WHERE observer = ?
                 AND json_extract(data, '$.source.revision') = ?
                 AND json_extract(data, '$.source.cursor') > ?
                 AND json_extract(data, '$.kind') IN ('result', 'claude_tool_activity')""",
            (r['id'], r['sourceRevision'], cited))
        results, messages = [], {}
        for kind, raw in rows:
            source = json.loads(raw)
            if kind == 'result':
                results.append(source)
            elif source['cursor'] > command_cursor:
                messages.setdefault(source['messageID'], set()).add(source['reportPoll'])
        return not results or any(source['cursor'] <= command_cursor or messages.get(source.get('messageID')) != {report}
                                  for source in results)

    @staticmethod
    def check_approval(session):
        if waiting(session) or session.get('runtime', {}).get('state') in {'approval', 'question'}:
            raise WorkerNotIdle('原工作有正式核准或問題等待；請在原對話處理。')

    def idle(self, session):
        self.check_approval(session)
        if running(session) or any(j.get('session') == session['id'] and j['state'] in ACTIVE for j in self.o._rows('jobs')):
            raise WorkerNotIdle('原工作仍在執行或有未完成交辦；請等結果。')
        if session.get('phase') not in {'idle', 'complete'}:
            raise ValueError('原工作尚未確認為閒置或完成；請核對原狀態。')

    def decision(self, d, state, **fields):
        d['closeout'] = {'state': state, **fields}
        self.o._save('work_deliveries', d)
        return d['closeout']

    def plan(self, data):
        if (set(data) != {'thread', 'turn', 'report', 'intent', 'instruction'} or
                data['intent'] not in ('report_only', 'closeout', 'unclear')):
            raise ValueError('需要原 Brain 回合、回報、本人事件與收尾判斷。')
        o = self.o
        with o.lock, o.db:
            d = self.active_report(data)
            if data['instruction'] != d['instruction']:
                raise ValueError('判斷須引用原本人指令事件。')
            if d['closeout']['state'] != 'pending':
                return d['closeout']
            if data['intent'] != 'closeout':
                if data['intent'] == 'unclear':
                    return self.decision(d, 'blocked', reason='請先釐清本人要求的收尾範圍，並核對交付證據。')
                return self.decision(d, 'report_only')
            try:
                self.check_brain_fence(d, data['turn'])
                record = self.readiness(d)
                if not hasattr(self.c.execute, 'prepare_report_closeout'):
                    raise ValueError('此 host 尚無原工作卡與精確關窗的接續執行器。')
                mapping, card = o.today_cards.closeout_card(record, d['session'])
                if not mapping['task'] and card['status'] in STOPPED:
                    raise ValueError('原工作卡已停止或取消；保留原工作。')
                card_digest = o.today_cards.card_digest(card) if not mapping['task'] else None
                month = datetime.fromtimestamp(o.clock(), timezone.utc).astimezone().strftime('%Y/%m')
            except (ValueError, OSError) as error:
                return self.decision(d, 'blocked', reason=str(error))
        try:
            action = self.c.execute.prepare_report_closeout(d, mapping, month)
        except (ValueError, OSError) as error:
            with o.lock, o.db:
                d = self.active_report(data)
                if d['closeout']['state'] != 'pending': return d['closeout']
                return self.decision(d, 'blocked', reason=str(error))
        actions = [{'kind': 'verify_delivery'}, action, {'kind': 'close_work'}]
        with o.lock, o.db:
            d = self.active_report(data)
            if d['closeout']['state'] != 'pending': return d['closeout']
            try:
                self.check_brain_fence(d, data['turn'])
                self.readiness(d)
            except (ValueError, OSError) as error:
                return self.decision(d, 'blocked', reason=str(error))
            return self.decision(d, 'planned', actions=actions, mapping=mapping,
                                 **({'cardDigest': card_digest} if card_digest else {}))

    def propose(self, data):
        if set(data) != {'thread', 'turn', 'report', 'actions'}:
            raise ValueError('收尾提案只接受原回報與已核對的精確動作。')
        o = self.o
        with o.lock, o.db:
            d = self.active_report(data)
            plan = d['closeout']
            if plan['state'] not in {'planned', 'proposed'} or data['actions'] != plan['actions']:
                raise ValueError('提案必須符合 Pilot 已核對的收尾範圍。')
            if d.get('continuation'):
                return self.c.status(d['continuation'])
            try:
                self.check_brain_fence(d, data['turn'])
                r = self.readiness(d)
                mapping, card = o.today_cards.closeout_card(r, d['session'])
                if mapping != plan['mapping'] or (plan.get('cardDigest') and o.today_cards.card_digest(card) != plan['cardDigest']):
                    raise ValueError('原工作卡或 Asana 對應已變更；保留原工作。')
            except (ValueError, OSError) as error:
                return self.decision(d, 'blocked', reason=str(error))
            return self.c.propose({'requestID': 'report-closeout:' + digest(d['id'])[:40],
                'session': d['session'], 'thread': d['workerThread'], 'work': d['work'],
                'generation': d['generation'], 'instruction': d['instruction'], 'sources': d['sources'],
                'when': 'now', 'expiresIn': 86400, 'actions': plan['actions']}, report=d)
