/**
 * Non-destructive in-page OverlayManager:
 * - Swaps `img.src` to translated ObjectURL while preserving exact original `src`/`srcset`,
 *   dimensions, aspect ratio, CSS layout, and scroll position without moving DOM nodes.
 * - Renders subtle hover controls (Original/Traduzido toggle + Retry reusing OCR cache +
 *   loading pill) inside an isolated Shadow DOM layer synced to each translated image.
 */

export type PageVisualState = "idle" | "loading" | "translated" | "no_text" | "error";

interface TrackedImageRecord {
  img: HTMLImageElement;
  origSrc: string;
  origSrcset: string;
  translatedUrl: string | null;
  showingTranslated: boolean;
  state: PageVisualState;
  errorMessage: string;
  blocksCount: number;
  hoverBar: HTMLDivElement;
  toggleBtn: HTMLButtonElement;
  retryBtn: HTMLButtonElement;
  statusPill: HTMLSpanElement;
  isHovered: boolean;
  barHovered: boolean;
}

export interface OverlayCallbacks {
  onRetryRequested: (img: HTMLImageElement) => void;
  onManualTranslateRequested: (img: HTMLImageElement) => void;
}

export class OverlayManager {
  private readonly callbacks: OverlayCallbacks;
  private readonly records = new Map<HTMLImageElement, TrackedImageRecord>();
  private hostEl: HTMLDivElement;
  private shadow: ShadowRoot;
  private layerEl: HTMLDivElement;
  private rafId: number | null = null;

  constructor(callbacks: OverlayCallbacks) {
    this.callbacks = callbacks;
    this.hostEl = document.createElement("div");
    this.hostEl.id = "sv-ptbr-overlay-root";
    this.hostEl.style.cssText =
      "position:fixed;inset:0;pointer-events:none;z-index:2147483640;overflow:hidden;";
    this.shadow = this.hostEl.attachShadow({ mode: "open" });

    const style = document.createElement("style");
    style.textContent = `
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
    `;
    this.layerEl = document.createElement("div");
    this.shadow.append(style, this.layerEl);
    document.documentElement.appendChild(this.hostEl);

    window.addEventListener("scroll", this.scheduleReposition, { passive: true });
    window.addEventListener("resize", this.scheduleReposition, { passive: true });
  }

  private ensureRecord(img: HTMLImageElement): TrackedImageRecord {
    const existing = this.records.get(img);
    if (existing) return existing;

    const hoverBar = document.createElement("div");
    hoverBar.className = "sv-bar";

    const statusPill = document.createElement("span");
    statusPill.className = "sv-pill";
    statusPill.textContent = "SV";

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "sv-btn";
    toggleBtn.textContent = "Ver Original";

    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "sv-btn";
    retryBtn.textContent = "Retry";
    retryBtn.title = "Refaz somente a traducao (reaproveita OCR/inpainting do cache)";

    hoverBar.append(statusPill, toggleBtn, retryBtn);
    this.layerEl.appendChild(hoverBar);

    const record: TrackedImageRecord = {
      img,
      origSrc: img.dataset.svOrigUrl || img.currentSrc || img.src,
      origSrcset: img.getAttribute("srcset") || "",
      translatedUrl: null,
      showingTranslated: false,
      state: "idle",
      errorMessage: "",
      blocksCount: 0,
      hoverBar,
      toggleBtn,
      retryBtn,
      statusPill,
      isHovered: false,
      barHovered: false,
    };

    toggleBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (record.state === "translated" && record.translatedUrl) {
        this.toggleOriginal(img);
      } else {
        this.callbacks.onManualTranslateRequested(img);
      }
    });

    retryBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.callbacks.onRetryRequested(img);
    });

    img.addEventListener("mouseenter", () => {
      record.isHovered = true;
      this.updateBarVisibility(record);
    });
    img.addEventListener("mouseleave", () => {
      record.isHovered = false;
      window.setTimeout(() => this.updateBarVisibility(record), 120);
    });
    hoverBar.addEventListener("mouseenter", () => {
      record.barHovered = true;
      this.updateBarVisibility(record);
    });
    hoverBar.addEventListener("mouseleave", () => {
      record.barHovered = false;
      this.updateBarVisibility(record);
    });

    this.records.set(img, record);
    return record;
  }

  getState(img: HTMLImageElement): PageVisualState {
    return this.records.get(img)?.state ?? "idle";
  }

  getTranslatedCount(): number {
    let count = 0;
    for (const rec of this.records.values()) {
      if (rec.img.isConnected && (rec.state === "translated" || rec.state === "no_text")) {
        count += 1;
      }
    }
    return count;
  }

  setLoading(img: HTMLImageElement): void {
    const rec = this.ensureRecord(img);
    rec.state = "loading";
    rec.errorMessage = "";
    this.syncRecordUi(rec);
  }

  applyTranslatedBlob(img: HTMLImageElement, blob: Blob, blocksCount: number): void {
    const rec = this.ensureRecord(img);
    if (rec.translatedUrl) {
      URL.revokeObjectURL(rec.translatedUrl);
    }
    const objUrl = URL.createObjectURL(blob);
    rec.translatedUrl = objUrl;
    rec.blocksCount = blocksCount;
    rec.state = "translated";
    rec.showingTranslated = true;
    rec.errorMessage = "";

    // Preserve natural dimensions & scroll position: swap src cleanly without touching DOM tree
    if (!img.dataset.svOrigUrl) {
      img.dataset.svOrigUrl = rec.origSrc;
    }
    if (rec.origSrcset) {
      img.removeAttribute("srcset");
    }
    img.src = objUrl;
    this.syncRecordUi(rec);
  }

  markNoText(img: HTMLImageElement): void {
    const rec = this.ensureRecord(img);
    rec.state = "no_text";
    rec.blocksCount = 0;
    rec.errorMessage = "";
    this.syncRecordUi(rec);
  }

  setError(img: HTMLImageElement, message: string): void {
    const rec = this.ensureRecord(img);
    // Preserve already translated image if retry failed!
    if (rec.state !== "translated") {
      rec.state = "error";
    }
    rec.errorMessage = message;
    this.syncRecordUi(rec);
  }

  toggleOriginal(img: HTMLImageElement): void {
    const rec = this.records.get(img);
    if (!rec || !rec.translatedUrl) return;
    if (rec.showingTranslated) {
      img.src = rec.origSrc;
      if (rec.origSrcset) {
        img.setAttribute("srcset", rec.origSrcset);
      }
      rec.showingTranslated = false;
    } else {
      if (rec.origSrcset) {
        img.removeAttribute("srcset");
      }
      img.src = rec.translatedUrl;
      rec.showingTranslated = true;
    }
    this.syncRecordUi(rec);
  }

  restoreAllOriginals(): void {
    for (const rec of this.records.values()) {
      if (rec.showingTranslated) {
        rec.img.src = rec.origSrc;
        if (rec.origSrcset) rec.img.setAttribute("srcset", rec.origSrcset);
        rec.showingTranslated = false;
      }
      if (rec.translatedUrl) {
        URL.revokeObjectURL(rec.translatedUrl);
        rec.translatedUrl = null;
      }
      rec.state = "idle";
      this.syncRecordUi(rec);
    }
  }

  private syncRecordUi(rec: TrackedImageRecord): void {
    if (rec.state === "loading") {
      rec.statusPill.className = "sv-pill loading";
      rec.statusPill.textContent = "Traduzindo...";
      rec.toggleBtn.style.display = "none";
      rec.retryBtn.style.display = "none";
    } else if (rec.state === "translated") {
      rec.statusPill.className = rec.showingTranslated ? "sv-pill done" : "sv-pill orig";
      rec.statusPill.textContent = rec.showingTranslated
        ? `PT-BR (${rec.blocksCount})`
        : "Original";
      rec.toggleBtn.style.display = "inline-block";
      rec.toggleBtn.textContent = rec.showingTranslated ? "Original" : "Traduzido";
      rec.retryBtn.style.display = "inline-block";
    } else if (rec.state === "no_text") {
      rec.statusPill.className = "sv-pill notext";
      rec.statusPill.textContent = "Sem texto";
      rec.toggleBtn.style.display = "none";
      rec.retryBtn.style.display = "inline-block";
    } else if (rec.state === "error") {
      rec.statusPill.className = "sv-pill error";
      rec.statusPill.textContent = "Erro";
      rec.statusPill.title = rec.errorMessage;
      rec.toggleBtn.style.display = "none";
      rec.retryBtn.style.display = "inline-block";
    } else {
      rec.hoverBar.classList.remove("visible");
      return;
    }
    this.updateBarVisibility(rec);
  }

  private updateBarVisibility(rec: TrackedImageRecord): void {
    if (!rec.img.isConnected || rec.state === "idle") {
      rec.hoverBar.classList.remove("visible");
      return;
    }
    const shouldShow =
      rec.state === "loading" ||
      rec.state === "error" ||
      rec.isHovered ||
      rec.barHovered;

    if (!shouldShow) {
      rec.hoverBar.classList.remove("visible");
      return;
    }
    this.positionBar(rec);
  }

  private positionBar(rec: TrackedImageRecord): void {
    const rect = rec.img.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw || rect.width < 120) {
      rec.hoverBar.classList.remove("visible");
      return;
    }
    rec.hoverBar.classList.add("visible");
    const top = Math.max(8, Math.min(vh - 38, rect.top + 10));
    const rightOffset = Math.max(8, vw - Math.min(vw - 8, rect.right - 10));
    rec.hoverBar.style.top = `${Math.round(top)}px`;
    rec.hoverBar.style.right = `${Math.round(rightOffset)}px`;
  }

  private scheduleReposition = (): void => {
    if (this.rafId !== null) return;
    this.rafId = window.requestAnimationFrame(() => {
      this.rafId = null;
      for (const rec of this.records.values()) {
        if (rec.hoverBar.classList.contains("visible")) {
          this.positionBar(rec);
        }
      }
    });
  };
}
