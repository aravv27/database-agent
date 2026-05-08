# Pipeline Run-Through

> **What this document is:** A complete walkthrough of every operation this system performs, in execution order, from cold start to query response. Not a file-by-file guide — a flow-level explanation of how data moves through the pipeline.

---

## Overview

This system takes a database (SQLite or PostgreSQL), introspects its schema, builds a relationship graph, generates AI descriptions of every table, creates semantic embeddings, and then uses all of that to answer natural-language questions by generating precise, context-aware SQL.

There are two fundamentally different flows:

1. **Startup Pipeline** — runs once per database load. Builds all the infrastructure.
2. **Query Pipeline** — runs on every user question. Uses the infrastructure to find relevant tables and generate SQL.

---

## Flow 1: Startup Pipeline

This is everything that happens when you type `use <database>` or when the system auto-loads a database on startup.

### Step 1 — Engine Creation

```
Input:  Database path (SQLite file) or connection URL (PostgreSQL)
Output: SQLAlchemy Engine object
```

- For SQLite: creates a `sqlite:///path` engine and attaches a `PRAGMA foreign_keys=ON` listener so FK constraints are actually enforced (SQLite disables them by default).
- For PostgreSQL: creates a direct engine from the URL. FKs are enforced natively.

The engine is the single connection object used by every subsequent step.

---

### Step 2 — Schema Reflection → Graph Construction

```
Input:  SQLAlchemy Engine
Output: NetworkX DiGraph (nodes = tables, edges = FK relationships)
```

This is a two-pass operation over the live database:

**Pass 1 — Build Nodes (one per table):**
- SQLAlchemy's `Inspector` reflects every table in the database (for PostgreSQL, restricted to the `public` schema to exclude system catalogs).
- For each table, it extracts:
  - **Columns**: name, type, nullable, primary_key
  - **PK constraint**: which columns form the primary key
  - **FK list**: all foreign key constraints
- It then runs **pattern detection** on column names alone (no row counting):
  - `soft_delete` — table has a `deleted_at` column
  - `timestamped` — table has `created_at` or `updated_at`
  - `audited` — table has columns ending in `_by` (like `created_by`)
  - `junction_pure` — table has ≥2 FK columns and zero non-FK, non-PK columns
  - `junction_with_payload` — table has ≥2 FK columns plus extra data columns
- Each table becomes a node in the DiGraph with attributes: `{columns, pk, patterns}`.

**Pass 2 — Build Edges (one per FK):**
- For each table, iterates its foreign keys again.
- Each FK becomes a directed edge from the source table to the referenced table.
- Edge attributes: `{fk_column, ref_column, on_delete, nullable}`.
- Direction matters: `tasks → users` means "tasks has a FK pointing to users".

After this step, the entire schema is an in-memory graph you can traverse.

---

### Step 3 — Schema Hashing (Change Detection)

```
Input:  Graph from Step 2
Output: {table_name: sha256_hex} dict
```

For each table node, the system constructs a deterministic string from:
- All columns sorted by name: `"col_name:type:nullable:primary_key"`
- All outgoing FK edges sorted by target: `"FK:fk_col→target.ref_col:on_delete"`

These parts are joined with `|` and SHA-256 hashed. The result is a 64-character hex string that uniquely fingerprints a table's structure.

**Why:** This hash is compared against the previously cached hash. If a table's hash hasn't changed, the system skips re-describing and re-embedding it. This is what makes the second run nearly instant — only structurally modified tables trigger expensive API calls.

**Stale detection:** A table is "stale" if:
- Its hash differs from the cached hash (schema changed), OR
- It exists in the cache but not in the current graph (table was dropped), OR
- It exists in the graph but not in the cache (new table)

---

### Step 4 — AI Description Generation

```
Input:  Graph + set of stale table names
Output: {table_name: {description, business_role}} for every table
```

This is the most expensive step. For each stale table, the system:

1. **Builds a neighborhood context** — a text block describing the table's position in the graph:
   - Its columns with types and nullability
   - Outgoing FKs: "Points to: → users via creator_id (required, ondelete=CASCADE)"
   - Incoming FKs: "Pointed to by: ← comments via task_id"
   - Detected patterns: "Patterns: timestamped, soft_delete"
   - Sibling tables: other tables that share the same parent via FKs

2. **Calls NVIDIA NIM API** (Mistral-Nemotron model) with:
   - System prompt: "You are a database schema analyst..."
   - User prompt: the neighborhood context + a JSON schema to follow
   - `response_format: json_object` for structured output
   - `temperature: 0.1` for consistency
   - `max_tokens: 200`

3. **Parses the JSON response** into two fields:
   - `description`: 1–2 sentence plain-English description of the table's business purpose
   - `business_role`: one of `core_entity`, `transaction`, `junction`, `detail`, `reference`, `audit`

4. **Fallback**: if the API call fails, generates a template description from the column names.

For unchanged tables, descriptions are loaded directly from cache — zero API calls.

A 0.5-second sleep is inserted between API calls for rate limiting. At 100+ tables, a cold start takes several minutes. A warm start (cache hit) takes milliseconds.

---

### Step 5 — Embedding Computation

```
Input:  Graph + descriptions (all tables)
Output: {table_name: numpy.float32[384]} embedding vectors
```

For each stale table, the system constructs a text string:
```
"{table_name}: {AI description}. Columns: {col1, col2, col3, ...}"
```

This string is encoded using the **all-MiniLM-L6-v2** sentence-transformer model (loaded once, ~80MB) into a 384-dimensional float32 vector.

For unchanged tables, embeddings are loaded from cache (stored as base64-encoded float32 byte strings in `schema_cache.json`).

These vectors are what enable semantic search — "show me billing data" will match `invoices` and `payments` even though neither contains the word "billing".

---

### Step 6 — BM25 Index Construction

```
Input:  Graph + descriptions + embeddings
Output: HybridRetriever object (ready to answer queries)
```

The `HybridRetriever` is instantiated with the graph, descriptions, and embeddings. During construction, it builds a **BM25Okapi index** over all tables.

Each table is represented as a BM25 document:
```
"{table_name} {table_name} {description} {col_names} {col_names}"
```

Table name and column names are **repeated twice** to boost their term frequency, so exact-match queries like "users" or "invoice_id" surface the right table even when semantic similarity is ambiguous.

The BM25 index is a sparse lexical ranker — it matches on exact token overlap (with TF-IDF weighting), not meaning.

---

### Step 7 — Cache Persistence

```
Input:  Hashes, descriptions, embeddings, serialized graph
Output: schema_cache.json (atomic write via tmp file + rename)
```

Everything is saved to disk so subsequent runs are fast:
- `hashes`: `{table_name: sha256}` — for change detection
- `descriptions`: `{table_name: {description, business_role}}` — AI outputs
- `embeddings`: `{table_name: base64_encoded_float32}` — vector data
- `graph`: serialized node/edge lists
- `generated_at`: UTC timestamp

The write is atomic (write to `.tmp`, then rename) to prevent corruption if the process is killed mid-write.

---

### After Startup

The system now holds a `DatabaseProject` object in memory containing:
- The SQLAlchemy engine (live DB connection)
- The NetworkX graph (schema structure)
- AI descriptions (table semantics)
- Embedding vectors (384-dim per table)
- A HybridRetriever (BM25 + semantic, ready to query)
- Schema hashes (for incremental refresh)

The REPL is now ready to accept natural-language queries.

---

## Flow 2: Query Pipeline

This is everything that happens when you type a natural-language question like *"show all tasks assigned to a user in a project"*.

### Step 1 — Hybrid Seed Selection (Phase 1 Retrieval)

```
Input:  User query string + table count
Output: Top-K (table_name, rrf_score) tuples
```

Two independent rankings are computed:

**BM25 (lexical):**
- The query is lowercased and tokenized.
- BM25 scores every table document and produces a ranking (best match first).
- Strength: exact name matches. "api_key_scopes" → directly finds the `api_key_scopes` table.

**Embedding (semantic):**
- The query is embedded into a 384-dim vector using the same sentence-transformer.
- Cosine similarity is computed against every table's pre-computed embedding.
- Tables are ranked by similarity score (highest first).
- Strength: meaning-based matches. "who is responsible for a deliverable" → finds `tasks` even though no column is named "deliverable".

**Reciprocal Rank Fusion (RRF):**
- Both rankings are merged using the formula: `score(t) = Σ 1/(k + rank)` where `k=60`.
- This means a table ranked #1 by BM25 and #3 by embedding gets: `1/61 + 1/63 = 0.0323`.
- RRF is rank-based, not score-based, so it's robust to different score scales between the two rankers.

**Adaptive K Selection:**
- The system doesn't use a fixed number of seed tables. It determines K dynamically:
  - **Schema-scaled ceiling:** `max_k = clamp(log2(table_count), 2, 6)`. For a 100-table schema, `max_k = 6`.
  - **Score-gap detection:** starting from the top, it stops adding seeds when the embedding score drops by more than 0.10 from the previous table. This finds the natural "elbow" in the score distribution.
- Result: focused queries ("show all users") get K=1. Broad queries ("tasks assigned to a user in a project") get K=3–5.

Output: a short list of seed tables — the starting points for graph expansion.

---

### Step 2 — Graph Expansion (Phase 2 Retrieval)

```
Input:  Seed tables + graph + descriptions
Output: Full context package (dict)
```

From the seed tables, the system walks the graph to assemble everything the SQL agent needs:

**1-Hop Expansion:**
- Every table directly connected to a seed (via outgoing or incoming FK) is added to the focus set.
- If seeds are `[tasks, users]`, this might expand to include `projects`, `task_assignments`, `task_labels`, `task_time_logs`, etc.

**Join Path Collection:**
- For every pair of focus tables connected by a FK, the system records the join path:
  ```
  tasks.project_id → projects.id (required, CASCADE)
  task_assignments.user_id → users.id (optional, SET NULL)
  ```
- These are given directly to the SQL agent so it knows exactly how to JOIN tables.

**Auth Chain Tracing:**
- Starting from each seed table, the system does a BFS through outgoing FK edges trying to reach the `users` table within 3 hops.
- If found, the chain is recorded: `tasks.creator_id → users`.
- This enables the SQL agent to add `WHERE user_id = :current_user` filters for row-level security.

**Pattern Hints:**
- For every focus table, detected patterns are converted to actionable hints:
  - `soft_delete` → "Apply soft-delete filter on tasks (WHERE tasks.deleted_at IS NULL)"
  - `junction_with_payload` → "task_assignments is a junction table with payload columns"
  - `junction_pure` → "role_permissions is a pure junction/linking table"

**Column Details:**
- Full column metadata (name, type, nullable, PK) for every focus table is included so the SQL agent knows exactly what columns are available.

The output is a **context package** — a single dict containing: `seed_tables`, `focus_tables`, `join_paths`, `auth_linkage`, `patterns`, `hints`, `descriptions`, `columns`.

---

### Step 3 — Context Formatting

```
Input:  Context package (dict)
Output: Formatted text block for the LLM prompt
```

The `BaseAgent.format_context()` method converts the context package into a structured text block:

```
=== Database Context ===

Focus tables: tasks, users, projects, task_assignments

  [tasks]: Stores individual work items within projects...
    - id INTEGER PK NOT NULL
    - title VARCHAR(255) NOT NULL
    - creator_id INTEGER NOT NULL
    ...

Join paths:
  tasks.project_id -> projects.id
  tasks.creator_id -> users.id (optional)

Auth linkage: tasks.creator_id → users

Generation hints:
  - Apply soft-delete filter on tasks (WHERE tasks.deleted_at IS NULL)
  - task_assignments is a junction table with payload columns
```

This text block is what the SQL-generating LLM actually sees. It never sees the raw graph or embedding vectors.

---

### Step 4 — SQL Generation (Agent Call)

```
Input:  Formatted context + user query + dialect
Output: {sql_query, explanation, tables_used, warnings}
```

The SQL agent sends a single API call to NVIDIA NIM (Mistral-Nemotron):

- **System prompt** is dialect-aware:
  - SQLite prompt: restricts to SQLite-compatible syntax
  - PostgreSQL prompt: enables CTEs, window functions, ILIKE, JSONB, etc.
- **User prompt** contains:
  - The JSON response schema the model must follow
  - The formatted context block from Step 3
  - The original user query
- **Response format:** `json_object` (structured output)
- **Temperature:** 0.1 (deterministic)

The model returns a JSON object with:
- `sql_query`: the generated SQL
- `explanation`: why it chose those tables and joins
- `tables_used`: which tables appear in the query
- `warnings`: caveats like "soft-delete filter applied" or "nullable join may exclude rows"

---

### Step 5 — SQL Execution (Tool)

```
Input:  Generated SQL query
Output: Formatted result table or write confirmation
```

The SQL executor tool:
1. **Shows the query** to the user and asks for confirmation (all queries require explicit approval).
2. **Warns** if it's a write operation (INSERT, UPDATE, DELETE, DROP, etc.).
3. **Executes** against the live database via SQLAlchemy.
4. For SELECT queries: fetches rows, formats as an ASCII table, shows row count.
5. For write operations: commits the transaction, reports rows affected.
6. **If the query was DDL** (CREATE, ALTER, DROP): sets a `schema_changed` flag.

---

### Step 6 — Schema Change Detection (Refresh)

```
Input:  schema_changed flag from Step 5
Output: Rebuilt graph, descriptions, embeddings for modified tables only
```

If the executed SQL was a DDL statement (e.g., `CREATE TABLE`, `ALTER TABLE ADD COLUMN`), the system triggers a full incremental refresh:

1. Re-reflects the database via `build_graph()` (picks up the new/altered table).
2. Re-hashes all tables.
3. Compares against the current hashes — only the modified table(s) are stale.
4. Re-describes only the stale tables via NVIDIA NIM API.
5. Re-embeds only the stale tables.
6. Rebuilds the HybridRetriever (new BM25 index + updated embedding set).
7. Saves the updated cache.

The REPL is immediately ready for the next query, fully aware of the schema change. No manual restart needed.

---

## Flow 3: Schema Design (Alternate Path)

When the active agent is `schema` instead of `sql`, the system bypasses retrieval entirely.

```
User query → Gemini 2.5 Flash → Pydantic-validated JSON schema → DDL statements → Execute → Load
```

1. The user describes what they want: *"I need a project management system with users, teams, tasks, and time tracking"*.
2. The **SchemaDesignAgent** sends this to **Gemini 2.5 Flash** (not Mistral-Nemotron) with a Pydantic-enforced response schema (`SchemaSpec`).
3. Gemini returns a structured JSON with table definitions, columns, and foreign keys.
4. The agent converts this to dialect-specific DDL (SQLite or PostgreSQL CREATE TABLE statements), ordered by FK dependencies (parents before children).
5. The user confirms, the DDL is executed, and the new database is loaded through the full startup pipeline (Steps 1–7 from Flow 1).

---

## Visualization

The `visualize` command generates a self-contained HTML file using **pyvis** (built on vis.js):

- **Nodes** = tables, sized by column count, colored by detected pattern:
  - Red = `soft_delete`, Blue = `timestamped`, Orange = `junction`, Purple = `audited`, Green = no pattern
- **Edges** = FK links, colored by `ON DELETE` behavior:
  - Red = CASCADE, Grey = RESTRICT, Blue dashed = SET NULL
- **Interactive sidebar** — clicking a node shows: AI description, business role, all columns with PK/FK/NULL badges, outgoing and incoming FK relationships
- **Physics simulation** — Force-directed layout (ForceAtlas2) that automatically clusters related tables

The HTML file is fully self-contained (no server needed) and opens in the default browser.

---

## Data Flow Diagram

```
┌──────────────┐
│  Database    │    SQLAlchemy Inspector
│  (SQLite/PG) │◄────────────────────────┐
└──────┬───────┘                         │
       │ reflect                         │
       ▼                                 │
┌──────────────┐                         │
│  NetworkX    │    nodes = tables       │
│  DiGraph     │    edges = FKs          │
└──────┬───────┘                         │
       │                                 │
  ┌────┴────┐                            │
  │         │                            │
  ▼         ▼                            │
┌──────┐  ┌──────────┐                   │
│ Hash │  │ Describe  │ NVIDIA NIM API   │
│ SHA  │  │ (AI)      │ 1 call/table     │
└──┬───┘  └────┬─────┘                   │
   │           │                         │
   │           ▼                         │
   │     ┌───────────┐                   │
   │     │ Embed     │ MiniLM-L6-v2     │
   │     │ 384-dim   │ (local model)    │
   │     └─────┬─────┘                   │
   │           │                         │
   ▼           ▼                         │
┌─────────────────────┐                  │
│   schema_cache.json │ (atomic write)   │
└─────────────────────┘                  │
                                         │
           QUERY TIME                    │
           ─────────                     │
┌──────────────┐                         │
│ User Query   │                         │
└──────┬───────┘                         │
       │                                 │
       ▼                                 │
┌──────────────────┐                     │
│ HybridRetriever  │                     │
│  BM25 + Cosine   │                     │
│  RRF fusion      │                     │
│  Adaptive K      │                     │
└──────┬───────────┘                     │
       │ seed tables                     │
       ▼                                 │
┌──────────────────┐                     │
│ Graph Expansion  │                     │
│  1-hop + auth    │                     │
│  + join paths    │                     │
│  + pattern hints │                     │
└──────┬───────────┘                     │
       │ context package                 │
       ▼                                 │
┌──────────────────┐                     │
│ SQL Agent        │ NVIDIA NIM API      │
│ (Mistral-Nemo)   │ 1 call/query       │
└──────┬───────────┘                     │
       │ sql_query                       │
       ▼                                 │
┌──────────────────┐                     │
│ SQL Executor     │────────────────────►│
│ (user confirms)  │  if DDL → refresh   │
└──────────────────┘                     │
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **Graph-first, not LLM-first** | The graph captures structural truth (FKs, patterns) that the LLM uses but doesn't discover. This prevents hallucinated joins. |
| **Hybrid retrieval (BM25 + semantic)** | BM25 catches exact name matches ("api_key_scopes"). Embeddings catch meaning ("billing" → invoices). Neither alone is sufficient at 100+ tables. |
| **RRF over linear combination** | RRF is rank-based, so it doesn't need score calibration between two fundamentally different scoring systems. |
| **Adaptive K over fixed K** | At 100 tables, a fixed K=5 wastes context on broad queries and under-selects on focused ones. The gap-based adaptive K adjusts per query. |
| **Incremental cache** | SHA-256 hashing per table means adding one column to one table only re-describes and re-embeds that one table. The other 100 are untouched. |
| **Separate models for description vs SQL** | Descriptions use Mistral-Nemotron (cheap, fast, structured output). Schema design uses Gemini 2.5 Flash (large context, Pydantic enforcement). SQL uses Mistral-Nemotron (SQL-specialized). |
| **Auth chain tracing** | The BFS from any table to `users` within 3 hops enables row-level security hints in generated SQL. |
| **Atomic cache writes** | Write to `.tmp`, then rename. Prevents corrupted cache if the process is killed during a 100-table cold start. |
