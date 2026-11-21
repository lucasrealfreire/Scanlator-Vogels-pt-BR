"""Pluggable multi-language OCR providers:
- `MangaOCRProvider`: Batched FP16 GPU inference (`kha-white/manga-ocr-base`) for Japanese.
- `RapidOCRProvider`: Fast local ONNX (`PP-OCRv6`) with preserved spaces for English/Latin,
  Korean, and Chinese (0 MB VRAM on CPU + wordninja safety net for tightly tracked comics).
- `AutoMultiLangOCRProvider`: Automatic JP/EN per-page & per-block router using DBNet line
  orientation + lightweight RapidOCR script probing (never loads `manga-ocr` onto VRAM on
  pure English pages!).
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image

from poc.config import MODELS_DIR
from poc.schemas import TextBlock
from poc.vram import release_gpu_memory, run_with_oom_fallback

logger = logging.getLogger(__name__)

_CJK_KANA_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fff\uac00-\ud7af]")
_LATIN_RE = re.compile(r"[A-Za-z]")


class OCRProvider(ABC):
    """Base contract for language-specific OCR engines."""

    name: str = "base"
    supported_languages: frozenset[str] = frozenset()
    detected_primary_language: str = "ja"

    @abstractmethod
    def load(self, device: str = "cuda") -> str:
        """Load model weights onto the requested device; return actual device used."""

    @abstractmethod
    def recognize_crop(self, crop_pil: Image.Image) -> str:
        """Recognize text from a single RGB PIL crop."""

    @abstractmethod
    def unload(self) -> None:
        """Explicitly delete model references and free all GPU/RAM memory."""

    def recognize_blocks(
        self,
        image_bgr: np.ndarray,
        blocks: list[TextBlock],
        *,
        crop_pad_px: int = 6,
    ) -> list[TextBlock]:
        """Crop each detected TextBlock from the page and populate `source_text`."""
        img_h, img_w = image_bgr.shape[:2]
        for block in blocks:
            padded = block.bbox.padded(crop_pad_px, img_w, img_h)
            if padded.width < 4 or padded.height < 4:
                block.source_text = ""
                continue
            crop_bgr = image_bgr[padded.y1 : padded.y2, padded.x1 : padded.x2]
            crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
            crop_pil = Image.fromarray(crop_rgb)
            raw_text = self.recognize_crop(crop_pil)
            block.source_text = _clean_cjk_ocr_text(raw_text)
        return blocks


def _clean_cjk_ocr_text(text: str) -> str:
    """Normalize Japanese/CJK OCR spacing without stripping spaces between Latin words."""
    cleaned = text.strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(
        r"(?<=[\u3000-\u9fff\uff00-\uffef])\s+(?=[\u3000-\u9fff\uff00-\uffef])",
        "",
        cleaned,
    )
    return cleaned.strip()


def _clean_latin_ocr_text(text: str, *, allow_wordninja: bool = True) -> str:
    """Normalize English/Latin comic OCR text while strictly preserving spaces between words."""
    cleaned = text.strip()
    # Rejoin line-break hyphenations (e.g. "IMPOS- SIBLE" -> "IMPOSSIBLE")
    cleaned = re.sub(r"([A-Za-z])-\s+([A-Za-z])", r"\1\2", cleaned)
    # Ensure a space after punctuation when followed immediately by a letter (except apostrophes)
    cleaned = re.sub(r"([.,!?;:])([A-Za-z])", r"\1 \2", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not allow_wordninja or not cleaned:
        return cleaned

    # Safety net ONLY if a stylized comic font caused RapidOCR to glue >=14 ASCII letters
    tokens = cleaned.split(" ")
    repaired: list[str] = []
    wn = None
    for tok in tokens:
        alpha_only = re.sub(r"[^A-Za-z]", "", tok)
        if len(alpha_only) >= 14 and tok.isascii() and "-" not in tok and "'" not in tok:
            if wn is None:
                try:
                    import wordninja

                    wn = wordninja
                except Exception:
                    wn = False
            if wn:
                # Preserve leading/trailing punctuation around glued uppercase/mixed token
                m = re.match(r"^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$", tok)
                if m:
                    lead, core, trail = m.groups()
                    parts = wn.split(core.lower())
                    if len(parts) > 1:
                        joined = " ".join(parts)
                        if core.isupper():
                            joined = joined.upper()
                        elif core[0].isupper():
                            joined = joined[0].upper() + joined[1:]
                        repaired.append(f"{lead}{joined}{trail}")
                        continue
        repaired.append(tok)
    return " ".join(repaired)


def _extract_crop_pil(
    image_bgr: np.ndarray,
    block: TextBlock,
    pad_px: int = 6,
) -> Optional[Image.Image]:
    img_h, img_w = image_bgr.shape[:2]
    padded = block.bbox.padded(pad_px, img_w, img_h)
    if padded.width < 4 or padded.height < 4:
        return None
    crop_bgr = image_bgr[padded.y1 : padded.y2, padded.x1 : padded.x2]
    crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(crop_rgb)


class MangaOCRProvider(OCRProvider):
    """Japanese manga OCR with batched FP16 GPU inference (`kha-white/manga-ocr-base`)."""

    name: str = "manga-ocr-fp16"
    supported_languages: frozenset[str] = frozenset({"ja", "jp", "jpn"})
    detected_primary_language: str = "ja"

    def __init__(
        self,
        pretrained_model_name_or_path: Optional[str] = None,
        *,
        batch_size: int = 12,
    ) -> None:
        local_dir = MODELS_DIR / "manga-ocr-base"
        if pretrained_model_name_or_path is not None:
            self.model_id = pretrained_model_name_or_path
        elif (local_dir / "config.json").exists():
            self.model_id = str(local_dir)
        else:
            self.model_id = "kha-white/manga-ocr-base"
        self.batch_size = max(1, int(batch_size))
        self._mocr = None
        self._device: str = "cpu"

    def _load_on_device(self, device: str) -> str:
        from manga_ocr import MangaOcr

        force_cpu = device != "cuda"
        is_local = Path(self.model_id).is_dir()
        prev_offline = os.environ.get("HF_HUB_OFFLINE")
        if is_local:
            os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            logger.info("Carregando manga-ocr (%s) em device=%s...", self.model_id, device)
            self._mocr = MangaOcr(
                pretrained_model_name_or_path=self.model_id,
                force_cpu=force_cpu,
            )
        finally:
            if is_local:
                if prev_offline is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = prev_offline
        if hasattr(self._mocr, "model"):
            self._mocr.model.eval()
        self._device = "cpu" if force_cpu else "cuda"
        return self._device

    def load(self, device: str = "cuda") -> str:
        if self._mocr is not None:
            return self._device
        _, used = run_with_oom_fallback(
            cuda_fn=lambda: self._load_on_device("cuda"),
            cpu_fn=lambda: self._load_on_device("cpu"),
            stage_name="ocr:manga_load",
            device=device,
        )
        self._device = used
        return used

    def _post_process_raw(self, text: str) -> str:
        try:
            from manga_ocr.ocr import post_process

            return _clean_cjk_ocr_text(post_process(text))
        except Exception:
            return _clean_cjk_ocr_text(text)

    def _recognize_pil_batch(self, crops: list[Image.Image]) -> list[str]:
        """Run batched FP16 inference on RTX 5060 Ti with automatic CPU OOM fallback."""
        if not crops:
            return []
        if self._mocr is None:
            raise RuntimeError("MangaOCRProvider.load() deve ser chamado antes de inferir.")

        import torch

        # Ensure RGB grayscale-normalized inputs matching manga-ocr preprocessing
        prepared = [img.convert("L").convert("RGB") for img in crops]

        # Support both manga-ocr >=0.1.15 (`processor`) and older (`feature_extractor`)
        img_processor = getattr(self._mocr, "processor", None) or getattr(
            self._mocr, "feature_extractor", None
        )
        if img_processor is None:
            raise RuntimeError("MangaOcr nao expoe `processor` nem `feature_extractor`.")

        def _run_cuda_batch() -> list[str]:
            results: list[str] = []
            for start in range(0, len(prepared), self.batch_size):
                chunk = prepared[start : start + self.batch_size]
                pixel_values = img_processor(
                    chunk,
                    return_tensors="pt",
                ).pixel_values.to("cuda", non_blocking=True)
                with torch.inference_mode(), torch.amp.autocast("cuda", dtype=torch.float16):
                    token_ids = self._mocr.model.generate(pixel_values, max_length=300)
                decoded = self._mocr.tokenizer.batch_decode(
                    token_ids,
                    skip_special_tokens=True,
                )
                results.extend(self._post_process_raw(t) for t in decoded)
                del pixel_values, token_ids
            return results

        def _run_cpu_batch() -> list[str]:
            if self._device == "cuda":
                self.unload()
                self._load_on_device("cpu")
            cpu_proc = getattr(self._mocr, "processor", None) or getattr(
                self._mocr, "feature_extractor", None
            )
            results: list[str] = []
            for start in range(0, len(prepared), max(1, min(4, self.batch_size))):
                chunk = prepared[start : start + max(1, min(4, self.batch_size))]
                pixel_values = cpu_proc(
                    chunk,
                    return_tensors="pt",
                ).pixel_values
                with torch.inference_mode():
                    token_ids = self._mocr.model.generate(pixel_values, max_length=300)
                decoded = self._mocr.tokenizer.batch_decode(
                    token_ids,
                    skip_special_tokens=True,
                )
                results.extend(self._post_process_raw(t) for t in decoded)
                del pixel_values, token_ids
            return results

        texts, used = run_with_oom_fallback(
            cuda_fn=_run_cuda_batch,
            cpu_fn=_run_cpu_batch,
            stage_name="ocr:manga_batch_fp16",
            device=self._device,
        )
        self._device = used
        return texts

    def recognize_crop(self, crop_pil: Image.Image) -> str:
        out = self._recognize_pil_batch([crop_pil])
        return out[0] if out else ""

    def warmup(self) -> None:
        """Warm FP16 encoder/decoder CUDA kernels in <0.3s without running 300-token decoding."""
        if self._mocr is None:
            return
        import torch

        img_processor = getattr(self._mocr, "processor", None) or getattr(
            self._mocr, "feature_extractor", None
        )
        if img_processor is None:
            return
        dummy = Image.new("RGB", (64, 64), (255, 255, 255))
        pixel_values = img_processor([dummy], return_tensors="pt").pixel_values
        if self._device == "cuda" and torch.cuda.is_available():
            pixel_values = pixel_values.to("cuda", non_blocking=True)
            with torch.inference_mode(), torch.amp.autocast("cuda", dtype=torch.float16):
                _ = self._mocr.model.generate(pixel_values, max_length=4)
        else:
            with torch.inference_mode():
                _ = self._mocr.model.generate(pixel_values, max_length=4)
        del pixel_values

    def recognize_blocks(
        self,
        image_bgr: np.ndarray,
        blocks: list[TextBlock],
        *,
        crop_pad_px: int = 6,
    ) -> list[TextBlock]:
        valid_indices: list[int] = []
        crops: list[Image.Image] = []
        for idx, block in enumerate(blocks):
            pil = _extract_crop_pil(image_bgr, block, crop_pad_px)
            if pil is None:
                block.source_text = ""
            else:
                valid_indices.append(idx)
                crops.append(pil)

        if crops:
            texts = self._recognize_pil_batch(crops)
            for idx, txt in zip(valid_indices, texts):
                blocks[idx].source_text = txt
        return blocks

    def unload(self) -> None:
        if self._mocr is not None:
            for attr in ("model", "processor", "feature_extractor", "tokenizer"):
                if hasattr(self._mocr, attr):
                    try:
                        delattr(self._mocr, attr)
                    except Exception:
                        pass
            del self._mocr
            self._mocr = None
        release_gpu_memory()


class RapidOCRProvider(OCRProvider):
    """Fast local ONNX OCR (`RapidOCR` / `PP-OCRv6`) for English/Latin, Korean, and Chinese.

    Runs cleanly on CPU with 0 MB VRAM and preserves real word spaces on ALL-CAPS comic fonts.
    """

    name: str = "rapidocr-onnx"
    supported_languages: frozenset[str] = frozenset({"en", "ko", "zh", "latin"})

    def __init__(self, lang: str = "en") -> None:
        self.lang = lang.strip().lower()
        self.detected_primary_language = self.lang
        self._engine = None

    def load(self, device: str = "cpu") -> str:
        if self._engine is not None:
            return "cpu"
        from rapidocr import EngineType, LangRec, RapidOCR

        lang_map = {
            "en": LangRec.EN,
            "latin": LangRec.EN,
            "ko": getattr(LangRec, "KOREAN", LangRec.EN),
            "zh": getattr(LangRec, "CH", LangRec.EN),
        }
        rec_lang = lang_map.get(self.lang, LangRec.EN)

        params = {
            "Rec.lang_type": rec_lang,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Cls.engine_type": EngineType.ONNXRUNTIME,
            "Global.use_cls": False,
            "Global.text_score": 0.35,
        }
        try:
            logger.info("Carregando RapidOCR (lang=%s) em CPU (0 MB VRAM)...", self.lang)
            self._engine = RapidOCR(params=params)
        except Exception as exc:
            # Offline fallback: use bundled default multilingual PP-OCRv6 model
            logger.warning(
                "RapidOCR lang=%s indisponivel offline (%s); usando modelo local padrao.",
                self.lang,
                exc,
            )
            params.pop("Rec.lang_type", None)
            self._engine = RapidOCR(params=params)
        return "cpu"

    def recognize_crop(self, crop_pil: Image.Image) -> str:
        if self._engine is None:
            self.load("cpu")

        arr = np.array(crop_pil.convert("RGB"))
        h, w = arr.shape[:2]

        # Upscale tiny comic bubbles so thin uppercase strokes and spaces separate cleanly
        if min(h, w) < 110:
            scale = min(2.0, 140.0 / max(1.0, float(min(h, w))))
            arr = cv2.resize(
                arr,
                (max(16, int(round(w * scale))), max(16, int(round(h * scale)))),
                interpolation=cv2.INTER_CUBIC,
            )

        # Add generous white quiet zone so DBNet detects edge characters reliably
        padded = cv2.copyMakeBorder(
            arr,
            18,
            18,
            18,
            18,
            cv2.BORDER_CONSTANT,
            value=(255, 255, 255),
        )

        res = self._engine(padded)
        txts = getattr(res, "txts", None) or ()
        boxes = getattr(res, "boxes", None)
        if not txts:
            return ""

        items: list[tuple[float, float, float, str]] = []
        if boxes is not None and len(boxes) == len(txts):
            for box, txt in zip(boxes, txts):
                pts = np.asarray(box, dtype=np.float32)
                y_top = float(pts[:, 1].min())
                y_bot = float(pts[:, 1].max())
                x_left = float(pts[:, 0].min())
                items.append((y_top, y_bot - y_top, x_left, str(txt).strip()))
            # Sort strictly top -> bottom in horizontal lines, left -> right within same line
            avg_h = max(10.0, float(np.median([it[1] for it in items])))
            items.sort(key=lambda it: (round(it[0] / (avg_h * 0.65)), it[2]))
            ordered_lines = [it[3] for it in items if it[3]]
        else:
            ordered_lines = [str(t).strip() for t in txts if str(t).strip()]

        raw_joined = " ".join(ordered_lines)
        if self.lang in {"zh", "ko"}:
            return _clean_cjk_ocr_text(raw_joined)
        return _clean_latin_ocr_text(raw_joined, allow_wordninja=(self.lang in {"en", "latin"}))

    def recognize_blocks(
        self,
        image_bgr: np.ndarray,
        blocks: list[TextBlock],
        *,
        crop_pad_px: int = 8,
    ) -> list[TextBlock]:
        for block in blocks:
            pil = _extract_crop_pil(image_bgr, block, crop_pad_px)
            if pil is None:
                block.source_text = ""
            else:
                block.source_text = self.recognize_crop(pil)
        return blocks

    def unload(self) -> None:
        if self._engine is not None:
            del self._engine
            self._engine = None


def _block_line_orientation_score(block: TextBlock) -> float:
    """Estimate line width/height ratio from DBNet quads.

    > 1.35 indicates horizontal lines (English/Latin/Korean webtoon);
    < 0.72 indicates vertical columns (Japanese manga).
    """
    ratios: list[float] = []
    for poly in block.lines:
        if len(poly) < 3:
            continue
        xs = [pt[0] for pt in poly]
        ys = [pt[1] for pt in poly]
        bw = max(1.0, float(max(xs) - min(xs)))
        bh = max(1.0, float(max(ys) - min(ys)))
        ratios.append(bw / bh)
    if ratios:
        return float(np.median(ratios))
    return float(block.bbox.width) / max(1.0, float(block.bbox.height))


class AutoMultiLangOCRProvider(OCRProvider):
    """Smart Auto (JP/EN) OCR router combining RapidOCR (Latin/EN) + batched FP16 MangaOCR (JA).

    Routes English pages/bubbles directly to RapidOCR (preserving spaces, 0 MB VRAM!) and
    Japanese vertical bubbles to batched FP16 MangaOCR on RTX 5060 Ti.
    """

    name: str = "auto-ocr(ja+en)"
    supported_languages: frozenset[str] = frozenset({"auto"})
    detected_primary_language: str = "ja"

    def __init__(self) -> None:
        self._preferred_device: str = "cuda"
        self._manga_ocr: Optional[MangaOCRProvider] = None
        self._rapid_ocr: Optional[RapidOCRProvider] = None

    def load(self, device: str = "cuda") -> str:
        # Lazy per-script loading so English pages NEVER allocate manga-ocr VRAM!
        self._preferred_device = device
        return device

    def _get_rapid(self) -> RapidOCRProvider:
        if self._rapid_ocr is None:
            self._rapid_ocr = RapidOCRProvider(lang="en")
            self._rapid_ocr.load("cpu")
        return self._rapid_ocr

    def _get_manga(self) -> MangaOCRProvider:
        if self._manga_ocr is None:
            self._manga_ocr = MangaOCRProvider()
            self._manga_ocr.load(self._preferred_device)
        return self._manga_ocr

    def recognize_crop(self, crop_pil: Image.Image) -> str:
        # Single-crop fallback: probe with RapidOCR first; if CJK/kana, use MangaOCR
        rapid_txt = self._get_rapid().recognize_crop(crop_pil)
        cjk_n = len(_CJK_KANA_RE.findall(rapid_txt))
        lat_n = len(_LATIN_RE.findall(rapid_txt))
        if lat_n >= 2 and lat_n >= cjk_n * 2:
            self.detected_primary_language = "en"
            return rapid_txt
        self.detected_primary_language = "ja"
        return self._get_manga().recognize_crop(crop_pil)

    def recognize_blocks(
        self,
        image_bgr: np.ndarray,
        blocks: list[TextBlock],
        *,
        crop_pad_px: int = 6,
    ) -> list[TextBlock]:
        if not blocks:
            return blocks

        # Step 1: Classify line geometry across all blocks (0 ms!)
        ratios = [_block_line_orientation_score(b) for b in blocks]
        horiz_count = sum(1 for r in ratios if r >= 1.25)
        vert_count = sum(1 for r in ratios if r <= 0.78)

        # Step 2: Fast CPU RapidOCR probe on up to 3 largest/horizontal blocks if any look horizontal/ambiguous
        probed_texts: dict[int, str] = {}
        latin_votes = 0
        cjk_votes = 0

        if vert_count == len(blocks) and len(blocks) >= 2:
            # 100% clearly vertical Japanese manga (fast-path: zero wasted CPU probe!)
            page_is_latin = False
        else:
            probe_indices = sorted(
                range(len(blocks)),
                key=lambda i: (ratios[i] >= 1.15, blocks[i].bbox.area),
                reverse=True,
            )[: min(3, len(blocks))]
            rapid = self._get_rapid()
            for idx in probe_indices:
                pil = _extract_crop_pil(image_bgr, blocks[idx], pad_px=8)
                if pil is None:
                    continue
                txt = rapid.recognize_crop(pil)
                probed_texts[idx] = txt
                lat_n = len(_LATIN_RE.findall(txt))
                cjk_n = len(_CJK_KANA_RE.findall(txt))
                if lat_n >= 3 and lat_n > cjk_n * 2:
                    latin_votes += 1
                elif cjk_n >= 2:
                    cjk_votes += 1

            page_is_latin = (latin_votes > cjk_votes) or (
                latin_votes == cjk_votes and horiz_count > vert_count
            )

        # Step 3: Route each block to the best engine
        ja_indices: list[int] = []
        en_indices: list[int] = []

        for idx, ratio in enumerate(ratios):
            if idx in probed_texts:
                ptxt = probed_texts[idx]
                lat_n = len(_LATIN_RE.findall(ptxt))
                cjk_n = len(_CJK_KANA_RE.findall(ptxt))
                if lat_n >= 2 and lat_n > cjk_n * 2:
                    blocks[idx].source_text = ptxt
                    continue
                if cjk_n >= 2:
                    ja_indices.append(idx)
                    continue

            if page_is_latin:
                # Route clearly vertical Japanese bubbles on mixed pages to MangaOCR, else RapidOCR
                if ratio <= 0.55 and blocks[idx].bbox.height > 120:
                    ja_indices.append(idx)
                else:
                    en_indices.append(idx)
            else:
                # Route clearly horizontal wide bubbles on mixed pages to RapidOCR if short/Latin-like, else MangaOCR
                if ratio >= 2.2 and blocks[idx].bbox.width > 120:
                    en_indices.append(idx)
                else:
                    ja_indices.append(idx)

        # Execute English/Latin blocks on RapidOCR (CPU, 0 MB VRAM)
        if en_indices:
            rapid = self._get_rapid()
            for idx in en_indices:
                pil = _extract_crop_pil(image_bgr, blocks[idx], pad_px=8)
                if pil is None:
                    blocks[idx].source_text = ""
                else:
                    txt = rapid.recognize_crop(pil)
                    # If RapidOCR actually returned CJK on a horizontal Japanese header, re-route to MangaOCR
                    if len(_CJK_KANA_RE.findall(txt)) > len(_LATIN_RE.findall(txt)):
                        ja_indices.append(idx)
                    else:
                        blocks[idx].source_text = txt

        # Execute Japanese blocks in ONE batched FP16 GPU pass on MangaOCR
        if ja_indices:
            manga = self._get_manga()
            ja_blocks = [blocks[i] for i in ja_indices]
            manga.recognize_blocks(image_bgr, ja_blocks, crop_pad_px=crop_pad_px)

        total_lat = sum(len(_LATIN_RE.findall(b.source_text)) for b in blocks)
        total_cjk = sum(len(_CJK_KANA_RE.findall(b.source_text)) for b in blocks)
        self.detected_primary_language = "en" if total_lat > total_cjk else "ja"
        return blocks

    def unload(self) -> None:
        if self._manga_ocr is not None:
            self._manga_ocr.unload()
            self._manga_ocr = None
        if self._rapid_ocr is not None:
            self._rapid_ocr.unload()
            self._rapid_ocr = None
        release_gpu_memory()


_OCR_REGISTRY: dict[str, Callable[[], OCRProvider]] = {
    "auto": AutoMultiLangOCRProvider,
    "ja": MangaOCRProvider,
    "jp": MangaOCRProvider,
    "jpn": MangaOCRProvider,
    "en": lambda: RapidOCRProvider(lang="en"),
    "ko": lambda: RapidOCRProvider(lang="ko"),
    "zh": lambda: RapidOCRProvider(lang="zh"),
}


def register_ocr_provider(language: str, factory: Callable[[], OCRProvider]) -> None:
    """Register a custom OCRProvider factory for any language code."""
    _OCR_REGISTRY[language.strip().lower()] = factory


def create_ocr_provider(language: str = "auto") -> OCRProvider:
    """Instantiate the registered OCRProvider for the requested source language."""
    lang = (language or "auto").strip().lower()
    factory = _OCR_REGISTRY.get(lang)
    if factory is None:
        supported = ", ".join(sorted(_OCR_REGISTRY.keys()))
        raise ValueError(
            f"Idioma de origem '{lang}' nao suportado. Opcoes disponiveis: [{supported}]."
        )
    return factory()
