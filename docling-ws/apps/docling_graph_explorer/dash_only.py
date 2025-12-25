from __future__ import annotations

from typing import Any, Dict, List, Optional
import threading
import logging
import time

import dash
from dash import Dash, dcc, html
from dash.dependencies import Input, Output
import dash_cytoscape as cyto

logger = logging.getLogger("docling_graph_explorer")

# -----------------------------------------------------------------------------
# Shared in-process state (written by Gradio, read by Dash)
# -----------------------------------------------------------------------------
_WINDOW_STATE: Dict[str, Any] = {
    "doc_hash": None,
    "visible_chunk_ids": [],
    "meta": {},
    "elements": [],
    "updated_at": None,
}

_LAST_SELECTION: Dict[str, Any] = {"node_id": None, "updated_at": None}
_LOCK = threading.Lock()


def update_window_state(state: Dict[str, Any]) -> None:
    """Update shared state from Gradio."""
    with _LOCK:
        _WINDOW_STATE.update(state)
    logger.info(
        "dash window_state update doc_hash=%s visible=%d meta_nodes=%s meta_edges=%s elements=%d",
        _WINDOW_STATE.get("doc_hash"),
        len(_WINDOW_STATE.get("visible_chunk_ids") or []),
        _WINDOW_STATE.get("meta", {}).get("nodes"),
        _WINDOW_STATE.get("meta", {}).get("edges"),
        len(_WINDOW_STATE.get("elements") or []),
    )


def current_state() -> Dict[str, Any]:
    with _LOCK:
        return dict(_WINDOW_STATE)


def push_selection(node_id: Optional[str]) -> None:
    if not node_id:
        return
    with _LOCK:
        _LAST_SELECTION["node_id"] = node_id
        _LAST_SELECTION["updated_at"] = time.time()
    logger.info("dash selection set node_id=%s", node_id)


def consume_last_selection() -> Optional[str]:
    with _LOCK:
        node_id = _LAST_SELECTION.get("node_id")
        _LAST_SELECTION["node_id"] = None
        return node_id


# -----------------------------------------------------------------------------
# Demo fallback (from dash-cytoscape usage-elements-extra)
# -----------------------------------------------------------------------------
def _demo_elements() -> List[Dict[str, Any]]:
    return [
        {"data": {"id": "one", "label": "Node 1"}, "position": {"x": 75, "y": 75}},
        {"data": {"id": "two", "label": "Node 2"}, "position": {"x": 75, "y": 200}},
        {"data": {"id": "three", "label": "Node 3"}, "position": {"x": 75, "y": 325}},
        {"data": {"source": "one", "target": "two"}, "classes": "autorotate"},
        {"data": {"source": "two", "target": "three"}, "classes": "autorotate"},
    ]


def _demo_stylesheet() -> List[Dict[str, Any]]:
    return [
        {
            "selector": "node",
            "style": {
                "label": "data(label)",
                "text-halign": "center",
                "text-valign": "center",
            },
        },
        {
            "selector": "edge",
            "style": {
                "curve-style": "bezier",
                "target-arrow-shape": "triangle",
            },
        },
        {"selector": ".autorotate", "style": {"text-rotation": "autorotate"}},
        {"selector": ".selected", "style": {"border-width": 3, "border-color": "#f97316"}},
    ]


# -----------------------------------------------------------------------------
# Dash app factory
# -----------------------------------------------------------------------------
def create_dash_app(base_path: str = "/dash") -> Dash:
    prefix = base_path if base_path.endswith("/") else base_path + "/"

    cyto.load_extra_layouts()

    app = dash.Dash(
        __name__,
        requests_pathname_prefix=prefix,
        routes_pathname_prefix=prefix,
        suppress_callback_exceptions=True,
    )

    server = app.server

    # -------------------------------------------------------------------------
    # FIXED: Serve BOTH Cytoscape bundles correctly
    # -------------------------------------------------------------------------
    @server.route("/_dash-component-suites/dash_cytoscape/<path:asset>")
    def serve_dash_cytoscape(asset: str):
        # dash_cytoscape registers these in cyto._js_dist
        fname = asset.split("/")[-1]

        allowed = []
        for item in (cyto._js_dist or []):
            rel = item.get("relative_package_path")
            if rel:
                allowed.append(rel.split("/")[-1])

        if fname not in allowed:
            logger.info("dash cytoscape asset miss: %s (allowed=%s)", fname, allowed)
            return ("Not Found", 404)

        import pkgutil

        data = pkgutil.get_data("dash_cytoscape", fname)
        if data is None:
            return ("Not Found", 404)

        return (data, 200, {"Content-Type": "application/javascript"})

    # -------------------------------------------------------------------------
    # Layout
    # -------------------------------------------------------------------------
    app.layout = html.Div(
        [
            dcc.Interval(id="refresh", interval=1500, n_intervals=0),
            html.Div(
                [
                    cyto.Cytoscape(
                        id="cytoscape",
                        elements=_demo_elements(),
                        layout={"name": "fcose"},
                        minZoom=0.15,
                        maxZoom=2.5,
                        style={
                            "width": "75%",
                            "height": "90vh",
                            "border": "1px solid #e2e8f0",
                            "borderRadius": "6px",
                        },
                        stylesheet=_demo_stylesheet(),
                    ),
                    html.Div(
                        [
                            html.Div(id="mode_banner", style={"fontWeight": "600", "marginBottom": "6px"}),
                            html.Div(id="counts", style={"fontWeight": "600", "marginBottom": "6px"}),
                            html.Div(
                                id="window_meta",
                                style={"fontSize": "12px", "opacity": 0.8, "marginBottom": "8px"},
                            ),
                            html.Div("Selected node", style={"fontWeight": "600", "marginBottom": "4px"}),
                            html.Pre(
                                id="selected_node",
                                style={"whiteSpace": "pre-wrap", "fontSize": "12px"},
                            ),
                        ],
                        style={
                            "width": "25%",
                            "border": "1px solid #e2e8f0",
                            "borderRadius": "6px",
                            "padding": "10px",
                            "background": "#f8fafc",
                        },
                    ),
                ],
                style={
                    "display": "flex",
                    "flexDirection": "row",
                    "gap": "12px",
                    "padding": "8px",
                },
            ),
        ]
    )

    # -------------------------------------------------------------------------
    # Refresh callback
    # -------------------------------------------------------------------------
    @app.callback(
        Output("cytoscape", "elements"),
        Output("window_meta", "children"),
        Output("counts", "children"),
        Output("mode_banner", "children"),
        Input("refresh", "n_intervals"),
    )
    def refresh(_n):
        state = current_state()
        meta = state.get("meta", {})
        elements: List[Dict[str, Any]] = state.get("elements") or []

        mode = "Shared state elements" if elements else "Demo elements (fallback)"
        if not elements:
            elements = _demo_elements()

        logger.info(
            "dash refresh doc_hash=%s visible=%d meta_nodes=%s meta_edges=%s elements=%d mode=%s",
            state.get("doc_hash"),
            len(state.get("visible_chunk_ids") or []),
            meta.get("nodes", 0),
            meta.get("edges", 0),
            len(elements),
            mode,
        )

        indicator = f"{meta.get('showing','')} | nodes={meta.get('nodes',0)} edges={meta.get('edges',0)}"
        counts = f"Nodes: {meta.get('nodes',0)} | Edges: {meta.get('edges',0)}"

        return elements, indicator, counts, mode

    # -------------------------------------------------------------------------
    # Node selection
    # -------------------------------------------------------------------------
    @app.callback(Output("selected_node", "children"), Input("cytoscape", "tapNodeData"))
    def on_node_click(data):
        if not data:
            return "Click a node"
        node_id = data.get("id") or data.get("node_id") or data.get("label")
        push_selection(node_id)
        return "\n".join(
            [
                f"id: {node_id}",
                f"type: {data.get('type','')}",
                f"label: {data.get('label','')}",
            ]
        )

    return app
