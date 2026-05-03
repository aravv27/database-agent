"""
context_engine.py — Assembles graph-neighborhood context for each table
and uses NVIDIA NIM to generate structured descriptions.

Uses JSON structured output so the LLM never goes off-format.
Model: nvidia/llama-3.1-nemotron-70b-instruct (best structured-output model on NIM).
"""

import os
import json
import time

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── NVIDIA NIM client ───────────────────────────────────────────────────────

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


# ── Neighborhood assembly ──────────────────────────────────────────────────

def _build_neighborhood(graph, table_name):
    """Build a text block describing a table's position in the schema graph."""
    node = graph.nodes[table_name]
    lines = [f"Table: {table_name}"]

    # Columns
    cols = []
    for c in node["columns"]:
        tag = "PK" if c["primary_key"] else c["type"]
        nullable = ", nullable" if c["nullable"] and not c["primary_key"] else ""
        cols.append(f"  {c['name']} ({tag}{nullable})")
    lines.append("Columns:\n" + "\n".join(cols))

    # Outgoing FKs
    out_fks = []
    for _, tgt, ed in graph.out_edges(table_name, data=True):
        req = "required" if not ed["nullable"] else "optional"
        out_fks.append(f"  → {tgt} via {ed['fk_column']} ({req}, ondelete={ed['on_delete']})")
    if out_fks:
        lines.append("Points to:\n" + "\n".join(out_fks))

    # Incoming FKs
    in_fks = []
    for src, _, ed in graph.in_edges(table_name, data=True):
        in_fks.append(f"  ← {src} via {ed['fk_column']} (ondelete={ed['on_delete']})")
    if in_fks:
        lines.append("Pointed to by:\n" + "\n".join(in_fks))

    # Patterns
    if node["patterns"]:
        lines.append(f"Patterns: {', '.join(node['patterns'])}")

    # Siblings (tables sharing the same parent via outgoing FKs)
    siblings = set()
    for _, parent, _ in graph.out_edges(table_name, data=True):
        for sibling, _, _ in graph.in_edges(parent, data=True):
            if sibling != table_name:
                siblings.add(sibling)
    if siblings:
        lines.append(f"Sibling tables: {', '.join(sorted(siblings))}")

    return "\n".join(lines)


# ── LLM description generation ─────────────────────────────────────────────

DESCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "1-2 sentence description of what this table represents and its business purpose"
        },
        "business_role": {
            "type": "string",
            "enum": ["core_entity", "transaction", "junction", "detail", "reference", "audit"],
            "description": "The business role of this table in the schema"
        }
    },
    "required": ["description", "business_role"]
}


def generate_description(graph, table_name):
    """
    Call NVIDIA NIM to generate a structured description for one table.
    Returns dict with 'description' and 'business_role' keys.
    """
    neighborhood = _build_neighborhood(graph, table_name)
    client = _get_client()

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a database schema analyst. You receive a table's position "
                        "in a schema graph and produce a structured JSON description. "
                        "Be concise and precise. Respond ONLY with valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Analyze this table and respond with JSON matching this schema:\n"
                        f'{json.dumps(DESCRIPTION_SCHEMA, indent=2)}\n\n'
                        f"{neighborhood}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()
        result = json.loads(raw)

        # Validate required fields
        if "description" not in result or "business_role" not in result:
            raise ValueError(f"Missing required fields in response: {raw}")

        return result

    except Exception as e:
        # Fallback: template-based description
        cols = ", ".join(c["name"] for c in graph.nodes[table_name]["columns"])
        return {
            "description": f"{table_name} table with columns: {cols}",
            "business_role": "core_entity",
            "_error": str(e),
        }


def generate_all_descriptions(graph, stale_tables=None, cached_descriptions=None):
    """
    Generate descriptions for tables. Only calls LLM for stale tables.
    Returns {table_name: {description, business_role}}.
    """
    cached_descriptions = cached_descriptions or {}
    all_tables = set(graph.nodes)
    stale = stale_tables if stale_tables is not None else all_tables

    descriptions = {}

    # Load unchanged from cache
    for t in all_tables - stale:
        if t in cached_descriptions:
            descriptions[t] = cached_descriptions[t]

    # Generate new/changed
    total_stale = len(stale & all_tables)
    for i, t in enumerate(sorted(stale & all_tables)):
        print(f"  Generating description [{i+1}/{total_stale}]: {t}")
        descriptions[t] = generate_description(graph, t)
        # Rate-limit: small sleep between API calls
        if i < total_stale - 1:
            time.sleep(0.5)

    return descriptions


if __name__ == "__main__":
    from database import get_engine, create_schema, seed_data
    from graph_builder import build_graph

    engine = create_schema()
    seed_data(engine)
    g = build_graph(engine)

    descs = generate_all_descriptions(g)
    for table, info in sorted(descs.items()):
        print(f"\n📦 {table}")
        print(f"   {info['description']}")
        print(f"   role: {info['business_role']}")
        if "_error" in info:
            print(f"   ⚠️  fallback (error: {info['_error']})")
