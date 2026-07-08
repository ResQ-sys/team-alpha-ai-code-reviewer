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
    OLLAMA_ENDPOINT,
    OLLAMA_MODEL,
    OPENAI_MODEL,
)


class LLMClient:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or LLM_PROVIDER

    # ------------------------------------------------------------------
    def complete(self, system: str, prompt: str, max_tokens: int = 1500) -> str:
        """Return raw text completion from whichever backend is configured."""
        if self.provider == "ollama":
            return self._ollama(system, prompt, max_tokens)
        if self.provider == "anthropic":
            return self._anthropic(system, prompt, max_tokens)
        if self.provider == "openai":
            return self._openai(system, prompt, max_tokens)
        if self.provider == "local_hf":
            return self._local_hf(system, prompt, max_tokens)
        raise ValueError(f"Unknown LLM_PROVIDER: {self.provider}")

    def complete_json(self, system: str, prompt: str, max_tokens: int = 1500) -> dict:
        """Ask the model for JSON-only output and parse it defensively."""
        raw = self.complete(
            system + "\nRespond with ONLY valid JSON. No markdown fences, no preamble.",
            prompt,
            max_tokens,
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

    def _openai(self, system: str, prompt: str, max_tokens: int) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content or ""

    def _ollama(self, system: str, prompt: str, max_tokens: int) -> str:
        """
        Calls a locally running Ollama server via its native generate API.
            POST /api/generate {"model", "system", "prompt", "stream": false,
                                "options": {"num_predict": max_tokens}}
            -> {"response": "..."}
        """
        payload = {
            "model": OLLAMA_MODEL,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
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
