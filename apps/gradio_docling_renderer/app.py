#!/usr/bin/env python3
"""
Gradio Docling JSON Renderer

Items mode:
- Section boxes (orange)
- Pictures cropped from page images (if available)
- Picture properties (classification + description), if present in Docling JSON

Chunks mode:
- Section-based chunk builder (label == section_header)
- Chunk cards (orange)
- Multi-select chunk selector

Annif Projects (STATIC):
- No API calls
- Dropdown populated from hard-coded list
- Labels normalized to ASCII to avoid � replacement chars
"""

from __future__ import annotations

import base64
import html
import io
import json
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import gradio as gr

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore


# ============================================================
# STATIC Annif projects (NO fetching)
# ============================================================
ANNIF_PROJECTS: Dict[str, List[Dict[str, Any]]] = {
    "projects": [
        {
            "backend": {"backend_id": "nn_ensemble"},
            "is_trained": True,
            "language": "en",
            "modification_time": "2022-10-29T10:36:34.615060+00:00",
            "name": "NN Ensemble",
            "project_id": "nn-ensemble-en",
        },
        {
            "backend": {"backend_id": "nn_ensemble"},
            "is_trained": True,
            "language": "en",
            "modification_time": "2022-11-12T09:58:38.767263+00:00",
            "name": "NN Finite Automata (FSA)",
            "project_id": "nn-bv-stw-ensemble-en",
        },
        {
            "backend": {"backend_id": "pav"},
            "is_trained": True,
            "language": "en",
            "modification_time": "2022-10-30T07:23:29.156954+00:00",
            "name": "NN PAV Ensemble",
            "project_id": "pav-en",
        },
        {
            "backend": {"backend_id": "stwfsa"},
            "is_trained": True,
            "language": "en",
            "modification_time": "2022-10-29T10:30:33.413864+00:00",
            "name": "Finite Automata (FSA",
            "project_id": "stwfsa-bv-en",
        },
        {
            "backend": {"backend_id": "tfidf"},
            "is_trained": True,
            "language": "en",
            "modification_time": "2022-12-27T18:34:40.749972+00:00",
            "name": "TFIDF",
            "project_id": "tfidf-en",
        },
        {
            "backend": {"backend_id": "mllm"},
            "is_trained": True,
            "language": "en",
            "modification_time": "2022-11-12T09:15:38.701268+00:00",
            "name": "MLLM",
            "project_id": "mllm-en",
        },
        {
            "backend": {"backend_id": "omikuji"},
            "is_trained": True,
            "language": "en",
            "modification_time": "2022-11-12T09:20:10.935459+00:00",
            "name": "Omikuji",
            "project_id": "omikuji-parabel-en",
        },
    ]
}

ANNIF_SEP = " - "  # ASCII only


def ascii_clean(s: str) -> str:
    """Normalize to ASCII to avoid Unicode replacement chars (�) in dropdown labels."""
    return (
        unicodedata.normalize("NFKD", s)
        .encode("ascii", "ignore")
        .decode("ascii")
        .strip()
    )


def annif_list_projects_static() -> List[Dict[str, Any]]:
    projs = ANNIF_PROJECTS.get("projects", [])
    return projs if isinstance(projs, list) else []


def annif_project_choices() -> List[str]:
    out: List[str] = []
    for p in annif_list_projects_static():
        pid = p.get("project_id", "")
        name = p.get("name", "")
        if not isinstance(pid, str) or not pid.strip():
            continue
        pid_clean = ascii_clean(pid)
        name_clean = ascii_clean(name) if isinstance(name, str) else ""
        out.append(f"{pid_clean}{ANNIF_SEP}{name_clean}" if name_clean else pid_clean)
    return out


def annif_project_id_from_choice(choice: str) -> Optional[str]:
    if not isinstance(choice, str) or not choice.strip():
        return None
    return choice.split(ANNIF_SEP, 1)[0].strip()


# ============================================================
# Data models
# ============================================================
@dataclass
class RenderItem:
    idx: int
    self_ref: str
    label: str
    text: str
    page_no: Optional[int] = None
    bbox: Optional[Dict[str, Any]] = None


@dataclass
class Chunk:
    chunk_id: str
    title: str
    text: str
    item_idxs: List[int]
    page_min: Optional[int] = None
    page_max: Optional[int] = None


# ============================================================
# Docling helpers
# ============================================================
def _ref_index(self_ref: str, prefix: str) -> Optional[int]:
    if not isinstance(self_ref, str) or not self_ref.startswith(prefix):
        return None
    try:
        return int(self_ref.split("/")[-1])
    except Exception:
        return None


def _first_prov(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prov = item.get("prov")
    if isinstance(prov, list) and prov:
        p0 = prov[0]
        return p0 if isinstance(p0, dict) else None
    return None


def _item_page_bbox(item: Dict[str, Any]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    p0 = _first_prov(item)
    if not p0:
        return None, None
    page_no = p0.get("page_no")
    bbox = p0.get("bbox")
    if not isinstance(page_no, int):
        page_no = None
    if not isinstance(bbox, dict):
        bbox = None
    return page_no, bbox


# ============================================================
# Picture cropping from embedded page image (if present)
# ============================================================
def crop_picture_from_page(pages: Dict[str, Any], page_no: int, bbox: Dict[str, Any]) -> Optional[str]:
    if Image is None:
        return None

    page = pages.get(str(page_no)) if isinstance(pages, dict) else None
    if page is None:
        page = pages.get(page_no) if isinstance(pages, dict) else None
    if not isinstance(page, dict):
        return None

    img_b64 = page.get("image")
    if not isinstance(img_b64, str) or not img_b64:
        return None

    try:
        raw = base64.b64decode(img_b64)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None

    try:
        l = float(bbox.get("l"))
        t = float(bbox.get("t"))
        r = float(bbox.get("r"))
        b = float(bbox.get("b"))
    except Exception:
        return None

    W, H = im.size

    # Convert BOTTOMLEFT -> TOPLEFT-ish for simple crops
    y1 = max(0, min(H, int(H - t)))
    y2 = max(0, min(H, int(H - b)))
    x1 = max(0, min(W, int(l)))
    x2 = max(0, min(W, int(r)))

    left = min(x1, x2)
    right = max(x1, x2)
    top = min(y1, y2)
    bottom = max(y1, y2)

    if right - left < 2 or bottom - top < 2:
        return None

    crop = im.crop((left, top, right, bottom))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ============================================================
# Picture properties rendering (classification + description)
# ============================================================
def _format_conf(v: Any) -> str:
    try:
        f = float(v)
        return f"{f:.3f}"
    except Exception:
        return ""


def picture_properties_html(doc_json: Dict[str, Any], pic_ref: str) -> str:
    """
    Render picture classification + description if present.
    Accepts several common keys (defensive).
    """
    pics = doc_json.get("pictures")
    if not isinstance(pics, list):
        return ""

    idx = _ref_index(pic_ref, "#/pictures/")
    if idx is None or idx < 0 or idx >= len(pics):
        return ""

    p = pics[idx]
    if not isinstance(p, dict):
        return ""

    meta = p.get("meta")
    if not isinstance(meta, dict):
        meta = {}

    rows: List[str] = []

    classes = (
        meta.get("classes")
        or meta.get("classification")
        or meta.get("predictions")
        or meta.get("classifications")
    )
    desc_text = meta.get("description") or meta.get("caption") or meta.get("desc")

    if isinstance(classes, dict):
        classes = classes.get("top_k") or classes.get("classes") or classes.get("predictions") or []

    if isinstance(classes, list) and classes:
        top = classes[:5]
        more = max(0, len(classes) - len(top))

        rows.append("<div class='dv-prop-head'>Class</div>")
        rows.append("<div class='dv-prop-head'>Confidence</div>")

        for c in top:
            if not isinstance(c, dict):
                continue
            cn = html.escape(str(c.get("class_name", c.get("label", "")) or ""))
            cf = _format_conf(c.get("confidence", c.get("score")))
            rows.append(f"<div class='dv-prop-cell'>{cn}</div>")
            rows.append(f"<div class='dv-prop-cell'>{cf}</div>")

        if more > 0:
            rows.append(f"<div class='dv-prop-muted'>{more} more</div>")
            rows.append("<div class='dv-prop-muted'>&nbsp;</div>")

    desc_block = ""
    if isinstance(desc_text, str) and desc_text.strip():
        desc_block = (
            "<div class='dv-desc'>"
            "<div class='dv-desc-title'>Description</div>"
            f"<div class='dv-desc-text'>{html.escape(desc_text.strip())}</div>"
            "</div>"
        )

    if not rows and not desc_block:
        return ""

    grid = "<div class='dv-prop-grid'>" + "".join(rows) + "</div>" if rows else ""
    return "<div class='dv-props'>" + grid + desc_block + "</div>"


# ============================================================
# Extraction (prefer doc_json["texts"])
# ============================================================
def extract_render_items(doc_json: Any, max_items: int) -> List[RenderItem]:
    if not isinstance(doc_json, dict):
        return []

    items: List[RenderItem] = []

    texts = doc_json.get("texts")
    if isinstance(texts, list) and texts:
        for i, t in enumerate(texts[: max_items or len(texts)]):
            if not isinstance(t, dict):
                continue
            self_ref = t.get("self_ref") or f"#/texts/{i}"
            label = t.get("label") or ""
            text = t.get("text") or t.get("orig") or ""
            if not isinstance(self_ref, str):
                self_ref = f"#/texts/{i}"
            if not isinstance(label, str):
                label = ""
            if not isinstance(text, str):
                text = ""
            page_no, bbox = _item_page_bbox(t)
            items.append(
                RenderItem(
                    idx=i,
                    self_ref=self_ref,
                    label=label,
                    text=text,
                    page_no=page_no,
                    bbox=bbox,
                )
            )
        return items

    # Fallback scan for other exports
    for key in ("body", "furniture", "pictures", "tables", "key_value_items", "form_items"):
        arr = doc_json.get(key)
        if not isinstance(arr, list):
            continue
        for t in arr:
            if len(items) >= max_items:
                break
            if not isinstance(t, dict):
                continue
            self_ref = t.get("self_ref") or ""
            label = t.get("label") or key
            text = t.get("text") or t.get("orig") or ""
            if not isinstance(self_ref, str):
                self_ref = ""
            if not isinstance(label, str):
                label = ""
            if not isinstance(text, str):
                text = ""
            page_no, bbox = _item_page_bbox(t)
            items.append(
                RenderItem(
                    idx=len(items),
                    self_ref=self_ref,
                    label=label,
                    text=text,
                    page_no=page_no,
                    bbox=bbox,
                )
            )

    return items


# ============================================================
# Chunking (section_header based)
# ============================================================
def build_section_chunks(items: List[RenderItem]) -> List[Chunk]:
    if not items:
        return []

    chunks: List[Chunk] = []
    current: Optional[Chunk] = None

    def _finalize(ch: Chunk) -> None:
        ch.text = (ch.text or "").strip()
        chunks.append(ch)

    for it in items:
        if it.label == "section_header":
            if current is not None:
                _finalize(current)

            title = (it.text or "").strip()
            current = Chunk(
                chunk_id=f"chunk_{len(chunks)+1:04d}",
                title=title if title else "(untitled)",
                text="",
                item_idxs=[],
                page_min=it.page_no,
                page_max=it.page_no,
            )
            continue

        if current is None:
            current = Chunk(chunk_id="chunk_0000", title="(preamble)", text="", item_idxs=[])

        current.item_idxs.append(it.idx)
        if it.text:
            current.text += it.text.strip() + "\n"

        if isinstance(it.page_no, int):
            if current.page_min is None or it.page_no < current.page_min:
                current.page_min = it.page_no
            if current.page_max is None or it.page_no > current.page_max:
                current.page_max = it.page_no

    if current is not None:
        _finalize(current)

    return chunks


# ============================================================
# Renderers
# ============================================================
def render_boxes(doc_json: Dict[str, Any], items: List[RenderItem], pages: Dict[str, Any], show_empty_text: bool, limit: int) -> str:
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
      .dv-section-title { font-weight:700; margin-bottom:8px; }
      .dv-imgwrap {
        background:#0b0b0b;
        color:#f4f4f4;
        border:1px solid #2a2a2a;
        border-radius:10px;
        padding:10px 12px;
        margin:8px 0;
      }
      .dv-imgmeta { opacity:0.8; font-size:12px; margin-bottom:8px; }
      .dv-imgwrap img { max-width:100%; height:auto; border-radius:8px; display:block; }
      .dv-props {
        border:1px solid #2a2a2a;
        border-radius:10px;
        padding:10px 12px;
        margin:10px 0 8px 0;
        background:rgba(255,255,255,0.03);
      }
      .dv-prop-grid {
        display:grid;
        grid-template-columns: 1fr 120px;
        gap:6px 12px;
        align-items:center;
      }
      .dv-prop-head { font-weight:700; opacity:0.9; }
      .dv-prop-cell { opacity:0.95; }
      .dv-prop-muted { opacity:0.7; font-size:12px; }
      .dv-desc { margin-top:10px; }
      .dv-desc-title { font-weight:700; opacity:0.9; margin-bottom:4px; }
      .dv-desc-text { opacity:0.95; white-space:pre-wrap; }
    </style>
    """

    parts = [css, "<div>"]
    section_open = False

    lim = max(0, int(limit or 0))
    for it in items[:lim]:
        if it.label == "section_header":
            if section_open:
                parts.append("</div>")
            section_open = True
            parts.append("<div class='dv-section'>")
            parts.append(f"<div class='dv-section-title'>{html.escape(it.text.strip() or '(untitled section)')}</div>")
            continue

        if not show_empty_text and (not it.text or not it.text.strip()):
            continue

        if it.label == "picture" and isinstance(it.self_ref, str) and it.self_ref.startswith("#/pictures/"):
            data_url = None
            if it.page_no is not None and isinstance(it.bbox, dict):
                data_url = crop_picture_from_page(pages, it.page_no, it.bbox)

            parts.append("<div class='dv-imgwrap'>")
            parts.append(f"<div class='dv-imgmeta'>picture {html.escape(it.self_ref)} | page {it.page_no}</div>")

            props = picture_properties_html(doc_json, it.self_ref)
            if props:
                parts.append(props)

            if data_url:
                parts.append(f"<img src='{data_url}' />")
            else:
                parts.append("<div class='dv-box'>(picture: could not crop from page image)</div>")

            parts.append("</div>")
            continue

        parts.append(f"<div class='dv-box'>{html.escape(it.text)}</div>")

    if section_open:
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


def render_chunk_cards(chunks: List[Chunk]) -> str:
    css = """
    <style>
      .dv-chunk {
        background:#0b0b0b;
        color:#f4f4f4;
        border:2px solid #ff8c00;
        border-radius:14px;
        padding:12px 14px;
        margin:12px 0;
      }
      .dv-chunk-head {
        display:flex;
        align-items:baseline;
        justify-content:space-between;
        gap:12px;
        margin-bottom:10px;
      }
      .dv-chunk-title { font-weight:800; font-size:16px; }
      .dv-chunk-meta { opacity:0.75; font-size:12px; white-space:nowrap; }
      .dv-chunk-body { white-space:pre-wrap; opacity:0.95; }
      .dv-box {
        background:#0b0b0b;
        color:#f4f4f4;
        border:1px solid #2a2a2a;
        border-radius:10px;
        padding:10px 12px;
        margin:8px 0;
        white-space:pre-wrap;
      }
    </style>
    """
    parts = [css, "<div>"]
    if not chunks:
        parts.append("<div class='dv-box'>No chunks found (no section_header items).</div>")
        parts.append("</div>")
        return "\n".join(parts)

    for ch in chunks:
        if ch.page_min is None or ch.page_max is None:
            page_txt = "pages: ?"
        elif ch.page_min == ch.page_max:
            page_txt = f"page: {ch.page_min}"
        else:
            page_txt = f"pages: {ch.page_min}-{ch.page_max}"

        # SINGLE f-string (prevents the SyntaxError you hit earlier)
        meta = f"{html.escape(ch.chunk_id)} {page_txt} items: {len(ch.item_idxs)}"

        parts.append("<div class='dv-chunk'>")
        parts.append("<div class='dv-chunk-head'>")
        parts.append(f"<div class='dv-chunk-title'>{html.escape(ch.title or '(untitled)')}</div>")
        parts.append(f"<div class='dv-chunk-meta'>{meta}</div>")
        parts.append("</div>")
        parts.append(f"<div class='dv-chunk-body'>{html.escape(ch.text or '')}</div>")
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


# ============================================================
# Chunk selector helpers (MULTI)  ASCII separator
# ============================================================
CHUNK_SEP = " - "  # ASCII only


def chunk_choices(chunks: List[Chunk]) -> List[str]:
    return ["(All chunks)"] + [f"{c.chunk_id}{CHUNK_SEP}{ascii_clean(c.title)}" for c in chunks]


def normalize_chunk_choice(choice: Union[str, List[str], None], choices: List[str]) -> List[str]:
    if choice is None:
        return ["(All chunks)"]
    if isinstance(choice, str):
        return [choice] if choice in choices else ["(All chunks)"]
    if isinstance(choice, list):
        cleaned = [c for c in choice if isinstance(c, str) and c in choices]
        return cleaned or ["(All chunks)"]
    return ["(All chunks)"]


def selected_chunk_ids(selected_labels: List[str]) -> Optional[set]:
    if "(All chunks)" in selected_labels:
        return None
    ids = set()
    for lab in selected_labels:
        chunk_id = lab.split(CHUNK_SEP, 1)[0].strip()
        if chunk_id:
            ids.add(chunk_id)
    return ids or None


# ============================================================
# Gradio callback
# ============================================================
def load_and_render(
    uploaded_file,
    mode: str,
    annif_project_choice: str,
    chunk_choice: Union[str, List[str], None],
    show_empty_text: bool,
    max_items: int,
    render_limit: int,
):
    # Keep dropdown stable (static choices)
    proj_choices = annif_project_choices()
    if proj_choices:
        if annif_project_choice not in proj_choices:
            annif_project_choice = proj_choices[0]
        annif_update = gr.update(choices=proj_choices, value=annif_project_choice)
    else:
        annif_update = gr.update(choices=[], value=None)

    if uploaded_file is None:
        dd_update = gr.update(choices=["(All chunks)"], value=["(All chunks)"])
        return (
            "<div class='dv-box'>Upload a Docling JSON file first.</div>",
            {"items": 0},
            annif_update,
            dd_update,
        )

    with open(uploaded_file.name, "r", encoding="utf-8") as f:
        data = json.load(f)

    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, dict):
        pages = {}

    items = extract_render_items(data, int(max_items))
    chunks = build_section_chunks(items)

    # Update chunk dropdown
    choices = chunk_choices(chunks)
    selected = normalize_chunk_choice(chunk_choice, choices)
    if "(All chunks)" in selected and len(selected) > 1:
        selected = [s for s in selected if s != "(All chunks)"]
    dd_update = gr.update(choices=choices, value=selected)

    annif_pid = annif_project_id_from_choice(annif_project_choice)

    if mode == "Chunks":
        want = selected_chunk_ids(selected)  # None => all
        visible = [c for c in chunks if (want is None or c.chunk_id in want)]
        html_out = render_chunk_cards(visible)
        return (
            html_out,
            {
                "items": len(items),
                "pages": len(pages),
                "chunks": len(chunks),
                "mode": "Chunks",
                "annif_project": annif_pid,
            },
            annif_update,
            dd_update,
        )

    html_out = render_boxes(data, items, pages, show_empty_text, int(render_limit))
    return (
        html_out,
        {
            "items": len(items),
            "pages": len(pages),
            "chunks": len(chunks),
            "mode": "Items",
            "annif_project": annif_pid,
        },
        annif_update,
        dd_update,
    )


# ============================================================
# UI
# ============================================================
with gr.Blocks(title="Docling JSON Renderer") as demo:
    file_in = gr.File(file_types=[".json"], label="Docling JSON")

    proj_choices = annif_project_choices()
    default_proj = proj_choices[0] if proj_choices else None

    with gr.Row():
        mode = gr.Radio(["Items", "Chunks"], value="Items", label="Render mode")
        annif_project = gr.Dropdown(
            choices=proj_choices,
            value=default_proj,
            label="Annif project (static)",
            info="Static project list (no API calls)  ASCII normalized",
        )
        chunk_select = gr.Dropdown(
            choices=["(All chunks)"],
            value=["(All chunks)"],
            multiselect=True,
            label="Chunk selector (multi)",
        )

    show_empty = gr.Checkbox(False, label="Show empty text")
    max_items = gr.Slider(100, 10000, value=2000, label="Max items to scan")
    render_limit = gr.Slider(50, 5000, value=600, label="Render limit (Items mode)")

    btn = gr.Button("Render")
    html_view = gr.HTML()
    stats = gr.JSON()

    btn.click(
        load_and_render,
        [file_in, mode, annif_project, chunk_select, show_empty, max_items, render_limit],
        [html_view, stats, annif_project, chunk_select],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
 