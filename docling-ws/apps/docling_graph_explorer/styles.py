from typing import List, Dict


def cytoscape_stylesheet() -> List[Dict]:
    """Single style set for fcose/cose layout."""
    return [
        {
            "selector": "node",
            "style": {
                "label": "data(label)",
                "font-size": "11px",
                "text-wrap": "wrap",
                "text-max-width": "180px",
                "background-color": "data(color)",
                "color": "#0f172a",
                "border-width": 1,
                "border-color": "#0f172a",
                "width": "mapData(degree, 0, 5, 24, 44)",
                "height": "mapData(degree, 0, 5, 24, 44)",
            },
        },
        {
            "selector": "edge",
            "style": {
                "curve-style": "bezier",
                "line-color": "#64748b",
                "target-arrow-color": "#64748b",
                "target-arrow-shape": "triangle",
                "arrow-scale": 0.7,
                "label": "data(label)",
                "font-size": "9px",
                "text-rotation": "autorotate",
            },
        },
        {"selector": ".docitem", "style": {"background-color": "#a5b4fc"}},
        {"selector": ".chunk", "style": {"background-color": "#fcd34d"}},
        {"selector": ".group", "style": {"background-color": "#93c5fd"}},
        {"selector": ".selected", "style": {"border-width": 3, "border-color": "#f97316"}},
    ]
