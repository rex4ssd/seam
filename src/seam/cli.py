import click

from .commands.init import cmd_init
from .commands.search import cmd_search
from .commands.score import cmd_score
from .commands.pick import cmd_pick
from .commands.run import cmd_run
from .commands.log_cmd import cmd_log
from .commands.harvest import cmd_harvest


@click.group()
@click.version_option(version="0.1.0", prog_name="seam")
def cli() -> None:
    """Seam — overnight intelligence prospector.\n
    \b
    Typical usage:
      seam run                          # full pipeline
      seam run --pipe | xargs -I{} vein fetch {}
      seam search | seam score | seam pick --top 3
    """


cli.add_command(cmd_init)
cli.add_command(cmd_search)
cli.add_command(cmd_score)
cli.add_command(cmd_pick)
cli.add_command(cmd_run)
cli.add_command(cmd_log)
cli.add_command(cmd_harvest)
