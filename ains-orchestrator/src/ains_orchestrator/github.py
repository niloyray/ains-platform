"""I4: thin wrapper over the `gh` and `git` CLIs.

All process execution goes through one injectable `runner` so tests can fake it. The PAT is
read by gh from GH_TOKEN in the environment; this module never prints it. `dry_run=True`
logs every write instead of running it. The merge helper refuses unless the PR has a human
approval (review or label, per target) and the required `ci` check is green.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ains_orchestrator.errors import GitHubError, MergeRefused

log = logging.getLogger("ains.github")
NETWORK_ERRORS = ("could not resolve host", "timed out", "connection reset", "tls handshake", "eof")
AGENT_REVIEW_MARKER = "[ains review agent]"


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], Path | None], RunResult]


def subprocess_runner(argv: list[str], cwd: Path | None) -> RunResult:
    p = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return RunResult(p.returncode, p.stdout, p.stderr)


@dataclass
class PullRequest:
    number: int
    url: str
    title: str
    head_sha: str
    head_ref: str
    is_draft: bool
    state: str
    review_decision: str
    labels: list[str]
    checks: dict[str, str]  # name -> conclusion/state
    human_approved: bool
    merged: bool

    @property
    def ci_green(self) -> bool:
        return self.checks.get("ci", "").upper() in {"SUCCESS", "PASS", "PASSED"}


class GitHubClient:
    def __init__(
        self,
        *,
        dry_run: bool = False,
        runner: Runner = subprocess_runner,
        sleep: Callable[[float], None] = time.sleep,
        bot_login: str | None = None,
    ) -> None:
        self.dry_run = dry_run
        self._run = runner
        self._sleep = sleep
        self.bot_login = bot_login  # login used by the orchestrator; its own reviews never count as approval
        self.dry_run_log: list[str] = []

    # ------------------------------------------------------------------ process helpers
    def _exec(self, argv: list[str], cwd: Path | None = None, *, write: bool = False, what: str = "") -> str:
        what = what or " ".join(argv[:3])
        if write and self.dry_run:
            self.dry_run_log.append(f"{what}: {' '.join(argv)}")
            log.info("[dry-run] %s", what)
            return ""
        for attempt in (1, 2):
            r = self._run(argv, cwd)
            if r.returncode == 0:
                return r.stdout
            err = (r.stderr or "").lower()
            if attempt == 1 and any(n in err for n in NETWORK_ERRORS):
                self._sleep(3)
                continue
            raise GitHubError(f"{what} failed (exit {r.returncode})", r.returncode, r.stderr)
        raise GitHubError(what)  # unreachable

    def _gh_json(self, argv: list[str], cwd: Path | None = None) -> Any:
        out = self._exec(argv, cwd)
        try:
            return json.loads(out) if out.strip() else None
        except json.JSONDecodeError as e:
            raise GitHubError(f"{argv[:3]}: not JSON: {out[:200]}") from e

    # ------------------------------------------------------------------ git
    def clone(self, repo: str, dest: Path, branch: str | None = None) -> None:
        argv = ["gh", "repo", "clone", repo, str(dest)]
        if branch:
            argv += ["--", "--branch", branch]
        self._exec(argv, write=True, what=f"clone {repo}")

    def create_branch(self, cwd: Path, name: str) -> None:
        self._exec(["git", "checkout", "-B", name], cwd, write=True, what=f"branch {name}")

    def commit_all(self, cwd: Path, message: str) -> bool:
        """Stage everything and commit. Returns False when there is nothing to commit."""
        if self.dry_run:
            self.dry_run_log.append(f"commit: {message.splitlines()[0]}")
            return True
        self._exec(["git", "add", "-A"], cwd, what="git add")
        status = self._exec(["git", "status", "--porcelain"], cwd, what="git status")
        if not status.strip():
            return False
        self._exec(["git", "commit", "-q", "-m", message], cwd, what="git commit")
        return True

    def push(self, cwd: Path, branch: str) -> None:
        self._exec(["git", "push", "-u", "origin", branch], cwd, write=True, what=f"push {branch}")

    def head_sha(self, cwd: Path) -> str:
        return self._exec(["git", "rev-parse", "HEAD"], cwd, what="rev-parse").strip()

    # ------------------------------------------------------------------ pull requests
    def pr_create(self, repo: str, head: str, base: str, title: str, body: str, draft: bool = True) -> int | None:
        argv = ["gh", "pr", "create", "--repo", repo, "--head", head, "--base", base, "--title", title, "--body", body]
        if draft:
            argv.append("--draft")
        out = self._exec(argv, write=True, what=f"pr create {title[:40]!r}")
        if not out:
            return None
        return int(out.strip().rstrip("/").split("/")[-1])

    def pr_ready(self, repo: str, number: int) -> None:
        self._exec(["gh", "pr", "ready", str(number), "--repo", repo], write=True, what=f"pr ready #{number}")

    _VIEW_FIELDS = (
        "number,url,title,headRefOid,headRefName,isDraft,state,reviewDecision,labels,statusCheckRollup,reviews,mergedAt"
    )

    def pr_view(
        self, repo: str, number: int, *, approval_mode: str = "review", approval_label: str = "g3-approved"
    ) -> PullRequest:
        d = self._gh_json(["gh", "pr", "view", str(number), "--repo", repo, "--json", self._VIEW_FIELDS])
        return self._to_pr(d, approval_mode, approval_label)

    def pr_list(self, repo: str, *, label: str | None = None, state: str = "open") -> list[dict[str, Any]]:
        argv = [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            state,
            "--json",
            "number,headRefOid,headRefName,isDraft,labels,title,url",
            "--limit",
            "50",
        ]
        if label:
            argv += ["--label", label]
        return self._gh_json(argv) or []

    def pr_review(self, repo: str, number: int, body: str, event: str) -> None:
        """Post a review as the orchestrator. APPROVE is refused: only humans approve (G3)."""
        event = event.upper()
        if event not in {"COMMENT", "REQUEST_CHANGES"}:
            raise GitHubError(f"review event {event!r} not allowed for the review agent")
        flag = "--comment" if event == "COMMENT" else "--request-changes"
        marked = body if body.startswith(AGENT_REVIEW_MARKER) else f"{AGENT_REVIEW_MARKER}\n\n{body}"
        self._exec(
            ["gh", "pr", "review", str(number), "--repo", repo, flag, "--body", marked],
            write=True,
            what=f"pr review #{number} {event}",
        )

    def pr_add_label(self, repo: str, number: int, label: str) -> None:
        self._exec(
            ["gh", "pr", "edit", str(number), "--repo", repo, "--add-label", label],
            write=True,
            what=f"label #{number} {label}",
        )

    def pr_merge(self, repo: str, pr: PullRequest) -> None:
        if not pr.human_approved:
            raise MergeRefused(f"#{pr.number}: no human approval yet")
        if not pr.ci_green:
            raise MergeRefused(f"#{pr.number}: required check 'ci' is not green ({pr.checks.get('ci', 'missing')})")
        self._exec(
            ["gh", "pr", "merge", str(pr.number), "--repo", repo, "--squash", "--delete-branch"],
            write=True,
            what=f"merge #{pr.number}",
        )

    def pr_comment(self, repo: str, number: int, body: str) -> None:
        self._exec(
            ["gh", "pr", "comment", str(number), "--repo", repo, "--body", body], write=True, what=f"comment #{number}"
        )

    # ------------------------------------------------------------------ parsing
    def _to_pr(self, d: dict[str, Any], approval_mode: str, approval_label: str) -> PullRequest:
        checks: dict[str, str] = {}
        for c in d.get("statusCheckRollup") or []:
            name = c.get("name") or c.get("context") or ""
            checks[name] = str(c.get("conclusion") or c.get("state") or "")
        labels = [lb.get("name", "") for lb in d.get("labels") or []]
        human = False
        if approval_mode == "label":
            human = approval_label in labels
        else:
            for rv in d.get("reviews") or []:
                author = (rv.get("author") or {}).get("login", "")
                body = rv.get("body") or ""
                if (
                    rv.get("state") == "APPROVED"
                    and author != self.bot_login
                    and not body.startswith(AGENT_REVIEW_MARKER)
                ):
                    human = True
            if not human and d.get("reviewDecision") == "APPROVED" and self.bot_login is None:
                human = True
        return PullRequest(
            number=int(d["number"]),
            url=d.get("url", ""),
            title=d.get("title", ""),
            head_sha=d.get("headRefOid", ""),
            head_ref=d.get("headRefName", ""),
            is_draft=bool(d.get("isDraft")),
            state=str(d.get("state", "")),
            review_decision=str(d.get("reviewDecision") or ""),
            labels=labels,
            checks=checks,
            human_approved=human,
            merged=bool(d.get("mergedAt")),
        )
