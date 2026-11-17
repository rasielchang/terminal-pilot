"use strict";
// A read-only view of the original work cards. No worker-text classification.
window.PilotToday = (() => {
  let key = "";
  const trackingSections = [["external", "等待外部"], ["scheduled", "延後／指定日期"], ["recurring", "定期追蹤"], ["other", "其他待追蹤"]];
  const activityLabels = {pending:"待做",needs_user:"需要你處理",in_progress:"處理中",blocked:"受阻 · 尚不能完成",review:"待核對",approval:"等待正式核准",completed:"已完成"};
  const format = value => value ? new Date(value * 1000).toLocaleString("zh-TW", {month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}) : "時間待確認";
  const button = (label, action) => { const b=element("button", "today-action", label); b.type="button"; b.addEventListener("click",action); return b; };
  function render(data, {connected, error, open, discuss, discussionDisabled}) {
    const panel=$("today-panel");
    const {readAt, ...content} = data || {};
    const next=JSON.stringify([content, connected, error, discussionDisabled]);
    if(next===key){const meta=$("today-read-at");if(meta && data)meta.textContent=`讀取 ${format(data.readAt)} · 正式工作卡`;return;}key=next;
    const expanded=new Set([...panel.querySelectorAll("details[open]")].map(d=>d.dataset.section));
    const disclosure=(className,id,label)=>{
      const d=element("details",className);d.dataset.section=id;d.open=expanded.has(id);
      d.append(element("summary","",label));return d;
    };
    function card(row, id, compact=false) {
      const card=compact ? disclosure("today-card today-tracking-card",`card:${row.id}`,row.title) : element("article","today-card");
      card.dataset.card=row.id;
      const body=element("div","today-card-body");card.append(body);
      const details=disclosure("today-source",`source:${row.id}`,`進度與來源 · 更新 ${format(row.updatedAt)}`);
      const context=compact ? body : details;
      const top=element("div","today-card-top");
      top.append(element("span","today-project",`${row.scheduledToday && row.rank < 999 ? `優先 ${row.rank} · ` : ""}${row.project}`));
      if(!compact)top.append(element("span",`today-state today-state-${row.activity}`,activityLabels[row.activity] || "待核對"));
      body.append(top);if(!compact)body.append(element("h3","",row.title));
      if(row.due)body.append(element("p","today-deadline",`期限 ${row.due}`));
      else if(!compact)body.append(element("p","today-deadline today-no-due",row.scheduledToday ? "已安排今天 · 未另訂期限" : "期限待確認"));
      if(row.validUntil)body.append(element("p","today-condition",`有效至 ${format(row.validUntil)}`));
      if(row.activity==="blocked")body.append(element("p","today-blocker",`阻礙：${row.waitingFor || "原卡標示受阻，解除前無法完成。"}`));
      if(row.next)body.append(element("p","today-next",`下一步：${row.next}`));
      if(row.followUpOn)context.append(element("p","today-condition",`追蹤日期：${row.followUpOn}`));
      if(row.schedule)context.append(element("p","today-condition",`週期：${row.schedule}`));
      if(row.result)context.append(element("p","today-result",row.result));
      if(id!=="completed" && row.activity!=="blocked" && row.waitingFor && row.waitingFor!==row.next)context.append(element("p","",`等待：${row.waitingFor}`));
      if(row.releaseCondition)context.append(element("p","today-condition",`解除條件：${row.releaseCondition}`));
      if(row.note || row.stale || row.unconfirmed)body.append(element("p","today-badge",row.note || (row.unconfirmed ? "有待確認的狀態，請核對來源" : "來源已過期或失聯，保留最後結果")));
      const actions=element("div","today-actions");
      const discussion=button("跟 Brain 討論",()=>discuss(row));discussion.disabled=Boolean(discussionDisabled);actions.append(discussion);
      if(row.target){const b=button("開啟專員 ↗",()=>open(row.target));b.disabled=!connected;actions.append(b);}
      if(row.reference){const a=element("a","today-reference","原任務 ↗");a.href=row.reference;a.target="_blank";a.rel="noopener noreferrer";actions.append(a);}
      body.append(actions);
      details.append(element("p","",row.id));
      if(row.source)details.append(element("p","",row.source));
      if(row.reason && row.scheduledToday)details.append(element("p","",`安排原因：${row.reason}`));
      if(row.reference)details.append(element("p","",`外部來源查核：${row.referenceCheckedAt || "待確認"}`));
      body.append(details);return card;
    }
    const header=element("header","today-heading");
    const title=element("h1","", "今天");title.id="today-title";
    header.append(element("p","eyebrow",data?.date || "TODAY"),title);
    const meta=element("p","today-meta",data ? `讀取 ${format(data.readAt)} · 正式工作卡` : "正在讀取正式工作卡…");
    meta.id="today-read-at";header.append(meta);panel.replaceChildren(header);
    if(!connected || error || data?.errors?.length)panel.append(element("p","today-alert",!connected ? "連線中斷，保留最後已知結果。" : error ? "更新未成功，保留最後已知結果。" : "部分來源無法讀取，保留最後已知結果。"));
    if(!data)return;
    // Agenda determines prominence. Execution state is a badge within the card.
    const todayRows=Object.entries(data.groups).filter(([id])=>id!=="completed").flatMap(([,rows])=>rows);
    todayRows.sort((a,b)=>a.rank-b.rank || (a.due || "9999").localeCompare(b.due || "9999") || a.id.localeCompare(b.id));
    const seen=new Set(), sections={required:[],optional:[],important:[]};
    for(const row of todayRows) {
      if(seen.has(row.id))continue;seen.add(row.id);
      (sections[row.priority] || sections.required).push(row);
    }
    const grid=element("div","today-grid");panel.append(grid);
    for(const [id,title,description] of [
      ["required","今天必做","依今天安排排序；有阻礙的事項會直接標明。"],
      ["important","今日重要阻礙","今天到期、仍等待外部或解除阻礙，尚不能完成。"],
      ["optional","今天可做","已安排今天，有餘裕再做。"]]) {
      const rows=sections[id];if(id!=="required" && !rows.length)continue;
      const section=element("section",`today-section today-${id}`);
      section.dataset.section=id;section.append(element("h2","",`${title} · ${rows.length}`));
      section.append(element("p","today-section-note",description));
      const body=element("div","today-cards");section.append(body);grid.append(section);
      if(!rows.length)body.append(element("p","today-empty","目前沒有已安排的今天必做事項。"));
      for(const row of rows)body.append(card(row,id));
    }
    // Each canonical ID is rendered once, even when it has a deadline, agenda,
    // external wait and a recurring schedule at the same time.
    for(const row of data.groups.completed || [])seen.add(row.id);
    const tracking=Object.fromEntries(trackingSections.map(([id])=>[id,[]]));
    for(const row of Object.values(data.tracking || {}).flat()) {
      if(seen.has(row.id))continue;seen.add(row.id);
      (tracking[row.trackingGroup] || tracking.other).push(row);
    }
    const other=element("section","today-section today-tracking");other.dataset.section="tracking";
    const trackingCount=Object.values(tracking).reduce((n,rows)=>n+rows.length,0);
    other.append(element("h2","",`其他追蹤 · ${trackingCount}`));
    other.append(element("p","today-meta","未安排今天、今天不必做的追蹤。展開分類與標題查看。"));
    const trackingGrid=element("div","today-tracking-grid");other.append(trackingGrid);panel.append(other);
    for(const [id,title] of trackingSections) {
      const rows=tracking[id], section=disclosure("today-tracking-group",`tracking-${id}`,`${title} · ${rows.length}`);
      if(rows.length) {
        section.firstChild.append(element("span","today-tracking-preview",rows.slice(0,2).map(r=>r.title).join("、")+(rows.length>2 ? `，另 ${rows.length-2} 件` : "")));
        const body=element("div","today-cards");section.append(body);
        for(const row of rows)body.append(card(row,"waiting",true));
      } else section.append(element("p","today-empty","目前沒有項目。"));
      trackingGrid.append(section);
    }
    const completed=data.groups.completed || [], done=disclosure("today-section today-completed","completed",`今日完成 · ${completed.length}`);
    const completedBody=element("div","today-cards");done.append(completedBody);panel.append(done);
    for(const row of completed)completedBody.append(card(row,"completed",true));
    if(!completed.length)completedBody.append(element("p","today-empty","今天的完成結果會留在這裡。"));
    if(data.deadlines?.length) {
      const deadlines=disclosure("today-deadlines","deadlines",`近期期限參考 · ${data.deadlines.length} 項`);
      for(const d of data.deadlines)deadlines.append(element("p","",`${d.due} · ${d.title}`));
      deadlines.append(element("p","today-meta","期限以原來源為準，不自動安排今天。"));panel.append(deadlines);
    }
    panel.append(element("p","today-footnote",`今天必做 ${sections.required.length} 件 · 今天可做 ${sections.optional.length} 件 · 今日重要阻礙 ${sections.important.length} 件 · 其他追蹤 ${trackingCount} 件 · 今日完成 ${completed.length} 件。每張原卡只計一次。`));
  }
  return {render, reset(){key="";$("today-panel").replaceChildren();}};
})();
