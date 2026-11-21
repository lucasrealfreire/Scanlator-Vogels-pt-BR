"""100% Silent Chrome Native Messaging Host for Scanlator Vogels PT-BR.

Starts the local FastAPI backend using regular `.venv\\Scripts\\python.exe` (NEVER
`pythonw.exe`, which sets stdout/stderr to None and breaks Uvicorn's logger/isatty)
with `CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` and explicit
redirection of stdout/stderr to `native_host/backend.log` (and host logs to
`native_host/host.log`). Marks Chrome stdin/stdout pipes non-inheritable before
spawning so the detached backend NEVER holds Chrome's pipe handles open.
"""

from __future__ import annotations

import json
import logging
import os
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

HOST_NAME = "com.scanlatorvogels.host"
HOST_VERSION = "1.2.0"
HOST_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = HOST_DIR.parent
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
BACKEND_LOG = HOST_DIR / "backend.log"
HOST_LOG = HOST_DIR / "host.log"

logging.basicConfig(
    filename=str(HOST_LOG),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("native_host")


def _read_message() -> dict[str, Any] | None:
    raw_len = sys.stdin.buffer.read(4)
    if not raw_len or len(raw_len) < 4:
        return None
    msg_len = struct.unpack("<I", raw_len)[0]
    if msg_len == 0 or msg_len > 10 * 1024 * 1024:
        return None
    raw_body = sys.stdin.buffer.read(msg_len)
    if len(raw_body) < msg_len:
        return None
    return json.loads(raw_body.decode("utf-8"))


def _send_message(payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def _http_json(url: str, *, method: str = "GET", timeout: float = 1.5) -> dict[str, Any] | None:
    req = urllib.request.Request(url, method=method)
    if method == "POST":
        req.add_header("Content-Type", "application/json")
        data = b"{}"
    else:
        data = None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return None


def _prevent_pipe_inheritance() -> None:
    """Ensure Chrome Native Messaging stdin/stdout FDs are NEVER inherited by backend."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "fileno"):
                os.set_inheritable(stream.fileno(), False)
        except Exception:
            pass


def _start_backend(port: int, idle_timeout_sec: int | None) -> dict[str, Any]:
    health_url = f"http://127.0.0.1:{port}/health"
    existing = _http_json(health_url, timeout=1.0)
    if existing is not None:
        logger.info("Backend ja estava ativo na porta %d (PID=%s).", port, existing.get("pid"))
        return {"ok": True, "status": "running", "already_running": True, "health": existing}

    if not PYTHON_EXE.exists():
        msg = f"Python .venv nao encontrado em {PYTHON_EXE}"
        logger.error(msg)
        return {"ok": False, "code": "VENV_MISSING", "message": msg}

    cmd = [
        str(PYTHON_EXE),
        "-m",
        "backend.server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    if idle_timeout_sec is not None:
        cmd.extend(["--idle-timeout", str(int(idle_timeout_sec))])

    creation_flags = 0
    startupinfo = None
    if os.name == "nt":
        # IMPORTANT: Do NOT combine DETACHED_PROCESS with CREATE_NO_WINDOW on Windows 11
        # (combining both conflicts in CreateProcess and can spawn a visible console!).
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP + SW_HIDE guarantees 0 console window
        # while allowing the server to outlive the short-lived native host cleanly.
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0  # SW_HIDE

    _prevent_pipe_inheritance()
    BACKEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Iniciando backend silencioso: %s (log=%s)", " ".join(cmd), BACKEND_LOG)

    with BACKEND_LOG.open("ab") as log_fh:
        log_fh.write(
            f"\n--- [native_host] Starting backend on 127.0.0.1:{port} ---\n".encode("utf-8")
        )
        log_fh.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            close_fds=True,
            creationflags=creation_flags,
            startupinfo=startupinfo,
        )

    # Poll /health up to 16s so Chrome gets a timely response (never hangs!)
    deadline = time.monotonic() + 16.0
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            tail = ""
            try:
                lines = BACKEND_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = " | ".join(lines[-4:])
            except Exception:
                pass
            msg = f"Motor local encerrou ao iniciar (exit={rc}). Log ({BACKEND_LOG.name}): {tail}"
            logger.error(msg)
            return {"ok": False, "code": "BACKEND_EXITED_EARLY", "message": msg}

        health = _http_json(health_url, timeout=0.8)
        if health is not None:
            logger.info("Backend pronto na porta %d (PID=%s).", port, health.get("pid"))
            return {"ok": True, "status": "running", "already_running": False, "health": health}
        time.sleep(0.25)

    msg = f"Timeout (16s) aguardando http://127.0.0.1:{port}/health. Verifique {BACKEND_LOG}."
    logger.error(msg)
    return {"ok": False, "code": "START_TIMEOUT", "message": msg}


def _stop_backend(port: int) -> dict[str, Any]:
    health_url = f"http://127.0.0.1:{port}/health"
    shutdown_url = f"http://127.0.0.1:{port}/shutdown"

    if _http_json(health_url, timeout=1.0) is None:
        logger.info("Backend ja estava parado na porta %d.", port)
        return {"ok": True, "status": "stopped", "already_stopped": True}

    logger.info("Enviando POST /shutdown para 127.0.0.1:%d...", port)
    _http_json(shutdown_url, method="POST", timeout=3.0)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if _http_json(health_url, timeout=0.5) is None:
            logger.info("Backend encerrado com sucesso.")
            return {"ok": True, "status": "stopped", "already_stopped": False}
        time.sleep(0.25)

    msg = "Pedido de desligamento enviado, mas o servidor ainda esta finalizando."
    logger.warning(msg)
    return {"ok": False, "code": "STOP_TIMEOUT", "message": msg}


def main() -> None:
    try:
        msg = _read_message()
        if msg is None:
            logger.warning("Nenhuma mensagem recebida em stdin.")
            return

        action = str(msg.get("action") or "").strip().lower()
        port = int(msg.get("port") or 8765)
        logger.info("Acao recebida: action=%s port=%d", action, port)

        if action == "ping":
            _send_message({"ok": True, "host": HOST_NAME, "version": HOST_VERSION})
        elif action == "status":
            health = _http_json(f"http://127.0.0.1:{port}/health", timeout=1.2)
            _send_message(
                {
                    "ok": True,
                    "status": "running" if health is not None else "stopped",
                    "health": health,
                }
            )
        elif action == "start":
            idle_sec = msg.get("idle_timeout_sec")
            _send_message(_start_backend(port, int(idle_sec) if idle_sec is not None else None))
        elif action == "stop":
            _send_message(_stop_backend(port))
        else:
            _send_message(
                {"ok": False, "code": "UNKNOWN_ACTION", "message": f"Acao desconhecida: {action}"}
            )
    except Exception as exc:
        logger.exception("Excecao nao tratada no native host:")
        try:
            _send_message({"ok": False, "code": "HOST_EXCEPTION", "message": str(exc)})
        except Exception:
            pass


if __name__ == "__main__":
    main()
