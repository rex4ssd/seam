from __future__ import annotations
import math

from ..core.config import SeamConfig
from ..core.models import Candidate, ScoredCandidate


def _keyword_score(text: str, keywords: list[str]) -> float:
    """0.0–1.0 fraction of keywords found in text (case-insensitive)."""
    if not keywords:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in text_lower)
    return hits / len(keywords)


def _star_score(stars: int) -> int:
    """Log-scale 0–100. 100 stars→~46, 1000→~69, 10000→~92, 100000→~100."""
    if stars <= 0:
        return 0
    return min(100, int(math.log10(stars + 1) / math.log10(100_001) * 100))


def score_heuristic(candidates: list[Candidate], cfg: SeamConfig) -> list[ScoredCandidate]:
    interests = cfg.interests
    primary = interests.get("primary", [])
    secondary = interests.get("secondary", [])
    avoid = interests.get("avoid", [])
    w_rel = cfg.scoring.get("target_relevance_weight", 0.6)
    w_sol = cfg.scoring.get("solo_feasible_weight", 0.4)

    results = []
    for c in candidates:
        blob = f"{c.title} {c.description} {' '.join(c.topics)} {c.language}"

        # penalise avoid topics
        if any(kw.lower() in blob.lower() for kw in avoid):
            continue

        primary_hit = _keyword_score(blob, primary)
        secondary_hit = _keyword_score(blob, secondary)
        # Each primary keyword hit = 25 pts; each secondary = 10 pts (capped at 100)
        hits_p = sum(1 for kw in primary if kw.lower() in blob.lower())
        hits_s = sum(1 for kw in secondary if kw.lower() in blob.lower())
        target_relevance = min(100, hits_p * 25 + hits_s * 10)

        star_s = _star_score(c.stars)
        # solo_feasible: star popularity + primary hit bonus
        solo_feasible = min(100, int(star_s * 0.8 + hits_p * 5))

        final = int(target_relevance * w_rel + solo_feasible * w_sol)

        reason_parts = []
        if primary_hit > 0:
            matched = [kw for kw in primary if kw.lower() in blob.lower()]
            reason_parts.append(f"matches {', '.join(matched[:3])}")
        reason_parts.append(f"★{c.stars:,}")
        reason = "; ".join(reason_parts) or "star count only"

        results.append(
            ScoredCandidate(
                candidate=c,
                score=final,
                reason=reason,
                dimensions={"target_relevance": target_relevance, "solo_feasible": solo_feasible},
            )
        )

    results.sort(key=lambda s: s.score, reverse=True)
    return results
