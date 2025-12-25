import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
import subprocess
from typing import Optional

DEFAULT_LOG_PATH = "/home/hp/docling-ws/logs/docling_graph_explorer.log"


def setup_logging(log_path: str = DEFAULT_LOG_PATH) -> logging.Logger:
    """Configure a module-level logger with rotation."""
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("docling_graph_explorer")
    logger.setLevel(logging.DEBUG if os.getenv("DOCLING_GRAPH_DEBUG") else logging.INFO)

    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Mirror to stdout if nothing is attached to root to avoid silent failures.
    root = logging.getLogger()
    if not root.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        root.addHandler(stream)
        root.setLevel(logger.level)

    logger.debug("Logging set up at %s", log_path)
    return logger


def tail_log(path: str, n_lines: int = 200) -> str:
    """Return the last N lines from a log file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        return "".join(lines[-n_lines:])
    except FileNotFoundError:
        return ""
    except Exception as exc:  # pragma: no cover - defensive
        return f"Error reading log: {exc}"


def filter_log(text: str, pattern: Optional[str], errors_only: bool = False) -> str:
    """Filter log text by substring/regex and optionally errors only."""
    if not text:
        return ""

    lines = text.splitlines()
    filtered = []
    regex = re.compile(pattern) if pattern else None
    for line in lines:
        if errors_only and not any(keyword in line for keyword in ("ERROR", "Traceback", "Exception")):
            continue
        if regex and not regex.search(line):
            continue
        filtered.append(line)
    return "\n".join(filtered)


def git_commit() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
        return out
    except Exception:
        return "unknown"
