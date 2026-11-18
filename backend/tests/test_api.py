"""HTTP API behaviour and the localhost security controls."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import make_settings


def wait_for_index(client, repo_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/api/repos/{repo_id}/index/status").json()
        if status["state"] in ("done", "error"):
            return status
        time.sleep(0.1)
    raise AssertionError("indexing did not finish")


def test_full_flow_over_http(client, repo_dir):
    assert client.get("/api/health").json()["llm"]["mock"] is True

    found = client.get("/api/repos/discover", params={"root": str(repo_dir.parent)}).json()
    assert [r["name"] for r in found["repositories"]] == ["demo-repo"]

    repo = client.post("/api/repos", json={"path": str(repo_dir)}).json()
    assert client.post("/api/repos", json={"path": str(repo_dir)}).json()["id"] == repo["id"]
    assert client.post("/api/learn", json={"repo_id": repo["id"], "question": "x"}).status_code == 400  # not indexed

    assert client.post(f"/api/repos/{repo['id']}/index").status_code == 200
    assert wait_for_index(client, repo["id"])["state"] == "done"

    arch = client.get(f"/api/repos/{repo['id']}/architecture").json()
    assert arch["summary"]["totals"]["files"] > 0 and "Architecture map" in arch["markdown"]
    assert client.get(f"/api/repos/{repo['id']}/features").json()
    assert client.get(f"/api/repos/{repo['id']}/symbols", params={"q": "create_order"}).json()
    assert client.get(f"/api/repos/{repo['id']}/search", params={"q": "validate order"}).json()[0]["path"] == "app/service.py"

    answer = client.post("/api/learn", json={"repo_id": repo["id"], "mode": "explain", "question": "create_order"}).json()
    assert answer["sources"] and answer["session_id"]
    session = client.get(f"/api/sessions/{answer['session_id']}").json()
    assert [m["role"] for m in session["messages"]] == ["user", "assistant"]

    bookmark = client.post("/api/bookmarks", json={"repo_id": repo["id"], "title": "t", "content": "c", "sources": answer["sources"]})
    assert bookmark.status_code == 201 and len(client.get("/api/bookmarks").json()) == 1
    assert client.get("/api/dashboard").json()["repos"][0]["file_count"] > 0
    assert client.delete(f"/api/repos/{repo['id']}").status_code == 204
    assert client.get("/api/repos").json() == []


def test_validation_and_error_codes(client, indexed, repo_dir):
    assert client.post("/api/learn", json={"repo_id": indexed, "mode": "bogus", "question": "x"}).status_code == 422
    assert client.post("/api/learn", json={"repo_id": indexed, "mode": "explain", "question": ""}).status_code == 400
    assert client.post("/api/learn", json={"repo_id": 999, "question": "x"}).status_code == 404
    assert client.post("/api/repos", json={"path": str(repo_dir / "nope")}).status_code == 400
    assert client.post("/api/quiz", json={"repo_id": indexed, "topic": "x", "count": 99}).status_code == 422


def test_file_endpoint_blocks_traversal_and_unindexed_files(client, indexed, repo_dir, tmp_path):
    (tmp_path / "secret.txt").write_text("top secret")
    for bad in ("../secret.txt", "../../secret.txt", str(tmp_path / "secret.txt"), "node_modules/ignored/index.js", "..\\secret.txt"):
        assert client.get(f"/api/repos/{indexed}/file", params={"path": bad}).status_code == 404, bad

    ok = client.get(f"/api/repos/{indexed}/file", params={"path": "app/service.py", "start": 9, "end": 12}).json()
    assert ok["start"] == 9 and 1 <= len(ok["lines"]) <= 4
    assert ok["changed_since_index"] is False
    (repo_dir / "app/service.py").write_text("changed\n")
    assert client.get(f"/api/repos/{indexed}/file", params={"path": "app/service.py"}).json()["changed_since_index"] is True


def test_allowed_roots_restrict_registration(tmp_path, repo_dir):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    client = TestClient(create_app(make_settings(tmp_path, allowed_roots=str(allowed))))
    assert client.post("/api/repos", json={"path": str(repo_dir)}).status_code == 403
    assert client.get("/api/repos/discover", params={"root": str(repo_dir)}).status_code == 403


def test_host_header_and_cross_origin_protection(client, tmp_path):
    assert client.get("/api/health", headers={"host": "evil.example.com"}).status_code == 400
    assert client.get("/api/health", headers={"host": "localhost:8000"}).status_code == 200
    cross = client.post("/api/repos", json={"path": str(tmp_path)}, headers={"origin": "https://evil.example.com"})
    assert cross.status_code == 403
    same = client.post("/api/repos", json={"path": str(tmp_path)}, headers={"origin": "http://testserver", "host": "testserver"})
    assert same.status_code == 201

    open_client = TestClient(create_app(make_settings(tmp_path, allowed_hosts="*")))
    assert open_client.get("/api/health", headers={"host": "tutor.internal"}).status_code == 200


def test_security_headers_present(client):
    headers = client.get("/api/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_settings_roundtrip_and_validation(client):
    assert client.put("/api/settings", json={"ollama_base_url": "file:///etc/passwd"}).status_code == 400
    assert client.put("/api/settings", json={"llm_provider": "nonsense"}).status_code == 422
    body = client.put("/api/settings", json={"llm_provider": "mock", "llm_model": "m"}).json()
    assert body["config"]["llm_model"] == "m" and body["openai_key_configured"] is False
    assert "openai_api_key" not in str(body).lower().replace("openai_key_configured", "")
    test = client.post("/api/settings/test").json()
    assert test["ok"] is False and test["error"]
