"""Comic/Manga text block, line, and pixel-mask detector (`comic-text-detector` ONNX).

Architectural choice (justified for Python 3.12 + PyTorch 2.11 + RTX 5060 Ti):
- Uses the official `dmMaze/comic-text-detector` multi-head architecture (YOLOv5 text
  block detector + UNet pixel-level text mask + DBNet text-line map) in its clean
  ONNX format (`comictextdetector.pt.onnx`).
- Avoids legacy pickled YOLOv5 `.pt` incompatibilities with PyTorch 2.6+/NumPy 2.x while
  delivering identical detection & mask accuracy, fast GPU inference via ONNX Runtime
  CUDAExecutionProvider (reusing PyTorch's bundled CUDA 12.8 / cuDNN 9 DLLs) with
  automatic CPU fallback, and immediate VRAM release after unloading.
- Includes recursive XY-Cut Japanese manga reading-order sorting (top-to-bottom panel
  bands, right-to-left columns/bubbles within each band).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pyclipper
from shapely.geometry import Polygon

from poc.config import MODELS_DIR
from poc.download_assets import ensure_ctd_onnx
from poc.schemas import BoundingBox, TextBlock
from poc.vram import release_gpu_memory

logger = logging.getLogger(__name__)

_TORCH_DLL_HANDLES: list[object] = []


def _register_torch_cuda_dlls() -> None:
    """Expose PyTorch's bundled CUDA/cuDNN DLLs so onnxruntime-gpu finds them on Windows."""
    if os.name != "nt":
        return
    try:
        import torch

        torch_lib = Path(torch.__file__).resolve().parent / "lib"
        if torch_lib.is_dir():
            if str(torch_lib) not in os.environ.get("PATH", ""):
                os.environ["PATH"] = str(torch_lib) + os.pathsep + os.environ.get("PATH", "")
            handle = os.add_dll_directory(str(torch_lib))
            _TORCH_DLL_HANDLES.append(handle)
    except Exception as exc:
        logger.debug("Nao foi possivel registrar torch/lib DLLs: %s", exc)


def _letterbox_bottom_right(
    img_bgr: np.ndarray,
    target_size: int = 1024,
) -> tuple[np.ndarray, int, int, float]:
    """Resize keeping aspect ratio and pad bottom/right to `target_size x target_size`."""
    h, w = img_bgr.shape[:2]
    scale = min(target_size / float(h), target_size / float(w))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))

    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(img_bgr, (new_w, new_h), interpolation=interp)

    pad_w = target_size - new_w
    pad_h = target_size - new_h
    padded = cv2.copyMakeBorder(
        resized,
        0,
        pad_h,
        0,
        pad_w,
        cv2.BORDER_CONSTANT,
        value=(0, 0, 0),
    )
    return padded, new_w, new_h, scale


def _sigmoid(x: np.ndarray) -> np.ndarray:
    x_clip = np.clip(x, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-x_clip))


def _box_iou_xyxy(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute IoU between box `a` (4,) and array `b` (N, 4)."""
    xx1 = np.maximum(a[0], b[:, 0])
    yy1 = np.maximum(a[1], b[:, 1])
    xx2 = np.minimum(a[2], b[:, 2])
    yy2 = np.minimum(a[3], b[:, 3])
    inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
    area_a = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = np.maximum(0.0, (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]))
    union = area_a + area_b - inter + 1e-6
    return inter / union


def _nms_yolo_blocks(
    pred: np.ndarray,
    conf_thresh: float = 0.35,
    iou_thresh: float = 0.45,
) -> list[tuple[np.ndarray, float, int]]:
    """Parse YOLOv5 block predictions `[1, N, 5+C]` into NMS-filtered `(xyxy, conf, cls)`."""
    if pred.ndim == 3:
        pred = pred[0]
    if pred.size == 0 or pred.shape[1] < 6:
        return []

    obj_conf = pred[:, 4]
    cls_scores = pred[:, 5:]
    cls_ids = np.argmax(cls_scores, axis=1)
    cls_max = cls_scores[np.arange(len(pred)), cls_ids]
    scores = obj_conf * cls_max

    keep = scores >= conf_thresh
    if not np.any(keep):
        return []

    filtered = pred[keep]
    scores = scores[keep]
    cls_ids = cls_ids[keep]

    # xywh -> xyxy
    cx, cy, w, h = filtered[:, 0], filtered[:, 1], filtered[:, 2], filtered[:, 3]
    boxes = np.stack([cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0], axis=1)

    order = np.argsort(-scores)
    selected: list[tuple[np.ndarray, float, int]] = []
    while order.size > 0:
        idx = int(order[0])
        selected.append((boxes[idx], float(scores[idx]), int(cls_ids[idx])))
        if order.size == 1:
            break
        rest = order[1:]
        ious = _box_iou_xyxy(boxes[idx], boxes[rest])
        order = rest[ious <= iou_thresh]
    return selected


def _extract_dbnet_polygons(
    det_map: np.ndarray,
    orig_w: int,
    orig_h: int,
    new_w: int,
    new_h: int,
    *,
    thresh: float = 0.30,
    box_thresh: float = 0.50,
    unclip_ratio: float = 1.6,
) -> list[list[list[int]]]:
    """Decode DBNet probability map into text-line quadrilaterals in original image coords."""
    prob = det_map
    if prob.ndim == 4:
        prob = prob[0, 0]
    elif prob.ndim == 3:
        prob = prob[0]

    if float(prob.min()) < 0.0 or float(prob.max()) > 1.0:
        prob = _sigmoid(prob)

    prob_crop = prob[:new_h, :new_w]
    bitmap = (prob_crop > thresh).astype(np.uint8)
    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    scale_x = orig_w / float(new_w)
    scale_y = orig_h / float(new_h)
    polys: list[list[list[int]]] = []

    for cnt in contours:
        if cnt.shape[0] < 3:
            continue
        rect = cv2.minAreaRect(cnt)
        (_, _), (rw, rh), _ = rect
        if min(rw, rh) < 3.0:
            continue

        x, y, bw, bh = cv2.boundingRect(cnt)
        roi_mask = np.zeros((bh, bw), dtype=np.uint8)
        shifted = cnt.copy()
        shifted[:, 0, 0] -= x
        shifted[:, 0, 1] -= y
        cv2.fillPoly(roi_mask, [shifted], 1)
        mean_score = float(cv2.mean(prob_crop[y : y + bh, x : x + bw], roi_mask)[0])
        if mean_score < box_thresh:
            continue

        box_pts = cv2.boxPoints(rect)
        try:
            poly = Polygon(box_pts)
            if poly.area <= 1.0 or poly.length <= 1.0:
                continue
            distance = poly.area * unclip_ratio / poly.length
            offset = pyclipper.PyclipperOffset()
            offset.AddPath(
                [(int(round(p[0])), int(round(p[1]))) for p in box_pts],
                pyclipper.JT_ROUND,
                pyclipper.ET_CLOSEDPOLYGON,
            )
            expanded = offset.Execute(distance)
            if not expanded:
                continue
            exp_arr = np.array(expanded[0], dtype=np.float32)
            exp_rect = cv2.minAreaRect(exp_arr)
            quad = cv2.boxPoints(exp_rect)
        except Exception:
            quad = box_pts

        scaled_quad = [
            [
                int(np.clip(round(float(pt[0]) * scale_x), 0, orig_w - 1)),
                int(np.clip(round(float(pt[1]) * scale_y), 0, orig_h - 1)),
            ]
            for pt in quad
        ]
        polys.append(scaled_quad)

    return polys


def _merge_contained_boxes(
    boxes: list[tuple[BoundingBox, float, bool]],
) -> list[tuple[BoundingBox, float, bool]]:
    """Remove redundant nested boxes (e.g., duplicate YOLO detections over the same bubble)."""
    if len(boxes) <= 1:
        return boxes
    ordered = sorted(boxes, key=lambda item: (item[0].area, item[1]), reverse=True)
    kept: list[tuple[BoundingBox, float, bool]] = []
    for cand_box, cand_conf, cand_vert in ordered:
        redundant = False
        for k_box, _, _ in kept:
            ix1 = max(cand_box.x1, k_box.x1)
            iy1 = max(cand_box.y1, k_box.y1)
            ix2 = min(cand_box.x2, k_box.x2)
            iy2 = min(cand_box.y2, k_box.y2)
            inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
            if cand_box.area > 0 and (inter / float(cand_box.area)) >= 0.78:
                redundant = True
                break
        if not redundant:
            kept.append((cand_box, cand_conf, cand_vert))
    return kept


def _cluster_orphan_polygons(
    orphan_polys: list[list[list[int]]],
    img_w: int,
    img_h: int,
) -> list[tuple[BoundingBox, list[list[list[int]]]]]:
    """Group DBNet lines missed by YOLO (e.g. borderless text on art) into text blocks."""
    if not orphan_polys:
        return []

    poly_boxes: list[BoundingBox] = []
    for p in orphan_polys:
        xs = [pt[0] for pt in p]
        ys = [pt[1] for pt in p]
        poly_boxes.append(BoundingBox(x1=min(xs), y1=min(ys), x2=max(xs), y2=max(ys)))

    n = len(orphan_polys)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        bi = poly_boxes[i]
        pad_x = max(18, int(min(bi.width, bi.height) * 0.9))
        pad_y = max(14, int(min(bi.width, bi.height) * 0.6))
        for j in range(i + 1, n):
            bj = poly_boxes[j]
            if (
                bi.x1 - pad_x <= bj.x2
                and bj.x1 - pad_x <= bi.x2
                and bi.y1 - pad_y <= bj.y2
                and bj.y1 - pad_y <= bi.y2
            ):
                union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        groups.setdefault(find(idx), []).append(idx)

    clusters: list[tuple[BoundingBox, list[list[list[int]]]]] = []
    for indices in groups.values():
        x1 = min(poly_boxes[i].x1 for i in indices)
        y1 = min(poly_boxes[i].y1 for i in indices)
        x2 = max(poly_boxes[i].x2 for i in indices)
        y2 = max(poly_boxes[i].y2 for i in indices)
        bbox = BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2).clamped(img_w, img_h)
        if bbox.area >= 350 and max(bbox.width, bbox.height) >= 18:
            clusters.append((bbox, [orphan_polys[i] for i in indices]))
    return clusters


def resolve_reading_direction(
    requested: str,
    *,
    detected_lang: str = "ja",
    blocks: Optional[list[TextBlock]] = None,
    img_w: int = 0,
    img_h: int = 0,
) -> str:
    """Resolve effective reading direction (`rtl` vs `ltr`) from user setting + page layout/script."""
    req = (requested or "auto").strip().lower()
    if req in {"rtl", "ltr"}:
        return req

    # Tall vertical webtoon / manhwa strips ALWAYS read top-to-bottom, left-to-right
    if img_w > 0 and img_h > int(round(img_w * 2.2)):
        return "ltr"

    lang = (detected_lang or "ja").strip().lower()
    if lang in {"ko", "en", "es", "fr", "latin"}:
        return "ltr"
    if lang == "ja":
        return "rtl"
    if lang in {"zh", "zh_tw"}:
        if blocks:
            vert = sum(1 for b in blocks if b.is_vertical)
            return "rtl" if (vert * 2 >= len(blocks)) else "ltr"
        return "rtl"
    return "rtl"


def sort_blocks_reading_order(
    blocks: list[TextBlock],
    *,
    direction: str = "rtl",
    img_w: int = 0,
    img_h: int = 0,
) -> list[TextBlock]:
    """Order text blocks cleanly in RTL (Manga/Vertical CJK) or LTR (Webtoon/Manhwa/Comics).

    Supports:
    - Double-page spreads (`img_w > img_h * 1.25`): splits cleanly at the center gutter so
      Page 1 (right half in RTL, left half in LTR) finishes completely before Page 2.
    - 4-koma dual-column pages: detects full-height vertical strip gutters and reads one
      vertical column top-to-bottom before moving to the next column.
    - Standard multi-row comic/manga pages via Recursive XY-Cut.
    """
    if len(blocks) <= 1:
        for i, b in enumerate(blocks, start=1):
            b.id = i
        return blocks

    is_rtl = (direction or "rtl").strip().lower() != "ltr"

    def _split_intervals(
        items: list[TextBlock],
        axis: str,
        min_gap_px: float = -4.0,
    ) -> list[list[TextBlock]]:
        if len(items) <= 1:
            return [items]
        if axis == "y":
            sorted_items = sorted(items, key=lambda b: (b.bbox.y1, b.bbox.y2))
            groups: list[list[TextBlock]] = [[sorted_items[0]]]
            cur_end = float(sorted_items[0].bbox.y2)
            for blk in sorted_items[1:]:
                if float(blk.bbox.y1) > cur_end + min_gap_px:
                    groups.append([blk])
                    cur_end = float(blk.bbox.y2)
                else:
                    groups[-1].append(blk)
                    cur_end = max(cur_end, float(blk.bbox.y2))
            return groups
        else:
            sorted_items = sorted(items, key=lambda b: (b.bbox.x1, b.bbox.x2))
            groups = [[sorted_items[0]]]
            cur_end = float(sorted_items[0].bbox.x2)
            for blk in sorted_items[1:]:
                if float(blk.bbox.x1) > cur_end + min_gap_px:
                    groups.append([blk])
                    cur_end = float(blk.bbox.x2)
                else:
                    groups[-1].append(blk)
                    cur_end = max(cur_end, float(blk.bbox.x2))
            if is_rtl:
                groups.reverse()  # Rightmost column/panel first
            return groups

    def _xy_cut(items: list[TextBlock], prefer_axis: str = "y") -> list[TextBlock]:
        if len(items) <= 1:
            return items

        axes = ("y", "x") if prefer_axis == "y" else ("x", "y")
        for ax in axes:
            for gap in (2.0, -6.0):
                parts = _split_intervals(items, ax, min_gap_px=gap)
                if len(parts) > 1:
                    next_axis = "x" if ax == "y" else "y"
                    ordered: list[TextBlock] = []
                    for part in parts:
                        ordered.extend(_xy_cut(part, prefer_axis=next_axis))
                    return ordered

        if is_rtl:
            return sorted(
                items,
                key=lambda b: (round(b.bbox.cy * 0.55 - b.bbox.cx * 1.0, 2), b.bbox.y1, -b.bbox.x2),
            )
        return sorted(
            items,
            key=lambda b: (round(b.bbox.cy * 0.55 + b.bbox.cx * 1.0, 2), b.bbox.y1, b.bbox.x1),
        )

    # 1. Double-page spread detection (wide landscape image with clean center seam)
    if img_w > 0 and img_h > 0 and img_w > int(round(img_h * 1.25)) and len(blocks) >= 2:
        mid_x = img_w / 2.0
        crossing = [b for b in blocks if b.bbox.x1 < mid_x - img_w * 0.04 and b.bbox.x2 > mid_x + img_w * 0.04]
        left_half = [b for b in blocks if b.bbox.cx < mid_x]
        right_half = [b for b in blocks if b.bbox.cx >= mid_x]
        if not crossing and left_half and right_half:
            ordered_halves = (right_half, left_half) if is_rtl else (left_half, right_half)
            ordered_blocks: list[TextBlock] = []
            for half in ordered_halves:
                ordered_blocks.extend(_xy_cut(half, prefer_axis="y"))
            for idx, blk in enumerate(ordered_blocks, start=1):
                blk.id = idx
            return ordered_blocks

    # 2. 4-koma dual-column strip detection:
    # If there is a clean full-height vertical gutter creating 2 tall columns each having >=3 stacked rows,
    # read column-by-column top-to-bottom instead of zig-zagging horizontally across the two strips.
    initial_axis = "y"
    if len(blocks) >= 6:
        x_cols = _split_intervals(list(blocks), "x", min_gap_px=12.0)
        if len(x_cols) == 2 and all(len(c) >= 3 for c in x_cols):
            # Confirm each column spans >= 55% of the overall vertical text height (4-koma signature)
            all_y1 = min(b.bbox.y1 for b in blocks)
            all_y2 = max(b.bbox.y2 for b in blocks)
            total_span = max(1, all_y2 - all_y1)
            col_spans = [(max(b.bbox.y2 for b in c) - min(b.bbox.y1 for b in c)) / total_span for c in x_cols]
            if all(sp >= 0.55 for sp in col_spans):
                initial_axis = "x"

    ordered_blocks = _xy_cut(list(blocks), prefer_axis=initial_axis)
    for idx, blk in enumerate(ordered_blocks, start=1):
        blk.id = idx
    return ordered_blocks


def sort_blocks_japanese_reading_order(blocks: list[TextBlock]) -> list[TextBlock]:
    """Backward-compatible wrapper for authentic Japanese RTL manga reading order."""
    return sort_blocks_reading_order(blocks, direction="rtl")


class ComicTextDetector:
    """Wrapper around `comic-text-detector` ONNX with GPU/CPU support and explicit unload."""

    name: str = "comic-text-detector-onnx"

    def __init__(
        self,
        *,
        input_size: int = 1024,
        conf_thresh: float = 0.35,
        nms_iou: float = 0.45,
        mask_thresh: float = 0.30,
        models_dir: Path = MODELS_DIR,
    ) -> None:
        self.input_size = input_size
        self.conf_thresh = conf_thresh
        self.nms_iou = nms_iou
        self.mask_thresh = mask_thresh
        self.models_dir = models_dir
        self._session = None
        self._device: str = "cpu"

    def load(self, device: str = "cuda") -> str:
        """Create ONNX Runtime session on CUDA (if available) or CPU."""
        if self._session is not None:
            return self._device

        model_path = ensure_ctd_onnx(self.models_dir)
        _register_torch_cuda_dlls()
        import onnxruntime as ort

        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls()
            except Exception as exc:
                logger.debug("ort.preload_dlls() ignorado: %s", exc)

        available = ort.get_available_providers()
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_opts.intra_op_num_threads = min(8, max(2, (os.cpu_count() or 4) // 2))
        sess_opts.inter_op_num_threads = 1

        det_override = os.getenv("SCANLATOR_DETECTOR_DEVICE", "auto").strip().lower()
        effective_dev = det_override if det_override in {"cuda", "cpu"} else device

        if effective_dev == "cuda" and "CUDAExecutionProvider" in available:
            try:
                logger.info("Carregando comic-text-detector ONNX em CUDAExecutionProvider...")
                self._session = ort.InferenceSession(
                    str(model_path),
                    sess_options=sess_opts,
                    providers=[
                        (
                            "CUDAExecutionProvider",
                            {
                                "arena_extend_strategy": "kSameAsRequested",
                                "cudnn_conv_algo_search": "HEURISTIC",
                                "do_copy_in_default_stream": True,
                            },
                        ),
                        "CPUExecutionProvider",
                    ],
                )
                active_providers = self._session.get_providers()
                if "CUDAExecutionProvider" in active_providers:
                    self._device = "cuda"
                    return self._device
            except Exception as exc:
                logger.warning(
                    "CUDAExecutionProvider falhou para comic-text-detector (%s); usando CPU.",
                    exc,
                )

        logger.info("Carregando comic-text-detector ONNX em CPUExecutionProvider...")
        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        self._device = "cpu"
        return self._device

    def detect(self, image_bgr: np.ndarray) -> tuple[list[TextBlock], np.ndarray]:
        """Detect speech bubbles / text blocks and refined pixel text mask in reading order."""
        if self._session is None:
            raise RuntimeError("ComicTextDetector.load() deve ser chamado antes de detect().")

        orig_h, orig_w = image_bgr.shape[:2]
        padded_bgr, new_w, new_h, _ = _letterbox_bottom_right(image_bgr, self.input_size)

        img_rgb = cv2.cvtColor(padded_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        inp = np.transpose(img_rgb, (2, 0, 1))[None, ...].astype(np.float32)

        input_name = self._session.get_inputs()[0].name
        output_names = [o.name for o in self._session.get_outputs()]
        outputs = self._session.run(output_names, {input_name: inp})
        by_name = dict(zip(output_names, outputs))

        pred_blk = by_name.get("blk", outputs[0])
        pred_seg = by_name.get("seg", outputs[1])
        pred_det = by_name.get("det", outputs[2])

        # 1. Full-resolution pixel text mask from UNet `seg` head
        seg = pred_seg[0, 0] if pred_seg.ndim == 4 else pred_seg[0]
        if float(seg.min()) < 0.0 or float(seg.max()) > 1.0:
            seg = _sigmoid(seg)
        seg_crop = seg[:new_h, :new_w]
        seg_full = cv2.resize(seg_crop, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
        text_mask = (seg_full >= self.mask_thresh).astype(np.uint8) * 255

        # 2. DBNet text-line polygons
        line_polys = _extract_dbnet_polygons(
            pred_det,
            orig_w,
            orig_h,
            new_w,
            new_h,
        )

        # 3. YOLOv5 text block boxes
        scale_x = orig_w / float(new_w)
        scale_y = orig_h / float(new_h)
        raw_dets = _nms_yolo_blocks(
            pred_blk,
            conf_thresh=self.conf_thresh,
            iou_thresh=self.nms_iou,
        )

        yolo_candidates: list[tuple[BoundingBox, float, bool]] = []
        for xyxy, conf, cls_id in raw_dets:
            box = BoundingBox(
                x1=int(round(float(xyxy[0]) * scale_x)),
                y1=int(round(float(xyxy[1]) * scale_y)),
                x2=int(round(float(xyxy[2]) * scale_x)),
                y2=int(round(float(xyxy[3]) * scale_y)),
            ).clamped(orig_w, orig_h)
            if box.width < 12 or box.height < 12 or box.area < 200:
                continue
            is_vertical = (box.height >= box.width * 0.95) or (cls_id == 0)
            yolo_candidates.append((box, conf, is_vertical))

        yolo_candidates = _merge_contained_boxes(yolo_candidates)

        # 4. Assign DBNet line polygons to YOLO blocks; recover borderless orphan clusters
        assigned_lines: list[list[list[list[int]]]] = [[] for _ in yolo_candidates]
        orphan_lines: list[list[list[int]]] = []

        for poly in line_polys:
            xs = [pt[0] for pt in poly]
            ys = [pt[1] for pt in poly]
            pcx = sum(xs) / len(xs)
            pcy = sum(ys) / len(ys)
            px1, py1, px2, py2 = min(xs), min(ys), max(xs), max(ys)
            p_area = max(1, (px2 - px1) * (py2 - py1))

            best_idx = -1
            best_score = 0.0
            for idx, (box, _, _) in enumerate(yolo_candidates):
                ix1, iy1 = max(px1, box.x1), max(py1, box.y1)
                ix2, iy2 = min(px2, box.x2), min(py2, box.y2)
                inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
                overlap_ratio = inter / float(p_area)
                center_in = (box.x1 - 6 <= pcx <= box.x2 + 6) and (box.y1 - 6 <= pcy <= box.y2 + 6)
                score = overlap_ratio + (0.5 if center_in else 0.0)
                if score > best_score and (overlap_ratio >= 0.35 or center_in):
                    best_score = score
                    best_idx = idx

            if best_idx >= 0:
                assigned_lines[best_idx].append(poly)
            else:
                orphan_lines.append(poly)

        blocks: list[TextBlock] = []
        for (box, conf, is_vertical), lines in zip(yolo_candidates, assigned_lines):
            sub_mask = text_mask[box.y1 : box.y2, box.x1 : box.x2]
            mask_pixels = int(np.count_nonzero(sub_mask))
            if not lines and mask_pixels < 30 and conf < 0.65:
                continue

            refined_box = box
            if lines:
                all_x = [pt[0] for p in lines for pt in p]
                all_y = [pt[1] for p in lines for pt in p]
                refined_box = BoundingBox(
                    x1=max(0, min(box.x1, min(all_x)) if (box.x1 - min(all_x)) < 18 else box.x1),
                    y1=max(0, min(box.y1, min(all_y)) if (box.y1 - min(all_y)) < 18 else box.y1),
                    x2=min(orig_w, max(box.x2, max(all_x)) if (max(all_x) - box.x2) < 18 else box.x2),
                    y2=min(orig_h, max(box.y2, max(all_y)) if (max(all_y) - box.y2) < 18 else box.y2),
                ).clamped(orig_w, orig_h)

            blocks.append(
                TextBlock(
                    id=len(blocks) + 1,
                    bbox=refined_box,
                    lines=lines,
                    is_vertical=is_vertical,
                    confidence=round(conf, 4),
                )
            )

        for orphan_box, orphan_group in _cluster_orphan_polygons(orphan_lines, orig_w, orig_h):
            sub_mask = text_mask[orphan_box.y1 : orphan_box.y2, orphan_box.x1 : orphan_box.x2]
            if int(np.count_nonzero(sub_mask)) >= 45:
                blocks.append(
                    TextBlock(
                        id=len(blocks) + 1,
                        bbox=orphan_box,
                        lines=orphan_group,
                        is_vertical=orphan_box.height >= orphan_box.width,
                        confidence=0.50,
                    )
                )

        ordered = sort_blocks_japanese_reading_order(blocks)
        return ordered, text_mask

    def unload(self) -> None:
        """Destroy ONNX session and release GPU memory."""
        if self._session is not None:
            del self._session
            self._session = None
        release_gpu_memory()
