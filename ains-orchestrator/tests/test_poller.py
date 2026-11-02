"""T-I6-1 to T-I6-5 and T-I7-7 (resume after block)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ains_orchestrator.config import Config, Target, TargetsFile, load_agent, load_flow
from ains_orchestrator.flow import FlowEngine
from ains_orchestrator.poller import Poller
from ains_orchestrator.store import Store
from ains_orchestrator.workspace import Workspace
from tests.fakes import FakeAgents, FakeGitHub, FakeJira


@pytest.fixture
def world(tmp_path: Path, config_root: Path):
    flow = load_flow(config_root / "flows" / "story.yaml")
    agent = load_agent(config_root / "agents" / "claude-code.yaml")
    targets = TargetsFile(targets={"AINSSB": Target(repo="niloyray/ains-sandbox")})
    cfg = Config(root=config_root, targets=targets, flows={"story": flow}, agents={"claude-code": agent})
    jira, gh, agents = FakeJira(), FakeGitHub(), FakeAgents()
    store = Store(tmp_path / "s.sqlite")
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=0)

    def engine_for(tkey: str) -> FlowEngine:
        return FlowEngine(
            flow=flow,
            target_key=tkey,
            target=cfg.targets.targets[tkey],
            store=store,
            jira=jira,
            github=gh,
            workspace=ws,
            agents=agents,
            max_attempts=2,
        )

    return {
        "cfg": cfg,
        "jira": jira,
        "gh": gh,
        "agents": agents,
        "store": store,
        "poller": Poller(cfg, store, jira, engine_for),
    }


def test_jql_covers_all_targets(world):
    assert world["poller"].jql() == (
        "project in (AINSSB) AND labels = ai-native AND status = Ready ORDER BY created ASC"
    )


def test_T_I6_1_ready_story_is_claimed(world):
    jira, store, p = world["jira"], world["store"], world["poller"]
    jira.add_issue("AINSSB-1", "Scaffold")
    rep = p.cycle()
    assert rep.claimed == ["AINSSB-1"]
    assert jira.status_of("AINSSB-1") == "Spec Review"  # claimed and the spec stage already ran in the same cycle
    s = store.get_session("AINSSB-1")
    assert s and s.stage == "wait_g1" and "picked up" in jira.comments["AINSSB-1"][0]


def test_T_I6_2_same_story_claimed_once(world):
    jira, p, agents = world["jira"], world["poller"], world["agents"]
    jira.add_issue("AINSSB-1", "Scaffold")
    p.cycle()
    jira.set_status("AINSSB-1", "Ready")  # even if someone drags it back to Ready
    rep = p.cycle()
    assert rep.claimed == [] and [r[0] for r in agents.runs] == ["spec"]


def test_T_I6_3_story_without_target_is_skipped(world):
    jira, p, store = world["jira"], world["poller"], world["store"]
    jira.add_issue("OTHER-1", "No mapping")
    rep = p.cycle()
    assert rep.skipped == ["OTHER-1"] and store.get_session("OTHER-1") is None


def test_T_I6_4_jira_unreachable_does_not_crash(world):
    jira, p = world["jira"], world["poller"]
    jira.fail_search = True
    rep = p.cycle()
    assert rep.errors and "search failed" in rep.errors[0]
    jira.fail_search = False
    jira.add_issue("AINSSB-1", "Scaffold")
    assert p.cycle().claimed == ["AINSSB-1"]


def test_T_I6_5_one_story_at_a_time_per_target(world):
    jira, p = world["jira"], world["poller"]
    jira.add_issue("AINSSB-1", "First")
    jira.add_issue("AINSSB-2", "Second")
    rep = p.cycle()
    assert rep.claimed == ["AINSSB-1"] and rep.skipped == ["AINSSB-2"]


def test_T_I7_7_blocked_story_resumes_at_blocked_stage(world):
    jira, p, store, agents = world["jira"], world["poller"], world["store"], world["agents"]
    jira.add_issue("AINSSB-1", "Scaffold")
    p.cycle()  # claimed, spec done, wait_g1
    jira.set_status("AINSSB-1", "Planning")
    agents.fail_times["plan"] = 2
    p.cycle()  # wait_g1 sees Planning -> stage plan (one transition per cycle)
    p.cycle()  # plan fails once (retry pending)
    p.cycle()  # fails again -> blocked
    assert store.get_session("AINSSB-1").stage == "blocked" and jira.status_of("AINSSB-1") == "Blocked"
    jira.set_status("AINSSB-1", "Ready")  # human unblocks
    p.cycle()
    resumed = [e for e in store.events("AINSSB-1") if e.kind == "resumed"]
    assert resumed and resumed[-1].detail["stage"] == "plan"
    assert store.get_session("AINSSB-1").stage in ("plan", "wait_g2")
    assert [r[0] for r in agents.runs].count("spec") == 1


def test_resume_after_post_agent_failure_skips_the_agent(world):
    """The spec agent succeeded but the push failed: on resume the agent must not run again."""
    jira, p, store, agents, gh = world["jira"], world["poller"], world["store"], world["agents"], world["gh"]
    jira.add_issue("AINSSB-1", "Scaffold")

    def failing_push(cwd, branch):
        raise __import__("ains_orchestrator.errors", fromlist=["GitHubError"]).GitHubError(
            "push failed: no credentials"
        )

    gh.push = failing_push
    p.cycle()  # claim, spec agent ok, push fails -> blocked
    assert store.get_session("AINSSB-1").stage == "blocked"
    assert [r[0] for r in agents.runs] == ["spec"]
    gh.push = lambda cwd, branch: gh.calls.append(f"push {branch}")
    jira.set_status("AINSSB-1", "Ready")
    p.cycle()  # resume: outputs present, agent skipped, push succeeds
    s = store.get_session("AINSSB-1")
    assert s.stage == "wait_g1" and [r[0] for r in agents.runs] == ["spec"]
    assert any(e.kind == "agent.skipped" for e in store.events("AINSSB-1"))
