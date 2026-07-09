# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# BuildKit cache mount: pip's downloaded/built wheels persist across builds, so
# a rebuild (or a small requirements change) reuses them instead of downloading
# the whole scientific stack again. --prefer-binary avoids source builds when a
# wheel exists. NOTE: do not set PIP_NO_CACHE_DIR — it would defeat this cache.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
    && pip install --prefer-binary -r requirements.txt

COPY . .

EXPOSE 8501

# App source now lives under src/. `streamlit run src/app.py` puts src/ on
# sys.path, so its `from config import ...` / `from agents...` imports resolve.
CMD ["streamlit", "run", "src/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
