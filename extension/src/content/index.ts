/**
 * Universal Content Script Entry Point for Scanlator Vogels PT-BR (Manifest V3).
 * Features:
 * - Hybrid smart queue: visible/manual page runs immediately via `/translate-page`, while
 *   Auto Translate prefetch (3–4 pages) and remaining chapter pages group into batches of up
 *   to 4 pages via `POST /translate-batch` (1 Gemini Free Tier call per batch!).
 * - Pipelined local GPU prefetch: prepares upcoming page(s) locally (`POST /prepare-page`)
 *   while the current page/batch waits on Gemini (strict concurrency = 1 on Gemini API).
 * - Compact WebP canvas capture & IndexedDB cache keyed by PROMPT_VERSION/lang/model/style/case.
 * - Keyboard shortcuts: `Alt+T` (translate visible page), `Alt+Shift+T` (toggle Auto Translate).
 */

import {
  DEFAULT_SETTINGS,
  type BackendHealth,
  type EngineActionResponse,
  type EngineLifecycleState,
  type ExtensionSettings,
  type RuntimeMessage,
  type SourceLanguage,
  type TranslateBatchResponse,
  type TranslatePageError,
  type TranslatePageResponse,
} from "../shared/types";
import type { DetectedMangaPage } from "./adapters/types";
import { CacheManager, buildCacheKey } from "./cacheManager";
import { FloatingPanel } from "./floatingPanel";
import { ImageDetector } from "./imageDetector";
import { OverlayManager } from "./overlayManager";
import { TranslationQueue, type QueueTask } from "./translationQueue";

if (!(window as unknown as { __svPtBrLoaded?: boolean }).__svPtBrLoaded) {
  (window as unknown as { __svPtBrLoaded?: boolean }).__svPtBrLoaded = true;

  let settings: ExtensionSettings = { ...DEFAULT_SETTINGS };
  let engineState: EngineLifecycleState = "stopped";
  let lastHealth: BackendHealth | null = null;
  const preparedUrls = new Set<string>();

  const cache = new CacheManager();

  function base64ToBlob(b64: string, mime = "image/webp"): Blob {
    const bin = atob(b64);
    const len = bin.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = bin.charCodeAt(i);
    }
    return new Blob([bytes], { type: mime });
  }

  function tryCaptureCanvasBase64(img: HTMLImageElement): string | undefined {
    try {
      if (!img.complete || img.naturalWidth < 100 || img.naturalHeight < 100) return undefined;
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth;
      canvas.height = img.naturalHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) return undefined;
      ctx.drawImage(img, 0, 0);
      // Lightweight WebP (~8x smaller than PNG) with PNG fallback
      let dataUrl = canvas.toDataURL("image/webp", 0.92);
      if (!dataUrl.startsWith("data:image/webp")) {
        dataUrl = canvas.toDataURL("image/png");
      }
      const comma = dataUrl.indexOf(",");
      return comma >= 0 ? dataUrl.slice(comma + 1) : undefined;
    } catch {
      return undefined;
    }
  }

  const overlay = new OverlayManager({
    onRetryRequested: (img) => {
      const page = detector.getPages().find((p) => p.element === img);
      if (page) {
        panel.clearAlert();
        queue.enqueue(page, 100, true);
      }
    },
    onManualTranslateRequested: (img) => {
      const page = detector.getPages().find((p) => p.element === img);
      if (page) {
        panel.clearAlert();
        queue.enqueue(page, 100, false);
      }
    },
  });

  const queue = new TranslationQueue(
    async (task, nextTasks) => {
      await processSingleTask(task, nextTasks);
    },
    async (batchTasks, nextTasks) => {
      await processBatchTasks(batchTasks, nextTasks);
    },
    (pending, activeCount, chapterActive) => {
      const active = activeCount > 0;
      const queuedAdjusted = active ? pending + Math.max(0, activeCount - 1) : pending;
      panel.updateProgress(
        overlay.getTranslatedCount(),
        detector.getPages().length,
        queuedAdjusted,
        active,
        chapterActive
      );
    },
    4
  );

  function sendEngineMessageWithTimeout(
    msg: RuntimeMessage,
    timeoutMs = 24_000
  ): Promise<EngineActionResponse> {
    return new Promise((resolve) => {
      let done = false;
      const timer = window.setTimeout(() => {
        if (!done) {
          done = true;
          resolve({
            ok: false,
            state: "stopped",
            health: null,
            message: `Timeout (${Math.round(timeoutMs / 1000)}s) aguardando resposta do motor local.`,
          });
        }
      }, timeoutMs);

      chrome.runtime
        .sendMessage(msg)
        .then((res) => {
          if (!done) {
            done = true;
            window.clearTimeout(timer);
            resolve(res as EngineActionResponse);
          }
        })
        .catch((err) => {
          if (!done) {
            done = true;
            window.clearTimeout(timer);
            resolve({
              ok: false,
              state: "stopped",
              health: null,
              message: err instanceof Error ? err.message : String(err),
            });
          }
        });
    });
  }

  const panel = new FloatingPanel({
    onToggleEngine: async () => {
      if (engineState === "running") {
        queue.cancelAll();
        preparedUrls.clear();
        panel.updateEngineState("stopping", lastHealth);
        const resp = await sendEngineMessageWithTimeout({ type: "ENGINE_STOP" }, 15_000);
        engineState = resp.state;
        lastHealth = resp.health;
        panel.updateEngineState(engineState, lastHealth);
      } else {
        panel.clearAlert();
        panel.updateEngineState("starting", null);
        const resp = await sendEngineMessageWithTimeout({ type: "ENGINE_START" }, 24_000);
        engineState = resp.state;
        lastHealth = resp.health;
        panel.updateEngineState(engineState, lastHealth);
        if (!resp.ok) {
          panel.showAlert({
            kind: "error",
            title: "Motor local não iniciou",
            detail: resp.message || "Falha desconhecida ao iniciar motor em 127.0.0.1.",
          });
        } else if (engineState === "running") {
          queue.resume();
          scheduleAutoForVisible();
        }
      }
    },
    onTranslateVisible: () => {
      void translateVisibleNow();
    },
    onToggleChapter: () => {
      if (queue.isChapterActive()) {
        queue.pauseAndClearPrefetch();
        panel.showToast("Tradução do capítulo pausada.");
      } else {
        void translateWholeChapter();
      }
    },
    onToggleAutoTranslate: async (enabled) => {
      await setAutoTranslate(enabled);
    },
    onChangeSourceLang: async (lang: SourceLanguage) => {
      settings = (await chrome.runtime.sendMessage({
        type: "SAVE_SETTINGS",
        settings: { sourceLang: lang },
      } satisfies RuntimeMessage)) as ExtensionSettings;
      preparedUrls.clear();
      panel.updateSettings(settings);
      panel.showToast(`Idioma de origem: ${lang.toUpperCase()}`);
    },
    onResumeCircuit: async () => {
      await chrome.runtime.sendMessage({
        type: "RESET_CIRCUIT_BREAKER",
      } satisfies RuntimeMessage);
      panel.clearAlert();
      queue.resume();
      void translateVisibleNow();
    },
  });

  const detector = new ImageDetector({
    onPagesChanged: (pages) => {
      panel.updateProgress(
        overlay.getTranslatedCount(),
        pages.length,
        queue.getPendingCount(),
        false,
        queue.isChapterActive()
      );
      void hydrateFromCache(pages);
      if (settings.autoTranslate && engineState === "running") {
        scheduleAutoForVisible();
      }
    },
    onVisiblePageChanged: () => {
      if (settings.autoTranslate && engineState === "running") {
        scheduleAutoForVisible();
      }
    },
  });

  async function setAutoTranslate(enabled: boolean): Promise<void> {
    settings = (await chrome.runtime.sendMessage({
      type: "SAVE_SETTINGS",
      settings: { autoTranslate: enabled },
    } satisfies RuntimeMessage)) as ExtensionSettings;
    panel.updateSettings(settings);
    if (enabled) {
      queue.resume();
      scheduleAutoForVisible();
      panel.showToast("Auto Translate ATIVADO (lote de 4 págs)");
    } else {
      queue.pauseAndClearPrefetch();
      panel.showToast("Auto Translate DESATIVADO");
    }
  }

  async function ensureEngineRunning(): Promise<boolean> {
    if (engineState === "running") return true;
    const st = (await chrome.runtime.sendMessage({
      type: "ENGINE_STATUS",
    } satisfies RuntimeMessage)) as EngineActionResponse;
    engineState = st.state;
    lastHealth = st.health;
    panel.updateEngineState(engineState, lastHealth);
    if (engineState === "running") return true;

    panel.showAlert({
      kind: "info",
      title: "Motor local desligado",
      detail: "Clique em 'Ligar Motor' (ou execute start.bat) para iniciar a tradução local.",
    });
    panel.setExpanded(true);
    return false;
  }

  async function hydrateFromCache(pages: DetectedMangaPage[]): Promise<void> {
    for (const p of pages) {
      if (overlay.getState(p.element) !== "idle") continue;
      const key = buildCacheKey(p.imageUrl, settings);
      const cached = await cache.get(key);
      if (!cached) continue;
      if (cached.noText) {
        overlay.markNoText(p.element);
      } else if (cached.blob) {
        overlay.applyTranslatedBlob(p.element, cached.blob, cached.blocksCount);
      }
    }
    panel.updateProgress(
      overlay.getTranslatedCount(),
      pages.length,
      queue.getPendingCount(),
      false,
      queue.isChapterActive()
    );
  }

  function scheduleAutoForVisible(): void {
    if (!settings.autoTranslate || engineState !== "running" || queue.isPaused()) return;
    const allPages = detector.getPages();
    const visible = detector.getVisiblePage();
    if (!visible) return;

    // Visible page gets fast single-page lane (priority 50)
    if (overlay.getState(visible.element) === "idle") {
      queue.enqueue(visible, 50, false);
    }

    // Next 3–4 pages share batch priority (10) so the queue groups them in 1 batch call!
    const prefetchLimit = Math.max(3, Math.min(4, settings.prefetchCount || 4));
    const startIdx = visible.index;
    const limit = Math.min(allPages.length, startIdx + 1 + prefetchLimit);
    for (let i = startIdx + 1; i < limit; i++) {
      const nextPage = allPages[i];
      if (overlay.getState(nextPage.element) === "idle") {
        queue.enqueue(nextPage, 10, false);
      }
    }
  }

  async function translateVisibleNow(): Promise<void> {
    detector.scanNow();
    const visible = detector.getVisiblePage();
    if (!visible) {
      panel.showAlert({
        kind: "info",
        title: "Nenhuma página de mangá detectada",
        detail: "Role até a imagem do capítulo e tente novamente.",
      });
      return;
    }
    if (!(await ensureEngineRunning())) return;
    panel.clearAlert();
    queue.enqueue(visible, 100, false);
  }

  async function translateWholeChapter(): Promise<void> {
    detector.scanNow();
    const allPages = detector.getPages();
    if (allPages.length === 0) {
      panel.showAlert({
        kind: "info",
        title: "Nenhuma página detectada neste capítulo",
        detail: "Aguarde as imagens do capítulo carregarem e tente novamente.",
      });
      return;
    }
    if (!(await ensureEngineRunning())) return;
    panel.clearAlert();
    const pending = allPages.filter((p) => {
      const st = overlay.getState(p.element);
      return st === "idle" || st === "error";
    });
    if (pending.length === 0) {
      panel.showToast("Todas as páginas já estão traduzidas!");
      return;
    }
    // Current visible page goes first as fast single-page; remaining pages run in 4-page batches!
    const visible = detector.getVisiblePage();
    queue.enqueueChapter(pending, visible);
    panel.showToast(`Traduzindo capítulo (${pending.length} págs em lotes de 4)...`);
  }

  function triggerPipelinedPrepare(nextPage: DetectedMangaPage | null): void {
    if (!nextPage || queue.isPaused() || engineState !== "running") return;
    if (lastHealth?.circuit_breaker?.state === "OPEN") return;
    if (!nextPage.element.isConnected) return;
    if (overlay.getState(nextPage.element) !== "idle") return;

    const prepKey = `${nextPage.imageUrl}::${settings.sourceLang}`;
    if (preparedUrls.has(prepKey)) return;
    preparedUrls.add(prepKey);

    // Fire-and-forget local GPU prep (detect+OCR+inpaint) while current page/batch waits on Gemini!
    const canvasB64 = tryCaptureCanvasBase64(nextPage.element);
    void chrome.runtime
      .sendMessage({
        type: "PREPARE_IMAGE_URL",
        imageUrl: nextPage.imageUrl,
        pageUrl: window.location.href,
        imageBase64: canvasB64,
      } satisfies RuntimeMessage)
      .catch(() => {
        preparedUrls.delete(prepKey);
      });
  }

  function handleTranslationError(resp: TranslatePageError): void {
    if (
      resp.code === "CIRCUIT_BREAKER_OPEN" ||
      resp.code === "RATE_LIMITED_429" ||
      resp.code === "UPSTREAM_UNAVAILABLE"
    ) {
      queue.pauseAndClearPrefetch();
      panel.showAlert({
        kind: "circuit_open",
        title:
          resp.code === "CIRCUIT_BREAKER_OPEN"
            ? "Circuito aberto (cota / instabilidade Gemini)"
            : resp.code === "RATE_LIMITED_429"
              ? "Limite Free Tier atingido (429)"
              : "Gemini temporariamente indisponível (503)",
        detail:
          "Prefetch e capítulo pausados; páginas já traduzidas foram preservadas. " + resp.message,
        retryAfterSec: resp.retry_after_sec ?? resp.circuit_breaker?.retry_after_sec ?? 30,
        showResumeButton: true,
      });
    } else if (resp.code === "CORS_OR_NETWORK_BLOCKED") {
      panel.showAlert({
        kind: "cors",
        title: "Acesso à imagem bloqueado pelo site",
        detail: resp.message,
      });
    } else if (resp.code === "ENGINE_OFFLINE") {
      queue.cancelAll();
      engineState = "stopped";
      lastHealth = null;
      panel.updateEngineState("stopped", null);
      panel.showAlert({
        kind: "info",
        title: "Motor local desligado",
        detail: resp.message,
      });
    } else {
      panel.showAlert({
        kind: "error",
        title: `Erro (${resp.code})`,
        detail: resp.message,
      });
    }
  }

  async function refreshEngineTelemetry(): Promise<void> {
    try {
      const st = (await chrome.runtime.sendMessage({
        type: "ENGINE_STATUS",
      } satisfies RuntimeMessage)) as EngineActionResponse;
      engineState = st.state;
      lastHealth = st.health;
      panel.updateEngineState(engineState, lastHealth);
    } catch {
      /* ignore transient telemetry error */
    }
  }

  async function processSingleTask(task: QueueTask, nextTasks: QueueTask[]): Promise<void> {
    const { page, forceRetranslate } = task;
    if (!page.element.isConnected) return;

    const cacheKey = buildCacheKey(page.imageUrl, settings);
    if (!forceRetranslate) {
      const cached = await cache.get(cacheKey);
      if (cached) {
        if (cached.noText) {
          overlay.markNoText(page.element);
        } else if (cached.blob) {
          overlay.applyTranslatedBlob(page.element, cached.blob, cached.blocksCount);
        }
        return;
      }
    } else {
      await cache.delete(cacheKey);
    }

    overlay.setLoading(page.element);

    // Pipeline local GPU prep for the next upcoming page while current page waits on Gemini!
    if (nextTasks.length > 0) {
      triggerPipelinedPrepare(nextTasks[0].page);
    } else if (settings.autoTranslate) {
      const allPages = detector.getPages();
      const candidateNext = allPages[page.index + 1] ?? null;
      triggerPipelinedPrepare(candidateNext);
    }

    const canvasB64 = tryCaptureCanvasBase64(page.element);
    const resp = (await chrome.runtime.sendMessage({
      type: "TRANSLATE_IMAGE_URL",
      imageUrl: page.imageUrl,
      pageUrl: window.location.href,
      imageBase64: canvasB64,
      forceRetranslate,
    } satisfies RuntimeMessage)) as TranslatePageResponse;

    if (!resp.ok) {
      overlay.setError(page.element, resp.message);
      handleTranslationError(resp);
      return;
    }

    if (resp.no_text) {
      overlay.markNoText(page.element);
      await cache.put({
        key: cacheKey,
        imageUrl: page.imageUrl,
        blob: null,
        noText: true,
        blocksCount: 0,
        model: resp.model,
        style: resp.style,
        uppercase: resp.uppercase,
        createdAt: Date.now(),
      });
    } else {
      const blob = base64ToBlob(resp.image_base64, resp.mime_type || "image/webp");
      overlay.applyTranslatedBlob(page.element, blob, resp.blocks_count);
      await cache.put({
        key: cacheKey,
        imageUrl: page.imageUrl,
        blob,
        noText: false,
        blocksCount: resp.blocks_count,
        model: resp.model,
        style: resp.style,
        uppercase: resp.uppercase,
        createdAt: Date.now(),
      });
    }

    await refreshEngineTelemetry();
  }

  async function processBatchTasks(tasks: QueueTask[], nextTasks: QueueTask[]): Promise<void> {
    const uncached: Array<{ task: QueueTask; pageId: string; cacheKey: string }> = [];

    for (let i = 0; i < tasks.length; i++) {
      const task = tasks[i];
      if (!task.page.element.isConnected) continue;
      const cacheKey = buildCacheKey(task.page.imageUrl, settings);
      if (!task.forceRetranslate) {
        const cached = await cache.get(cacheKey);
        if (cached) {
          if (cached.noText) {
            overlay.markNoText(task.page.element);
          } else if (cached.blob) {
            overlay.applyTranslatedBlob(task.page.element, cached.blob, cached.blocksCount);
          }
          continue;
        }
      } else {
        await cache.delete(cacheKey);
      }
      uncached.push({ task, pageId: `p${i}`, cacheKey });
    }

    if (uncached.length === 0) return;
    if (uncached.length === 1) {
      await processSingleTask(uncached[0].task, nextTasks);
      return;
    }

    for (const entry of uncached) {
      overlay.setLoading(entry.task.page.element);
    }

    // Pipeline local GPU prep for the first page of the NEXT batch while this batch awaits Gemini
    if (nextTasks.length > 0) {
      triggerPipelinedPrepare(nextTasks[0].page);
    }

    const byId = new Map(uncached.map((u) => [u.pageId, u]));
    const items = uncached.map((u) => ({
      pageId: u.pageId,
      imageUrl: u.task.page.imageUrl,
      imageBase64: tryCaptureCanvasBase64(u.task.page.element),
      forceRetranslate: u.task.forceRetranslate,
    }));

    const resp = (await chrome.runtime.sendMessage({
      type: "TRANSLATE_BATCH_URLS",
      pageUrl: window.location.href,
      items,
    } satisfies RuntimeMessage)) as TranslateBatchResponse;

    if (!resp.ok) {
      for (const u of uncached) {
        overlay.setError(u.task.page.element, resp.message);
      }
      handleTranslationError(resp);
      return;
    }

    if (resp.skipped_errors) {
      for (const [pid, errMsg] of Object.entries(resp.skipped_errors)) {
        const entry = byId.get(pid);
        if (entry) {
          overlay.setError(entry.task.page.element, errMsg);
        }
      }
    }

    for (const p of resp.pages) {
      const entry = byId.get(p.page_id);
      if (!entry || !entry.task.page.element.isConnected) continue;

      if (p.no_text) {
        overlay.markNoText(entry.task.page.element);
        await cache.put({
          key: entry.cacheKey,
          imageUrl: entry.task.page.imageUrl,
          blob: null,
          noText: true,
          blocksCount: 0,
          model: resp.model,
          style: resp.style,
          uppercase: resp.uppercase,
          createdAt: Date.now(),
        });
      } else {
        const blob = base64ToBlob(p.image_base64, p.mime_type || "image/webp");
        overlay.applyTranslatedBlob(entry.task.page.element, blob, p.blocks_count);
        await cache.put({
          key: entry.cacheKey,
          imageUrl: entry.task.page.imageUrl,
          blob,
          noText: false,
          blocksCount: p.blocks_count,
          model: resp.model,
          style: resp.style,
          uppercase: resp.uppercase,
          createdAt: Date.now(),
        });
      }
    }

    await refreshEngineTelemetry();
  }

  // Global keyboard shortcuts: Alt+T (translate current page), Alt+Shift+T (toggle Auto Translate)
  window.addEventListener("keydown", (e: KeyboardEvent) => {
    const target = e.target as HTMLElement | null;
    if (
      target &&
      (target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable)
    ) {
      return;
    }
    if (e.altKey && !e.ctrlKey && !e.metaKey && e.key.toLowerCase() === "t") {
      e.preventDefault();
      if (e.shiftKey) {
        void setAutoTranslate(!settings.autoTranslate);
      } else {
        void translateVisibleNow();
      }
    }
  });

  chrome.runtime.onMessage.addListener((message: RuntimeMessage, _sender, sendResponse) => {
    if (message.type === "SETTINGS_UPDATED") {
      const langChanged = message.settings.sourceLang !== settings.sourceLang;
      settings = message.settings;
      if (langChanged) preparedUrls.clear();
      panel.updateSettings(settings);
      if (!settings.autoTranslate) {
        queue.pauseAndClearPrefetch();
      } else if (engineState === "running") {
        queue.resume();
        scheduleAutoForVisible();
      }
      sendResponse({ ok: true });
    } else if (message.type === "ENGINE_STATE_BROADCAST") {
      engineState = message.state;
      lastHealth = message.health;
      panel.updateEngineState(engineState, lastHealth);
      if (engineState === "stopped" || engineState === "stopping") {
        queue.cancelAll();
        preparedUrls.clear();
      }
      sendResponse({ ok: true });
    } else if (message.type === "CLEAR_TAB_CACHE") {
      void (async () => {
        await cache.clearAll();
        preparedUrls.clear();
        overlay.restoreAllOriginals();
        sendResponse({ ok: true });
      })();
      return true;
    } else if (message.type === "TRANSLATE_VISIBLE_PAGE") {
      void translateVisibleNow();
      sendResponse({ ok: true });
    } else if (message.type === "TRANSLATE_CHAPTER") {
      void translateWholeChapter();
      sendResponse({ ok: true });
    } else if (message.type === "CANCEL_CHAPTER") {
      queue.pauseAndClearPrefetch();
      sendResponse({ ok: true });
    }
    return false;
  });

  void (async () => {
    try {
      settings = (await chrome.runtime.sendMessage({
        type: "GET_SETTINGS",
      } satisfies RuntimeMessage)) as ExtensionSettings;
      panel.updateSettings(settings);

      const st = (await chrome.runtime.sendMessage({
        type: "ENGINE_STATUS",
      } satisfies RuntimeMessage)) as EngineActionResponse;
      engineState = st.state;
      lastHealth = st.health;
      panel.updateEngineState(engineState, lastHealth);
    } catch {
      /* ignore lazy service worker wake */
    }
    detector.start();
  })();
}
