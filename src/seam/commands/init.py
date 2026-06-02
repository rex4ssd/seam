from __future__ import annotations
from pathlib import Path

import click
import yaml

from ..core.config import DEFAULT_PROFILE


@click.command("init")
@click.option("--dir", "target_dir", default=".", show_default=True,
              help="Directory to initialise (creates .seam/ inside it).")
@click.option("--force", is_flag=True, help="Overwrite existing profile.yaml.")
def cmd_init(target_dir: str, force: bool) -> None:
    """Create .seam/profile.yaml scaffold in the current (or given) directory."""
    seam_dir = Path(target_dir).resolve() / ".seam"
    seam_dir.mkdir(parents=True, exist_ok=True)

    profile_path = seam_dir / "profile.yaml"
    if profile_path.exists() and not force:
        click.echo(f"Already exists: {profile_path}  (use --force to overwrite)")
        return

    with open(profile_path, "w") as f:
        yaml.dump(DEFAULT_PROFILE, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    click.echo(f"Created: {profile_path}")
    click.echo("Edit profile.yaml to set your interests, queries, and GITHUB_TOKEN.")
