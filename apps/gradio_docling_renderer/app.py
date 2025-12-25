# app.py
# Minimal Docling JSON renderer (vertical flow) with ONLY the 2 top-level pictures rendered.
# Update: add picture "properties" (classification + description) like Docling-Serve.

from __future__ import annotations

import base64
import html
import io
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
from PIL import Image


# -----------------------------
# Data
# -----------------------------
@dataclass
class RenderItem:
    idx: int
    label: str
    text: str
    depth: int
    self_ref: Optional[str] = None  # <-- added
    page_no: Optional[int] = None
    bbox: Optional[Dict[str, float]] = None  # {"l","t","r","b","coord_origin"}


# -----------------------------
# Helpers: tolerant Docling JSON extraction
# -----------------------------
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
    for k in ("text", "content", "value", "title", "caption", "orig"):
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


def _pick_prov(obj: Dict[str, Any]) -> Tuple[Optional[int], Optional[Dict[str, float]]]:
    prov = obj.get("prov")
    if isinstance(prov, list) and prov:
        p0 = prov[0]
        if isinstance(p0, dict):
            page_no = _as_int(p0.get("page_no"))
            bbox = p0.get("bbox")
            if isinstance(bbox, dict):
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
        has_text = (
            isinstance(d.get("text"), (str, dict))
            or isinstance(d.get("content"), str)
            or isinstance(d.get("orig"), str)
        )
        has_children = isinstance(d.get("children"), list) or isinstance(d.get("items"), list)
        has_prov = isinstance(d.get("prov"), list)
        return (has_label and (has_text or has_children)) or (has_prov and (has_text or has_label))

    def walk(x: Any, depth: int):
        nonlocal idx
        if len(out) >= max_items:
            return

        if isinstance(x, dict):
            if is_candidate(x):
                label = _pick_label(x)
                text = _pick_text(x)
                page_no, bbox = _pick_prov(x)
                self_ref = x.get("self_ref") if isinstance(x.get("self_ref"), str) else None

                out.append(RenderItem(idx, label, text, depth, self_ref, page_no, bbox))
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
# Image helpers (crop picture from page raster)
# -----------------------------
def _data_url_to_pil(data_url: str) -> Image.Image:
    header, b64 = data_url.split(",", 1)
    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def _pil_to_data_url_png(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def crop_picture_from_page(
    pages: Dict[str, Any],
    page_no: int,
    bbox: Dict[str, float],
    pad_px: int = 2,
) -> Optional[str]:
    """
    Crop from pages[str(page_no)].image.uri using bbox in PDF coords (BOTTOMLEFT).
    """
    p = pages.get(str(page_no))
    if not isinstance(p, dict):
        return None

    img_meta = p.get("image") or {}
    page_uri = img_meta.get("uri")
    if not (isinstance(page_uri, str) and page_uri.startswith("data:")):
        return None

    pdf_size = p.get("size") or {}
    pdf_w = float(pdf_size.get("width") or 0)
    pdf_h = float(pdf_size.get("height") or 0)

    px_size = img_meta.get("size") or {}
    px_w = float(px_size.get("width") or 0)
    px_h = float(px_size.get("height") or 0)

    if pdf_w <= 0 or pdf_h <= 0 or px_w <= 0 or px_h <= 0:
        return None

    # bbox in PDF coords
    l = float(bbox.get("l", 0))
    t = float(bbox.get("t", 0))
    r = float(bbox.get("r", 0))
    b = float(bbox.get("b", 0))

    # Convert to pixel coords (TOPLEFT origin)
    x0 = int(round((l / pdf_w) * px_w))
    x1 = int(round((r / pdf_w) * px_w))
    y0 = int(round(((pdf_h - t) / pdf_h) * px_h))
    y1 = int(round(((pdf_h - b) / pdf_h) * px_h))

    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))

    x0 = max(0, x0 - pad_px)
    y0 = max(0, y0 - pad_px)
    x1 = min(int(px_w), x1 + pad_px)
    y1 = min(int(px_h), y1 + pad_px)

    if x1 - x0 < 2 or y1 - y0 < 2:
        return None

    im = _data_url_to_pil(page_uri)
    cropped = im.crop((x0, y0, x1, y1))
    return _pil_to_data_url_png(cropped)


# -----------------------------
# NEW: picture properties (classification + description)
# -----------------------------
def _format_conf(x: Any) -> str:
    try:
        v = float(x)
        # match Docling-Serve style: 1 for near-1, else 2 decimals, tiny as <0.01
        if v >= 0.995:
            return "1"
        if v < 0.01:
            return "&lt; 0.01"
        return f"{v:.2f}"
    except Exception:
        return ""


def picture_properties_html(doc_json: Dict[str, Any], self_ref: str) -> str:
    """
    self_ref looks like "#/pictures/0".
    We'll read doc_json["pictures"][idx]["annotations"].
    """
    try:
        idx = int(self_ref.split("/")[-1])
    except Exception:
        return ""

    pics = doc_json.get("pictures")
    if not isinstance(pics, list) or not (0 <= idx < len(pics)):
        return ""

    ann = pics[idx].get("annotations") or []
    if not isinstance(ann, list):
        return ""

    # Extract classification + description
    pred: List[Dict[str, Any]] = []
    desc_text: Optional[str] = None

    for a in ann:
        if not isinstance(a, dict):
            continue
        if a.get("kind") == "classification":
            pc = a.get("predicted_classes")
            if isinstance(pc, list):
                pred = [x for x in pc if isinstance(x, dict)]
        elif a.get("kind") == "description":
            t = a.get("text")
            if isinstance(t, str) and t.strip():
                desc_text = t.strip()

    # Build HTML (mini panel like Docling tooltip)
    rows = []
    if pred:
        top = pred[:3]
        more = max(0, len(pred) - len(top))

        rows.append("<div class='dv-prop-title'>Class</div>")
        rows.append("<div class='dv-prop-title'>Confidence</div>")

        for c in top:
            cn = html.escape(str(c.get("class_name", "")))
            cf = _format_conf(c.get("confidence"))
            rows.append(f"<div class='dv-prop-cell'>{cn}</div>")
            rows.append(f"<div class='dv-prop-cell'>{cf}</div>")

        if more > 0:
            rows.append(f"<div class='dv-prop-muted'>{more} more</div>")
            rows.append("<div class='dv-prop-muted'>&nbsp;</div>")

    desc_block = ""
    if desc_text:
        desc_block = (
            "<div class='dv-desc'>"
            "<div class='dv-desc-title'>Description</div>"
            f"<div class='dv-desc-text'>{html.escape(desc_text)}</div>"
            "</div>"
        )

    if not rows and not desc_block:
        return ""

    grid = ""
    if rows:
        grid = "<div class='dv-prop-grid'>" + "".join(rows) + "</div>"

    return "<div class='dv-props'>" + grid + desc_block + "</div>"


# -----------------------------
# Render (same as your original, plus ONLY 2 pictures)
# -----------------------------
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
      .dv-imgwrap {
        background:#0b0b0b;
        color:#f4f4f4;
        border:1px solid #2a2a2a;
        border-radius:10px;
        padding:10px 12px;
        margin:8px 0;
      }
      .dv-imgmeta {
        opacity:0.8;
        font-size:12px;
        margin-bottom:8px;
      }
      .dv-imgwrap img {
        max-width:100%;
        height:auto;
        border-radius:8px;
        display:block;
      }

      /* NEW: picture properties panel */
      .dv-props {
        margin: 10px 0 12px 0;
        padding: 10px 12px;
        border-radius: 12px;
        background: #111;
        border: 1px solid #2a2a2a;
      }
      .dv-prop-grid {
        display: grid;
        grid-template-columns: 1fr 120px;
        gap: 6px 14px;
        align-items: baseline;
      }
      .dv-prop-title {
        font-weight: 700;
        opacity: 0.95;
      }
      .dv-prop-cell {
        opacity: 0.92;
      }
      .dv-prop-muted {
        opacity: 0.65;
        font-size: 12px;
      }
      .dv-desc {
        margin-top: 10px;
        padding-top: 10px;
        border-top: 1px solid #2a2a2a;
      }
      .dv-desc-title {
        font-weight: 700;
        margin-bottom: 6px;
      }
      .dv-desc-text {
        opacity: 0.9;
      }
    </style>
    """

    parts = [css, "<div>"]
    section_open = False

    # Keep your original filtering for text items,
    # but ALSO allow the two top-level pictures through.
    visible: List[RenderItem] = []
    for it in items[:limit]:
        is_top_picture = (it.label == "picture" and isinstance(it.self_ref, str) and it.self_ref.startswith("#/pictures/"))
        if is_top_picture:
            visible.append(it)
        elif it.text or show_empty_text:
            visible.append(it)

    for it in visible:
        if it.label == "page_footer":
            if section_open:
                parts.append("</div>")
                section_open = False
            continue

        if it.label == "section_header":
            if section_open:
                parts.append("</div>")
            parts.append("<div class='dv-section'>")
            parts.append(f"<b>{html.escape(it.text or '(section)')}</b>")
            section_open = True
            continue

        # ONLY render the 2 pictures (#/pictures/0 and #/pictures/1)
        if it.label == "picture" and isinstance(it.self_ref, str) and it.self_ref.startswith("#/pictures/"):
            data_url = None
            if it.page_no is not None and isinstance(it.bbox, dict):
                data_url = crop_picture_from_page(pages, it.page_no, it.bbox)

            parts.append("<div class='dv-imgwrap'>")
            parts.append(f"<div class='dv-imgmeta'>picture {html.escape(it.self_ref)} | page {it.page_no}</div>")

            # NEW: properties panel (classification + description)
            props = picture_properties_html(doc_json, it.self_ref)
            if props:
                parts.append(props)

            if data_url:
                parts.append(f"<img src='{data_url}' />")
            else:
                parts.append("<div class='dv-box'>(picture: could not crop from page image)</div>")

            parts.append("</div>")
            continue

        # default: your original text box behavior
        parts.append(f"<div class='dv-box'>{html.escape(it.text)}</div>")

    if section_open:
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


# -----------------------------
# Gradio callbacks
# -----------------------------
def load_and_render(uploaded_file, show_empty_text: bool, max_items: int, render_limit: int):
    if uploaded_file is None:
        return "<div class='dv-box'>Upload a Docling JSON file first.</div>", {"items": 0}

    with open(uploaded_file.name, "r", encoding="utf-8") as f:
        data = json.load(f)

    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, dict):
        pages = {}

    items = extract_render_items(data, max_items)
    html_out = render_boxes(data, items, pages, show_empty_text, render_limit)
    return html_out, {"items": len(items), "pages": len(pages)}


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
