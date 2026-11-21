/**
 * Manual Crop / Screenshot Translation Mode (`Alt+S` + Panel/Popup button).
 *
 * Designed for `<canvas>` readers, light-DRM / scrambled-tile sites (Naver, Kakao,
 * MangaPlus), or any single speech bubble / borderless caption missed by auto detection:
 * - Renders an isolated Shadow DOM crosshair overlay (`Esc` cancels).
 * - Captures the visible tab via `chrome.tabs.captureVisibleTab` (Service Worker),
 *   crops exact DPR-scaled coordinates, sends to `POST /translate-page` with `direct_ocr=true`.
 * - Pins the translated overlay directly over the selected page region (scrolling with the
 *   page!) with quick buttons: `Original / Traduzido`, `Copiar` (clipboard), and `×` (close).
 */

import type {
  CaptureTabResponse,
  RuntimeMessage,
  TranslatePageResponse,
} from "../shared/types";

export interface CropOverlayCallbacks {
  ensureEngineRunning: () => Promise<boolean>;
  getActiveGlossary: () => Record<string, string>;
  onError: (resp: TranslatePageResponse) => void;
  onToast: (msg: string) => void;
}

interface CropRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

export class ManualCropOverlay {
  private readonly callbacks: CropOverlayCallbacks;
  private active = false;
  private selectorHost: HTMLDivElement | null = null;
  private pinsHost: HTMLDivElement;
  private pinsShadow: ShadowRoot;
  private pinsLayer: HTMLDivElement;

  constructor(callbacks: CropOverlayCallbacks) {
    this.callbacks = callbacks;

    // Document-anchored host so translated crop pins scroll naturally with the comic page!
    this.pinsHost = document.createElement("div");
    this.pinsHost.id = "sv-ptbr-crop-pins-root";
    this.pinsHost.style.cssText =
      "position:absolute;left:0;top:0;width:0;height:0;z-index:2147483642;pointer-events:none;";
    this.pinsShadow = this.pinsHost.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
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
    `;
    this.pinsLayer = document.createElement("div");
    this.pinsShadow.append(style, this.pinsLayer);
    document.documentElement.appendChild(this.pinsHost);
  }

  isActive(): boolean {
    return this.active;
  }

  cancel(): void {
    this.active = false;
    if (this.selectorHost) {
      this.selectorHost.remove();
      this.selectorHost = null;
    }
    window.removeEventListener("keydown", this.handleEsc, true);
  }

  clearAllPins(): void {
    this.pinsLayer.innerHTML = "";
  }

  private handleEsc = (e: KeyboardEvent): void => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      this.cancel();
      this.callbacks.onToast("Recorte manual cancelado.");
    }
  };

  async start(): Promise<void> {
    if (this.active) {
      this.cancel();
      return;
    }
    if (!(await this.callbacks.ensureEngineRunning())) return;

    this.active = true;
    const host = document.createElement("div");
    host.id = "sv-ptbr-crop-selector";
    host.style.cssText =
      "position:fixed;inset:0;z-index:2147483646;cursor:crosshair;user-select:none;";
    const shadow = host.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
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
    `;

    const backdrop = document.createElement("div");
    backdrop.className = "sv-backdrop";

    const hint = document.createElement("div");
    hint.className = "sv-hint";
    hint.textContent = "Arraste sobre o balão ou quadro para traduzir • Esc para cancelar";

    const rectEl = document.createElement("div");
    rectEl.className = "sv-rect";
    const dimEl = document.createElement("span");
    dimEl.className = "sv-dim";
    rectEl.appendChild(dimEl);

    shadow.append(style, backdrop, rectEl, hint);
    document.documentElement.appendChild(host);
    this.selectorHost = host;

    window.addEventListener("keydown", this.handleEsc, true);

    let dragging = false;
    let startX = 0;
    let startY = 0;
    let currentRect: CropRect = { left: 0, top: 0, width: 0, height: 0 };

    const updateRect = (clientX: number, clientY: number) => {
      const x1 = Math.max(0, Math.min(startX, clientX));
      const y1 = Math.max(0, Math.min(startY, clientY));
      const x2 = Math.min(window.innerWidth, Math.max(startX, clientX));
      const y2 = Math.min(window.innerHeight, Math.max(startY, clientY));
      currentRect = { left: x1, top: y1, width: x2 - x1, height: y2 - y1 };
      rectEl.style.left = `${x1}px`;
      rectEl.style.top = `${y1}px`;
      rectEl.style.width = `${currentRect.width}px`;
      rectEl.style.height = `${currentRect.height}px`;
      dimEl.textContent = `${Math.round(currentRect.width)}×${Math.round(currentRect.height)}px`;
    };

    backdrop.addEventListener("pointerdown", (e: PointerEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      rectEl.style.display = "block";
      updateRect(e.clientX, e.clientY);
      backdrop.setPointerCapture(e.pointerId);
    });

    backdrop.addEventListener("pointermove", (e: PointerEvent) => {
      if (!dragging) return;
      e.preventDefault();
      updateRect(e.clientX, e.clientY);
    });

    backdrop.addEventListener("pointerup", (e: PointerEvent) => {
      if (!dragging) return;
      dragging = false;
      updateRect(e.clientX, e.clientY);
      const selected = { ...currentRect };
      this.cancel();

      if (selected.width < 24 || selected.height < 24) {
        this.callbacks.onToast("Área muito pequena — arraste um retângulo maior.");
        return;
      }
      void this.captureAndTranslateSelection(selected);
    });
  }

  private async captureAndTranslateSelection(rect: CropRect): Promise<void> {
    // Wait two frames so the crosshair & dark backdrop are 100% removed before screenshot capture!
    await new Promise<void>((r) =>
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => r()))
    );

    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    const capResp = (await chrome.runtime.sendMessage({
      type: "CAPTURE_VISIBLE_TAB",
    } satisfies RuntimeMessage)) as CaptureTabResponse;

    if (!capResp?.ok || !capResp.dataUrl) {
      this.callbacks.onToast(capResp?.message || "Falha ao capturar tela da aba.");
      return;
    }

    // Crop exact DPR-scaled rectangle from screenshot on an offscreen canvas
    const cropResult = await this.cropDataUrl(capResp.dataUrl, rect);
    if (!cropResult) {
      this.callbacks.onToast("Não foi possível recortar a imagem capturada.");
      return;
    }

    // Create document-anchored pin over the exact selected region
    const pin = document.createElement("div");
    pin.className = "sv-pin";
    pin.style.left = `${Math.round(rect.left + scrollX)}px`;
    pin.style.top = `${Math.round(rect.top + scrollY)}px`;
    pin.style.width = `${Math.round(rect.width)}px`;
    pin.style.height = `${Math.round(rect.height)}px`;

    const bar = document.createElement("div");
    bar.className = "sv-pin-bar";

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "sv-pin-btn";
    toggleBtn.textContent = "Original";
    toggleBtn.style.display = "none";

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "sv-pin-btn";
    copyBtn.textContent = "Copiar";
    copyBtn.style.display = "none";

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "sv-pin-btn sv-pin-close";
    closeBtn.textContent = "×";
    closeBtn.title = "Fechar recorte";

    bar.append(toggleBtn, copyBtn, closeBtn);

    const loadingBox = document.createElement("div");
    loadingBox.className = "sv-pin-loading";
    loadingBox.textContent = "Traduzindo recorte...";

    pin.append(bar, loadingBox);
    this.pinsLayer.appendChild(pin);

    let translatedUrl: string | null = null;
    closeBtn.addEventListener("click", () => {
      if (translatedUrl) URL.revokeObjectURL(translatedUrl);
      pin.remove();
    });

    const glossary = this.callbacks.getActiveGlossary();
    const resp = (await chrome.runtime.sendMessage({
      type: "TRANSLATE_IMAGE_URL",
      imageUrl: `sv-crop://${Date.now()}`,
      pageUrl: window.location.href,
      imageBase64: cropResult.base64,
      forceRetranslate: true,
      directOcr: true,
      glossary: Object.keys(glossary).length > 0 ? glossary : undefined,
    } satisfies RuntimeMessage)) as TranslatePageResponse;

    if (!resp.ok) {
      loadingBox.textContent = `Erro: ${resp.message}`;
      loadingBox.style.color = "#fecaca";
      this.callbacks.onError(resp);
      return;
    }

    if (resp.no_text) {
      loadingBox.textContent = "Nenhum texto detectado neste recorte.";
      loadingBox.style.color = "#cbd5e1";
      return;
    }

    const bin = atob(resp.image_base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    const blob = new Blob([bytes], { type: resp.mime_type || "image/webp" });
    translatedUrl = URL.createObjectURL(blob);

    const imgEl = document.createElement("img");
    imgEl.className = "sv-pin-img";
    imgEl.src = translatedUrl;

    loadingBox.remove();
    pin.appendChild(imgEl);

    let showingTranslated = true;
    toggleBtn.style.display = "inline-block";
    toggleBtn.addEventListener("click", () => {
      showingTranslated = !showingTranslated;
      imgEl.src = showingTranslated ? translatedUrl! : cropResult.dataUrl;
      toggleBtn.textContent = showingTranslated ? "Original" : "Traduzido";
    });

    const translatedText = (resp.blocks ?? [])
      .map((b) => (b.translated_text || "").trim())
      .filter(Boolean)
      .join("\n");

    if (translatedText) {
      copyBtn.style.display = "inline-block";
      copyBtn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(translatedText);
          copyBtn.textContent = "Copiado!";
          window.setTimeout(() => {
            copyBtn.textContent = "Copiar";
          }, 1600);
        } catch {
          this.callbacks.onToast("Falha ao copiar texto.");
        }
      });
    }
  }

  private cropDataUrl(
    dataUrl: string,
    rect: CropRect
  ): Promise<{ base64: string; dataUrl: string } | null> {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        try {
          // Compute true pixel scale from captured bitmap vs viewport (handles DPR & browser zoom cleanly!)
          const scaleX = img.naturalWidth / Math.max(1, window.innerWidth);
          const scaleY = img.naturalHeight / Math.max(1, window.innerHeight);
          const sx = Math.max(0, Math.round(rect.left * scaleX));
          const sy = Math.max(0, Math.round(rect.top * scaleY));
          const sw = Math.max(1, Math.min(img.naturalWidth - sx, Math.round(rect.width * scaleX)));
          const sh = Math.max(1, Math.min(img.naturalHeight - sy, Math.round(rect.height * scaleY)));

          const canvas = document.createElement("canvas");
          canvas.width = sw;
          canvas.height = sh;
          const ctx = canvas.getContext("2d");
          if (!ctx) {
            resolve(null);
            return;
          }
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
          const croppedUrl = canvas.toDataURL("image/png");
          const comma = croppedUrl.indexOf(",");
          resolve({
            dataUrl: croppedUrl,
            base64: comma >= 0 ? croppedUrl.slice(comma + 1) : croppedUrl,
          });
        } catch {
          resolve(null);
        }
      };
      img.onerror = () => resolve(null);
      img.src = dataUrl;
    });
  }
}
