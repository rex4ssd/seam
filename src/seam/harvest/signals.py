"""
harvest/signals.py — static signal collector for a cloned repo.

Zero network. Zero execution of cloned code (P-H07).
Pure filesystem scan + one safe read-only git command (git tag --list).

Pitfalls:
  P-H07  never execute cloned code — no subprocess on repo files
  P-S05  truncate README snippet to 1500 chars
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from ..core.models import RepoSignals
from .layout import meta_path as _meta_path

# ── constants ──────────────────────────────────────────────────────────────────

_README_TRUNCATE = 1500   # P-S05
_MAX_SAMPLE_FILES = 8

# ── CI file patterns ───────────────────────────────────────────────────────────

_CI_EXACT = {
    ".travis.yml",
    ".circleci/config.yml",
    "Jenkinsfile",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "bitbucket-pipelines.yml",
    ".woodpecker.yml",
    "circle.yml",
    "appveyor.yml",
    ".appveyor.yml",
    ".drone.yml",
}
# .github/workflows/*.yml|*.yaml handled separately

# ── linter / formatter configs ─────────────────────────────────────────────────

_LINTER_EXACT = {
    "rustfmt.toml", ".rustfmt.toml",
    "ruff.toml", ".ruff.toml",
    ".flake8", ".pylintrc",
    "mypy.ini", ".mypy.ini",
    ".editorconfig",
    ".pre-commit-config.yaml",
    ".eslintrc", ".eslintrc.js", ".eslintrc.json",
    ".eslintrc.yaml", ".eslintrc.yml",
    "eslint.config.js", "eslint.config.mjs",
    ".prettierrc", ".prettierrc.js", ".prettierrc.json",
    "biome.json",
    ".golangci.yml", ".golangci.yaml",
    "clippy.toml", ".clippy.toml",
    ".rubocop.yml",
    ".scalafmt.conf",
}
# pyproject.toml [tool.<X>] sections that indicate linting
_PYPROJECT_LINTER_TOOLS = {
    "ruff", "black", "isort", "mypy",
    "pylint", "flake8", "bandit", "pyright",
}

# ── dependency manifests ───────────────────────────────────────────────────────

_DEP_MANIFESTS_EXACT = {
    "Cargo.toml",
    "pyproject.toml", "setup.py", "setup.cfg",
    "requirements.txt", "requirements-dev.txt", "Pipfile",
    "package.json",
    "go.mod",
    "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "pubspec.yaml",
    "Package.swift",
    "project.clj",
    "dune-project",
}

# ── walk: directories to prune entirely ───────────────────────────────────────

# seam-generated files to exclude from SLOC / sample selection
_SEAM_OWN_FILES = frozenset({".seam-meta.json", "STRENGTH.md"})

_SKIP_DIRS = {
    ".git",
    "node_modules", ".pnpm", ".yarn",
    ".venv", "venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "target",           # Rust/Java build output
    "dist", "build",
    ".tox", ".eggs",
    "vendor", "third_party", "thirdparty", "extern",
    "generated", "gen",
}

# ── source / text extensions ───────────────────────────────────────────────────

_SOURCE_EXTS = frozenset({
    ".rs", ".py", ".go",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".swift", ".kt", ".kts",
    ".java", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp",
    ".cs", ".rb", ".ex", ".exs",
    ".ml", ".mli", ".hs", ".clj", ".cljs",
    ".zig", ".nim", ".scala", ".dart",
    ".lua", ".r", ".jl", ".elm",
})
_TEXT_EXTS = _SOURCE_EXTS | frozenset({
    ".md", ".rst", ".txt",
    ".toml", ".yaml", ".yml", ".json", ".json5",
    ".sh", ".bash", ".zsh", ".fish",
    ".html", ".css", ".scss", ".sass", ".less",
    ".sql", ".tf", ".proto", ".graphql",
    ".ini", ".cfg", ".conf",
})

# ── test detection ─────────────────────────────────────────────────────────────

_TEST_DIR_NAMES = frozenset({
    "tests", "test", "spec", "__tests__", "_tests", "testing",
    "integration_tests", "e2e", "functests",
})
_TEST_FILE_RE = re.compile(
    r"^test_.*\.(py|go|js|ts|jsx|tsx|rb)$"
    r"|^.*_test\.(py|rs|go|js|ts|jsx|tsx)$"
    r"|^.*\.test\.(js|ts|jsx|tsx|mjs)$"
    r"|^.*\.spec\.(js|ts|jsx|tsx|rb)$"
    r"|^.*_spec\.rb$",
    re.IGNORECASE,
)

# ── semver pattern ─────────────────────────────────────────────────────────────

_SEMVER_RE = re.compile(
    r"v?(\d+\.\d+\.\d+(?:-[\w.]+)?(?:\+[\w.]+)?)"
)


# ── public API ─────────────────────────────────────────────────────────────────

def collect_signals(repo_dir: Path) -> RepoSignals:
    """
    Scan a cloned repo directory and return RepoSignals.
    Zero network, zero execution of cloned code (P-H07).
    """
    all_files = _walk(repo_dir)

    has_tests, test_count = _detect_tests(repo_dir, all_files)
    ci_files               = _detect_ci(repo_dir)
    linter_cfgs            = _detect_linters(repo_dir)
    has_license            = _detect_license(repo_dir)
    has_changelog          = _detect_changelog(repo_dir)
    semver_tags            = _detect_versions(repo_dir)
    dep_manifests          = _detect_manifests(repo_dir)
    lang_breakdown         = _compute_lang_breakdown(all_files, repo_dir)
    sloc                   = _compute_sloc(repo_dir, all_files)
    readme_len, snippet    = _read_readme(repo_dir)
    sample_files           = _pick_sample_files(repo_dir, all_files)
    slug                   = _read_slug(repo_dir)

    return RepoSignals(
        slug=slug,
        has_tests=has_tests,
        test_file_count=test_count,
        ci_files=ci_files,
        linter_cfgs=linter_cfgs,
        has_license=has_license,
        has_changelog=has_changelog,
        semver_tags=semver_tags,
        dep_manifests=dep_manifests,
        lang_breakdown=lang_breakdown,
        sloc=sloc,
        readme_len=readme_len,
        readme_snippet=snippet,
        sample_files=sample_files,
    )


# ── internals ──────────────────────────────────────────────────────────────────

def _walk(repo_dir: Path) -> list[Path]:
    """
    Collect all files under repo_dir, pruning _SKIP_DIRS early.
    Uses os.walk (topdown) for O(n) traversal instead of rglob.
    """
    result: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo_dir, topdown=True, followlinks=False):
        # Prune in-place — os.walk won't descend into removed entries
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        dp = Path(dirpath)
        for fname in filenames:
            result.append(dp / fname)
    return result


def _rel(p: Path, repo_dir: Path) -> str:
    return str(p.relative_to(repo_dir))


def _read_slug(repo_dir: Path) -> str:
    """Read slug from .seam-meta.json; fall back to dir name."""
    meta = _meta_path(repo_dir)
    if meta.exists():
        try:
            with open(meta, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("candidate_id", _dir_to_slug(repo_dir.name))
        except (json.JSONDecodeError, OSError):
            pass
    return _dir_to_slug(repo_dir.name)


def _dir_to_slug(name: str) -> str:
    """'owner__repo' → 'owner/repo'"""
    return name.replace("__", "/", 1)


# ── tests ──────────────────────────────────────────────────────────────────────

def _detect_tests(repo_dir: Path, all_files: list[Path]) -> tuple[bool, int]:
    """
    Count test files:
    - any file whose relative path has a parent component in _TEST_DIR_NAMES
    - any file whose name matches _TEST_FILE_RE
    """
    test_files: set[Path] = set()
    for f in all_files:
        rel = f.relative_to(repo_dir)
        parts = rel.parts
        # file in a test directory?
        if any(p in _TEST_DIR_NAMES for p in parts[:-1]):
            test_files.add(f)
            continue
        # test file name pattern?
        if _TEST_FILE_RE.match(f.name):
            test_files.add(f)

    count = len(test_files)
    return count > 0, count


# ── CI ─────────────────────────────────────────────────────────────────────────

def _detect_ci(repo_dir: Path) -> list[str]:
    """Collect CI config file paths (relative to repo root), sorted."""
    found: set[str] = set()

    for ci_path in _CI_EXACT:
        if (repo_dir / ci_path).exists():
            found.add(ci_path)

    # .github/workflows/*.yml|*.yaml
    wf_dir = repo_dir / ".github" / "workflows"
    if wf_dir.is_dir():
        for f in wf_dir.iterdir():
            if f.is_file() and f.suffix in (".yml", ".yaml"):
                found.add(_rel(f, repo_dir))

    return sorted(found)


# ── linters ────────────────────────────────────────────────────────────────────

def _detect_linters(repo_dir: Path) -> list[str]:
    """Collect linter/formatter config names, sorted."""
    found: set[str] = set()

    for name in _LINTER_EXACT:
        if (repo_dir / name).exists():
            found.add(name)

    # pyproject.toml: check for [tool.<linter>] sections
    ppt = repo_dir / "pyproject.toml"
    if ppt.exists():
        try:
            content = ppt.read_text(encoding="utf-8", errors="ignore")
            for tool in _PYPROJECT_LINTER_TOOLS:
                # match [tool.ruff] or [tool.ruff.xxx]
                if re.search(rf"\[tool\.{re.escape(tool)}[\].]", content):
                    found.add(f"pyproject.toml:[tool.{tool}]")
        except OSError:
            pass

    return sorted(found)


# ── license / changelog ────────────────────────────────────────────────────────

def _detect_license(repo_dir: Path) -> bool:
    for name in (
        "LICENSE", "LICENSE.txt", "LICENSE.md", "LICENSE.rst",
        "LICENCE", "LICENCE.txt",
        "COPYING", "COPYING.txt", "COPYING.md",
    ):
        if (repo_dir / name).exists():
            return True
    return False


def _detect_changelog(repo_dir: Path) -> bool:
    for name in (
        "CHANGELOG", "CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG.txt",
        "CHANGES", "CHANGES.md", "CHANGES.txt",
        "HISTORY", "HISTORY.md", "HISTORY.rst",
        "RELEASES", "RELEASES.md",
        "NEWS", "NEWS.md",
    ):
        if (repo_dir / name).exists():
            return True
    return False


# ── version / semver ───────────────────────────────────────────────────────────

def _detect_versions(repo_dir: Path) -> list[str]:
    """
    Extract version strings (semver-like) from manifests and CHANGELOG headings.
    Also tries `git tag --list` on the local clone (safe read-only, P-H07).
    Returns sorted unique list, capped at 10.
    """
    found: set[str] = set()

    # Manifest version fields
    for manifest in ("Cargo.toml", "pyproject.toml", "package.json"):
        _extract_versions_from_file(repo_dir / manifest, found)

    # CHANGELOG headings (first 80 lines)
    for name in ("CHANGELOG.md", "CHANGELOG", "CHANGES.md", "HISTORY.md"):
        _extract_versions_from_file(repo_dir / name, found, max_lines=80)

    # git tag --list (shallow clone with --no-tags will return nothing; safe to try)
    try:
        r = subprocess.run(
            ["git", "tag", "--list"],
            cwd=repo_dir,
            capture_output=True, text=True,
            timeout=5,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                tag = line.strip()
                if tag and _SEMVER_RE.search(tag):
                    found.add(tag)
    except Exception:
        pass

    return sorted(found)[:10]


def _extract_versions_from_file(
    path: Path, found: set[str], max_lines: int = 0
) -> None:
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        if max_lines:
            lines = lines[:max_lines]
        for line in lines:
            for m in _SEMVER_RE.finditer(line):
                v = m.group(0).lstrip("v")
                if v.count(".") >= 2:   # require at least x.y.z
                    found.add(v)
    except OSError:
        pass


# ── dep manifests ──────────────────────────────────────────────────────────────

def _detect_manifests(repo_dir: Path) -> list[str]:
    """List dep manifest files found at repo root, sorted."""
    return sorted(
        name for name in _DEP_MANIFESTS_EXACT
        if (repo_dir / name).exists()
    )


# ── language breakdown ─────────────────────────────────────────────────────────

def _compute_lang_breakdown(
    all_files: list[Path], repo_dir: Path
) -> dict[str, float]:
    """
    Return extension → percentage of source files (files with _SOURCE_EXTS).
    e.g. {".rs": 82.0, ".py": 12.0, ".js": 6.0}
    """
    ext_count: dict[str, int] = {}
    total = 0
    for f in all_files:
        ext = f.suffix.lower()
        if ext in _SOURCE_EXTS:
            ext_count[ext] = ext_count.get(ext, 0) + 1
            total += 1
    if total == 0:
        return {}
    return {
        ext: round(count / total * 100, 1)
        for ext, count in sorted(ext_count.items(), key=lambda kv: -kv[1])
    }


# ── SLOC ───────────────────────────────────────────────────────────────────────

def _compute_sloc(repo_dir: Path, all_files: list[Path]) -> int:
    """
    Estimate source lines of code.
    Tries tokei first (fast, accurate); falls back to counting non-blank lines
    in text files (conservative but always works).
    """
    # try tokei
    try:
        r = subprocess.run(
            ["tokei", str(repo_dir), "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            total = 0
            for lang_data in data.values():
                if isinstance(lang_data, dict) and "code" in lang_data:
                    total += lang_data.get("code", 0)
            if total > 0:
                return total
    except (FileNotFoundError, subprocess.TimeoutExpired,
            json.JSONDecodeError, OSError):
        pass

    # fallback: sum non-blank lines across text files
    total = 0
    for f in all_files:
        if f.suffix.lower() not in _TEXT_EXTS:
            continue
        if f.name in _SEAM_OWN_FILES:   # skip our own metadata files
            continue
        try:
            # read in binary mode and split on newlines — handles mixed encodings
            raw = f.read_bytes()
            for line in raw.split(b"\n"):
                if line.strip():
                    total += 1
        except OSError:
            pass
    return total


# ── README ─────────────────────────────────────────────────────────────────────

def _read_readme(repo_dir: Path) -> tuple[int, str]:
    """
    Return (readme_len_in_chars, readme_snippet).
    Snippet is truncated to _README_TRUNCATE chars (P-S05).
    """
    for name in (
        "README.md", "README.MD",
        "README.rst", "README.txt",
        "README.adoc", "README",
    ):
        p = repo_dir / name
        if p.exists() and p.is_file():
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                return len(text), text[:_README_TRUNCATE]
            except OSError:
                pass
    return 0, ""


# ── sample files ───────────────────────────────────────────────────────────────

def _pick_sample_files(repo_dir: Path, all_files: list[Path]) -> list[str]:
    """
    Pick ≤_MAX_SAMPLE_FILES representative source files.
    Strategy: source files not in test/generated dirs, sorted by size desc.
    Paths are relative to repo_dir.
    """
    _skip_sample_parts = _TEST_DIR_NAMES | {
        "generated", "vendor", "third_party", "thirdparty", "extern",
        "fixtures", "testdata", "snapshots",
    }

    candidates: list[tuple[int, str]] = []
    for f in all_files:
        if f.suffix.lower() not in _SOURCE_EXTS:
            continue
        rel = f.relative_to(repo_dir)
        parts = rel.parts
        # skip test / generated dirs
        if any(p in _skip_sample_parts for p in parts[:-1]):
            continue
        # skip test files by name pattern
        if _TEST_FILE_RE.match(f.name):
            continue
        # skip seam metadata
        if f.name in _SEAM_OWN_FILES:
            continue
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        candidates.append((size, str(rel)))

    candidates.sort(reverse=True)
    return [path for _, path in candidates[:_MAX_SAMPLE_FILES]]
