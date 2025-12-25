import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "docling-ws" / "apps"
sys.path.insert(0, str(ROOT))

from docling_graph_builder_docling import build_docling_elements  # noqa: E402


def test_handles_body_texts_with_refs_and_adds_edges():
    doc = {
        "body": {"self_ref": "#/body", "children": [{"$ref": "#/texts/0"}, {"$ref": "#/texts/1"}]},
        "texts": [
            {"self_ref": "#/texts/0", "text": "first text", "parent": {"$ref": "#/body"}},
            {"self_ref": "#/texts/1", "text": "second text", "parent": {"$ref": "#/body"}},
        ],
    }

    elements, counts = build_docling_elements(doc, max_nodes=10)

    nodes = [e for e in elements if "source" not in e.get("data", {})]
    edges = [e for e in elements if "source" in e.get("data", {})]
    child_edges = {(e["data"]["source"], e["data"]["target"]) for e in edges if e["data"]["label"] == "CHILD"}
    next_edges = {(e["data"]["source"], e["data"]["target"]) for e in edges if e["data"]["label"] == "NEXT"}

    assert len(nodes) >= 3  # body + texts
    assert ("#/body", "#/texts/0") in child_edges
    assert ("#/body", "#/texts/1") in child_edges
    assert counts.get("CHILD") == 2
    assert ("#/texts/0", "#/texts/1") in next_edges
    assert counts.get("NEXT", 0) >= 1


def test_empty_children_does_not_crash():
    doc = {"body": {"self_ref": "#/body", "children": []}, "texts": []}

    elements, counts = build_docling_elements(doc, max_nodes=5)

    nodes = [e for e in elements if "source" not in e.get("data", {})]
    edges = [e for e in elements if "source" in e.get("data", {})]

    assert len(nodes) >= 1
    assert len(edges) == 0 or counts.get("NEXT", 0) >= 0
