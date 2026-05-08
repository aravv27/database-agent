"""
query_test_visualization.py — Simple bar/line charts from query_metrics.json.

Run from project root:
    python tests/query_test_visualization.py

Output:
    tests/query_charts/  — individual PNGs
    tests/query_dashboard.html — HTML page with all charts
"""

import os, sys, json, base64
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS_IN = os.path.join(ROOT, "tests", "query_metrics.json")
CHARTS_DIR = os.path.join(ROOT, "tests", "query_charts")
DASH_OUT   = os.path.join(ROOT, "tests", "query_dashboard.html")
os.makedirs(CHARTS_DIR, exist_ok=True)

with open(METRICS_IN, encoding="utf-8") as f:
    data = json.load(f)

summary   = data["summary"]
per_query = data["per_query"]

# Extract arrays
labels     = [f"Q{r['query_index']}" for r in per_query]
queries    = [r["query"] for r in per_query]
prompt_t   = [r["prompt_tokens"] for r in per_query]
compl_t    = [r["completion_tokens"] for r in per_query]
total_t    = [r["total_tokens"] for r in per_query]
focus_c    = [r["focus_table_count"] for r in per_query]
seed_c     = [r["seed_count"] for r in per_query]
join_c     = [r["join_path_count"] for r in per_query]
agent_lat  = [r["agent_latency_ms"] for r in per_query]
ret_lat    = [r["retrieval_latency_ms"] for r in per_query]
k_vals     = [r["retrieval_k"] for r in per_query]
prompt_ch  = [r["prompt_chars"] for r in per_query]

# ── Style ──
BG      = "#0f0f1a"
SURFACE = "#1a1a2e"
ACCENT  = "#6c63ff"
TEAL    = "#00d4aa"
CORAL   = "#ff6b6b"
GOLD    = "#ffd166"
TEXT    = "#e0e0e0"
MUTED   = "#666680"

plt.rcParams.update({
    "figure.facecolor":  BG,
    "axes.facecolor":    SURFACE,
    "axes.edgecolor":    MUTED,
    "axes.labelcolor":   TEXT,
    "xtick.color":       MUTED,
    "ytick.color":       MUTED,
    "text.color":        TEXT,
    "grid.color":        "#2a2a3e",
    "grid.linestyle":    "--",
    "grid.linewidth":    0.6,
    "font.family":       "DejaVu Sans",
    "font.size":         11,
})

generated = []

def save(fig, name, title):
    path = os.path.join(CHARTS_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    generated.append((title, path))
    print(f"  ✓ {name}")


# ══════════════════════════════════════════════════════════════════════════
# 1 — Token Usage per Query (stacked bar)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(16, 5))
x = np.arange(len(labels))
ax.bar(x, prompt_t, color=ACCENT, alpha=0.85, label="Prompt tokens", width=0.7)
ax.bar(x, compl_t, bottom=prompt_t, color=TEAL, alpha=0.9, label="Completion tokens", width=0.7)
ax.axhline(summary["total_tokens_per_query"]["mean"], color=GOLD, linewidth=1.5,
           linestyle="--", label=f"Mean = {summary['total_tokens_per_query']['mean']:.0f}")
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel("Tokens")
ax.set_title("Token Usage per Query (Prompt + Completion)", fontsize=14,
             fontweight="bold", color=TEXT, pad=12)
ax.legend(framealpha=0, labelcolor=TEXT, fontsize=10)
ax.grid(axis="y", alpha=0.4)
ax.set_axisbelow(True)
save(fig, "01_tokens_per_query.png", "Token Usage per Query")


# ══════════════════════════════════════════════════════════════════════════
# 2 — Focus Tables per Query (bar, colored by K)
# ══════════════════════════════════════════════════════════════════════════
k_colors = {1: TEAL, 2: ACCENT, 3: GOLD, 4: CORAL, 5: "#a8dadc", 6: "#e07a5f"}
bar_colors = [k_colors.get(k, MUTED) for k in k_vals]

fig, ax = plt.subplots(figsize=(16, 5))
bars = ax.bar(x, focus_c, color=bar_colors, alpha=0.85, width=0.7, edgecolor=BG)

# Annotate K value on each bar
for i, (bar, k) in enumerate(zip(bars, k_vals)):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
            f"K={k}", ha="center", va="bottom", fontsize=7, color=MUTED)

ax.axhline(summary["focus_tables_per_query"]["mean"], color=GOLD, linewidth=1.5,
           linestyle="--", label=f"Mean = {summary['focus_tables_per_query']['mean']:.0f}")
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel("Focus Tables in Prompt")
ax.set_title("Focus Tables Injected per Query  (label = adaptive K)", fontsize=14,
             fontweight="bold", color=TEXT, pad=12)
ax.legend(framealpha=0, labelcolor=TEXT, fontsize=10)
ax.grid(axis="y", alpha=0.4)
ax.set_axisbelow(True)
save(fig, "02_focus_tables_per_query.png", "Focus Tables per Query")


# ══════════════════════════════════════════════════════════════════════════
# 3 — Focus Tables vs Prompt Tokens (scatter + trend line)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(10, 6))
sc = ax.scatter(focus_c, prompt_t, c=[k_colors.get(k, MUTED) for k in k_vals],
                s=65, alpha=0.85, edgecolors=BG, linewidths=0.5, zorder=5)

# Trend line
m, b = np.polyfit(focus_c, prompt_t, 1)
xs = np.array([min(focus_c), max(focus_c)])
ax.plot(xs, m*xs + b, color=GOLD, linewidth=2, linestyle="--",
        label=f"Trend: ~{m:.0f} tokens/table", zorder=4)

# Annotate outliers
for i, (fc, pt) in enumerate(zip(focus_c, prompt_t)):
    if fc > 20 or pt > 4000:
        ax.annotate(labels[i], (fc, pt), fontsize=8, color=MUTED,
                    xytext=(5, 5), textcoords="offset points")

ax.set_xlabel("Focus Tables (in prompt)")
ax.set_ylabel("Prompt Tokens")
ax.set_title("Focus Tables → Prompt Token Cost  (colour = K)", fontsize=14,
             fontweight="bold", color=TEXT, pad=12)
ax.legend(framealpha=0, labelcolor=TEXT, fontsize=10)
ax.grid(alpha=0.4)
save(fig, "03_focus_vs_tokens.png", "Focus Tables vs Prompt Tokens")


# ══════════════════════════════════════════════════════════════════════════
# 4 — Latency per Query (grouped bar: retrieval + agent)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(16, 5))
w = 0.35
ax.bar(x - w/2, ret_lat,   width=w, color=TEAL,   alpha=0.85, label="Retrieval")
ax.bar(x + w/2, agent_lat, width=w, color=ACCENT,  alpha=0.85, label="SQL Agent (API)")
ax.axhline(summary["agent_latency_ms"]["mean"], color=CORAL, linewidth=1.5,
           linestyle="--", label=f"Agent mean = {summary['agent_latency_ms']['mean']:.0f}ms")
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel("Latency (ms)")
ax.set_title("Latency per Query — Retrieval vs SQL Agent", fontsize=14,
             fontweight="bold", color=TEXT, pad=12)
ax.legend(framealpha=0, labelcolor=TEXT, fontsize=10)
ax.grid(axis="y", alpha=0.4)
ax.set_axisbelow(True)
save(fig, "04_latency_per_query.png", "Latency per Query")


# ══════════════════════════════════════════════════════════════════════════
# 5 — Adaptive K Distribution (bar)
# ══════════════════════════════════════════════════════════════════════════
k_dist = summary["adaptive_k_distribution"]
k_keys = sorted(k_dist.keys(), key=int)
k_counts = [k_dist[k] for k in k_keys]

fig, ax = plt.subplots(figsize=(8, 5))
bars = ax.bar(k_keys, k_counts, color=[k_colors.get(int(k), MUTED) for k in k_keys],
              alpha=0.85, width=0.5, edgecolor=BG)
for bar, count in zip(bars, k_counts):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.2,
            str(count), ha="center", va="bottom", fontsize=12, fontweight="bold", color=TEXT)
ax.set_xlabel("Adaptive K (seed count)")
ax.set_ylabel("Number of Queries")
ax.set_title("Adaptive K Distribution across 25 Queries", fontsize=14,
             fontweight="bold", color=TEXT, pad=12)
ax.yaxis.set_major_locator(MaxNLocator(integer=True))
ax.grid(axis="y", alpha=0.4)
ax.set_axisbelow(True)
save(fig, "05_k_distribution.png", "Adaptive K Distribution")


# ══════════════════════════════════════════════════════════════════════════
# 6 — Seeds + Focus + Join Paths per Query (grouped line)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(16, 5))
ax.plot(x, seed_c, color=TEAL, marker="o", markersize=5, linewidth=2, label="Seed tables")
ax.plot(x, focus_c, color=ACCENT, marker="s", markersize=5, linewidth=2, label="Focus tables")
ax.plot(x, join_c, color=CORAL, marker="^", markersize=5, linewidth=2, label="Join paths")
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel("Count")
ax.set_title("Retrieval Expansion — Seeds → Focus Tables → Join Paths", fontsize=14,
             fontweight="bold", color=TEXT, pad=12)
ax.legend(framealpha=0, labelcolor=TEXT, fontsize=10)
ax.grid(alpha=0.4)
save(fig, "06_retrieval_expansion.png", "Retrieval Expansion")


# ══════════════════════════════════════════════════════════════════════════
# 7 — Query detail table (summary card)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(12, 5))
ax.set_facecolor(BG)
ax.axis("off")

stats = [
    ("Queries",                f"{summary['success_count']} / {summary['total_queries']}  (0 errors)"),
    ("Total tokens",           f"{summary['total_tokens']:,}"),
    ("Prompt tokens / query",  f"min {summary['prompt_tokens_per_query']['min']}  ·  "
                               f"mean {summary['prompt_tokens_per_query']['mean']:.0f}  ·  "
                               f"max {summary['prompt_tokens_per_query']['max']}"),
    ("Agent latency / query",  f"min {summary['agent_latency_ms']['min']:.0f}ms  ·  "
                               f"mean {summary['agent_latency_ms']['mean']:.0f}ms  ·  "
                               f"p95 {summary['agent_latency_ms']['p95']:.0f}ms"),
    ("Retrieval latency",      f"min {summary['retrieval_latency_ms']['min']:.0f}ms  ·  "
                               f"mean {summary['retrieval_latency_ms']['mean']:.0f}ms"),
    ("Focus tables / query",   f"min {summary['focus_tables_per_query']['min']}  ·  "
                               f"mean {summary['focus_tables_per_query']['mean']:.0f}  ·  "
                               f"max {summary['focus_tables_per_query']['max']}"),
    ("Tokens per focus table",  f"{summary['focus_to_tokens_ratio']}"),
    ("Auth chain hit rate",    f"{summary['auth_chain_hit_rate']}%"),
    ("Finish reasons",         "  ".join(f"{k}: {v}" for k, v in summary["finish_reason_breakdown"].items())),
]

ax.text(0.5, 0.97, "Query Pipeline — Summary", transform=ax.transAxes,
        fontsize=16, fontweight="bold", color=TEXT, ha="center", va="top")

for i, (label, value) in enumerate(stats):
    y = 0.85 - i * 0.09
    ax.text(0.03, y, label, transform=ax.transAxes, fontsize=11, color=MUTED, va="top")
    ax.text(0.97, y, value, transform=ax.transAxes, fontsize=11, color=TEXT,
            va="top", ha="right", fontweight="bold")
    ax.plot([0.03, 0.97], [y - 0.02, y - 0.02],
            transform=ax.transAxes, color="#2a2a3e", linewidth=0.8, clip_on=False)

save(fig, "07_summary.png", "Summary Statistics")


# ══════════════════════════════════════════════════════════════════════════
# HTML Dashboard
# ══════════════════════════════════════════════════════════════════════════
def img_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()

def card(title, path, wide=False):
    cls = ' class="card wide"' if wide else ' class="card"'
    return f"""<div{cls}><h2>{title}</h2><img src="data:image/png;base64,{img_b64(path)}" alt="{title}"></div>"""

# Query reference table rows
q_rows = ""
for r in per_query:
    q_rows += (
        f"<tr><td>Q{r['query_index']}</td>"
        f"<td>{r['query']}</td>"
        f"<td>{r['retrieval_k']}</td>"
        f"<td>{r['seed_count']}</td>"
        f"<td>{r['focus_table_count']}</td>"
        f"<td>{r['total_tokens']:,}</td>"
        f"<td>{r['agent_latency_ms']:.0f}</td></tr>\n"
    )

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Query Pipeline — Benchmark Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0f0f1a; color: #e0e0e0;
    font-family: 'Inter', sans-serif;
    padding: 40px 24px 60px;
  }}
  header {{ text-align: center; margin-bottom: 48px; }}
  header h1 {{
    font-size: 2rem; font-weight: 700;
    background: linear-gradient(135deg, #6c63ff 0%, #00d4aa 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text; margin-bottom: 8px;
  }}
  header p {{ color: #666680; font-size: 0.95rem; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 700px), 1fr));
    gap: 28px; max-width: 1600px; margin: 0 auto;
  }}
  .card {{
    background: #1a1a2e; border: 1px solid rgba(108,99,255,0.18);
    border-radius: 20px; padding: 28px;
    transition: transform 0.2s, box-shadow 0.2s;
  }}
  .card:hover {{ transform: translateY(-4px); box-shadow: 0 12px 40px rgba(108,99,255,0.18); }}
  .card h2 {{
    font-size: 1rem; font-weight: 600; color: #a0a0c0;
    text-transform: uppercase; letter-spacing: 0.6px; margin-bottom: 18px;
  }}
  .card img {{ width: 100%; border-radius: 10px; display: block; }}
  .card.wide {{ grid-column: 1 / -1; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ text-align: left; color: #a0a0c0; padding: 8px 10px; border-bottom: 2px solid #2a2a3e; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #1a1a2e; color: #ccc; }}
  tr:hover td {{ background: rgba(108,99,255,0.05); }}
  footer {{ text-align: center; margin-top: 60px; color: #444466; font-size: 0.8rem; }}
</style>
</head>
<body>
<header>
  <h1>Query Pipeline — Benchmark Dashboard</h1>
  <p>{summary['total_queries']} queries &middot;
     {summary['total_tokens']:,} total tokens &middot;
     mean latency {summary['agent_latency_ms']['mean']:.0f}ms &middot;
     {summary['success_count']}/{summary['total_queries']} succeeded</p>
</header>
<div class="grid">
  {card(generated[6][0], generated[6][1])}
  {card(generated[4][0], generated[4][1])}
  {card(generated[0][0], generated[0][1], wide=True)}
  {card(generated[1][0], generated[1][1], wide=True)}
  {card(generated[2][0], generated[2][1])}
  {card(generated[3][0], generated[3][1], wide=True)}
  {card(generated[5][0], generated[5][1], wide=True)}
  <div class="card wide">
    <h2>Query Reference Table</h2>
    <table>
      <tr><th>#</th><th>Query</th><th>K</th><th>Seeds</th><th>Focus</th><th>Tokens</th><th>Latency</th></tr>
      {q_rows}
    </table>
  </div>
</div>
<footer>Generated from tests/query_metrics.json</footer>
</body>
</html>"""

with open(DASH_OUT, "w", encoding="utf-8") as f:
    f.write(html)

import webbrowser
print(f"\n  Dashboard saved to: {DASH_OUT}")
webbrowser.open(f"file:///{DASH_OUT.replace(os.sep, '/')}")
print("  Opened in browser.")
