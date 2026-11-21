"""End-to-end Stage 1 Scanlation Pipeline orchestrator (prepared for Stage 2 FastAPI).

Lifecycle guarantees:
- Models are loaded on-demand one stage at a time and immediately unloaded (`del`,
  `gc.collect()`, `torch.cuda.empty_cache()`) before the next stage loads.
- SHA-256 content-addressed cache stores detection, masks, OCR, and inpainting so
  translation retries NEVER re-run local GPU detection, OCR, or inpainting.
- `unload_all()` guarantees 0 MB active model VRAM at exit (or upon Stage 2 idle shutdown).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from poc.cache import PageCache, compute_image_sha256
from poc.config import OUTPUT_DIR, PoCConfig, ensure_runtime_dirs
from poc.detector import ComicTextDetector
from poc.inpainter import HybridInpainter
from poc.ocr import OCRProvider, create_ocr_provider
from poc.renderer import (
    MangaTextRenderer,
    create_detection_debug_image,
    create_side_by_side_comparison,
)
from poc.schemas import BenchmarkReport, QuotaSnapshot, StageMetrics, TextBlock
from poc.translator import GeminiFreeTierTranslator, TranslatorProvider
from poc.vram import (
    StageProfiler,
    get_nvidia_memory_info,
    get_process_peak_ram_mb,
    get_torch_vram_stats_mb,
    release_gpu_memory,
    resolve_device,
)

logger = logging.getLogger(__name__)


def read_image_bgr(path: Path) -> np.ndarray:
    """Read an image reliably on Windows even if path contains spaces or accents."""
    raw = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Nao foi possivel decodificar a imagem: {path}")
    return img


def write_image_png(path: Path, img_bgr: np.ndarray) -> None:
    """Write a PNG image reliably on Windows paths with spaces/Unicode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", img_bgr)
    if not ok:
        raise RuntimeError(f"Falha ao codificar PNG: {path}")
    path.write_bytes(encoded.tobytes())


class ScanlationPipeline:
    """Decoupled scanlation orchestrator ready for CLI (Stage 1) and FastAPI (Stage 2)."""

    def __init__(
        self,
        config: PoCConfig,
        *,
        source_lang: str = "ja",
        translator: Optional[TranslatorProvider] = None,
    ) -> None:
        self.config = config
        self.source_lang = source_lang
        self._detector: Optional[ComicTextDetector] = None
        self._ocr: Optional[OCRProvider] = None
        self._inpainter: Optional[HybridInpainter] = None
        self._translator: Optional[TranslatorProvider] = translator
        self._renderer: Optional[MangaTextRenderer] = None

    def _get_translator(self) -> TranslatorProvider:
        if self._translator is None:
            self._translator = GeminiFreeTierTranslator(
                api_key=self.config.gemini_api_key,
                model=self.config.gemini_model,
                timeout_seconds=self.config.gemini_timeout_seconds,
            )
        return self._translator

    def unload_all(self) -> None:
        """Unload every local model and release 100% GPU VRAM & Python garbage."""
        if self._detector is not None:
            self._detector.unload()
            self._detector = None
        if self._ocr is not None:
            self._ocr.unload()
            self._ocr = None
        if self._inpainter is not None:
            self._inpainter.unload()
            self._inpainter = None
        self._renderer = None
        release_gpu_memory()

    def process_image(
        self,
        image_path: Path,
        *,
        output_dir: Path = OUTPUT_DIR,
        use_cache: bool = True,
        skip_translation: bool = False,
        glossary: Optional[dict[str, str]] = None,
        uppercase: bool = True,
    ) -> BenchmarkReport:
        """Run full (or local-only) pipeline on an image, exporting outputs & benchmark."""
        ensure_runtime_dirs()
        output_dir.mkdir(parents=True, exist_ok=True)

        import torch

        gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU-only"
        cuda_ver = str(torch.version.cuda or "none")
        torch_ver = str(torch.__version__)

        total_start = time.perf_counter()
        nv_start_info = get_nvidia_memory_info()
        nv_start_mb = nv_start_info.used_mb if nv_start_info else None
        nv_peak_mb = nv_start_mb

        image_bgr = read_image_bgr(image_path)
        img_h, img_w = image_bgr.shape[:2]
        image_sha = compute_image_sha256(image_path)
        cache = PageCache(image_sha)

        stages: list[StageMetrics] = []
        blocks: list[TextBlock] = []
        text_mask: Optional[np.ndarray] = None
        inpainted_bgr: Optional[np.ndarray] = None
        quota_snapshot: Optional[QuotaSnapshot] = None
        translation_executed = False

        try:
            # ------------------------------------------------------------------
            # STAGE 1 & 2: DETECTION + OCR (cached together by image SHA-256)
            # ------------------------------------------------------------------
            cached_det_ocr = cache.load_detection_and_ocr() if use_cache else None
            if cached_det_ocr is not None:
                blocks, text_mask = cached_det_ocr
                for stage_name in ("deteccao", "ocr"):
                    with StageProfiler(stage_name) as prof:
                        prof.cache_hit = True
                        prof.device_used = "cache"
                        prof.details = {"blocks": len(blocks)}
                    assert prof.metrics is not None
                    stages.append(prof.metrics)
            else:
                # Stage 1: Comic Text Detector (load -> detect -> unload!)
                with StageProfiler("deteccao") as prof:
                    det_dev_req = resolve_device(
                        self.config.device_preference,
                        min_free_vram_mb=self.config.min_free_vram_mb,
                    )
                    self._detector = ComicTextDetector()
                    used_dev = self._detector.load(det_dev_req)
                    prof.device_used = used_dev
                    blocks, text_mask = self._detector.detect(image_bgr)
                    prof.details = {
                        "detector": self._detector.name,
                        "blocks_detected": len(blocks),
                    }
                    prof.sample_peak()
                    self._detector.unload()
                    self._detector = None
                assert prof.metrics is not None
                stages.append(prof.metrics)

                # Stage 2: OCR (`manga-ocr`: load -> recognize -> unload!)
                with StageProfiler("ocr") as prof:
                    ocr_dev_req = resolve_device(
                        self.config.device_preference,
                        min_free_vram_mb=self.config.min_free_vram_mb,
                    )
                    self._ocr = create_ocr_provider(self.source_lang)
                    used_dev = self._ocr.load(ocr_dev_req)
                    prof.device_used = used_dev
                    blocks = self._ocr.recognize_blocks(image_bgr, blocks)
                    non_empty = sum(1 for b in blocks if b.source_text.strip())
                    prof.details = {
                        "provider": self._ocr.name,
                        "blocks_recognized": non_empty,
                    }
                    prof.sample_peak()
                    self._ocr.unload()
                    self._ocr = None
                assert prof.metrics is not None
                stages.append(prof.metrics)

                assert text_mask is not None
                cache.save_detection_and_ocr(
                    blocks,
                    text_mask,
                    detector_name=ComicTextDetector.name,
                    ocr_name=f"manga-ocr ({self.source_lang})",
                )

            assert text_mask is not None

            # ------------------------------------------------------------------
            # STAGE 3: MASKS & HYBRID INPAINTING (OpenCV solid + LaMa crops)
            # Cached by image SHA-256 BEFORE translation so API retries skip GPU!
            # ------------------------------------------------------------------
            cached_inpainted = cache.load_inpainted(blocks) if use_cache else None
            if cached_inpainted is not None:
                inpainted_bgr = cached_inpainted
                with StageProfiler("inpainting") as prof:
                    prof.cache_hit = True
                    prof.device_used = "cache"
                    prof.details = {
                        "opencv_solid": sum(1 for b in blocks if b.inpaint_method == "opencv_solid"),
                        "lama_crop": sum(1 for b in blocks if b.inpaint_method == "lama_crop"),
                    }
                assert prof.metrics is not None
                stages.append(prof.metrics)
            else:
                with StageProfiler("inpainting") as prof:
                    inp_dev_req = resolve_device(
                        self.config.device_preference,
                        min_free_vram_mb=self.config.min_free_vram_mb,
                    )
                    self._inpainter = HybridInpainter(
                        crop_margin=self.config.inpaint_crop_margin,
                        max_crop_side=self.config.inpaint_max_crop_side,
                    )
                    inpainted_bgr, used_dev, stats = self._inpainter.inpaint_page(
                        image_bgr,
                        text_mask,
                        blocks,
                        device=inp_dev_req,
                    )
                    prof.device_used = used_dev
                    prof.details = dict(stats)
                    prof.sample_peak()
                    self._inpainter.unload()
                    self._inpainter = None
                assert prof.metrics is not None
                stages.append(prof.metrics)
                cache.save_inpainted(inpainted_bgr, blocks)

            # Export detection + reading order debug image immediately
            debug_img = create_detection_debug_image(image_bgr, blocks, text_mask)
            write_image_png(output_dir / "debug_deteccao.png", debug_img)

            # ------------------------------------------------------------------
            # STAGE 4: GROUPED JSON TRANSLATION (Gemini Free Tier + Circuit Breaker)
            # ------------------------------------------------------------------
            with StageProfiler("traducao") as prof:
                prof.device_used = "api:gemini-free"
                if skip_translation:
                    prof.details = {"skipped": True, "reason": "local_only_mode"}
                else:
                    translator = self._get_translator()
                    blocks, quota_snapshot = translator.translate_page(
                        blocks,
                        source_lang=self.source_lang,
                        target_lang="pt-BR",
                        glossary=glossary,
                    )
                    translation_executed = True
                    prof.details = {
                        "model": self.config.gemini_model,
                        "translated_blocks": sum(1 for b in blocks if b.translated_text.strip()),
                        "prompt_tokens": quota_snapshot.last_request_prompt_tokens if quota_snapshot else 0,
                        "output_tokens": quota_snapshot.last_request_output_tokens if quota_snapshot else 0,
                    }
            assert prof.metrics is not None
            stages.append(prof.metrics)

            # ------------------------------------------------------------------
            # STAGE 5: RENDERING + EXPORTS (`traduzida.png`, `comparacao.png`)
            # ------------------------------------------------------------------
            with StageProfiler("renderizacao") as prof:
                prof.device_used = "cpu"
                if translation_executed:
                    self._renderer = MangaTextRenderer(uppercase=uppercase)
                    translated_bgr = self._renderer.render_page(inpainted_bgr, blocks)
                    write_image_png(output_dir / "traduzida.png", translated_bgr)
                    comp_bgr = create_side_by_side_comparison(image_bgr, translated_bgr)
                    write_image_png(output_dir / "comparacao.png", comp_bgr)
                    prof.details = {
                        "font": self._renderer.font_path.name,
                        "uppercase": uppercase,
                        "contour_fitting": True,
                    }
                else:
                    # In local-only mode, save the cleaned page + side-by-side preview
                    write_image_png(output_dir / "inpainted_preview.png", inpainted_bgr)
                    comp_preview = create_side_by_side_comparison(
                        image_bgr,
                        inpainted_bgr,
                        left_label="ORIGINAL (JA)",
                        right_label="LIMPA / INPAINTED (Preview Local — Sem Gemini)",
                    )
                    write_image_png(output_dir / "comparacao_local_inpaint.png", comp_preview)
                    prof.details = {"local_only_preview": True}
            assert prof.metrics is not None
            stages.append(prof.metrics)

        finally:
            self.unload_all()

        total_duration = round(time.perf_counter() - total_start, 4)
        nv_end_info = get_nvidia_memory_info()
        nv_end_mb = nv_end_info.used_mb if nv_end_info else None

        for st in stages:
            if st.nvidia_used_peak_mb is not None:
                nv_peak_mb = max(nv_peak_mb or 0.0, st.nvidia_used_peak_mb)

        overall_torch_peak = max((s.vram_allocated_peak_mb for s in stages), default=0.0)
        alloc_final, _, _ = get_torch_vram_stats_mb()
        overall_ram_peak = max(
            get_process_peak_ram_mb(),
            max((s.ram_peak_mb for s in stages), default=0.0),
        )

        report = BenchmarkReport(
            image_path=str(image_path),
            image_sha256=image_sha,
            image_resolution=(img_w, img_h),
            gpu_name=gpu_name,
            cuda_version=cuda_ver,
            torch_version=torch_ver,
            total_blocks_detected=len(blocks),
            blocks_with_text=sum(1 for b in blocks if b.source_text.strip()),
            opencv_inpainted_blocks=sum(1 for b in blocks if b.inpaint_method == "opencv_solid"),
            lama_inpainted_blocks=sum(1 for b in blocks if b.inpaint_method == "lama_crop"),
            translation_executed=translation_executed,
            gemini_model=self.config.gemini_model,
            total_duration_sec=total_duration,
            overall_ram_peak_mb=round(overall_ram_peak, 2),
            overall_torch_vram_peak_mb=round(overall_torch_peak, 2),
            nvidia_vram_start_mb=nv_start_mb,
            nvidia_vram_peak_mb=nv_peak_mb,
            nvidia_vram_end_mb=nv_end_mb,
            vram_released_cleanly=(alloc_final <= 16.0),
            quota=quota_snapshot,
            stages=stages,
            blocks=blocks,
        )

        (output_dir / "benchmark.json").write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return report
