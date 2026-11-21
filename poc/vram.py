"""Strict VRAM/RAM lifecycle manager and GPU telemetry for RTX 5060 Ti (8 GB)."""

from __future__ import annotations

import gc
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

import psutil

from poc.schemas import StageMetrics

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class NvidiaMemoryInfo:
    used_mb: float
    free_mb: float
    total_mb: float


def get_nvidia_memory_info() -> Optional[NvidiaMemoryInfo]:
    """Read real GPU memory usage from NVIDIA driver via nvidia-smi."""
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        first_line = proc.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in first_line.split(",")]
        if len(parts) < 3:
            return None
        return NvidiaMemoryInfo(
            used_mb=float(parts[0]),
            free_mb=float(parts[1]),
            total_mb=float(parts[2]),
        )
    except Exception:
        return None


def get_process_ram_mb() -> float:
    """Return current process RSS memory in MiB."""
    process = psutil.Process()
    return round(process.memory_info().rss / (1024.0 * 1024.0), 2)


def get_process_peak_ram_mb() -> float:
    """Return process peak working set (Windows peak_wset) or current RSS in MiB."""
    mem = psutil.Process().memory_info()
    peak_bytes = getattr(mem, "peak_wset", mem.rss)
    return round(max(peak_bytes, mem.rss) / (1024.0 * 1024.0), 2)


def get_torch_vram_stats_mb() -> tuple[float, float, float]:
    """Return (current_allocated_mb, peak_allocated_mb, peak_reserved_mb)."""
    try:
        import torch

        if not torch.cuda.is_available():
            return (0.0, 0.0, 0.0)
        mb = 1024.0 * 1024.0
        return (
            round(torch.cuda.memory_allocated() / mb, 2),
            round(torch.cuda.max_memory_allocated() / mb, 2),
            round(torch.cuda.max_memory_reserved() / mb, 2),
        )
    except Exception:
        return (0.0, 0.0, 0.0)


def release_gpu_memory() -> None:
    """Force full release of PyTorch CUDA caches and Python garbage."""
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except Exception as exc:
        logger.debug("Ignoring CUDA cleanup error: %s", exc)
    gc.collect()


def resolve_device(preference: str = "auto", min_free_vram_mb: int = 900) -> str:
    """Pick 'cuda' or 'cpu' respecting available VRAM budget on an 8 GB shared GPU."""
    pref = preference.strip().lower()
    if pref == "cpu":
        return "cpu"

    try:
        import torch

        if not torch.cuda.is_available():
            logger.warning("CUDA indisponivel; usando CPU.")
            return "cpu"

        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
        free_mb = free_bytes / (1024.0 * 1024.0)
        total_mb = total_bytes / (1024.0 * 1024.0)

        if free_mb < min_free_vram_mb:
            logger.warning(
                "VRAM livre (%.0f MB de %.0f MB) abaixo do orcamento seguro (%d MB). "
                "Acionando fallback automatico para CPU.",
                free_mb,
                total_mb,
                min_free_vram_mb,
            )
            return "cpu"

        # Quick kernel sanity check (guards against sm_120 / Blackwell mismatches)
        test_tensor = torch.ones((4, 4), device="cuda")
        _ = (test_tensor @ test_tensor).item() if test_tensor.numel() == 1 else float(
            (test_tensor @ test_tensor).sum().item()
        )
        del test_tensor
        torch.cuda.empty_cache()
        return "cuda"
    except Exception as exc:
        logger.warning("Falha ao validar CUDA (%s); acionando fallback para CPU.", exc)
        release_gpu_memory()
        return "cpu"


def is_cuda_oom(exc: BaseException) -> bool:
    """Detect CUDA Out-Of-Memory errors across PyTorch and native extensions."""
    try:
        import torch

        if isinstance(exc, torch.cuda.OutOfMemoryError):
            return True
    except Exception:
        pass
    msg = str(exc).lower()
    return "out of memory" in msg and ("cuda" in msg or "cublas" in msg or "cudnn" in msg)


def run_with_oom_fallback(
    cuda_fn: Callable[[], T],
    cpu_fn: Callable[[], T],
    stage_name: str,
    device: str,
) -> tuple[T, str]:
    """Execute on CUDA with automatic VRAM flush and CPU fallback upon OOM."""
    if device != "cuda":
        return cpu_fn(), "cpu"
    try:
        return cuda_fn(), "cuda"
    except Exception as exc:
        if not is_cuda_oom(exc):
            raise
        logger.warning(
            "[%s] CUDA Out of Memory detectado! Liberando VRAM e reexecutando em CPU.",
            stage_name,
        )
        release_gpu_memory()
        return cpu_fn(), "cpu"


class StageProfiler:
    """Measure timing, RAM peak, PyTorch VRAM peak, and NVIDIA driver memory for a stage."""

    def __init__(self, stage_name: str) -> None:
        self.stage_name = stage_name
        self.device_used: str = "cpu"
        self.cache_hit: bool = False
        self.details: dict[str, object] = {}
        self._start_time: float = 0.0
        self._nv_before: Optional[float] = None
        self._nv_peak: Optional[float] = None
        self._ram_peak: float = 0.0
        self._torch_alloc_peak: float = 0.0
        self._torch_res_peak: float = 0.0
        self.metrics: Optional[StageMetrics] = None

    def sample_peak(self) -> None:
        """Call right before unloading a model to capture its live peak memory."""
        self._ram_peak = max(self._ram_peak, get_process_ram_mb())
        _, alloc_peak, res_peak = get_torch_vram_stats_mb()
        self._torch_alloc_peak = max(self._torch_alloc_peak, alloc_peak)
        self._torch_res_peak = max(self._torch_res_peak, res_peak)
        nv = get_nvidia_memory_info()
        if nv is not None:
            self._nv_peak = max(self._nv_peak or 0.0, nv.used_mb)

    def __enter__(self) -> "StageProfiler":
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
        nv = get_nvidia_memory_info()
        self._nv_before = nv.used_mb if nv else None
        self._nv_peak = self._nv_before
        self._ram_peak = get_process_ram_mb()
        self._start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.sample_peak()
        duration = round(time.perf_counter() - self._start_time, 4)
        release_gpu_memory()
        alloc_after, alloc_peak, res_peak = get_torch_vram_stats_mb()
        self._torch_alloc_peak = max(self._torch_alloc_peak, alloc_peak)
        self._torch_res_peak = max(self._torch_res_peak, res_peak)
        nv_after = get_nvidia_memory_info()
        self.metrics = StageMetrics(
            stage=self.stage_name,
            duration_sec=duration,
            device_used=self.device_used,
            cache_hit=self.cache_hit,
            ram_current_mb=get_process_ram_mb(),
            ram_peak_mb=round(max(self._ram_peak, get_process_ram_mb()), 2),
            vram_allocated_peak_mb=round(self._torch_alloc_peak, 2),
            vram_reserved_peak_mb=round(self._torch_res_peak, 2),
            vram_allocated_after_unload_mb=round(alloc_after, 2),
            nvidia_used_before_mb=self._nv_before,
            nvidia_used_peak_mb=self._nv_peak,
            nvidia_used_after_unload_mb=nv_after.used_mb if nv_after else None,
            details=dict(self.details),
        )
