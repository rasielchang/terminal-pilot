"use strict";
(() => {
  function path(value) {
    if (!value.startsWith('/api/attachments?') || /[\s\\#]/.test(value)) return null;
    const url = new URL(value, 'https://pilot.invalid');
    const q = url.searchParams;
    if (url.pathname !== '/api/attachments' || [...q.keys()].sort().join(',') !== 'id,target,topic' ||
        !/^[a-f0-9]{32}$/.test(q.get('id')) || !/^conversation:[a-f0-9]{32}$/.test(q.get('target')) ||
        !/^[A-Za-z0-9:_-]{1,100}$/.test(q.get('topic'))) return null;
    return url.pathname + url.search;
  }
  function link(label, href, getToken) {
    const route = path(href);
    if (!route) return document.createTextNode(label);
    const box = document.createElement('span'); box.className = 'private-attachment';
    const button = document.createElement('button'); button.type = 'button'; button.className = 'text-button'; button.textContent = label;
    const status = document.createElement('span'); status.setAttribute('role','status');
    box.append(button,status);
    button.addEventListener('click', async () => {
      const auth = getToken();
      if (!auth) {status.textContent = '請先配對 Pilot。'; return;}
      button.disabled = true; status.textContent = ' 正在取得私人檔案…';
      try {
        const response = await fetch(route,{headers:{Authorization:'Bearer '+auth},cache:'no-store',redirect:'error'});
        if (auth !== getToken()) throw new Error('配對已變更，請重新取得檔案。');
        if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? '請重新配對或確認檔案所屬工作。' : '檔案無法下載，請稍後重試。');
        if (!response.headers.get('Content-Type')?.startsWith('text/csv')) throw new Error('附件格式不正確。');
        const raw = await response.blob();
        if (!raw.size || raw.size > 5*1024*1024) throw new Error('附件大小不正確。');
        const match = response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i);
        const name = match ? decodeURIComponent(match[1]) : 'download.csv';
        if (/[\/\\\x00-\x1f]/.test(name) || !name.toLowerCase().endsWith('.csv')) throw new Error('附件檔名不正確。');
        const file = new File([raw],name,{type:'text/csv'});
        const url = URL.createObjectURL(file);
        const save = document.createElement('a'); save.href = url; save.download = name; save.textContent = '下載 CSV'; save.className = 'text-button';
        box.replaceChildren(save,status); status.textContent = ` ${name} · ${raw.size} bytes`;
        if (navigator.canShare?.({files:[file]})) {
          const share = document.createElement('button'); share.type = 'button'; share.className = 'text-button'; share.textContent = '儲存至檔案／分享';
          share.addEventListener('click', async () => {
            // WebKit can turn a lone title into a separate text share item.
            // Keep the filename on File only; share exactly one CSV.
            try { await navigator.share({files:[file]}); }
            catch (error) { if (error.name !== 'AbortError') status.textContent = '請點「下載 CSV」儲存。'; }
          });
          box.prepend(share);
        }
        setTimeout(() => {URL.revokeObjectURL(url); box.replaceChildren(button,status); button.disabled=false; status.textContent=' 點取重新下載';},300000);
      } catch (error) {status.textContent = ' '+error.message; button.disabled = false;}
    });
    return box;
  }
  window.PilotAttachments = {path,link};
})();
