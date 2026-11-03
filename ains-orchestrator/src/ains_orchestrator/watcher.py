"""I10: watch target repos for Human-lane PRs labelled for review and spawn the review agent once per head SHA.

Session PRs (opened by the orchestrator) are reviewed by the flow engine itself in the `review`
stage; this watcher only handles PRs that carry the target's `review_label` and belong to no
session. It never merges and never approves.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ains_orchestrator.config import Config
from ains_orchestrator.errors import AinsError
from ains_orchestrator.flow import AgentRunner
from ains_orchestrator.github import GitHubClient
from ains_orchestrator.store import Store
from ains_orchestrator.workspace import Workspace

log = logging.getLogger("ains.watcher")
PR_STAGE = "pr_reviewed"


@dataclass
class WatchReport:
    reviewed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def pr_session_key(target_key: str, number: int) -> str:
    return f"{target_key}-PR{number}"


def story_key_from_branch(head_ref: str, target_key: str) -> str:
    """`AINSSB-1-ci-smoke` -> `AINSSB-1`; anything else -> a sentence saying there is no story."""
    m = re.match(r"^([A-Z][A-Z0-9_]{1,9}-\d+)", head_ref or "")
    if m and m.group(1).startswith(target_key + "-"):
        return m.group(1)
    return "none (a pull request opened by a person, not tied to a Jira story)"


class PRWatcher:
    def __init__(
        self, config: Config, store: Store, github: GitHubClient, workspace: Workspace, agents: AgentRunner
    ) -> None:
        self.config, self.store, self.gh, self.ws, self.agents = config, store, github, workspace, agents

    def cycle(self) -> WatchReport:
        rep = WatchReport()
        for tkey, target in self.config.targets.targets.items():
            try:
                prs = self.gh.pr_list(target.repo, label=target.review_label)
            except AinsError as e:
                rep.errors.append(f"{target.repo}: {e}")
                continue
            session_prs = {
                s.pr_number for s in self.store.list_sessions(target=tkey) if s.pr_number and s.flow != "pr-review"
            }
            for pr in prs:
                number, sha = int(pr["number"]), str(pr.get("headRefOid", ""))
                ref = f"{target.repo}#{number}"
                if number in session_prs:
                    rep.skipped.append(f"{ref} (session PR, reviewed by the flow)")
                    continue
                if pr.get("isDraft"):
                    rep.skipped.append(f"{ref} (draft)")
                    continue
                key = pr_session_key(tkey, number)
                s = self.store.get_session(key)
                if s and s.reviewed_sha == sha:
                    rep.skipped.append(f"{ref} (already reviewed {sha[:7]})")
                    continue
                try:
                    self._review(
                        tkey, target, key, number, sha, str(pr.get("headRefName", "")), str(pr.get("title", ""))
                    )
                    rep.reviewed.append(ref)
                except AinsError as e:
                    rep.errors.append(f"{ref}: {e}")
                    log.warning("review of %s failed: %s", ref, e)
        return rep

    def _review(self, tkey: str, target: Any, key: str, number: int, sha: str, head_ref: str, title: str) -> None:
        if not self.store.get_session(key):
            self.store.create_session(key, tkey, target.repo, "pr-review", PR_STAGE)
            self.store.update_session(key, pr_number=number, branch=head_ref)
        workdir = self.ws.ensure(key, target, head_ref)
        self.gh.checkout(workdir, head_ref) if hasattr(self.gh, "checkout") else None
        self.store.add_event(key, "agent.start", {"stage": "review", "sha": sha})
        res = self.agents.run(
            "review",
            key,
            workdir,
            {
                "pr": number,
                "head_sha": sha,
                "branch": head_ref,
                "repo": target.repo,
                "summary": title,
                "description": "",
                "story_key": story_key_from_branch(head_ref, tkey),
                "diff": self.gh.pr_diff(target.repo, number) if hasattr(self.gh, "pr_diff") else "",
            },
        )
        self.store.add_event(
            key,
            "agent.done",
            {"stage": "review", "ok": res.ok, "turns": res.turns, "cost_usd": res.cost_usd, "error": res.error[:300]},
        )
        if not res.ok:
            raise AinsError(f"review agent failed: {res.error[:200]}")
        verdict = str(res.extra.get("verdict", "comment")).upper()
        event = "REQUEST_CHANGES" if verdict == "REQUEST_CHANGES" else "COMMENT"
        self.gh.pr_review(target.repo, number, res.text or "(no findings)", event)
        self.store.update_session(key, reviewed_sha=sha, head_sha=sha)
        self.store.add_event(key, "review.posted", {"sha": sha, "event": event})
        self.ws.cleanup(key)
        Path(self.ws.path(key)).exists()  # noqa: B018 (cleanup is best effort)
