from fastapi import FastAPI
from starlette.middleware.wsgi import WSGIMiddleware
import logging

from apps.docling_graph_explorer.dash_app import create_dash_app
from apps.docling_graph_explorer.gradio_view import create_blocks

logger = logging.getLogger("docling_graph_explorer")


def create_app(port: int = 8055) -> FastAPI:
    """
    Single-port application:
    - FastAPI root (/)
    - Dash mounted at /dash
    - Gradio UI mounted at /
    """
    app = FastAPI()

    # --- Create Dash app ---
    # IMPORTANT: base_path MUST match the mount path (/dash)
    dash_app = create_dash_app(base_path="/dash")

    # Mount Dash (WSGI) under /dash
    app.mount("/dash", WSGIMiddleware(dash_app.server))

    # --- Create Gradio blocks ---
    # Gradio embeds Dash via iframe pointing to /dash/
    blocks = create_blocks(dash_url="/dash/")

    # Mount Gradio at root
    app.mount("/", blocks)

    logger.info(
        "Starting Docling Graph Explorer single-port "
        "listening on %s (dash mounted at /dash)",
        port,
    )

    return app


def main():
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8055)
    args = parser.parse_args()

    app = create_app(port=args.port)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
