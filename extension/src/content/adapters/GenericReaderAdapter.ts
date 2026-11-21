import type { ReaderAdapter } from "./types";

const LAZY_SRC_ATTRS = [
  "data-src",
  "data-original",
  "data-lazy-src",
  "data-url",
  "data-cfsrc",
  "data-actualsrc",
  "data-image",
  "data-hi-res-src",
];

const LAZY_SRCSET_ATTRS = ["data-srcset", "data-lazy-srcset", "srcset"];

const EXCLUDED_HINT_RE =
  /\b(logo|avatar|icon|banner|thumb|thumbnail|cover|emoji|badge|button|sponsor|ads?|promo|gravatar|spinner|loading|placeholder)\b/i;

function pickLargestFromSrcset(srcset: string): string | null {
  const candidates = srcset
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((entry) => {
      const tokens = entry.split(/\s+/);
      const url = tokens[0];
      const desc = tokens[1] || "1x";
      let weight = 1;
      if (desc.endsWith("w")) weight = parseFloat(desc) || 1;
      else if (desc.endsWith("x")) weight = (parseFloat(desc) || 1) * 1000;
      return { url, weight };
    })
    .filter((c) => Boolean(c.url));

  if (candidates.length === 0) return null;
  candidates.sort((a, b) => b.weight - a.weight);
  return candidates[0].url;
}

function toAbsoluteUrl(raw: string | null): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  if (!trimmed || trimmed.startsWith("about:") || trimmed.startsWith("javascript:")) {
    return null;
  }
  // Ignore tiny inline SVG/GIF placeholders
  if (trimmed.startsWith("data:image/svg") || trimmed.startsWith("data:image/gif")) {
    return null;
  }
  try {
    return new URL(trimmed, window.location.href).href;
  } catch {
    return null;
  }
}

/**
 * Universal fallback adapter working across arbitrary manga/comic/webtoon readers
 * via real geometry, aspect ratio, lazy-load attribute, and srcset heuristics.
 */
export class GenericReaderAdapter implements ReaderAdapter {
  readonly name = "GenericReaderAdapter";

  matches(_url: URL, _doc: Document): boolean {
    return true;
  }

  resolveImageUrl(img: HTMLImageElement): string | null {
    // 1. Preserve original URL if our overlay manager already swapped `img.src`
    const savedOriginal = img.dataset.svOrigUrl;
    if (savedOriginal) return savedOriginal;

    // 2. Check lazy-loading attributes first when `src` is missing or placeholder
    const rawSrc = img.getAttribute("src") || "";
    const looksPlaceholder =
      !rawSrc ||
      rawSrc.startsWith("data:image/gif") ||
      rawSrc.startsWith("data:image/svg") ||
      /loading|placeholder|blank|spacer|pixel/i.test(rawSrc);

    if (looksPlaceholder) {
      for (const attr of LAZY_SRC_ATTRS) {
        const val = toAbsoluteUrl(img.getAttribute(attr));
        if (val) return val;
      }
      for (const attr of LAZY_SRCSET_ATTRS) {
        const val = img.getAttribute(attr);
        if (val) {
          const best = toAbsoluteUrl(pickLargestFromSrcset(val));
          if (best) return best;
        }
      }
    }

    // 3. Check <picture><source srcset=...> parent if present
    const parent = img.parentElement;
    if (parent && parent.tagName === "PICTURE") {
      const sources = Array.from(parent.querySelectorAll("source"));
      for (const srcEl of sources) {
        const ss = srcEl.getAttribute("srcset") || srcEl.getAttribute("data-srcset");
        if (ss) {
          const best = toAbsoluteUrl(pickLargestFromSrcset(ss));
          if (best) return best;
        }
      }
    }

    // 4. Highest-res candidate in img srcset
    for (const attr of LAZY_SRCSET_ATTRS) {
      const val = img.getAttribute(attr);
      if (val) {
        const best = toAbsoluteUrl(pickLargestFromSrcset(val));
        if (best) return best;
      }
    }

    // 5. Standard currentSrc / src / lazy attrs
    const direct = toAbsoluteUrl(img.currentSrc || img.src);
    if (direct && !direct.startsWith("blob:")) return direct;

    for (const attr of LAZY_SRC_ATTRS) {
      const val = toAbsoluteUrl(img.getAttribute(attr));
      if (val) return val;
    }

    return direct;
  }

  isComicPageCandidate(img: HTMLImageElement): boolean {
    if (!img.isConnected) return false;
    if (img.dataset.svIgnore === "1") return false;

    const url = this.resolveImageUrl(img);
    if (!url) return false;

    // Exclude invisible elements
    const style = window.getComputedStyle(img);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
      return false;
    }

    const rect = img.getBoundingClientRect();
    const natW = img.naturalWidth || 0;
    const natH = img.naturalHeight || 0;
    const effW = Math.max(natW, Math.round(rect.width));
    const effH = Math.max(natH, Math.round(rect.height));

    // Must be large enough to be a comic/manga/webtoon page (excludes icons, UI sprites, thumbs)
    if (effW < 260 || effH < 320) return false;

    // Aspect ratio bounds (supports double-spreads up to ~2.1 and tall webtoon strips down to 0.08)
    const ratio = effW / Math.max(1, effH);
    if (ratio > 2.25 || ratio < 0.06) return false;

    // Exclude small sidebar thumbnails or obvious non-comic UI images
    const hintText = `${img.className} ${img.id} ${img.alt}`;
    if (EXCLUDED_HINT_RE.test(hintText) && effW < 620) {
      return false;
    }

    // Exclude links wrapping tiny promo banners/covers in sidebars/grids
    const anchor = img.closest("a");
    if (anchor && rect.width < 420 && rect.height < 520) {
      return false;
    }

    return true;
  }

  findCandidateImages(doc: Document): HTMLImageElement[] {
    const imgs = Array.from(doc.querySelectorAll("img"));
    const candidates = imgs.filter((img) => this.isComicPageCandidate(img));

    // Sort strictly in document visual reading order (top -> bottom, left -> right)
    candidates.sort((a, b) => {
      const ra = a.getBoundingClientRect();
      const rb = b.getBoundingClientRect();
      const dy = ra.top - rb.top;
      if (Math.abs(dy) > 24) return dy;
      return ra.left - rb.left;
    });
    return candidates;
  }
}

const ADAPTER_REGISTRY: ReaderAdapter[] = [];

export function registerReaderAdapter(adapter: ReaderAdapter): void {
  ADAPTER_REGISTRY.unshift(adapter);
}

export function resolveReaderAdapter(doc: Document = document): ReaderAdapter {
  const url = new URL(window.location.href);
  for (const adapter of ADAPTER_REGISTRY) {
    if (adapter.matches(url, doc)) return adapter;
  }
  return new GenericReaderAdapter();
}
