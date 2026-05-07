"""
main.py — Wires everything together. CLI entry point.

On startup:
  1. Migrate legacy test.db if present
  2. Ask the user: SQLite or PostgreSQL?  (sets session_dialect)
  3. Auto-load the first DB matching that dialect, if one exists
  4. Enter interactive query loop

session_dialect is the single source of truth for intent:
  - schema agent always uses session_dialect (can create a DB even with no active project)
  - sql agent uses active_project.dialect (needs a loaded DB to query)
  - 'use <name>' loads any DB and updates session_dialect to match it
  - 'create <name>' defaults to session_dialect; --postgres / --sqlite override it

REPL commands:
  databases            — list all databases
  use <name>           — switch active database (updates dialect)
  create <name>        — create database using current session dialect
  create <name> --postgres — force PostgreSQL
  create <name> --sqlite   — force SQLite
  create demo          — create the ecommerce demo database (SQLite)
  dialect              — show or switch session dialect
  refresh              — re-reflect + re-describe + re-embed active DB
  graph                — print schema graph (text)
  visualize            — open interactive graph in browser
  tables               — list tables with descriptions
  agents               — list agents
  agent <name>         — switch active agent
  tools                — list tools
  quit                 — exit
"""

import sys
import time

from graph_builder import print_graph
from retrieval import find, print_context
from visualize import visualize_graph
from workspace import Workspace
from tools import ToolRegistry
from tools.sql_executor import SQLExecutorTool
from agents import AgentRegistry
from agents.sql_agent import SQLQueryAgent
from agents.schema_agent import SchemaDesignAgent
from database import create_schema, seed_data


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_tool_registry(engine=None) -> ToolRegistry:
    registry = ToolRegistry()
    if engine:
        registry.register(SQLExecutorTool(engine))
    return registry


def _make_agent_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(SQLQueryAgent())
    registry.register(SchemaDesignAgent())
    return registry


def _print_banner(ws: Workspace, agent_registry: AgentRegistry,
                  tool_registry: ToolRegistry, session_dialect: str):
    print("\n" + "=" * 60)
    print("  DATABASE CRUD GENERATOR")
    print("=" * 60)
    print("  databases           — list all databases")
    print("  use <name>          — switch active database (dialect follows)")
    print("  create <name>       — create DB using session dialect")
    print("  create <name> --postgres — force PostgreSQL")
    print("  create <name> --sqlite   — force SQLite")
    print("  create demo         — ecommerce demo (SQLite)")
    print("  dialect             — show/switch session dialect")
    print("  refresh             — rebuild graph/descriptions for active DB")
    print("  graph               — print schema graph")
    print("  visualize           — open graph in browser")
    print("  tables              — list tables with descriptions")
    print("  agents              — list agents  |  agent <name> to switch")
    print("  tools               — list tools")
    print("  quit                — exit")
    print("=" * 60)
    active = ws.active
    if active:
        print(f"  Active DB     : {active.name} ({active.table_count} tables, {active.dialect.upper()})")
    else:
        print(f"  Active DB     : none")
    print(f"  Session Dialect: {session_dialect.upper()}")
    print(f"  Active Agent  : {agent_registry.active.name if agent_registry.active else 'none'}")
    print(f"  Tools         : {', '.join(tool_registry.list_tools()) or 'none'}")
    print("=" * 60)


# ── Schema agent post-processing ───────────────────────────────────────────

def _handle_schema_result(result: dict, ws: Workspace,
                          tool_registry: ToolRegistry) -> bool:
    """
    After schema agent runs: confirm name, create DB, execute DDL,
    optionally load and make active.
    Returns True if a new DB was created.
    """
    if result.get("_error"):
        return False

    sql_statements = result.get("_sql_statements", [])
    if not sql_statements:
        print("  No SQL statements generated.")
        return False

    dialect = result.get("_dialect", "sqlite")
    db_name = result.get("database_name", "new_database").replace(" ", "_").lower()

    print(f"\n  Suggested database name: {db_name}")
    try:
        name_input = input(f"  Database name [{db_name}]: ").strip()
        if name_input:
            db_name = name_input
        confirm = input(
            f"\n  Create {dialect.upper()} database '{db_name}' "
            f"with {len(sql_statements)} table(s)? [y/N]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return False

    if confirm not in ("y", "yes"):
        print("  Cancelled.")
        return False

    # Create the DB
    ws.create_empty(db_name, dialect=dialect)
    engine = ws.get_engine_for(db_name)

    # Execute CREATE TABLE statements
    from sqlalchemy import text as sa_text
    with engine.connect() as conn:
        for stmt in sql_statements:
            conn.execute(sa_text(stmt))
        conn.commit()

    print(f"  {len(sql_statements)} table(s) created.")

    # Show graph preview
    from graph_builder import build_graph
    graph = build_graph(engine)
    print_graph(graph)

    # Ask to load and make active
    try:
        build_confirm = input(
            "\n  Build graph, descriptions and embeddings for this database? [Y/n]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Skipped.")
        return True

    if build_confirm in ("", "y", "yes"):
        print(f"  Loading '{db_name}'...")
        ws.load(db_name, engine=engine)
        ws.set_active(db_name)
        tool_registry.register(SQLExecutorTool(ws.active.engine))
        print(f"  Switched to database: {db_name}")
    else:
        print(f"  Database created. Use 'use {db_name}' to switch to it later.")

    return True


# ── Startup dialect selection ───────────────────────────────────────────────

def _ask_dialect() -> str:
    """
    Ask the user which dialect they want to work with this session.
    Returns 'sqlite' or 'postgresql'.
    """
    print("\n" + "=" * 60)
    print("  DATABASE CRUD GENERATOR")
    print("=" * 60)
    print("  Which database dialect do you want to use?")
    print("  [1] SQLite    (local file-based)")
    print("  [2] PostgreSQL (server-based)")
    print("=" * 60)

    while True:
        try:
            choice = input("  Choice [1/2] (default: 1): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            sys.exit(0)

        if choice in ("", "1", "sqlite", "s"):
            print("  Session dialect: SQLITE")
            return "sqlite"
        elif choice in ("2", "postgres", "postgresql", "pg", "p"):
            print("  Session dialect: POSTGRESQL")
            return "postgresql"
        else:
            print("  Please enter 1 for SQLite or 2 for PostgreSQL.")


# ── Main loop ──────────────────────────────────────────────────────────────

def interactive_loop(ws: Workspace, tool_registry: ToolRegistry,
                     agent_registry: AgentRegistry, session_dialect: str):

    _print_banner(ws, agent_registry, tool_registry, session_dialect)

    while True:
        active_project = ws.active
        db_label = active_project.name if active_project else "no db"
        active_agent = agent_registry.active
        agent_label = active_agent.name if active_agent else "none"

        try:
            query = input(f"\n[{agent_label}@{db_label}|{session_dialect[:2].upper()}] >> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not query:
            continue

        q = query.lower()

        # ── Meta commands ──
        if q in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        # databases — list all with dialect labels
        if q == "databases":
            dbs = ws.list_databases()
            if not dbs:
                print("  No databases found. Use 'create <name>' to create one.")
            for name in dbs:
                d = ws.get_dialect(name)
                marker = " <- active" if active_project and name == active_project.name else ""
                print(f"  {name} ({d.upper()}){marker}")
            continue

        # dialect — show or switch session dialect
        if q == "dialect":
            print(f"  Session dialect: {session_dialect.upper()}")
            print("  Type 'dialect sqlite' or 'dialect postgres' to switch.")
            continue

        if q in ("dialect sqlite", "dialect sqlite", "dialect sq"):
            session_dialect = "sqlite"
            print("  Session dialect switched to SQLITE.")
            print("  'agent schema' will now generate SQLite schemas.")
            continue

        if q in ("dialect postgres", "dialect postgresql", "dialect pg"):
            session_dialect = "postgresql"
            print("  Session dialect switched to POSTGRESQL.")
            print("  'agent schema' will now generate PostgreSQL schemas.")
            continue

        # use — switch active database, dialect follows the project
        if q.startswith("use "):
            name = query.split(" ", 1)[1].strip()
            if not ws.exists(name):
                print(f"  No database named '{name}'. Available: {', '.join(ws.list_databases())}")
                continue
            print(f"  Loading '{name}'...")
            ws.load(name)
            ws.set_active(name)
            tool_registry.register(SQLExecutorTool(ws.active.engine))
            p = ws.active
            # Session dialect follows the project
            session_dialect = p.dialect
            print(f"  Switched to '{name}' ({p.table_count} tables, {p.dialect.upper()}).")
            print(f"  Session dialect updated to {session_dialect.upper()}.")
            continue

        # create — uses session_dialect by default, flags override
        if q.startswith("create "):
            raw = query.split(" ", 1)[1].strip()

            # Parse optional dialect override flags
            if "--postgres" in raw:
                create_dialect = "postgresql"
                name = raw.replace("--postgres", "").strip()
            elif "--sqlite" in raw:
                create_dialect = "sqlite"
                name = raw.replace("--sqlite", "").strip()
            else:
                # Default: use session dialect
                create_dialect = session_dialect
                name = raw.strip()

            if name == "demo":
                # Demo is always SQLite
                if ws.exists("ecommerce"):
                    print("  Demo database 'ecommerce' already exists. Use 'use ecommerce'.")
                    continue
                ws.create_empty("ecommerce", dialect="sqlite")
                engine = ws.get_engine_for("ecommerce")
                create_schema(engine)
                seed_data(engine)
                ws.load("ecommerce", engine=engine)
                ws.set_active("ecommerce")
                tool_registry.register(SQLExecutorTool(ws.active.engine))
                session_dialect = "sqlite"
                print("  Demo database 'ecommerce' created and loaded.")
                print("  Session dialect set to SQLITE.")
            else:
                if ws.exists(name):
                    print(f"  Database '{name}' already exists. Use 'use {name}' to switch.")
                    continue
                ws.create_empty(name, dialect=create_dialect)
                # Update session dialect to match what was created
                session_dialect = create_dialect
                print(f"  Created {create_dialect.upper()} database '{name}'.")
                print(f"  Session dialect set to {session_dialect.upper()}.")
                print(f"  Use 'use {name}' to load it, or 'agent schema' to design tables.")
            continue

        if q == "refresh":
            if not active_project:
                print("  No active database. Use 'use <name>' first.")
                continue
            print(f"  Refreshing '{active_project.name}'...")
            stale = ws.refresh_active()
            if stale is not None:
                tool_registry.register(SQLExecutorTool(ws.active.engine))
                print(f"  Done. {len(stale)} table(s) rebuilt.")
            continue

        if q == "graph":
            if not active_project:
                print("  No active database.")
                continue
            print_graph(active_project.graph)
            continue

        if q == "visualize":
            if not active_project:
                print("  No active database.")
                continue
            visualize_graph(active_project.graph, active_project.descriptions)
            continue

        if q == "tables":
            if not active_project:
                print("  No active database.")
                continue
            for t in sorted(active_project.graph.nodes):
                desc = active_project.descriptions.get(t, {}).get("description", "")
                print(f"  {t}: {desc}")
            continue

        if q == "tools":
            for name in tool_registry.list_tools():
                t = tool_registry.get(name)
                print(f"  {t.name}: {t.description}")
            continue

        if q == "agents":
            for name in agent_registry.list_agents():
                ag = agent_registry.get(name)
                ctx = "no context" if not ag.needs_context else "uses context"
                marker = " <- active" if name == agent_label else ""
                print(f"  {ag.name} ({ctx}): {ag.description}{marker}")
            continue

        if q.startswith("agent "):
            target = query.split(" ", 1)[1].strip()
            if agent_registry.set_active(target):
                print(f"  Switched to agent: {target}")
            else:
                print(f"  Unknown agent '{target}'. Available: {', '.join(agent_registry.list_agents())}")
            continue

        # ── Agent query ──
        if not active_agent:
            print("  No active agent. Use 'agent <name>' to select one.")
            continue

        # Schema agent — uses session_dialect, no active DB required
        if not active_agent.needs_context:
            print(f"  [{active_agent.name}] Generating ({session_dialect})...")
            result = active_agent.ask(query, context={"dialect": session_dialect})
            active_agent.print_result(result)

            if active_agent.name == "schema":
                created = _handle_schema_result(result, ws, tool_registry)
                # If a new DB was created and loaded, sync session_dialect
                if created and ws.active:
                    session_dialect = ws.active.dialect
            continue

        # SQL agent — requires an active DB
        if not active_project:
            print("  No active database. Use 'use <name>' to load one.")
            continue

        # Retrieval
        print("  Finding relevant tables...")
        context = find(
            query,
            active_project.graph,
            active_project.descriptions,
            active_project.retriever,
        )
        print_context(context)

        # Agent — always uses active_project.dialect for SQL generation
        context["dialect"] = active_project.dialect
        print(f"  [{active_agent.name}] Generating ({active_project.dialect})...")
        result = active_agent.ask(query, context)
        active_agent.print_result(result)

        # SQL execution offer
        sql_query = result.get("sql_query", "")
        sql_tool = tool_registry.get("sql_executor")
        if sql_tool and sql_query and not sql_query.startswith("-- ERROR"):
            tool_result = sql_tool.run(query=sql_query)
            if tool_result.success:
                print(tool_result.message)
                if tool_result.metadata.get("schema_changed"):
                    print("  Schema change detected. Rebuilding...")
                    stale = ws.refresh_active()
                    tool_registry.register(SQLExecutorTool(ws.active.engine))
                    if stale:
                        print(f"  Rebuilt: {', '.join(sorted(stale))}")
                    else:
                        print("  No structural changes detected.")
            elif tool_result.error not in ("Declined by user", "Cancelled by user"):
                print(f"  Error: {tool_result.error}")


# ── Startup ────────────────────────────────────────────────────────────────

def main():
    ws = Workspace()
    ws.migrate_legacy()

    # Step 1: Ask dialect — this sets session_dialect for the whole session
    session_dialect = _ask_dialect()

    # Build registries
    tool_registry = _make_tool_registry()
    agent_registry = _make_agent_registry()

    # Step 2: Auto-load the first DB matching the chosen dialect
    dbs = ws.list_databases()
    matching = [n for n in dbs if ws.get_dialect(n) == session_dialect]

    if matching:
        first = matching[0]
        print(f"\n  Auto-loading '{first}' ({session_dialect.upper()})...")
        ws.load(first)
        ws.set_active(first)
        tool_registry.register(SQLExecutorTool(ws.active.engine))
        print(f"  Ready. Agents: {', '.join(agent_registry.list_agents())} (active: {agent_registry.active.name})")
    elif dbs:
        # DBs exist but not for this dialect
        other_dialect = "postgresql" if session_dialect == "sqlite" else "sqlite"
        print(f"\n  No {session_dialect.upper()} databases found.")
        print(f"  (You have {len(dbs)} {other_dialect.upper()} database(s) — use 'use <name>' to switch.)")
        print(f"  Use 'agent schema' to design a new {session_dialect.upper()} database.")
        print(f"  Use 'create <name>' to create an empty {session_dialect.upper()} database.")
    else:
        # No databases at all
        print(f"\n  No databases found.")
        print(f"  Use 'agent schema' to design a new database.")
        print(f"  Use 'create demo' to load the SQLite ecommerce demo.")

    interactive_loop(ws, tool_registry, agent_registry, session_dialect)


if __name__ == "__main__":
    main()
