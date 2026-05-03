"""
agent.py — Takes a retrieval context package + user query,
sends both to NVIDIA NIM, and returns structured SQL / explanation.

Uses JSON structured output to guarantee consistent format.
"""

import os
import json

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_API_KEY"),
        )
    return _client


MODEL = "mistralai/mistral-nemotron"


# ── Response schema ────────────────────────────────────────────────────────

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sql_query": {
            "type": "string",
            "description": "The SQLite-compatible SQL query that answers the user's request"
        },
        "explanation": {
            "type": "string",
            "description": "Brief explanation of what the query does and why these tables/joins were chosen"
        },
        "tables_used": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of table names used in the query"
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Any caveats: soft-delete filters applied, nullable joins, etc."
        }
    },
    "required": ["sql_query", "explanation", "tables_used"]
}


# ── Context formatting ─────────────────────────────────────────────────────

def _format_context(ctx):
    """Convert a retrieval context package into a text block for the LLM."""
    lines = ["=== Database Context ==="]

    # Focus tables with descriptions
    lines.append(f"\nFocus tables: {', '.join(ctx['focus_tables'])}")
    for t in ctx["focus_tables"]:
        desc = ctx["descriptions"].get(t, "")
        lines.append(f"\n  [{t}]: {desc}")
        cols = ctx["columns"].get(t, [])
        for c in cols:
            pk = " PK" if c["primary_key"] else ""
            null = " NULL" if c["nullable"] else " NOT NULL"
            lines.append(f"    - {c['name']} {c['type']}{pk}{null}")

    # Join paths
    if ctx["join_paths"]:
        lines.append("\nJoin paths:")
        for jp in ctx["join_paths"]:
            null_tag = " (optional)" if jp["nullable"] else ""
            lines.append(f"  {jp['from']}.{jp['fk_column']} → {jp['to']}.{jp['ref_column']}{null_tag}")

    # Auth linkage
    if ctx.get("auth_linkage"):
        lines.append(f"\nAuth linkage: {ctx['auth_linkage']}")

    # Patterns & hints
    if ctx.get("hints"):
        lines.append("\nGeneration hints:")
        for h in ctx["hints"]:
            lines.append(f"  • {h}")

    return "\n".join(lines)


# ── Query execution ────────────────────────────────────────────────────────

def ask(query, context):
    """
    Send user query + context to NVIDIA NIM.
    Returns structured dict with sql_query, explanation, tables_used, warnings.
    """
    ctx_text = _format_context(context)
    client = _get_client()

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a SQL generation assistant. You receive a database context "
                        "package describing available tables, their columns, join paths, and "
                        "patterns. Generate a SQLite-compatible query that answers the user's "
                        "request. ONLY use tables and columns described in the context. "
                        "Do NOT invent columns or tables. Respond ONLY with valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Respond with JSON matching this schema:\n"
                        f"{json.dumps(RESPONSE_SCHEMA, indent=2)}\n\n"
                        f"{ctx_text}\n\n"
                        f"=== User Request ===\n{query}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        result = json.loads(raw)

        # Ensure required fields
        if "sql_query" not in result:
            raise ValueError(f"Missing sql_query in response: {raw}")

        result.setdefault("warnings", [])
        result.setdefault("tables_used", [])

        return result

    except Exception as e:
        return {
            "sql_query": "-- ERROR: could not generate query",
            "explanation": f"LLM call failed: {e}",
            "tables_used": [],
            "warnings": [str(e)],
        }


def print_result(result):
    """Pretty-print the agent's response."""
    print("\n" + "─" * 60)
    print("📝 SQL Query:")
    print(result["sql_query"])
    print(f"\n💡 Explanation: {result['explanation']}")
    if result.get("tables_used"):
        print(f"📦 Tables: {', '.join(result['tables_used'])}")
    if result.get("warnings"):
        print("⚠️  Warnings:")
        for w in result["warnings"]:
            print(f"   • {w}")
    print("─" * 60)
