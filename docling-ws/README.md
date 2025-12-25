# Docling Graph Viewer

Lightweight Dash app for exploring Docling JSON outputs as a graph.

## Debugging a blank graph
- Ensure Docling JSON files are available. Set `DOCLING_JSON_ROOT` to the directory containing your Docling outputs (relative paths resolve from the current working directory). The app logs the chosen root and the number of files discovered.
- Watch server logs while switching files or clicking **Apply**; you should see counts for elements returned by the graph builder and callback.
- If the dropdown is empty or the callback logs `0 elements`, verify the root path and that the JSON includes `texts` with page provenance.
- Run `python3 -m unittest discover -s tests` to exercise the graph builder against the bundled fixture and the path resolution logic.
