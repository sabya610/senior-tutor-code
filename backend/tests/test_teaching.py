"""Retrieval quality, grounded answers, citation validation, follow-ups, caching and troubleshooting."""
from __future__ import annotations

import re

from app.teaching.context import check_citations
from app.teaching.orchestrator import is_followup
from app.teaching.troubleshoot import extract_signals, locate_frames


def test_retrieval_prefers_implementation_over_tests(rt, indexed):
    hits = rt.retriever.search(indexed, "how are orders validated when created")
    assert hits[0].path == "app/service.py"
    paths = [h.path for h in hits]
    if "tests/test_service.py" in paths:
        assert paths.index("app/service.py") < paths.index("tests/test_service.py")


def test_retrieval_matches_inside_camel_case_symbols(rt, indexed):
    hits = rt.retriever.search(indexed, "reconciler")
    assert hits[0].path == "controllers/order_controller.go"
    assert "OrderReconciler.Reconcile" in {h.symbol for h in hits[:2]}


def test_every_source_range_is_real(rt, repo_dir, indexed):
    result = rt.teacher.ask(repo_id=indexed, mode="explain", question="how does order creation work")
    assert result["sources"]
    for src in result["sources"]:
        total = len((repo_dir / src["path"]).read_text().splitlines())
        assert 1 <= src["start_line"] <= src["end_line"] <= total


def test_mock_mode_is_evidence_only_and_says_so(rt, indexed):
    result = rt.teacher.ask(repo_id=indexed, mode="explain", question="create_order")
    assert result["provider"]["mock"] is True
    assert "Evidence-only mode" in result["answer"]
    assert "`app/service.py:" in result["answer"]


def test_no_evidence_means_no_invention(rt, indexed):
    result = rt.teacher.ask(repo_id=indexed, mode="explain", question="zzqxjv blorptastic frobnicator")
    assert "will not guess" in result["answer"]
    assert result["sources"] == []


def test_invalid_citations_are_flagged(rt, indexed, fake_llm):
    result = rt.teacher.ask(repo_id=indexed, mode="explain", question="create_order")
    assert "[S99]" not in result["answer"] and "[unverified citation]" in result["answer"]
    assert result["citations"]["invalid"] == ["S99"]
    assert any("S99" in w for w in result["warnings"])
    assert "[S1]" in result["answer"]


def test_check_citations_unit():
    sources = [{"id": "S1"}, {"id": "S2"}]
    assert check_citations("a [S2] b [S1] c [S7]", sources) == {"cited": ["S1", "S2"], "invalid": ["S7"], "available": 2}


def test_prompt_contains_numbered_line_referenced_context(rt, indexed, fake_llm):
    rt.teacher.ask(repo_id=indexed, mode="flow", question="order creation flow")
    system, user = fake_llm.calls[0][0][0]["content"], fake_llm.calls[0][0][-1]["content"]
    assert "NEVER invent" in system
    assert re.search(r"\[S1\] [\w./-]+:\d+-\d+", user)
    assert re.search(r"^\s*\d+\| ", user, re.M)


def test_followup_resolution_pins_previous_sources(rt, indexed, fake_llm):
    first = rt.teacher.ask(repo_id=indexed, mode="explain", question="how does validate_order work")
    follow = rt.teacher.ask(repo_id=indexed, mode="chat", question="why is this required?", session_id=first["session_id"])
    assert follow["follow_up"]["topic"] == "how does validate_order work"
    assert follow["follow_up"]["pinned"]
    assert follow["session_id"] == first["session_id"]
    assert any("validate_order" in s["path"] or s["symbol"] == "validate_order" for s in follow["sources"])
    assert is_followup("what about the previous step?") and not is_followup(
        "Explain in detail how the order service validates every incoming request payload before persisting it to storage now")


def test_explanation_cache_and_staleness(rt, repo_dir, indexed, fake_llm):
    ask = lambda: rt.teacher.ask(repo_id=indexed, mode="explain", question="how are orders created")
    first, second = ask(), ask()
    assert not first["cached"] and second["cached"]
    assert len(fake_llm.calls) == 1

    service = repo_dir / "app/service.py"
    service.write_text(service.read_text() + "\n# changed\n")
    rt.indexer.run(indexed)
    stale = rt.store.get_explanation(first["explanation_id"])
    assert stale["stale"] is True
    third = ask()
    assert not third["cached"] and len(fake_llm.calls) == 2


def test_llm_failure_falls_back_to_evidence(rt, indexed, fake_llm):
    from app.llm.base import LLMError

    def boom(messages, kwargs):
        raise LLMError("backend down")

    fake_llm.reply = boom
    result = rt.teacher.ask(repo_id=indexed, mode="explain", question="create_order")
    assert "LLM unavailable" in result["answer"] and result["warnings"]
    assert result["explanation_id"] is None


def test_compare_mode_needs_two_concepts_and_groups_results(rt, indexed):
    import pytest

    with pytest.raises(ValueError):
        rt.teacher.ask(repo_id=indexed, mode="compare", question="reconciler")
    result = rt.teacher.ask(repo_id=indexed, mode="compare", question="reconciler", compare_with="validate_order")
    assert "### reconciler" in result["answer"] and "### validate_order" in result["answer"]


def test_troubleshoot_extracts_and_locates_frames(rt, indexed):
    log = (
        'Traceback (most recent call last):\n'
        '  File "/srv/app/app/main.py", line 11, in post_order\n'
        '    return create_order(payload)\n'
        '  File "/srv/app/app/service.py", line 14, in validate_order\n'
        'app.service.OrderError: customer_id is required\n'
        'Back-off restarting failed container: CrashLoopBackOff, Error: ImagePullBackOff'
    )
    signals = extract_signals(log)
    assert {f["func"] for f in signals["frames"]} >= {"post_order", "validate_order"}
    assert {h["label"] for h in signals["hints"]} >= {"CrashLoopBackOff", "Image pull failure"}
    located = {f["rel_path"] for f in locate_frames(rt.db, indexed, signals["frames"])}
    assert located == {"app/main.py", "app/service.py"}

    result = rt.teacher.ask(repo_id=indexed, mode="troubleshoot", question=log)
    assert "kubectl -n <ns> logs <pod>" in result["answer"]
    assert "`app/service.py:" in result["answer"]


def test_quiz_and_evaluation_without_llm(rt, indexed):
    quiz = rt.quizzer.generate(indexed, "order validation", count=3)
    assert quiz["questions"] and all(q["expected_points"] for q in quiz["questions"])
    q = quiz["questions"][0]
    good = rt.quizzer.evaluate(indexed, q["question"], " ".join(q["expected_points"]), q["expected_points"], "orders")
    bad = rt.quizzer.evaluate(indexed, q["question"], "I have no idea honestly", q["expected_points"], "orders")
    assert good["method"] == bad["method"] == "heuristic"
    assert good["score"] > bad["score"]
    assert len(rt.store.list_quiz_results(indexed)) == 2


def test_quiz_uses_llm_json_when_available(rt, indexed, fake_llm):
    fake_llm.reply = ('```json\n{"questions": [{"question": "Why validate first?", '
                      '"expected_points": ["rejects bad input"], "source": "S1"}]}\n```')
    quiz = rt.quizzer.generate(indexed, "order validation", count=2)
    assert quiz["questions"][0]["question"] == "Why validate first?"
    fake_llm.reply = '{"score": 14, "feedback": "ok", "gaps": ["x"], "model_answer": "m"}'
    result = rt.quizzer.evaluate(indexed, "Why validate first?", "because", ["rejects bad input"])
    assert result["score"] == 10.0 and result["method"] == "llm"


def test_session_prep_has_all_fourteen_sections(rt, indexed, fake_llm):
    fake_llm.reply = lambda messages, kwargs: "## 1. section\nbody [S1]"
    result = rt.session_prep.generate(indexed, "order creation")
    assert len(fake_llm.calls) == 4
    assert result["content"].startswith("# Session preparation: order creation")
    assert "## Appendix: sources" in result["content"]
    again = rt.session_prep.generate(indexed, "order creation")
    assert again["cached"] and len(fake_llm.calls) == 4


def test_session_prep_in_evidence_mode_lists_all_sections(rt, indexed):
    content = rt.session_prep.generate(indexed, "order creation")["content"]
    covered: set[int] = set()
    for first, last in re.findall(r"^## (\d+)(?:-(\d+))?\.", content, re.M):
        covered.update(range(int(first), int(last or first) + 1))
    assert covered == set(range(1, 15))
