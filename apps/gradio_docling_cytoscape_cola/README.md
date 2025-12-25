# apps/gradio_docling_cytoscape_cola/app.py

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Set

from dash import Dash, Input, Output, State, dcc, html, no_update
import dash_cytoscape as cyto

cyto.load_extra_layouts()

# =========================
# Docling JSON parsing
# =========================
@dataclass
class Item:
    id: str
    label: str
    text: str
    depth: int
    parent_id: Optional[str]
    page_no: Optional[int]


def pick_label(d: Dict[str, Any]) -> str:
    for k in ("label", "type", "kind", "name"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "ITEM"


def pick_text(d: Dict[str, Any]) -> str:
    for k in ("text", "content", "value", "title", "caption"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    v = d.get("text")
    if isinstance(v, dict):
        vv = v.get("content")
        if isinstance(vv, str):
            return vv
    return ""


def pick_page_no(d: Dict[str, Any]) -> Optional[int]:
    prov = d.get("prov")
    if isinstance(prov, list) and prov:
        p0 = prov[0]
        if isinstance(p0, dict):
            try:
                return int(p0.get("page_no"))
            except Exception:
                return None
    try:
        return int(d.get("page_no"))
    except Exception:
        return None


def children_of(d: Dict[str, Any]) -> List[Any]:
    for k in ("children", "items", "nodes", "content_items"):
        v = d.get(k)
        if isinstance(v, list):
            return v
    return []


def is_candidate(d: Dict[str, Any]) -> bool:
    if not isinstance(d, dict):
        return False
    has_label = any(isinstance(d.get(k), str) for k in ("label", "type", "kind", "name"))
    has_text = isinstance(d.get("text"), (str, dict)) or isinstance(d.get("content"), str)
    has_children = isinstance(d.get("children"), list)
    has_prov = isinstance(d.get("prov"), list) or ("page_no" in d)
    return (has_label and (has_text or has_children)) or (has_prov and has_text)


# =========================
# Label truncation helper (NEW)
# =========================
def label_max_lines(text: str, max_chars: int = 34, max_lines: int = 3) -> str:
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if len(lines) == max_lines and len(words) > len(" ".join(lines).split()):
        lines[-1] = lines[-1][: max_chars - 1] + "…"
    return "\n".join(lines[:max_lines])


def extract_items(doc: Any, max_items: int = 3000):
    items: List[Item] = []
    edges: List[Tuple[str, str, str]] = []
    counter = 0

    def walk(x: Any, depth: int, parent: Optional[str]) -> Optional[str]:
        nonlocal counter
        if len(items) >= max_items:
            return None

        if isinstance(x, dict):
            if is_candidate(x):
                nid = f"n{counter}"
                counter += 1
                items.append(
                    Item(
                        id=nid,
                        label=pick_label(x),
                        text=pick_text(x),
                        depth=depth,
                        parent_id=parent,
                        page_no=pick_page_no(x),
                    )
                )

                child_ids = []
                for ch in children_of(x):
                    cid = walk(ch, depth + 1, nid)
                    if cid:
                        edges.append((nid, cid, "CHILD"))
                        child_ids.append(cid)

                for a, b in zip(child_ids, child_ids[1:]):
                    edges.append((a, b, "NEXT"))

                return nid

            for v in x.values():
                walk(v, depth, parent)

        elif isinstance(x, list):
            prev = None
            for it in x:
                cid = walk(it, depth, parent)
                if cid and prev:
                    edges.append((prev, cid, "NEXT"))
                prev = cid
        return None

    walk(doc, 0, None)
    return items, edges


def build_elements(items: List[Item], edges: List[Tuple[str, str, str]]):
    elements: List[Dict[str, Any]] = []

    for it in items:
        body = label_max_lines(it.text or "")
        label = it.label if not body else f"{it.label}:\n{body}"

        elements.append(
            {
                "data": {
                    "id": it.id,
                    "label": label,
                    "page_no": it.page_no,
                    "full_text": it.text,
                }
            }
        )

    for src, tgt, etype in edges:
        elements.append(
            {
                "data": {
                    "id": f"{etype}-{src}-{tgt}",
                    "source": src,
                    "target": tgt,
                    "etype": etype,
                },
                "classes": etype.lower(),
            }
        )
    return elements


# =========================
# Dash App
# =========================
app = Dash(__name__)
server = app.server

COLA_LAYOUT = {
    "name": "cola",
    "animate": True,
    "fit": True,
    "padding": 60,
    "nodeSpacing": 60,
    "edgeLengthVal": 200,
}

# =========================
# NODE STYLE FIX (ONLY CHANGE)
# =========================
STYLESHEET = [
    {"selector": "core", "style": {"background-color": "#0b0b0b"}},
    {
        "selector": "node",
        "style": {
            "shape": "round-rectangle",
            "background-color": "#0f0f0f",
            "border-color": "#3a3a3a",
            "border-width": 1,
            "label": "data(label)",
            "color": "#ffffff",
            "font-size": 14,
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": 300,
            "width": 360,
            "height": 110,
            "padding": "10px",
        },
    },
    {
        "selector": "edge",
        "style": {
            "curve-style": "bezier",
            "width": 1,
            "line-color": "#888",
            "target-arrow-shape": "triangle",
            "target-arrow-color": "#888",
            "label": "",
        },
    },
    {"selector": "edge.next", "style": {"line-style": "dashed", "line-opacity": 0.25}},
    {"selector": "edge.child", "style": {"width": 2}},
]

app.layout = html.Div(
    style={"background": "#0b0b0b", "color": "#fff", "padding": "12px"},
    children=[
        html.H3("Docling Chunk Graph (CHILD / NEXT – cola layout)"),
        dcc.Upload(id="upload", children=html.Div("Upload Docling JSON"), multiple=False),
        html.Button("Load", id="load"),
        html.Div(id="status"),
        dcc.Store(id="elements_store"),
        cyto.Cytoscape(
            id="cy",
            elements=[],
            layout=COLA_LAYOUT,
            stylesheet=STYLESHEET,
            style={"width": "100%", "height": "80vh"},
        ),
        html.Pre(id="selected"),
    ],
)


def parse_upload(contents: str) -> Any:
    _, b64 = contents.split(",", 1)
    return json.loads(base64.b64decode(b64).decode("utf-8"))


@app.callback(
    Output("elements_store", "data"),
    Output("status", "children"),
    Input("load", "n_clicks"),
    State("upload", "contents"),
    prevent_initial_call=True,
)
def load_graph(_, contents):
    data = parse_upload(contents)
    items, edges = extract_items(data)
    return build_elements(items, edges), f"Loaded {len(items)} nodes"


@app.callback(Output("cy", "elements"), Input("elements_store", "data"))
def show_graph(elements):
    return elements or []


@app.callback(
    Output("selected", "children"),
    Input("cy", "tapNodeData"),
    Input("cy", "tapEdgeData"),
)
def show_selected(n, e):
    if n:
        return json.dumps(n, indent=2)
    if e:
        return json.dumps(e, indent=2)
    return "Click a node"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8057, debug=False)
