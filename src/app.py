"""
Streamlit dashboard for the AI Software Code Reviewer & Secure Development
Agent. Point it at a GitHub repo (or a local path), run the multi-agent
pipeline, then:
  - watch each agent/stage execute live (step-by-step progress),
  - see findings highlighted by severity with the offending code snippet,
  - click a finding's file:line to jump straight to that line on GitHub,
  - explore consolidated bug/issue pie, bar, gauge & treemap charts,
  - approve/reject pending high-risk fixes (Human-in-the-Loop),
  - download a finalized PDF report (KPIs + charts + listed issues).

Run with: streamlit run src/app.py
"""
import json
import os
import sys

# Make the app's own package dir (src/) importable regardless of how it's
# launched — `streamlit run src/app.py`, AppTest, or `python src/app.py` — so
# the `from agents... / from config import ...` imports below always resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

from agents.report_agent import report_node
from config import (ANTHROPIC_MODEL, GEMINI_MODEL, GROK_MODEL, OLLAMA_MODEL,
                    OPENAI_MODEL, REDIS_ENABLED, USE_RAG)
from graph import PIPELINE_NODES, build_graph
from utils import charts
from utils.code_parser import discover_source_files
from utils.github_repo import clone_repo
from utils.llm_client import (API_KEY_ENV, list_gemini_models, list_grok_models,
                              list_ollama_models, ollama_reachable, provider_ready,
                              set_gemini_model, set_grok_model, set_ollama_model,
                              set_provider)
from config import OLLAMA_ENDPOINT
from utils.pdf_report import build_pdf
from utils.redis_cache import get_cache, set_enabled

# Sidebar backend options -> internal provider id. Ollama (local) first so the
# default behaviour is unchanged; hosted APIs are far faster and make the
# per-file/per-finding parallelism actually overlap.
_BACKENDS = {
    "Ollama (local)": "ollama",
    "Gemini (API)": "gemini",
    "Grok / xAI (API)": "grok",
    "Anthropic (API)": "anthropic",
    "OpenAI (API)": "openai",
}
_HOSTED_MODEL = {
    "gemini": GEMINI_MODEL, "grok": GROK_MODEL,
    "anthropic": ANTHROPIC_MODEL, "openai": OPENAI_MODEL,
}

# Lighter models first — on a RAM-constrained box a smaller model is markedly
# faster (and lets Ollama fit the request without swapping).
_MODEL_PREFERENCE = ["llama3.2:1b", "qwen2.5-coder:1.5b"]


@st.cache_data(ttl=60)
def _available_models() -> list[str]:
    installed = list_ollama_models()
    ordered = [m for m in _MODEL_PREFERENCE if m in installed]
    ordered += [m for m in installed if m not in ordered]
    return ordered or [OLLAMA_MODEL]

st.set_page_config(page_title="AI Code Reviewer & Secure Dev Agent",
                   page_icon=":material/security:", layout="wide")

# --------------------------------------------------------------------------- #
# Styling — larger, more readable type + a bold gradient header. (CSS injection
# is used here because the user explicitly asked for bigger fonts / punchier
# visuals; native theming alone can't size the hero banner.)
# --------------------------------------------------------------------------- #
st.markdown("""
<style>
  html, body, [class*="css"], .stMarkdown, .stMarkdown p { font-size: 1.06rem; }
  .block-container { padding-top: 2rem; }

  .hero {
    background: linear-gradient(120deg, #1e3a8a 0%, #6d28d9 55%, #be185d 100%);
    padding: 1.4rem 1.8rem; border-radius: 16px; color: #fff;
    box-shadow: 0 6px 22px rgba(76, 29, 149, 0.35); margin-bottom: 1.1rem;
  }
  .hero h1 { color: #fff; font-size: 2.15rem; margin: 0 0 .3rem 0; font-weight: 800; }
  .hero p  { color: #e7e5ff; font-size: 1.02rem; margin: 0; }

  /* Bigger, bolder KPI metrics */
  [data-testid="stMetricValue"] { font-size: 2.2rem; font-weight: 800; }
  [data-testid="stMetricLabel"] { font-size: 1.02rem; opacity: .85; }

  /* Roomier, larger tab labels */
  .stTabs [data-baseweb="tab"] { font-size: 1.05rem; padding: .5rem 1rem; }
  .stTabs [aria-selected="true"] { font-weight: 700; }

  h2, h3 { font-weight: 750; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>🛡️ AI Software Code Reviewer &amp; Secure Development Agent</h1>
  <p>Multi-agent pipeline: ingestion → static analysis → Code-LLM review →
     vulnerability merge → secure-coding RAG → fix generation → verifier →
     human approval → report</p>
</div>
""", unsafe_allow_html=True)

_SEV_BADGE = {
    "CRITICAL": ":red-badge[🟥 CRITICAL]",
    "ERROR": ":red-badge[🟧 ERROR]",
    "WARNING": ":orange-badge[🟨 WARNING]",
    "INFO": ":blue-badge[🟦 INFO]",
}


def _sev_badge(sev: str) -> str:
    return _SEV_BADGE.get(sev, f":gray-badge[{sev}]")


def _loc_link(finding: dict) -> str:
    """Rendered file:line — a clickable GitHub link when we cloned from GitHub."""
    gh = st.session_state.get("gh")
    file, line = finding.get("file", "?"), finding.get("line", "?")
    if gh:
        url = gh.blob_url(file, finding.get("line"), finding.get("end_line"))
        return f"[`{gh.rel_path(file)}:{line}`]({url})"
    return f"`{file}:{line}`"


def _run_pipeline_streamed(state: dict) -> dict:
    """Invoke the graph via .stream() so the UI can show each stage as it runs.

    Nodes return the full state each step, so merging every update delta yields
    the complete final state — while the node key tells us which stage finished.
    """
    app = build_graph()
    labels = dict(PIPELINE_NODES)
    total = len(PIPELINE_NODES)
    final_state = dict(state)

    progress = st.progress(0.0, text="Starting pipeline…")
    had_error = False
    with st.status("Running multi-agent review pipeline…", expanded=True) as status:
        done = 0
        for chunk in app.stream(state, stream_mode="updates"):
            for node_name, delta in chunk.items():
                if delta:
                    final_state.update(delta)
                done += 1
                label = labels.get(node_name, node_name)
                # The traced wrapper records each node's status in the trace;
                # surface a real ✓/⚠ instead of always showing success.
                span = (final_state.get("trace") or [{}])[-1]
                if span.get("node") == node_name and span.get("status") == "error":
                    had_error = True
                    status.write(f":orange[⚠] **{label}** — {span.get('error', 'error')}")
                else:
                    status.write(f":green[✓] **{label}**")
                nxt = PIPELINE_NODES[done][1] if done < total else None
                txt = f"{label} done" + (f" · next: {nxt}" if nxt else " · finalizing")
                progress.progress(done / total, text=txt)
        if had_error:
            status.update(label="Review complete (with warnings — see notices)",
                          state="error", expanded=False)
        else:
            status.update(label="Review complete", state="complete", expanded=False)
    progress.empty()
    return final_state


# --------------------------------------------------------------------------- #
# Batch mode: review ALL files in chunks, accumulating results per chunk.
# --------------------------------------------------------------------------- #
_ACC_LIST_KEYS = ["files", "merged_findings", "quality_issues", "suggested_fixes",
                  "review_comments", "requires_approval", "approved_fixes",
                  "rejected_fixes", "trace"]


def _merge_batch(acc: dict, batch: dict) -> dict:
    """Fold one batch's pipeline result into the running accumulator."""
    for k in _ACC_LIST_KEYS:
        acc[k] = (acc.get(k) or []) + (batch.get(k) or [])
    cb = dict(acc.get("category_breakdown") or {})
    for k, v in (batch.get("category_breakdown") or {}).items():
        cb[k] = cb.get(k, 0) + v
    acc["category_breakdown"] = cb
    cg = dict(acc.get("code_graph_summary") or {})
    cg["total_loc"] = cg.get("total_loc", 0) + (batch.get("code_graph_summary") or {}).get("total_loc", 0)
    acc["code_graph_summary"] = cg
    for merge_key in ("retrieved_guidance", "languages"):
        m = dict(acc.get(merge_key) or {})
        m.update(batch.get(merge_key) or {})
        acc[merge_key] = m
    seen = acc.setdefault("errors", [])
    for e in (batch.get("errors") or []):
        if e not in seen:
            seen.append(e)
    for k in ("repo_path", "use_rag", "use_redis", "model_used", "auto_approve"):
        if k in batch:
            acc[k] = batch[k]
    return acc


def _finalize_acc(acc: dict) -> dict:
    """Recompute confidence over the union of fixes, then rebuild the report."""
    fixes = acc.get("suggested_fixes") or []
    flagged = sum(1 for f in fixes if not f.get("verified", False))
    acc["confidence_score"] = round(1 - flagged / (len(fixes) or 1), 3)
    return report_node(acc)


def _run_pipeline_batched(base_state: dict, all_files: list, batch_size: int) -> dict:
    """Run the pipeline over ALL files in chunks of `batch_size`, showing
    cumulative results after each chunk. Same total work as one pass, but with
    incremental progress and partial results if you stop early."""
    app = build_graph()
    batches = [all_files[i:i + batch_size] for i in range(0, len(all_files), batch_size)]
    acc: dict = {"repo_path": base_state["repo_path"], "errors": []}

    progress = st.progress(0.0, text=f"0/{len(batches)} batches")
    with st.status(f"Batch review: {len(all_files)} files in {len(batches)} "
                   f"batch(es) of up to {batch_size}…", expanded=True) as status:
        for bi, batch in enumerate(batches, start=1):
            status.write(f"⏳ Batch {bi}/{len(batches)} — reviewing {len(batch)} file(s)…")
            bresult = app.invoke({**base_state, "file_subset": batch, "errors": []})
            _merge_batch(acc, bresult)
            # Publish a cumulative snapshot so partial results survive if the
            # user navigates away mid-run.
            st.session_state.result = _finalize_acc(dict(acc))
            nf = len(acc.get("merged_findings") or [])
            nfix = len(acc.get("suggested_fixes") or [])
            status.write(f":green[✓] Batch {bi}/{len(batches)} done — "
                         f"cumulative: **{nf} findings, {nfix} fixes**")
            progress.progress(bi / len(batches),
                              text=f"{bi}/{len(batches)} batches · {nf} findings so far")
        status.update(label=f"Batch review complete — {len(all_files)} files, "
                            f"{len(batches)} batches", state="complete", expanded=False)
    progress.empty()
    return _finalize_acc(acc)


# --------------------------------------------------------------------------- #
# Sidebar — source + options
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("Review setup")
    st.caption("Fill in steps 1–4, then click **Run review pipeline** at the bottom.")

    # ---- 1. Source ----
    st.markdown("### 1 · Code to review")
    source_mode = st.radio("Where is the code?", ["GitHub URL", "Local path"],
                           label_visibility="collapsed", horizontal=True)
    if source_mode == "GitHub URL":
        github_url = st.text_input(
            "GitHub repository URL",
            placeholder="https://github.com/owner/repo")
        repo_path = None
        st.caption("Paste a **public** GitHub repo URL. Add `/tree/<branch>` to "
                   "pin a branch. The repo is cloned and its source files reviewed.")
    else:
        github_url = None
        repo_path = st.text_input("Repository path", value="./sample_repo")
        st.caption("Path to a folder already on this machine.")

    # ---- 2. Backend ----
    st.markdown("### 2 · AI model")
    backend_label = st.selectbox("Backend", list(_BACKENDS), index=0,
                                 label_visibility="collapsed")
    provider = _BACKENDS[backend_label]
    set_provider(provider)

    if provider == "ollama":
        models = _available_models()
        model_choice = st.selectbox(
            "Ollama model", models, index=0, label_visibility="collapsed")
        set_ollama_model(model_choice)
        speed = ("⚡ Fast, light on RAM — good for a quick pass."
                 if model_choice == "llama3.2:1b" else
                 "🎯 Code-specialized — more accurate but slower on CPU."
                 if "coder" in model_choice else "")
        st.caption(f"**Runs locally (free, private) but slow on CPU.** {speed}")
        if not ollama_reachable():
            st.error(
                f"Can't reach Ollama at `{OLLAMA_ENDPOINT}`. If the app runs in "
                f"**Docker**, the host's Ollama must listen on `0.0.0.0` "
                f"(set `OLLAMA_HOST=0.0.0.0:11434` and restart it) — a default "
                f"`127.0.0.1` bind refuses container connections. Otherwise start "
                f"it with `ollama serve`.", icon=":material/lan:")
    else:
        model_choice = _HOSTED_MODEL[provider]
        env = API_KEY_ENV[provider]
        st.caption("**Cloud API — much faster.** "
                   "Needs an API key (used only this session, never saved).")
        if not provider_ready(provider):
            key = st.text_input(f"{backend_label} API key", type="password")
            if key:
                os.environ[env] = key.strip()
        if provider_ready(provider):
            st.caption("🟢 API key detected — ready.")
            # Gemini model ids get retired (404 'no longer available'), so pick
            # from the models this key can actually use.
            if provider == "gemini":
                gm = list_gemini_models()
                if gm:
                    default_idx = gm.index(model_choice) if model_choice in gm else 0
                    model_choice = st.selectbox("Gemini model", gm, index=default_idx)
                else:
                    model_choice = st.text_input("Gemini model", value=model_choice,
                        help="Couldn't list models — check the key/network. Enter a "
                             "valid id, e.g. gemini-2.0-flash.")
                set_gemini_model(model_choice)
                st.caption("⚠️ Free tier ≈ 5 requests/min. For big repos, use a low "
                           "'Files per batch', or a paid key. The app auto-retries "
                           "on rate limits.")
            elif provider == "grok":
                gk = list_grok_models()
                if gk:
                    default_idx = gk.index(model_choice) if model_choice in gk else 0
                    model_choice = st.selectbox("Grok model", gk, index=default_idx)
                else:
                    model_choice = st.text_input("Grok model", value=model_choice,
                        help="Couldn't list models — check the key/network. Enter a "
                             "valid id, e.g. grok-3 or grok-4.")
                set_grok_model(model_choice)
                st.caption("🚀 Paid xAI key — fast and fully parallel (great for "
                           "big repos).")
            st.caption(f"Model: `{model_choice}`")
        else:
            st.warning(f"Enter your {backend_label} key to use this backend.",
                       icon=":material/key:")

    # ---- 3. Scope / batching ----
    st.markdown("### 3 · How files are processed")
    st.caption("The reviewer inspects **one file at a time with the AI model**, "
               "so run time grows with the number of files.")

    batch_size = st.number_input(
        "Files per batch (0 = all in one pass)", min_value=0, max_value=500,
        value=0, step=5)
    if batch_size > 0:
        st.success(
            f"**Batch mode: ALL files reviewed, {batch_size} at a time.** "
            f"Results appear after each batch (full coverage). Same total time as "
            f"one pass, but you see progress and partial results as it goes.",
            icon=":material/dynamic_feed:")
        max_files = 0  # batch mode always covers everything
    else:
        max_files = st.number_input(
            "Max files to review (0 = all)", min_value=0, max_value=1000,
            value=0, step=5)
        if max_files == 0:
            st.success("**Single pass over EVERY source file** (full coverage). "
                       "Best with a cloud model; a big repo on local Ollama can "
                       "take a while.", icon=":material/check_circle:")
        else:
            st.info(f"**Reviewing only the first {max_files} file(s)**, rest "
                    f"skipped — faster, partial coverage. Use *Files per batch* "
                    f"instead for full coverage with progress.",
                    icon=":material/filter_alt:")

    # ---- 4. Options ----
    st.markdown("### 4 · Options")
    use_rag = st.toggle("Ground fixes in OWASP/CWE guidance (RAG)", value=USE_RAG)
    st.caption("On: fix suggestions cite retrieved secure-coding guidance. "
               "Off: the model relies on general knowledge only.")
    use_redis = st.toggle("Cache AI responses (Redis)", value=REDIS_ENABLED)
    st.caption("On: identical file+model requests are cached, so re-running the "
               "same repo is near-instant.")
    require_all_approval = st.toggle("Require my approval for ALL fixes", value=False)
    auto_approve = not require_all_approval
    st.caption("Off: low-risk **verified** fixes are auto-approved; only "
               "**high-severity or unverified** fixes wait for you. "
               "On: every fix waits for your Approve/Reject in the "
               "**Human approval** tab.")

    set_enabled(use_redis)
    if use_redis:
        stats = get_cache().stats()
        if stats["available"]:
            st.caption(f"🟢 Cache: `{stats['backend']}` — {stats['keys']} keys, "
                       f"{stats['hits']} hits / {stats['misses']} misses")
        else:
            st.caption("🔴 Cache unavailable (running without it)")
    else:
        st.caption("⚪ Cache disabled")

    st.divider()
    run_btn = st.button("Run review pipeline", type="primary",
                        icon=":material/play_arrow:", width="stretch")
    st.caption("Runs all agents in order. Progress shows below, stage by stage.")

if "result" not in st.session_state:
    st.session_state.result = None
if "gh" not in st.session_state:
    st.session_state.gh = None

if run_btn:
    st.session_state.gh = None
    st.session_state.pop("pdf_bytes", None)
    if not provider_ready(provider):
        st.error(f"{backend_label} needs an API key — enter it in the sidebar.")
        st.stop()
    if source_mode == "GitHub URL":
        if not github_url:
            st.error("Enter a GitHub repository URL first.")
            st.stop()
        try:
            with st.spinner(f"Cloning {github_url} …"):
                gh = clone_repo(github_url)
            st.session_state.gh = gh
            repo_path = gh.local_path
            n_files = len(discover_source_files(repo_path))
            will_review = min(n_files, max_files) if max_files else n_files
            st.success(f"Cloned **{gh.full_name}** @ `{gh.ref}` — "
                       f"{n_files} source files, reviewing {will_review}.",
                       icon=":material/download_done:")
            if will_review > 20 and provider == "ollama":
                st.warning(f"Reviewing {will_review} files on local Ollama is slow "
                           f"(~1–2 min/file on CPU). Consider the Gemini backend "
                           f"or a lower 'Max files' value.", icon=":material/schedule:")
        except (ValueError, RuntimeError) as e:
            st.error(f"Could not clone repo: {e}")
            st.stop()

    set_enabled(use_redis)
    set_provider(provider)
    if provider == "ollama":
        set_ollama_model(model_choice)
    elif provider == "gemini":
        set_gemini_model(model_choice)
    elif provider == "grok":
        set_grok_model(model_choice)
    state = {
        "repo_path": repo_path,
        "auto_approve": auto_approve,
        "use_rag": use_rag,
        "use_redis": use_redis,
        "model_used": model_choice,
        "max_files": int(max_files),
        "errors": [],
    }
    if batch_size and batch_size > 0:
        all_files = discover_source_files(repo_path)
        if not all_files:
            st.warning("No supported source files found to review.")
            st.session_state.result = None
        else:
            st.session_state.result = _run_pipeline_batched(
                state, all_files, int(batch_size))
    else:
        st.session_state.result = _run_pipeline_streamed(state)

result = st.session_state.result
gh = st.session_state.get("gh")

if result:
    if gh:
        st.markdown(f"**Reviewed repo:** [{gh.full_name}]({gh.url}) "
                    f"&nbsp;·&nbsp; branch `{gh.ref}`")

    # ---- headline KPI cards ----
    with st.container(horizontal=True):
        st.metric("Quality score", f"{result.get('quality_score', 0)}/100", border=True)
        st.metric("Confidence score", result.get("confidence_score", "—"), border=True)
        st.metric("Findings", len(result.get("merged_findings", [])), border=True)
        st.metric("Pending approval", len(result.get("requires_approval", [])), border=True)

    rag_badge = ":green-badge[RAG grounded]" if result.get("use_rag", True) else ":gray-badge[RAG off]"
    st.caption(f"{rag_badge} &nbsp;•&nbsp; Model: `{result.get('model_used', model_choice)}` &nbsp;•&nbsp; "
               f"Redis cache: {'on' if result.get('use_redis', True) else 'off'}")

    cats = result.get("category_breakdown", {})
    with st.container(horizontal=True):
        st.metric("Syntax errors", cats.get("syntax", 0), border=True)
        st.metric("Logical bugs", cats.get("logic", 0), border=True)
        st.metric("Security vulns", cats.get("security", 0), border=True)
        st.metric("Quality issues", cats.get("quality", 0), border=True)

    with st.expander("What am I looking at? (tab guide)", icon=":material/help:"):
        st.markdown(
            "- **Quality score** (0–100): higher = healthier code. "
            "**Confidence**: how sure the pipeline is in its findings.\n"
            "- **Findings** — every bug/vulnerability, colour-coded by severity, "
            "with the code snippet and a clickable `file:line` link to GitHub.\n"
            "- **Charts** — visual breakdowns (severity, issue types, per file).\n"
            "- **Suggested fixes** — AI before/after code fixes for each finding.\n"
            "- **Quality issues** — non-security maintainability notes.\n"
            "- **Human approval** — approve/reject high-risk fixes before they count.\n"
            "- **PDF report** — one downloadable file with everything above.\n"
            "- **Agent trace / Raw JSON / Markdown** — how the run executed and raw data.")

    tabs = st.tabs([
        "Findings", "Charts", "Suggested fixes", "Quality issues",
        "Human approval", "PDF report", "Agent trace", "Raw JSON", "Markdown",
    ])

    # ---- Findings (highlighted + clickable links) ----
    with tabs[0]:
        findings = result.get("merged_findings", [])
        if not findings:
            st.success("No bug or vulnerability findings detected. 🎉")
        for f in findings:
            with st.container(border=True):
                st.markdown(f"{_sev_badge(f['severity'])} &nbsp; {_loc_link(f)} "
                            f"&nbsp; — &nbsp; `{f['rule_id']}`")
                st.markdown(f["message"])
                meta = []
                if f.get("cwe"):
                    meta.append("**CWE:** " + ", ".join(f["cwe"]))
                if f.get("owasp"):
                    meta.append("**OWASP:** " + ", ".join(f["owasp"]))
                if meta:
                    st.caption("  |  ".join(meta))
                snippet = (f.get("code_snippet") or "").strip()
                if snippet and snippet.lower() != "requires login":
                    st.code(snippet,
                            language=result.get("languages", {}).get(f.get("file"), "python"))

    # ---- Charts ----
    with tabs[1]:
        findings = result.get("merged_findings", [])
        quality_issues = result.get("quality_issues", [])
        if not findings and not quality_issues:
            st.info("No findings to chart.")
        else:
            sev = charts.severity_counts(findings)
            cat = charts.category_counts(findings, quality_issues)
            src = charts.source_counts(findings)
            rules = charts.top_rules(findings)
            per_file = charts.per_file_counts(findings)

            top = st.columns([1, 1, 1])
            with top[0].container(border=True):
                st.plotly_chart(charts.plotly_gauge(result["quality_score"]),
                                width="stretch")
            if sev:
                with top[1].container(border=True):
                    st.plotly_chart(
                        charts.plotly_pie(sev, "Findings by severity",
                                          charts.SEVERITY_COLORS), width="stretch")
            if cat:
                with top[2].container(border=True):
                    st.plotly_chart(
                        charts.plotly_pie(cat, "Consolidated issue types",
                                          charts.CATEGORY_COLORS,
                                          charts.CATEGORY_LABELS), width="stretch")

            mid = st.columns(2)
            if src:
                with mid[0].container(border=True):
                    st.plotly_chart(charts.plotly_bar(src, "Findings by detector"),
                                    width="stretch")
            if rules:
                with mid[1].container(border=True):
                    st.plotly_chart(charts.plotly_bar(rules, "Top rules triggered"),
                                    width="stretch")

            treemap = charts.plotly_treemap(findings)
            if treemap is not None:
                with st.container(border=True):
                    st.plotly_chart(treemap, width="stretch")
            if per_file:
                with st.container(border=True):
                    st.plotly_chart(charts.plotly_bar(per_file, "Findings per file"),
                                    width="stretch")

    # ---- Suggested fixes ----
    with tabs[2]:
        for fix in result.get("suggested_fixes", []):
            status = ":green-badge[✓ Verified]" if fix["verified"] else ":orange-badge[⚠ Flagged]"
            with st.expander(f"{fix['file']}:{fix['line']}"):
                st.markdown(f"{status} &nbsp; {_loc_link(fix)}")
                st.write(fix["explanation"])
                col_a, col_b = st.columns(2)
                col_a.code(fix["original_code"], language="python")
                col_b.code(fix["fixed_code"], language="python")
                if fix["verification_notes"]:
                    st.caption(f"Verifier notes: {fix['verification_notes']}")

    # ---- Quality issues ----
    with tabs[3]:
        for q in result.get("quality_issues", []):
            st.markdown(f"{_sev_badge(q['severity'])} {_loc_link(q)} — {q['message']}")

    # ---- Human approval ----
    with tabs[4]:
        pending = result.get("requires_approval", [])
        approved = result.get("approved_fixes", [])
        rejected = result.get("rejected_fixes", [])
        total_fixes = len(pending) + len(approved) + len(rejected)
        n_findings = len(result.get("merged_findings", []))
        manual_mode = not result.get("auto_approve", True)

        st.caption(
            "Every fix is triaged automatically. **Verified, low-severity** fixes "
            "are **auto-approved**. **High-severity (ERROR/CRITICAL) or unverified** "
            "fixes are **held for your decision** below — high-risk changes are "
            "never auto-applied. (Toggle *Require my approval for ALL fixes* in the "
            "sidebar to review every one.)")
        if manual_mode:
            st.info("Manual approval mode is **ON** — every generated fix is routed "
                    "here for your decision.", icon=":material/gavel:")
        s1, s2, s3 = st.columns(3)
        s1.metric("⏳ Awaiting you", len(pending), border=True)
        s2.metric("✅ Auto-approved", sum(1 for a in approved if a.get("auto_approved")), border=True)
        s3.metric("🚫 Rejected", len(rejected), border=True)

        # Distinguish "nothing to approve because no fixes were generated" from
        # "nothing pending because everything was auto-approved".
        if total_fixes == 0:
            if n_findings == 0:
                st.warning("No findings were produced, so there are no fixes to "
                           "approve. Try a different backend/model, raise the file "
                           "limit, or check the **Findings** tab.",
                           icon=":material/info:")
            else:
                st.warning(f"{n_findings} finding(s) were detected but **no fixes were "
                           f"generated** to approve — the fix-generation step returned "
                           f"nothing (often a weak local model or LLM errors; see "
                           f"**Pipeline notices**). Try the Gemini backend for better "
                           f"fixes.", icon=":material/info:")

        # -- Pending: needs a human decision --
        st.subheader(f"⏳ Awaiting your decision ({len(pending)})")
        if not pending and total_fixes > 0:
            st.success("Nothing waiting — all generated fixes were auto-approved "
                       "(none were high-risk or unverified).", icon=":material/task_alt:")
        for i, item in enumerate(pending):
            with st.container(border=True):
                st.markdown(f"{_sev_badge(item['severity'])} &nbsp; {_loc_link(item)}")
                st.warning(f"**Why it needs you:** {item.get('approval_reason', 'Flagged for review.')}",
                           icon=":material/gavel:")
                st.write(item["explanation"])
                col_y, col_n = st.columns(2)
                if col_y.button("Approve", key=f"approve_{i}", icon=":material/check:",
                                type="primary", width="stretch"):
                    result["requires_approval"].remove(item)
                    result["approved_fixes"].append(
                        {**item, "auto_approved": False,
                         "approval_reason": "Approved by human reviewer."})
                    st.session_state.result = report_node(result)
                    st.rerun()
                if col_n.button("Reject", key=f"reject_{i}", icon=":material/close:",
                                width="stretch"):
                    result["requires_approval"].remove(item)
                    result["rejected_fixes"].append(
                        {**item, "approval_reason": "Rejected by human reviewer."})
                    st.session_state.result = report_node(result)
                    st.rerun()

        # -- Approved: auto + human, each with its reason --
        st.subheader(f"✅ Approved ({len(approved)})")
        if not approved:
            st.caption("No fixes approved yet.")
        for j, item in enumerate(approved):
            tag = ":green-badge[🤖 Auto]" if item.get("auto_approved") else ":blue-badge[👤 By you]"
            with st.container(border=True):
                st.markdown(f"{tag} &nbsp; {_sev_badge(item['severity'])} &nbsp; {_loc_link(item)}")
                st.caption(item.get("approval_reason", ""))
                # Let a reviewer override an auto-approval.
                if st.button("Override → reject", key=f"unapprove_{j}",
                             icon=":material/undo:"):
                    result["approved_fixes"].remove(item)
                    result["rejected_fixes"].append(
                        {**item, "approval_reason": "Auto-approval overridden by reviewer."})
                    st.session_state.result = report_node(result)
                    st.rerun()

        # -- Rejected --
        if rejected:
            st.subheader(f"🚫 Rejected ({len(rejected)})")
            for item in rejected:
                st.markdown(f"{_sev_badge(item['severity'])} {_loc_link(item)} — "
                            f"{item.get('approval_reason', '')}")

    # ---- PDF report ----
    with tabs[5]:
        st.subheader("Finalized PDF report")
        st.write("A shareable PDF with KPI infographics, consolidated pie/bar "
                 "charts, and every issue listed with file:line"
                 + (", CWE, and a clickable GitHub link." if gh else " and CWE."))
        report_json = result.get("final_report_json", {})
        repo_slug = gh.repo if gh else "local"
        st.caption("Step 1: click **Generate** to build the PDF. "
                   "Step 2: a **Download** button appears — click it to save the file.")
        if st.button("Generate PDF report", type="primary",
                     icon=":material/picture_as_pdf:"):
            with st.spinner("Rendering PDF (charts + findings)…"):
                st.session_state["pdf_bytes"] = build_pdf(report_json, gh)
            st.success("PDF ready — click Download below.")
        if st.session_state.get("pdf_bytes"):
            st.download_button(
                "Download PDF report", data=st.session_state["pdf_bytes"],
                file_name=f"code_review_report_{repo_slug}.pdf",
                mime="application/pdf", icon=":material/download:")

    # ---- Agent trace ----
    with tabs[6]:
        st.subheader("Per-agent trace (observability)")
        trace = result.get("trace", [])
        summary = result.get("final_report_json", {}).get("trace_summary", {})
        if summary:
            with st.container(horizontal=True):
                st.metric("Nodes", summary.get("nodes", 0), border=True)
                st.metric("Total time (ms)", summary.get("total_ms", 0), border=True)
                st.metric("Errors", summary.get("errors", 0), border=True)
        if trace:
            st.dataframe(
                [{
                    "#": s["seq"], "agent": s["node"],
                    "duration_ms": s["duration_ms"], "status": s["status"],
                    "produced": ", ".join(f"{k}={v}" for k, v in s.get("produced", {}).items()),
                } for s in trace],
                width="stretch", hide_index=True)
        else:
            st.info("No trace recorded.")

    # ---- Raw JSON ----
    with tabs[7]:
        st.json(result.get("final_report_json", {}))
        st.download_button(
            "Download JSON report",
            data=json.dumps(result.get("final_report_json", {}), indent=2),
            file_name="code_review_report.json", icon=":material/download:")

    # ---- Markdown ----
    with tabs[8]:
        st.markdown(result.get("final_report_md", ""))
        st.download_button(
            "Download Markdown report", data=result.get("final_report_md", ""),
            file_name="code_review_report.md", icon=":material/download:")

    if result.get("errors"):
        st.warning("Pipeline notices:\n" + "\n".join(f"- {e}" for e in result["errors"]),
                   icon=":material/warning:")
else:
    st.info("Pick a source (GitHub URL or local path) and click "
            "**Run review pipeline** to begin.", icon=":material/rocket_launch:")
