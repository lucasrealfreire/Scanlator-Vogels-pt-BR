import {
  FREE_TIER_MODELS,
  SOURCE_LANGUAGES,
  type BackendHealth,
  type EngineActionResponse,
  type EngineLifecycleState,
  type ExtensionSettings,
  type FreeTierModel,
  type RuntimeMessage,
  type SourceLanguage,
  type TranslationStyle,
} from "../shared/types";

const statusBadge = document.getElementById("statusBadge") as HTMLSpanElement;
const engineBtn = document.getElementById("engineBtn") as HTMLButtonElement;
const translateNowBtn = document.getElementById("translateNowBtn") as HTMLButtonElement;
const translateChapterBtn = document.getElementById("translateChapterBtn") as HTMLButtonElement;
const vramVal = document.getElementById("vramVal") as HTMLSpanElement;
const quotaVal = document.getElementById("quotaVal") as HTMLSpanElement;
const cbVal = document.getElementById("cbVal") as HTMLSpanElement;
const idleVal = document.getElementById("idleVal") as HTMLSpanElement;
const cbBanner = document.getElementById("cbBanner") as HTMLDivElement;
const cbBannerText = document.getElementById("cbBannerText") as HTMLSpanElement;
const resetCbBtn = document.getElementById("resetCbBtn") as HTMLButtonElement;
const msgBox = document.getElementById("msgBox") as HTMLDivElement;

const autoTranslateEl = document.getElementById("autoTranslate") as HTMLInputElement;
const uppercaseEl = document.getElementById("uppercase") as HTMLInputElement;
const sourceLangSelect = document.getElementById("sourceLangSelect") as HTMLSelectElement;
const modelSelect = document.getElementById("modelSelect") as HTMLSelectElement;
const styleSelect = document.getElementById("styleSelect") as HTMLSelectElement;
const apiKeyInput = document.getElementById("apiKeyInput") as HTMLInputElement;
const idleTimeoutSelect = document.getElementById("idleTimeoutSelect") as HTMLSelectElement;
const saveBtn = document.getElementById("saveBtn") as HTMLButtonElement;
const clearCacheBtn = document.getElementById("clearCacheBtn") as HTMLButtonElement;

let currentState: EngineLifecycleState = "stopped";
let pollTimer: number | null = null;

for (const lang of SOURCE_LANGUAGES) {
  const opt = document.createElement("option");
  opt.value = lang.value;
  opt.textContent = lang.label;
  sourceLangSelect.appendChild(opt);
}

// Populate strictly from Free Tier whitelist
for (const model of FREE_TIER_MODELS) {
  const opt = document.createElement("option");
  opt.value = model;
  opt.textContent = model === "gemini-3.5-flash-lite" ? `${model} (Padrão)` : model;
  modelSelect.appendChild(opt);
}

function showMessage(text: string, isError = false): void {
  msgBox.textContent = text;
  msgBox.className = isError ? "msg error" : "msg";
}

function clearMessage(): void {
  msgBox.className = "msg hidden";
}

function renderEngineState(state: EngineLifecycleState, health: BackendHealth | null): void {
  currentState = state;
  statusBadge.className = `badge ${state}`;

  if (state === "running" && health) {
    statusBadge.textContent = health.warming_up ? "Ativo (Aquecendo GPU...)" : "Ativo";
    engineBtn.textContent = "Desligar Motor";
    engineBtn.className = "btn btn-stop";
    engineBtn.disabled = false;

    const nvTxt =
      health.nvidia_vram_used_mb !== null ? ` (GPU ${Math.round(health.nvidia_vram_used_mb)} MB)` : "";
    vramVal.textContent = health.warming_up
      ? "Aquecendo GPU..."
      : `${Math.round(health.torch_vram_allocated_mb)} MB${nvTxt}`;
    quotaVal.textContent = `${health.quota?.requests_today ?? 0} req(s)`;
    cbVal.textContent = health.circuit_breaker?.state ?? "CLOSED";
    const mins = Math.floor(health.idle_remaining_sec / 60);
    const secs = Math.floor(health.idle_remaining_sec % 60);
    idleVal.textContent = `${mins}m${String(secs).padStart(2, "0")}s`;

    if (health.circuit_breaker?.state === "OPEN") {
      cbBanner.classList.remove("hidden");
      cbBannerText.textContent = `CIRCUIT_BREAKER_OPEN — retomada em ${Math.ceil(health.circuit_breaker.retry_after_sec)}s.`;
    } else {
      cbBanner.classList.add("hidden");
    }
  } else if (state === "starting") {
    statusBadge.textContent = "Ligando...";
    engineBtn.textContent = "Ligando...";
    engineBtn.disabled = true;
  } else if (state === "stopping") {
    statusBadge.textContent = "Desligando...";
    engineBtn.textContent = "Desligando...";
    engineBtn.disabled = true;
  } else {
    statusBadge.textContent = "Desligado";
    engineBtn.textContent = "Ligar Motor";
    engineBtn.className = "btn btn-start";
    engineBtn.disabled = false;
    vramVal.textContent = "0 MB";
    quotaVal.textContent = "—";
    cbVal.textContent = "—";
    idleVal.textContent = "0% uso";
    cbBanner.classList.add("hidden");
  }
}

async function refreshStatus(): Promise<void> {
  const st = (await chrome.runtime.sendMessage({
    type: "ENGINE_STATUS",
  } satisfies RuntimeMessage)) as EngineActionResponse;
  renderEngineState(st.state, st.health);
}

async function init(): Promise<void> {
  const settings = (await chrome.runtime.sendMessage({
    type: "GET_SETTINGS",
  } satisfies RuntimeMessage)) as ExtensionSettings;

  autoTranslateEl.checked = settings.autoTranslate;
  uppercaseEl.checked = settings.uppercase;
  sourceLangSelect.value = settings.sourceLang;
  modelSelect.value = settings.model;
  styleSelect.value = settings.style;
  apiKeyInput.value = settings.apiKey;
  idleTimeoutSelect.value = String(settings.idleTimeoutSec);

  await refreshStatus();
  pollTimer = window.setInterval(() => {
    if (currentState === "running") {
      void refreshStatus();
    }
  }, 3000);
}

function sendRuntimeWithTimeout<T>(msg: RuntimeMessage, timeoutMs = 24_000): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let done = false;
    const timer = window.setTimeout(() => {
      if (!done) {
        done = true;
        reject(new Error(`Timeout (${Math.round(timeoutMs / 1000)}s) aguardando resposta do motor.`));
      }
    }, timeoutMs);

    chrome.runtime
      .sendMessage(msg)
      .then((res) => {
        if (!done) {
          done = true;
          window.clearTimeout(timer);
          resolve(res as T);
        }
      })
      .catch((err) => {
        if (!done) {
          done = true;
          window.clearTimeout(timer);
          reject(err instanceof Error ? err : new Error(String(err)));
        }
      });
  });
}

engineBtn.addEventListener("click", async () => {
  clearMessage();
  if (currentState === "running") {
    renderEngineState("stopping", null);
    try {
      const resp = await sendRuntimeWithTimeout<EngineActionResponse>(
        { type: "ENGINE_STOP" },
        15_000
      );
      renderEngineState(resp.state, resp.health);
      showMessage("Motor desligado e VRAM 100% liberada.");
    } catch (err) {
      renderEngineState("stopped", null);
      showMessage(err instanceof Error ? err.message : String(err), true);
    }
  } else {
    renderEngineState("starting", null);
    try {
      const resp = await sendRuntimeWithTimeout<EngineActionResponse>(
        { type: "ENGINE_START" },
        24_000
      );
      renderEngineState(resp.state, resp.health);
      if (!resp.ok) {
        showMessage(resp.message || "Falha ao ligar motor local.", true);
      } else {
        showMessage("Motor local ativo em 127.0.0.1.");
      }
    } catch (err) {
      renderEngineState("stopped", null);
      showMessage(err instanceof Error ? err.message : String(err), true);
    }
  }
});

translateNowBtn.addEventListener("click", async () => {
  clearMessage();
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!activeTab?.id) {
    showMessage("Nenhuma aba ativa encontrada.", true);
    return;
  }
  try {
    await chrome.tabs.sendMessage(activeTab.id, {
      type: "TRANSLATE_VISIBLE_PAGE",
    } satisfies RuntimeMessage);
    window.close();
  } catch {
    showMessage("Recarregue a aba do leitor de mangá para ativar a extensão.", true);
  }
});

translateChapterBtn.addEventListener("click", async () => {
  clearMessage();
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!activeTab?.id) {
    showMessage("Nenhuma aba ativa encontrada.", true);
    return;
  }
  try {
    await chrome.tabs.sendMessage(activeTab.id, {
      type: "TRANSLATE_CHAPTER",
    } satisfies RuntimeMessage);
    window.close();
  } catch {
    showMessage("Recarregue a aba do leitor de mangá para ativar a extensão.", true);
  }
});

async function persistSettings(showToast = true): Promise<void> {
  const patch: Partial<ExtensionSettings> = {
    autoTranslate: autoTranslateEl.checked,
    uppercase: uppercaseEl.checked,
    sourceLang: sourceLangSelect.value as SourceLanguage,
    model: modelSelect.value as FreeTierModel,
    style: styleSelect.value as TranslationStyle,
    apiKey: apiKeyInput.value.trim(),
    idleTimeoutSec: Number(idleTimeoutSelect.value) || 300,
  };
  await chrome.runtime.sendMessage({
    type: "SAVE_SETTINGS",
    settings: patch,
  } satisfies RuntimeMessage);
  if (showToast) {
    showMessage("Configurações salvas (apenas Free Tier).");
  }
  await refreshStatus();
}

saveBtn.addEventListener("click", () => void persistSettings(true));
autoTranslateEl.addEventListener("change", () => void persistSettings(false));
uppercaseEl.addEventListener("change", () => void persistSettings(false));
sourceLangSelect.addEventListener("change", () => void persistSettings(false));

resetCbBtn.addEventListener("click", async () => {
  await chrome.runtime.sendMessage({
    type: "RESET_CIRCUIT_BREAKER",
  } satisfies RuntimeMessage);
  await refreshStatus();
  showMessage("Circuit Breaker resetado para retomada.");
});

clearCacheBtn.addEventListener("click", async () => {
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (activeTab?.id) {
    try {
      await chrome.tabs.sendMessage(activeTab.id, {
        type: "CLEAR_TAB_CACHE",
      } satisfies RuntimeMessage);
      showMessage("Cache local (IndexedDB) limpo na aba atual.");
      return;
    } catch {
      /* ignore */
    }
  }
  showMessage("Abra uma aba de leitor de mangá para limpar o cache local.", true);
});

window.addEventListener("unload", () => {
  if (pollTimer !== null) window.clearInterval(pollTimer);
});

void init();
