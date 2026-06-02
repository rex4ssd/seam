from __future__ import annotations
import sys
from datetime import date
from pathlib import Path

import click

from ..core.config import load_config
from ..core.models import Pick
from ..core.store import append_picks, seen_ids
from ..score.heuristic import score_heuristic
from ..score.ollama import ollama_available, score_ollama
from ..sources.github import search_github


@click.command("run")
@click.option("--picks", "n_picks", default=None, type=int,
              help="Override picks_per_day from profile.")
@click.option("--source", default="github", show_default=True,
              type=click.Choice(["github"]), help="Source (github only in Phase 0).")
@click.option("--pipe", is_flag=True, help="Output owner/repo lines only (for piping to vein).")
@click.option("--no-save", is_flag=True, help="Don't write to picks.jsonl.")
@click.option("--no-cooldown", is_flag=True, help="Ignore 14-day cooldown.")
@click.option("--engine", default="auto", show_default=True,
              type=click.Choice(["auto", "ollama", "heuristic"]))
@click.option("--profile", "profile_path", default=None,
              type=click.Path(exists=True), help="Path to profile.yaml.")
@click.option("--verbose", is_flag=True)
def cmd_run(
    n_picks: int | None,
    source: str,
    pipe: bool,
    no_save: bool,
    no_cooldown: bool,
    engine: str,
    profile_path: str | None,
    verbose: bool,
) -> None:
    """Full pipeline: search → score → pick → print (and save)."""
    cfg = load_config(None if profile_path is None else Path(profile_path))
    n = n_picks or cfg.picks_per_day
    min_score = cfg.scoring.get("min_score", 60)
    gh = cfg.github

    # ── 1. Search ────────────────────────────────────────────────────────
    if not pipe:
        click.echo("[seam] searching GitHub …", err=True)
    candidates = search_github(
        queries=gh["queries"],
        token=cfg.github_token,
        min_stars=gh.get("min_stars", 200),
        max_age_days=gh.get("max_age_days", 90),
        per_query=30,
    )
    if not candidates:
        click.echo("[seam] no candidates found", err=True)
        sys.exit(0)
    if verbose:
        click.echo(f"[seam] {len(candidates)} candidates after search", err=True)

    # ── 2. Score ─────────────────────────────────────────────────────────
    if engine == "auto":
        use_ollama = ollama_available(cfg.ollama_base_url)
        if not use_ollama and verbose:
            click.echo(f"[seam] ollama not available, using heuristic", err=True)
    else:
        use_ollama = engine == "ollama"

    if use_ollama:
        if not pipe:
            click.echo(f"[seam] scoring with {cfg.score_model} …", err=True)
        scored = score_ollama(candidates, cfg, verbose=verbose)
    else:
        scored = score_heuristic(candidates, cfg)

    # ── 3. Filter + cooldown ─────────────────────────────────────────────
    scored = [s for s in scored if s.score >= min_score]
    if not no_cooldown:
        cold = seen_ids()
        scored = [s for s in scored if s.candidate.id not in cold]

    if not scored:
        click.echo("[seam] no picks after filters", err=True)
        sys.exit(0)

    # ── 4. Pick top-N ────────────────────────────────────────────────────
    today = date.today().isoformat()
    picks = [
        Pick(rank=i + 1, scored=s, date=today)
        for i, s in enumerate(scored[:n])
    ]

    # ── 5. Output ────────────────────────────────────────────────────────
    if pipe:
        for p in picks:
            print(p.scored.candidate.id)
    else:
        click.echo(f"\n── Seam Picks ({today}) " + "─" * 30)
        for p in picks:
            c = p.scored.candidate
            click.echo(f" {p.rank}. [{c.source}] {c.title}  ★{c.stars:,}  score={p.scored.score}/100")
            click.echo(f"    {c.url}")
            click.echo(f"    Why: {p.scored.reason}")
        click.echo("─" * 50)

    # ── 6. Save ──────────────────────────────────────────────────────────
    if not no_save:
        append_picks(picks)
        if verbose:
            click.echo(f"[seam] saved {len(picks)} picks to picks.jsonl", err=True)
