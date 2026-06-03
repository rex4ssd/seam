from __future__ import annotations
import sys
from datetime import date
from pathlib import Path

import click

from ..core.config import SeamConfig, load_config
from ..core.models import Pick
from ..core.store import append_picks, seen_ids
from ..score.heuristic import score_heuristic
from ..score.ollama import ollama_available, score_ollama
from ..sources.github import search_github


# ── programmatic API (used by seam_harvest_entry) ─────────────────────────────

def run_pipeline(
    cfg: SeamConfig,
    n_picks: int | None = None,
    engine: str = "auto",
    cooldown: bool = True,
    save: bool = True,
    verbose: bool = False,
) -> list[Pick]:
    """
    Execute search → score → filter → pick.
    Returns list of Pick (may be empty).
    Does NOT print anything — callers handle output.
    """
    n = n_picks or cfg.picks_per_day
    min_score = cfg.scoring.get("min_score", 60)
    gh = cfg.github

    candidates = search_github(
        queries=gh["queries"],
        token=cfg.github_token,
        min_stars=gh.get("min_stars", 200),
        max_age_days=gh.get("max_age_days", 90),
        per_query=30,
    )
    if not candidates:
        return []

    if engine == "auto":
        use_ollama = ollama_available(cfg.ollama_base_url)
    else:
        use_ollama = engine == "ollama"

    scored = (
        score_ollama(candidates, cfg, verbose=verbose)
        if use_ollama
        else score_heuristic(candidates, cfg)
    )

    scored = [s for s in scored if s.score >= min_score]
    if cooldown:
        cold = seen_ids()
        scored = [s for s in scored if s.candidate.id not in cold]

    if not scored:
        return []

    today = date.today().isoformat()
    picks = [Pick(rank=i + 1, scored=s, date=today) for i, s in enumerate(scored[:n])]

    if save:
        append_picks(picks)

    return picks


# ── Click command (thin wrapper around run_pipeline) ──────────────────────────

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

    if not pipe:
        click.echo("[seam] searching GitHub …", err=True)

    picks = run_pipeline(
        cfg,
        n_picks=n_picks,
        engine=engine,
        cooldown=not no_cooldown,
        save=not no_save,
        verbose=verbose,
    )

    if not picks:
        click.echo("[seam] no picks found", err=True)
        sys.exit(0)

    today = date.today().isoformat()
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
