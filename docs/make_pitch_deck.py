"""
Comprehensive technology / architecture pitch deck (<= 25 slides).

Explains, precisely: why each technology was chosen and what it solves, the
pipeline architecture, complete dataset info + how to input datasets, and the
rationale for RAG-optional, Redis, LoRA and every other major technology.

Run:    python docs/make_pitch_deck.py
Output: docs/tech_presentation_v1.pdf
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

# ---------------------------------------------------------------------------
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
plt.rcParams["figure.max_open_warning"] = 0
W, H = 13.33, 7.5

TOTAL = 25
_PAGE = [1]
slides = []


def new_slide():
    fig = plt.figure(figsize=(W, H), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")
    fig.patch.set_facecolor(WHITE)
    return fig, ax


def footer(ax):
    _PAGE[0] += 1
    ax.add_patch(plt.Rectangle((0, 0), 100, 2.2, color=NAVY, zorder=1))
    ax.text(2, 1.1, "AI Software Code Reviewer & Secure Development Agent",
            color=WHITE, fontsize=8, va="center")
    ax.text(98, 1.1, f"{_PAGE[0]} / {TOTAL}", color=WHITE, fontsize=8,
            va="center", ha="right")


def header(ax, title, subtitle=None, color=NAVY):
    ax.add_patch(plt.Rectangle((0, 90), 100, 10, color=color, zorder=1))
    ax.add_patch(plt.Rectangle((0, 89.3), 100, 0.7, color=BLUE, zorder=1))
    ax.text(3, 95, title, color=WHITE, fontsize=20, fontweight="bold", va="center")
    if subtitle:
        ax.text(3, 91.3, subtitle, color="#cbd5e1", fontsize=10.5, va="center")


def box(ax, x, y, w, h, text, fc, tc=WHITE, fs=11, bold=True, radius=1.5, ec=None):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                 boxstyle=f"round,pad=0.2,rounding_size={radius}",
                 linewidth=1.5, edgecolor=ec or fc, facecolor=fc, zorder=3))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            color=tc, fontsize=fs, fontweight="bold" if bold else "normal", zorder=4)


def arrow(ax, x1, y1, x2, y2, color=GREY, lw=2.2):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>",
                 mutation_scale=16, linewidth=lw, color=color, zorder=2))


def bullet(ax, x, y, text, fs=12, color="#1e293b", dot=BLUE):
    ax.text(x, y, "●", color=dot, fontsize=fs - 4, va="center")
    ax.text(x + 2.4, y, text, color=color, fontsize=fs, va="center")


def why_slide(title, subtitle, color, purpose, why, solves, rejected):
    """Standard 'why this technology' layout: 4 labelled blocks."""
    fig, ax = new_slide()
    header(ax, title, subtitle, color=color)
    blocks = [
        ("PURPOSE", purpose, BLUE, 70.5),
        ("WHY WE CHOSE IT", why, TEAL, 51.5),
        ("WHAT IT SOLVES", solves, AMBER, 32.5),
        ("ALTERNATIVE WE PASSED ON", rejected, GREY, 13.5),
    ]
    for label, body, c, y in blocks:
        box(ax, 4, y, 26, 12, label, c, fs=10.5, radius=1.6)
        ax.text(33, y + 6, body, fontsize=11.8, va="center", color="#1e293b", wrap=True)
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 1. Title
# ===========================================================================
def s_title():
    fig, ax = new_slide()
    ax.add_patch(plt.Rectangle((0, 0), 100, 100, color=NAVY, zorder=0))
    ax.add_patch(plt.Rectangle((0, 54), 100, 2, color=BLUE, zorder=1))
    ax.text(50, 76, "AI Software Code Reviewer", color=WHITE, fontsize=36,
            fontweight="bold", ha="center")
    ax.text(50, 67, "& Secure Development Agent", color=BLUE, fontsize=27,
            fontweight="bold", ha="center")
    ax.text(50, 59, "Technology & Architecture — why each choice, what it solves",
            color="#cbd5e1", fontsize=14, ha="center")
    chips = ["LangGraph", "Qwen2.5-Coder / Ollama", "Semgrep", "RAG", "Redis",
             "Verifier", "OpenTelemetry", "LoRA"]
    gap = 2.0
    widths = [0.92 * len(c) + 5 for c in chips]
    total = sum(widths) + gap * (len(chips) - 1)
    cx = (100 - total) / 2
    for c, w in zip(chips, widths):
        box(ax, cx, 42, w, 6, c, "#13385c", tc=WHITE, fs=9.5, radius=3, ec=BLUE)
        cx += w + gap
    ax.text(50, 30, "Team Alpha  •  Hackathon PS #06", color=WHITE,
            fontsize=13, ha="center", fontweight="bold")
    ax.text(50, 24, "Local-first  •  no API key required  •  runs offline",
            color="#94a3b8", fontsize=11.5, ha="center")
    slides.append(fig)


# ===========================================================================
# 2. What it does
# ===========================================================================
def s_what():
    fig, ax = new_slide()
    header(ax, "1. What it does", "One automated pipeline for secure code review")
    ax.text(4, 84, "The problem", fontsize=14, fontweight="bold", color=NAVY)
    for i, t in enumerate([
        "Manual review is slow and misses security bugs.",
        "Static tools flag issues but don't explain or fix them.",
        "Naive AI fixes hallucinate unsafe 'best practices'.",
    ]):
        bullet(ax, 5, 78 - i * 4.5, t)
    ax.text(4, 60, "Our solution — a multi-agent pipeline that:", fontsize=14,
            fontweight="bold", color=NAVY)
    steps = [("FINDS", "bugs + vulnerabilities", BLUE),
             ("GROUNDS", "fixes in real guidance", TEAL),
             ("VERIFIES", "its own fixes", AMBER),
             ("GUARDS", "via human approval", RED)]
    x = 5
    for t, d, c in steps:
        box(ax, x, 44, 21, 10, t, c, fs=14, radius=2)
        ax.text(x + 10.5, 39, d, ha="center", fontsize=10.5, color="#1e293b")
        x += 23.5
    ax.text(50, 26, "Input: a code repository      →      Output: explainable review report + verified fixes",
            ha="center", fontsize=12.5, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc=LIGHT, ec=GREY))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 3. Big picture architecture
# ===========================================================================
def s_bigpicture():
    fig, ax = new_slide()
    header(ax, "2. Architecture — the big picture",
           "9 specialist agents on a LangGraph pipeline over one shared state")
    box(ax, 4, 62, 15, 11, "CODE\nREPOSITORY", NAVY, fs=11, radius=2)
    arrow(ax, 19, 67.5, 29, 67.5, color=BLUE, lw=3)
    box(ax, 29, 60, 38, 15, "9-STAGE\nAI PIPELINE\n(LangGraph)", BLUE, fs=14, radius=2)
    arrow(ax, 67, 67.5, 77, 67.5, color=BLUE, lw=3)
    box(ax, 77, 62, 19, 11, "REVIEW REPORT\n(MD + JSON)", GREEN, fs=10.5, radius=2)
    ax.text(50, 52, "Every stage reads and adds to one typed ReviewState — flows left to right",
            ha="center", fontsize=11, color=GREY, style="italic")
    layers = [
        ("UNDERSTAND", "Ingestion • Static Analysis (Semgrep) • Code-LLM Review", BLUE),
        ("DETECT & GROUND", "Merge/Dedupe + Categorize • Secure-Coding RAG", TEAL),
        ("FIX & CHECK", "Explainable Fix Generation • Self-Verifier", AMBER),
        ("APPROVE & REPORT", "Human-in-the-Loop • Report + Trace", RED),
    ]
    y = 40
    for n, d, c in layers:
        box(ax, 5, y, 24, 6.4, n, c, fs=11, radius=1.5)
        box(ax, 31, y, 64, 6.4, d, LIGHT, tc="#1e293b", fs=10.5, bold=False, radius=1.5, ec=c)
        y -= 8
    ax.text(50, 6.5, "Cross-cutting: Redis cache • OpenTelemetry/LangSmith tracing • optional LoRA (offline)",
            ha="center", fontsize=10, color=PURPLE, fontweight="bold")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 4. Pipeline flow
# ===========================================================================
def s_flow():
    fig, ax = new_slide()
    header(ax, "3. The pipeline flow", "Data moves through 9 stages, in order")
    stages = [("1\nIngestion", BLUE), ("2\nStatic\nAnalysis", BLUE), ("3\nLLM\nReview", BLUE),
              ("4\nVuln +\nCategorize", TEAL), ("5\nRAG", TEAL), ("6\nReview\nGeneration", AMBER),
              ("7\nVerifier", AMBER), ("8\nHuman\nApproval", RED), ("9\nReport", GREEN)]
    x0, w, gap = 5, 16, 2.5
    for i, (t, c) in enumerate(stages[:5]):
        x = x0 + i * (w + gap)
        box(ax, x, 62, w, 14, t, c, fs=11, radius=2)
        if i < 4:
            arrow(ax, x + w, 69, x + w + gap, 69)
    lastx = x0 + 4 * (w + gap)
    arrow(ax, lastx + w / 2, 62, lastx + w / 2, 52)
    for i, (t, c) in enumerate(stages[5:]):
        x = x0 + (3 - i) * (w + gap)
        box(ax, x, 38, w, 14, t, c, fs=11, radius=2)
        if i < 3:
            nx = x0 + (3 - (i + 1)) * (w + gap)
            arrow(ax, x, 45, nx + w, 45)
    leg = [("Understand", BLUE), ("Detect & ground", TEAL), ("Fix & verify", AMBER), ("Approve/report", GREEN)]
    lx = 8
    for n, c in leg:
        ax.add_patch(plt.Rectangle((lx, 24), 3, 3, color=c))
        ax.text(lx + 4, 25.5, n, fontsize=10.5, va="center", color="#1e293b")
        lx += 22
    ax.text(50, 16, "Linear & traceable: each stage consumes the previous stage's output.",
            ha="center", fontsize=11, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 5. The 9 agents
# ===========================================================================
def s_agents():
    fig, ax = new_slide()
    header(ax, "4. The 9 agents", "Each LangGraph node is one specialist (agents/*.py)")
    agents = [
        ("1", "Ingestion", "Discover files, read contents, detect language", BLUE),
        ("2", "Static Analysis", "Semgrep rulesets + code graph + syntax errors", BLUE),
        ("3", "LLM Review", "Qwen reviews each file for logic/quality bugs", BLUE),
        ("4", "Vulnerability", "Merge+dedupe; categorize syntax/logic/security/quality", TEAL),
        ("5", "RAG", "Retrieve grounded OWASP/CWE + dataset guidance", TEAL),
        ("6", "Review Generation", "Explainable comment + minimal grounded fix", AMBER),
        ("7", "Verifier", "Syntax + no-op + Semgrep regression re-scan", AMBER),
        ("8", "Approval", "Route CRITICAL/ERROR or flagged fixes to a human", RED),
        ("9", "Report", "Assemble artifacts + agent trace (MD + JSON)", GREEN),
    ]
    y = 79; dy = 8.3
    for num, name, desc, c in agents:
        box(ax, 4, y, 7, 6, num, c, fs=14, radius=1.2)
        box(ax, 12, y, 25, 6, name, LIGHT, tc=NAVY, fs=11.5, radius=1.2, ec=c)
        ax.text(39, y + 3, desc, fontsize=10.8, va="center", color="#1e293b")
        y -= dy
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 6. Data flow (ReviewState)
# ===========================================================================
def s_dataflow():
    fig, ax = new_slide()
    header(ax, "5. Data flow — the shared ReviewState",
           "What each stage adds to one state object", color="#0369a1")
    rows = [
        ("Ingestion", "files, file_contents, languages", BLUE),
        ("Static Analysis", "semgrep_findings, syntax_findings, code_graph", BLUE),
        ("LLM Review", "llm_findings, llm_file_summaries", BLUE),
        ("Vulnerability", "merged_findings, quality_issues, category_breakdown", TEAL),
        ("RAG", "retrieved_guidance", TEAL),
        ("Review Generation", "suggested_fixes, review_comments", AMBER),
        ("Verifier", "verifier_report, confidence_score", AMBER),
        ("Approval", "approved_fixes, requires_approval, rejected_fixes", RED),
        ("Report", "final_report_md/json, quality_score, trace", GREEN),
    ]
    y = 79; dy = 8.0
    for i, (stage, fields, c) in enumerate(rows):
        box(ax, 5, y, 25, 5.6, stage, c, fs=10.5, radius=1.3)
        if i < len(rows) - 1:
            arrow(ax, 17, y, 17, y - dy + 5.6, color="#94a3b8", lw=1.6)
        ax.text(33, y + 2.8, "adds →", fontsize=9.5, color=GREY, style="italic", va="center")
        box(ax, 44, y, 52, 5.6, fields, LIGHT, tc="#1e293b", fs=10, bold=False, radius=1.3, ec=c)
        y -= dy
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 7. Technology map (why-at-a-glance)
# ===========================================================================
def s_techmap():
    fig, ax = new_slide()
    header(ax, "6. Technology choices at a glance", "Every major tech maps to a purpose")
    rows = [
        ("LangGraph", "Orchestrate 9 agents over one typed state", BLUE),
        ("Qwen2.5-Coder + Ollama", "Local code-LLM: semantic review + fixes", AMBER),
        ("Semgrep", "Fast deterministic static vuln detection", TEAL),
        ("AST code graph", "Structural context (GNN stand-in)", TEAL),
        ("RAG (FAISS/TF-IDF)", "Ground fixes in OWASP/CWE + datasets", PURPLE),
        ("Redis", "Cache LLM calls: repeat runs instant", RED),
        ("Verifier", "Validate AI fixes before applying", AMBER),
        ("OpenTelemetry / LangSmith", "Per-agent observability / tracing", "#0369a1"),
        ("LoRA (offline)", "Optional fine-tune toward secure coding", GREY),
        ("Streamlit", "Interactive dashboard + toggles", GREEN),
    ]
    y = 80; dy = 7.4
    for name, purpose, c in rows:
        box(ax, 5, y, 34, 5.6, name, c, fs=11, radius=1.3)
        ax.text(41, y + 2.8, purpose, fontsize=11.5, va="center", color="#1e293b")
        y -= dy
    ax.text(50, 5.5, "The next slides explain each choice precisely.",
            ha="center", fontsize=10.5, color=GREY, style="italic")
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 8-. Per-technology "why" slides
# ===========================================================================
def s_langgraph():
    why_slide(
        "7. LangGraph", "Multi-agent orchestration", BLUE,
        "Wire the 9 agents into an explicit StateGraph that passes one typed "
        "ReviewState from node to node.",
        "Explicit graph + shared state contract makes the pipeline deterministic, "
        "traceable, and easy to extend; integrates natively with LangSmith.",
        "Coordinating specialized agents (the PS 'Agentic Challenge') without "
        "brittle ad-hoc function chaining.",
        "Hand-rolled function calls: no state schema, no tracing hooks, hard to extend.")


def s_qwen():
    why_slide(
        "8. Qwen2.5-Coder + Ollama", "The Code-LLM (semantic review + fixes)", AMBER,
        "A code-specialised LLM that reads each file for logic bugs and writes "
        "minimal fixes, served locally by Ollama.",
        "Qwen2.5-Coder is code-trained (named in the PS) and beats a general model "
        "at code; Ollama runs it locally — no API key, no data egress, free, with "
        "native constrained-JSON (format:json) for reliable structured output.",
        "Semantic bugs static analysis can't see; producing grounded, parseable fixes "
        "on a small local model.",
        "Hosted API (cost, key, data leaves the machine) — kept pluggable for those who want it.")


def s_semgrep():
    why_slide(
        "9. Semgrep", "Static & semantic analysis", TEAL,
        "Scan the repo with OWASP/CWE rulesets for known vulnerability patterns "
        "(SQLi, secrets, eval, weak crypto, ...).",
        "Fast, deterministic, high-precision, industry-standard rules; ships an "
        "offline fallback ruleset so it works with no internet.",
        "High-confidence detection of known vuln classes — the deterministic half "
        "that complements the LLM's semantic half.",
        "CodeQL (heavier, slower setup); pure-LLM detection (hallucination-prone).")


def s_codegraph():
    why_slide(
        "10. AST Code Graph", "Structural context (honest GNN stand-in)", TEAL,
        "Build a function/class/call graph + flagged dangerous sinks and feed it to "
        "the LLM as structural context.",
        "AST/regex is cheap, deterministic and offline; it is an explicit stand-in "
        "for the PS's 'Graph Neural Network', which is NOT implemented.",
        "Gives the LLM code structure instead of raw text, improving review quality.",
        "A trained GNN (needs labeled graph data + GPU) — documented swap-in, out of scope.")


def s_rag():
    why_slide(
        "11. RAG (Retrieval-Augmented Generation)", "Grounded, explainable fixes", PURPLE,
        "For each finding, retrieve authoritative secure-coding guidance (OWASP/CWE "
        "+ dataset samples) and inject it into the fix prompt.",
        "Grounding kills hallucinated 'best practices'; gives every fix a citable "
        "source; updatable by editing a JSON file (no retraining); lets a small local "
        "model punch above its weight.",
        "The worst failure mode for a security tool: a confident but wrong fix.",
        "Rely on the model's memory alone (hallucinates, can't cite, hard to update).")


def s_rag_optional():
    fig, ax = new_slide()
    header(ax, "12. Why RAG is optional", "A user toggle, not a fixed stage", color=PURPLE)
    ax.text(4, 84, "RAG can be switched off from Streamlit, the CLI (--no-rag), or USE_RAG.",
            fontsize=12, color="#1e293b")
    reasons = [
        ("Compare grounded vs. ungrounded", "Run the same repo with RAG on and off to "
         "show the value of grounding — a clean ablation/demo."),
        ("Speed & simplicity", "Skip retrieval when you just want the model's raw "
         "behaviour or a faster pass."),
        ("Graceful fallback", "With RAG off, fix generation falls back to general "
         "secure-coding best practice (logged as a notice)."),
        ("Flexibility for evaluation", "Reviewers/mentors can inspect exactly how much "
         "RAG contributes to fix quality."),
    ]
    y = 74
    for t, d in reasons:
        box(ax, 4, y, 30, 9, t, PURPLE, fs=10.5, radius=1.5)
        ax.text(37, y + 4.5, d, fontsize=11, va="center", color="#1e293b")
        y -= 12
    ax.text(50, 8, "Default is ON (grounded). Off is a deliberate, surfaced choice — not a limitation.",
            ha="center", fontsize=11, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.5", fc=LIGHT, ec=PURPLE))
    footer(ax)
    slides.append(fig)


def s_rag_internals():
    fig, ax = new_slide()
    header(ax, "13. Inside the RAG engine", "How grounded guidance is retrieved", color="#0369a1")
    box(ax, 4, 74, 20, 10, "A finding\n(CWE / rule id\n+ message)", NAVY, fs=10.5, radius=2)
    arrow(ax, 24, 79, 33, 79, color="#0369a1", lw=2.5)
    box(ax, 33, 74, 26, 10, "1) Fast path:\nmatch by CWE / tag", TEAL, fs=11, radius=2)
    arrow(ax, 46, 74, 46, 66, color="#0369a1", lw=2.5)
    ax.text(61, 79, "no match?", fontsize=10, color=RED, style="italic", va="center")
    box(ax, 33, 56, 26, 10, "2) Semantic search:\nembedding similarity", BLUE, fs=11, radius=2)
    box(ax, 68, 58, 28, 24, "CORPUS\n\n• 12 OWASP/CWE docs\n• distilled dataset samples\n• fetched real rows\n  (Devign/CodeXGLUE)",
        LIGHT, tc="#1e293b", fs=10.5, bold=False, radius=2, ec="#0369a1")
    arrow(ax, 59, 79, 68, 74, color=GREY)
    arrow(ax, 59, 61, 68, 66, color=GREY)
    box(ax, 8, 40, 38, 8, "Primary: sentence-transformers + FAISS", BLUE, fs=10.5, radius=1.5)
    box(ax, 54, 40, 38, 8, "Fallback: scikit-learn TF-IDF (offline)", TEAL, fs=10.5, radius=1.5)
    ax.text(50, 32, "Auto-falls back to TF-IDF if embedding models can't download — RAG works offline.",
            ha="center", fontsize=10.5, color=GREY, style="italic")
    ax.text(50, 22, "Retrieved snippets are injected into the fix prompt; the LLM must ground its fix in them.",
            ha="center", fontsize=11.5, color="#0369a1", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc="#e0f2fe", ec="#0369a1"))
    footer(ax)
    slides.append(fig)


def s_redis():
    why_slide(
        "14. Redis", "LLM-response caching layer", RED,
        "Cache every LLM completion keyed by (provider, model, prompt, mode); a "
        "repeat request is served from cache instead of re-querying the model.",
        "LLM calls are the slow, expensive step; identical calls recur across runs. "
        "Redis is fast with TTL; redislite gives a zero-setup embedded fallback; it "
        "can also back a vector store later.",
        "Latency & cost of repeated identical LLM calls — measured 4.7s → 0.001s on a cache hit.",
        "No cache (every run re-pays full LLM latency) — painful on a local model.")


def s_verifier():
    why_slide(
        "15. Verifier / Self-Reflection", "Validate AI fixes before applying", AMBER,
        "Independently check each generated fix: syntax parse, no-op detection, and a "
        "Semgrep regression re-scan confirming the rule no longer fires.",
        "LLMs hallucinate and produce inconsistent code; an independent check is "
        "essential before a fix is ever called 'applyable'.",
        "Hallucinated / lazy / broken fixes — yields a per-fix + overall confidence score.",
        "Trusting raw LLM output (unsafe for security remediation).")


def s_hitl():
    why_slide(
        "16. Human-in-the-Loop Approval", "The safety gate", RED,
        "Route CRITICAL/ERROR-severity or verifier-flagged fixes to a human; low-risk "
        "verified fixes can auto-approve.",
        "Security changes need human accountability; nothing high-risk should apply "
        "automatically.",
        "Unsafe auto-application of high-impact changes — keeps a human in control.",
        "Fully autonomous apply (dangerous and non-auditable for security).")


def s_categories():
    why_slide(
        "17. Finding Categorization", "Distinct, surfaced categories", TEAL,
        "Tag every finding as syntax / logic / security / quality and surface each in "
        "its own report section, with a category_breakdown.",
        "Reviewers triage by type; syntax errors were previously only parser metadata, "
        "now promoted to first-class CRITICAL findings.",
        "The review requirement for clear separation of bug/vuln/quality classes.",
        "One undifferentiated list of findings (hard to triage).")


def s_observability():
    why_slide(
        "18. Observability (OpenTelemetry / LangSmith)", "Per-agent tracing", "#0369a1",
        "Wrap every node so each run records a span (name, duration, status, output) "
        "into state['trace']; export to OTLP (Phoenix/Jaeger) or LangSmith.",
        "You can't improve what you can't see; the PS names Phoenix/MLflow. Set "
        "TRACING_BACKEND to export real spans; default is in-process only.",
        "No visibility into per-agent timing/status/failures.",
        "Only printing final output + error strings (no timing, no external trace).")


def s_lora():
    why_slide(
        "19. LoRA Fine-Tuning", "Optional offline training track", GREY,
        "Parameter-efficient fine-tuning of a Code-LLM toward secure-coding "
        "recommendations; trains tiny adapter weights, saved to disk.",
        "LoRA trains ~1-2% of params (small, cheap adapter) vs. full fine-tune; it is "
        "the PS's 'trained model' technique, offered ALONGSIDE RAG, not replacing it.",
        "Specialising a model's default behaviour without a full retrain.",
        "Full fine-tune (GPU-heavy, static, can't cite sources) — and it never runs at app time.")


# ===========================================================================
# 20. Datasets - complete info
# ===========================================================================
def s_datasets():
    fig, ax = new_slide()
    header(ax, "20. Datasets — complete information",
           "The named vulnerability datasets and how each is used")
    data = [
        ("Devign", "C/C++ vuln functions", "RAG (fetched via CodeXGLUE)"),
        ("Big-Vul (MSR'20)", "CVE-labeled vulns", "RAG (distilled sample)"),
        ("CodeXGLUE", "Code intelligence", "RAG source (Devign defect-detection)"),
        ("Juliet (NIST)", "CWE weakness cases", "RAG (distilled sample)"),
        ("DiverseVul", "Large-scale vulns", "RAG (distilled sample)"),
        ("ManySStuBs4J", "Java bug fixes", "RAG (distilled sample)"),
        ("SAP Project-KB", "Java bug benchmark", "RAG (distilled sample)"),
        ("OWASP Benchmark", "Tool benchmark", "Reference / evaluation"),
    ]
    ax.text(6, 84, "Dataset", fontsize=11, fontweight="bold", color=NAVY)
    ax.text(34, 84, "Purpose", fontsize=11, fontweight="bold", color=NAVY)
    ax.text(63, 84, "How used here", fontsize=11, fontweight="bold", color=NAVY)
    y = 78
    for name, purpose, used in data:
        box(ax, 5, y, 26, 5, name, LIGHT, tc=NAVY, fs=10.5, radius=1.2, ec=BLUE)
        ax.text(34, y + 2.5, purpose, fontsize=10.3, va="center", color="#1e293b")
        ax.text(63, y + 2.5, used, fontsize=10.3, va="center", color="#1e293b")
        y -= 6.3
    ax.text(50, 20, "Representative, not exhaustive: RAG uses ~10 distilled samples + an optional",
            ha="center", fontsize=10.8, color=NAVY)
    ax.text(50, 16.5, "capped subset streamed from ONE dataset (default Devign). Full multi-GB",
            ha="center", fontsize=10.8, color=NAVY)
    ax.text(50, 13, "datasets are NOT ingested in full, and none is loaded live at review time.",
            ha="center", fontsize=10.8, color=NAVY)
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 21. How to input datasets
# ===========================================================================
def s_input_datasets():
    fig, ax = new_slide()
    header(ax, "21. How to input datasets", "Three ways to feed the RAG corpus", color=PURPLE)
    ways = [
        ("1  Bundled samples", "Curated CWE-labeled rows already in the repo.",
         "rag/datasets/vuln_samples.jsonl  (auto-loaded)"),
        ("2  Fetch real rows", "Stream vulnerable functions from HuggingFace and cache them.",
         "python rag/fetch_datasets.py --dataset <hf-id> --limit 200"),
        ("3  Drop-in your own", "Add any *.jsonl of {dataset,cwe,vulnerable,guidance,tags};\nthe loader globs the folder automatically.",
         "rag/datasets/my_data.jsonl"),
    ]
    y = 72
    for title, desc, code in ways:
        box(ax, 4, y, 26, 11, title, PURPLE, fs=11.5, radius=1.6)
        ax.text(33, y + 8, desc, fontsize=11, va="center", color="#1e293b")
        ax.text(33, y + 2.8, code, fontsize=10, va="center", color="#e2e8f0", family="monospace",
                bbox=dict(boxstyle="round,pad=0.4", fc=NAVY, ec=PURPLE))
        y -= 15
    ax.text(50, 20, "Toggle the whole feature with USE_DATASET_RAG. Fetched cache is auto-included, no code change.",
            ha="center", fontsize=10.5, color=GREY, style="italic")
    ax.text(50, 14, "Note: the CODE to review is a different input — pass it with  --repo <path>  (CLI) or the Streamlit path box.",
            ha="center", fontsize=10.5, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.5", fc=LIGHT, ec=GREY))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 22. Tech stack + run
# ===========================================================================
def s_run():
    fig, ax = new_slide()
    header(ax, "22. Stack & how to run", "Local-first, no API key")
    ax.text(5, 84, "Core stack", fontsize=13, fontweight="bold", color=NAVY)
    stack = [("Orchestration", "LangGraph", BLUE), ("Code-LLM", "Ollama – Qwen2.5-Coder", AMBER),
             ("Static analysis", "Semgrep", TEAL), ("RAG", "FAISS / TF-IDF + datasets", PURPLE),
             ("Cache", "Redis / redislite", RED), ("Tracing", "OpenTelemetry / LangSmith", "#0369a1"),
             ("UI", "Streamlit", GREEN), ("Fine-tune", "PEFT LoRA (offline)", GREY)]
    for i, (k, v, c) in enumerate(stack):
        col = 5 + (i % 2) * 47
        row = 76 - (i // 2) * 6.5
        box(ax, col, row, 19, 5, k, c, fs=10, radius=1.3)
        ax.text(col + 21, row + 2.5, v, fontsize=11, va="center", color="#1e293b")
    ax.text(5, 43, "Run", fontsize=13, fontweight="bold", color=NAVY)
    code = ("pip install -r requirements.txt\n"
            "ollama serve  &&  ollama pull qwen2.5-coder:1.5b\n\n"
            "python main.py --repo ./sample_repo          # CLI review\n"
            "python main.py --repo ./sample_repo --no-rag # ungrounded baseline\n"
            "streamlit run app.py                         # dashboard + toggles\n"
            "python rag/fetch_datasets.py                 # real dataset rows into RAG")
    ax.text(6, 22, code, fontsize=10.8, family="monospace", color="#e2e8f0", va="center",
            bbox=dict(boxstyle="round,pad=0.9", fc=NAVY, ec=BLUE))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 23. Scope & honest limitations
# ===========================================================================
def s_limits():
    fig, ax = new_slide()
    header(ax, "23. Scope & honest limitations", "What is real vs. a stand-in", color=NAVY)
    items = [
        ("No trained GNN / vuln model", "Structure is AST/regex; detection is Semgrep + LLM."),
        ("Dataset RAG is representative", "Distilled samples + a capped fetched subset, not full ingestion."),
        ("Tracing is span-level", "Real OTLP/LangSmith export, but not token-level LLM tracing."),
        ("LoRA is offline & optional", "Needs a GPU; the app never trains at runtime."),
        ("Local-repo review only", "No GitHub/GitLab/Docker/FastAPI integration."),
        ("Small local model ceiling", "qwen2.5-coder:1.5b can truncate; larger models improve quality."),
    ]
    y = 78
    for t, d in items:
        box(ax, 5, y, 34, 8, t, "#334155", fs=10.5, radius=1.4)
        ax.text(41, y + 4, d, fontsize=10.8, va="center", color="#1e293b")
        y -= 10.5
    ax.text(50, 8, "Stated plainly on purpose — credibility comes from precise claims.",
            ha="center", fontsize=11, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.5", fc=LIGHT, ec=GREY))
    footer(ax)
    slides.append(fig)


# ===========================================================================
# 24. Why this design wins
# ===========================================================================
def s_summary():
    fig, ax = new_slide()
    header(ax, "24. Why this design", "The through-line")
    points = [
        ("Hybrid detection", "Deterministic Semgrep + semantic LLM catch different bugs.", BLUE),
        ("Grounded & explainable", "RAG-cited fixes, not hallucinated best practice.", PURPLE),
        ("Self-checking + safe", "Verifier re-validates; humans gate high-risk changes.", AMBER),
        ("Observable", "Every agent traced; exportable to Phoenix/LangSmith.", "#0369a1"),
        ("Local-first & offline", "No API key; Ollama + Semgrep/RAG fallbacks.", TEAL),
        ("Honest & extensible", "Clear stand-ins; datasets, LoRA, tracing all pluggable.", GREEN),
    ]
    y = 78
    for t, d, c in points:
        box(ax, 5, y, 30, 8, t, c, fs=11.5, radius=1.4)
        ax.text(37, y + 4, d, fontsize=11.3, va="center", color="#1e293b")
        y -= 10.5
    ax.text(50, 8, "Finds → Grounds → Verifies → Guards → Explains",
            ha="center", fontsize=13, color=NAVY, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.6", fc=LIGHT, ec=BLUE))
    footer(ax)
    slides.append(fig)


# ===========================================================================
def main():
    s_title(); s_what(); s_bigpicture(); s_flow(); s_agents(); s_dataflow()
    s_techmap(); s_langgraph(); s_qwen(); s_semgrep(); s_codegraph(); s_rag()
    s_rag_optional(); s_rag_internals(); s_redis(); s_verifier(); s_hitl()
    s_categories(); s_observability(); s_lora(); s_datasets(); s_input_datasets()
    s_run(); s_limits(); s_summary()

    out = os.path.join(os.path.dirname(__file__), "tech_presentation_v1.pdf")
    with PdfPages(out) as pdf:
        for fig in slides:
            pdf.savefig(fig, facecolor=fig.get_facecolor())
            plt.close(fig)
    print(f"Wrote {out} ({len(slides)} slides)")


if __name__ == "__main__":
    main()
