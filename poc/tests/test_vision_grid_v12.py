"""Automated validation suite for v1.2 (Multimodal Vision Grid + Interior Inpainting Shield):
1. Vision Grid ordering (RTL vs LTR BEFORE numbering), single-page multimodal call,
   3-page multimodal batch, and empty-string `""` handling for noise/SFX cells.
2. Paginated Vision Grid on a synthetic tall colored Webtoon (800x17200px, >25 balloons)
   with colored/gradient RPG boxes + seamless reassembly + WebP >16,383px JPEG fallback.
3. White speech bubble touching a jet-black blackboard stays WHITE with crisp BLACK text
   (never painted as a black rectangle!).
4. Block with empty translation (`""`) is NEVER erased in post-translation inpainting
   (100% pixel-identical to original image).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from poc.config import DEFAULT_FREE_MODEL
from poc.inpainter import HybridInpainter
from poc.renderer import WEBP_MAX_DIMENSION, MangaTextRenderer, encode_pil_image_fast
from poc.schemas import BoundingBox, TextBlock
from poc.slicer import compute_vertical_slices, should_slice_image, stitch_inpainted_slices
from poc.translator import (
    GeminiFreeTierTranslator,
    QuotaTracker,
    _MODEL_THINKING_MODE,
)
from poc.vision_grid import MAX_CELLS_PER_SHEET, MAX_SHEET_WIDTH, build_vision_grid_sheets


@dataclass
class _FakeUsage:
    prompt_token_count: int = 210
    candidates_token_count: int = 55


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

    def generate_content(self, *, model: str, contents: Any, config: Any):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._fn(model, contents, config, len(self.calls))


class _FakeClient:
    def __init__(self, fn) -> None:
        self.models = _FakeModels(fn)


def test_vision_grid_orders_rtl_vs_ltr_before_numbering_and_handles_empty_cells(
    tmp_path: Path,
) -> None:
    img = np.full((1000, 800, 3), 255, dtype=np.uint8)
    raw_blocks = [
        TextBlock(id=1, bbox=BoundingBox(x1=80, y1=600, x2=280, y2=760), is_vertical=False),   # BL
        TextBlock(id=2, bbox=BoundingBox(x1=500, y1=600, x2=700, y2=760), is_vertical=False),  # BR
        TextBlock(id=3, bbox=BoundingBox(x1=80, y1=100, x2=280, y2=260), is_vertical=False),   # TL
        TextBlock(id=4, bbox=BoundingBox(x1=500, y1=100, x2=700, y2=260), is_vertical=False),  # TR
    ]

    # RTL -> TR(1), TL(2), BR(3), BL(4)
    sheets_rtl, ordered_rtl, dir_rtl = build_vision_grid_sheets(
        img, [b.model_copy(deep=True) for b in raw_blocks], reading_direction="rtl"
    )
    assert dir_rtl == "rtl"
    assert len(sheets_rtl) == 1
    assert sheets_rtl[0].width <= MAX_SHEET_WIDTH
    assert [(b.bbox.x1, b.bbox.y1) for b in ordered_rtl] == [
        (500, 100),
        (80, 100),
        (500, 600),
        (80, 600),
    ]
    assert [b.id for b in ordered_rtl] == [1, 2, 3, 4]

    # LTR -> TL(1), TR(2), BL(3), BR(4)
    sheets_ltr, ordered_ltr, dir_ltr = build_vision_grid_sheets(
        img, [b.model_copy(deep=True) for b in raw_blocks], reading_direction="ltr"
    )
    assert dir_ltr == "ltr"
    assert [(b.bbox.x1, b.bbox.y1) for b in ordered_ltr] == [
        (80, 100),
        (500, 100),
        (80, 600),
        (500, 600),
    ]

    # Multimodal single-page call handling explicit `""` for SFX/noise cell #3!
    _MODEL_THINKING_MODE[DEFAULT_FREE_MODEL] = "level_minimal"

    def handler(_model, contents, _config, _n):
        assert isinstance(contents, list)
        # Contains text prompt + JPEG Part
        assert len(contents) >= 2
        return _FakeResponse(text='{"1": "E aí, cara!", "2": "Bora lá!", "3": "", "4": "Fechou!"}')

    tr = GeminiFreeTierTranslator(
        api_key="fake",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "qv.json"),
    )
    fake = _FakeClient(handler)
    tr._client = fake

    out, snap = tr.translate_page_vision(ordered_ltr, sheets_ltr, source_lang="ko")
    assert len(fake.models.calls) == 1  # No wasted micro-fallback for explicit `""`!
    assert [b.translated_text for b in out] == ["E aí, cara!", "Bora lá!", "", "Fechou!"]
    assert snap is not None and snap.requests_today == 1


def test_vision_grid_3_page_batch_and_isolated_fallback(tmp_path: Path) -> None:
    _MODEL_THINKING_MODE[DEFAULT_FREE_MODEL] = "level_minimal"
    img = np.full((600, 500, 3), 245, dtype=np.uint8)
    pages_blocks = []
    pages_sheets = []
    for p_idx in range(3):
        blks = [
            TextBlock(id=1, bbox=BoundingBox(x1=40, y1=40, x2=220, y2=140), is_vertical=False),
            TextBlock(id=2, bbox=BoundingBox(x1=40, y1=260, x2=220, y2=360), is_vertical=False),
        ]
        shs, ordered, _ = build_vision_grid_sheets(
            img, blks, reading_direction="ltr", page_label=f"P{p_idx}"
        )
        pages_blocks.append(ordered)
        pages_sheets.append(shs)

    def handler(_model, contents, _config, call_num):
        if call_num == 1:
            # Batch call for 3 pages: omit p1 to test clean isolated fallback while preserving p0 & p2!
            assert any("p0" in str(c) and "p1" in str(c) and "p2" in str(c) for c in contents)
            return _FakeResponse(
                text=json.dumps(
                    {
                        "p0": {"1": "Cheguei!", "2": ""},
                        "p2": {"1": "Bora!", "2": "Partiu!"},
                    },
                    ensure_ascii=False,
                )
            )
        return _FakeResponse(text='{"1": "Calma aí!", "2": "Tô indo!"}')

    tr = GeminiFreeTierTranslator(
        api_key="fake",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "qvb.json"),
    )
    tr._client = _FakeClient(handler)

    out_pages, _snap, fallbacks = tr.translate_pages_batch_vision(
        pages_blocks, pages_sheets, source_lang="auto"
    )
    assert fallbacks == [1]
    assert [b.translated_text for b in out_pages[0]] == ["Cheguei!", ""]
    assert [b.translated_text for b in out_pages[1]] == ["Calma aí!", "Tô indo!"]
    assert [b.translated_text for b in out_pages[2]] == ["Bora!", "Partiu!"]


def test_paginated_vision_grid_tall_colored_webtoon_26_balloons_gradient_and_jpeg_fallback(
    tmp_path: Path,
) -> None:
    """Synthetic 800x17200 colored Webtoon with 26 balloons:
    - Paginates Vision Grid into 2 sheets (18 + 8 = 26) sent in 1 single multimodal call!
    - Preserves colored gradient RPG box via local Telea (not flat white rectangle).
    - Reassembles cleanly and falls back to JPEG past WebP's 16,383 px ceiling.
    """
    w, h = 800, 17200
    assert h > WEBP_MAX_DIMENSION
    assert should_slice_image(h, w) is True

    # Colored webtoon strip with clean panel gutters + smooth blue-to-purple gradient boxes!
    strip = np.full((h, w, 3), 255, dtype=np.uint8)
    blocks: list[TextBlock] = []
    global_mask = np.zeros((h, w), dtype=np.uint8)

    for idx in range(26):
        y_base = 120 + idx * 640
        x1, y1, x2, y2 = 120, y_base, 680, y_base + 220
        # Draw smooth colored gradient box (RPG system / manhwa panel)
        for row_i in range(y1, y2):
            t = (row_i - y1) / max(1.0, float(y2 - y1 - 1))
            b_val = int(180 + t * 65)
            g_val = int(120 + t * 80)
            r_val = int(60 + t * 70)
            strip[row_i, x1:x2] = (b_val, g_val, r_val)
        # Simulated text strokes inside center
        cv2.rectangle(strip, (x1 + 140, y1 + 80), (x2 - 140, y2 - 80), (250, 250, 250), -1)
        global_mask[y1 + 78 : y2 - 78, x1 + 138 : x2 - 138] = 255
        blocks.append(
            TextBlock(
                id=idx + 1,
                bbox=BoundingBox(x1=x1 + 80, y1=y1 + 40, x2=x2 - 80, y2=y2 - 40),
                is_vertical=False,
            )
        )

    sheets, ordered_blocks, eff_dir = build_vision_grid_sheets(
        strip,
        blocks,
        reading_direction="auto",
        source_lang="ko",
        max_cells_per_sheet=MAX_CELLS_PER_SHEET,
    )
    assert eff_dir == "ltr"
    assert len(sheets) == 2  # 18 + 8 = 26 cells -> 2 sheets!
    assert sheets[0].block_ids == list(range(1, 19))
    assert sheets[1].block_ids == list(range(19, 27))
    assert all(s.width <= MAX_SHEET_WIDTH for s in sheets)

    # Send BOTH sheets in ONE multimodal Gemini call!
    _MODEL_THINKING_MODE[DEFAULT_FREE_MODEL] = "level_minimal"

    def handler(_model, contents, _config, _n):
        # Confirm both Folha 1/2 and Folha 2/2 were attached in 1 call
        text_parts = [c for c in contents if isinstance(c, str)]
        assert any("Folha 1/2" in t for t in text_parts)
        assert any("Folha 2/2" in t for t in text_parts)
        reply = {str(i): f"Fala {i}!" for i in range(1, 27)}
        return _FakeResponse(text=json.dumps(reply, ensure_ascii=False))

    tr = GeminiFreeTierTranslator(
        api_key="fake",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "qtall.json"),
    )
    fake = _FakeClient(handler)
    tr._client = fake
    ordered_blocks, _ = tr.translate_page_vision(ordered_blocks, sheets, source_lang="ko")
    assert len(fake.models.calls) == 1
    assert all(b.translated_text == f"Fala {b.id}!" for b in ordered_blocks)

    # Slicing + post-translation inpainting preserves gradient (NOT painted pure white!)
    regions = compute_vertical_slices(strip)
    assert len(regions) >= 8
    inpainter = HybridInpainter()
    # Test inpainting on the first slice directly to verify gradient preservation
    r0 = regions[0]
    sl0 = strip[r0.y1 : r0.y2, :].copy()
    sl0_mask = global_mask[r0.y1 : r0.y2, :]
    sl0_blks = [b for b in ordered_blocks if b.bbox.y2 < r0.y2]
    cleaned_sl0, _, _ = inpainter.inpaint_page(sl0, sl0_mask, sl0_blks, device="cpu", only_translated=True)
    # Gradient box center MUST stay bluish/colored (B > R + 60), NEVER flat pure white!
    b0 = sl0_blks[0]
    center_px = cleaned_sl0[b0.bbox.y1 + 50 : b0.bbox.y2 - 50, b0.bbox.x1 + 100 : b0.bbox.x2 - 100].mean(axis=(0, 1))
    assert float(center_px[0]) - float(center_px[2]) > 60.0, f"Gradient lost: {center_px}"

    # Stitch back to full 800x17200 and confirm automatic WebP->JPEG fallback!
    all_slices = [cleaned_sl0] + [strip[r.y1 : r.y2, :].copy() for r in regions[1:]]
    stitched = stitch_inpainted_slices(all_slices, regions, h, w)
    assert stitched.shape == (h, w, 3)

    pil_tall = Image.fromarray(stitched[:, :, ::-1])
    encoded, mime = encode_pil_image_fast(pil_tall, output_format="webp")
    assert mime == "image/jpeg"
    assert Image.open(BytesIO(encoded)).size == (w, h)


def test_white_bubble_touching_blackboard_stays_white_with_black_text() -> None:
    """Reproduces the exact real-world Chrome bug: a white speech bubble pressed right
    against a jet-black classroom blackboard MUST remain WHITE with BLACK text (NEVER a
    black rectangle with white text!).
    """
    inpainter = HybridInpainter()
    renderer = MangaTextRenderer(uppercase=True)

    # Jet-black blackboard background covering the entire panel!
    page = np.full((340, 420, 3), 12, dtype=np.uint8)
    # White speech bubble sitting right on top of the black blackboard
    cv2.ellipse(page, (210, 170), (115, 85), 0, 0, 360, (252, 252, 252), -1)
    cv2.ellipse(page, (210, 170), (115, 85), 0, 0, 360, (18, 18, 18), 3)
    # Tight black text strokes inside the white bubble (leaving white interior between strokes)
    cv2.rectangle(page, (145, 138), (275, 152), (20, 20, 20), -1)
    cv2.rectangle(page, (150, 172), (270, 186), (20, 20, 20), -1)

    mask = np.zeros((340, 420), dtype=np.uint8)
    mask[137:153, 144:276] = 255
    mask[171:187, 149:271] = 255

    # Deliberately make bbox slightly generous so its corners touch the black oval border/blackboard!
    blk = TextBlock(
        id=1,
        bbox=BoundingBox(x1=125, y1=115, x2=295, y2=225),
        source_text="안녕!",
        translated_text="OI PESSOAL!",
    )

    cleaned, _, _ = inpainter.inpaint_page(page, mask, [blk], device="cpu", only_translated=True)

    # Bubble MUST be detected as LIGHT/WHITE (NEVER black!)
    assert blk.is_solid_bubble is True
    assert min(blk.bubble_bg_rgb) >= 225, f"White bubble misclassified as dark: {blk.bubble_bg_rgb}"
    cleaned_interior = cleaned[135:205, 145:275]
    assert float(cleaned_interior.mean()) >= 235.0, "White bubble got painted dark!"

    # Rendered text over white bubble MUST be BLACK ink (< 60) over WHITE background (> 230)!
    rendered = renderer.render_page(cleaned, [blk])
    roi = rendered[130:210, 140:280]
    white_bg_ratio = float(np.mean(roi.mean(axis=2) > 220))
    black_ink_px = int(np.count_nonzero(roi.mean(axis=2) < 60))
    assert white_bg_ratio >= 0.70, f"Bubble interior not white: {white_bg_ratio:.2f}"
    assert black_ink_px >= 120, f"Expected dark black lettering on white bubble, got {black_ink_px}px"


def test_empty_translation_block_is_never_erased_in_inpainting() -> None:
    """When Gemini returns `""` (SFX/noise/art) for block #2, post-translation inpainting
    MUST leave 100% of block #2's original pixels untouched while cleaning block #1 cleanly!
    """
    inpainter = HybridInpainter()
    renderer = MangaTextRenderer(uppercase=True)

    page = np.full((360, 520, 3), 255, dtype=np.uint8)
    # Block #1 (left): translated dialogue bubble
    cv2.ellipse(page, (130, 180), (95, 75), 0, 0, 360, (250, 250, 250), -1)
    cv2.ellipse(page, (130, 180), (95, 75), 0, 0, 360, (20, 20, 20), 3)
    cv2.rectangle(page, (90, 165), (170, 195), (20, 20, 20), -1)

    # Block #2 (right): colorful stylized SFX artwork that Gemini returned `""` for!
    cv2.rectangle(page, (300, 110), (470, 250), (40, 130, 230), -1)
    cv2.line(page, (310, 120), (460, 240), (20, 240, 250), 8)
    cv2.circle(page, (385, 180), 32, (220, 30, 190), -1)
    orig_sfx_patch = page[105:255, 295:475].copy()

    mask = np.zeros((360, 520), dtype=np.uint8)
    mask[163:197, 88:172] = 255
    mask[145:215, 340:430] = 255

    blk1 = TextBlock(
        id=1,
        bbox=BoundingBox(x1=85, y1=160, x2=175, y2=200),
        source_text="はい",
        translated_text="BELEZA!",
    )
    blk2 = TextBlock(
        id=2,
        bbox=BoundingBox(x1=335, y1=140, x2=435, y2=220),
        source_text="",
        translated_text="",  # Empty -> MUST NOT be erased!
    )

    cleaned, _, stats = inpainter.inpaint_page(
        page, mask, [blk1, blk2], device="cpu", only_translated=True
    )
    assert blk1.inpaint_method == "opencv_solid"
    assert blk2.inpaint_method == "skipped"
    assert stats["skipped"] == 1

    # 100% byte-for-byte preservation of untouched SFX/empty block BEFORE and AFTER rendering!
    assert np.array_equal(cleaned[105:255, 295:475], orig_sfx_patch)
    rendered = renderer.render_page(cleaned, [blk1, blk2])
    assert np.array_equal(rendered[105:255, 295:475], orig_sfx_patch)
