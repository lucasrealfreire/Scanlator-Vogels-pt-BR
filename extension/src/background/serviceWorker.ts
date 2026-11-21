/**
 * Manifest V3 Background Service Worker:
 * - Starts/stops local 127.0.0.1 engine on demand via Chrome Native Messaging Host
 *   (and gracefully works when the user launches `start.bat` manually).
 * - Proxies backend HTTP calls so HTTPS manga sites never hit mixed-content/PNA blocks.
 * - Fetches CORS-restricted comic images using MV3 host_permissions with clear error reporting.
 */

import { loadSettings, saveSettings } from "../shared/storage";
import {
  NATIVE_HOST_NAME,
  type BackendHealth,
  type EngineActionResponse,
  type EngineLifecycleState,
  type RuntimeMessage,
  type TranslateBatchResponse,
  type TranslatePageError,
  type TranslatePageResponse,
} from "../shared/types";

let currentState: EngineLifecycleState = "stopped";
let lastHealth: BackendHealth | null = null;
let stateChangedAt = 0;

const NATIVE_START_TIMEOUT_MS = 20_000;
const NATIVE_STOP_TIMEOUT_MS = 12_000;

function backendBase(port: number): string {
  return `http://127.0.0.1:${port}`;
}

async function fetchHealth(port: number, timeoutMs = 1500): Promise<BackendHealth | null> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(`${backendBase(port)}/health`, {
      method: "GET",
      signal: ctrl.signal,
    });
    if (!resp.ok) return null;
    const data = (await resp.json()) as BackendHealth;
    return data;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function sendNativeMessage(
  payload: Record<string, unknown>,
  timeoutMs = NATIVE_START_TIMEOUT_MS
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(
          new Error(
            `Timeout (${Math.round(timeoutMs / 1000)}s) aguardando resposta do Native Host. ` +
              "Verifique native_host/host.log e native_host/backend.log."
          )
        );
      }
    }, timeoutMs);

    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST_NAME, payload, (response) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        const err = chrome.runtime.lastError;
        if (err) {
          reject(new Error(err.message || "Native Messaging Host nao registrado."));
          return;
        }
        resolve((response ?? {}) as Record<string, unknown>);
      });
    } catch (err) {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    }
  });
}

async function broadcastEngineState(
  state: EngineLifecycleState,
  health: BackendHealth | null
): Promise<void> {
  currentState = state;
  lastHealth = health;
  stateChangedAt = Date.now();
  try {
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      if (tab.id !== undefined) {
        chrome.tabs
          .sendMessage(tab.id, {
            type: "ENGINE_STATE_BROADCAST",
            state,
            health,
          } satisfies RuntimeMessage)
          .catch(() => {
            /* ignore tabs without content script */
          });
      }
    }
  } catch {
    /* ignore */
  }
}

async function handleEngineStatus(): Promise<EngineActionResponse> {
  const settings = await loadSettings();
  const health = await fetchHealth(settings.backendPort);
  if (health) {
    currentState = "running";
    lastHealth = health;
    return { ok: true, state: "running", health };
  }
  // Never stay permanently stuck in "starting" if popup is reopened after a timeout/crash
  if (currentState === "starting" && Date.now() - stateChangedAt < NATIVE_START_TIMEOUT_MS + 2000) {
    return { ok: true, state: "starting", health: null };
  }
  currentState = "stopped";
  lastHealth = null;
  return { ok: true, state: "stopped", health: null };
}

async function handleEngineStart(): Promise<EngineActionResponse> {
  const settings = await loadSettings();
  const existing = await fetchHealth(settings.backendPort);
  if (existing) {
    await broadcastEngineState("running", existing);
    return { ok: true, state: "running", health: existing, nativeHostAvailable: true };
  }

  await broadcastEngineState("starting", null);
  try {
    const resp = await sendNativeMessage(
      {
        action: "start",
        port: settings.backendPort,
        idle_timeout_sec: settings.idleTimeoutSec,
      },
      NATIVE_START_TIMEOUT_MS
    );

    if (resp.ok) {
      const health =
        (resp.health as BackendHealth | undefined) ??
        (await fetchHealth(settings.backendPort, 3000));
      if (health) {
        await broadcastEngineState("running", health);
        return { ok: true, state: "running", health, nativeHostAvailable: true };
      }
    }

    // Even if Native Host returned late/error, double-check /health once before declaring failure
    const fallbackHealth = await fetchHealth(settings.backendPort, 1500);
    if (fallbackHealth) {
      await broadcastEngineState("running", fallbackHealth);
      return { ok: true, state: "running", health: fallbackHealth, nativeHostAvailable: true };
    }

    await broadcastEngineState("stopped", null);
    return {
      ok: false,
      state: "stopped",
      health: null,
      nativeHostAvailable: true,
      message: String(
        resp.message ||
          `Motor nao respondeu em http://127.0.0.1:${settings.backendPort}/health. Verifique native_host/backend.log.`
      ),
    };
  } catch (err) {
    const manualHealth = await fetchHealth(settings.backendPort, 1500);
    if (manualHealth) {
      await broadcastEngineState("running", manualHealth);
      return { ok: true, state: "running", health: manualHealth, nativeHostAvailable: false };
    }
    await broadcastEngineState("stopped", null);
    const reason = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      state: "stopped",
      health: null,
      nativeHostAvailable: false,
      message: `${reason} (ou execute start.bat manualmente).`,
    };
  }
}

async function handleEngineStop(): Promise<EngineActionResponse> {
  const settings = await loadSettings();
  await broadcastEngineState("stopping", lastHealth);

  try {
    await fetch(`${backendBase(settings.backendPort)}/shutdown`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch {
    try {
      await sendNativeMessage({ action: "stop", port: settings.backendPort }, NATIVE_STOP_TIMEOUT_MS);
    } catch {
      /* ignore */
    }
  }

  // Wait briefly until port closes so VRAM release is confirmed
  for (let i = 0; i < 16; i++) {
    await new Promise((r) => setTimeout(r, 250));
    const check = await fetchHealth(settings.backendPort, 500);
    if (!check) break;
  }

  await broadcastEngineState("stopped", null);
  return { ok: true, state: "stopped", health: null };
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const slice = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...slice);
  }
  return btoa(binary);
}

async function fetchImageAsBase64(imageUrl: string, pageUrl: string): Promise<string> {
  if (imageUrl.startsWith("data:")) {
    const commaIdx = imageUrl.indexOf(",");
    if (commaIdx >= 0) return imageUrl.slice(commaIdx + 1);
  }

  // Attempt fetch with credentials & referrer (works across most CDN/hotlink-protected readers)
  const resp = await fetch(imageUrl, {
    method: "GET",
    credentials: "include",
    referrer: pageUrl || undefined,
    referrerPolicy: "no-referrer-when-downgrade",
  });

  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status} ao baixar imagem do site.`);
  }
  const contentType = (resp.headers.get("content-type") || "").toLowerCase();
  if (contentType && !contentType.startsWith("image/") && !contentType.includes("octet-stream")) {
    throw new Error(
      `Site retornou '${contentType}' em vez de imagem (possivel bloqueio anti-hotlink ou desafio do site).`
    );
  }
  const buf = await resp.arrayBuffer();
  if (buf.byteLength < 512) {
    throw new Error("Imagem retornada esta vazia ou bloqueada pelo site.");
  }
  return arrayBufferToBase64(buf);
}

function mapBackendErrorResponse(
  status: number,
  data: Record<string, unknown>
): TranslatePageError {
  const code = String(data.code || "UNKNOWN_ERROR");
  return {
    ok: false,
    httpStatus: status,
    code:
      code === "CIRCUIT_BREAKER_OPEN" ||
      code === "RATE_LIMITED_429" ||
      code === "UPSTREAM_UNAVAILABLE" ||
      code === "MISSING_API_KEY" ||
      code === "PAID_MODEL_BLOCKED" ||
      code === "INVALID_IMAGE"
        ? code
        : status === 429
          ? "RATE_LIMITED_429"
          : status === 503
            ? "UPSTREAM_UNAVAILABLE"
            : "UNKNOWN_ERROR",
    message: String(data.message || `Falha HTTP ${status} no motor local.`),
    retry_after_sec: typeof data.retry_after_sec === "number" ? data.retry_after_sec : undefined,
    circuit_breaker: data.circuit_breaker as TranslatePageError["circuit_breaker"],
  };
}

async function handleTranslateImage(
  imageUrl: string,
  pageUrl: string,
  providedBase64: string | undefined,
  forceRetranslate: boolean
): Promise<TranslatePageResponse> {
  const settings = await loadSettings();
  const base = backendBase(settings.backendPort);

  let imageBase64 = providedBase64;
  if (!imageBase64) {
    try {
      imageBase64 = await fetchImageAsBase64(imageUrl, pageUrl);
    } catch (err) {
      return {
        ok: false,
        code: "CORS_OR_NETWORK_BLOCKED",
        message:
          `Este site bloqueou o acesso direto a imagem (${err instanceof Error ? err.message : String(err)}).`,
      };
    }
  }

  try {
    const resp = await fetch(`${base}/translate-page`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: imageBase64,
        model: settings.model,
        source_lang: settings.sourceLang,
        style: settings.style,
        uppercase: settings.uppercase,
        output_format: "webp",
        force_retranslate: forceRetranslate,
        use_cache: true,
        api_key: settings.apiKey || undefined,
        idle_timeout_sec: settings.idleTimeoutSec,
      }),
    });

    const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
    if (!resp.ok) {
      return mapBackendErrorResponse(resp.status, data);
    }

    return data as unknown as TranslatePageResponse;
  } catch {
    await broadcastEngineState("stopped", null);
    return {
      ok: false,
      code: "ENGINE_OFFLINE",
      message: "Motor local desligado. Clique em 'Ligar Motor' para traduzir.",
    };
  }
}

async function handleTranslateBatch(
  pageUrl: string,
  items: Array<{
    pageId: string;
    imageUrl: string;
    imageBase64?: string;
    forceRetranslate?: boolean;
  }>
): Promise<TranslateBatchResponse> {
  const settings = await loadSettings();
  const base = backendBase(settings.backendPort);

  const skippedErrors: Record<string, string> = {};
  const prepared = await Promise.all(
    items.slice(0, 4).map(async (item) => {
      if (item.imageBase64) {
        return {
          page_id: item.pageId,
          image_base64: item.imageBase64,
          force_retranslate: Boolean(item.forceRetranslate),
        };
      }
      try {
        const b64 = await fetchImageAsBase64(item.imageUrl, pageUrl);
        return {
          page_id: item.pageId,
          image_base64: b64,
          force_retranslate: Boolean(item.forceRetranslate),
        };
      } catch (err) {
        skippedErrors[item.pageId] =
          `Acesso bloqueado a imagem (${err instanceof Error ? err.message : String(err)}).`;
        return null;
      }
    })
  );

  const readyPages = prepared.filter((p): p is NonNullable<typeof p> => p !== null);
  if (readyPages.length === 0) {
    const firstErr = Object.values(skippedErrors)[0] || "Este site bloqueou o acesso as imagens.";
    return {
      ok: false,
      code: "CORS_OR_NETWORK_BLOCKED",
      message: firstErr,
    };
  }

  try {
    const resp = await fetch(`${base}/translate-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pages: readyPages,
        model: settings.model,
        source_lang: settings.sourceLang,
        style: settings.style,
        uppercase: settings.uppercase,
        output_format: "webp",
        use_cache: true,
        api_key: settings.apiKey || undefined,
        idle_timeout_sec: settings.idleTimeoutSec,
      }),
    });

    const data = (await resp.json().catch(() => ({}))) as Record<string, unknown>;
    if (!resp.ok) {
      return mapBackendErrorResponse(resp.status, data);
    }

    const result = data as unknown as TranslateBatchResponse;
    if (result.ok && Object.keys(skippedErrors).length > 0) {
      result.skipped_errors = skippedErrors;
    }
    return result;
  } catch {
    await broadcastEngineState("stopped", null);
    return {
      ok: false,
      code: "ENGINE_OFFLINE",
      message: "Motor local desligado. Clique em 'Ligar Motor' para traduzir.",
    };
  }
}

async function handlePrepareImage(
  imageUrl: string,
  pageUrl: string,
  providedBase64: string | undefined
): Promise<Record<string, unknown>> {
  const settings = await loadSettings();
  const base = backendBase(settings.backendPort);

  let imageBase64 = providedBase64;
  if (!imageBase64) {
    try {
      imageBase64 = await fetchImageAsBase64(imageUrl, pageUrl);
    } catch {
      return { ok: false, skipped: true };
    }
  }

  try {
    const resp = await fetch(`${base}/prepare-page`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: imageBase64,
        source_lang: settings.sourceLang,
        use_cache: true,
      }),
    });
    if (!resp.ok) return { ok: false };
    return (await resp.json()) as Record<string, unknown>;
  } catch {
    return { ok: false };
  }
}

async function handleResetCircuitBreaker(): Promise<Record<string, unknown>> {
  const settings = await loadSettings();
  try {
    const resp = await fetch(`${backendBase(settings.backendPort)}/circuit-breaker/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = (await resp.json()) as Record<string, unknown>;
    const health = await fetchHealth(settings.backendPort);
    if (health) await broadcastEngineState("running", health);
    return data;
  } catch {
    return { ok: false, message: "Motor local inacessivel." };
  }
}

async function handleUpdateIdleTimeout(idleTimeoutSec: number): Promise<Record<string, unknown>> {
  const settings = await saveSettings({ idleTimeoutSec });
  try {
    const resp = await fetch(`${backendBase(settings.backendPort)}/config/idle-timeout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idle_timeout_sec: settings.idleTimeoutSec }),
    });
    return (await resp.json()) as Record<string, unknown>;
  } catch {
    return { ok: true, saved_only: true };
  }
}

chrome.runtime.onMessage.addListener((message: RuntimeMessage, _sender, sendResponse) => {
  (async () => {
    switch (message.type) {
      case "GET_SETTINGS": {
        sendResponse(await loadSettings());
        break;
      }
      case "SAVE_SETTINGS": {
        const updated = await saveSettings(message.settings);
        if (typeof message.settings.idleTimeoutSec === "number") {
          await handleUpdateIdleTimeout(updated.idleTimeoutSec);
        }
        // Broadcast updated settings to all tabs
        const tabs = await chrome.tabs.query({});
        for (const t of tabs) {
          if (t.id !== undefined) {
            chrome.tabs
              .sendMessage(t.id, {
                type: "SETTINGS_UPDATED",
                settings: updated,
              } satisfies RuntimeMessage)
              .catch(() => {});
          }
        }
        sendResponse(updated);
        break;
      }
      case "ENGINE_STATUS": {
        sendResponse(await handleEngineStatus());
        break;
      }
      case "ENGINE_START": {
        sendResponse(await handleEngineStart());
        break;
      }
      case "ENGINE_STOP": {
        sendResponse(await handleEngineStop());
        break;
      }
      case "RESET_CIRCUIT_BREAKER": {
        sendResponse(await handleResetCircuitBreaker());
        break;
      }
      case "UPDATE_IDLE_TIMEOUT": {
        sendResponse(await handleUpdateIdleTimeout(message.idleTimeoutSec));
        break;
      }
      case "TRANSLATE_IMAGE_URL": {
        sendResponse(
          await handleTranslateImage(
            message.imageUrl,
            message.pageUrl,
            message.imageBase64,
            Boolean(message.forceRetranslate)
          )
        );
        break;
      }
      case "TRANSLATE_BATCH_URLS": {
        sendResponse(await handleTranslateBatch(message.pageUrl, message.items));
        break;
      }
      case "PREPARE_IMAGE_URL": {
        sendResponse(
          await handlePrepareImage(
            message.imageUrl,
            message.pageUrl,
            message.imageBase64
          )
        );
        break;
      }
      default:
        sendResponse({ ok: false, message: "Mensagem nao suportada." });
    }
  })().catch((err) => {
    sendResponse({ ok: false, message: err instanceof Error ? err.message : String(err) });
  });
  return true; // Keep message channel open for async response
});
