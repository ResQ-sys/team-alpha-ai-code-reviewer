# Architecture — AI Software Code Reviewer & Secure Development Agent

> Reference architecture for Hackathon Problem Statement **PS #06**
> (*"AI Software Code Reviewer & Secure Development Agent"*).
>
> This document describes the full system as it now exists: the original
> LangGraph review pipeline **plus** the additive production layer
> (structured LLM output, an autonomous secure-development / self-repair
> layer, a FastAPI service with SSE, SQLite persistence, an offline evaluation
> harness, and observability). Everything in the production layer was added
> **without editing** `graph.py`, `agents/state.py`, or any individual agent —
> the original pipeline remains importable and runnable exactly as before.

---

## 1. System at a glance

```
                          ┌─────────────────────────────────────────────┐
                          │            CLIENTS / SURFACES               │
                          │  CLI (main.py) · Streamlit (app.py) ·        │
                          │  FastAPI REST + SSE (server/api.py)          │
                          └───────────────┬─────────────────────────────┘
                                          │
              ┌───────────────────────────┼────────────────────────────────┐
              │                           │                                │
              ▼                           ▼                                ▼
   ┌───────────────────┐      ┌────────────────────────┐      ┌────────────────────┐
   │  REVIEW PIPELINE  │      │  SECURE-DEV LAYER       │      │  PERSISTENCE       │
   │  graph.py         │─────▶│  apply → self-repair →  │      │  persistence/      │
   │  (9 LangGraph      │ fixes│  verify (secure_dev/*) │      │  store.py (SQLite) │
   │   agents)          │      └────────────────────────┘      └────────────────────┘
   └─────────┬─────────┘                 │                                ▲
             │                           │                                │
             ▼                           ▼                                │
   ┌───────────────────┐      ┌────────────────────────┐                  │
   │  RAG KB           │      │  EVALUATION HARNESS     │──────────────────┘
   │  rag/*            │      │  evaluation/harness.py  │   report.json/.md
   └───────────────────┘      └────────────────────────┘

        cross-cutting: utils/llm_client.py (structured output) ·
                       observability/logging_setup.py · config.py
```

---

## 2. The review pipeline (unchanged core)

`graph.py` compiles a LangGraph `StateGraph` over the shared
`ReviewState` TypedDict (`agents/state.py`). Nine agents run in a fixed linear
order, each a pure `state -> state` node:

```
ingestion → static_analysis → llm_review → vulnerability
          → rag → review_generation → verifier → approval → report
```

| # | Node | Responsibility | PS #06 mapping |
|---|------|----------------|----------------|
| 1 | `ingestion` | Discover source files, read contents, detect language | Repo Ingestion |
| 2 | `static_analysis` | Semgrep security rulesets + lightweight code graph | Static & Semantic Analysis; GNN swap-in |
| 3 | `llm_review` | Per-file semantic review via Code-LLM | Code Understanding (Code-LLM) |
| 4 | `vulnerability` | Merge + dedupe Semgrep and LLM findings; split security vs quality | Vulnerability Detection |
| 5 | `rag` | Retrieve grounded secure-coding guidance per finding | Retrieval-Augmented Generation |
| 6 | `review_generation` | Explainable comment + concrete minimal fix per finding | Explainable AI |
| 7 | `verifier` | Independently check each fix (syntax, non-triviality, regression re-scan) | Self-Reflection / Verifier |
| 8 | `approval` | Route CRITICAL/ERROR or flagged fixes to a human | Human-in-the-Loop |
| 9 | `report` | Assemble every Final Output artifact as Markdown + JSON | Final Output |

Entry points into the pipeline:

- `build_graph()` → compiled graph.
- `run_pipeline(repo_path, auto_approve=True) -> ReviewState` — single-call
  driver used by the CLI, the API background worker, and the eval harness.

The canonical report is `ReviewState["final_report_json"]`, whose
`suggested_fixes` items carry
`{file, line, severity, rule_id, explanation, original_code, fixed_code,
verified, verification_notes, cwe?, finding_ref}`. The production layer treats
that report as its input contract — it never reaches into pipeline internals.

---

## 3. Production layer (additive)

Everything below is new, lives in new packages, and is backward-compatible.
Each new package carries an `__init__.py`; every subprocess/network call is
timeout-bounded; errors are surfaced, never silently swallowed.

### 3.1 Structured LLM output — `utils/llm_client.py`

New method (existing `complete` / `complete_json` untouched):

```python
LLMClient.complete_schema(system, prompt, schema, max_tokens=1500, retries=2) -> dict
```

- **Provider-aware JSON enforcement.** For `ollama`, native `format: json` is
  forced on the request body. For `anthropic` / `openai`, the schema is
  embedded in the system prompt ("respond with ONLY a single valid JSON
  object…") since no native structured-output flag is assumed.
- **Validation + corrective retry.** The parsed dict is checked against
  `schema["required"]`. On a parse failure or a missing key it retries up to
  `retries` more times, appending a corrective instruction that names the exact
  problem. On final failure it returns the sentinel
  `{"_parse_error": True, "_raw": <last raw text>}` — callers branch on
  `_parse_error` rather than catching exceptions.

This is the substrate the self-repair loop and the (optional) LLM detector in
the eval harness use to get reliable machine-readable output from any backend.

### 3.2 Secure-development layer — `secure_dev/`

The layer that turns *reviewed* fixes into *applied, self-verified* fixes. Four
modules, layered bottom-up:

```
patch.py  ─┐
git_ops.py ┼─▶ fix_applier.py ─▶ repair_loop.py
           ┘
```

#### `secure_dev/patch.py` — indentation-tolerant patching

LLM-suggested `original_code` rarely reproduces the file byte-for-byte
(indentation, whitespace drift). This module is the matching engine:

- `AppliedPatch(file, line, diff, applied, error=None)` — dataclass result.
- `make_unified_diff(rel_path, original, fixed) -> str` — git-style
  `a/…` `b/…` unified diff (empty string when identical).
- `locate_and_replace(file_text, original_code, fixed_code) -> (new_text, replaced?)`
  — exact substring match first, then a whitespace-normalized,
  indentation-tolerant block match that re-indents the replacement onto the
  displaced block's base indent. Only the first occurrence is replaced.
- `apply_fix_to_file(repo, rel_file, original_code, fixed_code) -> AppliedPatch`
  — read → replace → write-back, only writing when a real change occurred;
  a missed match or a no-op leaves the file untouched and reports the reason.

#### `secure_dev/git_ops.py` — timeout-guarded git + snapshot/restore

Every git call routes through one `_run_git` helper with a 30s timeout that
never raises on a non-zero exit (a synthetic failed result is returned so all
call sites handle failure uniformly). Every function degrades gracefully on a
non-git directory so the pipeline works on plain folders too.

- `ensure_git_repo(path) -> bool`
- `ensure_branch(repo, name) -> None` — create+checkout if missing (no-op off git).
- `working_tree_clean(repo) -> bool` — a non-git dir is treated as clean.
- `commit_all(repo, message) -> str | None` — stages all, injects a fallback
  committer identity **only** when the repo has none, returns the commit sha.
- `snapshot_backup(repo, files) -> dict` — `{rel_file: original_text | None}`;
  `None` marks a file that did not exist (so restore can delete it).
- `restore_backup(repo, backup) -> None` — undoes a batch; a `None` value
  deletes the created file. Individual failures are collected and re-raised so
  one bad path can't leave a partial rollback.

#### `secure_dev/fix_applier.py` — safe batch application

Turns a list of `suggested_fixes` into real edits — conservatively.

```python
ApplyResult(applied, skipped, combined_diff, commit, dry_run)
apply_fixes(repo_path, suggested_fixes, *, dry_run=True,
            only_verified=True, only_approved=False, approved_keys=None) -> ApplyResult
```

- **Dry-run by default.** Every candidate is *matched against the live file*
  and a real unified diff is computed, but nothing is written.
- **Filters.** `only_verified` (default) drops verifier-flagged fixes;
  `only_approved` drops fixes whose key (`finding_ref`, i.e.
  `file:line:rule_id`) is not in `approved_keys`. Malformed fixes (no
  `fixed_code`, no `original_code`, or a no-op) become **skips with a reason**,
  never silent drops.
- **Reversible real apply.** All target files are snapshotted up front; on any
  exception the snapshot is restored so the tree is never left half-patched.
- **Atomic-ish commit.** When the target is a git work tree and ≥1 fix landed,
  the batch is committed with a conventional-commit message
  `fix(security): <rule_id> in <file>:<line>`.

`applied` / `skipped` are plain JSON-serializable dicts so they persist and
travel over the API cleanly.

#### `secure_dev/repair_loop.py` — self-healing per finding

Verifies (and, if needed, improves) a candidate fix in a **throwaway copy** of
the repo before the real tree is ever touched.

```python
RepairResult(finding, fixed_code, attempts, resolved, regressed, test_ok, notes)
repair_finding(repo_path, finding, fix, *, max_attempts=3,
               run_tests=True, llm=None) -> RepairResult
```

Per attempt (up to `max_attempts`, config default `MAX_REPAIR_ATTEMPTS=3`):

1. `shutil.copytree` the repo into a temp dir (ignoring `.git`, caches,
   `.venv`, `node_modules`) — the **original is never modified**.
2. Apply the candidate `fixed_code` to the copied file (via `patch`).
3. **Regression check** — re-run `run_semgrep` scoped to the changed file and
   confirm the finding's `rule_id` no longer fires. (Timeout 120s.)
4. **Test check (optional)** — if the tree has pytest-style tests, run
   `pytest -q` (timeout 120s). Missing tests / exit-code-5 → inconclusive
   (`None`), which never blocks a repair on its own.
5. If it regressed or tests failed, ask the injected `llm.complete_schema`
   (schema `{"required": ["fixed_code"]}`) for an improved snippet and retry.

`run_semgrep` and `LLMClient` are referenced by name so tests can monkeypatch
them — no real scan, LLM call, or network access is needed to exercise it.

### 3.3 Persistence — `persistence/store.py`

Thread-safe SQLite job store; the single source of truth for the API.

```python
Store(db_path=":memory:")
  create_job(repo_path) -> job_id          # uuid, status='pending'
  set_status(job_id, status, error=None)
  save_report(job_id, report)
  get_job(job_id) -> dict | None
  list_jobs(limit=50) -> list[dict]
  record_approval(job_id, decisions)
  record_applied(job_id, applied)
```

- One shared `sqlite3.Connection` opened `check_same_thread=False`, guarded by
  an `RLock`, WAL journal mode — safe across the API's background threads.
  `:memory:` is fully supported (shared connection keeps it alive) for tests.
- One `jobs` table; `report` / `decisions` / `applied` are JSON text columns so
  the schema is stable regardless of report shape. Timestamps are ISO-8601 UTC.
- Lifecycle statuses: `pending → running → completed` (then `approved` /
  `applied`), or `error`.

### 3.4 Service layer — `server/api.py`

FastAPI app wrapping the pipeline behind REST + SSE, with CORS pre-allowed for
Vite (5173), CRA (3000) and Streamlit (8501) dev origins.

| Method & path | Purpose |
|---|---|
| `GET  /api/health` | Liveness → `{"status":"ok"}` |
| `POST /api/analyze` `{repo_path}` | Create a job, run `run_pipeline` on a background daemon thread, persist the report → `{job_id}` |
| `GET  /api/jobs/{job_id}` | Full job record (404 if missing) |
| `GET  /api/jobs` | `{jobs:[…]}` recent-first |
| `GET  /api/jobs/{job_id}/events` | **SSE** stream of status transitions until `completed`/`error` (0.1s poll, 120s cap) |
| `POST /api/approvals` `{job_id, decisions}` | Persist decisions, mark `approved` → `{ok:true}` |
| `POST /api/apply` `{job_id, dry_run=true}` | Run `fix_applier.apply_fixes` over the job's `suggested_fixes`; persist `applied`; return `ApplyResult` as a dict |

- `create_app(store=None, *, job_runner=None)` factory (tests inject an
  in-memory `Store` and a deterministic runner); module-level
  `app = create_app()` for `uvicorn server.api:app`.
- `graph.run_pipeline` and `secure_dev.fix_applier` are referenced **by
  module** (never imported by value / lazily imported) so tests monkeypatch
  them without the real pipeline, LLM, or network ever running.
- The background worker captures any exception and records it as an `error`
  status — a failed job never crashes the server.

### 3.5 Evaluation harness — `evaluation/`

Offline, deterministic CWE-detection scoring.

```python
run_eval(fixtures_dir=None, *, use_pipeline=True, llm=None, out_dir=None) -> dict
python -m evaluation.harness [--fixtures DIR] [--out DIR] [--pipeline]
```

- Each fixture is `evaluation/fixtures/<name>/` with a vulnerable source file
  and `expected.json` (`{"expected_cwes": ["CWE-89", …]}`).
- Detection = normalized CWE set from **semgrep** (always) ∪ full pipeline
  (opt-in `--pipeline`) ∪ an injected `llm` (opt-in, mockable). Scores
  precision / recall / F1 per fixture and micro-averaged, plus a `fix_success`
  rate (verified fixes ÷ total suggested) when the pipeline runs.
- **Offline-first:** with only semgrep installed it runs fully offline; if
  semgrep is missing it degrades to empty detections (recall 0) rather than
  crashing. Writes `evaluation/report.json` and `evaluation/report.md`.

**Shipped fixtures** (six vulnerability classes):

| Fixture | CWE(s) | Class |
|---|---|---|
| `sqli` | CWE-89 | SQL injection |
| `command_injection` | CWE-78 | OS command injection |
| `weak_hash` | CWE-327 | Weak crypto hash (MD5) |
| `insecure_deserialization` | CWE-502 | Insecure `pickle.loads` |
| `eval_exec` | CWE-95 | Code injection via `eval` |
| `hardcoded_secret` | CWE-798, CWE-259 | Hard-coded credential |

### 3.6 Observability — `observability/logging_setup.py`

- `configure_logging(level="INFO")` — idempotent, thread-safe; prefers a
  `rich` handler when available, else a structured plain formatter. Only ever
  touches handlers it owns (never clobbers other libraries').
- `get_logger(name)` — auto-configures on first use.
- `timed(label, logger=None)` — context manager logging block duration
  (`"<label> took <n> ms"`, or at ERROR with the exception type on failure;
  never suppresses the exception).

### 3.7 Configuration — `config.py` (additive)

All original constants are preserved. New env-overridable settings, parsed with
tolerant `_env_int` / `_env_bool` helpers that never crash on import:

| Setting | Default | Meaning |
|---|---|---|
| `MAX_REPAIR_ATTEMPTS` | 3 | Self-repair candidate iterations per finding |
| `DB_PATH` | `<pkg>/reviews.db` | SQLite job store path |
| `APPLY_DRY_RUN_DEFAULT` | `True` | Default apply mode |
| `APPLY_ONLY_VERIFIED` | `True` | Only apply verifier-approved fixes |
| `LOG_LEVEL` | `INFO` | Root log level |

`validate_settings() -> list[str]` returns human-readable warnings (e.g.
`LLM_PROVIDER=anthropic` without `ANTHROPIC_API_KEY`, `MAX_REPAIR_ATTEMPTS < 1`,
non-standard `LOG_LEVEL`) — surfaced at startup, never raised.

---

## 4. The extended flow: review → apply → self-repair → verify

```
   run_pipeline(repo)                     suggested_fixes[]
        │                                       │
        ▼                                       ▼
  final_report_json  ──────────────▶  fix_applier.apply_fixes(dry_run=True)
        │  (persisted via Store)               │  preview: real diffs, no writes
        │                                       ▼
        │                               human approval (/api/approvals)
        │                                       │
        │                                       ▼
        │                          repair_loop.repair_finding()  ← per finding
        │                          ┌───────────────────────────────┐
        │                          │ temp copy of repo             │
        │                          │  apply → semgrep re-scan      │
        │                          │  → pytest → (LLM improve)×N   │
        │                          └───────────────────────────────┘
        │                                       │ resolved fix
        ▼                                       ▼
   report.md / .json          fix_applier.apply_fixes(dry_run=False)
                                     → snapshot → write → git commit
                                     → Store.record_applied()
```

Two safety nets guarantee the original tree is only ever changed deliberately:
the **repair loop works on a temp copy**, and the **applier snapshots and
restores** on any failure.

---

## 5. Surfaces & how to run

### 5.1 CLI — `main.py`

```bash
python main.py --repo ./sample_repo
python main.py --repo ./sample_repo --interactive        # prompts for approval
python main.py --repo ./sample_repo --md-out r.md --json-out r.json
```

Current flags: `--repo` (required), `--interactive`, `--md-out`, `--json-out`.
Writes the Markdown + JSON report and prints quality/confidence scores.

> **Apply / self-repair from the CLI** is not yet wired as `main.py` flags; it
> is exercised today through the API (`POST /api/apply`), the
> `secure_dev.fix_applier` / `secure_dev.repair_loop` module APIs, and the
> `python -m evaluation.harness` entry point. A thin CLI wrapper
> (`--apply` / `--repair` / `--dry-run` / `--db`) over
> `apply_fixes` + `repair_finding` + `Store` is the intended next increment and
> maps 1:1 onto those existing functions.

### 5.2 API service

```bash
uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload
```

```bash
curl -s localhost:8000/api/health
JOB=$(curl -s -XPOST localhost:8000/api/analyze \
      -H 'content-type: application/json' \
      -d '{"repo_path":"./sample_repo"}' | jq -r .job_id)
curl -s -N localhost:8000/api/jobs/$JOB/events        # SSE status stream
curl -s localhost:8000/api/jobs/$JOB | jq .report.quality_score
curl -s -XPOST localhost:8000/api/apply \
      -H 'content-type: application/json' \
      -d "{\"job_id\":\"$JOB\",\"dry_run\":true}" | jq .combined_diff
```

### 5.3 Dashboard

```bash
streamlit run app.py
```

### 5.4 Evaluation

```bash
python -m evaluation.harness            # offline, semgrep-only
python -m evaluation.harness --pipeline # also run the full pipeline (needs LLM)
# → evaluation/report.json + evaluation/report.md
```

### 5.5 Tests

```bash
pytest -q                               # all mocked; no LLM / network / repo mutation
```

Coverage spans every new module: `test_llm_structured`, `test_patch`,
`test_git_ops`, `test_fix_applier`, `test_repair_loop`, `test_store`,
`test_api`, `test_eval_harness`, alongside the original `test_pipeline`.

---

## 6. Deployment recipes (Docker / CI)

> Example, entrypoint-accurate recipes matching the surfaces above. Adjust to
> your registry / secrets before shipping.

### 6.1 `Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV LLM_PROVIDER=ollama LOG_LEVEL=INFO DB_PATH=/data/reviews.db
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "server.api:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 6.2 `docker-compose.yml`

```yaml
services:
  api:
    build: .
    ports: ["8000:8000"]
    environment:
      LLM_PROVIDER: ollama
      OLLAMA_ENDPOINT: http://ollama:11434/api/generate
      DB_PATH: /data/reviews.db
    volumes: ["review-data:/data"]
    depends_on: [ollama]
  dashboard:
    build: .
    command: streamlit run app.py --server.port 8501 --server.address 0.0.0.0
    ports: ["8501:8501"]
  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: ["ollama:/root/.ollama"]
volumes:
  review-data:
  ollama:
```

### 6.3 CI (`.github/workflows/ci.yml`)

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: ruff check .
      - run: pytest -q --cov=. --cov-report=term-missing
      - run: python -m evaluation.harness      # offline CWE eval gate
```

---

## 7. PS #06 traceability (production additions)

| PS #06 requirement | Production-layer realization |
|---|---|
| Multi-Agent AI Workflow | Original 9-agent LangGraph graph, now driven by CLI, API background worker, and eval harness |
| Retrieval-Augmented Generation | Pipeline `rag` node unchanged; its grounded guidance flows into the fixes the secure-dev layer applies |
| Self-Reflection / Verifier | Extended beyond the in-pipeline `verifier` by `repair_loop` — sandbox re-scan + test verification with LLM-driven improvement |
| Human-in-the-Loop | `POST /api/approvals` + `only_approved`/`approved_keys` gating in `fix_applier`; nothing high-risk auto-applies |
| **Automated fixes** (secure development) | `secure_dev/` — `patch` → `fix_applier` (safe, reversible, git-committed) → `repair_loop` (self-healing) |
| Explainable AI | `suggested_fixes` carry explanations + verifier notes + CWE; apply/skip records carry per-fix reasons and real unified diffs |
| Structured Code-LLM output | `LLMClient.complete_schema` — provider-aware JSON with validation + corrective retry |
| DeepEval / evaluation | `evaluation/harness.py` — offline CWE precision/recall/F1 + fix-success over six vulnerability-class fixtures |
| FastAPI / Streamlit surfaces | `server/api.py` (REST + SSE), existing `app.py` dashboard |
| Persistence / auditability | `persistence/store.py` — jobs, reports, approvals, applied-fix records in SQLite |
| Observability | `observability/logging_setup.py` — structured logging + `timed` spans |
```
