"""
graph_builder.py — Reflects a live SQLite DB via SQLAlchemy and builds a
directed NetworkX graph of the schema.

Nodes  = tables   (attributes: columns, pk, patterns)
Edges  = FK links (attributes: fk_column, ref_column, on_delete, nullable)

Pattern detection is column-name-based only (no row counting).
"""

import networkx as nx
from sqlalchemy import inspect, MetaData


# ── Pattern detection ───────────────────────────────────────────────────────

def _detect_patterns(columns, fk_columns):
    """
    Detect structural patterns from column names alone.
    Returns a list of pattern strings.
    """
    col_names = {c["name"] for c in columns}
    patterns = []

    # Soft-delete
    if "deleted_at" in col_names:
        patterns.append("soft_delete")

    # Timestamped
    if "created_at" in col_names or "updated_at" in col_names:
        patterns.append("timestamped")

    # Audited (has *_by columns)
    if any(n.endswith("_by") for n in col_names):
        patterns.append("audited")

    # Junction table detection
    non_pk_non_fk = col_names - fk_columns - {"id"}
    if len(fk_columns) >= 2:
        if len(non_pk_non_fk) == 0:
            patterns.append("junction_pure")
        else:
            patterns.append("junction_with_payload")

    return patterns


# ── Graph building ──────────────────────────────────────────────────────────

def build_graph(engine):
    """
    Reflect the DB and return a NetworkX DiGraph.

    Each node is a table with attributes:
        - columns: list of {name, type, nullable, primary_key}
        - pk: list of PK column names
        - patterns: list of detected pattern strings

    Each edge (source → target) is a FK relationship with attributes:
        - fk_column: column in source table
        - ref_column: column in target table
        - on_delete: CASCADE / RESTRICT / SET NULL / etc.
        - nullable: whether the FK column is nullable
    """
    inspector = inspect(engine)
    metadata = MetaData()
    metadata.reflect(bind=engine)

    graph = nx.DiGraph()
    table_names = inspector.get_table_names()

    # ── Pass 1: create nodes ──
    for table_name in table_names:
        columns_raw = inspector.get_columns(table_name)
        pk_info = inspector.get_pk_constraint(table_name)
        fk_list = inspector.get_foreign_keys(table_name)

        # Structured column info
        columns = []
        for col in columns_raw:
            columns.append(
                {
                    "name": col["name"],
                    "type": str(col["type"]),
                    "nullable": col.get("nullable", True),
                    "primary_key": col["name"] in (pk_info.get("constrained_columns") or []),
                }
            )

        # FK column names (for pattern detection)
        fk_columns = set()
        for fk in fk_list:
            fk_columns.update(fk["constrained_columns"])

        patterns = _detect_patterns(columns, fk_columns)

        graph.add_node(
            table_name,
            columns=columns,
            pk=pk_info.get("constrained_columns") or [],
            patterns=patterns,
        )

    # ── Pass 2: create edges ──
    for table_name in table_names:
        fk_list = inspector.get_foreign_keys(table_name)
        columns_raw = inspector.get_columns(table_name)

        # Build a quick nullable lookup
        nullable_map = {c["name"]: c.get("nullable", True) for c in columns_raw}

        for fk in fk_list:
            source = table_name
            target = fk["referred_table"]
            fk_col = fk["constrained_columns"][0]  # single-column FK assumed
            ref_col = fk["referred_columns"][0]
            on_delete = (fk.get("options") or {}).get("ondelete", "NO ACTION")

            graph.add_edge(
                source,
                target,
                fk_column=fk_col,
                ref_column=ref_col,
                on_delete=on_delete,
                nullable=nullable_map.get(fk_col, True),
            )

    return graph


def print_graph(graph):
    """Pretty-print the graph for debugging."""
    print("\n" + "=" * 60)
    print("SCHEMA GRAPH")
    print("=" * 60)

    for node in sorted(graph.nodes):
        data = graph.nodes[node]
        col_summary = ", ".join(
            f"{c['name']}({'PK' if c['primary_key'] else c['type']})"
            for c in data["columns"]
        )
        patterns_str = ", ".join(data["patterns"]) if data["patterns"] else "none"
        print(f"\n📦 {node}")
        print(f"   columns: {col_summary}")
        print(f"   patterns: [{patterns_str}]")

        # Outgoing edges
        for _, target, edata in graph.out_edges(node, data=True):
            print(
                f"   → {target} via {edata['fk_column']}→{edata['ref_column']} "
                f"(ondelete={edata['on_delete']}, nullable={edata['nullable']})"
            )

        # Incoming edges
        for source, _, edata in graph.in_edges(node, data=True):
            print(
                f"   ← {source} via {edata['fk_column']}→{edata['ref_column']} "
                f"(ondelete={edata['on_delete']})"
            )

    print("\n" + "=" * 60)


if __name__ == "__main__":
    from database import get_engine, create_schema, seed_data

    engine = create_schema()
    seed_data(engine)
    g = build_graph(engine)
    print_graph(g)
