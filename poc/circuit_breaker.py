"""Production-grade Circuit Breaker with exponential backoff, jitter, and Retry-After.

Classification rules (non-negotiable):
- HTTP 400, 401, 403, 404 and invalid JSON/schema errors:
  * Fail fast and explicitly.
  * NEVER retried, NEVER increment failure counters, NEVER open the circuit.
- HTTP 429, 500, 502, 503, 504 and network/request timeouts:
  * Retried with exponential backoff + jitter (strictly respecting Retry-After).
  * Count toward opening the circuit when a call exhausts retries, and govern
    HALF_OPEN recovery probes so the system recovers automatically without
    getting permanently stuck.
"""

from __future__ import annotations

import email.utils
import json
import logging
import random
import re
import socket
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

from pydantic import ValidationError

logger = logging.getLogger(__name__)
T = TypeVar("T")

NON_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({400, 401, 403, 404})
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(RuntimeError):
    """Raised immediately when calls are rejected because the circuit is OPEN."""

    code: str = "CIRCUIT_BREAKER_OPEN"

    def __init__(self, retry_after: float, message: str | None = None) -> None:
        self.retry_after = max(0.0, retry_after)
        super().__init__(
            message
            or (
                f"CIRCUIT_BREAKER_OPEN: circuito aberto por falhas transitorias; "
                f"tente novamente em {self.retry_after:.1f}s."
            )
        )


class APIError(RuntimeError):
    """Structured API error carrying HTTP status and optional Retry-After."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.original_error = original_error


class NonRetryableAPIError(APIError):
    """Client/config/parse error (400/401/403/404 or invalid JSON). Never opens circuit."""


class InvalidJSONResponseError(NonRetryableAPIError):
    """Model returned malformed JSON or schema-incompatible payload. Never opens circuit."""


class RetryableAPIError(APIError):
    """Transient upstream/network error (429/500/503/timeout). Controls circuit state."""


@dataclass(frozen=True)
class CircuitBreakerConfig:
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    half_open_success_threshold: int = 1
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 32.0
    jitter_ratio: float = 0.5  # Delay multiplied by [1 - jitter_ratio, 1 + jitter_ratio] or equal jitter


@dataclass(frozen=True)
class ErrorClassification:
    is_transient: bool
    status_code: Optional[int]
    retry_after: Optional[float]
    reason: str


def parse_retry_after(value: Any, now_epoch: Optional[float] = None) -> Optional[float]:
    """Parse HTTP Retry-After (seconds, '15s', or HTTP-date) into delay in seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip()
    if not text:
        return None

    # Plain numeric seconds or Google RPC duration like "12.5s"
    match = re.fullmatch(r"(\d+(?:\.\d+)?)s?", text, flags=re.IGNORECASE)
    if match:
        return max(0.0, float(match.group(1)))

    # HTTP-date format (RFC 7231)
    try:
        dt = email.utils.parsedate_to_datetime(text)
        now = time.time() if now_epoch is None else now_epoch
        return max(0.0, dt.timestamp() - now)
    except Exception:
        return None


def _extract_status_code(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "code", "http_status"):
        val = getattr(exc, attr, None)
        if isinstance(val, int) and 100 <= val <= 599:
            return val
    resp = getattr(exc, "response", None)
    if resp is not None:
        for attr in ("status_code", "status"):
            val = getattr(resp, attr, None)
            if isinstance(val, int) and 100 <= val <= 599:
                return val
    # Fallback pattern matching for SDK error messages like "429 RESOURCE_EXHAUSTED"
    msg = str(exc)
    match = re.search(r"\b(400|401|403|404|429|500|502|503|504)\b", msg)
    if match:
        return int(match.group(1))
    return None


def _extract_retry_after(exc: BaseException) -> Optional[float]:
    # Direct attribute on custom error
    direct = getattr(exc, "retry_after", None)
    if direct is not None:
        parsed = parse_retry_after(direct)
        if parsed is not None:
            return parsed

    # Headers on exception or attached HTTP response
    headers_candidates = [getattr(exc, "headers", None)]
    resp = getattr(exc, "response", None)
    if resp is not None:
        headers_candidates.append(getattr(resp, "headers", None))
    for headers in headers_candidates:
        if isinstance(headers, dict) or hasattr(headers, "get"):
            for key in ("Retry-After", "retry-after", "RETRY-AFTER"):
                raw = headers.get(key)
                if raw is not None:
                    parsed = parse_retry_after(raw)
                    if parsed is not None:
                        return parsed

    # Google GenAI structured details (e.g. RetryInfo -> retryDelay: "14s")
    details = getattr(exc, "details", None)
    if details is not None:
        text = str(details)
        m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
        if m:
            return max(0.0, float(m.group(1)))

    # Text fallback: "Please retry in 12.4s"
    m_msg = re.search(r"retry\s+in\s+(\d+(?:\.\d+)?)s", str(exc), flags=re.IGNORECASE)
    if m_msg:
        return max(0.0, float(m_msg.group(1)))
    return None


def classify_exception(exc: BaseException) -> ErrorClassification:
    """Classify an exception into transient (circuit-controlling) vs non-retryable."""
    # Explicit non-retryable / parse / schema errors NEVER trip the circuit
    if isinstance(
        exc,
        (NonRetryableAPIError, InvalidJSONResponseError, json.JSONDecodeError, ValidationError),
    ):
        return ErrorClassification(
            is_transient=False,
            status_code=getattr(exc, "status_code", None),
            retry_after=None,
            reason=type(exc).__name__,
        )

    # Explicit retryable errors
    if isinstance(exc, RetryableAPIError):
        return ErrorClassification(
            is_transient=True,
            status_code=exc.status_code,
            retry_after=exc.retry_after if exc.retry_after is not None else _extract_retry_after(exc),
            reason=f"RetryableAPIError({exc.status_code})",
        )

    # Timeout & network transport errors are transient
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return ErrorClassification(
            is_transient=True,
            status_code=None,
            retry_after=None,
            reason=type(exc).__name__,
        )
    exc_type_name = type(exc).__name__.lower()
    if "timeout" in exc_type_name or "connecterror" in exc_type_name:
        return ErrorClassification(
            is_transient=True,
            status_code=None,
            retry_after=None,
            reason=type(exc).__name__,
        )

    # Inspect HTTP status code
    status_code = _extract_status_code(exc)
    if status_code in NON_RETRYABLE_STATUS_CODES:
        return ErrorClassification(
            is_transient=False,
            status_code=status_code,
            retry_after=None,
            reason=f"HTTP_{status_code}",
        )
    if status_code in RETRYABLE_STATUS_CODES:
        return ErrorClassification(
            is_transient=True,
            status_code=status_code,
            retry_after=_extract_retry_after(exc),
            reason=f"HTTP_{status_code}",
        )

    # Unknown programming/config error: fail explicitly without falsely opening circuit
    return ErrorClassification(
        is_transient=False,
        status_code=status_code,
        retry_after=None,
        reason=f"Unhandled({type(exc).__name__})",
    )


class CircuitBreaker:
    """Thread-safe Circuit Breaker with exponential backoff + jitter + Retry-After."""

    def __init__(
        self,
        config: Optional[CircuitBreakerConfig] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.config = config or CircuitBreakerConfig()
        self._clock = clock
        self._sleep = sleep_fn
        self._rng = rng or random.Random()
        self._lock = threading.RLock()

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._half_open_successes: int = 0
        self._opened_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._sleep_history: list[float] = []

    @property
    def state(self) -> CircuitState:
        """Current circuit state (lazily promotes OPEN -> HALF_OPEN after recovery_timeout)."""
        with self._lock:
            self._maybe_transition_open_to_half_open()
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    @property
    def sleep_history(self) -> list[float]:
        """Recorded backoff durations (useful for telemetry and deterministic testing)."""
        with self._lock:
            return list(self._sleep_history)

    def time_until_recovery(self) -> float:
        """Seconds remaining until an OPEN circuit transitions to HALF_OPEN (0.0 otherwise)."""
        with self._lock:
            self._maybe_transition_open_to_half_open()
            if self._state != CircuitState.OPEN or self._opened_at is None:
                return 0.0
            elapsed = self._clock() - self._opened_at
            return max(0.0, self.config.recovery_timeout - elapsed)

    def reset(self) -> None:
        """Reset circuit breaker back to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._half_open_successes = 0
            self._opened_at = None
            self._last_error = None

    def _maybe_transition_open_to_half_open(self) -> None:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = self._clock() - self._opened_at
            if elapsed >= self.config.recovery_timeout:
                logger.info(
                    "Circuit breaker transitioning OPEN -> HALF_OPEN after %.1fs.",
                    elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_successes = 0

    def _compute_backoff(self, attempt: int, retry_after: Optional[float]) -> float:
        """Compute exponential backoff with equal jitter, strictly respecting Retry-After."""
        exp_delay = min(self.config.max_delay, self.config.base_delay * (2**attempt))
        # Equal jitter: [0.5 * exp_delay, 1.0 * exp_delay] when jitter_ratio=0.5
        low = exp_delay * max(0.0, 1.0 - self.config.jitter_ratio)
        jittered = low + self._rng.random() * (exp_delay - low)
        if retry_after is not None:
            return max(retry_after, jittered)
        return jittered

    def _check_entry(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_open_to_half_open()
            if self._state == CircuitState.OPEN:
                elapsed = self._clock() - (self._opened_at or 0.0)
                remaining = max(0.0, self.config.recovery_timeout - elapsed)
                raise CircuitBreakerOpenError(retry_after=remaining)
            return self._state

    def _record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.config.half_open_success_threshold:
                    logger.info("Circuit breaker recovered: HALF_OPEN -> CLOSED.")
                    self._state = CircuitState.CLOSED
                    self._consecutive_failures = 0
                    self._half_open_successes = 0
                    self._opened_at = None
            else:
                self._consecutive_failures = 0

    def _record_transient_failure(self, reason: str, was_half_open: bool) -> None:
        with self._lock:
            self._last_error = reason
            if was_half_open:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()
                self._half_open_successes = 0
                logger.warning(
                    "Circuit breaker probe failed (%s): HALF_OPEN -> OPEN for %.1fs.",
                    reason,
                    self.config.recovery_timeout,
                )
                return

            self._consecutive_failures += 1
            if self._consecutive_failures >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()
                logger.warning(
                    "Circuit breaker tripped CLOSED -> OPEN after %d consecutive failures (%s).",
                    self._consecutive_failures,
                    reason,
                )

    def execute(self, fn: Callable[[], T]) -> T:
        """Execute `fn` under Circuit Breaker protection with retry/backoff for transient errors."""
        entry_state = self._check_entry()
        is_half_open_probe = entry_state == CircuitState.HALF_OPEN
        # In HALF_OPEN we perform a single probe attempt without hammering upstream
        max_attempts = 1 if is_half_open_probe else (self.config.max_retries + 1)

        last_exc: Optional[BaseException] = None
        last_reason = "unknown"

        for attempt in range(max_attempts):
            try:
                result = fn()
                self._record_success()
                return result
            except Exception as exc:
                classification = classify_exception(exc)
                if not classification.is_transient:
                    # 400/401/403/404 or JSON/schema error: NEVER open circuit, fail fast!
                    logger.error(
                        "Erro nao transitorio (%s, status=%s): falhando sem abrir circuito.",
                        classification.reason,
                        classification.status_code,
                    )
                    raise

                last_exc = exc
                last_reason = classification.reason

                if attempt < max_attempts - 1:
                    delay = self._compute_backoff(attempt, classification.retry_after)
                    with self._lock:
                        self._sleep_history.append(delay)
                    logger.warning(
                        "Erro transitorio (%s, tentativa %d/%d). Aguardando %.2fs (Retry-After=%s).",
                        classification.reason,
                        attempt + 1,
                        max_attempts,
                        delay,
                        classification.retry_after,
                    )
                    self._sleep(delay)

        # All allowed attempts failed due to transient errors
        self._record_transient_failure(last_reason, was_half_open=is_half_open_probe)
        assert last_exc is not None
        raise last_exc
