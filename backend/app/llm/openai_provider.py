"""OpenAI (and OpenAI-compatible) chat provider. API key comes from the environment only."""
from __future__ import annotations

from app.llm.base import LLMError, LLMProvider


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = "", temperature: float = 0.2,
                 max_tokens: int = 2500, timeout: float = 180.0):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url or None, timeout=timeout)
        self.model = model or "gpt-4o-mini"
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, messages, *, temperature=None, max_tokens=None, json_mode=False) -> str:
        kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as exc:  # SDK raises many subclasses; surface a clean message
            raise LLMError(f"OpenAI request failed: {type(exc).__name__}: {exc}") from exc
        return resp.choices[0].message.content or ""
