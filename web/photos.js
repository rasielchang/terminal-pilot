// Photo bytes live in the private upload store, never in conversation/context JSON.
(() => {
  const selections = new Map();
  let generation=0;
  const keyOf = target => target ? JSON.stringify([target.session,target.binding || "",target.thread]) : "";
  const imageURL = file => URL.createObjectURL(file);
  const base64 = blob => new Promise((resolve,reject) => { const r=new FileReader();r.onload=()=>resolve(r.result.split(",")[1]);r.onerror=()=>reject(new Error("無法讀取照片。"));r.readAsDataURL(blob); });
  async function convert(file) {
    if (file.size>12*1024*1024) throw new Error("每張照片最多12MB。");
    if (/\.(heic|heif)$/i.test(file.name) || /image\/hei[cf]/.test(file.type)) {
      const url=imageURL(file), img=new Image();
      try {
        await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=reject;img.src=url;});
        const ratio=Math.min(1,3072/Math.max(img.naturalWidth,img.naturalHeight)), canvas=document.createElement("canvas");
        canvas.width=Math.round(img.naturalWidth*ratio);canvas.height=Math.round(img.naturalHeight*ratio);
        canvas.getContext("2d").drawImage(img,0,0,canvas.width,canvas.height);
        const blob=await new Promise(r=>canvas.toBlob(r,"image/jpeg",.9));
        if (!blob) throw new Error();
        return {blob,name:file.name.replace(/\.(heic|heif)$/i,".jpg")};
      } catch {throw new Error("這張HEIC無法轉換。請從相簿分享為JPEG，或選擇截圖後重試。");}
      finally {URL.revokeObjectURL(url);}
    }
    if (!/\.(jpe?g|png)$/i.test(file.name) && !["image/jpeg","image/png"].includes(file.type)) throw new Error("只支援JPEG、PNG照片，不支援影片。");
    return {blob:file,name:file.name};
  }
  function composer(container,onChange=()=>{}) {
    let target=null,key="",locked=false;
    const input=document.createElement("input");input.type="file";input.accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif";input.multiple=true;input.hidden=true;
    const choose=document.createElement("button");choose.type="button";choose.className="secondary photo-choose";choose.textContent="＋ 照片";
    const list=document.createElement("div");list.className="photo-selection";list.setAttribute("aria-live","polite");
    const note=document.createElement("p");note.className="fine photo-note";
    container.append(choose,input,list,note);
    const entry=()=>{if(!selections.has(key))selections.set(key,{items:[],attempt:null,error:""});return selections.get(key);};
    function render(){
      choose.disabled=!target;const e=entry();list.replaceChildren();
      for(const item of e.items){
        const card=document.createElement("div");card.className="photo-preview";
        const img=document.createElement("img");img.src=item.url;img.alt=item.file.name;card.append(img);
        const label=document.createElement("span");label.textContent=item.state==="ready"?"已上傳":item.state==="uploading"?"上傳中…":item.error;card.append(label);
        if(item.state==="failed"){const retry=document.createElement("button");retry.type="button";retry.textContent="重試上傳";retry.onclick=()=>upload(item,e);card.append(retry);}
        const remove=document.createElement("button");remove.type="button";remove.textContent="移除";remove.disabled=Boolean(e.attempt)||locked;remove.onclick=()=>{e.items=e.items.filter(x=>x!==item);URL.revokeObjectURL(item.url);render();onChange();};card.append(remove);list.append(card);
      }
      note.textContent=e.error || (target?"每次最多4張、每張12MB。上傳後按送出才會交給agent。":"對話尚未連接；一般Terminal不支援照片。");
      choose.disabled ||= Boolean(e.attempt)||locked || e.items.length>=4;
    }
    async function upload(item,e){
      item.state="uploading";item.error="";render();onChange();
      try {item.encoded ||= await convert(item.file);item.data ||= await base64(item.encoded.blob);
        if(item.generation!==generation)return;
        const r=await api("/api/photos",{...item.target,requestID:item.requestID,name:item.encoded.name,data:item.data},{timeoutMs:60000});
        item.photo=r.photo;item.state="ready";delete item.data;
      }catch(error){item.state="failed";item.error=error.message || "上傳失敗，照片已保留。";}
      render();onChange();
    }
    choose.onclick=()=>input.click();input.onchange=()=>{
      if(!target||locked||entry().attempt)return;const e=entry();e.error="";
      for(const file of input.files){if(e.items.length>=4){e.error="每次最多4張照片。";break;}
        const item={file,target:{...target},requestID:requestID(),generation,url:imageURL(file),state:"uploading"};e.items.push(item);upload(item,e);}
      input.value="";render();onChange();
    };
    return {
      lock(value){locked=Boolean(value);render();},
      setTarget(next){target=next?.thread?next:null;key=keyOf(target);render();},
      has(){return Boolean(target&&entry().items.length);},
      ready(){return !entry().items.some(i=>i.state!=="ready");},
      ids(){if(!this.ready())throw new Error("請等待照片上傳完成，或重試失敗的照片。");return entry().items.map(i=>i.photo.id);},
      attempt(text){const e=entry(),ids=this.ids();if(e.attempt&&(e.attempt.text!==text||JSON.stringify(e.attempt.photos)!==JSON.stringify(ids)))throw new Error("上一筆照片送出尚未確認，請保留原內容重試。");e.attempt||={...target,text,photos:ids,requestID:requestID()};render();return e.attempt;},
      capture(){return {key,items:[...entry().items]};},
      clear(captured=this.capture()){const e=selections.get(captured.key);if(!e)return;for(const i of captured.items)URL.revokeObjectURL(i.url);e.items=e.items.filter(i=>!captured.items.includes(i));if(!e.items.length)e.attempt=null;e.error="";render();onChange();}
    };
  }
  function historyPhotos(article,message,target){
    if(!message.photos?.length||!target?.thread)return;
    const gallery=document.createElement("div");gallery.className="message-photos";
    for(const photo of message.photos){
      const button=document.createElement("button");button.type="button";button.className="photo-history";button.textContent="載入照片…";
      button.style.width="min(100%,240px)";button.style.aspectRatio=`${photo.width} / ${photo.height}`;button.style.maxHeight="260px";
      const img=document.createElement("img");img.alt=photo.name;img.loading="lazy";img.width=photo.width;img.height=photo.height;
      let url;
      const load=async()=>{try{const q=new URLSearchParams({...target,id:photo.id});const r=await fetch("/api/photos?"+q,{headers:{Authorization:`Bearer ${token}`},cache:"no-store"});if(!r.ok)throw new Error();url=URL.createObjectURL(await r.blob());if(!button.isConnected){URL.revokeObjectURL(url);return;}img.src=url;button.replaceChildren(img);button.onclick=()=>{const dialog=document.createElement("dialog");dialog.className="photo-dialog";const full=img.cloneNode();const close=document.createElement("button");close.textContent="關閉";close.onclick=()=>dialog.close();dialog.append(close,full);document.body.append(dialog);dialog.onclose=()=>dialog.remove();dialog.showModal();};
        const observer=new MutationObserver(()=>{if(!button.isConnected){URL.revokeObjectURL(url);observer.disconnect();}});observer.observe(document.body,{childList:true,subtree:true});
      }catch{button.textContent="照片載入失敗 · 點此重試";button.onclick=load;}};
      button.onclick=load;gallery.append(button);queueMicrotask(load);
    }
    article.append(gallery);
  }
  window.PilotPhotos={composer,history:historyPhotos,keyOf,reset(){generation++;for(const e of selections.values())for(const i of e.items)URL.revokeObjectURL(i.url);selections.clear();}};
})();
