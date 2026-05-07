"""
tools/sql_executor.py — Executes SQL queries against the active database.

User is shown the query and must confirm before execution.
Results are formatted as a readable table in the terminal.
"""

from sqlalchemy import text
from tools.base import BaseTool, ToolResult

# DDL statements that modify schema structure
_DDL_KEYWORDS = {"CREATE", "ALTER", "DROP", "RENAME"}


def is_schema_change(sql: str) -> bool:
    """Return True if the SQL statement modifies the database schema."""
    try:
        first_token = sql.strip().split()[0].upper()
        return first_token in _DDL_KEYWORDS
    except IndexError:
        return False


class SQLExecutorTool(BaseTool):
    """
    Executes a SQL query against the connected database.
    Shows the query to the user for confirmation before running.
    """

    name = "sql_executor"
    description = "Execute a SQL query against the database and display results"
    requires_confirmation = True

    def __init__(self, engine):
        """
        Args:
            engine: SQLAlchemy engine connected to the target database.
        """
        self.engine = engine

    def confirm(self, **kwargs) -> str:
        """Show the SQL query that will be executed."""
        query = kwargs.get("query", "")
        if not query or query.startswith("-- ERROR"):
            return ""

        lines = [
            "  The following SQL will be executed:\n",
            f"  {query}\n",
        ]

        # Warn about write operations
        upper = query.strip().upper()
        if any(upper.startswith(kw) for kw in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE")):
            lines.append("  ⚠️  This is a WRITE operation — it will modify the database.")

        return "\n".join(lines)

    def execute(self, **kwargs) -> ToolResult:
        """Run the SQL query and return results."""
        query = kwargs.get("query", "")

        if not query or query.startswith("-- ERROR"):
            return ToolResult(
                success=False,
                error="No valid SQL query to execute",
            )

        try:
            with self.engine.connect() as conn:
                result = conn.execute(text(query))

                # Check if this is a SELECT (returns rows)
                if result.returns_rows:
                    columns = list(result.keys())
                    rows = result.fetchall()

                    if not rows:
                        return ToolResult(
                            success=True,
                            data={"columns": columns, "rows": []},
                            message="  Query returned 0 rows.",
                        )

                    # Format as table
                    table_str = _format_table(columns, rows)

                    return ToolResult(
                        success=True,
                        data={"columns": columns, "rows": [list(r) for r in rows]},
                        message=table_str,
                        metadata={"row_count": len(rows)},
                    )
                else:
                    # Write operation
                    conn.commit()
                    rowcount = result.rowcount
                    schema_changed = is_schema_change(query)
                    return ToolResult(
                        success=True,
                        data={"rowcount": rowcount},
                        message=f"  Query executed successfully. {rowcount} row(s) affected.",
                        metadata={"schema_changed": schema_changed},
                    )

        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
            )


def _format_table(columns, rows, max_col_width=40, max_rows=50):
    """
    Format query results as a clean ASCII table.
    Truncates wide columns and limits row count.
    """
    # Convert all values to strings
    str_rows = []
    for row in rows[:max_rows]:
        str_rows.append([_truncate(str(v), max_col_width) for v in row])

    # Calculate column widths
    col_widths = []
    for i, col in enumerate(columns):
        max_w = len(str(col))
        for row in str_rows:
            if i < len(row):
                max_w = max(max_w, len(row[i]))
        col_widths.append(min(max_w, max_col_width))

    # Build table
    lines = []

    # Header
    header = " │ ".join(str(col).ljust(col_widths[i]) for i, col in enumerate(columns))
    lines.append(f"  {header}")
    separator = "─┼─".join("─" * w for w in col_widths)
    lines.append(f"  {separator}")

    # Rows
    for row in str_rows:
        row_str = " │ ".join(
            row[i].ljust(col_widths[i]) if i < len(row) else " " * col_widths[i]
            for i in range(len(columns))
        )
        lines.append(f"  {row_str}")

    # Footer
    total = len(rows)
    shown = min(total, max_rows)
    if total > max_rows:
        lines.append(f"\n  ... showing {shown} of {total} rows")
    else:
        lines.append(f"\n  {total} row(s) returned.")

    return "\n".join(lines)


def _truncate(s, max_len):
    """Truncate a string and add ellipsis if too long."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."
