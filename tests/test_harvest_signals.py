"""
Phase 3.2 — harvest/signals.py golden-file tests.

Fixtures are built in-memory using tmp_path.
No network, no git, no ollama required.

Fixtures:
  rust_repo    — Rust project with tests, CI, rustfmt, LICENSE, CHANGELOG
  python_repo  — Python project with tests, CI, pyproject.toml linter
  empty_repo   — nothing but .seam-meta.json
  no_readme    — source files but no README
  long_readme  — README > 1500 chars (truncation check)
  monorepo     — multiple Cargo.toml + package.json at subpackage level
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from seam.harvest.signals import (
    collect_signals,
    _detect_tests,
    _detect_ci,
    _detect_linters,
    _detect_license,
    _detect_changelog,
    _detect_versions,
    _detect_manifests,
    _compute_lang_breakdown,
    _compute_sloc,
    _read_readme,
    _pick_sample_files,
    _walk,
    _README_TRUNCATE,
    _TEST_FILE_RE,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def mkfile(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_meta(repo: Path, slug: str) -> None:
    meta = repo / ".seam-meta.json"
    meta.write_text(
        json.dumps({
            "candidate_id": slug,
            "commit": "abc123def456",
            "language": "rust",
            "stars": 5000,
            "clone_url": f"https://github.com/{slug}.git",
            "size_kb": 4096,
        }, indent=2) + "\n",
        encoding="utf-8",
    )


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def rust_repo(tmp_path: Path) -> Path:
    """Rust repo with tests, CI, rustfmt, LICENSE, CHANGELOG, README."""
    write_meta(tmp_path, "astral-sh/ruff")
    mkfile(tmp_path / "Cargo.toml",
           '[package]\nname = "ruff"\nversion = "0.4.0"\nedition = "2021"\n')
    mkfile(tmp_path / "src" / "main.rs",
           "fn main() {\n    println!(\"hello\");\n}\n")
    mkfile(tmp_path / "src" / "lib.rs",
           "pub mod core;\npub mod parser;\n")
    mkfile(tmp_path / "src" / "parser.rs",
           "pub fn parse(s: &str) -> Vec<u8> { s.bytes().collect() }\n")
    mkfile(tmp_path / "tests" / "integration_test.rs",
           "#[test]\nfn test_parse() { assert_eq!(1, 1); }\n")
    mkfile(tmp_path / ".github" / "workflows" / "ci.yml",
           "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
    mkfile(tmp_path / "rustfmt.toml", 'max_width = 100\n')
    mkfile(tmp_path / "LICENSE", "MIT License\n")
    mkfile(tmp_path / "CHANGELOG.md",
           "## [0.4.0] - 2026-05-01\n### Added\n- initial release\n")
    mkfile(tmp_path / "README.md",
           "# ruff\nA fast Python linter written in Rust.\n\n## Usage\n`ruff check .`\n")
    return tmp_path


@pytest.fixture()
def python_repo(tmp_path: Path) -> Path:
    """Python repo with tests, pyproject.toml linter, CI, LICENSE."""
    write_meta(tmp_path, "simonw/llm")
    mkfile(tmp_path / "pyproject.toml",
           "[project]\nname = \"llm\"\nversion = \"0.5.0\"\n\n"
           "[tool.ruff]\nline-length = 88\n\n"
           "[tool.mypy]\nstrict = true\n")
    mkfile(tmp_path / "src" / "llm" / "__init__.py", "")
    mkfile(tmp_path / "src" / "llm" / "core.py",
           "def run(prompt: str) -> str:\n    return prompt\n")
    mkfile(tmp_path / "tests" / "test_core.py",
           "from llm.core import run\n\ndef test_run():\n    assert run('hi') == 'hi'\n")
    mkfile(tmp_path / ".github" / "workflows" / "tests.yml",
           "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n")
    mkfile(tmp_path / "LICENSE", "Apache 2.0\n")
    mkfile(tmp_path / "README.md",
           "# llm\nA CLI tool for running LLMs.\n\n## Install\n`pip install llm`\n")
    return tmp_path


@pytest.fixture()
def empty_repo(tmp_path: Path) -> Path:
    """Repo with only .seam-meta.json — no source, no README, nothing."""
    write_meta(tmp_path, "ghost/empty")
    return tmp_path


@pytest.fixture()
def no_readme_repo(tmp_path: Path) -> Path:
    """Repo with source files but no README."""
    write_meta(tmp_path, "owner/noop")
    mkfile(tmp_path / "main.py", "print('hello')\n")
    mkfile(tmp_path / "utils.py", "def helper(): pass\n")
    return tmp_path


@pytest.fixture()
def long_readme_repo(tmp_path: Path) -> Path:
    """Repo with a README that exceeds _README_TRUNCATE chars."""
    write_meta(tmp_path, "owner/bigdoc")
    long_text = "A" * 3000
    mkfile(tmp_path / "README.md", long_text)
    return tmp_path


@pytest.fixture()
def monorepo(tmp_path: Path) -> Path:
    """
    Monorepo with Rust + TypeScript packages and multiple manifests.
    Tests that signals scan the full tree, not just root.
    """
    write_meta(tmp_path, "vercel/turbo")
    # root
    mkfile(tmp_path / "package.json",
           '{"name": "turbo", "version": "2.0.0", "private": true}\n')
    # rust crate
    mkfile(tmp_path / "crates" / "turborepo" / "Cargo.toml",
           '[package]\nname = "turborepo"\nversion = "0.1.0"\n')
    mkfile(tmp_path / "crates" / "turborepo" / "src" / "lib.rs",
           "pub fn run() {}\n")
    mkfile(tmp_path / "crates" / "turborepo" / "tests" / "test_run.rs",
           "#[test]\nfn it_works() {}\n")
    # typescript package
    mkfile(tmp_path / "packages" / "ui" / "index.ts",
           'export const Button = () => <button>click</button>;\n')
    mkfile(tmp_path / "packages" / "ui" / "index.test.ts",
           'import { Button } from "./index";\ntest("renders", () => {});\n')
    mkfile(tmp_path / ".github" / "workflows" / "ci.yml",
           "on: [push]\njobs:\n  build: {}\n")
    mkfile(tmp_path / ".editorconfig",
           "[*]\nindent_style = space\nindent_size = 2\n")
    mkfile(tmp_path / "LICENSE", "MIT\n")
    return tmp_path


# ── rust_repo tests ────────────────────────────────────────────────────────────

class TestRustRepo:
    def test_slug(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert sig.slug == "astral-sh/ruff"

    def test_tests_detected(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert sig.has_tests is True
        assert sig.test_file_count >= 1

    def test_ci_detected(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert any("ci.yml" in f for f in sig.ci_files), sig.ci_files

    def test_linter_detected(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert "rustfmt.toml" in sig.linter_cfgs

    def test_license_detected(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert sig.has_license is True

    def test_changelog_detected(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert sig.has_changelog is True

    def test_manifest_detected(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert "Cargo.toml" in sig.dep_manifests

    def test_lang_breakdown_has_rs(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert ".rs" in sig.lang_breakdown
        assert sig.lang_breakdown[".rs"] > 50.0  # majority Rust

    def test_sloc_positive(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert sig.sloc > 0

    def test_readme(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert sig.readme_len > 0
        assert "ruff" in sig.readme_snippet

    def test_sample_files(self, rust_repo):
        sig = collect_signals(rust_repo)
        assert len(sig.sample_files) > 0
        # sample files should not include test files
        for f in sig.sample_files:
            assert "tests/" not in f, f"test file leaked into sample: {f}"

    def test_version_extracted(self, rust_repo):
        sig = collect_signals(rust_repo)
        # Cargo.toml has version = "0.4.0"; CHANGELOG has [0.4.0]
        assert any("0.4.0" in v for v in sig.semver_tags), sig.semver_tags


# ── python_repo tests ──────────────────────────────────────────────────────────

class TestPythonRepo:
    def test_slug(self, python_repo):
        sig = collect_signals(python_repo)
        assert sig.slug == "simonw/llm"

    def test_tests_detected(self, python_repo):
        sig = collect_signals(python_repo)
        assert sig.has_tests is True
        assert sig.test_file_count >= 1

    def test_ci_detected(self, python_repo):
        sig = collect_signals(python_repo)
        assert any("tests.yml" in f for f in sig.ci_files), sig.ci_files

    def test_pyproject_linter_ruff(self, python_repo):
        sig = collect_signals(python_repo)
        assert "pyproject.toml:[tool.ruff]" in sig.linter_cfgs

    def test_pyproject_linter_mypy(self, python_repo):
        sig = collect_signals(python_repo)
        assert "pyproject.toml:[tool.mypy]" in sig.linter_cfgs

    def test_manifest_pyproject(self, python_repo):
        sig = collect_signals(python_repo)
        assert "pyproject.toml" in sig.dep_manifests

    def test_lang_breakdown_has_py(self, python_repo):
        sig = collect_signals(python_repo)
        assert ".py" in sig.lang_breakdown

    def test_version_from_pyproject(self, python_repo):
        sig = collect_signals(python_repo)
        assert any("0.5.0" in v for v in sig.semver_tags), sig.semver_tags


# ── empty_repo tests ───────────────────────────────────────────────────────────

class TestEmptyRepo:
    def test_no_tests(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.has_tests is False
        assert sig.test_file_count == 0

    def test_no_ci(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.ci_files == []

    def test_no_linter(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.linter_cfgs == []

    def test_no_license(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.has_license is False

    def test_no_changelog(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.has_changelog is False

    def test_no_manifests(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.dep_manifests == []

    def test_no_readme(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.readme_len == 0
        assert sig.readme_snippet == ""

    def test_zero_sloc(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.sloc == 0

    def test_empty_lang_breakdown(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.lang_breakdown == {}

    def test_no_sample_files(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.sample_files == []

    def test_slug_from_meta(self, empty_repo):
        sig = collect_signals(empty_repo)
        assert sig.slug == "ghost/empty"


# ── no_readme tests ────────────────────────────────────────────────────────────

class TestNoReadme:
    def test_readme_len_zero(self, no_readme_repo):
        sig = collect_signals(no_readme_repo)
        assert sig.readme_len == 0

    def test_readme_snippet_empty(self, no_readme_repo):
        sig = collect_signals(no_readme_repo)
        assert sig.readme_snippet == ""

    def test_sloc_positive(self, no_readme_repo):
        sig = collect_signals(no_readme_repo)
        assert sig.sloc > 0   # main.py + utils.py have non-blank lines


# ── README truncation ──────────────────────────────────────────────────────────

class TestReadmeTruncation:
    def test_readme_len_is_full_length(self, long_readme_repo):
        sig = collect_signals(long_readme_repo)
        assert sig.readme_len == 3000

    def test_snippet_truncated(self, long_readme_repo):
        sig = collect_signals(long_readme_repo)
        assert len(sig.readme_snippet) == _README_TRUNCATE

    def test_snippet_is_prefix(self, long_readme_repo):
        sig = collect_signals(long_readme_repo)
        assert sig.readme_snippet == "A" * _README_TRUNCATE


# ── monorepo tests ─────────────────────────────────────────────────────────────

class TestMonorepo:
    def test_tests_found_in_subdirs(self, monorepo):
        sig = collect_signals(monorepo)
        assert sig.has_tests is True
        assert sig.test_file_count >= 2   # test_run.rs + index.test.ts

    def test_mixed_lang_breakdown(self, monorepo):
        sig = collect_signals(monorepo)
        assert ".rs" in sig.lang_breakdown
        assert ".ts" in sig.lang_breakdown

    def test_ci_found(self, monorepo):
        sig = collect_signals(monorepo)
        assert len(sig.ci_files) >= 1

    def test_editorconfig_as_linter(self, monorepo):
        sig = collect_signals(monorepo)
        assert ".editorconfig" in sig.linter_cfgs

    def test_manifests_root_only(self, monorepo):
        """dep_manifests only checks repo root — not recursive."""
        sig = collect_signals(monorepo)
        # root has package.json only (Cargo.toml is in crates/turborepo/)
        assert "package.json" in sig.dep_manifests
        # Cargo.toml is NOT at root, so should not appear
        assert "Cargo.toml" not in sig.dep_manifests

    def test_sample_files_no_tests(self, monorepo):
        sig = collect_signals(monorepo)
        for f in sig.sample_files:
            # reject files whose name matches the test pattern (e.g. index.test.ts)
            fname = Path(f).name
            assert not _TEST_FILE_RE.match(fname), (
                f"test file should not appear in samples: {f}"
            )


# ── unit tests for individual helpers ─────────────────────────────────────────

class TestHelpers:
    def test_walk_skips_git_dir(self, tmp_path):
        """_walk must not descend into .git/"""
        mkfile(tmp_path / ".git" / "objects" / "pack" / "data.pack", "x")
        mkfile(tmp_path / "src" / "main.py", "pass\n")
        files = _walk(tmp_path)
        paths_str = [str(f) for f in files]
        assert not any(".git" in p for p in paths_str)
        assert any("main.py" in p for p in paths_str)

    def test_walk_skips_node_modules(self, tmp_path):
        mkfile(tmp_path / "node_modules" / "lodash" / "index.js", "module.exports={}")
        mkfile(tmp_path / "index.ts", "export const x = 1;\n")
        files = _walk(tmp_path)
        # Use relative paths to avoid matching against the tmp_path dir name itself
        rels = [str(f.relative_to(tmp_path)) for f in files]
        assert not any("node_modules" in r for r in rels)
        assert any("index.ts" in r for r in rels)

    def test_walk_skips_pycache(self, tmp_path):
        mkfile(tmp_path / "__pycache__" / "main.cpython-310.pyc", "bytecode")
        mkfile(tmp_path / "main.py", "pass\n")
        files = _walk(tmp_path)
        assert not any("__pycache__" in str(f) for f in files)

    def test_readme_fallback_rst(self, tmp_path):
        mkfile(tmp_path / "README.rst", "Title\n=====\nContent here.\n")
        length, snippet = _read_readme(tmp_path)
        assert length > 0
        assert "Title" in snippet

    def test_readme_md_takes_priority(self, tmp_path):
        mkfile(tmp_path / "README.md", "# MD readme\n")
        mkfile(tmp_path / "README.rst", "RST readme\n")
        _, snippet = _read_readme(tmp_path)
        assert "MD readme" in snippet

    def test_linter_detection_no_false_positives(self, tmp_path):
        """pyproject.toml without linter sections should not produce linter entries."""
        mkfile(tmp_path / "pyproject.toml",
               "[build-system]\nrequires = [\"setuptools\"]\n")
        linters = _detect_linters(tmp_path)
        # no tool.ruff / tool.mypy etc in that file
        assert not any("pyproject" in l for l in linters)

    def test_detect_tests_name_pattern(self, tmp_path):
        """test_*.py anywhere in the tree should be counted."""
        mkfile(tmp_path / "src" / "test_utils.py", "def test_foo(): pass\n")
        all_files = _walk(tmp_path)
        has, count = _detect_tests(tmp_path, all_files)
        assert has is True
        assert count >= 1

    def test_detect_tests_dir(self, tmp_path):
        """Files inside tests/ dir should be counted even without test_ prefix."""
        mkfile(tmp_path / "tests" / "helpers.py", "def helper(): pass\n")
        all_files = _walk(tmp_path)
        has, count = _detect_tests(tmp_path, all_files)
        assert has is True

    def test_sloc_fallback_counts_nonblank(self, tmp_path):
        """SLOC fallback counts non-blank lines (tokei not available in CI)."""
        mkfile(tmp_path / "a.py", "line1\n\nline2\nline3\n")
        mkfile(tmp_path / "b.py", "\n\nline_a\n")
        all_files = _walk(tmp_path)
        sloc = _compute_sloc(tmp_path, all_files)
        assert sloc == 4  # 3 from a.py + 1 from b.py

    def test_version_extraction_from_cargo(self, tmp_path):
        mkfile(tmp_path / "Cargo.toml",
               '[package]\nname = "foo"\nversion = "1.2.3"\n')
        versions = _detect_versions(tmp_path)
        assert "1.2.3" in versions

    def test_version_extraction_from_package_json(self, tmp_path):
        mkfile(tmp_path / "package.json",
               '{"name": "foo", "version": "3.0.1"}\n')
        versions = _detect_versions(tmp_path)
        assert "3.0.1" in versions

    def test_lang_breakdown_sums_to_100(self, tmp_path):
        mkfile(tmp_path / "a.rs", "fn main() {}\n")
        mkfile(tmp_path / "b.rs", "fn foo() {}\n")
        mkfile(tmp_path / "c.py", "pass\n")
        all_files = _walk(tmp_path)
        breakdown = _compute_lang_breakdown(all_files, tmp_path)
        total = sum(breakdown.values())
        assert abs(total - 100.0) < 0.2, f"breakdown total: {total}"

    def test_sample_files_capped(self, tmp_path):
        """_pick_sample_files returns at most _MAX_SAMPLE_FILES entries."""
        for i in range(20):
            mkfile(tmp_path / f"src_{i}.py", f"def fn_{i}(): pass\n")
        all_files = _walk(tmp_path)
        samples = _pick_sample_files(tmp_path, all_files)
        assert len(samples) <= 8

    def test_sample_files_excludes_tests(self, tmp_path):
        mkfile(tmp_path / "src" / "main.py", "def main(): pass\n")
        mkfile(tmp_path / "tests" / "test_main.py", "def test_main(): pass\n")
        all_files = _walk(tmp_path)
        samples = _pick_sample_files(tmp_path, all_files)
        assert all("tests/" not in f for f in samples), samples
