"""Automated tests for zero-tofu glyph normalization, space preservation,
moderate PT-BR hyphenation, and real English comic OCR with preserved spaces.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from poc.ocr import AutoMultiLangOCRProvider, RapidOCRProvider
from poc.renderer import (
    _fit_text_in_contour,
    _hyphenate_ptbr_word_once,
    _precompute_contour,
    ensure_manga_font,
    get_font_supported_codepoints,
    normalize_text_for_font,
)
from poc.schemas import BoundingBox, TextBlock
from poc.translator import has_glued_words


def test_zero_tofu_normalization_filters_unsupported_glyphs_and_converts_cjk_punct() -> None:
    font_path = ensure_manga_font()
    supported = get_font_supported_codepoints(str(font_path))
    assert len(supported) > 100  # Real TTF cmap parsed cleanly

    raw = "CARAMBA♥♥♪！ Isso… 「sério」？！　〜ー★"
    cleaned = normalize_text_for_font(raw, font_path)

    # Every remaining non-whitespace char MUST exist in the font's real cmap (zero tofu!)
    for ch in cleaned:
        if not ch.isspace():
            assert ord(ch) in supported, f"Unsupported char leaked: {ch!r} (U+{ord(ch):04X})"

    assert "CARAMBA!" in cleaned
    assert "..." in cleaned
    assert '"sério"?!' in cleaned
    assert "♥" not in cleaned
    assert "♪" not in cleaned
    assert "★" not in cleaned


def test_renderer_preserves_spaces_and_never_glues_words() -> None:
    font_path = ensure_manga_font()
    img = Image.new("RGB", (300, 320), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    mask = np.ones((240, 180), dtype=np.uint8)
    contour = _precompute_contour(mask, BoundingBox(x1=60, y1=40, x2=240, y2=280))
    assert contour is not None

    text = "JAMAIS TENTEM RESOLVER ISSO SOZINHOS DE NOVO!"
    layout = _fit_text_in_contour(
        draw,
        text,
        contour,
        font_path,
        uppercase=True,
        use_stroke=False,
        min_font_size=10,
        max_font_size=28,
    )
    assert layout is not None
    rejoined = " ".join(ln.text for ln in layout.lines)
    # Remove only valid hyphen-breaks if any; every word space MUST remain intact!
    assert rejoined.replace("- ", "") == text
    assert "JAMAISTENTEM" not in rejoined


def test_moderate_hyphenation_keeps_entendi_whole_and_max_one_hyphen_per_word() -> None:
    font_path = ensure_manga_font()
    font = ImageFont.truetype(str(font_path), size=16)
    img = Image.new("RGB", (300, 300), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    def measure(s: str) -> float:
        bb = draw.textbbox((0, 0), s, font=font)
        return float(bb[2] - bb[0])

    # Short/medium word ("ENTENDI." = 7 letters) MUST NOT be hyphenated!
    parts_short = _hyphenate_ptbr_word_once("ENTENDI.", 50.0, measure)
    assert parts_short == ["ENTENDI."]

    # Long word ("AVENTUREIROS") MUST split into AT MOST 2 pieces (max 1 hyphen — NEVER 3 lines!)
    parts_long = _hyphenate_ptbr_word_once("AVENTUREIROS", 85.0, measure)
    assert 1 <= len(parts_long) <= 2
    if len(parts_long) == 2:
        assert parts_long[0].endswith("-")
        assert not parts_long[1].endswith("-")
        assert "".join(parts_long).replace("-", "") == "AVENTUREIROS"


def test_glued_word_detector_flags_spaceless_portuguese_and_allows_normal_text() -> None:
    assert has_glued_words("JAMAISTENTEMRESOLVERISSOZINHOS!") is True
    assert has_glued_words("JAMAIS TENTEM RESOLVER ISSO SOZINHOS!") is False
    assert has_glued_words("AAAAAAAAAAAAAAAAAAAAAAAAAAAAH!") is False  # expressive scream


def test_real_english_comic_ocr_preserves_spaces_and_auto_routing() -> None:
    """Real end-to-end RapidOCR test on a synthetic English comic speech bubble image."""
    font_path = ensure_manga_font()
    img = Image.new("RGB", (460, 260), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    f = ImageFont.truetype(str(font_path), 25)
    draw.text((28, 34), "THAT'S THE KIND OF", fill=(15, 15, 15), font=f)
    draw.text((28, 76), "TEAM WE NEED RIGHT NOW!", fill=(15, 15, 15), font=f)
    draw.text((28, 140), "NEVER GIVE UP!", fill=(15, 15, 15), font=f)

    # 1. Direct English RapidOCR provider
    en_ocr = RapidOCRProvider(lang="en")
    en_ocr.load("cpu")
    try:
        out = en_ocr.recognize_crop(img)
    finally:
        en_ocr.unload()

    upper = out.upper()
    assert " " in upper, f"Spaces missing in English OCR output: {out!r}"
    assert "KIND" in upper and "TEAM" in upper and "NEVER" in upper
    assert "THATSTHEKINDOF" not in upper

    # 2. Auto (JP/EN) router classifies horizontal English bubble to `en` without loading manga-ocr!
    auto_ocr = AutoMultiLangOCRProvider()
    auto_ocr.load("cpu")
    try:
        bgr = np.array(img)[:, :, ::-1].copy()
        blk = TextBlock(
            id=1,
            bbox=BoundingBox(x1=10, y1=10, x2=450, y2=240),
            lines=[
                [[28, 34], [340, 34], [340, 62], [28, 62]],
                [[28, 76], [390, 76], [390, 104], [28, 104]],
            ],
            is_vertical=False,
        )
        auto_ocr.recognize_blocks(bgr, [blk])
        assert auto_ocr.detected_primary_language == "en"
        assert auto_ocr._manga_ocr is None  # NEVER loaded manga-ocr for pure English page!
        assert "TEAM" in blk.source_text.upper()
        assert " " in blk.source_text
    finally:
        auto_ocr.unload()
