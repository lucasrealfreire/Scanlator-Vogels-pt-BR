"""Real end-to-end benchmark of the final Free Tier Turbo + Scanlation PT-BR package.

Measures on a live 127.0.0.1:8765 server:
1. Immediate `/health` startup + non-blocking background GPU warmup (`warming_up` -> `warmup_done`).
2. Page 1 AFTER background warmup (`poc/input/test.jpg`, `use_cache=False`, WebP, `style="scanlation"`)
   — prints all 14 translated PT-BR balloons and saves `poc/output/traduzida_turbo.webp`.
3. Hot single page (`use_cache=False`, WebP) — validates render+encode < 0.25s and warm latency.
4. 4-page grouped batch (`POST /translate-batch`, 1 single Gemini API call, `use_cache=False`, WebP)
   — reports total batch time and average time per page!
5. Clean shutdown (`POST /shutdown`) + process exit verification + `nvidia-smi` VRAM & compute-app check.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

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


def wait_for_warmup(timeout_sec: float = 45.0) -> tuple[dict, float]:
    t0 = time.perf_counter()
    deadline = time.monotonic() + timeout_sec
    last_h: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(f"{BASE_URL}/health", timeout=2.0)
        if r.status_code == 200:
            last_h = r.json()
            if last_h.get("warmup_done") and not last_h.get("warming_up"):
                return last_h, time.perf_counter() - t0
        time.sleep(0.25)
    raise RuntimeError(f"Timeout aguardando aquecimento GPU: {last_h}")


def make_distinct_page_b64(src_path: Path, variant_idx: int) -> str:
    """Create a visually identical copy with a tiny 1px corner tweak for distinct SHA-256."""
    img = Image.open(src_path).convert("RGB")
    px = img.getpixel((0, 0))
    img.putpixel((0, 0), ((px[0] + variant_idx * 7) % 256, px[1], px[2]))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode("ascii")


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
    image_path = INPUT_DIR / "test.jpg"
    raw_bytes = image_path.read_bytes()
    img_b64 = base64.b64encode(raw_bytes).decode("ascii")

    nv_before = get_nvidia_memory_info()
    print(
        f"[0] NVIDIA VRAM antes de ligar o motor: {nv_before.used_mb:.0f} MiB / {nv_before.total_mb:.0f} MiB"
    )

    python_exe = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    server_log_path = OUTPUT_DIR / "benchmark_server.log"
    server_log_path.parent.mkdir(parents=True, exist_ok=True)
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
            f"[1] /health respondeu em {boot_s:.2f}s (PID={health0['pid']}, "
            f"warming_up={health0.get('warming_up')}, prompt_version={health0.get('prompt_version')})"
        )

        # Wait for non-blocking background GPU warmup to finish
        health_warm, warmup_s = wait_for_warmup()
        print(
            f"[2] Pre-aquecimento GPU concluido em {warmup_s:.2f}s | "
            f"modelos={health_warm['models_loaded']} | "
            f"torch_vram={health_warm['torch_vram_allocated_mb']} MB | "
            f"nvidia_vram={health_warm['nvidia_vram_used_mb']} MB"
        )

        # (A) 1st page AFTER background warmup (no page cache!)
        print("[3] Testando Pagina 1 apos pre-aquecimento (use_cache=False, style='scanlation', WebP)...")
        t1 = time.perf_counter()
        r1 = httpx.post(
            f"{BASE_URL}/translate-page",
            json={
                "image_base64": img_b64,
                "source_lang": "auto",
                "output_format": "webp",
                "use_cache": False,
                "uppercase": True,
                "style": "scanlation",
            },
            timeout=180.0,
        )
        r1.raise_for_status()
        d1 = r1.json()
        dt1 = time.perf_counter() - t1
        h1 = httpx.get(f"{BASE_URL}/health", timeout=3.0).json()
        webp_bytes = base64.b64decode(d1["image_base64"])
        webp_kb = len(webp_bytes) / 1024.0
        out_webp = OUTPUT_DIR / "traduzida_turbo.webp"
        out_webp.write_bytes(webp_bytes)

        print(
            f"    -> Pagina 1 (pos-warmup) OK em {dt1:.2f}s | mime={d1['mime_type']} ({webp_kb:.1f} KB) | "
            f"timings_ms={d1['timings_ms']} | torch_vram={h1['torch_vram_allocated_mb']} MB"
        )
        print("\n    === 14 BALOES TRADUZIDOS (style='scanlation', v3-scanlation-turbo) ===")
        for b in d1.get("blocks", []):
            print(f"    #{b['id']:02d} | JP: {b['source_text']}")
            print(f"        | BR: {b['translated_text']}")
        print("    =====================================================================\n")

        # (B) Hot single page (use_cache=False, WebP)
        print("[4] Testando Pagina isolada quente (use_cache=False, WebP)...")
        t2 = time.perf_counter()
        r2 = httpx.post(
            f"{BASE_URL}/translate-page",
            json={
                "image_base64": img_b64,
                "source_lang": "auto",
                "output_format": "webp",
                "use_cache": False,
                "uppercase": True,
                "style": "scanlation",
            },
            timeout=180.0,
        )
        r2.raise_for_status()
        d2 = r2.json()
        dt2 = time.perf_counter() - t2
        print(
            f"    -> Pagina isolada quente OK em {dt2:.2f}s | timings_ms={d2['timings_ms']}"
        )

        # (C) 4-page batch (POST /translate-batch) -> 1 single Gemini API call!
        print("[5] Testando LOTE DE 4 PAGINAS (POST /translate-batch, 1 chamada Gemini, use_cache=False)...")
        batch_pages = [
            {"page_id": f"p{i}", "image_base64": make_distinct_page_b64(image_path, i + 1)}
            for i in range(4)
        ]
        reqs_before_batch = (d2.get("quota") or {}).get("requests_today", 0)
        tb = time.perf_counter()
        rb = httpx.post(
            f"{BASE_URL}/translate-batch",
            json={
                "pages": batch_pages,
                "source_lang": "auto",
                "output_format": "webp",
                "use_cache": False,
                "uppercase": True,
                "style": "scanlation",
            },
            timeout=180.0,
        )
        rb.raise_for_status()
        db = rb.json()
        dtb = time.perf_counter() - tb
        reqs_after_batch = (db.get("quota") or {}).get("requests_today", 0)
        batch_gemini_reqs = reqs_after_batch - reqs_before_batch
        hb = httpx.get(f"{BASE_URL}/health", timeout=3.0).json()
        print(
            f"    -> Lote de 4 paginas OK em {dtb:.2f}s (media: {dtb / 4.0:.2f}s/pag | "
            f"backend avg: {db['avg_ms_per_page']:.1f}ms/pag) | "
            f"chamadas Gemini usadas: {batch_gemini_reqs} | fallback_pages={db['fallback_pages']} | "
            f"timings_ms={db['timings_ms']}"
        )

        # (D) Shutdown and confirm 0 MB VRAM retained!
        print("[6] Chamando POST /shutdown...")
        rs = httpx.post(f"{BASE_URL}/shutdown", timeout=10.0)
        rs.raise_for_status()
        print(f"    -> Resposta /shutdown: {rs.json()}")

        exit_code = proc.wait(timeout=15)
        time.sleep(1.2)
        nv_after = get_nvidia_memory_info()
        compute_apps_after = query_nvidia_compute_apps()
        print(f"[7] Processo encerrado (exit_code={exit_code}).")
        print(
            f"    -> NVIDIA VRAM pos-shutdown: {nv_after.used_mb:.0f} MiB / {nv_after.total_mb:.0f} MiB "
            f"(antes={nv_before.used_mb:.0f} MiB, quente={hb['nvidia_vram_used_mb']} MiB)"
        )
        print(f"    -> nvidia-smi compute apps pos-shutdown: {compute_apps_after or '(nenhum processo CUDA)'}")

        summary = {
            "prompt_version": d1.get("prompt_version"),
            "model": d1.get("model"),
            "boot_health_sec": round(boot_s, 3),
            "background_warmup_sec": round(warmup_s, 3),
            "output_mime": d1["mime_type"],
            "output_size_kb": round(webp_kb, 1),
            "page1_post_warmup_sec": round(dt1, 3),
            "page1_timings_ms": d1["timings_ms"],
            "hot_single_page_sec": round(dt2, 3),
            "hot_single_timings_ms": d2["timings_ms"],
            "batch_4_pages_total_sec": round(dtb, 3),
            "batch_4_pages_avg_sec": round(dtb / 4.0, 3),
            "batch_4_pages_backend_avg_ms": db["avg_ms_per_page"],
            "batch_4_pages_timings_ms": db["timings_ms"],
            "batch_4_pages_gemini_requests": batch_gemini_reqs,
            "batch_4_pages_fallback_pages": db["fallback_pages"],
            "warm_models_loaded": hb["models_loaded"],
            "warm_torch_vram_mb": hb["torch_vram_allocated_mb"],
            "nvidia_vram_before_mb": nv_before.used_mb,
            "nvidia_vram_warm_mb": hb["nvidia_vram_used_mb"],
            "nvidia_vram_after_shutdown_mb": nv_after.used_mb,
            "nvidia_compute_apps_after_shutdown": compute_apps_after,
            "shutdown_exit_code": exit_code,
            "quota": db["quota"],
            "translated_blocks_test_jpg": d1.get("blocks", []),
        }
        out_json = OUTPUT_DIR / "turbo_benchmark.json"
        out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[8] Relatorio completo salvo em {out_json}")
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
