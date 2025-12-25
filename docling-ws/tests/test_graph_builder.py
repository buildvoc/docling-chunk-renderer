import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import apps.docling_graph.graph_builder as graph_builder


class GraphBuilderTests(unittest.TestCase):
    def tearDown(self):
        # Reset module-level constants between tests
        importlib.reload(graph_builder)

    def test_build_graph_from_fixture_has_nodes(self):
        fixture_path = Path("tests/fixtures/sample_docling.json")
        elements = graph_builder.build_graph_from_docling_json(str(fixture_path))

        nodes = [e for e in elements if "id" in e.get("data", {})]
        edges = [e for e in elements if "source" in e.get("data", {}) and "target" in e.get("data", {})]

        self.assertGreaterEqual(len(nodes), 5, "Expected at least document + 2 pages + 2 text nodes")
        self.assertGreaterEqual(len(edges), 4, "Expected doc->page and page->text edges")

        node_ids = {e["data"]["id"] for e in nodes}
        for edge in edges:
            data = edge.get("data", {})
            self.assertIn(data.get("source"), node_ids)
            self.assertIn(data.get("target"), node_ids)

    def test_list_docling_files_supports_relative_env_root(self):
        with tempfile.TemporaryDirectory(dir=os.getcwd()) as tmpdir:
            rel_root = os.path.basename(tmpdir)
            sample_path = Path(tmpdir) / "simple.json"
            sample_path.write_text(
                '{"texts": [{"text": "A sample text that is long enough to be picked up for graph building.", '
                '"label": "body", "provenance": [{"page_no": 1}]}]}'
            )

            with patch.dict(os.environ, {"DOCLING_JSON_ROOT": rel_root}, clear=False):
                reloaded = importlib.reload(graph_builder)
                files = reloaded.list_docling_files()

            # Reset module to default paths for any follow-up imports
            importlib.reload(graph_builder)

        self.assertEqual(files, [str(sample_path)])


if __name__ == "__main__":
    unittest.main()
