"""
run_query_benchmark.py — Benchmark the full query pipeline (retrieval + SQL generation)
against the 101-table Postgres schema with 25 diverse queries.

Collects per-query:
  - Retrieval: K (adaptive), seed tables, focus table count, join path count, auth chain
  - SQL Agent: prompt_tokens, completion_tokens, latency_ms, finish_reason, prompt_chars
  - Quality: tables_used in SQL, warnings count

Saves everything to tests/query_metrics.json.

Run from project root:
    python tests/run_query_benchmark.py

Prerequisite: run tests/run_pipeline.py first to build the cache.
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

TEST_DB_URL = (
    POSTGRES_URL
    if POSTGRES_URL.rstrip("/").endswith("/postgres")
    else POSTGRES_URL.rstrip("/") + "/postgres"
)

CACHE_DIR  = os.path.join(ROOT, "databases", "scale_test")
CACHE_PATH = os.path.join(CACHE_DIR, "schema_cache.json")
METRICS_OUT = os.path.join(ROOT, "tests", "query_metrics.json")

# ── Imports ────────────────────────────────────────────────────────────────
from sqlalchemy import create_engine
from graph_builder import build_graph
from cache import load_cache, compute_all_hashes, decode_embedding
from retrieval import HybridRetriever, find
from agents.sql_agent import SQLQueryAgent


# ══════════════════════════════════════════════════════════════════════════
# SETUP — Load everything from cache (should be instant)
# ══════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
print("  Query Pipeline Benchmark — 25 queries × 101 tables")
print("═" * 60)

print("\n  Loading from cache...")
t0 = time.perf_counter()

engine = create_engine(TEST_DB_URL, echo=False)
graph  = build_graph(engine)
cache  = load_cache(CACHE_PATH)

descriptions = cache.get("descriptions", {})
embeddings   = {}
for t, b64 in cache.get("embeddings", {}).items():
    if t in graph.nodes:
        embeddings[t] = decode_embedding(b64)

retriever = HybridRetriever(graph, descriptions, embeddings)
agent     = SQLQueryAgent()

node_count = graph.number_of_nodes()
edge_count = graph.number_of_edges()
elapsed = time.perf_counter() - t0

print(f"  Graph: {node_count} tables, {edge_count} FK edges")
print(f"  Embeddings: {len(embeddings)} vectors loaded")
print(f"  BM25 index: built")
print(f"  Cache load time: {elapsed:.2f}s")

# ══════════════════════════════════════════════════════════════════════════
# QUERIES — 25 diverse queries spanning all domain clusters
# ══════════════════════════════════════════════════════════════════════════
QUERIES = [
    # ── Identity & Auth ──
    "show all users and their profiles",
    "list API keys created by a user with their scopes",
    "find users who have two-factor authentication enabled",

    # ── Organizations ──
    "list all organizations and their departments",
    "show members of an organization with their roles",
    "audit log entries for a specific organization in the last 30 days",

    # ── Projects & Tasks ──
    "tasks assigned to a user in a project with their labels",
    "show all milestones and their tasks for a project",
    "time logged by a user on tasks this week",
    "checklist items for a specific task",
    "custom field values set on tasks",

    # ── Documents & Collaboration ──
    "documents in a project folder with version history",
    "comments on a task ordered by creation date",
    "messages in a channel sent by a specific user",

    # ── Notifications ──
    "unread notifications for a user",

    # ── Billing & Subscriptions ──
    "invoices for an organization with line items and payments",
    "active subscriptions and their plans with feature limits",
    "usage records for a subscription in the current billing period",

    # ── Integrations ──
    "webhook deliveries that failed for a specific webhook",
    "OAuth tokens that are about to expire",

    # ── Analytics ──
    "feature usage statistics for an organization",
    "report schedules and their last run status",

    # ── Support ──
    "open support tickets assigned to an agent with their messages",
    "support tickets tagged with a specific tag",

    # ── Marketing & Affiliates ──
    "campaign performance with leads and their activities",
]


# ══════════════════════════════════════════════════════════════════════════
# RUN — Execute each query through retrieval + SQL agent
# ══════════════════════════════════════════════════════════════════════════
print(f"\n  Running {len(QUERIES)} queries...\n")

results = []

for i, query in enumerate(QUERIES):
    print(f"  [{i+1:2d}/{len(QUERIES)}] {query[:60]}...")

    # Phase 1+2: Retrieval
    t_ret = time.perf_counter()
    context = find(query, graph, descriptions, retriever)
    retrieval_ms = round((time.perf_counter() - t_ret) * 1000, 1)

    seed_tables  = context.get("seed_tables", [])
    focus_tables = context.get("focus_tables", [])
    join_paths   = context.get("join_paths", [])
    auth_linkage = context.get("auth_linkage")
    retrieval_k  = context.get("retrieval_k", len(seed_tables))
    seed_scores  = context.get("seed_scores", {})

    # Phase 3: SQL generation
    context["dialect"] = "postgresql"
    result = agent.ask(query, context)

    usage = result.get("_usage", {})
    sql_query   = result.get("sql_query", "")
    tables_used = result.get("tables_used", [])
    warnings    = result.get("warnings", [])
    explanation = result.get("explanation", "")
    is_error    = "_error" in result

    record = {
        "query":               query,
        "query_index":         i + 1,

        # Retrieval metrics
        "retrieval_k":         retrieval_k,
        "seed_tables":         seed_tables,
        "seed_count":          len(seed_tables),
        "seed_scores":         seed_scores,
        "focus_table_count":   len(focus_tables),
        "focus_tables":        focus_tables,
        "join_path_count":     len(join_paths),
        "has_auth_chain":      auth_linkage is not None,
        "retrieval_latency_ms": retrieval_ms,

        # SQL Agent metrics
        "prompt_tokens":       usage.get("prompt_tokens", 0),
        "completion_tokens":   usage.get("completion_tokens", 0),
        "total_tokens":        usage.get("total_tokens", 0),
        "finish_reason":       usage.get("finish_reason", ""),
        "agent_latency_ms":    usage.get("latency_ms", 0.0),
        "prompt_chars":        usage.get("prompt_chars", 0),

        # Output quality
        "tables_used_in_sql":  tables_used,
        "tables_used_count":   len(tables_used),
        "warning_count":       len(warnings),
        "warnings":            warnings,
        "is_error":            is_error,
        "sql_length":          len(sql_query),
    }

    results.append(record)

    # Brief output
    status = "✓" if not is_error else "✗"
    print(f"         {status}  K={retrieval_k}  seeds={seed_tables[:3]}  "
          f"focus={len(focus_tables)}  tokens={usage.get('total_tokens',0)}  "
          f"latency={usage.get('latency_ms',0):.0f}ms")

    # Small sleep to avoid rate limiting
    if i < len(QUERIES) - 1:
        time.sleep(0.3)


# ══════════════════════════════════════════════════════════════════════════
# AGGREGATE — Compute summary statistics
# ══════════════════════════════════════════════════════════════════════════
print(f"\n{'═'*60}")
print("  Computing aggregate statistics...")

total_queries = len(results)
success_count = sum(1 for r in results if not r["is_error"])

# Token stats
prompt_toks   = [r["prompt_tokens"]     for r in results if not r["is_error"]]
compl_toks    = [r["completion_tokens"]  for r in results if not r["is_error"]]
total_toks    = [r["total_tokens"]       for r in results if not r["is_error"]]

# Latency stats
agent_lats    = [r["agent_latency_ms"]   for r in results if not r["is_error"]]
ret_lats      = [r["retrieval_latency_ms"] for r in results]
lat_sorted    = sorted(agent_lats)
p95_idx       = max(0, int(len(lat_sorted) * 0.95) - 1)

# Retrieval stats
k_values      = [r["retrieval_k"]        for r in results]
seed_counts   = [r["seed_count"]         for r in results]
focus_counts  = [r["focus_table_count"]  for r in results]
join_counts   = [r["join_path_count"]    for r in results]
prompt_chars  = [r["prompt_chars"]       for r in results if not r["is_error"]]
auth_count    = sum(1 for r in results if r["has_auth_chain"])

# Finish reason breakdown
finish_reasons = {}
for r in results:
    fr = r["finish_reason"]
    finish_reasons[fr] = finish_reasons.get(fr, 0) + 1

# K distribution
k_dist = {}
for k in k_values:
    k_dist[str(k)] = k_dist.get(str(k), 0) + 1

def safe_stats(arr):
    if not arr:
        return {"min": 0, "max": 0, "mean": 0, "median": 0, "stdev": 0}
    return {
        "min":    round(min(arr), 1),
        "max":    round(max(arr), 1),
        "mean":   round(statistics.mean(arr), 1),
        "median": round(statistics.median(arr), 1),
        "stdev":  round(statistics.stdev(arr), 1) if len(arr) > 1 else 0.0,
    }

summary = {
    "total_queries":           total_queries,
    "success_count":           success_count,
    "error_count":             total_queries - success_count,

    # Token totals
    "total_prompt_tokens":     sum(prompt_toks),
    "total_completion_tokens": sum(compl_toks),
    "total_tokens":            sum(total_toks),

    # Per-query token stats
    "prompt_tokens_per_query":     safe_stats(prompt_toks),
    "completion_tokens_per_query": safe_stats(compl_toks),
    "total_tokens_per_query":      safe_stats(total_toks),

    # Latency
    "agent_latency_ms":            {**safe_stats(agent_lats), "p95": round(lat_sorted[p95_idx], 1) if lat_sorted else 0},
    "retrieval_latency_ms":        safe_stats(ret_lats),

    # Prompt size
    "prompt_chars_per_query":      safe_stats(prompt_chars),

    # Retrieval quality
    "adaptive_k_distribution":     k_dist,
    "seed_count_per_query":        safe_stats(seed_counts),
    "focus_tables_per_query":      safe_stats(focus_counts),
    "join_paths_per_query":        safe_stats(join_counts),
    "auth_chain_hit_rate":         round(auth_count / max(total_queries, 1) * 100, 1),

    # Finish reasons
    "finish_reason_breakdown":     finish_reasons,

    # Correlation: focus_tables → prompt_tokens
    "focus_to_tokens_ratio":       round(sum(total_toks) / max(sum(focus_counts), 1), 1),
}

report = {
    "summary":   summary,
    "per_query": results,
}

with open(METRICS_OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2)


# ══════════════════════════════════════════════════════════════════════════
# PRINT REPORT
# ══════════════════════════════════════════════════════════════════════════
print(f"\n{'─'*60}")
print(f"  QUERY PIPELINE BENCHMARK — RESULTS")
print(f"{'─'*60}")

print(f"\n  Queries:  {success_count} succeeded  /  {total_queries} total")

print(f"\n  ── Token Usage ──")
print(f"  Total prompt tokens:       {sum(prompt_toks):,}")
print(f"  Total completion tokens:   {sum(compl_toks):,}")
print(f"  Total tokens:              {sum(total_toks):,}")
print(f"  Prompt tokens / query:     min={summary['prompt_tokens_per_query']['min']}  "
      f"mean={summary['prompt_tokens_per_query']['mean']}  "
      f"max={summary['prompt_tokens_per_query']['max']}")
print(f"  Completion tokens / query: min={summary['completion_tokens_per_query']['min']}  "
      f"mean={summary['completion_tokens_per_query']['mean']}  "
      f"max={summary['completion_tokens_per_query']['max']}")

print(f"\n  ── Latency ──")
print(f"  Agent latency (ms):    min={summary['agent_latency_ms']['min']}  "
      f"mean={summary['agent_latency_ms']['mean']}  "
      f"p95={summary['agent_latency_ms']['p95']}  "
      f"max={summary['agent_latency_ms']['max']}")
print(f"  Retrieval latency (ms): min={summary['retrieval_latency_ms']['min']}  "
      f"mean={summary['retrieval_latency_ms']['mean']}  "
      f"max={summary['retrieval_latency_ms']['max']}")

print(f"\n  ── Retrieval Quality ──")
print(f"  Adaptive K distribution:  {k_dist}")
print(f"  Seeds per query:          min={summary['seed_count_per_query']['min']}  "
      f"mean={summary['seed_count_per_query']['mean']}  "
      f"max={summary['seed_count_per_query']['max']}")
print(f"  Focus tables per query:   min={summary['focus_tables_per_query']['min']}  "
      f"mean={summary['focus_tables_per_query']['mean']}  "
      f"max={summary['focus_tables_per_query']['max']}")
print(f"  Join paths per query:     min={summary['join_paths_per_query']['min']}  "
      f"mean={summary['join_paths_per_query']['mean']}  "
      f"max={summary['join_paths_per_query']['max']}")
print(f"  Auth chain hit rate:      {summary['auth_chain_hit_rate']}%")

print(f"\n  ── Prompt Efficiency ──")
print(f"  Prompt chars / query:     min={summary['prompt_chars_per_query']['min']}  "
      f"mean={summary['prompt_chars_per_query']['mean']}  "
      f"max={summary['prompt_chars_per_query']['max']}")
print(f"  Tokens per focus table:   {summary['focus_to_tokens_ratio']}")
print(f"  Finish reasons:           {finish_reasons}")

print(f"\n  Saved to: {METRICS_OUT}")
print(f"{'═'*60}\n")
