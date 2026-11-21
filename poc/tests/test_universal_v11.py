"""Automated validation suite for v1.1 (Universal Scanlator):
1. Translator resilience: pre-Gemini symbol passthrough (`…!`, `?!`) + tolerant omitted IDs
   (micro-fallback + graceful original fallback without ever aborting the page) + glossary prompt.
2. Smart vertical slicing + overlap deduplication + seamless stitching on a synthetic
   800x18000px Webtoon strip + automatic JPEG fallback past WebP's 16,383px physical limit.
3. Real local CPU OCR on Korean (Hangul), Chinese horizontal & vertical (Hanzi), Spanish
   (accents `áéíóúñ`), and English, plus `Auto (Universal)` script routing and RTL/LTR
   reading order (standard, spread, 4-koma).
4. Adaptive contrast rendering (white text on black bubbles) & color-preserving inpainting
   on colored speech bubbles and gradient boxes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from poc.config import DEFAULT_FREE_MODEL
from poc.detector import resolve_reading_direction, sort_blocks_reading_order
from poc.inpainter import HybridInpainter, _classify_region_background
from poc.ocr import AutoMultiLangOCRProvider, RapidOCRProvider
from poc.renderer import WEBP_MAX_DIMENSION, MangaTextRenderer, encode_pil_image_fast
from poc.schemas import BoundingBox, TextBlock
from poc.slicer import (
    compute_vertical_slices,
    deduplicate_slice_blocks,
    offset_slice_blocks,
    should_slice_image,
    stitch_inpainted_slices,
)
from poc.translator import (
    GeminiFreeTierTranslator,
    QuotaTracker,
    _MODEL_THINKING_MODE,
    is_non_translatable_symbol_text,
    normalize_passthrough_symbols,
)


@dataclass
class _FakeUsage:
    prompt_token_count: int = 120
    candidates_token_count: int = 40


@dataclass
class _FakeResponse:
    text: str
    usage_metadata: _FakeUsage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage_metadata is None:
            self.usage_metadata = _FakeUsage()


class _FakeModels:
    def __init__(self, fn) -> None:
        self._fn = fn
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: str, config: Any):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._fn(model, contents, config, len(self.calls))


class _FakeClient:
    def __init__(self, fn) -> None:
        self.models = _FakeModels(fn)


def _blk(bid: int, src: str, x1: int = 10, y1: int = 10, x2: int = 120, y2: int = 120) -> TextBlock:
    return TextBlock(id=bid, bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2), source_text=src)


# ---------------------------------------------------------------------------
# 1. Translator Resilience: symbol passthrough + omitted-ID tolerance + glossary
# ---------------------------------------------------------------------------
def test_symbol_only_balloons_bypass_gemini_and_normalize_cleanly(tmp_path: Path) -> None:
    assert is_non_translatable_symbol_text("…!") is True
    assert is_non_translatable_symbol_text("？！") is True
    assert is_non_translatable_symbol_text("——") is True
    assert is_non_translatable_symbol_text("100%") is True
    assert is_non_translatable_symbol_text("뭐?") is False
    assert is_non_translatable_symbol_text("你好") is False
    assert is_non_translatable_symbol_text("Ok!") is False

    assert normalize_passthrough_symbols("…!") == "...!"
    assert normalize_passthrough_symbols("？！") == "?!"

    _MODEL_THINKING_MODE[DEFAULT_FREE_MODEL] = "level_minimal"

    def handler(_model, contents, _config, _n):
        # ID 2 (`…!`) and ID 3 (`？！`) MUST NOT be sent in the JSON payload to Gemini!
        payload = json.loads(contents.splitlines()[-1])
        assert payload == {"1": "等一下！"}
        return _FakeResponse(text='{"1": "Calma aí!"}')

    tr = GeminiFreeTierTranslator(
        api_key="fake",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "q.json"),
    )
    fake = _FakeClient(handler)
    tr._client = fake

    blocks = [_blk(1, "等一下！"), _blk(2, "…!"), _blk(3, "？！")]
    out, _ = tr.translate_page(blocks, source_lang="zh")
    assert len(fake.models.calls) == 1
    assert out[0].translated_text == "Calma aí!"
    assert out[1].translated_text == "...!"
    assert out[2].translated_text == "?!"


def test_omitted_id_uses_micro_fallback_and_never_aborts_page(tmp_path: Path) -> None:
    _MODEL_THINKING_MODE[DEFAULT_FREE_MODEL] = "level_minimal"

    # Case A: Gemini omits ID 2 -> targeted micro-fallback recovers ONLY ID 2
    def handler_recover(_model, contents, _config, call_num):
        if call_num == 1:
            return _FakeResponse(text='{"1": "Oi!", "3": "Vamos logo!"}')
        assert '"2"' in contents and '"1"' not in contents and '"3"' not in contents
        return _FakeResponse(text='{"2": "Espera!"}')

    tr = GeminiFreeTierTranslator(
        api_key="fake",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "q1.json"),
    )
    tr._client = _FakeClient(handler_recover)

    blocks = [_blk(1, "你好"), _blk(2, "等等"), _blk(3, "快走")]
    out, _ = tr.translate_page(blocks, source_lang="zh")
    assert [b.translated_text for b in out] == ["Oi!", "Espera!", "Vamos logo!"]

    # Case B: Even if micro-fallback ALSO fails, page is NOT aborted; ID 1 & 3 stay translated!
    def handler_still_drops(_model, _contents, _config, call_num):
        if call_num == 1:
            return _FakeResponse(text='{"1": "Oi!", "3": "Vamos logo!"}')
        raise RuntimeError("transient micro-fallback failure")

    tr2 = GeminiFreeTierTranslator(
        api_key="fake",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "q2.json"),
    )
    tr2._client = _FakeClient(handler_still_drops)

    blocks2 = [_blk(1, "你好"), _blk(2, "SCAN CREDITS"), _blk(3, "快走")]
    out2, _ = tr2.translate_page(blocks2, source_lang="zh")
    assert out2[0].translated_text == "Oi!"
    assert out2[1].translated_text == "SCAN CREDITS"  # Original kept cleanly, zero crash!
    assert out2[2].translated_text == "Vamos logo!"


def test_glossary_injected_into_translator_prompt(tmp_path: Path) -> None:
    _MODEL_THINKING_MODE[DEFAULT_FREE_MODEL] = "level_minimal"
    captured: list[str] = []

    def handler(_model, contents, _config, _n):
        captured.append(contents)
        return _FakeResponse(text='{"1": "Sung Jin-Woo usou Qi!"}')

    tr = GeminiFreeTierTranslator(
        api_key="fake",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "qg.json"),
    )
    tr._client = _FakeClient(handler)

    glossary = {"성진우": "Sung Jin-Woo", "기": "Qi"}
    out, _ = tr.translate_page([_blk(1, "성진우가 기를 썼다!")], source_lang="ko", glossary=glossary)
    assert out[0].translated_text == "Sung Jin-Woo usou Qi!"
    assert "GLOSSÁRIO OBRIGATÓRIO DA OBRA" in captured[0]
    assert "성진우 => Sung Jin-Woo" in captured[0]
    assert "기 => Qi" in captured[0]


# ---------------------------------------------------------------------------
# 2. Smart Webtoon Slicing (800x18000px) + Seamless Stitch + WebP >16383 Fallback
# ---------------------------------------------------------------------------
def test_long_strip_800x18000_slicing_stitching_and_webp_jpeg_fallback() -> None:
    w, h = 800, 18000
    assert should_slice_image(h, w) is True
    assert should_slice_image(1510, 1080) is False

    # Synthesize a realistic 800x18000 Webtoon strip:
    # - Busy colored panels separated by clean white gutters at y = 1750, 3550, 5350, ...
    # - PLUS one continuous art block around y = 9000..11500 (no gutter -> forces overlap!)
    rng = np.random.default_rng(42)
    strip = np.full((h, w, 3), 255, dtype=np.uint8)
    for py in range(100, h - 100, 1800):
        y2 = min(h - 60, py + 1580)
        strip[py:y2, 40 : w - 40] = rng.integers(40, 210, size=(y2 - py, w - 80, 3), dtype=np.uint8)
    # Continuous full-bleed artwork spanning y=8800..11600 (forces overlap cut!)
    strip[8800:11600, :] = rng.integers(30, 220, size=(2800, w, 3), dtype=np.uint8)

    regions = compute_vertical_slices(strip)
    assert len(regions) >= 9
    assert regions[0].y1 == 0
    assert regions[-1].y2 == h
    # Confirm safe gutter cuts had 0 overlap AND continuous-art region used overlap!
    assert any(not r.has_overlap_bottom for r in regions[:-1])
    assert any(r.has_overlap_bottom for r in regions[:-1])

    # Verify deduplication in overlap zones keeps 1 intact box instead of 2 duplicates
    ov_idx = next(i for i in range(len(regions) - 1) if regions[i].has_overlap_bottom)
    r_top, r_bot = regions[ov_idx], regions[ov_idx + 1]
    seam_y = r_top.paste_y2
    b_top = TextBlock(id=1, bbox=BoundingBox(x1=200, y1=seam_y - 40, x2=420, y2=seam_y + 40), confidence=0.95)
    b_bot = TextBlock(id=1, bbox=BoundingBox(x1=204, y1=seam_y - 36, x2=418, y2=seam_y + 38), confidence=0.92)
    deduped = deduplicate_slice_blocks([(b_top, r_top), (b_bot, r_bot)])
    assert len(deduped) == 1

    # Verify offset_slice_blocks translates coordinates properly
    shifted = offset_slice_blocks([TextBlock(id=1, bbox=BoundingBox(x1=10, y1=20, x2=100, y2=80))], 1800)
    assert shifted[0].bbox.y1 == 1820 and shifted[0].bbox.y2 == 1880

    # Seamless stitch reconstructs EXACT dimensions (18000, 800, 3) with zero seam error!
    slices_bgr = [strip[r.y1 : r.y2, :].copy() for r in regions]
    stitched = stitch_inpainted_slices(slices_bgr, regions, h, w)
    assert stitched.shape == (h, w, 3)
    assert int(np.max(np.abs(stitched.astype(np.int16) - strip.astype(np.int16)))) <= 1

    # WebP physical limit fallback (> 16,383 px -> fast JPEG `image/jpeg` without crashing!)
    assert h > WEBP_MAX_DIMENSION
    pil_tall = Image.fromarray(stitched[:, :, ::-1])
    encoded, mime = encode_pil_image_fast(pil_tall, output_format="webp")
    assert mime == "image/jpeg"
    assert len(encoded) > 10_000
    decoded = Image.open(BytesIO(encoded))
    assert decoded.size == (w, h)


# ---------------------------------------------------------------------------
# 3. Real Multi-Language OCR (KO, ZH horiz + vert, ES, EN) + Auto + RTL/LTR
# ---------------------------------------------------------------------------
def _render_horizontal_bubble(text_lines: list[str], font_path: str, size: int = 30) -> Image.Image:
    img = Image.new("RGB", (520, 60 + len(text_lines) * 48), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, size)
    y = 26
    for ln in text_lines:
        draw.text((28, y), ln, fill=(15, 15, 15), font=font)
        y += 46
    return img


def _render_vertical_cjk_bubble(columns_rtl: list[str], font_path: str, size: int = 34) -> Image.Image:
    """Render authentic vertical manga columns ordered Right-to-Left (column 0 on the right)."""
    max_chars = max(len(c) for c in columns_rtl)
    col_step = size + 18
    w = 56 + len(columns_rtl) * col_step
    h = 56 + max_chars * (size + 8)
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_path, size)

    # Column 0 is rightmost!
    for col_idx, col_str in enumerate(columns_rtl):
        x = w - 28 - (col_idx + 1) * col_step + 8
        y = 26
        for ch in col_str:
            draw.text((x, y), ch, fill=(15, 15, 15), font=font)
            y += size + 8
    return img


def test_real_korean_hangul_ocr_and_auto_detection() -> None:
    font_path = r"C:\Windows\Fonts\malgunbd.ttf"
    if not Path(font_path).exists():
        pytest.skip("Malgun Gothic font not installed")

    img = _render_horizontal_bubble(["안녕하세요!", "준비됐어?"], font_path, size=32)

    # Direct Korean RapidOCR
    ko_ocr = RapidOCRProvider(lang="ko")
    ko_ocr.load("cpu")
    try:
        out = ko_ocr.recognize_crop(img)
    finally:
        ko_ocr.unload()
    assert "안녕" in out and "준비" in out

    # Auto (Universal) detects `ko` WITHOUT loading manga-ocr!
    auto = AutoMultiLangOCRProvider()
    auto.load("cpu")
    try:
        bgr = np.array(img)[:, :, ::-1].copy()
        blk = TextBlock(
            id=1,
            bbox=BoundingBox(x1=6, y1=6, x2=img.width - 6, y2=img.height - 6),
            is_vertical=False,
        )
        auto.recognize_blocks(bgr, [blk])
        assert auto.detected_primary_language == "ko"
        assert auto._manga_ocr is None
        assert "안녕" in blk.source_text
    finally:
        auto.unload()


def test_real_chinese_horizontal_and_vertical_ocr_and_auto_detection() -> None:
    font_path = r"C:\Windows\Fonts\msyhbd.ttc"
    if not Path(font_path).exists():
        pytest.skip("Microsoft YaHei font not installed")

    # 1. Horizontal Chinese
    h_img = _render_horizontal_bubble(["你在说什么？", "快点走吧！"], font_path, size=32)
    auto = AutoMultiLangOCRProvider()
    auto.load("cpu")
    try:
        bgr = np.array(h_img)[:, :, ::-1].copy()
        blk = TextBlock(
            id=1,
            bbox=BoundingBox(x1=6, y1=6, x2=h_img.width - 6, y2=h_img.height - 6),
            is_vertical=False,
        )
        auto.recognize_blocks(bgr, [blk])
        assert auto.detected_primary_language == "zh"
        assert auto._manga_ocr is None
        assert "你在" in blk.source_text or "什么" in blk.source_text
    finally:
        auto.unload()

    # 2. Vertical Chinese (2 right-to-left columns!)
    v_img = _render_vertical_cjk_bubble(["你好吗", "快点走"], font_path, size=36)
    zh_ocr = RapidOCRProvider(lang="zh")
    zh_ocr.load("cpu")
    try:
        v_txt, _sc = zh_ocr.recognize_crop_with_score(v_img, is_vertical_hint=True)
    finally:
        zh_ocr.unload()
    # Rightmost column (`你好吗`) MUST precede left column (`快点走`)!
    assert "你" in v_txt and "快" in v_txt
    assert v_txt.find("你") < v_txt.find("快"), f"Vertical RTL order wrong: {v_txt!r}"


def test_real_spanish_accents_ocr_and_auto_detection() -> None:
    font_path = r"C:\Windows\Fonts\arialbd.ttf"
    if not Path(font_path).exists():
        pytest.skip("Arial Bold font not installed")

    es_img = _render_horizontal_bubble(["¡Qué estás haciendo,", "niño! Vamos ahora."], font_path, size=28)
    es_ocr = RapidOCRProvider(lang="es")
    es_ocr.load("cpu")
    try:
        out = es_ocr.recognize_crop(es_img)
    finally:
        es_ocr.unload()
    assert "niño" in out.lower() or "estás" in out.lower() or "qué" in out.lower()
    assert " " in out

    auto = AutoMultiLangOCRProvider()
    auto.load("cpu")
    try:
        bgr = np.array(es_img)[:, :, ::-1].copy()
        blk = TextBlock(
            id=1,
            bbox=BoundingBox(x1=6, y1=6, x2=es_img.width - 6, y2=es_img.height - 6),
            is_vertical=False,
        )
        auto.recognize_blocks(bgr, [blk])
        assert auto.detected_primary_language == "es"
        assert auto._manga_ocr is None
    finally:
        auto.unload()


def test_reading_order_rtl_vs_ltr_spread_and_4koma() -> None:
    # 2x2 grid
    def make_grid():
        return [
            _blk(1, "BL", 100, 800, 300, 1000),
            _blk(2, "BR", 650, 800, 900, 1000),
            _blk(3, "TL", 100, 100, 300, 300),
            _blk(4, "TR", 650, 100, 900, 300),
        ]

    rtl = sort_blocks_reading_order(make_grid(), direction="rtl", img_w=1000, img_h=1400)
    assert [b.source_text for b in rtl] == ["TR", "TL", "BR", "BL"]

    ltr = sort_blocks_reading_order(make_grid(), direction="ltr", img_w=1000, img_h=1400)
    assert [b.source_text for b in ltr] == ["TL", "TR", "BL", "BR"]

    # Double-page spread (2000x1200): RTL reads entire right page (R1, R2) before left page (L1, L2)!
    spread = [
        _blk(1, "L1", 200, 120, 450, 320),
        _blk(2, "L2", 200, 700, 450, 900),
        _blk(3, "R1", 1400, 120, 1700, 320),
        _blk(4, "R2", 1400, 700, 1700, 900),
    ]
    spread_rtl = sort_blocks_reading_order(spread, direction="rtl", img_w=2000, img_h=1200)
    assert [b.source_text for b in spread_rtl] == ["R1", "R2", "L1", "L2"]

    spread_ltr = sort_blocks_reading_order(spread, direction="ltr", img_w=2000, img_h=1200)
    assert [b.source_text for b in spread_ltr] == ["L1", "L2", "R1", "R2"]

    # 4-koma (two tall vertical columns, 4 stacked panels each): MUST read column-by-column!
    yonkoma = [
        _blk(1, "R1", 600, 80, 880, 260),
        _blk(2, "R2", 600, 360, 880, 540),
        _blk(3, "R3", 600, 640, 880, 820),
        _blk(4, "R4", 600, 920, 880, 1100),
        _blk(5, "L1", 100, 80, 380, 260),
        _blk(6, "L2", 100, 360, 380, 540),
        _blk(7, "L3", 100, 640, 380, 820),
        _blk(8, "L4", 100, 920, 380, 1100),
    ]
    yk_rtl = sort_blocks_reading_order(yonkoma, direction="rtl", img_w=1000, img_h=1200)
    assert [b.source_text for b in yk_rtl] == ["R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4"]

    # Auto direction resolution
    assert resolve_reading_direction("auto", detected_lang="ja", img_w=1000, img_h=1400) == "rtl"
    assert resolve_reading_direction("auto", detected_lang="ko", img_w=1000, img_h=1400) == "ltr"
    assert resolve_reading_direction("auto", detected_lang="en", img_w=1000, img_h=1400) == "ltr"
    assert resolve_reading_direction("auto", detected_lang="es", img_w=1000, img_h=1400) == "ltr"
    assert resolve_reading_direction("auto", detected_lang="ja", img_w=800, img_h=6000) == "ltr"  # tall webtoon!
    assert resolve_reading_direction(
        "auto",
        detected_lang="zh",
        blocks=[TextBlock(id=1, bbox=BoundingBox(x1=0, y1=0, x2=60, y2=200), is_vertical=True)],
    ) == "rtl"
    assert resolve_reading_direction(
        "auto",
        detected_lang="zh",
        blocks=[TextBlock(id=1, bbox=BoundingBox(x1=0, y1=0, x2=220, y2=60), is_vertical=False)],
    ) == "ltr"


# ---------------------------------------------------------------------------
# 4. Adaptive Contrast (Black Bubble -> White Text) & Colored Bubble Preservation
# ---------------------------------------------------------------------------
def test_adaptive_contrast_black_bubble_and_colored_bubble_preservation() -> None:
    inpainter = HybridInpainter()
    renderer = MangaTextRenderer(uppercase=True)

    # --- A. Black speech bubble (RGB ~ (18, 18, 22)) with white original text ---
    dark_page = np.full((360, 420, 3), 240, dtype=np.uint8)
    # Draw solid black bubble (BGR = (22, 18, 18)) with white outline
    cv2.ellipse(dark_page, (210, 180), (150, 110), 0, 0, 360, (22, 18, 18), -1)
    cv2.ellipse(dark_page, (210, 180), (150, 110), 0, 0, 360, (245, 245, 245), 3)
    # Fake white original text strokes inside center
    cv2.rectangle(dark_page, (150, 155), (270, 205), (245, 245, 245), -1)
    mask_dark = np.zeros((360, 420), dtype=np.uint8)
    mask_dark[152:208, 148:272] = 255

    blk_dark = TextBlock(
        id=1,
        bbox=BoundingBox(x1=145, y1=150, x2=275, y2=210),
        source_text="DARK",
        translated_text="VOCÊ NÃO ESCAPA!",
    )
    cleaned_dark, _, _ = inpainter.inpaint_page(dark_page, mask_dark, [blk_dark], device="cpu")
    # Background MUST remain dark (NOT painted white!)
    assert blk_dark.is_solid_bubble is True
    assert max(blk_dark.bubble_bg_rgb) < 55
    center_cleaned = cleaned_dark[170:190, 190:230]
    assert float(center_cleaned.mean()) < 45.0

    # Rendered text over dark bubble MUST contain crisp bright white ink (> 200)!
    rendered_dark = renderer.render_page(cleaned_dark, [blk_dark])
    roi_rendered = rendered_dark[110:250, 90:330]
    bright_text_pixels = int(np.count_nonzero(roi_rendered.mean(axis=2) > 200))
    assert bright_text_pixels > 150, "Expected crisp white lettering on dark bubble!"

    # --- B. Colored pastel yellow/blue speech bubble (BGR = (165, 235, 250) -> warm pastel yellow) ---
    color_page = np.full((360, 420, 3), 255, dtype=np.uint8)
    bubble_bgr = (165, 235, 250)
    cv2.ellipse(color_page, (210, 180), (150, 110), 0, 0, 360, bubble_bgr, -1)
    cv2.ellipse(color_page, (210, 180), (150, 110), 0, 0, 360, (25, 25, 25), 3)
    cv2.rectangle(color_page, (150, 155), (270, 205), (20, 20, 20), -1)
    mask_color = np.zeros((360, 420), dtype=np.uint8)
    mask_color[152:208, 148:272] = 255

    blk_col = TextBlock(
        id=1,
        bbox=BoundingBox(x1=145, y1=150, x2=275, y2=210),
        source_text="COLOR",
        translated_text="SISTEMA ATIVADO!",
    )
    cleaned_col, _, _ = inpainter.inpaint_page(color_page, mask_color, [blk_col], device="cpu")
    assert blk_col.is_solid_bubble is True
    # Must preserve actual pastel yellow color (B channel ~165, NOT painted pure white 255!)
    cleaned_center_bgr = cleaned_col[170:190, 190:230].mean(axis=(0, 1))
    assert abs(float(cleaned_center_bgr[0]) - 165.0) < 15.0
    assert abs(float(cleaned_center_bgr[1]) - 235.0) < 15.0
    assert abs(float(cleaned_center_bgr[2]) - 250.0) < 15.0

    # --- C. Smooth vertical gradient RPG system box detected as `is_smooth_gradient` ---
    grad_crop = np.zeros((160, 240, 3), dtype=np.uint8)
    for y in range(160):
        b_val = int(120 + (y / 159.0) * 100)
        g_val = int(40 + (y / 159.0) * 70)
        grad_crop[y, :] = (b_val, g_val, 30)
    grad_mask = np.zeros((160, 240), dtype=np.uint8)
    grad_mask[60:100, 60:180] = 255
    is_solid, is_grad, _med = _classify_region_background(grad_crop, grad_mask)
    assert is_solid is False
    assert is_grad is True
