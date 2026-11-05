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
EPILOG = (
    "Quick start:\n"
    "  ains target add                  guided onboarding, one question at a time\n"
    "  ains target verify <repo> <KEY>  is a project onboarded correctly?\n"
    "  ains targets list                onboarded projects and their gates\n"
    "  ains doctor --no-live            tokens and tools\n"
    "Stories and the platform: ains-platform.cmd status | start | stop\n"
    "Portal: http://localhost:8787"
)
app = typer.Typer(no_args_is_help=True, help=HELP, epilog=EPILOG)
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
def use_bot_identity() -> str | None:
    """When AINS_BOT_GITHUB_TOKEN is set, the orchestrator runtime (PRs, pushes, merges) acts as that account,
    so a person on the owner account can approve its PRs with a real review (approval_mode `review`).
    Onboarding and verification keep the owner's token: creating repositories needs the owner."""
    load_dotenv(config_root())
    bot = os.environ.get("AINS_BOT_GITHUB_TOKEN")
    if not bot:
        return None
    os.environ["GH_TOKEN"] = bot
    os.environ["GITHUB_TOKEN"] = bot
    login = os.environ.get("AINS_BOT_LOGIN")
    log.info("acting on GitHub as the bot account %s", login or "(login not set: set AINS_BOT_LOGIN)")
    return login


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
    use_bot_identity()
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
# ----------------------------------------------------------------------------- guided onboarding (I16, one question a
def _choose(title: str, options: list[tuple[str, str, bool]], default: int = 1) -> str:
    """Numbered pick list. options: (id, text, selectable). Unsupported rows are shown but cannot be picked."""
    typer.echo(f"\n{title}")
    for n, (_id, text, ok) in enumerate(options, 1):
        typer.secho(f"  {n}. {text}" + ("" if ok else "   (not available yet)"), fg=None if ok else "bright_black")
    while True:
        raw = typer.prompt("  choice", default=str(default)).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options) and options[int(raw) - 1][2]:
            return options[int(raw) - 1][0]
        typer.secho("  pick one of the available numbers", fg="yellow")


def _ask(title: str, default: str | None, validate: str, hint: str) -> str:
    import re as _re

    while True:
        v = typer.prompt(title, default=default) if default else typer.prompt(title)
        v = v.strip()
        if _re.fullmatch(validate, v):
            return v
        typer.secho(f"  {hint}", fg="yellow")


def onboarding_wizard(catalog: dict[str, Any]) -> dict[str, Any]:
    """Ask for every onboarding value in turn; returns the same values the flags would carry."""
    typer.secho("AINS: add a project. One question at a time; Enter accepts the default in [brackets].", bold=True)
    repo = _ask(
        "Repository name (lower case, digits, hyphens)",
        None,
        r"[a-z0-9][a-z0-9-]{1,80}",
        "lower case letters, digits and hyphens only",
    )
    key_default = "".join(ch for ch in repo.upper() if ch.isalnum())[:10] or "PROJ"
    if not key_default[0].isalpha():
        key_default = "P" + key_default[:9]
    key = _ask(
        "Jira project key (2 to 10 capital letters/digits)",
        key_default,
        r"[A-Z][A-Z0-9_]{1,9}",
        "start with a letter, 2 to 10 capitals",
    )
    name = _ask("Jira project name", repo.replace("-", " ").title(), r".{2,80}", "2 to 80 characters")
    classes = [
        (
            e["id"],
            f"{e['name']}: {e.get('note', '')} [ceiling {e.get('autonomy_ceiling', 'L3')}]",
            bool(e.get("supported")),
        )
        for e in catalog["project_classes"]
    ]
    project_class = _choose("Project class (sets depth, gates and the autonomy ceiling)", classes)
    ceiling = next(
        (e.get("autonomy_ceiling", "L3") for e in catalog["project_classes"] if e["id"] == project_class), "L3"
    )
    order = [e["id"] for e in catalog["autonomy_levels"]]
    levels = []
    for e in catalog["autonomy_levels"]:
        gates = gates_for_autonomy(catalog, e["id"])
        within = e["id"] in order and ceiling in order and order.index(e["id"]) <= order.index(ceiling)
        ok = bool(e.get("supported")) and within
        why = "" if within else " [above the class ceiling]"
        levels.append(
            (
                e["id"],
                f"{e['name']}: {e.get('note', '')} gates " + " ".join(f"{g}={v}" for g, v in gates.items()) + why,
                ok,
            )
        )
    default_level = next(
        (n for n, lv in enumerate(levels, 1) if lv[0] == "L3" and lv[2]),
        next((n for n, lv in enumerate(levels, 1) if lv[2]), 1),
    )
    autonomy = _choose("Autonomy level (who leads each phase, where the gates sit)", levels, default=default_level)
    methods = [
        (e["id"], f"{e['name']}: {e.get('note', '')}", bool(e.get("supported"))) for e in catalog["methodologies"]
    ]
    methodology = _choose("Methodology (methods arrive as skills)", methods)
    phases = [e["id"] for e in catalog["lifecycle_phases"] if e.get("supported")]
    typer.echo(
        f"\nLifecycle phases: {' -> '.join(phases)} (every supported phase runs in the story flow; no opt-out yet)"
    )
    approval = _choose(
        "G3 approval mode",
        [
            ("label", "label: one GitHub account; approve with the g3-approved label (recommended)", True),
            ("review", "review: separate orchestrator account; approve with a real PR review", True),
        ],
    )
    gates = gates_for_autonomy(catalog, autonomy)
    typer.echo("\nGates from the level: " + " ".join(f"{g}={v}" for g, v in gates.items()))
    if not typer.confirm("Keep these gates?", default=True):
        for g in gates:
            gates[g] = "on" if typer.confirm(f"  {g} on?", default=gates[g] == "on") else "off"
    return {
        "repo_name": repo,
        "jira_key": key,
        "jira_name": name,
        "project_class": project_class,
        "autonomy": autonomy,
        "methodology": methodology,
        "phases": phases,
        "approval_mode": approval,
        "gates": gates,
    }


@target_app.command("add")
def target_add(
    repo_name: str = typer.Argument(
        None, help="Repository name under the owner, e.g. ains-sandbox; omit (or '.') for the guided wizard"
    ),
    jira_key: str = typer.Argument(None, help="Jira project key, e.g. AINSSB"),
    jira_name: str = typer.Argument(None, help="Jira project name"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    approval_mode: str = typer.Option("label", "--approval-mode", help="label | review"),
    gates: str = typer.Option("", "--gates", help="e.g. G1=on,G2=off,G3=on (default: from the autonomy level)"),
    owner: str = typer.Option("", "--owner", help="GitHub owner (default: gh's current login)"),
    project_class: str = typer.Option("small-poc", "--class", help="project class id from profiles.yaml"),
    methodology: str = typer.Option("spec-kit", "--methodology", help="methodology id from profiles.yaml"),
    autonomy: str = typer.Option("L3", "--autonomy", help="autonomy level id from profiles.yaml (L3 or L4 today)"),
    phases: str = typer.Option("", "--phases", help="comma-separated phase ids (default: every supported phase)"),
) -> None:
    """Onboard a project: Jira project, GitHub repo, protection, environments, secrets, labels, templates.

    Without arguments (or with '.') it asks for every value one at a time, shows a dry run, and asks before
    the real run."""
    catalog = load_profiles(config_root())
    guided = repo_name in (None, ".")
    gate_map: dict[str, str] = {}
    if guided:
        w = onboarding_wizard(catalog)
        repo_name, jira_key, jira_name = w["repo_name"], w["jira_key"], w["jira_name"]
        project_class, autonomy, methodology = w["project_class"], w["autonomy"], w["methodology"]
        approval_mode, gate_map, phase_ids = w["approval_mode"], w["gates"], w["phases"]
    else:
        if not (jira_key and jira_name):
            typer.secho(
                'usage: ains target add <repo> <KEY> "<name>" ...  or  ains target add  (guided)', fg="red", err=True
            )
            raise typer.Exit(2)
        phase_ids = [p.strip() for p in phases.split(",") if p.strip()] or [
            e["id"] for e in catalog["lifecycle_phases"] if e.get("supported")
        ]
        for part in filter(None, gates.split(",")):
            g, _, v = part.partition("=")
            gate_map[g.strip()] = v.strip() or "on"
    profile = Profile(project_class=project_class, methodology=methodology, autonomy=autonomy, phases=phase_ids)
    problems = validate_profile(catalog, profile)
    if problems:
        for p in problems:
            typer.secho(f"profile: {p}", fg="red", err=True)
        raise typer.Exit(2)
    if not gate_map:
        gate_map = gates_for_autonomy(catalog, autonomy)
    gates_txt = ",".join(f"{g}={v}" for g, v in gate_map.items())
    command = (
        f'ains target add {repo_name} {jira_key.upper()} "{jira_name}" --class {project_class} --autonomy {autonomy} '
        f"--methodology {methodology} --approval-mode {approval_mode} --gates {gates_txt}"
    )

    def run(dry: bool) -> Any:
        rep = _onboarder(dry, owner).add(
            repo_name,
            jira_key.upper(),
            jira_name,
            gates=gate_map,
            approval_mode=approval_mode,
            profile=profile.model_dump(),
        )
        typer.echo(rep.text())
        return rep

    if guided:
        typer.echo("\nThis is the command the answers amount to:\n  " + command)
        if not typer.confirm("Run it as a dry run first (nothing is created)?", default=True):
            raise typer.Exit(0)
        rep = run(True)
        if rep.failed or not typer.confirm("\nRun it for real now?", default=False):
            typer.echo("Stopped after the dry run. Re-run the command above when ready.")
            raise typer.Exit(1 if rep.failed else 0)
        rep = run(False)
        if not rep.failed:
            typer.echo("\nVerifying ...")
            try:
                target_verify(repo_name, jira_key.upper(), owner)
            except typer.Exit:
                pass
            typer.echo(f"\ntargets.yaml updated under {config_root()}; commit it with a line in docs/targets.md.")
        raise typer.Exit(1 if rep.failed else 0)
    rep = run(dry_run)
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
        gh,
        jira,
        repo=f"{owner}/{repo_name}",
        jira_key=jira_key.upper(),
        targets=cfg.targets.targets,
        templates_dir=default_templates_dir(root),
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
