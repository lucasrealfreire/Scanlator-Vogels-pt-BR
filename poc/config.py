"""Central configuration for Scanlator Vogels PT-BR (Stage 1 PoC & Stage 2 readiness)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# Project root & directories (Path handles spaces in Windows paths cleanly)
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
POC_DIR: Final[Path] = PROJECT_ROOT / "poc"
INPUT_DIR: Final[Path] = POC_DIR / "input"
OUTPUT_DIR: Final[Path] = POC_DIR / "output"
CACHE_DIR: Final[Path] = POC_DIR / "cache"
MODELS_DIR: Final[Path] = POC_DIR / "models"
ASSETS_DIR: Final[Path] = POC_DIR / "assets"
FONTS_DIR: Final[Path] = ASSETS_DIR / "fonts"

# Persist NVIDIA PTX->SASS JIT cache locally (avoids Blackwell sm_120 cold JIT stalls across runs)
_CUDA_JIT_CACHE = CACHE_DIR / "cuda_jit"
_CUDA_JIT_CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("CUDA_CACHE_PATH", str(_CUDA_JIT_CACHE))
os.environ.setdefault("CUDA_CACHE_MAXSIZE", "2147483648")

# Explicit Free Tier model whitelist — NO paid models allowed, NO silent fallback.
# Verified live via `client.models.list()` and official Google AI Studio Free Tier pricing.
FREE_TIER_MODELS: Final[frozenset[str]] = frozenset(
    {
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
    }
)

DEFAULT_FREE_MODEL: Final[str] = "gemini-3.5-flash-lite"


class PaidOrDisallowedModelError(ValueError):
    """Raised when a model outside the explicit Free Tier whitelist is requested."""


def validate_free_tier_model(model_name: str) -> str:
    """Validate that the requested Gemini model is explicitly in the Free Tier whitelist."""
    cleaned = model_name.strip()
    if cleaned not in FREE_TIER_MODELS:
        allowed = ", ".join(sorted(FREE_TIER_MODELS))
        raise PaidOrDisallowedModelError(
            f"Modelo '{cleaned}' bloqueado pela politica de CUSTO ZERO (R$ 0,00). "
            f"Apenas modelos da whitelist gratuita sao permitidos: [{allowed}]. "
            "Fallback automatico para modelos pagos e estritamente proibido."
        )
    return cleaned


@dataclass(frozen=True)
class ServiceLifecycleConfig:
    """Contract for Stage 2 on-demand local microservice (zero background usage)."""

    host: str = "127.0.0.1"
    port: int = 8765
    idle_shutdown_seconds: int = 300
    startup_mode: str = "on_demand_native_messaging"
    autostart_with_windows: bool = False


@dataclass(frozen=True)
class PoCConfig:
    """Runtime settings for Stage 1 PoC pipeline."""

    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_FREE_MODEL
    gemini_timeout_seconds: float = 45.0
    device_preference: str = "auto"  # auto | cuda | cpu
    min_free_vram_mb: int = 900
    inpaint_crop_margin: int = 40
    inpaint_max_crop_side: int = 768
    service: ServiceLifecycleConfig = field(default_factory=ServiceLifecycleConfig)

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "PoCConfig":
        """Load configuration from `.env` and environment variables."""
        dotenv_file = env_path or (PROJECT_ROOT / ".env")
        if dotenv_file.exists():
            load_dotenv(dotenv_path=dotenv_file, override=False)

        model = validate_free_tier_model(
            os.getenv("GEMINI_MODEL", DEFAULT_FREE_MODEL)
        )
        device = os.getenv("SCANLATOR_DEVICE", "auto").strip().lower()
        if device not in {"auto", "cuda", "cpu"}:
            device = "auto"

        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=model,
            gemini_timeout_seconds=float(os.getenv("GEMINI_TIMEOUT_SECONDS", "45")),
            device_preference=device,
            min_free_vram_mb=int(os.getenv("SCANLATOR_MIN_FREE_VRAM_MB", "900")),
            service=ServiceLifecycleConfig(
                host=os.getenv("SCANLATOR_HOST", "127.0.0.1"),
                port=int(os.getenv("SCANLATOR_PORT", "8765")),
                idle_shutdown_seconds=int(
                    os.getenv("SCANLATOR_IDLE_SHUTDOWN_SECONDS", "300")
                ),
            ),
        )


def ensure_runtime_dirs() -> None:
    """Create local runtime directories if missing."""
    for directory in (INPUT_DIR, OUTPUT_DIR, CACHE_DIR, MODELS_DIR, FONTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
