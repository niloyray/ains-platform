"""AINS management portal (FastAPI, served from the container on the laptop's loopback interface).

Read side: platform health from the poller heartbeat, every project with its gates and links, every story with its
stage, Jira status, next human action and the state map, the event trail with agent replies, the config files, docs.
Write side: onboard a project (`Onboarder.add`), remove one (`Onboarder.remove`), verify one (`verify_target`),
each run as a background job with a report page. Nothing here touches a story: gates stay in Jira and GitHub.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ains_orchestrator import __version__
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
from ains_orchestrator.github import GitHubClient
from ains_orchestrator.jira import JiraClient
from ains_orchestrator.onboard import LABELS, REQUIRED_STATUSES, Onboarder, default_templates_dir, verify_target
from ains_orchestrator.store import Session, Store

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "portal_templates"))
WAITING = {
    "wait_g1": "G1 spec",
    "wait_g2": "G2 plan",
    "wait_g3": "G3 code",
    "released": "G4/G5 release",
    "blocked": "Blocked",
}
JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

# ------------------------------------------------------------- state map (same geometry as the user guide)
FSM_NODES: list[dict[str, Any]] = [
    {"id": "To Do", "x": 20, "y": 70, "w": 100, "kind": "plain"},
    {"id": "Ready", "x": 135, "y": 70, "w": 100, "kind": "plain"},
    {"id": "Specifying", "x": 250, "y": 70, "w": 100, "kind": "plain"},
    {"id": "Spec Review", "x": 365, "y": 70, "w": 100, "kind": "human"},
    {"id": "Planning", "x": 480, "y": 70, "w": 100, "kind": "plain"},
    {"id": "Plan Review", "x": 595, "y": 70, "w": 100, "kind": "human"},
    {"id": "Implementing", "x": 710, "y": 70, "w": 100, "kind": "plain"},
    {"id": "Code Review", "x": 825, "y": 70, "w": 100, "kind": "human"},
    {"id": "Staging", "x": 940, "y": 70, "w": 100, "kind": "plain"},
    {"id": "Done", "x": 1055, "y": 70, "w": 100, "kind": "plain"},
    {"id": "Blocked", "x": 135, "y": 160, "w": 100, "kind": "bad"},
    {"id": "Rework", "x": 480, "y": 160, "w": 100, "kind": "bad"},
    {"id": "draft PR", "x": 250, "y": 340, "w": 100, "kind": "plain"},
    {"id": "ready for review", "x": 710, "y": 340, "w": 100, "kind": "plain"},
    {"id": "ci / ci green", "x": 825, "y": 340, "w": 100, "kind": "plain"},
    {"id": "g3-approved", "x": 940, "y": 340, "w": 100, "kind": "human"},
    {"id": "merged", "x": 1055, "y": 340, "w": 100, "kind": "plain"},
    {"id": "ci / ci red", "x": 710, "y": 420, "w": 100, "kind": "bad"},
    {"id": "g3-rework", "x": 940, "y": 420, "w": 100, "kind": "bad"},
    {"id": "image built", "x": 1055, "y": 515, "w": 100, "kind": "plain"},
    {"id": "staging approved", "x": 1170, "y": 515, "w": 110, "kind": "human"},
    {"id": "production approved", "x": 1295, "y": 515, "w": 110, "kind": "human"},
]


STAGE_TO_JIRA = {
    "spec": "Specifying",
    "wait_g1": "Spec Review",
    "plan": "Planning",
    "wait_g2": "Plan Review",
    "implement": "Implementing",
    "review": "Code Review",
    "wait_g3": "Code Review",
    "merge": "Code Review",
    "blocked": "Blocked",
}


def fsm_current(jira_status: str | None, stage: str, pr: dict[str, Any] | None) -> set[str]:
    """Which nodes to light up for a story: one in the Jira lane, up to one in the PR lane, one in the release lane."""
    cur: set[str] = set()
    if jira_status not in {n["id"] for n in FSM_NODES}:
        # Jira unreachable or no story: the platform stage implies the status it last set
        jira_status = STAGE_TO_JIRA.get(stage)
    if jira_status:
        cur.add(jira_status)
    if pr is None and stage in ("implement", "review", "wait_g3", "merge"):
        pr = {"draft": False}  # PR exists but GitHub was not asked: show the lane at "ready for review"
    if pr:
        if pr.get("merged"):
            cur.add("merged")
        elif pr.get("draft"):
            cur.add("draft PR")
        elif "g3-rework" in pr.get("labels", []):
            cur.add("g3-rework")
        elif pr.get("human_approved"):
            cur.add("g3-approved")
        elif pr.get("ci") in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
            cur.add("ci / ci red")
        elif pr.get("ci_green"):
            cur.add("ci / ci green")
        else:
            cur.add("ready for review")
    elif stage in ("spec", "wait_g1", "plan", "wait_g2"):
        cur.add("draft PR")
    if stage == "released":
        cur.add({"Staging": "staging approved", "Done": "production approved"}.get(jira_status or "", "image built"))
    return cur


# ----------------------------------------------------------------------------- runtime pieces
class Portal:
    """Everything a request needs, built fresh so config edits on the host show up at once."""

    def __init__(self, root: Path | None = None) -> None:
        from ains_orchestrator.cli import config_root, load_dotenv

        self.root = root or config_root()
        load_dotenv(self.root)
        self.work = Path(os.environ.get("AINS_WORK_DIR", "/work" if os.name != "nt" else str(self.root / "work")))
        self.docs_dir = next(
            (p for p in (self.root / "docs", self.root.parent / "docs", Path("/app/docs")) if p.exists()), None
        )

    # config and clients ---------------------------------------------------------------------------------------
    def config(self) -> Config:
        return load_config(self.root)

    def store(self) -> Store:
        return Store(self.work / "sessions.sqlite")

    def jira(self) -> JiraClient | None:
        if all(os.environ.get(v) for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")):
            try:
                return JiraClient(os.environ["JIRA_BASE_URL"], os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
            except AinsError:
                return None
        return None

    def github(self) -> GitHubClient:
        return GitHubClient(dry_run=False, bot_login=os.environ.get("AINS_BOT_LOGIN"))

    def heartbeat(self) -> dict[str, Any]:
        from ains_orchestrator.cli import read_heartbeat

        hb = read_heartbeat(self.work) or {}
        age: float | None = None
        if hb.get("at"):
            try:
                age = (
                    datetime.now(UTC) - datetime.strptime(hb["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                ).total_seconds()
            except ValueError:
                age = None
        interval = int(hb.get("interval_s") or os.environ.get("AINS_POLL_SECONDS", "60"))
        ok = age is not None and age < 3 * interval + 30
        return {**hb, "age_s": None if age is None else int(age), "ok": ok, "present": bool(hb)}

    # links --------------------------------------------------------------------------------------------------
    @staticmethod
    def jira_base() -> str:
        return os.environ.get("JIRA_BASE_URL", "").rstrip("/")

    def project_links(self, key: str, t: Target) -> dict[str, str]:
        gh = f"https://github.com/{t.repo}"
        owner, _, name = t.repo.partition("/")
        jb = self.jira_base()
        return {
            "jira_board": f"{jb}/jira/software/projects/{key}/boards" if jb else "",
            "jira_issues": f"{jb}/jira/software/projects/{key}/issues" if jb else "",
            "jira_waiting": f"{jb}/issues/?jql="
            + f"project%20%3D%20{key}%20AND%20labels%20%3D%20ai-native%20AND%20status%20not%20in%20(Done)"
            if jb
            else "",
            "repo": gh,
            "pulls": f"{gh}/pulls",
            "actions": f"{gh}/actions",
            "ci_runs": f"{gh}/actions/workflows/ci.yml",
            "release_runs": f"{gh}/actions/workflows/release.yml",
            "environments": f"{gh}/settings/environments",
            "branches": f"{gh}/settings/branches",
            "labels": f"{gh}/labels",
            "secrets": f"{gh}/settings/secrets/actions",
            "packages": f"https://github.com/{owner}?tab=packages&repo_name={name}",
            "claude_md": f"{gh}/blob/{t.default_branch}/CLAUDE.md",
            "settings_json": f"{gh}/blob/{t.default_branch}/.claude/settings.json",
            "ci_yml": f"{gh}/blob/{t.default_branch}/.github/workflows/ci.yml",
            "release_yml": f"{gh}/blob/{t.default_branch}/.github/workflows/release.yml",
        }

    def story_links(self, s: Session, t: Target | None) -> dict[str, str]:
        if not t:
            return {}
        gh = f"https://github.com/{t.repo}"
        ref = s.branch if s.branch and s.stage not in ("released", "abandoned") else t.default_branch
        links = {
            "jira": f"{self.jira_base()}/browse/{s.key}" if self.jira_base() and s.flow != "pr-review" else "",
            "branch": f"{gh}/tree/{s.branch}" if s.branch else "",
            "spec": f"{gh}/blob/{ref}/specs/{s.key}/spec.md" if s.flow != "pr-review" else "",
            "plan": f"{gh}/blob/{ref}/specs/{s.key}/plan.md" if s.flow != "pr-review" else "",
            "tasks": f"{gh}/blob/{ref}/specs/{s.key}/tasks.md" if s.flow != "pr-review" else "",
            "ci_runs": f"{gh}/actions/workflows/ci.yml?query=branch%3A{s.branch}" if s.branch else "",
            "release_runs": f"{gh}/actions/workflows/release.yml",
        }
        if s.pr_number:
            links.update(
                {
                    "pr": f"{gh}/pull/{s.pr_number}",
                    "pr_files": f"{gh}/pull/{s.pr_number}/files",
                    "pr_checks": f"{gh}/pull/{s.pr_number}/checks",
                    "pr_reviews": f"{gh}/pull/{s.pr_number}#issue-comment-box",
                }
            )
        return links

    # rows ---------------------------------------------------------------------------------------------------
    def story_row(self, s: Session, cfg: Config, jira: JiraClient | None, *, live: bool = True) -> dict[str, Any]:
        from ains_orchestrator.cli import next_action

        t = cfg.targets.targets.get(s.target)
        jira_status = None
        if live and jira and s.flow != "pr-review":
            try:
                jira_status = jira.status_of(s.key)
            except AinsError:
                jira_status = None
        waiting = WAITING.get(s.stage)
        if s.stage == "released" and jira_status == "Done":
            waiting = None
        return {
            "key": s.key,
            "target": s.target,
            "flow": s.flow,
            "stage": s.stage,
            "summary": (s.gate_state or {}).get("summary", ""),
            "pr": s.pr_number,
            "branch": s.branch,
            "updated": s.updated,
            "jira_status": jira_status,
            "waiting": waiting,
            "next": next_action(s, t, jira_status),
            "last_error": s.last_error,
            "links": self.story_links(s, t),
        }

    def pr_state(self, s: Session, t: Target | None) -> dict[str, Any] | None:
        if not (t and s.pr_number):
            return None
        try:
            pr = self.github().pr_view(
                t.repo, int(s.pr_number), approval_mode=t.approval_mode, approval_label=t.approval_label
            )
        except AinsError:
            return None
        return {
            "number": pr.number,
            "url": pr.url,
            "draft": pr.is_draft,
            "state": pr.state,
            "merged": bool(pr.merged),
            "labels": pr.labels,
            "ci": pr.checks.get("ci", ""),
            "ci_green": pr.ci_green,
            "human_approved": pr.human_approved,
            "head": pr.head_sha,
        }

    def onboarder(self, dry_run: bool = False) -> Onboarder:
        gh = self.github()
        owner = (
            os.environ.get("AINS_GITHUB_OWNER")
            or gh.exec(["gh", "api", "user", "--jq", ".login"], what="gh user").strip()
        )
        return Onboarder(
            config_root=self.root,
            templates_dir=default_templates_dir(self.root),
            github=gh,
            jira=self.jira(),
            owner=owner,
            dry_run=dry_run,
        )


# ----------------------------------------------------------------------------- jobs
def _job_start(kind: str, title: str) -> str:
    jid = uuid.uuid4().hex[:10]
    with _JOBS_LOCK:
        JOBS[jid] = {"id": jid, "kind": kind, "title": title, "status": "running", "lines": [], "started": _now()}
    return jid


def _job_finish(jid: str, lines: list[str], failed: bool) -> None:
    with _JOBS_LOCK:
        JOBS[jid].update(status="failed" if failed else "done", lines=lines, finished=_now())


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_add(
    portal: Portal,
    jid: str,
    repo: str,
    key: str,
    name: str,
    gates: dict[str, str],
    mode: str,
    dry: bool,
    profile: dict[str, Any] | None = None,
) -> None:
    try:
        rep = portal.onboarder(dry_run=dry).add(repo, key, name, gates=gates, approval_mode=mode, profile=profile)
        _job_finish(jid, rep.text().splitlines(), rep.failed)
    except Exception as e:  # noqa: BLE001 - the report page must always render
        _job_finish(jid, [f"failed: {e}"], True)


def run_remove(portal: Portal, jid: str, repo: str, delete_repo: bool, dry: bool) -> None:
    try:
        rep = portal.onboarder(dry_run=dry).remove(repo, delete_repo=delete_repo)
        _job_finish(jid, rep.text().splitlines(), rep.failed)
    except Exception as e:  # noqa: BLE001
        _job_finish(jid, [f"failed: {e}"], True)


def run_verify(portal: Portal, jid: str, repo: str, key: str) -> None:
    try:
        cfg = portal.config()
        checks = verify_target(portal.github(), portal.jira(), repo=repo, jira_key=key, targets=cfg.targets.targets)
        _job_finish(
            jid, [f"[{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in checks], any(not c.ok for c in checks)
        )
    except Exception as e:  # noqa: BLE001
        _job_finish(jid, [f"failed: {e}"], True)


# ----------------------------------------------------------------------------- app
def create_app(root: Path | None = None) -> FastAPI:
    portal = Portal(root)
    app = FastAPI(title="AINS platform portal", version=__version__, docs_url=None, redoc_url=None)

    def base_ctx(request: Request, cfg: Config, active: str) -> dict[str, Any]:
        return {
            "request": request,
            "version": __version__,
            "active": active,
            "projects": list(cfg.targets.targets.items()),
            "heartbeat": portal.heartbeat(),
            "docs_available": portal.docs_dir is not None,
            "jira_base": portal.jira_base(),
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> Any:
        cfg = portal.config()
        store, jira = portal.store(), portal.jira()
        try:
            rows = [portal.story_row(s, cfg, jira) for s in store.list_sessions()]
            recent = store.recent_events(20)
        finally:
            store.close()
        rows.sort(key=lambda r: r["updated"], reverse=True)
        waiting = [r for r in rows if r["waiting"]]
        env = {
            v: bool(os.environ.get(v))
            for v in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
        }
        bad = [k for k in os.environ if k.startswith("ANTHROPIC_")]
        ctx = base_ctx(request, cfg, "dashboard")
        ctx.update(
            rows=rows,
            waiting=waiting,
            recent=recent,
            env=env,
            anthropic_vars=bad,
            jobs=sorted(JOBS.values(), key=lambda j: j["started"], reverse=True)[:5],
        )
        return TEMPLATES.TemplateResponse(request, "dashboard.html", ctx)

    @app.get("/projects/{key}", response_class=HTMLResponse)
    def project(request: Request, key: str) -> Any:
        cfg = portal.config()
        t = cfg.targets.targets.get(key.upper())
        if not t:
            return RedirectResponse("/", status_code=303)
        store, jira = portal.store(), portal.jira()
        try:
            rows = [portal.story_row(s, cfg, jira) for s in store.list_sessions(target=key.upper())]
        finally:
            store.close()
        rows.sort(key=lambda r: r["updated"], reverse=True)
        ctx = base_ctx(request, cfg, f"project:{key.upper()}")
        ctx.update(
            key=key.upper(),
            target=t,
            repo_name=t.repo.partition("/")[2],
            links=portal.project_links(key.upper(), t),
            rows=rows,
            labels=LABELS,
            statuses=REQUIRED_STATUSES,
            catalog=load_profiles(portal.root),
            jobs=[j for j in JOBS.values() if j.get("project") == key.upper()][-5:],
        )
        return TEMPLATES.TemplateResponse(request, "project.html", ctx)

    @app.post("/projects/{key}/verify")
    def project_verify(key: str, background: BackgroundTasks) -> Any:
        cfg = portal.config()
        t = cfg.targets.targets.get(key.upper())
        if not t:
            return RedirectResponse("/", status_code=303)
        jid = _job_start("verify", f"verify {key.upper()} ({t.repo})")
        JOBS[jid]["project"] = key.upper()
        background.add_task(run_verify, portal, jid, t.repo, key.upper())
        return RedirectResponse(f"/jobs/{jid}", status_code=303)

    @app.post("/projects/{key}/remove")
    def project_remove(
        key: str,
        background: BackgroundTasks,
        confirm: str = Form(""),
        delete_repo: str = Form(""),
        dry_run: str = Form(""),
    ) -> Any:
        cfg = portal.config()
        t = cfg.targets.targets.get(key.upper())
        if not t or confirm.strip().upper() != key.upper():
            return RedirectResponse(f"/projects/{key.upper()}?error=confirm", status_code=303)
        repo_name = t.repo.partition("/")[2]
        jid = _job_start("remove", f"remove {key.upper()} ({t.repo}){' [dry run]' if dry_run else ''}")
        JOBS[jid]["project"] = key.upper()
        background.add_task(run_remove, portal, jid, repo_name, bool(delete_repo), bool(dry_run))
        return RedirectResponse(f"/jobs/{jid}", status_code=303)

    @app.get("/stories/{key}", response_class=HTMLResponse)
    def story(request: Request, key: str) -> Any:
        cfg = portal.config()
        store, jira = portal.store(), portal.jira()
        try:
            s = store.get_session(key)
            if not s:
                return RedirectResponse("/", status_code=303)
            row = portal.story_row(s, cfg, jira)
            events = store.events(key)
        finally:
            store.close()
        t = cfg.targets.targets.get(s.target)
        pr = portal.pr_state(s, t)
        current = fsm_current(row["jira_status"], s.stage, pr)
        ctx = base_ctx(request, cfg, f"project:{s.target}")
        ctx.update(
            row=row,
            session=s,
            events=events,
            pr=pr,
            fsm_nodes=FSM_NODES,
            current=current,
            gate_state=json.dumps(s.gate_state, indent=2),
        )
        return TEMPLATES.TemplateResponse(request, "story.html", ctx)

    def _add_ctx(
        request: Request, cfg: Config, errors: list[str] | None = None, values: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        catalog = load_profiles(portal.root)
        ctx = base_ctx(request, cfg, "add")
        ctx.update(
            catalog=catalog,
            gates_by_level={e["id"]: gates_for_autonomy(catalog, e["id"]) for e in catalog["autonomy_levels"]},
            errors=errors or [],
            values=values or {},
        )
        return ctx

    @app.get("/add", response_class=HTMLResponse)
    def add_form(request: Request) -> Any:
        return TEMPLATES.TemplateResponse(request, "add.html", _add_ctx(request, portal.config()))

    @app.post("/add")
    async def add_submit(request: Request, background: BackgroundTasks) -> Any:
        form = await request.form()
        f = {k: str(v) for k, v in form.items()}
        phases = [str(v) for v in form.getlist("phases")]
        profile = Profile(
            project_class=f.get("project_class", "small-poc"),
            methodology=f.get("methodology", "spec-kit"),
            autonomy=f.get("autonomy", "L3"),
            phases=phases,
        )
        cfg = portal.config()
        errors = validate_profile(load_profiles(portal.root), profile)
        key = f.get("jira_key", "").strip().upper()
        repo_name = f.get("repo_name", "").strip()
        jira_name = f.get("jira_name", "").strip()
        if not repo_name or not key or not jira_name:
            errors.append("repository name, Jira key and Jira name are required")
        if errors:
            values = {**f, "phases": phases}
            return TEMPLATES.TemplateResponse(request, "add.html", _add_ctx(request, cfg, errors, values))
        gates = {g: f.get(g.lower(), "on") for g in ("G1", "G2", "G3", "G4", "G5")}
        dry = bool(f.get("dry_run"))
        title = (
            f"onboard {repo_name} as {key} ({profile.project_class}, {profile.autonomy}){' [dry run]' if dry else ''}"
        )
        jid = _job_start("add", title)
        JOBS[jid]["project"] = key
        mode = f.get("approval_mode", "label")
        background.add_task(run_add, portal, jid, repo_name, key, jira_name, gates, mode, dry, profile.model_dump())
        return RedirectResponse(f"/jobs/{jid}", status_code=303)

    @app.get("/jobs/{jid}", response_class=HTMLResponse)
    def job(request: Request, jid: str) -> Any:
        cfg = portal.config()
        j = JOBS.get(jid)
        if not j:
            return RedirectResponse("/", status_code=303)
        ctx = base_ctx(request, cfg, "jobs")
        ctx.update(job=j)
        return TEMPLATES.TemplateResponse(request, "job.html", ctx)

    @app.get("/platform", response_class=HTMLResponse)
    def platform(request: Request) -> Any:
        cfg = portal.config()
        files = {}
        for rel in ("targets.yaml", "flows/story.yaml", "agents/claude-code.yaml", "claude-settings.json"):
            p = portal.root / rel
            files[rel] = p.read_text(encoding="utf-8") if p.exists() else "(missing)"
        env = {
            v: bool(os.environ.get(v))
            for v in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
        }
        ctx = base_ctx(request, cfg, "platform")
        ctx.update(
            files=files, env=env, flows=cfg.flows, agents=cfg.agents, work=str(portal.work), root=str(portal.root)
        )
        return TEMPLATES.TemplateResponse(request, "platform.html", ctx)

    @app.get("/docs/{name}")
    def docs(name: str) -> Any:
        allowed = {
            "user-guide": "user-guide.html",
            "developer-manual": "developer-manual.html",
            "log": "implementation-log.md",
        }
        if name not in allowed or portal.docs_dir is None:
            return RedirectResponse("https://github.com/niloyray/ains-platform/tree/main/docs", status_code=303)
        p = portal.docs_dir / allowed[name]
        if not p.exists():
            return RedirectResponse("https://github.com/niloyray/ains-platform/tree/main/docs", status_code=303)
        return FileResponse(str(p), media_type="text/html" if p.suffix == ".html" else "text/plain; charset=utf-8")

    @app.get("/api/status")
    def api_status() -> Any:
        cfg = portal.config()
        store, jira = portal.store(), portal.jira()
        try:
            rows = [portal.story_row(s, cfg, jira) for s in store.list_sessions()]
        finally:
            store.close()
        return JSONResponse(
            {
                "version": __version__,
                "heartbeat": portal.heartbeat(),
                "projects": list(cfg.targets.targets),
                "stories": rows,
            }
        )

    return app
