"""
desc_metrics_visualization.py — Generate PNG charts from api_metrics.json
and assemble them into a self-contained HTML dashboard.

Run from project root:
    python tests/desc_metrics_visualization.py

Output:
    tests/charts/  — individual PNG files
    tests/metrics_dashboard.html — HTML page embedding all charts
"""

import os, sys, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import MaxNLocator
import base64

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS_IN  = os.path.join(ROOT, "tests", "api_metrics.json")
CHARTS_DIR  = os.path.join(ROOT, "tests", "charts")
DASH_OUT    = os.path.join(ROOT, "tests", "metrics_dashboard.html")
os.makedirs(CHARTS_DIR, exist_ok=True)

with open(METRICS_IN, encoding="utf-8") as f:
    data = json.load(f)

summary   = data["summary"]
per_table = data["per_table"]
tables    = [r["table"]              for r in per_table]
latency   = [r["latency_ms"]        for r in per_table]
prompt_t  = [r["prompt_tokens"]     for r in per_table]
compl_t   = [r["completion_tokens"] for r in per_table]
total_t   = [r["total_tokens"]      for r in per_table]
nbr_chars = [r["neighborhood_chars"]for r in per_table]
desc_chars= [r["description_chars"] for r in per_table]
roles     = [r["business_role"]     for r in per_table]

# ── Shared style ───────────────────────────────────────────────────────────
BG      = "#0f0f1a"
SURFACE = "#1a1a2e"
ACCENT  = "#6c63ff"
TEAL    = "#00d4aa"
CORAL   = "#ff6b6b"
GOLD    = "#ffd166"
TEXT    = "#e0e0e0"
MUTED   = "#666680"

ROLE_PALETTE = {
    "core_entity":           "#6c63ff",
    "transaction":           "#00d4aa",
    "junction":              "#ffd166",
    "detail":                "#ff6b6b",
    "reference":             "#a8dadc",
    "audit":                 "#e07a5f",
}

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

generated = []   # (title, filepath) list for HTML assembly


def save(fig, name, title):
    path = os.path.join(CHARTS_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    generated.append((title, path))
    print(f"  ✓ {name}")
    return path


# ══════════════════════════════════════════════════════════════════════════
# Chart 1 — Latency Distribution (histogram + KDE + percentile lines)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(11, 5))
fig.patch.set_facecolor(BG)

bins = np.linspace(min(latency), max(latency), 28)
n, _, patches = ax.hist(latency, bins=bins, color=ACCENT, alpha=0.75, edgecolor=BG, linewidth=0.8)

# Colour bars by percentile zone
p50 = np.percentile(latency, 50)
p95 = np.percentile(latency, 95)
for patch, left in zip(patches, bins[:-1]):
    if left < p50:
        patch.set_facecolor(TEAL)
    elif left < p95:
        patch.set_facecolor(ACCENT)
    else:
        patch.set_facecolor(CORAL)

# KDE overlay
from scipy.stats import gaussian_kde
kde  = gaussian_kde(latency, bw_method=0.35)
xs   = np.linspace(min(latency), max(latency), 300)
ys   = kde(xs) * len(latency) * (bins[1]-bins[0])
ax.plot(xs, ys, color=GOLD, linewidth=2.5, label="KDE")

for pct, val, col, ls in [(50, p50, TEAL, "--"), (95, p95, CORAL, ":")]:
    ax.axvline(val, color=col, linewidth=1.8, linestyle=ls, label=f"p{pct} = {val:.0f}ms")

ax.set_xlabel("Latency (ms)", labelpad=8)
ax.set_ylabel("Number of API calls", labelpad=8)
ax.set_title("API Call Latency Distribution  —  102 tables", fontsize=14, fontweight="bold",
             color=TEXT, pad=14)
ax.legend(framealpha=0, labelcolor=TEXT)
ax.grid(axis="y", alpha=0.5)
ax.yaxis.set_major_locator(MaxNLocator(integer=True))
save(fig, "01_latency_distribution.png", "Latency Distribution")


# ══════════════════════════════════════════════════════════════════════════
# Chart 2 — Token Usage per Table (horizontal bar, sorted by total_tokens)
# ══════════════════════════════════════════════════════════════════════════
order = sorted(range(len(tables)), key=lambda i: total_t[i])
s_tables  = [tables[i]  for i in order]
s_prompt  = [prompt_t[i] for i in order]
s_compl   = [compl_t[i]  for i in order]

fig, ax = plt.subplots(figsize=(13, 22))
fig.patch.set_facecolor(BG)
y = np.arange(len(s_tables))

ax.barh(y, s_prompt, color=ACCENT,  alpha=0.85, label="Prompt tokens",     height=0.7)
ax.barh(y, s_compl,  left=s_prompt, color=TEAL,   alpha=0.9,  label="Completion tokens", height=0.7)

ax.set_yticks(y)
ax.set_yticklabels(s_tables, fontsize=7.5)
ax.set_xlabel("Tokens", labelpad=8)
ax.set_title("Token Usage per Table (Prompt + Completion)", fontsize=14, fontweight="bold",
             color=TEXT, pad=14)
ax.legend(framealpha=0, labelcolor=TEXT)
ax.grid(axis="x", alpha=0.4)
ax.set_axisbelow(True)
save(fig, "02_token_usage_per_table.png", "Token Usage Per Table")


# ══════════════════════════════════════════════════════════════════════════
# Chart 3 — Business Role Distribution (donut)
# ══════════════════════════════════════════════════════════════════════════
role_counts = summary["business_role_distribution"]
r_labels = list(role_counts.keys())
r_values = list(role_counts.values())
r_colors = [ROLE_PALETTE.get(r, "#888") for r in r_labels]

fig, ax = plt.subplots(figsize=(8, 8))
fig.patch.set_facecolor(BG)

wedges, texts, autotexts = ax.pie(
    r_values, labels=None, colors=r_colors,
    autopct="%1.0f%%", pctdistance=0.78,
    wedgeprops=dict(width=0.55, edgecolor=BG, linewidth=2),
    startangle=140,
)
for at in autotexts:
    at.set_fontsize(12); at.set_color(BG); at.set_fontweight("bold")

legend_labels = [f"{r}  ({c})" for r, c in zip(r_labels, r_values)]
ax.legend(wedges, legend_labels, loc="lower center", bbox_to_anchor=(0.5, -0.08),
          ncol=2, framealpha=0, labelcolor=TEXT, fontsize=11)
ax.set_title("Business Role Distribution", fontsize=14, fontweight="bold", color=TEXT, pad=18)
ax.text(0, 0, f"{sum(r_values)}\ntables", ha="center", va="center",
        fontsize=16, fontweight="bold", color=TEXT)
save(fig, "03_business_role_donut.png", "Business Role Distribution")


# ══════════════════════════════════════════════════════════════════════════
# Chart 4 — Neighborhood Size vs Latency (scatter, coloured by role)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(11, 6))
fig.patch.set_facecolor(BG)

for role in set(roles):
    mask = [i for i, r in enumerate(roles) if r == role]
    ax.scatter(
        [nbr_chars[i] for i in mask],
        [latency[i]   for i in mask],
        c=ROLE_PALETTE.get(role, "#888"),
        label=role, s=55, alpha=0.82, edgecolors=BG, linewidths=0.5,
    )

# Trend line
m, b = np.polyfit(nbr_chars, latency, 1)
xs = np.array([min(nbr_chars), max(nbr_chars)])
ax.plot(xs, m*xs+b, color=GOLD, linewidth=1.8, linestyle="--", label=f"trend  (slope={m:.2f})")

ax.set_xlabel("Neighborhood size (chars in prompt context)", labelpad=8)
ax.set_ylabel("Latency (ms)", labelpad=8)
ax.set_title("Prompt Context Size vs API Latency", fontsize=14, fontweight="bold", color=TEXT, pad=14)
ax.legend(framealpha=0, labelcolor=TEXT, fontsize=10)
ax.grid(alpha=0.4)
save(fig, "04_neighborhood_vs_latency.png", "Context Size vs Latency")


# ══════════════════════════════════════════════════════════════════════════
# Chart 5 — Prompt vs Completion Tokens (scatter)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(9, 6))
fig.patch.set_facecolor(BG)

sc = ax.scatter(prompt_t, compl_t,
                c=latency, cmap="plasma", s=55,
                alpha=0.85, edgecolors=BG, linewidths=0.4)
cbar = fig.colorbar(sc, ax=ax, pad=0.02)
cbar.set_label("Latency (ms)", color=TEXT, fontsize=11)
cbar.ax.yaxis.set_tick_params(color=MUTED)
plt.setp(cbar.ax.yaxis.get_ticklabels(), color=MUTED)

ax.set_xlabel("Prompt tokens", labelpad=8)
ax.set_ylabel("Completion tokens", labelpad=8)
ax.set_title("Prompt vs Completion Tokens  (colour = latency)", fontsize=14,
             fontweight="bold", color=TEXT, pad=14)
ax.grid(alpha=0.35)
save(fig, "05_prompt_vs_completion.png", "Prompt vs Completion Tokens")


# ══════════════════════════════════════════════════════════════════════════
# Chart 6 — Latency per Table (sorted bar, colour = role)
# ══════════════════════════════════════════════════════════════════════════
order2 = sorted(range(len(tables)), key=lambda i: latency[i], reverse=True)
s2_tables  = [tables[i]  for i in order2]
s2_latency = [latency[i] for i in order2]
s2_roles   = [roles[i]   for i in order2]
s2_colors  = [ROLE_PALETTE.get(r, "#888") for r in s2_roles]

fig, ax = plt.subplots(figsize=(20, 6))
fig.patch.set_facecolor(BG)

x = np.arange(len(s2_tables))
bars = ax.bar(x, s2_latency, color=s2_colors, alpha=0.85, edgecolor=BG, linewidth=0.5, width=0.8)

ax.axhline(summary["latency_ms"]["mean"], color=GOLD,  linewidth=1.8, linestyle="--",
           label=f"mean = {summary['latency_ms']['mean']:.0f}ms")
ax.axhline(summary["latency_ms"]["p95"],  color=CORAL, linewidth=1.8, linestyle=":",
           label=f"p95  = {summary['latency_ms']['p95']:.0f}ms")

ax.set_xticks(x)
ax.set_xticklabels(s2_tables, rotation=90, fontsize=6.5)
ax.set_ylabel("Latency (ms)", labelpad=8)
ax.set_title("API Call Latency per Table  (sorted desc, colour = business role)",
             fontsize=14, fontweight="bold", color=TEXT, pad=14)

legend_patches = [mpatches.Patch(color=c, label=r) for r, c in ROLE_PALETTE.items()]
ax.legend(handles=legend_patches + [
    plt.Line2D([0],[0], color=GOLD,  linewidth=1.8, linestyle="--", label=f"mean = {summary['latency_ms']['mean']:.0f}ms"),
    plt.Line2D([0],[0], color=CORAL, linewidth=1.8, linestyle=":",  label=f"p95 = {summary['latency_ms']['p95']:.0f}ms"),
], framealpha=0, labelcolor=TEXT, fontsize=9, ncol=4, loc="upper right")
ax.grid(axis="y", alpha=0.4)
ax.set_axisbelow(True)
save(fig, "06_latency_per_table.png", "Latency per Table")


# ══════════════════════════════════════════════════════════════════════════
# Chart 7 — Summary Stats Card (text figure)
# ══════════════════════════════════════════════════════════════════════════
fig, ax = plt.subplots(figsize=(10, 5))
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.axis("off")

stats = [
    ("Total API calls",          f"{summary['total_api_calls']}"),
    ("Success / Fallback",       f"{summary['success_count']} / {summary['fallback_count']}"),
    ("Total prompt tokens",      f"{summary['total_prompt_tokens']:,}"),
    ("Total completion tokens",  f"{summary['total_completion_tokens']:,}"),
    ("Total tokens",             f"{summary['total_tokens']:,}"),
    ("Token throughput",         f"{summary['token_throughput_per_sec']:,} tok/s"),
    ("Latency  min / mean / p95 / max",
     f"{summary['latency_ms']['min']:.0f}ms  /  {summary['latency_ms']['mean']:.0f}ms  /  "
     f"{summary['latency_ms']['p95']:.0f}ms  /  {summary['latency_ms']['max']:.0f}ms"),
    ("Latency stdev",            f"{summary['latency_ms']['stdev']:.1f}ms"),
    ("Avg neighborhood chars",   f"{summary['avg_neighborhood_chars']:,.1f}"),
    ("Avg description chars",    f"{summary['avg_description_chars']:,.1f}"),
    ("Finish reasons",           "  ".join(f"{k}: {v}" for k,v in summary["finish_reason_breakdown"].items())),
]

ax.text(0.5, 0.97, "API Metrics — Summary", transform=ax.transAxes,
        fontsize=16, fontweight="bold", color=TEXT, ha="center", va="top")

for i, (label, value) in enumerate(stats):
    y = 0.85 - i * 0.077
    ax.text(0.03, y, label, transform=ax.transAxes, fontsize=11, color=MUTED, va="top")
    ax.text(0.97, y, value, transform=ax.transAxes, fontsize=11, color=TEXT,
            va="top", ha="right", fontweight="bold")
    ax.plot([0.03, 0.97], [y - 0.015, y - 0.015],
            transform=ax.transAxes, color="#2a2a3e", linewidth=0.8, clip_on=False)

save(fig, "07_summary_stats.png", "Summary Statistics")


# ══════════════════════════════════════════════════════════════════════════
# Assemble HTML Dashboard
# ══════════════════════════════════════════════════════════════════════════
def img_tag(path, title):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"""
    <div class="card">
      <h2>{title}</h2>
      <img src="data:image/png;base64,{b64}" alt="{title}">
    </div>"""

cards_html = "\n".join(img_tag(path, title) for title, path in generated)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Description Pipeline — API Metrics Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #0f0f1a;
    color: #e0e0e0;
    font-family: 'Inter', sans-serif;
    min-height: 100vh;
    padding: 40px 24px 60px;
  }}
  header {{
    text-align: center;
    margin-bottom: 48px;
  }}
  header h1 {{
    font-size: 2rem;
    font-weight: 700;
    background: linear-gradient(135deg, #6c63ff 0%, #00d4aa 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 8px;
  }}
  header p {{ color: #666680; font-size: 0.95rem; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 760px), 1fr));
    gap: 28px;
    max-width: 1600px;
    margin: 0 auto;
  }}
  .card {{
    background: #1a1a2e;
    border: 1px solid rgba(108,99,255,0.18);
    border-radius: 20px;
    padding: 28px;
    transition: transform 0.2s, box-shadow 0.2s;
  }}
  .card:hover {{
    transform: translateY(-4px);
    box-shadow: 0 12px 40px rgba(108,99,255,0.18);
  }}
  .card h2 {{
    font-size: 1rem;
    font-weight: 600;
    color: #a0a0c0;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin-bottom: 18px;
  }}
  .card img {{
    width: 100%;
    border-radius: 10px;
    display: block;
  }}
  .card.wide {{ grid-column: 1 / -1; }}
  footer {{
    text-align: center;
    margin-top: 60px;
    color: #444466;
    font-size: 0.8rem;
  }}
</style>
</head>
<body>
<header>
  <h1>Description Pipeline — API Metrics Dashboard</h1>
  <p>{summary['total_api_calls']} API calls &middot;
     {summary['total_tokens']:,} total tokens &middot;
     mean latency {summary['latency_ms']['mean']:.0f}ms &middot;
     {summary['success_count']} / {summary['total_api_calls']} succeeded</p>
</header>

<div class="grid">
  {img_tag(generated[6][1], generated[6][0])}
  {img_tag(generated[2][1], generated[2][0])}
  {img_tag(generated[0][1], generated[0][0])}
  {img_tag(generated[3][1], generated[3][0])}
  {img_tag(generated[4][1], generated[4][0])}
  <div class="card wide">
    <h2>{generated[5][0]}</h2>
    <img src="data:image/png;base64,{
      base64.b64encode(open(generated[5][1],'rb').read()).decode()
    }" alt="{generated[5][0]}">
  </div>
  <div class="card wide">
    <h2>{generated[1][0]}</h2>
    <img src="data:image/png;base64,{
      base64.b64encode(open(generated[1][1],'rb').read()).decode()
    }" alt="{generated[1][0]}">
  </div>
</div>

<footer>Generated from tests/api_metrics.json &mdash; Graph-Guided Database CRUD Generator</footer>
</body>
</html>"""

with open(DASH_OUT, "w", encoding="utf-8") as f:
    f.write(html)

import webbrowser
print(f"\n  Dashboard saved to: {DASH_OUT}")
webbrowser.open(f"file:///{DASH_OUT.replace(os.sep, '/')}")
print("  Opened in browser.")
