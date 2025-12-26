#!/usr/bin/env python3
"""
Gradio Renderer (Docling -> Annif)

Key behaviors:
- Suggestions stored in Annif native format ONLY:
  { "label":"fire doorset", "score":0.37, "notation":null, "uri":"https://..." }

- In Chunks mode:
  * Default: render ONLY chunks that have at least one MED/HIGH suggestion (score >= 0.40)
  * If "Show LOW" is checked: render chunks that have ANY suggestion above MIN_SUGGEST_SCORE

- Suggestions box:
  * Not rendered at all if suggestions are null/empty OR all are filtered out (near-zero).
"""

from __future__ import annotations

import base64
import html
import io
import json
import time
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
# Annif constants + logging
# ============================================================
ANNIF_BASE_URL = "https://text-analytics.buildvoc.co.uk/v1"
ANNIF_TIMEOUT_S = 12.0

# Filter "0% | LOW" noise (tune if you want more/less aggressive)
MIN_SUGGEST_SCORE = 0.005  # 0.5%

# MED threshold
MED_SCORE_THRESHOLD = 0.40


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}")


def log_warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def log_err(msg: str) -> None:
    print(f"[ERR ] {msg}")


# ============================================================
# Robust JSON loader (handles trailing "Extra data")
# ============================================================
def load_json_robust(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        dec = json.JSONDecoder()
        obj, _end = dec.raw_decode(raw.lstrip())
        return obj


# ============================================================
# Normalization helpers
# ============================================================
SEP = " - "  # ASCII separator for dropdown labels


def ascii_clean(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").strip()


def clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        i = int(v)
    except Exception:
        return default
    return max(lo, min(hi, i))


def clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except Exception:
        return default
    return max(lo, min(hi, f))


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
    bboxes: List[Dict[str, Any]] = None


# ============================================================
# Docling parsing helpers
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
# Docling -> RenderItems (always include pictures)
# ============================================================
def extract_render_items(doc_json: Any, max_items: int) -> List[RenderItem]:
    if not isinstance(doc_json, dict):
        return []

    items: List[RenderItem] = []
    max_items = int(max_items) if max_items else 0

    # texts
    texts = doc_json.get("texts")
    if isinstance(texts, list) and texts:
        for i, t in enumerate(texts):
            if max_items and len(items) >= max_items:
                break
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
                    idx=len(items),
                    self_ref=self_ref,
                    label=label,
                    text=text,
                    page_no=page_no,
                    bbox=bbox,
                )
            )

    # pictures
    pics = doc_json.get("pictures")
    if isinstance(pics, list) and pics:
        for i, p in enumerate(pics):
            if max_items and len(items) >= max_items:
                break
            if not isinstance(p, dict):
                continue

            self_ref = p.get("self_ref") or f"#/pictures/{i}"
            if not isinstance(self_ref, str):
                self_ref = f"#/pictures/{i}"

            page_no, bbox = _item_page_bbox(p)

            items.append(
                RenderItem(
                    idx=len(items),
                    self_ref=self_ref,
                    label="picture",
                    text="",
                    page_no=page_no,
                    bbox=bbox,
                )
            )

    if items:
        return items

    # fallback
    for key in ("body", "furniture", "tables", "key_value_items", "form_items"):
        arr = doc_json.get(key)
        if not isinstance(arr, list):
            continue
        for t in arr:
            if max_items and len(items) >= max_items:
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
# Picture properties (classification + description)
# ============================================================
def _format_conf(v: Any) -> str:
    try:
        return f"{float(v):.3f}"
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

    rows: List[str] = []
    desc_text: Optional[str] = None

    ann = p.get("annotations")
    if isinstance(ann, list) and ann:
        for a in ann:
            if not isinstance(a, dict):
                continue
            if a.get("kind") == "classification":
                preds = a.get("predicted_classes")
                if isinstance(preds, list):
                    top = preds[:5]
                    if top:
                        rows.append("<div class='dv-prop-head'>Class</div>")
                        rows.append("<div class='dv-prop-head'>Confidence</div>")
                    for c in top:
                        if not isinstance(c, dict):
                            continue
                        cn = html.escape(str(c.get("class_name") or c.get("label") or ""))
                        cf = _format_conf(c.get("confidence") or c.get("score"))
                        rows.append(f"<div class='dv-prop-cell'>{cn}</div>")
                        rows.append(f"<div class='dv-prop-cell'>{cf}</div>")

            if a.get("kind") == "description" and desc_text is None:
                t = a.get("text")
                if isinstance(t, str) and t.strip():
                    desc_text = t.strip()

    if not rows and desc_text is None:
        meta = p.get("meta")
        if not isinstance(meta, dict):
            meta = {}

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

        if not (isinstance(desc_text, str) and desc_text.strip()):
            desc_text = None
        else:
            desc_text = desc_text.strip()

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
# Picture cropping (uri+sizes or base64 legacy)
# ============================================================
def _data_url_to_pil(data_url: str) -> Image.Image:
    _header, b64 = data_url.split(",", 1)
    raw = base64.b64decode(b64)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _pil_to_data_url_png(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def crop_picture_from_page(pages: Dict[str, Any], page_no: int, bbox: Dict[str, Any], pad_px: int = 2) -> Optional[str]:
    if Image is None:
        return None
    if not isinstance(pages, dict):
        return None

    page = pages.get(str(page_no))
    if page is None:
        page = pages.get(page_no)
    if not isinstance(page, dict):
        return None

    img_meta = page.get("image")
    if isinstance(img_meta, dict):
        uri = img_meta.get("uri")
        if isinstance(uri, str) and uri.startswith("data:"):
            try:
                im = _data_url_to_pil(uri)
            except Exception:
                return None

            pdf_size = page.get("size") if isinstance(page.get("size"), dict) else {}
            px_size = img_meta.get("size") if isinstance(img_meta.get("size"), dict) else {}

            try:
                pdf_w = float(pdf_size.get("width") or 0)
                pdf_h = float(pdf_size.get("height") or 0)
            except Exception:
                pdf_w, pdf_h = 0.0, 0.0

            try:
                px_w = float(px_size.get("width") or 0)
                px_h = float(px_size.get("height") or 0)
            except Exception:
                px_w, px_h = 0.0, 0.0

            if px_w <= 0 or px_h <= 0:
                W, H = im.size
                px_w, px_h = float(W), float(H)

            if pdf_w > 0 and pdf_h > 0 and px_w > 0 and px_h > 0:
                try:
                    l = float(bbox.get("l", 0))
                    t = float(bbox.get("t", 0))
                    r = float(bbox.get("r", 0))
                    b = float(bbox.get("b", 0))
                except Exception:
                    return None

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

                return _pil_to_data_url_png(im.crop((x0, y0, x1, y1)))

    if isinstance(img_meta, str) and img_meta:
        try:
            raw = base64.b64decode(img_meta)
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
        x0 = max(0, min(W, int(min(l, r)) - pad_px))
        x1 = max(0, min(W, int(max(l, r)) + pad_px))
        y0 = max(0, min(H, int(min(t, b)) - pad_px))
        y1 = max(0, min(H, int(max(t, b)) + pad_px))

        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        return _pil_to_data_url_png(im.crop((x0, y0, x1, y1)))

    return None


# ============================================================
# Chunking (section_header based)
# ============================================================
def build_section_chunks(items: List[RenderItem]) -> List[Chunk]:
    if not items:
        return []

    chunks: List[Chunk] = []
    current: Optional[Chunk] = None

    def finalize(ch: Chunk) -> None:
        ch.text = (ch.text or "").strip()
        if ch.bboxes is None:
            ch.bboxes = []
        chunks.append(ch)

    for it in items:
        if it.label == "section_header":
            if current is not None:
                finalize(current)
            title = (it.text or "").strip() or "(untitled)"
            current = Chunk(
                chunk_id=f"chunk_{len(chunks)+1:04d}",
                title=title,
                text="",
                item_idxs=[],
                page_min=it.page_no,
                page_max=it.page_no,
                bboxes=[],
            )
            continue

        if current is None:
            current = Chunk(chunk_id="chunk_0000", title="(preamble)", text="", item_idxs=[], bboxes=[])

        current.item_idxs.append(it.idx)
        if it.text:
            current.text += it.text.strip() + "\n"

        if isinstance(it.page_no, int):
            if current.page_min is None or it.page_no < current.page_min:
                current.page_min = it.page_no
            if current.page_max is None or it.page_no > current.page_max:
                current.page_max = it.page_no

        if isinstance(it.bbox, dict):
            current.bboxes.append(it.bbox)

    if current is not None:
        finalize(current)

    return chunks


def chunk_choices(chunks: List[Chunk]) -> List[str]:
    return ["(All chunks)"] + [f"{c.chunk_id}{SEP}{ascii_clean(c.title)}" for c in chunks]


def normalize_multi_choice(choice: Union[str, List[str], None], choices: List[str]) -> List[str]:
    if choice is None:
        return ["(All chunks)"]
    if isinstance(choice, str):
        return [choice] if choice in choices else ["(All chunks)"]
    if isinstance(choice, list):
        cleaned = [c for c in choice if isinstance(c, str) and c in choices]
        return cleaned or ["(All chunks)"]
    return ["(All chunks)"]


def selected_chunk_id_list(selected_labels: List[str]) -> Optional[List[str]]:
    if "(All chunks)" in selected_labels:
        return None
    out: List[str] = []
    for lab in selected_labels:
        if lab == "(All chunks)":
            continue
        cid = lab.split(SEP, 1)[0].strip()
        if cid:
            out.append(cid)
    return out or None


def first_selected_chunk_id(selected_labels: List[str]) -> Optional[str]:
    for lab in selected_labels:
        if lab == "(All chunks)":
            continue
        cid = lab.split(SEP, 1)[0].strip()
        if cid:
            return cid
    return None


# ============================================================
# Annif projects (cached)
# ============================================================
_PROJECT_CACHE: Dict[str, Any] = {"ts": 0.0, "projects": []}
_PROJECT_CACHE_TTL_S = 300.0


def annif_list_projects() -> List[Dict[str, Any]]:
    now = time.time()
    if (now - float(_PROJECT_CACHE.get("ts") or 0.0)) < _PROJECT_CACHE_TTL_S:
        projs = _PROJECT_CACHE.get("projects")
        return projs if isinstance(projs, list) else []

    url = f"{ANNIF_BASE_URL}/projects"
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=ANNIF_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        projs = data.get("projects")
        if not isinstance(projs, list):
            projs = []
    except Exception as e:
        log_warn(f"Annif /projects failed: {e}")
        projs = []

    _PROJECT_CACHE["ts"] = now
    _PROJECT_CACHE["projects"] = projs
    return projs


def annif_project_choices() -> List[str]:
    out: List[str] = []
    for p in annif_list_projects():
        if not isinstance(p, dict):
            continue
        pid = p.get("project_id")
        name = p.get("name")
        if not isinstance(pid, str) or not pid.strip():
            continue
        pid_clean = ascii_clean(pid)
        name_clean = ascii_clean(name) if isinstance(name, str) else ""
        out.append(f"{pid_clean}{SEP}{name_clean}" if name_clean else pid_clean)
    return out


def annif_project_id_from_choice(choice: Optional[str]) -> Optional[str]:
    if not isinstance(choice, str) or not choice.strip():
        return None
    return choice.split(SEP, 1)[0].strip()


# ============================================================
# Suggestions (Annif native format ONLY)
# ============================================================
def confidence_band(score: float) -> str:
    if score >= 0.75:
        return "HIGH"
    if score >= 0.40:
        return "MED"
    return "LOW"


def annif_suggest(project_id: str, text: str, limit: int, threshold: float) -> List[Dict[str, Any]]:
    if not project_id or not text or not text.strip():
        return []

    url = f"{ANNIF_BASE_URL}/projects/{project_id}/suggest"
    form = {"text": text, "limit": int(limit), "threshold": float(threshold)}

    try:
        r = requests.post(url, data=form, headers={"Accept": "application/json"}, timeout=ANNIF_TIMEOUT_S)
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
            sc = float(score)
            if sc < MIN_SUGGEST_SCORE:
                continue
            out.append(
                {
                    "label": label.strip(),
                    "score": sc,
                    "notation": it.get("notation"),
                    "uri": it.get("uri"),
                }
            )
    return out


def suggest_for_chunks(
    project_id: str,
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
            "suggestions": annif_suggest(project_id, ch.text, limit=limit, threshold=threshold),
        }
    return out


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
        lab = s.get("label")
        sc = s.get("score")
        if isinstance(lab, str) and lab.strip() and isinstance(sc, (int, float)) and float(sc) >= MIN_SUGGEST_SCORE:
            out.append(lab.strip())
    return out


def chunk_passes_filter(suggs: Any, show_low: bool) -> bool:
    """
    Chunk render gating (Chunks mode only):
    - show_low=False: need at least one MED/HIGH (score >= 0.40)
    - show_low=True: need at least one suggestion >= MIN_SUGGEST_SCORE
    """
    if not isinstance(suggs, list):
        return False

    if show_low:
        for s in suggs:
            if not isinstance(s, dict):
                continue
            sc = s.get("score")
            if isinstance(sc, (int, float)) and float(sc) >= MIN_SUGGEST_SCORE:
                return True
        return False

    for s in suggs:
        if not isinstance(s, dict):
            continue
        sc = s.get("score")
        if isinstance(sc, (int, float)) and float(sc) >= MED_SCORE_THRESHOLD:
            return True
    return False


# ============================================================
# Human curation state
# ============================================================
CuratedState = Dict[str, List[Dict[str, Any]]]


def _ensure_state(s: Any) -> CuratedState:
    return s if isinstance(s, dict) else {}


def _norm_label(x: Any) -> str:
    return str(x).strip() if isinstance(x, (str, int, float)) else ""


def _dedupe_entries(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        lab = _norm_label(e.get("label")).lower()
        uri = _norm_label(e.get("uri"))
        if not lab:
            continue
        key = (lab, uri)
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def curated_for_chunk(state: CuratedState, chunk_id: str) -> List[Dict[str, Any]]:
    state = _ensure_state(state)
    v = state.get(chunk_id, [])
    return v if isinstance(v, list) else []


def curated_choices_for_chunk(state: CuratedState, chunk_id: Optional[str]) -> List[str]:
    if not chunk_id:
        return []
    entries = curated_for_chunk(state, chunk_id)
    out: List[str] = []
    for e in entries:
        lab = _norm_label(e.get("label"))
        uri = _norm_label(e.get("uri"))
        out.append(f"{lab} | {uri}" if uri else lab)
    return out


def accept_many_to_state(
    state: CuratedState,
    chunk_id: Optional[str],
    suggestion_labels: List[str],
    suggestions_view: Dict[str, Any],
) -> CuratedState:
    state = _ensure_state(state)
    if not chunk_id or not suggestion_labels or not isinstance(suggestions_view, dict):
        return state

    block = suggestions_view.get(chunk_id)
    if not isinstance(block, dict):
        return state
    suggs = block.get("suggestions")
    if not isinstance(suggs, list):
        return state

    want = {_norm_label(v) for v in suggestion_labels if _norm_label(v)}
    if not want:
        return state

    cur = curated_for_chunk(state, chunk_id)

    for s in suggs:
        if not isinstance(s, dict):
            continue
        lab = _norm_label(s.get("label"))
        sc = s.get("score")
        if lab not in want:
            continue
        if isinstance(sc, (int, float)) and float(sc) < MIN_SUGGEST_SCORE:
            continue
        cur.append(
            {
                "label": lab,
                "uri": _norm_label(s.get("uri")) or None,
                "notation": s.get("notation"),
                "source": "annif",
            }
        )

    state[chunk_id] = _dedupe_entries(cur)
    return state


def add_manual_to_state(state: CuratedState, chunk_id: Optional[str], manual_label: Optional[str]) -> CuratedState:
    state = _ensure_state(state)
    if not chunk_id:
        return state
    lab = _norm_label(manual_label)
    if not lab:
        return state
    cur = curated_for_chunk(state, chunk_id)
    cur.append({"label": lab, "uri": None, "notation": None, "source": "manual"})
    state[chunk_id] = _dedupe_entries(cur)
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
# Rendering CSS + helpers (dark-mode safe pills)
# ============================================================
CSS = """
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

  .dv-curated {
    border:1px solid #2a2a2a;
    border-radius:12px;
    padding:10px 12px;
    background:rgba(255,255,255,0.03);
    margin:10px 0;
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

  .dv-suggbox {
    border:1px solid #2a2a2a;
    border-radius:12px;
    padding:10px 12px;
    background:rgba(255,255,255,0.02);
    margin:10px 0 0 0;
  }
  .dv-sugghead {
    font-weight:800;
    opacity:0.92;
    margin-bottom:8px;
    display:flex;
    justify-content:space-between;
    align-items:baseline;
    gap:12px;
  }
  .dv-sugghead small { font-weight:500; opacity:0.75; }

  .dv-suggpillwrap {
    display:flex;
    flex-wrap:wrap;
    gap:8px;
  }

  .dv-suggpill {
    display:inline-flex;
    align-items:center;
    gap:8px;
    padding:7px 10px;
    border:1px solid rgba(255,255,255,0.12);
    border-radius:999px;
    background:rgba(0,0,0,0.40);
    color:#f4f4f4;
    font-size:12px;
    line-height:1;
  }

  .dv-suggpill:hover {
    border-color: rgba(255,140,0,0.70);
    background: rgba(255,140,0,0.12);
  }

  .dv-suggpill-label { font-weight:650; opacity:0.95; }

  .dv-suggpill-meta {
    opacity:0.90;
    font-weight:800;
    padding:3px 7px;
    border-radius:999px;
    border:1px solid rgba(255,255,255,0.14);
    background:rgba(0,0,0,0.28);
    color:#ffffff;
  }

  .dv-suggpill.dv-band-HIGH .dv-suggpill-meta {
    border-color: rgba( 70, 200, 120, 0.75 );
    background:   rgba( 70, 200, 120, 0.12 );
    color: #DFF5E8;
  }
  .dv-suggpill.dv-band-MED .dv-suggpill-meta {
    border-color: rgba(255, 200,  80, 0.75 );
    background:   rgba(255, 200,  80, 0.12 );
    color: #FFF3CF;
  }
  .dv-suggpill.dv-band-LOW .dv-suggpill-meta {
    border-color: rgba(220,  90,  90, 0.75 );
    background:   rgba(220,  90,  90, 0.12 );
    color: #FDE2E2;
  }
</style>
"""


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


def _suggestions_pills_block(suggestions_view: Dict[str, Any], chunk_id: str) -> str:
    """
    Render nothing if:
    - suggestions null/empty
    - OR all suggestions filtered out (near-zero)
    """
    if not isinstance(suggestions_view, dict):
        return ""

    block = suggestions_view.get(chunk_id)
    if not isinstance(block, dict):
        return ""

    suggs = block.get("suggestions")
    if not isinstance(suggs, list) or len(suggs) == 0:
        return ""

    parts: List[str] = []
    parts.append("<div class='dv-suggbox'>")
    parts.append("<div class='dv-sugghead'>Suggestions <small>(Annif)</small></div>")
    parts.append("<div class='dv-suggpillwrap'>")

    any_rendered = False
    for s in suggs[:80]:
        if not isinstance(s, dict):
            continue
        label = s.get("label")
        score = s.get("score")
        if not isinstance(label, str) or not label.strip() or not isinstance(score, (int, float)):
            continue

        sc = float(score)
        if sc < MIN_SUGGEST_SCORE:
            continue

        pct = int(round(sc * 100))
        pct = max(0, min(100, pct))
        band = confidence_band(sc)

        label_esc = html.escape(label.strip())
        meta_esc = html.escape(f"{pct}% | {band}")

        parts.append(
            f"<span class='dv-suggpill dv-band-{band}'>"
            f"<span class='dv-suggpill-label'>{label_esc}</span>"
            f"<span class='dv-suggpill-meta'>{meta_esc}</span>"
            f"</span>"
        )
        any_rendered = True

    parts.append("</div></div>")
    return "".join(parts) if any_rendered else ""


def render_items_view(
    doc_json: Dict[str, Any],
    items: List[RenderItem],
    pages: Dict[str, Any],
    show_empty_text: bool,
    limit: int,
) -> str:
    parts = [CSS, "<div>"]
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

        if not show_empty_text and (not it.text or not it.text.strip()):
            continue

        parts.append(f"<div class='dv-box'>{html.escape(it.text)}</div>")

    if section_open:
        parts.append("</div>")
    parts.append("</div>")
    return "\n".join(parts)


def render_chunks_view(chunks: List[Chunk], curated_state: CuratedState, suggestions_view: Dict[str, Any]) -> str:
    curated_state = _ensure_state(curated_state)
    parts = [CSS, "<div>"]

    if not chunks:
        parts.append("<div class='dv-box'>No chunks to render (filtered out).</div></div>")
        return "\n".join(parts)

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

        parts.append(f"<div class='dv-chunk-body'>{html.escape(ch.text or '')}</div>")

        parts.append("<div class='dv-curated'>")
        parts.append("<div class='dv-curated-title'>Curated subjects</div>")
        parts.append(_curated_badges_html(curated_entries))
        parts.append("</div>")

        sugg_html = _suggestions_pills_block(suggestions_view, ch.chunk_id)
        if sugg_html:
            parts.append(sugg_html)

        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


# ============================================================
# Main Gradio callback
# ============================================================
def load_and_render(
    uploaded_file,
    mode: str,
    annif_project_choice: Optional[str],
    chunk_choice: Union[str, List[str], None],
    show_empty_text: bool,
    max_items: int,
    render_limit: int,
    suggest_limit: int,
    suggest_threshold: float,
    show_low: bool,  # NEW
    curated_state: CuratedState,
):
    curated_state = _ensure_state(curated_state)

    proj_choices = annif_project_choices()
    if proj_choices:
        if annif_project_choice not in proj_choices:
            annif_project_choice = proj_choices[0]
        annif_update = gr.update(choices=proj_choices, value=annif_project_choice)
    else:
        annif_update = gr.update(choices=[], value=None)

    if uploaded_file is None:
        dd_update = gr.update(choices=["(All chunks)"], value=["(All chunks)"])
        sugg_dd_update = gr.update(choices=[], value=[])
        rm_dd_update = gr.update(choices=[], value=None)
        return (
            "<div class='dv-box'>Upload a Docling JSON file first.</div>",
            {"items_total": 0},
            {},
            curated_state,
            curated_state,
            annif_update,
            dd_update,
            sugg_dd_update,
            rm_dd_update,
        )

    data = load_json_robust(uploaded_file.name)
    if not isinstance(data, dict):
        data = {}

    pages = data.get("pages")
    if not isinstance(pages, dict):
        pages = {}

    items = extract_render_items(data, clamp_int(max_items, 10, 100000, 2000))
    chunks = build_section_chunks(items)

    choices = chunk_choices(chunks)
    selected = normalize_multi_choice(chunk_choice, choices)
    if "(All chunks)" in selected and len(selected) > 1:
        selected = [s for s in selected if s != "(All chunks)"]
    dd_update = gr.update(choices=choices, value=selected)

    annif_pid = annif_project_id_from_choice(annif_project_choice)

    # "All chunks" handling
    sel_ids = selected_chunk_id_list(selected)  # None => All
    if sel_ids is not None and len(chunks) > 0 and len(sel_ids) >= len(chunks):
        sel_ids = None  # effectively all

    want_ids = set(sel_ids or [])
    visible = chunks if sel_ids is None else [c for c in chunks if c.chunk_id in want_ids]

    suggestions_view: Dict[str, Any] = {}
    if mode == "Chunks" and annif_pid:
        suggestions_view = suggest_for_chunks(
            project_id=annif_pid,
            chunks=visible,
            selected_ids=None,
            limit=clamp_int(suggest_limit, 1, 50, 10),
            threshold=clamp_float(suggest_threshold, 0.0, 1.0, 0.0),
        )

        # NEW: render only chunks that pass filter (MED/HIGH by default; LOW optional)
        filtered: List[Chunk] = []
        for ch in visible:
            block = suggestions_view.get(ch.chunk_id)
            if not isinstance(block, dict):
                continue
            suggs = block.get("suggestions")
            if chunk_passes_filter(suggs, show_low=bool(show_low)):
                filtered.append(ch)
        visible = filtered

    cur_chunk_id = first_selected_chunk_id(selected)
    sugg_labels = suggestion_label_choices_for_chunk(suggestions_view, cur_chunk_id)
    sugg_dd_update = gr.update(choices=sugg_labels, value=[])

    rm_choices = curated_choices_for_chunk(curated_state, cur_chunk_id)
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))

    stats = {
        "items_total": len(items),
        "chunks_total": len(chunks),
        "visible_chunks": len(visible),
        "mode": mode,
        "annif_base_url": ANNIF_BASE_URL,
        "annif_project": annif_pid,
        "suggest_limit": clamp_int(suggest_limit, 1, 50, 10),
        "suggest_threshold": clamp_float(suggest_threshold, 0.0, 1.0, 0.0),
        "show_low": bool(show_low),
        "min_suggest_score": MIN_SUGGEST_SCORE,
        "med_score_threshold": MED_SCORE_THRESHOLD,
        "curated_chunks": len(curated_state),
        "selected_chunk": cur_chunk_id,
    }

    if mode == "Chunks":
        html_out = render_chunks_view(visible, curated_state, suggestions_view)
        return (
            html_out,
            stats,
            suggestions_view,
            curated_state,
            curated_state,
            annif_update,
            dd_update,
            sugg_dd_update,
            rm_dd_update,
        )

    html_out = render_items_view(data, items, pages, bool(show_empty_text), clamp_int(render_limit, 1, 20000, 600))
    return (
        html_out,
        stats,
        suggestions_view,
        curated_state,
        curated_state,
        annif_update,
        dd_update,
        sugg_dd_update,
        rm_dd_update,
    )


# ============================================================
# Phase 5 callbacks
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


def on_accept_selected(curated_state: CuratedState, chunk_choice, suggestion_labels, suggestions_view):
    curated_state = _ensure_state(curated_state)
    chunk_id = _selected_chunk_for_actions(chunk_choice)

    vals: List[str] = []
    if isinstance(suggestion_labels, list):
        vals = [v for v in suggestion_labels if isinstance(v, str) and v.strip()]

    curated_state = accept_many_to_state(curated_state, chunk_id, vals, suggestions_view)

    rm_choices = curated_choices_for_chunk(curated_state, chunk_id)
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))
    return curated_state, curated_state, rm_dd_update


def on_add_manual(curated_state: CuratedState, chunk_choice, manual_label: str):
    curated_state = _ensure_state(curated_state)
    chunk_id = _selected_chunk_for_actions(chunk_choice)
    curated_state = add_manual_to_state(curated_state, chunk_id, manual_label)

    rm_choices = curated_choices_for_chunk(curated_state, chunk_id)
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))
    return curated_state, curated_state, gr.update(value=""), rm_dd_update


def on_remove_curated(curated_state: CuratedState, chunk_choice, curated_item: str):
    curated_state = _ensure_state(curated_state)
    chunk_id = _selected_chunk_for_actions(chunk_choice)
    curated_state = remove_curated_from_state(curated_state, chunk_id, curated_item)

    rm_choices = curated_choices_for_chunk(curated_state, chunk_id)
    rm_dd_update = gr.update(choices=rm_choices, value=(rm_choices[0] if rm_choices else None))
    return curated_state, curated_state, rm_dd_update


# ============================================================
# UI
# ============================================================
with gr.Blocks(title="Gradio Renderer (Docling -> Annif)") as demo:
    file_in = gr.File(file_types=[".json"], label="Docling JSON")
    curated_state = gr.State({})

    proj_choices = annif_project_choices()
    default_proj = proj_choices[0] if proj_choices else None

    with gr.Row():
        mode = gr.Radio(["Items", "Chunks"], value="Items", label="Render mode")
        annif_project = gr.Dropdown(
            choices=proj_choices,
            value=default_proj,
            label="Annif project",
            info="Fetched from Annif /projects (cached)",
        )
        chunk_select = gr.Dropdown(
            choices=["(All chunks)"],
            value=["(All chunks)"],
            multiselect=True,
            label="Chunk selector (multi)",
            info="Used in Chunks mode for rendering + suggestion scope",
        )

    show_empty = gr.Checkbox(False, label="Show empty text (Items mode)")
    max_items = gr.Slider(100, 10000, value=2000, step=100, label="Max items to scan")
    render_limit = gr.Slider(50, 5000, value=600, step=50, label="Render limit (Items mode)")

    with gr.Row():
        suggest_limit = gr.Slider(1, 50, value=10, step=1, label="Annif suggest limit")
        suggest_threshold = gr.Slider(0.0, 1.0, value=0.0, step=0.01, label="Annif suggest threshold")

    # NEW: checkbox to include LOW chunks
    show_low = gr.Checkbox(
        False,
        label="Show LOW",
        info="If checked, include chunks with LOW-confidence Annif suggestions",
    )

    btn = gr.Button("Render")

    html_view = gr.HTML()
    stats = gr.JSON(label="Stats")
    suggestions_view = gr.JSON(label="Annif suggestions (chunk map)")

    with gr.Accordion("Human curation (selected chunk)", open=True):
        with gr.Row():
            suggestion_pick = gr.Dropdown(
                choices=[],
                value=[],
                multiselect=True,
                label="Pick Annif suggestion(s) (selected chunk)",
            )
            accept_btn = gr.Button("Accept selected")

        with gr.Row():
            manual_in = gr.Textbox(label="Manual subject entry", placeholder="e.g. fire doorset")
            manual_btn = gr.Button("Add manual subject")

        with gr.Row():
            curated_pick = gr.Dropdown(choices=[], value=None, label="Remove curated subject (selected chunk)")
            remove_btn = gr.Button("Remove subject")

        curated_view = gr.JSON(label="Curated subjects state (all chunks)")

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
            show_low,        # NEW
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

    accept_btn.click(
        on_accept_selected,
        [curated_state, chunk_select, suggestion_pick, suggestions_view],
        [curated_state, curated_view, curated_pick],
    )

    manual_btn.click(
        on_add_manual,
        [curated_state, chunk_select, manual_in],
        [curated_state, curated_view, manual_in, curated_pick],
    )

    remove_btn.click(
        on_remove_curated,
        [curated_state, chunk_select, curated_pick],
        [curated_state, curated_view, curated_pick],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
 