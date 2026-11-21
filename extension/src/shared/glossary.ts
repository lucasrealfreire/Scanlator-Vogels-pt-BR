/**
 * Per-Series Glossary storage & deterministic URL slug detection (`chrome.storage.local`).
 *
 * Automatically groups all chapters of the same manga/manhwa/webtoon under one glossary,
 * allows renaming/switching active series, supports JSON export/import, and provides a
 * compact deterministic hash (`hashGlossary`) for IndexedDB translation cache keys.
 */

import type { GlossaryStore, SeriesGlossary } from "./types";

const GLOSSARY_STORAGE_KEY = "scanlator_vogels_glossaries_v1";

const SERIES_PREFIX_SEGMENTS = new Set([
  "manga",
  "manhwa",
  "manhua",
  "series",
  "title",
  "titles",
  "comic",
  "comics",
  "webtoon",
  "webtoons",
  "read",
  "obra",
  "obras",
  "book",
  "books",
  "viewer",
  "detail",
  "en",
  "pt",
  "pt-br",
  "es",
  "fr",
  "ko",
  "zh",
  "ja",
  "jp",
]);

const CHAPTER_SEGMENT_RE =
  /^(?:chapter|chap|ch|cap|capitulo|ep|episode|episodio|vol|volume|issue|part|page|read|viewer|list)[-._]?\d.*$/i;

function normalizeSlug(raw: string): string {
  const cleaned = decodeURIComponent(raw || "")
    .toLowerCase()
    .replace(/[^a-z0-9\u00c0-\u024f\u3040-\u9fff\uac00-\ud7af]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 72);
  return cleaned || "obra-padrao";
}

export function humanizeSlug(slug: string): string {
  return slug
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Derive a stable series slug + display name from any manga/manhwa/webtoon reader URL.
 * Strips chapter numbers & viewer suffixes so Chapter 1, Chapter 2, ... share 1 glossary.
 */
export function detectSeriesFromUrl(pageUrl: string): { slug: string; name: string } {
  try {
    const u = new URL(pageUrl);
    const host = u.hostname.replace(/^www\./, "").toLowerCase();
    const parts = u.pathname
      .split("/")
      .map((s) => s.trim())
      .filter(Boolean);

    // Query param support (e.g. Naver/Webtoons `?title_no=12345`)
    const titleNo = u.searchParams.get("title_no") || u.searchParams.get("titleId") || u.searchParams.get("id");

    // Pick first meaningful non-prefix, non-chapter segment
    let candidate = "";
    for (let i = 0; i < parts.length; i++) {
      const seg = parts[i];
      const lower = seg.toLowerCase();
      if (SERIES_PREFIX_SEGMENTS.has(lower)) {
        // If next segment exists and isn't a chapter number, take it right away!
        const nxt = parts[i + 1];
        if (nxt && !CHAPTER_SEGMENT_RE.test(nxt) && !SERIES_PREFIX_SEGMENTS.has(nxt.toLowerCase())) {
          candidate = nxt;
          break;
        }
        continue;
      }
      if (CHAPTER_SEGMENT_RE.test(seg)) continue;
      if (/^\d+$/.test(seg) && parts[i + 1] && !CHAPTER_SEGMENT_RE.test(parts[i + 1])) {
        // e.g. `/12345/solo-leveling/chapter-1` -> prefer the readable slug after ID
        candidate = parts[i + 1];
        break;
      }
      candidate = seg;
      break;
    }

    if (!candidate && titleNo) {
      candidate = `${host}-${titleNo}`;
    }
    if (!candidate) {
      candidate = host || "obra-local";
    }

    // Strip embedded chapter suffix like `solo-leveling-chapter-45`
    candidate = candidate.replace(/[-_](?:chapter|chap|ch|cap|ep|episode)[-_]?\d+.*$/i, "");

    const slug = normalizeSlug(candidate);
    return { slug, name: humanizeSlug(slug) };
  } catch {
    return { slug: "obra-padrao", name: "Obra Padrão" };
  }
}

export function createEmptyGlossaryStore(): GlossaryStore {
  return { series: {}, urlOverrides: {} };
}

export function sanitizeGlossaryStore(raw: unknown): GlossaryStore {
  if (!raw || typeof raw !== "object") return createEmptyGlossaryStore();
  const obj = raw as Partial<GlossaryStore>;
  const series: Record<string, SeriesGlossary> = {};
  if (obj.series && typeof obj.series === "object") {
    for (const [k, val] of Object.entries(obj.series)) {
      if (!val || typeof val !== "object") continue;
      const slug = normalizeSlug(val.slug || k);
      const terms: Record<string, string> = {};
      if (val.terms && typeof val.terms === "object") {
        for (const [tk, tv] of Object.entries(val.terms)) {
          const cleanK = String(tk).trim();
          const cleanV = String(tv).trim();
          if (cleanK && cleanV) terms[cleanK] = cleanV;
        }
      }
      series[slug] = {
        slug,
        name: String(val.name || humanizeSlug(slug)).trim() || humanizeSlug(slug),
        terms,
        updatedAt: typeof val.updatedAt === "number" ? val.updatedAt : Date.now(),
      };
    }
  }
  const urlOverrides: Record<string, string> = {};
  if (obj.urlOverrides && typeof obj.urlOverrides === "object") {
    for (const [k, v] of Object.entries(obj.urlOverrides)) {
      if (typeof v === "string" && v.trim()) {
        urlOverrides[normalizeSlug(k)] = normalizeSlug(v);
      }
    }
  }
  return { series, urlOverrides };
}

export async function loadGlossaryStore(): Promise<GlossaryStore> {
  const stored = await chrome.storage.local.get(GLOSSARY_STORAGE_KEY);
  return sanitizeGlossaryStore(stored[GLOSSARY_STORAGE_KEY]);
}

export async function saveGlossaryStore(store: GlossaryStore): Promise<GlossaryStore> {
  const clean = sanitizeGlossaryStore(store);
  await chrome.storage.local.set({ [GLOSSARY_STORAGE_KEY]: clean });
  return clean;
}

export function resolveActiveSeriesForUrl(
  store: GlossaryStore,
  pageUrl: string
): { detectedSlug: string; activeSlug: string; series: SeriesGlossary } {
  const detected = detectSeriesFromUrl(pageUrl);
  const activeSlug = store.urlOverrides[detected.slug] || detected.slug;
  const existing = store.series[activeSlug];
  if (existing) {
    return { detectedSlug: detected.slug, activeSlug, series: existing };
  }
  return {
    detectedSlug: detected.slug,
    activeSlug,
    series: {
      slug: activeSlug,
      name: activeSlug === detected.slug ? detected.name : humanizeSlug(activeSlug),
      terms: {},
      updatedAt: 0,
    },
  };
}

/** Deterministic fast hex hash of active glossary terms for IndexedDB cache versioning. */
export function hashGlossary(terms: Record<string, string> | undefined | null): string {
  if (!terms) return "g0";
  const entries = Object.entries(terms)
    .map(([k, v]) => [k.trim(), v.trim()] as const)
    .filter(([k, v]) => k.length > 0 && v.length > 0)
    .sort((a, b) => a[0].localeCompare(b[0]));
  if (entries.length === 0) return "g0";

  const canonical = JSON.stringify(entries);
  let hash = 0x811c9dc5;
  for (let i = 0; i < canonical.length; i++) {
    hash ^= canonical.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return `g${(hash >>> 0).toString(16)}`;
}

/** Parse imported JSON (accepts either a full `{ series }` export, `{ name, terms }`, or plain `{ "Src": "Dst" }`). */
export function parseImportedGlossaryJson(
  rawJson: string,
  targetSlug: string,
  targetName: string
): Record<string, string> {
  const parsed = JSON.parse(rawJson) as Record<string, unknown>;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Arquivo JSON de glossário inválido (esperado objeto).");
  }

  let sourceObj: Record<string, unknown> = parsed;
  if (parsed.terms && typeof parsed.terms === "object" && !Array.isArray(parsed.terms)) {
    sourceObj = parsed.terms as Record<string, unknown>;
  } else if (parsed.series && typeof parsed.series === "object" && !Array.isArray(parsed.series)) {
    const seriesMap = parsed.series as Record<string, { terms?: Record<string, unknown> }>;
    const first = seriesMap[targetSlug] || Object.values(seriesMap)[0];
    if (first?.terms && typeof first.terms === "object") {
      sourceObj = first.terms;
    }
  }

  const cleanTerms: Record<string, string> = {};
  for (const [k, v] of Object.entries(sourceObj)) {
    if (typeof v === "string" && k.trim() && v.trim()) {
      cleanTerms[k.trim()] = v.trim();
    }
  }
  if (Object.keys(cleanTerms).length === 0) {
    throw new Error(`Nenhum par "Original": "PT-BR" válido encontrado para ${targetName}.`);
  }
  return cleanTerms;
}
