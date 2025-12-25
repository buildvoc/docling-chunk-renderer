import json
import sys
from pathlib import Path

import pytest

# Ensure the app package is importable
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from apps.docling_graph_explorer import cache, dash_app, graph_builder, io, logs, networkx_metrics, next_edge_rules  # noqa: E402
from apps.docling_graph_explorer import gradio_view  # noqa: E402
from apps.docling_graph_explorer.gradio_view import create_blocks, process_upload  # noqa: E402
from apps.docling_graph_explorer import app as explorer_app  # noqa: E402
from fastapi.testclient import TestClient
import httpx


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_docling.json"


def _load_fixture():
    data = FIXTURE.read_bytes()
    doc, doc_hash = io.load_docling_json(data)
    io.validate_doc(doc)
    return doc, doc_hash, data


def test_io_and_stats():
    doc, doc_hash, _ = _load_fixture()
    stats = io.doc_stats(doc)
    assert doc_hash
    assert stats["texts"] == 3
    assert stats["chunks"] == 3


def test_graph_build_and_edges():
    doc, doc_hash, _ = _load_fixture()
    graph = graph_builder.build_full_graph(doc, doc_hash)
    assert len(graph.nodes) >= 3
    edge_types = {e.get("type") for e in graph.edges}
    assert "NEXT" in edge_types
    assert "CHILD" in edge_types


def test_next_determinism():
    doc, _, _ = _load_fixture()
    edges1 = next_edge_rules.deterministic_next_edges(doc)
    edges2 = next_edge_rules.deterministic_next_edges(doc)
    assert edges1 == edges2
    assert all(e.get("rationale") for e in edges1)


def test_metrics_and_windowing():
    doc, doc_hash, _ = _load_fixture()
    graph = graph_builder.build_full_graph(doc, doc_hash)
    metrics = networkx_metrics.compute_metrics(graph.nodes, graph.edges)
    assert metrics
    sample_id = graph.nodes[0]["id"]
    assert sample_id in metrics

    window = graph_builder.window_subgraph(graph, ["chunk-1"], buffer=1, max_nodes=10)
    window2 = graph_builder.window_subgraph(graph, ["chunk-1"], buffer=1, max_nodes=10)
    assert window["elements"] == window2["elements"]
    assert window["meta"]["nodes"] <= 10


def test_dash_app_smoke():
    app = dash_app.create_dash_app(base_path="/dash-test")
    cy = app.layout.children[1].children[0]  # Cytoscape component
    assert getattr(cy, "layout", {}).get("name") == "fcose"


def test_dash_runner_uses_run():
    assert not hasattr(dash_app, "start_dash")


def test_gradio_blocks_smoke():
    blocks = create_blocks(dash_url="/dash")
    assert hasattr(blocks, "launch")


def test_log_helpers(tmp_path):
    log_file = tmp_path / "log.txt"
    log_file.write_text("INFO test\nERROR boom\nException stack\n")
    tailed = logs.tail_log(str(log_file), 2)
    assert "boom" in tailed
    filtered = logs.filter_log(tailed, pattern="ERROR", errors_only=False)
    assert "ERROR" in filtered
    errors_only = logs.filter_log(tailed, pattern=None, errors_only=True)
    assert "ERROR" in errors_only


def test_cache_roundtrip():
    doc, doc_hash, _ = _load_fixture()
    graph = graph_builder.build_full_graph(doc, doc_hash)
    metrics = {n["id"]: n.get("metrics", {}) for n in graph.nodes}
    cache.set_entry(doc_hash, doc, {"nodes": graph.nodes, "edges": graph.edges}, metrics, graph.chunk_order)
    entry = cache.get(doc_hash)
    assert entry is not None
    assert entry.doc_hash == doc_hash


def test_rendered_styling_token():
    doc, doc_hash, data = _load_fixture()
    graph = graph_builder.build_full_graph(doc, doc_hash)
    html = gradio_view._render_chunks(graph)
    assert "#111" in html or "docling-rendered" in html


def test_sync_logic_and_selection():
    doc, doc_hash, _ = _load_fixture()
    graph = graph_builder.build_full_graph(doc, doc_hash)
    window_a = graph_builder.window_subgraph(graph, ["chunk-1"], buffer=1, max_nodes=10)
    window_b = graph_builder.window_subgraph(graph, ["chunk-1"], buffer=1, max_nodes=10)
    assert window_a == window_b

    dash_app.push_selection("chunk-2")
    assert dash_app.consume_last_selection() == "chunk-2"
    assert dash_app.consume_last_selection() is None


def test_selection_store():
    assert dash_app.consume_last_selection() is None
    dash_app.push_selection("node-1")
    assert dash_app.consume_last_selection() == "node-1"
    assert dash_app.consume_last_selection() is None


def test_assets_available():
    dash = dash_app.create_dash_app(base_path="/dash")
    blocks = create_blocks(dash_url="/dash/")
    api = explorer_app.create_server_app(blocks, dash)
    client = TestClient(api)
    health = client.get("/dash_health").json()
    paths = health.get("cyto_registered_paths") or []
    target = None
    for p in paths:
        if p.endswith(".js"):
            target = p
            break
    assert target, "no cytoscape js path found"
    ok = client.get(f"/dash/_dash-component-suites/dash_cytoscape/{target}")
    not_ok = client.get("/dash/_dash-component-suites/dash_cytoscape/cytoscape.min.js")
    assert ok.status_code == 200
    assert not_ok.status_code in (404, 403)


def test_dash_health_and_layout():
    dash = dash_app.create_dash_app(base_path="/")
    blocks = create_blocks(dash_url="/dash/")
    api = explorer_app.create_server_app(blocks, dash)
    client = TestClient(api)
    resp = client.get("/dash/")
    assert resp.status_code == 200
    resp_layout = client.get("/dash/_dash-layout")
    assert resp_layout.status_code == 200
    health = client.get("/dash_health")
    assert health.status_code == 200
    assert health.json().get("dash_mounted") is True
    assert dash_app.consume_last_selection() is None


def test_read_upload_variants(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text("{\"a\":1}")

    # filepath string
    b1 = gradio_view._read_upload(str(sample))
    assert b1.startswith(b"{")

    class Named:
        def __init__(self, name):
            self.name = name

    b2 = gradio_view._read_upload(Named(str(sample)))
    assert b2 == b1

    class FileLike:
        def read(self):
            return b"{\"b\":2}"

    b3 = gradio_view._read_upload(FileLike())
    assert b3 == b"{\"b\":2}"

    b4 = gradio_view._read_upload(sample)
    assert b4 == b1


def test_upload_handler_smoke():
    class Named:
        def __init__(self, name):
            self.name = name

    upload_file = Named(str(FIXTURE))
    graph_data, stats, dhash, status, html, cache_state, window_meta, visible_json = process_upload(upload_file)
    assert dhash
    assert graph_data.nodes
    assert window_meta["nodes"] > 0
    assert json.loads(visible_json)


def test_proxy_handler_with_mock_transport():
    called = {}

    def handler(request: httpx.Request):
        called["url"] = str(request.url)
        return httpx.Response(200, json={"layout": "ok"})

    transport = httpx.MockTransport(handler)
    blocks = create_blocks()
    dash = dash_app.create_dash_app(base_path="/")
    api = explorer_app.create_server_app(blocks, dash)
    client = TestClient(api)
    resp = client.get("/dash/_dash-layout?foo=1")
    assert resp.status_code == 200
    assert resp.json()
    # dash under WSGI; ensuring endpoint exists under single port


def test_iframe_src_is_relative():
    blocks = create_blocks()
    assert getattr(blocks, "_dash_iframe_src", "").startswith("/dash/")


def test_dash_proxy_smoke():
    dash = dash_app.create_dash_app(base_path="/")
    blocks = create_blocks(dash_url="/dash/")
    api = explorer_app.create_server_app(blocks, dash)
    client = TestClient(api)
    resp = client.get("/dash/_dash-layout")
    assert resp.status_code == 200
    assert resp.text  # non-empty


def test_window_subgraph_not_empty():
    doc, doc_hash, _ = _load_fixture()
    graph = graph_builder.build_full_graph(doc, doc_hash)
    visible = graph.chunk_order[:1]
    window = graph_builder.window_subgraph(graph, visible_chunks=visible, buffer=1, max_nodes=10)
    assert window["meta"]["nodes"] > 0
    assert window["elements"]


def test_dash_elements_from_state():
    state = {
        "doc_hash": "abc",
        "visible_chunk_ids": ["chunk-0"],
        "context_buffer": 1,
        "node_cap": 10,
        "updated_at": 0.0,
        "elements": [{"data": {"id": "n1"}}, {"data": {"source": "n1", "target": "n1"}}],
        "meta": {"nodes": 1, "edges": 1, "showing": "chunks 1-1 / 1"},
        "window": (0, 1),
    }
    dash_app.update_window_state(state)
    current = dash_app.current_state()
    assert current["meta"]["nodes"] == 1
    assert current["elements"]
