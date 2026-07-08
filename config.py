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
