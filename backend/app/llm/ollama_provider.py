"""Local Ollama chat provider."""
from __future__ import annotations

import httpx

from app.llm.base import LLMError, LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, temperature: float = 0.2, max_tokens: int = 2500,
                 timeout: float = 180.0, num_ctx: int = 8192):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.num_ctx = num_ctx

    def complete(self, messages, *, temperature=None, max_tokens=None, json_mode=False) -> str:
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature if temperature is None else temperature,
                "num_predict": max_tokens or self.max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        if json_mode:
            payload["format"] = "json"
        try:
            resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request to {self.base_url} failed: {exc}") from exc
        return resp.json().get("message", {}).get("content", "")


def list_ollama_models(base_url: str, timeout: float = 2.0) -> list[str] | None:
    """Return model names, or None when the server is unreachable."""
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except (httpx.HTTPError, ValueError, KeyError):
        return None
