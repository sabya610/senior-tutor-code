"""Health, dashboard and settings endpoints."""
from __future__ import annotations

import time
from collections import Counter
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException

from app import __version__
from app.api.deps import SettingsUpdate, get_runtime
from app.llm.base import LLMError
from app.llm.ollama_provider import list_ollama_models
from app.runtime import OVERRIDABLE, Runtime

router = APIRouter(tags=["system"])


@router.get("/health")
def health(rt: Runtime = Depends(get_runtime)):
    llm = rt.llm()
    return {"status": "ok", "version": __version__, "llm": llm.describe(), "embeddings": rt.embedder().name}


@router.get("/dashboard")
def dashboard(rt: Runtime = Depends(get_runtime)):
    repos = rt.store.list_repos()
    results = rt.store.list_quiz_results(limit=500)
    gaps = Counter(g for r in results for g in r["gaps"])
    return {
        "repos": repos,
        "recent_sessions": rt.store.list_sessions(limit=8),
        "recent_knowledge": [{k: v for k, v in e.items() if k != "content"} for e in rt.store.list_explanations(limit=8)],
        "quiz": {
            "attempts": len(results),
            "average_score": round(sum(r["score"] for r in results) / len(results), 1) if results else None,
            "top_gaps": [{"gap": g, "count": c} for g, c in gaps.most_common(6)],
        },
        "llm": rt.llm().describe(),
    }


def _config_payload(rt: Runtime) -> dict:
    cfg = rt.effective_config()
    ollama = list_ollama_models(cfg["ollama_base_url"], timeout=1.5)
    return {
        "config": cfg,
        "openai_key_configured": bool(rt.settings.openai_api_key),
        "ollama": {"reachable": ollama is not None, "models": ollama or []},
        "active": {"llm": rt.llm().describe(), "embeddings": rt.embedder().name},
        "allowed_roots": [str(p) for p in rt.settings.allowed_root_list],
    }


@router.get("/settings")
def get_settings_view(rt: Runtime = Depends(get_runtime)):
    return _config_payload(rt)


@router.put("/settings")
def update_settings(body: SettingsUpdate, rt: Runtime = Depends(get_runtime)):
    values = {k: v for k, v in body.model_dump().items() if v is not None and k in OVERRIDABLE}
    for key in ("ollama_base_url", "openai_base_url"):
        url = values.get(key)
        if url and (urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc):
            raise HTTPException(400, f"{key} must be an http(s) URL")
    before = rt.embedder().name
    rt.set_overrides(values)
    payload = _config_payload(rt)
    payload["reindex_recommended"] = rt.embedder().name != before
    return payload


@router.post("/settings/test")
def test_settings(rt: Runtime = Depends(get_runtime)):
    llm = rt.llm()
    if llm.is_mock:
        reason = getattr(llm, "config_error", None) or "No LLM provider is available (set OPENAI_API_KEY or start Ollama)."
        return {"ok": False, "provider": llm.describe(), "error": reason}
    started = time.monotonic()
    try:
        reply = llm.complete([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=16, temperature=0)
    except LLMError as exc:
        return {"ok": False, "provider": llm.describe(), "error": str(exc)}
    return {"ok": True, "provider": llm.describe(), "latency_ms": int((time.monotonic() - started) * 1000), "reply": reply[:80]}
