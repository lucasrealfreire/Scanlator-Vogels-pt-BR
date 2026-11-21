/**
 * Shared types & strict Free Tier constants for Scanlator Vogels PT-BR (Manifest V3).
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

export type TranslationStyle = "scanlation" | "informal" | "fiel" | "formal";

export const SOURCE_LANGUAGES = [
  { value: "auto", label: "Auto (JP/EN)" },
  { value: "ja", label: "Japonês" },
  { value: "en", label: "Inglês" },
  { value: "ko", label: "Coreano" },
  { value: "zh", label: "Chinês" },
] as const;

export type SourceLanguage = (typeof SOURCE_LANGUAGES)[number]["value"];

export interface ExtensionSettings {
  apiKey: string;
  model: FreeTierModel;
  sourceLang: SourceLanguage;
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
  style: "scanlation",
  uppercase: true,
  autoTranslate: false,
  idleTimeoutSec: 300,
  backendPort: 8765,
  prefetchCount: 4,
};

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

export type RuntimeMessage =
  | { type: "GET_SETTINGS" }
  | { type: "SAVE_SETTINGS"; settings: Partial<ExtensionSettings> }
  | { type: "ENGINE_STATUS" }
  | { type: "ENGINE_START" }
  | { type: "ENGINE_STOP" }
  | { type: "RESET_CIRCUIT_BREAKER" }
  | { type: "UPDATE_IDLE_TIMEOUT"; idleTimeoutSec: number }
  | {
      type: "TRANSLATE_IMAGE_URL";
      imageUrl: string;
      pageUrl: string;
      imageBase64?: string;
      forceRetranslate?: boolean;
    }
  | {
      type: "TRANSLATE_BATCH_URLS";
      pageUrl: string;
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
  | { type: "SETTINGS_UPDATED"; settings: ExtensionSettings }
  | { type: "ENGINE_STATE_BROADCAST"; state: EngineLifecycleState; health: BackendHealth | null };
