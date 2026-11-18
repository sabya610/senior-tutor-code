"""Shared request dependencies and request/response models."""
from __future__ import annotations

from typing import Literal

from fastapi import Request
from pydantic import BaseModel, Field

from app.runtime import Runtime


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


class RepoCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    name: str | None = Field(default=None, max_length=200)


class IndexRequest(BaseModel):
    full: bool = False


class LearnRequest(BaseModel):
    repo_id: int
    mode: Literal["explain", "flow", "feature", "compare", "chat", "troubleshoot"] = "explain"
    question: str = Field(default="", max_length=30000)
    depth: Literal["beginner", "intermediate", "expert"] = "intermediate"
    feature_id: int | None = None
    compare_with: str | None = Field(default=None, max_length=2000)
    session_id: int | None = None
    force: bool = False


class QuizRequest(BaseModel):
    repo_id: int
    topic: str = Field(default="", max_length=2000)
    count: int = Field(default=5, ge=1, le=10)
    feature_id: int | None = None
    interview: bool = False


class EvaluateRequest(BaseModel):
    repo_id: int
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=8000)
    expected_points: list[str] = Field(default_factory=list, max_length=12)
    topic: str = Field(default="", max_length=2000)
    session_id: int | None = None


class SessionPrepRequest(BaseModel):
    repo_id: int
    topic: str = Field(default="", max_length=2000)
    feature_id: int | None = None
    force: bool = False


class BookmarkCreate(BaseModel):
    repo_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)
    sources: list[dict] = Field(default_factory=list, max_length=100)


class SettingsUpdate(BaseModel):
    llm_provider: Literal["auto", "openai", "ollama", "mock"] | None = None
    llm_model: str | None = Field(default=None, max_length=200)
    embed_provider: Literal["auto", "openai", "ollama", "local"] | None = None
    embed_model: str | None = Field(default=None, max_length=200)
    ollama_base_url: str | None = Field(default=None, max_length=300)
    openai_base_url: str | None = Field(default=None, max_length=300)
