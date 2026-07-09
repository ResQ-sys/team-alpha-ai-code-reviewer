"""
Chart/infographic helpers for the code-review dashboard and PDF report.

Every chart is driven by the same finding list so the Streamlit UI (Plotly,
interactive) and the exported PDF (matplotlib PNG, static) always agree. Pure
aggregation lives in the ``*_counts`` helpers; rendering is layered on top.
"""
from __future__ import annotations

import io
from collections import Counter
from typing import Dict, List, Optional

# Consistent severity ordering + colors across UI and PDF.
SEVERITY_ORDER = ["CRITICAL", "ERROR", "WARNING", "INFO"]
SEVERITY_COLORS = {
    "CRITICAL": "#b71c1c",
    "ERROR": "#e53935",
    "WARNING": "#fb8c00",
    "INFO": "#1e88e5",
}
CATEGORY_LABELS = {
    "syntax": "Syntax errors",
    "logic": "Logical bugs",
    "security": "Security vulns",
    "quality": "Quality issues",
}
CATEGORY_COLORS = {
    "syntax": "#8e24aa",
    "logic": "#3949ab",
    "security": "#e53935",
    "quality": "#43a047",
}


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def severity_counts(findings: List[Dict]) -> Dict[str, int]:
    c = Counter(f.get("severity", "INFO") for f in findings)
    return {s: c.get(s, 0) for s in SEVERITY_ORDER if c.get(s, 0)}


def category_counts(findings: List[Dict], quality_issues: List[Dict]) -> Dict[str, int]:
    """Consolidated 'types of issues' across bugs/vulns + quality issues."""
    c = Counter()
    for f in findings:
        c[f.get("category", "security")] += 1
    if quality_issues:
        c["quality"] += len(quality_issues)
    return {k: v for k, v in c.items() if v}


def source_counts(findings: List[Dict]) -> Dict[str, int]:
    c = Counter(f.get("source", "unknown") for f in findings)
    return dict(c.most_common())


def top_rules(findings: List[Dict], limit: int = 8) -> Dict[str, int]:
    c = Counter(f.get("rule_id", "unknown") for f in findings)
    return dict(c.most_common(limit))


def per_file_counts(findings: List[Dict], limit: int = 10) -> Dict[str, int]:
    c = Counter(f.get("file", "?").split("/")[-1] for f in findings)
    return dict(c.most_common(limit))


# --------------------------------------------------------------------------- #
# Plotly (Streamlit UI)
# --------------------------------------------------------------------------- #
def plotly_pie(counts: Dict[str, int], title: str,
               color_map: Optional[Dict[str, str]] = None,
               label_map: Optional[Dict[str, str]] = None):
    import plotly.graph_objects as go

    keys = list(counts.keys())
    labels = [(label_map or {}).get(k, k) for k in keys]
    values = [counts[k] for k in keys]
    colors = [(color_map or {}).get(k) for k in keys] if color_map else None

    fig = go.Figure(
        data=[go.Pie(labels=labels, values=values, hole=0.4,
                     marker=dict(colors=colors),
                     textinfo="label+value+percent", sort=False)]
    )
    fig.update_layout(title=title, showlegend=True,
                      margin=dict(t=50, b=10, l=10, r=10), height=360)
    return fig


def plotly_gauge(score: float, title: str = "Quality Score"):
    """A radial gauge — a friendlier headline infographic than a bare number."""
    import plotly.graph_objects as go

    if score >= 80:
        bar_color = "#2e7d32"
    elif score >= 55:
        bar_color = "#fb8c00"
    else:
        bar_color = "#c62828"

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"suffix": " /100", "font": {"size": 34}},
        title={"text": title, "font": {"size": 18}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1},
            "bar": {"color": bar_color, "thickness": 0.3},
            "steps": [
                {"range": [0, 55], "color": "#ffebee"},
                {"range": [55, 80], "color": "#fff3e0"},
                {"range": [80, 100], "color": "#e8f5e9"},
            ],
            "threshold": {"line": {"color": bar_color, "width": 4},
                          "thickness": 0.75, "value": score},
        },
    ))
    fig.update_layout(height=280, margin=dict(t=50, b=10, l=20, r=20))
    return fig


def plotly_treemap(findings: List[Dict], title: str = "Findings map (file → severity)"):
    """Treemap of findings grouped by file then severity — an at-a-glance
    'where are the problems concentrated' infographic."""
    import plotly.express as px

    if not findings:
        return None
    rows = [{
        "file": f.get("file", "?").split("/")[-1],
        "severity": f.get("severity", "INFO"),
        "rule": f.get("rule_id", ""),
    } for f in findings]
    fig = px.treemap(
        rows, path=["file", "severity"],
        color="severity", color_discrete_map=SEVERITY_COLORS,
    )
    fig.update_layout(title=title, height=380,
                      margin=dict(t=50, b=10, l=10, r=10))
    return fig


def plotly_bar(counts: Dict[str, int], title: str, x_title: str = "count"):
    import plotly.graph_objects as go

    keys = list(counts.keys())[::-1]
    values = [counts[k] for k in keys]
    fig = go.Figure(data=[go.Bar(x=values, y=keys, orientation="h",
                                 marker=dict(color="#5c6bc0"))])
    fig.update_layout(title=title, xaxis_title=x_title,
                      margin=dict(t=50, b=10, l=10, r=10),
                      height=max(260, 40 * len(keys) + 80))
    return fig


# --------------------------------------------------------------------------- #
# Matplotlib (PDF export) — returns PNG bytes
# --------------------------------------------------------------------------- #
def _mpl_setup():
    import matplotlib
    matplotlib.use("Agg")


def pie_png(counts: Dict[str, int], title: str,
            color_map: Optional[Dict[str, str]] = None,
            label_map: Optional[Dict[str, str]] = None) -> Optional[bytes]:
    if not counts:
        return None
    _mpl_setup()
    import matplotlib.pyplot as plt

    keys = list(counts.keys())
    labels = [(label_map or {}).get(k, k) for k in keys]
    values = [counts[k] for k in keys]
    colors = [(color_map or {}).get(k) for k in keys] if color_map else None

    fig, ax = plt.subplots(figsize=(4.2, 3.4))
    ax.pie(values, labels=labels, colors=colors, autopct="%1.0f%%",
           startangle=90, textprops={"fontsize": 8})
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.axis("equal")
    return _fig_to_png(fig)


def bar_png(counts: Dict[str, int], title: str) -> Optional[bytes]:
    if not counts:
        return None
    _mpl_setup()
    import matplotlib.pyplot as plt

    keys = list(counts.keys())[::-1]
    values = [counts[k] for k in keys]
    fig, ax = plt.subplots(figsize=(4.6, max(2.6, 0.4 * len(keys) + 1)))
    ax.barh(keys, values, color="#5c6bc0")
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.tick_params(labelsize=8)
    for i, v in enumerate(values):
        ax.text(v, i, f" {v}", va="center", fontsize=8)
    fig.tight_layout()
    return _fig_to_png(fig)


def _fig_to_png(fig) -> bytes:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
