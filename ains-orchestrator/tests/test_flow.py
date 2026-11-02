"""T-I7-1 to T-I7-7: the state machine, table-driven, with in-memory fakes."""

from __future__ import annotations

from pathlib import Path

import pytest

from ains_orchestrator.config import Target, load_flow
from ains_orchestrator.flow import FlowEngine, Story
from ains_orchestrator.store import Store
from ains_orchestrator.workspace import Workspace
from tests.fakes import FakeAgents, FakeGitHub, FakeJira

STORY = Story("AINSSB-1", "Repository scaffold", "Add a README section and a health-check script.")


@pytest.fixture
def world(tmp_path: Path, shipped_root: Path):
    flow = load_flow(shipped_root / "flows" / "story.yaml")
    jira, gh, agents = FakeJira(), FakeGitHub(), FakeAgents()
    jira.add_issue(STORY.key, STORY.summary, "Ready", STORY.description)
    store = Store(tmp_path / "sessions.sqlite")
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=0)

    def make(gates: dict[str, str] | None = None, approval_mode: str = "review") -> FlowEngine:
        t = Target(repo="niloyray/ains-sandbox", gates=gates or {}, approval_mode=approval_mode)
        return FlowEngine(
            flow=flow,
            target_key="AINSSB",
            target=t,
            store=store,
            jira=jira,
            github=gh,
            workspace=ws,
            agents=agents,
            max_attempts=2,
        )

    return {"flow": flow, "jira": jira, "gh": gh, "agents": agents, "store": store, "ws": ws, "make": make}


def run_until(engine: FlowEngine, store: Store, key: str, stop: str, limit: int = 30) -> str:
    stage = store.get_session(key).stage
    for _ in range(limit):
        if stage == stop:
            return stage
        stage = engine.step(store.get_session(key), STORY)
    return stage


def test_T_I7_1_happy_path_all_gates_on(world):
    e, st, jira, gh = world["make"](), world["store"], world["jira"], world["gh"]
    e.claim(STORY)
    s = st.get_session(STORY.key)
    assert s.stage == "spec" and s.branch == "AINSSB-1-repository-scaffold"
    assert jira.status_of(STORY.key) == "Specifying" and "picked up" in jira.comments[STORY.key][0]

    assert e.step(s, STORY) == "wait_g1"
    assert jira.status_of(STORY.key) == "Spec Review" and gh.prs[7].is_draft
    assert jira.links[STORY.key][0][1] == "PR #7"
    assert e.step(st.get_session(STORY.key), STORY) == "wait_g1"  # nothing happens while the human thinks

    jira.set_status(STORY.key, "Planning")  # G1 approve
    assert e.step(st.get_session(STORY.key), STORY) == "plan"
    assert e.step(st.get_session(STORY.key), STORY) == "wait_g2"
    assert jira.status_of(STORY.key) == "Plan Review"

    jira.set_status(STORY.key, "Implementing")  # G2 approve
    assert e.step(st.get_session(STORY.key), STORY) == "implement"
    assert e.step(st.get_session(STORY.key), STORY) == "review"
    assert not gh.prs[7].is_draft and jira.status_of(STORY.key) == "Code Review"

    assert e.step(st.get_session(STORY.key), STORY) == "wait_g3"
    assert gh.reviews[0][1] == "COMMENT" and st.get_session(STORY.key).reviewed_sha == "sha-1"

    assert e.step(st.get_session(STORY.key), STORY) == "wait_g3"  # no approval yet
    gh.ci(7, "SUCCESS")
    gh.approve(7)  # G3
    assert e.step(st.get_session(STORY.key), STORY) == "merge"
    assert e.step(st.get_session(STORY.key), STORY) == "released"
    assert gh.merged == [7] and not (world["ws"].path(STORY.key)).exists()
    kinds = [ev.kind for ev in st.events(STORY.key)]
    assert kinds[0] == "session.created" and kinds[-1] == "merged"
    assert kinds.count("gate.passed") == 3
    assert [r[0] for r in world["agents"].runs] == ["spec", "plan", "implement", "review"]


def test_T_I7_2_gate_on_waits_without_respawn(world):
    e, st, agents = world["make"](), world["store"], world["agents"]
    e.claim(STORY)
    run_until(e, st, STORY.key, "wait_g1")
    for _ in range(5):
        assert e.step(st.get_session(STORY.key), STORY) == "wait_g1"
    assert [r[0] for r in agents.runs] == ["spec"]


def test_T_I7_3_all_gates_off_runs_to_released(world):
    gates = {g: "off" for g in ("G1", "G2", "G3", "G4", "G5")}
    e, st, gh, jira = world["make"](gates), world["store"], world["gh"], world["jira"]
    e.claim(STORY)
    stage = None
    for _ in range(20):
        s = st.get_session(STORY.key)
        if s.stage == "released":
            break
        if 7 in gh.prs:
            gh.ci(7, "SUCCESS")
        stage = e.step(s, STORY)
    assert st.get_session(STORY.key).stage == "released", stage
    comments = "\n".join(jira.comments[STORY.key])
    assert "G1 auto-approved" in comments and "G2 auto-approved" in comments and "G3 auto-approved" in comments
    assert jira.status_of(STORY.key) in ("Code Review", "Implementing")  # Staging/Done are set by Actions


def test_T_I7_4_rework_at_g2_feeds_comment_to_plan_agent(world):
    e, st, jira, agents = world["make"](), world["store"], world["jira"], world["agents"]
    e.claim(STORY)
    run_until(e, st, STORY.key, "wait_g1")
    jira.set_status(STORY.key, "Planning")
    run_until(e, st, STORY.key, "wait_g2")
    jira.human_comment(STORY.key, "split task 3 into two")
    jira.set_status(STORY.key, "Rework")
    assert e.step(st.get_session(STORY.key), STORY) == "plan"
    assert jira.status_of(STORY.key) == "Planning"
    assert e.step(st.get_session(STORY.key), STORY) == "wait_g2"
    plan_runs = [ctx for stage, _, ctx in agents.runs if stage == "plan"]
    assert len(plan_runs) == 2 and plan_runs[1]["rework_comment"] == "split task 3 into two"
    assert st.get_session(STORY.key).gate_state["rework_count"] == 1


def test_T_I7_5_changes_requested_at_g3_returns_to_implement(world):
    e, st, jira, gh, agents = world["make"](), world["store"], world["jira"], world["gh"], world["agents"]
    e.claim(STORY)
    run_until(e, st, STORY.key, "wait_g1")
    jira.set_status(STORY.key, "Planning")
    run_until(e, st, STORY.key, "wait_g2")
    jira.set_status(STORY.key, "Implementing")
    run_until(e, st, STORY.key, "wait_g3")
    gh.request_changes(7)
    assert e.step(st.get_session(STORY.key), STORY) == "implement"
    gh.prs[7].review_decision = "REVIEW_REQUIRED"
    gh.prs[7].human_changes_requested = False
    assert e.step(st.get_session(STORY.key), STORY) == "review"
    impl_runs = [ctx for stage, _, ctx in agents.runs if stage == "implement"]
    assert len(impl_runs) == 2 and "rework_comment" in impl_runs[1]


def test_T_I7_6_agent_fails_twice_blocks_and_reports(world):
    e, st, jira, agents = world["make"](), world["store"], world["jira"], world["agents"]
    agents.fail_times["spec"] = 2
    e.claim(STORY)
    assert e.step(st.get_session(STORY.key), STORY) == "spec"  # retry pending
    assert st.get_session(STORY.key).attempts == 1
    assert e.step(st.get_session(STORY.key), STORY) == "blocked"
    assert jira.status_of(STORY.key) == "Blocked" and "blocked" in jira.comments[STORY.key][-1].lower()
    assert e.step(st.get_session(STORY.key), STORY) == "blocked"  # stays


def test_missing_output_counts_as_failure(world):
    e, st, agents = world["make"](), world["store"], world["agents"]
    agents.produce = False
    e.claim(STORY)
    e.step(st.get_session(STORY.key), STORY)
    assert st.get_session(STORY.key).last_error and "without producing" in st.get_session(STORY.key).last_error


def test_new_commits_after_review_trigger_a_new_review(world):
    e, st, jira, gh = world["make"](), world["store"], world["jira"], world["gh"]
    e.claim(STORY)
    run_until(e, st, STORY.key, "wait_g1")
    jira.set_status(STORY.key, "Planning")
    run_until(e, st, STORY.key, "wait_g2")
    jira.set_status(STORY.key, "Implementing")
    run_until(e, st, STORY.key, "wait_g3")
    gh.new_commit("sha-2")
    assert e.step(st.get_session(STORY.key), STORY) == "review"
    assert e.step(st.get_session(STORY.key), STORY) == "wait_g3"
    assert len(gh.reviews) == 2 and st.get_session(STORY.key).reviewed_sha == "sha-2"


def test_label_approval_mode_merges_on_label(world):
    e, st, jira, gh = world["make"](approval_mode="label"), world["store"], world["jira"], world["gh"]
    e.claim(STORY)
    run_until(e, st, STORY.key, "wait_g1")
    jira.set_status(STORY.key, "Planning")
    run_until(e, st, STORY.key, "wait_g2")
    jira.set_status(STORY.key, "Implementing")
    run_until(e, st, STORY.key, "wait_g3")
    gh.ci(7, "SUCCESS")
    assert e.step(st.get_session(STORY.key), STORY) == "wait_g3"
    gh.prs[7].labels.append("g3-approved")
    assert e.step(st.get_session(STORY.key), STORY) == "merge"
    assert e.step(st.get_session(STORY.key), STORY) == "released"


def _to_g3(world, approval_mode="review"):
    e, st, jira = world["make"](approval_mode=approval_mode), world["store"], world["jira"]
    e.claim(STORY)
    run_until(e, st, STORY.key, "wait_g1")
    jira.set_status(STORY.key, "Planning")
    run_until(e, st, STORY.key, "wait_g2")
    jira.set_status(STORY.key, "Implementing")
    run_until(e, st, STORY.key, "wait_g3")
    return e


def test_ci_failure_at_g3_sends_story_back_to_implement_with_the_log(world):
    st, jira, gh, agents = world["store"], world["jira"], world["gh"], world["agents"]
    e = _to_g3(world)
    gh.ci(7, "FAILURE")
    assert e.step(st.get_session(STORY.key), STORY) == "implement"
    s = st.get_session(STORY.key)
    assert s.gate_state["ci_rework_rounds"] == 1 and "PLW1510" in s.gate_state["rework_implement"]
    assert "CI failed" in jira.comments[STORY.key][-1]
    gh.prs[7].checks = {}
    assert e.step(st.get_session(STORY.key), STORY) == "review"  # implement agent ran again
    assert [r[0] for r in agents.runs].count("implement") == 2
    assert "PLW1510" in agents.runs[-1][2]["rework_comment"]


def test_ci_failure_rounds_are_bounded(world):
    st, gh = world["store"], world["gh"]
    e = _to_g3(world)
    for _round in range(2):
        gh.ci(7, "FAILURE")
        assert e.step(st.get_session(STORY.key), STORY) == "implement"
        gh.prs[7].checks = {}
        run_until(e, st, STORY.key, "wait_g3")
    gh.ci(7, "FAILURE")
    assert e.step(st.get_session(STORY.key), STORY) == "blocked"
    assert "a human must look" in st.get_session(STORY.key).last_error


def test_label_mode_rework_label_returns_to_implement_with_the_comment(world):
    st, gh, agents = world["store"], world["gh"], world["agents"]
    e = _to_g3(world, approval_mode="label")
    gh.ci(7, "SUCCESS")
    gh.comments[7] = [{"author": "niloyray", "body": "please rename the script to health_check.py", "createdAt": "t"}]
    gh.prs[7].labels.append("g3-rework")
    assert e.step(st.get_session(STORY.key), STORY) == "implement"
    assert "g3-rework" not in gh.prs[7].labels
    assert e.step(st.get_session(STORY.key), STORY) == "review"
    assert agents.runs[-1][2]["rework_comment"] == "please rename the script to health_check.py"
