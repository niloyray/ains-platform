"""I6: poll Jira for Ready AI-native stories, claim them, and advance every active session."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ains_orchestrator.config import Config
from ains_orchestrator.errors import AinsError
from ains_orchestrator.flow import FlowEngine, Story
from ains_orchestrator.jira import JiraClient
from ains_orchestrator.store import Store

log = logging.getLogger("ains.poller")
LANE_LABEL = "ai-native"


@dataclass
class CycleReport:
    claimed: list[str] = field(default_factory=list)
    stepped: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


EngineFactory = Callable[[str], FlowEngine]  # target key -> engine


class Poller:
    def __init__(self, config: Config, store: Store, jira: JiraClient, engine_for: EngineFactory) -> None:
        self.config, self.store, self.jira, self.engine_for = config, store, jira, engine_for

    def jql(self) -> str:
        keys = ", ".join(sorted(self.config.targets.targets))
        if not keys:
            return ""
        return f"project in ({keys}) AND labels = {LANE_LABEL} AND status = Ready ORDER BY created ASC"

    def cycle(self) -> CycleReport:
        rep = CycleReport()
        self._claim_ready(rep)
        self._step_active(rep)
        return rep

    # ------------------------------------------------------------------ claim
    def _claim_ready(self, rep: CycleReport) -> None:
        jql = self.jql()
        if not jql:
            return
        try:
            issues = self.jira.search(jql, fields=["summary", "description", "status", "project"])
        except AinsError as e:
            rep.errors.append(f"jira search failed: {e}")
            log.warning("poll: %s", e)
            return
        active = self.store.active_keys()
        busy_targets = {
            s.target
            for s in self.store.list_sessions()
            if s.stage not in ("released", "abandoned", "blocked", "pr_reviewed")
        }
        for issue in issues:
            key = issue["key"]
            tkey = key.split("-")[0]
            if tkey not in self.config.targets.targets:
                rep.skipped.append(key)
                continue
            existing = self.store.get_session(key)
            if existing and existing.stage == "blocked":
                # a human moved a blocked story back to Ready: resume at the blocked stage
                self._resume(existing.key, rep)
                continue
            if key in active:
                continue
            if tkey in busy_targets:
                rep.skipped.append(key)  # one story at a time per target
                continue
            story = Story(key, issue["fields"].get("summary", ""), _plain(issue["fields"].get("description")))
            try:
                self.engine_for(tkey).claim(story)
                rep.claimed.append(key)
                busy_targets.add(tkey)
            except AinsError as e:
                rep.errors.append(f"{key}: claim failed: {e}")
                log.warning("claim %s failed: %s", key, e)

    def _resume(self, key: str, rep: CycleReport) -> None:
        events = [
            e for e in self.store.events(key) if e.kind in ("stage.done", "claimed", "gate.rework", "gate.passed")
        ]
        stage = _stage_before_block(self.store.get_session(key), events)
        session = self.store.get_session(key)
        gs = dict(session.gate_state) if session else {}
        if _last_agent_ok(self.store.events(key), stage):
            gs["skip_agent"] = stage
        self.store.update_session(key, stage=stage, attempts=0, last_error=None, gate_state=gs)
        self.store.add_event(key, "resumed", {"stage": stage})
        rep.stepped[key] = f"resumed at {stage}"

    # ------------------------------------------------------------------ step
    def _step_active(self, rep: CycleReport) -> None:
        for s in self.store.list_sessions():
            if s.stage in ("released", "abandoned", "blocked", "pr_reviewed"):
                continue
            try:
                issue = self.jira.get_issue(s.key, ["summary", "description"])
                story = Story(s.key, issue["fields"].get("summary", ""), _plain(issue["fields"].get("description")))
                new = self.engine_for(s.target).step(s, story)
                rep.stepped[s.key] = new
            except AinsError as e:
                rep.errors.append(f"{s.key}: {e}")
                log.warning("step %s failed: %s", s.key, e)


def _last_agent_ok(events, stage: str) -> bool:
    """True when the most recent agent run of `stage` finished ok (its output is still in the workspace)."""
    for e in reversed(events):
        if e.kind == "agent.done" and e.detail.get("stage") == stage:
            return bool(e.detail.get("ok"))
        if e.kind == "agent.start" and e.detail.get("stage") == stage:
            return False
    return False


def _stage_before_block(session, events) -> str:
    """The stage to resume at: the last stage that was running when the block happened."""
    if session is None:
        return "spec"
    for e in reversed(events):
        if e.kind == "stage.done":
            nxt = {"spec": "plan", "plan": "implement", "implement": "review"}.get(e.detail.get("stage", ""), "spec")
            return nxt
        if e.kind == "gate.rework":
            return e.detail.get("stage", "spec")
    return "spec"


def _plain(description) -> str:
    from ains_orchestrator.flow import _adf_text

    return _adf_text(description) if description else ""
