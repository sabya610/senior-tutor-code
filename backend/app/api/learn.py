"""Learning endpoints: teaching modes, quizzes, session prep, history, bookmarks and the knowledge base."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import BookmarkCreate, EvaluateRequest, LearnRequest, QuizRequest, SessionPrepRequest, get_runtime
from app.runtime import Runtime

router = APIRouter(tags=["learning"])


@router.post("/learn")
def learn(body: LearnRequest, rt: Runtime = Depends(get_runtime)):
    return rt.teacher.ask(repo_id=body.repo_id, mode=body.mode, question=body.question, depth=body.depth,
                          feature_id=body.feature_id, compare_with=body.compare_with,
                          session_id=body.session_id, force=body.force)


@router.post("/quiz")
def quiz(body: QuizRequest, rt: Runtime = Depends(get_runtime)):
    return rt.quizzer.generate(body.repo_id, body.topic, body.count, body.feature_id, body.interview)


@router.post("/quiz/evaluate")
def evaluate(body: EvaluateRequest, rt: Runtime = Depends(get_runtime)):
    return rt.quizzer.evaluate(body.repo_id, body.question, body.answer, body.expected_points, body.topic, body.session_id)


@router.get("/quiz/results")
def quiz_results(repo_id: int | None = None, rt: Runtime = Depends(get_runtime)):
    return rt.store.list_quiz_results(repo_id)


@router.post("/session-prep")
def session_prep(body: SessionPrepRequest, rt: Runtime = Depends(get_runtime)):
    return rt.session_prep.generate(body.repo_id, body.topic, body.feature_id, body.force)


# ---------------------------------------------------------------------- history
@router.get("/sessions")
def sessions(repo_id: int | None = None, rt: Runtime = Depends(get_runtime)):
    return rt.store.list_sessions(repo_id)


@router.get("/sessions/{session_id}")
def session(session_id: int, rt: Runtime = Depends(get_runtime)):
    row = rt.store.get_session(session_id)
    if not row:
        raise HTTPException(404, "Session not found")
    return {**row, "messages": rt.store.get_messages(session_id)}


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, rt: Runtime = Depends(get_runtime)):
    rt.store.delete_session(session_id)


# ---------------------------------------------------------------------- bookmarks
@router.get("/bookmarks")
def bookmarks(repo_id: int | None = None, rt: Runtime = Depends(get_runtime)):
    return rt.store.list_bookmarks(repo_id)


@router.post("/bookmarks", status_code=201)
def add_bookmark(body: BookmarkCreate, rt: Runtime = Depends(get_runtime)):
    if body.repo_id and not rt.store.get_repo(body.repo_id):
        raise HTTPException(404, "Repository not found")
    return {"id": rt.store.add_bookmark(body.repo_id, body.title, body.content, body.sources)}


@router.delete("/bookmarks/{bookmark_id}", status_code=204)
def delete_bookmark(bookmark_id: int, rt: Runtime = Depends(get_runtime)):
    rt.store.delete_bookmark(bookmark_id)


# ---------------------------------------------------------------------- knowledge base
@router.get("/knowledge")
def knowledge(repo_id: int | None = None, mode: str = Query("", max_length=30), rt: Runtime = Depends(get_runtime)):
    rows = rt.store.list_explanations(repo_id, mode)
    for row in rows:
        row["preview"] = row.pop("content")[:300]
    return rows


@router.get("/knowledge/{explanation_id}")
def knowledge_item(explanation_id: int, rt: Runtime = Depends(get_runtime)):
    row = rt.store.get_explanation(explanation_id)
    if not row:
        raise HTTPException(404, "Explanation not found")
    return row


@router.delete("/knowledge/{explanation_id}", status_code=204)
def delete_knowledge(explanation_id: int, rt: Runtime = Depends(get_runtime)):
    rt.store.delete_explanation(explanation_id)
