import {
  DEFAULT_FREE_MODEL,
  DEFAULT_SETTINGS,
  FREE_TIER_MODELS,
  type ExtensionSettings,
  type FreeTierModel,
  type SourceLanguage,
  type TranslationStyle,
} from "./types";

const STORAGE_KEY = "scanlator_vogels_settings_v1";

function sanitizeModel(model: unknown): FreeTierModel {
  if (typeof model === "string" && (FREE_TIER_MODELS as readonly string[]).includes(model)) {
    return model as FreeTierModel;
  }
  return DEFAULT_FREE_MODEL;
}

function sanitizeSourceLang(lang: unknown): SourceLanguage {
  if (lang === "auto" || lang === "ja" || lang === "en" || lang === "ko" || lang === "zh") {
    return lang;
  }
  return "auto";
}

function sanitizeStyle(style: unknown): TranslationStyle {
  if (style === "scanlation" || style === "informal" || style === "fiel" || style === "formal") {
    return style;
  }
  return "scanlation";
}

export async function loadSettings(): Promise<ExtensionSettings> {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const raw = (stored[STORAGE_KEY] ?? {}) as Partial<ExtensionSettings>;
  return {
    apiKey: typeof raw.apiKey === "string" ? raw.apiKey.trim() : DEFAULT_SETTINGS.apiKey,
    model: sanitizeModel(raw.model),
    sourceLang: sanitizeSourceLang(raw.sourceLang),
    style: sanitizeStyle(raw.style),
    uppercase: typeof raw.uppercase === "boolean" ? raw.uppercase : DEFAULT_SETTINGS.uppercase,
    autoTranslate:
      typeof raw.autoTranslate === "boolean" ? raw.autoTranslate : DEFAULT_SETTINGS.autoTranslate,
    idleTimeoutSec:
      typeof raw.idleTimeoutSec === "number" && raw.idleTimeoutSec >= 30 && raw.idleTimeoutSec <= 3600
        ? Math.round(raw.idleTimeoutSec)
        : DEFAULT_SETTINGS.idleTimeoutSec,
    backendPort:
      typeof raw.backendPort === "number" && raw.backendPort >= 1024 && raw.backendPort <= 65535
        ? Math.round(raw.backendPort)
        : DEFAULT_SETTINGS.backendPort,
    prefetchCount:
      typeof raw.prefetchCount === "number" && raw.prefetchCount >= 3 && raw.prefetchCount <= 6
        ? Math.round(raw.prefetchCount)
        : DEFAULT_SETTINGS.prefetchCount,
  };
}

export async function saveSettings(patch: Partial<ExtensionSettings>): Promise<ExtensionSettings> {
  const current = await loadSettings();
  const next: ExtensionSettings = {
    ...current,
    ...patch,
    model: sanitizeModel(patch.model ?? current.model),
    sourceLang: sanitizeSourceLang(patch.sourceLang ?? current.sourceLang),
    style: sanitizeStyle(patch.style ?? current.style),
    apiKey: typeof patch.apiKey === "string" ? patch.apiKey.trim() : current.apiKey,
  };
  await chrome.storage.local.set({ [STORAGE_KEY]: next });
  return next;
}
