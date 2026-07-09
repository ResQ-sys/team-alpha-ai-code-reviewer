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
    def __init__(self, provider: Optional[str] = None, model: Optional[str] = None):
        self.provider = provider or LLM_PROVIDER
        # Per-instance model override. When omitted, falls back to the module
        # default so existing callers are unchanged. This is what lets a
        # UI-selected model reach the Ollama backend (see ``_ollama``).
        self.model = model or OLLAMA_MODEL

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
    def complete_schema(
        self,
        system: str,
        prompt: str,
        schema: dict,
        max_tokens: int = 1500,
        retries: int = 2,
    ) -> dict:
        """Return a JSON object validated against ``schema['required']`` keys.

        Provider-aware JSON enforcement:
          * ``ollama`` -> forces native ``format: json`` on the request body.
          * ``anthropic`` / ``openai`` -> steered with the schema embedded in
            the system prompt (no native structured-output flag assumed).

        On a parse failure or a missing required key the call is retried up to
        ``retries`` times with an appended corrective instruction that names the
        offending problem. On final failure returns
        ``{"_parse_error": True, "_raw": <last raw text>}``.

        Args:
            system: Base system prompt.
            prompt: User prompt.
            schema: JSON-schema-ish dict; only its ``required`` list is enforced.
            max_tokens: Generation cap forwarded to the backend.
            retries: Number of *additional* attempts after the first (>= 0).

        Returns:
            The parsed dict on success, else the ``_parse_error`` sentinel.
        """
        required = list(schema.get("required", []) or [])
        schema_hint = self._schema_hint(schema)
        base_system = system
        if self.provider != "ollama":
            # For non-ollama providers we cannot rely on a native JSON mode,
            # so we steer the model with the schema in the system prompt.
            base_system = (
                f"{system}\n\nYou MUST respond with ONLY a single valid JSON "
                f"object. No markdown fences, no preamble, no trailing text.\n"
                f"{schema_hint}"
            )

        total_attempts = max(1, retries + 1)
        raw = ""
        correction = ""
        last_error = "no attempts executed"

        for attempt in range(total_attempts):
            system_prompt = base_system + correction
            raw = self._complete_enforced(system_prompt, prompt, max_tokens)
            parsed = self._safe_json(raw)

            if parsed.get("_parse_error"):
                last_error = "response was not valid JSON"
            else:
                missing = [key for key in required if key not in parsed]
                if not missing:
                    return parsed
                last_error = "missing required key(s): " + ", ".join(missing)

            # Prepare a corrective instruction for the next attempt (if any).
            correction = (
                "\n\nYour previous response was rejected because "
                f"{last_error}. Respond again with ONLY a valid JSON object "
                f"that includes all required keys.\n{schema_hint}"
            )

        return {"_parse_error": True, "_raw": raw}

    def _complete_enforced(self, system: str, prompt: str, max_tokens: int) -> str:
        """Call the backend, enabling native JSON mode for ollama."""
        if self.provider == "ollama":
            return self._ollama(system, prompt, max_tokens, force_json=True)
        return self.complete(system, prompt, max_tokens)

    @staticmethod
    def _schema_hint(schema: dict) -> str:
        """Render a compact required-key hint for prompt steering."""
        required = list(schema.get("required", []) or [])
        if not required:
            return "Required keys: (none specified)."
        return "Required top-level keys: " + ", ".join(required) + "."

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

    def _ollama(
        self,
        system: str,
        prompt: str,
        max_tokens: int,
        force_json: bool = False,
    ) -> str:
        """
        Calls a locally running Ollama server via its native generate API.
            POST /api/generate {"model", "system", "prompt", "stream": false,
                                "options": {"num_predict": max_tokens}}
            -> {"response": "..."}

        When ``force_json`` is set, ``"format": "json"`` is added so the server
        constrains decoding to syntactically valid JSON.
        """
        payload = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if force_json:
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
