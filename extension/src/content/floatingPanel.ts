/**
 * Discrete, minimizable-by-default floating control panel inside an isolated Shadow DOM.
 * Supports Ligar/Desligar Motor, Traduzir Página, Traduzir Capítulo (+ Pausar/Cancelar),
 * Auto Translate, Idioma de origem (Auto JP/EN, JA, EN, KO, ZH), shortcuts, and Circuit Breaker alerts.
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
  onToggleAutoTranslate: (enabled: boolean) => void;
  onChangeSourceLang: (lang: SourceLanguage) => void;
  onResumeCircuit: () => void;
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
      "position:fixed;right:14px;bottom:14px;z-index:2147483645;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;";
    this.shadow = this.host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
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
    `;

    this.toastEl = document.createElement("div");
    this.toastEl.className = "sv-toast";

    this.pillBtn = document.createElement("button");
    this.pillBtn.type = "button";
    this.pillBtn.className = "sv-fab";
    this.pillBtn.title = "Scanlator Vogels PT-BR (Alt+T página | Alt+Shift+T auto)";
    this.pillDot = document.createElement("span");
    this.pillDot.className = "sv-dot stopped";
    this.pillText = document.createElement("span");
    this.pillText.textContent = "SV";
    this.pillBtn.append(this.pillDot, this.pillText);

    this.cardEl = document.createElement("div");
    this.cardEl.className = "sv-card";
    this.cardEl.innerHTML = `
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
    `;

    const minBtn = this.cardEl.querySelector(".sv-min-btn") as HTMLButtonElement;
    this.statusTextEl = this.cardEl.querySelector(".sv-status") as HTMLSpanElement;
    this.engineBtn = this.cardEl.querySelector(".sv-row .sv-btn") as HTMLButtonElement;
    this.telemetryEl = this.cardEl.querySelector(".sv-telemetry") as HTMLDivElement;
    this.langSelect = this.cardEl.querySelector("#svLang") as HTMLSelectElement;
    this.translateNowBtn = this.cardEl.querySelector("#svPageBtn") as HTMLButtonElement;
    this.chapterBtn = this.cardEl.querySelector("#svChapterBtn") as HTMLButtonElement;
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

    if (state === "running" && health) {
      this.statusTextEl.textContent = "Motor: Ativo";
      this.engineBtn.textContent = "Desligar Motor";
      this.engineBtn.className = "sv-btn sv-btn-power-off";
      this.engineBtn.disabled = false;
      this.translateNowBtn.disabled = false;
      this.chapterBtn.disabled = false;

      const vramTxt = health.warming_up
        ? "Aquecendo GPU..."
        : `VRAM: ${Math.round(health.torch_vram_allocated_mb)} MB Torch`;
      const reqs = health.quota?.requests_today ?? 0;
      const idleMin = Math.ceil(health.idle_remaining_sec / 60);
      this.telemetryEl.textContent =
        `${vramTxt} • Cota hoje: ${reqs} req(s) • Auto-off: ~${idleMin}m`;

      if (health.circuit_breaker?.state === "OPEN") {
        this.pillDot.className = "sv-dot alert";
      }
    } else if (state === "starting") {
      this.statusTextEl.textContent = "Motor: Ligando...";
      this.engineBtn.textContent = "Ligando...";
      this.engineBtn.disabled = true;
      this.translateNowBtn.disabled = true;
      this.chapterBtn.disabled = true;
      this.telemetryEl.textContent = "Iniciando servidor local em 127.0.0.1...";
    } else if (state === "stopping") {
      this.statusTextEl.textContent = "Motor: Desligando...";
      this.engineBtn.textContent = "Desligando...";
      this.engineBtn.disabled = true;
      this.translateNowBtn.disabled = true;
      this.chapterBtn.disabled = true;
      this.telemetryEl.textContent = "Liberando 100% da VRAM...";
    } else {
      this.statusTextEl.textContent = "Motor: Desligado";
      this.engineBtn.textContent = "Ligar Motor";
      this.engineBtn.className = "sv-btn sv-btn-power-on";
      this.engineBtn.disabled = false;
      this.translateNowBtn.disabled = false;
      this.chapterBtn.disabled = false;
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
    this.pillText.textContent = chapterActive || active ? `SV ${translated}/${total}` : "SV";

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
