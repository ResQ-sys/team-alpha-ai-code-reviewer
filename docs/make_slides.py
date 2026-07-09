"""
Generates a simple, easy-to-understand 13-slide PDF explaining the
architecture and working of the AI Software Code Reviewer, including a
9-agents reference slide, a data-flow slide and a data-sources slide.

Run:  python docs/make_slides.py
Output: docs/architecture_slides_v2.pdf
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

# ----------------------------------------------------------------------------
# Theme
# ----------------------------------------------------------------------------
NAVY = "#0f2740"
BLUE = "#1f6feb"
TEAL = "#0d9488"
AMBER = "#d97706"
RED = "#dc2626"
GREEN = "#16a34a"
LIGHT = "#f1f5f9"
GREY = "#64748b"
WHITE = "#ffffff"

plt.rcParams["font.family"] = "DejaVu Sans"

W, H = 13.33, 7.5  # 16:9 slide in inches

TOTAL_SLIDES = 13   # cover + 12 content slides (incl. 9-agents, data-flow, sources)
_PAGE = [1]         # slide 1 is the cover (no footer); footers start at 2


def new_slide():
    fig = plt.figure(figsize=(W, H), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    fig.patch.set_facecolor(WHITE)
    return fig, ax


def footer(ax):
    _PAGE[0] += 1
    ax.add_patch(plt.Rectangle((0, 0), 100, 2.2, color=NAVY, zorder=1))
    ax.text(2, 1.1, "AI Software Code Reviewer & Secure Development Agent",
            color=WHITE, fontsize=8.5, va="center", ha="left")
    ax.text(98, 1.1, f"{_PAGE[0]} / {TOTAL_SLIDES}", color=WHITE, fontsize=8.5,
            va="center", ha="right")


def header(ax, title, subtitle=None, color=NAVY):
    ax.add_patch(plt.Rectangle((0, 90), 100, 10, color=color, zorder=1))
    ax.add_patch(plt.Rectangle((0, 89.3), 100, 0.7, color=BLUE, zorder=1))
    ax.text(3, 95, title, color=WHITE, fontsize=21, fontweight="bold", va="center")
    if subtitle:
        ax.text(3, 91.4, subtitle, color="#cbd5e1", fontsize=11, va="center")


def box(ax, x, y, w, h, text, fc, tc=WHITE, fs=11, bold=True, radius=1.5, ec=None):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f"round,pad=0.2,rounding_size={radius}",
                       linewidth=1.5, edgecolor=ec or fc, facecolor=fc, zorder=3)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            color=tc, fontsize=fs, fontweight="bold" if bold else "normal",
            zorder=4, wrap=True)


def arrow(ax, x1, y1, x2, y2, color=GREY, lw=2.2):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2),
                 arrowstyle="-|>", mutation_scale=18,
                 linewidth=lw, color=color, zorder=2))


def bullet(ax, x, y, text, fs=13, color="#1e293b", dot=BLUE, gap=0):
    ax.text(x, y, "●", color=dot, fontsize=fs - 3, va="center")
    ax.text(x + 2.6, y, text, color=color, fontsize=fs, va="center")


slides = []


# ---------------------------------------------------------------------------
# Slide 1 — Title
# ---------------------------------------------------------------------------
def slide_title():
    fig, ax = new_slide()
    ax.add_patch(plt.Rectangle((0, 0), 100, 100, color=NAVY, zorder=0))
    ax.add_patch(plt.Rectangle((0, 55), 100, 2, color=BLUE, zorder=1))
    ax.text(50, 78, "AI Software Code Reviewer", color=WHITE, fontsize=38,
            fontweight="bold", ha="center")
    ax.text(50, 69, "& Secure Development Agent", color=BLUE, fontsize=30,
            fontweight="bold", ha="center")
    ax.text(50, 60, "A multi-agent pipeline that finds bugs & vulnerabilities,",
            color="#cbd5e1", fontsize=14, ha="center")
    ax.text(50, 56.2, "suggests grounded fixes, verifies them, and asks a human before applying.",
            color="#cbd5e1", fontsize=14, ha="center")

    chips = ["LangGraph", "Ollama llama3.2:1b", "Semgrep", "RAG: FAISS / TF-IDF", "Streamlit"]
    gap = 2.5
    widths = [0.95 * len(c) + 5 for c in chips]
    total = sum(widths) + gap * (len(chips) - 1)
    cx = (100 - total) / 2
    for c, w in zip(chips, widths):
        box(ax, cx, 40, w, 6, c, "#13385c", tc=WHITE, fs=10, radius=3, ec=BLUE)
        cx += w + gap
    ax.text(50, 25, "Team Alpha  •  Hackathon PS #06", color=WHITE,
            fontsize=13, ha="center", fontweight="bold")
    ax.text(50, 19, "Architecture & Working — in 13 simple slides", color="#94a3b8",
            fontsize=12, ha="center")
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 2 — What problem it solves
# ---------------------------------------------------------------------------
def slide_problem():
    fig, ax = new_slide()
    header(ax, "1. What it does", "Automating secure code review, end to end")
    ax.text(4, 84, "The problem", fontsize=15, fontweight="bold", color=NAVY)
    for i, t in enumerate([
        "Manual code review is slow and misses security bugs.",
        "Static tools flag issues but don't explain or fix them.",
        "AI fixes can hallucinate unsafe 'best practices'.",
    ]):
        bullet(ax, 5, 78 - i * 5, t, fs=13)

    ax.text(4, 58, "Our solution — one automated pipeline that:", fontsize=15,
            fontweight="bold", color=NAVY)
    steps = [
        ("Finds", "Bugs + security\nvulnerabilities", BLUE),
        ("Grounds", "Fixes in real\nsecure-coding docs", TEAL),
        ("Verifies", "Its own fixes\nbefore applying", AMBER),
        ("Guards", "Human approves\nhigh-risk changes", RED),
    ]
    x = 6
    for title, desc, c in steps:
        box(ax, x, 40, 19, 12, f"{title}", c, fs=15, radius=2)
        ax.text(x + 9.5, 35, desc, ha="center", va="center", fontsize=11,
                color="#1e293b")
        x += 22
    ax.text(50, 22, "Input: a code repository        →        Output: an explainable review report + verified fixes",
            ha="center", fontsize=13, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc=LIGHT, ec=GREY))
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 3 — High level architecture (the big picture)
# ---------------------------------------------------------------------------
def slide_bigpicture():
    fig, ax = new_slide()
    header(ax, "2. The big picture", "9 specialist agents on a LangGraph pipeline")

    box(ax, 4, 60, 16, 12, "CODE\nREPOSITORY", NAVY, fs=12, radius=2)
    arrow(ax, 20, 66, 30, 66, color=BLUE, lw=3)
    box(ax, 30, 58, 40, 16, "9-STAGE\nAI PIPELINE\n(LangGraph)", BLUE, fs=15, radius=2)
    arrow(ax, 70, 66, 80, 66, color=BLUE, lw=3)
    box(ax, 80, 60, 16, 12, "REVIEW\nREPORT", GREEN, fs=12, radius=2)

    ax.text(50, 49, "Every stage adds to one shared 'ReviewState' that flows left to right",
            ha="center", fontsize=12, color=GREY, style="italic")

    layers = [
        ("UNDERSTAND", "Ingestion  •  Static Analysis (Semgrep)  •  Code-LLM Review", BLUE),
        ("DETECT & GROUND", "Merge & Dedupe Findings  •  Secure-Coding RAG", TEAL),
        ("FIX & CHECK", "Explainable Fix Generation  •  Self-Verifier", AMBER),
        ("APPROVE & REPORT", "Human-in-the-Loop Approval  •  Final Report", RED),
    ]
    y = 38
    for name, detail, c in layers:
        box(ax, 6, y, 22, 6.4, name, c, fs=12, radius=1.5)
        box(ax, 30, y, 64, 6.4, detail, LIGHT, tc="#1e293b", fs=11.5,
            bold=False, radius=1.5, ec=c)
        y -= 8.2
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 4 — The 9-stage flow
# ---------------------------------------------------------------------------
def slide_flow():
    fig, ax = new_slide()
    header(ax, "3. The pipeline flow", "Data moves through 9 stages, in order")

    stages = [
        ("1\nIngestion", BLUE), ("2\nStatic\nAnalysis", BLUE),
        ("3\nLLM\nReview", BLUE), ("4\nVulnerability\nMerge", TEAL),
        ("5\nRAG", TEAL), ("6\nReview\nGeneration", AMBER),
        ("7\nVerifier", AMBER), ("8\nHuman\nApproval", RED),
        ("9\nReport", GREEN),
    ]
    # two rows of boxes, snaking
    positions = []
    row1 = stages[:5]
    row2 = stages[5:]
    x0, w, gap = 5, 16, 2.5
    for i, (txt, c) in enumerate(row1):
        x = x0 + i * (w + gap)
        box(ax, x, 62, w, 14, txt, c, fs=11, radius=2)
        positions.append((x, 62, w))
        if i < len(row1) - 1:
            arrow(ax, x + w, 69, x + w + gap, 69, color=GREY, lw=2.5)
    # down arrow
    lastx = x0 + 4 * (w + gap)
    arrow(ax, lastx + w / 2, 62, lastx + w / 2, 52, color=GREY, lw=2.5)
    for i, (txt, c) in enumerate(row2):
        x = x0 + (len(row2) - 1 - i) * (w + gap)  # reverse for snake
        box(ax, x, 38, w, 14, txt, c, fs=11, radius=2)
        if i < len(row2) - 1:
            nx = x0 + (len(row2) - 1 - (i + 1)) * (w + gap)
            arrow(ax, x, 45, nx + w, 45, color=GREY, lw=2.5)

    # legend
    leg = [("Understand code", BLUE), ("Detect & ground", TEAL),
           ("Fix & verify", AMBER), ("Approve / report", "#b45309")]
    lx = 8
    for name, c in leg:
        ax.add_patch(plt.Rectangle((lx, 22), 3, 3, color=c))
        ax.text(lx + 4, 23.5, name, fontsize=11, va="center", color="#1e293b")
        lx += 22
    ax.text(50, 15, "Linear, easy to trace: each stage reads the previous stage's output.",
            ha="center", fontsize=12, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 5 — The 9 agents at a glance
# ---------------------------------------------------------------------------
def slide_agents():
    fig, ax = new_slide()
    header(ax, "4. The 9 agents at a glance",
           "Each LangGraph node is one specialist agent (agents/*.py)")

    agents = [
        ("1", "Ingestion", "Discovers files, reads contents, detects language.",
         "ingestion_agent.py", BLUE),
        ("2", "Static Analysis", "Runs Semgrep security rulesets + builds a code graph.",
         "static_analysis_agent.py", BLUE),
        ("3", "LLM Review", "Code-LLM reviews each file for logic & quality bugs.",
         "llm_review_agent.py", BLUE),
        ("4", "Vulnerability", "Merges & dedupes Semgrep + LLM findings; splits security vs quality.",
         "vulnerability_agent.py", TEAL),
        ("5", "RAG", "Retrieves grounded secure-coding guidance for each finding.",
         "rag_agent.py", TEAL),
        ("6", "Review Generation", "Writes an explainable comment + a minimal, grounded fix.",
         "review_generation_agent.py", AMBER),
        ("7", "Verifier", "Syntax check, no-op check, Semgrep regression re-scan.",
         "verifier_agent.py", AMBER),
        ("8", "Approval", "Routes CRITICAL/ERROR or flagged fixes to a human.",
         "approval_agent.py", RED),
        ("9", "Report", "Assembles all artifacts as Markdown + JSON.",
         "report_agent.py", GREEN),
    ]
    y = 80
    dy = 8.4
    for num, name, desc, fname, c in agents:
        box(ax, 4, y, 7, 6, num, c, fs=15, radius=1.2)
        box(ax, 12, y, 26, 6, name, LIGHT, tc="#0f2740", fs=12, radius=1.2, ec=c)
        ax.text(40, y + 3.9, desc, fontsize=10.8, va="center", color="#1e293b")
        ax.text(40, y + 1.4, f"agents/{fname}", fontsize=8.8, va="center",
                color=GREY, family="monospace")
        y -= dy
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 6 — Stages 1-3 understanding the code
# ---------------------------------------------------------------------------
def slide_understand():
    fig, ax = new_slide()
    header(ax, "5. Understanding the code", "Stages 1–3: read → scan → reason", color=BLUE)
    cards = [
        ("1  Ingestion", "Walk the repo, read every\nsource file, detect the\nlanguage by extension.",
         "→ files + contents", BLUE),
        ("2  Static Analysis", "Run Semgrep security rules\n(OWASP / CWE). Offline\nfallback ruleset included.",
         "→ rule-based findings", TEAL),
        ("3  Code-LLM Review", "Ollama llama3.2:1b reviews\neach file for logic bugs &\nissues rules can't see.",
         "→ semantic findings", AMBER),
    ]
    x = 5
    for title, body, out, c in cards:
        box(ax, x, 55, 28, 12, title, c, fs=14, radius=2)
        ax.text(x + 14, 42, body, ha="center", va="center", fontsize=12,
                color="#1e293b")
        ax.text(x + 14, 32, out, ha="center", va="center", fontsize=11.5,
                color=c, fontweight="bold")
        if x < 60:
            arrow(ax, x + 28, 61, x + 31, 61, color=GREY, lw=2.5)
        x += 31
    ax.text(50, 22, "Two independent lenses — deterministic (Semgrep) + intelligent (LLM) — catch different bugs.",
            ha="center", fontsize=12.5, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc=LIGHT, ec=BLUE))
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 6 — Stage 4 & 5 merge + RAG intro
# ---------------------------------------------------------------------------
def slide_merge_rag():
    fig, ax = new_slide()
    header(ax, "6. Detect & ground the findings", "Stages 4–5: merge, then retrieve guidance", color=TEAL)

    box(ax, 6, 66, 24, 9, "Semgrep findings", BLUE, fs=12)
    box(ax, 6, 54, 24, 9, "LLM findings", AMBER, fs=12)
    arrow(ax, 30, 70, 40, 63, color=GREY)
    arrow(ax, 30, 58, 40, 61, color=GREY)
    box(ax, 40, 56, 24, 12, "4  MERGE\n& DEDUPE", TEAL, fs=13, radius=2)
    ax.text(52, 51, "same file+line = one finding", ha="center", fontsize=10,
            color=GREY, style="italic")
    arrow(ax, 64, 62, 74, 62, color=GREY)
    box(ax, 74, 56, 22, 12, "5  RAG\nlookup", "#0369a1", fs=13, radius=2)

    ax.text(6, 42, "Then split:", fontsize=13, fontweight="bold", color=NAVY)
    box(ax, 20, 38, 30, 7, "Security vulnerabilities", RED, fs=12)
    box(ax, 54, 38, 30, 7, "Quality issues", GREY, fs=12)

    ax.text(50, 28, "RAG = Retrieval-Augmented Generation",
            ha="center", fontsize=15, fontweight="bold", color="#0369a1")
    ax.text(50, 23, "For each finding, pull the matching secure-coding guidance from a\nknowledge base so the fix is grounded in real documentation — not guessed.",
            ha="center", fontsize=12.5, color="#1e293b")
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 7 — RAG deep dive
# ---------------------------------------------------------------------------
def slide_rag():
    fig, ax = new_slide()
    header(ax, "7. Inside the RAG engine", "How grounded guidance is retrieved", color="#0369a1")

    box(ax, 4, 74, 20, 10, "A finding\n(CWE / rule id\n+ message)", NAVY, fs=11, radius=2)
    arrow(ax, 24, 79, 33, 79, color="#0369a1", lw=2.5)
    box(ax, 33, 74, 26, 10, "1) Fast path:\nmatch by CWE / tag", TEAL, fs=11.5, radius=2)
    arrow(ax, 46, 74, 46, 66, color="#0369a1", lw=2.5)
    ax.text(61, 79, "no match?", fontsize=10.5, color=RED, style="italic", va="center")
    box(ax, 33, 56, 26, 10, "2) Semantic search:\nembedding similarity", BLUE, fs=11.5, radius=2)

    # KB
    box(ax, 68, 60, 28, 22, "KNOWLEDGE BASE\n(12 OWASP / CWE docs)\n\nSQL-i • Cmd-i • Secrets\nWeak crypto • XSS • ...",
        LIGHT, tc="#1e293b", fs=11, bold=False, radius=2, ec="#0369a1")
    arrow(ax, 59, 79, 68, 74, color=GREY)
    arrow(ax, 59, 61, 68, 65, color=GREY)

    # backends
    ax.text(6, 48, "Two interchangeable backends:", fontsize=13, fontweight="bold", color=NAVY)
    box(ax, 8, 36, 38, 9, "Primary:  sentence-transformers + FAISS", BLUE, fs=11.5, radius=1.5)
    box(ax, 54, 36, 38, 9, "Fallback:  scikit-learn TF-IDF (offline)", TEAL, fs=11.5, radius=1.5)
    ax.text(50, 30, "If embedding models can't download, it auto-switches to TF-IDF — RAG still works with no internet.",
            ha="center", fontsize=12, color=GREY, style="italic")

    ax.text(50, 22, "Retrieved snippets are injected into the fix prompt → the LLM must ground its fix in them.",
            ha="center", fontsize=12.5, color="#0369a1", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#e0f2fe", ec="#0369a1"))
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 8 — Fix generation + verifier
# ---------------------------------------------------------------------------
def slide_fix_verify():
    fig, ax = new_slide()
    header(ax, "8. Fix & self-check", "Stages 6–7: generate, then verify", color=AMBER)

    box(ax, 6, 64, 40, 14, "6  REVIEW GENERATION", AMBER, fs=14, radius=2)
    for i, t in enumerate([
        "Plain-language explanation (why it matters)",
        "A minimal, targeted code fix",
        "Grounded in the retrieved RAG guidance",
    ]):
        bullet(ax, 8, 58 - i * 4.5, t, fs=11.5, dot=AMBER)

    box(ax, 54, 64, 40, 14, "7  VERIFIER (self-reflection)", "#b45309", fs=13, radius=2)
    for i, t in enumerate([
        "Syntax check — does the fix parse?",
        "No-op check — did it actually change code?",
        "Regression re-scan — Semgrep rule gone?",
    ]):
        bullet(ax, 56, 58 - i * 4.5, t, fs=11.5, dot="#b45309")

    arrow(ax, 46, 71, 54, 71, color=GREY, lw=3)

    ax.text(50, 38, "Each fix gets a confidence score", ha="center",
            fontsize=14, fontweight="bold", color=NAVY)
    box(ax, 20, 27, 26, 8, "Passes  →  ready", GREEN, fs=12.5, radius=2)
    box(ax, 54, 27, 26, 8, "Fails  →  flagged", RED, fs=12.5, radius=2)
    ax.text(50, 18, "The AI checks its own work before any fix is considered applyable.",
            ha="center", fontsize=12.5, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 9 — Human approval + report
# ---------------------------------------------------------------------------
def slide_approve_report():
    fig, ax = new_slide()
    header(ax, "9. Human gate & final report", "Stages 8–9: approve, then deliver", color=RED)

    box(ax, 6, 60, 26, 14, "8  HUMAN-IN-\nTHE-LOOP", RED, fs=13, radius=2)
    arrow(ax, 32, 67, 42, 74, color=GREEN)
    arrow(ax, 32, 67, 42, 60, color=RED)
    box(ax, 42, 71, 30, 7, "Low-risk  →  auto-approve", GREEN, fs=11.5, radius=1.5)
    box(ax, 42, 57, 30, 7, "CRITICAL / ERROR  →  human", RED, fs=11.5, radius=1.5)
    ax.text(50, 51, "Nothing high-risk is ever applied automatically.",
            ha="center", fontsize=12, color=GREY, style="italic")

    box(ax, 20, 34, 60, 12, "9  FINAL REPORT  (Markdown + JSON)", GREEN, fs=14, radius=2)
    outs = ["Bug summary", "Security report", "Quality score",
            "Suggested fixes", "Confidence score", "Approval log"]
    x = 8
    for i, o in enumerate(outs):
        col = 8 + (i % 3) * 30
        row = 26 - (i // 3) * 5
        bullet(ax, col, row, o, fs=12, dot=GREEN)
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 10 — Tech stack + run
# ---------------------------------------------------------------------------
def slide_stack():
    fig, ax = new_slide()
    header(ax, "10. Tech stack & how to run", "Local-first, no API key required")

    ax.text(5, 84, "Built with", fontsize=15, fontweight="bold", color=NAVY)
    stack = [
        ("Orchestration", "LangGraph StateGraph", BLUE),
        ("Code-LLM", "Ollama – llama3.2:1b", AMBER),
        ("Static analysis", "Semgrep (OWASP/CWE)", TEAL),
        ("RAG", "FAISS / TF-IDF + KB", "#0369a1"),
        ("Interfaces", "CLI + Streamlit app", GREEN),
        ("Output", "Markdown + JSON", GREY),
    ]
    for i, (k, v, c) in enumerate(stack):
        col = 5 + (i % 2) * 47
        row = 76 - (i // 2) * 8
        box(ax, col, row, 20, 6, k, c, fs=11, radius=1.5)
        ax.text(col + 22, row + 3, v, fontsize=12.5, va="center", color="#1e293b")

    ax.text(5, 44, "Run it", fontsize=15, fontweight="bold", color=NAVY)
    code = ("pip install -r requirements.txt\n"
            "ollama serve  &&  ollama pull llama3.2:1b\n\n"
            "python main.py --repo ./sample_repo     # CLI review\n"
            "streamlit run app.py                    # dashboard\n"
            "python -m tests.test_pipeline           # end-to-end test")
    ax.text(6, 22, code, fontsize=12.5, family="monospace", color="#e2e8f0",
            va="center", bbox=dict(boxstyle="round,pad=1.0", fc=NAVY, ec=BLUE))
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 11 — Data flow (how the ReviewState grows)
# ---------------------------------------------------------------------------
def slide_dataflow():
    fig, ax = new_slide()
    header(ax, "11. Data flow", "What each stage adds to the shared ReviewState",
           color="#0369a1")

    ax.text(50, 85, "One state object flows through the pipeline — every stage reads it and adds new fields.",
            ha="center", fontsize=12, color=GREY, style="italic")

    rows = [
        ("Ingestion", "files, file_contents, languages", BLUE),
        ("Static Analysis", "semgrep_findings, code_graph_summary", BLUE),
        ("LLM Review", "llm_findings, llm_file_summaries", BLUE),
        ("Vulnerability", "merged_findings, quality_issues", TEAL),
        ("RAG", "retrieved_guidance", TEAL),
        ("Review Generation", "suggested_fixes, review_comments", AMBER),
        ("Verifier", "verifier_report, confidence_score", AMBER),
        ("Approval", "approved_fixes, requires_approval, rejected_fixes", RED),
        ("Report", "final_report_md / json, quality_score", GREEN),
    ]
    y = 78
    dy = 7.3
    for i, (stage, fields, c) in enumerate(rows):
        box(ax, 5, y, 26, 5.6, stage, c, fs=11, radius=1.3)
        # "input state" arrow feeding down the left spine
        if i < len(rows) - 1:
            arrow(ax, 18, y, 18, y - dy + 5.6, color="#94a3b8", lw=1.8)
        ax.text(34, y + 2.8, "adds →", fontsize=10, color=GREY, va="center",
                style="italic")
        box(ax, 43, y, 53, 5.6, fields, LIGHT, tc="#1e293b", fs=10.5,
            bold=False, radius=1.3, ec=c)
        y -= dy

    ax.text(50, 10.5, "Result: a fully-populated ReviewState → rendered as the final Markdown + JSON report.",
            ha="center", fontsize=11.5, color="#0369a1", fontweight="bold")
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
# Slide 12 — Source data & references
# ---------------------------------------------------------------------------
def slide_sources():
    fig, ax = new_slide()
    header(ax, "12. Data sources & references",
           "What the system reads, retrieves from, and can train on")

    # In-pipeline sources (used at runtime)
    ax.text(5, 84, "Sources used at runtime", fontsize=14, fontweight="bold", color=NAVY)
    live = [
        ("RAG knowledge base", "rag/secure_coding_docs.json — 12 OWASP/CWE guidance docs", TEAL),
        ("Semgrep rulesets", "p/security-audit, p/owasp-top-ten, p/cwe-top-25, p/secrets", BLUE),
        ("Offline fallback rules", "rules/local_security_rules.yaml (no-internet mode)", BLUE),
        ("Demo input repo", "sample_repo/ — intentionally vulnerable code", GREY),
    ]
    y = 78
    for name, detail, c in live:
        box(ax, 5, y, 30, 5.4, name, c, fs=10.5, radius=1.3)
        ax.text(37, y + 2.7, detail, fontsize=10.5, va="center", color="#1e293b")
        y -= 6.6

    # Reference datasets (for training/fine-tuning — from README §5)
    ax.text(5, 48, "Reference datasets (for training a dedicated vuln model — README §5)",
            fontsize=14, fontweight="bold", color=NAVY)
    datasets = [
        ("Devign", "C/C++ vuln detection"),
        ("Big-Vul (MSR'20)", "CVE-labeled vulns"),
        ("CodeXGLUE", "Code intelligence"),
        ("CodeSearchNet", "Semantic code search"),
        ("Juliet (NIST)", "CWE weakness cases"),
        ("DiverseVul", "Large-scale vulns"),
        ("OWASP Benchmark", "Tool benchmark"),
        ("ManySStuBs4J", "Java bug fixes"),
        ("SAP Project-KB", "Java bug benchmark"),
    ]
    x0, y0, w, h = 5, 34, 29.5, 6.2
    for i, (name, desc) in enumerate(datasets):
        col = i % 3
        rrow = i // 3
        x = x0 + col * (w + 1.5)
        yy = y0 - rrow * (h + 1.5)
        box(ax, x, yy, w, h, "", "#eef2ff", ec=BLUE, radius=1.2)
        ax.text(x + 1.6, yy + 4.0, name, fontsize=10.5, fontweight="bold",
                color=NAVY, va="center")
        ax.text(x + 1.6, yy + 1.7, desc, fontsize=8.8, color=GREY, va="center")

    ax.text(50, 8.5, "Datasets are cited as reference benchmarks; this demo grounds fixes via the RAG KB rather than training on them.",
            ha="center", fontsize=10.5, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ---------------------------------------------------------------------------
def main():
    slide_title()
    slide_problem()
    slide_bigpicture()
    slide_flow()
    slide_agents()
    slide_understand()
    slide_merge_rag()
    slide_rag()
    slide_fix_verify()
    slide_approve_report()
    slide_stack()
    slide_dataflow()
    slide_sources()

    out = os.path.join(os.path.dirname(__file__), "architecture_slides_v2.pdf")
    with PdfPages(out) as pdf:
        for fig in slides:
            pdf.savefig(fig, facecolor=fig.get_facecolor())
            plt.close(fig)
    print(f"Wrote {out} ({len(slides)} slides)")


if __name__ == "__main__":
    main()
