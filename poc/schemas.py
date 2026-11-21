"""Typed data schemas shared across Stage 1 PoC modules and ready for Stage 2 FastAPI."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """Axis-aligned pixel bounding box [x1, y1, x2, y2]."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return max(0, self.x2 - self.x1)

    @property
    def height(self) -> int:
        return max(0, self.y2 - self.y1)

    @property
    def area(self) -> int:
        return self.width * self.height

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x1, self.y1, self.x2, self.y2)

    def clamped(self, img_w: int, img_h: int) -> "BoundingBox":
        return BoundingBox(
            x1=max(0, min(self.x1, img_w)),
            y1=max(0, min(self.y1, img_h)),
            x2=max(0, min(self.x2, img_w)),
            y2=max(0, min(self.y2, img_h)),
        )

    def padded(self, margin: int, img_w: int, img_h: int) -> "BoundingBox":
        return BoundingBox(
            x1=self.x1 - margin,
            y1=self.y1 - margin,
            x2=self.x2 + margin,
            y2=self.y2 + margin,
        ).clamped(img_w, img_h)


class TextBlock(BaseModel):
    """Single detected text region / speech bubble in reading order."""

    id: int = Field(..., ge=1, description="1-based Japanese reading order index")
    bbox: BoundingBox
    lines: list[list[list[int]]] = Field(default_factory=list)
    angle: float = 0.0
    is_vertical: bool = True
    confidence: float = 1.0
    source_text: str = ""
    translated_text: str = ""
    tone: str = ""
    inpaint_method: Literal["", "opencv_solid", "lama_crop", "skipped"] = ""
    is_solid_bubble: bool = False
    bubble_bg_rgb: tuple[int, int, int] = (255, 255, 255)
    render_bbox: Optional[BoundingBox] = None


class StageMetrics(BaseModel):
    """Execution timing and RAM/VRAM telemetry for a single pipeline stage."""

    stage: str
    duration_sec: float
    device_used: str = "cpu"
    cache_hit: bool = False
    ram_current_mb: float = 0.0
    ram_peak_mb: float = 0.0
    vram_allocated_peak_mb: float = 0.0
    vram_reserved_peak_mb: float = 0.0
    vram_allocated_after_unload_mb: float = 0.0
    nvidia_used_before_mb: Optional[float] = None
    nvidia_used_peak_mb: Optional[float] = None
    nvidia_used_after_unload_mb: Optional[float] = None
    details: dict[str, object] = Field(default_factory=dict)


class QuotaSnapshot(BaseModel):
    """Local Free Tier quota usage snapshot."""

    date_utc: str
    model: str
    requests_today: int
    prompt_tokens_today: int
    output_tokens_today: int
    total_tokens_today: int
    last_request_prompt_tokens: int = 0
    last_request_output_tokens: int = 0


class BenchmarkReport(BaseModel):
    """Full Stage 1 PoC benchmark saved to `poc/output/benchmark.json`."""

    image_path: str
    image_sha256: str
    image_resolution: tuple[int, int]
    gpu_name: str
    cuda_version: str
    torch_version: str
    total_blocks_detected: int
    blocks_with_text: int
    opencv_inpainted_blocks: int
    lama_inpainted_blocks: int
    translation_executed: bool
    gemini_model: str
    total_duration_sec: float
    overall_ram_peak_mb: float
    overall_torch_vram_peak_mb: float
    nvidia_vram_start_mb: Optional[float] = None
    nvidia_vram_peak_mb: Optional[float] = None
    nvidia_vram_end_mb: Optional[float] = None
    vram_released_cleanly: bool = True
    quota: Optional[QuotaSnapshot] = None
    stages: list[StageMetrics] = Field(default_factory=list)
    blocks: list[TextBlock] = Field(default_factory=list)
