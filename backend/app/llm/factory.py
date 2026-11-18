"""Resolve the configured LLM provider (explicit, or auto-detected)."""
from __future__ import annotations

import logging
import time

from app.config import Settings
from app.llm.base import LLMProvider, MockProvider
from app.llm.ollama_provider import OllamaProvider, list_ollama_models
from app.llm.openai_provider import OpenAIProvider

log = logging.getLogger(__name__)
_probe_cache: dict[str, tuple[float, list[str] | None]] = {}
PROBE_TTL_SECONDS = 20.0


def probe_ollama(base_url: str) -> list[str] | None:
    now = time.monotonic()
    cached = _probe_cache.get(base_url)
    if cached and now - cached[0] < PROBE_TTL_SECONDS:
        return cached[1]
    models = list_ollama_models(base_url)
    _probe_cache[base_url] = (now, models)
    return models


def _pick_ollama_model(requested: str, available: list[str]) -> str:
    if requested:
        return requested
    chat_models = [m for m in available if "embed" not in m.lower()]
    return (chat_models or available or [""])[0]


def build_llm(cfg: dict[str, str], settings: Settings) -> LLMProvider:
    """cfg holds the effective (env + UI override) values for provider/model/base URLs."""
    provider = (cfg["llm_provider"] or "auto").lower()
    common = dict(temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens, timeout=settings.llm_timeout_seconds)

    if provider == "mock":
        return MockProvider()
    if provider == "openai" or (provider == "auto" and settings.openai_api_key):
        if not settings.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai requires the OPENAI_API_KEY environment variable")
        return OpenAIProvider(settings.openai_api_key, cfg["llm_model"], cfg["openai_base_url"], **common)
    if provider in ("ollama", "auto"):
        models = probe_ollama(cfg["ollama_base_url"])
        if models is None:
            if provider == "ollama":
                raise ValueError(f"Ollama is not reachable at {cfg['ollama_base_url']}")
            return MockProvider()
        model = _pick_ollama_model(cfg["llm_model"], models)
        if not model:
            if provider == "ollama":
                raise ValueError("Ollama is running but has no models; run `ollama pull <model>`")
            return MockProvider()
        return OllamaProvider(cfg["ollama_base_url"], model, **common)
    raise ValueError(f"Unknown LLM provider: {provider}")
