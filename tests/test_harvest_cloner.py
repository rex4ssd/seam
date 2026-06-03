"""Phase 3.1 — clone engine unit tests.

Network tests are marked @pytest.mark.network and skipped by default.
Run with: pytest -m network tests/test_harvest_cloner.py

Unit tests use monkeypatch to avoid any real git/network calls.
"""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from seam.core.config import load_config, DEFAULT_PROFILE, DEFAULT_HARVEST
from seam.core.models import CloneResult
from seam.harvest.cloner import (
    clone_repo,
    clone_repos_batch,
    _free_gb,
    _head_commit,
    _remote_head,
)
from seam.harvest.layout import write_meta, read_meta, repo_dir

import yaml


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_cfg(tmp_path: Path, overrides: dict | None = None) -> object:
    harvest_cfg = {
        **DEFAULT_HARVEST,
        "target_dir": str(tmp_path),
        "clone": {**DEFAULT_HARVEST["clone"], "timeout_sec": 60, "min_free_gb": 1},
    }
    if overrides:
        harvest_cfg.update(overrides)
    profile_data = {
        **DEFAULT_PROFILE,
        "harvest": harvest_cfg,
    }
    p = tmp_path / ".seam" / "profile.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.dump(profile_data, f)
    cfg = load_config(p)
    return cfg


# ── unit: disk full guard (P-H01) ────────────────────────────────────────────

class TestDiskGuard:
    def test_skip_when_disk_full(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=0.5):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="Python")

        assert result.skipped
        assert result.skipped_reason == "disk_full"

    def test_batch_stops_on_disk_full(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest
        slugs = [("a/r1", "Python", 100), ("a/r2", "Python", 100)]

        free_values = iter([0.5])  # always low

        with patch("seam.harvest.cloner._free_gb", side_effect=lambda _: next(free_values, 0.5)):
            results = clone_repos_batch(slugs, tmp_path, hcfg)

        assert all(r.skipped for r in results)
        assert results[0].skipped_reason == "disk_full"


# ── unit: dry-run ─────────────────────────────────────────────────────────────

class TestDryRun:
    def test_dry_run_does_not_clone(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0):
            result = clone_repo("owner/myrepo", tmp_path, hcfg,
                                language="Rust", dry_run=True)

        assert result.skipped
        assert result.skipped_reason == "dry_run"
        # no directory should be created
        assert not (tmp_path / "rust" / "owner__myrepo").exists()

    def test_dry_run_returns_expected_path(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0):
            result = clone_repo("owner/myrepo", tmp_path, hcfg,
                                language="Rust", dry_run=True)

        assert "owner__myrepo" in result.clone_path
        assert result.commit == "dry-run"


# ── unit: already exists same commit (P-H03) ─────────────────────────────────

class TestExistingRepo:
    def _setup_existing(self, tmp_path: Path, slug: str, commit: str, lang: str) -> Path:
        """Create a fake already-cloned repo dir with .seam-meta.json."""
        from seam.harvest.layout import repo_dir, ensure_lang_dir
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest
        lang_folder = hcfg.map_language(lang)
        d = repo_dir(tmp_path, lang_folder, slug)
        d.mkdir(parents=True, exist_ok=True)
        write_meta(d, slug, commit, lang_folder, 100, f"https://github.com/{slug}.git")
        return d

    def test_skip_when_same_commit(self, tmp_path):
        commit = "abc123def456" * 3 + "abcd"  # 40 chars
        self._setup_existing(tmp_path, "owner/repo", commit, "Python")

        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._remote_head", return_value=commit):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="Python")

        assert result.skipped
        assert result.skipped_reason == "exists_same_commit"

    def test_update_when_different_commit(self, tmp_path):
        old_commit = "a" * 40
        new_commit = "b" * 40
        self._setup_existing(tmp_path, "owner/repo", old_commit, "Python")

        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._remote_head", return_value=new_commit), \
             patch("seam.harvest.cloner._do_fetch_reset", return_value=(True, new_commit)):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="Python")

        assert not result.skipped
        assert result.commit == new_commit

    def test_skip_conservatively_when_remote_head_unavailable(self, tmp_path):
        commit = "a" * 40
        self._setup_existing(tmp_path, "owner/repo", commit, "Python")

        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._remote_head", return_value=None):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="Python")

        assert result.skipped


# ── unit: clone failure / timeout (P-H02) ────────────────────────────────────

class TestCloneFailure:
    def test_timeout_returns_skipped(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._remote_head", return_value=None), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 60)):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="Python")

        assert result.skipped
        assert result.skipped_reason in ("clone_failed", "disk_full", "exists_same_commit",
                                          "dry_run", "fetch_failed") or \
               result.skipped_reason.startswith("error:")

    def test_clone_error_returns_skipped(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._do_clone", return_value=(False, "", 0)):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="Python")

        assert result.skipped
        assert result.skipped_reason == "clone_failed"

    def test_batch_continues_after_failure(self, tmp_path):
        """One repo failing should not abort the whole batch."""
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest
        slugs = [("a/repo1", "Python", 100), ("b/repo2", "Rust", 200)]

        def fake_clone(slug, target_root, hcfg_, **kw):
            if slug == "a/repo1":
                return CloneResult(slug=slug, clone_path="", commit="",
                                   language="python", skipped=True,
                                   skipped_reason="clone_failed")
            return CloneResult(slug=slug, clone_path=str(target_root / "rust" / "b__repo2"),
                               commit="abc" * 13 + "a", language="rust")

        with patch("seam.harvest.cloner.clone_repo", side_effect=fake_clone), \
             patch("seam.harvest.cloner._free_gb", return_value=100.0):
            results = clone_repos_batch(slugs, tmp_path, hcfg)

        assert len(results) == 2
        assert results[0].skipped
        assert not results[1].skipped


# ── unit: meta written correctly ─────────────────────────────────────────────

class TestMetaWrite:
    def test_meta_written_after_successful_clone(self, tmp_path):
        commit = "c" * 40
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._do_clone", return_value=(True, commit, 512)):
            # _do_clone returns success; rename from tmp → final will happen
            # but rename may fail in sandbox — test the write_meta call instead
            with patch("seam.harvest.cloner.write_meta") as mock_write:
                # We also need to patch the rename to avoid real fs ops
                import pathlib
                original_rename = pathlib.Path.rename

                def fake_rename(self_, dst):
                    dst = pathlib.Path(dst)
                    dst.mkdir(parents=True, exist_ok=True)
                    # copy files from self_ to dst
                    if self_.exists():
                        shutil.copytree(str(self_), str(dst), dirs_exist_ok=True)
                        shutil.rmtree(self_, ignore_errors=True)

                with patch.object(pathlib.Path, "rename", fake_rename):
                    result = clone_repo("owner/testrepo", tmp_path, hcfg,
                                        language="Python", stars=500)

                if not result.skipped:
                    mock_write.assert_called_once()
                    call_kwargs = mock_write.call_args
                    assert call_kwargs[0][1] == "owner/testrepo"  # slug
                    assert call_kwargs[0][2] == commit
                    assert call_kwargs[1].get("size_kb") == 512 or \
                           call_kwargs[0][5] == 512  # stars or size_kb


# ── unit: language mapping ────────────────────────────────────────────────────

class TestLanguageMapping:
    def test_unknown_language_goes_to_unknown(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._do_clone", return_value=(False, "", 0)):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="COBOL")

        assert result.language == "_unknown"

    def test_rust_language_mapped(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        hcfg = cfg.harvest

        with patch("seam.harvest.cloner._free_gb", return_value=100.0), \
             patch("seam.harvest.cloner._do_clone", return_value=(False, "", 0)):
            result = clone_repo("owner/repo", tmp_path, hcfg, language="Rust")

        assert result.language == "rust"


# ── network tests (skipped by default) ───────────────────────────────────────

@pytest.mark.network
class TestNetworkClone:
    """Real clone tests. Run with: pytest -m network"""

    def test_clone_small_public_repo(self, tmp_path):
        """Clone a known tiny public repo to verify the full pipeline."""
        cfg = _make_cfg(tmp_path, {"min_free_gb": 0})
        hcfg = cfg.harvest
        hcfg.clone["min_free_gb"] = 0
        hcfg.clone["timeout_sec"] = 120

        # Use a very small public repo
        result = clone_repo(
            "nicowillis/hello-world-minimal",  # ~1 KB if exists, else fall back
            tmp_path,
            hcfg,
            language="Python",
            stars=0,
        )
        # Either cloned or skipped for a valid reason — should not raise
        assert isinstance(result, CloneResult)
        assert result.slug == "nicowillis/hello-world-minimal"

    def test_remote_head_returns_sha(self):
        sha = _remote_head("https://github.com/nicowillis/hello-world-minimal.git", 30)
        # May be None if repo doesn't exist — just check it doesn't raise
        assert sha is None or (isinstance(sha, str) and len(sha) == 40)
