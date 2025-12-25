from __future__ import annotations

from typing import Dict, List, Tuple


def _coords(item: Dict) -> Tuple[int, float, float]:
    page = item.get("page_no") or item.get("page") or 0
    bbox = item.get("bbox") or {}
    y = bbox.get("y") if isinstance(bbox, dict) else None
    x = bbox.get("x") if isinstance(bbox, dict) else None
    # fallbacks keep determinism
    y = float(y) if y is not None else 0.0
    x = float(x) if x is not None else 0.0
    return int(page), y, x


def deterministic_next_edges(doc: Dict) -> List[Dict]:
    """Produce deterministic NEXT edges across docitems grouped by page and sorted by bbox."""
    items = doc.get("texts") or doc.get("chunks") or []
    ordered = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_id = item.get("self_ref") or item.get("id") or f"chunk_{idx}"
        page, y, x = _coords(item)
        ordered.append((page, y, x, item_id, item))

    ordered.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    edges: List[Dict] = []
    for i in range(len(ordered) - 1):
        prev = ordered[i]
        curr = ordered[i + 1]
        rationale = f"page {curr[0]} sorted by y={curr[1]} x={curr[2]} after {prev[3]}"
        edges.append(
            {
                "id": f"{prev[3]}->{curr[3]}->NEXT",
                "source": prev[3],
                "target": curr[3],
                "type": "NEXT",
                "rationale": rationale,
            }
        )
    return edges
