"""
agents/schema_agent.py — Designs new database schemas from natural language.

Uses Gemini 2.5 Flash (via google-genai) with Pydantic schema enforcement
for reliable large JSON output. Does NOT need retrieval context.

Supports both SQLite and PostgreSQL dialects with separate system prompts
and separate DDL generators.
"""

import os
import json
from typing import Optional
from pydantic import BaseModel

from google import genai
from google.genai import types
from dotenv import load_dotenv

from agents.base import BaseAgent

load_dotenv()

_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _gemini_client


# ── Pydantic schema (Gemini enforces this exactly) ─────────────────────────

class ColumnSpec(BaseModel):
    name: str
    type: str           # INTEGER, TEXT, REAL, BOOLEAN, DATETIME
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    default: Optional[str] = None


class ForeignKeySpec(BaseModel):
    column: str
    references_table: str
    references_column: str
    on_delete: str = "RESTRICT"   # CASCADE, RESTRICT, SET NULL, NO ACTION


class TableSpec(BaseModel):
    name: str
    columns: list[ColumnSpec]
    foreign_keys: list[ForeignKeySpec] = []


class SchemaSpec(BaseModel):
    database_name: str
    tables: list[TableSpec]
    explanation: str


# ── System prompts (separate per dialect) ──────────────────────────────────

SQLITE_SYSTEM_PROMPT = (
    "You are a senior database architect specializing in SQLite. "
    "The user describes what they want to build. Design a clean, normalized schema.\n"
    "Rules:\n"
    "- Integer primary keys named 'id' with autoincrement (primary_key=true, nullable=false)\n"
    "- FK ON DELETE: CASCADE for owned children (comments owned by post), "
    "RESTRICT for shared references (user referenced by post)\n"
    "- SQLite column types: INTEGER, TEXT, REAL, BOOLEAN, DATETIME\n"
    "- Add created_at and updated_at DATETIME (nullable=false) on transactional tables\n"
    "- Add deleted_at DATETIME (nullable=true) on core user-facing entities for soft delete\n"
    "- Mark NOT NULL (nullable=false) on required fields\n"
    "- Use snake_case for all names\n"
)

POSTGRES_SYSTEM_PROMPT = (
    "You are a senior database architect specializing in PostgreSQL. "
    "The user describes what they want to build. Design a clean, normalized schema.\n"
    "Rules:\n"
    "- Integer primary keys named 'id' (primary_key=true, nullable=false, type=INTEGER). "
    "The DDL generator will handle SERIAL for you.\n"
    "- FK ON DELETE: CASCADE for owned children (comments owned by post), "
    "RESTRICT for shared references (user referenced by post)\n"
    "- PostgreSQL column types: INTEGER, VARCHAR(n), TEXT, BOOLEAN, "
    "TIMESTAMP, NUMERIC, JSONB, UUID\n"
    "- Use VARCHAR(n) for bounded strings, TEXT for unbounded text\n"
    "- Use TIMESTAMP for all timestamps (created_at, updated_at, deleted_at)\n"
    "- Add created_at and updated_at TIMESTAMP (nullable=false) on transactional tables\n"
    "- Add deleted_at TIMESTAMP (nullable=true) on core user-facing entities for soft delete\n"
    "- Mark NOT NULL (nullable=false) on required fields\n"
    "- Use snake_case for all names\n"
)


# ── SQLite DDL generation ──────────────────────────────────────────────────

def schema_to_sql_sqlite(spec: dict) -> list[str]:
    """
    Convert the structured schema spec into SQLite CREATE TABLE statements.
    Returns statements in dependency order (parents before children).
    """
    tables = spec.get("tables", [])
    name_to_table = {t["name"]: t for t in tables}
    ordered = []
    visited = set()

    def visit(table_name):
        if table_name in visited:
            return
        visited.add(table_name)            # mark BEFORE recursing — breaks any cycle
        table = name_to_table.get(table_name)
        if not table:
            return
        for fk in table.get("foreign_keys", []):
            visit(fk["references_table"])
        ordered.append(table)

    for t in tables:
        visit(t["name"])

    statements = []
    for table in ordered:
        col_defs = []

        for col in table.get("columns", []):
            col_type = col.get("type", "TEXT").upper()
            parts = [f'  "{col["name"]}" {col_type}']

            if col.get("primary_key"):
                parts.append("PRIMARY KEY AUTOINCREMENT")
            if not col.get("nullable", True) and not col.get("primary_key"):
                parts.append("NOT NULL")
            if col.get("unique") and not col.get("primary_key"):
                parts.append("UNIQUE")
            if col.get("default") and col["default"] not in ("", "null", "NULL", None):
                dv = str(col["default"])
                # Only wrap in quotes if it's not a number, not a function, and not already quoted
                if not (dv.startswith("(") or dv.replace(".", "").isdigit() or (dv.startswith("'") and dv.endswith("'"))):
                    dv = f"'{dv}'"
                parts.append(f"DEFAULT {dv}")

            col_defs.append(" ".join(parts))

        for fk in table.get("foreign_keys", []):
            on_delete = fk.get("on_delete", "RESTRICT").upper()
            col_defs.append(
                f'  FOREIGN KEY ("{fk["column"]}") '
                f'REFERENCES "{fk["references_table"]}"("{fk["references_column"]}") '
                f'ON DELETE {on_delete}'
            )

        body = ",\n".join(col_defs)
        statements.append(f'CREATE TABLE IF NOT EXISTS "{table["name"]}" (\n{body}\n);')

    return statements


# ── PostgreSQL DDL generation ──────────────────────────────────────────────

_POSTGRES_TYPE_MAP = {
    "REAL": "DOUBLE PRECISION",
    "FLOAT": "DOUBLE PRECISION",
    "DATETIME": "TIMESTAMP",
}


def _map_postgres_type(col_type: str) -> str:
    """Map generic/SQLite types to PostgreSQL equivalents."""
    upper = col_type.upper()
    # Handle VARCHAR(n) — already valid Postgres
    if upper.startswith("VARCHAR("):
        return col_type
    return _POSTGRES_TYPE_MAP.get(upper, col_type)


def schema_to_sql_postgres(spec: dict) -> list[str]:
    """
    Convert the structured schema spec into PostgreSQL CREATE TABLE statements.
    Returns statements in dependency order (parents before children).
    """
    tables = spec.get("tables", [])
    name_to_table = {t["name"]: t for t in tables}
    ordered = []
    visited = set()

    def visit(table_name):
        if table_name in visited:
            return
        visited.add(table_name)
        table = name_to_table.get(table_name)
        if not table:
            return
        for fk in table.get("foreign_keys", []):
            visit(fk["references_table"])
        ordered.append(table)

    for t in tables:
        visit(t["name"])

    statements = []
    for table in ordered:
        col_defs = []

        for col in table.get("columns", []):
            col_type = col.get("type", "TEXT").upper()

            if col.get("primary_key"):
                # Use SERIAL for integer PKs, otherwise just PRIMARY KEY
                if col_type in ("INTEGER", "INT"):
                    parts = [f'  "{col["name"]}" SERIAL PRIMARY KEY']
                else:
                    parts = [f'  "{col["name"]}" {_map_postgres_type(col_type)} PRIMARY KEY']
            else:
                pg_type = _map_postgres_type(col_type)
                parts = [f'  "{col["name"]}" {pg_type}']

                if not col.get("nullable", True):
                    parts.append("NOT NULL")
                if col.get("unique"):
                    parts.append("UNIQUE")
                if col.get("default") and col["default"] not in ("", "null", "NULL", None):
                    dv = str(col["default"])
                    if not (dv.startswith("(") or dv.replace(".", "").isdigit() or (dv.startswith("'") and dv.endswith("'"))):
                        dv = f"'{dv}'"
                    parts.append(f"DEFAULT {dv}")

            col_defs.append(" ".join(parts))

        for fk in table.get("foreign_keys", []):
            on_delete = fk.get("on_delete", "RESTRICT").upper()
            col_defs.append(
                f'  FOREIGN KEY ("{fk["column"]}") '
                f'REFERENCES "{fk["references_table"]}"("{fk["references_column"]}") '
                f'ON DELETE {on_delete}'
            )

        body = ",\n".join(col_defs)
        statements.append(f'CREATE TABLE IF NOT EXISTS "{table["name"]}" (\n{body}\n);')

    return statements


def format_table_details(spec: dict) -> str:
    """Pretty-print schema for user confirmation."""
    lines = []
    for table in spec.get("tables", []):
        lines.append(f"\n  [{table['name']}]")
        for col in table.get("columns", []):
            pk = " PK" if col.get("primary_key") else ""
            null = "" if col.get("nullable", True) else " NOT NULL"
            uniq = " UNIQUE" if col.get("unique") else ""
            lines.append(f"    {col['name']} {col.get('type','TEXT')}{pk}{null}{uniq}")
        for fk in table.get("foreign_keys", []):
            od = fk.get("on_delete", "RESTRICT")
            lines.append(
                f"    FK: {fk['column']} -> {fk['references_table']}.{fk['references_column']} (ON DELETE {od})"
            )
    return "\n".join(lines)


# ── Agent ──────────────────────────────────────────────────────────────────

class SchemaDesignAgent(BaseAgent):
    """
    Designs a new database schema from plain-English requirements.
    Uses Gemini 2.5 Flash for large structured JSON output.
    Does not require retrieval context.
    """

    name = "schema"
    description = "Design a new database schema from natural language requirements"
    needs_context = False

    # Default prompt (SQLite) — kept for base class compatibility
    system_prompt = SQLITE_SYSTEM_PROMPT
    max_tokens = 8192

    def ask(self, query: str, context: dict) -> dict:
        """Override: use Gemini instead of NVIDIA NIM. Dialect-aware."""
        client = _get_gemini_client()
        dialect = context.get("dialect", "sqlite")

        # Select the correct system prompt
        if dialect == "postgresql":
            prompt_text = POSTGRES_SYSTEM_PROMPT
        else:
            prompt_text = SQLITE_SYSTEM_PROMPT

        prompt = (
            f"{prompt_text}\n\n"
            f"=== User Requirements ===\n{query}"
        )

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=SchemaSpec.model_json_schema(),
                    temperature=0.1,
                ),
            )
            result = json.loads(response.text)
            return self.post_process(result, dialect=dialect)

        except Exception as e:
            return self._error_result(str(e))

    def post_process(self, result: dict, dialect: str = "sqlite") -> dict:
        result.setdefault("tables", [])
        result.setdefault("database_name", "new_database")
        result.setdefault("explanation", "")

        # Use the correct DDL generator based on dialect
        if dialect == "postgresql":
            result["_sql_statements"] = schema_to_sql_postgres(result)
        else:
            result["_sql_statements"] = schema_to_sql_sqlite(result)

        result["_table_details"] = format_table_details(result)
        result["_dialect"] = dialect
        return result

    def _error_result(self, error_msg: str) -> dict:
        return {
            "database_name": "",
            "tables": [],
            "explanation": "",
            "_error": error_msg,
            "_sql_statements": [],
            "_table_details": "",
        }

    def format_result(self, result: dict) -> str:
        if result.get("_error"):
            return f"\n  Error: {result['_error']}"

        dialect_label = result.get("_dialect", "sqlite").upper()
        lines = [
            "",
            "-" * 60,
            f"  Database: {result['database_name']} ({dialect_label})",
            f"  Tables ({len(result['tables'])}):",
            result.get("_table_details", ""),
            "",
            f"  Design notes: {result.get('explanation', '')}",
            "",
            "  CREATE TABLE statements:",
            "",
        ]
        for stmt in result.get("_sql_statements", []):
            lines.append(stmt)
            lines.append("")
        lines.append("-" * 60)
        return "\n".join(lines)
