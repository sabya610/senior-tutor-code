"""Context management: turn retrieval hits into numbered, line-referenced context blocks."""
from __future__ import annotations

import re

from app.retrieval.retriever import Hit

MAX_CHUNK_CHARS = 3500
CITATION = re.compile(r"\[S(\d+)\]")


def source_of(hit: Hit, number: int) -> dict:
    return {
        "id": f"S{number}",
        "path": hit.path,
        "start_line": hit.start_line,
        "end_line": hit.end_line,
        "symbol": hit.symbol,
        "language": hit.language,
        "kind": hit.kind,
        "sha1": hit.sha1,
    }


def format_block(src: dict, text: str) -> str:
    lines = text.splitlines()
    width = len(str(src["end_line"]))
    numbered = "\n".join(f"{src['start_line'] + i:>{width}}| {line}" for i, line in enumerate(lines))
    label = f" (symbol: {src['symbol']})" if src.get("symbol") else ""
    return f"[{src['id']}] {src['path']}:{src['start_line']}-{src['end_line']}{label} [{src['language']}]\n```\n{numbered}\n```"


def build_context(hits: list[Hit], budget: int, pinned: list[Hit] | None = None) -> tuple[str, list[dict], list[Hit]]:
    """Return (context_text, sources, used_hits). Pinned hits come first, then ranked hits, within the char budget."""
    ordered: list[Hit] = []
    seen: set[int] = set()
    for hit in list(pinned or []) + hits:
        if hit.chunk_id not in seen:
            seen.add(hit.chunk_id)
            ordered.append(hit)

    blocks: list[str] = []
    sources: list[dict] = []
    used: list[Hit] = []
    total = 0
    for hit in ordered:
        text = hit.text
        if len(text) > MAX_CHUNK_CHARS:
            cut = text[:MAX_CHUNK_CHARS].rsplit("\n", 1)[0]
            text = cut + "\n... [truncated]"
            end_line = hit.start_line + cut.count("\n")
        else:
            end_line = hit.end_line
        src = source_of(hit, len(sources) + 1)
        src["end_line"] = end_line
        block = format_block(src, text)
        if total + len(block) > budget and blocks:
            continue
        blocks.append(block)
        sources.append(src)
        used.append(hit)
        total += len(block)
    return "\n\n".join(blocks), sources, used


def check_citations(answer: str, sources: list[dict]) -> dict:
    """Verify that every [S#] marker the model emitted refers to a real retrieved block."""
    valid_ids = {s["id"] for s in sources}
    cited = sorted({f"S{n}" for n in CITATION.findall(answer)}, key=lambda s: int(s[1:]))
    invalid = [c for c in cited if c not in valid_ids]
    return {"cited": [c for c in cited if c in valid_ids], "invalid": invalid, "available": len(valid_ids)}
