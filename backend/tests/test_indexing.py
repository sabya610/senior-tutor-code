"""Parsing, ignore rules and incremental indexing."""
from __future__ import annotations

from app.scanner.parsers import parse_file


def test_parsers_extract_symbols_routes_and_manifests(repo_dir):
    py = parse_file("app/main.py", "python", (repo_dir / "app/main.py").read_text())
    names = {(s.name, s.kind) for s in py.symbols}
    assert any(kind == "route" and "/orders" in name for name, kind in names)
    assert ("post_order", "function") in names

    go = parse_file("controllers/order_controller.go", "go", (repo_dir / "controllers/order_controller.go").read_text())
    assert any(s.kind == "reconciler" for s in go.symbols)
    assert any(s.name == "OrderReconciler.Reconcile" for s in go.symbols)

    manifest = parse_file("deploy/deployment.yaml", "yaml", (repo_dir / "deploy/deployment.yaml").read_text())
    assert {s.kind for s in manifest.symbols} >= {"k8s:Deployment", "k8s:Service"}
    assert any(r.kind == "image" and r.target.startswith("registry.example.com/order-api") for r in manifest.relations)


def test_chunk_line_ranges_point_at_real_lines(repo_dir):
    for rel, lang in (("app/service.py", "python"), ("controllers/order_controller.go", "go")):
        text = (repo_dir / rel).read_text()
        total = len(text.splitlines())
        for chunk in parse_file(rel, lang, text).chunks:
            assert 1 <= chunk.start_line <= chunk.end_line <= total
            first_line = text.splitlines()[chunk.start_line - 1]
            assert first_line.strip() in chunk.text


def test_index_skips_ignored_dirs_and_reports_features(rt, indexed):
    paths = {f["rel_path"] for f in rt.store.list_files(indexed)}
    assert "app/service.py" in paths
    assert not any(p.startswith("node_modules/") for p in paths)
    repo = rt.store.get_repo(indexed)
    assert repo["summary"]["totals"]["files"] == len(paths)
    assert repo["summary"]["k8s_kinds"]


def test_incremental_indexing_detects_change_and_delete(rt, repo_dir, indexed):
    again = rt.indexer.run(indexed)
    assert again["added"] == 0 and again["changed"] == 0 and again["unchanged"] > 0

    service = repo_dir / "app/service.py"
    service.write_text(service.read_text() + "\n\ndef refund_order(order_id):\n    return {'refunded': order_id}\n")
    changed = rt.indexer.run(indexed)
    assert changed["changed"] == 1 and changed["added"] == 0
    assert any(h.symbol == "refund_order" for h in rt.retriever.search(indexed, "refund_order"))

    (repo_dir / "app/service.py").unlink()
    removed = rt.indexer.run(indexed)
    assert removed["removed"] == 1
    assert not any(h.path == "app/service.py" for h in rt.retriever.search(indexed, "create_order validate_order"))


def test_resolver_links_python_import(rt, indexed):
    row = rt.db.one(
        "SELECT r.resolved_file_id, t.rel_path FROM relations r JOIN files f ON f.id = r.file_id "
        "JOIN files t ON t.id = r.resolved_file_id WHERE f.rel_path = 'app/main.py' AND r.kind = 'import' "
        "AND t.rel_path = 'app/service.py'"
    )
    assert row is not None
