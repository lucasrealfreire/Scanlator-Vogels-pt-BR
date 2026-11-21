"""Warm on-demand scanlation engine — v1.2 Multimodal Vision Grid & Zero-Contention Turbo.

Architecture:
- Clean language routing (ZERO slow CPU RapidOCR probes on default path!):
  * `ja` (`manga_ocr_v1`): `comic-text-detector` + batched FP16 `manga-ocr` on RTX 5060 Ti ->
    text JSON translation (up to 4 pages/call in `/translate-batch`).
  * `auto`, `ko`, `zh`, `en`, `es`, `fr` (`vision_grid_v1`): `comic-text-detector` (+ `poc/slicer.py`
    for tall Webtoons) -> high-contrast numbered Contact-Sheet Grid (`poc/vision_grid.py`, sorted
    RTL/LTR before numbering, paginated max 18 cells/sheet) -> 1 multimodal Gemini Free Tier call
    (up to 3 normal pages/call in `/translate-batch`; tall paginated strips get dedicated calls).
- Post-translation inpainting ONLY on blocks with non-empty `translated_text` (ZERO erased empty
  balloons! Empty `""` / noise / SFX cells keep 100% original artwork intact).
- Non-blocking lightweight GPU warmup (pre-loads ONLY `comic-text-detector`; `manga-ocr` loads
  lazily only for `ja`), and `/prepare-page` yields immediately whenever an active translation
  is in progress (ZERO `_gpu_lock` contention!).
- Compact 1-line per-request telemetry in `native_host/backend.log`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
import torch
from PIL import Image

from poc.cache import PageCache
from poc.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from poc.config import (
    CACHE_DIR,
    DEFAULT_FREE_MODEL,
    FREE_TIER_MODELS,
    PoCConfig,
    ensure_runtime_dirs,
    validate_free_tier_model,
)
from poc.detector import (
    ComicTextDetector,
    resolve_reading_direction,
    sort_blocks_reading_order,
)
from poc.inpainter import HybridInpainter
from poc.ocr import OCRProvider, create_ocr_provider
from poc.renderer import MangaTextRenderer, encode_pil_image_fast
from poc.schemas import BoundingBox, QuotaSnapshot, TextBlock
from poc.slicer import (
    SliceRegion,
    compute_vertical_slices,
    deduplicate_slice_blocks,
    offset_slice_blocks,
    should_slice_image,
    stitch_inpainted_slices,
)
from poc.translator import (
    EXTRACTION_MODE_MANGA_OCR,
    EXTRACTION_MODE_VISION,
    PROMPT_VERSION,
    GeminiFreeTierTranslator,
    QuotaTracker,
    TranslatorProvider,
)
from poc.vision_grid import MAX_CELLS_PER_SHEET, VisionGridSheet, build_vision_grid_sheets
from poc.vram import (
    get_nvidia_memory_info,
    get_process_ram_mb,
    get_torch_vram_stats_mb,
    release_gpu_memory,
    resolve_device,
)

logger = logging.getLogger(__name__)

SUPPORTED_SOURCE_LANGUAGES: tuple[str, ...] = ("auto", "ja", "ko", "zh", "en", "es", "fr")
SUPPORTED_READING_DIRECTIONS: tuple[str, ...] = ("auto", "rtl", "ltr")
MAX_BATCH_PAGES: int = 4
MAX_VISION_BATCH_PAGES: int = 3


def get_extraction_mode(source_lang: str) -> str:
    """Route `ja` to batched FP16 `manga-ocr` and all other languages + `auto` to Vision Grid."""
    return EXTRACTION_MODE_MANGA_OCR if source_lang == "ja" else EXTRACTION_MODE_VISION


@dataclass
class PageTranslationOutput:
    image_bytes: bytes
    mime_type: str
    image_sha256: str
    width: int
    height: int
    no_text: bool
    blocks_count: int
    translated_blocks_count: int
    cache_hit_local: bool
    model: str
    source_lang: str
    detected_lang: str
    style: str
    uppercase: bool
    device_detector: str
    device_ocr: str
    device_inpaint: str
    timings_ms: dict[str, float] = field(default_factory=dict)
    quota: Optional[QuotaSnapshot] = None
    blocks: list[TextBlock] = field(default_factory=list)
    page_id: str = ""
    used_fallback: bool = False
    reading_direction: str = "rtl"
    slices_count: int = 1
    extraction_mode: str = EXTRACTION_MODE_VISION


@dataclass
class BatchTranslationOutput:
    pages: list[PageTranslationOutput]
    model: str
    source_lang: str
    style: str
    uppercase: bool
    prompt_version: str
    extraction_mode: str
    timings_ms: dict[str, float]
    avg_ms_per_page: float
    fallback_pages: list[int]
    quota: Optional[QuotaSnapshot] = None


@dataclass
class PagePrepareOutput:
    image_sha256: str
    cache_hit_local: bool
    no_text: bool
    blocks_count: int
    source_lang: str
    detected_lang: str
    extraction_mode: str
    busy_skipped: bool = False
    timings_ms: dict[str, float] = field(default_factory=dict)


def decode_image_bytes_bgr(raw_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(raw_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise ValueError("Arquivo enviado nao e uma imagem valida (JPEG/PNG/WEBP/BMP).")
    return img


def encode_bgr_image(
    img_bgr: np.ndarray,
    *,
    output_format: str = "webp",
    webp_quality: int = 89,
    webp_method: int = 1,
) -> tuple[bytes, str]:
    """Encode BGR numpy image via fast Pillow WebP (`method=1`, `q=89`) or JPEG (>16383px)."""
    pil_rgb = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    return encode_pil_image_fast(
        pil_rgb,
        output_format=output_format,
        webp_quality=webp_quality,
        webp_method=webp_method,
    )


def normalize_source_lang(lang: Optional[str]) -> str:
    cleaned = (lang or "auto").strip().lower()
    if cleaned in {"jp", "jpn"}:
        return "ja"
    if cleaned in {"kr", "kor"}:
        return "ko"
    if cleaned in {"cn", "zh-cn", "zh-tw", "zh_tw", "zh_cn"}:
        return "zh"
    if cleaned in SUPPORTED_SOURCE_LANGUAGES:
        return cleaned
    return "auto"


def normalize_reading_direction(direction: Optional[str]) -> str:
    cleaned = (direction or "auto").strip().lower()
    if cleaned in SUPPORTED_READING_DIRECTIONS:
        return cleaned
    return "auto"


class WarmScanlationEngine:
    """Thread-safe warm-session scanlation engine with Multimodal Vision Grid & post-translation inpaint."""

    def __init__(
        self,
        config: Optional[PoCConfig] = None,
        *,
        source_lang: str = "auto",
        cache_dir: Path = CACHE_DIR,
        detector_factory: Optional[Callable[[], Any]] = None,
        ocr_factory: Optional[Callable[[str], OCRProvider]] = None,
        inpainter_factory: Optional[Callable[[], Any]] = None,
        translator_factory: Optional[
            Callable[[str, str, CircuitBreaker, QuotaTracker], TranslatorProvider]
        ] = None,
    ) -> None:
        ensure_runtime_dirs()
        self.config = config or PoCConfig.from_env()
        self.default_source_lang = normalize_source_lang(source_lang)
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._detector_factory = detector_factory or (lambda: ComicTextDetector())
        self._ocr_factory = ocr_factory or create_ocr_provider
        self._inpainter_factory = inpainter_factory or (
            lambda: HybridInpainter(
                crop_margin=self.config.inpaint_crop_margin,
                max_crop_side=self.config.inpaint_max_crop_side,
            )
        )
        self._translator_factory = translator_factory

        self._gpu_lock = threading.RLock()
        self._api_lock = threading.RLock()
        self._active_lock = threading.Lock()
        self._active_translate_count: int = 0

        self._detector: Optional[Any] = None
        self._detector_device: str = "unloaded"
        self._ocr_providers: dict[str, OCRProvider] = {}
        self._ocr_devices: dict[str, str] = {}
        self._inpainter: Optional[Any] = None
        self._renderers: dict[bool, MangaTextRenderer] = {}

        # Lightweight non-blocking background warmup state (ONLY detector + renderer!)
        self._warming_up: bool = False
        self._warmup_done: bool = False
        self._shutdown_requested = threading.Event()
        self._warmup_thread: Optional[threading.Thread] = None

        self.circuit_breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=30.0,
                max_retries=2,
                base_delay=1.5,
                max_delay=20.0,
            )
        )
        self.quota_tracker = QuotaTracker(path=self.cache_dir / "quota_usage.json")

        self.started_at = time.monotonic()
        self._last_activity = self.started_at
        self._idle_timeout_sec: int = max(30, int(self.config.service.idle_shutdown_seconds))
        self._pages_processed: int = 0

    def _begin_active_translate(self) -> None:
        with self._active_lock:
            self._active_translate_count += 1

    def _end_active_translate(self) -> None:
        with self._active_lock:
            self._active_translate_count = max(0, self._active_translate_count - 1)

    def _has_active_translate(self) -> bool:
        with self._active_lock:
            return self._active_translate_count > 0

    def touch_activity(self) -> None:
        self._last_activity = time.monotonic()

    @property
    def idle_timeout_sec(self) -> int:
        return self._idle_timeout_sec

    def set_idle_timeout(self, seconds: int) -> int:
        clamped = max(30, min(3600, int(seconds)))
        self._idle_timeout_sec = clamped
        self.touch_activity()
        return clamped

    def idle_elapsed_sec(self) -> float:
        return max(0.0, time.monotonic() - self._last_activity)

    def idle_remaining_sec(self) -> float:
        return max(0.0, float(self._idle_timeout_sec) - self.idle_elapsed_sec())

    def start_background_warmup(self) -> None:
        """Non-blocking background pre-load of ONLY `comic-text-detector` + renderer (~3s).

        `manga-ocr` loads lazily on the first `ja` request so `auto`/KO/ZH/EN/ES/FR users
        never waste startup lock time or 430 MB VRAM!
        """
        if self._warmup_thread is not None and self._warmup_thread.is_alive():
            return
        self._shutdown_requested.clear()
        self._warming_up = True
        self._warmup_thread = threading.Thread(
            target=self._run_background_warmup,
            name="gpu-warmup",
            daemon=True,
        )
        self._warmup_thread.start()

    def _run_background_warmup(self) -> None:
        t0 = time.perf_counter()
        logger.info("[warmup] Pre-aquecendo apenas comic-text-detector em segundo plano...")
        try:
            if self._shutdown_requested.is_set():
                return
            # Load detector weights without blocking an already-active user translation
            with self._gpu_lock:
                if self._shutdown_requested.is_set():
                    return
                det, det_dev = self._ensure_detector_loaded()
                if not self._has_active_translate() and not self._shutdown_requested.is_set():
                    dummy = np.full((256, 256, 3), 255, dtype=np.uint8)
                    try:
                        det.detect(dummy)
                    except Exception:
                        pass
                _ = self._get_renderer(True)
                self._warmup_done = True
                logger.info(
                    "[warmup] Detector pronto (%s) em %.2fs (manga-ocr sob demanda para 'ja').",
                    det_dev,
                    time.perf_counter() - t0,
                )
        except Exception as exc:
            logger.warning("[warmup] Pre-aquecimento interrompido (%s).", exc)
        finally:
            self._warming_up = False
            if self._shutdown_requested.is_set():
                self.unload_all()

    def _ensure_detector_loaded(self) -> tuple[Any, str]:
        if self._detector is None:
            dev_req = resolve_device(
                self.config.device_preference,
                min_free_vram_mb=self.config.min_free_vram_mb,
            )
            det = self._detector_factory()
            used = det.load(dev_req)
            self._detector = det
            self._detector_device = used
        return self._detector, self._detector_device

    def _ensure_ocr_loaded(self, source_lang: str) -> tuple[OCRProvider, str]:
        lang = normalize_source_lang(source_lang)
        existing = self._ocr_providers.get(lang)
        if existing is not None:
            return existing, self._ocr_devices.get(lang, "cpu")

        dev_req = resolve_device(
            self.config.device_preference,
            min_free_vram_mb=self.config.min_free_vram_mb,
        )
        ocr = self._ocr_factory(lang)
        used = ocr.load(dev_req)
        self._ocr_providers[lang] = ocr
        self._ocr_devices[lang] = used
        return ocr, used

    def _ensure_inpainter(self) -> Any:
        if self._inpainter is None:
            self._inpainter = self._inpainter_factory()
        return self._inpainter

    def _get_renderer(self, uppercase: bool) -> MangaTextRenderer:
        renderer = self._renderers.get(uppercase)
        if renderer is None:
            renderer = MangaTextRenderer(uppercase=uppercase)
            self._renderers[uppercase] = renderer
        return renderer

    def _make_translator(
        self,
        model: str,
        api_key_override: Optional[str],
    ) -> TranslatorProvider:
        validated = validate_free_tier_model(model)
        key = (api_key_override or "").strip() or self.config.gemini_api_key
        if self._translator_factory is not None:
            return self._translator_factory(
                key,
                validated,
                self.circuit_breaker,
                self.quota_tracker,
            )
        return GeminiFreeTierTranslator(
            api_key=key,
            model=validated,
            timeout_seconds=self.config.gemini_timeout_seconds,
            circuit_breaker=self.circuit_breaker,
            quota_tracker=self.quota_tracker,
        )

    def _cache_key(self, image_sha: str, source_lang: str, extraction_mode: str) -> str:
        return f"{image_sha}_{source_lang}_{extraction_mode}"

    def _negative_cache_path(self, cache_key: str) -> Path:
        return self.cache_dir / cache_key / "no_text.json"

    def _has_negative_cache(self, cache_key: str) -> bool:
        return self._negative_cache_path(cache_key).exists()

    def _save_negative_cache(self, cache_key: str, reason: str) -> None:
        p = self._negative_cache_path(cache_key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"no_text": True, "reason": reason, "cache_key": cache_key}),
            encoding="utf-8",
        )

    def _detect_and_extract_page(
        self,
        image_bgr: np.ndarray,
        image_sha: str,
        source_lang: str,
        extraction_mode: str,
        *,
        reading_direction: str = "auto",
        direct_ocr: bool = False,
        use_cache: bool = True,
        force_retranslate: bool = False,
        page_label: str = "",
    ) -> dict[str, Any]:
        """Phase 1 (NO inpainting yet!): Detect blocks (+ slice if tall strip) + OCR (`ja`) OR Vision Grid."""
        img_h, img_w = image_bgr.shape[:2]
        cache_key = self._cache_key(image_sha, source_lang, extraction_mode)
        page_cache = PageCache(cache_key, base_dir=self.cache_dir)
        effective_use_cache = use_cache and not direct_ocr

        detect_ms = 0.0
        ocr_or_grid_ms = 0.0
        dev_det = "cache"
        dev_ocr = "vision_grid" if extraction_mode == EXTRACTION_MODE_VISION else "cache"
        detected_lang = "ja" if source_lang == "ja" else source_lang
        slices_count = 1
        regions: Optional[list[SliceRegion]] = None
        slice_masks: Optional[list[np.ndarray]] = None
        sheets: list[VisionGridSheet] = []

        with self._gpu_lock:
            self.touch_activity()

            if effective_use_cache and not force_retranslate and self._has_negative_cache(cache_key):
                eff_dir = resolve_reading_direction(
                    reading_direction, detected_lang=detected_lang, img_w=img_w, img_h=img_h
                )
                return {
                    "no_text": True,
                    "cache_hit_local": True,
                    "blocks": [],
                    "text_mask": None,
                    "sheets": [],
                    "regions": None,
                    "slice_masks": None,
                    "slices_count": 1,
                    "detected_lang": detected_lang,
                    "reading_direction": eff_dir,
                    "dev_det": "cache",
                    "dev_ocr": "cache",
                    "detect_ms": 0.0,
                    "ocr_or_grid_ms": 0.0,
                }

            cached_det_ocr = page_cache.load_detection_and_ocr() if effective_use_cache else None
            cache_hit_local = False

            if cached_det_ocr is not None:
                blocks, text_mask = cached_det_ocr
                cache_hit_local = True
                if should_slice_image(img_h, img_w):
                    regions = compute_vertical_slices(image_bgr)
                    slices_count = len(regions)
                    slice_masks = [text_mask[r.y1 : r.y2, :] for r in regions]
            else:
                use_slicer = (not direct_ocr) and should_slice_image(img_h, img_w)
                t_det0 = time.perf_counter()
                detector, dev_det = self._ensure_detector_loaded()

                if use_slicer:
                    regions = compute_vertical_slices(image_bgr)
                    slices_count = len(regions)
                    global_mask = np.zeros((img_h, img_w), dtype=np.uint8)
                    slice_entries: list[tuple[TextBlock, SliceRegion]] = []
                    slice_masks = []
                    for reg in regions:
                        sl_bgr = image_bgr[reg.y1 : reg.y2, :]
                        sl_blocks, sl_mask = detector.detect(sl_bgr)
                        slice_masks.append(sl_mask)
                        global_mask[reg.y1 : reg.y2, :] = np.maximum(
                            global_mask[reg.y1 : reg.y2, :], sl_mask
                        )
                        for sb in offset_slice_blocks(sl_blocks, reg.y1):
                            slice_entries.append((sb, reg))
                    blocks = deduplicate_slice_blocks(slice_entries)
                    text_mask = global_mask
                else:
                    blocks, text_mask = detector.detect(image_bgr)
                    if not blocks and direct_ocr and img_w >= 16 and img_h >= 16:
                        pad = min(4, max(0, min(img_w, img_h) // 20))
                        blocks = [
                            TextBlock(
                                id=1,
                                bbox=BoundingBox(x1=pad, y1=pad, x2=img_w - pad, y2=img_h - pad),
                                is_vertical=(img_h > int(img_w * 1.4)),
                                confidence=1.0,
                            )
                        ]

                detect_ms = round((time.perf_counter() - t_det0) * 1000.0, 2)

                if not blocks:
                    if effective_use_cache:
                        self._save_negative_cache(cache_key, "zero_blocks_detected")
                    eff_dir = resolve_reading_direction(
                        reading_direction, detected_lang=detected_lang, img_w=img_w, img_h=img_h
                    )
                    return {
                        "no_text": True,
                        "cache_hit_local": False,
                        "blocks": [],
                        "text_mask": text_mask,
                        "sheets": [],
                        "regions": regions,
                        "slice_masks": slice_masks,
                        "slices_count": slices_count,
                        "detected_lang": detected_lang,
                        "reading_direction": eff_dir,
                        "dev_det": dev_det,
                        "dev_ocr": "skipped",
                        "detect_ms": detect_ms,
                        "ocr_or_grid_ms": 0.0,
                    }

                # Branch A: `ja` -> batched FP16 manga-ocr on RTX 5060 Ti (ZERO RapidOCR probes!)
                if extraction_mode == EXTRACTION_MODE_MANGA_OCR:
                    t_ocr0 = time.perf_counter()
                    ocr, dev_ocr = self._ensure_ocr_loaded("ja")
                    blocks = ocr.recognize_blocks(image_bgr, blocks)
                    if direct_ocr and not any(b.source_text.strip() for b in blocks):
                        pad = min(4, max(0, min(img_w, img_h) // 20))
                        full_blk = TextBlock(
                            id=1,
                            bbox=BoundingBox(x1=pad, y1=pad, x2=img_w - pad, y2=img_h - pad),
                            is_vertical=(img_h > int(img_w * 1.4)),
                            confidence=1.0,
                        )
                        blocks = ocr.recognize_blocks(image_bgr, [full_blk])
                    ocr_or_grid_ms = round((time.perf_counter() - t_ocr0) * 1000.0, 2)
                    detected_lang = "ja"

                    if not any(b.source_text.strip() for b in blocks):
                        if effective_use_cache:
                            self._save_negative_cache(cache_key, "ocr_empty")
                        eff_dir = resolve_reading_direction(
                            reading_direction, detected_lang="ja", blocks=blocks, img_w=img_w, img_h=img_h
                        )
                        return {
                            "no_text": True,
                            "cache_hit_local": False,
                            "blocks": blocks,
                            "text_mask": text_mask,
                            "sheets": [],
                            "regions": regions,
                            "slice_masks": slice_masks,
                            "slices_count": slices_count,
                            "detected_lang": "ja",
                            "reading_direction": eff_dir,
                            "dev_det": dev_det,
                            "dev_ocr": dev_ocr,
                            "detect_ms": detect_ms,
                            "ocr_or_grid_ms": ocr_or_grid_ms,
                        }

                # Save detection (+ manga-ocr if `ja`) to local SHA-256 cache (NO premature inpainting!)
                if effective_use_cache:
                    page_cache.save_detection_and_ocr(
                        blocks,
                        text_mask,
                        detector_name=getattr(detector, "name", "comic-text-detector-onnx"),
                        ocr_name=f"{extraction_mode} ({source_lang})",
                    )

        # Build reading order + Vision Grid sheets (pure CPU, outside `_gpu_lock` so GPU stays free!)
        if extraction_mode == EXTRACTION_MODE_VISION:
            t_grid0 = time.perf_counter()
            sheets, blocks, eff_dir = build_vision_grid_sheets(
                image_bgr,
                blocks,
                reading_direction=reading_direction,
                source_lang=source_lang,
                page_label=page_label,
                max_cells_per_sheet=MAX_CELLS_PER_SHEET,
            )
            ocr_or_grid_ms = round((time.perf_counter() - t_grid0) * 1000.0, 2)
            dev_ocr = "vision_grid"
        else:
            eff_dir = resolve_reading_direction(
                reading_direction,
                detected_lang=detected_lang,
                blocks=blocks,
                img_w=img_w,
                img_h=img_h,
            )
            blocks = sort_blocks_reading_order(
                blocks, direction=eff_dir, img_w=img_w, img_h=img_h
            )

        return {
            "no_text": False,
            "cache_hit_local": cache_hit_local,
            "blocks": blocks,
            "text_mask": text_mask,
            "sheets": sheets,
            "regions": regions,
            "slice_masks": slice_masks,
            "slices_count": slices_count,
            "detected_lang": detected_lang,
            "reading_direction": eff_dir,
            "dev_det": dev_det,
            "dev_ocr": dev_ocr,
            "detect_ms": detect_ms,
            "ocr_or_grid_ms": ocr_or_grid_ms,
        }

    def _run_post_translation_inpaint(
        self,
        image_bgr: np.ndarray,
        text_mask: np.ndarray,
        blocks: list[TextBlock],
        *,
        regions: Optional[list[SliceRegion]] = None,
        slice_masks: Optional[list[np.ndarray]] = None,
    ) -> tuple[np.ndarray, str, float]:
        """Run inpainting STRICTLY AFTER translation, ONLY on blocks with non-empty `translated_text`!"""
        translated = [b for b in blocks if b.translated_text.strip()]
        if not translated:
            for b in blocks:
                b.inpaint_method = "skipped"
            return image_bgr, "skipped", 0.0

        t0 = time.perf_counter()
        img_h, img_w = image_bgr.shape[:2]

        with self._gpu_lock:
            inpainter = self._ensure_inpainter()
            inp_dev_req = resolve_device(
                self.config.device_preference,
                min_free_vram_mb=self.config.min_free_vram_mb,
            )

            # Tall sliced strip: inpaint per slice and stitch cleanly with zero seams!
            if regions and slice_masks and len(regions) > 1:
                cleaned_slices: list[np.ndarray] = []
                dev_inp = "cpu"
                for reg, sl_mask in zip(regions, slice_masks):
                    sl_bgr = image_bgr[reg.y1 : reg.y2, :]
                    sl_blks_local: list[TextBlock] = []
                    sl_blks_refs: list[TextBlock] = []
                    for blk in blocks:
                        if not blk.translated_text.strip():
                            blk.inpaint_method = "skipped"
                            continue
                        if blk.bbox.y2 > reg.y1 and blk.bbox.y1 < reg.y2:
                            lb = blk.model_copy(deep=True)
                            lb.bbox = BoundingBox(
                                x1=blk.bbox.x1,
                                y1=max(0, blk.bbox.y1 - reg.y1),
                                x2=blk.bbox.x2,
                                y2=min(reg.height, blk.bbox.y2 - reg.y1),
                            )
                            lb.lines = [
                                [
                                    [pt[0], max(0, min(reg.height - 1, pt[1] - reg.y1))]
                                    for pt in poly
                                ]
                                for poly in blk.lines
                            ]
                            sl_blks_local.append(lb)
                            sl_blks_refs.append(blk)

                    if sl_blks_local:
                        sl_clean, dev_inp, _ = self._invoke_inpainter(
                            inpainter,
                            sl_bgr,
                            sl_mask,
                            sl_blks_local,
                            device=inp_dev_req,
                        )
                        for lb, orig_b in zip(sl_blks_local, sl_blks_refs):
                            if reg.paste_y1 <= orig_b.bbox.cy < reg.paste_y2:
                                orig_b.inpaint_method = lb.inpaint_method
                                orig_b.is_solid_bubble = lb.is_solid_bubble
                                orig_b.bubble_bg_rgb = lb.bubble_bg_rgb
                                if lb.render_bbox is not None:
                                    orig_b.render_bbox = BoundingBox(
                                        x1=lb.render_bbox.x1,
                                        y1=lb.render_bbox.y1 + reg.y1,
                                        x2=lb.render_bbox.x2,
                                        y2=lb.render_bbox.y2 + reg.y1,
                                    ).clamped(img_w, img_h)
                        cleaned_slices.append(sl_clean)
                    else:
                        cleaned_slices.append(sl_bgr.copy())

                cleaned_bgr = stitch_inpainted_slices(cleaned_slices, regions, img_h, img_w)
            else:
                cleaned_bgr, dev_inp, _ = self._invoke_inpainter(
                    inpainter,
                    image_bgr,
                    text_mask,
                    blocks,
                    device=inp_dev_req,
                )

        inpaint_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return cleaned_bgr, dev_inp, inpaint_ms

    @staticmethod
    def _invoke_inpainter(
        inpainter: Any,
        image_bgr: np.ndarray,
        text_mask: np.ndarray,
        blocks: list[TextBlock],
        *,
        device: str,
    ):
        """Call real `HybridInpainter` with `only_translated=True, keep_lama_warm=True`, or stub safely."""
        try:
            return inpainter.inpaint_page(
                image_bgr,
                text_mask,
                blocks,
                device=device,
                only_translated=True,
                keep_lama_warm=True,
            )
        except TypeError:
            # Compatibility for minimal unit-test stub inpainters
            active = [b for b in blocks if b.translated_text.strip()]
            return inpainter.inpaint_page(image_bgr, text_mask, active, device=device)

    def prepare_image_bytes(
        self,
        raw_bytes: bytes,
        *,
        source_lang: Optional[str] = None,
        reading_direction: str = "auto",
        use_cache: bool = True,
    ) -> PagePrepareOutput:
        """Non-blocking local prefetch (detection + `manga-ocr` if `ja`, ZERO inpainting).

        Immediately yields (`busy_skipped=True`) if an active `/translate-page` or
        `/translate-batch` is running or holding `_gpu_lock`, guaranteeing zero contention!
        """
        self.touch_activity()
        lang = normalize_source_lang(source_lang or self.default_source_lang)
        mode = get_extraction_mode(lang)
        rdir = normalize_reading_direction(reading_direction)
        image_sha = hashlib.sha256(raw_bytes).hexdigest()

        # Yield immediately if ANY active user translation is in progress or GPU lock is busy!
        if self._has_active_translate():
            return PagePrepareOutput(
                image_sha256=image_sha,
                cache_hit_local=False,
                no_text=False,
                blocks_count=0,
                source_lang=lang,
                detected_lang=lang,
                extraction_mode=mode,
                busy_skipped=True,
                timings_ms={"total": 0.0},
            )

        acquired = self._gpu_lock.acquire(blocking=False)
        if not acquired:
            return PagePrepareOutput(
                image_sha256=image_sha,
                cache_hit_local=False,
                no_text=False,
                blocks_count=0,
                source_lang=lang,
                detected_lang=lang,
                extraction_mode=mode,
                busy_skipped=True,
                timings_ms={"total": 0.0},
            )

        try:
            t_start = time.perf_counter()
            image_bgr = decode_image_bytes_bgr(raw_bytes)
            prep = self._detect_and_extract_page(
                image_bgr,
                image_sha,
                lang,
                mode,
                reading_direction=rdir,
                use_cache=use_cache,
                force_retranslate=False,
            )
            total_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
            self.touch_activity()
            return PagePrepareOutput(
                image_sha256=image_sha,
                cache_hit_local=prep["cache_hit_local"],
                no_text=prep["no_text"],
                blocks_count=len(prep["blocks"]),
                source_lang=lang,
                detected_lang=prep["detected_lang"],
                extraction_mode=mode,
                busy_skipped=False,
                timings_ms={
                    "deteccao": prep["detect_ms"],
                    "ocr": prep["ocr_or_grid_ms"],
                    "ocr_or_grid": prep["ocr_or_grid_ms"],
                    "total": total_ms,
                },
            )
        finally:
            self._gpu_lock.release()

    def translate_image_bytes(
        self,
        raw_bytes: bytes,
        *,
        model: Optional[str] = None,
        source_lang: Optional[str] = None,
        reading_direction: str = "auto",
        direct_ocr: bool = False,
        style: str = "scanlation",
        uppercase: bool = True,
        output_format: str = "webp",
        force_retranslate: bool = False,
        use_cache: bool = True,
        api_key_override: Optional[str] = None,
        glossary: Optional[dict[str, str]] = None,
    ) -> PageTranslationOutput:
        self._begin_active_translate()
        try:
            self.touch_activity()
            chosen_model = validate_free_tier_model(model or self.config.gemini_model)
            chosen_lang = normalize_source_lang(source_lang or self.default_source_lang)
            chosen_mode = get_extraction_mode(chosen_lang)
            chosen_dir = normalize_reading_direction(reading_direction)
            chosen_style = (style or "scanlation").strip().lower()
            if chosen_style not in {"scanlation", "informal", "fiel", "formal"}:
                chosen_style = "scanlation"

            t_total_start = time.perf_counter()
            t_dec0 = time.perf_counter()
            image_bgr = decode_image_bytes_bgr(raw_bytes)
            decode_ms = round((time.perf_counter() - t_dec0) * 1000.0, 2)
            img_h, img_w = image_bgr.shape[:2]
            image_sha = hashlib.sha256(raw_bytes).hexdigest()

            prep = self._detect_and_extract_page(
                image_bgr,
                image_sha,
                chosen_lang,
                chosen_mode,
                reading_direction=chosen_dir,
                direct_ocr=direct_ocr,
                use_cache=use_cache,
                force_retranslate=force_retranslate,
            )

            blocks: list[TextBlock] = prep["blocks"]
            text_mask: Optional[np.ndarray] = prep["text_mask"]
            sheets: list[VisionGridSheet] = prep["sheets"]
            detected_lang: str = prep["detected_lang"]
            effective_dir: str = prep["reading_direction"]
            slices_count: int = prep["slices_count"]

            timings: dict[str, float] = {
                "decode": decode_ms,
                "deteccao": prep["detect_ms"],
                "ocr": prep["ocr_or_grid_ms"],
                "ocr_or_grid": prep["ocr_or_grid_ms"],
                "traducao": 0.0,
                "inpainting": 0.0,
                "renderizacao": 0.0,
                "total": 0.0,
            }

            if prep["no_text"] or not blocks or text_mask is None:
                t_enc0 = time.perf_counter()
                encoded_bytes, mime_type = encode_bgr_image(image_bgr, output_format=output_format)
                timings["renderizacao"] = round((time.perf_counter() - t_enc0) * 1000.0, 2)
                timings["total"] = round((time.perf_counter() - t_total_start) * 1000.0, 2)
                self._pages_processed += 1
                self.touch_activity()
                logger.info(
                    "[telemetry] /translate-page mode=%s lang=%s dir=%s blocks=0/0 slices=%d "
                    "decode_ms=%.1f detect_ms=%.1f ocr_or_grid_ms=%.1f gemini_ms=0.0 "
                    "inpaint_ms=0.0 render_encode_ms=%.1f total_ms=%.1f",
                    chosen_mode,
                    chosen_lang,
                    effective_dir,
                    slices_count,
                    decode_ms,
                    timings["deteccao"],
                    timings["ocr_or_grid"],
                    timings["renderizacao"],
                    timings["total"],
                )
                return PageTranslationOutput(
                    image_bytes=encoded_bytes,
                    mime_type=mime_type,
                    image_sha256=image_sha,
                    width=img_w,
                    height=img_h,
                    no_text=True,
                    blocks_count=len(blocks),
                    translated_blocks_count=0,
                    cache_hit_local=prep["cache_hit_local"],
                    model=chosen_model,
                    source_lang=chosen_lang,
                    detected_lang=detected_lang,
                    style=chosen_style,
                    uppercase=uppercase,
                    device_detector=prep["dev_det"],
                    device_ocr=prep["dev_ocr"],
                    device_inpaint="skipped",
                    timings_ms=timings,
                    quota=self.quota_tracker.snapshot(chosen_model),
                    blocks=blocks,
                    reading_direction=effective_dir,
                    slices_count=slices_count,
                    extraction_mode=chosen_mode,
                )

            if force_retranslate:
                for b in blocks:
                    b.translated_text = ""
                    b.tone = ""

            # Phase 2: Gemini Free Tier Translation (Multimodal Vision Grid OR Text JSON for `ja`)
            t3 = time.perf_counter()
            with self._api_lock:
                self.touch_activity()
                translator = self._make_translator(chosen_model, api_key_override)
                if chosen_mode == EXTRACTION_MODE_VISION and hasattr(translator, "translate_page_vision"):
                    blocks, quota_snap = translator.translate_page_vision(
                        blocks,
                        sheets,
                        source_lang=chosen_lang,
                        target_lang="pt-BR",
                        glossary=glossary,
                        style=chosen_style,
                    )
                else:
                    blocks, quota_snap = translator.translate_page(
                        blocks,
                        source_lang=detected_lang,
                        target_lang="pt-BR",
                        glossary=glossary,
                        style=chosen_style,
                    )
            timings["traducao"] = round((time.perf_counter() - t3) * 1000.0, 2)

            translated_count = sum(1 for b in blocks if b.translated_text.strip())

            # Phase 3: Post-translation Inpainting ONLY on blocks with non-empty `translated_text`!
            inpainted_bgr, dev_inp, inpaint_ms = self._run_post_translation_inpaint(
                image_bgr,
                text_mask,
                blocks,
                regions=prep["regions"],
                slice_masks=prep["slice_masks"],
            )
            timings["inpainting"] = inpaint_ms

            # Phase 4: Fast direct Pillow WebP/JPEG rendering (skips untouched original art if 0 translated!)
            t4 = time.perf_counter()
            if translated_count == 0:
                encoded_bytes, mime_type = encode_bgr_image(image_bgr, output_format=output_format)
            else:
                renderer = self._get_renderer(uppercase=uppercase)
                encoded_bytes, mime_type = renderer.render_page_to_bytes(
                    inpainted_bgr,
                    blocks,
                    output_format=output_format,
                    webp_quality=89,
                    webp_method=1,
                )
            timings["renderizacao"] = round((time.perf_counter() - t4) * 1000.0, 2)
            timings["total"] = round((time.perf_counter() - t_total_start) * 1000.0, 2)

            self._pages_processed += 1
            self.touch_activity()

            logger.info(
                "[telemetry] /translate-page mode=%s lang=%s dir=%s blocks=%d/%d slices=%d "
                "decode_ms=%.1f detect_ms=%.1f ocr_or_grid_ms=%.1f gemini_ms=%.1f "
                "inpaint_ms=%.1f render_encode_ms=%.1f total_ms=%.1f",
                chosen_mode,
                chosen_lang,
                effective_dir,
                translated_count,
                len(blocks),
                slices_count,
                decode_ms,
                timings["deteccao"],
                timings["ocr_or_grid"],
                timings["traducao"],
                timings["inpainting"],
                timings["renderizacao"],
                timings["total"],
            )

            return PageTranslationOutput(
                image_bytes=encoded_bytes,
                mime_type=mime_type,
                image_sha256=image_sha,
                width=img_w,
                height=img_h,
                no_text=(translated_count == 0),
                blocks_count=len(blocks),
                translated_blocks_count=translated_count,
                cache_hit_local=prep["cache_hit_local"],
                model=chosen_model,
                source_lang=chosen_lang,
                detected_lang=detected_lang,
                style=chosen_style,
                uppercase=uppercase,
                device_detector=prep["dev_det"],
                device_ocr=prep["dev_ocr"],
                device_inpaint=dev_inp,
                timings_ms=timings,
                quota=quota_snap,
                blocks=blocks,
                reading_direction=effective_dir,
                slices_count=slices_count,
                extraction_mode=chosen_mode,
            )
        finally:
            self._end_active_translate()

    def translate_batch_bytes(
        self,
        items: list[tuple[str, bytes, bool]],
        *,
        model: Optional[str] = None,
        source_lang: Optional[str] = None,
        reading_direction: str = "auto",
        style: str = "scanlation",
        uppercase: bool = True,
        output_format: str = "webp",
        use_cache: bool = True,
        api_key_override: Optional[str] = None,
        glossary: Optional[dict[str, str]] = None,
    ) -> BatchTranslationOutput:
        """Translate a batch of pages:
        - `ja` (`manga_ocr_v1`): up to 4 pages in 1 grouped text JSON call.
        - `vision_grid_v1`: groups up to 3 normal pages (`P0`, `P1`, `P2`) per multimodal call,
          automatically isolating any paginated tall Webtoon strip (>1 sheet) into its own
          dedicated multimodal call so precision & context never degrade!
        - Post-translation inpainting runs STRICTLY on blocks with non-empty `translated_text`.
        """
        if not items or len(items) > MAX_BATCH_PAGES:
            raise ValueError(f"Lote deve conter entre 1 e {MAX_BATCH_PAGES} paginas.")

        self._begin_active_translate()
        try:
            self.touch_activity()
            chosen_model = validate_free_tier_model(model or self.config.gemini_model)
            chosen_lang = normalize_source_lang(source_lang or self.default_source_lang)
            chosen_mode = get_extraction_mode(chosen_lang)
            chosen_dir = normalize_reading_direction(reading_direction)
            chosen_style = (style or "scanlation").strip().lower()
            if chosen_style not in {"scanlation", "informal", "fiel", "formal"}:
                chosen_style = "scanlation"

            t_batch_start = time.perf_counter()
            decode_total_ms = 0.0
            detect_total_ms = 0.0
            ocr_grid_total_ms = 0.0

            # Phase 1: Decode + Detect + (`manga-ocr` if `ja`, OR Vision Grid sheets)
            prepared: list[dict[str, Any]] = []
            for idx, (page_id, raw_bytes, force_retranslate) in enumerate(items):
                pid = page_id or f"p{idx}"
                t_d0 = time.perf_counter()
                image_bgr = decode_image_bytes_bgr(raw_bytes)
                d_ms = round((time.perf_counter() - t_d0) * 1000.0, 2)
                decode_total_ms += d_ms
                img_h, img_w = image_bgr.shape[:2]
                image_sha = hashlib.sha256(raw_bytes).hexdigest()

                prep = self._detect_and_extract_page(
                    image_bgr,
                    image_sha,
                    chosen_lang,
                    chosen_mode,
                    reading_direction=chosen_dir,
                    use_cache=use_cache,
                    force_retranslate=force_retranslate,
                    page_label=f"P{idx}" if chosen_mode == EXTRACTION_MODE_VISION else "",
                )
                detect_total_ms += prep["detect_ms"]
                ocr_grid_total_ms += prep["ocr_or_grid_ms"]

                if force_retranslate:
                    for b in prep["blocks"]:
                        b.translated_text = ""
                        b.tone = ""

                prepared.append(
                    {
                        "page_id": pid,
                        "image_bgr": image_bgr,
                        "width": img_w,
                        "height": img_h,
                        "sha256": image_sha,
                        "no_text": prep["no_text"] or not prep["blocks"] or prep["text_mask"] is None,
                        "cache_hit": prep["cache_hit_local"],
                        "blocks": prep["blocks"],
                        "text_mask": prep["text_mask"],
                        "sheets": prep["sheets"],
                        "regions": prep["regions"],
                        "slice_masks": prep["slice_masks"],
                        "detected_lang": prep["detected_lang"],
                        "reading_direction": prep["reading_direction"],
                        "slices_count": prep["slices_count"],
                        "dev_det": prep["dev_det"],
                        "dev_ocr": prep["dev_ocr"],
                        "decode_ms": d_ms,
                        "detect_ms": prep["detect_ms"],
                        "ocr_or_grid_ms": prep["ocr_or_grid_ms"],
                    }
                )

            # Phase 2: Gemini Free Tier Translation
            text_indices = [i for i, p in enumerate(prepared) if not p["no_text"]]
            quota_snap: Optional[QuotaSnapshot] = self.quota_tracker.snapshot(chosen_model)
            fallback_indices_global: list[int] = []

            t_api_start = time.perf_counter()
            if text_indices:
                with self._api_lock:
                    self.touch_activity()
                    translator = self._make_translator(chosen_model, api_key_override)

                    if chosen_mode == EXTRACTION_MODE_VISION and hasattr(
                        translator, "translate_pages_batch_vision"
                    ):
                        # Separate paginated tall strips (>1 sheet) into dedicated calls;
                        # group normal 1-sheet pages in chunks of up to 3 pages (P0, P1, P2)!
                        tall_indices = [i for i in text_indices if len(prepared[i]["sheets"]) > 1]
                        normal_indices = [i for i in text_indices if len(prepared[i]["sheets"]) <= 1]

                        for chunk_start in range(0, len(normal_indices), MAX_VISION_BATCH_PAGES):
                            chunk_ids = normal_indices[
                                chunk_start : chunk_start + MAX_VISION_BATCH_PAGES
                            ]
                            if len(chunk_ids) == 1:
                                i0 = chunk_ids[0]
                                _, quota_snap = translator.translate_page_vision(
                                    prepared[i0]["blocks"],
                                    prepared[i0]["sheets"],
                                    source_lang=chosen_lang,
                                    target_lang="pt-BR",
                                    glossary=glossary,
                                    style=chosen_style,
                                )
                            else:
                                # Re-label sheets P0..Pk cleanly for this multimodal chunk!
                                chunk_blocks = [prepared[i]["blocks"] for i in chunk_ids]
                                chunk_sheets: list[list[VisionGridSheet]] = []
                                for rel_idx, orig_i in enumerate(chunk_ids):
                                    shs = prepared[orig_i]["sheets"]
                                    if shs and shs[0].page_label != f"P{rel_idx}":
                                        shs, prepared[orig_i]["blocks"], _ = build_vision_grid_sheets(
                                            prepared[orig_i]["image_bgr"],
                                            prepared[orig_i]["blocks"],
                                            reading_direction=prepared[orig_i]["reading_direction"],
                                            source_lang=chosen_lang,
                                            page_label=f"P{rel_idx}",
                                            max_cells_per_sheet=MAX_CELLS_PER_SHEET,
                                        )
                                        prepared[orig_i]["sheets"] = shs
                                        chunk_blocks[rel_idx] = prepared[orig_i]["blocks"]
                                    chunk_sheets.append(shs)

                                _, quota_snap, rel_fb = translator.translate_pages_batch_vision(
                                    chunk_blocks,
                                    chunk_sheets,
                                    source_lang=chosen_lang,
                                    target_lang="pt-BR",
                                    glossary=glossary,
                                    style=chosen_style,
                                )
                                fallback_indices_global.extend(chunk_ids[r] for r in rel_fb)

                        # Dedicated multimodal call for each paginated tall Webtoon strip
                        for ti in tall_indices:
                            _, quota_snap = translator.translate_page_vision(
                                prepared[ti]["blocks"],
                                prepared[ti]["sheets"],
                                source_lang=chosen_lang,
                                target_lang="pt-BR",
                                glossary=glossary,
                                style=chosen_style,
                            )
                    elif hasattr(translator, "translate_pages_batch"):
                        batch_blocks = [prepared[i]["blocks"] for i in text_indices]
                        batch_langs = [prepared[i]["detected_lang"] for i in text_indices]
                        _, quota_snap, rel_fallbacks = translator.translate_pages_batch(
                            batch_blocks,
                            source_langs=batch_langs,
                            target_lang="pt-BR",
                            glossary=glossary,
                            style=chosen_style,
                        )
                        fallback_indices_global = [text_indices[r] for r in rel_fallbacks]
                    else:
                        for i in text_indices:
                            _, quota_snap = translator.translate_page(
                                prepared[i]["blocks"],
                                source_lang=prepared[i]["detected_lang"],
                                target_lang="pt-BR",
                                glossary=glossary,
                                style=chosen_style,
                            )
            api_total_ms = round((time.perf_counter() - t_api_start) * 1000.0, 2)

            # Phase 3 + 4: Post-translation inpainting ONLY on translated blocks + fast WebP/JPEG render
            inpaint_total_ms = 0.0
            render_total_ms = 0.0
            renderer = self._get_renderer(uppercase=uppercase)
            outputs: list[PageTranslationOutput] = []

            for idx, p in enumerate(prepared):
                blks: list[TextBlock] = p["blocks"]
                translated_count = 0 if p["no_text"] else sum(1 for b in blks if b.translated_text.strip())

                if p["no_text"] or translated_count == 0 or p["text_mask"] is None:
                    dev_inp = "skipped"
                    inp_ms = 0.0
                    t_r0 = time.perf_counter()
                    img_bytes, mime = encode_bgr_image(p["image_bgr"], output_format=output_format)
                    render_ms = round((time.perf_counter() - t_r0) * 1000.0, 2)
                else:
                    inpainted_bgr, dev_inp, inp_ms = self._run_post_translation_inpaint(
                        p["image_bgr"],
                        p["text_mask"],
                        blks,
                        regions=p["regions"],
                        slice_masks=p["slice_masks"],
                    )
                    t_r0 = time.perf_counter()
                    img_bytes, mime = renderer.render_page_to_bytes(
                        inpainted_bgr,
                        blks,
                        output_format=output_format,
                        webp_quality=89,
                        webp_method=1,
                    )
                    render_ms = round((time.perf_counter() - t_r0) * 1000.0, 2)

                inpaint_total_ms += inp_ms
                render_total_ms += render_ms

                outputs.append(
                    PageTranslationOutput(
                        page_id=p["page_id"],
                        image_bytes=img_bytes,
                        mime_type=mime,
                        image_sha256=p["sha256"],
                        width=p["width"],
                        height=p["height"],
                        no_text=(p["no_text"] or translated_count == 0),
                        blocks_count=len(blks),
                        translated_blocks_count=translated_count,
                        cache_hit_local=p["cache_hit"],
                        model=chosen_model,
                        source_lang=chosen_lang,
                        detected_lang=p["detected_lang"],
                        style=chosen_style,
                        uppercase=uppercase,
                        device_detector=p["dev_det"],
                        device_ocr=p["dev_ocr"],
                        device_inpaint=dev_inp,
                        timings_ms={
                            "decode": p["decode_ms"],
                            "deteccao": p["detect_ms"],
                            "ocr": p["ocr_or_grid_ms"],
                            "ocr_or_grid": p["ocr_or_grid_ms"],
                            "inpainting": inp_ms,
                            "renderizacao": render_ms,
                        },
                        quota=quota_snap,
                        blocks=blks,
                        used_fallback=(idx in fallback_indices_global),
                        reading_direction=p["reading_direction"],
                        slices_count=p["slices_count"],
                        extraction_mode=chosen_mode,
                    )
                )
                self._pages_processed += 1

            total_ms = round((time.perf_counter() - t_batch_start) * 1000.0, 2)
            avg_ms = round(total_ms / max(1, len(outputs)), 2)
            self.touch_activity()

            logger.info(
                "[telemetry] /translate-batch pages=%d mode=%s lang=%s "
                "decode_ms=%.1f detect_ms=%.1f ocr_or_grid_ms=%.1f gemini_ms=%.1f "
                "inpaint_ms=%.1f render_encode_ms=%.1f total_ms=%.1f avg_ms_per_page=%.1f",
                len(outputs),
                chosen_mode,
                chosen_lang,
                decode_total_ms,
                detect_total_ms,
                ocr_grid_total_ms,
                api_total_ms,
                inpaint_total_ms,
                render_total_ms,
                total_ms,
                avg_ms,
            )

            return BatchTranslationOutput(
                pages=outputs,
                model=chosen_model,
                source_lang=chosen_lang,
                style=chosen_style,
                uppercase=uppercase,
                prompt_version=PROMPT_VERSION,
                extraction_mode=chosen_mode,
                timings_ms={
                    "decode": round(decode_total_ms, 2),
                    "deteccao": round(detect_total_ms, 2),
                    "ocr_or_grid": round(ocr_grid_total_ms, 2),
                    "local_vision": round(decode_total_ms + detect_total_ms + ocr_grid_total_ms, 2),
                    "traducao_lote": api_total_ms,
                    "inpainting_lote": round(inpaint_total_ms, 2),
                    "renderizacao_lote": round(render_total_ms, 2),
                    "total": total_ms,
                },
                avg_ms_per_page=avg_ms,
                fallback_pages=sorted(set(fallback_indices_global)),
                quota=quota_snap,
            )
        finally:
            self._end_active_translate()

    def get_circuit_breaker_status(self) -> dict[str, Any]:
        cb = self.circuit_breaker
        return {
            "state": cb.state.value,
            "consecutive_failures": cb.consecutive_failures,
            "retry_after_sec": round(cb.time_until_recovery(), 1),
        }

    def reset_circuit_breaker(self) -> dict[str, Any]:
        self.circuit_breaker.reset()
        self.touch_activity()
        return self.get_circuit_breaker_status()

    def clear_disk_cache(self) -> int:
        removed = 0
        if not self.cache_dir.exists():
            return 0
        for child in self.cache_dir.iterdir():
            if child.is_dir() and child.name != "cuda_jit":
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        self.touch_activity()
        return removed

    def get_health_snapshot(self, *, port: int) -> dict[str, Any]:
        alloc_mb, peak_mb, reserved_mb = get_torch_vram_stats_mb()
        nv = get_nvidia_memory_info()
        loaded_models: list[str] = []
        if self._detector is not None:
            loaded_models.append(f"detector:{self._detector_device}")
        for lang, _prov in self._ocr_providers.items():
            loaded_models.append(f"ocr[{lang}]:{self._ocr_devices.get(lang, 'cpu')}")
        if self._inpainter is not None and getattr(self._inpainter, "_lama_model", None) is not None:
            loaded_models.append(f"lama:{getattr(self._inpainter, '_lama_device', 'cuda')}")

        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU-only"
        quota_snap = self.quota_tracker.snapshot(self.config.gemini_model)

        return {
            "status": "ready",
            "pid": __import__("os").getpid(),
            "port": port,
            "uptime_sec": round(time.monotonic() - self.started_at, 1),
            "pages_processed": self._pages_processed,
            "idle_timeout_sec": self._idle_timeout_sec,
            "idle_remaining_sec": round(self.idle_remaining_sec(), 1),
            "models_loaded": loaded_models,
            "gpu_name": gpu_name,
            "ram_mb": round(get_process_ram_mb(), 1),
            "torch_vram_allocated_mb": alloc_mb,
            "torch_vram_peak_mb": peak_mb,
            "torch_vram_reserved_mb": reserved_mb,
            "nvidia_vram_used_mb": nv.used_mb if nv else None,
            "nvidia_vram_free_mb": nv.free_mb if nv else None,
            "nvidia_vram_total_mb": nv.total_mb if nv else None,
            "circuit_breaker": self.get_circuit_breaker_status(),
            "free_tier_models": sorted(FREE_TIER_MODELS),
            "default_model": DEFAULT_FREE_MODEL,
            "prompt_version": PROMPT_VERSION,
            "max_batch_pages": MAX_BATCH_PAGES,
            "max_vision_batch_pages": MAX_VISION_BATCH_PAGES,
            "warming_up": self._warming_up,
            "warmup_done": self._warmup_done,
            "has_env_api_key": bool(self.config.gemini_api_key),
            "quota": quota_snap.model_dump(),
        }

    def unload_all(self) -> None:
        """Cancel warmup, explicitly destroy all GPU/CPU models and release 100% VRAM."""
        self._shutdown_requested.set()
        with self._gpu_lock:
            if self._detector is not None:
                try:
                    self._detector.unload()
                except Exception:
                    pass
                self._detector = None
                self._detector_device = "unloaded"

            unloaded_ids: set[int] = set()
            for prov in list(self._ocr_providers.values()):
                if id(prov) in unloaded_ids:
                    continue
                unloaded_ids.add(id(prov))
                try:
                    prov.unload()
                except Exception:
                    pass
            self._ocr_providers.clear()
            self._ocr_devices.clear()

            if self._inpainter is not None:
                try:
                    self._inpainter.unload()
                except Exception:
                    pass
                self._inpainter = None

            self._renderers.clear()
            self._warming_up = False
            release_gpu_memory()


class IdleWatchdog:
    """Monitors engine inactivity and triggers clean zero-VRAM shutdown after timeout."""

    def __init__(
        self,
        engine: WarmScanlationEngine,
        on_timeout: Callable[[], None],
        *,
        poll_interval_sec: float = 5.0,
    ) -> None:
        self.engine = engine
        self.on_timeout = on_timeout
        self.poll_interval_sec = poll_interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="idle-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        while not self._stop_event.wait(self.poll_interval_sec):
            if self.engine.idle_remaining_sec() <= 0.0 and not self.engine._has_active_translate():
                logger.info(
                    "Timeout de inatividade atingido (%ds). Descarregando VRAM e encerrando motor...",
                    self.engine.idle_timeout_sec,
                )
                self.engine.unload_all()
                self.on_timeout()
                return
