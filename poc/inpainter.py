"""Hybrid local inpainter: fast OpenCV solid-bubble fill + local LaMa on complex crops.

Non-negotiable VRAM & quality guarantees:
- NEVER runs LaMa on the full-resolution page at once.
- Processes each complex text block ONLY on a padded crop around the text region,
  compositing ONLY the dilated text mask back onto the original image so 100% of
  surrounding manga artwork remains untouched.
- Uses instant zero-VRAM OpenCV cleaning for solid/white speech bubbles, and computes
  safe bubble-interior `render_bbox` polygons for horizontal PT-BR lettering.
- Lazy-loads `big-lama.pt` (TorchScript) only if needed, with CUDA -> CPU OOM fallback,
  and explicitly frees 100% VRAM immediately upon completion.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests

from poc.config import MODELS_DIR
from poc.schemas import BoundingBox, TextBlock
from poc.vram import release_gpu_memory, run_with_oom_fallback

logger = logging.getLogger(__name__)

LAMA_MODEL_FILENAME = "big-lama.pt"
LAMA_DOWNLOAD_URLS: tuple[str, ...] = (
    "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt",
    "https://huggingface.co/fashn-ai/LaMa/resolve/main/big-lama.pt",
)


def ensure_lama_weights(models_dir: Path = MODELS_DIR) -> Path:
    """Download `big-lama.pt` TorchScript checkpoint once if missing."""
    models_dir.mkdir(parents=True, exist_ok=True)
    target = models_dir / LAMA_MODEL_FILENAME
    if target.exists() and target.stat().st_size > 50_000_000:
        return target

    tmp_path = target.with_suffix(".pt.part")
    last_error: Optional[Exception] = None
    for url in LAMA_DOWNLOAD_URLS:
        try:
            logger.info("Baixando pesos locais do LaMa (%s)...", url)
            with requests.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with tmp_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
            if tmp_path.stat().st_size > 50_000_000:
                tmp_path.replace(target)
                logger.info("LaMa salvo em %s (%.1f MB).", target, target.stat().st_size / 1e6)
                return target
        except Exception as exc:
            last_error = exc
            logger.warning("Falha ao baixar LaMa de %s: %s", url, exc)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    raise RuntimeError(
        f"Nao foi possivel baixar big-lama.pt de nenhuma fonte oficial: {last_error}"
    )


def _pad_to_modulo(arr: np.ndarray, mod: int = 8, is_mask: bool = False) -> tuple[np.ndarray, int, int]:
    """Pad HxW or HxWxC numpy array so H and W are divisible by `mod`."""
    h, w = arr.shape[:2]
    pad_h = (mod - (h % mod)) % mod
    pad_w = (mod - (w % mod)) % mod
    if pad_h == 0 and pad_w == 0:
        return arr, 0, 0
    if is_mask:
        padded = cv2.copyMakeBorder(arr, 0, pad_h, 0, pad_w, cv2.BORDER_CONSTANT, value=0)
    else:
        padded = cv2.copyMakeBorder(arr, 0, pad_h, 0, pad_w, cv2.BORDER_REFLECT_101)
    return padded, pad_h, pad_w


def _classify_region_background(
    crop_bgr: np.ndarray,
    dilated_mask: np.ndarray,
) -> tuple[bool, bool, tuple[int, int, int]]:
    """Inspect the pixel ring immediately surrounding the text mask.

    Returns `(is_solid_bubble, is_smooth_gradient, median_bgr)`:
    - `is_solid_bubble=True`: uniform flat fill (white, black, colored bubble, or flat RPG
      system box) — cleaned cleanly with its true sampled `median_bgr` (NEVER hardcoded white!).
    - `is_smooth_gradient=True`: smooth color gradient without complex lineart — cleaned
      instantly via OpenCV Telea inpainting preserving the exact gradient with 0 MB VRAM.
    - Both False: complex textured artwork — deferred to local LaMa crop (or Telea fallback).
    """
    ring_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
    outer = cv2.dilate(dilated_mask, ring_kernel, iterations=1)
    ring = cv2.subtract(outer, dilated_mask)
    ring_ys, ring_xs = np.nonzero(ring > 127)

    if ring_ys.size < 20:
        fallback_med = np.median(crop_bgr.reshape(-1, 3), axis=0)
        return (
            False,
            False,
            (
                int(np.clip(round(float(fallback_med[0])), 0, 255)),
                int(np.clip(round(float(fallback_med[1])), 0, 255)),
                int(np.clip(round(float(fallback_med[2])), 0, 255)),
            ),
        )

    ring_pixels = crop_bgr[ring_ys, ring_xs]
    ring_f = ring_pixels.astype(np.float32)

    # Robust dominant-color clustering around the ring median (works identically for
    # white bubbles, black bubbles, pastel/colored bubbles, and dark RPG system boxes
    # while ignoring thin contrasting borders touching the ring!)
    raw_med = np.median(ring_f, axis=0)
    raw_dists = np.linalg.norm(ring_f - raw_med, axis=1)
    inlier_mask = raw_dists <= 28.0
    inlier_ratio = float(np.mean(inlier_mask))

    if int(np.count_nonzero(inlier_mask)) >= 16:
        clean_pixels = ring_f[inlier_mask]
    else:
        clean_pixels = ring_f

    median_bgr = np.median(clean_pixels, axis=0)
    inlier_dists = np.linalg.norm(clean_pixels - median_bgr, axis=1)
    within_tol = float(np.mean(inlier_dists <= 18.0))
    std_color = float(np.sqrt(np.mean(np.var(clean_pixels, axis=0))))

    # Detect color gradients across the ring (top vs bottom, left vs right)
    gradient_diff = 0.0
    if ring_ys.size >= 40:
        mid_y = float(np.median(ring_ys))
        mid_x = float(np.median(ring_xs))
        top_px = ring_f[ring_ys <= mid_y]
        bot_px = ring_f[ring_ys > mid_y]
        left_px = ring_f[ring_xs <= mid_x]
        right_px = ring_f[ring_xs > mid_x]
        if top_px.shape[0] >= 8 and bot_px.shape[0] >= 8:
            gradient_diff = max(
                gradient_diff,
                float(np.linalg.norm(np.median(top_px, axis=0) - np.median(bot_px, axis=0))),
            )
        if left_px.shape[0] >= 8 and right_px.shape[0] >= 8:
            gradient_diff = max(
                gradient_diff,
                float(np.linalg.norm(np.median(left_px, axis=0) - np.median(right_px, axis=0))),
            )

    is_solid = (
        inlier_ratio >= 0.72
        and within_tol >= 0.85
        and std_color <= 13.0
        and gradient_diff <= 16.0
    )

    # Smooth gradient check: high spatial color drift, but low local high-frequency edge texture
    is_smooth_gradient = False
    if not is_solid and gradient_diff > 16.0:
        gray_crop = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        lap = np.abs(cv2.Laplacian(gray_crop, cv2.CV_32F, ksize=3))
        ring_edge_energy = float(np.mean(lap[ring_ys, ring_xs]))
        if ring_edge_energy < 14.0 and std_color <= 26.0:
            is_smooth_gradient = True

    med_tuple = (
        int(np.clip(round(float(median_bgr[0])), 0, 255)),
        int(np.clip(round(float(median_bgr[1])), 0, 255)),
        int(np.clip(round(float(median_bgr[2])), 0, 255)),
    )
    return is_solid, is_smooth_gradient, med_tuple


def _extract_block_local_mask(
    global_mask: np.ndarray,
    block: TextBlock,
    crop_box: BoundingBox,
    img_bgr: np.ndarray,
) -> np.ndarray:
    """Build a crisp dilated mask for one block inside `crop_box`, avoiding bubble borders."""
    ch = crop_box.height
    cw = crop_box.width
    local_mask = np.zeros((ch, cw), dtype=np.uint8)

    # Restrict mask collection to the block's bbox (with slight 4px expansion for furigana/edges)
    bx1 = max(crop_box.x1, block.bbox.x1 - 4) - crop_box.x1
    by1 = max(crop_box.y1, block.bbox.y1 - 4) - crop_box.y1
    bx2 = min(crop_box.x2, block.bbox.x2 + 4) - crop_box.x1
    by2 = min(crop_box.y2, block.bbox.y2 + 4) - crop_box.y1

    sub_global = global_mask[crop_box.y1 : crop_box.y2, crop_box.x1 : crop_box.x2]
    if sub_global.size > 0 and bx2 > bx1 and by2 > by1:
        local_mask[by1:by2, bx1:bx2] = np.where(
            sub_global[by1:by2, bx1:bx2] > 100, 255, 0
        ).astype(np.uint8)

    # Also include line polygons if the pixel mask missed tiny punctuation/furigana
    for poly in block.lines:
        pts = np.array(
            [[pt[0] - crop_box.x1, pt[1] - crop_box.y1] for pt in poly],
            dtype=np.int32,
        )
        if pts.shape[0] >= 3:
            cv2.fillPoly(local_mask, [pts], 255)

    # Fallback if neither mask nor lines fired inside bbox:
    # Detect ink contrasting with the crop border (works for dark text on light AND white text on dark/colored!)
    if int(np.count_nonzero(local_mask)) < 16 and bx2 > bx1 and by2 > by1:
        sub_img = img_bgr[
            crop_box.y1 + by1 : crop_box.y1 + by2,
            crop_box.x1 + bx1 : crop_box.x1 + bx2,
        ].astype(np.float32)
        border_px = np.concatenate(
            [sub_img[0, :], sub_img[-1, :], sub_img[:, 0], sub_img[:, -1]],
            axis=0,
        )
        bg_med = np.median(border_px, axis=0)
        color_dist = np.linalg.norm(sub_img - bg_med, axis=2)
        local_mask[by1:by2, bx1:bx2] = np.where(color_dist > 38.0, 255, 0).astype(np.uint8)

    # Slightly dilate to fully erase anti-aliased ink halos around strokes
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    dilated = cv2.dilate(local_mask, kernel, iterations=1)
    return dilated


def extract_solid_bubble_safe_mask(
    cleaned_bgr: np.ndarray,
    block: TextBlock,
    all_blocks: list[TextBlock],
    median_bgr: tuple[int, int, int],
) -> tuple[Optional[np.ndarray], Optional[BoundingBox]]:
    """Extract the eroded pixel-accurate interior mask of a solid speech bubble.

    Returns `(safe_mask_in_window, window_box)` where `safe_mask_in_window` is a
    uint8 binary mask (0/1) guaranteed to stay well clear of curved oval borders,
    bubble tails, page edges, and sibling blocks in double-lobed bubbles.
    """
    img_h, img_w = cleaned_bgr.shape[:2]
    pad_x = max(85, int(block.bbox.width * 1.15))
    pad_y = max(65, int(block.bbox.height * 0.65))
    win = BoundingBox(
        x1=max(0, block.bbox.x1 - pad_x),
        y1=max(0, block.bbox.y1 - pad_y),
        x2=min(img_w, block.bbox.x2 + pad_x),
        y2=min(img_h, block.bbox.y2 + pad_y),
    )
    sub = cleaned_bgr[win.y1 : win.y2, win.x1 : win.x2]
    if sub.size == 0:
        return None, None

    med = np.array(median_bgr, dtype=np.float32)
    dist = np.linalg.norm(sub.astype(np.float32) - med, axis=2)
    interior = (dist <= 26.0).astype(np.uint8)

    # Enforce an artificial wall along raw outer page borders so cut-off bubbles never bleed to x=0
    edge_wall = 12
    if win.x1 < edge_wall:
        interior[:, : max(0, edge_wall - win.x1)] = 0
    if win.x2 > img_w - edge_wall:
        cut = win.x2 - (img_w - edge_wall)
        if cut > 0:
            interior[:, max(0, win.width - cut) :] = 0
    if win.y1 < edge_wall:
        interior[: max(0, edge_wall - win.y1), :] = 0
    if win.y2 > img_h - edge_wall:
        cut = win.y2 - (img_h - edge_wall)
        if cut > 0:
            interior[max(0, win.height - cut) :, :] = 0

    num_labels, labels = cv2.connectedComponents(interior, connectivity=4)

    # Pick dominant interior component overlapped by the block's bbox (robust for pinched/double bubbles)
    bx1 = max(0, block.bbox.x1 - win.x1)
    by1 = max(0, block.bbox.y1 - win.y1)
    bx2 = min(win.width, block.bbox.x2 - win.x1)
    by2 = min(win.height, block.bbox.y2 - win.y1)
    roi_labels = labels[by1:by2, bx1:bx2]
    nz_labels = roi_labels[roi_labels > 0]
    if nz_labels.size == 0:
        return None, None
    vals, counts = np.unique(nz_labels, return_counts=True)
    seed_label = int(vals[int(np.argmax(counts))])
    comp = (labels == seed_label).astype(np.uint8)

    # Check if flood leaked openly across > 2 sides of search window (borderless open margin)
    border_leak = (
        int(np.mean(comp[0, :]) > 0.55)
        + int(np.mean(comp[-1, :]) > 0.55)
        + int(np.mean(comp[:, 0]) > 0.55)
        + int(np.mean(comp[:, -1]) > 0.55)
    )
    if border_leak >= 2:
        return None, None

    # Clip against Voronoi half-spaces ONLY when another block is actually nearby
    nearby_others = [
        other
        for other in all_blocks
        if other.id != block.id
        and not (
            other.bbox.x2 < win.x1 - 20
            or other.bbox.x1 > win.x2 + 20
            or other.bbox.y2 < win.y1 - 20
            or other.bbox.y1 > win.y2 + 20
        )
    ]
    if nearby_others:
        ys_grid, xs_grid = np.mgrid[0 : win.height, 0 : win.width]
        gx = xs_grid + win.x1
        gy = ys_grid + win.y1
        dist_self_sq = (gx - block.bbox.cx) ** 2 + (gy - block.bbox.cy) ** 2
        for other in nearby_others:
            dist_other_sq = (gx - other.bbox.cx) ** 2 + (gy - other.bbox.cy) ** 2
            comp[dist_other_sq < dist_self_sq] = 0

    # Adaptive contour clearance proportional to useful inscribed bubble radius
    # (DIST_L2 with 3x3 mask is ~2x faster than 5x5 while accurate for 4-14px margins)
    dist_map = cv2.distanceTransform(comp, cv2.DIST_L2, 3)
    max_radius = float(dist_map.max())
    if max_radius < 10.0:
        return None, None

    if max_radius <= 48.0:
        margin_px = float(np.clip(max_radius * 0.115, 4.0, 6.0))
    elif max_radius <= 75.0:
        margin_px = float(np.clip(max_radius * 0.14, 6.5, 10.5))
    else:
        margin_px = float(np.clip(max_radius * 0.15, 10.5, 14.0))

    safe = (dist_map >= margin_px).astype(np.uint8)
    if int(np.count_nonzero(safe)) < 100:
        safe = (dist_map >= max(3.5, max_radius * 0.09)).astype(np.uint8)
    if int(np.count_nonzero(safe)) < 64:
        return None, None

    # Keep only the best connected core inside `safe` (drops detached thin bubble tails!)
    n_safe, safe_labels = cv2.connectedComponents(safe, connectivity=4)
    if n_safe > 2:
        s_roi = safe_labels[by1:by2, bx1:bx2]
        s_nz = s_roi[s_roi > 0]
        if s_nz.size > 0:
            sv, sc = np.unique(s_nz, return_counts=True)
            best_s = int(sv[int(np.argmax(sc))])
            safe = (safe_labels == best_s).astype(np.uint8)

    # Double-lobed / joined speech-bubble pinch detector:
    # When two speech bubbles are connected by a narrow waist, slightly stricter erosion
    # reveals separate chambers. If one chamber holds >= 65% of this block's text area,
    # isolate that chamber so text stays centered inside its own lobe and NEVER crosses
    # the seam or leaves half the bubble awkwardly empty.
    pinch_thresh = max(margin_px * 1.45, max_radius * 0.25)
    core_strict = (dist_map >= pinch_thresh).astype(np.uint8)
    n_lobes, lobe_labels = cv2.connectedComponents(core_strict, connectivity=4)
    if n_lobes > 2:
        total_core = int(np.count_nonzero(core_strict))
        lobe_areas = []
        for lbl in range(1, n_lobes):
            area = int(np.count_nonzero(lobe_labels == lbl))
            if area >= max(120, int(total_core * 0.18)):
                lobe_areas.append((lbl, area))

        if len(lobe_areas) >= 2:
            # Measure how much of `block.bbox` falls in each substantial lobe
            text_overlaps: list[tuple[int, int]] = []
            for lbl, _area in lobe_areas:
                ov = int(np.count_nonzero(lobe_labels[by1:by2, bx1:bx2] == lbl))
                text_overlaps.append((lbl, ov))
            total_ov = sum(ov for _, ov in text_overlaps)
            if total_ov > 0:
                best_lbl, best_ov = max(text_overlaps, key=lambda item: item[1])
                if (best_ov / float(total_ov)) >= 0.65:
                    # Expand ONLY the dominant lobe back out to `safe` using Voronoi partition
                    # between lobe centroids so the seam is cut cleanly!
                    lobe_ys, lobe_xs = np.nonzero(lobe_labels == best_lbl)
                    best_cy, best_cx = float(lobe_ys.mean()), float(lobe_xs.mean())
                    other_centers = []
                    for lbl, _ in lobe_areas:
                        if lbl == best_lbl:
                            continue
                        oys, oxs = np.nonzero(lobe_labels == lbl)
                        other_centers.append((float(oys.mean()), float(oxs.mean())))

                    if other_centers:
                        wy, wx = np.mgrid[0 : win.height, 0 : win.width]
                        d_best = (wy - best_cy) ** 2 + (wx - best_cx) ** 2
                        keep_mask = np.ones_like(safe, dtype=bool)
                        for ocy, ocx in other_centers:
                            d_other = (wy - ocy) ** 2 + (wx - ocx) ** 2
                            keep_mask &= d_best <= d_other
                        trimmed = (safe > 0) & keep_mask
                        if int(np.count_nonzero(trimmed)) >= 100:
                            safe = trimmed.astype(np.uint8)

    return safe, win


def _compute_solid_bubble_render_bbox(
    cleaned_bgr: np.ndarray,
    block: TextBlock,
    all_blocks: list[TextBlock],
    median_bgr: tuple[int, int, int],
) -> BoundingBox:
    """Compute a conservative inscribed bounding box inside a cleaned solid speech bubble."""
    img_h, img_w = cleaned_bgr.shape[:2]
    safe, win = extract_solid_bubble_safe_mask(cleaned_bgr, block, all_blocks, median_bgr)
    if safe is None or win is None:
        return block.bbox.padded(4, img_w, img_h)

    ys, xs = np.nonzero(safe)
    y_min, y_max = int(ys.min()), int(ys.max())
    y_core_top = y_min + int((y_max - y_min) * 0.08)
    y_core_bot = y_max - int((y_max - y_min) * 0.08)
    if y_core_bot <= y_core_top + 10:
        y_core_top, y_core_bot = y_min, y_max

    row_widths = safe[y_core_top:y_core_bot, :].sum(axis=1)
    if row_widths.size == 0 or row_widths.max() < 20:
        return block.bbox

    # Keep rows with at least 55% of widest safe width (excludes narrow tails)
    good_rows = np.where(row_widths >= row_widths.max() * 0.55)[0]
    if good_rows.size == 0:
        return block.bbox

    ry1 = y_core_top + int(good_rows.min())
    ry2 = y_core_top + int(good_rows.max())

    col_profile = safe[ry1 : ry2 + 1, :]
    row_lefts = []
    row_rights = []
    for r in range(col_profile.shape[0]):
        nz = np.nonzero(col_profile[r])[0]
        if nz.size > 0:
            row_lefts.append(int(nz[0]))
            row_rights.append(int(nz[-1]))
    if not row_lefts:
        return block.bbox

    # Stricter 35th/65th percentile prevents rectangular corners from clipping curved ovals
    rx1 = int(np.percentile(row_lefts, 35))
    rx2 = int(np.percentile(row_rights, 65))

    candidate = BoundingBox(
        x1=win.x1 + rx1,
        y1=win.y1 + ry1,
        x2=win.x1 + rx2,
        y2=win.y1 + ry2,
    ).clamped(img_w, img_h)

    if candidate.width < max(24, int(block.bbox.width * 0.65)) or candidate.height < max(
        24, int(block.bbox.height * 0.55)
    ):
        return block.bbox
    return candidate


class HybridInpainter:
    """Hybrid OpenCV + local LaMa crop-based inpainter with strict VRAM cleanup."""

    def __init__(
        self,
        *,
        crop_margin: int = 44,
        max_crop_side: int = 768,
        models_dir: Path = MODELS_DIR,
    ) -> None:
        self.crop_margin = crop_margin
        self.max_crop_side = max_crop_side
        self.models_dir = models_dir
        self._lama_model = None
        self._lama_device: str = "cpu"

    def _load_lama(self, device: str) -> str:
        if self._lama_model is not None:
            return self._lama_device
        weights_path = ensure_lama_weights(self.models_dir)
        import torch

        def _load_cuda():
            logger.info("Carregando LaMa (big-lama.pt) em CUDA...")
            m = torch.jit.load(str(weights_path), map_location="cuda")
            m.eval()
            return m

        def _load_cpu():
            logger.info("Carregando LaMa (big-lama.pt) em CPU...")
            m = torch.jit.load(str(weights_path), map_location="cpu")
            m.eval()
            return m

        model, used = run_with_oom_fallback(
            cuda_fn=_load_cuda,
            cpu_fn=_load_cpu,
            stage_name="inpaint:lama_load",
            device=device,
        )
        self._lama_model = model
        self._lama_device = used
        return used

    def _run_lama_on_crop(
        self,
        crop_bgr: np.ndarray,
        mask_u8: np.ndarray,
    ) -> np.ndarray:
        """Run local LaMa strictly on a single cropped region (with modulo-8 padding)."""
        import torch

        orig_h, orig_w = crop_bgr.shape[:2]
        scale = 1.0
        work_bgr = crop_bgr
        work_mask = mask_u8

        # Bound max crop side so VRAM stays strictly within budget even on 4K pages
        max_side = max(orig_h, orig_w)
        if max_side > self.max_crop_side:
            scale = self.max_crop_side / float(max_side)
            new_w = max(16, int(round(orig_w * scale)))
            new_h = max(16, int(round(orig_h * scale)))
            work_bgr = cv2.resize(crop_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
            work_mask = cv2.resize(mask_u8, (new_w, new_h), interpolation=cv2.INTER_NEAREST)

        work_rgb = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2RGB)
        pad_img, pad_h, pad_w = _pad_to_modulo(work_rgb, mod=8, is_mask=False)
        pad_msk, _, _ = _pad_to_modulo(work_mask, mod=8, is_mask=True)

        img_np = np.transpose(pad_img.astype(np.float32) / 255.0, (2, 0, 1))[None, ...]
        msk_np = ((pad_msk > 127).astype(np.float32))[None, None, ...]

        def _infer(dev: str) -> np.ndarray:
            with torch.inference_mode():
                img_t = torch.from_numpy(img_np).to(dev)
                msk_t = torch.from_numpy(msk_np).to(dev)
                out_t = self._lama_model(img_t, msk_t)
                out_np = out_t[0].permute(1, 2, 0).detach().cpu().numpy()
                del img_t, msk_t, out_t
            return out_np

        def _cuda_call() -> np.ndarray:
            return _infer("cuda")

        def _cpu_call() -> np.ndarray:
            if self._lama_device == "cuda":
                self.unload()
                self._load_lama("cpu")
            return _infer("cpu")

        out_rgb_f, used = run_with_oom_fallback(
            cuda_fn=_cuda_call,
            cpu_fn=_cpu_call,
            stage_name="inpaint:lama_crop",
            device=self._lama_device,
        )
        self._lama_device = used

        # Unpad and convert back to BGR uint8
        h_unpad = pad_img.shape[0] - pad_h
        w_unpad = pad_img.shape[1] - pad_w
        out_rgb_f = out_rgb_f[:h_unpad, :w_unpad]
        # Handle both [0, 1] and [0, 255] TorchScript variants safely
        if float(out_rgb_f.max()) <= 1.5:
            out_rgb_f = out_rgb_f * 255.0
        out_rgb = np.clip(out_rgb_f, 0, 255).astype(np.uint8)
        out_bgr = cv2.cvtColor(out_rgb, cv2.COLOR_RGB2BGR)

        if scale < 1.0:
            out_bgr = cv2.resize(out_bgr, (orig_w, orig_h), interpolation=cv2.INTER_CUBIC)
        return out_bgr

    def inpaint_page(
        self,
        image_bgr: np.ndarray,
        global_mask: np.ndarray,
        blocks: list[TextBlock],
        *,
        device: str = "cuda",
        force_lama_for_complex: bool = True,
    ) -> tuple[np.ndarray, str, dict[str, int]]:
        """Clean every detected text block using hybrid OpenCV (solid) + LaMa (complex crops).

        Returns `(cleaned_bgr, device_used, stats)`.
        """
        img_h, img_w = image_bgr.shape[:2]
        cleaned = image_bgr.copy()
        lama_queue: list[tuple[TextBlock, BoundingBox, np.ndarray]] = []
        opencv_count = 0
        lama_count = 0
        skipped_count = 0

        for block in blocks:
            crop_box = block.bbox.padded(self.crop_margin, img_w, img_h)
            if crop_box.width < 8 or crop_box.height < 8:
                block.inpaint_method = "skipped"
                skipped_count += 1
                continue

            local_mask = _extract_block_local_mask(global_mask, block, crop_box, cleaned)
            if int(np.count_nonzero(local_mask)) == 0:
                block.inpaint_method = "skipped"
                skipped_count += 1
                continue

            crop_bgr = cleaned[crop_box.y1 : crop_box.y2, crop_box.x1 : crop_box.x2]
            is_solid, is_smooth_gradient, median_bgr = _classify_region_background(
                crop_bgr, local_mask
            )

            if is_solid:
                # Fast, spotless OpenCV cleaning using the TRUE sampled background color
                # (preserves white, black, pastel/colored bubbles, and dark RPG system boxes!)
                fill_layer = np.empty_like(crop_bgr)
                fill_layer[:, :] = median_bgr
                # Soft 1px feathering on edges for anti-aliased blending
                alpha = cv2.GaussianBlur(local_mask.astype(np.float32) / 255.0, (3, 3), 0)
                alpha = np.clip(alpha * 1.15, 0.0, 1.0)[..., None]
                blended = (
                    fill_layer.astype(np.float32) * alpha
                    + crop_bgr.astype(np.float32) * (1.0 - alpha)
                )
                cleaned[crop_box.y1 : crop_box.y2, crop_box.x1 : crop_box.x2] = np.clip(
                    blended, 0, 255
                ).astype(np.uint8)

                block.inpaint_method = "opencv_solid"
                block.is_solid_bubble = True
                block.bubble_bg_rgb = (median_bgr[2], median_bgr[1], median_bgr[0])
                opencv_count += 1
            elif is_smooth_gradient or not force_lama_for_complex:
                # Preserve color gradients / colored system boxes cleanly via local Telea (0 MB VRAM!)
                inpainted_crop = cv2.inpaint(crop_bgr, local_mask, 5, cv2.INPAINT_TELEA)
                alpha = cv2.GaussianBlur(local_mask.astype(np.float32) / 255.0, (3, 3), 0)
                alpha = np.clip(alpha * 1.15, 0.0, 1.0)[..., None]
                blended = (
                    inpainted_crop.astype(np.float32) * alpha
                    + crop_bgr.astype(np.float32) * (1.0 - alpha)
                )
                cleaned[crop_box.y1 : crop_box.y2, crop_box.x1 : crop_box.x2] = np.clip(
                    blended, 0, 255
                ).astype(np.uint8)

                block.inpaint_method = "opencv_solid"
                block.is_solid_bubble = False
                block.bubble_bg_rgb = (median_bgr[2], median_bgr[1], median_bgr[0])
                opencv_count += 1
            else:
                # Defer to local LaMa crop batch so LaMa loads once and unloads right after
                block.is_solid_bubble = False
                block.bubble_bg_rgb = (median_bgr[2], median_bgr[1], median_bgr[0])
                lama_queue.append((block, crop_box, local_mask))

        # Run LaMa strictly on complex crops, then immediately unload from VRAM
        used_device = "cpu"
        if lama_queue:
            used_device = self._load_lama(device)
            try:
                for block, crop_box, local_mask in lama_queue:
                    crop_bgr = cleaned[crop_box.y1 : crop_box.y2, crop_box.x1 : crop_box.x2]
                    inpainted_crop = self._run_lama_on_crop(crop_bgr, local_mask)

                    # Composite ONLY the masked pixels so 100% of external artwork is untouched
                    alpha = cv2.GaussianBlur(local_mask.astype(np.float32) / 255.0, (5, 5), 0)
                    alpha = np.clip(alpha * 1.2, 0.0, 1.0)[..., None]
                    composited = (
                        inpainted_crop.astype(np.float32) * alpha
                        + crop_bgr.astype(np.float32) * (1.0 - alpha)
                    )
                    cleaned[crop_box.y1 : crop_box.y2, crop_box.x1 : crop_box.x2] = np.clip(
                        composited, 0, 255
                    ).astype(np.uint8)
                    block.inpaint_method = "lama_crop"
                    lama_count += 1
                used_device = self._lama_device
            finally:
                self.unload()

        # Compute optimal safe rendering boxes on the cleaned page
        for block in blocks:
            if block.is_solid_bubble:
                med_bgr = (
                    block.bubble_bg_rgb[2],
                    block.bubble_bg_rgb[1],
                    block.bubble_bg_rgb[0],
                )
                block.render_bbox = _compute_solid_bubble_render_bbox(
                    cleaned, block, blocks, med_bgr
                )
            else:
                # Over complex artwork: keep centered tightly around original text area
                # with a small horizontal breathability margin for Portuguese words
                pad_x = min(14, max(4, int(block.bbox.width * 0.12)))
                pad_y = min(8, max(2, int(block.bbox.height * 0.05)))
                block.render_bbox = BoundingBox(
                    x1=max(0, block.bbox.x1 - pad_x),
                    y1=max(0, block.bbox.y1 - pad_y),
                    x2=min(img_w, block.bbox.x2 + pad_x),
                    y2=min(img_h, block.bbox.y2 + pad_y),
                )

        return (
            cleaned,
            used_device,
            {
                "opencv_solid": opencv_count,
                "lama_crop": lama_count,
                "skipped": skipped_count,
            },
        )

    def unload(self) -> None:
        """Delete LaMa model and free 100% VRAM."""
        if self._lama_model is not None:
            del self._lama_model
            self._lama_model = None
        release_gpu_memory()
