"""Hybrid retrieval: FTS5 BM25 + vector similarity + exact symbol matches, fused with RRF."""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from app.retrieval.embeddings import blob_to_vector, split_identifier

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

RRF_K = 60
MAX_PER_FILE = 3
STOPWORDS = {
    "the", "and", "for", "with", "how", "does", "what", "why", "when", "where", "which", "this", "that", "these",
    "those", "from", "into", "are", "was", "were", "has", "have", "can", "will", "would", "should", "about", "explain",
    "show", "tell", "give", "please", "work", "works", "used", "use", "using", "its", "any", "all", "not", "you",
    "your", "our", "their", "them", "than", "then", "there", "here", "also", "step", "flow", "feature", "code",
}
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_WORD = re.compile(r"[A-Za-z0-9]+")
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|testdata|e2e|mocks?|fixtures?)/|_test\.go$|(^|/)test_[^/]*\.py$|\.(test|spec)\.[jt]sx?$|Tests?\.java$", re.I)
_BULKY_DATA = re.compile(r"(swagger|openapi)[^/]*\.(json|ya?ml)$|(^|/)(package-lock|yarn\.lock|go\.sum)$|\.min\.(js|css)$|\.json$", re.I)
_WANTS_TESTS = re.compile(r"\b(tests?|testing|spec|specs|e2e|mocks?|fixtures?)\b", re.I)
_DOC_PATH = re.compile(r"\.(md|mdx|rst|adoc|txt)$", re.I)
_WANTS_DOCS = re.compile(r"\b(readme|docs?|documentation|guide|overview|architecture|design|adr|changelog)\b", re.I)
_SUFFIXES = ("ations", "ation", "ings", "ing", "ions", "ion", "ied", "ies", "ers", "ed", "er", "es", "s")


def stem(term: str) -> str:
    """Crude suffix stripping so 'validated'/'creation' match symbols such as validate_order/create_order."""
    for suffix in _SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            return term[: -len(suffix)]
    return term


def path_weight(path: str, wants_tests: bool, wants_docs: bool = False) -> float:
    """Down-weight tests, docs and bulky data so implementation code ranks first (unless the user asks for them)."""
    weight = 1.0
    if _TEST_PATH.search(path) and not wants_tests:
        weight *= 0.55
    if _DOC_PATH.search(path) and not wants_docs:
        weight *= 0.8
    if _BULKY_DATA.search(path):
        weight *= 0.6
    return weight


@dataclass
class Hit:
    chunk_id: int
    file_id: int
    path: str
    language: str
    sha1: str
    start_line: int
    end_line: int
    kind: str
    symbol: str | None
    text: str
    score: float = 0.0
    via: list[str] = field(default_factory=list)


def query_terms(query: str, limit: int = 40) -> list[str]:
    """Lower-cased search terms: whole words plus camelCase/snake_case parts, stop-words removed."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        t = t.lower()
        if t not in seen and t not in STOPWORDS and (len(t) >= 3 or t.isdigit()):
            seen.add(t)
            terms.append(t)

    for ident in _IDENT.findall(query):
        for piece in ident.split("."):
            for word in _WORD.findall(piece):
                add(word)
            parts = split_identifier(piece)
            if len(parts) > 1:
                for p in parts:
                    add(p)
    return terms[:limit]


def fts_expression(terms: list[str]) -> str:
    return " OR ".join(f'"{t}"' for t in terms)


class Retriever:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self._matrix_cache: dict[tuple[int, str], tuple[tuple, np.ndarray, np.ndarray]] = {}

    # ------------------------------------------------------------------ candidate generators
    def _lexical(self, repo_id: int, terms: list[str], limit: int = 60) -> list[Hit]:
        if not terms:
            return []
        rows = self.rt.db.query(
            "SELECT c.id AS chunk_id, c.file_id, f.rel_path AS path, f.language, f.sha1, c.start_line, c.end_line, "
            "c.kind, c.symbol, c.text FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "JOIN files f ON f.id = c.file_id WHERE chunks_fts MATCH ? AND c.repo_id = ? "
            "ORDER BY bm25(chunks_fts, 1.0, 4.0, 2.0) LIMIT ?",
            (fts_expression(terms), repo_id, limit),
        )
        return [Hit(**r, via=["keyword"]) for r in rows]

    def _vector_matrix(self, repo_id: int, model: str) -> tuple[np.ndarray, np.ndarray] | None:
        db = self.rt.db
        rev = (db.one("SELECT index_rev FROM repos WHERE id = ?", (repo_id,)) or {}).get("index_rev", 0)
        count = db.one("SELECT COUNT(*) AS n FROM chunks WHERE repo_id = ? AND embed_model = ?", (repo_id, model))["n"]
        if not count:
            return None
        key = (rev, count)
        cached = self._matrix_cache.get((repo_id, model))
        if cached and cached[0] == key:
            return cached[1], cached[2]
        rows = db.query("SELECT id, embedding FROM chunks WHERE repo_id = ? AND embed_model = ?", (repo_id, model))
        ids = np.array([r["id"] for r in rows], dtype=np.int64)
        matrix = np.vstack([blob_to_vector(r["embedding"]) for r in rows])
        self._matrix_cache[(repo_id, model)] = (key, ids, matrix)
        return ids, matrix

    def _vector(self, repo_id: int, query: str, limit: int = 60) -> list[Hit]:
        embedder = self.rt.embedder()
        loaded = self._vector_matrix(repo_id, embedder.name)
        if loaded is None:
            return []
        ids, matrix = loaded
        try:
            qvec = embedder.embed([query])[0]
        except Exception as exc:
            log.warning("Query embedding failed (%s); using keyword results only", exc)
            return []
        if qvec.shape[0] != matrix.shape[1]:
            return []
        scores = matrix @ qvec
        floor = getattr(embedder, "min_similarity", 0.2)
        top = np.argsort(-scores)[:limit]
        top = [i for i in top if scores[i] >= floor]
        hits = self._load_hits([int(ids[i]) for i in top], "semantic")
        if getattr(embedder, "lexical", False):
            wanted = [stem(t) for t in query_terms(query)]
            hits = [h for h in hits if any(t in h.text.lower() for t in wanted)]
        return hits

    def _symbol_hits(self, repo_id: int, query: str, limit: int = 40) -> list[Hit]:
        """Symbols whose name contains query terms (substring, so 'reconciler' finds 'IsvcReconciler.Reconcile').

        Each term is weighted by rarity among the candidates; exact name matches get a large bonus.
        """
        terms = list(dict.fromkeys(stem(t) for t in query_terms(query) if len(t) >= 4))[:10]
        if not terms:
            return []
        idents = {i.lower() for i in _IDENT.findall(query) if len(i) >= 4} - STOPWORDS
        rows = self.rt.db.query(
            "SELECT s.name, s.kind, s.file_id, s.start_line, s.end_line FROM symbols s WHERE s.repo_id = ? AND ("
            + " OR ".join(["lower(s.name) LIKE ?"] * len(terms)) + ") LIMIT 800",
            [repo_id, *[f"%{t}%" for t in terms]],
        )
        if not rows:
            return []
        lowered = [r["name"].lower() for r in rows]
        weight = {t: 1.0 / (1.0 + math.log(1 + sum(t in n for n in lowered))) for t in terms}
        scored: list[tuple[float, dict]] = []
        for row, name in zip(rows, lowered):
            score = sum(weight[t] for t in terms if t in name)
            leaf = name.rsplit(".", 1)[-1]
            if name in idents or any(name.endswith("." + i) for i in idents):
                score += 3.0
            if any(len(leaf) >= 5 and (t.startswith(leaf) or leaf.startswith(t)) for t in terms):
                score += 1.0  # 'reconciler' -> method named Reconcile
            if any(t in row["kind"] for t in terms):
                score += 1.5  # role symbols: reconciler, webhook, route, entrypoint, command
            if row["kind"] in ("reconciler", "webhook", "entrypoint", "route", "command", "class", "struct", "function", "method", "interface"):
                score += 0.3
            scored.append((score, row))
        scored.sort(key=lambda x: -x[0])
        chunk_ids: list[int] = []
        for _, s in scored[:30]:
            found = self.rt.db.query(
                "SELECT id FROM chunks WHERE file_id = ? AND start_line <= ? AND end_line >= ? ORDER BY start_line LIMIT 3",
                (s["file_id"], s["end_line"], s["start_line"]),
            )
            chunk_ids += [r["id"] for r in found]
        return self._load_hits(list(dict.fromkeys(chunk_ids))[:limit], "symbol")

    def _load_hits(self, chunk_ids: list[int], via: str) -> list[Hit]:
        if not chunk_ids:
            return []
        marks = ",".join("?" * len(chunk_ids))
        rows = {
            r["chunk_id"]: r
            for r in self.rt.db.query(
                "SELECT c.id AS chunk_id, c.file_id, f.rel_path AS path, f.language, f.sha1, c.start_line, c.end_line, "
                f"c.kind, c.symbol, c.text FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.id IN ({marks})",
                chunk_ids,
            )
        }
        return [Hit(**rows[i], via=[via]) for i in chunk_ids if i in rows]

    # ------------------------------------------------------------------ public search
    def search(self, repo_id: int, query: str, k: int | None = None, scope_paths: list[str] | None = None,
               exclude_chunk_ids: set[int] | None = None) -> list[Hit]:
        k = k or self.rt.settings.top_k
        lists = [
            (self._symbol_hits(repo_id, query), 1.5),
            (self._lexical(repo_id, query_terms(query)), 1.0),
            (self._vector(repo_id, query), 1.0),
        ]
        return self._fuse(lists, k, scope_paths, exclude_chunk_ids, wants_tests=bool(_WANTS_TESTS.search(query)),
                          wants_docs=bool(_WANTS_DOCS.search(query)))

    def search_multi(self, repo_id: int, queries: list[str], k: int, scope_paths: list[str] | None = None) -> list[Hit]:
        lists: list[tuple[list[Hit], float]] = []
        for q in queries:
            lists += [(self._symbol_hits(repo_id, q), 1.5), (self._lexical(repo_id, query_terms(q)), 1.0), (self._vector(repo_id, q), 1.0)]
        wants_tests = any(_WANTS_TESTS.search(q) for q in queries)
        wants_docs = any(_WANTS_DOCS.search(q) for q in queries)
        return self._fuse(lists, k, scope_paths, None, max_per_file=4, wants_tests=wants_tests, wants_docs=wants_docs)

    def _fuse(self, lists: list[tuple[list[Hit], float]], k: int, scope_paths: list[str] | None,
              exclude: set[int] | None, max_per_file: int = MAX_PER_FILE, wants_tests: bool = False,
              wants_docs: bool = False) -> list[Hit]:
        scores: dict[int, float] = {}
        best: dict[int, Hit] = {}
        for hits, weight in lists:
            for rank, hit in enumerate(hits):
                scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + weight / (RRF_K + rank + 1)
                if hit.chunk_id in best:
                    for v in hit.via:
                        if v not in best[hit.chunk_id].via:
                            best[hit.chunk_id].via.append(v)
                else:
                    best[hit.chunk_id] = hit
        scope = set(scope_paths or [])
        out: list[Hit] = []
        for cid, score in sorted(scores.items(), key=lambda kv: -kv[1]):
            hit = best[cid]
            if exclude and cid in exclude:
                continue
            if scope and hit.path in scope:
                score *= 1.35
            score *= path_weight(hit.path, wants_tests, wants_docs)
            hit.score = score
            out.append(hit)
        out.sort(key=lambda h: -h.score)
        per_file: dict[str, int] = {}
        picked: list[Hit] = []
        for hit in out:
            if per_file.get(hit.path, 0) >= max_per_file:
                continue
            per_file[hit.path] = per_file.get(hit.path, 0) + 1
            picked.append(hit)
            if len(picked) >= k:
                break
        return picked

    def chunks_for_range(self, repo_id: int, path: str, start: int, end: int) -> list[Hit]:
        rows = self.rt.db.query(
            "SELECT c.id FROM chunks c JOIN files f ON f.id = c.file_id WHERE f.repo_id = ? AND f.rel_path = ? "
            "AND c.start_line <= ? AND c.end_line >= ? ORDER BY c.start_line LIMIT 4",
            (repo_id, path, end, start),
        )
        return self._load_hits([r["id"] for r in rows], "pinned")
