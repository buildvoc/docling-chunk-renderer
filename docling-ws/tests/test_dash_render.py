import sys
from pathlib import Path

import pytest

dash = pytest.importorskip("dash")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apps.test_dash_app import app


def test_dash_layout_contains_text():
    layout = app.layout
    assert "Dash test OK" in str(layout)
