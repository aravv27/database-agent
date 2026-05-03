"""
visualize.py — Interactive graph visualization using pyvis.

Generates a self-contained HTML file and opens it in the default browser.
Nodes are color-coded by detected patterns, edges show FK relationships.
Custom sidebar panel shows table details on click.
"""

import os
import webbrowser
import json
from pyvis.network import Network


# ── Color palette by pattern ────────────────────────────────────────────────

PATTERN_COLORS = {
    "soft_delete":            "#e74c3c",
    "timestamped":            "#3498db",
    "junction_with_payload":  "#e67e22",
    "junction_pure":          "#f39c12",
    "audited":                "#9b59b6",
}

DEFAULT_COLOR = "#2ecc71"

EDGE_COLORS = {
    "CASCADE":   "#e74c3c",
    "RESTRICT":  "#7f8c8d",
    "SET NULL":  "#3498db",
    "NO ACTION": "#bdc3c7",
}


def _get_node_color(patterns):
    for p in ["soft_delete", "junction_pure", "junction_with_payload", "audited", "timestamped"]:
        if p in patterns:
            return PATTERN_COLORS[p]
    return DEFAULT_COLOR


def _build_node_data_json(graph, descriptions):
    """Build a JSON dict of all node metadata for the JS side panel."""
    data = {}
    for table_name in graph.nodes:
        node = graph.nodes[table_name]
        desc_info = descriptions.get(table_name, {})
        desc_text = desc_info.get("description", "") if isinstance(desc_info, dict) else ""
        role = desc_info.get("business_role", "") if isinstance(desc_info, dict) else ""

        out_edges = []
        for _, tgt, ed in graph.out_edges(table_name, data=True):
            out_edges.append({
                "target": tgt,
                "fk_column": ed["fk_column"],
                "ref_column": ed["ref_column"],
                "on_delete": ed.get("on_delete", "NO ACTION"),
                "nullable": ed.get("nullable", True),
            })

        in_edges = []
        for src, _, ed in graph.in_edges(table_name, data=True):
            in_edges.append({
                "source": src,
                "fk_column": ed["fk_column"],
                "ref_column": ed["ref_column"],
                "on_delete": ed.get("on_delete", "NO ACTION"),
            })

        data[table_name] = {
            "description": desc_text,
            "business_role": role,
            "columns": node["columns"],
            "patterns": node.get("patterns", []),
            "outgoing": out_edges,
            "incoming": in_edges,
        }
    return data


def visualize_graph(graph, descriptions=None, output_path=None, open_browser=True):
    descriptions = descriptions or {}
    if output_path is None:
        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_graph.html")

    net = Network(
        height="100vh",
        width="100%",
        directed=True,
        bgcolor="#0f0f1a",
        font_color="#eee",
        select_menu=False,
        filter_menu=False,
    )

    net.set_options("""
    {
        "nodes": {
            "borderWidth": 2,
            "borderWidthSelected": 4,
            "font": {
                "size": 18,
                "face": "Inter, Segoe UI, sans-serif",
                "color": "#eee",
                "strokeWidth": 4,
                "strokeColor": "#0f0f1a"
            },
            "shadow": {
                "enabled": true,
                "color": "rgba(0,0,0,0.5)",
                "size": 15
            }
        },
        "edges": {
            "arrows": { "to": { "enabled": true, "scaleFactor": 0.8 } },
            "font": {
                "size": 12,
                "face": "Inter, Segoe UI, sans-serif",
                "color": "#888",
                "strokeWidth": 3,
                "strokeColor": "#0f0f1a",
                "align": "middle"
            },
            "smooth": { "type": "curvedCW", "roundness": 0.2 },
            "width": 2,
            "shadow": { "enabled": true, "color": "rgba(0,0,0,0.3)", "size": 8 }
        },
        "physics": {
            "forceAtlas2Based": {
                "gravitationalConstant": -200,
                "centralGravity": 0.005,
                "springLength": 300,
                "springConstant": 0.03,
                "damping": 0.5
            },
            "solver": "forceAtlas2Based",
            "stabilization": { "iterations": 300 }
        },
        "interaction": {
            "hover": true,
            "tooltipDelay": 200,
            "navigationButtons": false,
            "zoomView": true
        }
    }
    """)

    # ── Add nodes ──
    for table_name in sorted(graph.nodes):
        node_data = graph.nodes[table_name]
        color = _get_node_color(node_data.get("patterns", []))
        col_count = len(node_data.get("columns", []))
        size = 25 + col_count * 4

        # Simple plain-text tooltip (one line)
        desc_info = descriptions.get(table_name, {})
        desc_text = desc_info.get("description", "") if isinstance(desc_info, dict) else ""
        tooltip = f"{table_name}: {desc_text}" if desc_text else table_name

        net.add_node(
            table_name,
            label=table_name,
            title=tooltip,
            color={
                "background": color,
                "border": color,
                "highlight": {"background": "#fff", "border": color},
                "hover": {"background": color, "border": "#fff"},
            },
            size=size,
            shape="dot",
        )

    # ── Add edges ──
    for src, tgt, edata in graph.edges(data=True):
        on_delete = edata.get("on_delete", "NO ACTION")
        edge_color = EDGE_COLORS.get(on_delete, "#bdc3c7")
        nullable = edata.get("nullable", True)

        label = edata["fk_column"]
        tooltip = f"{src}.{edata['fk_column']} -> {tgt}.{edata['ref_column']} | ON DELETE {on_delete} | {'Optional' if nullable else 'Required'}"

        net.add_edge(
            src, tgt,
            label=label,
            title=tooltip,
            color={"color": edge_color, "highlight": "#fff", "hover": "#fff"},
            dashes=nullable,
            width=3 if not nullable else 2,
        )

    # Save base HTML
    net.save_graph(output_path)

    # ── Inject custom UI overlays ──
    node_data_json = json.dumps(_build_node_data_json(graph, descriptions))

    custom_css = """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        body { margin: 0; overflow: hidden; font-family: 'Inter', 'Segoe UI', sans-serif; }
        #mynetwork { border: none !important; }

        .overlay-panel {
            position: fixed;
            background: rgba(15,15,26,0.97);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 16px;
            font-family: 'Inter', 'Segoe UI', sans-serif;
            z-index: 9999;
            backdrop-filter: blur(20px);
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
        }

        #title-bar {
            position: fixed; top: 20px; left: 50%; transform: translateX(-50%);
            background: rgba(15,15,26,0.95); border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px; padding: 14px 32px;
            font-family: 'Inter', sans-serif; z-index: 9999;
            backdrop-filter: blur(20px); box-shadow: 0 4px 24px rgba(0,0,0,0.4);
            text-align: center;
        }
        #title-bar h1 { font-size: 20px; font-weight: 700; color: #fff; margin: 0; letter-spacing: -0.3px; }
        #title-bar p { font-size: 12px; color: #666; margin: 4px 0 0 0; }

        #legend {
            position: fixed; bottom: 24px; left: 24px;
            padding: 18px 22px; color: #ccc; font-size: 12px;
        }
        #legend .legend-title { font-size: 14px; font-weight: 700; color: #fff; margin-bottom: 12px; }
        #legend .legend-section { margin-bottom: 10px; }
        #legend .legend-section-title { font-weight: 600; color: #aaa; margin-bottom: 5px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
        #legend .legend-item { display: flex; align-items: center; gap: 8px; padding: 2px 0; }
        #legend .legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
        #legend .legend-line { width: 16px; height: 2px; display: inline-block; flex-shrink: 0; }
        #legend .legend-line.dashed { border-top: 2px dashed; height: 0; }

        #detail-panel {
            position: fixed; top: 80px; right: 24px; width: 360px;
            max-height: calc(100vh - 120px); overflow-y: auto;
            padding: 24px; color: #ddd; font-size: 13px;
            transform: translateX(420px);
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        #detail-panel.visible { transform: translateX(0); }
        #detail-panel .detail-title { font-size: 20px; font-weight: 700; color: #fff; margin-bottom: 4px; }
        #detail-panel .detail-role { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; padding: 3px 10px; border-radius: 20px; display: inline-block; margin-bottom: 12px; }
        #detail-panel .detail-desc { color: #aaa; line-height: 1.5; margin-bottom: 16px; font-size: 13px; }
        #detail-panel .detail-section { margin-bottom: 14px; }
        #detail-panel .detail-section-title { font-weight: 600; color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
        #detail-panel .col-row { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px; }
        #detail-panel .col-name { color: #ddd; font-weight: 500; }
        #detail-panel .col-type { color: #888; font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 11px; }
        #detail-panel .col-badge { font-size: 9px; padding: 1px 6px; border-radius: 8px; margin-left: 6px; font-weight: 600; }
        #detail-panel .badge-pk { background: rgba(46,204,113,0.2); color: #2ecc71; }
        #detail-panel .badge-null { background: rgba(52,152,219,0.15); color: #3498db; }
        #detail-panel .badge-fk { background: rgba(230,126,34,0.2); color: #e67e22; }
        #detail-panel .fk-row { padding: 5px 0; font-size: 12px; border-bottom: 1px solid rgba(255,255,255,0.04); }
        #detail-panel .fk-arrow { color: #666; }
        #detail-panel .pattern-tag { display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 11px; margin: 2px 4px 2px 0; font-weight: 500; }

        #detail-panel::-webkit-scrollbar { width: 4px; }
        #detail-panel::-webkit-scrollbar-track { background: transparent; }
        #detail-panel::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
    </style>
    """

    custom_html = f"""
    <div id="title-bar">
        <h1>Schema Graph</h1>
        <p>Click a node to inspect &middot; Drag to rearrange &middot; Scroll to zoom</p>
    </div>

    <div id="legend" class="overlay-panel">
        <div class="legend-title">Legend</div>
        <div class="legend-section">
            <div class="legend-section-title">Node Patterns</div>
            <div class="legend-item"><span class="legend-dot" style="background:#e74c3c"></span> soft_delete</div>
            <div class="legend-item"><span class="legend-dot" style="background:#3498db"></span> timestamped</div>
            <div class="legend-item"><span class="legend-dot" style="background:#e67e22"></span> junction (payload)</div>
            <div class="legend-item"><span class="legend-dot" style="background:#f39c12"></span> junction (pure)</div>
            <div class="legend-item"><span class="legend-dot" style="background:#9b59b6"></span> audited</div>
            <div class="legend-item"><span class="legend-dot" style="background:#2ecc71"></span> no special pattern</div>
        </div>
        <div class="legend-section">
            <div class="legend-section-title">Edges (ON DELETE)</div>
            <div class="legend-item"><span class="legend-line" style="background:#e74c3c"></span> CASCADE</div>
            <div class="legend-item"><span class="legend-line" style="background:#7f8c8d"></span> RESTRICT</div>
            <div class="legend-item"><span class="legend-line dashed" style="border-color:#3498db"></span> SET NULL (nullable)</div>
            <div class="legend-item"><span class="legend-line" style="background:#bdc3c7"></span> NO ACTION</div>
        </div>
    </div>

    <div id="detail-panel" class="overlay-panel">
        <div id="detail-content"></div>
    </div>

    <script>
    const nodeData = {node_data_json};
    const patternColors = {{
        "soft_delete": "#e74c3c", "timestamped": "#3498db",
        "junction_with_payload": "#e67e22", "junction_pure": "#f39c12",
        "audited": "#9b59b6"
    }};
    const roleColors = {{
        "core_entity": "#2ecc71", "transaction": "#e67e22", "junction": "#f39c12",
        "detail": "#3498db", "reference": "#9b59b6", "audit": "#e74c3c"
    }};

    // Wait for vis network to be ready
    setTimeout(function() {{
        if (typeof network !== 'undefined') {{
            network.on("click", function(params) {{
                const panel = document.getElementById("detail-panel");
                if (params.nodes.length > 0) {{
                    const tableName = params.nodes[0];
                    const data = nodeData[tableName];
                    if (!data) return;
                    showDetail(tableName, data);
                    panel.classList.add("visible");
                }} else {{
                    panel.classList.remove("visible");
                }}
            }});
        }}
    }}, 500);

    function showDetail(name, data) {{
        const roleColor = roleColors[data.business_role] || "#666";
        let html = `<div class="detail-title">${{name}}</div>`;

        if (data.business_role) {{
            html += `<span class="detail-role" style="background:${{roleColor}}22; color:${{roleColor}}">${{data.business_role}}</span>`;
        }}

        if (data.description) {{
            html += `<div class="detail-desc">${{data.description}}</div>`;
        }}

        // Patterns
        if (data.patterns.length > 0) {{
            html += `<div class="detail-section"><div class="detail-section-title">Patterns</div>`;
            data.patterns.forEach(p => {{
                const c = patternColors[p] || "#666";
                html += `<span class="pattern-tag" style="background:${{c}}22; color:${{c}}">${{p}}</span>`;
            }});
            html += `</div>`;
        }}

        // Columns
        html += `<div class="detail-section"><div class="detail-section-title">Columns (${{data.columns.length}})</div>`;
        // Check which columns are FK columns
        const fkCols = new Set(data.outgoing.map(e => e.fk_column));
        data.columns.forEach(col => {{
            let badges = '';
            if (col.primary_key) badges += '<span class="col-badge badge-pk">PK</span>';
            if (fkCols.has(col.name)) badges += '<span class="col-badge badge-fk">FK</span>';
            if (col.nullable && !col.primary_key) badges += '<span class="col-badge badge-null">NULL</span>';
            html += `<div class="col-row"><span class="col-name">${{col.name}}${{badges}}</span><span class="col-type">${{col.type}}</span></div>`;
        }});
        html += `</div>`;

        // Outgoing FKs
        if (data.outgoing.length > 0) {{
            html += `<div class="detail-section"><div class="detail-section-title">References (outgoing)</div>`;
            data.outgoing.forEach(e => {{
                const req = e.nullable ? "optional" : "required";
                html += `<div class="fk-row">${{e.fk_column}} <span class="fk-arrow">&rarr;</span> ${{e.target}}.${{e.ref_column}} <span style="color:#666">(${{req}}, ${{e.on_delete}})</span></div>`;
            }});
            html += `</div>`;
        }}

        // Incoming FKs
        if (data.incoming.length > 0) {{
            html += `<div class="detail-section"><div class="detail-section-title">Referenced by (incoming)</div>`;
            data.incoming.forEach(e => {{
                html += `<div class="fk-row">${{e.source}}.${{e.fk_column}} <span class="fk-arrow">&rarr;</span> ${{e.ref_column}} <span style="color:#666">(${{e.on_delete}})</span></div>`;
            }});
            html += `</div>`;
        }}

        document.getElementById("detail-content").innerHTML = html;
    }}
    </script>
    """

    # Read, inject, write
    with open(output_path, "r", encoding="utf-8") as f:
        html = f.read()

    html = html.replace("</head>", custom_css + "\n</head>")
    html = html.replace("</body>", custom_html + "\n</body>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Graph saved to: {output_path}")
    if open_browser:
        webbrowser.open(f"file:///{output_path.replace(os.sep, '/')}")
        print("  Opened in browser.")
    return output_path


if __name__ == "__main__":
    from database import get_engine, create_schema, seed_data
    from graph_builder import build_graph
    from cache import load_cache

    engine = create_schema()
    seed_data(engine)
    g = build_graph(engine)
    cache = load_cache()
    descs = cache.get("descriptions", {})
    visualize_graph(g, descs)
