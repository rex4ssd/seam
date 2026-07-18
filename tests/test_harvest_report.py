"""
Phase 3.4 — harvest/report.py + veinout.py tests.

Covers:
  report.write_report:
    - STRENGTH.md created with all sections
    - _index.jsonl appended atomically
    - idempotency: same (candidate_id, commit) → no duplicate line
    - different commit → new line

  veinout.to_vein_lines:
    - format: 'owner/repo --tag lang:x --tag strength:y --tag seam-score:n'
    - all strength tags appear as strength:<tag>
    - no tags → no strength: prefix

  veinout.write_vein_watchlist:
    - creates .vein/watchlist.yaml
    - correct YAML structure (seam-<day>: {repos: [...], compare: true})
    - idempotent: same day overwrites repos list
    - different days coexist
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from seam.core.models import StrengthReport
from seam.harvest.report import write_report, _render_markdown, _append_index
from seam.harvest.veinout import (
    WatchlistError,
    to_vein_lines,
    write_vein_watchlist,
    _vein_line,
)


# ── fixtures ───────────────────────────────────────────────────────────────────

def _make_report(
    slug: str = "astral-sh/ruff",
    commit: str = "abc123def456",
    engine: str = "ollama",
    tags: list[str] | None = None,
    dims: dict | None = None,
    stars: int = 5000,
    sloc: int = 8500,
    language: str = "rs",
    analyzed_at: str = "2026-06-03T02:00:00+00:00",
) -> StrengthReport:
    if tags is None:
        tags = ["tech_strong", "style_strong", "product_stable", "great_validation"]
    if dims is None:
        dims = {
            "tech_strength": 82,
            "coding_style": 74,
            "stability": 88,
            "validation": 76,
            "onboarding": 65,
        }
    return StrengthReport(
        candidate_id=slug,
        language=language,
        languages={".rs": 85.0, ".py": 15.0},
        clone_path=f"/Volumes/2T/rust/{slug.replace('/', '__')}",
        commit=commit,
        sloc=sloc,
        stars=stars,
        strength_tags=tags,
        dimensions=dims,
        summary="An excellent Rust linter with rigorous tests and clear docs.",
        evidence={
            "tech_strong":      ["src/linter.rs custom AST"],
            "style_strong":     ["rustfmt.toml", ".editorconfig"],
            "product_stable":   ["LICENSE MIT", "CHANGELOG.md", "v0.4.0"],
            "great_validation": ["tests/integration_test.rs", ".github/workflows/ci.yml"],
            "easy_onboarding":  [],
        },
        analyzed_at=analyzed_at,
        profile_version="abcd1234",
        engine=engine,
    )


# ── report.write_report ────────────────────────────────────────────────────────

class TestWriteReport:
    def test_creates_strength_md(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "_index.jsonl"
        md = write_report(rep, tmp_path, idx)
        assert md.exists()
        assert md.name == "STRENGTH.md"

    def test_returns_strength_md_path(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "_index.jsonl"
        result = write_report(rep, tmp_path, idx)
        assert result == tmp_path / "STRENGTH.md"

    def test_md_contains_slug(self, tmp_path):
        rep = _make_report(slug="simonw/llm")
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "simonw/llm" in content

    def test_md_contains_summary(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "excellent Rust linter" in content

    def test_md_contains_scores_section(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "## Scores" in content
        assert "tech_strength" in content
        assert "82" in content   # score value

    def test_md_contains_tags(self, tmp_path):
        rep = _make_report(tags=["tech_strong", "great_validation"])
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "tech_strong" in content
        assert "great_validation" in content

    def test_md_checkmark_for_passed_tags(self, tmp_path):
        rep = _make_report(tags=["tech_strong"])
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "✓" in content

    def test_md_dash_for_failed_tag(self, tmp_path):
        # onboarding is NOT in tags → should show "—"
        rep = _make_report(tags=["tech_strong"])
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "—" in content

    def test_md_contains_evidence(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "## Evidence" in content
        assert "rustfmt.toml" in content

    def test_md_contains_metadata(self, tmp_path):
        rep = _make_report(stars=5000, sloc=8500, commit="abc123def456")
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "## Metadata" in content
        assert "5,000" in content       # stars formatted with comma
        assert "8,500" in content       # sloc formatted with comma
        assert "abc123def456" in content

    def test_md_engine_field(self, tmp_path):
        rep = _make_report(engine="heuristic")
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "heuristic" in content

    def test_md_no_tags_message(self, tmp_path):
        rep = _make_report(tags=[])
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        content = (tmp_path / "STRENGTH.md").read_text()
        assert "none above threshold" in content

    def test_creates_index_file(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        assert idx.exists()

    def test_index_entry_has_candidate_id(self, tmp_path):
        rep = _make_report(slug="owner/test")
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        rows = [json.loads(l) for l in idx.read_text().splitlines() if l.strip()]
        assert any(r["candidate_id"] == "owner/test" for r in rows)

    def test_index_entry_fields(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        row = json.loads(idx.read_text().strip())
        assert row["commit"] == "abc123def456"
        assert "strength_tags" in row
        assert "dimensions" in row
        assert "analyzed_at" in row

    def test_idempotent_same_commit(self, tmp_path):
        rep = _make_report(slug="owner/repo", commit="aaa111")
        idx = tmp_path / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        write_report(rep, tmp_path, idx)   # second call — same (slug, commit)
        lines = [l for l in idx.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

    def test_different_commit_appends(self, tmp_path):
        rep1 = _make_report(slug="owner/repo", commit="aaa111")
        rep2 = _make_report(slug="owner/repo", commit="bbb222")
        idx = tmp_path / "_index.jsonl"
        write_report(rep1, tmp_path, idx)
        write_report(rep2, tmp_path, idx)
        lines = [l for l in idx.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_multiple_repos_in_index(self, tmp_path):
        idx = tmp_path / "_index.jsonl"
        for slug in ["owner/a", "owner/b", "owner/c"]:
            rep = _make_report(slug=slug, commit=f"commit-{slug[-1]}")
            (tmp_path / slug.replace("/", "__")).mkdir(exist_ok=True)
            write_report(rep, tmp_path, idx)
        lines = [l for l in idx.read_text().splitlines() if l.strip()]
        assert len(lines) == 3

    def test_index_parent_dir_created(self, tmp_path):
        rep = _make_report()
        idx = tmp_path / "sub" / "dir" / "_index.jsonl"
        write_report(rep, tmp_path, idx)
        assert idx.exists()


# ── veinout.to_vein_lines ──────────────────────────────────────────────────────

class TestToVeinLines:
    def test_returns_one_line_per_rep(self):
        reps = [_make_report("a/b"), _make_report("c/d")]
        lines = to_vein_lines(reps)
        assert len(lines) == 2

    def test_line_starts_with_slug(self):
        rep = _make_report("astral-sh/ruff")
        line = _vein_line(rep)
        assert line.startswith("astral-sh/ruff ")

    def test_lang_tag_present(self):
        rep = _make_report(language="rs")
        line = _vein_line(rep)
        assert "--tag lang:rs" in line

    def test_strength_tags_present(self):
        rep = _make_report(tags=["tech_strong", "great_validation"])
        line = _vein_line(rep)
        assert "--tag strength:tech_strong" in line
        assert "--tag strength:great_validation" in line

    def test_no_strength_tags(self):
        rep = _make_report(tags=[])
        line = _vein_line(rep)
        assert "strength:" not in line

    def test_seam_score_tag(self):
        rep = _make_report(dims={
            "tech_strength": 80, "coding_style": 70,
            "stability": 90, "validation": 80, "onboarding": 70,
        })
        line = _vein_line(rep)
        assert "--tag seam-score:" in line
        # average of (80+70+90+80+70)/5 = 78
        assert "--tag seam-score:78" in line

    def test_seam_pick_tag(self):
        rep = _make_report(analyzed_at="2026-06-03T02:00:00+00:00")
        line = _vein_line(rep)
        assert "--tag seam-pick:2026-06-03" in line

    def test_empty_list_returns_empty(self):
        assert to_vein_lines([]) == []

    def test_safe_tag_strips_slash(self):
        from seam.harvest.veinout import _safe_tag
        assert "/" not in _safe_tag("owner/repo")
        assert _safe_tag("tech_strong") == "tech_strong"

    def test_full_line_parseable(self):
        """The line must be splittable into 'slug args...' with no whitespace in tags."""
        rep = _make_report()
        line = _vein_line(rep)
        parts = line.split()
        # first part is slug, rest are --tag key pairs
        assert parts[0] == rep.candidate_id
        i = 1
        while i < len(parts):
            assert parts[i] == "--tag", f"expected --tag, got {parts[i]!r}"
            assert " " not in parts[i + 1], f"tag value has space: {parts[i+1]!r}"
            i += 2


# ── veinout.write_vein_watchlist ───────────────────────────────────────────────

class TestWriteVeinWatchlist:
    def test_creates_watchlist_yaml(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        reps = [_make_report("a/b"), _make_report("c/d")]
        write_vein_watchlist(reps, vein_dir, "2026-06-03")
        assert (vein_dir / "watchlist.yaml").exists()

    def test_returns_watchlist_path(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        path = write_vein_watchlist([_make_report()], vein_dir, "2026-06-03")
        assert path == vein_dir / "watchlist.yaml"

    def test_collection_key_format(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        write_vein_watchlist([_make_report("x/y")], vein_dir, "2026-06-03")
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        assert "seam-2026-06-03" in data

    def test_repos_list_correct(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        reps = [_make_report("a/b"), _make_report("c/d")]
        write_vein_watchlist(reps, vein_dir, "2026-06-03")
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        repos = data["seam-2026-06-03"]["repos"]
        assert "a/b" in repos
        assert "c/d" in repos
        assert len(repos) == 2

    def test_compare_true(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        write_vein_watchlist([_make_report()], vein_dir, "2026-06-03")
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        assert data["seam-2026-06-03"]["compare"] is True

    def test_creates_vein_dir(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        assert not vein_dir.exists()
        write_vein_watchlist([_make_report()], vein_dir, "2026-06-03")
        assert vein_dir.exists()

    def test_idempotent_same_day(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        reps1 = [_make_report("a/b")]
        reps2 = [_make_report("c/d")]
        write_vein_watchlist(reps1, vein_dir, "2026-06-03")
        write_vein_watchlist(reps2, vein_dir, "2026-06-03")   # overwrites same day
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        repos = data["seam-2026-06-03"]["repos"]
        # second call replaces — should have c/d not a/b
        assert "c/d" in repos
        assert "a/b" not in repos

    def test_different_days_coexist(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        write_vein_watchlist([_make_report("a/b")], vein_dir, "2026-06-01")
        write_vein_watchlist([_make_report("c/d")], vein_dir, "2026-06-03")
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        assert "seam-2026-06-01" in data
        assert "seam-2026-06-03" in data

    def test_preserves_existing_non_seam_keys(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        vein_dir.mkdir()
        existing = {"my-collection": {"repos": ["x/y"], "compare": False}}
        (vein_dir / "watchlist.yaml").write_text(yaml.dump(existing))
        write_vein_watchlist([_make_report("a/b")], vein_dir, "2026-06-03")
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        assert "my-collection" in data    # preserved
        assert "seam-2026-06-03" in data  # added

    def test_empty_reps_writes_empty_repos(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        write_vein_watchlist([], vein_dir, "2026-06-03")
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        assert data["seam-2026-06-03"]["repos"] == []

    def test_unparseable_watchlist_not_overwritten(self, tmp_path):
        """Corrupt existing YAML → raise, never clobber the file."""
        vein_dir = tmp_path / ".vein"
        vein_dir.mkdir()
        corrupt = "key: [unclosed\n  - broken: : :\n"
        wl = vein_dir / "watchlist.yaml"
        wl.write_text(corrupt)
        with pytest.raises(WatchlistError):
            write_vein_watchlist([_make_report("a/b")], vein_dir, "2026-06-03")
        assert wl.read_text() == corrupt   # untouched

    def test_non_mapping_watchlist_not_overwritten(self, tmp_path):
        """Valid YAML that isn't a mapping (e.g. a list) → raise, keep file."""
        vein_dir = tmp_path / ".vein"
        vein_dir.mkdir()
        original = "- just\n- a\n- list\n"
        wl = vein_dir / "watchlist.yaml"
        wl.write_text(original)
        with pytest.raises(WatchlistError):
            write_vein_watchlist([_make_report("a/b")], vein_dir, "2026-06-03")
        assert wl.read_text() == original

    def test_empty_watchlist_file_ok(self, tmp_path):
        """Empty file (safe_load → None) is fine — treated as fresh."""
        vein_dir = tmp_path / ".vein"
        vein_dir.mkdir()
        (vein_dir / "watchlist.yaml").write_text("")
        write_vein_watchlist([_make_report("a/b")], vein_dir, "2026-06-03")
        data = yaml.safe_load((vein_dir / "watchlist.yaml").read_text())
        assert data["seam-2026-06-03"]["repos"] == ["a/b"]

    def test_no_tmp_leftover_after_write(self, tmp_path):
        vein_dir = tmp_path / ".vein"
        write_vein_watchlist([_make_report("a/b")], vein_dir, "2026-06-03")
        leftovers = [p for p in vein_dir.iterdir() if ".tmp." in p.name]
        assert leftovers == []


# ── core.store.atomic_write_text ───────────────────────────────────────────────

class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        from seam.core.store import atomic_write_text
        p = tmp_path / "out.txt"
        atomic_write_text(p, "hello")
        assert p.read_text() == "hello"

    def test_overwrites_atomically(self, tmp_path):
        from seam.core.store import atomic_write_text
        p = tmp_path / "out.txt"
        p.write_text("old")
        atomic_write_text(p, "new")
        assert p.read_text() == "new"

    def test_creates_parent_dirs(self, tmp_path):
        from seam.core.store import atomic_write_text
        p = tmp_path / "a" / "b" / "out.txt"
        atomic_write_text(p, "x")
        assert p.read_text() == "x"

    def test_failed_write_keeps_original(self, tmp_path, monkeypatch):
        """Crash between temp-write and replace must keep the old file."""
        import seam.core.store as store_mod
        p = tmp_path / "out.txt"
        p.write_text("precious")

        def boom(src, dst):
            raise OSError("simulated crash")

        monkeypatch.setattr(store_mod.os, "replace", boom)
        with pytest.raises(OSError):
            store_mod.atomic_write_text(p, "half-written")
        assert p.read_text() == "precious"
        leftovers = [q for q in tmp_path.iterdir() if ".tmp." in q.name]
        assert leftovers == []   # temp cleaned up


# ── core.config — token env-only ───────────────────────────────────────────────

class TestGithubTokenEnvOnly:
    def _cfg_with_profile_token(self, token: str):
        from seam.core.config import SeamConfig, DEFAULT_PROFILE
        import copy
        data = copy.deepcopy(DEFAULT_PROFILE)
        data["github"]["token"] = token
        return SeamConfig(data)

    def test_profile_token_ignored(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        cfg = self._cfg_with_profile_token("ghp_plaintext_secret")
        assert cfg.github_token == ""          # NOT the profile value
        err = capsys.readouterr().err
        assert "IGNORED" in err                # user is warned

    def test_env_token_used(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_env")
        cfg = self._cfg_with_profile_token("ghp_plaintext_secret")
        assert cfg.github_token == "ghp_from_env"

    def test_warning_emitted_once(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        cfg = self._cfg_with_profile_token("ghp_x")
        cfg.github_token
        cfg.github_token
        err = capsys.readouterr().err
        assert err.count("IGNORED") == 1

    def test_no_warning_when_profile_token_empty(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        cfg = self._cfg_with_profile_token("")
        assert cfg.github_token == ""
        assert "IGNORED" not in capsys.readouterr().err
