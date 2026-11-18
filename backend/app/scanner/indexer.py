"""Incremental repository indexer.

Unchanged files are skipped by (size, mtime) and then by content hash. Changed files are
re-parsed and their chunks re-stored; chunk embeddings are reused when the chunk text is
unchanged so only genuinely new content is embedded again.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.analysis.architecture import build_summary
from app.analysis.features import detect_features
from app.retrieval.embeddings import split_identifier, vector_to_blob
from app.scanner.parsers import parse_file
from app.scanner.resolver import resolve_relations
from app.scanner.walker import ScannedFile, is_generated, iter_repo_files

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)
EMBED_BATCH = 96


def _chunk_hash(path: str, symbol: str | None, text: str) -> str:
    return hashlib.sha1(f"{path}\n{symbol or ''}\n{text}".encode("utf-8", "replace")).hexdigest()


def fts_symbol(symbol: str | None) -> str:
    """Symbol text for the FTS column plus its camelCase/snake_case parts, so 'reconciler' finds 'IsvcReconciler'."""
    if not symbol:
        return ""
    parts: list[str] = []
    for piece in symbol.replace(".", " ").split():
        parts += split_identifier(piece)
    return f"{symbol} {' '.join(dict.fromkeys(parts))}"


class Indexer:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self.status: dict[int, dict] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public API
    def start(self, repo_id: int, full: bool = False) -> dict:
        """Start a background index run (no-op if one is already running)."""
        with self._lock:
            current = self.status.get(repo_id)
            if current and current["state"] == "running":
                return current
            self.status[repo_id] = self._new_status()
            threading.Thread(target=self._run_safe, args=(repo_id, full), daemon=True, name=f"index-{repo_id}").start()
            return self.status[repo_id]

    def get_status(self, repo_id: int) -> dict:
        return self.status.get(repo_id) or {"state": "idle"}

    def run(self, repo_id: int, full: bool = False) -> dict:
        """Synchronous index run (used by tests and the CLI path)."""
        with self._lock:
            self.status[repo_id] = self._new_status()
        self._run_safe(repo_id, full)
        return self.status[repo_id]

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _new_status() -> dict:
        return {"state": "running", "phase": "queued", "files_total": 0, "files_done": 0, "added": 0, "changed": 0,
                "removed": 0, "unchanged": 0, "skipped": 0, "embedded": 0, "warnings": [], "error": None,
                "started_at": time.time(), "finished_at": None}

    def _run_safe(self, repo_id: int, full: bool) -> None:
        st = self.status[repo_id]
        try:
            self._run(repo_id, full, st)
            st["state"] = "done"
        except Exception as exc:  # report to UI, keep the server alive
            log.exception("Indexing failed for repo %s", repo_id)
            st["state"] = "error"
            st["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            st["phase"] = "finished"
            st["finished_at"] = time.time()

    def _run(self, repo_id: int, full: bool, st: dict) -> None:
        db, store, settings = self.rt.db, self.rt.store, self.rt.settings
        repo = store.get_repo(repo_id)
        if not repo:
            raise ValueError(f"Unknown repository id {repo_id}")
        root = Path(repo["path"])
        if not root.is_dir():
            raise ValueError(f"Repository path no longer exists: {root}")

        st["phase"] = "scanning"
        scanned = {f.rel_path: f for f in iter_repo_files(root, settings.max_file_bytes)}
        existing = {r["rel_path"]: r for r in db.query("SELECT * FROM files WHERE repo_id = ?", (repo_id,))}
        st["files_total"] = len(scanned)

        removed = [p for p in existing if p not in scanned]
        for rel in removed:
            self._delete_file(existing[rel]["id"])
        st["removed"] = len(removed)

        st["phase"] = "parsing"
        for rel, f in scanned.items():
            self._index_file(repo_id, f, existing.get(rel), full, st)
            st["files_done"] += 1

        changed_anything = bool(st["added"] or st["changed"] or st["removed"])
        st["phase"] = "embedding"
        st["embedded"] = self._embed_pending(repo_id, st)

        if changed_anything or full or not repo["last_indexed_at"]:
            st["phase"] = "analysing"
            resolve_relations(db, repo_id, root)
            detect_features(db, repo_id, repo["name"])
            store.save_summary(repo_id, build_summary(db, repo_id, repo["name"]))
            db.execute("UPDATE repos SET index_rev = index_rev + 1 WHERE id = ?", (repo_id,))
        db.execute("UPDATE repos SET last_indexed_at = datetime('now') WHERE id = ?", (repo_id,))

    def _delete_file(self, file_id: int) -> None:
        with self.rt.db.write() as conn:
            conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)", (file_id,))
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def _index_file(self, repo_id: int, f: ScannedFile, existing: dict | None, full: bool, st: dict) -> None:
        if existing and not full and existing["size"] == f.size and existing["mtime_ns"] == f.mtime_ns:
            st["unchanged"] += 1
            return
        try:
            data = f.abs_path.read_bytes()
        except OSError as exc:
            st["warnings"].append(f"Cannot read {f.rel_path}: {exc}")
            return
        sha = hashlib.sha1(data).hexdigest()
        if existing and not full and existing["sha1"] == sha:
            self.rt.db.execute("UPDATE files SET mtime_ns = ? WHERE id = ?", (f.mtime_ns, existing["id"]))
            st["unchanged"] += 1
            return
        text = data.decode("utf-8", errors="replace")
        if is_generated(text):
            if existing:
                self._delete_file(existing["id"])
            st["skipped"] += 1
            return

        parsed = parse_file(f.rel_path, f.language, text, self.rt.settings.chunk_lines, self.rt.settings.chunk_overlap)
        with self.rt.db.write() as conn:
            reuse: dict[str, tuple[bytes, str]] = {}
            if existing:
                for row in conn.execute("SELECT text_hash, embedding, embed_model FROM chunks WHERE file_id = ? AND embedding IS NOT NULL", (existing["id"],)):
                    reuse[row["text_hash"]] = (row["embedding"], row["embed_model"])
                conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE file_id = ?)", (existing["id"],))
                conn.execute("DELETE FROM files WHERE id = ?", (existing["id"],))
            file_id = conn.execute(
                "INSERT INTO files(repo_id, rel_path, language, size, mtime_ns, sha1, line_count) VALUES(?,?,?,?,?,?,?)",
                (repo_id, f.rel_path, parsed.language, f.size, f.mtime_ns, sha, parsed.line_count),
            ).lastrowid
            for ch in parsed.chunks:
                th = _chunk_hash(f.rel_path, ch.symbol, ch.text)
                emb, model = reuse.get(th, (None, None))
                chunk_id = conn.execute(
                    "INSERT INTO chunks(file_id, repo_id, start_line, end_line, kind, symbol, text, text_hash, embedding, embed_model) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (file_id, repo_id, ch.start_line, ch.end_line, ch.kind, ch.symbol, ch.text, th, emb, model),
                ).lastrowid
                conn.execute("INSERT INTO chunks_fts(rowid, text, symbol, path) VALUES(?,?,?,?)",
                             (chunk_id, ch.text, fts_symbol(ch.symbol), f.rel_path))
            conn.executemany(
                "INSERT INTO symbols(repo_id, file_id, name, kind, start_line, end_line, signature, parent) VALUES(?,?,?,?,?,?,?,?)",
                [(repo_id, file_id, s.name, s.kind, s.start_line, s.end_line, s.signature, s.parent) for s in parsed.symbols],
            )
            conn.executemany(
                "INSERT INTO relations(repo_id, file_id, kind, target, line) VALUES(?,?,?,?,?)",
                [(repo_id, file_id, r.kind, r.target, r.line) for r in parsed.relations],
            )
        st["changed" if existing else "added"] += 1

    def _embed_pending(self, repo_id: int, st: dict) -> int:
        """Embed chunks lacking a vector for the active embedding model. Failures degrade to lexical-only."""
        db = self.rt.db
        embedder = self.rt.embedder()
        done = 0
        pending_sql = (
            "SELECT c.id, f.rel_path, c.symbol, c.text FROM chunks c JOIN files f ON f.id = c.file_id "
            "WHERE c.repo_id = ? AND (c.embedding IS NULL OR c.embed_model IS NOT ?) LIMIT ?"
        )
        while True:
            rows = db.query(pending_sql, (repo_id, embedder.name, EMBED_BATCH))
            if not rows:
                return done
            try:
                vectors = embedder.embed([f"{r['rel_path']}\n{r['symbol'] or ''}\n{r['text']}" for r in rows])
            except Exception as exc:
                log.warning("Embedding failed (%s); retrieval falls back to keyword search", exc)
                st["warnings"].append(f"Embedding failed ({type(exc).__name__}: {exc}). Keyword search still works; fix the embedding settings and re-index.")
                return done
            with db.write() as conn:
                conn.executemany(
                    "UPDATE chunks SET embedding = ?, embed_model = ? WHERE id = ?",
                    [(vector_to_blob(v), embedder.name, r["id"]) for r, v in zip(rows, vectors)],
                )
            done += len(rows)
