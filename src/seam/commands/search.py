from __future__ import annotations
import json
import sys

import click

from ..core.config import load_config
from ..sources.github import search_github


@click.command("search")
@click.option("--source", default="github", show_default=True,
              type=click.Choice(["github"]), help="Source to query.")
@click.option("--profile", "profile_path", default=None,
              type=click.Path(exists=True), help="Path to profile.yaml.")
@click.option("--limit", default=50, show_default=True, help="Max candidates to return.")
def cmd_search(source: str, profile_path: str | None, limit: int) -> None:
    """Query GitHub, output candidate JSON (one object per line)."""
    cfg = load_config(None if profile_path is None else __import__("pathlib").Path(profile_path))
    gh = cfg.github

    candidates = search_github(
        queries=gh["queries"],
        token=cfg.github_token,
        min_stars=gh.get("min_stars", 200),
        max_age_days=gh.get("max_age_days", 90),
        per_query=30,
    )[:limit]

    for c in candidates:
        row = {
            "source": c.source,
            "id": c.id,
            "title": c.title,
            "description": c.description,
            "stars": c.stars,
            "url": c.url,
            "pushed_at": c.pushed_at,
            "language": c.language,
            "topics": c.topics,
        }
        print(json.dumps(row, ensure_ascii=False))

    print(f"[seam] {len(candidates)} candidates", file=sys.stderr)
