const N = "com.scanlatorvogels.host", O = [
  "gemini-3.5-flash-lite",
  "gemini-3.1-flash-lite",
  "gemini-3.6-flash",
  "gemini-3.8-flash"
], L = "gemini-3.5-flash-lite", p = {
  apiKey: "",
  uppercase: !0,
  autoTranslate: !1,
  idleTimeoutSec: 300,
  backendPort: 8765,
  prefetchCount: 4
}, T = "scanlator_vogels_settings_v1";
function y(e) {
  return typeof e == "string" && O.includes(e) ? e : L;
}
function w(e) {
  return e === "auto" || e === "ja" || e === "en" || e === "ko" || e === "zh" ? e : "auto";
}
function k(e) {
  return e === "scanlation" || e === "informal" || e === "fiel" || e === "formal" ? e : "scanlation";
}
async function l() {
  const t = (await chrome.storage.local.get(T))[T] ?? {};
  return {
    apiKey: typeof t.apiKey == "string" ? t.apiKey.trim() : p.apiKey,
    model: y(t.model),
    sourceLang: w(t.sourceLang),
    style: k(t.style),
    uppercase: typeof t.uppercase == "boolean" ? t.uppercase : p.uppercase,
    autoTranslate: typeof t.autoTranslate == "boolean" ? t.autoTranslate : p.autoTranslate,
    idleTimeoutSec: typeof t.idleTimeoutSec == "number" && t.idleTimeoutSec >= 30 && t.idleTimeoutSec <= 3600 ? Math.round(t.idleTimeoutSec) : p.idleTimeoutSec,
    backendPort: typeof t.backendPort == "number" && t.backendPort >= 1024 && t.backendPort <= 65535 ? Math.round(t.backendPort) : p.backendPort,
    prefetchCount: typeof t.prefetchCount == "number" && t.prefetchCount >= 3 && t.prefetchCount <= 6 ? Math.round(t.prefetchCount) : p.prefetchCount
  };
}
async function S(e) {
  const t = await l(), a = {
    ...t,
    ...e,
    model: y(e.model ?? t.model),
    sourceLang: w(e.sourceLang ?? t.sourceLang),
    style: k(e.style ?? t.style),
    apiKey: typeof e.apiKey == "string" ? e.apiKey.trim() : t.apiKey
  };
  return await chrome.storage.local.set({ [T]: a }), a;
}
let h = "stopped", g = null, A = 0;
const _ = 2e4, v = 12e3;
function f(e) {
  return `http://127.0.0.1:${e}`;
}
async function d(e, t = 1500) {
  const a = new AbortController(), r = setTimeout(() => a.abort(), t);
  try {
    const n = await fetch(`${f(e)}/health`, {
      method: "GET",
      signal: a.signal
    });
    return n.ok ? await n.json() : null;
  } catch {
    return null;
  } finally {
    clearTimeout(r);
  }
}
function I(e, t = _) {
  return new Promise((a, r) => {
    let n = !1;
    const i = setTimeout(() => {
      n || (n = !0, r(
        new Error(
          `Timeout (${Math.round(t / 1e3)}s) aguardando resposta do Native Host. Verifique native_host/host.log e native_host/backend.log.`
        )
      ));
    }, t);
    try {
      chrome.runtime.sendNativeMessage(N, e, (s) => {
        if (n) return;
        n = !0, clearTimeout(i);
        const o = chrome.runtime.lastError;
        if (o) {
          r(new Error(o.message || "Native Messaging Host nao registrado."));
          return;
        }
        a(s ?? {});
      });
    } catch (s) {
      n || (n = !0, clearTimeout(i), r(s instanceof Error ? s : new Error(String(s))));
    }
  });
}
async function c(e, t) {
  h = e, g = t, A = Date.now();
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
async function M() {
  const e = await l(), t = await d(e.backendPort);
  return t ? (h = "running", g = t, { ok: !0, state: "running", health: t }) : h === "starting" && Date.now() - A < _ + 2e3 ? { ok: !0, state: "starting", health: null } : (h = "stopped", g = null, { ok: !0, state: "stopped", health: null });
}
async function C() {
  const e = await l(), t = await d(e.backendPort);
  if (t)
    return await c("running", t), { ok: !0, state: "running", health: t, nativeHostAvailable: !0 };
  await c("starting", null);
  try {
    const a = await I(
      {
        action: "start",
        port: e.backendPort,
        idle_timeout_sec: e.idleTimeoutSec
      },
      _
    );
    if (a.ok) {
      const n = a.health ?? await d(e.backendPort, 3e3);
      if (n)
        return await c("running", n), { ok: !0, state: "running", health: n, nativeHostAvailable: !0 };
    }
    const r = await d(e.backendPort, 1500);
    return r ? (await c("running", r), { ok: !0, state: "running", health: r, nativeHostAvailable: !0 }) : (await c("stopped", null), {
      ok: !1,
      state: "stopped",
      health: null,
      nativeHostAvailable: !0,
      message: String(
        a.message || `Motor nao respondeu em http://127.0.0.1:${e.backendPort}/health. Verifique native_host/backend.log.`
      )
    });
  } catch (a) {
    const r = await d(e.backendPort, 1500);
    return r ? (await c("running", r), { ok: !0, state: "running", health: r, nativeHostAvailable: !1 }) : (await c("stopped", null), {
      ok: !1,
      state: "stopped",
      health: null,
      nativeHostAvailable: !1,
      message: `${a instanceof Error ? a.message : String(a)} (ou execute start.bat manualmente).`
    });
  }
}
async function R() {
  const e = await l();
  await c("stopping", g);
  try {
    await fetch(`${f(e.backendPort)}/shutdown`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
  } catch {
    try {
      await I({ action: "stop", port: e.backendPort }, v);
    } catch {
    }
  }
  for (let t = 0; t < 16 && (await new Promise((r) => setTimeout(r, 250)), !!await d(e.backendPort, 500)); t++)
    ;
  return await c("stopped", null), { ok: !0, state: "stopped", health: null };
}
function B(e) {
  const t = new Uint8Array(e), a = 32768;
  let r = "";
  for (let n = 0; n < t.length; n += a) {
    const i = t.subarray(n, n + a);
    r += String.fromCharCode(...i);
  }
  return btoa(r);
}
async function E(e, t) {
  if (e.startsWith("data:")) {
    const i = e.indexOf(",");
    if (i >= 0) return e.slice(i + 1);
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
  const n = await a.arrayBuffer();
  if (n.byteLength < 512)
    throw new Error("Imagem retornada esta vazia ou bloqueada pelo site.");
  return B(n);
}
function P(e, t) {
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
async function U(e, t, a, r) {
  const n = await l(), i = f(n.backendPort);
  let s = a;
  if (!s)
    try {
      s = await E(e, t);
    } catch (o) {
      return {
        ok: !1,
        code: "CORS_OR_NETWORK_BLOCKED",
        message: `Este site bloqueou o acesso direto a imagem (${o instanceof Error ? o.message : String(o)}).`
      };
    }
  try {
    const o = await fetch(`${i}/translate-page`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: s,
        model: n.model,
        source_lang: n.sourceLang,
        style: n.style,
        uppercase: n.uppercase,
        output_format: "webp",
        force_retranslate: r,
        use_cache: !0,
        api_key: n.apiKey || void 0,
        idle_timeout_sec: n.idleTimeoutSec
      })
    }), u = await o.json().catch(() => ({}));
    return o.ok ? u : P(o.status, u);
  } catch {
    return await c("stopped", null), {
      ok: !1,
      code: "ENGINE_OFFLINE",
      message: "Motor local desligado. Clique em 'Ligar Motor' para traduzir."
    };
  }
}
async function K(e, t) {
  const a = await l(), r = f(a.backendPort), n = {}, s = (await Promise.all(
    t.slice(0, 4).map(async (o) => {
      if (o.imageBase64)
        return {
          page_id: o.pageId,
          image_base64: o.imageBase64,
          force_retranslate: !!o.forceRetranslate
        };
      try {
        const u = await E(o.imageUrl, e);
        return {
          page_id: o.pageId,
          image_base64: u,
          force_retranslate: !!o.forceRetranslate
        };
      } catch (u) {
        return n[o.pageId] = `Acesso bloqueado a imagem (${u instanceof Error ? u.message : String(u)}).`, null;
      }
    })
  )).filter((o) => o !== null);
  if (s.length === 0)
    return {
      ok: !1,
      code: "CORS_OR_NETWORK_BLOCKED",
      message: Object.values(n)[0] || "Este site bloqueou o acesso as imagens."
    };
  try {
    const o = await fetch(`${r}/translate-batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pages: s,
        model: a.model,
        source_lang: a.sourceLang,
        style: a.style,
        uppercase: a.uppercase,
        output_format: "webp",
        use_cache: !0,
        api_key: a.apiKey || void 0,
        idle_timeout_sec: a.idleTimeoutSec
      })
    }), u = await o.json().catch(() => ({}));
    if (!o.ok)
      return P(o.status, u);
    const m = u;
    return m.ok && Object.keys(n).length > 0 && (m.skipped_errors = n), m;
  } catch {
    return await c("stopped", null), {
      ok: !1,
      code: "ENGINE_OFFLINE",
      message: "Motor local desligado. Clique em 'Ligar Motor' para traduzir."
    };
  }
}
async function D(e, t, a) {
  const r = await l(), n = f(r.backendPort);
  let i = a;
  if (!i)
    try {
      i = await E(e, t);
    } catch {
      return { ok: !1, skipped: !0 };
    }
  try {
    const s = await fetch(`${n}/prepare-page`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image_base64: i,
        source_lang: r.sourceLang,
        use_cache: !0
      })
    });
    return s.ok ? await s.json() : { ok: !1 };
  } catch {
    return { ok: !1 };
  }
}
async function G() {
  const e = await l();
  try {
    const a = await (await fetch(`${f(e.backendPort)}/circuit-breaker/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    })).json(), r = await d(e.backendPort);
    return r && await c("running", r), a;
  } catch {
    return { ok: !1, message: "Motor local inacessivel." };
  }
}
async function b(e) {
  const t = await S({ idleTimeoutSec: e });
  try {
    return await (await fetch(`${f(t.backendPort)}/config/idle-timeout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idle_timeout_sec: t.idleTimeoutSec })
    })).json();
  } catch {
    return { ok: !0, saved_only: !0 };
  }
}
chrome.runtime.onMessage.addListener((e, t, a) => ((async () => {
  switch (e.type) {
    case "GET_SETTINGS": {
      a(await l());
      break;
    }
    case "SAVE_SETTINGS": {
      const r = await S(e.settings);
      typeof e.settings.idleTimeoutSec == "number" && await b(r.idleTimeoutSec);
      const n = await chrome.tabs.query({});
      for (const i of n)
        i.id !== void 0 && chrome.tabs.sendMessage(i.id, {
          type: "SETTINGS_UPDATED",
          settings: r
        }).catch(() => {
        });
      a(r);
      break;
    }
    case "ENGINE_STATUS": {
      a(await M());
      break;
    }
    case "ENGINE_START": {
      a(await C());
      break;
    }
    case "ENGINE_STOP": {
      a(await R());
      break;
    }
    case "RESET_CIRCUIT_BREAKER": {
      a(await G());
      break;
    }
    case "UPDATE_IDLE_TIMEOUT": {
      a(await b(e.idleTimeoutSec));
      break;
    }
    case "TRANSLATE_IMAGE_URL": {
      a(
        await U(
          e.imageUrl,
          e.pageUrl,
          e.imageBase64,
          !!e.forceRetranslate
        )
      );
      break;
    }
    case "TRANSLATE_BATCH_URLS": {
      a(await K(e.pageUrl, e.items));
      break;
    }
    case "PREPARE_IMAGE_URL": {
      a(
        await D(
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
