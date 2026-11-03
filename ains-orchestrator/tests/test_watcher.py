"""T-I10-4, T-I10-5 and the once-per-SHA rule for Human-lane PRs.

T-I10-1 to T-I10-3 (session PRs) live in test_flow.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from ains_orchestrator.config import Config, Target, TargetsFile, load_agent, load_flow
from ains_orchestrator.store import Store
from ains_orchestrator.watcher import PRWatcher
from ains_orchestrator.workspace import Workspace
from tests.fakes import FakeAgents, FakeGitHub


class LabelledGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.labelled: list[dict] = []

    def pr_list(self, repo, *, label=None, state="open"):
        return [p for p in self.labelled if label in [lb["name"] for lb in p["labels"]]]


@pytest.fixture
def world(tmp_path: Path, config_root: Path):
    flow = load_flow(config_root / "flows" / "story.yaml")
    agent = load_agent(config_root / "agents" / "claude-code.yaml")
    cfg = Config(
        root=config_root,
        targets=TargetsFile(targets={"AINSSB": Target(repo="niloyray/ains-sandbox")}),
        flows={"story": flow},
        agents={"claude-code": agent},
    )
    gh, agents, store = LabelledGitHub(), FakeAgents(), Store(tmp_path / "s.sqlite")
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=0)
    return {"gh": gh, "agents": agents, "store": store, "watcher": PRWatcher(cfg, store, gh, ws, agents)}


def labelled(number, sha, draft=False, label="ai-review"):
    return {
        "number": number,
        "headRefOid": sha,
        "headRefName": f"feature-{number}",
        "isDraft": draft,
        "labels": [{"name": label}],
        "title": "human change",
        "url": "u",
    }


def test_T_I10_4_human_lane_pr_reviewed_once_never_merged(world):
    gh, w, agents, store = world["gh"], world["watcher"], world["agents"], world["store"]
    gh.labelled.append(labelled(12, "sha-a"))
    rep = w.cycle()
    assert rep.reviewed == ["niloyray/ains-sandbox#12"] and gh.reviews[0][:2] == (12, "COMMENT")
    rep = w.cycle()
    assert rep.reviewed == [] and "already reviewed" in rep.skipped[0]
    assert [r[0] for r in agents.runs] == ["review"] and gh.merged == []
    assert store.get_session("AINSSB-PR12").reviewed_sha == "sha-a"


def test_new_push_gets_a_new_review(world):
    gh, w = world["gh"], world["watcher"]
    gh.labelled.append(labelled(12, "sha-a"))
    w.cycle()
    gh.labelled[0]["headRefOid"] = "sha-b"
    rep = w.cycle()
    assert rep.reviewed == ["niloyray/ains-sandbox#12"] and len(gh.reviews) == 2


def test_T_I10_5_draft_pr_ignored(world):
    gh, w = world["gh"], world["watcher"]
    gh.labelled.append(labelled(13, "sha-c", draft=True))
    rep = w.cycle()
    assert rep.reviewed == [] and "(draft)" in rep.skipped[0]


def test_session_pr_left_to_the_flow(world):
    gh, w, store = world["gh"], world["watcher"], world["store"]
    store.create_session("AINSSB-1", "AINSSB", "niloyray/ains-sandbox", "story", "wait_g3")
    store.update_session("AINSSB-1", pr_number=12)
    gh.labelled.append(labelled(12, "sha-a"))
    rep = w.cycle()
    assert rep.reviewed == [] and "session PR" in rep.skipped[0]


def test_request_changes_verdict_posted_as_such(world):
    gh, w, agents = world["gh"], world["watcher"], world["agents"]
    agents.review_verdict = "request_changes"
    gh.labelled.append(labelled(14, "sha-d"))
    w.cycle()
    assert gh.reviews[0][1] == "REQUEST_CHANGES"


def test_story_key_from_branch():
    from ains_orchestrator.watcher import story_key_from_branch

    assert story_key_from_branch("AINSSB-1-ci-smoke", "AINSSB") == "AINSSB-1"
    assert story_key_from_branch("feature/x", "AINSSB").startswith("none")
    assert story_key_from_branch("OTHER-3-x", "AINSSB").startswith("none")


def test_review_agent_receives_diff_and_story_key(world):
    w, gh, agents = world["watcher"], world["gh"], world["agents"]
    gh.labelled.append({**labelled(5, "sha-5"), "headRefName": "AINSSB-5-thing"})
    w.cycle()
    ctx = agents.runs[-1][2]
    assert ctx["story_key"] == "AINSSB-5" and ctx["diff"].startswith("diff --git")
    gh.labelled.append(labelled(6, "sha-6"))
    w.cycle()
    assert agents.runs[-1][2]["story_key"].startswith("none")
