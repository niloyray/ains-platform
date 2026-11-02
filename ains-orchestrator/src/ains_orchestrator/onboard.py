"""I16: `ains target add` / `ains target remove` — project onboarding as a platform capability (D15).

GitHub side (fully automated with gh): repo, templates pushed to main, labels, Actions secrets and
variable, environments with a required reviewer, branch protection requiring the `ci` check.
Jira side (REST where Jira Cloud allows it): team-managed Kanban project; statuses and the Automation
rule are verified by reading back and, where missing, printed as an exact UI checklist.
The lane marker is the label `ai-native` on the story (no custom field needed).
Every step is idempotent and reported; `--dry-run` prints the plan and changes nothing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ains_orchestrator.config import GATE_NAMES
from ains_orchestrator.errors import AinsError, GitHubError, JiraError
from ains_orchestrator.github import GitHubClient
from ains_orchestrator.jira import JiraClient

log = logging.getLogger("ains.onboard")

REQUIRED_STATUSES = [
    "To Do",
    "Ready",
    "Specifying",
    "Spec Review",
    "Planning",
    "Plan Review",
    "Implementing",
    "Code Review",
    "Staging",
    "Done",
    "Rework",
    "Blocked",
]
LABELS = {
    "ai-review": ("2F5E9E", "ask the AINS review agent to review this PR"),
    "g3-approved": ("146B63", "human G3 approval (label approval mode)"),
    "ai-native": ("5A4A8A", "story runs through the AINS AI-native lane"),
}
TEMPLATE_FILES = {  # template name -> path in the target repo
    "ci.yml": ".github/workflows/ci.yml",
    "release.yml": ".github/workflows/release.yml",
    "CLAUDE.md": "CLAUDE.md",
    "claude-settings.json": ".claude/settings.json",
    "gitignore": ".gitignore",
    "README.md": "README.md",
}
JIRA_TEMPLATE = "com.pyxis.greenhopper.jira:gh-simplified-agility-kanban"


@dataclass
class Step:
    name: str
    status: str  # done | exists | skipped | planned | failed | manual
    detail: str = ""


@dataclass
class Report:
    steps: list[Step] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.steps.append(Step(name, status, detail))
        log.info("%s: %s %s", name, status, detail)

    @property
    def failed(self) -> bool:
        return any(s.status == "failed" for s in self.steps)

    def text(self) -> str:
        lines = [f"[{s.status:<7}] {s.name}{': ' + s.detail if s.detail else ''}" for s in self.steps]
        if self.manual:
            lines += ["", "Manual steps still required:"] + [f"  - {m}" for m in self.manual]
        return "\n".join(lines)


def render(template: str, values: dict[str, str]) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    return out


class Onboarder:
    def __init__(
        self,
        *,
        config_root: Path,
        templates_dir: Path,
        github: GitHubClient,
        jira: JiraClient | None,
        owner: str,
        dry_run: bool = False,
        platform_repo: str = "niloyray/ains-platform",
        platform_ref: str = "main",
        reviewer_login: str | None = None,
    ) -> None:
        self.root, self.templates, self.gh, self.jira = Path(config_root), Path(templates_dir), github, jira
        self.owner, self.dry_run = owner, dry_run
        self.platform_repo, self.platform_ref = platform_repo, platform_ref
        self.reviewer = reviewer_login or owner

    # ================================================================== add
    def add(
        self,
        repo_name: str,
        jira_key: str,
        jira_name: str,
        *,
        gates: dict[str, str] | None = None,
        approval_mode: str = "label",
    ) -> Report:
        rep = Report()
        repo = f"{self.owner}/{repo_name}"
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,9}", jira_key):
            raise AinsError(f"'{jira_key}' is not a valid Jira project key")
        gates = {g: (gates or {}).get(g, "on") for g in GATE_NAMES}
        values = {
            "repo": repo,
            "repo_name": repo_name,
            "owner": self.owner,
            "jira_key": jira_key,
            "jira_name": jira_name,
            "image_name": repo_name,
            "platform_repo": self.platform_repo,
            "platform_ref": self.platform_ref,
            "jira_base_url": os.environ.get("JIRA_BASE_URL", ""),
        }
        self._github_repo(rep, repo)
        self._github_templates(rep, repo, values)
        self._github_labels(rep, repo)
        self._github_secrets(rep, repo)
        self._github_environments(rep, repo)
        self._github_protection(rep, repo)
        self._jira_project(rep, jira_key, jira_name, repo)
        self._targets_block(rep, jira_key, repo, gates, approval_mode)
        return rep

    # ---------------------------------------------------------------- github
    def _gh(self, argv: list[str], *, write: bool, what: str, stdin: str | None = None) -> str:
        if write and self.dry_run:
            return ""
        return self.gh.exec(argv, write=write, what=what, stdin=stdin)

    def _repo_exists(self, repo: str) -> bool:
        try:
            self.gh.exec(["gh", "repo", "view", repo, "--json", "name"], what=f"repo view {repo}")
            return True
        except GitHubError:
            return False

    def _github_repo(self, rep: Report, repo: str) -> None:
        if self._repo_exists(repo):
            rep.add("github repo", "exists", repo)
            return
        if self.dry_run:
            rep.add("github repo", "planned", f"gh repo create {repo} --public")
            return
        self._gh(
            ["gh", "repo", "create", repo, "--public", "--description", "AINS onboarded project"],
            write=True,
            what=f"repo create {repo}",
        )
        rep.add("github repo", "done", repo)

    def _github_templates(self, rep: Report, repo: str, values: dict[str, str]) -> None:
        files = {
            dest: render((self.templates / name).read_text(encoding="utf-8"), values)
            for name, dest in TEMPLATE_FILES.items()
            if (self.templates / name).exists()
        }
        if self.dry_run:
            rep.add("templates", "planned", f"push {len(files)} files + Spec Kit init to {repo}@main")
            return
        tmp = Path(tempfile.mkdtemp(prefix="ains-onboard-"))
        try:
            dest = tmp / "repo"
            try:
                self.gh.exec(["gh", "repo", "clone", repo, str(dest)], what="clone")
            except GitHubError:
                dest.mkdir()
                self.gh.exec(["git", "init", "-b", "main"], cwd=dest, what="git init")
                self.gh.exec(
                    ["git", "remote", "add", "origin", f"https://github.com/{repo}.git"], cwd=dest, what="remote"
                )
            changed = 0
            for rel, content in files.items():
                p = dest / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists() or p.read_text(encoding="utf-8") != content:
                    p.write_text(content, encoding="utf-8")
                    changed += 1
            speckit = self._speckit_init(dest)
            dirty = self.gh.exec(["git", "status", "--porcelain"], cwd=dest, what="git status").strip()
            if dirty:
                self.gh.exec(["git", "add", "-A"], cwd=dest, what="git add")
                self.gh.exec(
                    [
                        "git",
                        "-c",
                        "user.name=ains-orchestrator",
                        "-c",
                        "user.email=ains@users.noreply.github.com",
                        "commit",
                        "-q",
                        "-m",
                        "chore: AINS onboarding templates",
                    ],
                    cwd=dest,
                    what="git commit",
                )
                self.gh.exec(["git", "push", "-u", "origin", "HEAD:main"], cwd=dest, what="git push")
                rep.add("templates", "done", f"{changed} file(s) pushed; Spec Kit: {speckit or 'unchanged'}")
            else:
                rep.add("templates", "exists", "already up to date")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _speckit_init(self, dest: Path) -> str:
        if (dest / ".specify").exists():
            return ""
        specify = shutil.which("specify") or shutil.which("specify.exe")
        if not specify:
            return "specify not installed; run `specify init --here --integration claude` in the repo later"
        try:
            self.gh.exec(
                [
                    specify,
                    "init",
                    "--here",
                    "--force",
                    "--non-interactive",
                    "--integration",
                    "claude",
                    "--ignore-agent-tools",
                ],
                cwd=dest,
                what="specify init",
            )
            return "initialised (.specify/, .claude/commands/)"
        except GitHubError as e:
            return f"specify init failed ({str(e)[:120]}); run it manually in the repo"

    def _github_labels(self, rep: Report, repo: str) -> None:
        for name, (color, desc) in LABELS.items():
            if self.dry_run:
                rep.add(f"label {name}", "planned")
                continue
            try:
                self._gh(
                    ["gh", "label", "create", name, "--repo", repo, "--color", color, "--description", desc, "--force"],
                    write=True,
                    what=f"label {name}",
                )
                rep.add(f"label {name}", "done")
            except GitHubError as e:
                rep.add(f"label {name}", "failed", str(e)[:200])

    def _github_secrets(self, rep: Report, repo: str) -> None:
        email, token, base = (
            os.environ.get("JIRA_EMAIL", ""),
            os.environ.get("JIRA_API_TOKEN", ""),
            os.environ.get("JIRA_BASE_URL", ""),
        )
        if not (email and token and base):
            rep.add("secrets", "skipped", "JIRA_EMAIL/JIRA_API_TOKEN/JIRA_BASE_URL not in the environment")
            return
        if self.dry_run:
            rep.add("secrets", "planned", "JIRA_EMAIL, JIRA_API_TOKEN; variable JIRA_BASE_URL")
            return
        self._gh(
            ["gh", "secret", "set", "JIRA_EMAIL", "--repo", repo], write=True, what="secret JIRA_EMAIL", stdin=email
        )
        self._gh(
            ["gh", "secret", "set", "JIRA_API_TOKEN", "--repo", repo],
            write=True,
            what="secret JIRA_API_TOKEN",
            stdin=token,
        )
        self._gh(
            ["gh", "variable", "set", "JIRA_BASE_URL", "--repo", repo, "--body", base], write=True, what="variable"
        )
        rep.add("secrets", "done", "JIRA_EMAIL, JIRA_API_TOKEN, variable JIRA_BASE_URL")

    def _github_environments(self, rep: Report, repo: str) -> None:
        if self.dry_run:
            rep.add("environments", "planned", f"staging, production with required reviewer {self.reviewer}")
            return
        try:
            uid = self.gh.exec(["gh", "api", f"users/{self.reviewer}", "--jq", ".id"], what="user id").strip()
            body = json.dumps({"reviewers": [{"type": "User", "id": int(uid)}], "deployment_branch_policy": None})
            for env in ("staging", "production"):
                self._gh(
                    ["gh", "api", "-X", "PUT", f"repos/{repo}/environments/{env}", "--input", "-"],
                    write=True,
                    what=f"environment {env}",
                    stdin=body,
                )
            rep.add("environments", "done", f"staging, production; reviewer {self.reviewer}")
        except (GitHubError, ValueError) as e:
            rep.add("environments", "failed", str(e)[:200])

    def _github_protection(self, rep: Report, repo: str) -> None:
        body = json.dumps(
            {
                "required_status_checks": {"strict": False, "contexts": ["ci"]},
                "enforce_admins": False,
                "required_pull_request_reviews": None,
                "restrictions": None,
                "allow_force_pushes": False,
                "allow_deletions": False,
            }
        )
        if self.dry_run:
            rep.add("branch protection", "planned", "main: require status check 'ci'")
            return
        try:
            self._gh(
                ["gh", "api", "-X", "PUT", f"repos/{repo}/branches/main/protection", "--input", "-"],
                write=True,
                what="branch protection",
                stdin=body,
            )
            rep.add("branch protection", "done", "main requires 'ci'")
        except GitHubError as e:
            rep.add("branch protection", "failed", str(e)[:200])

    # ---------------------------------------------------------------- jira
    def _jira_project(self, rep: Report, key: str, name: str, repo: str) -> None:
        if self.jira is None:
            rep.add("jira project", "skipped", "no Jira credentials")
            return
        if self.dry_run:
            rep.add(
                "jira project",
                "planned",
                f"{key} '{name}' (team-managed Kanban), verify statuses, print rule checklist",
            )
            return
        try:
            if self.jira.project_exists(key):
                rep.add("jira project", "exists", key)
            else:
                self.jira.create_project(key, name, JIRA_TEMPLATE)
                rep.add("jira project", "done", f"{key} '{name}'")
            categories = {"Ready": "TODO", "Done": "DONE"}
            wanted = [(n, categories.get(n, "IN_PROGRESS")) for n in REQUIRED_STATUSES if n not in ("To Do", "Done")]
            try:
                added = self.jira.ensure_statuses(key, wanted)
                rep.add(
                    "jira statuses",
                    "done" if added else "exists",
                    f"added {', '.join(added)}" if added else "all present",
                )
            except JiraError as e:
                rep.add("jira statuses", "manual", str(e)[:200])
                present = self.jira.project_statuses(key)
                missing = [s for s in REQUIRED_STATUSES if s not in present]
                rep.manual.append(
                    f"Jira > project {key} > Board > add the statuses {', '.join(missing)} by hand; the "
                    f"automatic workflow update failed ({str(e)[:120]})."
                )
            rep.add("jira lane", "exists", "stories carry the label 'ai-native' (no custom field needed)")
            rep.manual.append(
                f"Jira > project {key} > Project settings > Automation > Create rule 'AI-native intake ack': "
                f"WHEN issue transitioned to Ready, IF labels contains ai-native, THEN Send web request "
                f"POST https://api.github.com/repos/{self.platform_repo}/dispatches with header "
                f"'Authorization: Bearer <GitHub PAT>' and body "
                f'{{"event_type":"jira-story-ready","client_payload":{{"key":"{{{{issue.key}}}}"}}}}. '
                f"Test it with a story; the intake-ack workflow in {self.platform_repo} comments on the story."
            )
            rep.add("jira automation", "manual", "rule checklist printed")
        except JiraError as e:
            rep.add("jira project", "failed", str(e)[:300])

    # ---------------------------------------------------------------- targets.yaml
    def _targets_block(self, rep: Report, key: str, repo: str, gates: dict[str, str], approval_mode: str) -> None:
        path = self.root / "targets.yaml"
        text = path.read_text(encoding="utf-8") if path.exists() else "targets: {}\n"
        if re.search(rf"^\s{{2}}{re.escape(key)}:\s*$", text, flags=re.M):
            rep.add("targets.yaml", "exists", key)
            return
        gates_txt = ", ".join(f"{g}: {v}" for g, v in gates.items())
        block = (
            f"  {key}:\n    repo: {repo}\n    default_branch: main\n    flow: story\n"
            f"    gates: {{{gates_txt}}}\n    review_label: ai-review\n    approval_mode: {approval_mode}\n"
        )
        if re.search(r"^targets:\s*\{\}\s*$", text, flags=re.M):
            new = re.sub(r"^targets:\s*\{\}\s*$", "targets:\n" + block.rstrip("\n"), text, count=1, flags=re.M)
        elif re.search(r"^targets:\s*$", text, flags=re.M):
            new = re.sub(r"^targets:\s*$", "targets:\n" + block.rstrip("\n"), text, count=1, flags=re.M)
        else:
            new = text.rstrip("\n") + "\ntargets:\n" + block
        if self.dry_run:
            rep.add("targets.yaml", "planned", f"append block for {key} -> {repo}")
            return
        path.write_text(new, encoding="utf-8")
        rep.add("targets.yaml", "done", f"{key} -> {repo} (commit it in {self.platform_repo})")

    # ================================================================== remove
    def remove(self, repo_name: str, *, delete_repo: bool = False) -> Report:
        rep = Report()
        repo = f"{self.owner}/{repo_name}"
        key = self._key_for_repo(repo)
        if self.dry_run:
            rep.add("github repo", "planned", f"{'delete' if delete_repo else 'archive'} {repo}")
            rep.add("jira project", "planned", f"delete {key}" if key else "no targets block, nothing to delete")
            rep.add("targets.yaml", "planned", f"remove {key}" if key else "no block")
            return rep
        if self._repo_exists(repo):
            try:
                if delete_repo:
                    self._gh(["gh", "repo", "delete", repo, "--yes"], write=True, what="repo delete")
                    rep.add("github repo", "done", f"deleted {repo}")
                else:
                    self._gh(["gh", "repo", "archive", repo, "--yes"], write=True, what="repo archive")
                    rep.add("github repo", "done", f"archived {repo} (delete needs the delete_repo scope)")
            except GitHubError as e:
                rep.add("github repo", "failed", str(e)[:200])
        else:
            rep.add("github repo", "skipped", "does not exist")
        if key and self.jira is not None:
            try:
                if self.jira.project_exists(key):
                    self.jira.delete_project(key)
                    rep.add("jira project", "done", f"deleted {key} (recoverable from Jira trash for 60 days)")
                else:
                    rep.add("jira project", "skipped", f"{key} does not exist")
            except JiraError as e:
                rep.add("jira project", "failed", str(e)[:200])
                rep.manual.append(f"Delete or archive Jira project {key} in Jira > Projects.")
        if key:
            self._remove_block(key)
            rep.add("targets.yaml", "done", f"removed {key}")
        else:
            rep.add("targets.yaml", "skipped", "no block for this repo")
        return rep

    def _key_for_repo(self, repo: str) -> str | None:
        path = self.root / "targets.yaml"
        if not path.exists():
            return None
        current = None
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s{2}([A-Z][A-Z0-9_]{1,9}):\s*$", line)
            if m:
                current = m.group(1)
            elif current and re.match(rf"^\s{{4}}repo:\s*{re.escape(repo)}\s*$", line):
                return current
        return None

    def _remove_block(self, key: str) -> None:
        path = self.root / "targets.yaml"
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        out, skipping = [], False
        for line in lines:
            if re.match(rf"^\s{{2}}{re.escape(key)}:\s*$", line):
                skipping = True
                continue
            if skipping and re.match(r"^\s{4}\S", line):
                continue
            skipping = False
            out.append(line)
        text = "".join(out)
        if re.search(r"^targets:\s*$", text, flags=re.M) and not re.search(r"^\s{2}[A-Z]", text, flags=re.M):
            text = re.sub(r"^targets:\s*$", "targets: {}", text, count=1, flags=re.M)
        path.write_text(text, encoding="utf-8")


def report_json(rep: Report) -> str:
    return json.dumps({"steps": [s.__dict__ for s in rep.steps], "manual": rep.manual}, indent=2)


def default_templates_dir(config_root: Path) -> Path:
    for cand in (
        config_root / "bootstrap" / "templates",
        config_root.parent / "bootstrap" / "templates",
        Path("/app/bootstrap/templates"),
    ):
        if cand.exists():
            return cand
    return config_root.parent / "bootstrap" / "templates"


def summarize(rep: Report) -> dict[str, Any]:
    return {"failed": rep.failed, "manual": len(rep.manual), "steps": len(rep.steps)}
