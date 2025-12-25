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

Phase 4
- Annif suggest client wrapper (formData)
- limit/threshold controls
- suggest ALL chunks (or selected chunks)

Phase 5  Human Curation (THIS UPDATE)
- curated subject state per chunk
- accept suggestions
- manual subject entry
- remove subject

Fix:
- Robust JSON loading for files with trailing junk (JSONDecodeError: Extra data)
- Annif suggest uses formData + correct /v1 base URL
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
import requests

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore


# ============================================================
# Robust JSON loader (FIX: handles JSONDecodeError: Extra data)
# ============================================================
def load_json_robust(path: str) -> Any:
    """
    Try strict JSON first.
    If the file contains extra trailing data after a valid JSON object,
    parse only the first JSON object and ignore the rest.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        dec = json.JSONDecoder()
        obj, _end = dec.raw_decode(raw.lstrip())
        return obj


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
# Phase 4  Annif suggest client wrapper
# ============================================================
ANNIF_BASE_URL = "https://text-analytics.buildvoc.co.uk/v1"
ANNIF_TIMEOUT = 10.0


def annif_suggest(
    project_id: str,
    text: str,
    limit: int = 10,
    threshold: float = 0.0,
) -> List[Dict[str, Any]]:
    """
    Safe wrapper for POST /projects/{project_id}/suggest.

    Swagger says inputs are formData:
      text (required), limit (int), threshold (double)

    Returns:
      [{"label": str, "score": float, "notation": str|None, "uri": str|None}, ...]

    Never raises; returns [] on 404 / 503 / network / JSON issues.
    """
    if not project_id or not text or not text.strip():
        return []

    url = f"{ANNIF_BASE_URL}/projects/{project_id}/suggest"

    # formData (application/x-www-form-urlencoded), not JSON
    form = {"text": text, "limit": int(limit), "threshold": float(threshold)}

    try:
        r = requests.post(
            url,
            data=form,
            headers={"Accept": "application/json"},
            timeout=ANNIF_TIMEOUT,
        )
        if r.status_code in (404, 503):
            return []
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []

    results = data.get("results")
    if not isinstance(results, list):
        return []

    out: List[Dict[str, Any]] = []
    for it in results:
        if not isinstance(it, dict):
            continue
        label = it.get("label")
        score = it.get("score")
        if isinstance(label, str) and isinstance(score, (int, float)):
            out.append(
                {
                    "label": label,
                    "score": float(score),
                    "notation": it.get("notation"),
                    "uri": it.get("uri"),
                }
            )
    return out


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


def selected_chunk_id_list(selected_labels: List[str]) -> Optional[List[str]]:
    """
    Return list of selected chunk ids.
    None means '(All chunks)' => all chunks.
    """
    if "(All chunks)" in selected_labels:
        return None
    out: List[str] = []
    for lab in selected_labels:
        if lab == "(All chunks)":
            continue
        cid = lab.split(CHUNK_SEP, 1)[0].strip()
        if cid:
            out.append(cid)
    return out or None


def first_selected_chunk_id(selected_labels: List[str]) -> Optional[str]:
    """Pick the first non-(All chunks) chunk id from the multi-select."""
    for lab in selected_labels:
        if lab == "(All chunks)":
            continue
        cid = lab.split(CHUNK_SEP, 1)[0].strip()
        if cid:
            return cid
    return None


# ============================================================
# Phase 4: suggest multiple chunks
# ============================================================
def suggest_for_chunks(
    annif_project_id: str,
    chunks: List[Chunk],
    selected_ids: Optional[List[str]],
    limit: int,
    threshold: float,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    want = set(selected_ids) if selected_ids is not None else None

    for ch in chunks:
        if want is not None and ch.chunk_id not in want:
            continue
        out[ch.chunk_id] = {
            "title": ch.title,
            "suggestions": annif_suggest(
                project_id=annif_project_id,
                text=ch.text,
                limit=int(limit),
                threshold=float(threshold),
            ),
        }

    return out


# ============================================================
# Phase 5: curated state helpers
# ============================================================
CuratedState = Dict[str, List[Dict[str, Any]]]
# Store entries like:
# {"label": "...", "uri": "...", "notation": "...", "source": "annif"|"manual"}


def _ensure_state(s: Any) -> CuratedState:
    return s if isinstance(s, dict) else {}


def _norm_label(s: Any) -> str:
    return str(s).strip() if isinstance(s, (str, int, float)) else ""


def _dedupe_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        key = (_norm_label(e.get("label")).lower(), _norm_label(e.get("uri")))
        if not key[0]:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def curated_for_chunk(state: CuratedState, chunk_id: str) -> List[Dict[str, Any]]:
    state = _ensure_state(state)
    entries = state.get(chunk_id, [])
    return entries if isinstance(entries, list) else []


def curated_choices_for_chunk(state: CuratedState, chunk_id: str) -> List[str]:
    entries = curated_for_chunk(state, chunk_id)
    out = []
    for e in entries:
        lab = _norm_label(e.get("label"))
        uri = _norm_label(e.get("uri"))
        out.append(f"{lab} | {uri}" if uri else lab)
    return out


def accept_suggestion_to_state(
    state: CuratedState,
    chunk_id: Optional[str],
    suggestion_label: Optional[str],
    suggestions_view: Dict[str, Any],
) -> CuratedState:
    state = _ensure_state(state)
    if not chunk_id or not suggestion_label:
        return state
    if not isinstance(suggestions_view, dict):
        return state

    chunk_block = suggestions_view.get(chunk_id)
    if not isinstance(chunk_block, dict):
        return state
    suggs = chunk_block.get("suggestions")
    if not isinstance(suggs, list):
        return state

    chosen = None
    for s in suggs:
        if not isinstance(s, dict):
            continue
        if _norm_label(s.get("label")) == _norm_label(suggestion_label):
            chosen = s
            break

    if chosen is None:
        return state

    entry = {
        "label": _norm_label(chosen.get("label")),
        "uri": _norm_label(chosen.get("uri")) or None,
        "notation": chosen.get("notation"),
        "source": "annif",
    }

    current = curated_for_chunk(state, chunk_id)
    current.append(entry)
    state[chunk_id] = _dedupe_entries(current)
    return state


def add_manual_to_state(state: CuratedState, chunk_id: Optional[str], manual_label: Optional[str]) -> CuratedState:
    state = _ensure_state(state)
    if not chunk_id:
        return state
    lab = _norm_label(manual_label)
    if not lab:
        return state

    entry = {"label": lab, "uri": None, "notation": None, "source": "manual"}
    current = curated_for_chunk(state, chunk_id)
    current.append(entry)
    state[chunk_id] = _dedupe_entries(current)
    return state


def remove_curated_from_state(state: CuratedState, chunk_id: Optional[str], selected_item: Optional[str]) -> CuratedState:
    state = _ensure_state(state)
    if not chunk_id or not selected_item:
        return state

    entries = curated_for_chunk(state, chunk_id)
    keep: List[Dict[str, Any]] = []
    for e in entries:
        lab = _norm_label(e.get("label"))
        uri = _norm_label(e.get("uri"))
        disp = f"{lab} | {uri}" if uri else lab
        if disp != selected_item:
            keep.append(e)

    state[chunk_id] = keep
    return state


# ============================================================
# Renderers
# ============================================================
def render_boxes(
    doc_json: Dict[str, Any],
    items: List[RenderItem],
    pages: Dict[str, Any],
    show_empty_text: bool,
    limit: int,
) -> str:
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
            parts.append(
                f"<div class='dv-section-title'>{html.escape(it.text.strip() or '(untitled section)')}</div>"
            )
            continue

        if not show_empty_text and (not it.text or not it.text.strip()):
            continue

        if it.label == "picture" and isinstance(it.self_ref, str) and it.self_ref.startswith("#/pictures/"):
            data_url = None
            if it.page_no is not None and isinstance(it.bbox, dict):
                data_url = crop_picture_from_page(pages, it.page_no, it.bbox)

            parts.append("<div class='dv-imgwrap'>")
            parts.append(
                f"<div class='dv-imgmeta'>picture {html.escape(it.self_ref)} | page {it.page_no}</div>"
            )

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


def _curated_badges_html(curated_entries: List[Dict[str, Any]]) -> str:
    if not curated_entries:
        return "<div class='dv-curated-empty'>(no curated subjects)</div>"

    bits = []
    for e in curated_entries[:50]:
        if not isinstance(e, dict):
            continue
        lab = html.escape(_norm_label(e.get("label")))
        uri = _norm_label(e.get("uri"))
        src = html.escape(_norm_label(e.get("source") or ""))
        if uri:
            uri_esc = html.escape(uri)
            bits.append(
                f"<a class='dv-pill' href='{uri_esc}' target='_blank' rel='noopener noreferrer'>"
                f"{lab}<span class='dv-pill-src'>{src}</span></a>"
            )
        else:
            bits.append(f"<span class='dv-pill'>{lab}<span class='dv-pill-src'>{src}</span></span>")

    return "<div class='dv-pillwrap'>" + "".join(bits) + "</div>"


def render_chunk_cards(chunks: List[Chunk], curated_state: CuratedState) -> str:
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
        margin-bottom:8px;
      }
      .dv-chunk-title { font-weight:800; font-size:16px; }
      .dv-chunk-meta { opacity:0.75; font-size:12px; white-space:nowrap; }
      .dv-chunk-body { white-space:pre-wrap; opacity:0.95; margin-top:10px; }
      .dv-box {
        background:#0b0b0b;
        color:#f4f4f4;
        border:1px solid #2a2a2a;
        border-radius:10px;
        padding:10px 12px;
        margin:8px 0;
        white-space:pre-wrap;
      }
      .dv-curated {
        border:1px solid #2a2a2a;
        border-radius:12px;
        padding:10px 12px;
        background:rgba(255,255,255,0.03);
      }
      .dv-curated-title { font-weight:700; opacity:0.9; margin-bottom:6px; }
      .dv-curated-empty { opacity:0.65; font-size:12px; }
      .dv-pillwrap { display:flex; flex-wrap:wrap; gap:6px; }
      .dv-pill {
        display:inline-flex;
        align-items:center;
        gap:6px;
        padding:5px 9px;
        border:1px solid #2a2a2a;
        border-radius:999px;
        background:rgba(255,140,0,0.08);
        color:#f4f4f4;
        text-decoration:none;
        font-size:12px;
      }
      .dv-pill:hover { border-color:#ff8c00; }
      .dv-pill-src {
        opacity:0.7;
        font-size:10px;
        padding:2px 6px;
        border-radius:999px;
        border:1px solid #2a2a2a;
        background:rgba(0,0,0,0.25);
      }
    </style>
    """
    parts = [css, "<div>"]
    if not chunks:
        parts.append("<div class='dv-box'>No chunks found (no section_header items).</div>")
        parts.append("</div>")
        return "\n".join(parts)

    curated_state = _ensure_state(curated_state)

    for ch in chunks:
        if ch.page_min is None or ch.page_max is None:
            page_txt = "pages: ?"
        elif ch.page_min == ch.page_max:
            page_txt = f"page: {ch.page_min}"
        else:
            page_txt = f"pages: {ch.page_min}-{ch.page_max}"

        meta = f"{html.escape(ch.chunk_id)} {page_txt} items: {len(ch.item_idxs)}"

        curated_entries = curated_for_chunk(curated_state, ch.chunk_id)

        parts.append("<div class='dv-chunk'>")
        parts.append("<div class='dv-chunk-head'>")
        parts.append(f"<div class='dv-chunk-title'>{html.escape(ch.title or '(untitled)')}</div>")
        parts.append(f"<div class='dv-chunk-meta'>{meta}</div>")
        parts.append("</div>")

        parts.append("<div class='dv-curated'>")
        parts.append("<div class='dv-curated-title'>Curated subjects</div>")
        parts.append(_curated_badges_html(curated_entries))
        parts.append("</div>")

        parts.append(f"<div class='dv-chunk-body'>{html.escape(ch.text or '')}</div>")
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


# ============================================================
# Phase 5  UI helper: suggestions dropdown for selected chunk
# ============================================================
def suggestion_label_choices_for_chunk(suggestions_view: Dict[str, Any], chunk_id: Optional[str]) -> List[str]:
    if not chunk_id or not isinstance(suggestions_view, dict):
        return []
    block = suggestions_view.get(chunk_id)
    if not isinstance(block, dict):
        return []
    suggs = block.get("suggestions")
    if not isinstance(suggs, list):
        return []
    out: List[str] = []
    for s in suggs:
        if not isinstance(s, dict):
            continue
        lab = _norm_label(s.get("label"))
        if lab:
            out.append(lab)
    return out


# ============================================================
# Gradio main callback (Render)
# ============================================================
def load_and_render(
    uploaded_file,
    mode: str,
    annif_project_choice: str,
    chunk_choice: Union[str, List[str], None],
    show_empty_text: bool,
    max_items: int,
    render_limit: int,
    suggest_limit: int,
    suggest_threshold: float,
    curated_state: CuratedState,  # Phase 5 state
):
    proj_choices = annif_project_choices()
    if proj_choices:
        if annif_project_choice not in proj_choices:
            annif_project_choice = proj_choices[0]
        annif_update = gr.update(choices=proj_choices, value=annif_project_choice)
    else:
        annif_update = gr.update(choices=[], value=None)

    curated_state = _ensure_state(curated_state)

    if uploaded_file is None:
        dd_update = gr.update(choices=["(All chunks)"], value=["(All chunks)"])
        sugg_dd_update = gr.update(choices=[], value=None)
        rm_dd_update = gr.update(choices=[], value=None)
        return (
            "<div class='dv-box'>Upload a Docling JSON file first.</div>",
            {"items": 0},
            {},
            curated_state,
            {},
            annif_update,
            dd_update,
            sugg_dd_update,
            rm_dd_update,
        )

    data = load_json_robust(uploaded_file.name)

    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, dict):
        pages = {}

    items = extract_render_items(data, int(max_items))
    chunks = build_section_chunks(items)

    choices = chunk_choices(chunks)
    selected = normalize_chunk_choice(chunk_choice, choices)
    if "(All chunks)" in selected and len(selected) > 1:
        selected = [s for s in selected if s != "(All chunks)"]
    dd_update = gr.update(choices=choices, value=selected)

    annif_pid = annif_project_id_from_choice(annif_project_choice)

    base_stats = {
        "items": len(items),
        "pages": len(pages),
        "chunks": len(chunks),
        "mode": mode,
        "annif_project": annif_pid,
        "suggest_limit": int(suggest_limit),
        "suggest_threshold": float(suggest_threshold),
        "annif_base_url": ANNIF_BASE_URL,
        "curated_chunks": len(curated_state),
    }

    # Phase 4: suggestions (all or selected)
    suggestions_view: Dict[str, Any] = {}
    if mode == "Chunks" and annif_pid:
        selected_ids = selected_chunk_id_list(selected)  # None => all chunks
        suggestions_view = suggest_for_chunks(
            annif_project_id=annif_pid,
            chunks=chunks,
            selected_ids=selected_ids,
            limit=int(suggest_limit),
            threshold=float(suggest_threshold),
        )

    # Phase 5: selected chunk for curation controls
    cur_chunk_id = first_selected_chunk_id(selected)
    sugg_labels = suggestion_label_choices_for_chunk(suggestions_view, cur_chunk_id)
    sugg_dd_update = gr.update(choices=sugg_labels, value=(sugg_labels[0] if sugg_labels else None))

    rm_choices = curated_choices_for_chunk(curated_state, cur_chunk_id) if cur_chunk_id else []
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))

    curated_view = curated_state  # show raw state as JSON

    if mode == "Chunks":
        want = selected_chunk_ids(selected)  # None => all
        visible = [c for c in chunks if (want is None or c.chunk_id in want)]
        html_out = render_chunk_cards(visible, curated_state)
        return (
            html_out,
            base_stats,
            suggestions_view,
            curated_state,
            curated_view,
            annif_update,
            dd_update,
            sugg_dd_update,
            rm_dd_update,
        )

    html_out = render_boxes(data, items, pages, show_empty_text, int(render_limit))
    return (
        html_out,
        base_stats,
        suggestions_view,
        curated_state,
        curated_view,
        annif_update,
        dd_update,
        sugg_dd_update,
        rm_dd_update,
    )


# ============================================================
# Phase 5 callbacks (curation actions)
# ============================================================
def _selected_chunk_for_actions(chunk_choice: Union[str, List[str], None]) -> Optional[str]:
    if chunk_choice is None:
        return None
    if isinstance(chunk_choice, str):
        selected = [chunk_choice]
    elif isinstance(chunk_choice, list):
        selected = [c for c in chunk_choice if isinstance(c, str)]
    else:
        selected = []
    return first_selected_chunk_id(selected)


def on_accept_suggestion(
    curated_state: CuratedState,
    chunk_choice: Union[str, List[str], None],
    suggestion_label: Optional[str],
    suggestions_view: Dict[str, Any],
):
    curated_state = _ensure_state(curated_state)
    chunk_id = _selected_chunk_for_actions(chunk_choice)
    curated_state = accept_suggestion_to_state(curated_state, chunk_id, suggestion_label, suggestions_view)

    rm_choices = curated_choices_for_chunk(curated_state, chunk_id) if chunk_id else []
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))

    return curated_state, curated_state, rm_dd_update


def on_add_manual(
    curated_state: CuratedState,
    chunk_choice: Union[str, List[str], None],
    manual_label: Optional[str],
):
    curated_state = _ensure_state(curated_state)
    chunk_id = _selected_chunk_for_actions(chunk_choice)
    curated_state = add_manual_to_state(curated_state, chunk_id, manual_label)

    rm_choices = curated_choices_for_chunk(curated_state, chunk_id) if chunk_id else []
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))

    return curated_state, curated_state, gr.update(value=""), rm_dd_update


def on_remove_curated(
    curated_state: CuratedState,
    chunk_choice: Union[str, List[str], None],
    curated_item: Optional[str],
):
    curated_state = _ensure_state(curated_state)
    chunk_id = _selected_chunk_for_actions(chunk_choice)
    curated_state = remove_curated_from_state(curated_state, chunk_id, curated_item)

    rm_choices = curated_choices_for_chunk(curated_state, chunk_id) if chunk_id else []
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))

    return curated_state, curated_state, rm_dd_update


# ============================================================
# UI
# ============================================================
with gr.Blocks(title="Docling JSON Renderer") as demo:
    file_in = gr.File(file_types=[".json"], label="Docling JSON")

    curated_state = gr.State({})  # Phase 5: per-chunk curated subjects

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

    with gr.Row():
        suggest_limit = gr.Slider(1, 50, value=10, step=1, label="Annif suggest limit")
        suggest_threshold = gr.Slider(0.0, 1.0, value=0.0, step=0.01, label="Annif suggest threshold")

    btn = gr.Button("Render")

    html_view = gr.HTML()
    stats = gr.JSON()
    suggestions_view = gr.JSON(label="Annif suggestions (all / selected chunks)")

    # ----------------------------
    # Phase 5: Human Curation panel
    # ----------------------------
    with gr.Accordion("Human curation (per chunk)", open=True):
        with gr.Row():
            suggestion_pick = gr.Dropdown(choices=[], value=None, label="Pick Annif suggestion (selected chunk)")
            accept_btn = gr.Button("Accept suggestion")

        with gr.Row():
            manual_in = gr.Textbox(label="Manual subject entry", placeholder="e.g. sanitary appliance")
            manual_btn = gr.Button("Add manual subject")

        with gr.Row():
            curated_pick = gr.Dropdown(choices=[], value=None, label="Remove curated subject (selected chunk)")
            remove_btn = gr.Button("Remove subject")

        curated_view = gr.JSON(label="Curated subjects state (all chunks)")

    # Render callback (updates suggestion_pick + curated_pick)
    btn.click(
        load_and_render,
        [
            file_in,
            mode,
            annif_project,
            chunk_select,
            show_empty,
            max_items,
            render_limit,
            suggest_limit,
            suggest_threshold,
            curated_state,
        ],
        [
            html_view,
            stats,
            suggestions_view,
            curated_state,
            curated_view,
            annif_project,
            chunk_select,
            suggestion_pick,
            curated_pick,
        ],
    )

    # Accept suggestion -> updates curated_state + curated_view + curated_pick
    accept_btn.click(
        on_accept_suggestion,
        [curated_state, chunk_select, suggestion_pick, suggestions_view],
        [curated_state, curated_view, curated_pick],
    )

    # Add manual -> updates curated_state + curated_view + clears manual_in + curated_pick
    manual_btn.click(
        on_add_manual,
        [curated_state, chunk_select, manual_in],
        [curated_state, curated_view, manual_in, curated_pick],
    )

    # Remove curated -> updates curated_state + curated_view + curated_pick
    remove_btn.click(
        on_remove_curated,
        [curated_state, chunk_select, curated_pick],
        [curated_state, curated_view, curated_pick],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
 