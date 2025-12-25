from __future__ import annotations

from typing import Dict, List, Tuple


def _find_items(doc: Dict) -> List[Dict]:
    if not isinstance(doc, dict):
        return []
    candidates = [
        doc.get("document", {}).get("items"),
        doc.get("items"),
        doc.get("document", {}).get("body", {}).get("items"),
    ]
    for cand in candidates:
        if isinstance(cand, list):
            return [c for c in cand if isinstance(c, dict)]
    return []


def build_docling_elements(doc: Dict, max_nodes: int = 5000) -> Tuple[List[Dict], Dict[str, int]]:
    items = _find_items(doc)
    elements: List[Dict] = []
    edge_counts: Dict[str, int] = {}
    edges_seen: set[Tuple[str, str, str]] = set()

    # nodes
    node_ids: List[str] = []
    for idx, item in enumerate(items):
        if len(node_ids) >= max_nodes:
            break
        node_id = str(item.get("self_ref") or item.get("id") or f"n{idx}")
        label = str(item.get("label") or item.get("type") or f"Item {idx}")
        text = str(item.get("text") or "")
        elements.append({"data": {"id": node_id, "label": label, "text": text}})
        node_ids.append(node_id)

    def add_edge(src: str, tgt: str, etype: str):
        key = (src, tgt, etype)
        if src and tgt and key not in edges_seen:
            edges_seen.add(key)
            edge_counts[etype] = edge_counts.get(etype, 0) + 1
            elements.append({"data": {"id": f"{src}->{tgt}->{etype}", "source": src, "target": tgt, "label": etype}})

    # CHILD edges
    for idx, item in enumerate(items):
        if idx >= len(node_ids):
            continue
        parent_id = node_ids[idx]
        children = item.get("children")
        if isinstance(children, list):
            for child in children:
                child_id = None
                if isinstance(child, dict):
                    child_id = str(child.get("self_ref") or child.get("id") or "")
                elif isinstance(child, str):
                    child_id = child
                elif isinstance(child, int) and 0 <= child < len(node_ids):
                    child_id = node_ids[child]
                if child_id:
                    add_edge(parent_id, child_id, "CHILD")

    # NEXT edges
    for i in range(len(node_ids) - 1):
        add_edge(node_ids[i], node_ids[i + 1], "NEXT")

    return elements, edge_counts
