const y = "scanlator_vogels_glossaries_v1";
function _(e) {
  return decodeURIComponent(e || "").toLowerCase().replace(/[^a-z0-9\u00c0-\u024f\u3040-\u9fff\uac00-\ud7af]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 72) || "obra-padrao";
}
function k(e) {
  return e.split(/[-_]+/).filter(Boolean).map((t) => t.charAt(0).toUpperCase() + t.slice(1)).join(" ");
}
function D() {
  return { series: {}, urlOverrides: {} };
}
function O(e) {
  if (!e || typeof e != "object") return D();
  const t = e, a = {};
  if (t.series && typeof t.series == "object")
    for (const [o, n] of Object.entries(t.series)) {
      if (!n || typeof n != "object") continue;
      const i = _(n.slug || o), l = {};
      if (n.terms && typeof n.terms == "object")
        for (const [s, c] of Object.entries(n.terms)) {
          const d = String(s).trim(), S = String(c).trim();
          d && S && (l[d] = S);
        }
      a[i] = {
        slug: i,
        name: String(n.name || k(i)).trim() || k(i),
        terms: l,
        updatedAt: typeof n.updatedAt == "number" ? n.updatedAt : Date.now()
      };
    }
  const r = {};
  if (t.urlOverrides && typeof t.urlOverrides == "object")
    for (const [o, n] of Object.entries(t.urlOverrides))
      typeof n == "string" && n.trim() && (r[_(o)] = _(n));
  return { series: a, urlOverrides: r };
}
async function U() {
  const e = await chrome.storage.local.get(y);
  return O(e[y]);
}
async function B(e) {
  const t = O(e);
  return await chrome.storage.local.set({ [y]: t }), t;
}
const G = "com.scanlatorvogels.host", j = [
  "gemini-3.5-flash-lite",
  "gemini-3.1-flash-lite",
  "gemini-3.6-flash",
  "gemini-3.8-flash"
], K = "gemini-3.5-flash-lite", h = {
  apiKey: "",
  uppercase: !0,
  autoTranslate: !1,
  idleTimeoutSec: 300,
  backendPort: 8765,
  prefetchCount: 4
}, E = "scanlator_vogels_settings_v1";
function I(e) {
  return typeof e == "string" && j.includes(e) ? e : K;
}
function v(e) {
  return e === "auto" || e === "ja" || e === "ko" || e === "zh" || e === "en" || e === "es" || e === "fr" ? e : "auto";
}
function P(e) {
  return e === "auto" || e === "rtl" || e === "ltr" ? e : "auto";
}
function N(e) {
  return e === "scanlation" || e === "informal" || e === "fiel" || e === "formal" ? e : "scanlation";
}
async function f() {
  const t = (await chrome.storage.local.get(E))[E] ?? {};
  return {
    apiKey: typeof t.apiKey == "string" ? t.apiKey.trim() : h.apiKey,
    model: I(t.model),
    sourceLang: v(t.sourceLang),
    readingDirection: P(t.readingDirection),
    style: N(t.style),
    uppercase: typeof t.uppercase == "boolean" ? t.uppercase : h.uppercase,
    autoTranslate: typeof t.autoTranslate == "boolean" ? t.autoTranslate : h.autoTranslate,
    idleTimeoutSec: typeof t.idleTimeoutSec == "number" && t.idleTimeoutSec >= 30 && t.idleTimeoutSec <= 3600 ? Math.round(t.idleTimeoutSec) : h.idleTimeoutSec,
    backendPort: typeof t.backendPort == "number" && t.backendPort >= 1024 && t.backendPort <= 65535 ? Math.round(t.backendPort) : h.backendPort,
    prefetchCount: typeof t.prefetchCount == "number" && t.prefetchCount >= 3 && t.prefetchCount <= 6 ? Math.round(t.prefetchCount) : h.prefetchCount
  };
}
async function L(e) {
  const t = await f(), a = {
    ...t,
    ...e,
    model: I(e.model ?? t.model),
    sourceLang: v(e.sourceLang ?? t.sourceLang),
    readingDirection: P(e.readingDirection ?? t.readingDirection),
    style: N(e.style ?? t.style),
    apiKey: typeof e.apiKey == "string" ? e.apiKey.trim() : t.apiKey
  };
  return await chrome.storage.local.set({ [E]: a }), a;
}
let m = "stopped", b = null, R = 0;
const T = 2e4, $ = 12e3;
function p(e) {
  return `http://127.0.0.1:${e}`;
}
async function g(e, t = 1500) {
  const a = new AbortController(), r = setTimeout(() => a.abort(), t);
  try {
    const o = await fetch(`${p(e)}/health`, {
      method: "GET",
      signal: a.signal
    });
    return o.ok ? await o.json() : null;
  } catch {
    return null;
  } finally {
    clearTimeout(r);
  }
}
function C(e, t = T) {
  return new Promise((a, r) => {
    let o = !1;
    const n = setTimeout(() => {
      o || (o = !0, r(
        new Error(
          `Timeout (${Math.round(t / 1e3)}s) aguardando resposta do Native Host. Verifique native_host/host.log e native_host/backend.log.`
        )
      ));
    }, t);
    try {
      chrome.runtime.sendNativeMessage(G, e, (i) => {
        if (o) return;
        o = !0, clearTimeout(n);
        const l = chrome.runtime.lastError;
        if (l) {
          r(new Error(l.message || "Native Messaging Host nao registrado."));
          return;
        }
        a(i ?? {});
      });
    } catch (i) {
      o || (o = !0, clearTimeout(n), r(i instanceof Error ? i : new Error(String(i))));
    }
  });
}
async function u(e, t) {
  m = e, b = t, R = Date.now();
  try {
    const a = await chrome.tabs.query({});
    for (const r of a)
      r.id !== void 0 && chrome.tabs.sendMessage(r.id, {
        type: "ENGINE_STATE_BROADCAST",
        state: e,
        health: t
      }).catch(() => {
      });
  } catch {
  }
}
async function H() {
  const e = await f(), t = await g(e.backendPort);
  return t ? (m = "running", b = t, { ok: !0, state: "running", health: t }) : m === "starting" && Date.now() - R < T + 2e3 ? { ok: !0, state: "starting", health: null } : (m = "stopped", b = null, { ok: !0, state: "stopped", health: null });
}
async function z() {
  const e = await f(), t = await g(e.backendPort);
  if (t)
    return await u("running", t), { ok: !0, state: "running", health: t, nativeHostAvailable: !0 };
  await u("starting", null);
  try {
    const a = await C(
      {
        action: "start",
        port: e.backendPort,
        idle_timeout_sec: e.idleTimeoutSec
      },
      T
    );
    if (a.ok) {
      const o = a.health ?? await g(e.backendPort, 3e3);
      if (o)
        return await u("running", o), { ok: !0, state: "running", health: o, nativeHostAvailable: !0 };
    }
    const r = await g(e.backendPort, 1500);
    return r ? (await u("running", r), { ok: !0, state: "running", health: r, nativeHostAvailable: !0 }) : (await u("stopped", null), {
      ok: !1,
      state: "stopped",
      health: null,
      nativeHostAvailable: !0,
      message: String(
        a.message || `Motor nao respondeu em http://127.0.0.1:${e.backendPort}/health. Verifique native_host/backend.log.`
      )
    });
  } catch (a) {
    const r = await g(e.backendPort, 1500);
    return r ? (await u("running", r), { ok: !0, state: "running", health: r, nativeHostAvailable: !1 }) : (await u("stopped", null), {
      ok: !1,
      state: "stopped",
      health: null,
      nativeHostAvailable: !1,
      message: `${a instanceof Error ? a.message : String(a)} (ou execute start.bat manualmente).`
    });
  }
}
async function V() {
  const e = await f();
  await u("stopping", b);
  try {
    await fetch(`${p(e.backendPort)}/shutdown`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
  } catch {
    try {
      await C({ action: "stop", port: e.backendPort }, $);
    } catch {
    }
  }
  for (let t = 0; t < 16 && (await new Promise((r) => setTimeout(r, 250)), !!await g(e.backendPort, 500)); t++)
    ;
  return await u("stopped", null), { ok: !0, state: "stopped", health: null };
}
function q(e) {
  const t = new Uint8Array(e), a = 32768;
  let r = "";
  for (let o = 0; o < t.length; o += a) {
    const n = t.subarray(o, o + a);
    r += String.fromCharCode(...n);
  }
  return btoa(r);
}
async function w(e, t) {
  if (e.startsWith("data:")) {
    const n = e.indexOf(",");
    if (n >= 0) return e.slice(n + 1);
  }
  const a = await fetch(e, {
    method: "GET",
    credentials: "include",
    referrer: t || void 0,
    referrerPolicy: "no-referrer-when-downgrade"
  });
  if (!a.ok)
    throw new Error(`HTTP ${a.status} ao baixar imagem do site.`);
  const r = (a.headers.get("content-type") || "").toLowerCase();
  if (r && !r.startsWith("image/") && !r.includes("octet-stream"))
    throw new Error(
      `Site retornou '${r}' em vez de imagem (possivel bloqueio anti-hotlink ou desafio do site).`
    );
  const o = await a.arrayBuffer();
  if (o.byteLength < 512)
    throw new Error("Imagem retornada esta vazia ou bloqueada pelo site.");
  return q(o);
}
function M(e, t) {
  const a = String(t.code || "UNKNOWN_ERROR");
  return {
    ok: !1,
    httpStatus: e,
    code: a === "CIRCUIT_BREAKER_OPEN" || a === "RATE_LIMITED_429" || a === "UPSTREAM_UNAVAILABLE" || a === "MISSING_API_KEY" || a === "PAID_MODEL_BLOCKED" || a === "INVALID_IMAGE" ? a : e === 429 ? "RATE_LIMITED_429" : e === 503 ? "UPSTREAM_UNAVAILABLE" : "UNKNOWN_ERROR",
    message: String(t.message || `Falha HTTP ${e} no motor local.`),
    retry_after_sec: typeof t.retry_after_sec == "number" ? t.retry_after_sec : void 0,
    circuit_breaker: t.circuit_breaker
  };
}
async function F(e, t, a, r, o, n) {
  const i = await f(), l = p(i.backendPort);
  let s = a;
  if (!s)
    try {
      s = await w(e, t);
    } catch (c) {
      return {
        ok: !1,
        code: "CORS_OR_NETWORK_BLOCKED",
        message: `Este site bloqueou o acesso direto a imagem (${c instanceof Error ? c.message : String(c)}).`
      };
    }
  try {
    const c = await fetch(`${l}/translate-page`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: s,
        model: i.model,
        source_lang: i.sourceLang,
        reading_direction: i.readingDirection,
        direct_ocr: o,
        style: i.style,
        uppercase: i.uppercase,
        output_format: "webp",
        force_retranslate: r,
        use_cache: !o,
        api_key: i.apiKey || void 0,
        glossary: n && Object.keys(n).length > 0 ? n : void 0,
        idle_timeout_sec: i.idleTimeoutSec
      })
    }), d = await c.json().catch(() => ({}));
    return c.ok ? d : M(c.status, d);
  } catch {
    return await u("stopped", null), {
      ok: !1,
      code: "ENGINE_OFFLINE",
      message: "Motor local desligado. Clique em 'Ligar Motor' para traduzir."
    };
  }
}
async function W(e, t, a) {
  const r = await f(), o = p(r.backendPort), n = {}, l = (await Promise.all(
    t.slice(0, 4).map(async (s) => {
      if (s.imageBase64)
        return {
          page_id: s.pageId,
          image_base64: s.imageBase64,
          force_retranslate: !!s.forceRetranslate
        };
      try {
        const c = await w(s.imageUrl, e);
        return {
          page_id: s.pageId,
          image_base64: c,
          force_retranslate: !!s.forceRetranslate
        };
      } catch (c) {
        return n[s.pageId] = `Acesso bloqueado a imagem (${c instanceof Error ? c.message : String(c)}).`, null;
      }
    })
  )).filter((s) => s !== null);
  if (l.length === 0)
    return {
      ok: !1,
      code: "CORS_OR_NETWORK_BLOCKED",
      message: Object.values(n)[0] || "Este site bloqueou o acesso as imagens."
    };
  try {
    const s = await fetch(`${o}/translate-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pages: l,
        model: r.model,
        source_lang: r.sourceLang,
        reading_direction: r.readingDirection,
        style: r.style,
        uppercase: r.uppercase,
        output_format: "webp",
        use_cache: !0,
        api_key: r.apiKey || void 0,
        glossary: a && Object.keys(a).length > 0 ? a : void 0,
        idle_timeout_sec: r.idleTimeoutSec
      })
    }), c = await s.json().catch(() => ({}));
    if (!s.ok)
      return M(s.status, c);
    const d = c;
    return d.ok && Object.keys(n).length > 0 && (d.skipped_errors = n), d;
  } catch {
    return await u("stopped", null), {
      ok: !1,
      code: "ENGINE_OFFLINE",
      message: "Motor local desligado. Clique em 'Ligar Motor' para traduzir."
    };
  }
}
async function Y(e) {
  try {
    return { ok: !0, dataUrl: await new Promise((a, r) => {
      chrome.tabs.captureVisibleTab(e ?? chrome.windows.WINDOW_ID_CURRENT, { format: "png" }, (o) => {
        const n = chrome.runtime.lastError;
        if (n) {
          r(new Error(n.message || "Falha ao capturar aba visivel."));
          return;
        }
        if (!o) {
          r(new Error("Captura de tela retornou vazia."));
          return;
        }
        a(o);
      });
    }) };
  } catch (t) {
    return {
      ok: !1,
      message: t instanceof Error ? t.message : String(t)
    };
  }
}
async function x(e, t, a) {
  const r = await f(), o = p(r.backendPort);
  let n = a;
  if (!n)
    try {
      n = await w(e, t);
    } catch {
      return { ok: !1, skipped: !0 };
    }
  try {
    const i = await fetch(`${o}/prepare-page`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: n,
        source_lang: r.sourceLang,
        reading_direction: r.readingDirection,
        use_cache: !0
      })
    });
    return i.ok ? await i.json() : { ok: !1 };
  } catch {
    return { ok: !1 };
  }
}
async function J() {
  const e = await f();
  try {
    const a = await (await fetch(`${p(e.backendPort)}/circuit-breaker/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    })).json(), r = await g(e.backendPort);
    return r && await u("running", r), a;
  } catch {
    return { ok: !1, message: "Motor local inacessivel." };
  }
}
async function A(e) {
  const t = await L({ idleTimeoutSec: e });
  try {
    return await (await fetch(`${p(t.backendPort)}/config/idle-timeout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idle_timeout_sec: t.idleTimeoutSec })
    })).json();
  } catch {
    return { ok: !0, saved_only: !0 };
  }
}
chrome.runtime.onMessage.addListener((e, t, a) => ((async () => {
  var r;
  switch (e.type) {
    case "GET_SETTINGS": {
      a(await f());
      break;
    }
    case "SAVE_SETTINGS": {
      const o = await L(e.settings);
      typeof e.settings.idleTimeoutSec == "number" && await A(o.idleTimeoutSec);
      const n = await chrome.tabs.query({});
      for (const i of n)
        i.id !== void 0 && chrome.tabs.sendMessage(i.id, {
          type: "SETTINGS_UPDATED",
          settings: o
        }).catch(() => {
        });
      a(o);
      break;
    }
    case "GET_GLOSSARY_STORE": {
      a(await U());
      break;
    }
    case "SAVE_GLOSSARY_STORE": {
      const o = await B(e.store), n = await chrome.tabs.query({});
      for (const i of n)
        i.id !== void 0 && chrome.tabs.sendMessage(i.id, {
          type: "GLOSSARY_UPDATED",
          store: o
        }).catch(() => {
        });
      a(o);
      break;
    }
    case "CAPTURE_VISIBLE_TAB": {
      a(await Y((r = t.tab) == null ? void 0 : r.windowId));
      break;
    }
    case "ENGINE_STATUS": {
      a(await H());
      break;
    }
    case "ENGINE_START": {
      a(await z());
      break;
    }
    case "ENGINE_STOP": {
      a(await V());
      break;
    }
    case "RESET_CIRCUIT_BREAKER": {
      a(await J());
      break;
    }
    case "UPDATE_IDLE_TIMEOUT": {
      a(await A(e.idleTimeoutSec));
      break;
    }
    case "TRANSLATE_IMAGE_URL": {
      a(
        await F(
          e.imageUrl,
          e.pageUrl,
          e.imageBase64,
          !!e.forceRetranslate,
          !!e.directOcr,
          e.glossary
        )
      );
      break;
    }
    case "TRANSLATE_BATCH_URLS": {
      a(await W(e.pageUrl, e.items, e.glossary));
      break;
    }
    case "PREPARE_IMAGE_URL": {
      a(
        await x(
          e.imageUrl,
          e.pageUrl,
          e.imageBase64
        )
      );
      break;
    }
    default:
      a({ ok: !1, message: "Mensagem nao suportada." });
  }
})().catch((r) => {
  a({ ok: !1, message: r instanceof Error ? r.message : String(r) });
}), !0));
