# Graph-Guided Database CRUD Generator

An intelligent, multi-database REPL that uses LLMs to design schemas and generate SQL queries. It combines **Gemini 2.5 Flash** (for schema design) and **mistralai/mistral-nemotron** via NVIDIA NIM (for SQL generation), backed by a graph representation of your SQLite schema.

## Features

- **Multi-Database Workspace:** Manage multiple SQLite databases under `databases/`. Switch between them at runtime.
- **Natural Language Schema Design:** Describe the app you want to build, and the Schema Agent designs a normalized SQLite schema, generates `CREATE TABLE` statements, and loads everything into the graph.
- **Graph-Guided SQL Generation:** Incoming queries are resolved using a two-phase retrieval system — hybrid BM25/semantic seeding with adaptive K + graph expansion — that restricts what the LLM sees to only the relevant tables.
- **Incremental Cache:** Each table is SHA-256 hashed by structure. On restart, only changed tables are re-described and re-embedded; everything else loads from cache in milliseconds.
- **Auto-Rebuilding:** DDL operations (`CREATE`, `ALTER`, `DROP`) are detected automatically. The system incrementally rebuilds only the affected parts of the graph without a full restart.
- **Interactive Visualization:** Open a `pyvis`-powered HTML graph of your active schema in the browser, with a side panel for table inspection.

---

## How It Works

### 1. Graph Construction (`graph_builder.py`)

On startup, the database is reflected using SQLAlchemy's `inspect()` API in two passes:

**Pass 1 — Nodes:** Every table becomes a node with:
- Structured column metadata: `name`, `type`, `nullable`, `primary_key`
- Detected structural **patterns**, inferred purely from column names:
  - `soft_delete` — has a `deleted_at` column
  - `timestamped` — has `created_at` or `updated_at`
  - `audited` — has any `*_by` column
  - `junction_pure` — two or more FK columns, no other payload columns
  - `junction_with_payload` — two or more FK columns plus extra data columns

**Pass 2 — Edges:** Every foreign key becomes a directed edge from child to parent, carrying `fk_column`, `ref_column`, `on_delete`, and `nullable`.

The result is a `networkx.DiGraph` that represents the full relational structure of your database in memory.

### 2. Hashing & Incremental Cache (`cache.py`)

Each table is fingerprinted with a deterministic **SHA-256 hash** based on:
- All column names, types, nullability, and PK flags (sorted alphabetically)
- All outgoing FK relationships (`fk_column → target.ref_column:on_delete`)

On startup, live hashes are compared against the persisted `schema_cache.json`. Only tables whose hash has changed (or that are new/removed) are considered **stale** and trigger the downstream pipeline. Unchanged tables load their description and embedding from cache instantly.

The cache is written atomically (write to `.tmp`, then rename) to prevent corruption. Each database project has its own `schema_cache.json` inside its folder.

### 3. AI-Generated Descriptions (`context_engine.py`)

For each stale table, the system assembles a **neighborhood block** — a structured text snapshot of the table's position in the graph:
- Full column list with types and nullability
- Outgoing FK targets with cardinality hints (`required` / `optional`)
- Incoming FK references (what tables depend on this one)
- Detected patterns
- Sibling tables (tables sharing the same FK parent)

This neighborhood is sent to `mistralai/mistral-nemotron` via NVIDIA NIM with a strict JSON response schema, returning:
- `description` — 1-2 sentence human-readable explanation of the table's purpose
- `business_role` — one of: `core_entity`, `transaction`, `junction`, `detail`, `reference`, `audit`

### 4. Hybrid Retrieval & Adaptive Seeding (`retrieval.py`)

Each table is embedded using `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim vectors). Embeddings are stored as base64-encoded raw `float32` bytes in `schema_cache.json`.

When a user submits a query, a **`HybridRetriever`** runs two parallel rankers then fuses their results:

**BM25 (sparse lexical ranker)**
Each table's BM25 document is constructed as:
```
{table_name} {table_name} {description} {col1} {col2} ... {col1} {col2} ...
```
Table name and column names are repeated twice to boost their term frequency. This makes exact-match queries like `"user_id"` or `"orders table"` surface the right table reliably — something embeddings alone can miss.

**Embedding ranker (dense semantic)**
The query is embedded with the same model and cosine similarity is computed against all table embeddings. Captures meaning-based matches where the query doesn't share literal tokens with the schema.

**Reciprocal Rank Fusion (RRF, k=60)**
Both rankers produce an independent ranked list. RRF merges them without needing score normalization:
```
RRF score = Σ  1 / (60 + rank_i)   for each ranker i
```
The ranker that is more confident about a table (lower rank) contributes more. Neither ranker can dominate by raw score magnitude.

**Adaptive K**
Instead of a hardcoded `top_k`, the number of seeds is chosen automatically using two signals:
- **Schema-scaled ceiling:** `max_k = clamp(log₂(table_count), 2, 6)` — larger schemas allow more seeds
- **Score-gap detection:** seeds are added until the embedding score drops more than `0.10` from the previous entry — a natural cliff signals the end of the relevant cluster

| Schema size | max_k |
|------------|-------|
| ~20 tables | 4 |
| ~50 tables | 5 |
| 100+ tables | 6 |

**Phase 2 — Graph Expansion**
Starting from the seed tables, the graph is walked:
- All 1-hop FK neighbors (both incoming and outgoing) are added to the **focus set**
- Junction tables connecting any two focus tables are automatically included
- An **auth chain** tracer walks outgoing FKs up to 3 hops to find a path to the `users` table, surfacing the auth linkage if found

The final **context package** passed to the LLM contains: focus tables, join paths, auth linkage, pattern-derived generation hints (e.g., "apply soft-delete filter on orders"), and column details.

The REPL shows the adaptive K and strategy on every query:
```
Seeds (K=3, adaptive, hybrid):
  orders  rrf=0.03201 ← seed
  users   rrf=0.03187 ← seed
  products  rrf=0.03041 ← seed
Focus tables: ['order_items', 'orders', 'products', 'users']
```

---

## Setup

1. **Create and activate environment:**
   ```bash
   conda create -n crud-gen python=3.12
   conda activate crud-gen
   pip install sqlalchemy networkx pyvis sentence-transformers openai google-genai python-dotenv rank_bm25
   ```

2. **Add API keys to `.env`:**
   ```env
   NVIDIA_API_KEY=your_nvidia_nim_api_key
   GEMINI_API_KEY=your_gemini_api_key
   ```

3. **Run:**
   ```bash
   python main.py
   ```

---

## REPL Commands

| Command | Description |
|---|---|
| `databases` | List all database projects |
| `use <name>` | Switch the active database |
| `create <name>` | Create a new empty database |
| `create demo` | Load the built-in ecommerce demo database |
| `refresh` | Re-reflect and rebuild the active database |
| `tables` | List tables with AI descriptions |
| `graph` | Print the schema graph to the terminal |
| `visualize` | Open an interactive HTML graph in the browser |
| `agents` | List available agents |
| `agent <name>` | Switch the active agent |
| `tools` | List available tools |
| `quit` | Exit |

---

## Agents

### `agent schema` — Schema Design Agent
Uses **Gemini 2.5 Flash** with a Pydantic-enforced response schema. Takes a natural language description, outputs a fully normalized SQLite schema, runs the `CREATE TABLE` statements, and walks you through loading it into the graph.

```
[schema@ecommerce] >> Design a task manager with projects, tasks, users, labels, and comments
```

### `agent sql` — SQL Query Agent
Uses **mistral-nemotron** via NVIDIA NIM. Runs the two-phase retrieval pipeline against the active database and generates a context-aware SQL query. If the result is a write operation, the schema cache auto-updates after execution.

```
[sql@task_manager] >> show all overdue tasks assigned to a specific user
```

---

## Project Structure

```
database-crud-gen/
├── databases/               # Per-project SQLite databases and caches
│   └── {name}/
│       ├── db.sqlite
│       └── schema_cache.json
├── agents/
│   ├── base.py              # BaseAgent + AgentRegistry
│   ├── sql_agent.py         # SQL query generation agent (mistral-nemotron)
│   └── schema_agent.py      # Schema design agent (Gemini 2.5 Flash)
├── tools/
│   ├── base.py              # BaseTool + ToolRegistry
│   └── sql_executor.py      # SQL execution tool with confirmation + DDL detection
├── graph_builder.py         # SQLAlchemy reflection → NetworkX DiGraph
├── context_engine.py        # Per-table AI description generation
├── retrieval.py             # Hybrid retrieval: BM25 + semantic RRF seeding, adaptive K, graph expansion
├── cache.py                 # SHA-256 hashing, cache persistence, embedding codec
├── workspace.py             # Multi-database project manager
├── visualize.py             # pyvis-based interactive schema visualization
├── database.py              # Demo ecommerce schema + seed data
└── main.py                  # REPL entry point
```
