from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .logs import setup_logging

logger = setup_logging()


@dataclass
class CacheEntry:
    doc_hash: str
    doc: Dict
    graph: Dict[str, Any]
    metrics: Dict[str, Any]
    chunk_order: Tuple[str, ...]
    created_at: float


_CACHE: Dict[str, CacheEntry] = {}


def compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get(doc_hash: str) -> Optional[CacheEntry]:
    entry = _CACHE.get(doc_hash)
    if entry:
        logger.info("cache hit hash=%s age=%.2fs", doc_hash, time.time() - entry.created_at)
    else:
        logger.info("cache miss hash=%s", doc_hash)
    return entry


def set_entry(doc_hash: str, doc: Dict, graph: Dict[str, Any], metrics: Dict[str, Any], chunk_order) -> CacheEntry:
    entry = CacheEntry(
        doc_hash=doc_hash,
        doc=doc,
        graph=graph,
        metrics=metrics,
        chunk_order=tuple(chunk_order or []),
        created_at=time.time(),
    )
    _CACHE[doc_hash] = entry
    logger.info("cache store hash=%s nodes=%d edges=%d", doc_hash, len(graph.get("nodes", [])), len(graph.get("edges", [])))
    return entry
