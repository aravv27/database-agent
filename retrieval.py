"""
retrieval.py — Two-phase retrieval: semantic seeding + graph expansion.

Phase 1: Embed table descriptions with sentence-transformers, cosine-sim
          against user query to find seed tables.
Phase 2: Walk the NetworkX graph from seeds to assemble a full context
          package with join paths, auth linkage, and pattern hints.
"""

import numpy as np
from sentence_transformers import SentenceTransformer

# ── Embedding model (loaded once) ──────────────────────────────────────────

_model = None

def _get_model():
    global _model
    if _model is None:
        print("  Loading embedding model (first time may download ~80MB)...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def embed_text(text):
    """Embed a single string, return numpy float32 vector."""
    model = _get_model()
    return model.encode(text, convert_to_numpy=True).astype(np.float32)


def embed_tables(graph, descriptions):
    """
    Build embeddings for all tables.
    Embed string: "{table_name}: {description}. Columns: {col1, col2, ...}"
    Returns {table_name: np.array(384,)}.
    """
    embeddings = {}
    for table_name in sorted(graph.nodes):
        desc_info = descriptions.get(table_name, {})
        desc_text = desc_info.get("description", "")
        cols = ", ".join(c["name"] for c in graph.nodes[table_name]["columns"])
        text = f"{table_name}: {desc_text}. Columns: {cols}"
        embeddings[table_name] = embed_text(text)
    return embeddings


# ── Semantic seeding (Phase 1) ─────────────────────────────────────────────

def cosine_sim(a, b):
    """Cosine similarity between two vectors."""
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(dot / norm) if norm > 0 else 0.0


def find_seeds(query, embeddings, top_k=2):
    """
    Embed the query and return the top-K most similar table names.
    Returns list of (table_name, similarity_score).
    """
    query_vec = embed_text(query)
    scores = []
    for table_name, table_vec in embeddings.items():
        sim = cosine_sim(query_vec, table_vec)
        scores.append((table_name, sim))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]


# ── Graph expansion (Phase 2) ──────────────────────────────────────────────

def _trace_auth_chain(graph, start_table, target="users", max_hops=3):
    """
    Try to find a path from start_table to the target (usually 'users')
    by following outgoing FK edges. Returns the path as a list of
    (table, fk_col, ref_table) tuples, or None if unreachable.
    """
    if start_table == target:
        return []

    visited = {start_table}
    queue = [(start_table, [])]

    while queue:
        current, path = queue.pop(0)
        if len(path) >= max_hops:
            continue

        for _, neighbor, edata in graph.out_edges(current, data=True):
            if neighbor in visited:
                continue
            new_path = path + [(current, edata["fk_column"], neighbor)]
            if neighbor == target:
                return new_path
            visited.add(neighbor)
            queue.append((neighbor, new_path))

    return None


def expand_from_seeds(graph, seed_tables, descriptions):
    """
    From seed tables, expand via the graph to build a full context package.

    Expansion rules:
      1. All direct FK neighbors (1-hop outgoing + incoming)
      2. Junction tables connecting any two focus tables
      3. Auth chain (trace to 'users' within 3 hops)

    Returns a context package dict.
    """
    focus = set(seed_tables)

    # 1-hop expansion
    for seed in list(focus):
        # Outgoing FKs
        for _, neighbor, _ in graph.out_edges(seed, data=True):
            focus.add(neighbor)
        # Incoming FKs
        for neighbor, _, _ in graph.in_edges(seed, data=True):
            focus.add(neighbor)

    # Junction detection: any table in focus that has junction pattern
    # and connects two other focus tables
    for table in list(focus):
        patterns = graph.nodes[table].get("patterns", [])
        if "junction_pure" in patterns or "junction_with_payload" in patterns:
            # Already in focus — good
            pass

    # Collect join paths
    join_paths = []
    for table in focus:
        for _, tgt, edata in graph.out_edges(table, data=True):
            if tgt in focus:
                join_paths.append({
                    "from": table,
                    "fk_column": edata["fk_column"],
                    "to": tgt,
                    "ref_column": edata["ref_column"],
                    "nullable": edata["nullable"],
                    "on_delete": edata["on_delete"],
                })

    # Auth linkage
    auth_linkage = None
    for seed in seed_tables:
        chain = _trace_auth_chain(graph, seed)
        if chain:
            auth_linkage = " → ".join(
                f"{t}.{fk} → {ref}" for t, fk, ref in chain
            )
            # Add users to focus if found
            focus.add("users")
            break

    # Collect patterns per table
    patterns = {}
    for t in focus:
        p = graph.nodes[t].get("patterns", [])
        if p:
            patterns[t] = p

    # Generation hints
    hints = []
    for t in focus:
        p = graph.nodes[t].get("patterns", [])
        if "soft_delete" in p:
            hints.append(f"Apply soft-delete filter on {t} (WHERE {t}.deleted_at IS NULL)")
        if "junction_with_payload" in p:
            hints.append(f"{t} is a junction table with payload columns")
        if "junction_pure" in p:
            hints.append(f"{t} is a pure junction/linking table")

    # Table descriptions
    table_descriptions = {}
    for t in focus:
        info = descriptions.get(t, {})
        table_descriptions[t] = info.get("description", f"{t} table")

    # Column details for focus tables
    table_columns = {}
    for t in focus:
        table_columns[t] = graph.nodes[t]["columns"]

    return {
        "seed_tables": list(seed_tables),
        "focus_tables": sorted(focus),
        "join_paths": join_paths,
        "auth_linkage": auth_linkage,
        "patterns": patterns,
        "hints": hints,
        "descriptions": table_descriptions,
        "columns": table_columns,
    }


def find(query, graph, embeddings, descriptions, top_k=2):
    """
    Full retrieval pipeline: semantic seed → graph expand → context package.
    """
    seeds = find_seeds(query, embeddings, top_k=top_k)
    seed_names = [name for name, _ in seeds]
    context = expand_from_seeds(graph, seed_names, descriptions)
    context["seed_scores"] = {name: round(score, 4) for name, score in seeds}
    return context


def print_context(ctx):
    """Pretty-print a context package."""
    print("\n" + "=" * 60)
    print("RETRIEVAL CONTEXT")
    print("=" * 60)
    print(f"Seeds: {ctx['seed_tables']} (scores: {ctx.get('seed_scores', {})})")
    print(f"Focus tables: {ctx['focus_tables']}")
    print(f"\nJoin paths:")
    for jp in ctx["join_paths"]:
        print(f"  {jp['from']}.{jp['fk_column']} → {jp['to']}.{jp['ref_column']}")
    if ctx["auth_linkage"]:
        print(f"\nAuth linkage: {ctx['auth_linkage']}")
    if ctx["hints"]:
        print(f"\nGeneration hints:")
        for h in ctx["hints"]:
            print(f"  • {h}")
    print("=" * 60)


if __name__ == "__main__":
    from database import get_engine, create_schema, seed_data
    from graph_builder import build_graph
    from context_engine import generate_all_descriptions

    engine = create_schema()
    seed_data(engine)
    g = build_graph(engine)
    descs = generate_all_descriptions(g)
    embs = embed_tables(g, descs)

    # Test queries
    for q in ["purchase history", "product catalogue", "shipping status"]:
        print(f"\n🔍 Query: '{q}'")
        ctx = find(q, g, embs, descs)
        print_context(ctx)
