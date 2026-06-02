from __future__ import annotations
import json
import re
import sys

import httpx

from ..core.config import SeamConfig
from ..core.models import Candidate, ScoredCandidate

_TRUNCATE = 1500   # P-S05: truncate README/description before sending to ollama
_TIMEOUT = 120     # P-S05: ollama timeout


_PROMPT_TMPL = """\
You are a relevance scorer for a solo developer. Score the following GitHub repository against the developer profile.

Developer profile:
{profile}

Repository:
  Name: {name}
  Description: {description}
  Stars: {stars}
  Language: {language}
  Topics: {topics}

Score on two dimensions (0–100 each):
- target_relevance: How relevant is this to the developer's current projects and interests?
- solo_feasible: Can a solo developer with this profile realistically learn from or apply this?

Respond ONLY with a JSON object, no markdown, no explanation:
{{"target_relevance": <int>, "solo_feasible": <int>, "reason": "<one sentence>"}}
"""


def _parse_json_response(text: str) -> dict:
    """Extract JSON from ollama response (may contain <think>…</think> prefix)."""
    # strip <think>…</think> blocks (deepseek-r1 reasoning traces)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # find first {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON found in response: {text[:200]!r}")
    return json.loads(m.group())


def score_ollama(
    candidates: list[Candidate],
    cfg: SeamConfig,
    verbose: bool = False,
) -> list[ScoredCandidate]:
    """
    Score candidates with ollama. Falls back to heuristic on any failure
    if cfg.score_fallback == "heuristic".
    """
    from .heuristic import score_heuristic  # local import to avoid circular

    w_rel = cfg.scoring.get("target_relevance_weight", 0.6)
    w_sol = cfg.scoring.get("solo_feasible_weight", 0.4)
    results: list[ScoredCandidate] = []
    fallback_candidates: list[Candidate] = []

    for c in candidates:
        prompt = _PROMPT_TMPL.format(
            profile=cfg.profile_text,
            name=c.title,
            description=c.description[:_TRUNCATE],
            stars=c.stars,
            language=c.language,
            topics=", ".join(c.topics),
        )

        try:
            parsed = _call_ollama(cfg.ollama_base_url, cfg.score_model, prompt)
            rel = int(parsed.get("target_relevance", 0))
            sol = int(parsed.get("solo_feasible", 0))
            final = int(rel * w_rel + sol * w_sol)
            results.append(
                ScoredCandidate(
                    candidate=c,
                    score=final,
                    reason=parsed.get("reason", ""),
                    dimensions={"target_relevance": rel, "solo_feasible": sol},
                )
            )
            if verbose:
                print(f"  [ollama] {c.id}: {final}/100 — {parsed.get('reason','')}", file=sys.stderr)
        except Exception as exc:
            print(f"[seam] ollama failed for {c.id}: {exc}", file=sys.stderr)
            fallback_candidates.append(c)

    if fallback_candidates:
        if cfg.score_fallback == "heuristic":
            print(f"[seam] falling back to heuristic for {len(fallback_candidates)} candidates", file=sys.stderr)
            results.extend(score_heuristic(fallback_candidates, cfg))
        else:
            print(f"[seam] skipping {len(fallback_candidates)} candidates (fallback=skip)", file=sys.stderr)

    results.sort(key=lambda s: s.score, reverse=True)
    return results


def _call_ollama(base_url: str, model: str, prompt: str) -> dict:
    url = base_url.rstrip("/") + "/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    data = resp.json()
    raw = data.get("response", "")
    return _parse_json_response(raw)


def ollama_available(base_url: str) -> bool:
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(base_url.rstrip("/") + "/api/tags")
            return r.status_code == 200
    except Exception:
        return False
