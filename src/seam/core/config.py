from __future__ import annotations
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


PROFILE_VERSION = 1

# ── harvest defaults ──────────────────────────────────────────────────────────
DEFAULT_HARVEST: dict[str, Any] = {
    "target_dir": "",          # must be set in profile.yaml; empty → error on harvest
    "max_repos_per_night": 5,
    "retention": "permanent",
    "clone": {
        "depth": 1,
        "single_branch": True,
        "lfs": "skip",
        "size_cap_mb": 500,
        "timeout_sec": 300,
        "min_free_gb": 20,
    },
    "analyze": {
        "tag_threshold": 70,
        "readme_truncate": 1500,
        "sample_files": 8,
        "ollama_timeout_sec": 180,
    },
    "lang_map": {
        "Rust": "rust",
        "Python": "python",
        "Swift": "swift",
        "Go": "go",
        "TypeScript": "typescript",
        "JavaScript": "javascript",
        "C++": "cpp",
        "C": "c",
        "default": "_unknown",
    },
}


@dataclass
class HarvestCfg:
    target_dir: Path
    max_repos_per_night: int
    retention: str
    clone: dict[str, Any]
    analyze: dict[str, Any]
    lang_map: dict[str, str]

    # ── clone shortcuts ──────────────────────────────────────────────────
    @property
    def clone_depth(self) -> int:
        return int(self.clone.get("depth", 1))

    @property
    def clone_timeout_sec(self) -> int:
        return int(self.clone.get("timeout_sec", 300))

    @property
    def size_cap_mb(self) -> int:
        return int(self.clone.get("size_cap_mb", 500))

    @property
    def min_free_gb(self) -> int:
        return int(self.clone.get("min_free_gb", 20))

    # ── analyze shortcuts ────────────────────────────────────────────────
    @property
    def tag_threshold(self) -> int:
        return int(self.analyze.get("tag_threshold", 70))

    @property
    def readme_truncate(self) -> int:
        return int(self.analyze.get("readme_truncate", 1500))

    @property
    def sample_files(self) -> int:
        return int(self.analyze.get("sample_files", 8))

    @property
    def ollama_timeout_sec(self) -> int:
        return int(self.analyze.get("ollama_timeout_sec", 180))

    def map_language(self, github_lang: str) -> str:
        """Map GitHub language name → target folder name."""
        return self.lang_map.get(github_lang, self.lang_map.get("default", "_unknown"))


DEFAULT_PROFILE: dict[str, Any] = {
    "version": PROFILE_VERSION,
    "profile": (
        "Solo developer, Python 5+ years, Rust beginner, React/TypeScript intermediate. "
        "Mac Studio M1 32GB. Main projects: Lode (Tauri 2 desktop app, Rust+React), "
        "Vein (Python CLI, decision lore archive). Available: ~4 weeks per side project. "
        "Goals: improve Lode features, find reusable patterns, learn Rust idioms."
    ),
    "interests": {
        "primary": ["tauri", "macos-app", "rust", "file-diff", "code-search", "desktop-app"],
        "secondary": ["python-cli", "developer-tools", "ai-tooling", "sqlite"],
        "avoid": ["mobile", "kubernetes", "enterprise", "java"],
    },
    "scoring": {
        "target_relevance_weight": 0.6,
        "solo_feasible_weight": 0.4,
        "min_score": 60,
    },
    "picks_per_day": 3,
    "github": {
        "token": "",
        "min_stars": 200,
        "max_age_days": 90,
        "queries": [
            "topic:tauri stars:>200",
            "topic:macos-app language:rust stars:>100",
            "topic:python-cli stars:>500",
            "topic:developer-tools language:python stars:>300",
            "topic:file-diff stars:>100",
            "topic:ai-tooling language:python stars:>200",
            "topic:sqlite language:python stars:>200",
        ],
    },
    "model": {
        "base_url": "http://localhost:11434",
        "score_model": "deepseek-r1:14b",
        "fallback": "heuristic",
    },
    "harvest": DEFAULT_HARVEST,
}


class SeamConfig:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    # ── top-level accessors ──────────────────────────────────────────────
    @property
    def profile_text(self) -> str:
        return self._data.get("profile", "")

    @property
    def interests(self) -> dict[str, list[str]]:
        return self._data.get("interests", {})

    @property
    def scoring(self) -> dict[str, Any]:
        return self._data.get("scoring", DEFAULT_PROFILE["scoring"])

    @property
    def picks_per_day(self) -> int:
        return int(self._data.get("picks_per_day", 3))

    # ── github ───────────────────────────────────────────────────────────
    @property
    def github_token(self) -> str:
        # env var takes priority
        return os.environ.get("GITHUB_TOKEN", "") or self._data.get("github", {}).get("token", "")

    @property
    def github(self) -> dict[str, Any]:
        return self._data.get("github", DEFAULT_PROFILE["github"])

    # ── model ────────────────────────────────────────────────────────────
    @property
    def model(self) -> dict[str, Any]:
        return self._data.get("model", DEFAULT_PROFILE["model"])

    @property
    def ollama_base_url(self) -> str:
        return self.model.get("base_url", "http://localhost:11434")

    @property
    def score_model(self) -> str:
        return self.model.get("score_model", "deepseek-r1:14b")

    @property
    def score_fallback(self) -> str:
        return self.model.get("fallback", "heuristic")

    # ── harvest ──────────────────────────────────────────────────────────
    @property
    def harvest(self) -> HarvestCfg:
        raw = self._data.get("harvest", {})
        # deep-merge with defaults (non-destructive, one level deep for nested dicts).
        # Exception: lang_map is a complete override — if user sets it, their value wins entirely.
        merged: dict[str, Any] = {}
        for k, v in DEFAULT_HARVEST.items():
            if k == "lang_map":
                # full override: user's lang_map replaces defaults completely
                merged[k] = raw.get(k, v)
            elif isinstance(v, dict):
                merged[k] = {**v, **raw.get(k, {})}
            else:
                merged[k] = raw.get(k, v)

        target_raw = merged.get("target_dir", "")
        target_dir = Path(target_raw) if target_raw else Path("")
        return HarvestCfg(
            target_dir=target_dir,
            max_repos_per_night=int(merged.get("max_repos_per_night", 5)),
            retention=str(merged.get("retention", "permanent")),
            clone=merged.get("clone", DEFAULT_HARVEST["clone"]),
            analyze=merged.get("analyze", DEFAULT_HARVEST["analyze"]),
            lang_map=merged.get("lang_map", DEFAULT_HARVEST["lang_map"]),
        )

    def harvest_target_dir(self) -> Path:
        """Return target_dir; raise ValueError if not configured. (per harvest_plan §5)"""
        d = self.harvest.target_dir
        if not d or str(d) == ".":
            raise ValueError(
                "harvest.target_dir is not set in profile.yaml. "
                "Add a valid path (e.g. /Volumes/2T/github_open_project)."
            )
        return d

    def profile_hash(self) -> str:
        """Short hash of the profile text — used as profile_version in StrengthReport (P-S07)."""
        raw = str(self._data.get("profile", "")) + str(self._data.get("interests", ""))
        return hashlib.sha1(raw.encode()).hexdigest()[:8]

    def raw(self) -> dict[str, Any]:
        return self._data


def find_profile(start: Path | None = None) -> Path | None:
    """Walk up from start (or cwd) looking for .seam/profile.yaml."""
    d = (start or Path.cwd()).resolve()
    for parent in [d, *d.parents]:
        p = parent / ".seam" / "profile.yaml"
        if p.exists():
            return p
    return None


def load_config(profile_path: Path | None = None) -> SeamConfig:
    """
    Load config from profile_path, or auto-discover, or use defaults.
    Raises ValueError if version mismatch.
    """
    if profile_path is None:
        profile_path = find_profile()

    if profile_path is None:
        return SeamConfig(DEFAULT_PROFILE.copy())

    with open(profile_path) as f:
        data = yaml.safe_load(f) or {}

    version = data.get("version")
    if version != PROFILE_VERSION:
        raise ValueError(
            f"profile.yaml version {version!r} not supported (expected {PROFILE_VERSION}). "
            f"Run `seam init --upgrade` to migrate."
        )

    # merge missing top-level keys from defaults (non-destructive)
    for k, v in DEFAULT_PROFILE.items():
        data.setdefault(k, v)

    return SeamConfig(data)
