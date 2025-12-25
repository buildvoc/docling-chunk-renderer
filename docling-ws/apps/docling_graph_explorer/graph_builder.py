from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .logs import setup_logging
from .networkx_metrics import compute_metrics
from .next_edge_rules import deterministic_next_edges

logger = setup_logging()


def _safe_ref(item: Dict, default_id: str) -> str:
    return item.get("self_ref") or item.get("id") or default_id


def _text_snippet(text: str, limit: int = 180) -> str:
    if not text:
        return ""
    return text[:limit] + ("..." if len(text) > limit else "")


def _collect_docitems(doc: Dict) -> List[Dict]:
    items = []
    for idx, item in enumerate(doc.get("texts") or []):
        if isinstance(item, dict):
            items.append({**item, "chunk_id": _safe_ref(item, f"chunk_{idx}")})
    return items


def _collect_chunks(doc: Dict) -> List[Dict]:
    if isinstance(doc.get("chunks"), list):
        chunks = []
        for idx, chunk in enumerate(doc["chunks"]):
            if isinstance(chunk, dict):
                chunks.append({**chunk, "chunk_id": _safe_ref(chunk, f"chunk_{idx}")})
        return chunks
    return _collect_docitems(doc)


@dataclass
class GraphData:
    doc_hash: str
    nodes: List[Dict]
    edges: List[Dict]
    chunk_order: List[str]
    stats: Dict[str, int]


def build_full_graph(doc: Dict, doc_hash: str) -> GraphData:
    """Build full graph nodes/edges with NEXT + CHILD + optional CONTAINS."""
    chunks = _collect_chunks(doc)
    docitems = _collect_docitems(doc)
    chunk_map = {c["chunk_id"]: c for c in chunks}

    nodes: List[Dict] = []
    edges: List[Dict] = []

    for chunk in chunks:
        cid = chunk["chunk_id"]
        label = _text_snippet(chunk.get("text") or chunk.get("normalized_text") or "")
        page = chunk.get("page_no") or chunk.get("page") or 0
        bbox = chunk.get("bbox") or {}
        nodes.append(
            {
                "id": cid,
                "label": label or cid,
                "type": "chunk",
                "chunk_id": cid,
                "docitem_id": chunk.get("self_ref"),
                "page_no": page,
                "bbox": bbox,
                "text": chunk.get("text") or "",
                "color": "#fcd34d",
            }
        )

    for item in docitems:
        iid = _safe_ref(item, "docitem")
        label = _text_snippet(item.get("text") or item.get("normalized_text") or "")
        page = item.get("page_no") or item.get("page") or 0
        bbox = item.get("bbox") or {}
        nodes.append(
            {
                "id": iid,
                "label": label or iid,
                "type": "docitem",
                "chunk_id": item.get("chunk_id") or iid,
                "docitem_id": iid,
                "page_no": page,
                "bbox": bbox,
                "text": item.get("text") or "",
                "color": "#a5b4fc",
            }
        )
        if item.get("chunk_id") and item["chunk_id"] in chunk_map:
            edges.append(
                {
                    "id": f"{item['chunk_id']}->{iid}->CONTAINS",
                    "source": item["chunk_id"],
                    "target": iid,
                    "type": "CONTAINS",
                    "label": "CONTAINS",
                }
            )

    # CHILD edges from parent/children relationships
    for item in docitems:
        parent = item.get("parent")
        parent_id = parent.get("self_ref") if isinstance(parent, dict) else None
        if isinstance(parent, str):
            parent_id = parent
        if parent_id:
            edges.append(
                {"id": f"{parent_id}->{item['chunk_id']}->CHILD", "source": parent_id, "target": item["chunk_id"], "type": "CHILD", "label": "CHILD"}
            )
        children = item.get("children") or []
        for child in children:
            child_id = child.get("self_ref") if isinstance(child, dict) else child if isinstance(child, str) else None
            if child_id:
                edges.append(
                    {
                        "id": f"{item['chunk_id']}->{child_id}->CHILD",
                        "source": item["chunk_id"],
                        "target": child_id,
                        "type": "CHILD",
                        "label": "CHILD",
                    }
                )

    next_edges = deterministic_next_edges(doc)
    edges.extend(next_edges)

    # Deduplicate edges by (src, tgt, type)
    unique = {}
    for e in edges:
        key = (e.get("source"), e.get("target"), e.get("type"))
        if key not in unique:
            unique[key] = e
    edges = list(unique.values())

    metrics = compute_metrics(nodes, edges)
    for node in nodes:
        nid = node["id"]
        node["metrics"] = metrics.get(nid, {})
        node["degree"] = node["metrics"].get("degree", 0)
        node["community"] = node["metrics"].get("community", -1)

    chunk_order = [c["chunk_id"] for c in chunks]
    stats = {"nodes": len(nodes), "edges": len(edges), "chunks": len(chunk_order)}
    logger.info("built graph hash=%s nodes=%d edges=%d", doc_hash, len(nodes), len(edges))
    return GraphData(doc_hash=doc_hash, nodes=nodes, edges=edges, chunk_order=chunk_order, stats=stats)


def _window_range(chunk_order: List[str], visible: Sequence[str], buffer: int) -> Tuple[int, int]:
    if not visible:
        return 0, min(len(chunk_order), buffer)
    indexes = [chunk_order.index(cid) for cid in visible if cid in chunk_order]
    if not indexes:
        return 0, min(len(chunk_order), buffer)
    start = max(0, min(indexes) - buffer)
    end = min(len(chunk_order), max(indexes) + buffer + 1)
    return start, end


def window_subgraph(
    graph: GraphData,
    visible_chunks: Sequence[str],
    buffer: int = 2,
    max_nodes: int = 600,
    include_hierarchy: bool = True,
    include_next: bool = True,
) -> Dict[str, object]:
    """Extract a deterministic windowed subgraph."""
    start, end = _window_range(graph.chunk_order, visible_chunks, buffer)
    window_chunk_ids = graph.chunk_order[start:end]
    include_ids = set(window_chunk_ids)

    # Always include docitems that map to the windowed chunks.
    for node in graph.nodes:
        if node.get("chunk_id") in window_chunk_ids:
            include_ids.add(node["id"])

    if include_hierarchy:
        for edge in graph.edges:
            if edge.get("type") == "CHILD":
                if edge.get("source") in include_ids or edge.get("target") in include_ids:
                    include_ids.add(edge.get("source"))
                    include_ids.add(edge.get("target"))

    elements_nodes = [n for n in graph.nodes if n["id"] in include_ids]
    if len(elements_nodes) > max_nodes:
        # shrink deterministically: keep only visible chunks first
        priority_ids = list(dict.fromkeys(list(visible_chunks) + window_chunk_ids))
        trimmed = []
        for cid in priority_ids:
            node = next((n for n in elements_nodes if n["id"] == cid), None)
            if node and node not in trimmed:
                trimmed.append(node)
            if len(trimmed) >= max_nodes:
                break
        elements_nodes = trimmed
        include_ids = {n["id"] for n in elements_nodes}

    elements_edges = []
    for edge in graph.edges:
        if edge.get("source") in include_ids and edge.get("target") in include_ids:
            if include_next or edge.get("type") != "NEXT":
                elements_edges.append(edge)

    elements = []
    for node in elements_nodes:
        elements.append({"data": node})
    for edge in elements_edges:
        elements.append({"data": edge})

    meta = {
        "showing": f"chunks {start + 1}-{end} / {len(graph.chunk_order)}",
        "nodes": len(elements_nodes),
        "edges": len(elements_edges),
    }
    return {"elements": elements, "meta": meta, "window": (start, end)}
