"""SQLite persistence: schema, connection helper and tiny query utilities."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_indexed_at TEXT,
    index_rev INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha1 TEXT NOT NULL,
    line_count INTEGER NOT NULL,
    UNIQUE(repo_id, rel_path)
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    repo_id INTEGER NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    kind TEXT NOT NULL,
    symbol TEXT,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    embedding BLOB,
    embed_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, symbol, path, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT,
    parent TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_repo_name ON symbols(repo_id, name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);
CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    resolved_file_id INTEGER,
    line INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relations_file ON relations(file_id);
CREATE INDEX IF NOT EXISTS idx_relations_repo_kind ON relations(repo_id, kind);
CREATE TABLE IF NOT EXISTS features (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    root_path TEXT NOT NULL,
    description TEXT,
    files_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_features_repo ON features(repo_id);
CREATE TABLE IF NOT EXISTS explanations (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    provider TEXT,
    index_rev INTEGER NOT NULL DEFAULT 0,
    cache_key TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_explanations_repo ON explanations(repo_id, mode);
CREATE INDEX IF NOT EXISTS idx_explanations_cache ON explanations(repo_id, cache_key);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    title TEXT NOT NULL,
    feature TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS quiz_results (
    id INTEGER PRIMARY KEY,
    repo_id INTEGER REFERENCES repos(id) ON DELETE CASCADE,
    session_id INTEGER,
    topic TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    score REAL NOT NULL,
    feedback TEXT NOT NULL,
    gaps_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    """Thin thread-safe wrapper: one short-lived connection per operation."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock, self.connect() as conn:
            yield conn

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.write() as conn:
            return conn.execute(sql, tuple(params)).lastrowid

    # settings helpers -------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default
