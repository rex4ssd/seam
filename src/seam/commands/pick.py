from __future__ import annotations
import json
import sys
from datetime import date

import click

from ..core.config import load_config
from ..core.models import Candidate, ScoredCandidate, Pick
from ..core.store import seen_ids


def _scored_from_stdin() -> list[ScoredCandidate]:
    results = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        c = Candidate(
            source=row.get("source", "github"),
            id=row["id"],
            title=row.get("title", row["id"]),
            description="",
            stars=row.get("stars", 0),
            url=row.get("url", ""),
            pushed_at="",
            language="",
            topics=[],
        )
        results.append(ScoredCandidate(
            candidate=c,
            score=row.get("score", 0),
            reason=row.get("reason", ""),
            dimensions=row.get("dimensions", {}),
        ))
    return results


@click.command("pick")
@click.option("--top", default=None, type=int, help="Number of picks (overrides profile).")
@click.option("--profile", "profile_path", default=None,
              type=click.Path(exists=True), help="Path to profile.yaml.")
@click.option("--no-cooldown", is_flag=True, help="Ignore 14-day cooldown per repo.")
@click.option("--pipe", is_flag=True, help="Output owner/repo lines only.")
def cmd_pick(top: int | None, profile_path: str | None, no_cooldown: bool, pipe: bool) -> None:
    """Read scored JSON from stdin, print top-N picks."""
    cfg = load_config(None if profile_path is None else __import__("pathlib").Path(profile_path))
    n = top or cfg.picks_per_day
    min_score = cfg.scoring.get("min_score", 60)

    scored = _scored_from_stdin()
    if not scored:
        print("[seam] no scored candidates on stdin", file=sys.stderr)
        return

    # filter by min_score
    scored = [s for s in scored if s.score >= min_score]

    # cooldown filter (P-S04)
    if not no_cooldown:
        cold = seen_ids()
        scored = [s for s in scored if s.candidate.id not in cold]

    # top-N
    picks = [
        Pick(rank=i + 1, scored=s, date=date.today().isoformat())
        for i, s in enumerate(scored[:n])
    ]

    if not picks:
        print("[seam] no picks after filters", file=sys.stderr)
        return

    if pipe:
        for p in picks:
            print(p.scored.candidate.id)
    else:
        today = date.today().isoformat()
        click.echo(f"── Seam Picks ({today}) " + "─" * 30)
        for p in picks:
            c = p.scored.candidate
            click.echo(f" {p.rank}. [{c.source}] {c.title}  ★{c.stars:,}  score={p.scored.score}/100")
            click.echo(f"    {c.url}")
            click.echo(f"    Why: {p.scored.reason}")
        click.echo("─" * 50)

    print(f"[seam] {len(picks)} picks", file=sys.stderr)
