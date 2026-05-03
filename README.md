# Graph-Guided Database CRUD Generator

An intelligent, multi-database REPL environment that uses LLMs to design schemas, explore databases, and generate SQL queries.

It combines the power of **Gemini 2.5 Flash** (for designing complex database schemas from scratch) and **mistral-nemotron** (for precise, context-aware SQL generation), backed by an in-memory graph representation of your SQLite databases.

## Features

- **Multi-Database Workspace:** Easily manage multiple SQLite databases inside the `databases/` folder. Switch between them seamlessly.
- **Natural Language Schema Design:** Describe the app you want to build (e.g., "Design an Instagram clone"), and the **Schema Agent** will design a normalized SQLite schema, automatically handle foreign keys, and execute the `CREATE TABLE` statements.
- **Graph-Guided Context:** When querying an existing database, the system reflects your database into a `networkx` graph, runs a local semantic search (`all-MiniLM-L6-v2`) to find relevant tables, and passes only the relevant subgraph to the LLM.
- **SQL Execution:** Generate SQL from natural language and run it against the active database with an interactive approval step.
- **Auto-Rebuilding Cache:** Write operations (`CREATE`, `ALTER`, `DROP`) are automatically detected. The system incrementally rebuilds the graph, descriptions, and vector embeddings only for the changed tables.
- **Interactive Visualization:** Generate and open a beautiful, interactive HTML visualization of your active database schema graph using `pyvis`.

## Setup

1. Clone the repository and install dependencies:
   ```bash
   conda create -n crud-gen python=3.12
   conda activate crud-gen
   pip install sqlalchemy networkx pyvis sentence-transformers openai google-genai python-dotenv
   ```

2. Create a `.env` file in the root directory and add your API keys:
   ```env
   NVIDIA_API_KEY=your_nvidia_nim_api_key_here
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

3. Run the interactive REPL:
   ```bash
   python main.py
   ```

## Usage

When you start `python main.py`, you will enter the interactive REPL.

### Meta Commands
- `databases` — List all database projects in the workspace.
- `use <name>` — Switch the active database.
- `create <name>` — Create a new empty database project.
- `create demo` — Create and load the included ecommerce demo database.
- `tables` — List all tables in the active database with AI-generated descriptions.
- `graph` — Print a text representation of the schema graph.
- `visualize` — Open an interactive node-graph visualization of your schema in the browser.
- `agents` — List available agents and see which one is active.
- `agent <name>` — Switch the active agent (e.g., `agent schema` or `agent sql`).

### Agents

#### 1. Schema Agent (`agent schema`)
**Use this for:** Designing new databases from scratch.
**Example:**
```text
[schema@ecommerce] >> Design a task management app with projects, tasks, users, and tags.
```
The agent will output the `CREATE TABLE` statements and walk you through creating the database, reflecting the new tables into the graph, and generating descriptions and embeddings.

#### 2. SQL Agent (`agent sql`)
**Use this for:** Querying or modifying an existing database.
**Example:**
```text
[sql@task_manager] >> show me all completed tasks for user John
```
The agent will find the relevant tables using semantic search, generate the correct SQL query, and prompt you to execute it. If you execute a write operation, the schema cache automatically updates.

## Architecture

- **Workspace (`workspace.py`)**: Manages isolated environments for different databases.
- **Graph Builder (`graph_builder.py`)**: Uses SQLAlchemy reflection to build a directed graph of tables and foreign key relationships.
- **Context Engine (`context_engine.py`)**: Uses `mistral-nemotron` to generate business-logic descriptions for tables based on schema.
- **Retrieval Engine (`retrieval.py`)**: Performs two-phase retrieval (semantic seeding + graph expansion) to construct context packages for the LLM.
- **Agents System (`agents/`)**: Pluggable architecture for different LLM behaviors.
- **Tools System (`tools/`)**: Extensible tools, like `sql_executor.py`, for taking actions on the local environment.
