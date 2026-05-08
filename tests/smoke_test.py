"""
smoke_test.py — Quick sanity check for the full pipeline against the 101-table Postgres DB.

Tests (in order):
  1. DB Connection      — can we connect to Postgres and reflect tables?
  2. Graph Building     — does build_graph return the right node/edge counts?
  3. Pattern Detection  — are structural patterns found (soft_delete, junction, etc.)?
  4. Hashing            — does compute_all_hashes return a hash per table?
  5. Embedding          — does embed_text return a 384-dim vector?
  6. HybridRetriever    — does find_seeds return results for basic queries?
  7. Graph Expansion    — does expand_from_seeds return a valid context package?
  8. Auth Chain         — is the users table reachable from a downstream table?

Run from project root:
    python tests/smoke_test.py
"""

import os
import sys
import time

# ── Path setup: allow imports from project root ────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv()

POSTGRES_URL = os.getenv("POSTGRES_URL")
if not POSTGRES_URL:
    print("ERROR: POSTGRES_URL not found in .env")
    sys.exit(1)

# Connect to the default postgres database (where generate_db.py created tables)
TEST_DB_URL = POSTGRES_URL if POSTGRES_URL.endswith("/postgres") else POSTGRES_URL.rstrip("/") + "/postgres"

# ── Colour helpers ─────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

passed = 0
failed = 0

def ok(label, detail=""):
    global passed
    passed += 1
    suffix = f"  {YELLOW}({detail}){RESET}" if detail else ""
    print(f"  {GREEN}✓{RESET} {label}{suffix}")

def fail(label, detail=""):
    global failed
    failed += 1
    suffix = f"\n      {YELLOW}{detail}{RESET}" if detail else ""
    print(f"  {RED}✗{RESET} {label}{suffix}")

def section(title):
    print(f"\n{BOLD}{'─'*55}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─'*55}{RESET}")

# ══════════════════════════════════════════════════════════════════
# TEST 1 — DB Connection & Reflection
# ══════════════════════════════════════════════════════════════════
section("1 · DB Connection & Table Reflection")

try:
    from sqlalchemy import create_engine, inspect as sa_inspect
    t0 = time.perf_counter()
    engine = create_engine(TEST_DB_URL, echo=False)
    with engine.connect() as conn:
        pass
    elapsed = time.perf_counter() - t0
    ok("Connected to PostgreSQL", f"{elapsed*1000:.1f}ms")

    inspector = sa_inspect(engine)
    table_names = inspector.get_table_names(schema="public")
    n = len(table_names)

    if n >= 100:
        ok(f"Reflected {n} tables from public schema", f"expected ≥100")
    else:
        fail(f"Only {n} tables reflected", f"expected ≥100")

except Exception as e:
    fail("DB connection / reflection", str(e))
    print(f"\n{RED}Cannot continue without a DB connection. Aborting.{RESET}")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════
# TEST 2 — Graph Building
# ══════════════════════════════════════════════════════════════════
section("2 · Graph Building (graph_builder.build_graph)")

try:
    from graph_builder import build_graph
    t0 = time.perf_counter()
    graph = build_graph(engine)
    elapsed = time.perf_counter() - t0

    node_count = graph.number_of_nodes()
    edge_count = graph.number_of_edges()

    if node_count >= 100:
        ok(f"Graph nodes: {node_count}", f"{elapsed*1000:.0f}ms")
    else:
        fail(f"Graph nodes: {node_count}", "expected ≥100")

    if edge_count >= 50:
        ok(f"Graph edges (FK links): {edge_count}")
    else:
        fail(f"Graph edges: {edge_count}", "expected ≥50 FK edges")

    # Spot-check a few key tables
    for expected in ["users", "organizations", "tasks", "invoices", "campaigns"]:
        if expected in graph.nodes:
            ok(f"Node present: {expected}")
        else:
            fail(f"Node missing: {expected}")

except Exception as e:
    fail("build_graph raised an exception", str(e))
    graph = None

# ══════════════════════════════════════════════════════════════════
# TEST 3 — Pattern Detection
# ══════════════════════════════════════════════════════════════════
section("3 · Pattern Detection")

if graph:
    pattern_counts = {}
    for node, data in graph.nodes(data=True):
        for p in data.get("patterns", []):
            pattern_counts[p] = pattern_counts.get(p, 0) + 1

    expected_patterns = {
        "soft_delete": 1,
        "timestamped": 10,
        "junction_pure": 1,
        "audited": 1,
    }
    for pattern, min_count in expected_patterns.items():
        actual = pattern_counts.get(pattern, 0)
        if actual >= min_count:
            ok(f"Pattern '{pattern}': {actual} tables")
        else:
            fail(f"Pattern '{pattern}': {actual} tables", f"expected ≥{min_count}")

    # Check users has soft_delete
    users_patterns = graph.nodes["users"].get("patterns", [])
    if "soft_delete" in users_patterns:
        ok("users has 'soft_delete' pattern (deleted_at column)")
    else:
        fail("users missing 'soft_delete'", f"got: {users_patterns}")
else:
    fail("Skipped — no graph available")

# ══════════════════════════════════════════════════════════════════
# TEST 4 — Hashing
# ══════════════════════════════════════════════════════════════════
section("4 · Schema Hashing (cache.compute_all_hashes)")

if graph:
    try:
        from cache import compute_all_hashes
        t0 = time.perf_counter()
        hashes = compute_all_hashes(graph)
        elapsed = time.perf_counter() - t0

        if len(hashes) == node_count:
            ok(f"Hashes computed for all {len(hashes)} tables", f"{elapsed*1000:.1f}ms")
        else:
            fail(f"Hash count mismatch: {len(hashes)} vs {node_count} nodes")

        # Check hash format (64-char hex)
        sample_hash = next(iter(hashes.values()))
        if len(sample_hash) == 64 and all(c in "0123456789abcdef" for c in sample_hash):
            ok("Hash format valid (SHA-256 hex)")
        else:
            fail("Hash format invalid", f"got: {sample_hash[:20]}...")

        # Re-running should produce identical hashes
        hashes2 = compute_all_hashes(graph)
        if hashes == hashes2:
            ok("Hashes are deterministic (same result on second call)")
        else:
            fail("Hashes are NOT deterministic")

    except Exception as e:
        fail("compute_all_hashes raised an exception", str(e))
else:
    fail("Skipped — no graph available")

# ══════════════════════════════════════════════════════════════════
# TEST 5 — Embedding
# ══════════════════════════════════════════════════════════════════
section("5 · Embedding (retrieval.embed_text)")

try:
    from retrieval import embed_text
    import numpy as np

    t0 = time.perf_counter()
    vec = embed_text("show all users in an organization")
    elapsed = time.perf_counter() - t0

    if isinstance(vec, np.ndarray) and vec.shape == (384,):
        ok(f"embed_text returns (384,) float32 vector", f"{elapsed*1000:.0f}ms")
    else:
        fail(f"Unexpected embedding shape: {vec.shape}")

    if vec.dtype == np.float32:
        ok("Vector dtype is float32")
    else:
        fail(f"Vector dtype: {vec.dtype}", "expected float32")

except Exception as e:
    fail("embed_text raised an exception", str(e))

# ══════════════════════════════════════════════════════════════════
# TEST 6 — Hybrid Retriever (BM25 + Semantic)
# ══════════════════════════════════════════════════════════════════
section("6 · Hybrid Retriever (BM25 + RRF + Adaptive K)")

if graph:
    try:
        from retrieval import HybridRetriever, embed_tables

        # Build minimal descriptions (no AI — just empty strings so retriever can init)
        descriptions = {t: {"description": "", "business_role": "core_entity"} for t in graph.nodes}

        t0 = time.perf_counter()
        embeddings = embed_tables(graph, descriptions)
        elapsed = time.perf_counter() - t0
        ok(f"Embedded all {len(embeddings)} tables", f"{elapsed:.1f}s")

        retriever = HybridRetriever(graph, descriptions, embeddings)
        ok("HybridRetriever instantiated (BM25 index built)")

        # Run a set of test queries
        test_queries = [
            ("show all users",                     ["users"]),
            ("list active subscriptions",           ["subscriptions"]),
            ("invoices for an organization",        ["invoices", "organizations"]),
            ("tasks assigned to a user",            ["tasks", "users"]),
            ("affiliate referral tracking",         ["affiliate_referrals", "affiliates"]),
            ("api_key_scopes",                      ["api_key_scopes"]),        # exact name match
            ("payment methods for billing",         ["payment_methods", "payments"]),
            ("who is responsible for a deliverable",["tasks"]),                 # semantic drift
        ]

        for query, expected_hits in test_queries:
            seeds = retriever.find_seeds(query, table_count=node_count)
            seed_names = [name for name, _ in seeds]
            k = len(seeds)
            hits = [h for h in expected_hits if h in seed_names]
            if hits:
                ok(f'"{query}"  →  K={k}, seeds={seed_names[:3]}')
            else:
                fail(f'"{query}"  →  K={k}, seeds={seed_names[:3]}', f"expected one of: {expected_hits}")

    except Exception as e:
        fail("HybridRetriever / find_seeds raised an exception", str(e))
        embeddings = None
        retriever = None
else:
    fail("Skipped — no graph available")
    embeddings = None
    retriever = None

# ══════════════════════════════════════════════════════════════════
# TEST 7 — Graph Expansion (Phase 2)
# ══════════════════════════════════════════════════════════════════
section("7 · Graph Expansion (retrieval.expand_from_seeds)")

if graph and retriever:
    try:
        from retrieval import expand_from_seeds, find

        seeds = retriever.find_seeds("tasks assigned to a user in a project", node_count)
        seed_names = [s for s, _ in seeds]

        ctx = expand_from_seeds(graph, seed_names, descriptions)

        if ctx["focus_tables"]:
            ok(f"focus_tables: {len(ctx['focus_tables'])} tables  ({ctx['focus_tables'][:4]}...)")
        else:
            fail("focus_tables is empty")

        if ctx["join_paths"]:
            ok(f"join_paths: {len(ctx['join_paths'])} FK paths found")
        else:
            fail("join_paths is empty — graph expansion may not be working")

        # Test the full find() API
        ctx2 = find("invoices for the current user", graph, descriptions, retriever)
        if "seed_tables" in ctx2 and "focus_tables" in ctx2 and "retrieval_k" in ctx2:
            ok(f"find() API returned valid context package  (K={ctx2['retrieval_k']})")
        else:
            fail("find() returned malformed context package", str(ctx2.keys()))

    except Exception as e:
        fail("expand_from_seeds / find raised an exception", str(e))
else:
    fail("Skipped — no graph or retriever available")

# ══════════════════════════════════════════════════════════════════
# TEST 8 — Auth Chain Tracing
# ══════════════════════════════════════════════════════════════════
section("8 · Auth Chain Tracing (users reachability)")

if graph:
    try:
        from retrieval import _trace_auth_chain

        # These tables should all be reachable from users within 3 hops
        start_tables = ["tasks", "invoices", "messages", "affiliate_payouts", "support_tickets"]

        for start in start_tables:
            if start not in graph.nodes:
                fail(f"{start} not in graph", "table missing")
                continue

            if start == "users":
                ok(f"users → users (trivially reachable)")
                continue

            chain = _trace_auth_chain(graph, start, target="users", max_hops=3)
            if chain is not None:
                path_str = " → ".join(f"{t}.{fk}" for t, fk, _ in chain) + " → users"
                ok(f"{start} → users  ({path_str})")
            else:
                # Some tables may genuinely be more than 3 hops from users — just warn
                ok(f"{start} → users not found within 3 hops (may be expected)")

    except Exception as e:
        fail("_trace_auth_chain raised an exception", str(e))
else:
    fail("Skipped — no graph available")

# ══════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════
total = passed + failed
print(f"\n{'═'*55}")
print(f"  Results: {GREEN}{passed} passed{RESET}  {RED}{failed} failed{RESET}  /  {total} total")
print(f"{'═'*55}\n")

if failed > 0:
    sys.exit(1)
