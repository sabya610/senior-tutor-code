"""Feature detection: derives selectable features/components from verified symbols."""
from __future__ import annotations

import posixpath
from collections import Counter

from app.db import Database, dumps

MAX_FEATURE_FILES = 300
SKIP_COMPONENT_DIRS = {"docs", "doc", "test", "tests", ".github", "node_modules", "examples", "samples"}


def _dir(path: str) -> str:
    d = posixpath.dirname(path)
    return d


def _base(path: str, fallback: str) -> str:
    return posixpath.basename(path) or fallback


def detect_features(db: Database, repo_id: int, repo_name: str) -> list[dict]:
    files = db.query("SELECT rel_path, language FROM files WHERE repo_id = ?", (repo_id,))
    all_paths = [f["rel_path"] for f in files]
    syms = db.query(
        "SELECT s.name, s.kind, s.start_line, s.parent, f.rel_path AS path FROM symbols s "
        "JOIN files f ON f.id = s.file_id WHERE s.repo_id = ? "
        "AND s.kind IN ('helm_chart','entrypoint','reconciler','webhook','crd','command') "
        "ORDER BY f.rel_path, s.start_line",
        (repo_id,),
    )

    def under(root: str) -> list[str]:
        prefix = f"{root}/" if root else ""
        return [p for p in all_paths if p.startswith(prefix)][:MAX_FEATURE_FILES]

    def lang_summary(paths: list[str]) -> str:
        wanted = set(paths)
        by_lang = Counter(f["language"] for f in files if f["rel_path"] in wanted)
        return ", ".join(f"{n} {lang}" for lang, n in by_lang.most_common(4))

    features: dict[tuple[str, str, str], dict] = {}

    def add(kind: str, name: str, root: str, description: str, paths: list[str]) -> None:
        key = (kind, root, name)
        if key not in features:
            features[key] = {"name": name, "kind": kind, "root_path": root, "description": description, "files": paths[:MAX_FEATURE_FILES]}

    commands: list[dict] = []
    for s in syms:
        root = _dir(s["path"])
        if s["kind"] == "helm_chart":
            paths = under(root)
            add("helm-chart", s["name"], root, f"Helm chart `{s['name']}` at `{root or '.'}` ({len(paths)} files: {lang_summary(paths)}).", paths)
        elif s["kind"] == "entrypoint" and s["path"].endswith(".go") and s["name"] == "main":
            paths = under(root)
            add("service", _base(root, repo_name), root, f"Go program with `main` at {s['path']}:{s['start_line']} ({len(paths)} files in directory tree).", paths)
        elif s["kind"] == "entrypoint" and s["path"].endswith(".py") and "app (" in s["name"]:
            paths = under(root)
            add("api", f"{_base(root, repo_name)} ({s['name'].split(' ')[0]})", root, f"{s['name']} defined at {s['path']}:{s['start_line']}.", paths)
        elif s["kind"] == "reconciler":
            name = s["parent"] or s["name"].split(".")[0]
            paths = [p for p in under(root) if p.startswith(f"{root}/") and "/" not in p[len(root) + 1:]] if root else under("")
            add("controller", name, root, f"Reconciler `{s['name']}` at {s['path']}:{s['start_line']}.", paths)
        elif s["kind"] == "webhook":
            name = s["parent"] or s["name"]
            add("webhook", name, root, f"Admission webhook logic `{s['name']}` at {s['path']}:{s['start_line']}.", [s["path"]])
        elif s["kind"] == "crd":
            add("crd", s["name"], root, f"CustomResourceDefinition for `{s['name']}` at {s['path']}:{s['start_line']}.", [s["path"]])
        elif s["kind"] == "command":
            commands.append(s)

    if commands:
        root = posixpath.commonpath([_dir(c["path"]) for c in commands]) if len(commands) > 1 else _dir(commands[0]["path"])
        names = ", ".join(sorted({c["name"] for c in commands})[:25])
        add("cli", f"{repo_name} CLI", root, f"Cobra command definitions ({len(commands)}): {names}.", under(root))

    used_roots = {f["root_path"] for f in features.values()}
    top_dirs = Counter(p.split("/")[0] for p in all_paths if "/" in p)
    for d, count in top_dirs.most_common(40):
        if count >= 3 and d not in SKIP_COMPONENT_DIRS and not d.startswith(".") and d not in used_roots:
            paths = under(d)
            add("component", d, d, f"Top-level directory `{d}/` with {count} indexed files ({lang_summary(paths)}).", paths)

    rows = list(features.values())
    with db.write() as conn:
        conn.execute("DELETE FROM features WHERE repo_id = ?", (repo_id,))
        conn.executemany(
            "INSERT INTO features(repo_id, name, kind, root_path, description, files_json) VALUES(?,?,?,?,?,?)",
            [(repo_id, r["name"], r["kind"], r["root_path"], r["description"], dumps(r["files"])) for r in rows],
        )
    return rows
