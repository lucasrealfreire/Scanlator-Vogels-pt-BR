"""Warm on-demand scanlation engine — Turbo Free Tier edition.

Upgrades:
- Non-blocking background GPU warmup on startup (`start_background_warmup()`), pre-loading
  detector + FP16 `manga-ocr` so Page 1 runs warm immediately, while `GET /health` responds
  instantly (`warming_up` flag) and `POST /shutdown` cancels warmup & frees 100% VRAM.
- Multi-page batch translation (`translate_batch_bytes`, up to 4 pages in 1 Gemini JSON call)
  with negative caching for 0-text pages and isolated per-page fallback if batch JSON omits
  any page.
- Ultra-fast direct Pillow WebP encoding (`method=1`, `quality=89`).
- Pipelined `_gpu_lock` + `_api_lock` concurrency (Page N+1 preps locally during Gemini I/O).
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
    compute_vertical_slices,
    deduplicate_slice_blocks,
    offset_slice_blocks,
    should_slice_image,
    stitch_inpainted_slices,
)
from poc.translator import (
    PROMPT_VERSION,
    GeminiFreeTierTranslator,
    QuotaTracker,
    TranslatorProvider,
)
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


@dataclass
class BatchTranslationOutput:
    pages: list[PageTranslationOutput]
    model: str
    source_lang: str
    style: str
    uppercase: bool
    prompt_version: str
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
    """Encode BGR numpy image via fast Pillow WebP (`method=1`, `q=89`) or PNG."""
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
    """Thread-safe warm-session scanlation engine with background warmup & batch translation."""

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

        self._detector: Optional[Any] = None
        self._detector_device: str = "unloaded"
        self._ocr_providers: dict[str, OCRProvider] = {}
        self._ocr_devices: dict[str, str] = {}
        self._inpainter: Optional[Any] = None
        self._renderers: dict[bool, MangaTextRenderer] = {}

        # Non-blocking background warmup state
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
        """Non-blocking background pre-load of detector + FP16 manga-ocr right after boot."""
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
        logger.info("[warmup] Iniciando pre-aquecimento em segundo plano (detector + manga-ocr FP16)...")
        try:
            # Step 1: Pre-load & warm comic-text-detector
            if self._shutdown_requested.is_set():
                return
            with self._gpu_lock:
                if self._shutdown_requested.is_set():
                    return
                det, det_dev = self._ensure_detector_loaded()
                # Run tiny dummy detection so CUDA/ONNX graph is 100% hot before page 1
                dummy = np.full((256, 256, 3), 255, dtype=np.uint8)
                try:
                    det.detect(dummy)
                except Exception:
                    pass
                logger.info("[warmup] Detector pronto (%s) em %.2fs.", det_dev, time.perf_counter() - t0)

            # Step 2: Pre-load & warm FP16 manga-ocr inside the `auto` provider so `auto` & `ja` share it!
            if self._shutdown_requested.is_set():
                return
            with self._gpu_lock:
                if self._shutdown_requested.is_set():
                    return
                auto_ocr, _ = self._ensure_ocr_loaded("auto")
                if hasattr(auto_ocr, "_get_manga"):
                    manga = auto_ocr._get_manga()
                    # Also register the same warmed MangaOCRProvider under `ja` (zero duplicate VRAM!)
                    self._ocr_providers["ja"] = manga
                    self._ocr_devices["ja"] = getattr(manga, "_device", "cuda")
                    # Warm FP16 autocast kernels fast (max_length=4, avoids 300-token blank loop)
                    try:
                        if hasattr(manga, "warmup"):
                            manga.warmup()
                    except Exception:
                        pass
                # Warm Pillow renderer & pyphen pt_BR dictionary in RAM
                _ = self._get_renderer(True)
                self._warmup_done = True
                logger.info("[warmup] Pre-aquecimento concluido em %.2fs.", time.perf_counter() - t0)
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

        # Share already-warmed manga-ocr instance from `auto` when requesting `ja` (0 extra VRAM!)
        if lang == "ja" and "auto" in self._ocr_providers:
            auto_p = self._ocr_providers["auto"]
            if hasattr(auto_p, "_manga_ocr") and getattr(auto_p, "_manga_ocr") is not None:
                shared = getattr(auto_p, "_manga_ocr")
                self._ocr_providers["ja"] = shared
                self._ocr_devices["ja"] = getattr(shared, "_device", "cuda")
                return shared, self._ocr_devices["ja"]

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

    def _cache_key(self, image_sha: str, source_lang: str) -> str:
        return f"{image_sha}_{source_lang}"

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

    def _run_local_vision_stages(
        self,
        image_bgr: np.ndarray,
        image_sha: str,
        source_lang: str,
        *,
        reading_direction: str = "auto",
        direct_ocr: bool = False,
        use_cache: bool,
        force_retranslate: bool,
    ) -> tuple[
        bool,
        bool,
        list[TextBlock],
        Optional[np.ndarray],
        str,
        str,
        int,
        dict[str, str],
        dict[str, float],
    ]:
        img_h, img_w = image_bgr.shape[:2]
        cache_key = self._cache_key(image_sha, source_lang)
        page_cache = PageCache(cache_key, base_dir=self.cache_dir)
        timings = {"deteccao": 0.0, "ocr": 0.0, "inpainting": 0.0}
        devices = {"deteccao": "cache", "ocr": "cache", "inpainting": "cache"}
        detected_lang = "ja" if source_lang == "auto" else source_lang
        slices_count = 1
        effective_use_cache = use_cache and not direct_ocr

        with self._gpu_lock:
            self.touch_activity()

            if effective_use_cache and not force_retranslate and self._has_negative_cache(cache_key):
                eff_dir = resolve_reading_direction(
                    reading_direction, detected_lang=detected_lang, img_w=img_w, img_h=img_h
                )
                return True, True, [], None, detected_lang, eff_dir, 1, devices, timings

            cached_det_ocr = page_cache.load_detection_and_ocr() if effective_use_cache else None
            cache_hit_local = False

            if cached_det_ocr is not None:
                blocks, text_mask = cached_det_ocr
                cache_hit_local = True
                all_txt = " ".join(b.source_text for b in blocks)
                if source_lang != "auto":
                    detected_lang = source_lang
                else:
                    kana_n = sum(1 for c in all_txt if "\u3040" <= c <= "\u30ff")
                    hangul_n = sum(1 for c in all_txt if "\uac00" <= c <= "\ud7af")
                    hanzi_n = sum(1 for c in all_txt if "\u4e00" <= c <= "\u9fff")
                    lat_n = sum(1 for c in all_txt if "a" <= c.lower() <= "z")
                    if hangul_n >= 2:
                        detected_lang = "ko"
                    elif kana_n >= 1:
                        detected_lang = "ja"
                    elif hanzi_n >= 2 and hanzi_n >= lat_n:
                        detected_lang = "zh"
                    elif lat_n > hanzi_n:
                        detected_lang = "en"
                    else:
                        detected_lang = "ja"
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
                cached_inpainted = page_cache.load_inpainted(blocks) if effective_use_cache else None
                if cached_inpainted is not None:
                    inpainted_bgr = cached_inpainted
                else:
                    t2 = time.perf_counter()
                    inpainter = self._ensure_inpainter()
                    inp_dev_req = resolve_device(
                        self.config.device_preference,
                        min_free_vram_mb=self.config.min_free_vram_mb,
                    )
                    inpainted_bgr, dev_inp, _ = inpainter.inpaint_page(
                        image_bgr,
                        text_mask,
                        blocks,
                        device=inp_dev_req,
                    )
                    timings["inpainting"] = round((time.perf_counter() - t2) * 1000.0, 2)
                    devices["inpainting"] = dev_inp
                    if effective_use_cache:
                        page_cache.save_inpainted(inpainted_bgr, blocks)
                return (
                    False,
                    cache_hit_local,
                    blocks,
                    inpainted_bgr,
                    detected_lang,
                    eff_dir,
                    1,
                    devices,
                    timings,
                )

            # Determine whether this is a tall Webtoon/Manhwa strip requiring smart vertical slicing
            use_slicer = (not direct_ocr) and should_slice_image(img_h, img_w)
            if use_slicer:
                regions = compute_vertical_slices(image_bgr)
                slices_count = len(regions)
                logger.info(
                    "Tira longa detectada (%dx%d): fatiando em %d fatias seguras.",
                    img_w,
                    img_h,
                    slices_count,
                )
                t_det0 = time.perf_counter()
                detector, dev_det = self._ensure_detector_loaded()
                global_mask = np.zeros((img_h, img_w), dtype=np.uint8)
                slice_entries: list[tuple[TextBlock, Any]] = []
                slice_local_blocks: list[list[TextBlock]] = []
                slice_local_masks: list[np.ndarray] = []

                for reg in regions:
                    sl_bgr = image_bgr[reg.y1 : reg.y2, :]
                    sl_blocks, sl_mask = detector.detect(sl_bgr)
                    slice_local_blocks.append(sl_blocks)
                    slice_local_masks.append(sl_mask)
                    global_mask[reg.y1 : reg.y2, :] = np.maximum(
                        global_mask[reg.y1 : reg.y2, :], sl_mask
                    )
                    shifted = offset_slice_blocks(sl_blocks, reg.y1)
                    for sb in shifted:
                        slice_entries.append((sb, reg))

                blocks = deduplicate_slice_blocks(slice_entries)
                text_mask = global_mask
                timings["deteccao"] = round((time.perf_counter() - t_det0) * 1000.0, 2)
                devices["deteccao"] = dev_det

                if not blocks:
                    if effective_use_cache:
                        self._save_negative_cache(cache_key, "zero_blocks_detected")
                    devices["ocr"] = "skipped"
                    devices["inpainting"] = "skipped"
                    eff_dir = resolve_reading_direction(
                        reading_direction, detected_lang=detected_lang, img_w=img_w, img_h=img_h
                    )
                    return True, False, [], None, detected_lang, eff_dir, slices_count, devices, timings

                # Run OCR across unified deduplicated blocks directly from full strip
                t_ocr0 = time.perf_counter()
                ocr, dev_ocr = self._ensure_ocr_loaded(source_lang)
                blocks = ocr.recognize_blocks(image_bgr, blocks)
                timings["ocr"] = round((time.perf_counter() - t_ocr0) * 1000.0, 2)
                devices["ocr"] = dev_ocr
                detected_lang = getattr(ocr, "detected_primary_language", detected_lang)

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

                if not any(b.source_text.strip() for b in blocks):
                    if effective_use_cache:
                        self._save_negative_cache(cache_key, "ocr_empty")
                    devices["inpainting"] = "skipped"
                    return True, False, blocks, None, detected_lang, eff_dir, slices_count, devices, timings

                # Run inpainting per slice and seamlessly stitch back to full height
                t_inp0 = time.perf_counter()
                inpainter = self._ensure_inpainter()
                inp_dev_req = resolve_device(
                    self.config.device_preference,
                    min_free_vram_mb=self.config.min_free_vram_mb,
                )
                cleaned_slices: list[np.ndarray] = []
                dev_inp = "cpu"
                for reg, sl_mask in zip(regions, slice_local_masks):
                    sl_bgr = image_bgr[reg.y1 : reg.y2, :]
                    # Select unified blocks whose center falls inside this slice's window
                    sl_blks_local: list[TextBlock] = []
                    sl_blks_refs: list[TextBlock] = []
                    for blk in blocks:
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
                        sl_clean, dev_inp, _ = inpainter.inpaint_page(
                            sl_bgr, sl_mask, sl_blks_local, device=inp_dev_req
                        )
                        # Copy inpaint metadata back onto unified blocks owned by this slice
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

                inpainted_bgr = stitch_inpainted_slices(cleaned_slices, regions, img_h, img_w)
                timings["inpainting"] = round((time.perf_counter() - t_inp0) * 1000.0, 2)
                devices["inpainting"] = dev_inp

                if effective_use_cache:
                    page_cache.save_detection_and_ocr(
                        blocks,
                        text_mask,
                        detector_name=getattr(detector, "name", "comic-text-detector-onnx"),
                        ocr_name=f"{ocr.name} ({source_lang}->{detected_lang})",
                    )
                    page_cache.save_inpainted(inpainted_bgr, blocks)

                return (
                    False,
                    False,
                    blocks,
                    inpainted_bgr,
                    detected_lang,
                    eff_dir,
                    slices_count,
                    devices,
                    timings,
                )

            # Standard single-pass page (or manual screenshot crop with `direct_ocr=True`)
            t0 = time.perf_counter()
            detector, dev_det = self._ensure_detector_loaded()
            blocks, text_mask = detector.detect(image_bgr)
            timings["deteccao"] = round((time.perf_counter() - t0) * 1000.0, 2)
            devices["deteccao"] = dev_det

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

            if not blocks:
                if effective_use_cache:
                    self._save_negative_cache(cache_key, "zero_blocks_detected")
                devices["ocr"] = "skipped"
                devices["inpainting"] = "skipped"
                eff_dir = resolve_reading_direction(
                    reading_direction, detected_lang=detected_lang, img_w=img_w, img_h=img_h
                )
                return True, False, [], None, detected_lang, eff_dir, 1, devices, timings

            t1 = time.perf_counter()
            ocr, dev_ocr = self._ensure_ocr_loaded(source_lang)
            blocks = ocr.recognize_blocks(image_bgr, blocks)
            detected_lang = getattr(ocr, "detected_primary_language", detected_lang)

            # If manual screenshot crop still returned empty on sub-blocks, force full-crop direct OCR!
            if direct_ocr and not any(b.source_text.strip() for b in blocks):
                pad = min(4, max(0, min(img_w, img_h) // 20))
                full_blk = TextBlock(
                    id=1,
                    bbox=BoundingBox(x1=pad, y1=pad, x2=img_w - pad, y2=img_h - pad),
                    is_vertical=(img_h > int(img_w * 1.4)),
                    confidence=1.0,
                )
                blocks = ocr.recognize_blocks(image_bgr, [full_blk])
                detected_lang = getattr(ocr, "detected_primary_language", detected_lang)

            timings["ocr"] = round((time.perf_counter() - t1) * 1000.0, 2)
            devices["ocr"] = dev_ocr

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

            if not any(b.source_text.strip() for b in blocks):
                if effective_use_cache:
                    self._save_negative_cache(cache_key, "ocr_empty")
                devices["inpainting"] = "skipped"
                return True, False, blocks, None, detected_lang, eff_dir, 1, devices, timings

            if effective_use_cache:
                page_cache.save_detection_and_ocr(
                    blocks,
                    text_mask,
                    detector_name=getattr(detector, "name", "comic-text-detector-onnx"),
                    ocr_name=f"{ocr.name} ({source_lang}->{detected_lang})",
                )

            t2 = time.perf_counter()
            inpainter = self._ensure_inpainter()
            inp_dev_req = resolve_device(
                self.config.device_preference,
                min_free_vram_mb=self.config.min_free_vram_mb,
            )
            inpainted_bgr, dev_inp, _ = inpainter.inpaint_page(
                image_bgr,
                text_mask,
                blocks,
                device=inp_dev_req,
            )
            timings["inpainting"] = round((time.perf_counter() - t2) * 1000.0, 2)
            devices["inpainting"] = dev_inp
            if effective_use_cache:
                page_cache.save_inpainted(inpainted_bgr, blocks)

            return (
                False,
                False,
                blocks,
                inpainted_bgr,
                detected_lang,
                eff_dir,
                1,
                devices,
                timings,
            )

    def prepare_image_bytes(
        self,
        raw_bytes: bytes,
        *,
        source_lang: Optional[str] = None,
        reading_direction: str = "auto",
        use_cache: bool = True,
    ) -> PagePrepareOutput:
        self.touch_activity()
        lang = normalize_source_lang(source_lang or self.default_source_lang)
        rdir = normalize_reading_direction(reading_direction)
        image_bgr = decode_image_bytes_bgr(raw_bytes)
        image_sha = hashlib.sha256(raw_bytes).hexdigest()

        t_start = time.perf_counter()
        no_text, cache_hit, blocks, _inp, det_lang, _eff_dir, _slices, _devs, timings = (
            self._run_local_vision_stages(
                image_bgr,
                image_sha,
                lang,
                reading_direction=rdir,
                use_cache=use_cache,
                force_retranslate=False,
            )
        )
        timings["total"] = round((time.perf_counter() - t_start) * 1000.0, 2)
        self.touch_activity()
        return PagePrepareOutput(
            image_sha256=image_sha,
            cache_hit_local=cache_hit,
            no_text=no_text,
            blocks_count=len(blocks),
            source_lang=lang,
            detected_lang=det_lang,
            timings_ms=timings,
        )

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
        self.touch_activity()
        chosen_model = validate_free_tier_model(model or self.config.gemini_model)
        chosen_lang = normalize_source_lang(source_lang or self.default_source_lang)
        chosen_dir = normalize_reading_direction(reading_direction)
        chosen_style = (style or "scanlation").strip().lower()
        if chosen_style not in {"scanlation", "informal", "fiel", "formal"}:
            chosen_style = "scanlation"

        image_bgr = decode_image_bytes_bgr(raw_bytes)
        img_h, img_w = image_bgr.shape[:2]
        image_sha = hashlib.sha256(raw_bytes).hexdigest()
        t_total_start = time.perf_counter()

        (
            no_text,
            cache_hit_local,
            blocks,
            inpainted_bgr,
            detected_lang,
            effective_dir,
            slices_count,
            devices,
            local_timings,
        ) = self._run_local_vision_stages(
            image_bgr,
            image_sha,
            chosen_lang,
            reading_direction=chosen_dir,
            direct_ocr=direct_ocr,
            use_cache=use_cache,
            force_retranslate=force_retranslate,
        )

        timings: dict[str, float] = {
            "deteccao": local_timings["deteccao"],
            "ocr": local_timings["ocr"],
            "inpainting": local_timings["inpainting"],
            "traducao": 0.0,
            "renderizacao": 0.0,
            "total": 0.0,
        }

        if no_text or inpainted_bgr is None:
            encoded_bytes, mime_type = encode_bgr_image(image_bgr, output_format=output_format)
            timings["total"] = round((time.perf_counter() - t_total_start) * 1000.0, 2)
            self._pages_processed += 1
            self.touch_activity()
            return PageTranslationOutput(
                image_bytes=encoded_bytes,
                mime_type=mime_type,
                image_sha256=image_sha,
                width=img_w,
                height=img_h,
                no_text=True,
                blocks_count=len(blocks),
                translated_blocks_count=0,
                cache_hit_local=cache_hit_local,
                model=chosen_model,
                source_lang=chosen_lang,
                detected_lang=detected_lang,
                style=chosen_style,
                uppercase=uppercase,
                device_detector=devices["deteccao"],
                device_ocr=devices["ocr"],
                device_inpaint=devices["inpainting"],
                timings_ms=timings,
                quota=self.quota_tracker.snapshot(chosen_model),
                blocks=blocks,
                reading_direction=effective_dir,
                slices_count=slices_count,
            )

        if force_retranslate:
            for b in blocks:
                b.translated_text = ""
                b.tone = ""

        t3 = time.perf_counter()
        with self._api_lock:
            self.touch_activity()
            translator = self._make_translator(chosen_model, api_key_override)
            blocks, quota_snap = translator.translate_page(
                blocks,
                source_lang=detected_lang,
                target_lang="pt-BR",
                glossary=glossary,
                style=chosen_style,
            )
        timings["traducao"] = round((time.perf_counter() - t3) * 1000.0, 2)

        t4 = time.perf_counter()
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

        return PageTranslationOutput(
            image_bytes=encoded_bytes,
            mime_type=mime_type,
            image_sha256=image_sha,
            width=img_w,
            height=img_h,
            no_text=False,
            blocks_count=len(blocks),
            translated_blocks_count=sum(1 for b in blocks if b.translated_text.strip()),
            cache_hit_local=cache_hit_local,
            model=chosen_model,
            source_lang=chosen_lang,
            detected_lang=detected_lang,
            style=chosen_style,
            uppercase=uppercase,
            device_detector=devices["deteccao"],
            device_ocr=devices["ocr"],
            device_inpaint=devices["inpainting"],
            timings_ms=timings,
            quota=quota_snap,
            blocks=blocks,
            reading_direction=effective_dir,
            slices_count=slices_count,
        )

    def translate_batch_bytes(
        self,
        items: list[tuple[str, bytes, bool]],  # [(page_id, raw_bytes, force_retranslate), ...]
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
        """Translate up to 4 pages in ONE grouped Gemini Free Tier call + fast WebP rendering."""
        if not items or len(items) > MAX_BATCH_PAGES:
            raise ValueError(f"Lote deve conter entre 1 e {MAX_BATCH_PAGES} paginas.")

        self.touch_activity()
        chosen_model = validate_free_tier_model(model or self.config.gemini_model)
        chosen_lang = normalize_source_lang(source_lang or self.default_source_lang)
        chosen_dir = normalize_reading_direction(reading_direction)
        chosen_style = (style or "scanlation").strip().lower()
        if chosen_style not in {"scanlation", "informal", "fiel", "formal"}:
            chosen_style = "scanlation"

        t_batch_start = time.perf_counter()
        t_local_start = time.perf_counter()

        # Phase 1: Local Vision (Detection + OCR + Inpainting) for every page in batch
        prepared: list[dict[str, Any]] = []
        for idx, (page_id, raw_bytes, force_retranslate) in enumerate(items):
            pid = page_id or f"p{idx}"
            image_bgr = decode_image_bytes_bgr(raw_bytes)
            img_h, img_w = image_bgr.shape[:2]
            image_sha = hashlib.sha256(raw_bytes).hexdigest()

            (
                no_text,
                cache_hit,
                blocks,
                inpainted_bgr,
                det_lang,
                eff_dir,
                slices_count,
                devices,
                local_t,
            ) = self._run_local_vision_stages(
                image_bgr,
                image_sha,
                chosen_lang,
                reading_direction=chosen_dir,
                use_cache=use_cache,
                force_retranslate=force_retranslate,
            )
            if force_retranslate:
                for b in blocks:
                    b.translated_text = ""
                    b.tone = ""

            prepared.append(
                {
                    "page_id": pid,
                    "image_bgr": image_bgr,
                    "width": img_w,
                    "height": img_h,
                    "sha256": image_sha,
                    "no_text": no_text or inpainted_bgr is None,
                    "cache_hit": cache_hit,
                    "blocks": blocks,
                    "inpainted_bgr": inpainted_bgr,
                    "detected_lang": det_lang,
                    "reading_direction": eff_dir,
                    "slices_count": slices_count,
                    "devices": devices,
                    "local_timings": local_t,
                }
            )
        local_total_ms = round((time.perf_counter() - t_local_start) * 1000.0, 2)

        # Phase 2: ONE grouped Gemini API call for ALL text-bearing pages in the batch!
        text_indices = [i for i, p in enumerate(prepared) if not p["no_text"]]
        quota_snap: Optional[QuotaSnapshot] = self.quota_tracker.snapshot(chosen_model)
        fallback_indices_global: list[int] = []

        t_api_start = time.perf_counter()
        if text_indices:
            with self._api_lock:
                self.touch_activity()
                translator = self._make_translator(chosen_model, api_key_override)
                batch_blocks = [prepared[i]["blocks"] for i in text_indices]
                batch_langs = [prepared[i]["detected_lang"] for i in text_indices]

                if hasattr(translator, "translate_pages_batch"):
                    _, quota_snap, rel_fallbacks = translator.translate_pages_batch(
                        batch_blocks,
                        source_langs=batch_langs,
                        target_lang="pt-BR",
                        glossary=glossary,
                        style=chosen_style,
                    )
                    fallback_indices_global = [text_indices[r] for r in rel_fallbacks]
                else:
                    # Compatibility fallback for custom stub TranslatorProviders in unit tests
                    for i in text_indices:
                        _, quota_snap = translator.translate_page(
                            prepared[i]["blocks"],
                            source_lang=prepared[i]["detected_lang"],
                            target_lang="pt-BR",
                            glossary=glossary,
                            style=chosen_style,
                        )
        api_total_ms = round((time.perf_counter() - t_api_start) * 1000.0, 2)

        # Phase 3: Fast direct Pillow WebP rendering for all pages
        t_render_start = time.perf_counter()
        renderer = self._get_renderer(uppercase=uppercase)
        outputs: list[PageTranslationOutput] = []

        for idx, p in enumerate(prepared):
            t_r0 = time.perf_counter()
            if p["no_text"]:
                img_bytes, mime = encode_bgr_image(p["image_bgr"], output_format=output_format)
            else:
                img_bytes, mime = renderer.render_page_to_bytes(
                    p["inpainted_bgr"],
                    p["blocks"],
                    output_format=output_format,
                    webp_quality=89,
                    webp_method=1,
                )
            render_ms = round((time.perf_counter() - t_r0) * 1000.0, 2)
            local_t = p["local_timings"]
            outputs.append(
                PageTranslationOutput(
                    page_id=p["page_id"],
                    image_bytes=img_bytes,
                    mime_type=mime,
                    image_sha256=p["sha256"],
                    width=p["width"],
                    height=p["height"],
                    no_text=p["no_text"],
                    blocks_count=len(p["blocks"]),
                    translated_blocks_count=0
                    if p["no_text"]
                    else sum(1 for b in p["blocks"] if b.translated_text.strip()),
                    cache_hit_local=p["cache_hit"],
                    model=chosen_model,
                    source_lang=chosen_lang,
                    detected_lang=p["detected_lang"],
                    style=chosen_style,
                    uppercase=uppercase,
                    device_detector=p["devices"]["deteccao"],
                    device_ocr=p["devices"]["ocr"],
                    device_inpaint=p["devices"]["inpainting"],
                    timings_ms={
                        "deteccao": local_t["deteccao"],
                        "ocr": local_t["ocr"],
                        "inpainting": local_t["inpainting"],
                        "renderizacao": render_ms,
                    },
                    quota=quota_snap,
                    blocks=p["blocks"],
                    used_fallback=(idx in fallback_indices_global),
                    reading_direction=p["reading_direction"],
                    slices_count=p["slices_count"],
                )
            )
            self._pages_processed += 1

        render_total_ms = round((time.perf_counter() - t_render_start) * 1000.0, 2)
        total_ms = round((time.perf_counter() - t_batch_start) * 1000.0, 2)
        self.touch_activity()

        return BatchTranslationOutput(
            pages=outputs,
            model=chosen_model,
            source_lang=chosen_lang,
            style=chosen_style,
            uppercase=uppercase,
            prompt_version=PROMPT_VERSION,
            timings_ms={
                "local_vision": local_total_ms,
                "traducao_lote": api_total_ms,
                "renderizacao_lote": render_total_ms,
                "total": total_ms,
            },
            avg_ms_per_page=round(total_ms / max(1, len(outputs)), 2),
            fallback_pages=fallback_indices_global,
            quota=quota_snap,
        )

    def clear_disk_cache(self) -> int:
        removed = 0
        with self._gpu_lock:
            for item in self.cache_dir.iterdir():
                if item.is_dir() and item.name != "cuda_jit" and len(item.name) >= 64:
                    shutil.rmtree(item, ignore_errors=True)
                    removed += 1
        return removed

    def reset_circuit_breaker(self) -> dict[str, Any]:
        self.touch_activity()
        self.circuit_breaker.reset()
        return self.get_circuit_breaker_status()

    def get_circuit_breaker_status(self) -> dict[str, Any]:
        state = self.circuit_breaker.state
        retry_after = 0.0
        if state == CircuitState.OPEN and self.circuit_breaker._opened_at is not None:
            elapsed = self.circuit_breaker._clock() - self.circuit_breaker._opened_at
            retry_after = max(0.0, self.circuit_breaker.config.recovery_timeout - elapsed)
        return {
            "state": state.value,
            "consecutive_failures": self.circuit_breaker.consecutive_failures,
            "retry_after_sec": round(retry_after, 1),
        }

    def get_health_snapshot(self, *, port: int) -> dict[str, Any]:
        import os

        try:
            gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU-only"
        except Exception:
            gpu_name = "CPU-only"

        alloc_mb, peak_mb, res_mb = get_torch_vram_stats_mb()
        nv = get_nvidia_memory_info()
        models_loaded: list[str] = []
        if self._detector is not None:
            models_loaded.append(f"detector:{self._detector_device}")
        for lang, dev in self._ocr_devices.items():
            if lang in self._ocr_providers:
                models_loaded.append(f"ocr[{lang}]:{dev}")
        if self._inpainter is not None and getattr(self._inpainter, "_lama_model", None) is not None:
            models_loaded.append("lama:cuda")

        quota = self.quota_tracker.snapshot(self.config.gemini_model).model_dump()
        return {
            "status": "ready",
            "warming_up": self._warming_up,
            "warmup_done": self._warmup_done,
            "prompt_version": PROMPT_VERSION,
            "pid": os.getpid(),
            "port": port,
            "uptime_sec": round(time.monotonic() - self.started_at, 1),
            "pages_processed": self._pages_processed,
            "idle_timeout_sec": self._idle_timeout_sec,
            "idle_remaining_sec": round(self.idle_remaining_sec(), 1),
            "models_loaded": models_loaded,
            "gpu_name": gpu_name,
            "ram_mb": get_process_ram_mb(),
            "torch_vram_allocated_mb": alloc_mb,
            "torch_vram_peak_mb": peak_mb,
            "torch_vram_reserved_mb": res_mb,
            "nvidia_vram_used_mb": nv.used_mb if nv else None,
            "nvidia_vram_free_mb": nv.free_mb if nv else None,
            "nvidia_vram_total_mb": nv.total_mb if nv else None,
            "circuit_breaker": self.get_circuit_breaker_status(),
            "free_tier_models": sorted(FREE_TIER_MODELS),
            "default_model": self.config.gemini_model or DEFAULT_FREE_MODEL,
            "supported_source_languages": list(SUPPORTED_SOURCE_LANGUAGES),
            "max_batch_pages": MAX_BATCH_PAGES,
            "has_env_api_key": bool(self.config.gemini_api_key),
            "quota": quota,
        }

    def unload_all(self) -> None:
        """Cancel any in-flight warmup, unload every local model, and free 100% VRAM."""
        self._shutdown_requested.set()
        with self._gpu_lock, self._api_lock:
            if self._detector is not None:
                try:
                    self._detector.unload()
                except Exception as exc:
                    logger.debug("Erro ao descarregar detector: %s", exc)
                self._detector = None
                self._detector_device = "unloaded"
            unloaded_ids: set[int] = set()
            for ocr in self._ocr_providers.values():
                if id(ocr) in unloaded_ids:
                    continue
                unloaded_ids.add(id(ocr))
                try:
                    ocr.unload()
                except Exception as exc:
                    logger.debug("Erro ao descarregar OCR: %s", exc)
            self._ocr_providers.clear()
            self._ocr_devices.clear()
            if self._inpainter is not None:
                try:
                    self._inpainter.unload()
                except Exception as exc:
                    logger.debug("Erro ao descarregar inpainter: %s", exc)
                self._inpainter = None
            self._renderers.clear()
            self._warming_up = False
            self._warmup_done = False
        release_gpu_memory()


class IdleWatchdog:
    """Monitors inactivity and cleanly unloads VRAM + terminates the server when idle."""

    def __init__(
        self,
        engine: WarmScanlationEngine,
        on_timeout: Callable[[], None],
        *,
        poll_interval_sec: float = 2.0,
    ) -> None:
        self._engine = engine
        self._on_timeout = on_timeout
        self._poll_interval = poll_interval_sec
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
        while not self._stop_event.wait(self._poll_interval):
            if self._engine.idle_remaining_sec() <= 0.0:
                logger.info(
                    "Timeout de inatividade atingido (%ds). Descarregando VRAM e encerrando motor...",
                    self._engine.idle_timeout_sec,
                )
                self._engine.unload_all()
                self._on_timeout()
                return
