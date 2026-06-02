from __future__ import annotations

import click

from ..core.store import load_picks


@click.command("log")
@click.option("--days", default=7, show_default=True, help="How many days back to show.")
@click.option("--date", "pick_date", default=None, help="Show picks for a specific date (YYYY-MM-DD).")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON lines.")
def cmd_log(days: int, pick_date: str | None, as_json: bool) -> None:
    """Show past picks from picks.jsonl."""
    import json as _json

    if pick_date:
        picks = load_picks(since=__import__("datetime").date.fromisoformat(pick_date),
                           until=__import__("datetime").date.fromisoformat(pick_date))
    else:
        picks = load_picks(days=days)

    if not picks:
        click.echo("No picks found.")
        return

    if as_json:
        for p in picks:
            click.echo(_json.dumps(p, ensure_ascii=False))
        return

    current_date = None
    for p in sorted(picks, key=lambda x: (x.get("date", ""), x.get("rank", 0))):
        d = p.get("date", "?")
        if d != current_date:
            current_date = d
            click.echo(f"\n── {d} " + "─" * 40)
        click.echo(f" {p.get('rank','?')}. {p.get('title', p.get('id','?'))}  ★{p.get('stars',0):,}  score={p.get('score','?')}/100")
        click.echo(f"    {p.get('url','')}")
        click.echo(f"    Why: {p.get('reason','')}")
