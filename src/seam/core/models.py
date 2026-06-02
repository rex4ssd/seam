from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


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
            "score": self.scored.score,
            "reason": self.scored.reason,
            "dimensions": self.scored.dimensions,
        }
