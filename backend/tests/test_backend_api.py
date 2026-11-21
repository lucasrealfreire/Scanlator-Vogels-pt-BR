"""Automated FastAPI endpoint tests for Stage 2 on-demand local microservice."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.engine import WarmScanlationEngine
from backend.server import create_app
from poc.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState, RetryableAPIError
from poc.config import DEFAULT_FREE_MODEL, PoCConfig
from poc.ocr import OCRProvider
from poc.schemas import BoundingBox, QuotaSnapshot, TextBlock
from poc.translator import PROMPT_VERSION, QuotaTracker, TranslatorProvider


def _make_png_bytes(width: int = 320, height: int = 440, seed: int = 1) -> bytes:
    rng = np.random.default_rng(seed)
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    # Draw a dark speech bubble ring + tiny distinctive pixel for unique SHA-256
    cv2.ellipse(img, (width // 2, height // 2), (100, 120), 0, 0, 360, (20, 20, 20), 3)
    img[0, 0] = rng.integers(0, 255, size=3, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


class CountingDetector:
    name = "stub-detector"

    def __init__(self, *, empty: bool = False) -> None:
        self.empty = empty
        self.detect_calls = 0
        self.unloaded = False

    def load(self, device: str = "cpu") -> str:
        return "cpu"

    def detect(self, image_bgr: np.ndarray) -> tuple[list[TextBlock], np.ndarray]:
        self.detect_calls += 1
        h, w = image_bgr.shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)
        if self.empty:
            return [], mask
        mask[160:260, 120:200] = 255
        blk = TextBlock(
            id=1,
            bbox=BoundingBox(x1=120, y1=160, x2=200, y2=260),
            is_vertical=True,
            confidence=0.99,
        )
        return [blk], mask

    def unload(self) -> None:
        self.unloaded = True


class CountingOCR(OCRProvider):
    name = "stub-ocr"
    supported_languages = frozenset({"ja"})

    def __init__(self, text: str = "こんにちは！") -> None:
        self.text = text
        self.ocr_calls = 0
        self.unloaded = False

    def load(self, device: str = "cpu") -> str:
        return "cpu"

    def recognize_crop(self, crop_pil) -> str:
        self.ocr_calls += 1
        return self.text

    def unload(self) -> None:
        self.unloaded = True


class CountingInpainter:
    def __init__(self) -> None:
        self.inpaint_calls = 0
        self.unloaded = False

    def inpaint_page(self, image_bgr, global_mask, blocks, *, device="cpu"):
        self.inpaint_calls += 1
        for b in blocks:
            b.inpaint_method = "opencv_solid"
            b.is_solid_bubble = True
            b.bubble_bg_rgb = (255, 255, 255)
            b.render_bbox = b.bbox
        return image_bgr.copy(), "cpu", {"opencv_solid": len(blocks), "lama_crop": 0}

    def unload(self) -> None:
        self.unloaded = True


class CountingTranslator(TranslatorProvider):
    name = "stub-gemini"

    def __init__(
        self,
        model: str,
        cb: CircuitBreaker,
        tracker: QuotaTracker,
        counter: dict[str, int],
        *,
        fail_with: Optional[Exception] = None,
    ) -> None:
        self.model = model
        self.cb = cb
        self.tracker = tracker
        self.counter = counter
        self.fail_with = fail_with

    def translate_page(
        self,
        blocks: list[TextBlock],
        *,
        source_lang: str = "ja",
        target_lang: str = "pt-BR",
        glossary: Optional[dict[str, str]] = None,
        style: str = "scanlation",
    ) -> tuple[list[TextBlock], Optional[QuotaSnapshot]]:
        def _call():
            self.counter["calls"] += 1
            self.counter["last_glossary"] = glossary
            self.counter["last_source_lang"] = source_lang
            if self.fail_with is not None:
                raise self.fail_with
            for b in blocks:
                b.translated_text = f"Olá ({style}, #{self.counter['calls']})!"
            return self.tracker.record(self.model, 120, 45)

        snap = self.cb.execute(_call)
        return blocks, snap

    def translate_pages_batch(
        self,
        pages_blocks: list[list[TextBlock]],
        *,
        source_langs: Optional[list[str]] = None,
        target_lang: str = "pt-BR",
        glossary: Optional[dict[str, str]] = None,
        style: str = "scanlation",
    ) -> tuple[list[list[TextBlock]], Optional[QuotaSnapshot], list[int]]:
        def _call():
            self.counter["batch_calls"] = self.counter.get("batch_calls", 0) + 1
            self.counter["calls"] += 1
            if self.fail_with is not None:
                raise self.fail_with
            for p_idx, blks in enumerate(pages_blocks):
                for b in blks:
                    b.translated_text = f"Lote p{p_idx} ({style}, #{self.counter['calls']})!"
            return self.tracker.record(self.model, 240, 90)

        snap = self.cb.execute(_call)
        return pages_blocks, snap, []

    def translate_page_vision(
        self,
        blocks: list[TextBlock],
        sheets: list,
        *,
        source_lang: str = "auto",
        target_lang: str = "pt-BR",
        glossary: Optional[dict[str, str]] = None,
        style: str = "scanlation",
    ) -> tuple[list[TextBlock], Optional[QuotaSnapshot]]:
        def _call():
            self.counter["vision_calls"] = self.counter.get("vision_calls", 0) + 1
            self.counter["calls"] += 1
            self.counter["last_glossary"] = glossary
            self.counter["last_sheets"] = len(sheets)
            if self.fail_with is not None:
                raise self.fail_with
            for b in blocks:
                b.translated_text = f"Vision ({style}, #{self.counter['calls']})!"
            return self.tracker.record(self.model, 180, 50)

        snap = self.cb.execute(_call)
        return blocks, snap

    def translate_pages_batch_vision(
        self,
        pages_blocks: list[list[TextBlock]],
        pages_sheets: list[list],
        *,
        source_lang: str = "auto",
        target_lang: str = "pt-BR",
        glossary: Optional[dict[str, str]] = None,
        style: str = "scanlation",
    ) -> tuple[list[list[TextBlock]], Optional[QuotaSnapshot], list[int]]:
        def _call():
            self.counter["vision_batch_calls"] = self.counter.get("vision_batch_calls", 0) + 1
            self.counter["batch_calls"] = self.counter.get("batch_calls", 0) + 1
            self.counter["calls"] += 1
            self.counter["last_glossary"] = glossary
            if self.fail_with is not None:
                raise self.fail_with
            for p_idx, blks in enumerate(pages_blocks):
                for b in blks:
                    b.translated_text = f"VisionLote p{p_idx} ({style})!"
            return self.tracker.record(self.model, 320, 95)

        snap = self.cb.execute(_call)
        return pages_blocks, snap, []


def _build_test_client(
    tmp_path: Path,
    *,
    empty_detector: bool = False,
    fail_translator_with: Optional[Exception] = None,
):
    det = CountingDetector(empty=empty_detector)
    ocr = CountingOCR()
    inp = CountingInpainter()
    tr_counter = {"calls": 0}
    shutdown_called = {"flag": False}

    cfg = PoCConfig(
        gemini_api_key="test-free-key",
        gemini_model=DEFAULT_FREE_MODEL,
        device_preference="cpu",
    )
    engine = WarmScanlationEngine(
        cfg,
        cache_dir=tmp_path / "cache",
        detector_factory=lambda: det,
        ocr_factory=lambda _lang: ocr,
        inpainter_factory=lambda: inp,
        translator_factory=lambda _k, m, cb, qt: CountingTranslator(
            m, cb, qt, tr_counter, fail_with=fail_translator_with
        ),
    )

    def _on_shutdown():
        shutdown_called["flag"] = True

    app = create_app(
        engine=engine,
        port=8765,
        on_shutdown_request=_on_shutdown,
        enable_watchdog=False,
    )
    client = TestClient(app)
    return client, engine, det, ocr, inp, tr_counter, shutdown_called


def test_health_endpoint_reports_status_and_free_tier_models(tmp_path: Path) -> None:
    client, *_ = _build_test_client(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["default_model"] == DEFAULT_FREE_MODEL
    assert DEFAULT_FREE_MODEL in data["free_tier_models"]
    assert data["prompt_version"] == PROMPT_VERSION
    assert data["max_batch_pages"] == 4
    assert "warming_up" in data
    assert data["circuit_breaker"]["state"] == "CLOSED"
    assert data["idle_timeout_sec"] >= 30


def test_translate_page_multipart_and_retry_reuses_sha256_cache(tmp_path: Path) -> None:
    client, _eng, det, ocr, inp, tr_counter, _ = _build_test_client(tmp_path)
    png = _make_png_bytes(seed=10)

    # First call (`ja` -> `manga_ocr_v1`): runs detector, OCR, translation, then post-translation inpainting
    r1 = client.post(
        "/translate-page",
        files={"file": ("page1.png", png, "image/png")},
        data={
            "model": DEFAULT_FREE_MODEL,
            "source_lang": "ja",
            "style": "scanlation",
            "uppercase": "true",
        },
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["ok"] is True
    assert d1["extraction_mode"] == "manga_ocr_v1"
    assert d1["no_text"] is False
    assert d1["blocks_count"] == 1
    assert d1["cache_hit_local"] is False
    assert len(base64.b64decode(d1["image_base64"])) > 100
    assert det.detect_calls == 1
    assert ocr.ocr_calls == 1
    assert inp.inpaint_calls == 1
    assert tr_counter["calls"] == 1

    # Retry (`force_retranslate=True`) via Base64 JSON: MUST reuse detection + OCR cache!
    r2 = client.post(
        "/translate-page",
        json={
            "image_base64": base64.b64encode(png).decode("ascii"),
            "model": DEFAULT_FREE_MODEL,
            "source_lang": "ja",
            "style": "informal",
            "uppercase": False,
            "force_retranslate": True,
        },
    )
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["cache_hit_local"] is True
    assert d2["style"] == "informal"
    assert d2["uppercase"] is False
    assert det.detect_calls == 1      # NEVER re-ran detector!
    assert ocr.ocr_calls == 1         # NEVER re-ran OCR!
    assert tr_counter["calls"] == 2   # Re-ran translation!


def test_zero_text_page_never_calls_gemini_and_uses_negative_cache(tmp_path: Path) -> None:
    client, _eng, det, ocr, inp, tr_counter, _ = _build_test_client(
        tmp_path, empty_detector=True
    )
    png = _make_png_bytes(seed=99)

    r1 = client.post(
        "/translate-page",
        files={"file": ("art_only.png", png, "image/png")},
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["no_text"] is True
    assert d1["blocks_count"] == 0
    assert det.detect_calls == 1
    assert ocr.ocr_calls == 0
    assert inp.inpaint_calls == 0
    assert tr_counter["calls"] == 0  # Gemini NEVER called!

    # Second request hits negative cache immediately without even re-running detector!
    r2 = client.post(
        "/translate-page",
        files={"file": ("art_only.png", png, "image/png")},
    )
    assert r2.status_code == 200
    assert r2.json()["no_text"] is True
    assert r2.json()["cache_hit_local"] is True
    assert det.detect_calls == 1
    assert tr_counter["calls"] == 0


def test_paid_model_is_blocked_with_http_400(tmp_path: Path) -> None:
    client, _eng, _det, _ocr, _inp, tr_counter, _ = _build_test_client(tmp_path)
    png = _make_png_bytes(seed=7)

    resp = client.post(
        "/translate-page",
        json={
            "image_base64": base64.b64encode(png).decode("ascii"),
            "model": "gemini-2.5-pro",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "PAID_MODEL_BLOCKED"
    assert tr_counter["calls"] == 0


def test_circuit_breaker_open_response_and_ui_reset(tmp_path: Path) -> None:
    client, engine, *_ = _build_test_client(
        tmp_path,
        fail_translator_with=RetryableAPIError("429 Quota", status_code=429),
    )
    # Force circuit breaker directly into OPEN state (simulating exhausted 429 failures)
    engine.circuit_breaker._state = CircuitState.OPEN
    engine.circuit_breaker._opened_at = engine.circuit_breaker._clock()

    png = _make_png_bytes(seed=42)
    resp = client.post(
        "/translate-page",
        json={"image_base64": base64.b64encode(png).decode("ascii")},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "CIRCUIT_BREAKER_OPEN"
    assert body["circuit_breaker"]["state"] == "OPEN"

    # UI reset probe restores CLOSED state cleanly
    reset_resp = client.post("/circuit-breaker/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["circuit_breaker"]["state"] == "CLOSED"


def test_shutdown_unloads_all_models_and_triggers_clean_exit(tmp_path: Path) -> None:
    client, _eng, det, ocr, inp, _tr, shutdown_called = _build_test_client(tmp_path)
    png = _make_png_bytes(seed=33)

    # Warm up models (`ja` loads detector + manga-ocr + inpainter)
    r = client.post(
        "/translate-page",
        files={"file": ("p.png", png, "image/png")},
        data={"source_lang": "ja"},
    )
    assert r.status_code == 200
    assert r.json()["mime_type"] == "image/webp"
    assert not det.unloaded and not ocr.unloaded

    # Request shutdown
    s = client.post("/shutdown")
    assert s.status_code == 200
    assert s.json()["status"] == "shutting_down"
    assert det.unloaded is True
    assert ocr.unloaded is True
    assert inp.unloaded is True


def test_prepare_page_preloads_local_cache_without_calling_gemini_or_inpainting(
    tmp_path: Path,
) -> None:
    client, eng, det, ocr, inp, tr_counter, _ = _build_test_client(tmp_path)
    png = _make_png_bytes(seed=55)
    b64 = base64.b64encode(png).decode("ascii")

    # Pipelined prefetch: /prepare-page runs detect (+ OCR if `ja`), ZERO inpainting, ZERO Gemini!
    p = client.post("/prepare-page", json={"image_base64": b64, "source_lang": "auto"})
    assert p.status_code == 200
    assert p.json()["ok"] is True
    assert p.json()["busy_skipped"] is False
    assert p.json()["extraction_mode"] == "vision_grid_v1"
    assert det.detect_calls == 1
    assert ocr.ocr_calls == 0       # `auto` uses Vision Grid -> ZERO local RapidOCR!
    assert inp.inpaint_calls == 0   # Inpainting waits until AFTER translation!
    assert tr_counter["calls"] == 0

    # Subsequent /translate-page hits local detection cache, runs Vision Grid + post-translation inpaint!
    t = client.post(
        "/translate-page",
        json={"image_base64": b64, "source_lang": "auto", "output_format": "webp"},
    )
    assert t.status_code == 200
    assert t.json()["cache_hit_local"] is True
    assert t.json()["extraction_mode"] == "vision_grid_v1"
    assert t.json()["mime_type"] == "image/webp"
    assert det.detect_calls == 1
    assert ocr.ocr_calls == 0
    assert inp.inpaint_calls == 1
    assert tr_counter["vision_calls"] == 1

    # Non-blocking guarantee: while an active translation is running, /prepare-page yields immediately!
    eng._begin_active_translate()
    try:
        p_busy = client.post("/prepare-page", json={"image_base64": b64, "source_lang": "auto"})
        assert p_busy.status_code == 200
        assert p_busy.json()["busy_skipped"] is True
        assert det.detect_calls == 1  # Never waited on or touched GPU lock!
    finally:
        eng._end_active_translate()


def test_translate_batch_groups_multiple_pages_in_one_gemini_call_and_skips_empty_pages(
    tmp_path: Path,
) -> None:
    """Verify POST /translate-batch processes up to 4 pages in 1 Gemini call and skips 0-text pages."""

    class SelectiveDetector(CountingDetector):
        def detect(self, image_bgr: np.ndarray):
            self.detect_calls += 1
            # Treat image with green top-left pixel marker as a 0-text illustration page
            if int(image_bgr[1, 1, 1]) == 200 and int(image_bgr[1, 1, 0]) == 10:
                h, w = image_bgr.shape[:2]
                return [], np.zeros((h, w), dtype=np.uint8)
            return super().detect(image_bgr)

    det = SelectiveDetector()
    ocr = CountingOCR()
    inp = CountingInpainter()
    tr_counter = {"calls": 0, "batch_calls": 0}
    cfg = PoCConfig(
        gemini_api_key="test-free-key",
        gemini_model=DEFAULT_FREE_MODEL,
        device_preference="cpu",
    )
    engine = WarmScanlationEngine(
        cfg,
        cache_dir=tmp_path / "cache",
        detector_factory=lambda: det,
        ocr_factory=lambda _lang: ocr,
        inpainter_factory=lambda: inp,
        translator_factory=lambda _k, m, cb, qt: CountingTranslator(m, cb, qt, tr_counter),
    )
    app = create_app(engine=engine, port=8765, enable_watchdog=False, enable_background_warmup=False)
    client = TestClient(app)

    p0_bytes = _make_png_bytes(seed=101)
    p2_bytes = _make_png_bytes(seed=103)
    # Build an art-only (no text) page with our marker pixel
    art = np.full((440, 320, 3), 240, dtype=np.uint8)
    art[1, 1] = (10, 200, 10)
    ok, buf = cv2.imencode(".png", art)
    assert ok
    p1_empty_bytes = buf.tobytes()

    resp = client.post(
        "/translate-batch",
        json={
            "pages": [
                {"page_id": "pg-1", "image_base64": base64.b64encode(p0_bytes).decode("ascii")},
                {"page_id": "pg-2-art", "image_base64": base64.b64encode(p1_empty_bytes).decode("ascii")},
                {"page_id": "pg-3", "image_base64": base64.b64encode(p2_bytes).decode("ascii")},
            ],
            "style": "scanlation",
            "output_format": "webp",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["prompt_version"] == PROMPT_VERSION
    assert data["pages_count"] == 3
    assert data["fallback_pages"] == []
    assert tr_counter["calls"] == 1        # ONLY 1 Gemini API call for the entire batch!
    assert tr_counter["batch_calls"] == 1
    assert data["pages"][0]["page_id"] == "pg-1"
    assert data["pages"][0]["no_text"] is False
    assert data["pages"][0]["mime_type"] == "image/webp"
    assert data["pages"][1]["page_id"] == "pg-2-art"
    assert data["pages"][1]["no_text"] is True
    assert data["pages"][1]["blocks_count"] == 0
    assert data["pages"][2]["page_id"] == "pg-3"
    assert data["pages"][2]["no_text"] is False
    assert data["avg_ms_per_page"] > 0

    # Rejects > 4 pages cleanly
    too_many = [
        {"page_id": f"p{i}", "image_base64": base64.b64encode(_make_png_bytes(seed=200 + i)).decode("ascii")}
        for i in range(5)
    ]
    r_err = client.post("/translate-batch", json={"pages": too_many})
    assert r_err.status_code == 422

    # Blocks paid model inside batch too!
    r_paid = client.post(
        "/translate-batch",
        json={
            "pages": [{"page_id": "p0", "image_base64": base64.b64encode(p0_bytes).decode("ascii")}],
            "model": "gemini-2.5-pro",
        },
    )
    assert r_paid.status_code == 400
    assert r_paid.json()["code"] == "PAID_MODEL_BLOCKED"


def test_direct_ocr_manual_crop_and_glossary_forwarding_and_tall_strip_single_gemini_call(
    tmp_path: Path,
) -> None:
    # 1. Manual crop (`direct_ocr=True`) succeeds even when detector returns 0 blocks!
    client_empty, _eng, det, ocr, inp, tr_counter, _ = _build_test_client(
        tmp_path / "crop", empty_detector=True
    )
    png = _make_png_bytes(width=240, height=120, seed=77)
    r_crop = client_empty.post(
        "/translate-page",
        json={
            "image_base64": base64.b64encode(png).decode("ascii"),
            "direct_ocr": True,
            "glossary": {"Qi": "Qi", "Sung Jin-Woo": "Sung Jin-Woo"},
            "reading_direction": "ltr",
        },
    )
    assert r_crop.status_code == 200
    dc = r_crop.json()
    assert dc["no_text"] is False
    assert dc["blocks_count"] == 1
    assert dc["reading_direction"] == "ltr"
    assert tr_counter["calls"] == 1
    assert tr_counter["last_glossary"] == {"Qi": "Qi", "Sung Jin-Woo": "Sung Jin-Woo"}

    # 2. Tall Webtoon strip (800x5200) runs detector per slice, ONE Gemini call for entire strip!
    client_tall, _eng2, det2, _ocr2, _inp2, tr_counter2, _ = _build_test_client(tmp_path / "tall")
    tall_img = np.full((5200, 800, 3), 255, dtype=np.uint8)
    cv2.rectangle(tall_img, (100, 200), (700, 1400), (80, 120, 160), -1)
    cv2.rectangle(tall_img, (100, 2100), (700, 3300), (120, 80, 160), -1)
    cv2.rectangle(tall_img, (100, 3900), (700, 4900), (160, 120, 80), -1)
    ok, tall_buf = cv2.imencode(".jpg", tall_img)
    assert ok

    r_tall = client_tall.post(
        "/translate-page",
        json={
            "image_base64": base64.b64encode(tall_buf.tobytes()).decode("ascii"),
            "reading_direction": "auto",
            "output_format": "webp",
        },
    )
    assert r_tall.status_code == 200
    dt = r_tall.json()
    assert dt["slices_count"] >= 3
    assert dt["reading_direction"] == "ltr"  # Auto resolved tall Webtoon to LTR!
    assert det2.detect_calls == dt["slices_count"]
    assert tr_counter2["calls"] == 1  # ONE single Gemini call for ALL slices!


def test_telemetry_logs_all_stage_timings_on_single_and_batch(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, *_ = _build_test_client(tmp_path)
    png1 = _make_png_bytes(seed=301)
    png2 = _make_png_bytes(seed=302)

    with caplog.at_level("INFO", logger="backend.engine"):
        r1 = client.post(
            "/translate-page",
            json={"image_base64": base64.b64encode(png1).decode("ascii"), "source_lang": "auto"},
        )
        assert r1.status_code == 200

        r2 = client.post(
            "/translate-batch",
            json={
                "pages": [
                    {"page_id": "p0", "image_base64": base64.b64encode(png1).decode("ascii")},
                    {"page_id": "p1", "image_base64": base64.b64encode(png2).decode("ascii")},
                ],
                "source_lang": "auto",
            },
        )
        assert r2.status_code == 200

    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "[telemetry] /translate-page" in joined
    assert "[telemetry] /translate-batch" in joined
    for key in ("decode_ms=", "detect_ms=", "ocr_or_grid_ms=", "gemini_ms=", "inpaint_ms=", "render_encode_ms=", "total_ms="):
        assert key in joined, f"Missing telemetry field {key}"
