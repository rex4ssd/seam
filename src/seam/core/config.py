from __future__ import annotations
import os
from pathlib import Path
from typing import Any

import yaml


PROFILE_VERSION = 1

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
