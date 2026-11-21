'use strict';
const identity = decodeURIComponent(location.hash.slice(1));
const el = id => document.getElementById(id);
const labels = {verify_delivery:'核對此工作交付來源',complete_brain_card:'在原 Brain 工作卡記錄結案',close_work:'關閉此工作（僅限閒置、無核准等待、視窗不含其他工作）',asana_complete_existing:'完成既有 Asana 任務並移至指定月份'};
async function call(path, data) {
  let token = sessionStorage.getItem('pilot-token') || '';
  try { token = localStorage.getItem('pilot-token') || token; } catch {}
  if (!token) throw new Error('請先返回 Pilot 配對，再開啟此收據。');
  const response = await fetch(path, {method:data ? 'POST':'GET', headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:data ? JSON.stringify(data):undefined});
  const result = await response.json(); if (!response.ok) throw new Error(result.error || '讀取失敗'); return result;
}
async function refresh() {
  try {
    const r = await call('/api/brain/continuation?id='+encodeURIComponent(identity));
    const states={authorized:r.report?'已授權，等待正式回報送達':'已授權，等待核對工作結果',queued:'已排入原 Brain 接續',completed:r.summarized?'已完成，Brain 已統整':'動作已完成，等待 Brain 統整',blocked:'接續停止，請查看原因',revoked:'已停止這次接續'};
    el('state').textContent = `${states[r.state] || r.state} ${r.error || ''}`;
    el('scope').textContent = `原訊息：${r.original.text}\n\n工作：${r.work}\n原對話：${r.workerThread}\n執行時機：${r.proposal.when==='result'?'等待新結果後':'核對目前結果後'}\n有效至：${new Date(r.expires*1000).toLocaleString()}\n\n` + r.actions.map(a=>`${labels[a.scope.kind]}${a.scope.task ? `\n任務 ${a.scope.task}／專案 ${a.scope.project}／月份 ${a.scope.month}／section ${a.scope.section}`:a.scope.card ? `\n工作卡 ${a.scope.card}`:''}`).join('\n\n');
    el('receipt').textContent=JSON.stringify(r,null,2);
    el('revoke').disabled=['completed','revoked'].includes(r.state);
  } catch(error) { el('state').textContent=error.message; }
}
el('revoke').onclick=async()=>{
  el('revoke').disabled=true;
  try { await call('/api/brain/continuation-revoke',{continuation:identity}); await refresh(); }
  catch(error) { el('state').textContent=error.message; }
};
refresh(); setInterval(refresh,3000);
