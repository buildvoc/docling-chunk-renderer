from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gradio as gr

from . import cache
from .dash_app import consume_last_selection, update_window_state
from .graph_builder import GraphData, build_full_graph, window_subgraph
from .io import doc_stats, load_docling_json, validate_doc
from .logs import DEFAULT_LOG_PATH, filter_log, git_commit, setup_logging, tail_log

logger = setup_logging()


def _read_upload(file_obj) -> bytes:
    """Support Gradio uploads (paths, NamedString, file-like)."""
    if file_obj is None:
        raise ValueError("No file provided")

    if isinstance(file_obj, Path):
        return file_obj.read_bytes()

    # File-like with read
    if hasattr(file_obj, "read"):
        return file_obj.read()

    # Has .name attribute (NamedString or UploadedFile)
    if hasattr(file_obj, "name"):
        path = file_obj.name
        return Path(path).read_bytes()

    # Plain path-like
    if isinstance(file_obj, (str, bytes, Path)):
        return Path(file_obj).read_bytes()

    raise ValueError("Unsupported upload type")


def _render_chunks(graph: GraphData) -> str:
    """Render HTML for chunk list with anchors and scroll observer."""
    chunks_by_id = {cid: n for cid, n in [(n["chunk_id"], n) for n in graph.nodes if n.get("chunk_id")] if cid}
    parts = [
        """
        <style>
          .docling-rendered {height: 78vh; overflow-y: auto; border: 1px solid #1f2937; padding: 10px; border-radius: 8px; background: #0b0b0b;}
          .docling-rendered .chunk-block {padding: 10px; margin-bottom: 10px; border: 1px solid #262626; border-radius: 8px; background: #111; color: #f8fafc; box-shadow: 0 1px 2px rgba(0,0,0,0.4);}
          .docling-rendered .meta {font-size: 11px; color: #9ca3af;}
        </style>
        <div id="chunk-pane" class="docling-rendered">
        """
    ]
    for cid in graph.chunk_order:
        node = chunks_by_id.get(cid) or {}
        text = (node.get("text") or "")[:500]
        meta = f"page {node.get('page_no', '?')}"
        parts.append(
            f'<div class="chunk-block docling-card" id="chunk-{cid}" data-chunk-id="{cid}">'
            f"<div class='meta'>chunk {cid} · {meta}</div>"
            f"<div class='body'>{text}</div>"
            "</div>"
        )
    parts.append("</div>")
    parts.append(
        """
        <script>
        (function(){
          const pane = document.getElementById('chunk-pane');
          const output = document.querySelector('#visible-chunks textarea');
          const scrollTarget = document.querySelector('#scroll-target textarea');
          if(!pane || !output) return;
          const debounce = (fn, wait) => {
            let t; return (...args) => { clearTimeout(t); t=setTimeout(()=>fn(...args), wait); };
          };
          const publish = () => {
            const visible=[];
            const children = pane.querySelectorAll('.chunk-block');
            children.forEach(el => {
              const rect = el.getBoundingClientRect();
              const parentRect = pane.getBoundingClientRect();
              const visibleHeight = Math.min(rect.bottom, parentRect.bottom) - Math.max(rect.top, parentRect.top);
              if (visibleHeight > 30) { visible.push(el.dataset.chunkId); }
            });
            output.value = JSON.stringify(visible);
            output.dispatchEvent(new Event('input', {bubbles: true}));
          };
          pane.addEventListener('scroll', debounce(publish, 200));
          publish();

          const observer = new MutationObserver(publish);
          observer.observe(pane, {childList: true});

          if (scrollTarget) {
            scrollTarget.addEventListener('input', () => {
              const targetId = scrollTarget.value;
              if (!targetId) return;
              const el = document.getElementById(`chunk-${targetId}`);
              if (el && el.scrollIntoView) {
                el.scrollIntoView({behavior: 'smooth', block: 'start'});
              }
            });
          }
        })();
        </script>
        """
    )
    return "\n".join(parts)


def _build_graph_from_bytes(file_bytes: bytes) -> Tuple[GraphData, Dict, str, str]:
    doc, doc_hash = load_docling_json(file_bytes)
    validate_doc(doc)
    entry = cache.get(doc_hash)
    if entry:
        graph_data = GraphData(
            doc_hash=doc_hash,
            nodes=entry.graph.get("nodes", []),
            edges=entry.graph.get("edges", []),
            chunk_order=list(entry.chunk_order),
            stats={"nodes": len(entry.graph.get("nodes", [])), "edges": len(entry.graph.get("edges", [])), "chunks": len(entry.chunk_order)},
        )
        return graph_data, doc_stats(doc), doc_hash, "cache_hit"

    graph_data = build_full_graph(doc, doc_hash)
    metrics = {n["id"]: n.get("metrics", {}) for n in graph_data.nodes}
    cache.set_entry(
        doc_hash=doc_hash,
        doc=doc,
        graph={"nodes": graph_data.nodes, "edges": graph_data.edges},
        metrics=metrics,
        chunk_order=graph_data.chunk_order,
    )
    return graph_data, doc_stats(doc), doc_hash, "cache_miss"


def process_upload(file, buffer: int = 2, max_nodes: int = 600) -> Tuple[GraphData, Dict, str, str, str, str, Dict, str]:
    """Standalone upload handler usable in tests and Gradio callbacks."""
    if file is None:
        return None, {}, "", "No file uploaded", "<div>Upload a Docling JSON to begin</div>", "unknown", {}, "[]"
    data = _read_upload(file)
    graph_data, stats, dhash, cache_status = _build_graph_from_bytes(data)
    initial_visible = graph_data.chunk_order[: max(1, min(5, len(graph_data.chunk_order)))]
    window = window_subgraph(graph_data, visible_chunks=initial_visible, buffer=buffer, max_nodes=max_nodes)
    shared_state = {
        "doc_hash": dhash,
        "visible_chunk_ids": initial_visible,
        "context_buffer": buffer,
        "node_cap": max_nodes,
        "updated_at": datetime.utcnow().timestamp(),
        "elements": window["elements"],
        "meta": window["meta"],
        "window": window["window"],
    }
    update_window_state(shared_state)
    html = _render_chunks(graph_data)
    status = f"Loaded hash={dhash} nodes={len(graph_data.nodes)} edges={len(graph_data.edges)} ({cache_status})"
    logger.info(
        "upload ok doc_hash=%s cache=%s initial_visible=%d nodes=%d edges=%d",
        dhash,
        cache_status,
        len(initial_visible),
        window["meta"]["nodes"],
        window["meta"]["edges"],
    )
    return graph_data, stats, dhash, status, html, cache_status, window["meta"], json.dumps(initial_visible)


def _format_debug_bundle(doc_hash: Optional[str], window_meta: Dict, lines: str, cache_state: str) -> str:
    meta = {
        "doc_hash": doc_hash or "n/a",
        "window": window_meta.get("showing") if window_meta else "n/a",
        "nodes": window_meta.get("nodes") if window_meta else 0,
        "edges": window_meta.get("edges") if window_meta else 0,
    }
    return "\n".join(
        [
            f"Docling Graph Explorer debug bundle",
            f"timestamp: {datetime.utcnow().isoformat()}Z",
            f"git_commit: {git_commit()}",
            f"cache_state: {cache_state or 'n/a'}",
            f"doc_hash: {meta['doc_hash']}",
            f"window: {meta['window']} nodes={meta['nodes']} edges={meta['edges']}",
            "---- recent errors ----",
            lines,
        ]
    )


def create_blocks(dash_url: str = "/dash/") -> gr.Blocks:
    with gr.Blocks(title="Docling Graph Explorer") as demo:
        gr.Markdown("### Docling Graph Explorer — split view with windowed graph rendering")
        doc_state = gr.State(None)  # GraphData
        doc_hash_state = gr.State(None)
        window_meta_state = gr.State({})
        cache_state = gr.State("unknown")
        visible_state = gr.State("[]")

        with gr.Row():
            file_input = gr.File(label="Upload Docling JSON", file_types=[".json"])
            sync_toggle = gr.Checkbox(label="Sync ON/OFF", value=True)
            buffer_slider = gr.Slider(label="Context buffer", minimum=0, maximum=5, value=2, step=1)
            max_nodes_slider = gr.Slider(label="Node cap", minimum=100, maximum=800, value=600, step=50)
            go_to_chunk = gr.Textbox(label="Go to chunk id", placeholder="chunk_1")

        with gr.Row():
            with gr.Column(scale=1):
                stats_json = gr.JSON(label="Doc stats")
                status = gr.Markdown()
            with gr.Column(scale=2):
                chunk_html = gr.HTML(label="Chunks", value="<div>Load a document</div>")
                visible_chunks = gr.Textbox(label="Visible chunk ids", elem_id="visible-chunks", interactive=False, visible=False)
                scroll_target = gr.Textbox(label="Scroll target", elem_id="scroll-target", visible=False)
            with gr.Column(scale=2):
                gr.HTML(f"<iframe src='{dash_url}' style='width:100%;height:80vh;border:1px solid #e2e8f0;border-radius:8px;'></iframe>")

        with gr.Row():
            with gr.Column():
                gr.Markdown("#### Debug / Logs")
                line_count = gr.Dropdown(choices=[200, 500, 1000], value=200, label="Tail lines")
                filter_text = gr.Textbox(label="Filter (regex/substring)")
                errors_only = gr.Checkbox(label="Errors only", value=False)
                refresh_logs = gr.Button("Refresh logs")
                copy_bundle = gr.Button("Copy debug bundle")
            with gr.Column():
                log_output = gr.Textbox(label="Log output", lines=14)

        poll_timer = gr.Timer(value=2.0, active=True)

        def on_visible_change(visible_json: str, sync_on: bool, buffer: int, max_nodes: int, graph: GraphData, dhash: str):
            if not sync_on or graph is None:
                return {}, "Sync disabled", visible_json
            try:
                visible = json.loads(visible_json) if visible_json else []
            except Exception:
                visible = []
            window = window_subgraph(graph, visible_chunks=visible, buffer=buffer, max_nodes=int(max_nodes))
            shared_state = {
                "doc_hash": dhash,
                "visible_chunk_ids": visible,
                "context_buffer": buffer,
                "node_cap": max_nodes,
                "updated_at": datetime.utcnow().timestamp(),
                "elements": window["elements"],
                "meta": window["meta"],
                "window": window["window"],
            }
            update_window_state(shared_state)
            logger.info(
                "window update doc_hash=%s visible=%d nodes=%d edges=%d",
                dhash,
                len(visible),
                window["meta"]["nodes"],
                window["meta"]["edges"],
            )
            return window["meta"], f"Updated window: {window['meta'].get('showing')}", json.dumps(visible)

        def manual_jump(chunk_id: str):
            return chunk_id

        def poll_selection():
            selected = consume_last_selection()
            return selected or ""

        def refresh_log_view(n_lines: int, pattern: str, only_errors: bool):
            raw = tail_log(DEFAULT_LOG_PATH, n_lines)
            filtered = filter_log(raw, pattern, errors_only=only_errors)
            return filtered or raw

        def make_debug_bundle(dhash: Optional[str], meta: Dict, cache_status: str):
            filtered = filter_log(tail_log(DEFAULT_LOG_PATH, 200), pattern="(ERROR|Exception|Traceback)", errors_only=False)
            return _format_debug_bundle(dhash, meta, filtered, cache_status)

        upload_outputs = [doc_state, stats_json, doc_hash_state, status, chunk_html, cache_state, window_meta_state, visible_chunks]
        file_input.upload(process_upload, inputs=[file_input], outputs=upload_outputs)

        visible_chunks.change(
            on_visible_change,
            inputs=[visible_chunks, sync_toggle, buffer_slider, max_nodes_slider, doc_state, doc_hash_state],
            outputs=[window_meta_state, status, visible_state],
        )

        go_to_chunk.submit(fn=manual_jump, inputs=[go_to_chunk], outputs=[scroll_target])
        poll_timer.tick(fn=poll_selection, inputs=None, outputs=scroll_target)

        refresh_logs.click(refresh_log_view, inputs=[line_count, filter_text, errors_only], outputs=[log_output])
        copy_bundle.click(make_debug_bundle, inputs=[doc_hash_state, window_meta_state, cache_state], outputs=[log_output])

    demo._dash_iframe_src = dash_url  # type: ignore[attr-defined]
    return demo


if __name__ == "__main__":
    update_window_state([], {}, (0, 0))
    blocks = create_blocks()
    blocks.launch(server_name="0.0.0.0", server_port=8055)
