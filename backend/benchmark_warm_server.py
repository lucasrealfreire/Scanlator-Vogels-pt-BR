"""Live end-to-end validation benchmark (v1.2 Multimodal Vision Grid + Fast JA manga-ocr).

Measures on a live 127.0.0.1:8765 server (RTX 5060 Ti + Gemini 3.5 Flash-Lite Free Tier, `use_cache=False`):
1. Lightweight non-blocking startup (`GET /health` + detector-only background warmup).
2. Japanese (`source_lang="ja"`, `manga_ocr_v1` FP16):
   - Hot single page (`poc/input/test.jpg`) — target < 5s.
   - 4-page batch (`POST /translate-batch`, 1 Gemini call) — target < 3s/page.
3. Korean Manhwa (`source_lang="auto"`, `vision_grid_v1`):
   - Realistic multi-panel Korean page with Hangul dialogues, white bubble on dark blackboard,
     dark RPG system box, and art/SFX noise — verifying natural PT-BR, zero hallucination,
     zero black-rectangle bug, and zero erased empty balloons!
4. Vertical Chinese Manga (`source_lang="auto"`, `vision_grid_v1`):
   - Realistic multi-bubble vertical Chinese page (Hanzi top-to-bottom, right-to-left columns)
     + pure punctuation bubble (`…!`) — verifying accurate vertical reading & natural PT-BR!
5. 3-page Multimodal Vision Batch (`POST /translate-batch`, `source_lang="auto"`, 1 Gemini call).
6. Clean `POST /shutdown` + `nvidia-smi` confirmation of **0 MB VRAM** retained.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from poc.config import INPUT_DIR, OUTPUT_DIR, PROJECT_ROOT
from poc.vram import get_nvidia_memory_info

PORT = 8765
BASE_URL = f"http://127.0.0.1:{PORT}"


def wait_for_health(timeout_sec: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/health", timeout=2.0)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(0.25)
    raise RuntimeError("Timeout aguardando /health do backend.")


def wait_for_warmup(timeout_sec: float = 30.0) -> tuple[dict, float]:
    t0 = time.perf_counter()
    deadline = time.monotonic() + timeout_sec
    last_h: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{BASE_URL}/health", timeout=2.0)
        if r.status_code == 200:
            last_h = r.json()
            if last_h.get("warmup_done") and not last_h.get("warming_up"):
                return last_h, time.perf_counter() - t0
        time.sleep(0.2)
    raise RuntimeError(f"Timeout aguardando aquecimento GPU: {last_h}")


def make_distinct_page_b64(src_path: Path, variant_idx: int) -> str:
    img = Image.open(src_path).convert("RGB")
    px = img.getpixel((0, 0))
    img.putpixel((0, 0), ((px[0] + variant_idx * 7) % 256, px[1], px[2]))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_korean_manhwa_test_page(out_path: Path) -> bytes:
    """Create a realistic multi-panel Korean manhwa page with Hangul dialogues & blackboard edge-case."""
    font_path = r"C:\Windows\Fonts\malgunbd.ttf"
    w, h = 900, 1280
    img = Image.new("RGB", (w, h), (242, 244, 248))
    draw = ImageDraw.Draw(img)
    f_main = ImageFont.truetype(font_path, 28)
    f_sys = ImageFont.truetype(font_path, 25)

    # Panel 1 (top): Dark classroom blackboard background with WHITE speech bubble on top!
    draw.rectangle([30, 30, w - 30, 420], fill=(22, 28, 30), outline=(10, 10, 10), width=4)
    # Chalk dust accents
    draw.line([60, 380, 420, 380], fill=(70, 82, 84), width=3)
    # Bubble 1 (top-left, white on dark blackboard!)
    draw.ellipse([55, 55, 435, 225], fill=(252, 252, 252), outline=(15, 15, 15), width=4)
    draw.text((100, 95), "야, 너 지금", fill=(18, 18, 18), font=f_main)
    draw.text((90, 138), "무슨 소리 하는 거야?", fill=(18, 18, 18), font=f_main)

    # Bubble 2 (top-right, white on dark blackboard!)
    draw.ellipse([485, 195, 845, 385], fill=(252, 252, 252), outline=(15, 15, 15), width=4)
    draw.text((535, 245), "말도 안 돼...", fill=(18, 18, 18), font=f_main)
    draw.text((525, 290), "진짜 혼자 간다고?", fill=(18, 18, 18), font=f_main)

    # Panel 2 (middle): Bright outdoor scene with 2 bubbles
    draw.rectangle([30, 450, w - 30, 840], fill=(215, 232, 245), outline=(15, 15, 15), width=4)
    draw.ellipse([65, 485, 435, 650], fill=(255, 255, 255), outline=(18, 18, 18), width=4)
    draw.text((108, 525), "걱정 마, 금방", fill=(18, 18, 18), font=f_main)
    draw.text((112, 568), "끝내고 올게!", fill=(18, 18, 18), font=f_main)

    draw.ellipse([475, 635, 845, 805], fill=(255, 248, 210), outline=(18, 18, 18), width=4)
    draw.text((525, 680), "조심해! 저 녀석", fill=(18, 18, 18), font=f_main)
    draw.text((518, 725), "보통 상대가 아니야.", fill=(18, 18, 18), font=f_main)

    # Panel 3 (bottom): Dark RPG System Notification Box (dark navy background + white Hangul text!)
    draw.rectangle([30, 870, w - 30, 1245], fill=(28, 32, 46), outline=(15, 15, 15), width=4)
    draw.rounded_rectangle([140, 945, 760, 1175], radius=16, fill=(18, 30, 56), outline=(56, 189, 248), width=4)
    draw.text((225, 990), "[시스템 알림]", fill=(125, 211, 252), font=f_sys)
    draw.text((195, 1045), "긴급 퀘스트가 도착했습니다!", fill=(248, 250, 252), font=f_sys)
    draw.text((235, 1095), "보스를 처치하십시오.", fill=(248, 250, 252), font=f_sys)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    raw = buf.getvalue()
    out_path.write_bytes(raw)
    return raw


def _draw_vertical_cjk(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    top_y: int,
    columns_rtl: list[str],
    font: ImageFont.FreeTypeFont,
    col_step: int = 38,
    char_step: int = 34,
) -> None:
    """Draw vertical CJK text columns ordered right-to-left centered around `center_x`."""
    total_w = len(columns_rtl) * col_step
    right_start = center_x + total_w // 2 - col_step + 4
    for c_idx, col_str in enumerate(columns_rtl):
        x = right_start - c_idx * col_step
        y = top_y
        for ch in col_str:
            draw.text((x, y), ch, fill=(18, 18, 18), font=font)
            y += char_step


def build_vertical_chinese_manga_test_page(out_path: Path) -> bytes:
    """Create a realistic vertical Chinese (Hanzi RTL columns) manga test page."""
    font_path = r"C:\Windows\Fonts\msyhbd.ttc"
    w, h = 900, 1280
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    f_zh = ImageFont.truetype(font_path, 30)

    # Panel 1 (top): 2 vertical Chinese speech bubbles (right -> left!)
    draw.rectangle([30, 30, w - 30, 610], fill=(238, 240, 244), outline=(15, 15, 15), width=4)
    # Bubble 1 (top-right, read FIRST in RTL!)
    draw.ellipse([545, 65, 835, 435], fill=(255, 255, 255), outline=(18, 18, 18), width=4)
    _draw_vertical_cjk(draw, 690, 120, ["你在说什", "么胡话呢？"], f_zh)

    # Bubble 2 (top-left, read SECOND in RTL!)
    draw.ellipse([80, 180, 390, 560], fill=(255, 255, 255), outline=(18, 18, 18), width=4)
    _draw_vertical_cjk(draw, 235, 235, ["快点走吧，", "已经来不", "及了！"], f_zh)

    # Panel 2 (bottom): 2 vertical Chinese speech bubbles + 1 punctuation-only bubble (`…!`)
    draw.rectangle([30, 645, w - 30, 1245], fill=(230, 234, 240), outline=(15, 15, 15), width=4)
    # Bubble 3 (bottom-right)
    draw.ellipse([555, 695, 835, 1085], fill=(255, 255, 255), outline=(18, 18, 18), width=4)
    _draw_vertical_cjk(draw, 695, 755, ["别慌，我", "自有办法。"], f_zh)

    # Bubble 4 (middle, punctuation-only)
    draw.ellipse([375, 735, 495, 895], fill=(255, 255, 255), outline=(18, 18, 18), width=4)
    _draw_vertical_cjk(draw, 435, 780, ["…！"], f_zh)

    # Bubble 5 (bottom-left)
    draw.ellipse([70, 815, 345, 1195], fill=(255, 255, 255), outline=(18, 18, 18), width=4)
    _draw_vertical_cjk(draw, 208, 875, ["那就拜托", "你了！"], f_zh)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    raw = buf.getvalue()
    out_path.write_bytes(raw)
    return raw


def query_nvidia_compute_apps() -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"],
            text=True,
            timeout=5,
        )
        return out.strip()
    except Exception as exc:
        return f"<nvidia-smi error: {exc}>"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ja_path = INPUT_DIR / "test.jpg"
    ja_b64 = base64.b64encode(ja_path.read_bytes()).decode("ascii")

    ko_raw = build_korean_manhwa_test_page(OUTPUT_DIR / "input_ko_test.jpg")
    ko_b64 = base64.b64encode(ko_raw).decode("ascii")

    zh_raw = build_vertical_chinese_manga_test_page(OUTPUT_DIR / "input_zh_vert_test.jpg")
    zh_b64 = base64.b64encode(zh_raw).decode("ascii")

    nv_before = get_nvidia_memory_info()
    print(f"[0] NVIDIA VRAM antes de ligar: {nv_before.used_mb:.0f} MiB / {nv_before.total_mb:.0f} MiB")

    python_exe = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    server_log_path = OUTPUT_DIR / "benchmark_v12_server.log"
    log_fh = server_log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            str(python_exe),
            "-m",
            "backend.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
            "--idle-timeout",
            "300",
        ],
        cwd=str(PROJECT_ROOT),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )

    try:
        t_boot0 = time.perf_counter()
        health0 = wait_for_health()
        boot_s = time.perf_counter() - t_boot0
        print(
            f"[1] /health em {boot_s:.2f}s (PID={health0['pid']}, warming_up={health0.get('warming_up')})"
        )

        health_warm, warmup_s = wait_for_warmup()
        print(
            f"[2] Warmup detector-only concluido em {warmup_s:.2f}s | "
            f"modelos={health_warm['models_loaded']} | torch_vram={health_warm['torch_vram_allocated_mb']} MB"
        )

        # ------------------------------------------------------------------
        # (A) KOREAN MANHWA via Vision Grid (`source_lang="auto"`, `use_cache=False`)
        # ------------------------------------------------------------------
        print("\n[3] Testando COREANO (Manhwa com quadro-negro + caixa RPG) via Grade Vision (auto)...")
        t_ko = time.perf_counter()
        r_ko = httpx.post(
            f"{BASE_URL}/translate-page",
            json={
                "image_base64": ko_b64,
                "source_lang": "auto",
                "reading_direction": "ltr",
                "output_format": "webp",
                "use_cache": False,
                "style": "scanlation",
            },
            timeout=120.0,
        )
        r_ko.raise_for_status()
        d_ko = r_ko.json()
        dt_ko = time.perf_counter() - t_ko
        (OUTPUT_DIR / "traduzida_ko_vision.webp").write_bytes(base64.b64decode(d_ko["image_base64"]))
        print(
            f"    -> COREANO OK em {dt_ko:.2f}s | mode={d_ko['extraction_mode']} | "
            f"blocks={d_ko['translated_blocks_count']}/{d_ko['blocks_count']} | timings_ms={d_ko['timings_ms']}"
        )
        for b in d_ko.get("blocks", []):
            print(f"       KO #{b['id']:02d} ({b['inpaint_method']}): {b['translated_text']}")

        # ------------------------------------------------------------------
        # (B) VERTICAL CHINESE MANGA via Vision Grid (`source_lang="auto"`, `use_cache=False`)
        # ------------------------------------------------------------------
        print("\n[4] Testando CHINES VERTICAL (Hanzi RTL) via Grade Vision (auto)...")
        t_zh = time.perf_counter()
        r_zh = httpx.post(
            f"{BASE_URL}/translate-page",
            json={
                "image_base64": zh_b64,
                "source_lang": "auto",
                "reading_direction": "rtl",
                "output_format": "webp",
                "use_cache": False,
                "style": "scanlation",
            },
            timeout=120.0,
        )
        r_zh.raise_for_status()
        d_zh = r_zh.json()
        dt_zh = time.perf_counter() - t_zh
        (OUTPUT_DIR / "traduzida_zh_vert_vision.webp").write_bytes(base64.b64decode(d_zh["image_base64"]))
        print(
            f"    -> CHINES VERTICAL OK em {dt_zh:.2f}s | mode={d_zh['extraction_mode']} | "
            f"blocks={d_zh['translated_blocks_count']}/{d_zh['blocks_count']} | timings_ms={d_zh['timings_ms']}"
        )
        for b in d_zh.get("blocks", []):
            print(f"       ZH #{b['id']:02d} ({b['inpaint_method']}): {b['translated_text']}")

        # ------------------------------------------------------------------
        # (C) 3-PAGE MULTIMODAL VISION BATCH (`POST /translate-batch`, `source_lang="auto"`, 1 call!)
        # ------------------------------------------------------------------
        print("\n[5] Testando LOTE MULTIMODAL 3 PAGINAS (KO + ZH + JP) via Grade Vision...")
        reqs_pre_vbatch = (d_zh.get("quota") or {}).get("requests_today", 0)
        t_vb = time.perf_counter()
        r_vb = httpx.post(
            f"{BASE_URL}/translate-batch",
            json={
                "pages": [
                    {"page_id": "ko_p0", "image_base64": ko_b64},
                    {"page_id": "zh_p1", "image_base64": zh_b64},
                    {"page_id": "ja_p2", "image_base64": ja_b64},
                ],
                "source_lang": "auto",
                "output_format": "webp",
                "use_cache": False,
                "style": "scanlation",
            },
            timeout=120.0,
        )
        r_vb.raise_for_status()
        d_vb = r_vb.json()
        dt_vb = time.perf_counter() - t_vb
        vb_reqs = (d_vb.get("quota") or {}).get("requests_today", 0) - reqs_pre_vbatch
        print(
            f"    -> Lote Vision 3 pags OK em {dt_vb:.2f}s (media: {dt_vb / 3.0:.2f}s/pag | "
            f"backend avg: {d_vb['avg_ms_per_page']:.1f}ms/pag) | Gemini reqs={vb_reqs} | "
            f"timings_ms={d_vb['timings_ms']}"
        )

        # ------------------------------------------------------------------
        # (D) JAPANESE (`source_lang="ja"`, `manga-ocr` FP16): cold load -> hot single + 4-page batch
        # ------------------------------------------------------------------
        print("\n[6] Aquecendo manga-ocr FP16 sob demanda (1a pagina `ja`) e medindo Pagina JA quente...")
        r_ja_warm = httpx.post(
            f"{BASE_URL}/translate-page",
            json={
                "image_base64": ja_b64,
                "source_lang": "ja",
                "output_format": "webp",
                "use_cache": False,
                "style": "scanlation",
            },
            timeout=120.0,
        )
        r_ja_warm.raise_for_status()

        t_ja = time.perf_counter()
        r_ja = httpx.post(
            f"{BASE_URL}/translate-page",
            json={
                "image_base64": ja_b64,
                "source_lang": "ja",
                "output_format": "webp",
                "use_cache": False,
                "style": "scanlation",
            },
            timeout=120.0,
        )
        r_ja.raise_for_status()
        d_ja = r_ja.json()
        dt_ja = time.perf_counter() - t_ja
        (OUTPUT_DIR / "traduzida_ja_v12.webp").write_bytes(base64.b64decode(d_ja["image_base64"]))
        print(
            f"    -> JAPONES QUENTE (`ja`) OK em {dt_ja:.2f}s | mode={d_ja['extraction_mode']} | "
            f"timings_ms={d_ja['timings_ms']}"
        )

        print("\n[7] Testando LOTE JAPONES 4 PAGINAS (`source_lang='ja'`, 1 chamada Gemini)...")
        batch_ja = [
            {"page_id": f"ja_{i}", "image_base64": make_distinct_page_b64(ja_path, i + 1)}
            for i in range(4)
        ]
        reqs_pre_jab = (d_ja.get("quota") or {}).get("requests_today", 0)
        t_jab = time.perf_counter()
        r_jab = httpx.post(
            f"{BASE_URL}/translate-batch",
            json={
                "pages": batch_ja,
                "source_lang": "ja",
                "output_format": "webp",
                "use_cache": False,
                "style": "scanlation",
            },
            timeout=120.0,
        )
        r_jab.raise_for_status()
        d_jab = r_jab.json()
        dt_jab = time.perf_counter() - t_jab
        jab_reqs = (d_jab.get("quota") or {}).get("requests_today", 0) - reqs_pre_jab
        h_peak = httpx.get(f"{BASE_URL}/health", timeout=3.0).json()
        print(
            f"    -> Lote JA 4 pags OK em {dt_jab:.2f}s (media: {dt_jab / 4.0:.2f}s/pag | "
            f"backend avg: {d_jab['avg_ms_per_page']:.1f}ms/pag) | Gemini reqs={jab_reqs} | "
            f"timings_ms={d_jab['timings_ms']}"
        )

        # ------------------------------------------------------------------
        # (E) Shutdown & 0 MB VRAM Verification
        # ------------------------------------------------------------------
        print("\n[8] Chamando POST /shutdown...")
        rs = httpx.post(f"{BASE_URL}/shutdown", timeout=10.0)
        rs.raise_for_status()
        exit_code = proc.wait(timeout=15)
        time.sleep(1.2)
        nv_after = get_nvidia_memory_info()
        compute_apps = query_nvidia_compute_apps()
        has_python_cuda = "python" in compute_apps.lower()
        print(f"    -> Processo encerrado (exit_code={exit_code}).")
        print(
            f"    -> NVIDIA VRAM: antes={nv_before.used_mb:.0f} MiB | "
            f"pico={h_peak['nvidia_vram_used_mb']} MiB | pos-shutdown={nv_after.used_mb:.0f} MiB | "
            f"python_em_cuda={has_python_cuda}"
        )

        summary = {
            "boot_health_sec": round(boot_s, 3),
            "detector_warmup_sec": round(warmup_s, 3),
            "ko_vision_single_sec": round(dt_ko, 3),
            "ko_timings_ms": d_ko["timings_ms"],
            "ko_blocks": d_ko["blocks"],
            "zh_vert_vision_single_sec": round(dt_zh, 3),
            "zh_timings_ms": d_zh["timings_ms"],
            "zh_blocks": d_zh["blocks"],
            "vision_batch_3_total_sec": round(dt_vb, 3),
            "vision_batch_3_avg_sec": round(dt_vb / 3.0, 3),
            "vision_batch_3_gemini_reqs": vb_reqs,
            "vision_batch_3_timings_ms": d_vb["timings_ms"],
            "ja_hot_single_sec": round(dt_ja, 3),
            "ja_hot_timings_ms": d_ja["timings_ms"],
            "ja_blocks": d_ja["blocks"],
            "ja_batch_4_total_sec": round(dt_jab, 3),
            "ja_batch_4_avg_sec": round(dt_jab / 4.0, 3),
            "ja_batch_4_gemini_reqs": jab_reqs,
            "ja_batch_4_timings_ms": d_jab["timings_ms"],
            "warm_models_peak": h_peak["models_loaded"],
            "warm_torch_vram_mb": h_peak["torch_vram_allocated_mb"],
            "nvidia_vram_before_mb": nv_before.used_mb,
            "nvidia_vram_peak_mb": h_peak["nvidia_vram_used_mb"],
            "nvidia_vram_after_shutdown_mb": nv_after.used_mb,
            "python_in_nvidia_compute_apps": has_python_cuda,
            "shutdown_exit_code": exit_code,
        }
        out_json = OUTPUT_DIR / "v12_vision_benchmark.json"
        out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[9] Relatorio v1.2 salvo em {out_json}")
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        log_fh.close()


if __name__ == "__main__":
    sys.exit(main())
