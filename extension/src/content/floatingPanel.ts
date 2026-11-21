/**
 * Discrete, minimizable-by-default floating control panel inside an isolated Shadow DOM.
 * Positioned in the bottom-left corner with a One Piece Dark aesthetic (obsidian, crimson red,
 * straw gold) and inline Straw Hat SVG branding.
 */

import {
  SOURCE_LANGUAGES,
  type BackendHealth,
  type EngineLifecycleState,
  type ExtensionSettings,
  type SourceLanguage,
} from "../shared/types";

export interface AlertBannerState {
  kind: "circuit_open" | "cors" | "error" | "info";
  title: string;
  detail: string;
  retryAfterSec?: number;
  showResumeButton?: boolean;
}

export interface FloatingPanelCallbacks {
  onToggleEngine: () => void;
  onTranslateVisible: () => void;
  onToggleChapter: () => void;
  onManualCrop: () => void;
  onToggleAutoTranslate: (enabled: boolean) => void;
  onChangeSourceLang: (lang: SourceLanguage) => void;
  onResumeCircuit: () => void;
}

function createStrawHatSvg(sizePx: number): string {
  return `
    <svg class="sv-hat-icon" width="${sizePx}" height="${sizePx}" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <ellipse cx="32" cy="41.2" rx="28.2" ry="10.4" fill="#F4C430" stroke="#1A1108" stroke-width="2.8"/>
      <ellipse cx="29.5" cy="40.2" rx="22.8" ry="6.8" fill="#FFE066" opacity="0.48"/>
      <ellipse cx="32" cy="40.6" rx="18.6" ry="5.1" fill="#B0760E" opacity="0.55"/>
      <path d="M14.5 38.2 C14.5 21 21.6 13.6 32 13.6 C42.4 13.6 49.5 21 49.5 38.2 C43.4 42.2 20.6 42.2 14.5 38.2 Z" fill="#F4C430" stroke="#1A1108" stroke-width="2.8" stroke-linejoin="round"/>
      <ellipse cx="25.5" cy="22.2" rx="8.6" ry="5.6" fill="#FFE066" opacity="0.62"/>
      <path d="M15.1 30.2 C22.5 34.0 41.5 34.0 48.9 30.2 L49.5 38.2 C43.4 42.2 20.6 42.2 14.5 38.2 Z" fill="#D62828" stroke="#1A1108" stroke-width="2.2" stroke-linejoin="round"/>
      <path d="M18.2 33.4 C24.5 36.0 34.5 36.2 38.8 35.1" stroke="#FF6B6B" stroke-width="1.8" stroke-linecap="round" opacity="0.72"/>
    </svg>
  `.trim();
}

export class FloatingPanel {
  private readonly callbacks: FloatingPanelCallbacks;
  private readonly host: HTMLDivElement;
  private readonly shadow: ShadowRoot;
  private expanded = false;

  private pillBtn: HTMLButtonElement;
  private pillDot: HTMLSpanElement;
  private pillText: HTMLSpanElement;
  private cardEl: HTMLDivElement;
  private statusTextEl: HTMLSpanElement;
  private engineBtn: HTMLButtonElement;
  private telemetryEl: HTMLDivElement;
  private langSelect: HTMLSelectElement;
  private translateNowBtn: HTMLButtonElement;
  private chapterBtn: HTMLButtonElement;
  private cropBtn: HTMLButtonElement;
  private autoCheckbox: HTMLInputElement;
  private progressEl: HTMLDivElement;
  private alertEl: HTMLDivElement;
  private alertTitleEl: HTMLDivElement;
  private alertDetailEl: HTMLDivElement;
  private resumeBtn: HTMLButtonElement;
  private toastEl: HTMLDivElement;

  private countdownTimer: number | null = null;
  private toastTimer: number | null = null;
  private countdownSec = 0;

  constructor(callbacks: FloatingPanelCallbacks) {
    this.callbacks = callbacks;
    this.host = document.createElement("div");
    this.host.id = "sv-ptbr-floating-root";
    this.host.style.cssText =
      "position:fixed;left:18px;bottom:18px;z-index:2147483645;display:flex;flex-direction:column;align-items:flex-start;max-width:calc(100vw - 36px);max-height:calc(100vh - 36px);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;";
    this.shadow = this.host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
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
    `;

    this.toastEl = document.createElement("div");
    this.toastEl.className = "sv-toast";

    this.pillBtn = document.createElement("button");
    this.pillBtn.type = "button";
    this.pillBtn.className = "sv-fab";
    this.pillBtn.title = "Scanlator Vogels PT-BR (Alt+T página | Alt+S recorte | Alt+Shift+T auto)";
    this.pillDot = document.createElement("span");
    this.pillDot.className = "sv-dot stopped";
    const pillHat = document.createElement("span");
    pillHat.innerHTML = createStrawHatSvg(24);
    this.pillText = document.createElement("span");
    this.pillText.className = "sv-fab-counter";
    this.pillText.textContent = "";
    this.pillBtn.append(this.pillDot, pillHat, this.pillText);

    this.cardEl = document.createElement("div");
    this.cardEl.className = "sv-card";
    this.cardEl.innerHTML = `
      <div class="sv-header">
        <span class="sv-brand">
          ${createStrawHatSvg(22)}
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
    `;

    const minBtn = this.cardEl.querySelector(".sv-min-btn") as HTMLButtonElement;
    this.statusTextEl = this.cardEl.querySelector(".sv-status") as HTMLSpanElement;
    this.engineBtn = this.cardEl.querySelector(".sv-row .sv-btn") as HTMLButtonElement;
    this.telemetryEl = this.cardEl.querySelector(".sv-telemetry") as HTMLDivElement;
    this.langSelect = this.cardEl.querySelector("#svLang") as HTMLSelectElement;
    this.translateNowBtn = this.cardEl.querySelector("#svPageBtn") as HTMLButtonElement;
    this.chapterBtn = this.cardEl.querySelector("#svChapterBtn") as HTMLButtonElement;
    this.cropBtn = this.cardEl.querySelector("#svCropBtn") as HTMLButtonElement;
    this.autoCheckbox = this.cardEl.querySelector("input[type='checkbox']") as HTMLInputElement;
    this.progressEl = this.cardEl.querySelector(".sv-progress") as HTMLDivElement;
    this.alertEl = this.cardEl.querySelector(".sv-alert") as HTMLDivElement;
    this.alertTitleEl = this.cardEl.querySelector(".sv-alert-title") as HTMLDivElement;
    this.alertDetailEl = this.cardEl.querySelector(".sv-alert-detail") as HTMLDivElement;
    this.resumeBtn = this.cardEl.querySelector(".sv-btn-resume") as HTMLButtonElement;

    for (const item of SOURCE_LANGUAGES) {
      const opt = document.createElement("option");
      opt.value = item.value;
      opt.textContent = item.label;
      this.langSelect.appendChild(opt);
    }

    this.pillBtn.addEventListener("click", () => this.setExpanded(true));
    minBtn.addEventListener("click", () => this.setExpanded(false));
    this.engineBtn.addEventListener("click", () => this.callbacks.onToggleEngine());
    this.translateNowBtn.addEventListener("click", () => this.callbacks.onTranslateVisible());
    this.chapterBtn.addEventListener("click", () => this.callbacks.onToggleChapter());
    this.cropBtn.addEventListener("click", () => this.callbacks.onManualCrop());
    this.autoCheckbox.addEventListener("change", () =>
      this.callbacks.onToggleAutoTranslate(this.autoCheckbox.checked)
    );
    this.langSelect.addEventListener("change", () =>
      this.callbacks.onChangeSourceLang(this.langSelect.value as SourceLanguage)
    );
    this.resumeBtn.addEventListener("click", () => this.callbacks.onResumeCircuit());

    this.shadow.append(style, this.toastEl, this.pillBtn, this.cardEl);
    document.documentElement.appendChild(this.host);
  }

  setExpanded(open: boolean): void {
    this.expanded = open;
    this.cardEl.classList.toggle("open", open);
    this.pillBtn.style.display = open ? "none" : "inline-flex";
  }

  showToast(message: string, durationMs = 2200): void {
    if (this.toastTimer !== null) window.clearTimeout(this.toastTimer);
    this.toastEl.textContent = message;
    this.toastEl.classList.add("visible");
    this.toastTimer = window.setTimeout(() => {
      this.toastEl.classList.remove("visible");
      this.toastTimer = null;
    }, durationMs);
  }

  updateEngineState(state: EngineLifecycleState, health: BackendHealth | null): void {
    this.pillDot.className = `sv-dot ${state}`;
    this.pillBtn.classList.toggle("running", state === "running");
    this.pillBtn.classList.remove("alert");

    if (state === "running" && health) {
      this.statusTextEl.textContent = "Motor: Ativo";
      this.engineBtn.textContent = "Desligar Motor";
      this.engineBtn.className = "sv-btn sv-btn-power-off";
      this.engineBtn.disabled = false;
      this.translateNowBtn.disabled = false;
      this.chapterBtn.disabled = false;
      this.cropBtn.disabled = false;

      const vramTxt = health.warming_up
        ? "Aquecendo GPU..."
        : `VRAM: ${Math.round(health.torch_vram_allocated_mb)} MB Torch`;
      const reqs = health.quota?.requests_today ?? 0;
      const idleMin = Math.ceil(health.idle_remaining_sec / 60);
      this.telemetryEl.textContent =
        `${vramTxt} • Cota hoje: ${reqs} req(s) • Auto-off: ~${idleMin}m`;

      if (health.circuit_breaker?.state === "OPEN") {
        this.pillDot.className = "sv-dot alert";
        this.pillBtn.classList.remove("running");
        this.pillBtn.classList.add("alert");
      }
    } else if (state === "starting") {
      this.statusTextEl.textContent = "Motor: Ligando...";
      this.engineBtn.textContent = "Ligando...";
      this.engineBtn.disabled = true;
      this.translateNowBtn.disabled = true;
      this.chapterBtn.disabled = true;
      this.cropBtn.disabled = true;
      this.telemetryEl.textContent = "Iniciando servidor local em 127.0.0.1...";
    } else if (state === "stopping") {
      this.statusTextEl.textContent = "Motor: Desligando...";
      this.engineBtn.textContent = "Desligando...";
      this.engineBtn.disabled = true;
      this.translateNowBtn.disabled = true;
      this.chapterBtn.disabled = true;
      this.cropBtn.disabled = true;
      this.telemetryEl.textContent = "Liberando 100% da VRAM...";
    } else {
      this.statusTextEl.textContent = "Motor: Desligado";
      this.engineBtn.textContent = "Ligar Motor";
      this.engineBtn.className = "sv-btn sv-btn-power-on";
      this.engineBtn.disabled = false;
      this.translateNowBtn.disabled = false;
      this.chapterBtn.disabled = false;
      this.cropBtn.disabled = false;
      this.telemetryEl.textContent = "0% CPU • 0 MB RAM • 0 MB VRAM";
    }
  }

  updateSettings(settings: ExtensionSettings): void {
    this.autoCheckbox.checked = settings.autoTranslate;
    this.langSelect.value = settings.sourceLang;
  }

  updateProgress(
    translated: number,
    total: number,
    queued: number,
    active: boolean,
    chapterActive: boolean
  ): void {
    const queueNote = active || queued > 0 ? ` • fila: ${queued + (active ? 1 : 0)}` : "";
    this.progressEl.textContent = `${translated}/${total} págs${queueNote}`;
    this.pillText.textContent = chapterActive || active ? `${translated}/${total}` : "";

    if (chapterActive) {
      this.chapterBtn.textContent = "Pausar / Cancelar";
      this.chapterBtn.className = "sv-btn sv-btn-cancel";
    } else {
      this.chapterBtn.textContent = "Traduzir Capítulo";
      this.chapterBtn.className = "sv-btn sv-btn-chapter";
    }
  }

  showAlert(alert: AlertBannerState): void {
    if (this.countdownTimer !== null) {
      window.clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }

    this.alertEl.className =
      alert.kind === "cors" || alert.kind === "error" ? "sv-alert error visible" : "sv-alert visible";
    this.alertTitleEl.textContent = alert.title;
    this.resumeBtn.style.display = alert.showResumeButton ? "block" : "none";

    if (alert.retryAfterSec && alert.retryAfterSec > 0) {
      this.countdownSec = Math.ceil(alert.retryAfterSec);
      const renderTick = () => {
        this.alertDetailEl.textContent = `${alert.detail} (retomada automática em ${this.countdownSec}s)`;
      };
      renderTick();
      this.countdownTimer = window.setInterval(() => {
        this.countdownSec = Math.max(0, this.countdownSec - 1);
        if (this.countdownSec <= 0) {
          if (this.countdownTimer !== null) window.clearInterval(this.countdownTimer);
          this.countdownTimer = null;
          this.alertDetailEl.textContent = `${alert.detail} — pronto para testar retomada.`;
        } else {
          renderTick();
        }
      }, 1000);
    } else {
      this.alertDetailEl.textContent = alert.detail;
    }

    if (alert.kind === "circuit_open") {
      this.pillDot.className = "sv-dot alert";
      this.pillBtn.classList.remove("running");
      this.pillBtn.classList.add("alert");
      this.setExpanded(true);
    }
  }

  clearAlert(): void {
    if (this.countdownTimer !== null) {
      window.clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    }
    this.alertEl.classList.remove("visible");
  }
}
