"""seam harvest — Phase 3 CLI command."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from ..core.config import load_config
from ..core.models import Pick
from ..core.store import load_picks, load_index, harvested_keys
from ..harvest.cloner import clone_repos_batch, clone_repo
from ..harvest.layout import assert_target_dir


def _picks_to_clone_list(picks: list[dict]) -> list[tuple[str, str, int]]:
    """Convert pick dicts to (slug, language, stars) tuples."""
    return [
        (p["id"], p.get("language", ""), p.get("stars", 0))
        for p in picks
        if p.get("id")
    ]


@click.command("harvest")
@click.option("--days", default=1, show_default=True,
              help="Look back N days in picks.jsonl for un-harvested repos.")
@click.option("--repo", "single_repo", default=None, metavar="OWNER/REPO",
              help="Harvest a single repo (bypasses picks.jsonl).")
@click.option("--language", default="", help="Language hint for --repo (e.g. rust).")
@click.option("--stars", default=0, type=int, help="Stars hint for --repo.")
@click.option("--dry-run", is_flag=True,
              help="Print what would be cloned without touching disk.")
@click.option("--yes", "auto_update", is_flag=True,
              help="Allow destructive in-place update (fetch + reset --hard) "
                   "of already-cloned repos. Default: skip updated repos.")
@click.option("--pipe-vein", is_flag=True,
              help="Output 'owner/repo --tag ...' lines suitable for vein fetch.")
@click.option("--json", "as_json", is_flag=True,
              help="Output CloneResult JSON lines.")
@click.option("--profile", "profile_path", default=None,
              type=click.Path(exists=True))
@click.option("--verbose", is_flag=True)
def cmd_harvest(
    days: int,
    single_repo: str | None,
    language: str,
    stars: int,
    dry_run: bool,
    auto_update: bool,
    pipe_vein: bool,
    as_json: bool,
    profile_path: str | None,
    verbose: bool,
) -> None:
    """Clone + archive recently-picked repos to the 2T drive."""
    cfg = load_config(
        None if profile_path is None else Path(profile_path)
    )

    # ── validate target_dir ───────────────────────────────────────────────
    try:
        target_dir = cfg.harvest_target_dir()
    except ValueError as exc:
        click.echo(f"[seam harvest] error: {exc}", err=True)
        sys.exit(1)

    if not dry_run:
        try:
            assert_target_dir(target_dir)
        except ValueError as exc:
            click.echo(f"[seam harvest] error: {exc}", err=True)
            sys.exit(1)

    hcfg = cfg.harvest

    # ── build slug list ───────────────────────────────────────────────────
    if single_repo:
        slugs = [(single_repo, language, stars)]
    else:
        picks = load_picks(days=days)
        if not picks:
            click.echo("[seam harvest] no picks found in the last "
                       f"{days} day(s).", err=True)
            sys.exit(0)

        # filter out already-harvested (same commit not required for re-harvest check)
        done = {k[0] for k in harvested_keys(target_dir)} if target_dir.exists() else set()
        pending = [p for p in picks if p.get("id") not in done]

        if not pending and not dry_run:
            click.echo("[seam harvest] all recent picks already harvested.", err=True)
            sys.exit(0)

        slugs = _picks_to_clone_list(pending or picks[:hcfg.max_repos_per_night])

    # ── run ───────────────────────────────────────────────────────────────
    if dry_run:
        click.echo(f"[seam harvest] dry-run — {len(slugs)} repo(s) queued:", err=True)
        for slug, lang, s in slugs:
            lang_folder = hcfg.map_language(lang) if lang else "_unknown"
            dest = target_dir / lang_folder / slug.replace("/", "__")
            click.echo(f"  {slug}  →  {dest}")
        sys.exit(0)

    results = clone_repos_batch(slugs, target_dir, hcfg,
                                dry_run=False, verbose=verbose,
                                auto_update=auto_update)

    # ── output ────────────────────────────────────────────────────────────
    for r in results:
        if r.skipped:
            continue
        if pipe_vein:
            tags = _build_tags(r)
            print(f"{r.slug} " + " ".join(f"--tag {t}" for t in tags))
        elif as_json:
            print(json.dumps({
                "slug": r.slug, "commit": r.commit,
                "language": r.language, "clone_path": r.clone_path,
                "skipped": r.skipped, "skipped_reason": r.skipped_reason,
            }))
        else:
            _print_summary(results)
            return

    if not pipe_vein and not as_json:
        _print_summary(results)


def _build_tags(r) -> list[str]:
    """Minimal tags for vein handoff (Phase 3.4 will enrich with strength tags)."""
    tags = [f"lang:{r.language}", f"seam-cloned:{r.commit[:8] if r.commit else 'unknown'}"]
    return tags


def _print_summary(results) -> None:
    ok = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]
    click.echo(f"\n── Harvest results " + "─" * 32)
    for r in ok:
        click.echo(f"  ✓  {r.slug}  ({r.language})  commit={r.commit[:8]}")
    for r in skipped:
        click.echo(f"  –  {r.slug}  skipped: {r.skipped_reason}")
    click.echo(f"\n  {len(ok)} cloned, {len(skipped)} skipped")
