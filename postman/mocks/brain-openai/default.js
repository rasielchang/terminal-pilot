// Synthetic Responses SSE used by a real, isolated Codex app-server.
const http = require('node:http');
http.createServer((req, res) => {
  const fail = (code, message) => { res.writeHead(code, {'Content-Type':'application/json'}); res.end(JSON.stringify({error:{message}})); };
  if (req.method !== 'POST' || req.url !== '/v1/responses') return fail(501, 'Not modeled');
  const body = req.body;
  if (!body || body.stream !== true) return fail(400, 'Expected Codex streaming request');
  const input = JSON.stringify(body.input || []);
  if (input.includes('fixture-503')) return fail(503, 'Synthetic upstream unavailable');
  const text = JSON.stringify({reply:'Brain Codex 合成協定測試完成。', dispatches:[], release:[]});
  const item = {id:'msg_synthetic', type:'message', role:'assistant', status:'completed', phase:'final_answer', content:[{type:'output_text',text,annotations:[]}]};
  res.writeHead(200, {'Content-Type':'text/event-stream', 'Cache-Control':'no-cache'});
  let sequence = 0;
  const event = (type, data) => res.write(`event: ${type}\ndata: ${JSON.stringify({type,sequence_number:sequence++,...data})}\n\n`);
  event('response.created', {response:{id:'resp_synthetic',object:'response',status:'in_progress',output:[]}});
  event('response.output_item.added', {output_index:0,item:{...item,status:'in_progress',content:[]}});
  event('response.content_part.added', {item_id:item.id,output_index:0,content_index:0,part:{type:'output_text',text:'',annotations:[]}});
  event('response.output_text.delta', {item_id:item.id,output_index:0,content_index:0,delta:text});
  event('response.output_text.done', {item_id:item.id,output_index:0,content_index:0,text});
  event('response.content_part.done', {item_id:item.id,output_index:0,content_index:0,part:item.content[0]});
  event('response.output_item.done', {output_index:0,item});
  event('response.completed', {response:{id:'resp_synthetic',object:'response',status:'completed',output:[item],usage:{input_tokens:10,output_tokens:10,total_tokens:20}}});
  res.end();
}).listen(process.env.PORT);
