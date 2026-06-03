"""
core/ollama_utils.py — shared ollama HTTP helpers.

Used by both score/ollama.py (candidate scoring) and harvest/analyzer.py (strength analysis).
Extracted to avoid duplication; neither module should re-implement these.

Public:
  parse_json_response(text)             → dict  (raises ValueError / JSONDecodeError)
  call_ollama(base_url, model, prompt, timeout) → dict  (raises on any failure)
  ollama_available(base_url)            → bool
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx


def parse_json_response(text: str) -> dict[str, Any]:
    """
    Extract a JSON object from an ollama response string.

    Handles:
    - <think>…</think> reasoning traces (deepseek-r1)
    - ```json … ``` and ``` … ``` markdown fences
    - Leading/trailing prose around the JSON object
    """
    # strip deepseek-r1 thinking blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    # find first complete {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(
            f"no JSON object found in ollama response "
            f"(first 200 chars): {text[:200]!r}"
        )
    return json.loads(m.group())


def call_ollama(
    base_url: str,
    model: str,
    prompt: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """
    POST to ollama /api/generate and return the parsed JSON response.
    Raises httpx.HTTPError on HTTP errors, ValueError on bad JSON.
    """
    url = base_url.rstrip("/") + "/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    raw = resp.json().get("response", "")
    return parse_json_response(raw)


def ollama_available(base_url: str, timeout: int = 5) -> bool:
    """Return True if the ollama server is reachable and responding."""
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(base_url.rstrip("/") + "/api/tags")
            return r.status_code == 200
    except Exception:
        return False
