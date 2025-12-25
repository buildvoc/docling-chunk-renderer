# Docling Graph Explorer (Standalone)

Standalone split-pane Gradio UI (no Docling-Serve integration) with an embedded Dash + Cytoscape graph to inspect Docling-exported JSON. The app runs on **8055** by default; the internal Dash iframe uses **127.0.0.1:8056**. Port **5001** (Docling API) and **8050** are untouched.

## Layout and sync
- Left pane renders Docling chunks as black cards with white text (Docling Rendered style) and stable `chunk_id` anchors.
- Right pane hosts Dash/Cytoscape (fcose layout only) fed by a windowed subgraph.
- Scroll in the rendered pane publishes the visible chunk window (debounced ~200ms) and the graph renders only that window plus deterministic context.
- Clicking a graph node scrolls the rendered pane to the matching chunk. Sync can be toggled on/off; manual “Go to chunk id” remains available.

## Windowed rendering
- Visible chunk ids drive a window extractor with context buffer and hard node cap (default 600).
- Context includes adjacent chunks and hierarchy neighbors; NEXT edges included only when both endpoints are in-window.
- If the cap is exceeded the buffer shrinks and visible chunks are prioritized. UI shows “chunks X–Y (N nodes, M edges)”.

## NEXT heuristic
- Deterministic rules: group by `page_no`, sort by bbox `(y, x)`, then emit sequential NEXT edges with a human-readable rationale (page, sort keys, previous id).

## NetworkX analytics
- Degree, betweenness centrality, and deterministic community id are computed on the full graph and attached to nodes; metrics carry into the windowed subset.

## Caching
- Hash-based cache (SHA256 of uploaded JSON bytes) stores parsed doc, full graph, metrics, and chunk order. Logs include cache hit/miss and timings.

## Logs and debugging
- Rotating log file at `/home/hp/docling-ws/logs/docling_graph_explorer.log` (INFO; DEBUG via `DOCLING_GRAPH_DEBUG=1`).
- Debug panel: tail lines (200/500/1000), regex/substring filter, errors-only toggle, refresh, and “Copy debug bundle” with commit/hash, window meta, and recent errors.

## Running
```bash
.venv/bin/python -m apps.docling_graph_explorer.app --port 8055 --dash-port 8056
```

## Tests
```bash
pytest docling-ws/apps/docling_graph_explorer/tests
```

## Troubleshooting
- Empty graph window: ensure the JSON contains `texts`/`chunks`; check logs for cache hits and window sizes.
- iframe issues: confirm Dash is listening on 127.0.0.1:8056 and the iframe src matches.
- Missing provenance (bbox/page): NEXT edges fall back to defaults but remain deterministic.
- Log viewer empty: verify `/home/hp/docling-ws/logs/` permissions; enable `DOCLING_GRAPH_DEBUG=1` for verbose traces.
