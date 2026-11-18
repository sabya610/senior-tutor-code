"""Application configuration. All secrets come from environment variables only."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Storage
    data_dir: Path = Path("data")

    # Safety: when set, repositories can only be registered under these roots
    # (comma separated). Empty = any readable local path (local-only use).
    allowed_roots: str = ""

    # Host header allow-list (comma separated). Loopback names are always allowed; "*" disables the check
    # (needed when the app is reached through another hostname, e.g. a reverse proxy).
    allowed_hosts: str = ""

    # LLM: auto | openai | ollama | mock
    llm_provider: str = "auto"
    llm_model: str = ""  # empty = provider default
    openai_api_key: str = Field(default="", repr=False)
    openai_base_url: str = ""
    ollama_base_url: str = "http://localhost:11434"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 2500
    llm_timeout_seconds: float = 180.0

    # Embeddings: auto | openai | ollama | local
    embed_provider: str = "auto"
    embed_model: str = ""  # empty = provider default
    embed_dimensions: int = 512  # honoured by OpenAI text-embedding-3-*

    # Indexing
    max_file_bytes: int = 1_000_000
    chunk_lines: int = 60
    chunk_overlap: int = 10

    # Retrieval / context management
    top_k: int = 8
    context_char_budget: int = 24_000
    history_messages: int = 6

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tutor.db"

    @property
    def allowed_root_list(self) -> list[Path]:
        return [Path(p.strip()).resolve() for p in self.allowed_roots.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
