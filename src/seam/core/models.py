from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Candidate:
    source: str          # "github"
    id: str              # "owner/repo"
    title: str
    description: str     # README snippet / repo description
    stars: int
    url: str
    pushed_at: str       # ISO datetime string
    language: str
    topics: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: int           # 0–100
    reason: str          # one-line explanation
    dimensions: dict[str, int] = field(default_factory=dict)
    # e.g. {"target_relevance": 82, "solo_feasible": 71}


@dataclass
class Pick:
    rank: int
    scored: ScoredCandidate
    date: str            # YYYY-MM-DD

    def to_dict(self) -> dict[str, Any]:
        c = self.scored.candidate
        return {
            "rank": self.rank,
            "date": self.date,
            "source": c.source,
            "id": c.id,
            "title": c.title,
            "stars": c.stars,
            "url": c.url,
            "language": c.language,
            "topics": c.topics,
            "score": self.scored.score,
            "reason": self.scored.reason,
            "dimensions": self.scored.dimensions,
        }


# ── Phase 3: harvest models ───────────────────────────────────────────────────

@dataclass
class CloneResult:
    slug: str                    # "owner/repo"
    clone_path: str              # absolute path on 2T
    commit: str                  # HEAD sha after clone
    language: str                # primary language folder name (e.g. "rust")
    skipped: bool = False
    skipped_reason: str = ""     # "size", "disk_full", "timeout", "exists_same_commit"
    size_kb: int = 0


@dataclass
class RepoSignals:
    slug: str
    has_tests: bool = False
    test_file_count: int = 0
    ci_files: list[str] = field(default_factory=list)      # e.g. [".github/workflows/ci.yml"]
    linter_cfgs: list[str] = field(default_factory=list)   # e.g. ["rustfmt.toml", ".ruff.toml"]
    has_license: bool = False
    has_changelog: bool = False
    semver_tags: list[str] = field(default_factory=list)   # from .seam-meta or GH API
    dep_manifests: list[str] = field(default_factory=list) # e.g. ["Cargo.toml", "pyproject.toml"]
    lang_breakdown: dict[str, float] = field(default_factory=dict)  # ext → % of files
    sloc: int = 0
    readme_len: int = 0          # chars
    readme_snippet: str = ""     # first 1500 chars (P-S05)
    sample_files: list[str] = field(default_factory=list)  # ≤8 representative file paths


@dataclass
class StrengthReport:
    candidate_id: str            # "owner/repo"
    language: str                # primary language
    languages: dict[str, float]  # {"Rust": 82.0, "Python": 12.0} by file ext
    clone_path: str              # absolute path on 2T
    commit: str                  # HEAD sha at analysis time
    sloc: int
    stars: int
    strength_tags: list[str]     # taxonomy tags that passed threshold
    dimensions: dict[str, int]   # 5-dim scores 0–100
    summary: str                 # 2–3 sentence ollama summary
    evidence: dict[str, list[str]]   # tag → evidence file/signal list
    analyzed_at: str             # ISO datetime
    profile_version: str         # hash of profile.yaml at analysis time (P-S07)
    engine: str = "ollama"       # "ollama" or "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "language": self.language,
            "languages": self.languages,
            "clone_path": self.clone_path,
            "commit": self.commit,
            "sloc": self.sloc,
            "stars": self.stars,
            "strength_tags": self.strength_tags,
            "dimensions": self.dimensions,
            "summary": self.summary,
            "evidence": self.evidence,
            "analyzed_at": self.analyzed_at,
            "profile_version": self.profile_version,
            "engine": self.engine,
        }
