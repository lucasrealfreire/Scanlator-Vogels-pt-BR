/**
 * Universal ImageDetector combining decoupled ReaderAdapters, MutationObserver
 * (lazy-loading / infinite scroll / SPA navigation), and IntersectionObserver
 * (viewport priority tracking for active reading + prefetch).
 */

import { resolveReaderAdapter } from "./adapters/GenericReaderAdapter";
import type { DetectedMangaPage, ReaderAdapter } from "./adapters/types";

export interface ImageDetectorCallbacks {
  onPagesChanged: (pages: DetectedMangaPage[]) => void;
  onVisiblePageChanged: (visiblePage: DetectedMangaPage | null, allPages: DetectedMangaPage[]) => void;
}

export class ImageDetector {
  private readonly adapter: ReaderAdapter;
  private readonly callbacks: ImageDetectorCallbacks;
  private mutationObserver: MutationObserver | null = null;
  private intersectionObserver: IntersectionObserver | null = null;
  private observedElements = new Set<HTMLImageElement>();
  private visibilityRatios = new Map<HTMLImageElement, number>();
  private pages: DetectedMangaPage[] = [];
  private scanTimer: number | null = null;
  private lastVisibleEl: HTMLImageElement | null = null;

  constructor(callbacks: ImageDetectorCallbacks, adapter?: ReaderAdapter) {
    this.callbacks = callbacks;
    this.adapter = adapter ?? resolveReaderAdapter(document);
  }

  getAdapterName(): string {
    return this.adapter.name;
  }

  getPages(): DetectedMangaPage[] {
    return this.pages;
  }

  getVisiblePage(): DetectedMangaPage | null {
    if (this.pages.length === 0) return null;

    // 1. Prefer element with highest IntersectionObserver ratio
    let bestEl: HTMLImageElement | null = null;
    let bestScore = -1;
    const vh = window.innerHeight || 900;

    for (const page of this.pages) {
      const rect = page.element.getBoundingClientRect();
      const visibleTop = Math.max(0, rect.top);
      const visibleBot = Math.min(vh, rect.bottom);
      const visibleH = Math.max(0, visibleBot - visibleTop);
      const ratio = this.visibilityRatios.get(page.element) ?? 0;
      const score = visibleH + ratio * 250;
      if (visibleH > 40 && score > bestScore) {
        bestScore = score;
        bestEl = page.element;
      }
    }

    if (bestEl) {
      return this.pages.find((p) => p.element === bestEl) ?? this.pages[0];
    }
    return this.pages[0];
  }

  start(): void {
    this.intersectionObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const img = entry.target as HTMLImageElement;
          this.visibilityRatios.set(img, entry.isIntersecting ? entry.intersectionRatio : 0);
        }
        this.emitVisibleIfChanged();
      },
      { threshold: [0, 0.15, 0.35, 0.6, 0.85] }
    );

    this.mutationObserver = new MutationObserver(() => {
      this.scheduleScan();
    });
    this.mutationObserver.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["src", "srcset", "data-src", "data-original", "data-lazy-src", "class", "style"],
    });

    window.addEventListener("load", this.handleGlobalTrigger, { passive: true });
    window.addEventListener("scroll", this.handleScroll, { passive: true });
    window.addEventListener("resize", this.handleGlobalTrigger, { passive: true });

    this.scanNow();
  }

  stop(): void {
    if (this.scanTimer !== null) {
      window.clearTimeout(this.scanTimer);
      this.scanTimer = null;
    }
    this.mutationObserver?.disconnect();
    this.intersectionObserver?.disconnect();
    this.observedElements.clear();
    this.visibilityRatios.clear();
    window.removeEventListener("load", this.handleGlobalTrigger);
    window.removeEventListener("scroll", this.handleScroll);
    window.removeEventListener("resize", this.handleGlobalTrigger);
  }

  private handleGlobalTrigger = (): void => {
    this.scheduleScan();
  };

  private handleScroll = (): void => {
    this.emitVisibleIfChanged();
  };

  scheduleScan(delayMs = 250): void {
    if (this.scanTimer !== null) return;
    this.scanTimer = window.setTimeout(() => {
      this.scanTimer = null;
      this.scanNow();
    }, delayMs);
  }

  scanNow(): void {
    const candidates = this.adapter.findCandidateImages(document);
    const nextPages: DetectedMangaPage[] = [];

    for (let i = 0; i < candidates.length; i++) {
      const el = candidates[i];
      const url = this.adapter.resolveImageUrl(el);
      if (!url) continue;
      if (!el.dataset.svOrigUrl) {
        el.dataset.svOrigUrl = url;
      }
      nextPages.push({ element: el, imageUrl: url, index: i });

      if (!this.observedElements.has(el)) {
        this.observedElements.add(el);
        this.intersectionObserver?.observe(el);
        el.addEventListener("load", this.handleGlobalTrigger, { passive: true });
      }
    }

    // Prune disconnected elements
    const currentSet = new Set(candidates);
    for (const prev of Array.from(this.observedElements)) {
      if (!currentSet.has(prev)) {
        this.intersectionObserver?.unobserve(prev);
        this.observedElements.delete(prev);
        this.visibilityRatios.delete(prev);
      }
    }

    this.pages = nextPages;
    this.callbacks.onPagesChanged(this.pages);
    this.emitVisibleIfChanged();
  }

  private emitVisibleIfChanged(): void {
    const visible = this.getVisiblePage();
    const visEl = visible?.element ?? null;
    if (visEl !== this.lastVisibleEl) {
      this.lastVisibleEl = visEl;
      this.callbacks.onVisiblePageChanged(visible, this.pages);
    }
  }
}
