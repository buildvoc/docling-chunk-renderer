from __future__ import annotations

from typing import Dict, Iterable, List

import networkx as nx


def _build_graph(nodes: Iterable[Dict], edges: Iterable[Dict]) -> nx.Graph:
    g = nx.Graph()
    for node in nodes:
        node_id = node.get("id")
        if node_id is None:
            continue
        g.add_node(node_id)
    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src is None or tgt is None:
            continue
        g.add_edge(src, tgt)
    return g


def compute_metrics(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Dict]:
    """Compute degree, betweenness, and community id for nodes."""
    g = _build_graph(nodes, edges)
    degree = dict(g.degree())
    betweenness = nx.betweenness_centrality(g, normalized=True, seed=0)

    # Deterministic greedy modularity communities
    communities = list(nx.algorithms.community.greedy_modularity_communities(g, weight=None))
    communities = [sorted(list(c)) for c in communities]

    cluster_map: Dict[str, int] = {}
    for cid, members in enumerate(sorted(communities, key=lambda c: (len(c) * -1, c))):
        for node_id in members:
            cluster_map[node_id] = cid

    metrics: Dict[str, Dict] = {}
    for node in nodes:
        nid = node.get("id")
        metrics[nid] = {
            "degree": float(degree.get(nid, 0)),
            "betweenness": float(betweenness.get(nid, 0.0)),
            "community": int(cluster_map.get(nid, -1)),
        }
    return metrics
