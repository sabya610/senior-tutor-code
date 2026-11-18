"""Embedding back-ends. All return L2-normalised float32 matrices (n, dims)."""
from __future__ import annotations

import logging
import math
import re
import zlib
from typing import Protocol

import httpx
import numpy as np

log = logging.getLogger(__name__)

MAX_EMBED_CHARS = 6000
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


class Embedder(Protocol):
    name: str
    dims: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


def split_identifier(token: str) -> list[str]:
    parts = _CAMEL.findall(token)
    return [p.lower() for p in parts] if parts else [token.lower()]


def tokenize(text: str) -> list[str]:
    """Code-aware tokens: whole identifiers plus their camelCase / snake_case parts."""
    out: list[str] = []
    for tok in _TOKEN.findall(text):
        low = tok.lower()
        out.append(low)
        parts = split_identifier(tok)
        if len(parts) > 1:
            out.extend(parts)
    return out


class LocalHashEmbedder:
    """Dependency-free hashing embedder: works offline, deterministic, good for lexical overlap."""

    # Hash collisions give most unrelated chunks cosines up to ~0.15, so the floor sits above that. A short query
    # meeting a tiny chunk can still clear it through a single colliding bucket, which is why the retriever also
    # requires a hit to contain a query term when `lexical` is set.
    min_similarity = 0.22
    lexical = True

    def __init__(self, dims: int = 512):
        self.dims = dims
        self.name = f"local-hash-{dims}"

    def embed(self, texts: list[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = tokenize(text[:MAX_EMBED_CHARS])
            counts: dict[int, float] = {}
            grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
            for gram in grams:
                h = zlib.crc32(gram.encode("utf-8"))
                idx = h % self.dims
                sign = 1.0 if (h >> 16) & 1 else -1.0
                counts[idx] = counts.get(idx, 0.0) + sign
            for idx, val in counts.items():
                matrix[row, idx] = math.copysign(1.0 + math.log(abs(val)), val) if val else 0.0
        return _normalise(matrix)


class OpenAIEmbedder:
    min_similarity = 0.2

    def __init__(self, api_key: str, model: str, dims: int, base_url: str = ""):
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.model = model
        self.dims = dims
        self.name = f"openai:{model}:{dims}"

    def embed(self, texts: list[str]) -> np.ndarray:
        kwargs = {"dimensions": self.dims} if self.model.startswith("text-embedding-3") else {}
        vectors: list[list[float]] = []
        for i in range(0, len(texts), 64):
            batch = [t[:MAX_EMBED_CHARS] or " " for t in texts[i : i + 64]]
            resp = self.client.embeddings.create(model=self.model, input=batch, **kwargs)
            vectors.extend(d.embedding for d in resp.data)
        matrix = np.array(vectors, dtype=np.float32)
        self.dims = matrix.shape[1]
        return _normalise(matrix)


class OllamaEmbedder:
    min_similarity = 0.3  # embedding models such as nomic-embed-text have a higher unrelated-text baseline

    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.dims = 0
        self.name = f"ollama:{model}"

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        with httpx.Client(timeout=self.timeout) as client:
            for i in range(0, len(texts), 32):
                batch = [t[:MAX_EMBED_CHARS] or " " for t in texts[i : i + 32]]
                resp = client.post(f"{self.base_url}/api/embed", json={"model": self.model, "input": batch})
                if resp.status_code == 404:  # older Ollama: single-input endpoint
                    for text in batch:
                        r = client.post(f"{self.base_url}/api/embeddings", json={"model": self.model, "prompt": text})
                        r.raise_for_status()
                        vectors.append(r.json()["embedding"])
                    continue
                resp.raise_for_status()
                vectors.extend(resp.json()["embeddings"])
        matrix = np.array(vectors, dtype=np.float32)
        self.dims = matrix.shape[1]
        return _normalise(matrix)


def build_embedder(provider: str, model: str, dims: int, openai_key: str, openai_base_url: str, ollama_base_url: str) -> Embedder:
    provider = (provider or "auto").lower()
    if provider == "auto":
        provider = "openai" if openai_key else "local"
    if provider == "openai":
        if not openai_key:
            raise ValueError("EMBED_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAIEmbedder(openai_key, model or "text-embedding-3-small", dims, openai_base_url)
    if provider == "ollama":
        return OllamaEmbedder(ollama_base_url, model or "nomic-embed-text")
    if provider == "local":
        return LocalHashEmbedder(dims)
    raise ValueError(f"Unknown embed provider: {provider}")


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
