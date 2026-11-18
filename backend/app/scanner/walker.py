"""Repository walking: decides which files are worth indexing."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "build", "dist", "out", "target", "bin", "obj",
    "venv", ".venv", "env", ".env", "virtualenv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".idea", ".vscode", ".gradle", ".m2", "site-packages", "vendor",
    ".next", ".nuxt", "coverage", ".cache", ".terraform", "bower_components", ".eggs",
}

IGNORED_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "composer.lock", "npm-shrinkwrap.json", ".DS_Store", "uv.lock",
}

IGNORED_SUFFIXES = (
    ".min.js", ".min.css", ".pb.go", ".pb.gw.go", ".map", ".lock", ".svg", ".pyc", "_generated.go",
)

IGNORED_PREFIXES = ("zz_generated",)

EXTENSION_LANGUAGES = {
    ".py": "python", ".go": "go", ".java": "java",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".yaml": "yaml", ".yml": "yaml", ".tpl": "helm", ".json": "json",
    ".md": "markdown", ".markdown": "markdown", ".rst": "markdown", ".txt": "text",
    ".sh": "shell", ".bash": "shell", ".mk": "make",
    ".toml": "config", ".ini": "config", ".cfg": "config", ".conf": "config",
    ".proto": "config", ".sql": "config", ".xml": "config", ".gradle": "config",
    ".properties": "config", ".template": "config", ".tmpl": "config", ".mod": "config",
}

FILENAME_LANGUAGES = {
    "Dockerfile": "docker", "Makefile": "make", "makefile": "make", "GNUmakefile": "make",
}

BINARY_PROBE_BYTES = 8192


@dataclass(frozen=True)
class ScannedFile:
    rel_path: str
    abs_path: Path
    size: int
    mtime_ns: int
    language: str


def detect_language(filename: str) -> str | None:
    if filename in IGNORED_FILENAMES:
        return None
    lower = filename.lower()
    if lower.endswith(IGNORED_SUFFIXES) or lower.startswith(IGNORED_PREFIXES):
        return None
    if filename in FILENAME_LANGUAGES:
        return FILENAME_LANGUAGES[filename]
    if filename.startswith("Dockerfile"):
        return "docker"
    # README / LICENSE-like files without extension are documentation
    if filename.upper() in {"README", "CHANGELOG", "CONTRIBUTING"}:
        return "markdown"
    return EXTENSION_LANGUAGES.get(Path(filename).suffix.lower())


def is_ignored_dir(name: str) -> bool:
    return name in IGNORED_DIRS or name.endswith(".egg-info")


def looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\0" in fh.read(BINARY_PROBE_BYTES)
    except OSError:
        return True


def iter_repo_files(root: Path, max_bytes: int) -> Iterator[ScannedFile]:
    """Yield indexable files under *root*, pruning ignored directories in place."""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not is_ignored_dir(d))
        for name in sorted(filenames):
            language = detect_language(name)
            if language is None:
                continue
            path = Path(dirpath) / name
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_size == 0 or st.st_size > max_bytes:
                continue
            if looks_binary(path):
                continue
            rel = path.relative_to(root).as_posix()
            yield ScannedFile(rel, path, st.st_size, st.st_mtime_ns, language)


def is_generated(text: str) -> bool:
    head = "\n".join(text.splitlines()[:5]).lower()
    return "code generated" in head and "do not edit" in head


def discover_repositories(root: Path, max_depth: int = 2) -> list[dict]:
    """Find candidate repositories (git repos, or dirs with recognisable sources) under *root*."""
    root = Path(root)
    found: list[dict] = []

    def walk(directory: Path, depth: int) -> None:
        if (directory / ".git").exists():
            found.append({"name": directory.name, "path": str(directory), "git": True})
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir() and not is_ignored_dir(p.name) and not p.name.startswith("."))
        except OSError:
            return
        for child in children:
            walk(child, depth + 1)

    if (root / ".git").exists():
        return [{"name": root.name, "path": str(root), "git": True}]
    walk(root, 0)
    return found
