"""One-shot downloader for open-source Stage 1 PoC model weights and SIL OFL font."""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from poc.config import FONTS_DIR, MODELS_DIR, ensure_runtime_dirs
from poc.inpainter import ensure_lama_weights
from poc.renderer import ensure_manga_font

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CTD_ONNX_FILENAME = "comictextdetector.pt.onnx"
CTD_ONNX_URLS: tuple[str, ...] = (
    "https://huggingface.co/mayocream/comic-text-detector-onnx/resolve/main/comic-text-detector.onnx",
    "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.3/comictextdetector.pt.onnx",
    "https://huggingface.co/dreMaz/mit_models/resolve/main/comictextdetector.pt.onnx",
)


def ensure_ctd_onnx(models_dir: Path = MODELS_DIR) -> Path:
    """Download `comictextdetector.pt.onnx` once into `poc/models/`."""
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / CTD_ONNX_FILENAME
    if target.exists() and target.stat().st_size > 40_000_000:
        return target

    tmp = target.with_suffix(".onnx.part")
    last_err: Exception | None = None
    for url in CTD_ONNX_URLS:
        try:
            logger.info("Baixando comic-text-detector ONNX (%s)...", url)
            with requests.get(url, stream=True, timeout=180) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            if tmp.stat().st_size > 40_000_000:
                tmp.replace(target)
                logger.info(
                    "comic-text-detector ONNX salvo em %s (%.1f MB).",
                    target,
                    target.stat().st_size / 1e6,
                )
                return target
        except Exception as exc:
            last_err = exc
            logger.warning("Falha ao baixar de %s: %s", url, exc)
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    raise RuntimeError(
        f"Nao foi possivel baixar comictextdetector.pt.onnx: {last_err}"
    )


def ensure_manga_ocr_weights(models_dir: Path = MODELS_DIR) -> Path:
    """Download `kha-white/manga-ocr-base` directly into `poc/models/manga-ocr-base`."""
    from huggingface_hub import snapshot_download

    target = models_dir / "manga-ocr-base"
    if (target / "config.json").exists() and any(target.glob("*.bin")) or any(
        target.glob("*.safetensors")
    ):
        return target
    logger.info("Baixando manga-ocr (kha-white/manga-ocr-base) para %s...", target)
    snapshot_download(
        repo_id="kha-white/manga-ocr-base",
        local_dir=str(target),
    )
    logger.info("manga-ocr salvo localmente em %s.", target)
    return target


def download_all_assets() -> None:
    ensure_runtime_dirs()
    ctd_path = ensure_ctd_onnx()
    lama_path = ensure_lama_weights()
    font_path = ensure_manga_font()
    mocr_path = ensure_manga_ocr_weights()
    logger.info(
        "Assets locais prontos:\n  - CTD:      %s\n  - LaMa:     %s\n  - Font:     %s\n  - MangaOCR: %s",
        ctd_path,
        lama_path,
        font_path,
        mocr_path,
    )


if __name__ == "__main__":
    download_all_assets()
