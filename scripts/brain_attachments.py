"""Brain's explicit attachment registration and work ownership boundary."""
import hashlib
from urllib.parse import urlencode


class BrainAttachments:
    def register_attachment(self, data):
        required = {'thread','contextID','turn','requestID','topic','target','name','mime','data'}
        if set(data) != required: raise ValueError('附件註冊欄位不正確。')
        with self.lock:
            context = next((x for x in self._rows('action_contexts') if x['id']==data['contextID']),None)
            if not context or context['thread'] != data['thread'] or context['turnID'] != data['turn'] or data['thread'] != self.backend.data.get('thread'):
                raise PermissionError('附件來源不是原 Brain 回合。')
            current = self.backend.current_turn()
            if current.get('id') != data['turn'] or current.get('status') != 'inProgress' or hashlib.sha256(self._human_text(current).encode()).hexdigest() != context['humanFingerprint']:
                raise ValueError('Brain 回合已改變，請重新取得 context。')
            if context['source'].get('reportOnly'): raise ValueError('回報回合不能註冊新附件。')
            owner = self.attachment_owner(data['topic'],data['target'])
            value = self.attachments.register({k:data[k] for k in ('requestID','name','mime','data')},owner)
            return {'attachment':value,'link':self.attachment_link(value)}
    def attachment_owner(self, topic, target):
        if not isinstance(topic,str) or not isinstance(target,str) or target not in self._continuation_routes().get(topic,[]):
            raise PermissionError('附件必須屬於已綁定的明確工作。')
        return {'topic':topic,'target':target,'brainThread':self.backend.data['thread']}
    @staticmethod
    def attachment_link(value):
        return '/api/attachments?'+urlencode({k:value[k] for k in ('id','topic','target')})
    def read_attachment(self, data):
        if set(data) != {'id','topic','target'}: raise ValueError('附件下載欄位不正確。')
        with self.lock:
            owner = self.attachment_owner(data['topic'],data['target'])
            return self.attachments.read(data['id'],owner)
