# AI Software Code Reviewer & Secure Development Agent

A working reference implementation for Hackathon Problem Statement **PS #06**
(*"AI Software Code Reviewer & Secure Development Agent"*, SPOC – Munish/Naman),
built as a LangGraph multi-agent pipeline that reviews a code repository for
bugs, security vulnerabilities, and code-quality issues; grounds its fixes in
retrieved secure-coding guidance (Code RAG); verifies its own suggestions
before they can be applied; and routes high-risk changes through a
human-in-the-loop approval step.

## 1. Mapping to the problem statement

| Problem statement item | Where it lives here |
|---|---|
| Multi-Agent AI Workflow | `graph.py` (LangGraph `StateGraph`), 9 nodes in `agents/` |
| Code Large Language Models (Code Llama, DeepSeek-Coder, StarCoder, Qwen2.5-Coder) | `utils/llm_client.py` — pluggable provider (`anthropic` / `openai` / `local_hf`); swap in any locally-served Code-LLM via the `local_hf` branch |
| Retrieval-Augmented Generation (Code RAG) | `rag/knowledge_base.py` + `rag/secure_coding_docs.json`, used by `agents/rag_agent.py` |
| Static & Semantic Code Analysis | `utils/semgrep_runner.py` (Semgrep, OWASP/CWE rulesets, with an offline fallback ruleset in `rules/local_security_rules.yaml`) |
| Graph Neural Networks (Code Graph Representation) | `utils/code_parser.py` — AST/regex-derived function/class/call graph fed to the LLM as structural context (documented swap-in point for a trained GNN) |
| Vulnerability Detection Models | Semgrep + LLM semantic findings merged/deduped in `agents/vulnerability_agent.py` |
| Explainable AI | `agents/review_generation_agent.py` — every finding gets a plain-language explanation + RAG citations |
| Self-Reflection & Verifier Agents | `agents/verifier_agent.py` — syntax check, no-op check, semgrep regression re-scan |
| Human-in-the-Loop Approval | `agents/approval_agent.py`, CLI prompt in `main.py --interactive`, interactive tab in `app.py` |
| GitHub API / GitLab API / Docker | Extension points noted in §5 below (not required for local repo review) |
| PyTorch / TensorFlow / Transformers | Used inside whichever local Code-LLM you plug into `local_hf` |
| Tree-sitter / Semgrep / CodeQL | Semgrep wired in; Tree-sitter/CodeQL are documented swap-ins for `utils/code_parser.py` |
| Streamlit / FastAPI / Next.js | `app.py` (Streamlit dashboard) |
| DeepEval / Phoenix / MLflow | Extension point — pipeline emits structured JSON (`final_report_json`) that DeepEval/Ragas or Phoenix tracing can consume |

**Final Output artifacts** (all in `agents/report_agent.py`, written by `main.py`):
Code Review Report, Bug Detection Summary, Security Vulnerability Report, Code
Quality Score, Suggested Code Fixes, Secure Coding Recommendations,
Explainability Report, Confidence Score, Human Approval Report.

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
ollama serve                 # start the local server (if not already running)
ollama pull llama3.2:1b      # default model (override via OLLAMA_MODEL in .env)
```

To use a hosted model instead, set `LLM_PROVIDER=anthropic` (and
`ANTHROPIC_API_KEY`) or `LLM_PROVIDER=openai` (and `OPENAI_API_KEY`) in `.env`.

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
python main.py --repo ./sample_repo
python main.py --repo ./sample_repo --interactive   # prompts for human approval
```
Writes `code_review_report.md` and `code_review_report.json`.

**Dashboard:**
```bash
streamlit run app.py
```
Enter a repo path, run the pipeline, inspect findings/fixes across tabs, and
approve/reject pending high-risk fixes interactively.

**Tests (no API key required — LLM calls are mocked):**
```bash
python -m tests.test_pipeline
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
| Juliet Test Suite (NIST) | CWE security weakness test cases | https://samate.nist.gov/SRD/testsuite.php |
| DiverseVul | Large-scale vulnerability detection | https://github.com/wagner-group/diversevul |
| OWASP Benchmark | Security-testing-tool benchmark | https://owasp.org/www-project-benchmark/ |
| ManySStuBs4J | Real Java bug-fix dataset | https://github.com/mast-group/mineSStuBs |
| SAP Project-KB | Java bug-fix benchmark | https://github.com/SAP/project-kb |

This demo does not train a model on these datasets (out of scope for a
hackathon timeline); it uses them as reference for the offline ruleset and
as suggested benchmarks in the deck for anyone extending the vulnerability
classifier into a trained GNN/transformer model.

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
