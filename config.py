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
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")   # ollama | anthropic | openai | local_hf
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LOCAL_HF_ENDPOINT = os.getenv("LOCAL_HF_ENDPOINT", "http://localhost:8001/generate")
LOCAL_HF_MODEL_NAME = os.getenv("LOCAL_HF_MODEL_NAME", "deepseek-ai/deepseek-coder-6.7b-instruct")

# Ollama backend (local Code-LLM served via `ollama serve`).
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")

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

# Quality score weights (used in report_agent.py)
SCORE_WEIGHTS = {
    "CRITICAL": 25,
    "ERROR": 12,
    "WARNING": 4,
    "INFO": 1,
}

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

# ---------------------------------------------------------------------------
# Production settings (additive — new subsystems: repair loop, persistence,
# fix application, observability). Kept here so every module reads one source
# of truth. All are env-overridable and never crash on import.
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    """Parse an int env var, falling back to `default` on missing/invalid."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean env var (1/true/yes/on), falling back to `default`."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# Self-repair loop: max candidate-fix iterations per finding.
MAX_REPAIR_ATTEMPTS = _env_int("MAX_REPAIR_ATTEMPTS", 3)

# Persistence: SQLite database path for jobs/reports/approvals.
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "reviews.db"))

# Fix application defaults.
APPLY_DRY_RUN_DEFAULT = _env_bool("APPLY_DRY_RUN_DEFAULT", True)
APPLY_ONLY_VERIFIED = _env_bool("APPLY_ONLY_VERIFIED", True)

# Observability.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ---------------------------------------------------------------------------
# Agent mode (ReAct loop over the active workspace).
# ---------------------------------------------------------------------------
# Hard cap on reasoning steps for a single agent run, bounding both LLM cost and
# the blast radius of a misbehaving loop.
MAX_AGENT_STEPS = _env_int("MAX_AGENT_STEPS", 12)

# Byte cap applied when the agent reads a file, so a single read cannot pull an
# unbounded blob into the model context / memory.
AGENT_MAX_FILE_BYTES = _env_int("AGENT_MAX_FILE_BYTES", 100_000)

# ---------------------------------------------------------------------------
# API / workspace security.
#
# ALLOWED_REPO_ROOT confines every repo_path accepted by the HTTP API to a
# single workspace directory: an incoming path must resolve (realpath, so
# symlinks and '..' are collapsed) to that root or a descendant of it. Empty /
# unset means "no allowlist" — the API still requires the path to be a real,
# existing directory, but does not confine it to a subtree. Setting this is
# STRONGLY recommended for any networked deployment: without it, a caller can
# point the pipeline at (and, on a non-dry-run apply, mutate) any directory the
# server process can reach.
ALLOWED_REPO_ROOT = os.getenv("ALLOWED_REPO_ROOT", "").strip()

# Optional shared-secret gate for state-changing endpoints (analyze/apply/
# approvals). When set, requests must carry a matching ``X-API-Key`` header.
# Empty / unset disables the check (single-user / localhost default).
API_KEY = os.getenv("API_KEY", "").strip()


def validate_settings() -> list[str]:
    """Return human-readable configuration warnings (never raises).

    Checks for common misconfigurations — e.g. a hosted LLM provider selected
    without the corresponding API key present. Callers can surface these at
    startup without crashing the process.
    """
    warnings: list[str] = []

    provider = (LLM_PROVIDER or "").strip().lower()
    if provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        warnings.append(
            "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set; "
            "LLM calls will fail."
        )
    elif provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        warnings.append(
            "LLM_PROVIDER=openai but OPENAI_API_KEY is not set; "
            "LLM calls will fail."
        )
    elif provider not in {"ollama", "anthropic", "openai", "local_hf"}:
        warnings.append(
            f"LLM_PROVIDER='{LLM_PROVIDER}' is not one of "
            "ollama|anthropic|openai|local_hf."
        )

    if MAX_REPAIR_ATTEMPTS < 1:
        warnings.append(
            f"MAX_REPAIR_ATTEMPTS={MAX_REPAIR_ATTEMPTS} is < 1; "
            "the repair loop will not run."
        )

    if TOP_K_RETRIEVAL < 1:
        warnings.append(f"TOP_K_RETRIEVAL={TOP_K_RETRIEVAL} is < 1; RAG disabled.")

    valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
    if (LOG_LEVEL or "").strip().upper() not in valid_levels:
        warnings.append(
            f"LOG_LEVEL='{LOG_LEVEL}' is not a standard logging level; "
            "defaulting to INFO at runtime."
        )

    if not ALLOWED_REPO_ROOT:
        warnings.append(
            "ALLOWED_REPO_ROOT is not set; the API will accept any existing "
            "directory as a repo_path. Set it to confine reviews (and fix "
            "application) to one workspace before exposing the API."
        )
    elif not os.path.isdir(ALLOWED_REPO_ROOT):
        warnings.append(
            f"ALLOWED_REPO_ROOT='{ALLOWED_REPO_ROOT}' is not a directory; "
            "all repo_path submissions will be rejected."
        )

    if not API_KEY:
        warnings.append(
            "API_KEY is not set; state-changing API endpoints are unauthenticated. "
            "Set API_KEY before exposing the API beyond localhost."
        )

    return warnings
