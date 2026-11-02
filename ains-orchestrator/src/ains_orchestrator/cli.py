"""`ains` command line. I1 skeleton: command groups exist, bodies arrive with I6 to I12."""

from __future__ import annotations

import typer

from ains_orchestrator import __version__

app = typer.Typer(no_args_is_help=True, help="AINS orchestrator: Jira stories to merged PRs through stage agents and gates.")
agent_app = typer.Typer(no_args_is_help=True, help="Stage agent templates.")
flow_app = typer.Typer(no_args_is_help=True, help="Flow definitions (stages and gates).")
targets_app = typer.Typer(no_args_is_help=True, help="Onboarded projects (targets.yaml).")
session_app = typer.Typer(no_args_is_help=True, help="Story sessions.")
target_app = typer.Typer(no_args_is_help=True, help="Project onboarding: add or remove a target.")

app.add_typer(agent_app, name="agent")
app.add_typer(flow_app, name="flow")
app.add_typer(targets_app, name="targets")
app.add_typer(session_app, name="session")
app.add_typer(target_app, name="target")

NOT_YET = "not implemented yet (see plan section 9)"


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Print the version and exit.", is_eager=True),
) -> None:
    if version:
        typer.echo(f"ains-orchestrator {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@agent_app.command("list")
def agent_list() -> None:
    """List stage agent templates."""
    typer.echo(NOT_YET)


@flow_app.command("list")
def flow_list() -> None:
    """List flows."""
    typer.echo(NOT_YET)


@targets_app.command("list")
def targets_list() -> None:
    """List onboarded targets."""
    typer.echo(NOT_YET)


@session_app.command("dispatch")
def session_dispatch(key: str, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Start or continue the flow for one story key."""
    typer.echo(NOT_YET)


@session_app.command("advance")
def session_advance(key: str, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Advance one story by one stage if its gate allows."""
    typer.echo(NOT_YET)


@session_app.command("status")
def session_status(key: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """Show a story's stage, gate state and event trace."""
    typer.echo(NOT_YET)


@target_app.command("add")
def target_add(repo: str, jira_key: str, jira_name: str, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Onboard a project: Jira project, GitHub repo, protection, environments, secrets, templates."""
    typer.echo(NOT_YET)


@target_app.command("remove")
def target_remove(repo: str, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Reverse `target add` for one project."""
    typer.echo(NOT_YET)


@app.command()
def poll(once: bool = typer.Option(False, "--once"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Poll Jira and the target repos; claim stories and advance sessions."""
    typer.echo(NOT_YET)


@app.command()
def doctor() -> None:
    """Check tokens, gh, claude, specify and the environment."""
    typer.echo(NOT_YET)
