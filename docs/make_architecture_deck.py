"""
Generates a detailed ~21-slide architecture deck for the AI Software Code
Reviewer & Secure Development Agent.

Covers: the complete architecture, the rationale + advantages behind each design
choice, why RAG, why Redis, every LangGraph node and what it does, the datasets
used, every technology in the stack, and the Streamlit UI.

Run:  python docs/make_architecture_deck.py
Output: docs/architecture_deck.pdf
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
PURPLE = "#7c3aed"
LIGHT = "#f1f5f9"
GREY = "#64748b"
WHITE = "#ffffff"

plt.rcParams["font.family"] = "DejaVu Sans"

W, H = 13.33, 7.5  # 16:9

slides = []
_PAGE = [0]
TOTAL_SLIDES = 22


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
    ax.text(98, 1.1, f"{_PAGE[0] + 1} / {TOTAL_SLIDES}", color=WHITE, fontsize=8.5,
            va="center", ha="right")


def header(ax, title, subtitle=None, color=NAVY):
    ax.add_patch(plt.Rectangle((0, 90), 100, 10, color=color, zorder=1))
    ax.add_patch(plt.Rectangle((0, 89.3), 100, 0.7, color=BLUE, zorder=1))
    ax.text(3, 95, title, color=WHITE, fontsize=20, fontweight="bold", va="center")
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
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                 mutation_scale=18, linewidth=lw, color=color, zorder=2))


def bullet(ax, x, y, text, fs=12.5, color="#1e293b", dot=BLUE):
    ax.text(x, y, "●", color=dot, fontsize=fs - 3, va="center")
    ax.text(x + 2.4, y, text, color=color, fontsize=fs, va="center")


def why_advantage(ax, why_lines, adv_lines, y0=74):
    """Two-column 'Why / Advantages' layout used on rationale slides."""
    box(ax, 5, y0, 43, 6, "WHY WE CHOSE IT", NAVY, fs=12.5, radius=1.5)
    box(ax, 52, y0, 43, 6, "ADVANTAGES", GREEN, fs=12.5, radius=1.5)
    for i, t in enumerate(why_lines):
        bullet(ax, 6, y0 - 5 - i * 5.4, t, fs=11.5, dot=BLUE)
    for i, t in enumerate(adv_lines):
        bullet(ax, 53, y0 - 5 - i * 5.4, t, fs=11.5, dot=GREEN)


# ===========================================================================
# 1 — Title
# ===========================================================================
def s_title():
    fig, ax = new_slide()
    ax.add_patch(plt.Rectangle((0, 0), 100, 100, color=NAVY, zorder=0))
    ax.add_patch(plt.Rectangle((0, 56), 100, 2, color=BLUE, zorder=1))
    ax.text(50, 80, "AI Software Code Reviewer", color=WHITE, fontsize=37,
            fontweight="bold", ha="center")
    ax.text(50, 71, "& Secure Development Agent", color=BLUE, fontsize=28,
            fontweight="bold", ha="center")
    ax.text(50, 62, "Complete Architecture & Design Rationale",
            color="#cbd5e1", fontsize=15, ha="center")
    chip_rows = [["LangGraph", "Semgrep", "RAG (FAISS / TF-IDF)", "Redis"],
                 ["Ollama / Gemini / Claude / GPT", "Streamlit", "Docker"]]
    gap = 1.8
    yy = 47
    for row in chip_rows:
        widths = [0.75 * len(c) + 4 for c in row]
        total = sum(widths) + gap * (len(row) - 1)
        cx = (100 - total) / 2
        for c, w in zip(row, widths):
            box(ax, cx, yy, w, 5.2, c, "#13385c", tc=WHITE, fs=9.5, radius=3, ec=BLUE)
            cx += w + gap
        yy -= 7.2
    ax.text(50, 30, "9 specialist agents · one LangGraph pipeline · grounded, verified, human-approved fixes",
            color="#94a3b8", fontsize=12, ha="center")
    ax.text(50, 22, "Team Alpha  •  Architecture Deck  •  21 slides", color=WHITE,
            fontsize=13, ha="center", fontweight="bold")
    slides.append(fig)


# ===========================================================================
# 2 — Problem & solution
# ===========================================================================
def s_problem():
    fig, ax = new_slide()
    header(ax, "The problem & our solution", "Why an automated, agentic reviewer")
    ax.text(4, 84, "The problem", fontsize=15, fontweight="bold", color=NAVY)
    for i, t in enumerate([
        "Manual review is slow, inconsistent, and misses security bugs.",
        "Static tools flag issues but don't explain or fix them.",
        "Raw LLM fixes can hallucinate unsafe 'best practices'.",
        "High-risk changes must never be auto-applied without a human.",
    ]):
        bullet(ax, 5, 78 - i * 4.8, t, fs=12.5)

    ax.text(4, 55, "Our solution — one pipeline that:", fontsize=15,
            fontweight="bold", color=NAVY)
    steps = [("FINDS", "bugs + security\nvulnerabilities", BLUE),
             ("GROUNDS", "fixes in real\nsecure-coding docs", TEAL),
             ("VERIFIES", "its own fixes\nbefore applying", AMBER),
             ("GUARDS", "human approves\nhigh-risk changes", RED)]
    x = 6
    for t, d, c in steps:
        box(ax, x, 38, 20, 10, t, c, fs=14, radius=2)
        ax.text(x + 10, 32, d, ha="center", va="center", fontsize=10.5, color="#1e293b")
        x += 23
    ax.text(50, 19, "Input: a GitHub repo (or local path)   →   Output: explainable report + verified fixes + PDF",
            ha="center", fontsize=12.5, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc=LIGHT, ec=GREY))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 3 — High-level architecture
# ===========================================================================
def s_bigpicture():
    fig, ax = new_slide()
    header(ax, "High-level architecture", "One shared state flows through 9 agents")
    box(ax, 3, 62, 15, 12, "GitHub /\nLocal repo", NAVY, fs=11, radius=2)
    arrow(ax, 18, 68, 27, 68, color=BLUE, lw=3)
    box(ax, 27, 58, 42, 18, "9-STAGE AGENTIC PIPELINE\n(LangGraph StateGraph)", BLUE, fs=14, radius=2)
    arrow(ax, 69, 68, 78, 68, color=BLUE, lw=3)
    box(ax, 78, 62, 18, 12, "Report\nMD · JSON · PDF", GREEN, fs=11, radius=2)

    # side services
    box(ax, 27, 47, 20, 6, "Semgrep (static)", TEAL, fs=10, radius=1.3)
    box(ax, 49, 47, 20, 6, "RAG knowledge base", PURPLE, fs=10, radius=1.3)
    box(ax, 27, 39, 20, 6, "Redis cache", RED, fs=10, radius=1.3)
    box(ax, 49, 39, 20, 6, "LLM (local/cloud)", AMBER, fs=10, radius=1.3)
    ax.text(48, 34, "Pluggable side services the pipeline calls into",
            ha="center", fontsize=10.5, color=GREY, style="italic")

    layers = [("UNDERSTAND", "Ingestion · Static Analysis · Code-LLM Review", BLUE),
              ("DETECT & GROUND", "Merge & Dedupe · Secure-Coding RAG", TEAL),
              ("FIX & CHECK", "Explainable Fix Generation · Self-Verifier", AMBER),
              ("APPROVE & REPORT", "Human-in-the-Loop · Final Report", RED)]
    y = 26
    for name, detail, c in layers:
        box(ax, 6, y, 24, 5, name, c, fs=10.5, radius=1.3)
        box(ax, 32, y, 62, 5, detail, LIGHT, tc="#1e293b", fs=10.5, bold=False, radius=1.3, ec=c)
        y -= 5.8
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 4 — Why a multi-agent pipeline
# ===========================================================================
def s_why_agents():
    fig, ax = new_slide()
    header(ax, "Why a multi-agent pipeline?", "Design principle #1: separation of concerns",
           color=BLUE)
    why_advantage(ax,
        ["Each concern (scan, reason, fix, verify)\n  is a hard problem on its own.",
         "One giant prompt would be unreliable\n  and impossible to debug.",
         "Security demands checks & balances —\n  a generator AND an independent verifier."],
        ["Each agent is small, testable, swappable.",
         "Failures are isolated & observable\n  (per-node tracing).",
         "New capabilities = add a node, not a\n  rewrite.",
         "Mirrors how human review teams work."])
    ax.text(50, 20, "9 specialist agents, each doing one job well, coordinated by a shared state.",
            ha="center", fontsize=12.5, color=BLUE, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#e6effd", ec=BLUE))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 5 — Why LangGraph
# ===========================================================================
def s_why_langgraph():
    fig, ax = new_slide()
    header(ax, "Why LangGraph for orchestration?", "Design principle #2: an explicit state machine",
           color=TEAL)
    why_advantage(ax,
        ["We need a deterministic, ordered flow\n  of agents — not free-form chatter.",
         "State must accumulate across stages\n  (findings → fixes → approvals).",
         "We want to stream progress and trace\n  every step for observability."],
        ["StateGraph = explicit nodes + edges;\n  easy to read and reason about.",
         "Typed shared ReviewState passed node\n  to node — no hidden globals.",
         "Native streaming drives the live UI\n  progress bar, stage by stage.",
         "Each node wrapped with tracing spans."])
    ax.text(50, 20, "graph.py wires 9 nodes as a linear StateGraph:  ingestion → … → report.",
            ha="center", fontsize=12, color=TEAL, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#e6f5f3", ec=TEAL))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 6 — Pipeline flow
# ===========================================================================
def s_flow():
    fig, ax = new_slide()
    header(ax, "The 9-node pipeline flow", "Data moves through nine stages, in order")
    stages = [("1\nIngestion", BLUE), ("2\nStatic\nAnalysis", BLUE),
              ("3\nLLM\nReview", BLUE), ("4\nVulnerability\nMerge", TEAL),
              ("5\nRAG", PURPLE), ("6\nReview\nGeneration", AMBER),
              ("7\nVerifier", AMBER), ("8\nHuman\nApproval", RED),
              ("9\nReport", GREEN)]
    row1, row2 = stages[:5], stages[5:]
    x0, w, gap = 5, 16, 2.5
    for i, (txt, c) in enumerate(row1):
        x = x0 + i * (w + gap)
        box(ax, x, 60, w, 14, txt, c, fs=11, radius=2)
        if i < len(row1) - 1:
            arrow(ax, x + w, 67, x + w + gap, 67, color=GREY, lw=2.5)
    lastx = x0 + 4 * (w + gap)
    arrow(ax, lastx + w / 2, 60, lastx + w / 2, 50, color=GREY, lw=2.5)
    for i, (txt, c) in enumerate(row2):
        x = x0 + (len(row2) - 1 - i) * (w + gap)
        box(ax, x, 36, w, 14, txt, c, fs=11, radius=2)
        if i < len(row2) - 1:
            nx = x0 + (len(row2) - 1 - (i + 1)) * (w + gap)
            arrow(ax, x, 43, nx + w, 43, color=GREY, lw=2.5)
    leg = [("Understand", BLUE), ("Detect & ground", TEAL),
           ("Retrieve", PURPLE), ("Fix & verify", AMBER), ("Approve/report", GREEN)]
    lx = 5
    for name, c in leg:
        ax.add_patch(plt.Rectangle((lx, 22), 3, 3, color=c))
        ax.text(lx + 4, 23.5, name, fontsize=10.5, va="center", color="#1e293b")
        lx += 18.5
    ax.text(50, 14, "Linear & traceable: each node reads the previous node's output from the shared state.",
            ha="center", fontsize=12, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 7 — ReviewState / data flow
# ===========================================================================
def s_dataflow():
    fig, ax = new_slide()
    header(ax, "The shared ReviewState", "What each node adds to the state (agents/state.py)",
           color="#0369a1")
    rows = [("Ingestion", "files, file_contents, languages", BLUE),
            ("Static Analysis", "semgrep_findings, code_graph_summary", BLUE),
            ("LLM Review", "llm_findings, llm_file_summaries", BLUE),
            ("Vulnerability", "merged_findings, quality_issues, category_breakdown", TEAL),
            ("RAG", "retrieved_guidance", PURPLE),
            ("Review Generation", "suggested_fixes, review_comments", AMBER),
            ("Verifier", "verifier_report, confidence_score", AMBER),
            ("Approval", "approved_fixes, requires_approval, rejected_fixes", RED),
            ("Report", "final_report_md / json, quality_score", GREEN)]
    y = 80
    for i, (stage, fields, c) in enumerate(rows):
        box(ax, 5, y, 26, 5.4, stage, c, fs=10.5, radius=1.3)
        if i < len(rows) - 1:
            arrow(ax, 18, y, 18, y - 7 + 5.4, color="#94a3b8", lw=1.6)
        ax.text(33, y + 2.7, "adds →", fontsize=9.5, color=GREY, va="center", style="italic")
        box(ax, 42, y, 54, 5.4, fields, LIGHT, tc="#1e293b", fs=9.8, bold=False, radius=1.3, ec=c)
        y -= 7
    ax.text(50, 10, "One typed dict grows as it flows — every stage is a pure state → state function.",
            ha="center", fontsize=11, color="#0369a1", fontweight="bold")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 8 — All 9 nodes at a glance
# ===========================================================================
def s_agents_glance():
    fig, ax = new_slide()
    header(ax, "The 9 LangGraph nodes at a glance",
           "Each node = one specialist agent (agents/*.py)")
    agents = [
        ("1", "Ingestion", "Walks the repo, reads files, detects language; honours batch/file caps.", BLUE),
        ("2", "Static Analysis", "Runs Semgrep security rulesets + builds an AST code graph.", BLUE),
        ("3", "LLM Review", "A Code-LLM reads each file for logic/semantic bugs rules miss.", BLUE),
        ("4", "Vulnerability", "Merges & de-dupes Semgrep + LLM findings; splits security vs quality.", TEAL),
        ("5", "RAG", "Retrieves grounded secure-coding guidance for each finding.", PURPLE),
        ("6", "Review Generation", "Writes a plain-language comment + a minimal, grounded fix.", AMBER),
        ("7", "Verifier", "Syntax check, no-op check, Semgrep regression re-scan → confidence.", AMBER),
        ("8", "Approval", "Routes CRITICAL/ERROR or unverified fixes to a human, with reasons.", RED),
        ("9", "Report", "Assembles Markdown + JSON (+ PDF); computes the quality score.", GREEN),
    ]
    y = 80
    for num, name, desc, c in agents:
        box(ax, 4, y, 6.5, 6, num, c, fs=14, radius=1.2)
        box(ax, 11.5, y, 25, 6, name, LIGHT, tc="#0f2740", fs=11.5, radius=1.2, ec=c)
        ax.text(38, y + 3, desc, fontsize=10.3, va="center", color="#1e293b")
        y -= 8.3
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 9 — Nodes 1-2
# ===========================================================================
def s_nodes_1_2():
    fig, ax = new_slide()
    header(ax, "Node 1 & 2 — Understand the code", "Ingestion + Static Analysis", color=BLUE)
    box(ax, 5, 62, 42, 12, "NODE 1 · INGESTION", BLUE, fs=13, radius=2)
    for i, t in enumerate(["Discovers source files across 9 languages",
                            "Reads contents, detects language by extension",
                            "Applies the file cap / batch subset from the UI"]):
        bullet(ax, 6, 57 - i * 4.4, t, fs=11, dot=BLUE)
    ax.text(6, 40, "→ files, file_contents, languages", fontsize=11, color=BLUE, fontweight="bold")

    box(ax, 53, 62, 42, 12, "NODE 2 · STATIC ANALYSIS", TEAL, fs=13, radius=2)
    for i, t in enumerate(["Runs Semgrep (OWASP/CWE/secrets rulesets)",
                           "Offline fallback ruleset if registry unreachable",
                           "Builds an AST code graph (funcs, calls, sinks)"]):
        bullet(ax, 54, 57 - i * 4.4, t, fs=11, dot=TEAL)
    ax.text(54, 40, "→ semgrep_findings, code_graph_summary", fontsize=11, color=TEAL, fontweight="bold")

    ax.text(50, 30, "Why two lenses here?", ha="center", fontsize=14, fontweight="bold", color=NAVY)
    ax.text(50, 24, "Deterministic pattern matching (Semgrep) and structural context (code graph)\n"
                    "give the LLM in Node 3 solid ground to reason over — not raw text alone.",
            ha="center", fontsize=12, color="#1e293b")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 10 — Node 3 LLM Review + pluggable models
# ===========================================================================
def s_node3():
    fig, ax = new_slide()
    header(ax, "Node 3 — Code-LLM semantic review", "The intelligent lens (llm_review_agent.py)",
           color=BLUE)
    for i, t in enumerate(["Summarizes each file's purpose",
                           "Finds logic bugs, missing auth, race conditions, insecure design",
                           "Catches what pattern-based tools structurally cannot",
                           "Per-file calls run in parallel (ThreadPoolExecutor)"]):
        bullet(ax, 6, 80 - i * 5, t, fs=12, dot=BLUE)

    ax.text(5, 54, "Pluggable LLM backend (utils/llm_client.py) — swap without code changes:",
            fontsize=12.5, fontweight="bold", color=NAVY)
    models = [("Ollama (local)", "qwen2.5-coder:1.5b · llama3.2:1b", AMBER),
              ("Google Gemini", "gemini-2.5-flash", PURPLE),
              ("Anthropic", "claude-sonnet-4-6", BLUE),
              ("OpenAI", "gpt-4o-mini", TEAL)]
    x = 5
    for name, m, c in models:
        box(ax, x, 40, 22, 7, name, c, fs=11, radius=1.5)
        ax.text(x + 11, 35, m, ha="center", fontsize=9.2, color="#1e293b", family="monospace")
        x += 23.5
    ax.text(50, 24, "Local = free & private; Cloud = far faster and fully parallel. Chosen at runtime in the UI.",
            ha="center", fontsize=12, color=BLUE, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#e6effd", ec=BLUE))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 11 — Node 4 merge
# ===========================================================================
def s_node4():
    fig, ax = new_slide()
    header(ax, "Node 4 — Vulnerability merge & dedupe", "Combine the two lenses (vulnerability_agent.py)",
           color=TEAL)
    box(ax, 6, 64, 24, 9, "Semgrep findings", BLUE, fs=12)
    box(ax, 6, 52, 24, 9, "LLM findings", AMBER, fs=12)
    arrow(ax, 30, 68, 40, 62, color=GREY)
    arrow(ax, 30, 56, 40, 60, color=GREY)
    box(ax, 40, 54, 26, 12, "MERGE\n& DEDUPE", TEAL, fs=13, radius=2)
    ax.text(53, 49, "same file+line = one finding", ha="center", fontsize=10, color=GREY, style="italic")
    arrow(ax, 66, 60, 74, 60, color=GREY)
    box(ax, 74, 61, 22, 6.5, "Security vulns", RED, fs=11, radius=1.3)
    box(ax, 74, 52.5, 22, 6.5, "Quality issues", GREY, fs=11, radius=1.3)

    why_advantage(ax,
        ["Two detectors overlap — the same bug\n  shouldn't be reported twice.",
         "Security and maintainability deserve\n  different handling downstream."],
        ["One clean, de-duplicated finding list.",
         "category_breakdown drives the charts\n  and the quality score.",
         "Severity tags feed the approval policy."],
        y0=40)
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 12 — Node 5 RAG + WHY RAG
# ===========================================================================
def s_why_rag():
    fig, ax = new_slide()
    header(ax, "Node 5 & Why RAG", "Retrieval-Augmented Generation for grounded fixes",
           color=PURPLE)
    ax.text(50, 84, "RAG = retrieve real secure-coding guidance, then make the LLM fix WITH it.",
            ha="center", fontsize=12.5, color=PURPLE, fontweight="bold")
    why_advantage(ax,
        ["Ungrounded LLM fixes hallucinate\n  unsafe 'best practices'.",
         "Security guidance (OWASP/CWE) is\n  authoritative and specific.",
         "We want fixes traceable to a source,\n  not the model's guesswork."],
        ["Fixes cite real CWE/OWASP remediation.",
         "Far fewer hallucinated / unsafe fixes.",
         "Explainable & auditable — every fix\n  links to its guidance.",
         "OPTIONAL: a UI toggle turns it off to\n  compare grounded vs. ungrounded."],
        y0=76)
    ax.text(50, 17, "RAG is optional & toggleable (USE_RAG). ON → fixes grounded in the KB; OFF → general best practice.",
            ha="center", fontsize=11.5, color=PURPLE, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#f0e9fd", ec=PURPLE))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 13 — Inside the RAG engine
# ===========================================================================
def s_rag_engine():
    fig, ax = new_slide()
    header(ax, "Inside the RAG engine", "Knowledge base + two interchangeable backends",
           color=PURPLE)
    box(ax, 4, 72, 20, 10, "A finding\n(CWE / rule id\n+ message)", NAVY, fs=10.5, radius=2)
    arrow(ax, 24, 77, 32, 77, color=PURPLE, lw=2.5)
    box(ax, 32, 76, 26, 8, "1) Fast path:\nmatch by CWE / tag", TEAL, fs=10.5, radius=2)
    arrow(ax, 45, 76, 45, 69, color=PURPLE, lw=2.5)
    box(ax, 32, 60, 26, 8, "2) Semantic search\n(embedding similarity)", BLUE, fs=10.5, radius=2)
    box(ax, 64, 62, 32, 22, "KNOWLEDGE BASE\nrag/secure_coding_docs.json\n(OWASP / CWE guidance)\n+ CWE-labeled dataset samples",
        LIGHT, tc="#1e293b", fs=10.5, bold=False, radius=2, ec=PURPLE)
    arrow(ax, 58, 79, 64, 76, color=GREY)
    arrow(ax, 58, 64, 64, 66, color=GREY)

    ax.text(5, 52, "Two interchangeable backends (auto-selected):", fontsize=12.5,
            fontweight="bold", color=NAVY)
    box(ax, 6, 41, 42, 8, "Optional: sentence-transformers + FAISS", BLUE, fs=11, radius=1.5)
    box(ax, 52, 41, 42, 8, "Default: scikit-learn TF-IDF (torch-free)", TEAL, fs=11, radius=1.5)
    for i, t in enumerate([
        "Ships torch-free by default → small, fast Docker image; TF-IDF needs no model download.",
        "Add sentence-transformers + FAISS for semantic embeddings when higher recall is needed.",
        "Graceful fallback: if embeddings can't load, RAG still works offline via TF-IDF."]):
        bullet(ax, 6, 34 - i * 4.6, t, fs=11, dot=PURPLE)
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 13b — RAG knowledge base documents
# ===========================================================================
def s_rag_documents():
    fig, ax = new_slide()
    header(ax, "What's in the RAG knowledge base", "The 12 grounded documents "
           "(rag/secure_coding_docs.json)", color=PURPLE)
    ax.text(50, 84, "Each finding is matched to one of these curated OWASP/CWE "
            "remediation docs; the snippet is injected into the fix prompt.",
            ha="center", fontsize=11.5, color=GREY, style="italic")
    docs = [
        ("CWE-89", "SQL Injection"), ("CWE-78", "OS Command Injection"),
        ("CWE-79", "Cross-Site Scripting (XSS)"), ("CWE-20", "Improper Input Validation"),
        ("CWE-502", "Untrusted Deserialization"), ("CWE-327", "Weak / Broken Crypto"),
        ("CWE-798", "Hard-coded Credentials"), ("CWE-259", "Hard-coded Password"),
        ("CWE-732", "Incorrect Permissions"), ("OWASP A09", "Insufficient Logging & Monitoring"),
        ("Quality", "Secure & Maintainable Error Handling"),
        ("Quality", "Avoid Magic Numbers & Duplication"),
    ]
    x0, y0, w, h = 5, 70, 44, 8.2
    for i, (code, title) in enumerate(docs):
        col = i % 2
        row = i // 2
        x = x0 + col * (w + 2)
        y = y0 - row * (h + 1.6)
        c = GREEN if code == "Quality" else (AMBER if "OWASP" in code else PURPLE)
        box(ax, x, y, 12, h - 2, code, c, fs=10, radius=1.3)
        box(ax, x + 13, y, w - 13, h - 2, title, LIGHT, tc="#1e293b", fs=10.5,
            bold=False, radius=1.3, ec=c)
    ax.text(50, 8.5, "10 security (CWE/OWASP) remediation docs + 2 code-quality guides. "
            "Extend by adding entries to the JSON.",
            ha="center", fontsize=10.5, color=PURPLE, fontweight="bold")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 14 — Node 6 review generation
# ===========================================================================
def s_node6():
    fig, ax = new_slide()
    header(ax, "Node 6 — Explainable fix generation", "review_generation_agent.py", color=AMBER)
    box(ax, 6, 64, 88, 12, "For every merged finding, the Code-LLM produces:", AMBER, fs=13, radius=2)
    for i, t in enumerate([
        "A plain-language explanation — WHY this is a problem (for a mid-level dev).",
        "A minimal, targeted code fix — no unrelated rewrites.",
        "Grounded in the RAG guidance retrieved for that finding.",
        "Per-finding calls run in parallel; prompts use repo-relative paths so the cache reuses across runs."]):
        bullet(ax, 8, 57 - i * 5.2, t, fs=12, dot=AMBER)
    ax.text(50, 30, "→ suggested_fixes  (original code, fixed code, explanation)  +  review_comments",
            ha="center", fontsize=12, color=AMBER, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#fdf1e0", ec=AMBER))
    ax.text(50, 20, "Covers the 'explainable code review + automated fix' objective — with Explainable-AI reasoning.",
            ha="center", fontsize=12, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 15 — Node 7 verifier
# ===========================================================================
def s_node7():
    fig, ax = new_slide()
    header(ax, "Node 7 — Verifier / self-reflection", "The AI checks its own work (verifier_agent.py)",
           color=AMBER)
    box(ax, 6, 66, 88, 8, "Three independent checks on every generated fix:", "#b45309", fs=12.5, radius=1.5)
    checks = [("Syntax check", "Does the fixed code parse (AST for Python)?", BLUE),
              ("No-op check", "Did it actually change the code, or fake a fix?", TEAL),
              ("Regression re-scan", "Re-run Semgrep — is the original rule now gone?", AMBER)]
    y = 56
    for name, desc, c in checks:
        box(ax, 8, y, 26, 7, name, c, fs=11.5, radius=1.5)
        ax.text(37, y + 3.5, desc, fontsize=11.5, va="center", color="#1e293b")
        y -= 9
    why_advantage(ax,
        ["LLM fixes can be hallucinated,\n  incomplete, or no-ops.",
         "Security fixes must be proven,\n  not trusted."],
        ["A confidence score per fix & per run.",
         "Unverified fixes get flagged → sent to\n  a human, never auto-applied.",
         "Resilient: one bad fix can't crash it."],
        y0=27)
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 16 — Node 8 approval
# ===========================================================================
def s_node8():
    fig, ax = new_slide()
    header(ax, "Node 8 — Human-in-the-loop approval", "Never auto-apply high risk (approval_agent.py)",
           color=RED)
    box(ax, 6, 62, 26, 12, "Every fix is\ntriaged", RED, fs=13, radius=2)
    arrow(ax, 32, 68, 42, 74, color=GREEN)
    arrow(ax, 32, 68, 42, 60, color=RED)
    box(ax, 42, 71, 40, 7, "Verified + low severity → auto-approve", GREEN, fs=11, radius=1.5)
    box(ax, 42, 57, 40, 7, "CRITICAL/ERROR or unverified → human", RED, fs=11, radius=1.5)
    ax.text(50, 50, "Each decision carries a plain-language reason, shown in the UI.",
            ha="center", fontsize=11.5, color=GREY, style="italic")
    why_advantage(ax,
        ["High-risk code changes must have a\n  human gate (compliance & trust).",
         "Reviewers need to see WHY each fix\n  was auto-approved or escalated."],
        ["Nothing high-risk is applied silently.",
         "Approve / Reject / override buttons\n  in the Streamlit dashboard.",
         "'Require approval for ALL' toggle for\n  strict mode."],
        y0=42)
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 17 — Node 9 report
# ===========================================================================
def s_node9():
    fig, ax = new_slide()
    header(ax, "Node 9 — Final report", "Assemble every artifact (report_agent.py)", color=GREEN)
    box(ax, 25, 74, 50, 10, "REVIEWSTATE  →  REPORT", GREEN, fs=14, radius=2)
    outs = ["Bug & vulnerability summary", "Security (CWE/OWASP) report",
            "Code quality score (0–100)", "Suggested + verified fixes",
            "Confidence score", "Human-approval log",
            "Markdown + JSON export", "Downloadable PDF (reportlab)"]
    for i, o in enumerate(outs):
        col = 10 + (i % 2) * 45
        row = 64 - (i // 2) * 8
        bullet(ax, col, row, o, fs=12.5, dot=GREEN)
    ax.text(50, 26, "Quality score = smooth exponential decay on issue density",
            ha="center", fontsize=12.5, fontweight="bold", color=NAVY)
    ax.text(50, 20, "so a vulnerable repo lands in a low-but-informative band instead of a flat 0 — a usable signal.",
            ha="center", fontsize=11.5, color="#1e293b")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 18 — Why Redis
# ===========================================================================
def s_why_redis():
    fig, ax = new_slide()
    header(ax, "Why Redis?", "Caching LLM responses for speed & cost", color=RED)
    ax.text(50, 84, "LLM calls are the slow, expensive part. Redis caches every completion.",
            ha="center", fontsize=12.5, color=RED, fontweight="bold")
    why_advantage(ax,
        ["Local CPU inference is slow; cloud\n  calls cost money & latency.",
         "Re-reviewing the same file+model\n  should be free and instant.",
         "A demo/hackathon box needs zero-setup\n  infra."],
        ["Key = (provider, model, system, prompt,\n  max_tokens) → exact-match reuse.",
         "Repeat runs of a repo are near-instant.",
         "redislite embedded fallback → no server\n  to install; degrades to no-op safely.",
         "7-day TTL; live hit/miss stats in the UI."],
        y0=76)
    ax.text(50, 17, "utils/redis_cache.py: standalone Redis → embedded redislite → transparent no-op (never a hard dep).",
            ha="center", fontsize=11, color=RED, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#fdecec", ec=RED))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 19 — Datasets
# ===========================================================================
def s_datasets():
    fig, ax = new_slide()
    header(ax, "Datasets used", "Grounding & benchmarking the vulnerability knowledge",
           color=PURPLE)
    ax.text(5, 84, "Folded into the RAG corpus (CWE-labeled vulnerable/fixed samples):",
            fontsize=12.5, fontweight="bold", color=NAVY)
    used = [("Devign", "C/C++ function-level vuln detection (CodeXGLUE)"),
            ("Big-Vul (MSR'20)", "CVE-labeled real-world vulnerabilities"),
            ("Juliet (NIST)", "Synthetic CWE weakness test cases"),
            ("DiverseVul", "Large-scale diverse vulnerable functions")]
    y = 78
    for name, desc in used:
        box(ax, 6, y, 26, 5.6, name, PURPLE, fs=10.5, radius=1.3)
        ax.text(34, y + 2.8, desc, fontsize=11, va="center", color="#1e293b")
        y -= 7
    ax.text(5, 46, "Additional reference benchmarks (README):", fontsize=12.5,
            fontweight="bold", color=NAVY)
    refs = [("CodeXGLUE", "code intelligence"), ("CodeSearchNet", "semantic search"),
            ("OWASP Benchmark", "tool scoring"), ("ManySStuBs4J", "Java bug fixes"),
            ("SAP Project-KB", "Java vuln benchmark"), ("CVEfixes", "CVE fix commits")]
    x0, y0, w, h = 6, 32, 29, 6
    for i, (name, desc) in enumerate(refs):
        x = x0 + (i % 3) * (w + 1.5)
        yy = y0 - (i // 3) * (h + 1.5)
        box(ax, x, yy, w, h, "", "#f0e9fd", ec=PURPLE, radius=1.2)
        ax.text(x + 1.6, yy + 3.9, name, fontsize=10.5, fontweight="bold", color=NAVY, va="center")
        ax.text(x + 1.6, yy + 1.6, desc, fontsize=8.6, color=GREY, va="center")
    ax.text(50, 8.5, "rag/dataset_loader.py streams samples into the KB; fetch_datasets.py can cache real HF datasets offline.",
            ha="center", fontsize=10.3, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 20 — Technology stack
# ===========================================================================
def s_stack():
    fig, ax = new_slide()
    header(ax, "Technology stack — and why each", "Every technology in the system")
    tech = [
        ("LangGraph", "Explicit, streamable agent state machine", BLUE),
        ("Ollama", "Local Code-LLMs (qwen2.5-coder, llama3.2)", AMBER),
        ("Gemini / Claude / GPT", "Fast, high-quality cloud LLM options", PURPLE),
        ("Semgrep", "Deterministic OWASP/CWE static analysis", TEAL),
        ("scikit-learn TF-IDF", "Torch-free default RAG retrieval", GREEN),
        ("sentence-transf. + FAISS", "Optional semantic embedding search", BLUE),
        ("Redis / redislite", "LLM-response cache (zero-setup fallback)", RED),
        ("Streamlit", "Interactive review dashboard", TEAL),
        ("Plotly + matplotlib", "Interactive charts + PDF chart images", AMBER),
        ("reportlab", "Finalized PDF report generation", GREY),
        ("Python ast", "Code-graph structural context", BLUE),
        ("Docker + compose", "Reproducible, portable deployment", NAVY),
    ]
    for i, (k, v, c) in enumerate(tech):
        col = 5 + (i % 2) * 47
        row = 80 - (i // 2) * 6.3
        box(ax, col, row, 22, 5, k, c, fs=10, radius=1.3)
        ax.text(col + 23, row + 2.5, v, fontsize=10, va="center", color="#1e293b")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 21 — Streamlit + deployment + close
# ===========================================================================
def s_streamlit_close():
    fig, ax = new_slide()
    header(ax, "Streamlit UI & deployment", "Where a reviewer actually works", color=TEAL)
    ax.text(5, 84, "The Streamlit dashboard (app.py):", fontsize=13, fontweight="bold", color=NAVY)
    feats = ["Point at a GitHub URL (auto-clone) or local path",
             "Live, stage-by-stage progress as agents run",
             "Findings highlighted by severity + clickable file:line links to GitHub",
             "Interactive charts: severity/type pies, gauge, treemap, per-file bars",
             "Human-approval tab with reasons + Approve/Reject/override",
             "One-click PDF report; batch mode for large repos; model & cache controls"]
    for i, t in enumerate(feats):
        bullet(ax, 6, 78 - i * 4.6, t, fs=11.5, dot=TEAL)

    box(ax, 5, 34, 43, 8, "Run locally", NAVY, fs=12, radius=1.5)
    ax.text(6, 28, "streamlit run src/app.py", fontsize=11, family="monospace", color="#0f2740")
    box(ax, 52, 34, 43, 8, "Run in Docker", NAVY, fs=12, radius=1.5)
    ax.text(53, 28, "docker compose up --build", fontsize=11, family="monospace", color="#0f2740")

    ax.text(50, 17, "Find → Ground (RAG) → Fix → Verify → Human-approve → Report.  Explainable, cached, and portable.",
            ha="center", fontsize=12, color=TEAL, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#e6f5f3", ec=TEAL))
    footer(ax)
    slides.append(fig)


def main():
    for fn in [s_title, s_problem, s_bigpicture, s_why_agents, s_why_langgraph,
               s_flow, s_dataflow, s_agents_glance, s_nodes_1_2, s_node3, s_node4,
               s_why_rag, s_rag_engine, s_rag_documents, s_node6, s_node7, s_node8,
               s_node9, s_why_redis, s_datasets, s_stack, s_streamlit_close]:
        fn()
    out = os.path.join(os.path.dirname(__file__), "architecture_deck.pdf")
    with PdfPages(out) as pdf:
        for fig in slides:
            pdf.savefig(fig, facecolor=fig.get_facecolor())
            plt.close(fig)
    print(f"Wrote {out} ({len(slides)} slides)")


if __name__ == "__main__":
    main()
