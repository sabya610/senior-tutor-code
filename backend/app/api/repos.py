"""Repository management, exploration, architecture and search endpoints."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analysis.architecture import render_markdown
from app.api.deps import IndexRequest, RepoCreate, get_runtime
from app.knowledge.store import safe_join
from app.runtime import Runtime
from app.scanner.walker import discover_repositories
from app.teaching.narrative import architecture_narrative

router = APIRouter(tags=["repositories"])


def _repo(rt: Runtime, repo_id: int) -> dict:
    repo = rt.store.get_repo(repo_id)
    if not repo:
        raise HTTPException(404, "Repository not found")
    return repo


@router.get("/repos")
def list_repos(rt: Runtime = Depends(get_runtime)):
    repos = rt.store.list_repos()
    for r in repos:
        r["index"] = rt.indexer.get_status(r["id"])
    return repos


@router.post("/repos", status_code=201)
def add_repo(body: RepoCreate, rt: Runtime = Depends(get_runtime)):
    path = rt.check_repo_path(body.path)
    return rt.store.add_repo(body.name or path.name, str(path))


@router.get("/repos/discover")
def discover(root: str = Query(min_length=1, max_length=1024), rt: Runtime = Depends(get_runtime)):
    base = rt.check_repo_path(root)
    known = {r["path"] for r in rt.store.list_repos()}
    found = discover_repositories(base)
    for item in found:
        item["registered"] = item["path"] in known
    return {"root": str(base), "repositories": found[:500]}


@router.delete("/repos/{repo_id}", status_code=204)
def delete_repo(repo_id: int, rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    rt.store.delete_repo(repo_id)


@router.post("/repos/{repo_id}/index")
def start_index(repo_id: int, body: IndexRequest | None = None, rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    return rt.indexer.start(repo_id, full=bool(body and body.full))


@router.get("/repos/{repo_id}/index/status")
def index_status(repo_id: int, rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    return rt.indexer.get_status(repo_id)


@router.get("/repos/{repo_id}/files")
def list_files(repo_id: int, rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    return [{"path": f["rel_path"], "language": f["language"], "size": f["size"], "lines": f["line_count"]}
            for f in rt.store.list_files(repo_id)]


@router.get("/repos/{repo_id}/file")
def read_file(repo_id: int, path: str = Query(min_length=1, max_length=1024), start: int = Query(1, ge=1),
              end: int | None = Query(None, ge=1), rt: Runtime = Depends(get_runtime)):
    """Return source lines. Only indexed files inside the repository root can be read."""
    repo = _repo(rt, repo_id)
    row = rt.store.file_by_path(repo_id, path)
    target = safe_join(repo["path"], path) if row else None
    if not row or not target or not target.is_file():
        raise HTTPException(404, "File not found in this repository's index")
    if target.stat().st_size > rt.settings.max_file_bytes:
        raise HTTPException(413, "File is too large to display")
    data = target.read_bytes()
    lines = data.decode("utf-8", errors="replace").splitlines()
    last = min(end or len(lines), len(lines))
    start = min(start, max(len(lines), 1))
    symbols = [s for s in rt.store.search_symbols(repo_id, limit=2000) if s["path"] == path]
    return {
        "path": path,
        "language": row["language"],
        "total_lines": len(lines),
        "start": start,
        "end": last,
        "lines": lines[start - 1:last],
        "changed_since_index": hashlib.sha1(data).hexdigest() != row["sha1"],
        "symbols": [{"name": s["name"], "kind": s["kind"], "start_line": s["start_line"], "end_line": s["end_line"]} for s in symbols],
    }


@router.get("/repos/{repo_id}/symbols")
def symbols(repo_id: int, q: str = Query("", max_length=200), kind: str = Query("", max_length=50),
            limit: int = Query(100, ge=1, le=500), rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    return rt.store.search_symbols(repo_id, q, kind, limit)


@router.get("/repos/{repo_id}/search")
def search(repo_id: int, q: str = Query(min_length=1, max_length=500), k: int = Query(12, ge=1, le=40),
           rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    return [{"path": h.path, "start_line": h.start_line, "end_line": h.end_line, "symbol": h.symbol, "kind": h.kind,
             "language": h.language, "score": round(h.score, 5), "via": h.via, "preview": h.text[:400]}
            for h in rt.retriever.search(repo_id, q, k=k)]


@router.get("/repos/{repo_id}/architecture")
def architecture(repo_id: int, rt: Runtime = Depends(get_runtime)):
    repo = _repo(rt, repo_id)
    if not repo.get("summary"):
        raise HTTPException(409, "This repository has not been indexed yet")
    return {"summary": repo["summary"], "markdown": render_markdown(repo["summary"])}


@router.post("/repos/{repo_id}/architecture/narrative")
def narrative(repo_id: int, force: bool = False, rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    return architecture_narrative(rt, repo_id, force)


@router.get("/repos/{repo_id}/features")
def features(repo_id: int, rt: Runtime = Depends(get_runtime)):
    _repo(rt, repo_id)
    return rt.store.list_features(repo_id)


@router.get("/features/{feature_id}")
def feature(feature_id: int, rt: Runtime = Depends(get_runtime)):
    row = rt.store.get_feature(feature_id)
    if not row:
        raise HTTPException(404, "Feature not found")
    return row
