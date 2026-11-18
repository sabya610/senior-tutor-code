"""Application runtime: wires settings, storage, providers, indexer, retriever and teacher."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import Settings, get_settings
from app.db import Database
from app.knowledge.store import KnowledgeStore
from app.llm.base import LLMProvider, MockProvider
from app.llm.factory import build_llm
from app.retrieval.embeddings import Embedder, LocalHashEmbedder, build_embedder

log = logging.getLogger(__name__)

# Non-secret settings that can be overridden from the UI and are persisted in the database.
OVERRIDABLE = ("llm_provider", "llm_model", "embed_provider", "embed_model", "ollama_base_url", "openai_base_url")


class Runtime:
    def __init__(self, settings: Settings | None = None):
        from app.retrieval.retriever import Retriever
        from app.scanner.indexer import Indexer
        from app.teaching.orchestrator import Teacher
        from app.teaching.quiz import Quizzer
        from app.teaching.session_prep import SessionPrep

        self.settings = settings or get_settings()
        self.db = Database(self.settings.db_path)
        self.store = KnowledgeStore(self.db)
        self._llm_cache: tuple[tuple, LLMProvider] | None = None
        self._embed_cache: tuple[tuple, Embedder] | None = None
        self.indexer = Indexer(self)
        self.retriever = Retriever(self)
        self.teacher = Teacher(self)
        self.quizzer = Quizzer(self)
        self.session_prep = SessionPrep(self)

    # ------------------------------------------------------------------ configuration
    def cfg(self, key: str) -> str:
        override = self.db.get_setting(f"cfg.{key}", "")
        return override if override else str(getattr(self.settings, key))

    def effective_config(self) -> dict[str, str]:
        return {key: self.cfg(key) for key in OVERRIDABLE}

    def set_overrides(self, values: dict[str, str]) -> None:
        for key, value in values.items():
            if key in OVERRIDABLE:
                self.db.set_setting(f"cfg.{key}", value.strip())
        self._llm_cache = self._embed_cache = None

    # ------------------------------------------------------------------ providers
    def llm(self) -> LLMProvider:
        cfg = self.effective_config()
        key = tuple(cfg.items()) + (bool(self.settings.openai_api_key),)
        if self._llm_cache is None or self._llm_cache[0] != key or isinstance(self._llm_cache[1], MockProvider):
            try:
                provider = build_llm(cfg, self.settings)
            except ValueError as exc:
                log.warning("LLM configuration problem, falling back to evidence-only mode: %s", exc)
                provider = MockProvider()
                provider.config_error = str(exc)  # type: ignore[attr-defined]
            self._llm_cache = (key, provider)
        return self._llm_cache[1]

    def embedder(self) -> Embedder:
        cfg = self.effective_config()
        key = (cfg["embed_provider"], cfg["embed_model"], cfg["ollama_base_url"], cfg["openai_base_url"], bool(self.settings.openai_api_key))
        if self._embed_cache is None or self._embed_cache[0] != key:
            try:
                emb = build_embedder(cfg["embed_provider"], cfg["embed_model"], self.settings.embed_dimensions,
                                     self.settings.openai_api_key, cfg["openai_base_url"], cfg["ollama_base_url"])
            except ValueError as exc:
                log.warning("Embedding configuration problem, using local embeddings: %s", exc)
                emb = LocalHashEmbedder(self.settings.embed_dimensions)
            self._embed_cache = (key, emb)
        return self._embed_cache[1]

    # ------------------------------------------------------------------ safety
    def check_repo_path(self, path: str) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"Not a directory: {resolved}")
        roots = self.settings.allowed_root_list
        if roots and not any(resolved == r or r in resolved.parents for r in roots):
            raise PermissionError(f"Path is outside the allowed roots ({', '.join(str(r) for r in roots)})")
        return resolved
