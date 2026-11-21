var ye=Object.defineProperty;var Ee=(F,L,D)=>L in F?ye(F,L,{enumerable:!0,configurable:!0,writable:!0,value:D}):F[L]=D;var c=(F,L,D)=>Ee(F,typeof L!="symbol"?L+"":L,D);(function(){"use strict";const F=new Set(["manga","manhwa","manhua","series","title","titles","comic","comics","webtoon","webtoons","read","obra","obras","book","books","viewer","detail","en","pt","pt-br","es","fr","ko","zh","ja","jp"]),L=/^(?:chapter|chap|ch|cap|capitulo|ep|episode|episodio|vol|volume|issue|part|page|read|viewer|list)[-._]?\d.*$/i;function D(f){return decodeURIComponent(f||"").toLowerCase().replace(/[^a-z0-9\u00c0-\u024f\u3040-\u9fff\uac00-\ud7af]+/g,"-").replace(/^-+|-+$/g,"").slice(0,72)||"obra-padrao"}function G(f){return f.split(/[-_]+/).filter(Boolean).map(e=>e.charAt(0).toUpperCase()+e.slice(1)).join(" ")}function J(f){try{const e=new URL(f),t=e.hostname.replace(/^www\./,"").toLowerCase(),n=e.pathname.split("/").map(i=>i.trim()).filter(Boolean),s=e.searchParams.get("title_no")||e.searchParams.get("titleId")||e.searchParams.get("id");let r="";for(let i=0;i<n.length;i++){const l=n[i],b=l.toLowerCase();if(F.has(b)){const h=n[i+1];if(h&&!L.test(h)&&!F.has(h.toLowerCase())){r=h;break}continue}if(!L.test(l)){if(/^\d+$/.test(l)&&n[i+1]&&!L.test(n[i+1])){r=n[i+1];break}r=l;break}}!r&&s&&(r=`${t}-${s}`),r||(r=t||"obra-local"),r=r.replace(/[-_](?:chapter|chap|ch|cap|ep|episode)[-_]?\d+.*$/i,"");const o=D(r);return{slug:o,name:G(o)}}catch{return{slug:"obra-padrao",name:"Obra Padrão"}}}function ee(){return{series:{},urlOverrides:{}}}function te(f,e){const t=J(e),n=f.urlOverrides[t.slug]||t.slug,s=f.series[n];return s?{detectedSlug:t.slug,activeSlug:n,series:s}:{detectedSlug:t.slug,activeSlug:n,series:{slug:n,name:n===t.slug?t.name:G(n),terms:{},updatedAt:0}}}function $(f){if(!f)return"g0";const e=Object.entries(f).map(([s,r])=>[s.trim(),r.trim()]).filter(([s,r])=>s.length>0&&r.length>0).sort((s,r)=>s[0].localeCompare(r[0]));if(e.length===0)return"g0";const t=JSON.stringify(e);let n=2166136261;for(let s=0;s<t.length;s++)n^=t.charCodeAt(s),n=Math.imul(n,16777619);return`g${(n>>>0).toString(16)}`}const se="gemini-3.5-flash-lite",ae="v3-scanlation-turbo";function ne(f){return f==="ja"?"manga_ocr_v1":"vision_grid_v1"}const re=[{value:"auto",label:"Auto (Universal)"},{value:"ja",label:"Japonês"},{value:"ko",label:"Coreano"},{value:"zh",label:"Chinês"},{value:"en",label:"Inglês"},{value:"es",label:"Espanhol"},{value:"fr",label:"Francês"}],oe={apiKey:"",model:se,sourceLang:"auto",readingDirection:"auto",style:"scanlation",uppercase:!0,autoTranslate:!1,idleTimeoutSec:300,backendPort:8765,prefetchCount:4},ie="scanlator_vogels_cache_v1",M="translated_pages",le=1,V=250;function z(f,e,t="g0"){const n=ne(e.sourceLang);return`${ae}::${n}::${f}::${e.sourceLang}::${e.readingDirection}::${e.model}::${e.style}::${e.uppercase?"U1":"U0"}::${t}`}class ce{constructor(){c(this,"dbPromise",null)}openDb(){return this.dbPromise?this.dbPromise:(this.dbPromise=new Promise((e,t)=>{const n=indexedDB.open(ie,le);n.onupgradeneeded=()=>{const s=n.result;s.objectStoreNames.contains(M)||s.createObjectStore(M,{keyPath:"key"}).createIndex("createdAt","createdAt",{unique:!1})},n.onsuccess=()=>e(n.result),n.onerror=()=>t(n.error)}),this.dbPromise)}async get(e){try{const t=await this.openDb();return await new Promise((n,s)=>{const o=t.transaction(M,"readonly").objectStore(M).get(e);o.onsuccess=()=>n(o.result??null),o.onerror=()=>s(o.error)})}catch{return null}}async put(e){try{const t=await this.openDb();await new Promise((n,s)=>{const r=t.transaction(M,"readwrite");r.objectStore(M).put(e),r.oncomplete=()=>n(),r.onerror=()=>s(r.error)}),await this.pruneIfNeeded()}catch{}}async delete(e){try{const t=await this.openDb();await new Promise((n,s)=>{const r=t.transaction(M,"readwrite");r.objectStore(M).delete(e),r.oncomplete=()=>n(),r.onerror=()=>s(r.error)})}catch{}}async clearAll(){try{const e=await this.openDb();await new Promise((t,n)=>{const s=e.transaction(M,"readwrite");s.objectStore(M).clear(),s.oncomplete=()=>t(),s.onerror=()=>n(s.error)})}catch{}}async pruneIfNeeded(){try{const e=await this.openDb(),t=await new Promise((s,r)=>{const i=e.transaction(M,"readonly").objectStore(M).index("createdAt"),l=[],b=i.openCursor();b.onsuccess=()=>{const h=b.result;if(h){const k=h.value;l.push({key:k.key,createdAt:k.createdAt}),h.continue()}else s(l)},b.onerror=()=>r(b.error)});if(t.length<=V)return;const n=t.slice(0,t.length-V);await new Promise((s,r)=>{const o=e.transaction(M,"readwrite"),i=o.objectStore(M);for(const l of n)i.delete(l.key);o.oncomplete=()=>s(),o.onerror=()=>r(o.error)})}catch{}}}class de{constructor(e){c(this,"callbacks");c(this,"active",!1);c(this,"selectorHost",null);c(this,"pinsHost");c(this,"pinsShadow");c(this,"pinsLayer");c(this,"handleEsc",e=>{e.key==="Escape"&&(e.preventDefault(),e.stopPropagation(),this.cancel(),this.callbacks.onToast("Recorte manual cancelado."))});this.callbacks=e,this.pinsHost=document.createElement("div"),this.pinsHost.id="sv-ptbr-crop-pins-root",this.pinsHost.style.cssText="position:absolute;left:0;top:0;width:0;height:0;z-index:2147483642;pointer-events:none;",this.pinsShadow=this.pinsHost.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
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
    `;const s=document.createElement("div");s.className="sv-backdrop";const r=document.createElement("div");r.className="sv-hint",r.textContent="Arraste sobre o balão ou quadro para traduzir • Esc para cancelar";const o=document.createElement("div");o.className="sv-rect";const i=document.createElement("span");i.className="sv-dim",o.appendChild(i),t.append(n,s,o,r),document.documentElement.appendChild(e),this.selectorHost=e,window.addEventListener("keydown",this.handleEsc,!0);let l=!1,b=0,h=0,k={left:0,top:0,width:0,height:0};const v=(g,C)=>{const S=Math.max(0,Math.min(b,g)),y=Math.max(0,Math.min(h,C)),E=Math.min(window.innerWidth,Math.max(b,g)),p=Math.min(window.innerHeight,Math.max(h,C));k={left:S,top:y,width:E-S,height:p-y},o.style.left=`${S}px`,o.style.top=`${y}px`,o.style.width=`${k.width}px`,o.style.height=`${k.height}px`,i.textContent=`${Math.round(k.width)}×${Math.round(k.height)}px`};s.addEventListener("pointerdown",g=>{g.button===0&&(g.preventDefault(),l=!0,b=g.clientX,h=g.clientY,o.style.display="block",v(g.clientX,g.clientY),s.setPointerCapture(g.pointerId))}),s.addEventListener("pointermove",g=>{l&&(g.preventDefault(),v(g.clientX,g.clientY))}),s.addEventListener("pointerup",g=>{if(!l)return;l=!1,v(g.clientX,g.clientY);const C={...k};if(this.cancel(),C.width<24||C.height<24){this.callbacks.onToast("Área muito pequena — arraste um retângulo maior.");return}this.captureAndTranslateSelection(C)})}async captureAndTranslateSelection(e){await new Promise(R=>window.requestAnimationFrame(()=>window.requestAnimationFrame(()=>R())));const t=window.scrollX,n=window.scrollY,s=await chrome.runtime.sendMessage({type:"CAPTURE_VISIBLE_TAB"});if(!(s!=null&&s.ok)||!s.dataUrl){this.callbacks.onToast((s==null?void 0:s.message)||"Falha ao capturar tela da aba.");return}const r=await this.cropDataUrl(s.dataUrl,e);if(!r){this.callbacks.onToast("Não foi possível recortar a imagem capturada.");return}const o=document.createElement("div");o.className="sv-pin",o.style.left=`${Math.round(e.left+t)}px`,o.style.top=`${Math.round(e.top+n)}px`,o.style.width=`${Math.round(e.width)}px`,o.style.height=`${Math.round(e.height)}px`;const i=document.createElement("div");i.className="sv-pin-bar";const l=document.createElement("button");l.type="button",l.className="sv-pin-btn",l.textContent="Original",l.style.display="none";const b=document.createElement("button");b.type="button",b.className="sv-pin-btn",b.textContent="Copiar",b.style.display="none";const h=document.createElement("button");h.type="button",h.className="sv-pin-btn sv-pin-close",h.textContent="×",h.title="Fechar recorte",i.append(l,b,h);const k=document.createElement("div");k.className="sv-pin-loading",k.textContent="Traduzindo recorte...",o.append(i,k),this.pinsLayer.appendChild(o);let v=null;h.addEventListener("click",()=>{v&&URL.revokeObjectURL(v),o.remove()});const g=this.callbacks.getActiveGlossary(),C=await chrome.runtime.sendMessage({type:"TRANSLATE_IMAGE_URL",imageUrl:`sv-crop://${Date.now()}`,pageUrl:window.location.href,imageBase64:r.base64,forceRetranslate:!0,directOcr:!0,glossary:Object.keys(g).length>0?g:void 0});if(!C.ok){k.textContent=`Erro: ${C.message}`,k.style.color="#fecaca",this.callbacks.onError(C);return}if(C.no_text){k.textContent="Nenhum texto detectado neste recorte.",k.style.color="#cbd5e1";return}const S=atob(C.image_base64),y=new Uint8Array(S.length);for(let R=0;R<S.length;R++)y[R]=S.charCodeAt(R);const E=new Blob([y],{type:C.mime_type||"image/webp"});v=URL.createObjectURL(E);const p=document.createElement("img");p.className="sv-pin-img",p.src=v,k.remove(),o.appendChild(p);let N=!0;l.style.display="inline-block",l.addEventListener("click",()=>{N=!N,p.src=N?v:r.dataUrl,l.textContent=N?"Original":"Traduzido"});const B=(C.blocks??[]).map(R=>(R.translated_text||"").trim()).filter(Boolean).join(`
`);B&&(b.style.display="inline-block",b.addEventListener("click",async()=>{try{await navigator.clipboard.writeText(B),b.textContent="Copiado!",window.setTimeout(()=>{b.textContent="Copiar"},1600)}catch{this.callbacks.onToast("Falha ao copiar texto.")}}))}cropDataUrl(e,t){return new Promise(n=>{const s=new Image;s.onload=()=>{try{const r=s.naturalWidth/Math.max(1,window.innerWidth),o=s.naturalHeight/Math.max(1,window.innerHeight),i=Math.max(0,Math.round(t.left*r)),l=Math.max(0,Math.round(t.top*o)),b=Math.max(1,Math.min(s.naturalWidth-i,Math.round(t.width*r))),h=Math.max(1,Math.min(s.naturalHeight-l,Math.round(t.height*o))),k=document.createElement("canvas");k.width=b,k.height=h;const v=k.getContext("2d");if(!v){n(null);return}v.drawImage(s,i,l,b,h,0,0,b,h);const g=k.toDataURL("image/png"),C=g.indexOf(",");n({dataUrl:g,base64:C>=0?g.slice(C+1):g})}catch{n(null)}},s.onerror=()=>n(null),s.src=e})}}function W(f){return`
    <svg class="sv-hat-icon" width="${f}" height="${f}" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <ellipse cx="32" cy="41.2" rx="28.2" ry="10.4" fill="#F4C430" stroke="#1A1108" stroke-width="2.8"/>
      <ellipse cx="29.5" cy="40.2" rx="22.8" ry="6.8" fill="#FFE066" opacity="0.48"/>
      <ellipse cx="32" cy="40.6" rx="18.6" ry="5.1" fill="#B0760E" opacity="0.55"/>
      <path d="M14.5 38.2 C14.5 21 21.6 13.6 32 13.6 C42.4 13.6 49.5 21 49.5 38.2 C43.4 42.2 20.6 42.2 14.5 38.2 Z" fill="#F4C430" stroke="#1A1108" stroke-width="2.8" stroke-linejoin="round"/>
      <ellipse cx="25.5" cy="22.2" rx="8.6" ry="5.6" fill="#FFE066" opacity="0.62"/>
      <path d="M15.1 30.2 C22.5 34.0 41.5 34.0 48.9 30.2 L49.5 38.2 C43.4 42.2 20.6 42.2 14.5 38.2 Z" fill="#D62828" stroke="#1A1108" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M18.2 33.4 C24.5 36.0 34.5 36.2 38.8 35.1" stroke="#FF6B6B" stroke-width="1.8" stroke-linecap="round" opacity="0.72"/>
    </svg>
  `.trim()}class ue{constructor(e){c(this,"callbacks");c(this,"host");c(this,"shadow");c(this,"expanded",!1);c(this,"pillBtn");c(this,"pillDot");c(this,"pillText");c(this,"cardEl");c(this,"statusTextEl");c(this,"engineBtn");c(this,"telemetryEl");c(this,"langSelect");c(this,"translateNowBtn");c(this,"chapterBtn");c(this,"cropBtn");c(this,"autoCheckbox");c(this,"progressEl");c(this,"alertEl");c(this,"alertTitleEl");c(this,"alertDetailEl");c(this,"resumeBtn");c(this,"toastEl");c(this,"countdownTimer",null);c(this,"toastTimer",null);c(this,"countdownSec",0);this.callbacks=e,this.host=document.createElement("div"),this.host.id="sv-ptbr-floating-root",this.host.style.cssText="position:fixed;left:18px;bottom:18px;z-index:2147483645;display:flex;flex-direction:column;align-items:flex-start;max-width:calc(100vw - 36px);max-height:calc(100vh - 36px);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;",this.shadow=this.host.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
      * { box-sizing: border-box; }
      .sv-hat-icon {
        display: block;
        flex-shrink: 0;
        filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.45));
      }
      .sv-fab {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        padding: 7px 11px;
        border-radius: 999px;
        border: 1px solid rgba(244, 196, 48, 0.42);
        background: rgba(18, 16, 18, 0.90);
        color: #FFD166;
        font-size: 12px;
        font-weight: 700;
        cursor: pointer;
        box-shadow: 0 6px 22px rgba(0, 0, 0, 0.48), inset 0 1px 0 rgba(255, 209, 102, 0.14);
        backdrop-filter: blur(8px);
        opacity: 0.86;
        transition: opacity 0.16s ease, transform 0.16s ease, box-shadow 0.2s ease, border-color 0.2s ease;
      }
      .sv-fab:hover {
        opacity: 1;
        transform: translateY(-1px);
        border-color: rgba(244, 196, 48, 0.85);
      }
      .sv-fab.running {
        opacity: 0.94;
        border-color: rgba(244, 196, 48, 0.78);
        box-shadow: 0 0 0 1px rgba(244, 196, 48, 0.32), 0 0 18px rgba(244, 196, 48, 0.30), 0 6px 20px rgba(0, 0, 0, 0.50);
      }
      .sv-fab.alert {
        opacity: 1;
        border-color: rgba(224, 49, 49, 0.85);
        box-shadow: 0 0 14px rgba(201, 42, 42, 0.45), 0 6px 20px rgba(0, 0, 0, 0.50);
      }
      .sv-fab-counter {
        font-size: 11.5px;
        font-weight: 700;
        color: #FFD166;
        letter-spacing: 0.02em;
      }
      .sv-fab-counter:empty { display: none; }
      .sv-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: #7c6f64; display: inline-block; flex-shrink: 0;
      }
      .sv-dot.running { background: #22c55e; box-shadow: 0 0 8px rgba(34, 197, 94, 0.82); }
      .sv-dot.starting { background: #F4C430; box-shadow: 0 0 8px rgba(244, 196, 48, 0.75); }
      .sv-dot.stopping { background: #F4C430; }
      .sv-dot.stopped { background: #7c6f64; }
      .sv-dot.alert { background: #E03131; box-shadow: 0 0 8px rgba(224, 49, 49, 0.88); }

      .sv-toast {
        display: none;
        margin-bottom: 8px;
        padding: 6px 10px;
        border-radius: 9px;
        background: rgba(18, 16, 18, 0.95);
        border: 1px solid rgba(244, 196, 48, 0.48);
        border-left: 3px solid #C92A2A;
        color: #FFE8A3;
        font-size: 11.5px;
        font-weight: 600;
        text-align: left;
        max-width: min(292px, calc(100vw - 36px));
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.48);
      }
      .sv-toast.visible { display: block; }

      .sv-card {
        display: none;
        width: min(290px, calc(100vw - 36px));
        max-height: calc(100vh - 46px);
        overflow-y: auto;
        padding: 12px;
        border-radius: 14px;
        background: linear-gradient(180deg, rgba(27, 22, 24, 0.97) 0%, rgba(18, 16, 18, 0.98) 100%);
        border: 1px solid rgba(244, 196, 48, 0.35);
        border-top: 3px solid #C92A2A;
        color: #F4EDE4;
        box-shadow: 0 14px 36px rgba(0, 0, 0, 0.62), 0 0 0 1px rgba(244, 196, 48, 0.12);
        backdrop-filter: blur(10px);
        font-size: 12px;
        transform-origin: bottom left;
      }
      .sv-card.open { display: block; }
      .sv-header {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px;
        margin-bottom: 10px;
        padding-bottom: 8px;
        border-bottom: 1px solid rgba(244, 196, 48, 0.18);
      }
      .sv-brand {
        display: inline-flex;
        align-items: center;
        gap: 7px;
        min-width: 0;
      }
      .sv-title {
        font-weight: 800;
        font-size: 13px;
        color: #FFD166;
        letter-spacing: 0.01em;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .sv-min-btn {
        background: transparent; border: 1px solid transparent; color: #B8A99A; cursor: pointer;
        font-size: 14px; line-height: 1; padding: 3px 7px; border-radius: 6px;
        transition: background 0.14s ease, color 0.14s ease, border-color 0.14s ease;
      }
      .sv-min-btn:hover {
        background: rgba(244, 196, 48, 0.14);
        border-color: rgba(244, 196, 48, 0.35);
        color: #FFD166;
      }
      .sv-row {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; margin-bottom: 8px;
      }
      .sv-status { font-size: 11.5px; color: #E7DFD5; font-weight: 600; }
      .sv-btn {
        border: 1px solid transparent; border-radius: 8px; padding: 7px 10px;
        font-size: 11.5px; font-weight: 700; cursor: pointer;
        transition: background-color 0.14s ease, border-color 0.14s ease, color 0.14s ease, transform 0.12s ease;
      }
      .sv-btn:active:not(:disabled) { transform: translateY(1px); }
      .sv-btn:disabled { opacity: 0.48; cursor: not-allowed; }
      .sv-btn-power-on { background: #16a34a; color: #ffffff; }
      .sv-btn-power-on:hover:not(:disabled) { background: #15803d; }
      .sv-btn-power-off { background: #b91c1c; color: #ffffff; }
      .sv-btn-power-off:hover:not(:disabled) { background: #dc2626; }
      .sv-actions-grid {
        display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-bottom: 8px;
      }
      .sv-btn-primary {
        background: #C92A2A;
        color: #ffffff;
      }
      .sv-btn-primary:hover:not(:disabled) { background: #E03131; }
      .sv-btn-chapter {
        background: #F4C430;
        color: #1A120B;
        font-weight: 800;
      }
      .sv-btn-chapter:hover:not(:disabled) { background: #FFD166; }
      .sv-btn-crop {
        width: 100%;
        margin-bottom: 8px;
        background: #121012;
        border: 1px solid #F4C430;
        color: #F4C430;
      }
      .sv-btn-crop:hover:not(:disabled) {
        background: rgba(244, 196, 48, 0.16);
        color: #FFD166;
      }
      .sv-btn-cancel {
        background: #9B1C1C;
        border: 1px solid rgba(255, 209, 102, 0.45);
        color: #ffffff;
      }
      .sv-btn-cancel:hover:not(:disabled) { background: #C92A2A; }
      .sv-telemetry {
        font-size: 11px;
        color: #D6C9B8;
        background: rgba(18, 16, 18, 0.78);
        border: 1px solid rgba(244, 196, 48, 0.18);
        border-radius: 8px;
        padding: 6px 8px;
        margin-bottom: 8px;
        line-height: 1.45;
      }
      .sv-lang-row {
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px; margin-bottom: 8px; font-size: 11.5px; color: #E7DFD5;
      }
      .sv-select {
        background: #121012;
        color: #F4EDE4;
        border: 1px solid rgba(244, 196, 48, 0.34);
        border-radius: 7px;
        padding: 4px 7px;
        font-size: 11.5px;
        outline: none;
        transition: border-color 0.14s ease, box-shadow 0.14s ease;
      }
      .sv-select:focus {
        border-color: #F4C430;
        box-shadow: 0 0 0 2px rgba(244, 196, 48, 0.24);
      }
      .sv-toggle {
        display: flex; align-items: center; justify-content: space-between;
        padding: 6px 8px;
        border-radius: 8px;
        background: rgba(18, 16, 18, 0.62);
        border: 1px solid rgba(244, 196, 48, 0.16);
        margin-bottom: 7px;
        cursor: pointer;
        color: #F4EDE4;
      }
      .sv-toggle input[type="checkbox"] {
        accent-color: #F4C430;
        width: 14px;
        height: 14px;
        cursor: pointer;
      }
      .sv-meta-row {
        display: flex; align-items: center; justify-content: space-between;
        font-size: 10.5px; color: #B8A99A;
      }
      .sv-progress {
        color: #FFD166;
        font-weight: 700;
      }
      .sv-alert {
        display: none;
        margin-top: 8px; padding: 8px; border-radius: 8px;
        background: rgba(244, 196, 48, 0.14);
        border: 1px solid rgba(244, 196, 48, 0.48);
        color: #FFE8A3;
        font-size: 11px; line-height: 1.4;
      }
      .sv-alert.visible { display: block; }
      .sv-alert.error {
        background: rgba(201, 42, 42, 0.20);
        border-color: rgba(224, 49, 49, 0.56);
        color: #FECACA;
      }
      .sv-alert-title { font-weight: 700; margin-bottom: 3px; color: #FFD166; }
      .sv-alert.error .sv-alert-title { color: #FCA5A5; }
      .sv-btn-resume {
        margin-top: 6px; width: 100%;
        background: #F4C430; color: #1A120B; font-weight: 800;
      }
      .sv-btn-resume:hover:not(:disabled) { background: #FFD166; }
    `,this.toastEl=document.createElement("div"),this.toastEl.className="sv-toast",this.pillBtn=document.createElement("button"),this.pillBtn.type="button",this.pillBtn.className="sv-fab",this.pillBtn.title="Scanlator Vogels PT-BR (Alt+T página | Alt+S recorte | Alt+Shift+T auto)",this.pillDot=document.createElement("span"),this.pillDot.className="sv-dot stopped";const n=document.createElement("span");n.innerHTML=W(24),this.pillText=document.createElement("span"),this.pillText.className="sv-fab-counter",this.pillText.textContent="",this.pillBtn.append(this.pillDot,n,this.pillText),this.cardEl=document.createElement("div"),this.cardEl.className="sv-card",this.cardEl.innerHTML=`
      <div class="sv-header">
        <span class="sv-brand">
          ${W(22)}
          <span class="sv-title">Scanlator Vogels PT-BR</span>
        </span>
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
        <span>Auto Translate (lote automático)</span>
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
    `;const s=this.cardEl.querySelector(".sv-min-btn");this.statusTextEl=this.cardEl.querySelector(".sv-status"),this.engineBtn=this.cardEl.querySelector(".sv-row .sv-btn"),this.telemetryEl=this.cardEl.querySelector(".sv-telemetry"),this.langSelect=this.cardEl.querySelector("#svLang"),this.translateNowBtn=this.cardEl.querySelector("#svPageBtn"),this.chapterBtn=this.cardEl.querySelector("#svChapterBtn"),this.cropBtn=this.cardEl.querySelector("#svCropBtn"),this.autoCheckbox=this.cardEl.querySelector("input[type='checkbox']"),this.progressEl=this.cardEl.querySelector(".sv-progress"),this.alertEl=this.cardEl.querySelector(".sv-alert"),this.alertTitleEl=this.cardEl.querySelector(".sv-alert-title"),this.alertDetailEl=this.cardEl.querySelector(".sv-alert-detail"),this.resumeBtn=this.cardEl.querySelector(".sv-btn-resume");for(const r of re){const o=document.createElement("option");o.value=r.value,o.textContent=r.label,this.langSelect.appendChild(o)}this.pillBtn.addEventListener("click",()=>this.setExpanded(!0)),s.addEventListener("click",()=>this.setExpanded(!1)),this.engineBtn.addEventListener("click",()=>this.callbacks.onToggleEngine()),this.translateNowBtn.addEventListener("click",()=>this.callbacks.onTranslateVisible()),this.chapterBtn.addEventListener("click",()=>this.callbacks.onToggleChapter()),this.cropBtn.addEventListener("click",()=>this.callbacks.onManualCrop()),this.autoCheckbox.addEventListener("change",()=>this.callbacks.onToggleAutoTranslate(this.autoCheckbox.checked)),this.langSelect.addEventListener("change",()=>this.callbacks.onChangeSourceLang(this.langSelect.value)),this.resumeBtn.addEventListener("click",()=>this.callbacks.onResumeCircuit()),this.shadow.append(t,this.toastEl,this.pillBtn,this.cardEl),document.documentElement.appendChild(this.host)}setExpanded(e){this.expanded=e,this.cardEl.classList.toggle("open",e),this.pillBtn.style.display=e?"none":"inline-flex"}showToast(e,t=2200){this.toastTimer!==null&&window.clearTimeout(this.toastTimer),this.toastEl.textContent=e,this.toastEl.classList.add("visible"),this.toastTimer=window.setTimeout(()=>{this.toastEl.classList.remove("visible"),this.toastTimer=null},t)}updateEngineState(e,t){var n,s;if(this.pillDot.className=`sv-dot ${e}`,this.pillBtn.classList.toggle("running",e==="running"),this.pillBtn.classList.remove("alert"),e==="running"&&t){this.statusTextEl.textContent="Motor: Ativo",this.engineBtn.textContent="Desligar Motor",this.engineBtn.className="sv-btn sv-btn-power-off",this.engineBtn.disabled=!1,this.translateNowBtn.disabled=!1,this.chapterBtn.disabled=!1,this.cropBtn.disabled=!1;const r=t.warming_up?"Aquecendo GPU...":`VRAM: ${Math.round(t.torch_vram_allocated_mb)} MB Torch`,o=((n=t.quota)==null?void 0:n.requests_today)??0,i=Math.ceil(t.idle_remaining_sec/60);this.telemetryEl.textContent=`${r} • Cota hoje: ${o} req(s) • Auto-off: ~${i}m`,((s=t.circuit_breaker)==null?void 0:s.state)==="OPEN"&&(this.pillDot.className="sv-dot alert",this.pillBtn.classList.remove("running"),this.pillBtn.classList.add("alert"))}else e==="starting"?(this.statusTextEl.textContent="Motor: Ligando...",this.engineBtn.textContent="Ligando...",this.engineBtn.disabled=!0,this.translateNowBtn.disabled=!0,this.chapterBtn.disabled=!0,this.cropBtn.disabled=!0,this.telemetryEl.textContent="Iniciando servidor local em 127.0.0.1..."):e==="stopping"?(this.statusTextEl.textContent="Motor: Desligando...",this.engineBtn.textContent="Desligando...",this.engineBtn.disabled=!0,this.translateNowBtn.disabled=!0,this.chapterBtn.disabled=!0,this.cropBtn.disabled=!0,this.telemetryEl.textContent="Liberando 100% da VRAM..."):(this.statusTextEl.textContent="Motor: Desligado",this.engineBtn.textContent="Ligar Motor",this.engineBtn.className="sv-btn sv-btn-power-on",this.engineBtn.disabled=!1,this.translateNowBtn.disabled=!1,this.chapterBtn.disabled=!1,this.cropBtn.disabled=!1,this.telemetryEl.textContent="0% CPU • 0 MB RAM • 0 MB VRAM")}updateSettings(e){this.autoCheckbox.checked=e.autoTranslate,this.langSelect.value=e.sourceLang}updateProgress(e,t,n,s,r){const o=s||n>0?` • fila: ${n+(s?1:0)}`:"";this.progressEl.textContent=`${e}/${t} págs${o}`,this.pillText.textContent=r||s?`${e}/${t}`:"",r?(this.chapterBtn.textContent="Pausar / Cancelar",this.chapterBtn.className="sv-btn sv-btn-cancel"):(this.chapterBtn.textContent="Traduzir Capítulo",this.chapterBtn.className="sv-btn sv-btn-chapter")}showAlert(e){if(this.countdownTimer!==null&&(window.clearInterval(this.countdownTimer),this.countdownTimer=null),this.alertEl.className=e.kind==="cors"||e.kind==="error"?"sv-alert error visible":"sv-alert visible",this.alertTitleEl.textContent=e.title,this.resumeBtn.style.display=e.showResumeButton?"block":"none",e.retryAfterSec&&e.retryAfterSec>0){this.countdownSec=Math.ceil(e.retryAfterSec);const t=()=>{this.alertDetailEl.textContent=`${e.detail} (retomada automática em ${this.countdownSec}s)`};t(),this.countdownTimer=window.setInterval(()=>{this.countdownSec=Math.max(0,this.countdownSec-1),this.countdownSec<=0?(this.countdownTimer!==null&&window.clearInterval(this.countdownTimer),this.countdownTimer=null,this.alertDetailEl.textContent=`${e.detail} — pronto para testar retomada.`):t()},1e3)}else this.alertDetailEl.textContent=e.detail;e.kind==="circuit_open"&&(this.pillDot.className="sv-dot alert",this.pillBtn.classList.remove("running"),this.pillBtn.classList.add("alert"),this.setExpanded(!0))}clearAlert(){this.countdownTimer!==null&&(window.clearInterval(this.countdownTimer),this.countdownTimer=null),this.alertEl.classList.remove("visible")}}const K=["data-src","data-original","data-lazy-src","data-url","data-cfsrc","data-actualsrc","data-image","data-hi-res-src"],Y=["data-srcset","data-lazy-srcset","srcset"],he=/\b(logo|avatar|icon|banner|thumb|thumbnail|cover|emoji|badge|button|sponsor|ads?|promo|gravatar|spinner|loading|placeholder)\b/i;function H(f){const e=f.split(",").map(t=>t.trim()).filter(Boolean).map(t=>{const n=t.split(/\s+/),s=n[0],r=n[1]||"1x";let o=1;return r.endsWith("w")?o=parseFloat(r)||1:r.endsWith("x")&&(o=(parseFloat(r)||1)*1e3),{url:s,weight:o}}).filter(t=>!!t.url);return e.length===0?null:(e.sort((t,n)=>n.weight-t.weight),e[0].url)}function I(f){if(!f)return null;const e=f.trim();if(!e||e.startsWith("about:")||e.startsWith("javascript:")||e.startsWith("data:image/svg")||e.startsWith("data:image/gif"))return null;try{return new URL(e,window.location.href).href}catch{return null}}class pe{constructor(){c(this,"name","GenericReaderAdapter")}matches(e,t){return!0}resolveImageUrl(e){const t=e.dataset.svOrigUrl;if(t)return t;const n=e.getAttribute("src")||"";if(!n||n.startsWith("data:image/gif")||n.startsWith("data:image/svg")||/loading|placeholder|blank|spacer|pixel/i.test(n)){for(const i of K){const l=I(e.getAttribute(i));if(l)return l}for(const i of Y){const l=e.getAttribute(i);if(l){const b=I(H(l));if(b)return b}}}const r=e.parentElement;if(r&&r.tagName==="PICTURE"){const i=Array.from(r.querySelectorAll("source"));for(const l of i){const b=l.getAttribute("srcset")||l.getAttribute("data-srcset");if(b){const h=I(H(b));if(h)return h}}}for(const i of Y){const l=e.getAttribute(i);if(l){const b=I(H(l));if(b)return b}}const o=I(e.currentSrc||e.src);if(o&&!o.startsWith("blob:"))return o;for(const i of K){const l=I(e.getAttribute(i));if(l)return l}return o}isComicPageCandidate(e){if(!e.isConnected||e.dataset.svIgnore==="1"||!this.resolveImageUrl(e))return!1;const n=window.getComputedStyle(e);if(n.display==="none"||n.visibility==="hidden"||n.opacity==="0")return!1;const s=e.getBoundingClientRect(),r=e.naturalWidth||0,o=e.naturalHeight||0,i=Math.max(r,Math.round(s.width)),l=Math.max(o,Math.round(s.height));if(i<260||l<320)return!1;const b=i/Math.max(1,l);if(b>2.25||b<.06)return!1;const h=`${e.className} ${e.id} ${e.alt}`;return!(he.test(h)&&i<620||e.closest("a")&&s.width<420&&s.height<520)}findCandidateImages(e){const n=Array.from(e.querySelectorAll("img")).filter(s=>this.isComicPageCandidate(s));return n.sort((s,r)=>{const o=s.getBoundingClientRect(),i=r.getBoundingClientRect(),l=o.top-i.top;return Math.abs(l)>24?l:o.left-i.left}),n}}const ge=[];function be(f=document){const e=new URL(window.location.href);for(const t of ge)if(t.matches(e,f))return t;return new pe}class me{constructor(e,t){c(this,"adapter");c(this,"callbacks");c(this,"mutationObserver",null);c(this,"intersectionObserver",null);c(this,"observedElements",new Set);c(this,"visibilityRatios",new Map);c(this,"pages",[]);c(this,"scanTimer",null);c(this,"lastVisibleEl",null);c(this,"handleGlobalTrigger",()=>{this.scheduleScan()});c(this,"handleScroll",()=>{this.emitVisibleIfChanged()});this.callbacks=e,this.adapter=t??be(document)}getAdapterName(){return this.adapter.name}getPages(){return this.pages}getVisiblePage(){if(this.pages.length===0)return null;let e=null,t=-1;const n=window.innerHeight||900;for(const s of this.pages){const r=s.element.getBoundingClientRect(),o=Math.max(0,r.top),i=Math.min(n,r.bottom),l=Math.max(0,i-o),b=this.visibilityRatios.get(s.element)??0,h=l+b*250;l>40&&h>t&&(t=h,e=s.element)}return e?this.pages.find(s=>s.element===e)??this.pages[0]:this.pages[0]}start(){this.intersectionObserver=new IntersectionObserver(e=>{for(const t of e){const n=t.target;this.visibilityRatios.set(n,t.isIntersecting?t.intersectionRatio:0)}this.emitVisibleIfChanged()},{threshold:[0,.15,.35,.6,.85]}),this.mutationObserver=new MutationObserver(()=>{this.scheduleScan()}),this.mutationObserver.observe(document.documentElement,{childList:!0,subtree:!0,attributes:!0,attributeFilter:["src","srcset","data-src","data-original","data-lazy-src","class","style"]}),window.addEventListener("load",this.handleGlobalTrigger,{passive:!0}),window.addEventListener("scroll",this.handleScroll,{passive:!0}),window.addEventListener("resize",this.handleGlobalTrigger,{passive:!0}),this.scanNow()}stop(){var e,t;this.scanTimer!==null&&(window.clearTimeout(this.scanTimer),this.scanTimer=null),(e=this.mutationObserver)==null||e.disconnect(),(t=this.intersectionObserver)==null||t.disconnect(),this.observedElements.clear(),this.visibilityRatios.clear(),window.removeEventListener("load",this.handleGlobalTrigger),window.removeEventListener("scroll",this.handleScroll),window.removeEventListener("resize",this.handleGlobalTrigger)}scheduleScan(e=250){this.scanTimer===null&&(this.scanTimer=window.setTimeout(()=>{this.scanTimer=null,this.scanNow()},e))}scanNow(){var s,r;const e=this.adapter.findCandidateImages(document),t=[];for(let o=0;o<e.length;o++){const i=e[o],l=this.adapter.resolveImageUrl(i);l&&(i.dataset.svOrigUrl||(i.dataset.svOrigUrl=l),t.push({element:i,imageUrl:l,index:o}),this.observedElements.has(i)||(this.observedElements.add(i),(s=this.intersectionObserver)==null||s.observe(i),i.addEventListener("load",this.handleGlobalTrigger,{passive:!0})))}const n=new Set(e);for(const o of Array.from(this.observedElements))n.has(o)||((r=this.intersectionObserver)==null||r.unobserve(o),this.observedElements.delete(o),this.visibilityRatios.delete(o));this.pages=t,this.callbacks.onPagesChanged(this.pages),this.emitVisibleIfChanged()}emitVisibleIfChanged(){const e=this.getVisiblePage(),t=(e==null?void 0:e.element)??null;t!==this.lastVisibleEl&&(this.lastVisibleEl=t,this.callbacks.onVisiblePageChanged(e,this.pages))}}class fe{constructor(e){c(this,"callbacks");c(this,"records",new Map);c(this,"hostEl");c(this,"shadow");c(this,"layerEl");c(this,"rafId",null);c(this,"scheduleReposition",()=>{this.rafId===null&&(this.rafId=window.requestAnimationFrame(()=>{this.rafId=null;for(const e of this.records.values())e.hoverBar.classList.contains("visible")&&this.positionBar(e)}))});this.callbacks=e,this.hostEl=document.createElement("div"),this.hostEl.id="sv-ptbr-overlay-root",this.hostEl.style.cssText="position:fixed;inset:0;pointer-events:none;z-index:2147483640;overflow:hidden;",this.shadow=this.hostEl.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
      .sv-bar {
        position: fixed;
        display: none;
        align-items: center;
        gap: 6px;
        padding: 4px 7px;
        border-radius: 999px;
        background: rgba(18, 16, 18, 0.92);
        border: 1px solid rgba(244, 196, 48, 0.42);
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.50);
        backdrop-filter: blur(6px);
        pointer-events: auto;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        font-size: 11px;
        color: #F4EDE4;
        transition: opacity 0.14s ease;
        user-select: none;
      }
      .sv-bar.visible { display: flex; }
      .sv-pill {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        padding: 2px 7px;
        border-radius: 999px;
        font-weight: 700;
        letter-spacing: 0.02em;
      }
      .sv-pill.loading {
        background: rgba(244, 196, 48, 0.22);
        color: #FFD166;
      }
      .sv-pill.done {
        background: rgba(244, 196, 48, 0.18);
        border: 1px solid rgba(244, 196, 48, 0.38);
        color: #FFD166;
      }
      .sv-pill.orig {
        background: rgba(231, 223, 213, 0.14);
        color: #E7DFD5;
      }
      .sv-pill.notext {
        background: rgba(184, 169, 154, 0.16);
        color: #D6C9B8;
      }
      .sv-pill.error {
        background: rgba(201, 42, 42, 0.30);
        color: #FCA5A5;
      }
      .sv-btn {
        appearance: none;
        border: 1px solid rgba(244, 196, 48, 0.35);
        background: #1B1618;
        color: #F4EDE4;
        border-radius: 999px;
        padding: 3px 9px;
        font-size: 11px;
        font-weight: 700;
        cursor: pointer;
        line-height: 1.2;
        transition: background-color 0.14s ease, border-color 0.14s ease, color 0.14s ease;
      }
      .sv-btn:hover {
        background: rgba(201, 42, 42, 0.30);
        border-color: #F4C430;
        color: #FFD166;
      }
    `,this.layerEl=document.createElement("div"),this.shadow.append(t,this.layerEl),document.documentElement.appendChild(this.hostEl),window.addEventListener("scroll",this.scheduleReposition,{passive:!0}),window.addEventListener("resize",this.scheduleReposition,{passive:!0})}ensureRecord(e){const t=this.records.get(e);if(t)return t;const n=document.createElement("div");n.className="sv-bar";const s=document.createElement("span");s.className="sv-pill",s.textContent="SV";const r=document.createElement("button");r.type="button",r.className="sv-btn",r.textContent="Ver Original";const o=document.createElement("button");o.type="button",o.className="sv-btn",o.textContent="Retry",o.title="Refaz somente a traducao (reaproveita OCR/inpainting do cache)",n.append(s,r,o),this.layerEl.appendChild(n);const i={img:e,origSrc:e.dataset.svOrigUrl||e.currentSrc||e.src,origSrcset:e.getAttribute("srcset")||"",translatedUrl:null,showingTranslated:!1,state:"idle",errorMessage:"",blocksCount:0,hoverBar:n,toggleBtn:r,retryBtn:o,statusPill:s,isHovered:!1,barHovered:!1};return r.addEventListener("click",l=>{l.preventDefault(),l.stopPropagation(),i.state==="translated"&&i.translatedUrl?this.toggleOriginal(e):this.callbacks.onManualTranslateRequested(e)}),o.addEventListener("click",l=>{l.preventDefault(),l.stopPropagation(),this.callbacks.onRetryRequested(e)}),e.addEventListener("mouseenter",()=>{i.isHovered=!0,this.updateBarVisibility(i)}),e.addEventListener("mouseleave",()=>{i.isHovered=!1,window.setTimeout(()=>this.updateBarVisibility(i),120)}),n.addEventListener("mouseenter",()=>{i.barHovered=!0,this.updateBarVisibility(i)}),n.addEventListener("mouseleave",()=>{i.barHovered=!1,this.updateBarVisibility(i)}),this.records.set(e,i),i}getState(e){var t;return((t=this.records.get(e))==null?void 0:t.state)??"idle"}getTranslatedCount(){let e=0;for(const t of this.records.values())t.img.isConnected&&(t.state==="translated"||t.state==="no_text")&&(e+=1);return e}setLoading(e){const t=this.ensureRecord(e);t.state="loading",t.errorMessage="",this.syncRecordUi(t)}applyTranslatedBlob(e,t,n){const s=this.ensureRecord(e);s.translatedUrl&&URL.revokeObjectURL(s.translatedUrl);const r=URL.createObjectURL(t);s.translatedUrl=r,s.blocksCount=n,s.state="translated",s.showingTranslated=!0,s.errorMessage="",e.dataset.svOrigUrl||(e.dataset.svOrigUrl=s.origSrc),s.origSrcset&&e.removeAttribute("srcset"),e.src=r,this.syncRecordUi(s)}markNoText(e){const t=this.ensureRecord(e);t.state="no_text",t.blocksCount=0,t.errorMessage="",this.syncRecordUi(t)}setError(e,t){const n=this.ensureRecord(e);n.state!=="translated"&&(n.state="error"),n.errorMessage=t,this.syncRecordUi(n)}toggleOriginal(e){const t=this.records.get(e);!t||!t.translatedUrl||(t.showingTranslated?(e.src=t.origSrc,t.origSrcset&&e.setAttribute("srcset",t.origSrcset),t.showingTranslated=!1):(t.origSrcset&&e.removeAttribute("srcset"),e.src=t.translatedUrl,t.showingTranslated=!0),this.syncRecordUi(t))}restoreAllOriginals(){for(const e of this.records.values())e.showingTranslated&&(e.img.src=e.origSrc,e.origSrcset&&e.img.setAttribute("srcset",e.origSrcset),e.showingTranslated=!1),e.translatedUrl&&(URL.revokeObjectURL(e.translatedUrl),e.translatedUrl=null),e.state="idle",this.syncRecordUi(e)}syncRecordUi(e){if(e.state==="loading")e.statusPill.className="sv-pill loading",e.statusPill.textContent="Traduzindo...",e.toggleBtn.style.display="none",e.retryBtn.style.display="none";else if(e.state==="translated")e.statusPill.className=e.showingTranslated?"sv-pill done":"sv-pill orig",e.statusPill.textContent=e.showingTranslated?`PT-BR (${e.blocksCount})`:"Original",e.toggleBtn.style.display="inline-block",e.toggleBtn.textContent=e.showingTranslated?"Original":"Traduzido",e.retryBtn.style.display="inline-block";else if(e.state==="no_text")e.statusPill.className="sv-pill notext",e.statusPill.textContent="Sem texto",e.toggleBtn.style.display="none",e.retryBtn.style.display="inline-block";else if(e.state==="error")e.statusPill.className="sv-pill error",e.statusPill.textContent="Erro",e.statusPill.title=e.errorMessage,e.toggleBtn.style.display="none",e.retryBtn.style.display="inline-block";else{e.hoverBar.classList.remove("visible");return}this.updateBarVisibility(e)}updateBarVisibility(e){if(!e.img.isConnected||e.state==="idle"){e.hoverBar.classList.remove("visible");return}if(!(e.state==="loading"||e.state==="error"||e.isHovered||e.barHovered)){e.hoverBar.classList.remove("visible");return}this.positionBar(e)}positionBar(e){const t=e.img.getBoundingClientRect(),n=window.innerWidth,s=window.innerHeight;if(t.bottom<0||t.top>s||t.right<0||t.left>n||t.width<120){e.hoverBar.classList.remove("visible");return}e.hoverBar.classList.add("visible");const r=Math.max(8,Math.min(s-38,t.top+10)),o=Math.max(8,n-Math.min(n-8,t.right-10));e.hoverBar.style.top=`${Math.round(r)}px`,e.hoverBar.style.right=`${Math.round(o)}px`}}class ve{constructor(e,t,n,s=3){c(this,"singleWorkerFn");c(this,"batchWorkerFn");c(this,"onQueueStateChange");c(this,"maxBatchSize");c(this,"queue",[]);c(this,"activeUrls",new Set);c(this,"running",!1);c(this,"paused",!1);c(this,"chapterActive",!1);c(this,"generation",0);this.singleWorkerFn=e,this.batchWorkerFn=t,this.onQueueStateChange=n,this.maxBatchSize=Math.max(1,Math.min(4,s))}setMaxBatchSize(e){this.maxBatchSize=Math.max(1,Math.min(4,Math.round(e)))}enqueue(e,t,n=!1){if(this.paused&&t<90||this.activeUrls.has(e.imageUrl)&&!n)return;const s=this.queue.find(r=>r.page.imageUrl===e.imageUrl);s?(s.priority=Math.max(s.priority,t),s.forceRetranslate=s.forceRetranslate||n,s.page=e):this.queue.push({page:e,priority:t,forceRetranslate:n}),this.sortQueue(),this.emitState(),this.pump()}enqueueChapter(e,t){this.paused=!1,this.chapterActive=!0;const n=(t==null?void 0:t.imageUrl)??null;for(const s of e){if(this.activeUrls.has(s.imageUrl))continue;const r=n&&s.imageUrl===n?100:30,o=this.queue.find(i=>i.page.imageUrl===s.imageUrl);o?(o.priority=Math.max(o.priority,r),o.page=s):this.queue.push({page:s,priority:r,forceRetranslate:!1})}this.sortQueue(),this.emitState(),this.pump()}pauseAndClearPrefetch(){this.paused=!0,this.chapterActive=!1,this.queue=[],this.emitState()}cancelAll(){this.paused=!0,this.chapterActive=!1,this.generation+=1,this.queue=[],this.activeUrls.clear(),this.emitState()}resume(){this.paused=!1,this.emitState(),this.pump()}isPaused(){return this.paused}isChapterActive(){return this.chapterActive&&(this.queue.length>0||this.activeUrls.size>0)}getPendingCount(){return this.queue.length+this.activeUrls.size}sortQueue(){this.queue.sort((e,t)=>t.priority-e.priority||e.page.index-t.page.index)}emitState(){const e=this.activeUrls.size;e===0&&this.queue.length===0&&(this.chapterActive=!1),this.onQueueStateChange(this.queue.length,e,this.chapterActive)}async pump(){if(this.running)return;this.running=!0;const e=this.generation;try{for(;this.queue.length>0&&e===this.generation;){const t=this.queue[0];if(this.paused&&t.priority<90)break;if(t.priority>=50||t.forceRetranslate){const r=this.queue.shift(),o=this.paused?[]:this.queue.slice(0,2);this.activeUrls.add(r.page.imageUrl),this.emitState();try{await this.singleWorkerFn(r,o)}finally{e===this.generation&&(this.activeUrls.delete(r.page.imageUrl),this.emitState())}continue}const n=[];for(;n.length<this.maxBatchSize&&this.queue.length>0&&this.queue[0].priority<50&&!this.queue[0].forceRetranslate;){const r=this.queue.shift();n.push(r),this.activeUrls.add(r.page.imageUrl)}const s=this.paused?[]:this.queue.slice(0,2);this.emitState();try{n.length>=2?await this.batchWorkerFn(n,s):n.length===1&&await this.singleWorkerFn(n[0],s)}finally{if(e===this.generation){for(const r of n)this.activeUrls.delete(r.page.imageUrl);this.emitState()}}}}finally{this.running=!1,this.emitState()}}}if(!window.__svPtBrLoaded){let f=function(){return te(k,window.location.href).series.terms},e=function(){return $(f())},t=function(a,u="image/webp"){const d=atob(a),x=d.length,w=new Uint8Array(x);for(let A=0;A<x;A++)w[A]=d.charCodeAt(A);return new Blob([w],{type:u})},n=function(a){const u=a.naturalWidth||0,d=a.naturalHeight||0;return u<100||d<100?!1:d>14e3||u>14e3||d>2400&&d>u*2.4},s=function(a){try{if(!a.complete||a.naturalWidth<100||a.naturalHeight<100||n(a))return;const u=document.createElement("canvas");u.width=a.naturalWidth,u.height=a.naturalHeight;const d=u.getContext("2d");if(!d)return;d.drawImage(a,0,0);let x=u.toDataURL("image/webp",.89);x.startsWith("data:image/webp")||(x=u.toDataURL("image/jpeg",.89));const w=x.indexOf(",");return w>=0?x.slice(w+1):void 0}catch{return}},r=function(){E.setMaxBatchSize(h.sourceLang==="ja"?4:3)},o=function(a,u=24e3){return new Promise(d=>{let x=!1;const w=window.setTimeout(()=>{x||(x=!0,d({ok:!1,state:"stopped",health:null,message:`Timeout (${Math.round(u/1e3)}s) aguardando resposta do motor local.`}))},u);chrome.runtime.sendMessage(a).then(A=>{x||(x=!0,window.clearTimeout(w),d(A))}).catch(A=>{x||(x=!0,window.clearTimeout(w),d({ok:!1,state:"stopped",health:null,message:A instanceof Error?A.message:String(A)}))})})},i=function(){if(!h.autoTranslate||v!=="running"||E.isPaused())return;const a=B.getPages(),u=B.getVisiblePage();if(!u)return;y.getState(u.element)==="idle"&&E.enqueue(u,50,!1);const d=Math.max(3,Math.min(4,h.prefetchCount||4)),x=u.index,w=Math.min(a.length,x+1+d);for(let A=x+1;A<w;A++){const P=a[A];y.getState(P.element)==="idle"&&E.enqueue(P,10,!1)}},l=function(a){var x;if(!a||E.isPaused()||v!=="running"||((x=g==null?void 0:g.circuit_breaker)==null?void 0:x.state)==="OPEN"||!a.element.isConnected||y.getState(a.element)!=="idle")return;const u=`${a.imageUrl}::${h.sourceLang}`;if(C.has(u))return;C.add(u);const d=s(a.element);chrome.runtime.sendMessage({type:"PREPARE_IMAGE_URL",imageUrl:a.imageUrl,pageUrl:window.location.href,imageBase64:d}).catch(()=>{C.delete(u)})},b=function(a){var u;a.code==="CIRCUIT_BREAKER_OPEN"||a.code==="RATE_LIMITED_429"||a.code==="UPSTREAM_UNAVAILABLE"?(E.pauseAndClearPrefetch(),p.showAlert({kind:"circuit_open",title:a.code==="CIRCUIT_BREAKER_OPEN"?"Circuito aberto (cota / instabilidade Gemini)":a.code==="RATE_LIMITED_429"?"Limite Free Tier atingido (429)":"Gemini temporariamente indisponível (503)",detail:"Prefetch e capítulo pausados; páginas já traduzidas foram preservadas. "+a.message,retryAfterSec:a.retry_after_sec??((u=a.circuit_breaker)==null?void 0:u.retry_after_sec)??30,showResumeButton:!0})):a.code==="CORS_OR_NETWORK_BLOCKED"?p.showAlert({kind:"cors",title:"Acesso à imagem bloqueado pelo site",detail:a.message}):a.code==="ENGINE_OFFLINE"?(E.cancelAll(),v="stopped",g=null,p.updateEngineState("stopped",null),p.showAlert({kind:"info",title:"Motor local desligado",detail:a.message})):p.showAlert({kind:"error",title:`Erro (${a.code})`,detail:a.message})};window.__svPtBrLoaded=!0;let h={...oe},k=ee(),v="stopped",g=null;const C=new Set,S=new ce,y=new fe({onRetryRequested:a=>{const u=B.getPages().find(d=>d.element===a);u&&(p.clearAlert(),E.enqueue(u,100,!0))},onManualTranslateRequested:a=>{const u=B.getPages().find(d=>d.element===a);u&&(p.clearAlert(),E.enqueue(u,100,!1))}}),E=new ve(async(a,u)=>{await Z(a,u)},async(a,u)=>{await we(a,u)},(a,u,d)=>{const x=u>0,w=x?a+Math.max(0,u-1):a;p.updateProgress(y.getTranslatedCount(),B.getPages().length,w,x,d)},3),p=new ue({onToggleEngine:async()=>{if(v==="running"){E.cancelAll(),C.clear(),p.updateEngineState("stopping",g);const a=await o({type:"ENGINE_STOP"},15e3);v=a.state,g=a.health,p.updateEngineState(v,g)}else{p.clearAlert(),p.updateEngineState("starting",null);const a=await o({type:"ENGINE_START"},24e3);v=a.state,g=a.health,p.updateEngineState(v,g),a.ok?v==="running"&&(E.resume(),i()):p.showAlert({kind:"error",title:"Motor local não iniciou",detail:a.message||"Falha desconhecida ao iniciar motor em 127.0.0.1."})}},onTranslateVisible:()=>{q()},onToggleChapter:()=>{E.isChapterActive()?(E.pauseAndClearPrefetch(),p.showToast("Tradução do capítulo pausada.")):X()},onManualCrop:()=>{p.setExpanded(!1),N.start()},onToggleAutoTranslate:async a=>{await R(a)},onChangeSourceLang:async a=>{h=await chrome.runtime.sendMessage({type:"SAVE_SETTINGS",settings:{sourceLang:a}}),C.clear(),r(),p.updateSettings(h),p.showToast(`Idioma de origem: ${a.toUpperCase()}`)},onResumeCircuit:async()=>{await chrome.runtime.sendMessage({type:"RESET_CIRCUIT_BREAKER"}),p.clearAlert(),E.resume(),q()}}),N=new de({ensureEngineRunning:j,getActiveGlossary:f,onError:a=>{a.ok||b(a)},onToast:a=>p.showToast(a)}),B=new me({onPagesChanged:a=>{p.updateProgress(y.getTranslatedCount(),a.length,E.getPendingCount(),!1,E.isChapterActive()),xe(a),h.autoTranslate&&v==="running"&&i()},onVisiblePageChanged:()=>{h.autoTranslate&&v==="running"&&i()}});async function R(a){h=await chrome.runtime.sendMessage({type:"SAVE_SETTINGS",settings:{autoTranslate:a}}),p.updateSettings(h),a?(E.resume(),i(),p.showToast("Auto Translate ATIVADO (lote de 4 págs)")):(E.pauseAndClearPrefetch(),p.showToast("Auto Translate DESATIVADO"))}async function j(){if(v==="running")return!0;const a=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});return v=a.state,g=a.health,p.updateEngineState(v,g),v==="running"?!0:(p.showAlert({kind:"info",title:"Motor local desligado",detail:"Clique em 'Ligar Motor' (ou execute start.bat) para iniciar a tradução local."}),p.setExpanded(!0),!1)}async function xe(a){const u=e();for(const d of a){if(y.getState(d.element)!=="idle")continue;const x=z(d.imageUrl,h,u),w=await S.get(x);w&&(w.noText?y.markNoText(d.element):w.blob&&y.applyTranslatedBlob(d.element,w.blob,w.blocksCount))}p.updateProgress(y.getTranslatedCount(),a.length,E.getPendingCount(),!1,E.isChapterActive())}async function q(){B.scanNow();const a=B.getVisiblePage();if(!a){p.showAlert({kind:"info",title:"Nenhuma página de mangá detectada",detail:"Role até a imagem do capítulo e tente novamente."});return}await j()&&(p.clearAlert(),E.enqueue(a,100,!1))}async function X(){B.scanNow();const a=B.getPages();if(a.length===0){p.showAlert({kind:"info",title:"Nenhuma página detectada neste capítulo",detail:"Aguarde as imagens do capítulo carregarem e tente novamente."});return}if(!await j())return;p.clearAlert();const u=a.filter(w=>{const A=y.getState(w.element);return A==="idle"||A==="error"});if(u.length===0){p.showToast("Todas as páginas já estão traduzidas!");return}const d=B.getVisiblePage();r(),E.enqueueChapter(u,d);const x=h.sourceLang==="ja"?"4":"3";p.showToast(`Traduzindo capítulo (${u.length} págs em lotes de ${x})...`)}async function Q(){try{const a=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});v=a.state,g=a.health,p.updateEngineState(v,g)}catch{}}async function Z(a,u){const{page:d,forceRetranslate:x}=a;if(!d.element.isConnected)return;const w=f(),A=$(w),P=z(d.imageUrl,h,A);if(x)await S.delete(P);else{const T=await S.get(P);if(T){T.noText?y.markNoText(d.element):T.blob&&y.applyTranslatedBlob(d.element,T.blob,T.blocksCount);return}}if(y.setLoading(d.element),u.length>0)l(u[0].page);else if(h.autoTranslate){const _=B.getPages()[d.index+1]??null;l(_)}const U=s(d.element),m=await chrome.runtime.sendMessage({type:"TRANSLATE_IMAGE_URL",imageUrl:d.imageUrl,pageUrl:window.location.href,imageBase64:U,forceRetranslate:x,glossary:Object.keys(w).length>0?w:void 0});if(!m.ok){y.setError(d.element,m.message),b(m);return}if(m.no_text)y.markNoText(d.element),await S.put({key:P,imageUrl:d.imageUrl,blob:null,noText:!0,blocksCount:0,model:m.model,style:m.style,uppercase:m.uppercase,createdAt:Date.now()});else{const T=t(m.image_base64,m.mime_type||"image/webp");y.applyTranslatedBlob(d.element,T,m.blocks_count),await S.put({key:P,imageUrl:d.imageUrl,blob:T,noText:!1,blocksCount:m.blocks_count,model:m.model,style:m.style,uppercase:m.uppercase,createdAt:Date.now()})}await Q()}async function we(a,u){const d=f(),x=$(d),w=[];for(let m=0;m<a.length;m++){const T=a[m];if(!T.page.element.isConnected)continue;const _=z(T.page.imageUrl,h,x);if(T.forceRetranslate)await S.delete(_);else{const O=await S.get(_);if(O){O.noText?y.markNoText(T.page.element):O.blob&&y.applyTranslatedBlob(T.page.element,O.blob,O.blocksCount);continue}}w.push({task:T,pageId:`p${m}`,cacheKey:_})}if(w.length===0)return;if(w.length===1){await Z(w[0].task,u);return}for(const m of w)y.setLoading(m.task.page.element);u.length>0&&l(u[0].page);const A=new Map(w.map(m=>[m.pageId,m])),P=w.map(m=>({pageId:m.pageId,imageUrl:m.task.page.imageUrl,imageBase64:s(m.task.page.element),forceRetranslate:m.task.forceRetranslate})),U=await chrome.runtime.sendMessage({type:"TRANSLATE_BATCH_URLS",pageUrl:window.location.href,glossary:Object.keys(d).length>0?d:void 0,items:P});if(!U.ok){for(const m of w)y.setError(m.task.page.element,U.message);b(U);return}if(U.skipped_errors)for(const[m,T]of Object.entries(U.skipped_errors)){const _=A.get(m);_&&y.setError(_.task.page.element,T)}for(const m of U.pages){const T=A.get(m.page_id);if(!(!T||!T.task.page.element.isConnected))if(m.no_text)y.markNoText(T.task.page.element),await S.put({key:T.cacheKey,imageUrl:T.task.page.imageUrl,blob:null,noText:!0,blocksCount:0,model:U.model,style:U.style,uppercase:U.uppercase,createdAt:Date.now()});else{const _=t(m.image_base64,m.mime_type||"image/webp");y.applyTranslatedBlob(T.task.page.element,_,m.blocks_count),await S.put({key:T.cacheKey,imageUrl:T.task.page.imageUrl,blob:_,noText:!1,blocksCount:m.blocks_count,model:U.model,style:U.style,uppercase:U.uppercase,createdAt:Date.now()})}}await Q()}window.addEventListener("keydown",a=>{const u=a.target;if(!(u&&(u.tagName==="INPUT"||u.tagName==="TEXTAREA"||u.tagName==="SELECT"||u.isContentEditable))&&a.altKey&&!a.ctrlKey&&!a.metaKey){const d=a.key.toLowerCase();d==="t"?(a.preventDefault(),a.shiftKey?R(!h.autoTranslate):q()):d==="s"&&!a.shiftKey&&(a.preventDefault(),p.setExpanded(!1),N.start())}}),chrome.runtime.onMessage.addListener((a,u,d)=>{if(a.type==="SETTINGS_UPDATED"){const x=a.settings.sourceLang!==h.sourceLang;h=a.settings,x&&C.clear(),r(),p.updateSettings(h),h.autoTranslate?v==="running"&&(E.resume(),i()):E.pauseAndClearPrefetch(),d({ok:!0})}else if(a.type==="GLOSSARY_UPDATED")k=a.store,d({ok:!0});else if(a.type==="ENGINE_STATE_BROADCAST")v=a.state,g=a.health,p.updateEngineState(v,g),(v==="stopped"||v==="stopping")&&(E.cancelAll(),C.clear()),d({ok:!0});else{if(a.type==="CLEAR_TAB_CACHE")return(async()=>(await S.clearAll(),C.clear(),y.restoreAllOriginals(),N.clearAllPins(),d({ok:!0})))(),!0;a.type==="TRANSLATE_VISIBLE_PAGE"?(q(),d({ok:!0})):a.type==="TRANSLATE_CHAPTER"?(X(),d({ok:!0})):a.type==="CANCEL_CHAPTER"?(E.pauseAndClearPrefetch(),d({ok:!0})):a.type==="START_MANUAL_CROP"&&(p.setExpanded(!1),N.start(),d({ok:!0}))}return!1}),(async()=>{try{h=await chrome.runtime.sendMessage({type:"GET_SETTINGS"}),r(),p.updateSettings(h),k=await chrome.runtime.sendMessage({type:"GET_GLOSSARY_STORE"});const a=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});v=a.state,g=a.health,p.updateEngineState(v,g)}catch{}B.start()})()}})();
