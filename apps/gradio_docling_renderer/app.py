# app.py
# Gradio app: upload a Docling JSON export and render it as "black boxes / white text".
#
# Behaviour:
# - ONE orange section box: section_header → next section_header
# - page_footer is never included inside the section box
# - Detects DoclingDocument.groups (optional structural grouping, commonly lists)

from __future__ import annotations

import json
import html
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr


# -----------------------------
# Helpers: tolerant Docling JSON extraction
# -----------------------------
@dataclass
class RenderItem:
    idx: int
    label: str
    text: str
    depth: int
    page_no: Optional[int] = None
    bbox: Optional[List[float]] = None


def _as_int(x: Any) -> Optional[int]:
    try:
        return int(x) if x is not None else None
    except Exception:
        return None


def _pick_label(obj: Dict[str, Any]) -> str:
    for k in ("label", "type", "kind", "name"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "ITEM"


def _pick_text(obj: Dict[str, Any]) -> str:
    for k in ("text", "content", "value", "title", "caption"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    v = obj.get("text")
    if isinstance(v, dict):
        for kk in ("content", "value", "text"):
            vv = v.get(kk)
            if isinstance(vv, str) and vv.strip():
                return vv.strip()

    return ""


def _pick_prov(obj: Dict[str, Any]) -> Tuple[Optional[int], Optional[List[float]]]:
    prov = obj.get("prov")
    if isinstance(prov, list) and prov:
        p0 = prov[0]
        if isinstance(p0, dict):
            page_no = _as_int(p0.get("page_no"))
            bbox = p0.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                return page_no, bbox
            return page_no, None
    return None, None


def _iter_children(obj: Dict[str, Any]) -> List[Any]:
    for k in ("children", "items", "nodes", "content_items"):
        v = obj.get(k)
        if isinstance(v, list):
            return v
    return []


def extract_render_items(doc_json: Any, max_items: int = 2000) -> List[RenderItem]:
    out: List[RenderItem] = []
    idx = 0

    def is_candidate(d: Dict[str, Any]) -> bool:
        if not isinstance(d, dict):
            return False
        has_label = any(isinstance(d.get(k), str) for k in ("label", "type", "kind", "name"))
        has_text = isinstance(d.get("text"), (str, dict)) or isinstance(d.get("content"), str)
        has_children = isinstance(d.get("children"), list) or isinstance(d.get("items"), list)
        has_prov = isinstance(d.get("prov"), list)
        return (has_label and (has_text or has_children)) or (has_prov and has_text)

    def walk(x: Any, depth: int):
        nonlocal idx
        if len(out) >= max_items:
            return

        if isinstance(x, dict):
            if is_candidate(x):
                label = _pick_label(x)
                text = _pick_text(x)
                page_no, bbox = _pick_prov(x)
                out.append(RenderItem(idx, label, text, depth, page_no, bbox))
                idx += 1

                for c in _iter_children(x):
                    walk(c, depth + 1)
            else:
                for v in x.values():
                    walk(v, depth)

        elif isinstance(x, list):
            for it in x:
                walk(it, depth)

    walk(doc_json, 0)
    return out


# -----------------------------
# Render
# -----------------------------
def render_boxes(items: List[RenderItem], show_empty_text: bool, limit: int) -> str:
    css = """
    <style>
      .dv-box {
        background:#0b0b0b;
        color:#f4f4f4;
        border:1px solid #2a2a2a;
        border-radius:10px;
        padding:10px 12px;
        margin:8px 0;
        white-space:pre-wrap;
      }
      .dv-section {
        border:2px solid #ff8c00;
        border-radius:14px;
        padding:10px 12px;
        margin:14px 0;
        background:rgba(255,140,0,0.06);
      }
    </style>
    """

    visible = [it for it in items[:limit] if it.text or show_empty_text]

    parts = [css, "<div>"]
    section_open = False

    for it in visible:
        if it.label == "page_footer":
            if section_open:
                parts.append("</div>")
                section_open = False

        if it.label == "section_header":
            if section_open:
                parts.append("</div>")
            parts.append("<div class='dv-section'>")
            parts.append(f"<b>{html.escape(it.text)}</b>")
            section_open = True
            continue

        parts.append(f"<div class='dv-box'>{html.escape(it.text)}</div>")

    if section_open:
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


# -----------------------------
# Gradio callbacks
# -----------------------------
def load_and_render(uploaded_file, show_empty_text: bool, max_items: int, render_limit: int):
    with open(uploaded_file.name, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Small, low-risk detection of DoclingDocument.groups
    groups_present = isinstance(data.get("groups"), list)
    groups_count = len(data["groups"]) if groups_present else 0

    items = extract_render_items(data, max_items)
    html_out = render_boxes(items, show_empty_text, render_limit)

    return html_out, {
        "items": len(items),
        "groups_present": groups_present,
        "groups_count": groups_count,
        "note": "DoclingDocument.groups is optional and commonly used for list collections."
    }


# -----------------------------
# UI
# -----------------------------
with gr.Blocks(title="Docling JSON Renderer") as demo:
    file_in = gr.File(file_types=[".json"])
    show_empty = gr.Checkbox(False, label="Show empty text")
    max_items = gr.Slider(100, 10000, value=2000)
    render_limit = gr.Slider(50, 5000, value=600)
    btn = gr.Button("Render")
    html_view = gr.HTML()
    stats = gr.JSON()

    btn.click(
        load_and_render,
        [file_in, show_empty, max_items, render_limit],
        [html_view, stats],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
