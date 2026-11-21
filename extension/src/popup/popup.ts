import {
  createEmptyGlossaryStore,
  parseImportedGlossaryJson,
  resolveActiveSeriesForUrl,
} from "../shared/glossary";
import {
  FREE_TIER_MODELS,
  READING_DIRECTIONS,
  SOURCE_LANGUAGES,
  type BackendHealth,
  type EngineActionResponse,
  type EngineLifecycleState,
  type ExtensionSettings,
  type FreeTierModel,
  type GlossaryStore,
  type ReadingDirection,
  type RuntimeMessage,
  type SourceLanguage,
  type TranslationStyle,
} from "../shared/types";

const statusBadge = document.getElementById("statusBadge") as HTMLSpanElement;
const engineBtn = document.getElementById("engineBtn") as HTMLButtonElement;
const translateNowBtn = document.getElementById("translateNowBtn") as HTMLButtonElement;
const translateChapterBtn = document.getElementById("translateChapterBtn") as HTMLButtonElement;
const manualCropBtn = document.getElementById("manualCropBtn") as HTMLButtonElement;
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
const readingDirectionSelect = document.getElementById("readingDirectionSelect") as HTMLSelectElement;
const modelSelect = document.getElementById("modelSelect") as HTMLSelectElement;
const styleSelect = document.getElementById("styleSelect") as HTMLSelectElement;
const apiKeyInput = document.getElementById("apiKeyInput") as HTMLInputElement;
const idleTimeoutSelect = document.getElementById("idleTimeoutSelect") as HTMLSelectElement;
const saveBtn = document.getElementById("saveBtn") as HTMLButtonElement;
const clearCacheBtn = document.getElementById("clearCacheBtn") as HTMLButtonElement;

// Glossary elements
const glossaryCountBadge = document.getElementById("glossaryCountBadge") as HTMLSpanElement;
const glossarySeriesSelect = document.getElementById("glossarySeriesSelect") as HTMLSelectElement;
const glossaryRenameBtn = document.getElementById("glossaryRenameBtn") as HTMLButtonElement;
const glossarySrcInput = document.getElementById("glossarySrcInput") as HTMLInputElement;
const glossaryDstInput = document.getElementById("glossaryDstInput") as HTMLInputElement;
const glossaryAddBtn = document.getElementById("glossaryAddBtn") as HTMLButtonElement;
const glossaryList = document.getElementById("glossaryList") as HTMLDivElement;
const glossaryExportBtn = document.getElementById("glossaryExportBtn") as HTMLButtonElement;
const glossaryImportBtn = document.getElementById("glossaryImportBtn") as HTMLButtonElement;
const glossaryImportFile = document.getElementById("glossaryImportFile") as HTMLInputElement;

let currentState: EngineLifecycleState = "stopped";
let pollTimer: number | null = null;
let activeTabUrl = "";
let detectedSlug = "obra-padrao";
let activeSeriesSlug = "obra-padrao";
let glossaryStore: GlossaryStore = createEmptyGlossaryStore();

for (const lang of SOURCE_LANGUAGES) {
  const opt = document.createElement("option");
  opt.value = lang.value;
  opt.textContent = lang.label;
  sourceLangSelect.appendChild(opt);
}

for (const dir of READING_DIRECTIONS) {
  const opt = document.createElement("option");
  opt.value = dir.value;
  opt.textContent = dir.label;
  readingDirectionSelect.appendChild(opt);
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
    statusBadge.textContent = health.warming_up ? "Ativo (Aquecendo...)" : "Ativo";
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

function getActiveSeriesEntry() {
  const existing = glossaryStore.series[activeSeriesSlug];
  if (existing) return existing;
  const resolved = resolveActiveSeriesForUrl(glossaryStore, activeTabUrl);
  return {
    slug: activeSeriesSlug,
    name: activeSeriesSlug === resolved.activeSlug ? resolved.series.name : activeSeriesSlug,
    terms: {} as Record<string, string>,
    updatedAt: Date.now(),
  };
}

function renderGlossaryUI(): void {
  const activeEntry = getActiveSeriesEntry();

  // Populate series dropdown (active series + all saved series)
  glossarySeriesSelect.innerHTML = "";
  const allSlugs = new Set<string>([activeSeriesSlug, ...Object.keys(glossaryStore.series)]);
  for (const slug of allSlugs) {
    const entry = glossaryStore.series[slug];
    const opt = document.createElement("option");
    opt.value = slug;
    const count = entry ? Object.keys(entry.terms).length : 0;
    const label = entry ? entry.name : activeEntry.name;
    opt.textContent = `${label} (${count})`;
    glossarySeriesSelect.appendChild(opt);
  }
  glossarySeriesSelect.value = activeSeriesSlug;

  const entries = Object.entries(activeEntry.terms);
  glossaryCountBadge.textContent =
    entries.length === 1 ? "1 termo" : `${entries.length} termos`;

  glossaryList.innerHTML = "";
  if (entries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "glossary-empty";
    empty.textContent = "Nenhum termo salvo para esta obra.";
    glossaryList.appendChild(empty);
    return;
  }

  for (const [src, dst] of entries) {
    const row = document.createElement("div");
    row.className = "glossary-item";

    const pair = document.createElement("span");
    pair.className = "glossary-pair";
    pair.title = `${src} = ${dst}`;
    const strong = document.createElement("strong");
    strong.textContent = src;
    pair.append(strong, document.createTextNode(` → ${dst}`));

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "glossary-del";
    delBtn.textContent = "×";
    delBtn.title = "Remover termo";
    delBtn.addEventListener("click", () => void removeGlossaryTerm(src));

    row.append(pair, delBtn);
    glossaryList.appendChild(row);
  }
}

async function persistGlossaryStore(): Promise<void> {
  glossaryStore = (await chrome.runtime.sendMessage({
    type: "SAVE_GLOSSARY_STORE",
    store: glossaryStore,
  } satisfies RuntimeMessage)) as GlossaryStore;
  renderGlossaryUI();
}

async function addGlossaryTerm(): Promise<void> {
  const src = glossarySrcInput.value.trim();
  const dst = glossaryDstInput.value.trim();
  if (!src || !dst) {
    showMessage("Preencha o termo original e a tradução PT-BR.", true);
    return;
  }
  const entry = getActiveSeriesEntry();
  glossaryStore.series[activeSeriesSlug] = {
    ...entry,
    terms: { ...entry.terms, [src]: dst },
    updatedAt: Date.now(),
  };
  glossarySrcInput.value = "";
  glossaryDstInput.value = "";
  await persistGlossaryStore();
  showMessage(`Glossário atualizado: ${src} → ${dst}`);
}

async function removeGlossaryTerm(src: string): Promise<void> {
  const entry = glossaryStore.series[activeSeriesSlug];
  if (!entry) return;
  const nextTerms = { ...entry.terms };
  delete nextTerms[src];
  glossaryStore.series[activeSeriesSlug] = {
    ...entry,
    terms: nextTerms,
    updatedAt: Date.now(),
  };
  await persistGlossaryStore();
}

async function refreshStatus(): Promise<void> {
  const st = (await chrome.runtime.sendMessage({
    type: "ENGINE_STATUS",
  } satisfies RuntimeMessage)) as EngineActionResponse;
  renderEngineState(st.state, st.health);
}

async function init(): Promise<void> {
  const [settings, store, tabs] = await Promise.all([
    chrome.runtime.sendMessage({ type: "GET_SETTINGS" } satisfies RuntimeMessage) as Promise<ExtensionSettings>,
    chrome.runtime.sendMessage({ type: "GET_GLOSSARY_STORE" } satisfies RuntimeMessage) as Promise<GlossaryStore>,
    chrome.tabs.query({ active: true, currentWindow: true }),
  ]);

  autoTranslateEl.checked = settings.autoTranslate;
  uppercaseEl.checked = settings.uppercase;
  sourceLangSelect.value = settings.sourceLang;
  readingDirectionSelect.value = settings.readingDirection;
  modelSelect.value = settings.model;
  styleSelect.value = settings.style;
  apiKeyInput.value = settings.apiKey;
  idleTimeoutSelect.value = String(settings.idleTimeoutSec);

  activeTabUrl = tabs[0]?.url || "";
  glossaryStore = store || createEmptyGlossaryStore();
  const resolved = resolveActiveSeriesForUrl(glossaryStore, activeTabUrl);
  detectedSlug = resolved.detectedSlug;
  activeSeriesSlug = resolved.activeSlug;
  renderGlossaryUI();

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

async function sendToActiveTab(msg: RuntimeMessage): Promise<void> {
  clearMessage();
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!activeTab?.id) {
    showMessage("Nenhuma aba ativa encontrada.", true);
    return;
  }
  try {
    await chrome.tabs.sendMessage(activeTab.id, msg);
    window.close();
  } catch {
    showMessage("Recarregue a aba do leitor para ativar a extensão.", true);
  }
}

translateNowBtn.addEventListener("click", () =>
  void sendToActiveTab({ type: "TRANSLATE_VISIBLE_PAGE" })
);
translateChapterBtn.addEventListener("click", () =>
  void sendToActiveTab({ type: "TRANSLATE_CHAPTER" })
);
manualCropBtn.addEventListener("click", () =>
  void sendToActiveTab({ type: "START_MANUAL_CROP" })
);

async function persistSettings(showToast = true): Promise<void> {
  const patch: Partial<ExtensionSettings> = {
    autoTranslate: autoTranslateEl.checked,
    uppercase: uppercaseEl.checked,
    sourceLang: sourceLangSelect.value as SourceLanguage,
    readingDirection: readingDirectionSelect.value as ReadingDirection,
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
readingDirectionSelect.addEventListener("change", () => void persistSettings(false));

glossaryAddBtn.addEventListener("click", () => void addGlossaryTerm());
glossaryDstInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    void addGlossaryTerm();
  }
});

glossarySeriesSelect.addEventListener("change", async () => {
  activeSeriesSlug = glossarySeriesSelect.value;
  if (detectedSlug && activeSeriesSlug !== detectedSlug) {
    glossaryStore.urlOverrides[detectedSlug] = activeSeriesSlug;
  } else {
    delete glossaryStore.urlOverrides[detectedSlug];
  }
  await persistGlossaryStore();
});

glossaryRenameBtn.addEventListener("click", async () => {
  const current = getActiveSeriesEntry();
  const nextName = window.prompt("Nome da obra para este glossário:", current.name);
  if (!nextName || !nextName.trim()) return;
  glossaryStore.series[activeSeriesSlug] = {
    ...current,
    name: nextName.trim(),
    updatedAt: Date.now(),
  };
  await persistGlossaryStore();
  showMessage(`Obra renomeada para "${nextName.trim()}".`);
});

glossaryExportBtn.addEventListener("click", () => {
  const entry = getActiveSeriesEntry();
  const payload = JSON.stringify(
    { slug: entry.slug, name: entry.name, terms: entry.terms },
    null,
    2
  );
  const blob = new Blob([payload], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `glossario-${entry.slug}.json`;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  showMessage(`Glossário de "${entry.name}" exportado.`);
});

glossaryImportBtn.addEventListener("click", () => glossaryImportFile.click());
glossaryImportFile.addEventListener("change", async () => {
  const file = glossaryImportFile.files?.[0];
  if (!file) return;
  try {
    const raw = await file.text();
    const current = getActiveSeriesEntry();
    const importedTerms = parseImportedGlossaryJson(raw, current.slug, current.name);
    glossaryStore.series[activeSeriesSlug] = {
      ...current,
      terms: { ...current.terms, ...importedTerms },
      updatedAt: Date.now(),
    };
    await persistGlossaryStore();
    showMessage(`Importados ${Object.keys(importedTerms).length} termo(s) em "${current.name}".`);
  } catch (err) {
    showMessage(err instanceof Error ? err.message : String(err), true);
  } finally {
    glossaryImportFile.value = "";
  }
});

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
  showMessage("Abra uma aba de leitor para limpar o cache local.", true);
});

window.addEventListener("unload", () => {
  if (pollTimer !== null) window.clearInterval(pollTimer);
});

void init();
