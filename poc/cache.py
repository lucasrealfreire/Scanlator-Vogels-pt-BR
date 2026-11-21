"""Local content-addressed cache keyed by SHA-256 of the input image.

Ensures translation retries NEVER re-run local GPU detection, OCR, or inpainting.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from poc.config import CACHE_DIR
from poc.schemas import TextBlock


def compute_image_sha256(image_path: Path) -> str:
    """Compute SHA-256 hex digest of an image file."""
    hasher = hashlib.sha256()
    with image_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class PageCache:
    """Stores detection blocks, text masks, OCR text, and inpainted canvas per image hash."""

    CACHE_VERSION = 1

    def __init__(self, image_sha256: str, base_dir: Path = CACHE_DIR) -> None:
        self.image_sha256 = image_sha256
        self.dir = base_dir / image_sha256
        self.dir.mkdir(parents=True, exist_ok=True)
        self.detection_ocr_file = self.dir / "detection_ocr.json"
        self.mask_file = self.dir / "text_mask.png"
        self.inpainted_file = self.dir / "inpainted.png"
        self.inpaint_meta_file = self.dir / "inpaint_meta.json"

    def save_detection_and_ocr(
        self,
        blocks: list[TextBlock],
        text_mask: np.ndarray,
        detector_name: str,
        ocr_name: str,
    ) -> None:
        """Persist detected blocks + OCR results + refined pixel mask."""
        payload = {
            "version": self.CACHE_VERSION,
            "image_sha256": self.image_sha256,
            "detector": detector_name,
            "ocr": ocr_name,
            "blocks": [b.model_dump() for b in blocks],
        }
        self.detection_ocr_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ok, encoded = cv2.imencode(".png", text_mask)
        if ok:
            self.mask_file.write_bytes(encoded.tobytes())

    def load_detection_and_ocr(self) -> Optional[tuple[list[TextBlock], np.ndarray]]:
        """Load cached detection blocks, OCR text, and text mask if complete."""
        if not (self.detection_ocr_file.exists() and self.mask_file.exists()):
            return None
        try:
            data = json.loads(self.detection_ocr_file.read_text(encoding="utf-8"))
            if data.get("version") != self.CACHE_VERSION:
                return None
            blocks = [TextBlock.model_validate(item) for item in data.get("blocks", [])]
            mask_bytes = np.frombuffer(self.mask_file.read_bytes(), dtype=np.uint8)
            mask = cv2.imdecode(mask_bytes, cv2.IMREAD_GRAYSCALE)
            if mask is None:
                return None
            return blocks, mask
        except Exception:
            return None

    def save_inpainted(
        self,
        inpainted_bgr: np.ndarray,
        blocks: list[TextBlock],
    ) -> None:
        """Persist cleaned/inpainted image and per-block inpainting/bubble metadata."""
        ok, encoded = cv2.imencode(".png", inpainted_bgr)
        if ok:
            self.inpainted_file.write_bytes(encoded.tobytes())
        meta = {
            "version": self.CACHE_VERSION,
            "blocks": [
                {
                    "id": b.id,
                    "inpaint_method": b.inpaint_method,
                    "is_solid_bubble": b.is_solid_bubble,
                    "bubble_bg_rgb": list(b.bubble_bg_rgb),
                    "render_bbox": b.render_bbox.model_dump() if b.render_bbox else None,
                }
                for b in blocks
            ],
        }
        self.inpaint_meta_file.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_inpainted(
        self,
        blocks: list[TextBlock],
    ) -> Optional[np.ndarray]:
        """Load cached inpainted image and rehydrate per-block inpainting metadata."""
        if not (self.inpainted_file.exists() and self.inpaint_meta_file.exists()):
            return None
        try:
            meta = json.loads(self.inpaint_meta_file.read_text(encoding="utf-8"))
            if meta.get("version") != self.CACHE_VERSION:
                return None
            by_id = {item["id"]: item for item in meta.get("blocks", [])}
            if len(by_id) != len(blocks):
                return None
            img_bytes = np.frombuffer(self.inpainted_file.read_bytes(), dtype=np.uint8)
            inpainted = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
            if inpainted is None:
                return None

            from poc.schemas import BoundingBox

            for block in blocks:
                info = by_id.get(block.id)
                if info is None:
                    return None
                block.inpaint_method = info["inpaint_method"]
                block.is_solid_bubble = bool(info["is_solid_bubble"])
                rgb = info["bubble_bg_rgb"]
                block.bubble_bg_rgb = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
                rb = info.get("render_bbox")
                block.render_bbox = BoundingBox.model_validate(rb) if rb else None
            return inpainted
        except Exception:
            return None
