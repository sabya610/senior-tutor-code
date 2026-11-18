"""LLM provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """Raised when the configured LLM backend fails; the message is safe to show to users."""


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""
    is_mock: bool = False

    @abstractmethod
    def complete(self, messages: list[dict], *, temperature: float | None = None,
                 max_tokens: int | None = None, json_mode: bool = False) -> str:
        """Return the assistant text for a chat-style message list."""

    def describe(self) -> dict:
        return {"provider": self.name, "model": self.model, "mock": self.is_mock}


class MockProvider(LLMProvider):
    """Marker provider used when no LLM is configured: the teacher renders evidence-only reports."""

    name = "mock"
    model = "evidence-only"
    is_mock = True

    def complete(self, messages: list[dict], **_: object) -> str:  # pragma: no cover - never called
        raise LLMError("No LLM provider configured")
