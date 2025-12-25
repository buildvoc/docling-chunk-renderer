"""Docling Graph Explorer package."""

# Expose creators for integration with Docling-Serve UI.
from .gradio_view import create_blocks as create_gradio_blocks  # noqa: F401
from .dash_app import create_dash_app  # noqa: F401
