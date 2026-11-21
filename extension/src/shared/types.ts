/**
 * Shared types & strict Free Tier constants for Scanlator Vogels PT-BR v1.1 (Manifest V3).
 */

export const NATIVE_HOST_NAME = "com.scanlatorvogels.host" as const;

/** Strictly verified Gemini API Free Tier models (R$ 0,00 — NO paid fallback). */
export const FREE_TIER_MODELS = [
  "gemini-3.5-flash-lite",
  "gemini-3.1-flash-lite",
  "gemini-3.6-flash",
  "gemini-3.8-flash",
] as const;

export type FreeTierModel = (typeof FREE_TIER_MODELS)[number];

export const DEFAULT_FREE_MODEL: FreeTierModel = "gemini-3.5-flash-lite";

/** Bump whenever scanlation translation prompt changes to refresh IndexedDB translations. */
export const PROMPT_VERSION = "v3-scanlation-turbo" as const;

export type ExtractionMode = "manga_ocr_v1" | "vision_grid_v1";

export function getExtractionModeForLang(lang: SourceLanguage): ExtractionMode {
  return lang === "ja" ? "manga_ocr_v1" : "vision_grid_v1";
}

export type TranslationStyle = "scanlation" | "informal" | "fiel" | "formal";

export const SOURCE_LANGUAGES = [
  { value: "auto", label: "Auto (Universal)" },
  { value: "ja", label: "Japonês" },
  { value: "ko", label: "Coreano" },
  { value: "zh", label: "Chinês" },
  { value: "en", label: "Inglês" },
  { value: "es", label: "Espanhol" },
  { value: "fr", label: "Francês" },
] as const;

export type SourceLanguage = (typeof SOURCE_LANGUAGES)[number]["value"];

export const READING_DIRECTIONS = [
  { value: "auto", label: "Auto" },
  { value: "rtl", label: "Mangá (Dir → Esq)" },
  { value: "ltr", label: "Webtoon/Comic (Esq → Dir)" },
] as const;

export type ReadingDirection = (typeof READING_DIRECTIONS)[number]["value"];

export interface ExtensionSettings {
  apiKey: string;
  model: FreeTierModel;
  sourceLang: SourceLanguage;
  readingDirection: ReadingDirection;
  style: TranslationStyle;
  uppercase: boolean;
  autoTranslate: boolean; // OFF by default — explicit activation required
  idleTimeoutSec: number;
  backendPort: number;
  prefetchCount: number;
}

export const DEFAULT_SETTINGS: ExtensionSettings = {
  apiKey: "",
  model: DEFAULT_FREE_MODEL,
  sourceLang: "auto",
  readingDirection: "auto",
  style: "scanlation",
  uppercase: true,
  autoTranslate: false,
  idleTimeoutSec: 300,
  backendPort: 8765,
  prefetchCount: 4,
};

export interface SeriesGlossary {
  slug: string;
  name: string;
  terms: Record<string, string>;
  updatedAt: number;
}

export interface GlossaryStore {
  series: Record<string, SeriesGlossary>;
  /** Optional explicit mapping from detected URL slug -> active custom series slug */
  urlOverrides: Record<string, string>;
}

export type EngineLifecycleState = "stopped" | "starting" | "running" | "stopping";

export interface CircuitBreakerStatus {
  state: "CLOSED" | "OPEN" | "HALF_OPEN";
  consecutive_failures: number;
  retry_after_sec: number;
}

export interface QuotaStatus {
  date_utc: string;
  model: string;
  requests_today: number;
  prompt_tokens_today: number;
  output_tokens_today: number;
  total_tokens_today: number;
  last_request_prompt_tokens?: number;
  last_request_output_tokens?: number;
}

export interface BackendHealth {
  status: string;
  pid: number;
  port: number;
  uptime_sec: number;
  pages_processed: number;
  idle_timeout_sec: number;
  idle_remaining_sec: number;
  models_loaded: string[];
  gpu_name: string;
  ram_mb: number;
  torch_vram_allocated_mb: number;
  torch_vram_peak_mb: number;
  torch_vram_reserved_mb: number;
  nvidia_vram_used_mb: number | null;
  nvidia_vram_free_mb: number | null;
  nvidia_vram_total_mb: number | null;
  circuit_breaker: CircuitBreakerStatus;
  free_tier_models: string[];
  default_model: string;
  prompt_version?: string;
  max_batch_pages?: number;
  warming_up?: boolean;
  warmup_done?: boolean;
  has_env_api_key: boolean;
  quota: QuotaStatus;
}

export interface TranslatedBlockInfo {
  id: number;
  source_text: string;
  translated_text: string;
  inpaint_method?: string;
}

export interface TranslatePageResult {
  ok: true;
  prompt_version?: string;
  mime_type: string;
  image_base64: string;
  image_sha256: string;
  width: number;
  height: number;
  no_text: boolean;
  blocks_count: number;
  translated_blocks_count: number;
  cache_hit_local: boolean;
  model: string;
  source_lang?: string;
  detected_lang?: string;
  reading_direction?: string;
  slices_count?: number;
  style: string;
  uppercase: boolean;
  timings_ms: Record<string, number>;
  vram: {
    torch_allocated_mb: number;
    torch_peak_mb: number;
    nvidia_used_mb: number | null;
  };
  circuit_breaker: CircuitBreakerStatus;
  quota: QuotaStatus | null;
  blocks?: TranslatedBlockInfo[];
}

export interface TranslateBatchPageResult {
  page_id: string;
  mime_type: string;
  image_base64: string;
  image_sha256: string;
  width: number;
  height: number;
  no_text: boolean;
  blocks_count: number;
  translated_blocks_count: number;
  cache_hit_local: boolean;
  detected_lang?: string;
  reading_direction?: string;
  slices_count?: number;
  used_fallback?: boolean;
  timings_ms: Record<string, number>;
}

export interface TranslateBatchResult {
  ok: true;
  prompt_version?: string;
  model: string;
  source_lang: string;
  style: string;
  uppercase: boolean;
  pages_count: number;
  fallback_pages: string[];
  timings_ms: Record<string, number>;
  avg_ms_per_page: number;
  vram: {
    torch_allocated_mb: number;
    torch_peak_mb: number;
    nvidia_used_mb: number | null;
  };
  circuit_breaker: CircuitBreakerStatus;
  quota: QuotaStatus | null;
  pages: TranslateBatchPageResult[];
  skipped_errors?: Record<string, string>;
}

export interface TranslatePageError {
  ok: false;
  httpStatus?: number;
  code:
    | "ENGINE_OFFLINE"
    | "CORS_OR_NETWORK_BLOCKED"
    | "CIRCUIT_BREAKER_OPEN"
    | "RATE_LIMITED_429"
    | "UPSTREAM_UNAVAILABLE"
    | "MISSING_API_KEY"
    | "PAID_MODEL_BLOCKED"
    | "INVALID_IMAGE"
    | "UNKNOWN_ERROR";
  message: string;
  retry_after_sec?: number;
  circuit_breaker?: CircuitBreakerStatus;
}

export type TranslatePageResponse = TranslatePageResult | TranslatePageError;
export type TranslateBatchResponse = TranslateBatchResult | TranslatePageError;

export interface EngineActionResponse {
  ok: boolean;
  state: EngineLifecycleState;
  health: BackendHealth | null;
  nativeHostAvailable?: boolean;
  message?: string;
}

export interface CaptureTabResponse {
  ok: boolean;
  dataUrl?: string;
  message?: string;
}

export type RuntimeMessage =
  | { type: "GET_SETTINGS" }
  | { type: "SAVE_SETTINGS"; settings: Partial<ExtensionSettings> }
  | { type: "GET_GLOSSARY_STORE" }
  | { type: "SAVE_GLOSSARY_STORE"; store: GlossaryStore }
  | { type: "GLOSSARY_UPDATED"; store: GlossaryStore }
  | { type: "ENGINE_STATUS" }
  | { type: "ENGINE_START" }
  | { type: "ENGINE_STOP" }
  | { type: "RESET_CIRCUIT_BREAKER" }
  | { type: "UPDATE_IDLE_TIMEOUT"; idleTimeoutSec: number }
  | { type: "CAPTURE_VISIBLE_TAB" }
  | {
      type: "TRANSLATE_IMAGE_URL";
      imageUrl: string;
      pageUrl: string;
      imageBase64?: string;
      forceRetranslate?: boolean;
      directOcr?: boolean;
      glossary?: Record<string, string>;
    }
  | {
      type: "TRANSLATE_BATCH_URLS";
      pageUrl: string;
      glossary?: Record<string, string>;
      items: Array<{
        pageId: string;
        imageUrl: string;
        imageBase64?: string;
        forceRetranslate?: boolean;
      }>;
    }
  | {
      type: "PREPARE_IMAGE_URL";
      imageUrl: string;
      pageUrl: string;
      imageBase64?: string;
    }
  | { type: "CLEAR_TAB_CACHE" }
  | { type: "TRANSLATE_VISIBLE_PAGE" }
  | { type: "TRANSLATE_CHAPTER" }
  | { type: "CANCEL_CHAPTER" }
  | { type: "START_MANUAL_CROP" }
  | { type: "SETTINGS_UPDATED"; settings: ExtensionSettings }
  | { type: "ENGINE_STATE_BROADCAST"; state: EngineLifecycleState; health: BackendHealth | null };
