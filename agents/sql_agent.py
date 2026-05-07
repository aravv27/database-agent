"""
agents/sql_agent.py — Generates SQL queries from natural language.

Given a retrieval context package and a user query, produces a
structured response with: sql_query, explanation, tables_used, warnings.

Supports both SQLite and PostgreSQL dialects with separate system prompts.
"""

from agents.base import BaseAgent


# ── System prompts (separate per dialect) ──────────────────────────────────

SQLITE_SYSTEM_PROMPT = (
    "You are a SQL generation assistant. You receive a database context "
    "package describing available tables, their columns, join paths, and "
    "patterns. Generate a SQLite-compatible query that answers the user's "
    "request. ONLY use tables and columns described in the context. "
    "Do NOT invent columns or tables. Respond ONLY with valid JSON."
)

POSTGRES_SYSTEM_PROMPT = (
    "You are a SQL generation assistant. You receive a database context "
    "package describing available tables, their columns, join paths, and "
    "patterns. Generate a PostgreSQL-compatible query that answers the user's "
    "request. You may use PostgreSQL features: CTEs, window functions, "
    "RETURNING, ILIKE, array operations, JSONB operators, date/interval "
    "arithmetic, etc. ONLY use tables and columns described in the context. "
    "Do NOT invent columns or tables. Respond ONLY with valid JSON."
)


class SQLQueryAgent(BaseAgent):
    """Agent that generates dialect-aware SQL queries from natural language."""

    name = "sql"
    description = "Generate SQL queries from natural language using schema context"

    # Default prompt (SQLite) — kept for base class compatibility
    system_prompt = SQLITE_SYSTEM_PROMPT

    response_schema = {
        "type": "object",
        "properties": {
            "sql_query": {
                "type": "string",
                "description": "The SQL query that answers the user's request",
            },
            "explanation": {
                "type": "string",
                "description": "Brief explanation of what the query does and why these tables/joins were chosen",
            },
            "tables_used": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of table names used in the query",
            },
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Any caveats: soft-delete filters applied, nullable joins, etc.",
            },
        },
        "required": ["sql_query", "explanation", "tables_used"],
    }

    max_tokens = 800

    def get_system_prompt(self, context: dict) -> str:
        """Select system prompt based on the active database dialect."""
        dialect = context.get("dialect", "sqlite")
        if dialect == "postgresql":
            return POSTGRES_SYSTEM_PROMPT
        return SQLITE_SYSTEM_PROMPT

    def post_process(self, result):
        """Ensure optional fields have defaults."""
        result.setdefault("warnings", [])
        result.setdefault("tables_used", [])
        result.setdefault("sql_query", "-- ERROR: no query generated")
        result.setdefault("explanation", "")
        return result

    def _error_result(self, error_msg):
        return {
            "sql_query": "-- ERROR: could not generate query",
            "explanation": f"LLM call failed: {error_msg}",
            "tables_used": [],
            "warnings": [error_msg],
        }

    def format_result(self, result):
        """Format SQL result for terminal display."""
        lines = [
            "",
            "\u2500" * 60,
            "\U0001f4dd SQL Query:",
            result.get("sql_query", ""),
            f"\n\U0001f4a1 Explanation: {result.get('explanation', '')}",
        ]
        if result.get("tables_used"):
            lines.append(f"\U0001f4e6 Tables: {', '.join(result['tables_used'])}")
        if result.get("warnings"):
            lines.append("\u26a0\ufe0f  Warnings:")
            for w in result["warnings"]:
                lines.append(f"   \u2022 {w}")
        lines.append("\u2500" * 60)
        return "\n".join(lines)
