# Gradio Docling Renderer — Base Version

This document defines the **baseline version** of the Gradio Docling Renderer.
It is intended to be committed as the **reference implementation** for future
graphing, chunking, and Docling experiments.

---

## Purpose

A lightweight Gradio app for inspecting **Docling JSON exports** as
**black cards with white text**, used to validate structure before:

- chunking
- graph construction (CHILD / NEXT)
- indexing (search / RAG)

---

## Rendering Rules (Authoritative)

### 1. Section grouping — orange box

Exactly **one** orange section wrapper is rendered with this rule:

- **Open** a section at: `label == section_header`
- **Close** the section at: the **next** `section_header`
- `page_footer` is **never included** inside the section box

This rule is intentionally simple and deterministic.

---

### 2. Docling groups — detected (not required)

Docling JSON may include:

```
DoclingDocument.groups
```

This field is:

- **optional**
- commonly used to represent **list collections**
- not guaranteed to be present in all exports

This base version:

- detects whether `groups` exists
- reports:
  - `groups_present`
  - `groups_count`
- does **not** depend on groups for correct rendering

This ensures the renderer is safe even when `groups` is missing or incomplete.

---

## What the renderer shows

For each detected DocItem:

- label (Docling tag, e.g. `section_header`, `text`, `list_item`)
- extracted text
- hierarchy depth (best-effort)
- provenance when available:
  - `prov[].page_no`
  - `prov[].bbox`

---

## Supported DocItem labels (examples)

- `section_header`
- `text`
- `list_item`
- `table`
- `page_header`
- `page_footer`

Unknown labels are rendered as generic items.

---

## Design constraints (important)

- No reliance on reading-order heuristics
- No implicit grouping based on layout
- No assumptions about `groups[]` structure
- No string-concatenation tricks that can break parsing

The goal is **robust inspection**, not perfect layout.

---

## Recommended Git baseline

This version should be committed as the baseline with a message like:

```
Establish gradio docling renderer base (sections + groups detection)
```

All future changes should be incremental and testable against this version.

---

## Roadmap (explicitly out of scope for this base)

- Rendering Docling `groups[]` as visual list collections
- NEXT-edge / reading-order validation
- Chunk export (Markdown / JSONL)
- Graph visualisation

Those features should be layered **after** this base is stable.
