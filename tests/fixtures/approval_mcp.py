"""Synthetic stdio MCP server: permission round trip without running a command.

Used only by the opt-in real Codex protocol test. Never accesses files/network.
"""
import json
import sys

pending = {}
def send(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    identity = message.get('id')
    if method == 'initialize':
        send({'jsonrpc':'2.0','id':identity,'result':{'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'pilot_synthetic','version':'1'}}})
    elif method == 'tools/list':
        send({'jsonrpc':'2.0','id':identity,'result':{'tools':[{'name':'confirm','description':'Synthetic confirmation, no side effects','inputSchema':{'type':'object','properties':{}},'annotations':{'readOnlyHint':True}}]}})
    elif method == 'tools/call':
        pending['synthetic-consent'] = identity
        send({'jsonrpc':'2.0','id':'synthetic-consent','method':'elicitation/create','params':{
            'mode':'form','message':'Terminal Pilot synthetic approval test. No files or applications will be accessed.',
            'requestedSchema':{'type':'object','properties':{}}}})
    elif method is None and identity in pending:
        action = message.get('result',{}).get('action','error')
        send({'jsonrpc':'2.0','id':pending.pop(identity),'result':{'content':[{'type':'text','text':'synthetic decision: '+action}]}})
    elif method and identity is not None:
        send({'jsonrpc':'2.0','id':identity,'error':{'code':-32601,'message':'Unknown test method'}})
