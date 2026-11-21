var se=Object.defineProperty;var ae=(C,A,B)=>A in C?se(C,A,{enumerable:!0,configurable:!0,writable:!0,value:B}):C[A]=B;var d=(C,A,B)=>ae(C,typeof A!="symbol"?A+"":A,B);(function(){"use strict";const C="gemini-3.5-flash-lite",A="v3-scanlation-turbo",B=[{value:"auto",label:"Auto (JP/EN)"},{value:"ja",label:"Japonês"},{value:"en",label:"Inglês"},{value:"ko",label:"Coreano"},{value:"zh",label:"Chinês"}],$={apiKey:"",model:C,sourceLang:"auto",style:"scanlation",uppercase:!0,autoTranslate:!1,idleTimeoutSec:300,backendPort:8765,prefetchCount:4},G="scanlator_vogels_cache_v1",T="translated_pages",W=1,M=250;function _(E,e){return`${A}::${E}::${e.sourceLang}::${e.model}::${e.style}::${e.uppercase?"U1":"U0"}`}class j{constructor(){d(this,"dbPromise",null)}openDb(){return this.dbPromise?this.dbPromise:(this.dbPromise=new Promise((e,t)=>{const n=indexedDB.open(G,W);n.onupgradeneeded=()=>{const a=n.result;a.objectStoreNames.contains(T)||a.createObjectStore(T,{keyPath:"key"}).createIndex("createdAt","createdAt",{unique:!1})},n.onsuccess=()=>e(n.result),n.onerror=()=>t(n.error)}),this.dbPromise)}async get(e){try{const t=await this.openDb();return await new Promise((n,a)=>{const i=t.transaction(T,"readonly").objectStore(T).get(e);i.onsuccess=()=>n(i.result??null),i.onerror=()=>a(i.error)})}catch{return null}}async put(e){try{const t=await this.openDb();await new Promise((n,a)=>{const o=t.transaction(T,"readwrite");o.objectStore(T).put(e),o.oncomplete=()=>n(),o.onerror=()=>a(o.error)}),await this.pruneIfNeeded()}catch{}}async delete(e){try{const t=await this.openDb();await new Promise((n,a)=>{const o=t.transaction(T,"readwrite");o.objectStore(T).delete(e),o.oncomplete=()=>n(),o.onerror=()=>a(o.error)})}catch{}}async clearAll(){try{const e=await this.openDb();await new Promise((t,n)=>{const a=e.transaction(T,"readwrite");a.objectStore(T).clear(),a.oncomplete=()=>t(),a.onerror=()=>n(a.error)})}catch{}}async pruneIfNeeded(){try{const e=await this.openDb(),t=await new Promise((a,o)=>{const r=e.transaction(T,"readonly").objectStore(T).index("createdAt"),c=[],m=r.openCursor();m.onsuccess=()=>{const y=m.result;if(y){const b=y.value;c.push({key:b.key,createdAt:b.createdAt}),y.continue()}else a(c)},m.onerror=()=>o(m.error)});if(t.length<=M)return;const n=t.slice(0,t.length-M);await new Promise((a,o)=>{const i=e.transaction(T,"readwrite"),r=i.objectStore(T);for(const c of n)r.delete(c.key);i.oncomplete=()=>a(),i.onerror=()=>o(i.error)})}catch{}}}class F{constructor(e){d(this,"callbacks");d(this,"host");d(this,"shadow");d(this,"expanded",!1);d(this,"pillBtn");d(this,"pillDot");d(this,"pillText");d(this,"cardEl");d(this,"statusTextEl");d(this,"engineBtn");d(this,"telemetryEl");d(this,"langSelect");d(this,"translateNowBtn");d(this,"chapterBtn");d(this,"autoCheckbox");d(this,"progressEl");d(this,"alertEl");d(this,"alertTitleEl");d(this,"alertDetailEl");d(this,"resumeBtn");d(this,"toastEl");d(this,"countdownTimer",null);d(this,"toastTimer",null);d(this,"countdownSec",0);this.callbacks=e,this.host=document.createElement("div"),this.host.id="sv-ptbr-floating-root",this.host.style.cssText="position:fixed;right:14px;bottom:14px;z-index:2147483645;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;",this.shadow=this.host.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
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
    `,this.toastEl=document.createElement("div"),this.toastEl.className="sv-toast",this.pillBtn=document.createElement("button"),this.pillBtn.type="button",this.pillBtn.className="sv-fab",this.pillBtn.title="Scanlator Vogels PT-BR (Alt+T página | Alt+Shift+T auto)",this.pillDot=document.createElement("span"),this.pillDot.className="sv-dot stopped",this.pillText=document.createElement("span"),this.pillText.textContent="SV",this.pillBtn.append(this.pillDot,this.pillText),this.cardEl=document.createElement("div"),this.cardEl.className="sv-card",this.cardEl.innerHTML=`
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
      <label class="sv-toggle">
        <span>Auto Translate (prefetch)</span>
        <input type="checkbox" />
      </label>
      <div class="sv-meta-row">
        <span>Alt+T • Alt+Shift+T</span>
        <span class="sv-progress">0 / 0 págs</span>
      </div>
      <div class="sv-alert">
        <div class="sv-alert-title"></div>
        <div class="sv-alert-detail"></div>
        <button type="button" class="sv-btn sv-btn-resume">Testar Retomada</button>
      </div>
    `;const n=this.cardEl.querySelector(".sv-min-btn");this.statusTextEl=this.cardEl.querySelector(".sv-status"),this.engineBtn=this.cardEl.querySelector(".sv-row .sv-btn"),this.telemetryEl=this.cardEl.querySelector(".sv-telemetry"),this.langSelect=this.cardEl.querySelector("#svLang"),this.translateNowBtn=this.cardEl.querySelector("#svPageBtn"),this.chapterBtn=this.cardEl.querySelector("#svChapterBtn"),this.autoCheckbox=this.cardEl.querySelector("input[type='checkbox']"),this.progressEl=this.cardEl.querySelector(".sv-progress"),this.alertEl=this.cardEl.querySelector(".sv-alert"),this.alertTitleEl=this.cardEl.querySelector(".sv-alert-title"),this.alertDetailEl=this.cardEl.querySelector(".sv-alert-detail"),this.resumeBtn=this.cardEl.querySelector(".sv-btn-resume");for(const a of B){const o=document.createElement("option");o.value=a.value,o.textContent=a.label,this.langSelect.appendChild(o)}this.pillBtn.addEventListener("click",()=>this.setExpanded(!0)),n.addEventListener("click",()=>this.setExpanded(!1)),this.engineBtn.addEventListener("click",()=>this.callbacks.onToggleEngine()),this.translateNowBtn.addEventListener("click",()=>this.callbacks.onTranslateVisible()),this.chapterBtn.addEventListener("click",()=>this.callbacks.onToggleChapter()),this.autoCheckbox.addEventListener("change",()=>this.callbacks.onToggleAutoTranslate(this.autoCheckbox.checked)),this.langSelect.addEventListener("change",()=>this.callbacks.onChangeSourceLang(this.langSelect.value)),this.resumeBtn.addEventListener("click",()=>this.callbacks.onResumeCircuit()),this.shadow.append(t,this.toastEl,this.pillBtn,this.cardEl),document.documentElement.appendChild(this.host)}setExpanded(e){this.expanded=e,this.cardEl.classList.toggle("open",e),this.pillBtn.style.display=e?"none":"inline-flex"}showToast(e,t=2200){this.toastTimer!==null&&window.clearTimeout(this.toastTimer),this.toastEl.textContent=e,this.toastEl.classList.add("visible"),this.toastTimer=window.setTimeout(()=>{this.toastEl.classList.remove("visible"),this.toastTimer=null},t)}updateEngineState(e,t){var n,a;if(this.pillDot.className=`sv-dot ${e}`,e==="running"&&t){this.statusTextEl.textContent="Motor: Ativo",this.engineBtn.textContent="Desligar Motor",this.engineBtn.className="sv-btn sv-btn-power-off",this.engineBtn.disabled=!1,this.translateNowBtn.disabled=!1,this.chapterBtn.disabled=!1;const o=t.warming_up?"Aquecendo GPU...":`VRAM: ${Math.round(t.torch_vram_allocated_mb)} MB Torch`,i=((n=t.quota)==null?void 0:n.requests_today)??0,r=Math.ceil(t.idle_remaining_sec/60);this.telemetryEl.textContent=`${o} • Cota hoje: ${i} req(s) • Auto-off: ~${r}m`,((a=t.circuit_breaker)==null?void 0:a.state)==="OPEN"&&(this.pillDot.className="sv-dot alert")}else e==="starting"?(this.statusTextEl.textContent="Motor: Ligando...",this.engineBtn.textContent="Ligando...",this.engineBtn.disabled=!0,this.translateNowBtn.disabled=!0,this.chapterBtn.disabled=!0,this.telemetryEl.textContent="Iniciando servidor local em 127.0.0.1..."):e==="stopping"?(this.statusTextEl.textContent="Motor: Desligando...",this.engineBtn.textContent="Desligando...",this.engineBtn.disabled=!0,this.translateNowBtn.disabled=!0,this.chapterBtn.disabled=!0,this.telemetryEl.textContent="Liberando 100% da VRAM..."):(this.statusTextEl.textContent="Motor: Desligado",this.engineBtn.textContent="Ligar Motor",this.engineBtn.className="sv-btn sv-btn-power-on",this.engineBtn.disabled=!1,this.translateNowBtn.disabled=!1,this.chapterBtn.disabled=!1,this.telemetryEl.textContent="0% CPU • 0 MB RAM • 0 MB VRAM")}updateSettings(e){this.autoCheckbox.checked=e.autoTranslate,this.langSelect.value=e.sourceLang}updateProgress(e,t,n,a,o){const i=a||n>0?` • fila: ${n+(a?1:0)}`:"";this.progressEl.textContent=`${e}/${t} págs${i}`,this.pillText.textContent=o||a?`SV ${e}/${t}`:"SV",o?(this.chapterBtn.textContent="Pausar / Cancelar",this.chapterBtn.className="sv-btn sv-btn-cancel"):(this.chapterBtn.textContent="Traduzir Capítulo",this.chapterBtn.className="sv-btn sv-btn-chapter")}showAlert(e){if(this.countdownTimer!==null&&(window.clearInterval(this.countdownTimer),this.countdownTimer=null),this.alertEl.className=e.kind==="cors"||e.kind==="error"?"sv-alert error visible":"sv-alert visible",this.alertTitleEl.textContent=e.title,this.resumeBtn.style.display=e.showResumeButton?"block":"none",e.retryAfterSec&&e.retryAfterSec>0){this.countdownSec=Math.ceil(e.retryAfterSec);const t=()=>{this.alertDetailEl.textContent=`${e.detail} (retomada automática em ${this.countdownSec}s)`};t(),this.countdownTimer=window.setInterval(()=>{this.countdownSec=Math.max(0,this.countdownSec-1),this.countdownSec<=0?(this.countdownTimer!==null&&window.clearInterval(this.countdownTimer),this.countdownTimer=null,this.alertDetailEl.textContent=`${e.detail} — pronto para testar retomada.`):t()},1e3)}else this.alertDetailEl.textContent=e.detail;e.kind==="circuit_open"&&(this.pillDot.className="sv-dot alert",this.setExpanded(!0))}clearAlert(){this.countdownTimer!==null&&(window.clearInterval(this.countdownTimer),this.countdownTimer=null),this.alertEl.classList.remove("visible")}}const N=["data-src","data-original","data-lazy-src","data-url","data-cfsrc","data-actualsrc","data-image","data-hi-res-src"],I=["data-srcset","data-lazy-srcset","srcset"],H=/\b(logo|avatar|icon|banner|thumb|thumbnail|cover|emoji|badge|button|sponsor|ads?|promo|gravatar|spinner|loading|placeholder)\b/i;function P(E){const e=E.split(",").map(t=>t.trim()).filter(Boolean).map(t=>{const n=t.split(/\s+/),a=n[0],o=n[1]||"1x";let i=1;return o.endsWith("w")?i=parseFloat(o)||1:o.endsWith("x")&&(i=(parseFloat(o)||1)*1e3),{url:a,weight:i}}).filter(t=>!!t.url);return e.length===0?null:(e.sort((t,n)=>n.weight-t.weight),e[0].url)}function R(E){if(!E)return null;const e=E.trim();if(!e||e.startsWith("about:")||e.startsWith("javascript:")||e.startsWith("data:image/svg")||e.startsWith("data:image/gif"))return null;try{return new URL(e,window.location.href).href}catch{return null}}class K{constructor(){d(this,"name","GenericReaderAdapter")}matches(e,t){return!0}resolveImageUrl(e){const t=e.dataset.svOrigUrl;if(t)return t;const n=e.getAttribute("src")||"";if(!n||n.startsWith("data:image/gif")||n.startsWith("data:image/svg")||/loading|placeholder|blank|spacer|pixel/i.test(n)){for(const r of N){const c=R(e.getAttribute(r));if(c)return c}for(const r of I){const c=e.getAttribute(r);if(c){const m=R(P(c));if(m)return m}}}const o=e.parentElement;if(o&&o.tagName==="PICTURE"){const r=Array.from(o.querySelectorAll("source"));for(const c of r){const m=c.getAttribute("srcset")||c.getAttribute("data-srcset");if(m){const y=R(P(m));if(y)return y}}}for(const r of I){const c=e.getAttribute(r);if(c){const m=R(P(c));if(m)return m}}const i=R(e.currentSrc||e.src);if(i&&!i.startsWith("blob:"))return i;for(const r of N){const c=R(e.getAttribute(r));if(c)return c}return i}isComicPageCandidate(e){if(!e.isConnected||e.dataset.svIgnore==="1"||!this.resolveImageUrl(e))return!1;const n=window.getComputedStyle(e);if(n.display==="none"||n.visibility==="hidden"||n.opacity==="0")return!1;const a=e.getBoundingClientRect(),o=e.naturalWidth||0,i=e.naturalHeight||0,r=Math.max(o,Math.round(a.width)),c=Math.max(i,Math.round(a.height));if(r<260||c<320)return!1;const m=r/Math.max(1,c);if(m>2.25||m<.06)return!1;const y=`${e.className} ${e.id} ${e.alt}`;return!(H.test(y)&&r<620||e.closest("a")&&a.width<420&&a.height<520)}findCandidateImages(e){const n=Array.from(e.querySelectorAll("img")).filter(a=>this.isComicPageCandidate(a));return n.sort((a,o)=>{const i=a.getBoundingClientRect(),r=o.getBoundingClientRect(),c=i.top-r.top;return Math.abs(c)>24?c:i.left-r.left}),n}}const Q=[];function Y(E=document){const e=new URL(window.location.href);for(const t of Q)if(t.matches(e,E))return t;return new K}class X{constructor(e,t){d(this,"adapter");d(this,"callbacks");d(this,"mutationObserver",null);d(this,"intersectionObserver",null);d(this,"observedElements",new Set);d(this,"visibilityRatios",new Map);d(this,"pages",[]);d(this,"scanTimer",null);d(this,"lastVisibleEl",null);d(this,"handleGlobalTrigger",()=>{this.scheduleScan()});d(this,"handleScroll",()=>{this.emitVisibleIfChanged()});this.callbacks=e,this.adapter=t??Y(document)}getAdapterName(){return this.adapter.name}getPages(){return this.pages}getVisiblePage(){if(this.pages.length===0)return null;let e=null,t=-1;const n=window.innerHeight||900;for(const a of this.pages){const o=a.element.getBoundingClientRect(),i=Math.max(0,o.top),r=Math.min(n,o.bottom),c=Math.max(0,r-i),m=this.visibilityRatios.get(a.element)??0,y=c+m*250;c>40&&y>t&&(t=y,e=a.element)}return e?this.pages.find(a=>a.element===e)??this.pages[0]:this.pages[0]}start(){this.intersectionObserver=new IntersectionObserver(e=>{for(const t of e){const n=t.target;this.visibilityRatios.set(n,t.isIntersecting?t.intersectionRatio:0)}this.emitVisibleIfChanged()},{threshold:[0,.15,.35,.6,.85]}),this.mutationObserver=new MutationObserver(()=>{this.scheduleScan()}),this.mutationObserver.observe(document.documentElement,{childList:!0,subtree:!0,attributes:!0,attributeFilter:["src","srcset","data-src","data-original","data-lazy-src","class","style"]}),window.addEventListener("load",this.handleGlobalTrigger,{passive:!0}),window.addEventListener("scroll",this.handleScroll,{passive:!0}),window.addEventListener("resize",this.handleGlobalTrigger,{passive:!0}),this.scanNow()}stop(){var e,t;this.scanTimer!==null&&(window.clearTimeout(this.scanTimer),this.scanTimer=null),(e=this.mutationObserver)==null||e.disconnect(),(t=this.intersectionObserver)==null||t.disconnect(),this.observedElements.clear(),this.visibilityRatios.clear(),window.removeEventListener("load",this.handleGlobalTrigger),window.removeEventListener("scroll",this.handleScroll),window.removeEventListener("resize",this.handleGlobalTrigger)}scheduleScan(e=250){this.scanTimer===null&&(this.scanTimer=window.setTimeout(()=>{this.scanTimer=null,this.scanNow()},e))}scanNow(){var a,o;const e=this.adapter.findCandidateImages(document),t=[];for(let i=0;i<e.length;i++){const r=e[i],c=this.adapter.resolveImageUrl(r);c&&(r.dataset.svOrigUrl||(r.dataset.svOrigUrl=c),t.push({element:r,imageUrl:c,index:i}),this.observedElements.has(r)||(this.observedElements.add(r),(a=this.intersectionObserver)==null||a.observe(r),r.addEventListener("load",this.handleGlobalTrigger,{passive:!0})))}const n=new Set(e);for(const i of Array.from(this.observedElements))n.has(i)||((o=this.intersectionObserver)==null||o.unobserve(i),this.observedElements.delete(i),this.visibilityRatios.delete(i));this.pages=t,this.callbacks.onPagesChanged(this.pages),this.emitVisibleIfChanged()}emitVisibleIfChanged(){const e=this.getVisiblePage(),t=(e==null?void 0:e.element)??null;t!==this.lastVisibleEl&&(this.lastVisibleEl=t,this.callbacks.onVisiblePageChanged(e,this.pages))}}class J{constructor(e){d(this,"callbacks");d(this,"records",new Map);d(this,"hostEl");d(this,"shadow");d(this,"layerEl");d(this,"rafId",null);d(this,"scheduleReposition",()=>{this.rafId===null&&(this.rafId=window.requestAnimationFrame(()=>{this.rafId=null;for(const e of this.records.values())e.hoverBar.classList.contains("visible")&&this.positionBar(e)}))});this.callbacks=e,this.hostEl=document.createElement("div"),this.hostEl.id="sv-ptbr-overlay-root",this.hostEl.style.cssText="position:fixed;inset:0;pointer-events:none;z-index:2147483640;overflow:hidden;",this.shadow=this.hostEl.attachShadow({mode:"open"});const t=document.createElement("style");t.textContent=`
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
    `,this.layerEl=document.createElement("div"),this.shadow.append(t,this.layerEl),document.documentElement.appendChild(this.hostEl),window.addEventListener("scroll",this.scheduleReposition,{passive:!0}),window.addEventListener("resize",this.scheduleReposition,{passive:!0})}ensureRecord(e){const t=this.records.get(e);if(t)return t;const n=document.createElement("div");n.className="sv-bar";const a=document.createElement("span");a.className="sv-pill",a.textContent="SV";const o=document.createElement("button");o.type="button",o.className="sv-btn",o.textContent="Ver Original";const i=document.createElement("button");i.type="button",i.className="sv-btn",i.textContent="Retry",i.title="Refaz somente a traducao (reaproveita OCR/inpainting do cache)",n.append(a,o,i),this.layerEl.appendChild(n);const r={img:e,origSrc:e.dataset.svOrigUrl||e.currentSrc||e.src,origSrcset:e.getAttribute("srcset")||"",translatedUrl:null,showingTranslated:!1,state:"idle",errorMessage:"",blocksCount:0,hoverBar:n,toggleBtn:o,retryBtn:i,statusPill:a,isHovered:!1,barHovered:!1};return o.addEventListener("click",c=>{c.preventDefault(),c.stopPropagation(),r.state==="translated"&&r.translatedUrl?this.toggleOriginal(e):this.callbacks.onManualTranslateRequested(e)}),i.addEventListener("click",c=>{c.preventDefault(),c.stopPropagation(),this.callbacks.onRetryRequested(e)}),e.addEventListener("mouseenter",()=>{r.isHovered=!0,this.updateBarVisibility(r)}),e.addEventListener("mouseleave",()=>{r.isHovered=!1,window.setTimeout(()=>this.updateBarVisibility(r),120)}),n.addEventListener("mouseenter",()=>{r.barHovered=!0,this.updateBarVisibility(r)}),n.addEventListener("mouseleave",()=>{r.barHovered=!1,this.updateBarVisibility(r)}),this.records.set(e,r),r}getState(e){var t;return((t=this.records.get(e))==null?void 0:t.state)??"idle"}getTranslatedCount(){let e=0;for(const t of this.records.values())t.img.isConnected&&(t.state==="translated"||t.state==="no_text")&&(e+=1);return e}setLoading(e){const t=this.ensureRecord(e);t.state="loading",t.errorMessage="",this.syncRecordUi(t)}applyTranslatedBlob(e,t,n){const a=this.ensureRecord(e);a.translatedUrl&&URL.revokeObjectURL(a.translatedUrl);const o=URL.createObjectURL(t);a.translatedUrl=o,a.blocksCount=n,a.state="translated",a.showingTranslated=!0,a.errorMessage="",e.dataset.svOrigUrl||(e.dataset.svOrigUrl=a.origSrc),a.origSrcset&&e.removeAttribute("srcset"),e.src=o,this.syncRecordUi(a)}markNoText(e){const t=this.ensureRecord(e);t.state="no_text",t.blocksCount=0,t.errorMessage="",this.syncRecordUi(t)}setError(e,t){const n=this.ensureRecord(e);n.state!=="translated"&&(n.state="error"),n.errorMessage=t,this.syncRecordUi(n)}toggleOriginal(e){const t=this.records.get(e);!t||!t.translatedUrl||(t.showingTranslated?(e.src=t.origSrc,t.origSrcset&&e.setAttribute("srcset",t.origSrcset),t.showingTranslated=!1):(t.origSrcset&&e.removeAttribute("srcset"),e.src=t.translatedUrl,t.showingTranslated=!0),this.syncRecordUi(t))}restoreAllOriginals(){for(const e of this.records.values())e.showingTranslated&&(e.img.src=e.origSrc,e.origSrcset&&e.img.setAttribute("srcset",e.origSrcset),e.showingTranslated=!1),e.translatedUrl&&(URL.revokeObjectURL(e.translatedUrl),e.translatedUrl=null),e.state="idle",this.syncRecordUi(e)}syncRecordUi(e){if(e.state==="loading")e.statusPill.className="sv-pill loading",e.statusPill.textContent="Traduzindo...",e.toggleBtn.style.display="none",e.retryBtn.style.display="none";else if(e.state==="translated")e.statusPill.className=e.showingTranslated?"sv-pill done":"sv-pill orig",e.statusPill.textContent=e.showingTranslated?`PT-BR (${e.blocksCount})`:"Original",e.toggleBtn.style.display="inline-block",e.toggleBtn.textContent=e.showingTranslated?"Original":"Traduzido",e.retryBtn.style.display="inline-block";else if(e.state==="no_text")e.statusPill.className="sv-pill notext",e.statusPill.textContent="Sem texto",e.toggleBtn.style.display="none",e.retryBtn.style.display="inline-block";else if(e.state==="error")e.statusPill.className="sv-pill error",e.statusPill.textContent="Erro",e.statusPill.title=e.errorMessage,e.toggleBtn.style.display="none",e.retryBtn.style.display="inline-block";else{e.hoverBar.classList.remove("visible");return}this.updateBarVisibility(e)}updateBarVisibility(e){if(!e.img.isConnected||e.state==="idle"){e.hoverBar.classList.remove("visible");return}if(!(e.state==="loading"||e.state==="error"||e.isHovered||e.barHovered)){e.hoverBar.classList.remove("visible");return}this.positionBar(e)}positionBar(e){const t=e.img.getBoundingClientRect(),n=window.innerWidth,a=window.innerHeight;if(t.bottom<0||t.top>a||t.right<0||t.left>n||t.width<120){e.hoverBar.classList.remove("visible");return}e.hoverBar.classList.add("visible");const o=Math.max(8,Math.min(a-38,t.top+10)),i=Math.max(8,n-Math.min(n-8,t.right-10));e.hoverBar.style.top=`${Math.round(o)}px`,e.hoverBar.style.right=`${Math.round(i)}px`}}class Z{constructor(e,t,n,a=4){d(this,"singleWorkerFn");d(this,"batchWorkerFn");d(this,"onQueueStateChange");d(this,"maxBatchSize");d(this,"queue",[]);d(this,"activeUrls",new Set);d(this,"running",!1);d(this,"paused",!1);d(this,"chapterActive",!1);d(this,"generation",0);this.singleWorkerFn=e,this.batchWorkerFn=t,this.onQueueStateChange=n,this.maxBatchSize=Math.max(1,Math.min(4,a))}enqueue(e,t,n=!1){if(this.paused&&t<90||this.activeUrls.has(e.imageUrl)&&!n)return;const a=this.queue.find(o=>o.page.imageUrl===e.imageUrl);a?(a.priority=Math.max(a.priority,t),a.forceRetranslate=a.forceRetranslate||n,a.page=e):this.queue.push({page:e,priority:t,forceRetranslate:n}),this.sortQueue(),this.emitState(),this.pump()}enqueueChapter(e,t){this.paused=!1,this.chapterActive=!0;const n=(t==null?void 0:t.imageUrl)??null;for(const a of e){if(this.activeUrls.has(a.imageUrl))continue;const o=n&&a.imageUrl===n?100:30,i=this.queue.find(r=>r.page.imageUrl===a.imageUrl);i?(i.priority=Math.max(i.priority,o),i.page=a):this.queue.push({page:a,priority:o,forceRetranslate:!1})}this.sortQueue(),this.emitState(),this.pump()}pauseAndClearPrefetch(){this.paused=!0,this.chapterActive=!1,this.queue=[],this.emitState()}cancelAll(){this.paused=!0,this.chapterActive=!1,this.generation+=1,this.queue=[],this.activeUrls.clear(),this.emitState()}resume(){this.paused=!1,this.emitState(),this.pump()}isPaused(){return this.paused}isChapterActive(){return this.chapterActive&&(this.queue.length>0||this.activeUrls.size>0)}getPendingCount(){return this.queue.length+this.activeUrls.size}sortQueue(){this.queue.sort((e,t)=>t.priority-e.priority||e.page.index-t.page.index)}emitState(){const e=this.activeUrls.size;e===0&&this.queue.length===0&&(this.chapterActive=!1),this.onQueueStateChange(this.queue.length,e,this.chapterActive)}async pump(){if(this.running)return;this.running=!0;const e=this.generation;try{for(;this.queue.length>0&&e===this.generation;){const t=this.queue[0];if(this.paused&&t.priority<90)break;if(t.priority>=50||t.forceRetranslate){const o=this.queue.shift(),i=this.paused?[]:this.queue.slice(0,2);this.activeUrls.add(o.page.imageUrl),this.emitState();try{await this.singleWorkerFn(o,i)}finally{e===this.generation&&(this.activeUrls.delete(o.page.imageUrl),this.emitState())}continue}const n=[];for(;n.length<this.maxBatchSize&&this.queue.length>0&&this.queue[0].priority<50&&!this.queue[0].forceRetranslate;){const o=this.queue.shift();n.push(o),this.activeUrls.add(o.page.imageUrl)}const a=this.paused?[]:this.queue.slice(0,2);this.emitState();try{n.length>=2?await this.batchWorkerFn(n,a):n.length===1&&await this.singleWorkerFn(n[0],a)}finally{if(e===this.generation){for(const o of n)this.activeUrls.delete(o.page.imageUrl);this.emitState()}}}}finally{this.running=!1,this.emitState()}}}if(!window.__svPtBrLoaded){let E=function(s,l="image/webp"){const u=atob(s),p=u.length,x=new Uint8Array(p);for(let f=0;f<p;f++)x[f]=u.charCodeAt(f);return new Blob([x],{type:l})},e=function(s){try{if(!s.complete||s.naturalWidth<100||s.naturalHeight<100)return;const l=document.createElement("canvas");l.width=s.naturalWidth,l.height=s.naturalHeight;const u=l.getContext("2d");if(!u)return;u.drawImage(s,0,0);let p=l.toDataURL("image/webp",.92);p.startsWith("data:image/webp")||(p=l.toDataURL("image/png"));const x=p.indexOf(",");return x>=0?p.slice(x+1):void 0}catch{return}},t=function(s,l=24e3){return new Promise(u=>{let p=!1;const x=window.setTimeout(()=>{p||(p=!0,u({ok:!1,state:"stopped",health:null,message:`Timeout (${Math.round(l/1e3)}s) aguardando resposta do motor local.`}))},l);chrome.runtime.sendMessage(s).then(f=>{p||(p=!0,window.clearTimeout(x),u(f))}).catch(f=>{p||(p=!0,window.clearTimeout(x),u({ok:!1,state:"stopped",health:null,message:f instanceof Error?f.message:String(f)}))})})},n=function(){if(!i.autoTranslate||r!=="running"||w.isPaused())return;const s=S.getPages(),l=S.getVisiblePage();if(!l)return;b.getState(l.element)==="idle"&&w.enqueue(l,50,!1);const u=Math.max(3,Math.min(4,i.prefetchCount||4)),p=l.index,x=Math.min(s.length,p+1+u);for(let f=p+1;f<x;f++){const h=s[f];b.getState(h.element)==="idle"&&w.enqueue(h,10,!1)}},a=function(s){var p;if(!s||w.isPaused()||r!=="running"||((p=c==null?void 0:c.circuit_breaker)==null?void 0:p.state)==="OPEN"||!s.element.isConnected||b.getState(s.element)!=="idle")return;const l=`${s.imageUrl}::${i.sourceLang}`;if(m.has(l))return;m.add(l);const u=e(s.element);chrome.runtime.sendMessage({type:"PREPARE_IMAGE_URL",imageUrl:s.imageUrl,pageUrl:window.location.href,imageBase64:u}).catch(()=>{m.delete(l)})},o=function(s){var l;s.code==="CIRCUIT_BREAKER_OPEN"||s.code==="RATE_LIMITED_429"||s.code==="UPSTREAM_UNAVAILABLE"?(w.pauseAndClearPrefetch(),g.showAlert({kind:"circuit_open",title:s.code==="CIRCUIT_BREAKER_OPEN"?"Circuito aberto (cota / instabilidade Gemini)":s.code==="RATE_LIMITED_429"?"Limite Free Tier atingido (429)":"Gemini temporariamente indisponível (503)",detail:"Prefetch e capítulo pausados; páginas já traduzidas foram preservadas. "+s.message,retryAfterSec:s.retry_after_sec??((l=s.circuit_breaker)==null?void 0:l.retry_after_sec)??30,showResumeButton:!0})):s.code==="CORS_OR_NETWORK_BLOCKED"?g.showAlert({kind:"cors",title:"Acesso à imagem bloqueado pelo site",detail:s.message}):s.code==="ENGINE_OFFLINE"?(w.cancelAll(),r="stopped",c=null,g.updateEngineState("stopped",null),g.showAlert({kind:"info",title:"Motor local desligado",detail:s.message})):g.showAlert({kind:"error",title:`Erro (${s.code})`,detail:s.message})};window.__svPtBrLoaded=!0;let i={...$},r="stopped",c=null;const m=new Set,y=new j,b=new J({onRetryRequested:s=>{const l=S.getPages().find(u=>u.element===s);l&&(g.clearAlert(),w.enqueue(l,100,!0))},onManualTranslateRequested:s=>{const l=S.getPages().find(u=>u.element===s);l&&(g.clearAlert(),w.enqueue(l,100,!1))}}),w=new Z(async(s,l)=>{await z(s,l)},async(s,l)=>{await te(s,l)},(s,l,u)=>{const p=l>0,x=p?s+Math.max(0,l-1):s;g.updateProgress(b.getTranslatedCount(),S.getPages().length,x,p,u)},4),g=new F({onToggleEngine:async()=>{if(r==="running"){w.cancelAll(),m.clear(),g.updateEngineState("stopping",c);const s=await t({type:"ENGINE_STOP"},15e3);r=s.state,c=s.health,g.updateEngineState(r,c)}else{g.clearAlert(),g.updateEngineState("starting",null);const s=await t({type:"ENGINE_START"},24e3);r=s.state,c=s.health,g.updateEngineState(r,c),s.ok?r==="running"&&(w.resume(),n()):g.showAlert({kind:"error",title:"Motor local não iniciou",detail:s.message||"Falha desconhecida ao iniciar motor em 127.0.0.1."})}},onTranslateVisible:()=>{L()},onToggleChapter:()=>{w.isChapterActive()?(w.pauseAndClearPrefetch(),g.showToast("Tradução do capítulo pausada.")):D()},onToggleAutoTranslate:async s=>{await q(s)},onChangeSourceLang:async s=>{i=await chrome.runtime.sendMessage({type:"SAVE_SETTINGS",settings:{sourceLang:s}}),m.clear(),g.updateSettings(i),g.showToast(`Idioma de origem: ${s.toUpperCase()}`)},onResumeCircuit:async()=>{await chrome.runtime.sendMessage({type:"RESET_CIRCUIT_BREAKER"}),g.clearAlert(),w.resume(),L()}}),S=new X({onPagesChanged:s=>{g.updateProgress(b.getTranslatedCount(),s.length,w.getPendingCount(),!1,w.isChapterActive()),ee(s),i.autoTranslate&&r==="running"&&n()},onVisiblePageChanged:()=>{i.autoTranslate&&r==="running"&&n()}});async function q(s){i=await chrome.runtime.sendMessage({type:"SAVE_SETTINGS",settings:{autoTranslate:s}}),g.updateSettings(i),s?(w.resume(),n(),g.showToast("Auto Translate ATIVADO (lote de 4 págs)")):(w.pauseAndClearPrefetch(),g.showToast("Auto Translate DESATIVADO"))}async function O(){if(r==="running")return!0;const s=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});return r=s.state,c=s.health,g.updateEngineState(r,c),r==="running"?!0:(g.showAlert({kind:"info",title:"Motor local desligado",detail:"Clique em 'Ligar Motor' (ou execute start.bat) para iniciar a tradução local."}),g.setExpanded(!0),!1)}async function ee(s){for(const l of s){if(b.getState(l.element)!=="idle")continue;const u=_(l.imageUrl,i),p=await y.get(u);p&&(p.noText?b.markNoText(l.element):p.blob&&b.applyTranslatedBlob(l.element,p.blob,p.blocksCount))}g.updateProgress(b.getTranslatedCount(),s.length,w.getPendingCount(),!1,w.isChapterActive())}async function L(){S.scanNow();const s=S.getVisiblePage();if(!s){g.showAlert({kind:"info",title:"Nenhuma página de mangá detectada",detail:"Role até a imagem do capítulo e tente novamente."});return}await O()&&(g.clearAlert(),w.enqueue(s,100,!1))}async function D(){S.scanNow();const s=S.getPages();if(s.length===0){g.showAlert({kind:"info",title:"Nenhuma página detectada neste capítulo",detail:"Aguarde as imagens do capítulo carregarem e tente novamente."});return}if(!await O())return;g.clearAlert();const l=s.filter(p=>{const x=b.getState(p.element);return x==="idle"||x==="error"});if(l.length===0){g.showToast("Todas as páginas já estão traduzidas!");return}const u=S.getVisiblePage();w.enqueueChapter(l,u),g.showToast(`Traduzindo capítulo (${l.length} págs em lotes de 4)...`)}async function V(){try{const s=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});r=s.state,c=s.health,g.updateEngineState(r,c)}catch{}}async function z(s,l){const{page:u,forceRetranslate:p}=s;if(!u.element.isConnected)return;const x=_(u.imageUrl,i);if(p)await y.delete(x);else{const v=await y.get(x);if(v){v.noText?b.markNoText(u.element):v.blob&&b.applyTranslatedBlob(u.element,v.blob,v.blocksCount);return}}if(b.setLoading(u.element),l.length>0)a(l[0].page);else if(i.autoTranslate){const k=S.getPages()[u.index+1]??null;a(k)}const f=e(u.element),h=await chrome.runtime.sendMessage({type:"TRANSLATE_IMAGE_URL",imageUrl:u.imageUrl,pageUrl:window.location.href,imageBase64:f,forceRetranslate:p});if(!h.ok){b.setError(u.element,h.message),o(h);return}if(h.no_text)b.markNoText(u.element),await y.put({key:x,imageUrl:u.imageUrl,blob:null,noText:!0,blocksCount:0,model:h.model,style:h.style,uppercase:h.uppercase,createdAt:Date.now()});else{const v=E(h.image_base64,h.mime_type||"image/webp");b.applyTranslatedBlob(u.element,v,h.blocks_count),await y.put({key:x,imageUrl:u.imageUrl,blob:v,noText:!1,blocksCount:h.blocks_count,model:h.model,style:h.style,uppercase:h.uppercase,createdAt:Date.now()})}await V()}async function te(s,l){const u=[];for(let h=0;h<s.length;h++){const v=s[h];if(!v.page.element.isConnected)continue;const k=_(v.page.imageUrl,i);if(v.forceRetranslate)await y.delete(k);else{const U=await y.get(k);if(U){U.noText?b.markNoText(v.page.element):U.blob&&b.applyTranslatedBlob(v.page.element,U.blob,U.blocksCount);continue}}u.push({task:v,pageId:`p${h}`,cacheKey:k})}if(u.length===0)return;if(u.length===1){await z(u[0].task,l);return}for(const h of u)b.setLoading(h.task.page.element);l.length>0&&a(l[0].page);const p=new Map(u.map(h=>[h.pageId,h])),x=u.map(h=>({pageId:h.pageId,imageUrl:h.task.page.imageUrl,imageBase64:e(h.task.page.element),forceRetranslate:h.task.forceRetranslate})),f=await chrome.runtime.sendMessage({type:"TRANSLATE_BATCH_URLS",pageUrl:window.location.href,items:x});if(!f.ok){for(const h of u)b.setError(h.task.page.element,f.message);o(f);return}if(f.skipped_errors)for(const[h,v]of Object.entries(f.skipped_errors)){const k=p.get(h);k&&b.setError(k.task.page.element,v)}for(const h of f.pages){const v=p.get(h.page_id);if(!(!v||!v.task.page.element.isConnected))if(h.no_text)b.markNoText(v.task.page.element),await y.put({key:v.cacheKey,imageUrl:v.task.page.imageUrl,blob:null,noText:!0,blocksCount:0,model:f.model,style:f.style,uppercase:f.uppercase,createdAt:Date.now()});else{const k=E(h.image_base64,h.mime_type||"image/webp");b.applyTranslatedBlob(v.task.page.element,k,h.blocks_count),await y.put({key:v.cacheKey,imageUrl:v.task.page.imageUrl,blob:k,noText:!1,blocksCount:h.blocks_count,model:f.model,style:f.style,uppercase:f.uppercase,createdAt:Date.now()})}}await V()}window.addEventListener("keydown",s=>{const l=s.target;l&&(l.tagName==="INPUT"||l.tagName==="TEXTAREA"||l.tagName==="SELECT"||l.isContentEditable)||s.altKey&&!s.ctrlKey&&!s.metaKey&&s.key.toLowerCase()==="t"&&(s.preventDefault(),s.shiftKey?q(!i.autoTranslate):L())}),chrome.runtime.onMessage.addListener((s,l,u)=>{if(s.type==="SETTINGS_UPDATED"){const p=s.settings.sourceLang!==i.sourceLang;i=s.settings,p&&m.clear(),g.updateSettings(i),i.autoTranslate?r==="running"&&(w.resume(),n()):w.pauseAndClearPrefetch(),u({ok:!0})}else if(s.type==="ENGINE_STATE_BROADCAST")r=s.state,c=s.health,g.updateEngineState(r,c),(r==="stopped"||r==="stopping")&&(w.cancelAll(),m.clear()),u({ok:!0});else{if(s.type==="CLEAR_TAB_CACHE")return(async()=>(await y.clearAll(),m.clear(),b.restoreAllOriginals(),u({ok:!0})))(),!0;s.type==="TRANSLATE_VISIBLE_PAGE"?(L(),u({ok:!0})):s.type==="TRANSLATE_CHAPTER"?(D(),u({ok:!0})):s.type==="CANCEL_CHAPTER"&&(w.pauseAndClearPrefetch(),u({ok:!0}))}return!1}),(async()=>{try{i=await chrome.runtime.sendMessage({type:"GET_SETTINGS"}),g.updateSettings(i);const s=await chrome.runtime.sendMessage({type:"ENGINE_STATUS"});r=s.state,c=s.health,g.updateEngineState(r,c)}catch{}S.start()})()}})();
