"""Best-effort resolution of import-like relations to files inside the same repository."""
from __future__ import annotations

import posixpath
import re
from pathlib import Path

from app.db import Database

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
GO_MODULE = re.compile(r"^module\s+(\S+)", re.MULTILINE)


def _suffix_index(files: list[dict], ext: str) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for f in files:
        path = f["rel_path"]
        if not path.endswith(ext):
            continue
        parts = path[: -len(ext)].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            index.setdefault(".".join(parts[i:]), []).append(f)
    return index


def _pick(candidates: list[dict], importer: str, single_component: bool) -> int | None:
    if not candidates:
        return None
    imp_parts = importer.split("/")
    best, best_shared = None, -1
    for cand in candidates:
        shared = 0
        for a, b in zip(imp_parts[:-1], cand["rel_path"].split("/")[:-1]):
            if a != b:
                break
            shared += 1
        if shared > best_shared:
            best, best_shared = cand, shared
    if single_component and best_shared < 1 and "/" in importer:
        return None  # avoid mapping a bare name like "config" to an unrelated top-level directory
    return best["id"] if best else None


def _go_modules(root: Path, files: list[dict]) -> list[tuple[str, str]]:
    modules: list[tuple[str, str]] = []
    for f in files:
        if f["rel_path"].endswith("go.mod"):
            try:
                text = (root / f["rel_path"]).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            m = GO_MODULE.search(text)
            if m:
                modules.append((posixpath.dirname(f["rel_path"]), m.group(1)))
    return modules


def resolve_relations(db: Database, repo_id: int, root: Path) -> int:
    files = db.query("SELECT id, rel_path, language FROM files WHERE repo_id = ?", (repo_id,))
    by_path = {f["rel_path"]: f["id"] for f in files}
    py_index = _suffix_index([f for f in files if f["language"] == "python"], ".py")
    java_index = _suffix_index([f for f in files if f["language"] == "java"], ".java")
    go_modules = _go_modules(root, files)
    go_dirs: dict[str, int] = {}
    for f in files:
        if f["language"] == "go":
            go_dirs.setdefault(posixpath.dirname(f["rel_path"]), f["id"])

    rows = db.query(
        "SELECT r.id, r.kind, r.target, f.rel_path, f.language FROM relations r JOIN files f ON f.id = r.file_id "
        "WHERE r.repo_id = ? AND r.kind IN ('import', 'import_name', 'source')",
        (repo_id,),
    )
    updates: list[tuple[int | None, int]] = []
    for row in rows:
        target, importer, lang = row["target"], row["rel_path"], row["language"]
        resolved: int | None = None
        if lang == "python":
            resolved = _resolve_python(target, importer, by_path, py_index)
        elif lang == "go":
            for mod_dir, module in go_modules:
                if target == module or target.startswith(module + "/"):
                    sub = target[len(module):].lstrip("/")
                    pkg_dir = posixpath.normpath(posixpath.join(mod_dir, sub)) if (mod_dir or sub) else ""
                    pkg_dir = "" if pkg_dir == "." else pkg_dir
                    resolved = go_dirs.get(pkg_dir)
                    if resolved:
                        break
        elif lang in ("typescript", "javascript") and target.startswith("."):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), target))
            for cand in [base] + [base + e for e in JS_EXTS] + [f"{base}/index{e}" for e in JS_EXTS]:
                if cand in by_path:
                    resolved = by_path[cand]
                    break
        elif lang == "java":
            parts = target.split(".")
            for cut in range(0, min(3, len(parts))):
                key = ".".join(parts[: len(parts) - cut])
                if key in java_index:
                    resolved = _pick(java_index[key], importer, False)
                    break
        elif lang == "shell" and row["kind"] == "source":
            cand = posixpath.normpath(posixpath.join(posixpath.dirname(importer), target))
            resolved = by_path.get(cand)
        updates.append((resolved, row["id"]))

    with db.write() as conn:
        conn.executemany("UPDATE relations SET resolved_file_id = ? WHERE id = ?", updates)
    return sum(1 for r, _ in updates if r)


def _resolve_python(target: str, importer: str, by_path: dict[str, int], py_index: dict[str, list[dict]]) -> int | None:
    if target.startswith("."):
        level = len(target) - len(target.lstrip("."))
        rest = target[level:]
        base_parts = importer.split("/")[:-1]
        if level > 1:
            base_parts = base_parts[: len(base_parts) - (level - 1)] if len(base_parts) >= level - 1 else []
        path_parts = base_parts + (rest.split(".") if rest else [])
        for cand in ("/".join(path_parts) + ".py", "/".join(path_parts + ["__init__.py"])):
            if cand in by_path:
                return by_path[cand]
        return None
    parts = target.split(".")
    for cut in range(0, min(2, len(parts))):
        key = ".".join(parts[: len(parts) - cut])
        if key in py_index:
            return _pick(py_index[key], importer, key.count(".") == 0)
    return None
