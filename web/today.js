"use strict";
// A read-only view of the original work cards. No worker-text classification.
window.PilotToday = (() => {
  let key = "";
  const sections = [["needs_user", "需要你處理", false], ["in_progress", "專員處理中", false], ["waiting", "等待／延後", true], ["completed", "今日完成", true]];
  const format = value => value ? new Date(value * 1000).toLocaleString("zh-TW", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "時間待確認";
  const button = (label, action) => { const b=element("button", "today-action", label); b.type="button"; b.addEventListener("click",action); return b; };
  function render(data, {connected, error, open, chat}) {
    const panel=$("today-panel");
    const {readAt, ...content} = data || {};
    const next=JSON.stringify([content, connected, error]);
    if(next===key){const meta=$("today-read-at");if(meta && data)meta.textContent=`讀取 ${format(data.readAt)} · 正式工作卡`;return;}key=next;
    const expanded=new Set([...panel.querySelectorAll("details[open]")].map(d=>d.dataset.section));
    const header=element("header","today-heading");
    const title=element("h1","", "今天，把重要的事放在眼前。");title.id="today-title";
    header.append(element("p","eyebrow",data?.date || "TODAY"),title);
    const meta=element("p","today-meta",data ? `讀取 ${format(data.readAt)} · 正式工作卡` : "正在讀取正式工作卡…");
    meta.id="today-read-at";header.append(meta);panel.replaceChildren(header);
    if(!connected || error || data?.errors?.length)panel.append(element("p","today-alert",!connected ? "連線中斷，保留最後已知結果。" : error ? "更新未成功，保留最後已知結果。" : "部分來源無法讀取，保留最後已知結果。"));
    if(!data)return;
    if(data.deadlines?.length) {
      const deadlines=element("details","today-deadlines");deadlines.dataset.section="deadlines";deadlines.open=expanded.has("deadlines");
      deadlines.append(element("summary","",`近期期限 · ${data.deadlines[0].due} ${data.deadlines[0].title}${data.deadlines.length>1 ? `（共 ${data.deadlines.length} 項）` : ""}`));
      for(const d of data.deadlines)deadlines.append(element("p","",`${d.due} · ${d.title}`));
      deadlines.append(element("p","today-meta","期限提醒不代表已安排今天；指派與期限以原來源為準。"));panel.append(deadlines);
    }
    const grid=element("div","today-grid");panel.append(grid);
    for(const [id,title,collapsed] of sections) {
      const rows=data.groups[id] || [], section=element(collapsed?"details":"section",`today-section today-${id}`);
      section.dataset.section=id;if(collapsed)section.open=expanded.has(id);
      section.append(element(collapsed?"summary":"h2","",`${title} · ${rows.length}`));
      const body=element("div","today-cards");section.append(body);grid.append(section);
      if(!rows.length)body.append(element("p","today-empty",id==="needs_user" ? "目前沒有已排入今天、需要你處理的工作。" : id==="completed" ? "今天的完成結果會留在這裡。" : "目前沒有項目。"));
      for(const row of rows) {
        const card=element("article","today-card");card.dataset.card=row.id;
        const top=element("div","today-card-top");top.append(element("span","today-project",`${row.rank < 999 ? `優先 ${row.rank} · ` : id === "needs_user" ? "優先序待確認 · " : ""}${row.project}`));
        if(row.due)top.append(element("span","today-due",`期限 ${row.due}`));
        if(row.validUntil)top.append(element("span","today-due",`有效至 ${format(row.validUntil)}`));card.append(top,element("h3","",row.title));
        if(row.result)card.append(element("p","today-result",row.result));
        if(row.next)card.append(element("p","today-next",`下一步：${row.next}`));
        if(id==="waiting" && row.waitingFor)card.append(element("p","",`等待：${row.waitingFor}`));
        if(row.releaseCondition)card.append(element("p","today-condition",`解除條件：${row.releaseCondition}`));
        if(row.note || row.stale || row.unconfirmed)card.append(element("p","today-badge",row.note || (row.unconfirmed ? "有待確認的狀態，請核對來源" : "來源已過期或失聯，保留最後結果")));
        const actions=element("div","today-actions");
        if(row.target){const b=button(id==="needs_user" ? "前往處理 ↗" : "開啟原專員 ↗",()=>open(row.target));b.disabled=!connected;actions.append(b);}
        else actions.append(button("與助理核對",()=>chat()));
        if(row.reference){const a=element("a","today-reference","原任務 ↗");a.href=row.reference;a.target="_blank";a.rel="noopener noreferrer";actions.append(a);}
        card.append(actions);
        const details=element("details","today-source");details.dataset.section=row.id;details.open=expanded.has(row.id);
        details.append(element("summary","",`來源 · 更新 ${format(row.updatedAt)}`),element("p","",row.id));
        if(row.source)details.append(element("p","",row.source));
        if(row.reason)details.append(element("p","",`安排原因：${row.reason}`));
        if(row.reference)details.append(element("p","",`外部來源查核：${row.referenceCheckedAt || "待確認"}`));
        card.append(details);body.append(card);
      }
    }
    panel.append(element("p","today-footnote",`另有 ${data.notScheduled} 件未安排今天。日切保留原工作，不自動排入明日。`));
  }
  return {render, reset(){key="";$("today-panel").replaceChildren();}};
})();
