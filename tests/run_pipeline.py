"""
run_pipeline.py — Runs the full production pipeline against the 101-table
Postgres database and opens the interactive schema graph in the browser.

Mirrors exactly what main.py does when you type:
  use <db>        — load + build graph, descriptions, embeddings
  tables          — print all tables with AI descriptions
  graph           — print the schema graph text
  visualize       — open the pyvis HTML graph in browser

Run from project root:
    python tests/run_pipeline.py

The script connects directly to the postgres DB (POSTGRES_URL from .env).
All generated cache is saved to databases/scale_test/schema_cache.json so
subsequent runs skip re-describing unchanged tables (the incremental cache
works exactly as in production).
"""

import os
import sys
import time
import json
import statistics

# ── Path setup ─────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

POSTGRES_URL = os.getenv("POSTGRES_URL")
if not POSTGRES_URL:
    print("ERROR: POSTGRES_URL not found in .env")
    sys.exit(1)

# The generate_db.py created tables in the default 'postgres' database
TEST_DB_URL = (
    POSTGRES_URL
    if POSTGRES_URL.rstrip("/").endswith("/postgres")
    else POSTGRES_URL.rstrip("/") + "/postgres"
)

# Cache is stored alongside other projects under databases/
CACHE_DIR  = os.path.join(ROOT, "databases", "scale_test")
CACHE_PATH = os.path.join(CACHE_DIR, "schema_cache.json")
os.makedirs(CACHE_DIR, exist_ok=True)

# Visualization output
VIZ_PATH = os.path.join(ROOT, "tests", "scale_test_graph.html")

# ── Imports ────────────────────────────────────────────────────────────────
from sqlalchemy import create_engine

from graph_builder import build_graph, print_graph
from cache import (
    compute_all_hashes,
    get_stale_tables,
    load_cache,
    save_cache,
    serialize_graph,
    encode_embedding,
    decode_embedding,
)
from context_engine import generate_all_descriptions
from retrieval import embed_text, embed_tables, HybridRetriever, find, print_context
from visualize import visualize_graph


# ── Step helpers ───────────────────────────────────────────────────────────

def banner(step, title):
    print(f"\n{'═'*60}")
    print(f"  Step {step} — {title}")
    print(f"{'═'*60}")


# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — Connect
# ══════════════════════════════════════════════════════════════════════════
banner(1, "Connecting to PostgreSQL")
t0 = time.perf_counter()
engine = create_engine(TEST_DB_URL, echo=False)
with engine.connect() as conn:
    pass
print(f"  Connected to: {TEST_DB_URL}")
print(f"  Elapsed: {(time.perf_counter()-t0)*1000:.0f}ms")


# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — Build Graph
# ══════════════════════════════════════════════════════════════════════════
banner(2, "Building Schema Graph (graph_builder)")
t0 = time.perf_counter()
graph = build_graph(engine)
elapsed = time.perf_counter() - t0

node_count = graph.number_of_nodes()
edge_count = graph.number_of_edges()
print(f"  Tables (nodes): {node_count}")
print(f"  FK edges:       {edge_count}")
print(f"  Elapsed:        {elapsed*1000:.0f}ms")

# Pattern breakdown
pattern_counts = {}
for _, data in graph.nodes(data=True):
    for p in data.get("patterns", []):
        pattern_counts[p] = pattern_counts.get(p, 0) + 1
print("\n  Detected patterns:")
for pat, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
    print(f"    {pat:<28} {count} tables")


# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — Hashing & Cache Diff
# ══════════════════════════════════════════════════════════════════════════
banner(3, "Hashing & Incremental Cache Check")
t0 = time.perf_counter()
cache          = load_cache(CACHE_PATH)
current_hashes = compute_all_hashes(graph)
cached_hashes  = cache.get("hashes", {})
stale          = get_stale_tables(current_hashes, cached_hashes)
stale_in_graph = stale & set(graph.nodes)
cached_count   = node_count - len(stale_in_graph)
elapsed = time.perf_counter() - t0

print(f"  Stale tables (need re-describing): {len(stale_in_graph)}")
print(f"  Cached tables (skip):              {cached_count}")
print(f"  Elapsed:                           {elapsed*1000:.1f}ms")
if stale_in_graph:
    print(f"\n  Stale tables:")
    for t in sorted(stale_in_graph):
        print(f"    - {t}")


# ══════════════════════════════════════════════════════════════════════════
# STEP 4 — AI Descriptions (context_engine)
# ══════════════════════════════════════════════════════════════════════════
banner(4, "Generating AI Descriptions (context_engine)")

if stale_in_graph:
    print(f"  Describing {len(stale_in_graph)} table(s) via Mistral-Nemotron (NVIDIA NIM)...")
    print(f"  This may take a few minutes for all 100+ tables on first run.\n")
    t0 = time.perf_counter()
    descriptions = generate_all_descriptions(
        graph,
        stale_tables=stale,
        cached_descriptions=cache.get("descriptions", {}),
    )
    elapsed = time.perf_counter() - t0
    print(f"\n  Done. {len(stale_in_graph)} descriptions generated in {elapsed:.1f}s")
    print(f"  Avg per table: {elapsed/max(len(stale_in_graph),1):.1f}s")
else:
    descriptions = cache.get("descriptions", {})
    print(f"  All {node_count} tables loaded from cache — no API calls needed.")


# ── API Metrics Collection (only for newly generated descriptions) ──────────
METRICS_PATH = os.path.join(ROOT, "tests", "api_metrics.json")

per_call = []
for table_name, desc in descriptions.items():
    usage = desc.get("_usage")
    if usage and stale_in_graph and table_name in stale_in_graph:
        per_call.append({
            "table":              table_name,
            "prompt_tokens":      usage["prompt_tokens"],
            "completion_tokens":  usage["completion_tokens"],
            "total_tokens":       usage["total_tokens"],
            "finish_reason":      usage["finish_reason"],
            "latency_ms":         usage["latency_ms"],
            "neighborhood_chars": usage["neighborhood_chars"],
            "description_chars":  usage["description_chars"],
            "business_role":      desc.get("business_role", ""),
            "is_fallback":        "_error" in desc,
        })

if per_call:
    latencies   = [m["latency_ms"] for m in per_call]
    lat_sorted  = sorted(latencies)
    total_calls = len(per_call)
    p95_idx     = max(0, int(total_calls * 0.95) - 1)

    finish_reasons = {}
    role_dist      = {}
    for m in per_call:
        finish_reasons[m["finish_reason"]] = finish_reasons.get(m["finish_reason"], 0) + 1
        role_dist[m["business_role"]]      = role_dist.get(m["business_role"], 0) + 1

    total_tokens      = sum(m["total_tokens"]      for m in per_call)
    total_prompt_tok  = sum(m["prompt_tokens"]     for m in per_call)
    total_compl_tok   = sum(m["completion_tokens"] for m in per_call)
    total_latency_s   = sum(latencies) / 1000

    metrics_report = {
        "summary": {
            "total_api_calls":         total_calls,
            "success_count":           total_calls - sum(1 for m in per_call if m["is_fallback"]),
            "fallback_count":          sum(1 for m in per_call if m["is_fallback"]),
            "total_prompt_tokens":     total_prompt_tok,
            "total_completion_tokens": total_compl_tok,
            "total_tokens":            total_tokens,
            "token_throughput_per_sec": round(total_tokens / max(total_latency_s, 0.001), 1),
            "latency_ms": {
                "min":    round(min(latencies), 1),
                "max":    round(max(latencies), 1),
                "mean":   round(statistics.mean(latencies), 1),
                "median": round(statistics.median(latencies), 1),
                "stdev":  round(statistics.stdev(latencies), 1) if total_calls > 1 else 0.0,
                "p95":    round(lat_sorted[p95_idx], 1),
            },
            "avg_neighborhood_chars": round(statistics.mean(m["neighborhood_chars"] for m in per_call), 1),
            "avg_description_chars":  round(statistics.mean(m["description_chars"]  for m in per_call if not m["is_fallback"]) if any(not m["is_fallback"] for m in per_call) else 0, 1),
            "finish_reason_breakdown":      finish_reasons,
            "business_role_distribution":   role_dist,
        },
        "per_table": sorted(per_call, key=lambda m: m["table"]),
    }

    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics_report, f, indent=2)

    print(f"\n  ── API Metrics Summary ──")
    print(f"  Calls:              {total_calls}  ({metrics_report['summary']['fallback_count']} fallback)")
    print(f"  Prompt tokens:      {total_prompt_tok:,}")
    print(f"  Completion tokens:  {total_compl_tok:,}")
    print(f"  Total tokens:       {total_tokens:,}")
    print(f"  Throughput:         {metrics_report['summary']['token_throughput_per_sec']:,} tok/s")
    print(f"  Latency (ms):       min={metrics_report['summary']['latency_ms']['min']}  "
          f"mean={metrics_report['summary']['latency_ms']['mean']}  "
          f"p95={metrics_report['summary']['latency_ms']['p95']}  "
          f"max={metrics_report['summary']['latency_ms']['max']}")
    print(f"  Finish reasons:     {finish_reasons}")
    print(f"  Role distribution:  {role_dist}")
    print(f"  Saved to:           {METRICS_PATH}")

# Strip _usage from descriptions before caching (keeps schema_cache.json clean)
for desc in descriptions.values():
    desc.pop("_usage", None)



# ══════════════════════════════════════════════════════════════════════════
# STEP 5 — Embeddings
# ══════════════════════════════════════════════════════════════════════════
banner(5, "Computing Embeddings (sentence-transformers)")

# Load cached embeddings for unchanged tables
embeddings = {}
for t, b64 in cache.get("embeddings", {}).items():
    if t not in stale and t in graph.nodes:
        embeddings[t] = decode_embedding(b64)

if stale_in_graph:
    print(f"  Embedding {len(stale_in_graph)} stale table(s)...")
    t0 = time.perf_counter()
    for table_name in sorted(stale_in_graph):
        desc_text = descriptions.get(table_name, {}).get("description", "")
        cols = ", ".join(c["name"] for c in graph.nodes[table_name]["columns"])
        embeddings[table_name] = embed_text(f"{table_name}: {desc_text}. Columns: {cols}")
    elapsed = time.perf_counter() - t0
    print(f"  Embedded {len(stale_in_graph)} tables in {elapsed:.2f}s")
else:
    print(f"  All {node_count} embeddings loaded from cache.")

print(f"  Total embeddings in memory: {len(embeddings)}")


# ══════════════════════════════════════════════════════════════════════════
# STEP 6 — Save Cache
# ══════════════════════════════════════════════════════════════════════════
banner(6, "Saving Cache")
t0 = time.perf_counter()
save_cache(
    {
        "hashes":       current_hashes,
        "descriptions": descriptions,
        "embeddings":   {t: encode_embedding(v) for t, v in embeddings.items()},
        "graph":        serialize_graph(graph),
    },
    CACHE_PATH,
)
elapsed = time.perf_counter() - t0
print(f"  Cache saved to: {CACHE_PATH}")
print(f"  Elapsed: {elapsed*1000:.1f}ms")


# ══════════════════════════════════════════════════════════════════════════
# STEP 7 — Build Hybrid Retriever
# ══════════════════════════════════════════════════════════════════════════
banner(7, "Building Hybrid Retriever (BM25 + Semantic)")
t0 = time.perf_counter()
retriever = HybridRetriever(graph, descriptions, embeddings)
elapsed = time.perf_counter() - t0
print(f"  BM25 index built over {node_count} tables.")
print(f"  Elapsed: {elapsed*1000:.1f}ms")


# ══════════════════════════════════════════════════════════════════════════
# STEP 8 — Print Tables (like 'tables' command)
# ══════════════════════════════════════════════════════════════════════════
banner(8, "Table List with AI Descriptions")
for t in sorted(graph.nodes):
    desc = descriptions.get(t, {})
    desc_text = desc.get("description", "(no description)") if isinstance(desc, dict) else "(no description)"
    role = desc.get("business_role", "") if isinstance(desc, dict) else ""
    role_str = f" [{role}]" if role else ""
    print(f"  {t}{role_str}")
    print(f"    {desc_text}")


# ══════════════════════════════════════════════════════════════════════════
# STEP 9 — Print Schema Graph (like 'graph' command)
# ══════════════════════════════════════════════════════════════════════════
banner(9, "Schema Graph (text)")
print_graph(graph)


# ══════════════════════════════════════════════════════════════════════════
# STEP 10 — Test Retrieval Queries (like 'agent sql' queries)
# ══════════════════════════════════════════════════════════════════════════
banner(10, "Retrieval Test — Sample Queries")

test_queries = [
    "show all users in an organization",
    "list active subscriptions and their plans",
    "tasks assigned to a user in a project",
    "invoices and payment methods for billing",
    "affiliate referral tracking and payouts",
    "who is responsible for a deliverable",
    "api_key_scopes for an organization",
    "support tickets assigned to an agent",
]

for query in test_queries:
    ctx = find(query, graph, descriptions, retriever)
    print(f"\n  Query: \"{query}\"")
    print_context(ctx)


# ══════════════════════════════════════════════════════════════════════════
# STEP 11 — Visualize (like 'visualize' command)
# ══════════════════════════════════════════════════════════════════════════
banner(11, "Visualizing Schema Graph")
print(f"  Generating pyvis HTML graph ({node_count} nodes, {edge_count} edges)...")
t0 = time.perf_counter()
out = visualize_graph(
    graph,
    descriptions,
    output_path=VIZ_PATH,
    open_browser=True,
)
elapsed = time.perf_counter() - t0
print(f"  Output: {out}")
print(f"  Elapsed: {elapsed*1000:.0f}ms")

print(f"\n{'═'*60}")
print(f"  Pipeline complete. {node_count} tables, {edge_count} FK edges.")
print(f"  Graph saved to: {VIZ_PATH}")
print(f"{'═'*60}\n")
