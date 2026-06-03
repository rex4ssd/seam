"""
harvest/analyzer.py — strength analyzer for a cloned repo.

Two paths:
  1. analyze()         → build prompt → call ollama → parse → StrengthReport
  2. heuristic_score() → pure signals → StrengthReport (fallback when ollama unavailable)

Pitfalls implemented:
  P-H05  only send: signals summary + README snippet (≤readme_truncate) +
         ≤sample_files files (each ≤40 lines / 1500 chars) — never full repo
  P-V01  explicit stderr warning when falling back to heuristic (never silent)
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..core.config import SeamConfig
from ..core.models import Candidate, RepoSignals, StrengthReport

# ── constants ──────────────────────────────────────────────────────────────────

_SAMPLE_FILE_MAX_LINES = 40
_SAMPLE_FILE_MAX_CHARS = 1500

# dimension name → tag name
_DIM_TAG: dict[str, str] = {
    "tech_strength":  "tech_strong",
    "coding_style":   "style_strong",
    "stability":      "product_stable",
    "validation":     "great_validation",
    "onboarding":     "easy_onboarding",
}

_PROMPT_TMPL = """\
You are evaluating a GitHub repository for a solo developer's strength analysis.

## Developer profile
{profile_text}

## Repository: {slug}  (★{stars}, primary language: {primary_language})

### Static signals
- Estimated SLOC: {sloc}
- Test files: {test_count} (has_tests: {has_tests})
- CI workflows: {ci_files}
- Linter/formatter configs: {linter_cfgs}
- License: {has_license}  |  Changelog: {has_changelog}
- Versions found: {semver_tags}
- Dep manifests: {dep_manifests}
- Language breakdown: {lang_breakdown}

### README (first {readme_truncate} chars)
{readme_snippet}

### Sample source files
{file_excerpts}
---
Score this repository on 5 dimensions (0–100 each):
- tech_strength:  technical depth, non-trivial algorithms, architecture quality
- coding_style:   formatters, linters, consistency, type safety
- stability:      versioning, changelog, license, production-readiness
- validation:     test coverage, CI/CD, benchmarks, property tests
- onboarding:     README quality, quickstart, examples/, docs/

Tag threshold: {tag_threshold} — only attach a tag when the dimension score ≥ threshold.
Tags: tech_strong (tech_strength), style_strong (coding_style), product_stable (stability), \
great_validation (validation), easy_onboarding (onboarding)

Respond ONLY with JSON (no markdown, no explanation outside the JSON):
{{
  "tech_strength": <int 0-100>,
  "coding_style": <int 0-100>,
  "stability": <int 0-100>,
  "validation": <int 0-100>,
  "onboarding": <int 0-100>,
  "summary": "<2-3 sentences on what makes this repo notable>",
  "evidence": {{
    "tech_strong":        ["<signal or file path>"],
    "style_strong":       ["<signal or file path>"],
    "product_stable":     ["<signal or file path>"],
    "great_validation":   ["<signal or file path>"],
    "easy_onboarding":    ["<signal or file path>"]
  }}
}}
"""


# ── public API ─────────────────────────────────────────────────────────────────

def analyze(
    signals: RepoSignals,
    repo_dir: Path,
    cand: Candidate,
    cfg: SeamConfig,
) -> StrengthReport:
    """
    Build prompt from signals + sample files + README → call ollama → StrengthReport.
    Falls back to heuristic_score() if ollama unavailable or parse fails (P-V01).
    """
    harvest_cfg = cfg.harvest
    profile_version = cfg.profile_hash()

    prompt = _build_prompt(signals, repo_dir, cand, cfg)

    try:
        parsed = _call_ollama(
            base_url=cfg.ollama_base_url,
            model=cfg.score_model,
            prompt=prompt,
            timeout=harvest_cfg.ollama_timeout_sec,
        )
        return _build_report(
            parsed=parsed,
            signals=signals,
            cand=cand,
            repo_dir=repo_dir,
            tag_threshold=harvest_cfg.tag_threshold,
            profile_version=profile_version,
            engine="ollama",
        )
    except Exception as exc:
        print(
            f"[seam/analyze] ⚠ ollama unavailable for {cand.id}: {exc}\n"
            f"  → heuristic fallback (P-V01)",
            file=sys.stderr,
        )
        return heuristic_score(signals, cand, cfg, repo_dir)


def heuristic_score(
    signals: RepoSignals,
    cand: Candidate,
    cfg: SeamConfig,
    repo_dir: Path,
) -> StrengthReport:
    """
    Score purely from static signals — no ollama required.
    Used as fallback (P-V01) and as fast pre-screen.
    """
    harvest_cfg = cfg.harvest
    profile_version = cfg.profile_hash()

    dims = _heuristic_dimensions(signals, cand)
    evidence = _heuristic_evidence(signals)
    tags = [tag for dim, tag in _DIM_TAG.items() if dims[dim] >= harvest_cfg.tag_threshold]

    primary_lang = _primary_language(signals, cand)
    commit = _read_commit(repo_dir)

    summary_parts = []
    if signals.has_tests and signals.ci_files:
        summary_parts.append(f"Validated: {signals.test_file_count} test files + CI.")
    if signals.has_license and signals.has_changelog:
        summary_parts.append("Stable: LICENSE + CHANGELOG present.")
    if signals.readme_len > 3000:
        summary_parts.append(f"Well-documented: README {signals.readme_len:,} chars.")
    if not summary_parts:
        summary_parts.append(f"Heuristic score based on {cand.stars:,} stars and static signals.")

    return StrengthReport(
        candidate_id=cand.id,
        language=primary_lang,
        languages=signals.lang_breakdown,
        clone_path=str(repo_dir),
        commit=commit,
        sloc=signals.sloc,
        stars=cand.stars,
        strength_tags=tags,
        dimensions=dims,
        summary=" ".join(summary_parts),
        evidence=evidence,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        profile_version=profile_version,
        engine="heuristic",
    )


# ── prompt builder ─────────────────────────────────────────────────────────────

def _build_prompt(
    signals: RepoSignals,
    repo_dir: Path,
    cand: Candidate,
    cfg: SeamConfig,
) -> str:
    harvest_cfg = cfg.harvest
    primary_lang = _primary_language(signals, cand)

    # Format signals for readability
    ci_str = ", ".join(signals.ci_files) if signals.ci_files else "none"
    linter_str = ", ".join(signals.linter_cfgs) if signals.linter_cfgs else "none"
    semver_str = ", ".join(signals.semver_tags[:5]) if signals.semver_tags else "none"
    manifest_str = ", ".join(signals.dep_manifests) if signals.dep_manifests else "none"
    lang_str = ", ".join(
        f"{ext}:{pct}%" for ext, pct in list(signals.lang_breakdown.items())[:5]
    ) if signals.lang_breakdown else "unknown"

    file_excerpts = _build_file_excerpts(signals.sample_files, repo_dir)

    return _PROMPT_TMPL.format(
        profile_text=cfg.profile_text,
        slug=cand.id,
        stars=cand.stars,
        primary_language=primary_lang,
        sloc=signals.sloc,
        test_count=signals.test_file_count,
        has_tests=signals.has_tests,
        ci_files=ci_str,
        linter_cfgs=linter_str,
        has_license=signals.has_license,
        has_changelog=signals.has_changelog,
        semver_tags=semver_str,
        dep_manifests=manifest_str,
        lang_breakdown=lang_str,
        readme_truncate=harvest_cfg.readme_truncate,
        readme_snippet=signals.readme_snippet or "(no README)",
        file_excerpts=file_excerpts or "(no source files found)",
        tag_threshold=harvest_cfg.tag_threshold,
    )


def _build_file_excerpts(sample_files: list[str], repo_dir: Path) -> str:
    """
    Read first _SAMPLE_FILE_MAX_LINES lines (≤_SAMPLE_FILE_MAX_CHARS) from each
    sample file. Returns a formatted string of all excerpts. (P-H05)
    """
    parts: list[str] = []
    for rel_path in sample_files:
        full = repo_dir / rel_path
        if not full.exists():
            continue
        try:
            raw = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = raw.splitlines()[:_SAMPLE_FILE_MAX_LINES]
        excerpt = "\n".join(lines)[:_SAMPLE_FILE_MAX_CHARS]
        parts.append(f"#### {rel_path}\n{excerpt}")

    return "\n\n".join(parts)


# ── ollama call ────────────────────────────────────────────────────────────────

def _call_ollama(
    base_url: str,
    model: str,
    prompt: str,
    timeout: int,
) -> dict[str, Any]:
    """POST to ollama /api/generate; raise on failure or bad response."""
    url = base_url.rstrip("/") + "/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    raw = resp.json().get("response", "")
    return _parse_json_response(raw)


def _parse_json_response(text: str) -> dict[str, Any]:
    """
    Extract a JSON object from an ollama response.
    Handles:
    - <think>…</think> reasoning traces (deepseek-r1)
    - ```json … ``` markdown fences
    - Leading/trailing prose
    """
    # strip deepseek-r1 thinking blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    # find first complete {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object found in response (first 200 chars): {text[:200]!r}")
    parsed = json.loads(m.group())
    _validate_response(parsed)
    return parsed


def _validate_response(data: dict[str, Any]) -> None:
    """Raise ValueError if required keys are missing or scores are out of range."""
    for dim in _DIM_TAG:
        if dim not in data:
            raise ValueError(f"missing dimension '{dim}' in ollama response")
        val = data[dim]
        if not isinstance(val, (int, float)) or not (0 <= val <= 100):
            raise ValueError(f"dimension '{dim}' value {val!r} not in 0-100")
    if "summary" not in data:
        raise ValueError("missing 'summary' in ollama response")


# ── report builder ─────────────────────────────────────────────────────────────

def _build_report(
    parsed: dict[str, Any],
    signals: RepoSignals,
    cand: Candidate,
    repo_dir: Path,
    tag_threshold: int,
    profile_version: str,
    engine: str,
) -> StrengthReport:
    dims = {dim: _clamp(int(parsed[dim])) for dim in _DIM_TAG}
    tags = [tag for dim, tag in _DIM_TAG.items() if dims[dim] >= tag_threshold]

    raw_ev = parsed.get("evidence", {})
    evidence = {tag: list(raw_ev.get(tag, [])) for tag in _DIM_TAG.values()}

    primary_lang = _primary_language(signals, cand)
    commit = _read_commit(repo_dir)

    return StrengthReport(
        candidate_id=cand.id,
        language=primary_lang,
        languages=signals.lang_breakdown,
        clone_path=str(repo_dir),
        commit=commit,
        sloc=signals.sloc,
        stars=cand.stars,
        strength_tags=tags,
        dimensions=dims,
        summary=str(parsed.get("summary", "")),
        evidence=evidence,
        analyzed_at=datetime.now(timezone.utc).isoformat(),
        profile_version=profile_version,
        engine=engine,
    )


# ── heuristic scoring ──────────────────────────────────────────────────────────

def _heuristic_dimensions(signals: RepoSignals, cand: Candidate) -> dict[str, int]:
    """
    Map static signals to 5-dim scores.
    Conservative: heuristic caps at ~85 — AI can push higher with code reading.
    """
    return {
        "tech_strength": _score_tech(signals, cand),
        "coding_style":  _score_style(signals),
        "stability":     _score_stability(signals),
        "validation":    _score_validation(signals),
        "onboarding":    _score_onboarding(signals),
    }


def _score_tech(signals: RepoSignals, cand: Candidate) -> int:
    """Stars (log-scale) + SLOC bonus. Cap at 85 — tech depth needs code reading."""
    stars = max(0, cand.stars)
    if stars > 0:
        # log10(100_001) ≈ 5; maps 0→0, 100→46, 1000→60, 10000→80, 100000→100
        base = min(80, int(math.log10(stars + 1) / math.log10(100_001) * 90))
    else:
        base = 15
    if signals.sloc > 50_000:
        base = min(85, base + 5)
    return base


def _score_style(signals: RepoSignals) -> int:
    """Linter/formatter config count → style score."""
    n = len(signals.linter_cfgs)
    if n == 0: return 15
    if n == 1: return 40
    if n == 2: return 58
    if n == 3: return 70
    return min(80, 70 + (n - 3) * 3)


def _score_stability(signals: RepoSignals) -> int:
    """LICENSE + CHANGELOG + semver tags → stability score."""
    score = 0
    if signals.has_license:   score += 30
    if signals.has_changelog: score += 25
    if signals.semver_tags:
        score += 25
        if len(signals.semver_tags) >= 3:
            score += 15
    return min(95, score)


def _score_validation(signals: RepoSignals) -> int:
    """Tests presence + count + CI → validation score."""
    score = 0
    if signals.has_tests:
        score += 30
        if signals.test_file_count > 5:  score += 15
        if signals.test_file_count > 20: score += 10
    if signals.ci_files:
        score += 25
        if len(signals.ci_files) >= 2: score += 5
    return min(85, score)


def _score_onboarding(signals: RepoSignals) -> int:
    """README length → onboarding score."""
    rl = signals.readme_len
    if rl >= 8_000: return 80
    if rl >= 3_000: return 65
    if rl >= 1_000: return 50
    if rl >= 300:   return 35
    return 15


def _heuristic_evidence(signals: RepoSignals) -> dict[str, list[str]]:
    return {
        "tech_strong": [f"sloc:{signals.sloc}"] if signals.sloc else [],
        "style_strong": signals.linter_cfgs[:3],
        "product_stable": (
            (["LICENSE"] if signals.has_license else [])
            + (["CHANGELOG"] if signals.has_changelog else [])
            + signals.semver_tags[:2]
        ),
        "great_validation": (
            ([f"test_files:{signals.test_file_count}"] if signals.has_tests else [])
            + signals.ci_files[:3]
        ),
        "easy_onboarding": (
            [f"README:{signals.readme_len}_chars"] if signals.readme_len else []
        ),
    }


# ── helpers ────────────────────────────────────────────────────────────────────

def _clamp(v: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, v))


def _primary_language(signals: RepoSignals, cand: Candidate) -> str:
    """Derive primary language: signals lang_breakdown → ext with highest %; else cand.language."""
    if signals.lang_breakdown:
        ext = max(signals.lang_breakdown, key=signals.lang_breakdown.__getitem__)
        # strip leading dot: ".rs" → "rs"
        return ext.lstrip(".")
    return cand.language or "unknown"


def _read_commit(repo_dir: Path) -> str:
    """Read HEAD sha from .seam-meta.json; fallback to git rev-parse."""
    meta = repo_dir / ".seam-meta.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text(encoding="utf-8", errors="ignore"))
            commit = data.get("commit", "")
            if commit:
                return commit
        except (json.JSONDecodeError, OSError):
            pass
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""
