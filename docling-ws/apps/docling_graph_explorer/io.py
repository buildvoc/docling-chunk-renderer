from __future__ import annotations

import json
from typing import Dict, Tuple

from .cache import compute_hash
from .logs import setup_logging

logger = setup_logging()


def load_docling_json(data: bytes) -> Tuple[Dict, str]:
    """Load Docling JSON bytes and return dict + hash."""
    doc_hash = compute_hash(data)
    try:
        doc = json.loads(data.decode("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("Docling export must be a JSON object")
        logger.info("loaded doc hash=%s keys=%s", doc_hash, ",".join(sorted(doc.keys())))
        return doc, doc_hash
    except Exception:
        logger.exception("failed to parse docling json")
        raise


def validate_doc(doc: Dict) -> Dict:
    """Lightweight validation for expected Docling export shape."""
    if not isinstance(doc, dict):
        raise ValueError("Doc must be a dict")

    if "texts" not in doc and "chunks" not in doc:
        logger.warning("doc missing texts/chunks; graph may be sparse")
    return doc


def doc_stats(doc: Dict) -> Dict[str, int]:
    """Basic stats for UI badges."""
    texts = doc.get("texts") or []
    groups = doc.get("groups") or []
    pictures = doc.get("pictures") or []
    tables = doc.get("tables") or []
    chunks = doc.get("chunks") or texts
    return {
        "texts": len(texts) if isinstance(texts, list) else 0,
        "chunks": len(chunks) if isinstance(chunks, list) else 0,
        "groups": len(groups) if isinstance(groups, list) else 0,
        "pictures": len(pictures) if isinstance(pictures, list) else 0,
        "tables": len(tables) if isinstance(tables, list) else 0,
    }
