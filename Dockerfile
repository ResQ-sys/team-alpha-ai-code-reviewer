# syntax=docker/dockerfile:1

##############################################################################
# team-alpha-ai-code-reviewer — production image
#
# Serves the FastAPI backend via uvicorn (server.api:app).
# The multi-agent pipeline (LangGraph + semgrep + RAG) runs in-process.
# LLM inference is delegated to a separate Ollama service (see docker-compose).
##############################################################################

FROM python:3.12-slim AS base

# --- Runtime environment -----------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_HOME=/app \
    # Point the LLM client at the compose-networked Ollama service by default.
    OLLAMA_HOST=http://ollama:11434 \
    LLM_PROVIDER=ollama \
    DB_PATH=/app/data/reviews.db \
    LOG_LEVEL=INFO

# --- System dependencies -----------------------------------------------------
# git: required by secure_dev/git_ops.py for branch/commit operations.
# curl: used by the container healthcheck.
# build-essential: some wheels (faiss / sentence-transformers deps) may compile.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR ${APP_HOME}

# --- Python dependencies (cached layer) --------------------------------------
# Copy only the dependency manifest first so edits to source don't bust the
# pip cache layer.
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    # Extras present in the dev/runtime env but not pinned in requirements.txt.
    && pip install "pydantic-settings>=2.3.0"

# --- Application source -------------------------------------------------------
COPY . .

# Install the project itself (exposes the `code-reviewer` console script).
RUN pip install --no-deps -e .

# --- Non-root user -----------------------------------------------------------
# Create an unprivileged user and a writable data dir for the SQLite store.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p ${APP_HOME}/data \
    && chown -R appuser:appuser ${APP_HOME}

USER appuser

EXPOSE 8000

# --- Healthcheck -------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# --- Entrypoint --------------------------------------------------------------
ENTRYPOINT ["uvicorn"]
CMD ["server.api:app", "--host", "0.0.0.0", "--port", "8000"]
