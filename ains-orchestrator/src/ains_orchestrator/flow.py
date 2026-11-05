"""I7: the flow state machine with gates.

One story = one session row. `FlowEngine.step()` performs at most one transition per call and is
invoked for every active session on every poll cycle, so the orchestrator is resumable at any
point from the store. Stage work (spawning an agent) goes through the `AgentRunner` protocol
(I8); git and PR work through `Workspace` (I11) and `GitHubClient` (I4); Jira through
`JiraClient` (I3).

States:  spec -> wait_g1 -> plan -> wait_g2 -> implement -> review -> wait_g3 -> merge -> released
         any stage -> blocked (after retries); rework loops return to the previous stage.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from ains_orchestrator.config import Flow, Target
from ains_orchestrator.errors import AinsError, MergeRefused
from ains_orchestrator.github import GitHubClient
from ains_orchestrator.jira import JiraClient
from ains_orchestrator.store import Session, Store
from ains_orchestrator.workspace import Workspace

log = logging.getLogger("ains.flow")

STAGE_OUTPUTS = {
    "spec": ["specs/{key}/spec.md"],
    "plan": ["specs/{key}/plan.md", "specs/{key}/tasks.md"],
    "implement": [],
    "review": [],
}
TERMINAL = {"released", "abandoned"}
MAX_CI_REWORK_ROUNDS = 2
MAX_WAIT_ERRORS = 10  # consecutive Jira/GitHub errors while waiting at a gate before the story is blocked
TRANSIENT_MARKERS = (
    "network error",
    "ConnectError",
    "error connecting",
    "connection refused",
    "connection reset",
    "dial tcp",
    "could not resolve",
    "Temporary failure in name resolution",
    "HTTP 502",
    "HTTP 503",
    "HTTP 504",
    "timed out",
    "Timeout",
    "TLS handshake",
)


def is_transient(error: str) -> bool:
    """An outage, not a fault in the story: never counts toward blocking."""
    return any(m in error for m in TRANSIENT_MARKERS)


AGENT_REVIEW_MARKER_TEXT = "[ains review agent]"


@dataclass
class AgentResult:
    ok: bool
    text: str = ""
    turns: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class AgentRunner(Protocol):
    def run(self, stage: str, key: str, workdir: Any, context: dict[str, Any]) -> AgentResult: ...


class StoryInfo(Protocol):
    """What the engine needs to know about the Jira story."""

    key: str
    summary: str
    description: str


@dataclass
class Story:
    key: str
    summary: str
    description: str


class FlowEngine:
    def __init__(
        self,
        *,
        flow: Flow,
        target_key: str,
        target: Target,
        store: Store,
        jira: JiraClient,
        github: GitHubClient,
        workspace: Workspace,
        agents: AgentRunner,
        max_attempts: int = 2,
    ) -> None:
        self.flow, self.tkey, self.target = flow, target_key, target
        self.store, self.jira, self.gh, self.ws, self.agents = store, jira, github, workspace, agents
        self.max_attempts = max_attempts
        self.st = flow.jira_status

    # ------------------------------------------------------------------ public
    def claim(self, story: Story) -> Session:
        """Called by the poller for a Ready story with no session."""
        branch = self.ws.branch_name(story.key, story.summary)
        s = self.store.create_session(story.key, self.tkey, self.target.repo, self.flow.name, "spec")
        self.store.update_session(story.key, branch=branch, gate_state={"summary": story.summary})
        self.jira.transition(story.key, self.st["claim"])
        self.jira.add_comment(
            story.key,
            f"ains-orchestrator picked up this story. target {self.target.repo}, flow {self.flow.name}, "
            f"gates {self._gates_text()}, branch {branch}.",
        )
        self.store.add_event(story.key, "claimed", {"branch": branch})
        return self.store.get_session(story.key) or s

    def step(self, session: Session, story: Story) -> str:
        """Perform at most one transition. Returns the stage after the step."""
        stage = session.stage
        if stage in TERMINAL or stage == "blocked":
            return stage
        handler = getattr(self, f"_do_{stage}", None)
        if handler is None:
            raise AinsError(f"{session.key}: unknown stage {stage}")
        try:
            out = handler(session, story)
            if stage.startswith("wait_") and out == stage and session.attempts:
                # a good cycle while waiting: only consecutive errors count toward blocking
                self.store.update_session(session.key, attempts=0, last_error=None)
            return out
        except MergeRefused as e:  # not an error, just not yet
            self.store.add_event(session.key, "merge.waiting", {"reason": str(e)})
            return session.stage
        except AinsError as e:
            if stage.startswith("wait_"):
                # Waiting is idempotent: an outage must not block the story, however long it lasts; other errors
                # (bad credentials, a deleted PR) get MAX_WAIT_ERRORS cycles before a human is asked.
                n = session.attempts if is_transient(str(e)) else session.attempts + 1
                self.store.add_event(session.key, "wait.error", {"stage": stage, "attempt": n, "error": str(e)[:300]})
                if n < MAX_WAIT_ERRORS:
                    self.store.update_session(session.key, attempts=n, last_error=str(e))
                    log.warning("%s: %s (attempt %s/%s, staying at %s)", session.key, e, n, MAX_WAIT_ERRORS, stage)
                    return stage
                return self._fail(session, f"{MAX_WAIT_ERRORS} consecutive errors while waiting at {stage}: {e}")
            return self._fail(session, str(e))

    # ------------------------------------------------------------------ stage handlers
    def _do_spec(self, s: Session, story: Story) -> str:
        return self._run_stage_and_gate(s, story, "spec", gate="G1", review_status="spec_review", next_stage="plan")

    def _do_plan(self, s: Session, story: Story) -> str:
        return self._run_stage_and_gate(
            s, story, "plan", gate="G2", review_status="plan_review", next_stage="implement"
        )

    def _do_implement(self, s: Session, story: Story) -> str:
        self._ensure_ws(s, story)
        res = self._run_agent("implement", s, story)
        if not res.ok:
            return self._retry_or_block(s, "implement", res.error)
        self.ws.commit(s.key, "implement", "implement tasks.md")
        self.ws.push(s.key, s.branch or "")
        sha = self.ws.head_sha(s.key)
        pr = self._ensure_pr(s, story, draft=False)
        if pr.is_draft:
            self.gh.pr_ready(self.target.repo, pr.number)
        self._jira_status(s.key, "code_review")
        self.store.update_session(s.key, stage="review", head_sha=sha, attempts=0)
        self.store.add_event(s.key, "stage.done", {"stage": "implement", "sha": sha, "pr": pr.number})
        return "review"

    def _do_review(self, s: Session, story: Story) -> str:
        pr = self.gh.pr_view(
            self.target.repo,
            int(s.pr_number or 0),
            approval_mode=self.target.approval_mode,
            approval_label=self.target.approval_label,
        )
        if s.reviewed_sha == pr.head_sha:
            self.store.update_session(s.key, stage="wait_g3")
            return "wait_g3"
        res = self._run_agent(
            "review",
            s,
            story,
            context={
                "pr": pr.number,
                "head_sha": pr.head_sha,
                "story_key": s.key,
                "diff": self.gh.pr_diff(self.target.repo, pr.number),
            },
        )
        if not res.ok:
            return self._retry_or_block(s, "review", res.error)
        verdict = str(res.extra.get("verdict", "comment")).upper()
        event = "REQUEST_CHANGES" if verdict == "REQUEST_CHANGES" else "COMMENT"
        self.gh.pr_review(self.target.repo, pr.number, res.text or "(no findings)", event)
        self._jira_status(s.key, "code_review")  # idempotent; restores the status after a resume
        self.store.update_session(s.key, stage="wait_g3", reviewed_sha=pr.head_sha, attempts=0)
        self.store.add_event(s.key, "review.posted", {"sha": pr.head_sha, "event": event})
        if self.target.gates["G3"] == "off":
            self.jira.add_comment(s.key, "G3 auto-approved (gate off for this target)")
        return "wait_g3"

    def _do_wait_g1(self, s: Session, story: Story) -> str:
        return self._wait_jira_gate(s, "G1", prev_stage="spec", next_stage="plan", review_status="spec_review")

    def _do_wait_g2(self, s: Session, story: Story) -> str:
        return self._wait_jira_gate(s, "G2", prev_stage="plan", next_stage="implement", review_status="plan_review")

    def _do_wait_g3(self, s: Session, story: Story) -> str:
        pr = self.gh.pr_view(
            self.target.repo,
            int(s.pr_number or 0),
            approval_mode=self.target.approval_mode,
            approval_label=self.target.approval_label,
        )
        if pr.head_sha != s.reviewed_sha:
            self.store.update_session(s.key, stage="review", head_sha=pr.head_sha)
            self.store.add_event(s.key, "pr.new_commits", {"sha": pr.head_sha})
            return "review"
        if pr.human_changes_requested:
            comments = "\n\n".join(pr.review_bodies) or "changes requested on the PR; see the review comments"
            self._rework(s, "implement", "G3", comments)
            return "implement"
        ci_state = pr.checks.get("ci", "").upper()
        if ci_state in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}:
            rounds = int(s.gate_state.get("ci_rework_rounds", 0))
            if rounds >= MAX_CI_REWORK_ROUNDS:
                return self._fail(
                    s, f"CI failed {rounds + 1} times on PR #{pr.number}; a human must look at the branch"
                )
            excerpt = self.gh.ci_failure_log(self.target.repo, s.branch or pr.head_ref)
            self._rework(
                s,
                "implement",
                "CI",
                f"CI failed on your last push. Fix the cause below, run the tests "
                f"and `uvx ruff check .` yourself, then stop.\n\n{excerpt}",
            )
            gs = dict(self.store.get_session(s.key).gate_state)  # type: ignore[union-attr]
            gs["ci_rework_rounds"] = rounds + 1
            self.store.update_session(s.key, gate_state=gs)
            self.jira.add_comment(
                s.key,
                f"CI failed on PR #{pr.number}; sending the story back to the implement agent "
                f"(round {rounds + 1} of {MAX_CI_REWORK_ROUNDS}).",
            )
            return "implement"
        if self.target.approval_mode == "label" and self.target.rework_label in pr.labels:
            comments = [
                c
                for c in self.gh.pr_comments(self.target.repo, pr.number)
                if not c["body"].startswith(AGENT_REVIEW_MARKER_TEXT)
            ]
            text = comments[-1]["body"] if comments else "rework requested via label; see the PR comments"
            self.gh.pr_remove_label(self.target.repo, pr.number, self.target.rework_label)
            self._rework(s, "implement", "G3", text)
            return "implement"
        gate_on = self.target.gates["G3"] == "on"
        if (pr.human_approved or not gate_on) and pr.ci_green:
            self.store.update_session(s.key, stage="merge")
            self.store.add_event(s.key, "gate.passed", {"gate": "G3", "mode": "human" if pr.human_approved else "off"})
            return "merge"
        return "wait_g3"

    def _do_merge(self, s: Session, story: Story) -> str:
        pr = self.gh.pr_view(
            self.target.repo,
            int(s.pr_number or 0),
            approval_mode=self.target.approval_mode,
            approval_label=self.target.approval_label,
        )
        if self.target.gates["G3"] == "off" and not pr.human_approved:
            pr.human_approved = True  # gate off: the orchestrator merges on green CI
        self.gh.pr_merge(self.target.repo, pr)
        self.jira.add_comment(
            s.key, f"Squash-merged PR #{pr.number} ({pr.url}). Release and G4/G5 continue in GitHub Actions."
        )
        self.ws.cleanup(s.key)
        self.store.update_session(s.key, stage="released")
        self.store.add_event(s.key, "merged", {"pr": pr.number, "sha": pr.head_sha})
        return "released"

    # ------------------------------------------------------------------ shared pieces
    def _run_stage_and_gate(
        self, s: Session, story: Story, stage: str, *, gate: str, review_status: str, next_stage: str
    ) -> str:
        self._ensure_ws(s, story)
        expected = [p.format(key=s.key) for p in STAGE_OUTPUTS[stage]]
        if s.gate_state.get("skip_agent") == stage and expected and not self.ws.has_files(s.key, *expected):
            gs = dict(s.gate_state)
            gs.pop("skip_agent", None)
            self.store.update_session(s.key, gate_state=gs)
            self.store.add_event(s.key, "agent.skipped", {"stage": stage, "reason": "outputs present after resume"})
        else:
            res = self._run_agent(stage, s, story)
            if not res.ok:
                return self._retry_or_block(s, stage, res.error)
        missing = self.ws.has_files(s.key, *expected)
        if missing:
            return self._retry_or_block(s, stage, f"agent finished without producing {missing}")
        self.ws.commit(s.key, stage, f"{stage} output")
        self.ws.push(s.key, s.branch or "")
        pr = self._ensure_pr(s, story, draft=True)
        self.store.add_event(s.key, "stage.done", {"stage": stage, "pr": pr.number})
        if self.target.gates[gate] == "on":
            self._jira_status(s.key, review_status)
            self.jira.add_comment(
                s.key,
                f"{stage} output is in PR #{pr.number} ({pr.url}). "
                f"Gate {gate}: move this story to '{self._approve_status(gate)}' to approve "
                f"or to '{self.st['rework']}' with a comment to rework.",
            )
            self.store.update_session(s.key, stage=f"wait_{gate.lower()}", attempts=0)
            self.store.add_event(s.key, "gate.waiting", {"gate": gate})
            return f"wait_{gate.lower()}"
        self.jira.add_comment(s.key, f"{gate} auto-approved (gate off for this target)")
        self._jira_status_name(s.key, self._approve_status(gate))
        self.store.update_session(s.key, stage=next_stage, attempts=0)
        self.store.add_event(s.key, "gate.passed", {"gate": gate, "mode": "off"})
        return next_stage

    def _wait_jira_gate(self, s: Session, gate: str, *, prev_stage: str, next_stage: str, review_status: str) -> str:
        status = self.jira.status_of(s.key)
        if status == self._approve_status(gate):
            self.store.update_session(s.key, stage=next_stage)
            self.store.add_event(s.key, "gate.passed", {"gate": gate, "mode": "human"})
            self.jira.add_comment(s.key, f"{gate} approved; starting {next_stage}.")
            return next_stage
        if status == self.st["rework"]:
            comment = self._latest_comment(s.key)
            self._rework(s, prev_stage, gate, comment)
            self._jira_status(s.key, "claim" if prev_stage == "spec" else "planning")
            return prev_stage
        if status == self.st["blocked"]:
            self.store.update_session(s.key, stage="blocked", last_error="blocked by a human in Jira")
            return "blocked"
        if status != self.st[review_status]:
            # something else (the CI callback, a stray drag on the board) moved the story; the gate is still open
            self.jira.transition(s.key, self.st[review_status])
            self.store.add_event(s.key, "jira.restored", {"gate": gate, "from": status, "to": self.st[review_status]})
        return s.stage

    def _rework(self, s: Session, stage: str, gate: str, comment: str) -> None:
        gs = dict(s.gate_state)
        gs[f"rework_{stage}"] = comment
        gs["rework_count"] = int(gs.get("rework_count", 0)) + 1
        self.store.update_session(s.key, stage=stage, gate_state=gs, attempts=0)
        self.store.add_event(s.key, "gate.rework", {"gate": gate, "stage": stage, "comment": comment[:500]})

    def _run_agent(self, stage: str, s: Session, story: Story, context: dict[str, Any] | None = None) -> AgentResult:
        ctx: dict[str, Any] = {
            "summary": story.summary,
            "description": story.description,
            "branch": s.branch,
            "repo": self.target.repo,
        }
        rework = s.gate_state.get(f"rework_{stage}")
        if rework:
            ctx["rework_comment"] = rework
        if context:
            ctx.update(context)
        self.store.add_event(s.key, "agent.start", {"stage": stage, "attempt": s.attempts + 1})
        res = self.agents.run(stage, s.key, self.ws.path(s.key), ctx)
        self.store.add_event(
            s.key,
            "agent.done",
            {
                "stage": stage,
                "ok": res.ok,
                "turns": res.turns,
                "cost_usd": res.cost_usd,
                "duration_ms": res.duration_ms,
                "error": res.error[:300],
                "result": (res.text or "")[:3000],  # the agent's final reply, for diagnosis
            },
        )
        return res

    def _retry_or_block(self, s: Session, stage: str, error: str) -> str:
        attempts = s.attempts + 1
        if attempts < self.max_attempts:
            self.store.update_session(s.key, attempts=attempts, last_error=error)
            self.store.add_event(s.key, "agent.retry", {"stage": stage, "attempt": attempts, "error": error[:300]})
            return s.stage
        return self._fail(s, f"{stage}: {error}")

    def _fail(self, s: Session, error: str) -> str:
        self.store.update_session(s.key, stage="blocked", last_error=error)
        self.store.add_event(s.key, "blocked", {"error": error[:500]})
        try:
            self._jira_status(s.key, "blocked")
            self.jira.add_comment(
                s.key,
                f"ains-orchestrator is blocked: {error[:800]}\nMove the story back to '{self.st['ready']}' to resume.",
            )
        except AinsError as e:  # never mask the original failure
            log.error("could not report block to Jira for %s: %s", s.key, e)
        return "blocked"

    def _ensure_ws(self, s: Session, story: Story) -> None:
        branch = s.branch or self.ws.branch_name(s.key, story.summary)
        self.ws.ensure(s.key, self.target, branch)
        if not s.branch:
            self.store.update_session(s.key, branch=branch)

    def _ensure_pr(self, s: Session, story: Story, *, draft: bool):
        if s.pr_number:
            return self.gh.pr_view(
                self.target.repo,
                s.pr_number,
                approval_mode=self.target.approval_mode,
                approval_label=self.target.approval_label,
            )
        title = f"{s.key}: {story.summary}"[:120]
        g3 = (
            "approve the PR, or request changes with a comment"
            if self.target.approval_mode == "review"
            else f"add the label `{self.target.approval_label}`, or comment and add the label `g3-rework`"
        )
        body = (
            f"Jira: {s.key}\n\n{story.description[:2000]}\n\n---\n"
            f"**How this PR moves.** G1 (spec): in Jira move {s.key} to **Planning**, or to **Rework** with a comment. "
            f"G2 (plan): in Jira move {s.key} to **Implementing**, or to **Rework** with a comment. "
            f"G3 (code, once the PR is ready for review and CI is green): {g3}. "
            f"G1 and G2 have no GitHub labels; the platform reads the Jira status.\n\n"
            f"Opened by ains-orchestrator (flow {self.flow.name}). Gates: {self._gates_text()}."
        )
        number = self.gh.pr_create(
            self.target.repo, s.branch or "", self.target.default_branch, title, body, draft=draft
        )
        if number is None:  # dry run
            number = 0
        self.store.update_session(s.key, pr_number=number)
        pr = self.gh.pr_view(
            self.target.repo, number, approval_mode=self.target.approval_mode, approval_label=self.target.approval_label
        )
        self.jira.add_remote_link(s.key, pr.url, f"PR #{number}")
        self.store.add_event(s.key, "pr.opened", {"pr": number, "draft": draft})
        return pr

    def _jira_status(self, key: str, status_key: str) -> None:
        self._jira_status_name(key, self.st[status_key])

    def _jira_status_name(self, key: str, status_name: str) -> None:
        if self.jira.status_of(key) != status_name:
            self.jira.transition(key, status_name)

    def _approve_status(self, gate: str) -> str:
        return self.flow.gates[gate].approve or ""

    def _gates_text(self) -> str:
        return " ".join(f"{g}={v}" for g, v in self.target.gates.items())

    def _latest_comment(self, key: str) -> str:
        issue = self.jira.get_issue(key, ["comment"])
        comments = (issue.get("fields", {}).get("comment", {}) or {}).get("comments", [])
        if not comments:
            return ""
        return _adf_text(comments[-1].get("body"))


def _adf_text(body: Any) -> str:
    """Flatten an Atlassian Document Format body to plain text."""
    if isinstance(body, str):
        return body
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                out.append(str(node.get("text", "")))
            for c in node.get("content", []) or []:
                walk(c)
            if node.get("type") == "paragraph":
                out.append("\n")
        elif isinstance(node, list):
            for c in node:
                walk(c)

    walk(body)
    return "".join(out).strip()
