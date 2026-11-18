"""Teacher: the single entry point for every learning mode (explain, flow, feature, compare, chat, troubleshoot)."""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from app.llm.base import LLMError
from app.retrieval.retriever import Hit
from app.teaching import prompts, troubleshoot
from app.teaching.context import build_context, check_citations
from app.teaching.evidence import render_evidence

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

MODES = ("explain", "flow", "feature", "compare", "chat", "troubleshoot")
DEPTHS = ("beginner", "intermediate", "expert")
CACHEABLE = {"explain", "flow", "feature", "compare"}
DEICTIC = re.compile(r"\b(this|that|these|those|it|its|previous|above|earlier|same|prior|last|former|latter|there)\b", re.I)
FOLLOWUP_START = re.compile(r"^\s*(why|what about|and|so|then|how about|but|also|what if)\b", re.I)
MAX_LOG_CHARS = 8000
NO_EVIDENCE = (
    "I could not find anything in the indexed source that relates to this question, so I will not guess.\n\n"
    "Try: naming a specific file, function, CRD or Helm value; selecting a feature in the header; or re-indexing the "
    "repository if the code is new."
)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def is_followup(question: str) -> bool:
    words = question.split()
    return bool(words) and len(words) <= 16 and bool(DEICTIC.search(question) or FOLLOWUP_START.match(question))


def parse_json_loose(text: str) -> dict:
    """Parse JSON from an LLM reply that may be wrapped in a code fence or surrounded by prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


class Teacher:
    def __init__(self, rt: "Runtime"):
        self.rt = rt

    # ------------------------------------------------------------------ helpers shared with quiz / session prep
    def require_repo(self, repo_id: int) -> dict:
        repo = self.rt.store.get_repo(repo_id)
        if not repo:
            raise LookupError("Repository not found")
        if not self.rt.db.one("SELECT 1 AS x FROM chunks WHERE repo_id = ? LIMIT 1", (repo_id,)):
            raise ValueError("This repository has not been indexed yet. Open My Repositories and index it first.")
        return repo

    def gather(self, repo_id: int, queries: list[str], k: int, budget: int | None = None,
               scope_paths: list[str] | None = None, pinned: list[Hit] | None = None) -> tuple[str, list[dict], list[Hit]]:
        retriever = self.rt.retriever
        hits = retriever.search(repo_id, queries[0], k=k, scope_paths=scope_paths) if len(queries) == 1 \
            else retriever.search_multi(repo_id, queries, k=k, scope_paths=scope_paths)
        return build_context(hits, budget or self.rt.settings.context_char_budget, pinned)

    def complete(self, messages: list[dict], json_mode: bool = False, max_tokens: int | None = None) -> str:
        s = self.rt.settings
        return self.rt.llm().complete(messages, temperature=s.llm_temperature,
                                      max_tokens=max_tokens or s.llm_max_tokens, json_mode=json_mode)

    # ------------------------------------------------------------------ main entry point
    def ask(self, *, repo_id: int, mode: str, question: str = "", depth: str = "intermediate",
            feature_id: int | None = None, compare_with: str | None = None,
            session_id: int | None = None, force: bool = False) -> dict:
        rt, store, s = self.rt, self.rt.store, self.rt.settings
        repo = self.require_repo(repo_id)
        mode = mode if mode in MODES else "explain"
        depth = depth if depth in DEPTHS else "intermediate"
        question = (question or "").strip()
        compare_with = (compare_with or "").strip() or None

        feature = store.get_feature(feature_id) if feature_id else None
        if feature_id and (not feature or feature["repo_id"] != repo_id):
            raise LookupError("Feature not found in this repository")
        if not question and feature:
            question = f"Explain the {feature['name']} feature"
        if not question:
            raise ValueError("A question or topic is required")
        if mode == "compare" and not compare_with:
            raise ValueError("Compare mode needs a second concept to compare with")

        history: list[dict] = []
        if session_id:
            session = store.get_session(session_id)
            if not session or session["repo_id"] != repo_id:
                raise LookupError("Session not found")
            history = store.get_messages(session_id)

        scope_paths = feature["files"] if feature else []
        search_query = f"{feature['name']} {question}" if feature else question
        pinned: list[Hit] = []
        resolved: dict | None = None
        prior = next((m for m in reversed(history) if m["role"] == "assistant" and m["sources"]), None)
        if prior and mode in ("chat", "explain", "flow", "feature") and is_followup(question):
            topic = prior["meta"].get("topic") or question
            search_query = f"{topic}. {question}"
            for src in prior["sources"][:4]:
                pinned += rt.retriever.chunks_for_range(repo_id, src["path"], src["start_line"], src["end_line"])[:1]
            resolved = {"topic": topic, "pinned": [f"{h.path}:{h.start_line}-{h.end_line}" for h in pinned]}
        topic = resolved["topic"] if resolved else question

        llm = rt.llm()
        provider_label = f"{llm.name}:{llm.model}"
        cache_key = "||".join([mode, depth, normalise(question), str(feature_id or ""), normalise(compare_with or ""), provider_label])
        if mode in CACHEABLE and not force and not resolved and not llm.is_mock:
            cached = store.find_cached_explanation(repo_id, cache_key)
            if cached:
                session_id = self._record(repo_id, session_id, mode, question, cached["content"], cached["sources"], feature,
                                          {"topic": topic, "mode": mode, "depth": depth, "cached": True,
                                           "provider": llm.name, "model": llm.model}, compare_with)
                return self._result(session_id, cached["content"], cached["sources"], llm, cached=True,
                                    explanation_id=cached["id"], citations=check_citations(cached["content"], cached["sources"]),
                                    resolved=resolved, warnings=[])

        signals = located = groups = None
        k = s.top_k * 2 if mode in ("flow", "feature", "troubleshoot") else s.top_k
        if mode == "troubleshoot":
            signals = troubleshoot.extract_signals(question)
            located = troubleshoot.locate_frames(rt.db, repo_id, signals["frames"])
            for frame in located[:6]:
                pinned += rt.retriever.chunks_for_range(repo_id, frame["rel_path"], frame["line"] - 2, frame["line"] + 2)[:1]
            hits = rt.retriever.search(repo_id, troubleshoot.retrieval_query(question, signals), k=k, scope_paths=scope_paths)
        elif mode == "compare":
            half = max(4, s.top_k // 2 + 1)
            first = rt.retriever.search(repo_id, question, k=half)
            second = rt.retriever.search(repo_id, compare_with, k=half)  # type: ignore[arg-type]
            groups = [(question, first), (compare_with, second)]
            hits = [h for pair in zip(first, second) for h in pair] + first[len(second):] + second[len(first):]
        else:
            hits = rt.retriever.search(repo_id, search_query, k=k, scope_paths=scope_paths)

        context, sources, used = build_context(hits, s.context_char_budget, pinned)
        warnings: list[str] = []
        llm_failed = False
        answer: str

        if not used:
            answer = NO_EVIDENCE
        elif llm.is_mock:
            notice = getattr(llm, "config_error", None)
            answer = render_evidence(rt, repo, mode, question, used, sources, groups, signals, located,
                                     notice=f"> **Evidence-only mode.** LLM configuration problem: {notice}" if notice else None)
        else:
            try:
                answer = llm.complete(self._messages(mode, depth, question, compare_with, feature, context, history,
                                                     signals, bool(pinned)),
                                      temperature=s.llm_temperature, max_tokens=s.llm_max_tokens)
            except LLMError as exc:
                log.warning("LLM call failed: %s", exc)
                warnings.append(f"The LLM call failed ({exc}). Showing an evidence-only report instead.")
                answer = render_evidence(rt, repo, mode, question, used, sources, groups, signals, located,
                                         notice=f"> **LLM unavailable:** {exc}. Showing evidence from the index only.")
                llm_failed = True

        citations = check_citations(answer, sources)
        if used and not llm.is_mock and not llm_failed:
            for bad in citations["invalid"]:
                answer = answer.replace(f"[{bad}]", "[unverified citation]")
            if citations["invalid"]:
                warnings.append(f"The model cited {', '.join(citations['invalid'])}, which were not in the retrieved context; "
                                "those citations were marked unverified.")
            if not citations["cited"]:
                warnings.append("The answer contains no source citations; treat it as unverified.")

        meta = {"topic": topic, "mode": mode, "depth": depth, "provider": llm.name, "model": llm.model,
                "mock": llm.is_mock, "warnings": warnings}
        session_id = self._record(repo_id, session_id, mode, question, answer, sources, feature, meta, compare_with)

        explanation_id = None
        if mode in CACHEABLE and used and not llm.is_mock and not llm_failed:
            explanation_id = store.save_explanation(repo_id, mode, question, answer, sources, provider_label,
                                                    repo["index_rev"], cache_key)
        return self._result(session_id, answer, sources, llm, cached=False, explanation_id=explanation_id,
                            citations=citations, resolved=resolved, warnings=warnings)

    # ------------------------------------------------------------------ internals
    def _messages(self, mode: str, depth: str, question: str, compare_with: str | None, feature: dict | None,
                  context: str, history: list[dict], signals: dict | None, has_pinned: bool) -> list[dict]:
        messages = [{"role": "system", "content": prompts.system_prompt(mode, depth)}]
        for m in history[-self.rt.settings.history_messages:]:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": m["content"][:2500]})

        parts = [f"CONTEXT (numbered excerpts from the repository):\n\n{context}"]
        if has_pinned:
            parts.append("Note: the first excerpts are the code cited in the previous answer; the question may refer to them.")
        if feature:
            parts.append(f"Selected feature: {feature['name']} ({feature['kind']}, root {feature['root_path']}). {feature.get('description') or ''}")
        if mode == "compare":
            parts.append(f"COMPARE:\n  A = {question}\n  B = {compare_with}")
        elif mode == "troubleshoot":
            parts.append(f"REPORTED PROBLEM / LOGS:\n```\n{question[:MAX_LOG_CHARS]}\n```")
            if signals and signals["hints"]:
                parts.append("Recognised patterns: " + "; ".join(h["label"] for h in signals["hints"]))
        else:
            parts.append(f"QUESTION / TOPIC: {question}")
        messages.append({"role": "user", "content": "\n\n---\n".join(parts)})
        return messages

    def _record(self, repo_id: int, session_id: int | None, mode: str, question: str, answer: str, sources: list[dict],
                feature: dict | None, meta: dict, compare_with: str | None) -> int:
        store = self.rt.store
        if not session_id:
            title = question if mode != "troubleshoot" else "Troubleshooting: " + question.splitlines()[0]
            session_id = store.create_session(repo_id, mode, title[:120], feature["name"] if feature else None)
        store.add_message(session_id, "user", question if not compare_with else f"{question}  vs  {compare_with}",
                          meta={"mode": mode, "feature": feature["name"] if feature else None})
        store.add_message(session_id, "assistant", answer, sources, meta)
        return session_id

    def _result(self, session_id: int, answer: str, sources: list[dict], llm, *, cached: bool, explanation_id: int | None,
                citations: dict, resolved: dict | None, warnings: list[str]) -> dict:
        return {
            "session_id": session_id,
            "answer": answer,
            "sources": sources,
            "provider": llm.describe(),
            "cached": cached,
            "explanation_id": explanation_id,
            "citations": citations,
            "follow_up": resolved,
            "warnings": warnings,
        }
