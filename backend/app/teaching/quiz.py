"""Quiz, interview and answer evaluation. Uses LLM JSON when available, otherwise a transparent heuristic."""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.llm.base import LLMError
from app.retrieval.embeddings import tokenize
from app.retrieval.retriever import STOPWORDS, Hit
from app.teaching import prompts
from app.teaching.orchestrator import parse_json_loose

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger(__name__)

CODE_KEYWORDS = {
    "func", "function", "def", "class", "return", "import", "from", "var", "const", "let", "type", "struct", "interface",
    "package", "self", "this", "nil", "none", "null", "true", "false", "else", "elif", "for", "range", "while", "with",
    "err", "error", "string", "int", "bool", "ctx", "context", "args", "kwargs", "async", "await", "try", "except",
    "raise", "pass", "lambda", "public", "private", "static", "void", "new", "case", "switch", "break", "continue",
}
DOC_LINE = re.compile(r'^\s*(?://+|#+|\*+|"""|\'\'\')\s*(.{12,})$')


def _terms(text: str, minimum: int = 4) -> list[str]:
    seen: dict[str, None] = {}
    for tok in tokenize(text):
        if len(tok) >= minimum and tok not in STOPWORDS and tok not in CODE_KEYWORDS and not tok.isdigit():
            seen.setdefault(tok)
    return list(seen)


def _doc_line(hit: Hit) -> str | None:
    for line in hit.text.splitlines()[:6]:
        m = DOC_LINE.match(line)
        if m and not m.group(1).lower().startswith(("copyright", "license", "nolint", "kubebuilder")):
            return m.group(1).strip(" \"'")
    return None


class Quizzer:
    def __init__(self, rt: "Runtime"):
        self.rt = rt

    # ------------------------------------------------------------------ question generation
    def generate(self, repo_id: int, topic: str, count: int = 5, feature_id: int | None = None,
                 interview: bool = False) -> dict:
        teacher = self.rt.teacher
        teacher.require_repo(repo_id)
        feature = self.rt.store.get_feature(feature_id) if feature_id else None
        topic = topic.strip() or (f"the {feature['name']} feature" if feature else "")
        if not topic:
            raise ValueError("A topic or a selected feature is required")
        count = 1 if interview else max(1, min(count, 10))
        scope = feature["files"] if feature else None
        context, sources, used = teacher.gather(repo_id, [f"{feature['name']} {topic}" if feature else topic],
                                                self.rt.settings.top_k, scope_paths=scope)
        if not used:
            return {"topic": topic, "questions": [], "sources": [], "mock": self.rt.llm().is_mock,
                    "warnings": ["No indexed code matches this topic, so no questions can be grounded."]}

        llm, warnings = self.rt.llm(), []
        questions: list[dict] = []
        if not llm.is_mock:
            try:
                system = prompts.SYSTEM_BASE + "\n\n" + (prompts.INTERVIEW_PROMPT if interview else prompts.QUIZ_PROMPT.format(count=count))
                raw = teacher.complete([{"role": "system", "content": system},
                                        {"role": "user", "content": f"CONTEXT:\n\n{context}\n\n---\nTOPIC: {topic}"}], json_mode=True)
                questions = [q for q in parse_json_loose(raw).get("questions", []) if q.get("question")][:count]
            except (LLMError, ValueError) as exc:
                log.warning("Question generation via LLM failed: %s", exc)
                warnings.append(f"The LLM could not generate questions ({exc}); using code-derived questions instead.")
        if not questions:
            questions = self._heuristic_questions(used, sources, count)
        for i, q in enumerate(questions, 1):
            q["id"] = i
            q["expected_points"] = [str(p) for p in q.get("expected_points", [])][:6]
            q.setdefault("source", None)
        return {"topic": topic, "questions": questions, "sources": sources, "mock": llm.is_mock or bool(warnings),
                "provider": llm.describe(), "warnings": warnings}

    def _heuristic_questions(self, hits: list[Hit], sources: list[dict], count: int) -> list[dict]:
        templates = [
            "What is the responsibility of `{sym}` in `{path}`, and what does it do step by step?",
            "Where is `{sym}` defined, and which other components or files does it interact with?",
            "What would happen to the system if `{sym}` (in `{path}`) failed or returned an error? Explain using the code.",
        ]
        questions, seen = [], set()
        for hit, src in zip(hits, sources):
            if not hit.symbol or hit.symbol in seen or hit.symbol == "module":
                continue
            seen.add(hit.symbol)
            points = [p for p in (_doc_line(hit),) if p] + _terms(hit.text)[:5]
            questions.append({
                "question": templates[len(questions) % len(templates)].format(sym=hit.symbol, path=hit.path),
                "expected_points": points[:6],
                "source": src["id"],
            })
            if len(questions) >= count:
                break
        return questions

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, repo_id: int, question: str, answer: str, expected_points: list[str] | None = None,
                 topic: str = "", session_id: int | None = None) -> dict:
        teacher = self.rt.teacher
        teacher.require_repo(repo_id)
        if not answer.strip():
            raise ValueError("Write an answer first")
        expected_points = expected_points or []
        llm = self.rt.llm()
        result: dict | None = None
        note = None
        if not llm.is_mock:
            try:
                context, sources, _ = teacher.gather(repo_id, [question + " " + topic], self.rt.settings.top_k)
                raw = teacher.complete([
                    {"role": "system", "content": prompts.SYSTEM_BASE + "\n\n" + prompts.EVALUATE_PROMPT},
                    {"role": "user", "content": f"CONTEXT:\n\n{context}\n\n---\nQUESTION: {question}\n"
                                                f"EXPECTED POINTS: {expected_points}\nLEARNER ANSWER:\n{answer[:4000]}"}], json_mode=True)
                data = parse_json_loose(raw)
                result = {
                    "score": max(0.0, min(10.0, float(data.get("score", 0)))),
                    "feedback": str(data.get("feedback", "")),
                    "gaps": [str(g) for g in data.get("gaps", [])][:8],
                    "model_answer": str(data.get("model_answer", "")),
                    "method": "llm",
                    "sources": sources,
                }
            except (LLMError, ValueError, TypeError) as exc:
                log.warning("LLM evaluation failed: %s", exc)
                note = f"LLM evaluation failed ({exc}); used keyword matching instead."
        if result is None:
            result = self._heuristic(answer, expected_points)
            if note:
                result["feedback"] = note + " " + result["feedback"]
        self.rt.store.add_quiz_result(repo_id, session_id, topic or question[:80], question, answer,
                                      result["score"], result["feedback"], result["gaps"])
        return result

    @staticmethod
    def _heuristic(answer: str, expected_points: list[str]) -> dict:
        answer_terms = set(_terms(answer, 3))
        covered, gaps = 0, []
        for point in expected_points:
            terms = _terms(point, 3)
            hit = [t for t in terms if t in answer_terms]
            if terms and len(hit) / len(terms) >= 0.4:
                covered += 1
            else:
                gaps.append(point)
        total = len(expected_points)
        score = round(10 * covered / total, 1) if total else 0.0
        feedback = (f"Keyword check: your answer covers {covered} of {total} expected points. "
                    if total else "No expected points were available to compare against. ")
        feedback += "This is a heuristic (no LLM is configured); configure a model in Settings for a real evaluation."
        return {"score": score, "feedback": feedback, "gaps": gaps, "model_answer": "", "method": "heuristic", "sources": []}
