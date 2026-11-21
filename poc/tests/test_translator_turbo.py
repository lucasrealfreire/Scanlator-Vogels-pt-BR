"""Automated tests for v3-scanlation-turbo translator:
- Natural PT-BR scanlation system prompt & style temperatures
- Ultra-lean single-page and multi-page batch JSON parsing
- Zero thinking overhead (`thinking_budget=0`) + graceful fallback when rejected
- Isolated per-page fallback on incomplete batch JSON (preserving translated pages)
- Glued-word micro-repair inside batch translation
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poc.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from poc.config import DEFAULT_FREE_MODEL
from poc.schemas import BoundingBox, TextBlock
from poc.translator import (
    PROMPT_VERSION,
    SCANLATION_SYSTEM_PROMPT,
    STYLE_TEMPERATURES,
    GeminiFreeTierTranslator,
    QuotaTracker,
    _MODEL_THINKING_MODE,
    _parse_lean_batch_json,
    _parse_lean_single_page_json,
    build_system_prompt,
    get_style_temperature,
)


@dataclass
class _FakeUsage:
    prompt_token_count: int = 180
    candidates_token_count: int = 60


@dataclass
class _FakeResponse:
    text: str
    usage_metadata: _FakeUsage = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.usage_metadata is None:
            self.usage_metadata = _FakeUsage()


class _FakeModelsAPI:
    def __init__(self, handler) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: str, config: Any):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._handler(model, contents, config, len(self.calls))


class _FakeClient:
    def __init__(self, handler) -> None:
        self.models = _FakeModelsAPI(handler)


def _block(bid: int, src: str) -> TextBlock:
    return TextBlock(id=bid, bbox=BoundingBox(x1=10, y1=10, x2=100, y2=100), source_text=src)


def test_prompt_version_temperatures_and_scanlation_contrast_examples() -> None:
    assert PROMPT_VERSION == "v3-scanlation-turbo"
    assert STYLE_TEMPERATURES["scanlation"] == 0.50
    assert STYLE_TEMPERATURES["informal"] == 0.65
    assert STYLE_TEMPERATURES["fiel"] == 0.20
    assert STYLE_TEMPERATURES["formal"] == 0.20
    assert get_style_temperature("scanlation") == 0.50
    assert get_style_temperature("informal") == 0.65
    assert get_style_temperature("fiel") == 0.20
    assert get_style_temperature("formal") == 0.20

    prompt = build_system_prompt("scanlation")
    # Ensure all 5 required contrast examples are present verbatim
    assert "Tá falando do quê?" in SCANLATION_SYSTEM_PROMPT
    assert "Como assim?" in SCANLATION_SYSTEM_PROMPT
    assert "Já tô colando aí perto." in SCANLATION_SYSTEM_PROMPT
    assert "Tem uns poréns..." in SCANLATION_SYSTEM_PROMPT
    assert "Lógico que já tem uma galera casada, com família e tal, mas..." in SCANLATION_SYSTEM_PROMPT
    assert "SCANLATION BR" in prompt


def test_parse_lean_single_and_batch_json() -> None:
    single = _parse_lean_single_page_json('{"1": "Tá falando do quê?", "2": "Como assim?"}')
    assert single == {1: "Tá falando do quê?", 2: "Como assim?"}

    batch = _parse_lean_batch_json(
        '```json\n{"p0": {"1": "E aí?", "2": "Bora logo!"}, "p1": {"1": "Peraí!"}}\n```'
    )
    assert batch == {
        "p0": {1: "E aí?", 2: "Bora logo!"},
        "p1": {1: "Peraí!"},
    }


def test_thinking_config_minimal_or_zero_and_graceful_fallback_on_rejection(
    tmp_path: Path,
) -> None:
    _MODEL_THINKING_MODE.pop(DEFAULT_FREE_MODEL, None)
    try:
        def handler(_model, _contents, config, call_num):
            tc = getattr(config, "thinking_config", None)
            if call_num == 1:
                assert tc is not None
                assert str(getattr(tc, "thinking_level", "")).endswith("MINIMAL")
                raise Exception("400 INVALID_ARGUMENT: Request contains an invalid argument.")
            if call_num == 2:
                assert tc is not None
                assert getattr(tc, "thinking_budget", None) == 0
                raise Exception("400 INVALID_ARGUMENT: thinking_budget is not supported")
            assert tc is None
            return _FakeResponse(text='{"1": "Tá falando do quê?"}')

        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, max_retries=1))
        tr = GeminiFreeTierTranslator(
            api_key="fake-key",
            model=DEFAULT_FREE_MODEL,
            circuit_breaker=cb,
            quota_tracker=QuotaTracker(tmp_path / "quota.json"),
        )
        fake = _FakeClient(handler)
        tr._client = fake

        blocks = [_block(1, "What are you talking about?")]
        out, snap = tr.translate_page(blocks, source_lang="en", style="scanlation")
        assert out[0].translated_text == "Tá falando do quê?"
        assert len(fake.models.calls) == 3
        assert fake.models.calls[0]["config"].temperature == 0.50
        # 400 rejections MUST NOT trip the Circuit Breaker!
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0
        assert snap is not None and snap.requests_today == 1
        assert _MODEL_THINKING_MODE[DEFAULT_FREE_MODEL] == "none"
    finally:
        _MODEL_THINKING_MODE.pop(DEFAULT_FREE_MODEL, None)


def test_translate_pages_batch_partial_fallback_preserves_translated_pages(
    tmp_path: Path,
) -> None:
    _MODEL_THINKING_MODE.pop(DEFAULT_FREE_MODEL, None)

    def handler(_model, contents, _config, call_num):
        if call_num == 1:
            # First call is the 3-page batch: intentionally OMIT p1 to verify isolated fallback!
            assert '"p0"' in contents and '"p1"' in contents and '"p2"' in contents
            return _FakeResponse(
                text=json.dumps(
                    {
                        "p0": {"1": "Já tô colando aí perto.", "2": "Bora!"},
                        "p2": {"1": "É complicado..."},
                    },
                    ensure_ascii=False,
                )
            )
        # Second call MUST be an isolated single-page fallback ONLY for p1!
        assert "What do you mean?" in contents
        assert '"p0"' not in contents
        return _FakeResponse(text='{"1": "Como assim?"}')

    tr = GeminiFreeTierTranslator(
        api_key="fake-key",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "quota.json"),
    )
    fake = _FakeClient(handler)
    tr._client = fake

    p0 = [_block(1, "I'm heading to your area now."), _block(2, "Let's go!")]
    p1 = [_block(1, "What do you mean?")]
    p2 = [_block(1, "There are circumstances...")]

    pages_out, snap, fallbacks = tr.translate_pages_batch(
        [p0, p1, p2],
        source_langs=["en", "en", "en"],
        style="informal",
    )

    assert fallbacks == [1]  # ONLY p1 needed isolated fallback!
    assert len(fake.models.calls) == 2
    assert fake.models.calls[0]["config"].temperature == 0.65
    assert pages_out[0][0].translated_text == "Já tô colando aí perto."
    assert pages_out[0][1].translated_text == "Bora!"
    assert pages_out[1][0].translated_text == "Como assim?"
    assert pages_out[2][0].translated_text == "É complicado..."
    assert snap is not None and snap.requests_today == 2


def test_batch_glued_word_micro_repair(tmp_path: Path) -> None:
    _MODEL_THINKING_MODE.pop(DEFAULT_FREE_MODEL, None)

    def handler(_model, contents, _config, call_num):
        if call_num == 1:
            return _FakeResponse(
                text=json.dumps(
                    {
                        "p0": {"1": "JAMAISTENTEMRESOLVERISSOZINHOS!"},
                        "p1": {"1": "Calma aí, galera!"},
                    },
                    ensure_ascii=False,
                )
            )
        assert "JAMAISTENTEMRESOLVERISSOZINHOS!" in contents
        return _FakeResponse(text='{"1": "JAMAIS TENTEM RESOLVER ISSO SOZINHOS!"}')

    tr = GeminiFreeTierTranslator(
        api_key="fake-key",
        model=DEFAULT_FREE_MODEL,
        quota_tracker=QuotaTracker(tmp_path / "quota.json"),
    )
    tr._client = _FakeClient(handler)

    p0 = [_block(1, "Never try to solve this alone!")]
    p1 = [_block(1, "Calm down, everyone!")]
    pages_out, _snap, fallbacks = tr.translate_pages_batch([p0, p1], source_langs=["en", "en"])
    assert fallbacks == []
    assert pages_out[0][0].translated_text == "JAMAIS TENTEM RESOLVER ISSO SOZINHOS!"
    assert pages_out[1][0].translated_text == "Calma aí, galera!"
