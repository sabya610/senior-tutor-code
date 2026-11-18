"""Shared fixtures: a small multi-language demo repository and an isolated app instance."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.base import LLMProvider
from app.main import create_app

FILES = {
    "app/main.py": '''
        """Order API."""
        from fastapi import FastAPI
        from app.service import create_order

        app = FastAPI()


        @app.post("/orders")
        def post_order(payload: dict):
            """HTTP entry point: accepts an order and delegates to the service layer."""
            return create_order(payload)
    ''',
    "app/service.py": '''
        class OrderError(Exception):
            pass


        def validate_order(payload):
            """Reject orders without a customer id or with a non-positive quantity."""
            if "customer_id" not in payload:
                raise OrderError("customer_id is required")
            if payload.get("quantity", 0) <= 0:
                raise OrderError("quantity must be positive")


        def create_order(payload):
            """Validate then persist the order and return its identifier."""
            validate_order(payload)
            return {"order_id": 42, "status": "created"}
    ''',
    "tests/test_service.py": '''
        from app.service import create_order, validate_order


        def test_create_order_validation():
            assert create_order({"customer_id": 1, "quantity": 2})["status"] == "created"
    ''',
    "controllers/order_controller.go": '''
        package controllers

        import (
        \t"context"
        \tctrl "sigs.k8s.io/controller-runtime"
        )

        type OrderReconciler struct {
        \tScheme string
        }

        // Reconcile drives an Order resource towards its desired state.
        func (r *OrderReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
        \tif req.Name == "" {
        \t\treturn ctrl.Result{}, nil
        \t}
        \treturn ctrl.Result{}, nil
        }
    ''',
    "deploy/deployment.yaml": '''
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: order-api
        spec:
          template:
            spec:
              containers:
                - name: api
                  image: registry.example.com/order-api:1.0.0
        ---
        apiVersion: v1
        kind: Service
        metadata:
          name: order-api
        spec:
          ports:
            - port: 8000
    ''',
    "charts/demo/Chart.yaml": '''
        apiVersion: v2
        name: demo
        version: 0.1.0
    ''',
    "charts/demo/templates/deployment.yaml": '''
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: {{ .Release.Name }}-api
        spec:
          template:
            spec:
              containers:
                - name: api
                  image: {{ .Values.image }}
    ''',
    "README.md": '''
        # Demo orders service

        ## Architecture
        The API layer calls the service layer which validates and creates orders.
    ''',
    "node_modules/ignored/index.js": "module.exports = 'should never be indexed';\n",
}


def write_repo(root: Path) -> Path:
    for rel, body in FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body).lstrip("\n").replace("\\t", "\t"), encoding="utf-8")
    (root / ".git").mkdir(exist_ok=True)
    return root


class FakeLLM(LLMProvider):
    name = "fake"
    model = "fake-1"
    is_mock = False

    def __init__(self, reply="Verified: create_order validates first [S1]. Invented claim [S99]."):
        self.reply = reply
        self.calls: list[tuple[list[dict], dict]] = []

    def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.reply(messages, kwargs) if callable(self.reply) else self.reply


def make_settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", llm_provider="mock", embed_provider="local",
                    openai_api_key="", **overrides)


@pytest.fixture
def repo_dir(tmp_path):
    return write_repo(tmp_path / "demo-repo")


@pytest.fixture
def app(tmp_path):
    return create_app(make_settings(tmp_path))


@pytest.fixture
def rt(app):
    return app.state.runtime


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def indexed(rt, repo_dir):
    """A registered and fully indexed demo repo; returns its id."""
    repo = rt.store.add_repo("demo-repo", str(repo_dir))
    status = rt.indexer.run(repo["id"])
    assert status["state"] == "done", status
    return repo["id"]


@pytest.fixture
def fake_llm(rt, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(rt, "llm", lambda: fake)
    return fake
