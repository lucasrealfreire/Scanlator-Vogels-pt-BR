"""Real end-to-end test of `native_host/scanlator_host.exe` simulating exact Chrome behavior.

CRITICAL: Chrome's `chrome.runtime.sendNativeMessage` writes `[4-byte LE len][UTF-8 JSON]`
to stdin and KEEPS STDIN OPEN until the native host responds/exits. This test intentionally
keeps stdin open to prove ZERO pipe-buffering or grandchild-handle deadlocks!
"""

from __future__ import annotations

import json
import struct
import subprocess
import sys
import time
from pathlib import Path

import httpx

from poc.vram import get_nvidia_memory_info

HOST_DIR = Path(__file__).resolve().parent
RUN_HOST_BAT = HOST_DIR / "run_host.bat"
PORT = 8765


def call_native_host_like_chrome(payload: dict, timeout_sec: float = 20.0) -> tuple[dict, int, float]:
    """Send 1 framed Native Messaging request WITHOUT closing stdin (exact Chrome behavior)."""
    t0 = time.perf_counter()
    # Chrome launches .bat native hosts with CREATE_NO_WINDOW via cmd.exe /c
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    proc = subprocess.Popen(
        ["cmd.exe", "/c", str(RUN_HOST_BAT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
    )
    assert proc.stdin is not None and proc.stdout is not None
    raw = json.dumps(payload).encode("utf-8")
    proc.stdin.write(struct.pack("<I", len(raw)) + raw)
    proc.stdin.flush()
    # INTENTIONALLY DO NOT CLOSE proc.stdin HERE — exact Chrome behavior!

    # Read 4-byte response length prefix
    len_bytes = proc.stdout.read(4)
    if len(len_bytes) < 4:
        stderr_out = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        raise RuntimeError(f"Native host encerrou sem resposta (stderr={stderr_out!r})")
    resp_len = struct.unpack("<I", len_bytes)[0]
    body = proc.stdout.read(resp_len)
    if len(body) < resp_len:
        raise RuntimeError(f"Resposta truncada: esperado {resp_len}, recebido {len(body)}")

    exit_code = proc.wait(timeout=timeout_sec)
    proc.stdin.close()
    dt = time.perf_counter() - t0
    return json.loads(body.decode("utf-8")), exit_code, dt


def main() -> int:
    assert RUN_HOST_BAT.exists(), f"{RUN_HOST_BAT} nao encontrado!"

    nv_before = get_nvidia_memory_info()
    print(f"[0] NVIDIA VRAM antes: {nv_before.used_mb:.0f} MiB / {nv_before.total_mb:.0f} MiB")

    # 1. Ping (keeping stdin open!)
    ping_resp, ping_rc, ping_dt = call_native_host_like_chrome({"action": "ping"})
    print(f"[1] PING OK em {ping_dt:.2f}s (exit={ping_rc}): {ping_resp}")
    assert ping_resp.get("ok") is True and ping_rc == 0

    # 2. Start backend via scanlator_host.exe (keeping stdin open!)
    start_resp, start_rc, start_dt = call_native_host_like_chrome(
        {"action": "start", "port": PORT, "idle_timeout_sec": 300}
    )
    print(f"[2] START em {start_dt:.2f}s (exit={start_rc}): ok={start_resp.get('ok')} status={start_resp.get('status')}")
    assert start_resp.get("ok") is True, f"Start falhou: {start_resp}"
    assert start_rc == 0

    # 3. Verify GET /health directly after scanlator_host.exe already exited cleanly
    r = httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=3.0)
    assert r.status_code == 200
    h = r.json()
    print(
        f"[3] GET /health 200 OK | PID={h['pid']} | status={h['status']} | "
        f"torch_vram={h['torch_vram_allocated_mb']} MB | idle_remaining={h['idle_remaining_sec']}s"
    )

    # 4. Stop backend via scanlator_host.exe (keeping stdin open!)
    stop_resp, stop_rc, stop_dt = call_native_host_like_chrome({"action": "stop", "port": PORT})
    print(f"[4] STOP em {stop_dt:.2f}s (exit={stop_rc}): {stop_resp}")
    assert stop_resp.get("ok") is True and stop_rc == 0

    # 5. Confirm port closed & NVIDIA VRAM back to baseline
    try:
        httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=1.0)
        raise AssertionError("Servidor ainda respondeu apos STOP!")
    except httpx.HTTPError:
        pass

    time.sleep(0.5)
    nv_after = get_nvidia_memory_info()
    print(
        f"[5] Confirmado: porta {PORT} fechada | NVIDIA VRAM pos-stop={nv_after.used_mb:.0f} MiB "
        f"(antes={nv_before.used_mb:.0f} MiB)"
    )
    print(f"[6] Logs gravados em: {HOST_DIR / 'host.log'} e {HOST_DIR / 'backend.log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
