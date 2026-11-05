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
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ains_orchestrator.config import GATE_NAMES
from ains_orchestrator.errors import AinsError, GitHubError, JiraError
from ains_orchestrator.github import GitHubClient
from ains_orchestrator.jira import JiraClient

# Check-run name GitHub gives the reusable CI job when the caller job in templates/ci.yml is named `ci`.
REQUIRED_CHECK_CONTEXT = "ci / ci"

log = logging.getLogger("ains.onboard")

BOARD_COLUMN_ORDER = [  # the flow left to right; shown to the person as a manual step after onboarding
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
]
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
    "g3-rework": ("A43E2A", "send the PR back to the implement agent (label approval mode); explain in a PR comment"),
    "ai-native": ("5A4A8A", "story runs through the AINS AI-native lane"),
}
TEMPLATE_FILES = {  # template name -> path in the target repo
    "ci.yml": ".github/workflows/ci.yml",
    "release.yml": ".github/workflows/release.yml",
    "CLAUDE.md": "CLAUDE.md",
    "claude-settings.json": ".claude/settings.json",
    "gitignore": ".gitignore",
    "README.md": "README.md",
    "CODEOWNERS": ".github/CODEOWNERS",
}
JIRA_TEMPLATE = "com.pyxis.greenhopper.jira:gh-simplified-agility-kanban"
SKILL_FILES = {  # the /ains skill ships into every project so a clone brings it into Claude Code
    "SKILL.md": ".claude/skills/ains/SKILL.md",
    "reference.md": ".claude/skills/ains/reference.md",
}


def default_skill_dir(config_root: Path) -> Path | None:
    """The platform repository's own copy of the /ains skill (host checkout or the container mount)."""
    for cand in (
        config_root.parent / ".claude" / "skills" / "ains",
        config_root / ".claude" / "skills" / "ains",
        Path("/app/skills/ains"),
    ):
        if (cand / "SKILL.md").exists():
            return cand
    return None


PROTECTED_PATHS = ("/.github/", "/.claude/", "/.specify/", "/CLAUDE.md")  # platform-owned inside a project repo


def environment_reviewers(gates: dict[str, str]) -> dict[str, bool]:
    """staging needs a reviewer when G4 is on, production when G5 is on (default on)."""
    return {"staging": gates.get("G4", "on") != "off", "production": gates.get("G5", "on") != "off"}


def protection_body(approval_mode: str) -> dict[str, Any]:
    """Branch protection for main. In `review` mode (a separate orchestrator account) PRs that touch
    platform-owned paths need a code-owner review; in `label` mode (one account) GitHub would then refuse every
    merge, so the CI platform guard blocks drift instead."""
    reviews = (
        {"require_code_owner_reviews": True, "required_approving_review_count": 1, "dismiss_stale_reviews": True}
        if approval_mode == "review"
        else None
    )
    return {
        "required_status_checks": {"strict": False, "contexts": [REQUIRED_CHECK_CONTEXT]},
        "enforce_admins": False,
        "required_pull_request_reviews": reviews,
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


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
        skill_dir: Path | None = None,
    ) -> None:
        self.root, self.templates, self.gh, self.jira = Path(config_root), Path(templates_dir), github, jira
        self.owner, self.dry_run = owner, dry_run
        self.platform_repo, self.platform_ref = platform_repo, platform_ref
        self.reviewer = reviewer_login or owner
        self.skill_dir = skill_dir or default_skill_dir(config_root)

    # ================================================================== add
    def add(
        self,
        repo_name: str,
        jira_key: str,
        jira_name: str,
        *,
        gates: dict[str, str] | None = None,
        approval_mode: str = "label",
        profile: dict[str, Any] | None = None,
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
        self._github_environments(rep, repo, gates)
        self._github_protection(rep, repo, approval_mode)
        self._github_bot_collaborator(rep, repo)
        self._jira_project(rep, jira_key, jira_name, repo)
        self._targets_block(rep, jira_key, repo, gates, approval_mode, profile)
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
        if self.skill_dir is not None:
            for name, dest in SKILL_FILES.items():
                if (self.skill_dir / name).exists():
                    files[dest] = (self.skill_dir / name).read_text(encoding="utf-8")
        else:
            status = "planned" if self.dry_run else "skipped"
            rep.add("ains skill", status, "platform skill folder not found; the project will not carry /ains")
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

    def _github_environments(self, rep: Report, repo: str, gates: dict[str, str] | None = None) -> None:
        """G4 and G5 live in GitHub: an environment has a required reviewer exactly when its gate is on."""
        wanted = environment_reviewers(gates or {})
        plan = ", ".join(f"{env} ({'reviewer' if need else 'no reviewer'})" for env, need in wanted.items())
        if self.dry_run:
            rep.add("environments", "planned", f"{plan}; reviewer {self.reviewer}")
            return
        try:
            uid = self.gh.exec(["gh", "api", f"users/{self.reviewer}", "--jq", ".id"], what="user id").strip()
            for env, need in wanted.items():
                reviewers = [{"type": "User", "id": int(uid)}] if need else []
                body = json.dumps({"reviewers": reviewers, "deployment_branch_policy": None})
                self._gh(
                    ["gh", "api", "-X", "PUT", f"repos/{repo}/environments/{env}", "--input", "-"],
                    write=True,
                    what=f"environment {env}",
                    stdin=body,
                )
            rep.add("environments", "done", f"{plan}; reviewer {self.reviewer}")
        except (GitHubError, ValueError) as e:
            rep.add("environments", "failed", str(e)[:200])

    def _github_bot_collaborator(self, rep: Report, repo: str) -> None:
        """The orchestrator's bot account (AINS_BOT_LOGIN) needs write access to every target; the invitation is
        accepted at once with the bot's own token (AINS_BOT_GITHUB_TOKEN) so no one has to click an e-mail."""
        bot = os.environ.get("AINS_BOT_LOGIN")
        if not bot:
            status = "planned" if self.dry_run else "skipped"
            rep.add("bot collaborator", status, "no AINS_BOT_LOGIN: the owner's account runs the orchestrator")
            return
        if self.dry_run:
            rep.add("bot collaborator", "planned", f"invite {bot} with write access and accept the invitation")
            return
        try:
            out = self.gh.exec(
                ["gh", "api", "-X", "PUT", f"repos/{repo}/collaborators/{bot}", "-f", "permission=push"],
                write=True,
                what=f"invite {bot}",
            )
            accepted = self._accept_invitation(repo, bot)
            state = "already a collaborator" if not out.strip() else ("invited and accepted" if accepted else "invited")
            rep.add("bot collaborator", "done", f"{bot}: {state} (write)")
        except GitHubError as e:
            rep.add("bot collaborator", "failed", str(e)[:200])

    def _accept_invitation(self, repo: str, bot: str) -> bool:
        token = os.environ.get("AINS_BOT_GITHUB_TOKEN")
        if not token:
            return False
        env = {**os.environ, "GH_TOKEN": token, "GITHUB_TOKEN": token}
        try:
            out = subprocess.run(
                [
                    "gh",
                    "api",
                    "user/repository_invitations",
                    "--jq",
                    f'.[] | select(.repository.full_name == "{repo}") | .id',
                ],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            ).stdout.split()
            for inv in out:
                subprocess.run(
                    ["gh", "api", "-X", "PATCH", f"user/repository_invitations/{inv}"],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
            return bool(out)
        except OSError:
            return False

    def _github_protection(self, rep: Report, repo: str, approval_mode: str = "label") -> None:
        body = json.dumps(protection_body(approval_mode))
        if self.dry_run:
            rep.add("branch protection", "planned", f"main: require status check '{REQUIRED_CHECK_CONTEXT}'")
            return
        try:
            self._gh(
                ["gh", "api", "-X", "PUT", f"repos/{repo}/branches/main/protection", "--input", "-"],
                write=True,
                what="branch protection",
                stdin=body,
            )
            rep.add("branch protection", "done", f"main requires '{REQUIRED_CHECK_CONTEXT}'")
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
            self._order_board_columns(key, rep)
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

    def _order_board_columns(self, key: str, rep: Report) -> None:
        """Columns left to right in flow order, Rework and Blocked after Done; the Jira defaults last."""
        wanted = BOARD_COLUMN_ORDER + ["Rework", "Blocked"]
        manual = (
            f"Jira > project {key} > Board > ... > Configure board > Columns: order them "
            f"{' > '.join(wanted)}; delete the default In Progress and In Review columns (unused)."
        )
        try:
            bid = self.jira.board_id(key)
            if bid is None:
                rep.add("board columns", "manual", "board not found")
                rep.manual.append(manual)
                return
            before = self.jira.board_columns(bid)
            if before[: len(wanted)] == wanted:
                rep.add("board columns", "exists", " > ".join(before))
                return
            after = self.jira.reorder_board_columns(bid, wanted)
            if self.dry_run or after[: len(wanted)] == wanted:
                rep.add("board columns", "planned" if self.dry_run else "done", " > ".join(after))
            else:
                rep.add("board columns", "manual", f"reorder did not take: {' > '.join(after)}")
                rep.manual.append(manual)
        except JiraError as e:
            rep.add("board columns", "manual", str(e)[:200])
            rep.manual.append(manual)

    # ---------------------------------------------------------------- targets.yaml
    def _targets_block(
        self,
        rep: Report,
        key: str,
        repo: str,
        gates: dict[str, str],
        approval_mode: str,
        profile: dict[str, Any] | None = None,
    ) -> None:
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
        if profile:
            phases = ", ".join(str(p) for p in profile.get("phases", []))
            block += (
                f"    profile: {{project_class: {profile.get('project_class', 'small-poc')}, "
                f"methodology: {profile.get('methodology', 'spec-kit')}, autonomy: {profile.get('autonomy', 'L3')}, "
                f"phases: [{phases}]}}\n"
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


# ----------------------------------------------------------------------------- verify (read-only)
@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def verify_target(
    gh: GitHubClient,
    jira: JiraClient | None,
    *,
    repo: str,
    jira_key: str,
    targets: dict[str, Any],
    templates_dir: Path | None = None,
) -> list[Check]:
    """Read-only check that everything `target add` creates is in place. Nothing is changed."""
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name, ok, detail))

    try:
        d = json.loads(gh.exec(["gh", "repo", "view", repo, "--json", "name,defaultBranchRef"], what="repo view"))
        add("github repo", True, f"default branch {(d.get('defaultBranchRef') or {}).get('name', '?')}")
    except (GitHubError, json.JSONDecodeError) as e:
        add("github repo", False, str(e)[:120])
        return checks
    for path in list(TEMPLATE_FILES.values()) + list(SKILL_FILES.values()) + [".specify", ".claude/skills"]:
        try:
            gh.exec(
                ["gh", "api", f"repos/{repo}/contents/{path}", "--jq", 'if type == "array" then length else .path end'],
                what="contents",
            )
            add(f"file {path}", True)
        except GitHubError:
            add(f"file {path}", False, "missing")
    if templates_dir is not None:
        # platform-owned files that must not drift from the templates (gap 1); CLAUDE.md and the thin workflows
        # carry rendered values, so only the placeholder-free settings file is compared byte for byte
        import base64

        tpl = templates_dir / "claude-settings.json"
        try:
            raw = gh.exec(
                ["gh", "api", f"repos/{repo}/contents/.claude/settings.json", "--jq", ".content"], what="settings"
            )

            def norm(text: str) -> str:
                return text.replace("\r\n", "\n").strip()

            same = norm(base64.b64decode(raw).decode("utf-8")) == norm(tpl.read_text(encoding="utf-8"))
            detail = "matches the template" if same else "differs from the template: re-run target add"
            add("platform guard .claude/settings.json", same, detail)
        except (GitHubError, ValueError, OSError) as e:
            add("platform guard .claude/settings.json", False, str(e)[:120])
    try:
        names = gh.exec(["gh", "label", "list", "--repo", repo, "--json", "name", "--jq", ".[].name"], what="labels")
        missing = [lb for lb in LABELS if lb not in names.split()]
        add("labels", not missing, "all present" if not missing else "missing " + ", ".join(missing))
    except GitHubError as e:
        add("labels", False, str(e)[:120])
    try:
        names = gh.exec(["gh", "secret", "list", "--repo", repo, "--json", "name", "--jq", ".[].name"], what="secrets")
        for s in ("JIRA_EMAIL", "JIRA_API_TOKEN"):
            add(f"secret {s}", s in names.split(), "set" if s in names.split() else "missing")
    except GitHubError as e:
        add("secrets", False, str(e)[:120])
    try:
        envs = json.loads(gh.exec(["gh", "api", f"repos/{repo}/environments", "--jq", ".environments"], what="envs"))
        by_name = {e.get("name"): e for e in envs or []}
        t_gates = getattr(targets.get(jira_key), "gates", None) or {}
        for n, need in environment_reviewers(dict(t_gates)).items():
            e = by_name.get(n)
            has_reviewer = bool(e) and any(r.get("type") == "required_reviewers" for r in e.get("protection_rules", []))
            ok = bool(e) and has_reviewer == need
            state = "missing" if not e else ("with required reviewer" if has_reviewer else "no required reviewer")
            add(f"environment {n}", ok, f"{state}; gate {'on' if need else 'off'} wants {'one' if need else 'none'}")
    except (GitHubError, json.JSONDecodeError) as e:
        add("environments", False, str(e)[:120])
    try:
        ctx = json.loads(
            gh.exec(
                ["gh", "api", f"repos/{repo}/branches/main/protection/required_status_checks", "--jq", ".contexts"],
                what="protection",
            )
        )
        add("branch protection", REQUIRED_CHECK_CONTEXT in (ctx or []), f"main requires {ctx}")
    except (GitHubError, json.JSONDecodeError):
        add("branch protection", False, "main is not protected")
    if jira is None:
        add("jira", False, "no Jira credentials in the environment")
    else:
        try:
            if not jira.project_exists(jira_key):
                add("jira project", False, f"{jira_key} missing")
            else:
                add("jira project", True, jira_key)
                present = jira.project_statuses(jira_key)
                missing = [s for s in REQUIRED_STATUSES if s not in present]
                add("jira statuses", not missing, "all present" if not missing else "missing " + ", ".join(missing))
        except JiraError as e:
            add("jira", False, str(e)[:120])
    bot = os.environ.get("AINS_BOT_LOGIN")
    if bot:
        try:
            perm = gh.exec(
                ["gh", "api", f"repos/{repo}/collaborators/{bot}/permission", "--jq", ".permission"],
                what="bot permission",
            ).strip()
            ok = perm in ("write", "admin", "maintain")
            add(f"bot collaborator {bot}", ok, f"permission {perm or 'none'}" + ("" if ok else ": run target add"))
        except GitHubError as e:
            add(f"bot collaborator {bot}", False, str(e)[:120])
    t = targets.get(jira_key)
    t_repo = getattr(t, "repo", None) if t is not None else None
    add("targets.yaml", t_repo == repo, f"{jira_key} -> {t_repo}" if t is not None else f"no block for {jira_key}")
    return checks
