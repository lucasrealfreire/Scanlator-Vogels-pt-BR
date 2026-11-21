"""Smart vertical slicing & seamless stitching for tall Webtoon / Manhwa / Manhua strips.

Why this is essential:
- `comic-text-detector` letterboxes inputs into 1024x1024; tall webtoons (e.g. 800x18000 px)
  get crushed to ~45 px wide, losing almost all speech bubbles.
- This slicer detects safe solid/low-variance horizontal gutter bands between panels near
  target ~1800 px windows so speech bubbles are NEVER sliced in half.
- When continuous full-bleed art crosses a window boundary with no clean gutter, it creates
  a ~220 px overlap zone, deduplicates overlapping boxes cleanly (preferring the non-clipped
  detection), and feathers the inpainted seam linearly so zero seam artifacts remain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from poc.schemas import BoundingBox, TextBlock

MIN_SLICE_TRIGGER_HEIGHT = 2400
MIN_SLICE_ASPECT_RATIO = 2.4
DEFAULT_TARGET_SLICE_H = 1800
DEFAULT_MIN_SLICE_H = 1200
DEFAULT_MAX_SLICE_H = 2200
DEFAULT_OVERLAP_PX = 220


@dataclass(frozen=True)
class SliceRegion:
    """Vertical slice `[y1, y2)` inside the full strip, plus authoritative paste bounds."""

    index: int
    y1: int
    y2: int
    paste_y1: int
    paste_y2: int
    has_overlap_top: bool
    has_overlap_bottom: bool

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)


def should_slice_image(img_h: int, img_w: int) -> bool:
    """Return True when image is a tall vertical Webtoon/Manhwa strip needing slicing."""
    if img_w <= 0 or img_h <= MIN_SLICE_TRIGGER_HEIGHT:
        return False
    return float(img_h) > float(img_w) * MIN_SLICE_ASPECT_RATIO


def _find_safe_horizontal_cut(
    gray: np.ndarray,
    search_y1: int,
    search_y2: int,
    preferred_y: int,
    *,
    min_band_px: int = 10,
) -> Optional[int]:
    """Find the center Y of a solid/low-variance horizontal gutter band inside `[search_y1, search_y2)`."""
    h = gray.shape[0]
    y1 = max(0, min(h - 2, search_y1))
    y2 = max(y1 + 2, min(h, search_y2))
    window = gray[y1:y2, :]
    if window.shape[0] < min_band_px:
        return None

    row_std = window.std(axis=1)
    # Row-to-row vertical delta detects uniform horizontal solid/gradient bands vs busy panels
    vdiff = np.abs(np.diff(window.astype(np.float32), axis=0)).mean(axis=1)
    vdiff = np.append(vdiff, vdiff[-1] if vdiff.size > 0 else 0.0)
    is_clean = (row_std <= 6.5) & (vdiff <= 2.8)

    if not np.any(is_clean):
        return None

    padded = np.concatenate(([False], is_clean, [False]))
    changes = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)

    best_cut: Optional[int] = None
    best_score = -1e18
    for s, e in zip(starts, ends):
        band_len = int(e - s)
        if band_len < min_band_px:
            continue
        center_y = y1 + (int(s) + int(e)) // 2
        # Reward thick clean gutters and proximity to preferred target slice height
        dist_penalty = abs(center_y - preferred_y) / max(1.0, float(search_y2 - search_y1))
        score = min(band_len, 160) * 1.2 - dist_penalty * 90.0
        if score > best_score:
            best_score = score
            best_cut = center_y

    return best_cut


def compute_vertical_slices(
    image_bgr: np.ndarray,
    *,
    target_height: int = DEFAULT_TARGET_SLICE_H,
    min_height: int = DEFAULT_MIN_SLICE_H,
    max_height: int = DEFAULT_MAX_SLICE_H,
    overlap_px: int = DEFAULT_OVERLAP_PX,
) -> list[SliceRegion]:
    """Partition a tall strip into safe vertical slices, using overlap only when no gutter exists."""
    img_h, _img_w = image_bgr.shape[:2]
    if img_h <= max_height:
        return [
            SliceRegion(
                index=0,
                y1=0,
                y2=img_h,
                paste_y1=0,
                paste_y2=img_h,
                has_overlap_top=False,
                has_overlap_bottom=False,
            )
        ]

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    # List of tuples: (cut_y, used_overlap) separating consecutive non-overlapping nominal spans
    cuts: list[tuple[int, bool]] = []
    cursor = 0

    while img_h - cursor > max_height:
        preferred = cursor + target_height
        search_start = cursor + min_height
        search_end = min(img_h - 320, cursor + max_height)
        if search_end <= search_start:
            break

        safe_y = _find_safe_horizontal_cut(gray, search_start, search_end, preferred)
        if safe_y is not None:
            cuts.append((safe_y, False))
            cursor = safe_y
        else:
            cut_y = min(img_h - 320, preferred)
            cuts.append((cut_y, True))
            cursor = cut_y

    # Build SliceRegions with nominal paste bounds & overlap margins where needed
    boundaries = [0] + [c[0] for c in cuts] + [img_h]
    overlaps = [False] + [c[1] for c in cuts] + [False]
    half_ov = max(32, overlap_px // 2)

    regions: list[SliceRegion] = []
    for i in range(len(boundaries) - 1):
        paste_y1 = boundaries[i]
        paste_y2 = boundaries[i + 1]
        ov_top = overlaps[i]
        ov_bot = overlaps[i + 1]

        y1 = max(0, paste_y1 - half_ov) if ov_top else paste_y1
        y2 = min(img_h, paste_y2 + half_ov) if ov_bot else paste_y2

        regions.append(
            SliceRegion(
                index=i,
                y1=y1,
                y2=y2,
                paste_y1=paste_y1,
                paste_y2=paste_y2,
                has_overlap_top=ov_top,
                has_overlap_bottom=ov_bot,
            )
        )

    return regions


def offset_slice_blocks(blocks: list[TextBlock], y_offset: int) -> list[TextBlock]:
    """Translate slice-local `TextBlock` coordinates into full-strip coordinates."""
    if y_offset == 0:
        return [b.model_copy(deep=True) for b in blocks]
    shifted: list[TextBlock] = []
    for b in blocks:
        nb = b.model_copy(deep=True)
        nb.bbox = BoundingBox(
            x1=b.bbox.x1,
            y1=b.bbox.y1 + y_offset,
            x2=b.bbox.x2,
            y2=b.bbox.y2 + y_offset,
        )
        nb.lines = [
            [[pt[0], pt[1] + y_offset] for pt in poly]
            for poly in b.lines
        ]
        if b.render_bbox is not None:
            nb.render_bbox = BoundingBox(
                x1=b.render_bbox.x1,
                y1=b.render_bbox.y1 + y_offset,
                x2=b.render_bbox.x2,
                y2=b.render_bbox.y2 + y_offset,
            )
        shifted.append(nb)
    return shifted


def _box_overlap_metrics(a: BoundingBox, b: BoundingBox) -> tuple[float, float]:
    """Return `(iou, containment_over_smaller)` for two BoundingBoxes."""
    ix1 = max(a.x1, b.x1)
    iy1 = max(a.y1, b.y1)
    ix2 = min(a.x2, b.x2)
    iy2 = min(a.y2, b.y2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0, 0.0
    union = max(1, a.area + b.area - inter)
    min_area = max(1, min(a.area, b.area))
    return inter / float(union), inter / float(min_area)


def deduplicate_slice_blocks(
    entries: list[tuple[TextBlock, SliceRegion]],
) -> list[TextBlock]:
    """Deduplicate detections falling in overlap zones, preferring intact non-clipped boxes."""
    if len(entries) <= 1:
        return [e[0] for e in entries]

    def _quality_score(item: tuple[TextBlock, SliceRegion]) -> tuple[int, float, int]:
        blk, reg = item
        # Penalize boxes clipped right against an overlapping top/bottom slice edge
        clipped_top = reg.has_overlap_top and (blk.bbox.y1 <= reg.y1 + 6)
        clipped_bot = reg.has_overlap_bottom and (blk.bbox.y2 >= reg.y2 - 6)
        intact = 0 if (clipped_top or clipped_bot) else 1
        return (intact, blk.confidence, blk.bbox.area)

    ranked = sorted(entries, key=_quality_score, reverse=True)
    kept: list[tuple[TextBlock, SliceRegion]] = []

    for cand_blk, cand_reg in ranked:
        duplicate = False
        for k_blk, _k_reg in kept:
            iou, cont = _box_overlap_metrics(cand_blk.bbox, k_blk.bbox)
            if iou >= 0.40 or cont >= 0.70:
                duplicate = True
                break
        if not duplicate:
            kept.append((cand_blk, cand_reg))

    # Restore natural top-to-bottom order before final RTL/LTR reading-order sort
    kept.sort(key=lambda item: (item[0].bbox.y1, item[0].bbox.x1))
    result = [item[0] for item in kept]
    for idx, blk in enumerate(result, start=1):
        blk.id = idx
    return result


def stitch_inpainted_slices(
    cleaned_slices: list[np.ndarray],
    regions: list[SliceRegion],
    full_h: int,
    full_w: int,
    *,
    feather_px: int = 24,
) -> np.ndarray:
    """Reassemble cleaned BGR slices into a seamless full-height image."""
    if len(cleaned_slices) == 1 and regions[0].y1 == 0 and regions[0].y2 == full_h:
        return cleaned_slices[0].copy()

    canvas = np.zeros((full_h, full_w, 3), dtype=np.uint8)
    for idx, (sl_img, reg) in enumerate(zip(cleaned_slices, regions)):
        local_top = reg.paste_y1 - reg.y1
        local_bot = reg.paste_y2 - reg.y1
        canvas[reg.paste_y1 : reg.paste_y2, :] = sl_img[local_top:local_bot, :]

        # Smooth linear crossfade across overlapping seam boundaries so zero edge line shows
        if idx > 0 and reg.has_overlap_top:
            prev_img = cleaned_slices[idx - 1]
            prev_reg = regions[idx - 1]
            seam_y = reg.paste_y1
            half_f = min(
                feather_px // 2,
                seam_y - reg.y1,
                prev_reg.y2 - seam_y,
                reg.paste_y2 - seam_y,
                seam_y - prev_reg.paste_y1,
            )
            if half_f >= 2:
                gy1 = seam_y - half_f
                gy2 = seam_y + half_f
                prev_band = prev_img[gy1 - prev_reg.y1 : gy2 - prev_reg.y1, :].astype(np.float32)
                curr_band = sl_img[gy1 - reg.y1 : gy2 - reg.y1, :].astype(np.float32)
                alpha = np.linspace(0.0, 1.0, gy2 - gy1, dtype=np.float32)[:, None, None]
                blended = prev_band * (1.0 - alpha) + curr_band * alpha
                canvas[gy1:gy2, :] = np.clip(blended, 0, 255).astype(np.uint8)

    return canvas
