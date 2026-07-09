# AI Software Code Reviewer & Secure Development Agent

A working reference implementation for Hackathon Problem Statement **PS #06**
(*"AI Software Code Reviewer & Secure Development Agent"*, SPOC – Munish/Naman),
built as a LangGraph multi-agent pipeline that reviews a code repository for
bugs, security vulnerabilities, and code-quality issues; grounds its fixes in
retrieved secure-coding guidance (Code RAG); verifies its own suggestions
before they can be applied; and routes high-risk changes through a
human-in-the-loop approval step.

## 1. Mapping to the problem statement

> **Project layout:** all Python source lives under **`src/`** (`src/app.py`,
> `src/main.py`, `src/graph.py`, `src/config.py`, and the `agents/`, `utils/`,
> `rag/`, `rules/`, `tests/`, `finetune/` packages). Paths in the table below are
> relative to `src/`. Build/config files (`Dockerfile`, `docker-compose.yml`,
> `requirements.txt`), `docs/`, and `sample_repo/` stay at the repo root.

| Problem statement item | Where it lives here |
|---|---|
| Multi-Agent AI Workflow | `graph.py` (LangGraph `StateGraph`), 9 nodes in `agents/` |
| Code Large Language Models (Code Llama, DeepSeek-Coder, StarCoder, Qwen2.5-Coder) | `utils/llm_client.py` — **runs Qwen2.5-Coder locally via Ollama by default**; also pluggable to `anthropic` / `openai` / `local_hf` |
| Retrieval-Augmented Generation (Code RAG) | `rag/knowledge_base.py` + `agents/rag_agent.py`, over `rag/secure_coding_docs.json` **plus distilled dataset samples** (`rag/dataset_loader.py`) |
| Static & Semantic Code Analysis | `utils/semgrep_runner.py` (Semgrep, OWASP/CWE rulesets, with an offline fallback ruleset in `rules/local_security_rules.yaml`) |
| Graph Neural Networks (Code Graph Representation) | **Stand-in, not an actual GNN:** `utils/code_parser.py` builds an AST/regex function/class/call graph fed to the LLM as structural context. A real GNN is a documented swap-in, not implemented. |
| Vulnerability Detection Models | Semgrep + LLM semantic findings merged/deduped in `agents/vulnerability_agent.py` (no separately-trained detector) |
| Explainable AI | `agents/review_generation_agent.py` — every finding gets a plain-language explanation + RAG citations |
| Self-Reflection & Verifier Agents | `agents/verifier_agent.py` — syntax check, no-op check, semgrep regression re-scan |
| Human-in-the-Loop Approval | `agents/approval_agent.py`, CLI prompt in `main.py --interactive`, interactive tab in `app.py` |
| GitHub API / GitLab API / Docker | **Not implemented** — extension points noted in §5 (not required for local repo review) |
| PyTorch / TensorFlow / Transformers | Used by the optional LoRA track (`finetune/`) and inside a local Code-LLM |
| Tree-sitter / Semgrep / CodeQL | Semgrep wired in; Tree-sitter/CodeQL are **documented swap-ins**, not implemented |
| DeepEval / Phoenix / MLflow | `utils/tracing.py` exports per-agent spans via **OpenTelemetry** (OTLP → Phoenix/Jaeger) or **LangSmith** (`TRACING_BACKEND`). DeepEval/Ragas can consume `final_report_json`. |
| Streamlit / FastAPI / Next.js | `app.py` (Streamlit dashboard). FastAPI/Next.js not implemented. |

**Final Output artifacts** (all in `agents/report_agent.py`, written by `main.py`):
Code Review Report, Bug Detection Summary, Security Vulnerability Report, Code
Quality Score, Suggested Code Fixes, Secure Coding Recommendations,
Explainability Report, Confidence Score, Human Approval Report.

### Scope & honest limitations

This is a working reference implementation, not a production system. To be clear
about what is real vs. a stand-in:

- **No trained GNN / vulnerability model.** Structural context is an AST/regex
  code graph; detection is Semgrep + LLM. The named datasets (Devign, Big-Vul, …)
  are **not** used to train a model at runtime.
- **Dataset-backed RAG uses distilled samples.** The corpus folds in CWE-labeled
  samples derived from those datasets (`rag/datasets/`), not the full multi-GB
  datasets. An optional HuggingFace loader (`rag/dataset_loader.load_hf_dataset_docs`)
  can pull the real datasets but is off by default.
- **Tracing is span-level.** OpenTelemetry/LangSmith export is real but records
  per-agent spans (timing/status/output), not deep token-level LLM tracing.
- **LoRA fine-tuning is an optional offline track** (`finetune/`), decoupled from
  the app; a full run needs a GPU.
- **No GitHub/GitLab/Docker/FastAPI integration** — local-repo review only.

## 2. Pipeline architecture

```
ingestion -> static_analysis -> llm_review -> vulnerability -> rag
         -> review_generation -> verifier -> approval -> report
```

1. **Ingestion** — discovers source files, reads contents, detects language.
2. **Static & Semantic Analysis** — runs Semgrep security rulesets; builds a
   lightweight code graph (functions/classes/calls/flagged dangerous sinks).
3. **Code Understanding (Code-LLM)** — per-file semantic review for logic
   bugs, missing auth checks, and quality issues static rules can't see.
4. **Vulnerability Detection** — merges Semgrep + LLM findings, deduplicates
   near-identical hits on the same file/line, splits security vs. quality.
5. **Secure-Coding RAG** — retrieves grounded guidance per finding from an
   OWASP/CWE-style knowledge base (FAISS + sentence-transformers, with an
   automatic TF-IDF fallback if embedding-model download isn't available).
6. **Review Generation** — produces an explainable comment + concrete
   minimal fix per finding, grounded in the retrieved guidance.
7. **Verifier / Self-Reflection** — independently checks each fix: syntax
   validity, non-triviality (not a no-op), and (when the finding came from
   Semgrep) a regression re-scan confirming the rule no longer fires.
8. **Human-in-the-Loop Approval** — CRITICAL/ERROR-severity or
   verifier-flagged fixes are always routed to a human; nothing high-risk
   is ever auto-applied.
9. **Report** — assembles every Final Output artifact as Markdown + JSON.

## 3. Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # install all Python dependencies
cp .env.example .env
```

The default backend is **Ollama** (`LLM_PROVIDER=ollama`), so no API key is
required. Make sure Ollama is running and the model is pulled:

```bash
ollama serve                     # start the local server (if not already running)
ollama pull qwen2.5-coder:1.5b   # default model (override via OLLAMA_MODEL in .env)
```

The default model is **Qwen2.5-Coder** — a code-specialised LLM named in the
problem statement — which outperforms a general model at code review. To use a
hosted model instead, set `LLM_PROVIDER=anthropic` (and `ANTHROPIC_API_KEY`) or
`LLM_PROVIDER=openai` (and `OPENAI_API_KEY`) in `.env`.

**Redis cache (optional, zero-setup):** LLM completions are cached so repeat runs
are instant. If no standalone Redis is reachable at `REDIS_URL`, the pipeline
automatically spins up an embedded `redislite` server; if that also fails it
runs without caching. Nothing to install or configure. Toggle with `REDIS_ENABLED`
or the Streamlit sidebar.

Semgrep needs internet access to pull its hosted rulesets
(`p/security-audit`, `p/owasp-top-ten`, `p/cwe-top-25`, `p/secrets`). If the
registry is unreachable (offline judging box, firewall), the pipeline
automatically falls back to the bundled offline ruleset at
`rules/local_security_rules.yaml`, which already covers the vulnerability
classes present in `sample_repo/` (SQL injection, hard-coded secrets, weak
hashing, insecure deserialization, command injection, `eval`, bare excepts).

## 4. Running it

**CLI:**
```bash
python src/main.py --repo ./sample_repo
python src/main.py --repo ./sample_repo --interactive   # prompts for human approval
python src/main.py --repo ./sample_repo --no-rag        # ungrounded (RAG off) baseline
python src/main.py --repo ./sample_repo --no-redis      # disable the LLM cache
```
Writes `code_review_report.md` and `code_review_report.json`.

**Dashboard:**
```bash
streamlit run src/app.py
```
Enter a repo path, run the pipeline, inspect findings/fixes across tabs, and
approve/reject pending high-risk fixes interactively. The sidebar has toggles
for **secure-coding RAG** and the **Redis cache** (with a live status badge), so
you can compare grounded vs. ungrounded generation from the UI.

**Docker (runtime model input via env vars):**
```bash
docker build -t ai-code-reviewer .

# Or with Compose
docker compose up --build

# Example: hosted provider
docker run --rm -p 8501:8501 \
  -e LLM_PROVIDER=openai \
  -e OPENAI_API_KEY=your-key \
  -e OPENAI_MODEL=gpt-4o-mini \
  ai-code-reviewer

# Example: Ollama running outside the container
docker run --rm -p 8501:8501 \
  --add-host=host.docker.internal:host-gateway \
  -e LLM_PROVIDER=ollama \
  -e OLLAMA_ENDPOINT=http://host.docker.internal:11434/api/generate \
  -e OLLAMA_MODEL=qwen2.5-coder:1.5b \
  ai-code-reviewer

# Example: review a local repo mounted into the container
docker run --rm -p 8501:8501 \
  -v "$PWD:/workspace" \
  -e LLM_PROVIDER=openai \
  -e OPENAI_API_KEY=your-key \
  ai-code-reviewer
```
The image does not bundle a model. Pick the backend at runtime with
`LLM_PROVIDER` and the corresponding model/env vars (`OLLAMA_MODEL`,
`OPENAI_MODEL`, `ANTHROPIC_MODEL`, `GEMINI_MODEL`, or `LOCAL_HF_MODEL_NAME`).
To review a local repository from inside Docker, mount it and use the mounted
path in the UI, for example `-v "$PWD:/workspace"` then review `/workspace`.
The container serves the Streamlit app on `http://localhost:8501`.
The included Compose file mounts this repo at `/workspace`, reads env vars from
`.env`, and is the quickest local setup.

**Tests (no API key required — LLM calls are mocked):**
```bash
pytest src/tests                          # runs the whole suite (root conftest.py adds src/ to the path)
# or run a module directly from inside src/:
cd src && python -m tests.test_pipeline   # end-to-end integration test
cd src && python -m unittest tests.test_unit
```

**Findings are surfaced in distinct categories** — *syntax errors* (promoted from
the parser), *logical bugs*, *security vulnerabilities*, and *quality issues* —
each shown separately in the report and dashboard (`category_breakdown`).

**Observability:** every agent run is traced (name / duration / status / output)
into `state["trace"]`, surfaced in the report JSON+Markdown and an "Agent Trace"
tab in Streamlit. Set `TRACING_BACKEND=otel` (OTLP → Phoenix/Jaeger via
`OTEL_EXPORTER_OTLP_ENDPOINT`), `langsmith`, or `console` to export spans to a
real external tracer; default is in-process only.

**Dataset-backed RAG:** the retrieval corpus folds in CWE-labeled samples
distilled from the named datasets (Devign, Big-Vul, Juliet, DiverseVul, …) via
`rag/dataset_loader.py`. To back it with the **real** datasets, run
`python rag/fetch_datasets.py` — it streams vulnerable-labeled functions from
HuggingFace (default: Devign/CodeXGLUE) into `rag/datasets/hf_cache.jsonl`, which
RAG then includes automatically. Toggle the whole feature with `USE_DATASET_RAG`.

**Optional (offline track): LoRA fine-tuning & rating** — a *separate, offline*
pipeline to train a Code-LLM towards secure-coding recommendations and rate it
against the base model. It is fully decoupled from the app/pipeline (nothing at
runtime imports it) and has extra dependencies kept **out** of the core install.
A GPU is strongly recommended. See `finetune/README.md`.
```bash
pip install -r requirements-finetune.txt             # extra deps (peft, datasets, accelerate)
python finetune/prepare_data.py                      # build the dataset
python finetune/train_lora.py --max-steps 5          # LoRA fine-tune (GPU recommended)
python finetune/evaluate.py --adapter finetune/adapter   # rate base vs fine-tuned
```

## 5. Datasets (for training/fine-tuning a dedicated vulnerability model)

The problem statement's dataset list — for use if you fine-tune a
classifier/GNN instead of (or alongside) prompting a Code-LLM:

| Dataset | Purpose | Link |
|---|---|---|
| Devign | Vulnerability detection in C/C++ functions | https://github.com/epicosy/devign |
| Big-Vul (MSR 2020) | Large vulnerability dataset with CVEs | https://github.com/ZeoVan/MSR_20_Code_vulnerability_CSV_Dataset |
| CodeXGLUE | Code intelligence benchmark | https://github.com/microsoft/CodeXGLUE |
| CodeSearchNet | Code search / semantic understanding | https://github.com/github/CodeSearchNet |
| Juliet Test Suite (NIST) | CWE security weakness test cases | https://samate.nist.gov/SARD/ (NIST SARD — the canonical distribution; Juliet has no official GitHub repo, only community mirrors) |
| DiverseVul | Large-scale vulnerability detection | https://github.com/wagner-group/diversevul |
| OWASP Benchmark | Security-testing-tool benchmark | https://owasp.org/www-project-benchmark/ |
| ManySStuBs4J | Real Java bug-fix dataset | https://github.com/mast-group/mineSStuBs |
| SAP Project-KB | Java bug-fix benchmark | https://github.com/SAP/project-kb |

These datasets are used two ways here: (1) **RAG retrieval** — distilled
CWE-labeled samples are bundled, and `rag/fetch_datasets.py` streams real rows
(e.g. Devign/CodeXGLUE) into the corpus on demand; (2) as **reference benchmarks**
for anyone extending this into a trained GNN/transformer detector or the optional
LoRA track (`finetune/`). The runtime pipeline does **not** train a model on them.

## 6. Extension points

- **Real Code-LLM instead of a hosted API**: point `LLM_PROVIDER=local_hf` at
  a vLLM/TGI/Ollama server running Code Llama, DeepSeek-Coder, StarCoder, or
  Qwen2.5-Coder; `utils/llm_client.py._local_hf` already defines the request
  contract.
- **GitHub/GitLab integration**: add an ingestion variant that clones a repo
  URL via the GitHub/GitLab API instead of reading a local path, and (once
  approved) opens a PR with the suggested fixes via the same API.
- **CodeQL / Tree-sitter**: swap or augment `utils/semgrep_runner.py` /
  `utils/code_parser.py` — the rest of the pipeline is agnostic to which
  static-analysis engine produced a `FileFinding`.
- **DeepEval / Ragas / Phoenix / MLflow**: `final_report_json` is structured
  enough to feed directly into an eval harness for tracking review quality,
  hallucination rate, and fix-acceptance rate over time.

## 7. Project layout

```
code_reviewer_agent/
├── agents/                # one file per LangGraph node
│   ├── state.py           # shared ReviewState schema
│   ├── ingestion_agent.py
│   ├── static_analysis_agent.py
│   ├── llm_review_agent.py
│   ├── vulnerability_agent.py
│   ├── rag_agent.py
│   ├── review_generation_agent.py
│   ├── verifier_agent.py
│   ├── approval_agent.py
│   └── report_agent.py
├── rag/
│   ├── knowledge_base.py
│   └── secure_coding_docs.json
├── rules/
│   └── local_security_rules.yaml   # offline Semgrep fallback ruleset
├── utils/
│   ├── llm_client.py
│   ├── code_parser.py
│   └── semgrep_runner.py
├── sample_repo/            # intentionally vulnerable demo code
├── tests/test_pipeline.py  # end-to-end test with mocked LLM
├── graph.py                # LangGraph wiring
├── main.py                 # CLI entrypoint
├── app.py                  # Streamlit dashboard
├── config.py
└── requirements.txt
```
