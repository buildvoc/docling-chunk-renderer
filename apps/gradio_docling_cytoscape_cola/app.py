from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Set

from dash import Dash, Input, Output, State, dcc, html, no_update
import dash_cytoscape as cyto

import networkx as nx  # <-- ADDED


# NOTE:
# Dash Cytoscape extra layouts do NOT include 'fcose' (they include cola, dagre, klay, etc.).
# fCoSE must be registered via external scripts, per cytoscape-fcose README.
# See: https://github.com/iVis-at-Bilkent/cytoscape.js-fcose#usage-instructions
EXTERNAL_SCRIPTS = [
    "https://unpkg.com/layout-base/layout-base.js",
    "https://unpkg.com/cose-base/cose-base.js",
    "https://unpkg.com/cytoscape-fcose/cytoscape-fcose.js",
]

# Keep this if you still want other extra layouts available (cola/dagre/klay/etc.)
cyto.load_extra_layouts()

DOCLING_ROOT = "/home/hp/docling-ws/data/docling"


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
    for k in ("text", "content", "value", "title"):
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if isinstance(d.get("text"), dict):
        vv = d["text"].get("content")
        if isinstance(vv, str):
            return vv
    return ""


def pick_page_no(d: Dict[str, Any]) -> Optional[int]:
    """
    Docling items often have: prov: [{"page_no": 1, "bbox": [...], ...}, ...]
    We'll use the first provenance entry.
    """
    prov = d.get("prov")
    if isinstance(prov, list) and prov:
        p0 = prov[0]
        if isinstance(p0, dict):
            pn = p0.get("page_no")
            try:
                return int(pn) if pn is not None else None
            except Exception:
                return None

    # fallback keys (sometimes present in alternate exports)
    pn = d.get("page_no")
    try:
        return int(pn) if pn is not None else None
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
    has_text = (
        isinstance(d.get("text"), (str, dict))
        or isinstance(d.get("content"), str)
        or isinstance(d.get("value"), str)
    )
    has_children = isinstance(d.get("children"), list) or isinstance(d.get("items"), list)
    has_prov = isinstance(d.get("prov"), list) or ("page_no" in d)
    return (has_label and (has_text or has_children)) or (has_prov and (has_text or has_children))


# =========================
# Label clamp helper
# Ensures the text cannot overflow outside the rectangle.
# =========================
def label_max_lines(text: str, max_chars_per_line: int = 34, max_lines: int = 3) -> str:
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return ""

    words = text.split()
    lines: List[str] = []
    cur = ""

    for w in words:
        if len(cur) + (1 if cur else 0) + len(w) <= max_chars_per_line:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) >= max_lines:
                break

    if cur and len(lines) < max_lines:
        lines.append(cur)

    # If we truncated, add ellipsis to the last line
    original = " ".join(words)
    shown = " ".join(lines)
    if len(shown) < len(original) and lines:
        last = lines[-1]
        if len(last) >= max_chars_per_line:
            last = last[: max(0, max_chars_per_line - 1)]
        lines[-1] = (last + "&").strip()

    return "\n".join(lines[:max_lines])


# =========================
# $ref dereferencing (MINIMAL ADDITION TO ENABLE CHILD EDGES)
# =========================
def resolve_ref(root: Any, ref: str) -> Optional[Any]:
    """
    Supports refs like "#/texts/0", "#/body", "#/pages/2", etc.
    """
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    cur: Any = root
    for part in ref[2:].split("/"):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
        if cur is None:
            return None
    return cur


def deref(root: Any, x: Any) -> Any:
    """
    If x is {"$ref": "#/..."} return the referenced object, else return x.
    """
    if isinstance(x, dict) and "$ref" in x and isinstance(x["$ref"], str):
        target = resolve_ref(root, x["$ref"])
        return target if target is not None else x
    return x


def extract_items(doc: Any, max_items: int = 3000, id_prefix: str = "f0"):
    """
    id_prefix prevents collisions when loading multiple JSON files at once.
    """
    items: List[Item] = []
    edges: List[Tuple[str, str, str]] = []
    counter = 0

    def walk(x: Any, depth: int, parent: Optional[str]) -> Optional[str]:
        nonlocal counter
        if len(items) >= max_items:
            return None

        # --- ONLY CHANGE THAT MAKES CHILD EDGES WORK ---
        x = deref(doc, x)

        if isinstance(x, dict):
            if is_candidate(x):
                node_id = f"{id_prefix}_n{counter}"
                counter += 1

                items.append(
                    Item(
                        id=node_id,
                        label=pick_label(x),
                        text=pick_text(x),
                        depth=depth,
                        parent_id=parent,
                        page_no=pick_page_no(x),
                    )
                )

                child_ids: List[str] = []
                for ch in children_of(x):
                    cid = walk(ch, depth + 1, node_id)
                    if cid:
                        edges.append((node_id, cid, "CHILD"))
                        child_ids.append(cid)

                # NEXT edges between siblings
                for a, b in zip(child_ids, child_ids[1:]):
                    edges.append((a, b, "NEXT"))

                return node_id

            # not a candidate: search deeper
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
        body = label_max_lines(it.text or "", max_chars_per_line=34, max_lines=3)
        if body:
            label = f"{it.label}:\n{body}"
        else:
            label = it.label

        elements.append(
            {
                "data": {
                    "id": it.id,
                    "label": label,           # rendered label for display
                    "kind": it.label,         # raw docling kind (used for section_header filter)
                    "depth": it.depth,
                    "page_no": it.page_no,
                    "full_text": (it.text or "").replace("\n", " ").strip(),
                    # networkx fields will be injected later
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
                    "label": etype,
                },
                "classes": etype.lower(),
            }
        )

    return elements


# =========================
# NetworkX metrics
# =========================
def add_networkx_metrics_to_elements(
    elements: List[Dict[str, Any]],
    edges: List[Tuple[str, str, str]],
) -> Dict[str, Any]:
    """
    Adds NetworkX-derived metrics into node.data:
      nx_in, nx_out, nx_degree, nx_betweenness, nx_component
    Returns a small summary dict (counts) for status/debug.
    """
    G = nx.DiGraph()
    node_ids: Set[str] = set()

    # add nodes from elements
    for el in elements:
        d = el.get("data", {})
        if "source" not in d and "target" not in d:
            nid = d.get("id")
            if isinstance(nid, str):
                node_ids.add(nid)
                G.add_node(nid)

    # add edges from parsed tuples (more reliable than scanning elements)
    for src, tgt, etype in edges:
        if src in node_ids and tgt in node_ids:
            G.add_edge(src, tgt, etype=etype)

    # degrees
    indeg = dict(G.in_degree())
    outdeg = dict(G.out_degree())
    deg = {n: indeg.get(n, 0) + outdeg.get(n, 0) for n in G.nodes()}

    # betweenness + components on undirected view (more intuitive for clusters)
    UG = G.to_undirected()
    try:
        bet = nx.betweenness_centrality(UG, normalized=True)
    except Exception:
        bet = {n: 0.0 for n in UG.nodes()}

    comp_map: Dict[str, int] = {}
    for i, comp in enumerate(nx.connected_components(UG)):
        for n in comp:
            comp_map[n] = i

    # inject metrics into node elements
    for el in elements:
        d = el.get("data", {})
        if "source" in d and "target" in d:
            continue
        nid = d.get("id")
        if not isinstance(nid, str):
            continue
        d["nx_in"] = int(indeg.get(nid, 0))
        d["nx_out"] = int(outdeg.get(nid, 0))
        d["nx_degree"] = int(deg.get(nid, 0))
        d["nx_betweenness"] = float(bet.get(nid, 0.0))
        d["nx_component"] = int(comp_map.get(nid, -1))

    return {
        "nx_nodes": G.number_of_nodes(),
        "nx_edges": G.number_of_edges(),
        "nx_components": len(set(comp_map.values())) if comp_map else 0,
    }


# =========================
# Load from filesystem (all subdirectories)
# =========================
def list_docling_json_files(root: str) -> List[str]:
    paths: List[str] = []
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".json"):
                paths.append(os.path.join(dirpath, fn))
    paths.sort()
    return paths


def load_docling_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# =========================
# Dash App
# =========================
app = Dash(__name__, external_scripts=EXTERNAL_SCRIPTS)
server = app.server

# fCoSE layout options (subset of README defaults, tuned for readability)
FCOSE_LAYOUT = {
    "name": "fcose",
    "quality": "default",      # 'draft'|'default'|'proof'
    "randomize": True,
    "animate": True,
    "animationDuration": 800,
    "fit": True,
    "padding": 60,
    "nodeSeparation": 140,
    "packComponents": False,
    "idealEdgeLength": 180,
    "edgeElasticity": 0.45,
    "nestingFactor": 0.1,
    "numIter": 2000,
}

STYLESHEET = [
    {"selector": "core", "style": {"background-color": "#0b0b0b"}},
    {
        "selector": "node",
        "style": {
            "shape": "round-rectangle",
            "background-color": "#0b0b0b",
            "border-color": "#2a2a2a",
            "border-width": 1,
            "color": "#ffffff",
            "label": "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "text-wrap": "wrap",
            "text-max-width": 300,
            "width": 360,
            "height": 110,
            "padding": "10px",
            "font-size": 12,
            "line-height": 1.2,
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
            "label": "data(etype)",
            "font-size": 9,
            "color": "#ccc",
            "text-background-color": "#000",
            "text-background-opacity": 1,
            "text-background-padding": "2px",
        },
    },
    {"selector": "edge.next", "style": {"line-style": "dashed"}},
]

# Precompute file list once at startup
FILE_PATHS = list_docling_json_files(DOCLING_ROOT)
FILE_OPTIONS = [{"label": os.path.relpath(p, DOCLING_ROOT), "value": p} for p in FILE_PATHS]

app.layout = html.Div(
    style={"background": "#0b0b0b", "color": "#fff", "padding": "12px"},
    children=[
        html.H3("Docling Chunk Graph (CHILD / NEXT \u0013 fCoSE layout)"),
        html.Div(
            style={"display": "flex", "gap": "10px", "flexWrap": "wrap", "alignItems": "center"},
            children=[
                dcc.Dropdown(
                    id="file_paths",
                    options=FILE_OPTIONS,
                    value=[],
                    multi=True,
                    clearable=True,
                    searchable=True,
                    placeholder="Select one or more Docling JSON files from data/docling/\u0003",
                    style={"minWidth": "520px", "background": "#111", "color": "#000"},
                ),

                # section_header filter (multi-select)
                dcc.Dropdown(
                    id="section_headers",
                    options=[],
                    value=[],
                    multi=True,
                    clearable=True,
                    searchable=True,
                    placeholder="section_header\u0003",
                    style={"minWidth": "220px", "width": "220px", "background": "#111", "color": "#000"},
                ),

                dcc.Dropdown(
                    id="edge_types",
                    options=[
                        {"label": "CHILD", "value": "CHILD"},
                        {"label": "NEXT", "value": "NEXT"},
                    ],
                    value=["CHILD", "NEXT"],
                    multi=True,
                    clearable=False,
                    searchable=False,
                    style={"minWidth": "160px", "width": "160px", "background": "#111", "color": "#000"},
                ),

                html.Button("Load", id="load", n_clicks=0),
                html.Div("Page:", style={"color": "#bbb", "marginLeft": "10px"}),
                dcc.Dropdown(
                    id="page_filter",
                    options=[{"label": "All pages", "value": "__ALL__"}],
                    value="__ALL__",
                    clearable=False,
                    style={"minWidth": "160px", "width": "160px", "background": "#111", "color": "#000"},
                ),
            ],
        ),
        html.Div(id="status", style={"margin": "8px 0", "color": "#bbb"}),
        dcc.Store(id="elements_store"),
        dcc.Store(id="pages_store"),
        cyto.Cytoscape(
            id="cy",
            elements=[],
            layout=FCOSE_LAYOUT,
            stylesheet=STYLESHEET,
            style={"width": "100%", "height": "80vh", "border": "1px solid #1e1e1e", "borderRadius": "12px"},
        ),
        html.Pre(id="selected", style={"whiteSpace": "pre-wrap", "wordBreak": "break-word"}),
    ],
)


def collect_pages(elements: List[Dict[str, Any]]) -> List[int]:
    pages: Set[int] = set()
    for el in elements:
        data = el.get("data", {})
        if "source" not in data and "target" not in data:
            pn = data.get("page_no")
            if pn is not None:
                try:
                    pages.add(int(pn))
                except Exception:
                    pass
    return sorted(pages)


@app.callback(
    Output("elements_store", "data"),
    Output("pages_store", "data"),
    Output("page_filter", "options"),
    Output("page_filter", "value"),
    Output("status", "children"),
    Output("section_headers", "options"),
    Input("load", "n_clicks"),
    State("file_paths", "value"),
    prevent_initial_call=True,
)
def load_graph(_, file_paths: Optional[List[str]]):
    if not file_paths:
        return no_update, no_update, no_update, no_update, "No JSON files selected", []

    all_items: List[Item] = []
    all_edges: List[Tuple[str, str, str]] = []

    for i, file_path in enumerate(file_paths):
        try:
            data = load_docling_json(file_path)
        except Exception as e:
            return no_update, no_update, no_update, no_update, f"Failed to read JSON: {file_path} :: {e}", []

        items, edges = extract_items(data, id_prefix=f"f{i}")
        all_items.extend(items)
        all_edges.extend(edges)

    elements = build_elements(all_items, all_edges)

    # --- NetworkX metrics injected here (NEW) ---
    nx_summary = add_networkx_metrics_to_elements(elements, all_edges)

    pages = collect_pages(elements)
    options = [{"label": "All pages", "value": "__ALL__"}] + [{"label": f"Page {p}", "value": p} for p in pages]

    child_count = sum(1 for _, _, t in all_edges if t == "CHILD")
    next_count = sum(1 for _, _, t in all_edges if t == "NEXT")

    status = (
        f"Loaded {len(file_paths)} file(s)  nodes: {len(all_items)}, edges: {len(all_edges)} "
        f"(CHILD: {child_count}, NEXT: {next_count}). Pages detected: {len(pages)}. "
        f"NX: components={nx_summary.get('nx_components', 0)}"
    )

    # initial section_header options (all pages); will be narrowed by page_filter callback below
    section_opts: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for el in elements:
        d = el.get("data", {})
        if "source" in d and "target" in d:
            continue  # edge
        if (d.get("kind") or "").lower() != "section_header":
            continue
        txt = (d.get("full_text") or "").strip()
        if not txt:
            continue
        node_id = d.get("id")
        if not isinstance(node_id, str) or node_id in seen:
            continue
        seen.add(node_id)
        section_opts.append({"label": txt[:160], "value": node_id})

    return elements, pages, options, "__ALL__", status, section_opts


# =========================
# NEW: page_filter applied first � section_header options only from that page
# =========================
@app.callback(
    Output("section_headers", "options", allow_duplicate=True),
    Input("elements_store", "data"),
    Input("page_filter", "value"),
    prevent_initial_call=True,
)
def update_section_headers_for_page(
    stored_elements: Optional[List[Dict[str, Any]]],
    page_value,
):
    if not stored_elements:
        return []

    selected_page: Optional[int] = None
    if page_value not in (None, "__ALL__"):
        try:
            selected_page = int(page_value)
        except Exception:
            selected_page = None

    opts: List[Dict[str, str]] = []
    seen: Set[str] = set()

    for el in stored_elements:
        d = el.get("data", {})
        if "source" in d and "target" in d:
            continue  # edge
        if (d.get("kind") or "").lower() != "section_header":
            continue

        pn = d.get("page_no")
        try:
            pn_i = int(pn) if pn is not None else None
        except Exception:
            pn_i = None

        if selected_page is not None and pn_i != selected_page:
            continue

        txt = (d.get("full_text") or "").strip()
        if not txt:
            continue
        node_id = d.get("id")
        if not isinstance(node_id, str) or node_id in seen:
            continue

        seen.add(node_id)
        opts.append({"label": txt[:160], "value": node_id})

    return opts


@app.callback(
    Output("cy", "elements"),
    Input("elements_store", "data"),
    Input("page_filter", "value"),
    Input("edge_types", "value"),
    Input("section_headers", "value"),
)
def apply_page_filter(
    stored_elements: Optional[List[Dict[str, Any]]],
    page_value,
    edge_types,
    section_headers,
):
    if not stored_elements:
        return []

    allowed_edge_types = set(edge_types or [])

    visible_nodes: Set[str] = set()
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

    selected_page: Optional[int] = None
    if page_value not in (None, "__ALL__"):
        try:
            selected_page = int(page_value)
        except Exception:
            selected_page = None

    # Page filter FIRST (build visible_nodes only from selected page)
    for el in stored_elements:
        data = el.get("data", {})
        if "source" in data and "target" in data:
            edges.append(el)
        else:
            if selected_page is None:
                nid = data.get("id")
                if isinstance(nid, str):
                    visible_nodes.add(nid)
                nodes.append(el)
            else:
                pn = data.get("page_no")
                try:
                    pn_i = int(pn) if pn is not None else None
                except Exception:
                    pn_i = None

                if pn_i == selected_page:
                    nid = data.get("id")
                    if isinstance(nid, str):
                        visible_nodes.add(nid)
                    nodes.append(el)

    # Then section_header filter ONLY within that page (intersection keeps it page-scoped)
    selected_sections = set(section_headers or [])
    if selected_sections:
        allowed_edge_types |= {"CHILD", "NEXT"}

        expanded = set(selected_sections)
        added = True
        while added:
            added = False
            for el in edges:
                d = el.get("data", {})
                if d.get("etype") != "CHILD":
                    continue
                src = d.get("source")
                tgt = d.get("target")
                if src in expanded and tgt not in expanded:
                    expanded.add(tgt)
                    added = True

        added = True
        while added:
            added = False
            for el in edges:
                d = el.get("data", {})
                if d.get("etype") != "CHILD":
                    continue
                src = d.get("source")
                tgt = d.get("target")
                if tgt in expanded and src not in expanded:
                    expanded.add(src)
                    added = True

        # critical: ensures the filtered section stays inside the page-filtered set
        visible_nodes &= expanded

    kept_edges: List[Dict[str, Any]] = []
    for el in edges:
        data = el.get("data", {})
        etype = data.get("etype")
        if etype not in allowed_edge_types:
            continue

        src = data.get("source")
        tgt = data.get("target")
        if src in visible_nodes and tgt in visible_nodes:
            kept_edges.append(el)

    kept_nodes = [n for n in nodes if n.get("data", {}).get("id") in visible_nodes]
    return kept_nodes + kept_edges


@app.callback(
    Output("selected", "children"),
    Input("cy", "tapNodeData"),
    Input("cy", "tapEdgeData"),
)
def show_selected(node, edge):
    if node:
        return json.dumps(node, indent=2, ensure_ascii=False)
    if edge:
        return json.dumps(edge, indent=2, ensure_ascii=False)
    return "Click a node or edge"


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8057,
        debug=False,
    )
 