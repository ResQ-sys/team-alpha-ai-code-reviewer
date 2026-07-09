"""
Pluggable Code-LLM client.

The hackathon problem statement names Code Llama, DeepSeek-Coder, StarCoder
and Qwen2.5-Coder as reference models. In a production deployment any of
these would be served locally (vLLM / TGI / Ollama) behind the
`local_hf` branch below. For the demo, `anthropic` or `openai` are used
since they need no GPU.

Swap providers with the LLM_PROVIDER env var: anthropic | openai | local_hf
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import requests

from config import (
    ANTHROPIC_MODEL,
    GEMINI_MODEL,
    GROK_MODEL,
    LLM_PROVIDER,
    LOCAL_HF_ENDPOINT,
    LOCAL_HF_MODEL_NAME,
    OLLAMA_MODEL,
    OLLAMA_ENDPOINT,
    OPENAI_MODEL,
    XAI_BASE_URL,
)
from utils.redis_cache import cache_get, cache_set

_MODEL_BY_PROVIDER = {
    "ollama": OLLAMA_MODEL,
    "anthropic": ANTHROPIC_MODEL,
    "openai": OPENAI_MODEL,
    "gemini": GEMINI_MODEL,
    "grok": GROK_MODEL,
    "local_hf": LOCAL_HF_MODEL_NAME,
}

# Which env var holds each hosted provider's API key.
API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",   # google-genai also honors GEMINI_API_KEY
    "grok": "XAI_API_KEY",
}

# Runtime override for the Ollama model, settable from the UI (same spirit as
# redis_cache.set_enabled). Lets a reviewer swap e.g. qwen2.5-coder:1.5b for a
# lighter/faster llama3.2:1b without editing config or restarting. None => use
# the configured OLLAMA_MODEL default.
_ollama_model_override: Optional[str] = None

# Runtime override for the Gemini model. Gemini model ids get retired often
# (e.g. gemini-2.5-flash may return 404 "no longer available"), so the UI lets a
# reviewer pick a currently-valid model listed by their own API key.
_gemini_model_override: Optional[str] = None

# Runtime override for the whole provider (ollama | anthropic | openai | gemini
# | local_hf), so the UI can switch from local Ollama to a fast hosted API
# without an env var / restart. None => use the configured LLM_PROVIDER default.
_provider_override: Optional[str] = None


def set_ollama_model(name: Optional[str]) -> None:
    global _ollama_model_override
    _ollama_model_override = name or None


def active_ollama_model() -> str:
    return _ollama_model_override or OLLAMA_MODEL


def set_gemini_model(name: Optional[str]) -> None:
    global _gemini_model_override
    _gemini_model_override = name or None


def active_gemini_model() -> str:
    return _gemini_model_override or GEMINI_MODEL


# Runtime override for the Grok (xAI) model, same rationale as Gemini.
_grok_model_override: Optional[str] = None


def set_grok_model(name: Optional[str]) -> None:
    global _grok_model_override
    _grok_model_override = name or None


def active_grok_model() -> str:
    return _grok_model_override or GROK_MODEL


def set_provider(provider: Optional[str]) -> None:
    global _provider_override
    _provider_override = provider or None


def active_provider() -> str:
    return _provider_override or LLM_PROVIDER


def provider_ready(provider: str) -> bool:
    """True if the provider can actually be used right now (local, or key set)."""
    if provider in ("ollama", "local_hf"):
        return True
    env = API_KEY_ENV.get(provider)
    return bool(env and os.environ.get(env))


def list_ollama_models() -> list[str]:
    """Names of models installed on the local Ollama server (empty on failure).

    Excludes embedding-only models (e.g. nomic-embed-text) which can't do the
    chat/generate completions the review agents need.
    """
    try:
        tags_url = OLLAMA_ENDPOINT.replace("/api/generate", "/api/tags")
        resp = requests.get(tags_url, timeout=3)
        resp.raise_for_status()
        names = [m["name"] for m in resp.json().get("models", [])]
        return [n for n in names if "embed" not in n.lower()]
    except Exception:
        return []


def ollama_reachable() -> bool:
    """True if the configured Ollama server answers (used to warn early when a
    Docker container can't reach a host Ollama bound only to 127.0.0.1)."""
    try:
        tags_url = OLLAMA_ENDPOINT.replace("/api/generate", "/api/tags")
        requests.get(tags_url, timeout=3).raise_for_status()
        return True
    except Exception:
        return False


def list_gemini_models() -> list[str]:
    """Gemini models available to the current API key that support text
    generation (empty on failure). Lets the UI offer a valid model instead of a
    hard-coded id that Google may have retired."""
    try:
        from google import genai

        key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not key:
            return []
        client = genai.Client(api_key=key)
        out = []
        for m in client.models.list():
            name = (getattr(m, "name", "") or "").replace("models/", "")
            if not name or "embedding" in name.lower() or "embed" in name.lower():
                continue
            actions = (getattr(m, "supported_actions", None)
                       or getattr(m, "supported_generation_methods", None) or [])
            if not actions or "generateContent" in actions:
                out.append(name)
        # Prefer flash (fast/cheap) models first for a sensible default.
        out.sort(key=lambda n: (0 if "flash" in n else 1, n))
        return out
    except Exception:
        return []


def list_grok_models() -> list[str]:
    """Grok (xAI) models available to the current key (empty on failure). xAI is
    OpenAI-compatible, so we hit the standard /v1/models endpoint."""
    try:
        from openai import OpenAI

        key = os.environ.get("XAI_API_KEY")
        if not key:
            return []
        client = OpenAI(api_key=key, base_url=XAI_BASE_URL)
        names = [m.id for m in client.models.list().data]
        # Text chat/reasoning models only — skip image/video/audio/embedding
        # variants (e.g. grok-imagine-video) that can't do code review.
        skip = ("image", "imagine", "video", "audio", "tts", "embed", "vision")
        names = [n for n in names if not any(t in n.lower() for t in skip)]
        names.sort()
        return names
    except Exception:
        return []


class LLMClient:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or active_provider()

    @property
    def model_id(self) -> str:
        if self.provider == "ollama":
            return active_ollama_model()
        if self.provider == "gemini":
            return active_gemini_model()
        if self.provider == "grok":
            return active_grok_model()
        return _MODEL_BY_PROVIDER.get(self.provider, self.provider)

    # ------------------------------------------------------------------
    def complete(self, system: str, prompt: str, max_tokens: int = 1500,
                 use_cache: bool = True, json_mode: bool = False) -> str:
        """Return raw text completion from whichever backend is configured.

        Transparently served from the Redis cache when an identical
        (provider, model, system, prompt, max_tokens, json_mode) request was
        seen before. `json_mode` asks backends that support it (Ollama, OpenAI)
        to constrain output to valid JSON.
        """
        cache_key = {
            "provider": self.provider,
            "model": self.model_id,
            "system": system,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "json_mode": json_mode,
        }
        if use_cache:
            cached = cache_get("llm", cache_key)
            if cached is not None:
                return cached

        result = self._dispatch_with_retry(system, prompt, max_tokens, json_mode)

        if use_cache:
            cache_set("llm", cache_key, result)
        return result

    def _dispatch_with_retry(self, system: str, prompt: str, max_tokens: int,
                             json_mode: bool, max_retries: int = 4) -> str:
        """Dispatch with backoff on rate-limit / transient errors (e.g. Gemini
        free-tier 429 RESOURCE_EXHAUSTED, 503 overloaded). Non-transient errors
        like a 404 'model no longer available' are NOT retried — they can't
        recover, so we fail fast and surface them."""
        delay = 5.0
        for attempt in range(max_retries + 1):
            try:
                return self._dispatch(system, prompt, max_tokens, json_mode)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                transient = ("429" in msg or "RESOURCE_EXHAUSTED" in msg
                             or "rate limit" in msg.lower() or "503" in msg
                             or "overloaded" in msg.lower())
                if not transient or attempt == max_retries:
                    raise
                m = re.search(r"retry.?[Dd]elay['\":\s]+(\d+)", msg)
                wait = float(m.group(1)) if m else delay
                time.sleep(min(wait, 60))
                delay = min(delay * 2, 60)

    def _dispatch(self, system: str, prompt: str, max_tokens: int,
                  json_mode: bool = False) -> str:
        if self.provider == "ollama":
            return self._ollama(system, prompt, max_tokens, json_mode)
        if self.provider == "anthropic":
            return self._anthropic(system, prompt, max_tokens)
        if self.provider == "openai":
            return self._openai(system, prompt, max_tokens, json_mode)
        if self.provider == "gemini":
            return self._gemini(system, prompt, max_tokens, json_mode)
        if self.provider == "grok":
            return self._grok(system, prompt, max_tokens, json_mode)
        if self.provider == "local_hf":
            return self._local_hf(system, prompt, max_tokens)
        raise ValueError(f"Unknown LLM_PROVIDER: {self.provider}")

    def complete_json(self, system: str, prompt: str, max_tokens: int = 1500) -> dict:
        """Ask the model for JSON-only output and parse it defensively.

        Uses the backend's native constrained-JSON mode where available, which
        makes small local models (e.g. Qwen2.5-Coder) reliably return parseable
        JSON instead of prose.
        """
        raw = self.complete(
            system + "\nRespond with ONLY valid JSON. No markdown fences, no preamble.",
            prompt,
            max_tokens,
            json_mode=True,
        )
        return self._safe_json(raw)

    # ------------------------------------------------------------------
    @staticmethod
    def _safe_json(raw: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json\n", "", 1)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1:
                try:
                    return json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    pass
            return {"_parse_error": True, "_raw": raw}

    # ------------------------------------------------------------------
    def _anthropic(self, system: str, prompt: str, max_tokens: int) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")

    def _openai(self, system: str, prompt: str, max_tokens: int,
                json_mode: bool = False) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def _grok(self, system: str, prompt: str, max_tokens: int,
              json_mode: bool = False) -> str:
        """Grok (xAI) via its OpenAI-compatible endpoint — same shape as _openai
        but pointed at api.x.ai with the XAI_API_KEY."""
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("XAI_API_KEY"), base_url=XAI_BASE_URL)
        kwargs = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(
            model=active_grok_model(),
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def _gemini(self, system: str, prompt: str, max_tokens: int,
                json_mode: bool = False) -> str:
        """Google Gemini via the google-genai SDK.

        Uses `system_instruction` for the system prompt and, in json_mode,
        constrains output with `response_mime_type=application/json` — the
        Gemini equivalent of Ollama's `format: json`.
        """
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        resp = client.models.generate_content(
            model=active_gemini_model(), contents=prompt, config=cfg)
        return resp.text or ""

    def _ollama(self, system: str, prompt: str, max_tokens: int,
                json_mode: bool = False) -> str:
        """
        Calls a locally running Ollama server via its native generate API.
            POST /api/generate {"model", "system", "prompt", "stream": false,
                                "format": "json"?, "options": {"num_predict": ...}}
            -> {"response": "..."}

        `format: json` constrains the model to emit valid JSON — essential for
        reliable structured output from small local models.
        """
        payload = {
            "model": active_ollama_model(),
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"
        resp = requests.post(OLLAMA_ENDPOINT, json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json().get("response", "")

    def _local_hf(self, system: str, prompt: str, max_tokens: int) -> str:
        """
        Calls a locally hosted Code-LLM (e.g. DeepSeek-Coder / Code Llama via
        vLLM's OpenAI-compatible server, or a simple Flask/FastAPI wrapper).
        Expected endpoint contract:
            POST {"model": ..., "system": ..., "prompt": ..., "max_tokens": ...}
            -> {"text": "..."}
        """
        payload = {
            "model": LOCAL_HF_MODEL_NAME,
            "system": system,
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        resp = requests.post(LOCAL_HF_ENDPOINT, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("text", "")
