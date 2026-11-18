"""Evidence-only reports, used when no LLM is configured or the LLM call fails. Everything shown comes from the index."""
from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

from app.retrieval.retriever import Hit

if TYPE_CHECKING:
    from app.runtime import Runtime

BANNER = (
    "> **Evidence-only mode.** No LLM is available, so no prose explanation is generated. Below is exactly what the index "
    "found for your question, with `[S#]` references to the source excerpts. Set `OPENAI_API_KEY`, or start Ollama and pick "
    "a model in **Settings**, to get full teaching answers."
)

_RANK = {"entrypoint": 0, "route": 0, "command": 0, "webhook": 1, "reconciler": 1, "crd": 2}


def _rank(kind: str) -> int:
    if kind in _RANK:
        return _RANK[kind]
    if kind in ("function", "method", "class", "struct", "interface", "type"):
        return 3
    if kind.startswith("k8s:") or kind.startswith("helm"):
        return 4
    return 5


def _loc(hit: Hit) -> str:
    return f"`{hit.path}:{hit.start_line}-{hit.end_line}`"


def _cite(ids: dict[int, str], hit: Hit) -> str:
    return f"[{ids[hit.chunk_id]}]" if hit.chunk_id in ids else ""


def _table(hits: list[Hit], ids: dict[int, str]) -> list[str]:
    rows = ["| Ref | Location | Symbol | Kind |", "|---|---|---|---|"]
    for h in hits:
        rows.append(f"| {_cite(ids, h)} | {_loc(h)} | {h.symbol or ''} | {h.kind} |")
    return rows


def _snippet(hit: Hit, ids: dict[int, str], max_lines: int = 14) -> list[str]:
    lines = hit.text.splitlines()[:max_lines]
    width = len(str(hit.start_line + len(lines)))
    body = "\n".join(f"{hit.start_line + i:>{width}}| {line}" for i, line in enumerate(lines))
    more = "\n... (truncated; open the file for the rest)" if len(hit.text.splitlines()) > max_lines else ""
    return [f"**{_cite(ids, hit)} {_loc(hit)}**" + (f" — `{hit.symbol}`" if hit.symbol else ""), "```", body + more, "```"]


def _import_graph(rt: "Runtime", repo_id: int, hits: list[Hit]) -> list[str]:
    file_ids = sorted({h.file_id for h in hits})
    if len(file_ids) < 2:
        return []
    marks = ",".join("?" * len(file_ids))
    rows = rt.db.query(
        f"SELECT file_id, resolved_file_id FROM relations WHERE repo_id = ? AND kind = 'import' "
        f"AND file_id IN ({marks}) AND resolved_file_id IN ({marks})",
        [repo_id, *file_ids, *file_ids],
    )
    edges = {(r["file_id"], r["resolved_file_id"]) for r in rows if r["file_id"] != r["resolved_file_id"]}
    if not edges:
        return []
    names = {h.file_id: posixpath.basename(h.path) for h in hits}
    clean = lambda s: re.sub(r'["\[\]()<>:{}|]', "", s)
    used = {n for e in edges for n in e}
    out = ["", "### Import relationships between these files (verified from source imports)", "", "```mermaid", "graph LR"]
    out += [f'    n{fid}["{clean(names[fid])}"]' for fid in sorted(used)]
    out += [f"    n{a} --> n{b}" for a, b in sorted(edges)]
    out.append("```")
    return out


def render_evidence(rt: "Runtime", repo: dict, mode: str, question: str, hits: list[Hit], sources: list[dict],
                    groups: list[tuple[str, list[Hit]]] | None = None, signals: dict | None = None,
                    located: list[dict] | None = None, notice: str | None = None) -> str:
    ids = {h.chunk_id: s["id"] for h, s in zip(hits, sources)}
    shown = [h for h in hits if h.chunk_id in ids]
    out = [notice or BANNER, "", f"## {mode.replace('_', ' ').title()} evidence for: {question.strip()[:200]}", ""]

    if mode == "troubleshoot" and signals is not None:
        out += _troubleshoot_section(signals, located or [], ids, shown)
    elif groups and mode == "compare":
        for label, group in groups:
            group = [h for h in group if h.chunk_id in ids]
            out += [f"### {label}", ""]
            out += _table(group, ids) if group else ["_Nothing relevant found in this repository._"]
            out.append("")
    elif mode == "flow":
        ordered = sorted(shown, key=lambda h: _rank(h.kind))
        out += ["### Probable reading order", "",
                "_Derived from symbol roles (entry points and routes first, then controllers, then implementation, then manifests). "
                "The call graph was **not** traced, so treat this as a starting order, not a verified sequence._", ""]
        out += [f"{i}. {_cite(ids, h)} {_loc(h)} — {h.symbol or h.kind} _({h.kind})_" for i, h in enumerate(ordered, 1)]
    else:
        out += ["### Most relevant locations", ""] + _table(shown, ids)

    k8s = [h for h in shown if h.kind.startswith("k8s:")]
    if k8s:
        out += ["", "### Kubernetes resources found", ""]
        out += [f"- `{h.kind[4:]}` {h.symbol or ''} — {_loc(h)} {_cite(ids, h)}" for h in k8s]

    out += ["", "### Source excerpts", ""]
    for hit in shown[:6]:
        out += _snippet(hit, ids) + [""]
    if len(shown) > 6:
        out.append(f"_{len(shown) - 6} more matching excerpt(s) are listed in the Sources panel._")

    out += _import_graph(rt, repo["id"], shown)
    return "\n".join(out).strip()


def _troubleshoot_section(signals: dict, located: list[dict], ids: dict[int, str], shown: list[Hit]) -> list[str]:
    out: list[str] = []
    if signals["error_lines"]:
        out += ["### Error lines found in the report", ""] + [f"- `{l[:200]}`" for l in signals["error_lines"]] + [""]
    if signals["frames"]:
        out += ["### Stack frames and where they live in this repository", "",
                "| Frame in the report | Repository file | Function |", "|---|---|---|"]
        by_path = {f"{l['path']}:{l['line']}": l for l in located}
        for f in signals["frames"][:20]:
            hit = by_path.get(f"{f['path']}:{f['line']}")
            target = f"`{hit['rel_path']}:{hit['line']}`" + (" (ambiguous match)" if hit and hit.get("ambiguous") else "") if hit else "_not in this repository_"
            out.append(f"| `{f['path']}:{f['line']}` | {target} | {f['func'] or ''} |")
        out.append("")
    if signals["hints"]:
        out += ["### Known failure patterns recognised", "",
                "_General platform guidance, not derived from this repository's code._", ""]
        for hint in signals["hints"]:
            out += [f"**{hint['label']}** — {hint['meaning']}", "", "```bash", *hint["commands"], "```", ""]
    if not (signals["error_lines"] or signals["frames"] or signals["hints"]):
        out += ["_No recognisable error lines, stack frames or known failure patterns were found in the pasted text. "
                "Paste the failing log lines, `kubectl describe` output or the full traceback._", ""]
    out += ["### Code that matches the report", ""] + (_table(shown, ids) if shown else ["_No matching code found._"])
    return out
