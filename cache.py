"""
cache.py — Incremental hashing and cache persistence.

Hashes each table's structure (columns + types + FKs) so that on restart
only changed tables trigger re-embedding and re-description.
"""

import hashlib
import json
import os
import base64
from datetime import datetime, timezone

import numpy as np

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_cache.json")


def hash_table(graph, table_name):
    """Deterministic SHA-256 for a table node based on columns + FK edges."""
    node = graph.nodes[table_name]
    parts = []
    for col in sorted(node["columns"], key=lambda c: c["name"]):
        parts.append(f"{col['name']}:{col['type']}:{col['nullable']}:{col['primary_key']}")
    for _, target, edata in sorted(graph.out_edges(table_name, data=True), key=lambda e: e[1]):
        parts.append(f"FK:{edata['fk_column']}→{target}.{edata['ref_column']}:{edata['on_delete']}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def compute_all_hashes(graph):
    return {name: hash_table(graph, name) for name in graph.nodes}


def get_stale_tables(current_hashes, cached_hashes):
    stale = set()
    for name, h in current_hashes.items():
        if cached_hashes.get(name) != h:
            stale.add(name)
    for name in cached_hashes:
        if name not in current_hashes:
            stale.add(name)
    return stale


def encode_embedding(vec):
    return base64.b64encode(np.array(vec, dtype=np.float32).tobytes()).decode("ascii")


def decode_embedding(b64str):
    return np.frombuffer(base64.b64decode(b64str), dtype=np.float32).copy()


def serialize_graph(graph):
    nodes = []
    for name in sorted(graph.nodes):
        d = graph.nodes[name]
        nodes.append({"name": name, "columns": d["columns"], "pk": d["pk"], "patterns": d["patterns"]})
    edges = []
    for src, tgt, d in graph.edges(data=True):
        edges.append({"source": src, "target": tgt, "fk_column": d["fk_column"],
                       "ref_column": d["ref_column"], "on_delete": d["on_delete"], "nullable": d["nullable"]})
    return {"nodes": nodes, "edges": edges}


def load_cache(path=None):
    path = path or CACHE_PATH
    if not os.path.exists(path):
        return {"hashes": {}, "descriptions": {}, "embeddings": {}, "graph": None, "generated_at": None}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cache(data, path=None):
    path = path or CACHE_PATH
    tmp = path + ".tmp"
    data["generated_at"] = datetime.now(timezone.utc).isoformat()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)
