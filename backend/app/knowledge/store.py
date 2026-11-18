"""Persistent knowledge store: repositories, files, sessions, explanations, bookmarks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db import Database, dumps, loads


class KnowledgeStore:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------- repositories
    def add_repo(self, name: str, path: str) -> dict:
        existing = self.db.one("SELECT * FROM repos WHERE path = ?", (path,))
        if existing:
            return existing
        repo_id = self.db.execute("INSERT INTO repos(name, path) VALUES(?, ?)", (name, path))
        return self.get_repo(repo_id)  # type: ignore[return-value]

    def get_repo(self, repo_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM repos WHERE id = ?", (repo_id,))
        if row:
            row["summary"] = loads(row.pop("summary_json"), None)
        return row

    def list_repos(self) -> list[dict]:
        rows = self.db.query(
            "SELECT r.id, r.name, r.path, r.created_at, r.last_indexed_at, r.index_rev, "
            "(SELECT COUNT(*) FROM files f WHERE f.repo_id = r.id) AS file_count, "
            "(SELECT COUNT(*) FROM chunks c WHERE c.repo_id = r.id) AS chunk_count, "
            "(SELECT COUNT(*) FROM symbols s WHERE s.repo_id = r.id) AS symbol_count "
            "FROM repos r ORDER BY r.name"
        )
        return rows

    def delete_repo(self, repo_id: int) -> None:
        with self.db.write() as conn:
            conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE repo_id = ?)", (repo_id,))
            conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))

    def save_summary(self, repo_id: int, summary: dict) -> None:
        self.db.execute("UPDATE repos SET summary_json = ? WHERE id = ?", (dumps(summary), repo_id))

    # ------------------------------------------------------------- files / symbols
    def file_by_path(self, repo_id: int, rel_path: str) -> dict | None:
        return self.db.one("SELECT * FROM files WHERE repo_id = ? AND rel_path = ?", (repo_id, rel_path))

    def list_files(self, repo_id: int) -> list[dict]:
        return self.db.query(
            "SELECT id, rel_path, language, size, line_count, sha1 FROM files WHERE repo_id = ? ORDER BY rel_path",
            (repo_id,),
        )

    def search_symbols(self, repo_id: int, query: str = "", kind: str = "", limit: int = 100) -> list[dict]:
        sql = (
            "SELECT s.id, s.name, s.kind, s.start_line, s.end_line, s.signature, s.parent, f.rel_path AS path "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.repo_id = ?"
        )
        params: list[Any] = [repo_id]
        if query:
            sql += " AND s.name LIKE ?"
            params.append(f"%{query}%")
        if kind:
            sql += " AND s.kind = ?"
            params.append(kind)
        sql += " ORDER BY f.rel_path, s.start_line LIMIT ?"
        params.append(limit)
        return self.db.query(sql, params)

    # ------------------------------------------------------------- features
    def list_features(self, repo_id: int) -> list[dict]:
        rows = self.db.query("SELECT * FROM features WHERE repo_id = ? ORDER BY kind, name", (repo_id,))
        for row in rows:
            row["files"] = loads(row.pop("files_json"), [])
        return rows

    def get_feature(self, feature_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM features WHERE id = ?", (feature_id,))
        if row:
            row["files"] = loads(row.pop("files_json"), [])
        return row

    # ------------------------------------------------------------- explanations (knowledge base)
    def _current_sha(self, repo_id: int, path: str) -> str | None:
        row = self.file_by_path(repo_id, path)
        return row["sha1"] if row else None

    def is_stale(self, repo_id: int, sources: list[dict]) -> bool:
        """An explanation is stale when any file it cited changed or disappeared."""
        for src in sources:
            sha = src.get("sha1")
            if sha and self._current_sha(repo_id, src["path"]) != sha:
                return True
        return False

    def save_explanation(self, repo_id: int, mode: str, topic: str, content: str, sources: list[dict],
                         provider: str, index_rev: int, cache_key: str | None = None) -> int:
        return self.db.execute(
            "INSERT INTO explanations(repo_id, mode, topic, content, sources_json, provider, index_rev, cache_key) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (repo_id, mode, topic, content, dumps(sources), provider, index_rev, cache_key),
        )

    def _explanation_row(self, row: dict) -> dict:
        row["sources"] = loads(row.pop("sources_json"), [])
        row["stale"] = self.is_stale(row["repo_id"], row["sources"])
        return row

    def get_explanation(self, explanation_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM explanations WHERE id = ?", (explanation_id,))
        return self._explanation_row(row) if row else None

    def find_cached_explanation(self, repo_id: int, cache_key: str) -> dict | None:
        row = self.db.one(
            "SELECT * FROM explanations WHERE repo_id = ? AND cache_key = ? ORDER BY id DESC LIMIT 1",
            (repo_id, cache_key),
        )
        if not row:
            return None
        row = self._explanation_row(row)
        return None if row["stale"] else row

    def list_explanations(self, repo_id: int | None = None, mode: str = "", limit: int = 200) -> list[dict]:
        sql, params = "SELECT * FROM explanations WHERE 1=1", []
        if repo_id:
            sql += " AND repo_id = ?"
            params.append(repo_id)
        if mode:
            sql += " AND mode = ?"
            params.append(mode)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [self._explanation_row(r) for r in self.db.query(sql, params)]

    def delete_explanation(self, explanation_id: int) -> None:
        self.db.execute("DELETE FROM explanations WHERE id = ?", (explanation_id,))

    # ------------------------------------------------------------- sessions / messages
    def create_session(self, repo_id: int | None, mode: str, title: str, feature: str | None = None) -> int:
        return self.db.execute(
            "INSERT INTO sessions(repo_id, mode, title, feature) VALUES(?,?,?,?)",
            (repo_id, mode, title[:200], feature),
        )

    def get_session(self, session_id: int) -> dict | None:
        return self.db.one("SELECT * FROM sessions WHERE id = ?", (session_id,))

    def add_message(self, session_id: int, role: str, content: str, sources: list[dict] | None = None,
                    meta: dict | None = None) -> int:
        msg_id = self.db.execute(
            "INSERT INTO messages(session_id, role, content, sources_json, meta_json) VALUES(?,?,?,?,?)",
            (session_id, role, content, dumps(sources or []), dumps(meta or {})),
        )
        self.db.execute("UPDATE sessions SET updated_at = datetime('now') WHERE id = ?", (session_id,))
        return msg_id

    def get_messages(self, session_id: int, limit: int | None = None) -> list[dict]:
        rows = self.db.query("SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,))
        for row in rows:
            row["sources"] = loads(row.pop("sources_json"), [])
            row["meta"] = loads(row.pop("meta_json"), {})
        return rows[-limit:] if limit else rows

    def list_sessions(self, repo_id: int | None = None, limit: int = 100) -> list[dict]:
        sql = (
            "SELECT s.*, r.name AS repo_name, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count "
            "FROM sessions s LEFT JOIN repos r ON r.id = s.repo_id"
        )
        params: list[Any] = []
        if repo_id:
            sql += " WHERE s.repo_id = ?"
            params.append(repo_id)
        sql += " ORDER BY s.updated_at DESC, s.id DESC LIMIT ?"
        params.append(limit)
        return self.db.query(sql, params)

    def delete_session(self, session_id: int) -> None:
        self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ------------------------------------------------------------- bookmarks
    def add_bookmark(self, repo_id: int | None, title: str, content: str, sources: list[dict]) -> int:
        return self.db.execute(
            "INSERT INTO bookmarks(repo_id, title, content, sources_json) VALUES(?,?,?,?)",
            (repo_id, title[:200], content, dumps(sources)),
        )

    def list_bookmarks(self, repo_id: int | None = None) -> list[dict]:
        sql, params = "SELECT * FROM bookmarks", []
        if repo_id:
            sql += " WHERE repo_id = ?"
            params.append(repo_id)
        rows = self.db.query(sql + " ORDER BY id DESC", params)
        for row in rows:
            row["sources"] = loads(row.pop("sources_json"), [])
        return rows

    def delete_bookmark(self, bookmark_id: int) -> None:
        self.db.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))

    # ------------------------------------------------------------- quiz results
    def add_quiz_result(self, repo_id: int | None, session_id: int | None, topic: str, question: str,
                        answer: str, score: float, feedback: str, gaps: list[str]) -> int:
        return self.db.execute(
            "INSERT INTO quiz_results(repo_id, session_id, topic, question, answer, score, feedback, gaps_json) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (repo_id, session_id, topic, question, answer, score, feedback, dumps(gaps)),
        )

    def list_quiz_results(self, repo_id: int | None = None, limit: int = 200) -> list[dict]:
        sql, params = "SELECT * FROM quiz_results", []
        if repo_id:
            sql += " WHERE repo_id = ?"
            params.append(repo_id)
        rows = self.db.query(sql + " ORDER BY id DESC LIMIT ?", params + [limit])
        for row in rows:
            row["gaps"] = loads(row.pop("gaps_json"), [])
        return rows


def safe_join(root: str | Path, rel_path: str) -> Path | None:
    """Resolve rel_path under root; return None if it escapes root (path traversal guard)."""
    base = Path(root).resolve()
    target = (base / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target
