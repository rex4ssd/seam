from __future__ import annotations
import json
import sys

import click

from ..core.config import load_config
from ..core.models import Candidate
from ..score.ollama import ollama_available, score_ollama
from ..score.heuristic import score_heuristic


def _candidates_from_stdin() -> list[Candidate]:
    candidates = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates.append(Candidate(
            source=row.get("source", "github"),
            id=row["id"],
            title=row.get("title", row["id"]),
            description=row.get("description", ""),
            stars=row.get("stars", 0),
            url=row.get("url", ""),
            pushed_at=row.get("pushed_at", ""),
            language=row.get("language", ""),
            topics=row.get("topics", []),
            metadata=row,
        ))
    return candidates


@click.command("score")
@click.option("--profile", "profile_path", default=None,
              type=click.Path(exists=True), help="Path to profile.yaml.")
@click.option("--engine", default="auto", show_default=True,
              type=click.Choice(["auto", "ollama", "heuristic"]),
              help="Scoring engine. auto = try ollama, fall back to heuristic.")
@click.option("--verbose", is_flag=True)
def cmd_score(profile_path: str | None, engine: str, verbose: bool) -> None:
    """Read candidate JSON from stdin, output scored JSON (one object per line)."""
    cfg = load_config(None if profile_path is None else __import__("pathlib").Path(profile_path))
    candidates = _candidates_from_stdin()

    if not candidates:
        print("[seam] no candidates on stdin", file=sys.stderr)
        return

    if engine == "auto":
        use_ollama = ollama_available(cfg.ollama_base_url)
        if not use_ollama:
            print(f"[seam] ollama not available at {cfg.ollama_base_url}, using heuristic", file=sys.stderr)
    else:
        use_ollama = engine == "ollama"

    if use_ollama:
        scored = score_ollama(candidates, cfg, verbose=verbose)
    else:
        scored = score_heuristic(candidates, cfg)

    for s in scored:
        row = {
            "source": s.candidate.source,
            "id": s.candidate.id,
            "title": s.candidate.title,
            "stars": s.candidate.stars,
            "url": s.candidate.url,
            "score": s.score,
            "reason": s.reason,
            "dimensions": s.dimensions,
        }
        print(json.dumps(row, ensure_ascii=False))

    print(f"[seam] scored {len(scored)} candidates", file=sys.stderr)
