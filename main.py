"""
main.py — Wires everything together. CLI entry point.

On startup:
  1. Migrate legacy test.db if present
  2. Load the workspace (databases/ folder)
  3. Auto-load the first available database, or prompt to create one
  4. Enter interactive query loop

REPL commands:
  databases            — list all databases
  use <name>           — switch active database
  create <name>        — create empty database
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

def _make_tool_registry(engine) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SQLExecutorTool(engine))
    return registry


def _make_agent_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(SQLQueryAgent())
    registry.register(SchemaDesignAgent())
    return registry


def _print_banner(ws: Workspace, agent_registry: AgentRegistry, tool_registry: ToolRegistry):
    print("\n" + "=" * 60)
    print("  DATABASE CRUD GENERATOR")
    print("=" * 60)
    print("  databases           — list all databases")
    print("  use <name>          — switch active database")
    print("  create <name>       — create a new empty database")
    print("  create demo         — create the ecommerce demo database")
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
        print(f"  Active DB   : {active.name} ({active.table_count} tables)")
    print(f"  Active Agent: {agent_registry.active.name if agent_registry.active else 'none'}")
    print(f"  Tools       : {', '.join(tool_registry.list_tools())}")
    print("=" * 60)


# ── Schema agent post-processing ───────────────────────────────────────────

def _handle_schema_result(result: dict, ws: Workspace, tool_registry: ToolRegistry) -> bool:
    """
    After the schema agent runs: show statements, ask to create the DB,
    then ask whether to build graph + descriptions.
    Returns True if a new DB was created and loaded.
    """
    if result.get("_error"):
        return False

    sql_statements = result.get("_sql_statements", [])
    if not sql_statements:
        print("  No SQL statements generated.")
        return False

    db_name = result.get("database_name", "new_database").replace(" ", "_").lower()

    # Ask to create the DB
    print(f"\n  Suggested database name: {db_name}")
    try:
        name_input = input(f"  Database name [{db_name}]: ").strip()
        if name_input:
            db_name = name_input
        confirm = input(f"\n  Create database '{db_name}' with {len(sql_statements)} table(s)? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.")
        return False

    if confirm not in ("y", "yes"):
        print("  Cancelled.")
        return False

    # Create the DB
    ws.create_empty(db_name)
    engine = ws.get_engine_for(db_name)

    # Execute CREATE TABLE statements
    from sqlalchemy import text as sa_text
    with engine.connect() as conn:
        for stmt in sql_statements:
            conn.execute(sa_text(stmt))
        conn.commit()

    print(f"  {len(sql_statements)} table(s) created.")

    # Print table details
    from graph_builder import build_graph
    graph = build_graph(engine)
    print_graph(graph)

    # Ask to build graph + descriptions
    try:
        build_confirm = input(
            "\n  Build graph, descriptions and embeddings for this database? [Y/n]: "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Skipped.")
        return True

    if build_confirm in ("", "y", "yes"):
        print(f"  Loading database '{db_name}'...")
        ws.load(db_name, engine=engine)
        ws.set_active(db_name)
        # Update sql_executor engine
        tool_registry.register(SQLExecutorTool(ws.active.engine))
        print(f"  Switched to database: {db_name}")
    else:
        print(f"  Database created. Use 'use {db_name}' to switch to it later.")

    return True


# ── Main loop ──────────────────────────────────────────────────────────────

def interactive_loop(ws: Workspace, tool_registry: ToolRegistry, agent_registry: AgentRegistry):
    _print_banner(ws, agent_registry, tool_registry)

    while True:
        active_project = ws.active
        db_label = active_project.name if active_project else "no db"
        active_agent = agent_registry.active
        agent_label = active_agent.name if active_agent else "none"

        try:
            query = input(f"\n[{agent_label}@{db_label}] >> ").strip()
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

        if q == "databases":
            dbs = ws.list_databases()
            if not dbs:
                print("  No databases found. Use 'create <name>' to create one.")
            for name in dbs:
                marker = " <- active" if active_project and name == active_project.name else ""
                print(f"  {name}{marker}")
            continue

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
            print(f"  Switched to '{name}' ({p.table_count} tables).")
            continue

        if q.startswith("create "):
            name = query.split(" ", 1)[1].strip()
            if name == "demo":
                # Create ecommerce demo
                if ws.exists("ecommerce"):
                    print("  Demo database 'ecommerce' already exists. Use 'use ecommerce'.")
                    continue
                ws.create_empty("ecommerce")
                engine = ws.get_engine_for("ecommerce")
                create_schema(engine)
                seed_data(engine)
                ws.load("ecommerce", engine=engine)
                ws.set_active("ecommerce")
                tool_registry.register(SQLExecutorTool(ws.active.engine))
                print("  Demo database 'ecommerce' created and loaded.")
            else:
                if ws.exists(name):
                    print(f"  Database '{name}' already exists. Use 'use {name}' to switch.")
                    continue
                ws.create_empty(name)
                print(f"  Empty database '{name}' created. Use 'use {name}' to load it.")
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

        # Schema agent — no retrieval
        if not active_agent.needs_context:
            print(f"  [{active_agent.name}] Generating...")
            result = active_agent.ask(query, context={})
            active_agent.print_result(result)

            # Handle schema creation flow
            if active_agent.name == "schema":
                _handle_schema_result(result, ws, tool_registry)
            continue

        # Context-needing agents — require an active DB
        if not active_project:
            print("  No active database. Use 'use <name>' or 'create <name>'.")
            continue

        # Retrieval
        print("  Finding relevant tables...")
        context = find(
            query,
            active_project.graph,
            active_project.embeddings,
            active_project.descriptions,
        )
        print_context(context)

        # Agent
        print(f"  [{active_agent.name}] Generating...")
        result = active_agent.ask(query, context)
        active_agent.print_result(result)

        # SQL execution offer
        sql_query = result.get("sql_query", "")
        sql_tool = tool_registry.get("sql_executor")
        if sql_tool and sql_query and not sql_query.startswith("-- ERROR"):
            tool_result = sql_tool.run(query=sql_query)
            if tool_result.success:
                print(tool_result.message)
                # Auto-rebuild if DDL changed schema
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

    # Migrate legacy files if they exist
    ws.migrate_legacy()

    # Build registries
    tool_registry = ToolRegistry()
    agent_registry = _make_agent_registry()

    # Auto-load first available DB, or print guidance
    dbs = ws.list_databases()
    if dbs:
        first = dbs[0]
        print(f"Loading database '{first}'...")
        ws.load(first)
        ws.set_active(first)
        tool_registry.register(SQLExecutorTool(ws.active.engine))
        print(f"Agents: {', '.join(agent_registry.list_agents())} (active: {agent_registry.active.name})")
    else:
        print("No databases found.")
        print("  Type 'create demo' to load the ecommerce demo.")
        print("  Type 'create <name>' to create a new empty database.")
        print("  Type 'agent schema' then describe your schema to design one from scratch.")
        # Still need a placeholder tool registry (no engine yet)
        tool_registry = ToolRegistry()

    interactive_loop(ws, tool_registry, agent_registry)


if __name__ == "__main__":
    main()
