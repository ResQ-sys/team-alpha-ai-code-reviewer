import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# LLM provider selection.
# The problem statement lists Code Llama / DeepSeek-Coder / StarCoder /
# Qwen2.5-Coder as reference Code-LLMs. This project keeps the LLM behind a
# thin interface (utils/llm_client.py) so any of those can be swapped in via
# a local HF/vLLM endpoint. For the hackathon demo we default to a hosted
# API (Anthropic or OpenAI) since that requires no GPU / model download.
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")   # ollama | anthropic | openai | gemini | grok | local_hf
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Grok (xAI) — OpenAI-compatible API. Model ids evolve (grok-4, grok-3, ...),
# so the UI lets a reviewer pick from the models their key can actually use.
GROK_MODEL = os.getenv("GROK_MODEL", "grok-4")
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
# Gemini model ids are retired periodically (a stale id returns 404 "no longer
# available"). The Streamlit sidebar lets a reviewer pick from the models their
# key can actually use; this is only the fallback default.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
LOCAL_HF_ENDPOINT = os.getenv("LOCAL_HF_ENDPOINT", "http://localhost:8001/generate")
LOCAL_HF_MODEL_NAME = os.getenv("LOCAL_HF_MODEL_NAME", "deepseek-ai/deepseek-coder-6.7b-instruct")

# Ollama backend (local Code-LLM served via `ollama serve`).
# Default is Qwen2.5-Coder — a code-specialised model named in the problem
# statement — which materially outperforms a general 1B model at code review.
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b")

# ---------------------------------------------------------------------------
# Secure-Coding RAG toggle.
# RAG grounds fix suggestions in retrieved OWASP/CWE guidance. It can be
# switched off (e.g. from the Streamlit UI) to compare grounded vs. ungrounded
# generation; when off, the review agent falls back to general best practice.
# ---------------------------------------------------------------------------
USE_RAG = os.getenv("USE_RAG", "true").lower() in {"1", "true", "yes", "on"}

# Fold CWE-labeled samples distilled from the named vulnerability datasets
# (Devign / Big-Vul / Juliet / DiverseVul / ...) into the RAG corpus so
# retrieval is genuinely dataset-backed, not only the hand-written KB.
USE_DATASET_RAG = os.getenv("USE_DATASET_RAG", "true").lower() in {"1", "true", "yes", "on"}

# ---------------------------------------------------------------------------
# Redis caching layer.
# Caches LLM completions (and can back other caches) so repeat runs are fast
# and cheap. Falls back to an embedded `redislite` server, then to a no-op,
# if a standalone Redis isn't reachable — the pipeline never hard-depends on it.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Agent tracing / observability backend.
#   none      — in-process spans only (default; recorded in state["trace"])
#   console   — also print each span
#   otel      — export spans via OpenTelemetry (OTLP if OTEL_EXPORTER_OTLP_ENDPOINT
#               is set, e.g. Phoenix/Jaeger; else console span exporter)
#   langsmith — export a run per node to LangSmith (needs LANGSMITH_API_KEY)
# ---------------------------------------------------------------------------
TRACING_BACKEND = os.getenv("TRACING_BACKEND", "none").lower()

REDIS_ENABLED = os.getenv("REDIS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_TTL = int(os.getenv("REDIS_TTL", "604800"))          # 7 days
REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "aicr")
REDIS_LITE_PATH = os.getenv("REDIS_LITE_PATH", "/tmp/aicr_redis.db")

# ---------------------------------------------------------------------------
# LLM concurrency.
# The LLM review + fix-generation nodes make one model call per file / per
# finding. Those calls are independent and I/O-bound (HTTP to Ollama / a hosted
# API), so we fan them out across a small thread pool instead of running them
# one at a time. Tune down to 1 to force sequential behaviour (e.g. a tiny
# local Ollama that serializes requests anyway); tune up for hosted providers.
# ---------------------------------------------------------------------------
MAX_LLM_WORKERS = int(os.getenv("MAX_LLM_WORKERS", "4"))

# Static analysis
SEMGREP_RULESETS = [
    "p/security-audit",
    "p/owasp-top-ten",
    "p/cwe-top-25",
    "p/secrets",
]

# Severity thresholds that force human-in-the-loop approval before a fix
# is considered "applyable".
HUMAN_APPROVAL_SEVERITIES = {"ERROR", "CRITICAL"}

# Quality score weights (used in report_agent.py). Deliberately lenient so the
# score degrades gracefully rather than collapsing to 0 on a vulnerable repo —
# it's a relative quality signal, not a pass/fail gate.
SCORE_WEIGHTS = {
    "CRITICAL": 18,
    "ERROR": 9,
    "WARNING": 3,
    "INFO": 1,
}

# Quality score curve: score = 100 * exp(-(penalty/LOC) / SCORE_DECAY), floored
# at SCORE_FLOOR. Larger SCORE_DECAY = more lenient (a vulnerable repo scores
# higher). Tuned so a deliberately-insecure app lands in a low-but-nonzero band
# instead of a flat 0. Override via env to taste.
SCORE_DECAY = float(os.getenv("SCORE_DECAY", "0.2"))
SCORE_FLOOR = float(os.getenv("SCORE_FLOOR", "5"))

# RAG
KNOWLEDGE_BASE_PATH = os.path.join(os.path.dirname(__file__), "rag", "secure_coding_docs.json")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
TOP_K_RETRIEVAL = 3

SUPPORTED_EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
}
