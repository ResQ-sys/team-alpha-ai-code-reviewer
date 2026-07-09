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
from typing import Optional

import requests

from config import (
    ANTHROPIC_MODEL,
    LLM_PROVIDER,
    LOCAL_HF_ENDPOINT,
    LOCAL_HF_MODEL_NAME,
    OLLAMA_MODEL,
    OLLAMA_ENDPOINT,
    OPENAI_MODEL,
)
from utils.redis_cache import cache_get, cache_set

_MODEL_BY_PROVIDER = {
    "ollama": OLLAMA_MODEL,
    "anthropic": ANTHROPIC_MODEL,
    "openai": OPENAI_MODEL,
    "local_hf": LOCAL_HF_MODEL_NAME,
}


class LLMClient:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or LLM_PROVIDER

    @property
    def model_id(self) -> str:
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

        result = self._dispatch(system, prompt, max_tokens, json_mode)

        if use_cache:
            cache_set("llm", cache_key, result)
        return result

    def _dispatch(self, system: str, prompt: str, max_tokens: int,
                  json_mode: bool = False) -> str:
        if self.provider == "ollama":
            return self._ollama(system, prompt, max_tokens, json_mode)
        if self.provider == "anthropic":
            return self._anthropic(system, prompt, max_tokens)
        if self.provider == "openai":
            return self._openai(system, prompt, max_tokens, json_mode)
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
            "model": OLLAMA_MODEL,
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
