"""Optional LLM narrative for the Architecture Overview, grounded in the index summary and retrieved code."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.analysis.architecture import render_markdown
from app.llm.base import LLMError
from app.teaching import prompts
from app.teaching.context import check_citations

if TYPE_CHECKING:
    from app.runtime import Runtime

OVERVIEW_QUERIES = ["main entry point application startup", "controller reconcile handler", "api routes http server",
                    "helm chart deployment configuration", "kubernetes manifests custom resource definition"]


def architecture_narrative(rt: "Runtime", repo_id: int, force: bool = False) -> dict:
    teacher = rt.teacher
    repo = teacher.require_repo(repo_id)
    summary = repo.get("summary")
    if not summary:
        raise ValueError("The architecture summary is not ready yet; index the repository first.")
    facts = render_markdown(summary)
    llm = rt.llm()
    if llm.is_mock:
        return {"content": facts, "sources": [], "provider": llm.describe(), "cached": False, "warnings": [
            "No LLM configured: showing the index-derived architecture map without a narrative."]}

    label = f"{llm.name}:{llm.model}"
    cache_key = f"architecture||{label}"
    if not force:
        cached = rt.store.find_cached_explanation(repo_id, cache_key)
        if cached and cached["index_rev"] == repo["index_rev"]:
            return {"content": cached["content"], "sources": cached["sources"], "provider": llm.describe(),
                    "cached": True, "warnings": []}

    s = rt.settings
    context, sources, _ = teacher.gather(repo_id, OVERVIEW_QUERIES, s.top_k * 2, s.context_char_budget)
    messages = [
        {"role": "system", "content": prompts.SYSTEM_BASE + "\n\n" + prompts.DEPTH["intermediate"]},
        {"role": "user", "content": f"{prompts.ARCHITECTURE_NARRATIVE}\n\nVERIFIED FACTS FROM THE INDEX:\n{facts[:9000]}\n\n"
                                    f"CONTEXT:\n\n{context}"},
    ]
    warnings: list[str] = []
    try:
        content = teacher.complete(messages)
    except LLMError as exc:
        return {"content": facts, "sources": [], "provider": llm.describe(), "cached": False,
                "warnings": [f"The LLM call failed ({exc}); showing the index-derived map instead."]}
    citations = check_citations(content, sources)
    for bad in citations["invalid"]:
        content = content.replace(f"[{bad}]", "[unverified citation]")
    if citations["invalid"]:
        warnings.append("Some citations referenced excerpts that were not retrieved and were marked unverified.")
    explanation_id = rt.store.save_explanation(repo_id, "architecture", f"Architecture of {repo['name']}", content, sources,
                                               label, repo["index_rev"], cache_key)
    return {"content": content, "sources": sources, "provider": llm.describe(), "cached": False,
            "explanation_id": explanation_id, "warnings": warnings}
