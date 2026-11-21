"""Automated tests for CircuitBreaker, error classification, Retry-After, and recovery."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

import pytest

from poc.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    InvalidJSONResponseError,
    NonRetryableAPIError,
    RetryableAPIError,
    parse_retry_after,
)


class FakeClock:
    """Deterministic monotonic clock for instant tests."""

    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeHTTPException(Exception):
    """Simulates SDK exceptions with HTTP status code and optional headers."""

    status_code: int
    message: str = "HTTP error"
    headers: dict[str, str] | None = None

    def __str__(self) -> str:
        return f"HTTP {self.status_code}: {self.message}"


def _make_breaker(
    clock: FakeClock,
    *,
    failure_threshold: int = 2,
    recovery_timeout: float = 20.0,
    max_retries: int = 2,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
) -> tuple[CircuitBreaker, list[float]]:
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    breaker = CircuitBreaker(
        CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            max_retries=max_retries,
            base_delay=base_delay,
            max_delay=max_delay,
            jitter_ratio=0.5,
        ),
        clock=clock,
        sleep_fn=fake_sleep,
        rng=random.Random(42),
    )
    return breaker, sleeps


def test_recovery_from_429_respects_retry_after_header() -> None:
    clock = FakeClock()
    breaker, sleeps = _make_breaker(clock, max_retries=2, base_delay=1.0)
    attempts = 0

    def flaky_429() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FakeHTTPException(
                status_code=429,
                message="RESOURCE_EXHAUSTED",
                headers={"Retry-After": "7"},
            )
        return "ok-after-429"

    result = breaker.execute(flaky_429)
    assert result == "ok-after-429"
    assert attempts == 2
    assert len(sleeps) == 1
    assert sleeps[0] >= 7.0
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_recovery_from_503_with_exponential_backoff_and_jitter() -> None:
    clock = FakeClock()
    breaker, sleeps = _make_breaker(clock, max_retries=3, base_delay=1.0, max_delay=16.0)
    attempts = 0

    def flaky_503() -> str:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise FakeHTTPException(status_code=503, message="Service Unavailable")
        return "ok-after-503"

    result = breaker.execute(flaky_503)
    assert result == "ok-after-503"
    assert attempts == 3
    assert len(sleeps) == 2
    # Attempt 0: base=1.0 -> [0.5, 1.0]; Attempt 1: base=2.0 -> [1.0, 2.0]
    assert 0.5 <= sleeps[0] <= 1.0
    assert 1.0 <= sleeps[1] <= 2.0
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_recovery_from_timeout() -> None:
    clock = FakeClock()
    breaker, sleeps = _make_breaker(clock, max_retries=2, base_delay=0.5)
    attempts = 0

    def flaky_timeout() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("request timed out")
        return "ok-after-timeout"

    result = breaker.execute(flaky_timeout)
    assert result == "ok-after-timeout"
    assert attempts == 2
    assert len(sleeps) == 1
    assert breaker.state == CircuitState.CLOSED


def test_opens_circuit_and_recovers_via_half_open() -> None:
    clock = FakeClock()
    breaker, _ = _make_breaker(
        clock,
        failure_threshold=2,
        recovery_timeout=15.0,
        max_retries=1,
        base_delay=0.5,
    )

    def always_500() -> str:
        raise RetryableAPIError("Internal Server Error", status_code=500)

    # First call fails retries -> failure #1 (still CLOSED)
    with pytest.raises(RetryableAPIError):
        breaker.execute(always_500)
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 1

    # Second call fails retries -> failure #2 (trips to OPEN)
    with pytest.raises(RetryableAPIError):
        breaker.execute(always_500)
    assert breaker.state == CircuitState.OPEN

    # Subsequent call MUST be blocked immediately with CIRCUIT_BREAKER_OPEN
    called = False

    def should_not_run() -> str:
        nonlocal called
        called = True
        return "unexpected"

    with pytest.raises(CircuitBreakerOpenError) as exc_info:
        breaker.execute(should_not_run)
    assert not called
    assert exc_info.value.code == "CIRCUIT_BREAKER_OPEN"
    assert exc_info.value.retry_after > 0

    # Advance past recovery_timeout -> transitions OPEN -> HALF_OPEN
    clock.advance(15.5)
    assert breaker.state == CircuitState.HALF_OPEN

    # Failed probe in HALF_OPEN -> returns immediately to OPEN (no retry storm)
    probe_calls = 0

    def failing_probe() -> str:
        nonlocal probe_calls
        probe_calls += 1
        raise FakeHTTPException(status_code=503, message="Still down")

    with pytest.raises(FakeHTTPException):
        breaker.execute(failing_probe)
    assert probe_calls == 1
    assert breaker.state == CircuitState.OPEN

    # Advance again -> HALF_OPEN -> successful probe closes circuit automatically!
    clock.advance(15.5)
    assert breaker.state == CircuitState.HALF_OPEN
    recovered = breaker.execute(lambda: "service-back")
    assert recovered == "service-back"
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_client_errors_400_401_403_404_never_open_circuit_and_never_retry(
    status_code: int,
) -> None:
    clock = FakeClock()
    breaker, sleeps = _make_breaker(clock, failure_threshold=2, max_retries=3)

    call_count = 0

    def client_error() -> str:
        nonlocal call_count
        call_count += 1
        raise FakeHTTPException(status_code=status_code, message="Client error")

    # Even after exceeding failure_threshold calls, circuit MUST stay CLOSED
    for _ in range(5):
        with pytest.raises(FakeHTTPException):
            breaker.execute(client_error)

    assert call_count == 5  # Zero retries!
    assert sleeps == []
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_invalid_json_never_opens_circuit_and_never_retries() -> None:
    clock = FakeClock()
    breaker, sleeps = _make_breaker(clock, failure_threshold=2, max_retries=3)

    def bad_json_direct() -> dict:
        return json.loads("{invalid json")

    def bad_json_wrapped() -> dict:
        raise InvalidJSONResponseError("Schema invalido na resposta do Gemini")

    for _ in range(4):
        with pytest.raises(json.JSONDecodeError):
            breaker.execute(bad_json_direct)

    for _ in range(4):
        with pytest.raises(InvalidJSONResponseError):
            breaker.execute(bad_json_wrapped)

    assert sleeps == []
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_explicit_non_retryable_error_never_opens_circuit() -> None:
    clock = FakeClock()
    breaker, sleeps = _make_breaker(clock, failure_threshold=1, max_retries=2)

    for _ in range(3):
        with pytest.raises(NonRetryableAPIError):
            breaker.execute(
                lambda: (_ for _ in ()).throw(
                    NonRetryableAPIError("API key invalida", status_code=401)
                )
            )

    assert sleeps == []
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_parse_retry_after_formats() -> None:
    assert parse_retry_after(10) == 10.0
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("14.5s") == 14.5
    assert parse_retry_after("invalid") is None


@pytest.mark.parametrize(
    "blocked_model",
    [
        "gemini-2.5-flash-lite",
        "gemini-2.5-pro",
        "gemini-3.1-pro-preview",
        "gemini-3.7-flash",
        "gemini-4-carbon",
        "gpt-4o",
    ],
)
def test_paid_or_non_whitelisted_models_are_blocked(blocked_model: str) -> None:
    from poc.config import PaidOrDisallowedModelError, validate_free_tier_model

    with pytest.raises(PaidOrDisallowedModelError):
        validate_free_tier_model(blocked_model)


def test_whitelisted_free_tier_models_allowed() -> None:
    from poc.config import DEFAULT_FREE_MODEL, FREE_TIER_MODELS, validate_free_tier_model

    assert DEFAULT_FREE_MODEL == "gemini-3.5-flash-lite"
    assert DEFAULT_FREE_MODEL in FREE_TIER_MODELS
    for model in FREE_TIER_MODELS:
        assert validate_free_tier_model(model) == model


def test_japanese_reading_order_multi_row_panels() -> None:
    from poc.detector import sort_blocks_japanese_reading_order
    from poc.schemas import BoundingBox, TextBlock

    # 2 rows x 2 columns manga page (width 1000, height 1400)
    # Expected reading order: top-right -> top-left -> bottom-right -> bottom-left
    blocks = [
        TextBlock(id=1, bbox=BoundingBox(x1=100, y1=800, x2=300, y2=1100), source_text="BL"),
        TextBlock(id=2, bbox=BoundingBox(x1=650, y1=800, x2=900, y2=1100), source_text="BR"),
        TextBlock(id=3, bbox=BoundingBox(x1=100, y1=100, x2=300, y2=400), source_text="TL"),
        TextBlock(id=4, bbox=BoundingBox(x1=650, y1=100, x2=900, y2=400), source_text="TR"),
    ]
    ordered = sort_blocks_japanese_reading_order(blocks)
    assert [b.source_text for b in ordered] == ["TR", "TL", "BR", "BL"]
    assert [b.id for b in ordered] == [1, 2, 3, 4]


