"""Batched page & multi-page translator using Gemini API Free Tier (google-genai).

Upgrades (v3-scanlation-turbo):
- Authentic Brazilian Portuguese scanlation style with 5 JP/EN few-shot contrast examples
  and style-calibrated temperatures (`scanlation=0.5`, `informal=0.65`, `fiel/formal=0.2`).
- Zero hidden thinking overhead (`ThinkingConfig(thinking_budget=0)` with graceful
  per-model fallback if a whitelisted model rejects `thinking_config`).
- Ultra-lean JSON output:
  * Single page: `{"1": "FALA", "2": "FALA"}`
  * Multi-page batch (up to 4 pages): `{"p0": {"1": "FALA"}, "p1": {"1": "FALA"}}`
    with clean per-page fallback if any single page is missing from batch JSON.
- Strictly locked to `FREE_TIER_MODELS` (R$ 0,00 — ZERO paid fallback).
- Preserves `has_glued_words` post-validation & micro-repair + `CircuitBreaker` + `QuotaTracker`.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from poc.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    InvalidJSONResponseError,
    NonRetryableAPIError,
)
from poc.config import CACHE_DIR, DEFAULT_FREE_MODEL, validate_free_tier_model
from poc.schemas import QuotaSnapshot, TextBlock

logger = logging.getLogger(__name__)

# Bumped whenever translation prompt/style changes so IndexedDB/translation caches refresh
# WITHOUT invalidating local SHA-256 detection/OCR/inpainting caches.
PROMPT_VERSION: str = "v3-scanlation-turbo"

STYLE_TEMPERATURES: dict[str, float] = {
    "scanlation": 0.50,
    "informal": 0.65,
    "fiel": 0.20,
    "formal": 0.20,
}

# Per-model discovered thinking disable mode ("budget_zero" | "level_minimal" | "none").
# Remembered in-process so we never waste a 400 roundtrip twice (stays strictly Free Tier).
_MODEL_THINKING_MODE: dict[str, str] = {}


class MissingAPIKeyError(NonRetryableAPIError):
    """Raised when GEMINI_API_KEY is not configured in `.env` or environment."""


class QuotaTracker:
    """Persists local daily request and token consumption for Free Tier awareness."""

    def __init__(self, path: Path = CACHE_DIR / "quota_usage.json") -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def record(
        self,
        model: str,
        prompt_tokens: int,
        output_tokens: int,
    ) -> QuotaSnapshot:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = self._load()
        day_bucket = data.setdefault(today, {})
        entry = day_bucket.setdefault(
            model,
            {
                "requests_today": 0,
                "prompt_tokens_today": 0,
                "output_tokens_today": 0,
                "total_tokens_today": 0,
            },
        )
        entry["requests_today"] += 1
        entry["prompt_tokens_today"] += max(0, int(prompt_tokens))
        entry["output_tokens_today"] += max(0, int(output_tokens))
        entry["total_tokens_today"] = (
            entry["prompt_tokens_today"] + entry["output_tokens_today"]
        )
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return QuotaSnapshot(
            date_utc=today,
            model=model,
            requests_today=entry["requests_today"],
            prompt_tokens_today=entry["prompt_tokens_today"],
            output_tokens_today=entry["output_tokens_today"],
            total_tokens_today=entry["total_tokens_today"],
            last_request_prompt_tokens=max(0, int(prompt_tokens)),
            last_request_output_tokens=max(0, int(output_tokens)),
        )

    def snapshot(self, model: str) -> QuotaSnapshot:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = self._load()
        entry = data.get(today, {}).get(model, {})
        return QuotaSnapshot(
            date_utc=today,
            model=model,
            requests_today=int(entry.get("requests_today", 0)),
            prompt_tokens_today=int(entry.get("prompt_tokens_today", 0)),
            output_tokens_today=int(entry.get("output_tokens_today", 0)),
            total_tokens_today=int(entry.get("total_tokens_today", 0)),
        )


class TranslatorProvider(ABC):
    """Abstract translator interface ready for optional external providers."""

    name: str = "base"

    @abstractmethod
    def translate_page(
        self,
        blocks: list[TextBlock],
        *,
        source_lang: str = "ja",
        target_lang: str = "pt-BR",
        glossary: Optional[dict[str, str]] = None,
        style: str = "scanlation",
    ) -> tuple[list[TextBlock], Optional[QuotaSnapshot]]:
        """Translate all text blocks of a page in a single grouped call."""


SCANLATION_SYSTEM_PROMPT = """Você é um tradutor e adaptador experiente de scanlation brasileira (PT-BR) de mangá/manhwa/HQ.
Traduza os balões em ordem de leitura com ritmo natural de diálogo falado no Brasil, concisão de balão e fidelidade ao clima da cena.

REGRAS DE OURO (SCANLATION BR):
1. PROIBIDO PORTUGUÊS DE LIVRO DIDÁTICO / LEGENDA ROBÓTICA:
   - Corte excesso de pronomes ("eu", "você", "ele") quando o sujeito oculto soar mais fluente.
   - Evite construções engessadas como: "O que você está falando?", "O que você quer dizer?", "Eu estou indo", "Existem circunstâncias", "constituiu família", "certamente", "todavia", "entretanto".
2. PORTUGUÊS FALADO REAL DO BRASIL:
   - Use contrações e oralidade natural ("tá", "tô", "pra", "pro", "né", "pô", "ué", "peraí", "calma aí", "tipo", "caramba", "galera") quando combinar com o personagem.
   - Adapte expressões idiomáticas pelo SENTIDO real, nunca palavra por palavra.
   - Seja CONCISO: falas enxutas cabem melhor e ficam mais legíveis em balões estreitos.
3. RESPEITE O TOM DA CENA E DO PERSONAGEM:
   - Amigos, rivais, ação e comédia: falas vivas e soltas. Se houver xingamento, grosseria ou intensidade no original, mantenha proporcional em PT-BR (sem censurar/suavizar à força, mas sem inventar meme fora de hora).
   - Nobres, chefes, mestres ou idosos: postura firme ou respeitosa, mas ainda em PT-BR fluido (nada arcaico ou robótico).
4. REFERÊNCIAS DE CONTRASTE (EVITE vs USE):
   - "What are you talking about?" -> EVITE: "Do que você está falando?" | USE: "Tá falando do quê?"
   - "What do you mean?" -> EVITE: "O que você quer dizer?" | USE: "Como assim?"
   - "I'm heading to your area now." -> EVITE: "Estou indo para a sua área agora." | USE: "Já tô colando aí perto."
   - "There are circumstances..." -> EVITE: "Existem circunstâncias..." | USE: "Tem uns poréns..." ou "É complicado..."
   - "家庭持ってるとかはもちろんだけど…" -> EVITE: "Claro que tem quem já constituiu família, mas..." | USE: "Lógico que já tem uma galera casada, com família e tal, mas..."
5. LIMPEZA DE OCR, ESPAÇOS E PONTUAÇÃO:
   - Corrija pequenos ruídos óbvios de OCR (japonês, inglês, coreano ou chinês) pelo contexto da conversa.
   - SEMPRE separe todas as palavras em português com espaços normais (NUNCA junte palavras sem espaço como "JAMAISTENTEMRESOLVER").
   - Use APENAS pontuação padrão PT-BR (!, ?, ..., -, aspas simples/duplas). NUNCA retorne símbolos CJK ou emojis (〜, ♥, ♪, ★, 「」).
6. SAÍDA JSON MÍNIMA ESTRITA:
   - Retorne APENAS o objeto JSON solicitado, sem markdown, sem comentários, sem repetir o texto original e preservando exatamente todas as chaves/IDs recebidos.
"""

TRANSLATION_STYLES: dict[str, str] = {
    "scanlation": (
        "PERFIL ATIVO: SCANLATION BR (PADRÃO). "
        "Português falado brasileiro profissional de scan: fluente, expressivo, coloquial natural, "
        "conciso para caber nos balões e fiel ao registro/personalidade de cada personagem."
    ),
    "informal": (
        "PERFIL ATIVO: INFORMAL / SOLTO. "
        "Ainda mais descontraído e coloquial (ótimo para slice-of-life e comédia): use bastante "
        "oralidade brasileira natural (tá, tô, pra, né, pô, ué, peraí, tipo, caramba) sem perder o sentido."
    ),
    "fiel": (
        "PERFIL ATIVO: FIEL AO ORIGINAL. "
        "Mantenha maior proximidade com a estrutura e nuances do original, evitando gírias pesadas, "
        "porém ainda com português claro, natural e conciso para balões."
    ),
    "formal": (
        "PERFIL ATIVO: FORMAL / CULTA. "
        "Evite gírias e contrações coloquiais (use 'está', 'para', 'estou'); mantenha português "
        "polido, claro, elegante e direto para os balões."
    ),
}


def get_style_temperature(style: str) -> float:
    return STYLE_TEMPERATURES.get((style or "scanlation").strip().lower(), 0.50)


def build_system_prompt(style: str = "scanlation") -> str:
    key = (style or "scanlation").strip().lower()
    style_note = TRANSLATION_STYLES.get(key, TRANSLATION_STYLES["scanlation"])
    return f"{SCANLATION_SYSTEM_PROMPT}\n{style_note}\n"


def has_glued_words(text: str) -> bool:
    """Detect malformed glued output lacking spaces (e.g. `JAMAISTENTEMRESOLVERISSOZINHOS`).

    Ignores URLs and expressive letter elongations like `AAAAAAAAAAAAH!` (low unique char count).
    """
    if not text:
        return False
    for tok in re.split(r"\s+", text.strip()):
        if tok.startswith(("http://", "https://", "www.")):
            continue
        letters = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", tok)
        if len(letters) >= 24 and len(set(letters.lower())) >= 7:
            return True
    return False


_PASSTHROUGH_PUNCT_MAP: tuple[tuple[str, str], ...] = (
    ("\u2026", "..."),  # …
    ("\u22ef", "..."),  # ⋯
    ("\ufe19", "..."),  # ︙
    ("\u2025", ".."),   # ‥
    ("\uff01", "!"),    # ！
    ("\uff1f", "?"),    # ？
    ("\u203c", "!!"),   # ‼
    ("\u2049", "!?"),   # ⁉
    ("\u2048", "?!"),   # ⁈
    ("\u2014", "-"),    # —
    ("\u2015", "-"),    # ―
    ("\u301c", "~"),    # 〜
    ("\uff5e", "~"),    # ～
    ("\u3002", "."),    # 。
    ("\u3001", ","),    # 、
    ("\u300c", '"'),    # 「
    ("\u300d", '"'),    # 」
    ("\u3000", " "),    # ideographic space
)


def is_non_translatable_symbol_text(text: str) -> bool:
    """Return True if text has NO alphabetic/CJK/Hangul letters (only punctuation/symbols/digits).

    Examples: `…!`, `?!`, `...`, `——`, `!!`, `100%`, `2024`.
    Such balloons bypass Gemini directly so Flash-Lite never omits them and tokens are saved.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    return not any(ch.isalpha() for ch in stripped)


def normalize_passthrough_symbols(text: str) -> str:
    """Normalize pure punctuation/symbol/number balloons cleanly for PT-BR rendering."""
    import unicodedata

    out = (text or "").strip()
    for src, dst in _PASSTHROUGH_PUNCT_MAP:
        if src in out:
            out = out.replace(src, dst)
    out = unicodedata.normalize("NFKC", out)
    out = re.sub(r"\.{4,}", "...", out)
    out = re.sub(r"-{3,}", "--", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _format_glossary_section(glossary: Optional[dict[str, str]]) -> str:
    if not glossary:
        return ""
    clean_pairs = [
        (str(k).strip(), str(v).strip())
        for k, v in glossary.items()
        if str(k).strip() and str(v).strip()
    ]
    if not clean_pairs:
        return ""
    lines = ["\nGLOSSÁRIO OBRIGATÓRIO DA OBRA (respeite exatamente nomes/termos/gênero):"]
    for src, dst in clean_pairs[:120]:
        lines.append(f"- {src} => {dst}")
    return "\n".join(lines)


def _strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_lean_single_page_json(raw_text: str) -> dict[int, str]:
    """Parse ultra-lean `{"1": "FALA", "2": "FALA"}` (with defensive fallback for nested formats)."""
    cleaned = _strip_json_fences(raw_text)
    if not cleaned:
        raise InvalidJSONResponseError("Gemini retornou resposta vazia.")
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InvalidJSONResponseError(
            f"JSON invalido retornado pelo Gemini: {exc}. Trecho: {cleaned[:260]}",
            original_error=exc,
        ) from exc

    if not isinstance(obj, dict):
        raise InvalidJSONResponseError(
            f"Esperado objeto JSON `{{\"1\": \"...\"}}`, recebido {type(obj).__name__}."
        )

    # Defensive unwrap if model wrapped in `{"translations": [...]}` or `{"p0": {...}}`
    if "translations" in obj and isinstance(obj["translations"], list):
        mapped: dict[int, str] = {}
        for item in obj["translations"]:
            if isinstance(item, dict) and "id" in item:
                txt = item.get("pt_br") or item.get("text") or item.get("translation") or ""
                mapped[int(item["id"])] = str(txt).strip()
        return mapped

    if len(obj) == 1:
        only_val = next(iter(obj.values()))
        if isinstance(only_val, dict):
            obj = only_val

    result: dict[int, str] = {}
    for k, v in obj.items():
        try:
            bid = int(str(k).strip().lstrip("#"))
        except ValueError:
            continue
        if isinstance(v, str):
            result[bid] = v.strip()
        elif isinstance(v, dict):
            txt = v.get("pt_br") or v.get("text") or ""
            result[bid] = str(txt).strip()
    if not result:
        raise InvalidJSONResponseError(
            f"JSON do Gemini nao contem pares id->traducao validos. Trecho: {cleaned[:260]}"
        )
    return result


def _parse_lean_batch_json(raw_text: str) -> dict[str, dict[int, str]]:
    """Parse ultra-lean multi-page JSON `{"p0": {"1": "FALA"}, "p1": {"1": "FALA"}}`."""
    cleaned = _strip_json_fences(raw_text)
    if not cleaned:
        raise InvalidJSONResponseError("Gemini retornou resposta vazia no lote.")
    try:
        obj = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InvalidJSONResponseError(
            f"JSON de lote invalido retornado pelo Gemini: {exc}. Trecho: {cleaned[:260]}",
            original_error=exc,
        ) from exc

    if not isinstance(obj, dict):
        raise InvalidJSONResponseError(
            f"Esperado objeto JSON de lote `{{\"p0\": ...}}`, recebido {type(obj).__name__}."
        )

    # Unwrap accidental outer container like `{"pages": {"p0": ...}}`
    if "pages" in obj and isinstance(obj["pages"], dict):
        obj = obj["pages"]

    parsed_pages: dict[str, dict[int, str]] = {}
    for page_key, page_val in obj.items():
        if not isinstance(page_val, dict):
            continue
        page_map: dict[int, str] = {}
        for k, v in page_val.items():
            try:
                bid = int(str(k).strip().lstrip("#"))
            except ValueError:
                continue
            if isinstance(v, str):
                page_map[bid] = v.strip()
            elif isinstance(v, dict):
                page_map[bid] = str(v.get("pt_br") or v.get("text") or "").strip()
        parsed_pages[str(page_key)] = page_map
    return parsed_pages


def _is_thinking_config_rejection(exc: BaseException) -> bool:
    """Detect HTTP 400 INVALID_ARGUMENT rejections when sending `thinking_config`."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 400:
        return True
    msg = str(exc).lower()
    return (
        "invalid_argument" in msg
        or "400" in msg
        or (
            ("thinking" in msg or "thinking_budget" in msg or "thinking_level" in msg)
            and ("invalid" in msg or "unsupported" in msg or "not supported" in msg)
        )
    )


def _thinking_fallback_chain(model_name: str) -> list[str]:
    """Return ordered zero-thinking modes to try for a whitelisted Free Tier model.

    Gemini 3.x models reject `thinking_budget=0` with 400 INVALID_ARGUMENT on v1 and
    instead accept `thinking_level='MINIMAL'` (0 thinking tokens). Older/other models
    accept `thinking_budget=0`. Final fallback is `none` (no `thinking_config`).
    """
    cached = _MODEL_THINKING_MODE.get(model_name)
    if cached:
        return [cached]
    lower = model_name.lower()
    if lower.startswith("gemini-3.") or lower.startswith("gemini-3-"):
        return ["level_minimal", "budget_zero", "none"]
    return ["budget_zero", "level_minimal", "none"]


class GeminiFreeTierTranslator(TranslatorProvider):
    """Google Gemini API translator strictly locked to verified Free Tier models."""

    name: str = "gemini-free-tier"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_FREE_MODEL,
        *,
        timeout_seconds: float = 45.0,
        circuit_breaker: Optional[CircuitBreaker] = None,
        quota_tracker: Optional[QuotaTracker] = None,
        disable_thinking: bool = True,
    ) -> None:
        self.model = validate_free_tier_model(model)
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.disable_thinking = disable_thinking
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=3,
                recovery_timeout=30.0,
                max_retries=3,
                base_delay=1.5,
                max_delay=30.0,
            )
        )
        self.quota_tracker = quota_tracker or QuotaTracker()
        self._client = None

    def _get_client(self):
        if not self.api_key:
            raise MissingAPIKeyError(
                "GEMINI_API_KEY nao configurada! Cole sua chave gratuita do Google AI Studio "
                "em `.env` (GEMINI_API_KEY=...) e execute novamente.",
                status_code=401,
            )
        if self._client is None:
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    timeout=int(self.timeout_seconds * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    def _build_config(
        self,
        *,
        system_prompt: str,
        temperature: float,
        thinking_mode: str = "level_minimal",
    ):
        from google.genai import types

        kwargs: dict[str, Any] = {
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
            "temperature": temperature,
        }
        if hasattr(types, "ThinkingConfig"):
            if thinking_mode == "level_minimal":
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="MINIMAL")
            elif thinking_mode == "budget_zero":
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**kwargs)

    def _call_gemini_json(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        temperature: float,
    ):
        """Invoke Gemini once under CircuitBreaker with zero-thinking config + graceful fallback."""
        client = self._get_client()
        modes = _thinking_fallback_chain(self.model) if self.disable_thinking else ["none"]

        def _invoke(mode: str):
            cfg = self._build_config(
                system_prompt=system_prompt,
                temperature=temperature,
                thinking_mode=mode,
            )
            return client.models.generate_content(
                model=self.model,
                contents=user_prompt,
                config=cfg,
            )

        last_exc: Optional[Exception] = None
        for idx, mode in enumerate(modes):
            try:
                resp = self.circuit_breaker.execute(lambda m=mode: _invoke(m))
                _MODEL_THINKING_MODE[self.model] = mode
                return resp
            except Exception as exc:
                last_exc = exc
                has_next = idx + 1 < len(modes)
                if mode != "none" and has_next and _is_thinking_config_rejection(exc):
                    next_mode = modes[idx + 1]
                    logger.info(
                        "Modelo %s rejeitou thinking_mode=%s (HTTP 400); tentando %s.",
                        self.model,
                        mode,
                        next_mode,
                    )
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    def _repair_glued_blocks_if_needed(
        self,
        blocks: list[TextBlock],
        *,
        system_prompt: str,
    ) -> tuple[int, int]:
        """Detect & repair any glued spaceless PT-BR output in 1 micro-request."""
        glued = [b for b in blocks if has_glued_words(b.translated_text)]
        if not glued:
            return (0, 0)

        logger.warning(
            "Detectado texto colado sem espacos nos baloes %s; acionando micro-reparo.",
            [b.id for b in glued],
        )
        repair_payload = {
            str(b.id): {"quebrado": b.translated_text, "orig": b.source_text}
            for b in glued
        }
        repair_prompt = (
            "Corrija SOMENTE os espacos entre palavras em PT-BR nos baloes abaixo. "
            'Retorne apenas JSON enxuto {"ID": "TEXTO CORRIGIDO"}:\n'
            + json.dumps(repair_payload, ensure_ascii=False)
        )
        try:
            resp = self._call_gemini_json(
                user_prompt=repair_prompt,
                system_prompt=system_prompt,
                temperature=0.1,
            )
            raw = (getattr(resp, "text", None) or "").strip()
            fixed_map = _parse_lean_single_page_json(raw)
            for b in glued:
                fixed = fixed_map.get(b.id, "")
                if fixed and not has_glued_words(fixed):
                    b.translated_text = re.sub(r"[ \t]{2,}", " ", fixed).strip()
            usage = getattr(resp, "usage_metadata", None)
            return (
                int(getattr(usage, "prompt_token_count", 0) or 0),
                int(getattr(usage, "candidates_token_count", 0) or 0),
            )
        except Exception as exc:
            logger.warning("Micro-reparo de espacos falhou (%s); mantendo saida.", exc)
            return (0, 0)

    def _micro_fallback_missing_blocks(
        self,
        missing_blocks: list[TextBlock],
        *,
        source_lang: str,
        target_lang: str,
        glossary: Optional[dict[str, str]],
        system_prompt: str,
    ) -> tuple[int, int]:
        """Targeted 1-shot micro-fallback ONLY for IDs omitted by Gemini; never aborts the page."""
        if not missing_blocks:
            return (0, 0)

        # Immediately handle any pure-symbol/noise blocks without wasting an API call
        still_needed: list[TextBlock] = []
        for blk in missing_blocks:
            if is_non_translatable_symbol_text(blk.source_text):
                blk.translated_text = normalize_passthrough_symbols(blk.source_text)
            else:
                still_needed.append(blk)

        if not still_needed:
            return (0, 0)

        payload = {str(b.id): b.source_text.strip() for b in still_needed}
        glossary_block = _format_glossary_section(glossary)
        prompt = (
            f"Traduza SOMENTE estes baloes faltantes ({source_lang} -> {target_lang}).{glossary_block}\n"
            'Retorne APENAS JSON {"ID": "TRADUCAO"} contendo obrigatoriamente TODAS as chaves:\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        prompt_tok = 0
        output_tok = 0
        try:
            resp = self._call_gemini_json(
                user_prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.2,
            )
            raw = (getattr(resp, "text", None) or "").strip()
            fixed_map = _parse_lean_single_page_json(raw)
            for blk in still_needed:
                val = fixed_map.get(blk.id, "").strip()
                if val:
                    blk.translated_text = re.sub(r"[ \t]{2,}", " ", val).strip()
            usage = getattr(resp, "usage_metadata", None)
            prompt_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
            output_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
        except Exception as exc:
            logger.warning(
                "Micro-fallback para IDs omitidos %s falhou (%s); preservando original nesses baloes.",
                [b.id for b in still_needed],
                exc,
            )

        # Guarantee NO block is ever left empty or crashes the page!
        for blk in still_needed:
            if not blk.translated_text.strip():
                blk.translated_text = normalize_passthrough_symbols(blk.source_text) or blk.source_text.strip()

        return (prompt_tok, output_tok)

    def translate_page(
        self,
        blocks: list[TextBlock],
        *,
        source_lang: str = "ja",
        target_lang: str = "pt-BR",
        glossary: Optional[dict[str, str]] = None,
        style: str = "scanlation",
    ) -> tuple[list[TextBlock], Optional[QuotaSnapshot]]:
        translatable: list[TextBlock] = []
        for b in blocks:
            raw_src = b.source_text.strip()
            if not raw_src:
                continue
            if is_non_translatable_symbol_text(raw_src):
                # Bypass Gemini for punctuation/symbol/number-only balloons (e.g. `…!`, `?!`, `——`)
                b.translated_text = normalize_passthrough_symbols(raw_src)
                continue
            translatable.append(b)

        if not translatable:
            return blocks, self.quota_tracker.snapshot(self.model)

        compact_input = {str(b.id): b.source_text.strip() for b in translatable}
        glossary_section = _format_glossary_section(glossary)
        user_prompt = (
            f"Idioma origem: {source_lang} -> {target_lang}.{glossary_section}\n"
            'Retorne APENAS JSON enxuto no formato exato {"1": "FALA", "2": "FALA"} com todas as chaves:\n'
            + json.dumps(compact_input, ensure_ascii=False)
        )
        system_prompt = build_system_prompt(style)
        temperature = get_style_temperature(style)

        response = self._call_gemini_json(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        raw_text = (getattr(response, "text", None) or "").strip()
        by_id = _parse_lean_single_page_json(raw_text)

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)

        missing_blocks: list[TextBlock] = []
        for block in translatable:
            translated = by_id.get(block.id, "").strip()
            if translated:
                block.translated_text = re.sub(r"[ \t]{2,}", " ", translated).strip()
            else:
                missing_blocks.append(block)

        if missing_blocks:
            logger.warning(
                "Gemini omitiu IDs %s na pagina; executando micro-fallback sem abortar a pagina.",
                [b.id for b in missing_blocks],
            )
            fb_in, fb_out = self._micro_fallback_missing_blocks(
                missing_blocks,
                source_lang=source_lang,
                target_lang=target_lang,
                glossary=glossary,
                system_prompt=system_prompt,
            )
            prompt_tokens += fb_in
            output_tokens += fb_out

        rep_in, rep_out = self._repair_glued_blocks_if_needed(
            translatable,
            system_prompt=system_prompt,
        )
        prompt_tokens += rep_in
        output_tokens += rep_out

        quota_snap = self.quota_tracker.record(
            model=self.model,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )
        return blocks, quota_snap

    def translate_pages_batch(
        self,
        pages_blocks: list[list[TextBlock]],
        *,
        source_langs: Optional[list[str]] = None,
        target_lang: str = "pt-BR",
        glossary: Optional[dict[str, str]] = None,
        style: str = "scanlation",
    ) -> tuple[list[list[TextBlock]], Optional[QuotaSnapshot], list[int]]:
        """Translate up to 4 sequential manga pages in ONE grouped Gemini Free Tier JSON call.

        Returns `(pages_blocks, quota_snapshot, fallback_page_indices)` where
        `fallback_page_indices` lists any page indices that needed a targeted fallback
        because the batch JSON omitted them or some of their IDs (preserving all translated balloons!).
        """
        non_empty_indices: list[int] = []
        translatable_by_page: dict[int, list[TextBlock]] = {}
        batch_input: dict[str, dict[str, str]] = {}

        for idx, blks in enumerate(pages_blocks):
            page_translatable: list[TextBlock] = []
            for b in blks:
                raw_src = b.source_text.strip()
                if not raw_src:
                    continue
                if is_non_translatable_symbol_text(raw_src):
                    b.translated_text = normalize_passthrough_symbols(raw_src)
                    continue
                page_translatable.append(b)
            if not page_translatable:
                continue
            non_empty_indices.append(idx)
            translatable_by_page[idx] = page_translatable
            batch_input[f"p{idx}"] = {str(b.id): b.source_text.strip() for b in page_translatable}

        if not non_empty_indices:
            return pages_blocks, self.quota_tracker.snapshot(self.model), []

        # Single non-empty page -> delegate directly to lean single-page call
        if len(non_empty_indices) == 1:
            idx = non_empty_indices[0]
            lang = source_langs[idx] if source_langs and idx < len(source_langs) else "ja"
            _, snap = self.translate_page(
                pages_blocks[idx],
                source_lang=lang,
                target_lang=target_lang,
                glossary=glossary,
                style=style,
            )
            return pages_blocks, snap, []

        lang_summary = ",".join(sorted(set(source_langs))) if source_langs else "ja"
        glossary_section = _format_glossary_section(glossary)
        user_prompt = (
            f"Lote sequencial de {len(non_empty_indices)} paginas vizinhas do mesmo capitulo "
            f"(origem: {lang_summary} -> {target_lang}). Mantenha continuidade de dialogo.{glossary_section}\n"
            'Retorne APENAS JSON enxuto no formato exato {"p0": {"1": "FALA"}, "p1": {"1": "FALA"}} '
            "preservando todas as paginas e todos os IDs:\n"
            + json.dumps(batch_input, ensure_ascii=False)
        )
        system_prompt = build_system_prompt(style)
        temperature = get_style_temperature(style)

        response = self._call_gemini_json(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        raw_text = (getattr(response, "text", None) or "").strip()
        parsed_batch = _parse_lean_batch_json(raw_text)

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
        output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)

        full_fallback_pages: list[int] = []
        partial_missing_by_page: dict[int, list[TextBlock]] = {}
        all_translated_ok: list[TextBlock] = []

        for idx in non_empty_indices:
            pkey = f"p{idx}"
            translatable = translatable_by_page[idx]
            page_map = parsed_batch.get(pkey) or {}
            if not page_map:
                logger.warning(
                    "Lote omitiu pagina inteira %s; acionando fallback isolado.",
                    pkey,
                )
                full_fallback_pages.append(idx)
                continue

            page_missing: list[TextBlock] = []
            for b in translatable:
                val = page_map.get(b.id, "").strip()
                if val:
                    b.translated_text = re.sub(r"[ \t]{2,}", " ", val).strip()
                    all_translated_ok.append(b)
                else:
                    page_missing.append(b)

            if page_missing:
                if len(page_missing) == len(translatable):
                    full_fallback_pages.append(idx)
                else:
                    logger.warning(
                        "Lote omitiu parcialmente IDs %s na pagina %s; acionando micro-fallback.",
                        [b.id for b in page_missing],
                        pkey,
                    )
                    partial_missing_by_page[idx] = page_missing

        # Targeted micro-fallback for partially missing IDs (NEVER drops already-translated balloons!)
        for idx, missing_list in partial_missing_by_page.items():
            lang = source_langs[idx] if source_langs and idx < len(source_langs) else "ja"
            fb_in, fb_out = self._micro_fallback_missing_blocks(
                missing_list,
                source_lang=lang,
                target_lang=target_lang,
                glossary=glossary,
                system_prompt=system_prompt,
            )
            prompt_tokens += fb_in
            output_tokens += fb_out
            all_translated_ok.extend(missing_list)

        # Micro-repair any glued words across successfully translated batch pages
        rep_in, rep_out = self._repair_glued_blocks_if_needed(
            all_translated_ok,
            system_prompt=system_prompt,
        )
        prompt_tokens += rep_in
        output_tokens += rep_out

        quota_snap = self.quota_tracker.record(
            model=self.model,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
        )

        # Clean isolated fallback ONLY for completely omitted pages (preserving all already-translated pages!)
        for idx in full_fallback_pages:
            lang = source_langs[idx] if source_langs and idx < len(source_langs) else "ja"
            _, quota_snap = self.translate_page(
                pages_blocks[idx],
                source_lang=lang,
                target_lang=target_lang,
                glossary=glossary,
                style=style,
            )

        fallback_pages_all = sorted(set(full_fallback_pages) | set(partial_missing_by_page.keys()))
        return pages_blocks, quota_snap, fallback_pages_all

    def list_available_free_models(self) -> list[str]:
        from poc.config import FREE_TIER_MODELS

        client = self._get_client()
        available: list[str] = []
        for m in client.models.list():
            name = (getattr(m, "name", "") or "").removeprefix("models/")
            if name in FREE_TIER_MODELS:
                available.append(name)
        return sorted(set(available))
