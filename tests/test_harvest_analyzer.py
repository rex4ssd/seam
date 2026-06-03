"""
Phase 3.3 — harvest/analyzer.py tests.

Covers:
  - _parse_json_response: valid JSON, think tags, markdown fences, bad JSON
  - _validate_response: missing dims, out-of-range scores
  - _build_prompt: key sections present
  - heuristic_score: each dimension's signal mapping
  - analyze(): ollama path (mock httpx) and fallback path (mock failure)
  - StrengthReport fields: engine, profile_version, tags, evidence
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from seam.core.config import load_config, DEFAULT_PROFILE, DEFAULT_HARVEST
from seam.core.models import Candidate, RepoSignals, StrengthReport
from seam.core.ollama_utils import parse_json_response as _parse_json_response
from seam.harvest.analyzer import (
    analyze,
    heuristic_score,
    _build_prompt,
    _validate_response,
    _heuristic_dimensions,
    _score_tech,
    _score_style,
    _score_stability,
    _score_validation,
    _score_onboarding,
    _primary_language,
    _clamp,
    _DIM_TAG,
)


# ── fixtures ───────────────────────────────────────────────────────────────────

def _make_cfg(tmp_path: Path, overrides: dict | None = None) -> object:
    """Create a minimal SeamConfig pointing at tmp_path."""
    harvest_cfg = {
        **DEFAULT_HARVEST,
        "target_dir": str(tmp_path),
        "analyze": {
            **DEFAULT_HARVEST["analyze"],
            "tag_threshold": 70,
            "ollama_timeout_sec": 30,
        },
    }
    if overrides:
        harvest_cfg.update(overrides)
    profile_data = {**DEFAULT_PROFILE, "harvest": harvest_cfg}
    p = tmp_path / ".seam" / "profile.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(profile_data, f)
    return load_config(p)


def _make_candidate(slug: str = "owner/repo", stars: int = 1000, lang: str = "Rust") -> Candidate:
    return Candidate(
        source="github",
        id=slug,
        title=slug,
        description="A test repo",
        stars=stars,
        url=f"https://github.com/{slug}",
        pushed_at="2026-01-01T00:00:00Z",
        language=lang,
        topics=["test"],
    )


def _make_signals(
    has_tests: bool = True,
    test_file_count: int = 10,
    ci_files: list[str] | None = None,
    linter_cfgs: list[str] | None = None,
    has_license: bool = True,
    has_changelog: bool = True,
    semver_tags: list[str] | None = None,
    dep_manifests: list[str] | None = None,
    lang_breakdown: dict[str, float] | None = None,
    sloc: int = 5000,
    readme_len: int = 3000,
    readme_snippet: str = "# Test Repo\nA great project.",
    sample_files: list[str] | None = None,
) -> RepoSignals:
    return RepoSignals(
        slug="owner/repo",
        has_tests=has_tests,
        test_file_count=test_file_count,
        ci_files=ci_files if ci_files is not None else [".github/workflows/ci.yml"],
        linter_cfgs=linter_cfgs if linter_cfgs is not None else ["rustfmt.toml", ".editorconfig"],
        has_license=has_license,
        has_changelog=has_changelog,
        semver_tags=semver_tags if semver_tags is not None else ["0.4.0", "0.3.0"],
        dep_manifests=dep_manifests if dep_manifests is not None else ["Cargo.toml"],
        lang_breakdown=lang_breakdown if lang_breakdown is not None else {".rs": 85.0, ".py": 15.0},
        sloc=sloc,
        readme_len=readme_len,
        readme_snippet=readme_snippet,
        sample_files=sample_files if sample_files is not None else [],
    )


def _valid_ollama_json(
    tech: int = 80,
    style: int = 75,
    stab: int = 85,
    val: int = 78,
    on: int = 72,
) -> str:
    return json.dumps({
        "tech_strength": tech,
        "coding_style": style,
        "stability": stab,
        "validation": val,
        "onboarding": on,
        "summary": "An excellent Rust CLI with strong tests and clear docs.",
        "evidence": {
            "tech_strong":      ["src/parser.rs uses custom AST"],
            "style_strong":     ["rustfmt.toml enforces 100-char lines"],
            "product_stable":   ["LICENSE MIT", "CHANGELOG.md maintained", "v0.4.0"],
            "great_validation": ["tests/integration_test.rs", ".github/workflows/ci.yml"],
            "easy_onboarding":  ["README.md 5000 chars with quickstart"],
        },
    })


# ── _parse_json_response ───────────────────────────────────────────────────────

class TestParseJsonResponse:
    def test_plain_json(self):
        raw = _valid_ollama_json()
        data = _parse_json_response(raw)
        assert data["tech_strength"] == 80
        assert data["summary"].startswith("An excellent")

    def test_strips_think_tags(self):
        raw = (
            "<think>Let me analyse this carefully...\n"
            "The Rust code looks solid.</think>\n"
            + _valid_ollama_json()
        )
        data = _parse_json_response(raw)
        assert data["tech_strength"] == 80

    def test_strips_markdown_json_fence(self):
        raw = "```json\n" + _valid_ollama_json() + "\n```"
        data = _parse_json_response(raw)
        assert data["tech_strength"] == 80

    def test_strips_markdown_plain_fence(self):
        raw = "```\n" + _valid_ollama_json() + "\n```"
        data = _parse_json_response(raw)
        assert data["coding_style"] == 75

    def test_leading_prose_then_json(self):
        raw = "Here is my evaluation:\n\n" + _valid_ollama_json()
        data = _parse_json_response(raw)
        assert data["stability"] == 85

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="no JSON object found"):
            _parse_json_response("Sorry, I cannot evaluate this repository.")

    def test_bad_json_raises(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            _parse_json_response("{ this is not valid json }")

    def test_nested_think_stripped(self):
        """Multiple think blocks all stripped."""
        raw = (
            "<think>step 1</think>"
            "<think>step 2</think>"
            + _valid_ollama_json()
        )
        data = _parse_json_response(raw)
        assert data["onboarding"] == 72


# ── _validate_response ─────────────────────────────────────────────────────────

class TestValidateResponse:
    def test_valid_passes(self):
        data = json.loads(_valid_ollama_json())
        _validate_response(data)  # should not raise

    def test_missing_dimension_raises(self):
        data = json.loads(_valid_ollama_json())
        del data["tech_strength"]
        with pytest.raises(ValueError, match="tech_strength"):
            _validate_response(data)

    def test_out_of_range_raises(self):
        data = json.loads(_valid_ollama_json())
        data["coding_style"] = 150
        with pytest.raises(ValueError, match="coding_style"):
            _validate_response(data)

    def test_negative_score_raises(self):
        data = json.loads(_valid_ollama_json())
        data["validation"] = -5
        with pytest.raises(ValueError):
            _validate_response(data)

    def test_missing_summary_raises(self):
        data = json.loads(_valid_ollama_json())
        del data["summary"]
        with pytest.raises(ValueError, match="summary"):
            _validate_response(data)


# ── _build_prompt ──────────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_slug_in_prompt(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals()
        cand = _make_candidate("astral-sh/ruff", stars=5000)
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "astral-sh/ruff" in prompt

    def test_stars_in_prompt(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals()
        cand = _make_candidate(stars=5000)
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "5000" in prompt

    def test_readme_snippet_in_prompt(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(readme_snippet="# Great Project\nInstall with cargo.")
        cand = _make_candidate()
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "Great Project" in prompt

    def test_ci_files_in_prompt(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(ci_files=[".github/workflows/ci.yml"])
        cand = _make_candidate()
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "ci.yml" in prompt

    def test_linter_cfgs_in_prompt(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(linter_cfgs=["rustfmt.toml"])
        cand = _make_candidate()
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "rustfmt.toml" in prompt

    def test_no_readme_placeholder(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(readme_snippet="", readme_len=0)
        cand = _make_candidate()
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "no README" in prompt

    def test_sample_files_included(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        # create a real sample file
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "main.rs").write_text("fn main() { println!(\"hi\"); }\n")
        sig = _make_signals(sample_files=["src/main.rs"])
        cand = _make_candidate()
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "main.rs" in prompt
        assert "println" in prompt

    def test_sample_file_missing_skipped(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(sample_files=["src/nonexistent.rs"])
        cand = _make_candidate()
        # should not raise even if file doesn't exist
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        assert "nonexistent.rs" not in prompt

    def test_profile_text_in_prompt(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals()
        cand = _make_candidate()
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        # DEFAULT_PROFILE has "Solo developer" in profile text
        assert "Solo developer" in prompt

    def test_all_five_dims_mentioned(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals()
        cand = _make_candidate()
        prompt = _build_prompt(sig, tmp_path, cand, cfg)
        for dim in _DIM_TAG:
            assert dim in prompt


# ── heuristic_score ────────────────────────────────────────────────────────────

class TestHeuristicScore:
    def test_returns_strength_report(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals()
        cand = _make_candidate()
        rep = heuristic_score(sig, cand, cfg, tmp_path)
        assert isinstance(rep, StrengthReport)

    def test_engine_is_heuristic(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(), _make_candidate(), cfg, tmp_path)
        assert rep.engine == "heuristic"

    def test_candidate_id_preserved(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(), _make_candidate("foo/bar"), cfg, tmp_path)
        assert rep.candidate_id == "foo/bar"

    def test_stars_preserved(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(), _make_candidate(stars=9999), cfg, tmp_path)
        assert rep.stars == 9999

    def test_sloc_preserved(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(sloc=42000), _make_candidate(), cfg, tmp_path)
        assert rep.sloc == 42000

    def test_all_dimensions_present(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(), _make_candidate(), cfg, tmp_path)
        for dim in _DIM_TAG:
            assert dim in rep.dimensions
            assert 0 <= rep.dimensions[dim] <= 100

    def test_tags_pass_threshold(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals()
        rep = heuristic_score(sig, _make_candidate(), cfg, tmp_path)
        # tags must match dimensions above threshold
        threshold = cfg.harvest.tag_threshold
        for dim, tag in _DIM_TAG.items():
            if rep.dimensions[dim] >= threshold:
                assert tag in rep.strength_tags
            else:
                assert tag not in rep.strength_tags

    def test_well_validated_repo_tag(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(
            has_tests=True, test_file_count=25,
            ci_files=[".github/workflows/ci.yml", ".github/workflows/release.yml"],
        )
        rep = heuristic_score(sig, _make_candidate(), cfg, tmp_path)
        assert rep.dimensions["validation"] >= 70
        assert "great_validation" in rep.strength_tags

    def test_stable_repo_tag(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(
            has_license=True, has_changelog=True,
            semver_tags=["1.0.0", "0.9.0", "0.8.0"],
        )
        rep = heuristic_score(sig, _make_candidate(), cfg, tmp_path)
        assert rep.dimensions["stability"] >= 70
        assert "product_stable" in rep.strength_tags

    def test_minimal_repo_no_tags(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(
            has_tests=False, test_file_count=0,
            ci_files=[], linter_cfgs=[],
            has_license=False, has_changelog=False,
            semver_tags=[], dep_manifests=[],
            sloc=100, readme_len=50, readme_snippet="# Hi",
            sample_files=[],
        )
        rep = heuristic_score(sig, _make_candidate(stars=50), cfg, tmp_path)
        assert rep.strength_tags == []

    def test_primary_language_from_breakdown(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(lang_breakdown={".rs": 90.0, ".py": 10.0})
        rep = heuristic_score(sig, _make_candidate(), cfg, tmp_path)
        assert rep.language == "rs"

    def test_primary_language_fallback_to_candidate(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        sig = _make_signals(lang_breakdown={})
        rep = heuristic_score(sig, _make_candidate(lang="Go"), cfg, tmp_path)
        assert rep.language == "Go"

    def test_evidence_has_all_tags(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(), _make_candidate(), cfg, tmp_path)
        for tag in _DIM_TAG.values():
            assert tag in rep.evidence

    def test_profile_version_set(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(), _make_candidate(), cfg, tmp_path)
        assert rep.profile_version == cfg.profile_hash()
        assert len(rep.profile_version) == 8  # sha1[:8]

    def test_analyzed_at_is_iso(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        rep = heuristic_score(_make_signals(), _make_candidate(), cfg, tmp_path)
        from datetime import datetime
        # should parse without error
        datetime.fromisoformat(rep.analyzed_at.replace("Z", "+00:00"))


# ── individual heuristic dimension functions ───────────────────────────────────

class TestHeuristicDimensions:
    def test_score_tech_zero_stars(self):
        cand = _make_candidate(stars=0)
        assert _score_tech(_make_signals(sloc=100), cand) == 15

    def test_score_tech_high_stars(self):
        cand = _make_candidate(stars=10_000)
        score = _score_tech(_make_signals(), cand)
        assert score >= 70   # 10k stars should be near 80

    def test_score_tech_sloc_bonus(self):
        cand = _make_candidate(stars=5_000)
        s1 = _score_tech(_make_signals(sloc=1_000), cand)
        s2 = _score_tech(_make_signals(sloc=60_000), cand)
        assert s2 >= s1   # large sloc gives bonus

    def test_score_style_no_linters(self):
        sig = _make_signals(linter_cfgs=[])
        assert _score_style(sig) < 20

    def test_score_style_many_linters(self):
        sig = _make_signals(linter_cfgs=["a", "b", "c", "d"])
        assert _score_style(sig) >= 70

    def test_score_stability_full(self):
        sig = _make_signals(
            has_license=True, has_changelog=True,
            semver_tags=["1.0.0", "0.9.0", "0.8.0"],
        )
        assert _score_stability(sig) >= 90

    def test_score_stability_empty(self):
        sig = _make_signals(has_license=False, has_changelog=False, semver_tags=[])
        assert _score_stability(sig) == 0

    def test_score_stability_license_only(self):
        sig = _make_signals(has_license=True, has_changelog=False, semver_tags=[])
        score = _score_stability(sig)
        assert 20 <= score <= 35

    def test_score_validation_full(self):
        sig = _make_signals(
            has_tests=True, test_file_count=30,
            ci_files=["ci.yml", "release.yml"],
        )
        score = _score_validation(sig)
        assert score >= 70

    def test_score_validation_none(self):
        sig = _make_signals(has_tests=False, test_file_count=0, ci_files=[])
        assert _score_validation(sig) == 0

    def test_score_onboarding_long_readme(self):
        sig = _make_signals(readme_len=10_000)
        assert _score_onboarding(sig) == 80

    def test_score_onboarding_no_readme(self):
        sig = _make_signals(readme_len=0)
        assert _score_onboarding(sig) == 15

    def test_score_onboarding_short(self):
        sig = _make_signals(readme_len=500)
        score = _score_onboarding(sig)
        assert 30 <= score <= 40


# ── analyze() with mocked ollama ───────────────────────────────────────────────

class TestAnalyze:
    def _mock_httpx_response(self, json_str: str) -> MagicMock:
        """Build a mock httpx response that returns json_str as ollama response."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": json_str}
        return mock_resp

    def test_ollama_path_engine_field(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        mock_resp = self._mock_httpx_response(_valid_ollama_json())
        with patch("seam.core.ollama_utils.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            rep = analyze(_make_signals(), tmp_path, _make_candidate(), cfg)

        assert rep.engine == "ollama"

    def test_ollama_path_dimensions_parsed(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        ollama_json = _valid_ollama_json(tech=88, style=72, stab=91, val=80, on=75)
        mock_resp = self._mock_httpx_response(ollama_json)
        with patch("seam.core.ollama_utils.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            rep = analyze(_make_signals(), tmp_path, _make_candidate(), cfg)

        assert rep.dimensions["tech_strength"] == 88
        assert rep.dimensions["coding_style"] == 72
        assert rep.dimensions["stability"] == 91

    def test_ollama_path_tags_applied(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        # all dims >= 70 → all tags
        ollama_json = _valid_ollama_json(tech=75, style=71, stab=80, val=72, on=74)
        mock_resp = self._mock_httpx_response(ollama_json)
        with patch("seam.core.ollama_utils.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            rep = analyze(_make_signals(), tmp_path, _make_candidate(), cfg)

        for tag in _DIM_TAG.values():
            assert tag in rep.strength_tags

    def test_ollama_path_below_threshold_no_tag(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        # onboarding=50 < threshold=70 → no easy_onboarding tag
        ollama_json = _valid_ollama_json(tech=80, style=80, stab=80, val=80, on=50)
        mock_resp = self._mock_httpx_response(ollama_json)
        with patch("seam.core.ollama_utils.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            rep = analyze(_make_signals(), tmp_path, _make_candidate(), cfg)

        assert "easy_onboarding" not in rep.strength_tags

    def test_ollama_connection_error_falls_back(self, tmp_path, capsys):
        cfg = _make_cfg(tmp_path)
        with patch("seam.core.ollama_utils.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = Exception("Connection refused")
            mock_client_cls.return_value = mock_client

            rep = analyze(_make_signals(), tmp_path, _make_candidate(), cfg)

        # P-V01: warning printed to stderr
        captured = capsys.readouterr()
        assert "heuristic fallback" in captured.err or "fallback" in captured.err.lower()
        assert rep.engine == "heuristic"

    def test_bad_json_response_falls_back(self, tmp_path, capsys):
        cfg = _make_cfg(tmp_path)
        mock_resp = self._mock_httpx_response("I cannot evaluate this.")
        with patch("seam.core.ollama_utils.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            rep = analyze(_make_signals(), tmp_path, _make_candidate(), cfg)

        assert rep.engine == "heuristic"

    def test_analyze_returns_strength_report(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        with patch("seam.core.ollama_utils.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = Exception("no ollama")
            mock_client_cls.return_value = mock_client

            rep = analyze(_make_signals(), tmp_path, _make_candidate(), cfg)

        assert isinstance(rep, StrengthReport)
        assert rep.candidate_id == "owner/repo"
        assert rep.clone_path == str(tmp_path)


# ── _clamp and _primary_language helpers ───────────────────────────────────────

class TestHelpers:
    def test_clamp_in_range(self):
        assert _clamp(50) == 50

    def test_clamp_above(self):
        assert _clamp(150) == 100

    def test_clamp_below(self):
        assert _clamp(-10) == 0

    def test_primary_language_from_breakdown(self):
        sig = _make_signals(lang_breakdown={".ts": 60.0, ".rs": 40.0})
        cand = _make_candidate(lang="Python")
        assert _primary_language(sig, cand) == "ts"

    def test_primary_language_fallback(self):
        sig = _make_signals(lang_breakdown={})
        cand = _make_candidate(lang="Swift")
        assert _primary_language(sig, cand) == "Swift"

    def test_primary_language_strips_dot(self):
        sig = _make_signals(lang_breakdown={".go": 100.0})
        cand = _make_candidate(lang="Unknown")
        assert _primary_language(sig, cand) == "go"
