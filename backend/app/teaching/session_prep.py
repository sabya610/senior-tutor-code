"""Session preparation: a 14-section study document and presentation script for a feature or topic."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.llm.base import LLMError
from app.teaching import prompts
from app.teaching.context import check_citations
from app.teaching.evidence import _rank
from app.teaching.orchestrator import normalise

if TYPE_CHECKING:
    from app.retrieval.retriever import Hit
    from app.runtime import Runtime

log = logging.getLogger(__name__)
MOCK_NOTE = "_Requires an LLM to write; not generated in evidence-only mode. Configure a model in **Settings** and regenerate._"


class SessionPrep:
    def __init__(self, rt: "Runtime"):
        self.rt = rt

    def generate(self, repo_id: int, topic: str, feature_id: int | None = None, force: bool = False) -> dict:
        rt, teacher, s = self.rt, self.rt.teacher, self.rt.settings
        repo = teacher.require_repo(repo_id)
        feature = rt.store.get_feature(feature_id) if feature_id else None
        if feature_id and (not feature or feature["repo_id"] != repo_id):
            raise LookupError("Feature not found in this repository")
        topic = topic.strip() or (f"the {feature['name']} feature" if feature else "")
        if not topic:
            raise ValueError("A topic or a selected feature is required")

        llm = rt.llm()
        label = f"{llm.name}:{llm.model}"
        cache_key = f"session_prep||{normalise(topic)}||{feature_id or ''}||{label}"
        if not force and not llm.is_mock:
            cached = rt.store.find_cached_explanation(repo_id, cache_key)
            if cached:
                return self._result(cached["content"], cached["sources"], llm, True, cached["id"], [], None)

        base = f"{feature['name']} {topic}" if feature else topic
        queries = [base, f"{base} request flow entry point handler", f"{base} configuration helm values deployment",
                   f"{base} error handling failure retry validation"]
        context, sources, used = teacher.gather(repo_id, queries, s.top_k * 3, int(s.context_char_budget * 1.5),
                                                feature["files"] if feature else None)
        if not used:
            raise ValueError("No indexed code matches this topic, so a grounded session plan cannot be written.")

        warnings: list[str] = []
        failed = False
        if llm.is_mock:
            body = self._evidence_document(repo, topic, used, sources)
        else:
            sections = []
            for name, instruction in prompts.SESSION_PREP_SECTIONS:
                try:
                    sections.append(teacher.complete([
                        {"role": "system", "content": prompts.SYSTEM_BASE + "\n\n" + prompts.DEPTH["intermediate"]},
                        {"role": "user", "content": f"CONTEXT:\n\n{context}\n\n---\nTOPIC FOR THE SESSION: {topic}\n\n{instruction}"}],
                        max_tokens=max(s.llm_max_tokens, 3000)))
                except LLMError as exc:
                    failed = True
                    warnings.append(f"Sections {name} could not be generated: {exc}")
                    sections.append(f"## Sections {name}\n\n_Generation failed: {exc}_")
            body = "\n\n".join(sections)
            citations = check_citations(body, sources)
            for bad in citations["invalid"]:
                body = body.replace(f"[{bad}]", "[unverified citation]")
            if citations["invalid"]:
                warnings.append(f"The model cited {', '.join(citations['invalid'])}, which were not retrieved; marked unverified.")

        content = self._wrap(repo, topic, body, sources, llm.is_mock)
        explanation_id = None
        if not llm.is_mock and not failed:
            explanation_id = rt.store.save_explanation(repo_id, "session_prep", topic, content, sources, label,
                                                       repo["index_rev"], cache_key)
        session_id = rt.store.create_session(repo_id, "session_prep", f"Session prep: {topic}"[:120], feature["name"] if feature else None)
        rt.store.add_message(session_id, "user", topic, meta={"mode": "session_prep"})
        rt.store.add_message(session_id, "assistant", content, sources, {"mode": "session_prep", "topic": topic, "mock": llm.is_mock})
        return self._result(content, sources, llm, False, explanation_id, warnings, session_id)

    # ------------------------------------------------------------------ rendering
    @staticmethod
    def _wrap(repo: dict, topic: str, body: str, sources: list[dict], mock: bool) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        head = [f"# Session preparation: {topic}", "",
                f"_Repository **{repo['name']}** · generated {stamp} from indexed source only · {len(sources)} source excerpts_", ""]
        if mock:
            head += ["> **Evidence-only mode.** Sections that need prose are marked as such. The evidence sections are complete and cited.", ""]
        appendix = ["", "---", "", "## Appendix: sources", "", "| Ref | Location | Symbol |", "|---|---|---|"]
        appendix += [f"| [{s['id']}] | `{s['path']}:{s['start_line']}-{s['end_line']}` | {s.get('symbol') or ''} |" for s in sources]
        return "\n".join(head) + body + "\n".join(appendix)

    @staticmethod
    def _evidence_document(repo: dict, topic: str, hits: list["Hit"], sources: list[dict]) -> str:
        ids = {h.chunk_id: s["id"] for h, s in zip(hits, sources)}
        ref = lambda h: f"[{ids[h.chunk_id]}]"
        ordered = sorted(hits, key=lambda h: _rank(h.kind))
        files: dict[str, list["Hit"]] = {}
        for h in hits:
            files.setdefault(h.path, []).append(h)
        code = [h for h in hits if h.kind in ("function", "method", "class", "struct", "interface", "type", "reconciler", "webhook", "route", "entrypoint", "command")]
        k8s = [h for h in hits if h.kind.startswith(("k8s:", "helm"))]
        cfg = [h for h in hits if h.kind in ("config", "script") or h.path.endswith((".yaml", ".yml", ".tpl", ".sh"))]

        out = ["## 1-4. Overview, problem, rationale, architecture", "", MOCK_NOTE, "",
               "Where this topic lives in the code:", ""]
        out += [f"- `{p}` — {len(hs)} relevant excerpt(s) {' '.join(ref(h) for h in hs)}" for p, hs in files.items()]
        out += ["", "## 5. End-to-end request flow", "",
                "_Probable reading order from symbol roles (call graph not traced):_", ""]
        out += [f"{i}. {ref(h)} `{h.path}:{h.start_line}-{h.end_line}` — {h.symbol or h.kind}" for i, h in enumerate(ordered[:12], 1)]
        out += ["", "## 6. Important repositories, directories and files", "", "| File | Excerpts | Languages |", "|---|---|---|"]
        out += [f"| `{p}` | {' '.join(ref(h) for h in hs)} | {hs[0].language} |" for p, hs in files.items()]
        out += ["", "## 7. Important functions and their responsibilities", "", "| Symbol | Location | Kind |", "|---|---|---|"]
        out += [f"| {h.symbol or ''} {ref(h)} | `{h.path}:{h.start_line}-{h.end_line}` | {h.kind} |" for h in code[:25]] or ["| _none found_ | | |"]
        out += ["", "## 8. Integration with other components", "", MOCK_NOTE]
        if k8s:
            out += ["", "Kubernetes / Helm resources found:", ""]
            out += [f"- `{h.kind}` {h.symbol or ''} — `{h.path}:{h.start_line}` {ref(h)}" for h in k8s]
        out += ["", "## 9. Configuration and deployment", ""]
        out += [f"- `{h.path}:{h.start_line}-{h.end_line}` {ref(h)} — {h.symbol or h.kind}" for h in cfg[:15]] or ["_No configuration excerpts retrieved._"]
        for n, title in ((10, "Failure scenarios and troubleshooting"), (11, "Technical decisions and design considerations"),
                         (12, "Potential questions from senior engineers"), (13, "Suggested answers"),
                         (14, "Presentation scripts (5-minute and 15-minute)")):
            out += ["", f"## {n}. {title}", "", MOCK_NOTE]
        return "\n".join(out)

    @staticmethod
    def _result(content: str, sources: list[dict], llm, cached: bool, explanation_id: int | None,
                warnings: list[str], session_id: int | None) -> dict:
        return {"content": content, "sources": sources, "provider": llm.describe(), "cached": cached,
                "explanation_id": explanation_id, "warnings": warnings, "session_id": session_id}
