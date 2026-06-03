"""Phase 3.0 — Foundation unit tests.

Covers:
- HarvestCfg config parsing (full + missing fields → defaults)
- SeamConfig.harvest_target_dir() error on empty/missing target_dir
- slug → repo dir path mapping
- layout: assert_target_dir raises on missing / non-dir
- store: append_index_entry + load_index idempotency + fsync
- store: harvested_keys
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from seam.core.config import load_config, SeamConfig, DEFAULT_HARVEST, DEFAULT_PROFILE, PROFILE_VERSION
from seam.core.store import append_index_entry, load_index, harvested_keys
from seam.harvest.layout import (
    slug_to_dir_name,
    repo_dir,
    assert_target_dir,
    ensure_lang_dir,
    write_meta,
    read_meta,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_profile(tmp: Path, overrides: dict | None = None) -> Path:
    data = {**DEFAULT_PROFILE}
    if overrides:
        data.update(overrides)
    p = tmp / ".seam" / "profile.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(data, f)
    return p


# ── config: HarvestCfg ────────────────────────────────────────────────────────

class TestHarvestCfg:
    def test_defaults_when_no_harvest_section(self, tmp_path):
        """Profile without harvest: section → full defaults applied."""
        profile = {k: v for k, v in DEFAULT_PROFILE.items() if k != "harvest"}
        p = _write_profile(tmp_path, profile)
        cfg = load_config(p)
        hc = cfg.harvest
        assert hc.max_repos_per_night == 5
        assert hc.clone_depth == 1
        assert hc.size_cap_mb == 500
        assert hc.min_free_gb == 20
        assert hc.tag_threshold == 70
        assert hc.readme_truncate == 1500
        assert hc.sample_files == 8
        assert hc.ollama_timeout_sec == 180
        assert hc.retention == "permanent"

    def test_partial_harvest_section_merges_defaults(self, tmp_path):
        """Partial harvest: section keeps user values and fills rest from defaults."""
        profile = {
            **DEFAULT_PROFILE,
            "harvest": {
                "max_repos_per_night": 10,
                "clone": {"size_cap_mb": 200},
            },
        }
        p = _write_profile(tmp_path, profile)
        cfg = load_config(p)
        hc = cfg.harvest
        assert hc.max_repos_per_night == 10
        assert hc.size_cap_mb == 200
        # untouched defaults
        assert hc.clone_depth == 1
        assert hc.min_free_gb == 20

    def test_lang_map_default(self, tmp_path):
        p = _write_profile(tmp_path)
        cfg = load_config(p)
        hc = cfg.harvest
        assert hc.map_language("Rust") == "rust"
        assert hc.map_language("Python") == "python"
        assert hc.map_language("COBOL") == "_unknown"

    def test_lang_map_custom(self, tmp_path):
        profile = {
            **DEFAULT_PROFILE,
            "harvest": {"lang_map": {"Zig": "zig", "default": "_misc"}},
        }
        p = _write_profile(tmp_path, profile)
        cfg = load_config(p)
        hc = cfg.harvest
        assert hc.map_language("Zig") == "zig"
        assert hc.map_language("Rust") == "_misc"  # not in custom map

    def test_harvest_target_dir_raises_when_empty(self, tmp_path):
        """harvest.target_dir = '' → harvest_target_dir() raises ValueError."""
        profile = {
            **DEFAULT_PROFILE,
            "harvest": {**DEFAULT_HARVEST, "target_dir": ""},
        }
        p = _write_profile(tmp_path, profile)
        cfg = load_config(p)
        with pytest.raises(ValueError, match="target_dir is not set"):
            cfg.harvest_target_dir()

    def test_harvest_target_dir_returns_path_when_set(self, tmp_path):
        fake_dir = tmp_path / "2T"
        fake_dir.mkdir()
        profile = {
            **DEFAULT_PROFILE,
            "harvest": {**DEFAULT_HARVEST, "target_dir": str(fake_dir)},
        }
        p = _write_profile(tmp_path, profile)
        cfg = load_config(p)
        assert cfg.harvest_target_dir() == fake_dir

    def test_profile_hash_stable(self, tmp_path):
        p = _write_profile(tmp_path)
        cfg = load_config(p)
        h1 = cfg.profile_hash()
        h2 = cfg.profile_hash()
        assert h1 == h2
        assert len(h1) == 8


# ── layout: slug → path ───────────────────────────────────────────────────────

class TestLayout:
    def test_slug_to_dir_name(self):
        assert slug_to_dir_name("astral-sh/ruff") == "astral-sh__ruff"
        assert slug_to_dir_name("owner/repo") == "owner__repo"

    def test_repo_dir(self, tmp_path):
        d = repo_dir(tmp_path, "rust", "astral-sh/ruff")
        assert d == tmp_path / "rust" / "astral-sh__ruff"

    def test_assert_target_dir_raises_on_missing(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with pytest.raises(ValueError, match="does not exist"):
            assert_target_dir(missing)

    def test_assert_target_dir_raises_on_empty_string(self):
        with pytest.raises(ValueError, match="not set"):
            assert_target_dir(Path(""))

    def test_assert_target_dir_ok(self, tmp_path):
        assert_target_dir(tmp_path)  # should not raise

    def test_assert_target_dir_raises_on_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            assert_target_dir(f)

    def test_ensure_lang_dir_creates(self, tmp_path):
        d = ensure_lang_dir(tmp_path, "rust")
        assert d.is_dir()
        assert d == tmp_path / "rust"

    def test_write_read_meta(self, tmp_path):
        clone_dir = tmp_path / "rust" / "owner__repo"
        clone_dir.mkdir(parents=True)
        write_meta(clone_dir, "owner/repo", "abc123", "rust", 1234, "https://github.com/owner/repo", size_kb=512)
        meta = read_meta(clone_dir)
        assert meta is not None
        assert meta["candidate_id"] == "owner/repo"
        assert meta["commit"] == "abc123"
        assert meta["language"] == "rust"
        assert meta["stars"] == 1234
        assert meta["size_kb"] == 512

    def test_read_meta_missing(self, tmp_path):
        assert read_meta(tmp_path / "no_such_dir") is None


# ── store: _index.jsonl ───────────────────────────────────────────────────────

class TestIndexStore:
    def _entry(self, candidate_id="owner/repo", commit="abc123", **kw):
        return {"candidate_id": candidate_id, "commit": commit, **kw}

    def test_append_and_load(self, tmp_path):
        e = self._entry(stars=500)
        append_index_entry(e, tmp_path)
        loaded = load_index(tmp_path)
        assert len(loaded) == 1
        assert loaded[0]["candidate_id"] == "owner/repo"
        assert loaded[0]["stars"] == 500

    def test_idempotent_same_commit(self, tmp_path):
        """Appending the same (candidate_id, commit) twice → only one entry."""
        e = self._entry()
        append_index_entry(e, tmp_path)
        append_index_entry(e, tmp_path)
        loaded = load_index(tmp_path)
        assert len(loaded) == 1

    def test_different_commit_allowed(self, tmp_path):
        """Same slug but different commit (re-harvested) → two entries."""
        e1 = self._entry(commit="aaa")
        e2 = self._entry(commit="bbb")
        append_index_entry(e1, tmp_path)
        append_index_entry(e2, tmp_path)
        loaded = load_index(tmp_path)
        assert len(loaded) == 2

    def test_load_filtered_by_slug(self, tmp_path):
        append_index_entry(self._entry("owner/a", "c1"), tmp_path)
        append_index_entry(self._entry("owner/b", "c2"), tmp_path)
        loaded = load_index(tmp_path, slug="owner/a")
        assert len(loaded) == 1
        assert loaded[0]["candidate_id"] == "owner/a"

    def test_load_empty(self, tmp_path):
        assert load_index(tmp_path) == []

    def test_harvested_keys(self, tmp_path):
        append_index_entry(self._entry("r1", "c1"), tmp_path)
        append_index_entry(self._entry("r2", "c2"), tmp_path)
        keys = harvested_keys(tmp_path)
        assert ("r1", "c1") in keys
        assert ("r2", "c2") in keys
        assert len(keys) == 2

    def test_fsync_called(self, tmp_path, monkeypatch):
        """Verify os.fsync is actually called on append."""
        synced = []
        orig = os.fsync
        monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or orig(fd))
        append_index_entry(self._entry(), tmp_path)
        assert len(synced) >= 1
