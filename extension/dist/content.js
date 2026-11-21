var fe=Object.defineProperty;var ve=(_,U,I)=>U in _?fe(_,U,{enumerable:!0,configurable:!0,writable:!0,value:I}):_[U]=I;var c=(_,U,I)=>ve(_,typeof U!="symbol"?U+"":U,I);(function(){"use strict";const _=new Set(["manga","manhwa","manhua","series","title","titles","comic","comics","webtoon","webtoons","read","obra","obras","book","books","viewer","detail","en","pt","pt-br","es","fr","ko","zh","ja","jp"]),U=/^(?:chapter|chap|ch|cap|capitulo|ep|episode|episodio|vol|volume|issue|part|page|read|viewer|list)[-._]?\d.*$/i;function I(E){return decodeURIComponent(E||"").toLowerCase().replace(/[^a-z0-9\u00c0-\u024f\u3040-\u9fff\uac00-\ud7af]+/g,"-").replace(/^-+|-+$/g,"").slice(0,72)||"obra-padrao"}function H(E){return E.split(/[-_]+/).filter(Boolean).map(e=>e.charAt(0).toUpperCase()+e.slice(1)).join(" ")}function Q(E){try{const e=new URL(E),t=e.hostname.replace(/^www\./,"").toLowerCase(),n=e.pathname.split("/").map(l=>l.trim()).filter(Boolean),s=e.searchParams.get("title_no")||e.searchParams.get("titleId")||e.searchParams.get("id");let i="";for(let l=0;l<n.length;l++){const o=n[l],g=o.toLowerCase();if(_.has(g)){const d=n[l+1];if(d&&!U.test(d)&&!_.has(d.toLowerCase())){i=d;break}continue}if(!U.test(o)){if(/^\d+$/.test(o)&&n[l+1]&&!U.test(n[l+1])){i=n[l+1];break}i=o;break}}!i&&s&&(i=`${t}-${s}`),i||(i=t||"obra-local"),i=i.replace(/[-_](?:chapter|chap|ch|cap|ep|episode)[-_]?\d+.*$/i,"");const r=I(i);return{slug:r,name:H(r)}}catch{return{slug:"obra-padrao",name:"Obra Padrão"}}}function J(){return{series:{},urlOverrides:{}}}function Z(E,e){const t=Q(e),n=E.urlOverrides[t.slug]||t.slug,s=E.series[n];return s?{detectedSlug:t.slug,activeSlug:n,series:s}:{detectedSlug:t.slug,activeSlug:n,series:{slug:n,name:n===t.slug?t.name:H(n),terms:{},updatedAt:0}}}function $(E){if(!E)return"g0";const e=Object.entries(E).map(([s,i])=>[s.trim(),i.trim()]).filter(([s,i])=>s.length>0&&i.length>0).sort((s,i)=>s[0].localeCompare(i[0]));if(e.length===0)return"g0";const t=JSON.stringify(e);let n=2166136261;for(let s=0;s<t.length;s++)n^=t.charCodeAt(s),n=Math.imul(n,16777619);return`g${(n>>>0).toString(16)}`}const ee="gemini-3.5-flash-lite",te="v3-scanlation-turbo",se=[{value:"auto",label:"Auto (Universal)"},{value:"ja",label:"Japonês"},{value:"ko",label:"Coreano"},{value:"zh",label:"Chinês"},{value:"en",label:"Inglês"},{value:"es",label:"Espanhol"},{value:"fr",label:"Francês"}],ae={apiKey:"",model:ee,sourceLang:"auto",readingDirection:"auto",style:"scanlation",uppercase:!0,autoTranslate:!1,idleTimeoutSec:300,backendPort:8765,prefetchCount:4},ne="scanlator_vogels_cache_v1",A="translated_pages",ie=1,j=250;function V(E,e,t="g0"){return`${te}::${E}::${e.sourceLang}::${e.readingDirection}::${e.model}::${e.style}::${e.uppercase?"U1":"U0"}::${t}`}class re{constructor(){c(this,"dbPromise",null)}openDb(){return this.dbPromise?this.dbPromise:(this.dbPromise=new Promise((e,t)=>{const n=indexedDB.open(ne,ie);n.onupgradeneeded=()=>{const s=n.result;s.objectStoreNames.contains(A)||s.createObjectStore(A,{keyPath:"key"}).createIndex("createdAt","createdAt",{unique:!1})},n.onsuccess=()=>e(n.result),n.onerror=()=>t(n.error)}),this.dbPromise)}async get(e){try{const t=await this.openDb();return await new Promise((n,s)=>{const r=t.transaction(A,"readonly").objectStore(A).get(e);r.onsuccess=()=>n(r.result??null),r.onerror=()=>s(r.error)})}catch{return null}}async put(e){try{const t=await this.openDb();await new Promise((n,s)=>{const i=t.transaction(A,"readwrite");i.objectStore(A).put(e),i.oncomplete=()=>n(),i.onerror=()=>s(i.error)}),await this.pruneIfNeeded()}catch{}}async delete(e){try{const t=await this.openDb();await new Promise((n,s)=>{const i=t.transaction(A,"readwrite");i.objectStore(A).delete(e),i.oncomplete=()=>n(),i.onerror=()=>s(i.error)})}catch{}}async clearAll(){try{const e=await this.openDb();await new Promise((t,n)=>{const s=e.transaction(A,"readwrite");s.objectStore(A).clear(),s.oncomplete=()=>t(),s.onerror=()=>n(s.error)})}catch{}}async pruneIfNeeded(){try{const e=await this.openDb(),t=await new Promise((s,i)=>{const l=e.transaction(A,"readonly").objectStore(A).index("createdAt"),o=[],g=l.openCursor();g.onsuccess=()=>{const d=g.result;if(d){const m=d.value;o.push({key:m.key,createdAt:m.createdAt}),d.continue()}else s(o)},g.onerror=()=>i(g.error)});if(t.length<=j)return;const n=t.slice(0,t.length-j);await new Promise((s,i)=>{const r=e.transaction(A,"readwrite"),l=r.objectStore(A);for(const o of n)l.delete(o.key);r.oncomplete=()=>s(),r.onerror=()=>i(r.error)})}catch{}}}class oe{constructor(e){c(this,"callbacks");c(this,"active",!1);c(this,"selectorHost",null);c(this,"pinsHost");c(this,"pinsShadow");c(this,"pinsLayer");c(this,"handleEsc",e=>{e.key==="Escape"&&(e.preventDefault(),e.stopPropagation(),this.cancel(),this.callbacks.onToast("Recorte manual cancelado."))});this.callbacks=e,this.pinsHost=document.createElement("div"),this.pinsHost.id="sv-ptbr-crop-pins-root",this.pinsHost.style.cssText="position:absolute;left:0;top:0;width:0;height:0;z-index:2147483642;pointer-events:none;",this.pinsShadow=this.pinsHost.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
      * { box-sizing: border-box; }
      .sv-pin {
        position: absolute;
        pointer-events: auto;
        border: 2px solid rgba(56, 189, 248, 0.85);
        border-radius: 6px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.48);
        overflow: visible;
        background: rgba(15, 23, 42, 0.45);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      .sv-pin-img {
        display: block;
        width: 100%;
        height: 100%;
        object-fit: fill;
        border-radius: 4px;
      }
      .sv-pin-loading {
        width: 100%;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(15, 23, 42, 0.82);
        color: #7dd3fc;
        font-size: 12px;
        font-weight: 700;
        border-radius: 4px;
        padding: 8px;
        text-align: center;
      }
      .sv-pin-bar {
        position: absolute;
        top: -30px;
        right: 0;
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 6px;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.94);
        border: 1px solid rgba(148, 163, 184, 0.4);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
        white-space: nowrap;
      }
      .sv-pin-btn {
        border: none;
        border-radius: 999px;
        background: rgba(30, 41, 59, 0.95);
        color: #f8fafc;
        font-size: 10.5px;
        font-weight: 600;
        padding: 3px 8px;
        cursor: pointer;
      }
      .sv-pin-btn:hover { background: rgba(56, 189, 248, 0.35); }
      .sv-pin-close {
        background: rgba(239, 68, 68, 0.85);
        color: #fff;
        padding: 2px 7px;
        font-weight: 700;
      }
      .sv-pin-close:hover { background: #dc2626; }
    `,this.pinsLayer=document.createElement("div"),this.pinsShadow.append(t,this.pinsLayer),document.documentElement.appendChild(this.pinsHost)}isActive(){return this.active}cancel(){this.active=!1,this.selectorHost&&(this.selectorHost.remove(),this.selectorHost=null),window.removeEventListener("keydown",this.handleEsc,!0)}clearAllPins(){this.pinsLayer.innerHTML=""}async start(){if(this.active){this.cancel();return}if(!await this.callbacks.ensureEngineRunning())return;this.active=!0;const e=document.createElement("div");e.id="sv-ptbr-crop-selector",e.style.cssText="position:fixed;inset:0;z-index:2147483646;cursor:crosshair;user-select:none;";const t=e.attachShadow({mode:"open"}),n=document.createElement("style");n.textContent=`
      .sv-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(15, 23, 42, 0.28);
        cursor: crosshair;
      }
      .sv-hint {
        position: fixed;
        top: 14px;
        left: 50%;
        transform: translateX(-50%);
        padding: 7px 14px;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.94);
        border: 1px solid rgba(56, 189, 248, 0.55);
        color: #e0f2fe;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 12px;
        font-weight: 600;
        box-shadow: 0 6px 20px rgba(0, 0, 0, 0.45);
        pointer-events: none;
      }
      .sv-rect {
        position: fixed;
        display: none;
        border: 2px solid #38bdf8;
        background: rgba(56, 189, 248, 0.14);
        box-shadow: 0 0 0 9999px rgba(15, 23, 42, 0.35);
        pointer-events: none;
      }
      .sv-dim {
        position: absolute;
        bottom: -22px;
        right: 0;
        padding: 2px 6px;
        border-radius: 4px;
        background: rgba(15, 23, 42, 0.92);
        color: #bae6fd;
        font-family: monospace;
        font-size: 10.5px;
      }
    `;const s=document.createElement("div");s.className="sv-backdrop";const i=document.createElement("div");i.className="sv-hint",i.textContent="Arraste sobre o balão ou quadro para traduzir • Esc para cancelar";const r=document.createElement("div");r.className="sv-rect";const l=document.createElement("span");l.className="sv-dim",r.appendChild(l),t.append(n,s,r,i),document.documentElement.appendChild(e),this.selectorHost=e,window.addEventListener("keydown",this.handleEsc,!0);let o=!1,g=0,d=0,m={left:0,top:0,width:0,height:0};const k=(v,b)=>{const x=Math.max(0,Math.min(g,v)),p=Math.max(0,Math.min(d,b)),N=Math.min(window.innerWidth,Math.max(g,v)),S=Math.min(window.innerHeight,Math.max(d,b));m={left:x,top:p,width:N-x,height:S-p},r.style.left=`${x}px`,r.style.top=`${p}px`,r.style.width=`${m.width}px`,r.style.height=`${m.height}px`,l.textContent=`${Math.round(m.width)}×${Math.round(m.height)}px`};s.addEventListener("pointerdown",v=>{v.button===0&&(v.preventDefault(),o=!0,g=v.clientX,d=v.clientY,r.style.display="block",k(v.clientX,v.clientY),s.setPointerCapture(v.pointerId))}),s.addEventListener("pointermove",v=>{o&&(v.preventDefault(),k(v.clientX,v.clientY))}),s.addEventListener("pointerup",v=>{if(!o)return;o=!1,k(v.clientX,v.clientY);const b={...m};if(this.cancel(),b.width<24||b.height<24){this.callbacks.onToast("Área muito pequena — arraste um retângulo maior.");return}this.captureAndTranslateSelection(b)})}async captureAndTranslateSelection(e){await new Promise(M=>window.requestAnimationFrame(()=>window.requestAnimationFrame(()=>M())));const t=window.scrollX,n=window.scrollY,s=await chrome.runtime.sendMessage({type:"CAPTURE_VISIBLE_TAB"});if(!(s!=null&&s.ok)||!s.dataUrl){this.callbacks.onToast((s==null?void 0:s.message)||"Falha ao capturar tela da aba.");return}const i=await this.cropDataUrl(s.dataUrl,e);if(!i){this.callbacks.onToast("Não foi possível recortar a imagem capturada.");return}const r=document.createElement("div");r.className="sv-pin",r.style.left=`${Math.round(e.left+t)}px`,r.style.top=`${Math.round(e.top+n)}px`,r.style.width=`${Math.round(e.width)}px`,r.style.height=`${Math.round(e.height)}px`;const l=document.createElement("div");l.className="sv-pin-bar";const o=document.createElement("button");o.type="button",o.className="sv-pin-btn",o.textContent="Original",o.style.display="none";const g=document.createElement("button");g.type="button",g.className="sv-pin-btn",g.textContent="Copiar",g.style.display="none";const d=document.createElement("button");d.type="button",d.className="sv-pin-btn sv-pin-close",d.textContent="×",d.title="Fechar recorte",l.append(o,g,d);const m=document.createElement("div");m.className="sv-pin-loading",m.textContent="Traduzindo recorte...",r.append(l,m),this.pinsLayer.appendChild(r);let k=null;d.addEventListener("click",()=>{k&&URL.revokeObjectURL(k),r.remove()});const v=this.callbacks.getActiveGlossary(),b=await chrome.runtime.sendMessage({type:"TRANSLATE_IMAGE_URL",imageUrl:`sv-crop://${Date.now()}`,pageUrl:window.location.href,imageBase64:i.base64,forceRetranslate:!0,directOcr:!0,glossary:Object.keys(v).length>0?v:void 0});if(!b.ok){m.textContent=`Erro: ${b.message}`,m.style.color="#fecaca",this.callbacks.onError(b);return}if(b.no_text){m.textContent="Nenhum texto detectado neste recorte.",m.style.color="#cbd5e1";return}const x=atob(b.image_base64),p=new Uint8Array(x.length);for(let M=0;M<x.length;M++)p[M]=x.charCodeAt(M);const N=new Blob([p],{type:b.mime_type||"image/webp"});k=URL.createObjectURL(N);const S=document.createElement("img");S.className="sv-pin-img",S.src=k,m.remove(),r.appendChild(S);let P=!0;o.style.display="inline-block",o.addEventListener("click",()=>{P=!P,S.src=P?k:i.dataUrl,o.textContent=P?"Original":"Traduzido"});const q=(b.blocks??[]).map(M=>(M.translated_text||"").trim()).filter(Boolean).join(`
`);q&&(g.style.display="inline-block",g.addEventListener("click",async()=>{try{await navigator.clipboard.writeText(q),g.textContent="Copiado!",window.setTimeout(()=>{g.textContent="Copiar"},1600)}catch{this.callbacks.onToast("Falha ao copiar texto.")}}))}cropDataUrl(e,t){return new Promise(n=>{const s=new Image;s.onload=()=>{try{const i=s.naturalWidth/Math.max(1,window.innerWidth),r=s.naturalHeight/Math.max(1,window.innerHeight),l=Math.max(0,Math.round(t.left*i)),o=Math.max(0,Math.round(t.top*r)),g=Math.max(1,Math.min(s.naturalWidth-l,Math.round(t.width*i))),d=Math.max(1,Math.min(s.naturalHeight-o,Math.round(t.height*r))),m=document.createElement("canvas");m.width=g,m.height=d;const k=m.getContext("2d");if(!k){n(null);return}k.drawImage(s,l,o,g,d,0,0,g,d);const v=m.toDataURL("image/png"),b=v.indexOf(",");n({dataUrl:v,base64:b>=0?v.slice(b+1):v})}catch{n(null)}},s.onerror=()=>n(null),s.src=e})}}class le{constructor(e){c(this,"callbacks");c(this,"host");c(this,"shadow");c(this,"expanded",!1);c(this,"pillBtn");c(this,"pillDot");c(this,"pillText");c(this,"cardEl");c(this,"statusTextEl");c(this,"engineBtn");c(this,"telemetryEl");c(this,"langSelect");c(this,"translateNowBtn");c(this,"chapterBtn");c(this,"cropBtn");c(this,"autoCheckbox");c(this,"progressEl");c(this,"alertEl");c(this,"alertTitleEl");c(this,"alertDetailEl");c(this,"resumeBtn");c(this,"toastEl");c(this,"countdownTimer",null);c(this,"toastTimer",null);c(this,"countdownSec",0);this.callbacks=e,this.host=document.createElement("div"),this.host.id="sv-ptbr-floating-root",this.host.style.cssText="position:fixed;right:14px;bottom:14px;z-index:2147483645;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;",this.shadow=this.host.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
      * { box-sizing: border-box; }
      .sv-fab {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 7px 11px;
        border-radius: 999px;
        border: 1px solid rgba(148, 163, 184, 0.32);
        background: rgba(15, 23, 42, 0.86);
        color: #f8fafc;
        font-size: 12px;
        font-weight: 700;
        cursor: pointer;
        box-shadow: 0 6px 20px rgba(0,0,0,0.35);
        backdrop-filter: blur(8px);
        opacity: 0.75;
        transition: opacity 0.16s ease, transform 0.16s ease;
      }
      .sv-fab:hover { opacity: 1; transform: translateY(-1px); }
      .sv-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: #64748b; display: inline-block;
      }
      .sv-dot.running { background: #22c55e; box-shadow: 0 0 8px rgba(34,197,94,0.75); }
      .sv-dot.starting { background: #f59e0b; }
      .sv-dot.stopped { background: #64748b; }
      .sv-dot.alert { background: #ef4444; box-shadow: 0 0 8px rgba(239,68,68,0.8); }

      .sv-toast {
        display: none;
        margin-bottom: 8px;
        padding: 6px 10px;
        border-radius: 9px;
        background: rgba(15, 23, 42, 0.94);
        border: 1px solid rgba(56, 189, 248, 0.45);
        color: #e0f2fe;
        font-size: 11.5px;
        font-weight: 600;
        text-align: right;
        box-shadow: 0 6px 18px rgba(0,0,0,0.4);
      }
      .sv-toast.visible { display: block; }

      .sv-card {
        display: none;
        width: 286px;
        padding: 12px;
        border-radius: 14px;
        background: rgba(15, 23, 42, 0.96);
        border: 1px solid rgba(148, 163, 184, 0.25);
        color: #f1f5f9;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5);
        backdrop-filter: blur(10px);
        font-size: 12px;
      }
      .sv-card.open { display: block; }
      .sv-header {
        display: flex; align-items: center; justify-content: space-between;
        margin-bottom: 10px;
      }
      .sv-title { font-weight: 700; font-size: 13px; color: #38bdf8; }
      .sv-min-btn {
        background: transparent; border: none; color: #94a3b8; cursor: pointer;
        font-size: 14px; padding: 2px 6px; border-radius: 6px;
      }
      .sv-min-btn:hover { background: rgba(148,163,184,0.16); color: #fff; }
      .sv-row {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; margin-bottom: 8px;
      }
      .sv-status { font-size: 11.5px; color: #cbd5e1; }
      .sv-btn {
        border: none; border-radius: 8px; padding: 7px 10px;
        font-size: 11.5px; font-weight: 600; cursor: pointer;
        transition: filter 0.14s ease;
      }
      .sv-btn:hover { filter: brightness(1.08); }
      .sv-btn:disabled { opacity: 0.5; cursor: not-allowed; }
      .sv-btn-power-on { background: #16a34a; color: #fff; }
      .sv-btn-power-off { background: #dc2626; color: #fff; }
      .sv-actions-grid {
        display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 8px;
      }
      .sv-btn-primary { background: #0284c7; color: #fff; }
      .sv-btn-chapter { background: #6366f1; color: #fff; }
      .sv-btn-crop { width: 100%; margin-bottom: 8px; background: #0f766e; color: #fff; }
      .sv-btn-cancel { background: #b91c1c; color: #fff; }
      .sv-telemetry {
        font-size: 11px; color: #94a3b8; background: rgba(30, 41, 59, 0.72);
        border-radius: 8px; padding: 6px 8px; margin-bottom: 8px; line-height: 1.45;
      }
      .sv-lang-row {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; margin-bottom: 8px; font-size: 11.5px; color: #cbd5e1;
      }
      .sv-select {
        background: #0f172a; color: #f8fafc;
        border: 1px solid rgba(148, 163, 184, 0.3);
        border-radius: 7px; padding: 4px 7px; font-size: 11.5px;
      }
      .sv-toggle {
        display: flex; align-items: center; justify-content: space-between;
        padding: 6px 8px; border-radius: 8px; background: rgba(30, 41, 59, 0.55);
        margin-bottom: 6px; cursor: pointer;
      }
      .sv-meta-row {
        display: flex; align-items: center; justify-content: space-between;
        font-size: 10.5px; color: #94a3b8;
      }
      .sv-alert {
        display: none;
        margin-top: 8px; padding: 8px; border-radius: 8px;
        background: rgba(245, 158, 11, 0.16);
        border: 1px solid rgba(245, 158, 11, 0.45);
        color: #fde68a;
        font-size: 11px; line-height: 1.4;
      }
      .sv-alert.visible { display: block; }
      .sv-alert.error {
        background: rgba(239, 68, 68, 0.16);
        border-color: rgba(239, 68, 68, 0.48);
        color: #fecaca;
      }
      .sv-alert-title { font-weight: 700; margin-bottom: 3px; }
      .sv-btn-resume {
        margin-top: 6px; width: 100%;
        background: #f59e0b; color: #0f172a; font-weight: 700;
      }
    `,this.toastEl=document.createElement("div"),this.toastEl.className="sv-toast",this.pillBtn=document.createElement("button"),this.pillBtn.type="button",this.pillBtn.className="sv-fab",this.pillBtn.title="Scanlator Vogels PT-BR (Alt+T página | Alt+S recorte | Alt+Shift+T auto)",this.pillDot=document.createElement("span"),this.pillDot.className="sv-dot stopped",this.pillText=document.createElement("span"),this.pillText.textContent="SV",this.pillBtn.append(this.pillDot,this.pillText),this.cardEl=document.createElement("div"),this.cardEl.className="sv-card",this.cardEl.innerHTML=`
      <div class="sv-header">
        <span class="sv-title">Scanlator Vogels PT-BR</span>
        <button type="button" class="sv-min-btn" title="Minimizar">—</button>
      </div>
      <div class="sv-row">
        <span class="sv-status">Motor: Desligado</span>
        <button type="button" class="sv-btn sv-btn-power-on">Ligar Motor</button>
      </div>
      <div class="sv-telemetry">0% CPU • 0 MB RAM • 0 MB VRAM</div>
      <div class="sv-lang-row">
        <span>Idioma de origem</span>
        <select class="sv-select" id="svLang"></select>
      </div>
      <div class="sv-actions-grid">
        <button type="button" class="sv-btn sv-btn-primary" id="svPageBtn">Traduzir Página</button>
        <button type="button" class="sv-btn sv-btn-chapter" id="svChapterBtn">Traduzir Capítulo</button>
      </div>
      <button type="button" class="sv-btn sv-btn-crop" id="svCropBtn">Recorte Manual (Alt+S)</button>
      <label class="sv-toggle">
        <span>Auto Translate (lote de 4)</span>
        <input type="checkbox" />
      </label>
      <div class="sv-meta-row">
        <span>Alt+T • Alt+S • Alt+Shift+T</span>
        <span class="sv-progress">0 / 0 págs</span>
      </div>
      <div class="sv-alert">
        <div class="sv-alert-title"></div>
        <div class="sv-alert-detail"></div>
        <button type="button" class="sv-btn sv-btn-resume">Testar Retomada</button>
      </div>
    `;const n=this.cardEl.querySelector(".sv-min-btn");this.statusTextEl=this.cardEl.querySelector(".sv-status"),this.engineBtn=this.cardEl.querySelector(".sv-row .sv-btn"),this.telemetryEl=this.cardEl.querySelector(".sv-telemetry"),this.langSelect=this.cardEl.querySelector("#svLang"),this.translateNowBtn=this.cardEl.querySelector("#svPageBtn"),this.chapterBtn=this.cardEl.querySelector("#svChapterBtn"),this.cropBtn=this.cardEl.querySelector("#svCropBtn"),this.autoCheckbox=this.cardEl.querySelector("input[type='checkbox']"),this.progressEl=this.cardEl.querySelector(".sv-progress"),this.alertEl=this.cardEl.querySelector(".sv-alert"),this.alertTitleEl=this.cardEl.querySelector(".sv-alert-title"),this.alertDetailEl=this.cardEl.querySelector(".sv-alert-detail"),this.resumeBtn=this.cardEl.querySelector(".sv-btn-resume");for(const s of se){const i=document.createElement("option");i.value=s.value,i.textContent=s.label,this.langSelect.appendChild(i)}this.pillBtn.addEventListener("click",()=>this.setExpanded(!0)),n.addEventListener("click",()=>this.setExpanded(!1)),this.engineBtn.addEventListener("click",()=>this.callbacks.onToggleEngine()),this.translateNowBtn.addEventListener("click",()=>this.callbacks.onTranslateVisible()),this.chapterBtn.addEventListener("click",()=>this.callbacks.onToggleChapter()),this.cropBtn.addEventListener("click",()=>this.callbacks.onManualCrop()),this.autoCheckbox.addEventListener("change",()=>this.callbacks.onToggleAutoTranslate(this.autoCheckbox.checked)),this.langSelect.addEventListener("change",()=>this.callbacks.onChangeSourceLang(this.langSelect.value)),this.resumeBtn.addEventListener("click",()=>this.callbacks.onResumeCircuit()),this.shadow.append(t,this.toastEl,this.pillBtn,this.cardEl),document.documentElement.appendChild(this.host)}setExpanded(e){this.expanded=e,this.cardEl.classList.toggle("open",e),this.pillBtn.style.display=e?"none":"inline-flex"}showToast(e,t=2200){this.toastTimer!==null&&window.clearTimeout(this.toastTimer),this.toastEl.textContent=e,this.toastEl.classList.add("visible"),this.toastTimer=window.setTimeout(()=>{this.toastEl.classList.remove("visible"),this.toastTimer=null},t)}updateEngineState(e,t){var n,s;if(this.pillDot.className=`sv-dot ${e}`,e==="running"&&t){this.statusTextEl.textContent="Motor: Ativo",this.engineBtn.textContent="Desligar Motor",this.engineBtn.className="sv-btn sv-btn-power-off",this.engineBtn.disabled=!1,this.translateNowBtn.disabled=!1,this.chapterBtn.disabled=!1,this.cropBtn.disabled=!1;const i=t.warming_up?"Aquecendo GPU...":`VRAM: ${Math.round(t.torch_vram_allocated_mb)} MB Torch`,r=((n=t.quota)==null?void 0:n.requests_today)??0,l=Math.ceil(t.idle_remaining_sec/60);this.telemetryEl.textContent=`${i} • Cota hoje: ${r} req(s) • Auto-off: ~${l}m`,((s=t.circuit_breaker)==null?void 0:s.state)==="OPEN"&&(this.pillDot.className="sv-dot alert")}else e==="starting"?(this.statusTextEl.textContent="Motor: Ligando...",this.engineBtn.textContent="Ligando...",this.engineBtn.disabled=!0,this.translateNowBtn.disabled=!0,this.chapterBtn.disabled=!0,this.cropBtn.disabled=!0,this.telemetryEl.textContent="Iniciando servidor local em 127.0.0.1..."):e==="stopping"?(this.statusTextEl.textContent="Motor: Desligando...",this.engineBtn.textContent="Desligando...",this.engineBtn.disabled=!0,this.translateNowBtn.disabled=!0,this.chapterBtn.disabled=!0,this.cropBtn.disabled=!0,this.telemetryEl.textContent="Liberando 100% da VRAM..."):(this.statusTextEl.textContent="Motor: Desligado",this.engineBtn.textContent="Ligar Motor",this.engineBtn.className="sv-btn sv-btn-power-on",this.engineBtn.disabled=!1,this.translateNowBtn.disabled=!1,this.chapterBtn.disabled=!1,this.cropBtn.disabled=!1,this.telemetryEl.textContent="0% CPU • 0 MB RAM • 0 MB VRAM")}updateSettings(e){this.autoCheckbox.checked=e.autoTranslate,this.langSelect.value=e.sourceLang}updateProgress(e,t,n,s,i){const r=s||n>0?` • fila: ${n+(s?1:0)}`:"";this.progressEl.textContent=`${e}/${t} págs${r}`,this.pillText.textContent=i||s?`SV ${e}/${t}`:"SV",i?(this.chapterBtn.textContent="Pausar / Cancelar",this.chapterBtn.className="sv-btn sv-btn-cancel"):(this.chapterBtn.textContent="Traduzir Capítulo",this.chapterBtn.className="sv-btn sv-btn-chapter")}showAlert(e){if(this.countdownTimer!==null&&(window.clearInterval(this.countdownTimer),this.countdownTimer=null),this.alertEl.className=e.kind==="cors"||e.kind==="error"?"sv-alert error visible":"sv-alert visible",this.alertTitleEl.textContent=e.title,this.resumeBtn.style.display=e.showResumeButton?"block":"none",e.retryAfterSec&&e.retryAfterSec>0){this.countdownSec=Math.ceil(e.retryAfterSec);const t=()=>{this.alertDetailEl.textContent=`${e.detail} (retomada automática em ${this.countdownSec}s)`};t(),this.countdownTimer=window.setInterval(()=>{this.countdownSec=Math.max(0,this.countdownSec-1),this.countdownSec<=0?(this.countdownTimer!==null&&window.clearInterval(this.countdownTimer),this.countdownTimer=null,this.alertDetailEl.textContent=`${e.detail} — pronto para testar retomada.`):t()},1e3)}else this.alertDetailEl.textContent=e.detail;e.kind==="circuit_open"&&(this.pillDot.className="sv-dot alert",this.setExpanded(!0))}clearAlert(){this.countdownTimer!==null&&(window.clearInterval(this.countdownTimer),this.countdownTimer=null),this.alertEl.classList.remove("visible")}}const F=["data-src","data-original","data-lazy-src","data-url","data-cfsrc","data-actualsrc","data-image","data-hi-res-src"],W=["data-srcset","data-lazy-srcset","srcset"],ce=/\b(logo|avatar|icon|banner|thumb|thumbnail|cover|emoji|badge|button|sponsor|ads?|promo|gravatar|spinner|loading|placeholder)\b/i;function G(E){const e=E.split(",").map(t=>t.trim()).filter(Boolean).map(t=>{const n=t.split(/\s+/),s=n[0],i=n[1]||"1x";let r=1;return i.endsWith("w")?r=parseFloat(i)||1:i.endsWith("x")&&(r=(parseFloat(i)||1)*1e3),{url:s,weight:r}}).filter(t=>!!t.url);return e.length===0?null:(e.sort((t,n)=>n.weight-t.weight),e[0].url)}function O(E){if(!E)return null;const e=E.trim();if(!e||e.startsWith("about:")||e.startsWith("javascript:")||e.startsWith("data:image/svg")||e.startsWith("data:image/gif"))return null;try{return new URL(e,window.location.href).href}catch{return null}}class de{constructor(){c(this,"name","GenericReaderAdapter")}matches(e,t){return!0}resolveImageUrl(e){const t=e.dataset.svOrigUrl;if(t)return t;const n=e.getAttribute("src")||"";if(!n||n.startsWith("data:image/gif")||n.startsWith("data:image/svg")||/loading|placeholder|blank|spacer|pixel/i.test(n)){for(const l of F){const o=O(e.getAttribute(l));if(o)return o}for(const l of W){const o=e.getAttribute(l);if(o){const g=O(G(o));if(g)return g}}}const i=e.parentElement;if(i&&i.tagName==="PICTURE"){const l=Array.from(i.querySelectorAll("source"));for(const o of l){const g=o.getAttribute("srcset")||o.getAttribute("data-srcset");if(g){const d=O(G(g));if(d)return d}}}for(const l of W){const o=e.getAttribute(l);if(o){const g=O(G(o));if(g)return g}}const r=O(e.currentSrc||e.src);if(r&&!r.startsWith("blob:"))return r;for(const l of F){const o=O(e.getAttribute(l));if(o)return o}return r}isComicPageCandidate(e){if(!e.isConnected||e.dataset.svIgnore==="1"||!this.resolveImageUrl(e))return!1;const n=window.getComputedStyle(e);if(n.display==="none"||n.visibility==="hidden"||n.opacity==="0")return!1;const s=e.getBoundingClientRect(),i=e.naturalWidth||0,r=e.naturalHeight||0,l=Math.max(i,Math.round(s.width)),o=Math.max(r,Math.round(s.height));if(l<260||o<320)return!1;const g=l/Math.max(1,o);if(g>2.25||g<.06)return!1;const d=`${e.className} ${e.id} ${e.alt}`;return!(ce.test(d)&&l<620||e.closest("a")&&s.width<420&&s.height<520)}findCandidateImages(e){const n=Array.from(e.querySelectorAll("img")).filter(s=>this.isComicPageCandidate(s));return n.sort((s,i)=>{const r=s.getBoundingClientRect(),l=i.getBoundingClientRect(),o=r.top-l.top;return Math.abs(o)>24?o:r.left-l.left}),n}}const ue=[];function he(E=document){const e=new URL(window.location.href);for(const t of ue)if(t.matches(e,E))return t;return new de}class pe{constructor(e,t){c(this,"adapter");c(this,"callbacks");c(this,"mutationObserver",null);c(this,"intersectionObserver",null);c(this,"observedElements",new Set);c(this,"visibilityRatios",new Map);c(this,"pages",[]);c(this,"scanTimer",null);c(this,"lastVisibleEl",null);c(this,"handleGlobalTrigger",()=>{this.scheduleScan()});c(this,"handleScroll",()=>{this.emitVisibleIfChanged()});this.callbacks=e,this.adapter=t??he(document)}getAdapterName(){return this.adapter.name}getPages(){return this.pages}getVisiblePage(){if(this.pages.length===0)return null;let e=null,t=-1;const n=window.innerHeight||900;for(const s of this.pages){const i=s.element.getBoundingClientRect(),r=Math.max(0,i.top),l=Math.min(n,i.bottom),o=Math.max(0,l-r),g=this.visibilityRatios.get(s.element)??0,d=o+g*250;o>40&&d>t&&(t=d,e=s.element)}return e?this.pages.find(s=>s.element===e)??this.pages[0]:this.pages[0]}start(){this.intersectionObserver=new IntersectionObserver(e=>{for(const t of e){const n=t.target;this.visibilityRatios.set(n,t.isIntersecting?t.intersectionRatio:0)}this.emitVisibleIfChanged()},{threshold:[0,.15,.35,.6,.85]}),this.mutationObserver=new MutationObserver(()=>{this.scheduleScan()}),this.mutationObserver.observe(document.documentElement,{childList:!0,subtree:!0,attributes:!0,attributeFilter:["src","srcset","data-src","data-original","data-lazy-src","class","style"]}),window.addEventListener("load",this.handleGlobalTrigger,{passive:!0}),window.addEventListener("scroll",this.handleScroll,{passive:!0}),window.addEventListener("resize",this.handleGlobalTrigger,{passive:!0}),this.scanNow()}stop(){var e,t;this.scanTimer!==null&&(window.clearTimeout(this.scanTimer),this.scanTimer=null),(e=this.mutationObserver)==null||e.disconnect(),(t=this.intersectionObserver)==null||t.disconnect(),this.observedElements.clear(),this.visibilityRatios.clear(),window.removeEventListener("load",this.handleGlobalTrigger),window.removeEventListener("scroll",this.handleScroll),window.removeEventListener("resize",this.handleGlobalTrigger)}scheduleScan(e=250){this.scanTimer===null&&(this.scanTimer=window.setTimeout(()=>{this.scanTimer=null,this.scanNow()},e))}scanNow(){var s,i;const e=this.adapter.findCandidateImages(document),t=[];for(let r=0;r<e.length;r++){const l=e[r],o=this.adapter.resolveImageUrl(l);o&&(l.dataset.svOrigUrl||(l.dataset.svOrigUrl=o),t.push({element:l,imageUrl:o,index:r}),this.observedElements.has(l)||(this.observedElements.add(l),(s=this.intersectionObserver)==null||s.observe(l),l.addEventListener("load",this.handleGlobalTrigger,{passive:!0})))}const n=new Set(e);for(const r of Array.from(this.observedElements))n.has(r)||((i=this.intersectionObserver)==null||i.unobserve(r),this.observedElements.delete(r),this.visibilityRatios.delete(r));this.pages=t,this.callbacks.onPagesChanged(this.pages),this.emitVisibleIfChanged()}emitVisibleIfChanged(){const e=this.getVisiblePage(),t=(e==null?void 0:e.element)??null;t!==this.lastVisibleEl&&(this.lastVisibleEl=t,this.callbacks.onVisiblePageChanged(e,this.pages))}}class ge{constructor(e){c(this,"callbacks");c(this,"records",new Map);c(this,"hostEl");c(this,"shadow");c(this,"layerEl");c(this,"rafId",null);c(this,"scheduleReposition",()=>{this.rafId===null&&(this.rafId=window.requestAnimationFrame(()=>{this.rafId=null;for(const e of this.records.values())e.hoverBar.classList.contains("visible")&&this.positionBar(e)}))});this.callbacks=e,this.hostEl=document.createElement("div"),this.hostEl.id="sv-ptbr-overlay-root",this.hostEl.style.cssText="position:fixed;inset:0;pointer-events:none;z-index:2147483640;overflow:hidden;",this.shadow=this.hostEl.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
      .sv-bar {
        position: fixed;
        display: none;
        align-items: center;
        gap: 6px;
        padding: 4px 7px;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.88);
        border: 1px solid rgba(148, 163, 184, 0.35);
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
        backdrop-filter: blur(6px);
        pointer-events: auto;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 11px;
        color: #f8fafc;
        transition: opacity 0.14s ease;
        user-select: none;
      }
      .sv-bar.visible { display: flex; }
      .sv-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 2px 6px;
        border-radius: 999px;
        font-weight: 600;
        letter-spacing: 0.02em;
      }
      .sv-pill.loading { background: rgba(56, 189, 248, 0.22); color: #7dd3fc; }
      .sv-pill.done { background: rgba(34, 197, 94, 0.20); color: #86efac; }
      .sv-pill.orig { background: rgba(148, 163, 184, 0.22); color: #e2e8f0; }
      .sv-pill.notext { background: rgba(148, 163, 184, 0.18); color: #cbd5e1; }
      .sv-pill.error { background: rgba(239, 68, 68, 0.24); color: #fca5a5; }
      .sv-btn {
        appearance: none;
        border: 1px solid rgba(148, 163, 184, 0.32);
        background: rgba(30, 41, 59, 0.92);
        color: #f8fafc;
        border-radius: 999px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        line-height: 1.2;
      }
      .sv-btn:hover { background: rgba(56, 189, 248, 0.28); border-color: #38bdf8; }
    `,this.layerEl=document.createElement("div"),this.shadow.append(t,this.layerEl),document.documentElement.appendChild(this.hostEl),window.addEventListener("scroll",this.scheduleReposition,{passive:!0}),window.addEventListener("resize",this.scheduleReposition,{passive:!0})}ensureRecord(e){const t=this.records.get(e);if(t)return t;const n=document.createElement("div");n.className="sv-bar";const s=document.createElement("span");s.className="sv-pill",s.textContent="SV";const i=document.createElement("button");i.type="button",i.className="sv-btn",i.textContent="Ver Original";const r=document.createElement("button");r.type="button",r.className="sv-btn",r.textContent="Retry",r.title="Refaz somente a traducao (reaproveita OCR/inpainting do cache)",n.append(s,i,r),this.layerEl.appendChild(n);const l={img:e,origSrc:e.dataset.svOrigUrl||e.currentSrc||e.src,origSrcset:e.getAttribute("srcset")||"",translatedUrl:null,showingTranslated:!1,state:"idle",errorMessage:"",blocksCount:0,hoverBar:n,toggleBtn:i,retryBtn:r,statusPill:s,isHovered:!1,barHovered:!1};return i.addEventListener("click",o=>{o.preventDefault(),o.stopPropagation(),l.state==="translated"&&l.translatedUrl?this.toggleOriginal(e):this.callbacks.onManualTranslateRequested(e)}),r.addEventListener("click",o=>{o.preventDefault(),o.stopPropagation(),this.callbacks.onRetryRequested(e)}),e.addEventListener("mouseenter",()=>{l.isHovered=!0,this.updateBarVisibility(l)}),e.addEventListener("mouseleave",()=>{l.isHovered=!1,window.setTimeout(()=>this.updateBarVisibility(l),120)}),n.addEventListener("mouseenter",()=>{l.barHovered=!0,this.updateBarVisibility(l)}),n.addEventListener("mouseleave",()=>{l.barHovered=!1,this.updateBarVisibility(l)}),this.records.set(e,l),l}getState(e){var t;return((t=this.records.get(e))==null?void 0:t.state)??"idle"}getTranslatedCount(){let e=0;for(const t of this.records.values())t.img.isConnected&&(t.state==="translated"||t.state==="no_text")&&(e+=1);return e}setLoading(e){const t=this.ensureRecord(e);t.state="loading",t.errorMessage="",this.syncRecordUi(t)}applyTranslatedBlob(e,t,n){const s=this.ensureRecord(e);s.translatedUrl&&URL.revokeObjectURL(s.translatedUrl);const i=URL.createObjectURL(t);s.translatedUrl=i,s.blocksCount=n,s.state="translated",s.showingTranslated=!0,s.errorMessage="",e.dataset.svOrigUrl||(e.dataset.svOrigUrl=s.origSrc),s.origSrcset&&e.removeAttribute("srcset"),e.src=i,this.syncRecordUi(s)}markNoText(e){const t=this.ensureRecord(e);t.state="no_text",t.blocksCount=0,t.errorMessage="",this.syncRecordUi(t)}setError(e,t){const n=this.ensureRecord(e);n.state!=="translated"&&(n.state="error"),n.errorMessage=t,this.syncRecordUi(n)}toggleOriginal(e){const t=this.records.get(e);!t||!t.translatedUrl||(t.showingTranslated?(e.src=t.origSrc,t.origSrcset&&e.setAttribute("srcset",t.origSrcset),t.showingTranslated=!1):(t.origSrcset&&e.removeAttribute("srcset"),e.src=t.translatedUrl,t.showingTranslated=!0),this.syncRecordUi(t))}restoreAllOriginals(){for(const e of this.records.values())e.showingTranslated&&(e.img.src=e.origSrc,e.origSrcset&&e.img.setAttribute("srcset",e.origSrcset),e.showingTranslated=!1),e.translatedUrl&&(URL.revokeObjectURL(e.translatedUrl),e.translatedUrl=null),e.state="idle",this.syncRecordUi(e)}syncRecordUi(e){if(e.state==="loading")e.statusPill.className="sv-pill loading",e.statusPill.textContent="Traduzindo...",e.toggleBtn.style.display="none",e.retryBtn.style.display="none";else if(e.state==="translated")e.statusPill.className=e.showingTranslated?"sv-pill done":"sv-pill orig",e.statusPill.textContent=e.showingTranslated?`PT-BR (${e.blocksCount})`:"Original",e.toggleBtn.style.display="inline-block",e.toggleBtn.textContent=e.showingTranslated?"Original":"Traduzido",e.retryBtn.style.display="inline-block";else if(e.state==="no_text")e.statusPill.className="sv-pill notext",e.statusPill.textContent="Sem texto",e.toggleBtn.style.display="none",e.retryBtn.style.display="inline-block";else if(e.state==="error")e.statusPill.className="sv-pill error",e.statusPill.textContent="Erro",e.statusPill.title=e.errorMessage,e.toggleBtn.style.display="none",e.retryBtn.style.display="inline-block";else{e.hoverBar.classList.remove("visible");return}this.updateBarVisibility(e)}updateBarVisibility(e){if(!e.img.isConnected||e.state==="idle"){e.hoverBar.classList.remove("visible");return}if(!(e.state==="loading"||e.state==="error"||e.isHovered||e.barHovered)){e.hoverBar.classList.remove("visible");return}this.positionBar(e)}positionBar(e){const t=e.img.getBoundingClientRect(),n=window.innerWidth,s=window.innerHeight;if(t.bottom<0||t.top>s||t.right<0||t.left>n||t.width<120){e.hoverBar.classList.remove("visible");return}e.hoverBar.classList.add("visible");const i=Math.max(8,Math.min(s-38,t.top+10)),r=Math.max(8,n-Math.min(n-8,t.right-10));e.hoverBar.style.top=`${Math.round(i)}px`,e.hoverBar.style.right=`${Math.round(r)}px`}}class me{constructor(e,t,n,s=4){c(this,"singleWorkerFn");c(this,"batchWorkerFn");c(this,"onQueueStateChange");c(this,"maxBatchSize");c(this,"queue",[]);c(this,"activeUrls",new Set);c(this,"running",!1);c(this,"paused",!1);c(this,"chapterActive",!1);c(this,"generation",0);this.singleWorkerFn=e,this.batchWorkerFn=t,this.onQueueStateChange=n,this.maxBatchSize=Math.max(1,Math.min(4,s))}enqueue(e,t,n=!1){if(this.paused&&t<90||this.activeUrls.has(e.imageUrl)&&!n)return;const s=this.queue.find(i=>i.page.imageUrl===e.imageUrl);s?(s.priority=Math.max(s.priority,t),s.forceRetranslate=s.forceRetranslate||n,s.page=e):this.queue.push({page:e,priority:t,forceRetranslate:n}),this.sortQueue(),this.emitState(),this.pump()}enqueueChapter(e,t){this.paused=!1,this.chapterActive=!0;const n=(t==null?void 0:t.imageUrl)??null;for(const s of e){if(this.activeUrls.has(s.imageUrl))continue;const i=n&&s.imageUrl===n?100:30,r=this.queue.find(l=>l.page.imageUrl===s.imageUrl);r?(r.priority=Math.max(r.priority,i),r.page=s):this.queue.push({page:s,priority:i,forceRetranslate:!1})}this.sortQueue(),this.emitState(),this.pump()}pauseAndClearPrefetch(){this.paused=!0,this.chapterActive=!1,this.queue=[],this.emitState()}cancelAll(){this.paused=!0,this.chapterActive=!1,this.generation+=1,this.queue=[],this.activeUrls.clear(),this.emitState()}resume(){this.paused=!1,this.emitState(),this.pump()}isPaused(){return this.paused}isChapterActive(){return this.chapterActive&&(this.queue.length>0||this.activeUrls.size>0)}getPendingCount(){return this.queue.length+this.activeUrls.size}sortQueue(){this.queue.sort((e,t)=>t.priority-e.priority||e.page.index-t.page.index)}emitState(){const e=this.activeUrls.size;e===0&&this.queue.length===0&&(this.chapterActive=!1),this.onQueueStateChange(this.queue.length,e,this.chapterActive)}async pump(){if(this.running)return;this.running=!0;const e=this.generation;try{for(;this.queue.length>0&&e===this.generation;){const t=this.queue[0];if(this.paused&&t.priority<90)break;if(t.priority>=50||t.forceRetranslate){const i=this.queue.shift(),r=this.paused?[]:this.queue.slice(0,2);this.activeUrls.add(i.page.imageUrl),this.emitState();try{await this.singleWorkerFn(i,r)}finally{e===this.generation&&(this.activeUrls.delete(i.page.imageUrl),this.emitState())}continue}const n=[];for(;n.length<this.maxBatchSize&&this.queue.length>0&&this.queue[0].priority<50&&!this.queue[0].forceRetranslate;){const i=this.queue.shift();n.push(i),this.activeUrls.add(i.page.imageUrl)}const s=this.paused?[]:this.queue.slice(0,2);this.emitState();try{n.length>=2?await this.batchWorkerFn(n,s):n.length===1&&await this.singleWorkerFn(n[0],s)}finally{if(e===this.generation){for(const i of n)this.activeUrls.delete(i.page.imageUrl);this.emitState()}}}}finally{this.running=!1,this.emitState()}}}if(!window.__svPtBrLoaded){let E=function(){return Z(g,window.location.href).series.terms},e=function(){return $(E())},t=function(a,h="image/webp"){const u=atob(a),w=u.length,y=new Uint8Array(w);for(let C=0;C<w;C++)y[C]=u.charCodeAt(C);return new Blob([y],{type:h})},n=function(a){try{if(!a.complete||a.naturalWidth<100||a.naturalHeight<100)return;const h=document.createElement("canvas");h.width=a.naturalWidth,h.height=a.naturalHeight;const u=h.getContext("2d");if(!u)return;u.drawImage(a,0,0);let w=h.toDataURL("image/webp",.92);w.startsWith("data:image/webp")||(w=h.toDataURL("image/png"));const y=w.indexOf(",");return y>=0?w.slice(y+1):void 0}catch{return}},s=function(a,h=24e3){return new Promise(u=>{let w=!1;const y=window.setTimeout(()=>{w||(w=!0,u({ok:!1,state:"stopped",health:null,message:`Timeout (${Math.round(h/1e3)}s) aguardando resposta do motor local.`}))},h);chrome.runtime.sendMessage(a).then(C=>{w||(w=!0,window.clearTimeout(y),u(C))}).catch(C=>{w||(w=!0,window.clearTimeout(y),u({ok:!1,state:"stopped",health:null,message:C instanceof Error?C.message:String(C)}))})})},i=function(){if(!o.autoTranslate||d!=="running"||x.isPaused())return;const a=S.getPages(),h=S.getVisiblePage();if(!h)return;b.getState(h.element)==="idle"&&x.enqueue(h,50,!1);const u=Math.max(3,Math.min(4,o.prefetchCount||4)),w=h.index,y=Math.min(a.length,w+1+u);for(let C=w+1;C<y;C++){const L=a[C];b.getState(L.element)==="idle"&&x.enqueue(L,10,!1)}},r=function(a){var w;if(!a||x.isPaused()||d!=="running"||((w=m==null?void 0:m.circuit_breaker)==null?void 0:w.state)==="OPEN"||!a.element.isConnected||b.getState(a.element)!=="idle")return;const h=`${a.imageUrl}::${o.sourceLang}`;if(k.has(h))return;k.add(h);const u=n(a.element);chrome.runtime.sendMessage({type:"PREPARE_IMAGE_URL",imageUrl:a.imageUrl,pageUrl:window.location.href,imageBase64:u}).catch(()=>{k.delete(h)})},l=function(a){var h;a.code==="CIRCUIT_BREAKER_OPEN"||a.code==="RATE_LIMITED_429"||a.code==="UPSTREAM_UNAVAILABLE"?(x.pauseAndClearPrefetch(),p.showAlert({kind:"circuit_open",title:a.code==="CIRCUIT_BREAKER_OPEN"?"Circuito aberto (cota / instabilidade Gemini)":a.code==="RATE_LIMITED_429"?"Limite Free Tier atingido (429)":"Gemini temporariamente indisponível (503)",detail:"Prefetch e capítulo pausados; páginas já traduzidas foram preservadas. "+a.message,retryAfterSec:a.retry_after_sec??((h=a.circuit_breaker)==null?void 0:h.retry_after_sec)??30,showResumeButton:!0})):a.code==="CORS_OR_NETWORK_BLOCKED"?p.showAlert({kind:"cors",title:"Acesso à imagem bloqueado pelo site",detail:a.message}):a.code==="ENGINE_OFFLINE"?(x.cancelAll(),d="stopped",m=null,p.updateEngineState("stopped",null),p.showAlert({kind:"info",title:"Motor local desligado",detail:a.message})):p.showAlert({kind:"error",title:`Erro (${a.code})`,detail:a.message})};window.__svPtBrLoaded=!0;let o={...ae},g=J(),d="stopped",m=null;const k=new Set,v=new re,b=new ge({onRetryRequested:a=>{const h=S.getPages().find(u=>u.element===a);h&&(p.clearAlert(),x.enqueue(h,100,!0))},onManualTranslateRequested:a=>{const h=S.getPages().find(u=>u.element===a);h&&(p.clearAlert(),x.enqueue(h,100,!1))}}),x=new me(async(a,h)=>{await X(a,h)},async(a,h)=>{await be(a,h)},(a,h,u)=>{const w=h>0,y=w?a+Math.max(0,h-1):a;p.updateProgress(b.getTranslatedCount(),S.getPages().length,y,w,u)},4),p=new le({onToggleEngine:async()=>{if(d==="running"){x.cancelAll(),k.clear(),p.updateEngineState("stopping",m);const a=await s({type:"ENGINE_STOP"},15e3);d=a.state,m=a.health,p.updateEngineState(d,m)}else{p.clearAlert(),p.updateEngineState("starting",null);const a=await s({type:"ENGINE_START"},24e3);d=a.state,m=a.health,p.updateEngineState(d,m),a.ok?d==="running"&&(x.resume(),i()):p.showAlert({kind:"error",title:"Motor local não iniciou",detail:a.message||"Falha desconhecida ao iniciar motor em 127.0.0.1."})}},onTranslateVisible:()=>{z()},onToggleChapter:()=>{x.isChapterActive()?(x.pauseAndClearPrefetch(),p.showToast("Tradução do capítulo pausada.")):K()},onManualCrop:()=>{p.setExpanded(!1),N.start()},onToggleAutoTranslate:async a=>{await P(a)},onChangeSourceLang:async a=>{o=await chrome.runtime.sendMessage({type:"SAVE_SETTINGS",settings:{sourceLang:a}}),k.clear(),p.updateSettings(o),p.showToast(`Idioma de origem: ${a.toUpperCase()}`)},onResumeCircuit:async()=>{await chrome.runtime.sendMessage({type:"RESET_CIRCUIT_BREAKER"}),p.clearAlert(),x.resume(),z()}}),N=new oe({ensureEngineRunning:q,getActiveGlossary:E,onError:a=>{a.ok||l(a)},onToast:a=>p.showToast(a)}),S=new pe({onPagesChanged:a=>{p.updateProgress(b.getTranslatedCount(),a.length,x.getPendingCount(),!1,x.isChapterActive()),M(a),o.autoTranslate&&d==="running"&&i()},onVisiblePageChanged:()=>{o.autoTranslate&&d==="running"&&i()}});async function P(a){o=await chrome.runtime.sendMessage({type:"SAVE_SETTINGS",settings:{autoTranslate:a}}),p.updateSettings(o),a?(x.resume(),i(),p.showToast("Auto Translate ATIVADO (lote de 4 págs)")):(x.pauseAndClearPrefetch(),p.showToast("Auto Translate DESATIVADO"))}async function q(){if(d==="running")return!0;const a=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});return d=a.state,m=a.health,p.updateEngineState(d,m),d==="running"?!0:(p.showAlert({kind:"info",title:"Motor local desligado",detail:"Clique em 'Ligar Motor' (ou execute start.bat) para iniciar a tradução local."}),p.setExpanded(!0),!1)}async function M(a){const h=e();for(const u of a){if(b.getState(u.element)!=="idle")continue;const w=V(u.imageUrl,o,h),y=await v.get(w);y&&(y.noText?b.markNoText(u.element):y.blob&&b.applyTranslatedBlob(u.element,y.blob,y.blocksCount))}p.updateProgress(b.getTranslatedCount(),a.length,x.getPendingCount(),!1,x.isChapterActive())}async function z(){S.scanNow();const a=S.getVisiblePage();if(!a){p.showAlert({kind:"info",title:"Nenhuma página de mangá detectada",detail:"Role até a imagem do capítulo e tente novamente."});return}await q()&&(p.clearAlert(),x.enqueue(a,100,!1))}async function K(){S.scanNow();const a=S.getPages();if(a.length===0){p.showAlert({kind:"info",title:"Nenhuma página detectada neste capítulo",detail:"Aguarde as imagens do capítulo carregarem e tente novamente."});return}if(!await q())return;p.clearAlert();const h=a.filter(w=>{const y=b.getState(w.element);return y==="idle"||y==="error"});if(h.length===0){p.showToast("Todas as páginas já estão traduzidas!");return}const u=S.getVisiblePage();x.enqueueChapter(h,u),p.showToast(`Traduzindo capítulo (${h.length} págs em lotes de 4)...`)}async function Y(){try{const a=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});d=a.state,m=a.health,p.updateEngineState(d,m)}catch{}}async function X(a,h){const{page:u,forceRetranslate:w}=a;if(!u.element.isConnected)return;const y=E(),C=$(y),L=V(u.imageUrl,o,C);if(w)await v.delete(L);else{const T=await v.get(L);if(T){T.noText?b.markNoText(u.element):T.blob&&b.applyTranslatedBlob(u.element,T.blob,T.blocksCount);return}}if(b.setLoading(u.element),h.length>0)r(h[0].page);else if(o.autoTranslate){const R=S.getPages()[u.index+1]??null;r(R)}const B=n(u.element),f=await chrome.runtime.sendMessage({type:"TRANSLATE_IMAGE_URL",imageUrl:u.imageUrl,pageUrl:window.location.href,imageBase64:B,forceRetranslate:w,glossary:Object.keys(y).length>0?y:void 0});if(!f.ok){b.setError(u.element,f.message),l(f);return}if(f.no_text)b.markNoText(u.element),await v.put({key:L,imageUrl:u.imageUrl,blob:null,noText:!0,blocksCount:0,model:f.model,style:f.style,uppercase:f.uppercase,createdAt:Date.now()});else{const T=t(f.image_base64,f.mime_type||"image/webp");b.applyTranslatedBlob(u.element,T,f.blocks_count),await v.put({key:L,imageUrl:u.imageUrl,blob:T,noText:!1,blocksCount:f.blocks_count,model:f.model,style:f.style,uppercase:f.uppercase,createdAt:Date.now()})}await Y()}async function be(a,h){const u=E(),w=$(u),y=[];for(let f=0;f<a.length;f++){const T=a[f];if(!T.page.element.isConnected)continue;const R=V(T.page.imageUrl,o,w);if(T.forceRetranslate)await v.delete(R);else{const D=await v.get(R);if(D){D.noText?b.markNoText(T.page.element):D.blob&&b.applyTranslatedBlob(T.page.element,D.blob,D.blocksCount);continue}}y.push({task:T,pageId:`p${f}`,cacheKey:R})}if(y.length===0)return;if(y.length===1){await X(y[0].task,h);return}for(const f of y)b.setLoading(f.task.page.element);h.length>0&&r(h[0].page);const C=new Map(y.map(f=>[f.pageId,f])),L=y.map(f=>({pageId:f.pageId,imageUrl:f.task.page.imageUrl,imageBase64:n(f.task.page.element),forceRetranslate:f.task.forceRetranslate})),B=await chrome.runtime.sendMessage({type:"TRANSLATE_BATCH_URLS",pageUrl:window.location.href,glossary:Object.keys(u).length>0?u:void 0,items:L});if(!B.ok){for(const f of y)b.setError(f.task.page.element,B.message);l(B);return}if(B.skipped_errors)for(const[f,T]of Object.entries(B.skipped_errors)){const R=C.get(f);R&&b.setError(R.task.page.element,T)}for(const f of B.pages){const T=C.get(f.page_id);if(!(!T||!T.task.page.element.isConnected))if(f.no_text)b.markNoText(T.task.page.element),await v.put({key:T.cacheKey,imageUrl:T.task.page.imageUrl,blob:null,noText:!0,blocksCount:0,model:B.model,style:B.style,uppercase:B.uppercase,createdAt:Date.now()});else{const R=t(f.image_base64,f.mime_type||"image/webp");b.applyTranslatedBlob(T.task.page.element,R,f.blocks_count),await v.put({key:T.cacheKey,imageUrl:T.task.page.imageUrl,blob:R,noText:!1,blocksCount:f.blocks_count,model:B.model,style:B.style,uppercase:B.uppercase,createdAt:Date.now()})}}await Y()}window.addEventListener("keydown",a=>{const h=a.target;if(!(h&&(h.tagName==="INPUT"||h.tagName==="TEXTAREA"||h.tagName==="SELECT"||h.isContentEditable))&&a.altKey&&!a.ctrlKey&&!a.metaKey){const u=a.key.toLowerCase();u==="t"?(a.preventDefault(),a.shiftKey?P(!o.autoTranslate):z()):u==="s"&&!a.shiftKey&&(a.preventDefault(),p.setExpanded(!1),N.start())}}),chrome.runtime.onMessage.addListener((a,h,u)=>{if(a.type==="SETTINGS_UPDATED"){const w=a.settings.sourceLang!==o.sourceLang;o=a.settings,w&&k.clear(),p.updateSettings(o),o.autoTranslate?d==="running"&&(x.resume(),i()):x.pauseAndClearPrefetch(),u({ok:!0})}else if(a.type==="GLOSSARY_UPDATED")g=a.store,u({ok:!0});else if(a.type==="ENGINE_STATE_BROADCAST")d=a.state,m=a.health,p.updateEngineState(d,m),(d==="stopped"||d==="stopping")&&(x.cancelAll(),k.clear()),u({ok:!0});else{if(a.type==="CLEAR_TAB_CACHE")return(async()=>(await v.clearAll(),k.clear(),b.restoreAllOriginals(),N.clearAllPins(),u({ok:!0})))(),!0;a.type==="TRANSLATE_VISIBLE_PAGE"?(z(),u({ok:!0})):a.type==="TRANSLATE_CHAPTER"?(K(),u({ok:!0})):a.type==="CANCEL_CHAPTER"?(x.pauseAndClearPrefetch(),u({ok:!0})):a.type==="START_MANUAL_CROP"&&(p.setExpanded(!1),N.start(),u({ok:!0}))}return!1}),(async()=>{try{o=await chrome.runtime.sendMessage({type:"GET_SETTINGS"}),p.updateSettings(o),g=await chrome.runtime.sendMessage({type:"GET_GLOSSARY_STORE"});const a=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});d=a.state,m=a.health,p.updateEngineState(d,m)}catch{}S.start()})()}})();
