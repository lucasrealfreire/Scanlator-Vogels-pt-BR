"""FastAPI local on-demand microservice bound strictly to 127.0.0.1.

Endpoints:
- GET  /health                -> Motor status, warming_up flag, VRAM/RAM, Circuit Breaker, Free Tier quota.
- POST /prepare-page          -> Local-only GPU prep (detect+OCR+inpaint) into SHA-256 cache for prefetch.
- POST /translate-page        -> Single-page translation (multipart or Base64 JSON, fast WebP).
- POST /translate-batch       -> Multi-page batch translation (up to 4 pages in 1 Gemini Free Tier call!).
- POST /circuit-breaker/reset -> Controlled UI probe/reset for Circuit Breaker.
- POST /config/idle-timeout   -> Update auto-shutdown inactivity timeout live.
- POST /cache/clear           -> Clear local SHA-256 disk cache.
- POST /shutdown              -> Cancel warmup, release 100% VRAM and terminate process cleanly.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.engine import MAX_BATCH_PAGES, IdleWatchdog, WarmScanlationEngine
from poc.circuit_breaker import (
    CircuitBreakerOpenError,
    InvalidJSONResponseError,
    NonRetryableAPIError,
    classify_exception,
)
from poc.config import PaidOrDisallowedModelError, PoCConfig
from poc.translator import PROMPT_VERSION, MissingAPIKeyError
from poc.vram import get_nvidia_memory_info, get_torch_vram_stats_mb, release_gpu_memory

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 25 * 1024 * 1024


class IdleTimeoutRequest(BaseModel):
    idle_timeout_sec: int = Field(..., ge=30, le=3600)


class PreparePageRequest(BaseModel):
    image_base64: str
    source_lang: str = "auto"
    use_cache: bool = True


class Base64TranslateRequest(BaseModel):
    image_base64: str
    model: Optional[str] = None
    source_lang: str = "auto"
    style: str = "scanlation"
    uppercase: bool = True
    output_format: str = "webp"
    force_retranslate: bool = False
    use_cache: bool = True
    api_key: Optional[str] = None
    glossary: Optional[dict[str, str]] = None
    idle_timeout_sec: Optional[int] = None


class BatchPageInput(BaseModel):
    page_id: str = ""
    image_base64: str
    force_retranslate: bool = False


class BatchTranslateRequest(BaseModel):
    pages: list[BatchPageInput] = Field(..., min_length=1, max_length=MAX_BATCH_PAGES)
    model: Optional[str] = None
    source_lang: str = "auto"
    style: str = "scanlation"
    uppercase: bool = True
    output_format: str = "webp"
    use_cache: bool = True
    api_key: Optional[str] = None
    glossary: Optional[dict[str, str]] = None
    idle_timeout_sec: Optional[int] = None


def _decode_base64_image(data_str: str) -> bytes:
    cleaned = data_str.strip()
    if "," in cleaned and cleaned.lower().startswith("data:"):
        cleaned = cleaned.split(",", 1)[1]
    try:
        raw = base64.b64decode(cleaned, validate=True)
    except Exception as exc:
        raise ValueError(f"Base64 de imagem invalido: {exc}") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Imagem vazia ou excede limite de 25 MB.")
    return raw


def _parse_bool(val: Any, default: bool = True) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _map_translation_error(eng: WarmScanlationEngine, exc: Exception) -> JSONResponse:
    if isinstance(exc, PaidOrDisallowedModelError):
        return JSONResponse(
            status_code=400,
            content={"code": "PAID_MODEL_BLOCKED", "message": str(exc)},
        )
    if isinstance(exc, MissingAPIKeyError):
        return JSONResponse(
            status_code=401,
            content={"code": "MISSING_API_KEY", "message": str(exc)},
        )
    if isinstance(exc, CircuitBreakerOpenError):
        return JSONResponse(
            status_code=503,
            content={
                "code": "CIRCUIT_BREAKER_OPEN",
                "message": str(exc),
                "retry_after_sec": round(exc.retry_after, 1),
                "circuit_breaker": eng.get_circuit_breaker_status(),
            },
        )
    if isinstance(exc, InvalidJSONResponseError):
        return JSONResponse(
            status_code=502,
            content={
                "code": "INVALID_MODEL_JSON",
                "message": str(exc),
                "circuit_breaker": eng.get_circuit_breaker_status(),
            },
        )
    if isinstance(exc, NonRetryableAPIError):
        return JSONResponse(
            status_code=exc.status_code or 400,
            content={
                "code": f"API_HTTP_{exc.status_code or 400}",
                "message": str(exc),
                "circuit_breaker": eng.get_circuit_breaker_status(),
            },
        )
    if isinstance(exc, ValueError):
        return JSONResponse(
            status_code=400,
            content={"code": "INVALID_IMAGE", "message": str(exc)},
        )

    cls_info = classify_exception(exc)
    http_status = cls_info.status_code or (503 if cls_info.is_transient else 500)
    code = (
        "RATE_LIMITED_429"
        if http_status == 429
        else ("UPSTREAM_UNAVAILABLE" if cls_info.is_transient else "INTERNAL_ERROR")
    )
    logger.exception("Erro ao traduzir pagina/lote (%s): %s", code, exc)
    return JSONResponse(
        status_code=http_status,
        content={
            "code": code,
            "message": str(exc),
            "retry_after_sec": cls_info.retry_after,
            "circuit_breaker": eng.get_circuit_breaker_status(),
        },
    )


def create_app(
    *,
    engine: Optional[WarmScanlationEngine] = None,
    port: int = 8765,
    on_shutdown_request: Optional[Callable[[], None]] = None,
    enable_watchdog: bool = True,
    enable_background_warmup: bool = False,
) -> FastAPI:
    eng = engine or WarmScanlationEngine()
    watchdog: Optional[IdleWatchdog] = None

    def _trigger_shutdown() -> None:
        if on_shutdown_request is not None:
            on_shutdown_request()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal watchdog
        if enable_watchdog and on_shutdown_request is not None:
            watchdog = IdleWatchdog(eng, on_timeout=_trigger_shutdown)
            watchdog.start()
        if enable_background_warmup:
            eng.start_background_warmup()
        try:
            yield
        finally:
            if watchdog is not None:
                watchdog.stop()
            eng.unload_all()
            release_gpu_memory()

    app = FastAPI(
        title="Scanlator Vogels PT-BR — Motor Local Sob Demanda",
        version="0.4.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        ],
        allow_origin_regex=r"^(chrome-extension://[a-z]{32}|http://(127\.0\.0\.1|localhost)(:\d+)?)$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return eng.get_health_snapshot(port=port)

    @app.post("/circuit-breaker/reset")
    def reset_circuit_breaker() -> dict[str, Any]:
        cb = eng.reset_circuit_breaker()
        return {"ok": True, "circuit_breaker": cb}

    @app.post("/config/idle-timeout")
    def update_idle_timeout(body: IdleTimeoutRequest) -> dict[str, Any]:
        updated = eng.set_idle_timeout(body.idle_timeout_sec)
        return {
            "ok": True,
            "idle_timeout_sec": updated,
            "idle_remaining_sec": round(eng.idle_remaining_sec(), 1),
        }

    @app.post("/cache/clear")
    def clear_cache() -> dict[str, Any]:
        removed = eng.clear_disk_cache()
        return {"ok": True, "removed_pages": removed}

    @app.post("/shutdown")
    def shutdown() -> dict[str, Any]:
        eng.unload_all()
        alloc_after, _, _ = get_torch_vram_stats_mb()
        nv = get_nvidia_memory_info()
        if on_shutdown_request is not None:
            threading.Timer(0.25, _trigger_shutdown).start()
        return {
            "ok": True,
            "status": "shutting_down",
            "torch_vram_allocated_mb": alloc_after,
            "nvidia_vram_used_mb": nv.used_mb if nv else None,
        }

    @app.post("/prepare-page")
    async def prepare_page(body: PreparePageRequest):
        try:
            raw_bytes = _decode_base64_image(body.image_base64)
            prep = await run_in_threadpool(
                eng.prepare_image_bytes,
                raw_bytes,
                source_lang=body.source_lang,
                use_cache=body.use_cache,
            )
            return {
                "ok": True,
                "image_sha256": prep.image_sha256,
                "cache_hit_local": prep.cache_hit_local,
                "no_text": prep.no_text,
                "blocks_count": prep.blocks_count,
                "source_lang": prep.source_lang,
                "detected_lang": prep.detected_lang,
                "timings_ms": prep.timings_ms,
            }
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"code": "INVALID_IMAGE", "message": str(exc)},
            )
        except Exception as exc:
            logger.exception("Erro ao preparar pagina no prefetch: %s", exc)
            return JSONResponse(
                status_code=500,
                content={"code": "PREPARE_ERROR", "message": str(exc)},
            )

    @app.post("/translate-page")
    async def translate_page(request: Request):
        content_type = (request.headers.get("content-type") or "").lower()

        raw_bytes: bytes
        model: Optional[str] = None
        source_lang: str = "auto"
        style: str = "scanlation"
        uppercase: bool = True
        output_format: str = "webp"
        force_retranslate: bool = False
        use_cache: bool = True
        api_key: Optional[str] = None
        glossary: Optional[dict[str, str]] = None
        idle_timeout_sec: Optional[int] = None

        try:
            if "multipart/form-data" in content_type:
                form = await request.form()
                upload = form.get("file") or form.get("image")
                if upload is None or not hasattr(upload, "read"):
                    return JSONResponse(
                        status_code=400,
                        content={
                            "code": "INVALID_INPUT",
                            "message": "Envie o campo multipart `file` com a imagem da pagina.",
                        },
                    )
                raw_bytes = await upload.read()
                if not raw_bytes or len(raw_bytes) > MAX_IMAGE_BYTES:
                    return JSONResponse(
                        status_code=400,
                        content={
                            "code": "INVALID_IMAGE_SIZE",
                            "message": "Imagem vazia ou acima do limite de 25 MB.",
                        },
                    )
                model = str(form.get("model")).strip() if form.get("model") else None
                source_lang = str(form.get("source_lang") or "auto")
                style = str(form.get("style") or "scanlation")
                uppercase = _parse_bool(form.get("uppercase"), True)
                output_format = str(form.get("output_format") or "webp")
                force_retranslate = _parse_bool(form.get("force_retranslate"), False)
                use_cache = _parse_bool(form.get("use_cache"), True)
                api_key = str(form.get("api_key")).strip() if form.get("api_key") else None
                if form.get("idle_timeout_sec"):
                    idle_timeout_sec = int(str(form.get("idle_timeout_sec")))
                if form.get("glossary"):
                    glossary = json.loads(str(form.get("glossary")))
            else:
                body_raw = await request.json()
                parsed = Base64TranslateRequest.model_validate(body_raw)
                raw_bytes = _decode_base64_image(parsed.image_base64)
                model = parsed.model
                source_lang = parsed.source_lang
                style = parsed.style
                uppercase = parsed.uppercase
                output_format = parsed.output_format
                force_retranslate = parsed.force_retranslate
                use_cache = parsed.use_cache
                api_key = parsed.api_key
                glossary = parsed.glossary
                idle_timeout_sec = parsed.idle_timeout_sec
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"code": "INVALID_REQUEST", "message": str(exc)},
            )

        if idle_timeout_sec is not None:
            eng.set_idle_timeout(idle_timeout_sec)

        try:
            result = await run_in_threadpool(
                eng.translate_image_bytes,
                raw_bytes,
                model=model,
                source_lang=source_lang,
                style=style,
                uppercase=uppercase,
                output_format=output_format,
                force_retranslate=force_retranslate,
                use_cache=use_cache,
                api_key_override=api_key,
                glossary=glossary,
            )
        except Exception as exc:
            return _map_translation_error(eng, exc)

        alloc_mb, peak_mb, _ = get_torch_vram_stats_mb()
        nv = get_nvidia_memory_info()
        img_b64 = base64.b64encode(result.image_bytes).decode("ascii")

        return {
            "ok": True,
            "prompt_version": PROMPT_VERSION,
            "mime_type": result.mime_type,
            "image_base64": img_b64,
            "image_sha256": result.image_sha256,
            "width": result.width,
            "height": result.height,
            "no_text": result.no_text,
            "blocks_count": result.blocks_count,
            "translated_blocks_count": result.translated_blocks_count,
            "cache_hit_local": result.cache_hit_local,
            "model": result.model,
            "source_lang": result.source_lang,
            "detected_lang": result.detected_lang,
            "style": result.style,
            "uppercase": result.uppercase,
            "devices": {
                "deteccao": result.device_detector,
                "ocr": result.device_ocr,
                "inpainting": result.device_inpaint,
            },
            "timings_ms": result.timings_ms,
            "vram": {
                "torch_allocated_mb": alloc_mb,
                "torch_peak_mb": peak_mb,
                "nvidia_used_mb": nv.used_mb if nv else None,
            },
            "circuit_breaker": eng.get_circuit_breaker_status(),
            "quota": result.quota.model_dump() if result.quota else None,
            "blocks": [
                {
                    "id": b.id,
                    "source_text": b.source_text,
                    "translated_text": b.translated_text,
                    "inpaint_method": b.inpaint_method,
                }
                for b in result.blocks
            ],
        }

    @app.post("/translate-batch")
    async def translate_batch(body: BatchTranslateRequest):
        """Translate up to 4 sequential manga pages in ONE grouped Gemini Free Tier call."""
        if body.idle_timeout_sec is not None:
            eng.set_idle_timeout(body.idle_timeout_sec)

        decoded_items: list[tuple[str, bytes, bool]] = []
        try:
            for idx, page in enumerate(body.pages):
                raw = _decode_base64_image(page.image_base64)
                decoded_items.append((page.page_id or f"p{idx}", raw, page.force_retranslate))
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={"code": "INVALID_IMAGE", "message": str(exc)},
            )

        try:
            batch_res = await run_in_threadpool(
                eng.translate_batch_bytes,
                decoded_items,
                model=body.model,
                source_lang=body.source_lang,
                style=body.style,
                uppercase=body.uppercase,
                output_format=body.output_format,
                use_cache=body.use_cache,
                api_key_override=body.api_key,
                glossary=body.glossary,
            )
        except Exception as exc:
            return _map_translation_error(eng, exc)

        alloc_mb, peak_mb, _ = get_torch_vram_stats_mb()
        nv = get_nvidia_memory_info()

        return {
            "ok": True,
            "prompt_version": batch_res.prompt_version,
            "model": batch_res.model,
            "source_lang": batch_res.source_lang,
            "style": batch_res.style,
            "uppercase": batch_res.uppercase,
            "pages_count": len(batch_res.pages),
            "fallback_pages": batch_res.fallback_pages,
            "timings_ms": batch_res.timings_ms,
            "avg_ms_per_page": batch_res.avg_ms_per_page,
            "vram": {
                "torch_allocated_mb": alloc_mb,
                "torch_peak_mb": peak_mb,
                "nvidia_used_mb": nv.used_mb if nv else None,
            },
            "circuit_breaker": eng.get_circuit_breaker_status(),
            "quota": batch_res.quota.model_dump() if batch_res.quota else None,
            "pages": [
                {
                    "page_id": p.page_id,
                    "mime_type": p.mime_type,
                    "image_base64": base64.b64encode(p.image_bytes).decode("ascii"),
                    "image_sha256": p.image_sha256,
                    "width": p.width,
                    "height": p.height,
                    "no_text": p.no_text,
                    "blocks_count": p.blocks_count,
                    "translated_blocks_count": p.translated_blocks_count,
                    "cache_hit_local": p.cache_hit_local,
                    "detected_lang": p.detected_lang,
                    "used_fallback": p.used_fallback,
                    "timings_ms": p.timings_ms,
                    "blocks": [
                        {
                            "id": b.id,
                            "source_text": b.source_text,
                            "translated_text": b.translated_text,
                            "inpaint_method": b.inpaint_method,
                        }
                        for b in p.blocks
                    ],
                }
                for p in batch_res.pages
            ],
        }

    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    idle_timeout_sec: Optional[int] = None,
    enable_warmup: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Seguranca: o microserviço local so pode escutar em 127.0.0.1.")

    config = PoCConfig.from_env()
    engine = WarmScanlationEngine(config)
    if idle_timeout_sec is not None:
        engine.set_idle_timeout(idle_timeout_sec)

    server_ref: dict[str, Optional[uvicorn.Server]] = {"server": None}

    def _graceful_exit() -> None:
        engine.unload_all()
        srv = server_ref.get("server")
        if srv is not None:
            srv.should_exit = True

        def _force_if_hung() -> None:
            time.sleep(3.5)
            engine.unload_all()
            os._exit(0)

        threading.Thread(target=_force_if_hung, daemon=True).start()

    app = create_app(
        engine=engine,
        port=port,
        on_shutdown_request=_graceful_exit,
        enable_watchdog=True,
        enable_background_warmup=enable_warmup,
    )
    uvi_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        use_colors=False,
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(uvi_config)
    server_ref["server"] = server
    try:
        server.run()
    finally:
        engine.unload_all()
        release_gpu_memory()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scanlator Vogels PT-BR — Microservico Local Sob Demanda (127.0.0.1)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host loopback (padrao: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Porta TCP (padrao: .env ou 8765)")
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        help="Auto-desligamento por inatividade em segundos (padrao: 300)",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Desativa o pre-aquecimento em segundo plano na GPU ao iniciar",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    cfg = PoCConfig.from_env()
    port = args.port or cfg.service.port
    run_server(
        host=args.host,
        port=port,
        idle_timeout_sec=args.idle_timeout,
        enable_warmup=not args.no_warmup,
    )


if __name__ == "__main__":
    main()
