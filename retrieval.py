"""
retrieval.py — Two-phase retrieval: hybrid semantic/lexical seeding + graph expansion.

Phase 1: HybridRetriever
  - BM25 (sparse lexical): exact column/table name matching
  - Sentence-transformer embeddings (dense semantic): meaning-based matching
  - Reciprocal Rank Fusion (RRF, k=60): merges both ranked lists
  - Adaptive K: auto-selects seed count from score distribution + schema size

Phase 2: Graph expansion
  - Walk the NetworkX graph from seeds to assemble a full context package
    with join paths, auth linkage, and pattern hints.
"""

import math
import numpy as np
from rank_bm25 import BM25Okapi
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


# ── Utilities ───────────────────────────────────────────────────────────────

def cosine_sim(a, b):
    """Cosine similarity between two numpy vectors."""
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(dot / norm) if norm > 0 else 0.0


def build_bm25_doc(table_name: str, description: str, columns: list) -> str:
    """
    Construct a BM25-optimized document for one table.
    Table name and column names are repeated 2x to boost their term frequency,
    so exact-match queries like 'user_id' or 'orders' surface the right table.
    """
    col_names = " ".join(c["name"] for c in columns)
    return f"{table_name} {table_name} {description} {col_names} {col_names}"


# ── Adaptive K ──────────────────────────────────────────────────────────────

def compute_adaptive_k(table_count: int, scores: list, gap_threshold: float = 0.10) -> int:
    """
    Determine the optimal number of seed tables (K) using two signals:
      1. Schema-scaled ceiling: max_k = clamp(log2(table_count), 2, 6)
      2. Score-gap detection: stop adding seeds when the drop from the
         previous score exceeds gap_threshold.

    Args:
        table_count:    Total number of tables in the schema.
        scores:         Sorted list of (table_name, score) tuples, descending.
        gap_threshold:  Score drop that signals a natural cut point.

    Returns:
        K (int), always in [1, max_k].

    Schema-to-max_k mapping (approximate):
        20  tables → max_k = 4
        50  tables → max_k = 5
        100 tables → max_k = 6
        200 tables → max_k = 6  (capped)
    """
    max_k = max(2, min(6, round(math.log2(max(table_count, 2)))))
    min_k = 1

    if len(scores) <= min_k:
        return min_k

    k = min_k
    for i in range(min_k, min(max_k, len(scores))):
        gap = scores[i - 1][1] - scores[i][1]
        if gap > gap_threshold:
            break
        k += 1

    return k


# ── Hybrid Retriever ────────────────────────────────────────────────────────

class HybridRetriever:
    """
    Combines BM25 lexical ranking and dense embedding ranking via
    Reciprocal Rank Fusion (RRF). Selects seed count adaptively.

    Build once per DatabaseProject, rebuild on schema refresh.
    """

    def __init__(self, graph, descriptions: dict, embeddings: dict):
        self.table_names = sorted(embeddings.keys())
        self.embeddings = embeddings

        # Build BM25 index
        docs = []
        for t in self.table_names:
            desc = descriptions.get(t, {}).get("description", "")
            cols = graph.nodes[t]["columns"]
            doc = build_bm25_doc(t, desc, cols)
            docs.append(doc.lower().split())
        
        if docs:
            self.bm25 = BM25Okapi(docs)
        else:
            self.bm25 = None

    def find_seeds(self, query: str, table_count: int) -> list:
        """
        Run hybrid retrieval and return top-K (table_name, rrf_score) tuples.
        K is selected adaptively based on score distribution and schema size.
        """
        tokens = query.lower().split()

        # BM25 ranking
        if self.bm25:
            bm25_raw = self.bm25.get_scores(tokens)
            bm25_ranking = [self.table_names[i] for i in np.argsort(bm25_raw)[::-1]]
        else:
            bm25_ranking = []

        # Embedding ranking
        query_vec = embed_text(query)
        emb_scores = [
            (t, cosine_sim(query_vec, self.embeddings[t]))
            for t in self.table_names
        ]
        emb_scores.sort(key=lambda x: x[1], reverse=True)
        emb_ranking = [t for t, _ in emb_scores]
        emb_score_map = {t: s for t, s in emb_scores}

        # Reciprocal Rank Fusion (k=60, Cormack et al. 2009)
        rrf: dict[str, float] = {}
        for rank, t in enumerate(bm25_ranking):
            rrf[t] = rrf.get(t, 0.0) + 1.0 / (60 + rank + 1)
        for rank, t in enumerate(emb_ranking):
            rrf[t] = rrf.get(t, 0.0) + 1.0 / (60 + rank + 1)

        fused = sorted(rrf.items(), key=lambda x: x[1], reverse=True)

        # Adaptive K — use embedding scores for gap detection (0–1 scale, stable)
        fused_with_emb = [(t, emb_score_map.get(t, 0.0)) for t, _ in fused]
        k = compute_adaptive_k(table_count, fused_with_emb)

        return fused[:k]


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
        for _, neighbor, _ in graph.out_edges(seed, data=True):
            focus.add(neighbor)
        for neighbor, _, _ in graph.in_edges(seed, data=True):
            focus.add(neighbor)

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
            focus.add("users")
            break

    # Patterns per table
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

    # Column details
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


# ── Public API ──────────────────────────────────────────────────────────────

def find(query: str, graph, descriptions: dict, retriever: HybridRetriever) -> dict:
    """
    Full retrieval pipeline: hybrid seed → graph expand → context package.

    Args:
        query:      Natural language query from the user.
        graph:      NetworkX DiGraph of the schema.
        descriptions: {table_name: {description, business_role}} from context_engine.
        retriever:  HybridRetriever built for the active DatabaseProject.

    Returns:
        Context package dict with seed_tables, focus_tables, join_paths, etc.
    """
    table_count = len(graph.nodes)
    seeds = retriever.find_seeds(query, table_count)
    seed_names = [name for name, _ in seeds]

    context = expand_from_seeds(graph, seed_names, descriptions)
    context["seed_scores"] = {name: round(score, 5) for name, score in seeds}
    context["retrieval_k"] = len(seeds)

    return context


def print_context(ctx: dict):
    """Pretty-print a context package."""
    k = ctx.get("retrieval_k", len(ctx["seed_tables"]))
    print("\n" + "=" * 60)
    print("RETRIEVAL CONTEXT")
    print("=" * 60)
    print(f"Seeds (K={k}, adaptive, hybrid):")
    for name, score in ctx.get("seed_scores", {}).items():
        marker = " ← seed" if name in ctx["seed_tables"] else ""
        print(f"  {name}  rrf={score:.5f}{marker}")
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
