"""Separate observation data from announcements; never grants authority."""
import re

INTERNAL={'registered','projection','summary','scope','source_reset','source_recovered','dispatch_receipt','tool_notification'}
ACK=re.compile(r'^(?:好(?:的|了)?|收到|知道了|執行了|已執行|已完成|完成了|已登入|登入了|已處理|做完了|ok(?:ay)?|done|executed|logged in)[。！! .]*$',re.I)
REQUEST=re.compile(r'(?:需要(?:你|本人)|請(?:你|本人)?(?:先)?(?:登入|登錄|核准|授權|選擇|回答|決定)|等待(?:你|本人)|(?:開好|執行完|登入後|操作完).{0,5}(?:跟我說|告訴我)|\b(?:need(?:s)? (?:your |a )?(?:approval|decision|login)|please (?:log ?in|approve|choose|answer))\b)',re.I)
FAILURE=re.compile(r'(?:無法繼續|目前受阻|仍然受阻|(?:部署|建置|測試|執行|連線|登入|驗證|發布)(?:已|仍|又)?失敗|(?:發生|遇到|出現)(?:了)?錯誤|\b(?:build|deployment|test|connection|login) (?:has |is |still )?(?:failed|blocked)|\b(?:error|failure|blocked)\s*[:：]|^(?:build failed|failed|blocked)[.!。！]*$)',re.I)
NEGATED=re.compile(r'(?:沒有|未發生|不再|無任何|沒有任何|有沒有|是否|檢查.*有無|\bno\b|without|check(?:ing)? whether)',re.I)
PROGRESS=re.compile(r'^(?:(?:我(?:會|先|正在|還在)?|目前|現在|接著|正在|仍在|開始|繼續).*(?:查|檢查|處理|調查|確認|等待|跑測試)|(?:I(?: am|[’\']m| will)|checking|investigating|working on|looking into)\b)',re.I)
OUTCOME=re.compile(r'(?:已(?:完成|修正|確認|找到|通過|合併|部署)|測試通過|成功|結果|發現|找到|\b(?:passed|completed|fixed|verified|found|merged|deployed|result)\b)',re.I)


def background_notification(text):
    return text.lstrip().startswith(('<task-notification>','<system-reminder>'))


def acknowledgment(text):return bool(ACK.fullmatch(text.strip()))


def urgent(text):
    # A mention of error logs or a question about a failure is not a failure.
    text=re.sub(r'```.*?```','',text,flags=re.S)
    for clause in re.split(r'[\n。！？!?;；]',text):
        if REQUEST.search(clause):return True
        if FAILURE.search(clause) and not NEGATED.search(clause):return True
    return False


def progress_only(text):
    text=text.strip()
    return bool(len(text)<=240 and '\n' not in text and PROGRESS.search(text) and not urgent(text) and not OUTCOME.search(text))


def source_kind(item):
    if item.get('provenance')=='tool_notification' or background_notification(item.get('text','')):return 'tool_notification'
    if item.get('phase')=='error':return 'error'
    if item.get('role')=='user':return 'instruction'
    if item.get('role')!='assistant':return 'status'
    if item.get('phase')=='commentary' or progress_only(item.get('text','')):return 'progress'
    return 'result'


def reportable(event):
    if not event.get('notify') or event['kind'] in INTERNAL|{'progress','status','instruction'}:return False
    if background_notification(event.get('text','')):return False  # Existing build46 receipts too.
    if event['kind']=='result' and progress_only(event.get('text','')):return False
    return True


def attention(event):
    if not reportable(event):return 'none'
    if event['kind'] in {'waiting','error','source_gap'} or urgent(event.get('text','')):return 'urgent'
    return 'result' if event['kind']=='result' else 'none'


def answers_wait(text,wait):
    value=text.strip()
    return bool(re.fullmatch(r'(?:好(?:的|了)?|執行了|已執行|已完成|完成了|已登入|登入了|已處理|做完了|done|executed|logged in)[。！! .]*',value,re.I)) or bool(re.fullmatch(r'(?:選|我選|選擇)?\s*(?:[A-D]|是|否|yes|no)[。！! .]*',value,re.I))
