"""Architecture map: a deterministic, evidence-backed summary of an indexed repository."""
from __future__ import annotations

import posixpath
import re
from collections import Counter

from app.db import Database

COMPONENT_WRAPPERS = {"src", "backend", "app", "services", "pkg", "internal", "cmd", "packages", "libs", "lib", "api", "controllers"}


def _component(path: str) -> str:
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] in COMPONENT_WRAPPERS and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _external_name(language: str, target: str) -> str | None:
    if target.startswith("."):
        return None
    if language == "python":
        return target.split(".")[0]
    if language == "go":
        return "/".join(target.split("/")[:3]) if "." in target.split("/")[0] else target.split("/")[0]
    if language in ("javascript", "typescript"):
        parts = target.split("/")
        return "/".join(parts[:2]) if target.startswith("@") else parts[0]
    if language == "java":
        return ".".join(target.split(".")[:2])
    return None


def _symbols(db: Database, repo_id: int, kinds: tuple[str, ...], limit: int) -> list[dict]:
    marks = ",".join("?" * len(kinds))
    return db.query(
        f"SELECT s.name, s.kind, s.start_line AS line, s.signature, f.rel_path AS path FROM symbols s "
        f"JOIN files f ON f.id = s.file_id WHERE s.repo_id = ? AND s.kind IN ({marks}) "
        f"ORDER BY f.rel_path, s.start_line LIMIT ?",
        (repo_id, *kinds, limit),
    )


def build_summary(db: Database, repo_id: int, repo_name: str) -> dict:
    languages = db.query(
        "SELECT language, COUNT(*) AS files, COALESCE(SUM(line_count),0) AS lines FROM files "
        "WHERE repo_id = ? GROUP BY language ORDER BY files DESC",
        (repo_id,),
    )
    totals = {
        "files": sum(l["files"] for l in languages),
        "lines": sum(l["lines"] for l in languages),
        "chunks": db.one("SELECT COUNT(*) AS n FROM chunks WHERE repo_id = ?", (repo_id,))["n"],
        "symbols": db.one("SELECT COUNT(*) AS n FROM symbols WHERE repo_id = ?", (repo_id,))["n"],
    }
    k8s_kinds = db.query(
        "SELECT substr(kind, 5) AS kind, COUNT(*) AS count FROM symbols WHERE repo_id = ? AND kind LIKE 'k8s:%' "
        "GROUP BY kind ORDER BY count DESC",
        (repo_id,),
    )
    route_count = db.one("SELECT COUNT(*) AS n FROM symbols WHERE repo_id = ? AND kind = 'route'", (repo_id,))["n"]
    images = db.query(
        "SELECT r.target, MIN(f.rel_path) AS path, MIN(r.line) AS line, COUNT(*) AS uses FROM relations r "
        "JOIN files f ON f.id = r.file_id WHERE r.repo_id = ? AND r.kind = 'image' GROUP BY r.target "
        "ORDER BY uses DESC LIMIT 60",
        (repo_id,),
    )

    ext_counter: Counter[str] = Counter()
    for row in db.query(
        "SELECT r.target, f.language FROM relations r JOIN files f ON f.id = r.file_id "
        "WHERE r.repo_id = ? AND r.kind = 'import' AND r.resolved_file_id IS NULL LIMIT 60000",
        (repo_id,),
    ):
        name = _external_name(row["language"], row["target"])
        if name:
            ext_counter[name] += 1

    edge_counter: Counter[tuple[str, str]] = Counter()
    for row in db.query(
        "SELECT f.rel_path AS src, t.rel_path AS dst FROM relations r JOIN files f ON f.id = r.file_id "
        "JOIN files t ON t.id = r.resolved_file_id WHERE r.repo_id = ? AND r.kind = 'import'",
        (repo_id,),
    ):
        a, b = _component(row["src"]), _component(row["dst"])
        if a != b:
            edge_counter[(a, b)] += 1

    paths = db.query("SELECT rel_path, language FROM files WHERE repo_id = ? ORDER BY rel_path", (repo_id,))
    docs = [p["rel_path"] for p in paths if p["language"] == "markdown" and (posixpath.basename(p["rel_path"]).upper().startswith("README") or p["rel_path"].startswith(("docs/", "doc/")))][:40]
    dockerfiles = [p["rel_path"] for p in paths if p["language"] == "docker"][:30]
    ci = [p["rel_path"] for p in paths if p["rel_path"].startswith((".github/workflows/", ".gitlab-ci")) or posixpath.basename(p["rel_path"]) == "Jenkinsfile"][:30]
    scripts = [p["rel_path"] for p in paths if p["language"] == "shell"][:30]

    features = db.query("SELECT id, name, kind, root_path, description FROM features WHERE repo_id = ? ORDER BY kind, name", (repo_id,))

    summary = {
        "repo": repo_name,
        "totals": totals,
        "languages": languages,
        "entrypoints": _symbols(db, repo_id, ("entrypoint",), 40),
        "routes": _symbols(db, repo_id, ("route",), 120),
        "route_count": route_count,
        "controllers": _symbols(db, repo_id, ("reconciler",), 40),
        "webhooks": _symbols(db, repo_id, ("webhook",), 40),
        "crds": _symbols(db, repo_id, ("crd",), 60),
        "commands": _symbols(db, repo_id, ("command",), 60),
        "helm_charts": _symbols(db, repo_id, ("helm_chart",), 60),
        "k8s_kinds": k8s_kinds,
        "images": images,
        "external_dependencies": [{"name": n, "imports": c} for n, c in ext_counter.most_common(40)],
        "component_edges": [{"from": a, "to": b, "count": c} for (a, b), c in edge_counter.most_common(60)],
        "docs": docs,
        "dockerfiles": dockerfiles,
        "ci": ci,
        "scripts": scripts,
        "features": features,
    }
    summary["mermaid_components"] = mermaid_components(summary)
    summary["mermaid_features"] = mermaid_features(summary)
    return summary


def _node_id(prefix: str, name: str) -> str:
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]', '_', name)}"


def _label(text: str) -> str:
    return text.replace('"', "'")[:60]


def mermaid_components(summary: dict) -> str:
    edges = summary["component_edges"][:30]
    if not edges:
        return ""
    lines = ["graph LR"]
    nodes: dict[str, str] = {}
    for e in edges:
        for name in (e["from"], e["to"]):
            nodes.setdefault(name, _node_id("c", name))
    for name, nid in nodes.items():
        lines.append(f'    {nid}["{_label(name)}"]')
    for e in edges:
        lines.append(f"    {nodes[e['from']]} -->|{e['count']}| {nodes[e['to']]}")
    return "\n".join(lines)


def mermaid_features(summary: dict) -> str:
    features = summary["features"][:30]
    if not features:
        return ""
    lines = ["graph TD", f'    R["{_label(summary["repo"])}"]']
    for i, f in enumerate(features):
        lines.append(f'    F{i}["{_label(f["name"])}<br/>{f["kind"]}"]')
        lines.append(f"    R --> F{i}")
    return "\n".join(lines)


def render_markdown(summary: dict) -> str:
    t = summary["totals"]
    out = [f"# Architecture map: {summary['repo']}", "",
           f"_Generated from indexed source only: {t['files']} files, {t['lines']:,} lines, {t['symbols']:,} symbols, {t['chunks']:,} chunks._", ""]
    out += ["## Languages", "", "| Language | Files | Lines |", "|---|---:|---:|"]
    out += [f"| {l['language']} | {l['files']} | {l['lines']:,} |" for l in summary["languages"]]

    def section(title: str, rows: list[dict], fmt) -> None:
        if rows:
            out.extend(["", f"## {title}", ""])
            out.extend(f"- {fmt(r)}" for r in rows)

    loc = lambda r: f"`{r['path']}:{r['line']}`"
    section("Entry points", summary["entrypoints"], lambda r: f"**{r['name']}** — {loc(r)}")
    section("Kubernetes controllers / reconcilers", summary["controllers"], lambda r: f"**{r['name']}** — {loc(r)}")
    section("Admission webhooks", summary["webhooks"], lambda r: f"**{r['name']}** — {loc(r)}")
    section("Custom resource definitions", summary["crds"], lambda r: f"**{r['name']}** — {loc(r)}")
    section("CLI commands", summary["commands"], lambda r: f"**{r['name']}** — {loc(r)}")
    section("Helm charts", summary["helm_charts"], lambda r: f"**{r['name']}** — {loc(r)}")
    if summary["route_count"]:
        out.extend(["", f"## HTTP routes ({summary['route_count']})", ""])
        out.extend(f"- `{r['name']}` — {loc(r)}" for r in summary["routes"][:40])
    if summary["k8s_kinds"]:
        out.extend(["", "## Kubernetes resource kinds defined", "", "| Kind | Count |", "|---|---:|"])
        out.extend(f"| {k['kind']} | {k['count']} |" for k in summary["k8s_kinds"][:30])
    if summary["images"]:
        out.extend(["", "## Container images referenced", ""])
        out.extend(f"- `{i['target']}` — `{i['path']}:{i['line']}` ({i['uses']} use(s))" for i in summary["images"][:25])
    if summary["external_dependencies"]:
        out.extend(["", "## Most-imported external dependencies", ""])
        out.extend(f"- `{d['name']}` ({d['imports']} imports)" for d in summary["external_dependencies"][:20])
    for title, key in (("Documentation", "docs"), ("Dockerfiles", "dockerfiles"), ("CI / pipelines", "ci"), ("Scripts", "scripts")):
        if summary[key]:
            out.extend(["", f"## {title}", ""])
            out.extend(f"- `{p}`" for p in summary[key][:20])
    if summary["mermaid_components"]:
        out.extend(["", "## Component dependency graph (resolved imports)", "", "```mermaid", summary["mermaid_components"], "```"])
    elif summary["mermaid_features"]:
        out.extend(["", "## Feature map", "", "```mermaid", summary["mermaid_features"], "```"])
    return "\n".join(out)
