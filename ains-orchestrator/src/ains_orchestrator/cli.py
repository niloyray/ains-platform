"""`ains` command line (I12). Wires config, clients, store, workspace, agents, poller and watcher."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from ains_orchestrator import __version__
from ains_orchestrator.agent import ClaudeCodeRunner, DryRunAgents
from ains_orchestrator.config import (
    Config,
    Profile,
    Target,
    gates_for_autonomy,
    load_config,
    load_profiles,
    validate_profile,
)
from ains_orchestrator.errors import AinsError
from ains_orchestrator.flow import FlowEngine, Story, _adf_text
from ains_orchestrator.github import GitHubClient
from ains_orchestrator.jira import JiraClient
from ains_orchestrator.onboard import Onboarder, default_templates_dir, verify_target
from ains_orchestrator.poller import Poller
from ains_orchestrator.store import Session, Store
from ains_orchestrator.watcher import PRWatcher
from ains_orchestrator.workspace import Workspace

# Windows consoles default to cp1252; never let a report line crash the command.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass

log = logging.getLogger("ains.cli")
HELP = "AINS orchestrator: Jira stories to merged PRs through stage agents and gates."
app = typer.Typer(no_args_is_help=True, help=HELP)
agent_app = typer.Typer(no_args_is_help=True, help="Stage agent templates.")
flow_app = typer.Typer(no_args_is_help=True, help="Flow definitions (stages and gates).")
targets_app = typer.Typer(no_args_is_help=True, help="Onboarded projects (targets.yaml).")
session_app = typer.Typer(no_args_is_help=True, help="Story sessions.")
target_app = typer.Typer(no_args_is_help=True, help="Project onboarding: add or remove a target.")
for name, sub in (
    ("agent", agent_app),
    ("flow", flow_app),
    ("targets", targets_app),
    ("session", session_app),
    ("target", target_app),
):
    app.add_typer(sub, name=name)

NOT_YET = "not implemented yet (see plan section 9)"


# ----------------------------------------------------------------------------- environment
def config_root() -> Path:
    env = os.environ.get("AINS_CONFIG_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for p in here.parents:  # .../ains-orchestrator/src/ains_orchestrator/cli.py -> ains-orchestrator
        if (p / "targets.yaml").exists():
            return p
    return Path("/app")


def load_dotenv(root: Path) -> None:
    """Load KEY=VALUE lines from <root>/.env into os.environ without overriding existing variables."""
    p = root / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def work_dir(root: Path) -> Path:
    """Story clones (throwaway)."""
    return Path(os.environ.get("AINS_WORK_DIR", "/work" if os.name != "nt" else str(root / "work")))


def data_dir(root: Path) -> Path:
    """Durable platform data: sessions.sqlite, heartbeat.json, logs/. Defaults to the work dir for older setups."""
    d = Path(os.environ.get("AINS_DATA_DIR") or work_dir(root))
    d.mkdir(parents=True, exist_ok=True)
    _migrate_store(work_dir(root), d)
    return d


def _migrate_store(old: Path, new: Path) -> None:
    """One-time move of the store from the work volume to the data volume (first start after the split)."""
    if old == new or (new / "sessions.sqlite").exists() or not (old / "sessions.sqlite").exists():
        return
    for name in ("sessions.sqlite", "sessions.sqlite-wal", "sessions.sqlite-shm", "heartbeat.json"):
        if (old / name).exists():
            shutil.copy2(old / name, new / name)
    log.warning("store migrated from %s to %s; the old copy is left in place", old, new)


def setup_file_log(data: Path, name: str) -> None:
    """Rotating log under <data>/logs next to the container's stdout log, so it survives `docker compose down`."""
    from logging.handlers import RotatingFileHandler

    logs = data / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(logs / f"{name}.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.formatter.converter = time.gmtime  # type: ignore[union-attr]
        logging.getLogger().addHandler(h)
        # the cycle line goes to stdout for `docker logs` and to the file only, never twice to the console
        cycle_log.propagate = False
        cycle_log.addHandler(h)
    except OSError as e:
        log.warning("file log not available: %s", e)


cycle_log = logging.getLogger("ains.cycle")


def reload_config(rt: Runtime, *consumers: Any) -> bool:
    """Re-read the mounted config files before a cycle so `ains target add` takes effect without a restart.

    On a parse error the previous config stays in force and the error is logged once per change."""
    try:
        cfg = load_config(rt.root)
    except AinsError as e:
        log.warning("config not reloaded, keeping the previous one: %s", e)
        return False
    if cfg == rt.config:
        return False
    rt.config = cfg
    for c in consumers:
        c.config = cfg
    log.info("config reloaded: targets %s", sorted(cfg.targets.targets))
    return True


@dataclass
class Runtime:
    root: Path
    config: Config
    store: Store
    jira: JiraClient
    github: GitHubClient
    workspace: Workspace
    agents: Any
    dry_run: bool

    def engine_for(self, tkey: str) -> FlowEngine:
        target = self.config.targets.targets[tkey]
        flow = self.config.flow_for(target)
        agent = next(iter(self.config.agents.values()))
        return FlowEngine(
            flow=flow,
            target_key=tkey,
            target=target,
            store=self.store,
            jira=self.jira,
            github=self.github,
            workspace=self.workspace,
            agents=self.agents,
            max_attempts=agent.retry.attempts,
        )


def build_runtime(dry_run: bool = False, *, need_jira: bool = True) -> Runtime:
    root = config_root()
    load_dotenv(root)
    try:
        cfg = load_config(root)
    except AinsError as e:
        typer.secho(f"config error: {e}", fg="red", err=True)
        raise typer.Exit(2) from e
    work = work_dir(root)
    store = Store(data_dir(root) / "sessions.sqlite")
    gh = GitHubClient(dry_run=dry_run, bot_login=os.environ.get("AINS_BOT_LOGIN"))
    ws = Workspace(work, gh)
    agent = next(iter(cfg.agents.values()))
    agents: Any = DryRunAgents() if dry_run else ClaudeCodeRunner(agent, root)
    jira: JiraClient | None = None
    if need_jira:
        try:
            jira = JiraClient(
                os.environ.get("JIRA_BASE_URL", ""),
                os.environ.get("JIRA_EMAIL", ""),
                os.environ.get("JIRA_API_TOKEN", ""),
                dry_run=dry_run,
            )
        except AinsError as e:
            typer.secho(str(e), fg="red", err=True)
            raise typer.Exit(2) from e
    return Runtime(root, cfg, store, jira, gh, ws, agents, dry_run)  # type: ignore[arg-type]


def _story(rt: Runtime, key: str) -> Story:
    issue = rt.jira.get_issue(key, ["summary", "description"])
    f = issue.get("fields", {})
    return Story(key, f.get("summary", ""), _adf_text(f.get("description")) if f.get("description") else "")


# ----------------------------------------------------------------------------- root
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


# ----------------------------------------------------------------------------- listings
@agent_app.command("list")
def agent_list() -> None:
    """List stage agent templates and their stages."""
    cfg = load_config(config_root())
    for a in cfg.agents.values():
        typer.echo(
            f"{a.name}: {' '.join(a.command[:2])}  passthrough={','.join(a.env_passthrough)}  "
            f"retry={a.retry.attempts}x/{a.retry.backoff_seconds}s  limit={a.wall_clock_limit_minutes}min"
        )
        for stage, sc in a.stages.items():
            ro = " read-only" if sc.read_only else ""
            typer.echo(f"  {stage:<10} prompt={sc.prompt} mode={sc.permission_mode} max_turns={sc.max_turns}{ro}")


@flow_app.command("list")
def flow_list() -> None:
    """List flows with stages and gates."""
    cfg = load_config(config_root())
    for f in cfg.flows.values():
        typer.echo(f"{f.name}: {' -> '.join(f.stages)}")
        for g, gd in f.gates.items():
            typer.echo(
                f"  {g}: after {gd.after} at {gd.where} approve={gd.approve or gd.name} rework={gd.rework or '-'}"
            )


@targets_app.command("list")
def targets_list() -> None:
    """List onboarded targets."""
    cfg = load_config(config_root())
    if not cfg.targets.targets:
        typer.echo("(no targets onboarded; use `ains target add`)")
    for k, t in cfg.targets.targets.items():
        gates = " ".join(f"{g}={v}" for g, v in t.gates.items())
        typer.echo(
            f"{k}: {t.repo} ({t.default_branch}) flow={t.flow} approval={t.approval_mode} "
            f"review_label={t.review_label} {gates}"
        )


# ----------------------------------------------------------------------------- status (what is a human waiting to do?)
def next_action(s: Session, target: Target | None, jira_status: str | None = None) -> str:
    """The one thing a person can do for this story right now, in their words."""
    if s.stage == "released" and jira_status == "Done":
        return "Done: released through G4 and G5; nothing to do"
    if s.stage == "released" and jira_status == "Staging":
        return (
            "G5: in GitHub Actions open the release run, Review deployments, approve production; Jira then shows Done"
        )
    pr = f"PR #{s.pr_number}" if s.pr_number else "the PR"
    label_mode = target is None or target.approval_mode == "label"
    approve = f"add label {target.approval_label if target else 'g3-approved'}" if label_mode else "approve the PR"
    rework = (
        f"comment and add label {target.rework_label if target else 'g3-rework'}" if label_mode else "request changes"
    )
    if s.flow == "pr-review":
        return f"human-lane {pr} was reviewed by the agent; merging it is yours"
    return {
        "wait_g1": f"G1: in Jira move {s.key} to Planning (approve) or to Rework with a comment (send back)",
        "wait_g2": f"G2: in Jira move {s.key} to Implementing (approve) or to Rework with a comment (send back)",
        "wait_g3": f"G3: on {pr} check that ci / ci is green, read the review, then {approve} (or {rework})",
        "released": f"G4/G5: in GitHub Actions open the release run for {pr}, Review deployments, approve staging "
        "then production; Jira then shows Staging and Done. Nothing else is left on the platform side",
        "blocked": f"Blocked: {s.last_error or 'see the Jira comment'}. Fix the cause, then move {s.key} to Ready",
        "abandoned": "abandoned; nothing to do",
    }.get(s.stage, f"platform is working on stage {s.stage}; nothing to do until it waits")


@app.command()
def status(as_json: bool = typer.Option(False, "--json")) -> None:
    """Every story the platform knows, its stage, and what a person must do next."""
    rt = build_runtime(need_jira=False)
    sessions = sorted(rt.store.list_sessions(), key=lambda x: x.updated, reverse=True)
    jira: JiraClient | None = None  # optional: lets "released" stories report Staging/Done from Jira
    if all(os.environ.get(v) for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")):
        try:
            jira = JiraClient(os.environ["JIRA_BASE_URL"], os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
        except AinsError:
            jira = None

    def jira_status(sess: Session) -> str | None:
        if jira is None or sess.stage != "released" or sess.flow == "pr-review":
            return None
        try:
            return jira.status_of(sess.key)
        except AinsError:
            return None

    if as_json:
        rows = [
            {
                **{k: getattr(s, k) for k in ("key", "target", "stage", "pr_number", "updated")},
                "next": next_action(s, rt.config.targets.targets.get(s.target), jira_status(s)),
            }
            for s in sessions
        ]
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    if not sessions:
        typer.echo("no stories yet. In Jira: give a story the label ai-native and move it to Ready.")
        return
    for s in sessions:
        t = rt.config.targets.targets.get(s.target)
        typer.echo(f"{s.key:<12} {s.stage:<10} pr=#{s.pr_number or '-'}  updated {s.updated}")
        typer.echo(f"    next: {next_action(s, t, jira_status(s))}")


# ----------------------------------------------------------------------------- sessions
@session_app.command("status")
def session_status(key: str, as_json: bool = typer.Option(False, "--json")) -> None:
    """Show a story's stage, gate state and event trace."""
    rt = build_runtime(need_jira=False)
    s = rt.store.get_session(key)
    if not s:
        typer.secho(f"no session for {key}", fg="red", err=True)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(rt.store.export(key), indent=2, default=str))
        return
    typer.echo(
        f"{s.key}: target={s.target} repo={s.repo} stage={s.stage} branch={s.branch} pr={s.pr_number} "
        f"attempts={s.attempts} updated={s.updated}"
    )
    if s.last_error:
        typer.echo(f"  last error: {s.last_error}")
    for e in rt.store.events(key):
        typer.echo(f"  {e.ts} {e.kind:<16} {json.dumps(e.detail, default=str)[:140]}")


@session_app.command("dispatch")
def session_dispatch(
    key: str, dry_run: bool = typer.Option(False, "--dry-run"), max_steps: int = typer.Option(10, "--max-steps")
) -> None:
    """Claim a story (if needed) and step it until it waits for a human or finishes."""
    rt = build_runtime(dry_run)
    tkey = key.split("-")[0]
    if tkey not in rt.config.targets.targets:
        typer.secho(f"{tkey} is not an onboarded target", fg="red", err=True)
        raise typer.Exit(1)
    engine = rt.engine_for(tkey)
    story = _story(rt, key)
    s = rt.store.get_session(key)
    if not s:
        s = engine.claim(story)
        typer.echo(f"claimed {key} -> {s.stage}")
    for _ in range(max_steps):
        before = s.stage
        after = engine.step(s, story)
        typer.echo(f"{key}: {before} -> {after}")
        s = rt.store.get_session(key)  # type: ignore[assignment]
        if after == before or after.startswith("wait_") or after in ("released", "blocked"):
            break
    if rt.dry_run:
        for line in rt.jira.dry_run_log + rt.github.dry_run_log:
            typer.echo(f"  [dry-run] {line}")


@session_app.command("advance")
def session_advance(key: str, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Advance one story by exactly one transition if its gate allows."""
    rt = build_runtime(dry_run)
    s = rt.store.get_session(key)
    if not s:
        typer.secho(f"no session for {key}", fg="red", err=True)
        raise typer.Exit(1)
    after = rt.engine_for(s.target).step(s, _story(rt, key))
    typer.echo(f"{key}: {s.stage} -> {after}")


# ----------------------------------------------------------------------------- loop
def write_heartbeat(work: Path, report: dict[str, Any]) -> None:
    """Last cycle report for the portal and for `ains status`; a missing or stale file means the poller is down."""
    try:
        (work / "heartbeat.json").write_text(json.dumps(report), encoding="utf-8")
    except OSError as e:  # never let a status file stop the loop
        log.warning("heartbeat not written: %s", e)


def read_heartbeat(work: Path) -> dict[str, Any] | None:
    try:
        return json.loads((work / "heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@app.command()
def poll(
    once: bool = typer.Option(False, "--once"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    interval: int = typer.Option(0, "--interval", help="Seconds between cycles (default AINS_POLL_SECONDS or 60)"),
) -> None:
    """Poll Jira and the target repos; claim stories, advance sessions, review labelled PRs."""
    rt = build_runtime(dry_run)
    poller = Poller(rt.config, rt.store, rt.jira, rt.engine_for)
    watcher = PRWatcher(rt.config, rt.store, rt.github, rt.workspace, rt.agents)
    every = interval or int(os.environ.get("AINS_POLL_SECONDS", "60"))
    # module logs (agent start, claims, warnings) go to stderr with a UTC timestamp, same as the cycle line
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.Formatter.converter = time.gmtime
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Jira call is noise
    setup_file_log(rt.store.path.parent, "orchestrator")
    log.info("poll loop started: every %ss, data in %s, work in %s", every, rt.store.path.parent, rt.workspace.root)
    while True:
        reload_config(rt, poller, watcher)
        rep = poller.cycle()
        wrep = watcher.cycle()
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = (
            f"cycle: claimed={rep.claimed} stepped={rep.stepped} skipped={rep.skipped} "
            f"reviewed={wrep.reviewed} errors={rep.errors + wrep.errors}"
        )
        typer.echo(f"{stamp} {line}")
        cycle_log.info(line)
        write_heartbeat(
            rt.store.path.parent,
            {
                "at": stamp,
                "claimed": rep.claimed,
                "stepped": rep.stepped,
                "skipped": rep.skipped,
                "reviewed": wrep.reviewed,
                "errors": rep.errors + wrep.errors,
                "interval_s": every,
            },
        )
        if once:
            break
        time.sleep(every)


# ----------------------------------------------------------------------------- portal (I20)
@app.command()
def portal(
    host: str = typer.Option(
        "127.0.0.1", "--host", help="Bind address; the container binds 0.0.0.0 and compose maps it to localhost"
    ),
    port: int = typer.Option(8787, "--port"),
) -> None:
    """Serve the management portal: dashboard, projects, stories with the state map, onboarding, docs."""
    import uvicorn

    from ains_orchestrator.portal import create_app

    load_dotenv(config_root())
    uvicorn.run(create_app(config_root()), host=host, port=port, log_level="warning")


# ----------------------------------------------------------------------------- onboarding (I16)
@target_app.command("add")
def target_add(
    repo_name: str = typer.Argument(..., help="Repository name under the owner, e.g. ains-sandbox"),
    jira_key: str = typer.Argument(..., help="Jira project key, e.g. AINSSB"),
    jira_name: str = typer.Argument(..., help="Jira project name"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    approval_mode: str = typer.Option("label", "--approval-mode", help="label | review"),
    gates: str = typer.Option("", "--gates", help="e.g. G1=on,G2=off,G3=on (default: from the autonomy level)"),
    owner: str = typer.Option("", "--owner", help="GitHub owner (default: gh's current login)"),
    project_class: str = typer.Option("small-poc", "--class", help="project class id from profiles.yaml"),
    methodology: str = typer.Option("spec-kit", "--methodology", help="methodology id from profiles.yaml"),
    autonomy: str = typer.Option("L3", "--autonomy", help="autonomy level id from profiles.yaml (L3 or L4 today)"),
    phases: str = typer.Option("", "--phases", help="comma-separated phase ids (default: every supported phase)"),
) -> None:
    """Onboard a project: Jira project, GitHub repo, protection, environments, secrets, labels, templates."""
    catalog = load_profiles(config_root())
    phase_ids = [p.strip() for p in phases.split(",") if p.strip()] or [
        e["id"] for e in catalog["lifecycle_phases"] if e.get("supported")
    ]
    profile = Profile(project_class=project_class, methodology=methodology, autonomy=autonomy, phases=phase_ids)
    problems = validate_profile(catalog, profile)
    if problems:
        for p in problems:
            typer.secho(f"profile: {p}", fg="red", err=True)
        raise typer.Exit(2)
    ob = _onboarder(dry_run, owner)
    gate_map = {}
    for part in filter(None, gates.split(",")):
        g, _, v = part.partition("=")
        gate_map[g.strip()] = v.strip() or "on"
    if not gate_map:
        gate_map = gates_for_autonomy(catalog, autonomy)
    rep = ob.add(
        repo_name,
        jira_key.upper(),
        jira_name,
        gates=gate_map,
        approval_mode=approval_mode,
        profile=profile.model_dump(),
    )
    typer.echo(rep.text())
    if not dry_run and not rep.failed:
        typer.echo(f"\ntargets.yaml updated under {config_root()}; commit it in the platform repo.")
    raise typer.Exit(1 if rep.failed else 0)


@target_app.command("remove")
def target_remove(
    repo_name: str,
    dry_run: bool = typer.Option(False, "--dry-run"),
    delete_repo: bool = typer.Option(
        False, "--delete-repo", help="Delete instead of archive (needs the delete_repo scope)"
    ),
    owner: str = typer.Option("", "--owner"),
) -> None:
    """Reverse `target add` for one project: archive (or delete) the repo, delete the Jira project, drop the block."""
    rep = _onboarder(dry_run, owner).remove(repo_name, delete_repo=delete_repo)
    typer.echo(rep.text())
    raise typer.Exit(1 if rep.failed else 0)


@target_app.command("verify")
def target_verify(
    repo_name: str = typer.Argument(..., help="Repository name under the owner, e.g. ains-sandbox"),
    jira_key: str = typer.Argument(..., help="Jira project key, e.g. AINSSB"),
    owner: str = typer.Option("", "--owner"),
) -> None:
    """Read-only check that a project's onboarding is complete (repo files, labels, secrets, environments,
    branch protection, Jira project and statuses, targets.yaml). Changes nothing. Exit 1 on any failure."""
    root = config_root()
    load_dotenv(root)
    gh = GitHubClient(dry_run=False)
    owner = owner or gh.exec(["gh", "api", "user", "--jq", ".login"], what="gh user").strip()
    jira = None
    if all(os.environ.get(v) for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")):
        jira = JiraClient(os.environ["JIRA_BASE_URL"], os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
    cfg = load_config(root)
    checks = verify_target(
        gh, jira, repo=f"{owner}/{repo_name}", jira_key=jira_key.upper(), targets=cfg.targets.targets
    )
    failed = False
    for c in checks:
        typer.secho(f"[{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}", fg=None if c.ok else "red")
        failed |= not c.ok
    raise typer.Exit(1 if failed else 0)


def _onboarder(dry_run: bool, owner: str) -> Onboarder:
    root = config_root()
    load_dotenv(root)
    gh = GitHubClient(dry_run=False)
    owner = owner or gh.exec(["gh", "api", "user", "--jq", ".login"], what="gh user").strip()
    jira = None
    if all(os.environ.get(v) for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")):
        jira = JiraClient(
            os.environ["JIRA_BASE_URL"], os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"], dry_run=dry_run
        )
    return Onboarder(
        config_root=root, templates_dir=default_templates_dir(root), github=gh, jira=jira, owner=owner, dry_run=dry_run
    )


# ----------------------------------------------------------------------------- doctor
def _which(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name + ".cmd") or shutil.which(name + ".exe")


def _version(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (
            (p.stdout or p.stderr).strip().splitlines()[0] if (p.stdout or p.stderr).strip() else f"exit {p.returncode}"
        )
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


@app.command()
def doctor(live: bool = typer.Option(True, "--live/--no-live", help="Also call Jira and GitHub")) -> None:
    """Check config, tokens, gh, claude, specify and the environment. Exit 1 on any failure."""
    root = config_root()
    load_dotenv(root)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    try:
        cfg = load_config(root)
        check(
            "config", True, f"{len(cfg.targets.targets)} target(s), flows {list(cfg.flows)}, agents {list(cfg.agents)}"
        )
    except AinsError as e:
        check("config", False, str(e))
    for var in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        check(f"env {var}", bool(os.environ.get(var)), "set" if os.environ.get(var) else "missing")
    bad = [k for k in os.environ if k.startswith("ANTHROPIC_")]
    check("no ANTHROPIC_* variables", not bad, ", ".join(bad) if bad else "clean")
    for tool in ("git", "gh", "claude", "specify"):
        path = _which(tool)
        check(f"tool {tool}", path is not None, _version([path, "--version"]) if path else "not on PATH")
    work = work_dir(root)
    try:
        data = data_dir(root)
        check("data dir", True, f"{data} (sessions.sqlite, heartbeat.json, logs/)")
        work.mkdir(parents=True, exist_ok=True)
        (work / ".write-test").write_text("ok")
        (work / ".write-test").unlink()
        check("work dir writable", True, str(work))
    except OSError as e:
        check("work dir writable", False, f"{work}: {e}")
    if live:
        if _which("gh"):
            check(
                "gh auth", "Logged in" in _version(["gh", "auth", "status"]) or True, _version(["gh", "auth", "status"])
            )
        if all(os.environ.get(v) for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")):
            try:
                me = JiraClient(
                    os.environ["JIRA_BASE_URL"], os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"]
                ).myself()
                check("jira", True, f"as {me.get('displayName')}")
            except AinsError as e:
                check("jira", False, str(e))
    failed = False
    for name, ok, detail in checks:
        typer.secho(f"[{'ok' if ok else 'FAIL'}] {name}: {detail}", fg=None if ok else "red")
        failed |= not ok
    raise typer.Exit(1 if failed else 0)
