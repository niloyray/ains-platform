"""Storyboarding (I24): turn an idea into a Jira story with the story agent, and keep it in Jira.

The agent runs read-only in a clone of the project's default branch so the story fits the code that exists. The
result is a `StoryDraft`; the terminal (`ains story new`), the portal and the `/ains` skill all go through
`draft_story` and `write_story` here, so they behave the same.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ains_orchestrator.config import Target
from ains_orchestrator.errors import AinsError
from ains_orchestrator.jira import JiraClient

LANE_LABEL = "ai-native"
DRAFTS_KEY_SUFFIX = "-stories"  # workspace clone used by the story agent: <PROJECT>-stories on the default branch


@dataclass
class StoryDraft:
    summary: str
    description: str
    acceptance_criteria: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "description": self.description,
            "acceptance_criteria": list(self.acceptance_criteria),
            "out_of_scope": list(self.out_of_scope),
            "questions": list(self.questions),
        }

    def jira_description(self) -> str:
        """The description field as written to Jira: prose, then the two lists, so humans and agents read the same."""
        parts = [self.description.strip()]
        if self.acceptance_criteria:
            parts.append("Acceptance criteria:\n" + "\n".join(f"- {c}" for c in self.acceptance_criteria))
        if self.out_of_scope:
            parts.append("Out of scope:\n" + "\n".join(f"- {c}" for c in self.out_of_scope))
        return "\n\n".join(p for p in parts if p)

    def text(self) -> str:
        lines = [f"Summary: {self.summary}", "", self.description.strip()]
        if self.acceptance_criteria:
            lines += ["", "Acceptance criteria:"] + [f"  - {c}" for c in self.acceptance_criteria]
        if self.out_of_scope:
            lines += ["", "Out of scope:"] + [f"  - {c}" for c in self.out_of_scope]
        if self.questions:
            lines += ["", "Questions only you can answer:"] + [f"  ? {q}" for q in self.questions]
        return "\n".join(lines)


def parse_draft(reply: str) -> StoryDraft:
    """The agent replies with one fenced JSON object; be tolerant of prose around it."""
    m = re.search(r"```json\s*(\{.*?\})\s*```", reply, flags=re.S)
    raw = m.group(1) if m else None
    if raw is None:
        start, end = reply.find("{"), reply.rfind("}")
        if start < 0 or end <= start:
            raise AinsError("the story agent did not reply with a JSON story")
        raw = reply[start : end + 1]
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AinsError(f"the story agent's JSON could not be parsed: {e}") from e
    if not isinstance(d, dict) or not d.get("summary") or not d.get("description"):
        raise AinsError("the story agent's reply lacks a summary or a description")

    def lst(k: str) -> list[str]:
        v = d.get(k) or []
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else [str(v).strip()]

    return StoryDraft(
        summary=str(d["summary"]).strip()[:120],
        description=str(d["description"]).strip(),
        acceptance_criteria=lst("acceptance_criteria"),
        out_of_scope=lst("out_of_scope"),
        questions=lst("questions"),
    )


def split_description(text: str) -> tuple[str, list[str], list[str]]:
    """Inverse of `jira_description` for stories written by this module (best effort for hand-written ones)."""
    body, ac, oos = [], [], []
    bucket = body
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("acceptance criteria"):
            bucket = ac
            continue
        if s.lower().startswith("out of scope"):
            bucket = oos
            continue
        if bucket is body:
            body.append(line)
        elif s.startswith(("- ", "* ")):
            bucket.append(s[2:].strip())
        elif s:
            bucket.append(s)
    return "\n".join(body).strip(), ac, oos


def draft_story(
    agents: Any,
    workspace: Any,
    target_key: str,
    target: Target,
    idea: str,
    *,
    existing: StoryDraft | None = None,
    feedback: str | None = None,
) -> StoryDraft:
    """Run the story agent once. `existing` is the current draft or Jira story when refining or updating."""
    workdir = workspace.ensure(f"{target_key}{DRAFTS_KEY_SUFFIX}", target, target.default_branch)
    ctx = {
        "repo": target.repo,
        "project_key": target_key,
        "branch": target.default_branch,
        "idea": idea.strip(),
        "existing": ("## The current story (rewrite it, keep what still holds)\n\n" + existing.text())
        if existing
        else "",
        "feedback": ("## Feedback from the person on the previous draft\n\n" + feedback.strip()) if feedback else "",
        "summary": idea.strip()[:80],
        "description": idea.strip(),
    }
    res = agents.run("story", f"{target_key}{DRAFTS_KEY_SUFFIX}", Path(workdir), ctx)
    if not res.ok:
        raise AinsError(f"story agent failed: {res.error or 'no reply'}")
    return parse_draft(res.text or "")


def write_story(jira: JiraClient, project_key: str, draft: StoryDraft, *, ready: bool = False) -> str:
    """Create the story in Jira with the lane label; move it to Ready when asked. Returns the key."""
    key = jira.create_issue(project_key, draft.summary, draft.jira_description(), labels=[LANE_LABEL])
    if ready:
        jira.transition(key, "Ready")
    return key


def update_story(jira: JiraClient, key: str, draft: StoryDraft, *, ready: bool = False) -> None:
    jira.update_issue(key, summary=draft.summary, description=draft.jira_description())
    if ready:
        jira.transition(key, "Ready")


def read_story(jira: JiraClient, key: str) -> StoryDraft:
    from ains_orchestrator.flow import _adf_text

    f = jira.get_issue(key, ["summary", "description"]).get("fields", {})
    desc = _adf_text(f.get("description")) if f.get("description") else ""
    body, ac, oos = split_description(desc)
    return StoryDraft(summary=f.get("summary", ""), description=body, acceptance_criteria=ac, out_of_scope=oos)
