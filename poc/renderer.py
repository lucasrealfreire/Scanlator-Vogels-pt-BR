"""Fast scanlation-grade PT-BR text renderer with zero-tofu glyph filtering & moderate hyphenation.

Features:
- Free open-source SIL OFL comic font (`ComicNeue-Bold.ttf`) with full Brazilian
  Portuguese accent coverage (ÁÉÍÓÚÂÊÔÃÕÇáéíóúâêôãõç).
- Pure-Python TrueType `cmap` inspection + Unicode NFKC & CJK punctuation normalization,
  mathematically guaranteeing ZERO broken tofu glyphs (`□`).
- Strict preservation of spaces between every word during wrapping and hyphenation.
- Whole-word-first binary search: keeps Portuguese words intact whenever legible; uses
  `pyphen` (`pt_BR`) syllabic hyphenation strictly as a last resort, capped at MAXIMUM 1
  hyphen per word (never slicing a word across 3 lines).
- Precomputed per-row contour spans (one pass per bubble) for < 0.5s CPU rendering.
"""

from __future__ import annotations

import io
import logging
import re
import struct
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

from poc.config import FONTS_DIR
from poc.inpainter import extract_solid_bubble_safe_mask
from poc.schemas import BoundingBox, TextBlock

logger = logging.getLogger(__name__)

FONT_FILENAME = "ComicNeue-Bold.ttf"
FONT_DOWNLOAD_URLS: tuple[str, ...] = (
    "https://github.com/google/fonts/raw/main/ofl/comicneue/ComicNeue-Bold.ttf",
    "https://raw.githubusercontent.com/google/fonts/main/ofl/comicneue/ComicNeue-Bold.ttf",
)
WINDOWS_FALLBACK_FONTS: tuple[str, ...] = (
    r"C:\Windows\Fonts\comicbd.ttf",
    r"C:\Windows\Fonts\comic.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
)

# Explicit punctuation/symbol normalization before NFKC + font cmap filtering
_PUNCT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("\u2026", "..."),  # …
    ("\u22ef", "..."),  # ⋯
    ("\ufe19", "..."),  # ︙
    ("\u2025", ".."),   # ‥
    ("\u301c", "~"),    # 〜 (wave dash)
    ("\uff5e", "~"),    # ～ (fullwidth tilde)
    ("\u3030", "~"),    # 〰
    ("\u30fc", "-"),    # ー (katakana prolonged sound mark)
    ("\u2015", "-"),    # ―
    ("\u2014", "-"),    # — (em dash)
    ("\u2013", "-"),    # – (en dash)
    ("\u2212", "-"),    # −
    ("\u2018", "'"),    # ‘
    ("\u2019", "'"),    # ’
    ("\u201a", "'"),    # ‚
    ("\u201b", "'"),    # ‛
    ("\u201c", '"'),    # “
    ("\u201d", '"'),    # ”
    ("\u201e", '"'),    # „
    ("\u300c", '"'),    # 「
    ("\u300d", '"'),    # 」
    ("\u300e", '"'),    # 『
    ("\u300f", '"'),    # 』
    ("\u00ab", '"'),    # «
    ("\u00bb", '"'),    # »
    ("\u3001", ", "),   # 、
    ("\u3002", ". "),   # 。
    ("\uff01", "!"),    # ！
    ("\uff1f", "?"),    # ？
    ("\u203c", "!!"),   # ‼
    ("\u2049", "!?"),   # ⁉
    ("\u2048", "?!"),   # ⁈
    ("\u30fb", " "),    # ・
    ("\u00b7", " "),    # ·
    ("\u2022", " "),    # •
    ("\u3000", " "),    # ideographic space
    ("\u00a0", " "),    # nbsp
    ("\u200b", ""),     # zero-width space
    ("\ufeff", ""),     # BOM
    ("\u00ad", ""),     # soft hyphen
)


@dataclass(frozen=True)
class PlacedLine:
    """Single rendered line with exact canvas coordinates guaranteed inside bubble contour."""

    text: str
    y_top: float
    x_center: float
    safe_left: float
    safe_right: float


@dataclass(frozen=True)
class FittedLayout:
    font: ImageFont.FreeTypeFont
    font_size: int
    stroke_w: int
    lines: list[PlacedLine]


@dataclass(frozen=True)
class PrecomputedContour:
    """Precomputed per-row horizontal spans of a bubble's eroded safe interior mask."""

    win: BoundingBox
    height: int
    y_min: int
    y_max: int
    cy_local: float
    max_width: float
    row_left: np.ndarray
    row_right: np.ndarray
    valid_prefix: np.ndarray


@lru_cache(maxsize=1)
def _get_ptbr_hyphenator():
    try:
        import pyphen

        return pyphen.Pyphen(lang="pt_BR")
    except Exception as exc:
        logger.debug("pyphen pt_BR indisponivel (%s).", exc)
        return None


@lru_cache(maxsize=64)
def _load_font_cached(font_path_str: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path_str, size=size)


@lru_cache(maxsize=16)
def get_font_supported_codepoints(font_path_str: str) -> frozenset[int]:
    """Parse TrueType/OpenType `cmap` table directly to obtain exact supported Unicode codepoints.

    Zero external dependencies; runs once per font file in < 1 ms and prevents tofu boxes (`□`).
    """
    try:
        data = Path(font_path_str).read_bytes()
        if len(data) < 12:
            return frozenset()
        num_tables = struct.unpack_from(">H", data, 4)[0]
        cmap_offset = None
        for i in range(num_tables):
            off = 12 + i * 16
            if off + 16 > len(data):
                break
            tag = data[off : off + 4]
            if tag == b"cmap":
                cmap_offset = struct.unpack_from(">I", data, off + 8)[0]
                break
        if cmap_offset is None or cmap_offset + 4 > len(data):
            return frozenset()

        num_subtables = struct.unpack_from(">H", data, cmap_offset + 2)[0]
        codepoints: set[int] = set()
        for i in range(num_subtables):
            rec_off = cmap_offset + 4 + i * 8
            if rec_off + 8 > len(data):
                break
            platform_id, _encoding_id, sub_rel = struct.unpack_from(">HHI", data, rec_off)
            if platform_id not in (0, 3):  # Unicode or Windows Unicode
                continue
            sub_off = cmap_offset + sub_rel
            if sub_off + 4 > len(data):
                continue
            fmt = struct.unpack_from(">H", data, sub_off)[0]
            if fmt == 4 and sub_off + 14 <= len(data):
                seg_count = struct.unpack_from(">H", data, sub_off + 6)[0] // 2
                end_off = sub_off + 14
                start_off = end_off + seg_count * 2 + 2
                delta_off = start_off + seg_count * 2
                range_off = delta_off + seg_count * 2
                for seg in range(seg_count):
                    end_c = struct.unpack_from(">H", data, end_off + seg * 2)[0]
                    start_c = struct.unpack_from(">H", data, start_off + seg * 2)[0]
                    id_delta = struct.unpack_from(">h", data, delta_off + seg * 2)[0]
                    id_range = struct.unpack_from(">H", data, range_off + seg * 2)[0]
                    if start_c == 0xFFFF and end_c == 0xFFFF:
                        continue
                    for cp in range(start_c, end_c + 1):
                        if id_range == 0:
                            gid = (cp + id_delta) & 0xFFFF
                        else:
                            gid_off = (range_off + seg * 2) + id_range + (cp - start_c) * 2
                            if gid_off + 2 > len(data):
                                continue
                            raw_gid = struct.unpack_from(">H", data, gid_off)[0]
                            gid = ((raw_gid + id_delta) & 0xFFFF) if raw_gid != 0 else 0
                        if gid != 0:
                            codepoints.add(cp)
            elif fmt == 12 and sub_off + 16 <= len(data):
                n_groups = struct.unpack_from(">I", data, sub_off + 12)[0]
                grp_off = sub_off + 16
                for g in range(n_groups):
                    if grp_off + g * 12 + 12 > len(data):
                        break
                    start_c, end_c, start_gid = struct.unpack_from(">III", data, grp_off + g * 12)
                    if start_gid == 0 and start_c == end_c:
                        continue
                    for cp in range(start_c, min(end_c + 1, 0x110000)):
                        codepoints.add(cp)
        return frozenset(codepoints)
    except Exception as exc:
        logger.debug("Falha ao ler tabela cmap da fonte %s: %s", font_path_str, exc)
        return frozenset()


def normalize_text_for_font(text: str, font_path: Path) -> str:
    """Normalize Unicode/CJK punctuation and strip any glyph unsupported by `font_path`.

    Guarantees zero tofu boxes (`□`) and preserves clean single spaces between words.
    """
    if not text:
        return ""

    out = text
    for src, dst in _PUNCT_REPLACEMENTS:
        if src in out:
            out = out.replace(src, dst)

    out = unicodedata.normalize("NFKC", out)
    # Collapse repeated decorative tildes like "~~~~" into at most one "~" or remove if unsupported
    out = re.sub(r"~{2,}", "~", out)
    out = re.sub(r"\.{4,}", "...", out)

    supported = get_font_supported_codepoints(str(font_path))
    if supported:
        filtered_chars: list[str] = []
        for ch in out:
            if ch in ("\n", "\r", "\t", " "):
                filtered_chars.append(" " if ch == "\t" else ch)
                continue
            cp = ord(ch)
            if cp in supported:
                filtered_chars.append(ch)
            else:
                # Try ASCII-stripped fallback for rare accented/symbol characters before dropping
                decomposed = unicodedata.normalize("NFKD", ch)
                fallback = "".join(c for c in decomposed if ord(c) in supported)
                if fallback:
                    filtered_chars.append(fallback)
                # Otherwise drop cleanly (no tofu box!)
        out = "".join(filtered_chars)

    # Normalize spacing per line without ever merging separate words
    cleaned_lines = [re.sub(r" {2,}", " ", ln).strip() for ln in out.splitlines()]
    cleaned = "\n".join(ln for ln in cleaned_lines if ln)
    # Clean stray space before punctuation introduced by stripped symbols (e.g. "CARAMBA !")
    cleaned = re.sub(r"\s+([!?.,;:])", r"\1", cleaned)
    return cleaned.strip()


def ensure_manga_font(fonts_dir: Path = FONTS_DIR) -> Path:
    """Ensure the free SIL OFL comic font is cached locally, falling back to system fonts."""
    fonts_dir.mkdir(parents=True, exist_ok=True)
    target = fonts_dir / FONT_FILENAME
    if target.exists() and target.stat().st_size > 20_000:
        return target

    for url in FONT_DOWNLOAD_URLS:
        try:
            logger.info("Baixando fonte livre SIL OFL (%s)...", url)
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            if len(resp.content) > 20_000:
                target.write_bytes(resp.content)
                logger.info("Fonte salva em %s.", target)
                return target
        except Exception as exc:
            logger.warning("Nao foi possivel baixar fonte de %s: %s", url, exc)

    for fallback in WINDOWS_FALLBACK_FONTS:
        p = Path(fallback)
        if p.exists():
            logger.warning("Usando fonte fallback do sistema: %s", p)
            return p

    raise FileNotFoundError("Nenhuma fonte TrueType disponivel para renderizacao.")


def _precompute_contour(safe_mask: np.ndarray, win: BoundingBox) -> Optional[PrecomputedContour]:
    """Compute per-row widest safe span ONCE per bubble so binary search runs in O(1)."""
    mh, mw = safe_mask.shape[:2]
    row_left = np.full(mh, -1, dtype=np.int32)
    row_right = np.full(mh, -1, dtype=np.int32)

    nonzero_rows = np.where(safe_mask.any(axis=1))[0]
    if nonzero_rows.size == 0:
        return None

    for r in nonzero_rows:
        row = safe_mask[r] > 0
        nz = np.flatnonzero(row)
        first, last = int(nz[0]), int(nz[-1]) + 1
        if last - first == nz.size:
            row_left[r] = first
            row_right[r] = last
        else:
            padded = np.concatenate(([False], row, [False]))
            diffs = np.diff(padded.astype(np.int8))
            starts = np.flatnonzero(diffs == 1)
            ends = np.flatnonzero(diffs == -1)
            best = int(np.argmax(ends - starts))
            row_left[r] = int(starts[best])
            row_right[r] = int(ends[best])

    widths = np.maximum(0, row_right - row_left)
    valid_rows = np.flatnonzero(widths >= 14)
    if valid_rows.size == 0:
        return None

    y_min = int(valid_rows[0])
    y_max = int(valid_rows[-1])
    w_slice = widths[y_min : y_max + 1].astype(np.float64)
    weights = w_slice ** 2
    if weights.sum() > 0:
        cy_local = float(y_min + np.sum(np.arange(len(w_slice)) * weights) / weights.sum())
    else:
        cy_local = (y_min + y_max) / 2.0

    invalid = (row_left < 0).astype(np.int32)
    valid_prefix = np.zeros(mh + 1, dtype=np.int32)
    valid_prefix[1:] = np.cumsum(invalid)

    return PrecomputedContour(
        win=win,
        height=mh,
        y_min=y_min,
        y_max=y_max,
        cy_local=cy_local,
        max_width=float(widths.max()),
        row_left=row_left,
        row_right=row_right,
        valid_prefix=valid_prefix,
    )


def _get_spans_fast(
    contour: PrecomputedContour,
    start_y_local: float,
    num_lines: int,
    line_height: float,
    line_step: float,
) -> Optional[list[tuple[float, float, float]]]:
    """O(1) safe horizontal span lookup per line using precomputed row spans."""
    mh = contour.height
    rl = contour.row_left
    rr = contour.row_right
    vp = contour.valid_prefix
    wx1 = contour.win.x1
    wy1 = contour.win.y1

    spans: list[tuple[float, float, float]] = []
    for i in range(num_lines):
        ly1 = int(np.floor(start_y_local + i * line_step))
        ly2 = int(np.ceil(start_y_local + i * line_step + line_height))
        if ly1 < 0 or ly2 > mh or ly2 <= ly1:
            return None
        if vp[ly2] - vp[ly1] > 0:
            return None
        lx1 = int(rl[ly1:ly2].max()) + 1
        lx2 = int(rr[ly1:ly2].min()) - 1
        if lx2 - lx1 < 14:
            return None
        spans.append((float(wy1 + start_y_local + i * line_step), float(wx1 + lx1), float(wx1 + lx2)))
    return spans


def _measure_line_metrics(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.FreeTypeFont,
    stroke_w: int,
    *,
    uppercase: bool,
) -> tuple[float, float]:
    if uppercase:
        ref_bbox = draw.textbbox((0, 0), "ÁÉÍÓÚÂÊÔÃÕÇ", font=font, stroke_width=stroke_w)
        cap_h = float(ref_bbox[3] - ref_bbox[1])
        return cap_h, cap_h * 1.04
    ascent, descent = font.getmetrics()
    lh = float(ascent + descent + stroke_w * 2)
    return lh, lh * 1.08


def _alpha_len(word: str) -> int:
    return sum(1 for c in word if c.isalpha())


def _hyphenate_ptbr_word_once(
    word: str,
    max_width: float,
    measure_fn,
    *,
    min_part_chars: int = 3,
    force_single_char_fallback: bool = False,
) -> list[str]:
    """Split a long Portuguese word into AT MOST 2 parts (max 1 hyphen!) on a syllable boundary.

    - Never hyphenates words shorter than 9 alphabetic characters (prevents `EN-TENDI.`).
    - Never produces 3+ fragments (prevents `AVEN-TUREI-ROS`).
    - Both halves must contain >= 3 alphabetic characters.
    """
    if measure_fn(word) <= max_width:
        return [word]

    # Separate leading/trailing punctuation cleanly
    lead = ""
    trail = ""
    core = word
    while core and not core[0].isalpha():
        lead += core[0]
        core = core[1:]
    while core and not core[-1].isalpha():
        trail = core[-1] + trail
        core = core[:-1]

    if len(core) < 9 and not force_single_char_fallback:
        return [word]

    dic = _get_ptbr_hyphenator()
    if dic is not None and len(core) >= min_part_chars * 2:
        best_pair: Optional[tuple[str, str]] = None
        for left_s, right_s in dic.iterate(core):
            if _alpha_len(left_s) < min_part_chars or _alpha_len(right_s) < min_part_chars:
                continue
            p1 = f"{lead}{left_s}-"
            p2 = f"{right_s}{trail}"
            if measure_fn(p1) <= max_width and measure_fn(p2) <= max_width:
                if best_pair is None or abs(len(p1) - len(p2)) < abs(
                    len(best_pair[0]) - len(best_pair[1])
                ):
                    best_pair = (p1, p2)
        if best_pair is not None:
            return [best_pair[0], best_pair[1]]

    if not force_single_char_fallback or len(core) < 6:
        return [word]

    # Absolute last resort at minimum font size: still capped at 1 single balanced split!
    mid = len(core) // 2
    p1 = f"{lead}{core[:mid]}-"
    p2 = f"{core[mid:]}{trail}"
    if measure_fn(p1) <= max_width and measure_fn(p2) <= max_width:
        return [p1, p2]
    return [word]


def _wrap_words_into_spans(
    words: list[str],
    spans: list[tuple[float, float, float]],
    measure_fn,
    space_w: float,
) -> Optional[list[str]]:
    """DP-balanced wrap preserving exact spaces between words across each span's width."""
    k = len(spans)
    n = len(words)
    if n == 0 or k == 0 or n < k:
        return None

    widths = [max(0.0, sp[2] - sp[1]) for sp in spans]
    max_any_w = max(widths)
    word_w = [measure_fn(w) for w in words]

    pref = [0.0] * (n + 1)
    for idx, ww in enumerate(word_w):
        pref[idx + 1] = pref[idx] + ww

    INF = 1e18
    dp = [[INF] * (n + 1) for _ in range(k + 1)]
    parent = [[-1] * (n + 1) for _ in range(k + 1)]
    dp[0][0] = 0.0

    for line_i in range(k):
        max_w = widths[line_i]
        rem_lines = k - line_i - 1
        for start_w in range(line_i, n - rem_lines):
            if dp[line_i][start_w] >= INF:
                continue
            for end_w in range(start_w + 1, n - rem_lines + 1):
                approx = (pref[end_w] - pref[start_w]) + space_w * (end_w - start_w - 1)
                if approx > max_w * 1.15 and approx > max_any_w * 1.05:
                    break
                seg = words[start_w] if end_w == start_w + 1 else " ".join(words[start_w:end_w])
                w_px = word_w[start_w] if end_w == start_w + 1 else measure_fn(seg)
                if w_px > max_w:
                    break
                fill_ratio = w_px / max(1.0, max_w)
                penalty = (1.0 - fill_ratio) ** 2
                if end_w - start_w == 1 and fill_ratio < 0.35 and k > 1:
                    penalty += 0.30
                cost = dp[line_i][start_w] + penalty
                if cost < dp[line_i + 1][end_w]:
                    dp[line_i + 1][end_w] = cost
                    parent[line_i + 1][end_w] = start_w

    if dp[k][n] >= INF:
        return None

    result: list[str] = [""] * k
    cur = n
    for line_i in range(k - 1, -1, -1):
        prev = parent[line_i + 1][cur]
        if prev < 0:
            return None
        # Always join tokens on the same line with explicit ASCII space
        result[line_i] = " ".join(words[prev:cur])
        cur = prev
    return result


def _fit_text_in_contour(
    draw: ImageDraw.ImageDraw,
    text: str,
    contour: PrecomputedContour,
    font_path: Path,
    *,
    uppercase: bool,
    use_stroke: bool,
    min_font_size: int = 10,
    max_font_size: int = 34,
) -> Optional[FittedLayout]:
    """Whole-word-first contour fitting; hyphenates ONLY as last resort (max 1 hyphen/word)."""
    raw_words = [w for p in text.splitlines() for w in p.split() if w.strip()]
    if not raw_words:
        return None

    safe_h = contour.y_max - contour.y_min + 1
    if safe_h < 14:
        return None

    font_path_str = str(font_path)

    def _try_font_size(
        size: int,
        *,
        use_syllabic_hyph: bool,
        force_char_hyph: bool,
    ) -> Optional[FittedLayout]:
        stroke_w = max(2, size // 7) if use_stroke else 0
        font = _load_font_cached(font_path_str, size)
        width_cache: dict[str, float] = {}

        def measure(s: str) -> float:
            cached = width_cache.get(s)
            if cached is not None:
                return cached
            bbox = draw.textbbox((0, 0), s, font=font, stroke_width=stroke_w)
            w_val = float(bbox[2] - bbox[0])
            width_cache[s] = w_val
            return w_val

        space_w = max(2.0, measure("A A") - 2.0 * measure("A"))
        lh, line_step = _measure_line_metrics(draw, font, stroke_w, uppercase=uppercase)

        words = list(raw_words)
        if use_syllabic_hyph or force_char_hyph:
            target_w = max(24.0, contour.max_width * 0.92)
            expanded: list[str] = []
            for w in words:
                if measure(w) > target_w:
                    expanded.extend(
                        _hyphenate_ptbr_word_once(
                            w,
                            target_w,
                            measure,
                            force_single_char_fallback=force_char_hyph,
                        )
                    )
                else:
                    expanded.append(w)
            words = expanded

        if any(measure(w) > contour.max_width - 2.0 for w in words):
            return None

        max_k = min(len(words), max(1, int((safe_h - lh) // line_step) + 1))
        best_layout: Optional[FittedLayout] = None
        best_score = 1e18

        for k in range(1, max_k + 1):
            total_h = lh + (k - 1) * line_step
            if total_h > safe_h:
                break

            base_y = contour.cy_local - total_h / 2.0
            nudge_offsets = (0.0, -safe_h * 0.06, safe_h * 0.06, -safe_h * 0.12, safe_h * 0.12)
            for dy in nudge_offsets:
                sy = float(np.clip(base_y + dy, contour.y_min, contour.y_max - total_h + 1))
                spans = _get_spans_fast(contour, sy, k, lh, line_step)
                if spans is None:
                    continue
                wrapped = _wrap_words_into_spans(words, spans, measure, space_w)
                if wrapped is None:
                    continue

                y_dev = abs((sy + total_h / 2.0) - contour.cy_local) / max(1.0, safe_h)
                score = y_dev * 1.5 + k * 0.05
                if score < best_score:
                    best_score = score
                    median_cx = float(np.median([(sp[1] + sp[2]) / 2.0 for sp in spans]))
                    placed: list[PlacedLine] = []
                    for line_txt, (y_glob, lx1, lx2) in zip(wrapped, spans):
                        lw = measure(line_txt)
                        span_cx = (lx1 + lx2) / 2.0
                        desired_cx = span_cx * 0.60 + median_cx * 0.40
                        clamped_cx = float(np.clip(desired_cx, lx1 + lw / 2.0, lx2 - lw / 2.0))
                        placed.append(
                            PlacedLine(
                                text=line_txt,
                                y_top=y_glob,
                                x_center=clamped_cx,
                                safe_left=lx1,
                                safe_right=lx2,
                            )
                        )
                    best_layout = FittedLayout(
                        font=font,
                        font_size=size,
                        stroke_w=stroke_w,
                        lines=placed,
                    )
                break

        return best_layout

    def _binary_search(*, use_syllabic_hyph: bool) -> Optional[FittedLayout]:
        best_found: Optional[FittedLayout] = None
        lo, hi = min_font_size, max_font_size
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = _try_font_size(mid, use_syllabic_hyph=use_syllabic_hyph, force_char_hyph=False)
            if cand is not None:
                best_found = cand
                lo = mid + 1
            else:
                hi = mid - 1
        return best_found

    # Pass 1: ALWAYS prefer intact whole words (accepting a slightly smaller font before hyphenating!)
    best_clean = _binary_search(use_syllabic_hyph=False)

    # Pass 2: Hyphenate STRICTLY as last resort when whole-word font dropped below legible minimum
    # (e.g. < 13px on a 1080px page) due to a >=9-letter word, and only if gain >= +3px.
    min_legible_whole = max(min_font_size + 3, int(round(max_font_size * 0.41)))
    has_very_long_word = any(_alpha_len(w) >= 9 for w in raw_words)
    if has_very_long_word and (best_clean is None or best_clean.font_size < min_legible_whole):
        best_hyph = _binary_search(use_syllabic_hyph=True)
        if best_hyph is not None:
            if best_clean is None or best_hyph.font_size >= best_clean.font_size + 3:
                return best_hyph

    if best_clean is not None:
        return best_clean

    # Last-resort downward scan below min_font_size (still max 1 hyphen per word)
    for size in range(min_font_size, 7, -1):
        cand = _try_font_size(size, use_syllabic_hyph=True, force_char_hyph=False)
        if cand is not None:
            return cand
    for size in range(min_font_size, 7, -1):
        cand = _try_font_size(size, use_syllabic_hyph=True, force_char_hyph=True)
        if cand is not None:
            return cand
    return None


def _fit_text_in_rect(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: BoundingBox,
    font_path: Path,
    *,
    uppercase: bool,
    use_stroke: bool,
    min_font_size: int = 10,
    max_font_size: int = 34,
) -> FittedLayout:
    pad_x = max(4, int(box.width * 0.08))
    pad_y = max(4, int(box.height * 0.08))
    inner = BoundingBox(
        x1=box.x1 + pad_x,
        y1=box.y1 + pad_y,
        x2=max(box.x1 + pad_x + 16, box.x2 - pad_x),
        y2=max(box.y1 + pad_y + 16, box.y2 - pad_y),
    )
    rect_mask = np.ones((inner.height, inner.width), dtype=np.uint8)
    contour = _precompute_contour(rect_mask, inner)
    if contour is not None:
        fitted = _fit_text_in_contour(
            draw,
            text,
            contour,
            font_path,
            uppercase=uppercase,
            use_stroke=use_stroke,
            min_font_size=min_font_size,
            max_font_size=max_font_size,
        )
        if fitted is not None:
            return fitted

    font = _load_font_cached(str(font_path), min_font_size)
    stroke_w = max(2, min_font_size // 7) if use_stroke else 0
    return FittedLayout(
        font=font,
        font_size=min_font_size,
        stroke_w=stroke_w,
        lines=[
            PlacedLine(
                text=text,
                y_top=float(inner.y1),
                x_center=(inner.x1 + inner.x2) / 2.0,
                safe_left=float(inner.x1),
                safe_right=float(inner.x2),
            )
        ],
    )


class MangaTextRenderer:
    """Renders translated PT-BR dialogue onto an inpainted manga page with scanlation styling."""

    def __init__(
        self,
        font_path: Optional[Path] = None,
        *,
        uppercase: bool = True,
    ) -> None:
        self.font_path = font_path or ensure_manga_font()
        self.uppercase = uppercase

    def prepare_display_text(self, raw_text: str) -> str:
        """Normalize, filter unsupported glyphs (zero tofu), and apply case styling."""
        normalized = normalize_text_for_font(raw_text, self.font_path)
        return normalized.upper() if self.uppercase else normalized

    def render_page_pil(
        self,
        inpainted_bgr: np.ndarray,
        blocks: list[TextBlock],
    ) -> Image.Image:
        """Render all blocks with non-empty `translated_text` and return RGB PIL.Image directly."""
        img_rgb = cv2.cvtColor(inpainted_bgr, cv2.COLOR_BGR2RGB)
        img_h, img_w = img_rgb.shape[:2]
        pil_img = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(pil_img)

        max_font = max(20, min(36, int(round(img_w * 0.028))))
        min_font = max(10, int(round(img_w * 0.011)))

        for block in blocks:
            raw_text = block.translated_text.strip()
            if not raw_text:
                continue
            text = self.prepare_display_text(raw_text)
            if not text:
                continue

            target_box = (block.render_bbox or block.bbox).clamped(img_w, img_h)
            if target_box.width < 12 or target_box.height < 12:
                continue

            patch = img_rgb[target_box.y1 : target_box.y2, target_box.x1 : target_box.x2]
            if patch.size > 0:
                lum_map = (
                    0.299 * patch[:, :, 0]
                    + 0.587 * patch[:, :, 1]
                    + 0.114 * patch[:, :, 2]
                )
                lum = float(np.median(lum_map))
                bg_std = float(np.std(lum_map))
            else:
                lum = 255.0
                bg_std = 0.0

            # True adaptive contrast for colored webtoons, black bubbles, and dark RPG system boxes:
            # - Dark background (`lum < 128`): crisp white lettering + thin black stroke
            # - Light/colored background (`lum >= 128`): crisp black lettering + subtle white halo if textured/colored
            is_dark_bg = lum < 128.0
            if is_dark_bg:
                fill_color = (250, 250, 250)
                stroke_color = (12, 12, 12)
                use_stroke = True
            else:
                fill_color = (18, 18, 18)
                stroke_color = (252, 252, 252)
                use_stroke = (not block.is_solid_bubble) or (bg_std >= 14.0)

            layout: Optional[FittedLayout] = None
            if block.is_solid_bubble:
                med_bgr = (
                    block.bubble_bg_rgb[2],
                    block.bubble_bg_rgb[1],
                    block.bubble_bg_rgb[0],
                )
                safe_mask, win = extract_solid_bubble_safe_mask(
                    inpainted_bgr,
                    block,
                    blocks,
                    med_bgr,
                )
                if safe_mask is not None and win is not None:
                    contour = _precompute_contour(safe_mask, win)
                    if contour is not None:
                        layout = _fit_text_in_contour(
                            draw,
                            text,
                            contour,
                            self.font_path,
                            uppercase=self.uppercase,
                            use_stroke=use_stroke,
                            min_font_size=min_font,
                            max_font_size=max_font,
                        )

            if layout is None:
                layout = _fit_text_in_rect(
                    draw,
                    text,
                    target_box,
                    self.font_path,
                    uppercase=self.uppercase,
                    use_stroke=use_stroke,
                    min_font_size=min_font,
                    max_font_size=max_font,
                )

            for pl in layout.lines:
                bbox = draw.textbbox(
                    (0, 0),
                    pl.text,
                    font=layout.font,
                    stroke_width=layout.stroke_w,
                )
                lw = bbox[2] - bbox[0]
                x = pl.x_center - lw / 2.0 - bbox[0]
                y = pl.y_top - bbox[1]
                draw.text(
                    (x, y),
                    pl.text,
                    font=layout.font,
                    fill=fill_color,
                    stroke_width=layout.stroke_w,
                    stroke_fill=stroke_color if layout.stroke_w > 0 else None,
                )

        return pil_img

    def render_page(
        self,
        inpainted_bgr: np.ndarray,
        blocks: list[TextBlock],
    ) -> np.ndarray:
        """Render all blocks and return BGR numpy array."""
        pil_img = self.render_page_pil(inpainted_bgr, blocks)
        return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    def render_page_to_bytes(
        self,
        inpainted_bgr: np.ndarray,
        blocks: list[TextBlock],
        *,
        output_format: str = "webp",
        webp_quality: int = 89,
        webp_method: int = 1,
    ) -> tuple[bytes, str]:
        """Render and encode directly from PIL.Image using fast WebP (`method=1`, `quality=89`)."""
        pil_img = self.render_page_pil(inpainted_bgr, blocks)
        return encode_pil_image_fast(
            pil_img,
            output_format=output_format,
            webp_quality=webp_quality,
            webp_method=webp_method,
        )


WEBP_MAX_DIMENSION = 16383


def encode_pil_image_fast(
    pil_img: Image.Image,
    *,
    output_format: str = "webp",
    webp_quality: int = 89,
    webp_method: int = 1,
) -> tuple[bytes, str]:
    """Encode PIL RGB image directly to ultra-fast WebP (`method=1`, `q=89`), JPEG, or PNG.

    Automatically falls back to fast JPEG (`quality=90`, `image/jpeg`) when image width or
    height exceeds WebP's physical 16,383 px limit (e.g. giant Webtoon strips).
    """
    fmt = (output_format or "webp").strip().lower()
    buf = io.BytesIO()
    if fmt == "webp":
        if pil_img.width > WEBP_MAX_DIMENSION or pil_img.height > WEBP_MAX_DIMENSION:
            logger.info(
                "Dimensao %dx%d excede limite fisico WebP (%dpx); usando fallback JPEG rapido (q=90).",
                pil_img.width,
                pil_img.height,
                WEBP_MAX_DIMENSION,
            )
            rgb_img = pil_img.convert("RGB") if pil_img.mode != "RGB" else pil_img
            rgb_img.save(buf, format="JPEG", quality=90, optimize=False)
            return buf.getvalue(), "image/jpeg"
        pil_img.save(
            buf,
            format="WEBP",
            quality=int(np.clip(webp_quality, 70, 100)),
            method=int(np.clip(webp_method, 0, 6)),
        )
        return buf.getvalue(), "image/webp"
    if fmt in {"jpg", "jpeg"}:
        rgb_img = pil_img.convert("RGB") if pil_img.mode != "RGB" else pil_img
        rgb_img.save(buf, format="JPEG", quality=90, optimize=False)
        return buf.getvalue(), "image/jpeg"
    pil_img.save(buf, format="PNG", compress_level=3)
    return buf.getvalue(), "image/png"


def create_side_by_side_comparison(
    original_bgr: np.ndarray,
    translated_bgr: np.ndarray,
    *,
    left_label: str = "ORIGINAL (JA)",
    right_label: str = "TRADUZIDA (PT-BR — Scanlator Vogels)",
) -> np.ndarray:
    h, w = original_bgr.shape[:2]
    if translated_bgr.shape[:2] != (h, w):
        translated_bgr = cv2.resize(translated_bgr, (w, h), interpolation=cv2.INTER_AREA)

    header_h = 44
    divider_w = 8
    canvas = np.full((h + header_h, w * 2 + divider_w, 3), 24, dtype=np.uint8)
    canvas[header_h:, :w] = original_bgr
    canvas[header_h:, w + divider_w :] = translated_bgr
    canvas[header_h:, w : w + divider_w] = (55, 55, 55)

    pil_canvas = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_canvas)
    try:
        font = _load_font_cached(str(ensure_manga_font()), 20)
    except Exception:
        font = ImageFont.load_default()

    draw.text((16, 10), left_label, fill=(235, 235, 235), font=font)
    draw.text((w + divider_w + 16, 10), right_label, fill=(130, 230, 175), font=font)
    return cv2.cvtColor(np.array(pil_canvas), cv2.COLOR_RGB2BGR)


def create_detection_debug_image(
    original_bgr: np.ndarray,
    blocks: list[TextBlock],
    text_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    debug = original_bgr.copy()

    if text_mask is not None and text_mask.shape[:2] == debug.shape[:2]:
        overlay = debug.copy()
        overlay[text_mask > 100] = (
            overlay[text_mask > 100].astype(np.float32) * 0.55
            + np.array([30, 30, 235]) * 0.45
        ).astype(np.uint8)
        debug = overlay

    if len(blocks) >= 2:
        for i in range(len(blocks) - 1):
            p1 = (int(blocks[i].bbox.cx), int(blocks[i].bbox.cy))
            p2 = (int(blocks[i + 1].bbox.cx), int(blocks[i + 1].bbox.cy))
            cv2.arrowedLine(
                debug,
                p1,
                p2,
                color=(255, 160, 0),
                thickness=2,
                tipLength=0.04,
                line_type=cv2.LINE_AA,
            )

    for block in blocks:
        color = (40, 190, 60) if block.is_solid_bubble else (210, 50, 210)
        b = block.bbox
        cv2.rectangle(debug, (b.x1, b.y1), (b.x2, b.y2), color, 2)

        if block.render_bbox is not None:
            rb = block.render_bbox
            cv2.rectangle(debug, (rb.x1, rb.y1), (rb.x2, rb.y2), (220, 180, 0), 1)

        badge_text = f"#{block.id}"
        (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        bx2 = min(debug.shape[1] - 1, b.x2)
        bx1 = max(0, bx2 - tw - 10)
        by1 = max(0, b.y1 - th - 8)
        by2 = by1 + th + 8
        cv2.rectangle(debug, (bx1, by1), (bx2, by2), color, -1)
        cv2.putText(
            debug,
            badge_text,
            (bx1 + 5, by2 - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

    return debug
