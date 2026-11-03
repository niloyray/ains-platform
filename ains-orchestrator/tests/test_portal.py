"""Portal routes against a temp config root and store; no network (Jira and GitHub are absent or faked)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ains_orchestrator.portal import FSM_NODES, Portal, create_app, fsm_current
from ains_orchestrator.store import Store


@pytest.fixture
def client(config_root: Path, monkeypatch, tmp_path: Path):
    (config_root / "targets.yaml").write_text(
        "targets:\n  AINSSB:\n    repo: niloyray/ains-sandbox\n    approval_mode: label\n", encoding="utf-8"
    )
    monkeypatch.setenv("AINS_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("AINS_WORK_DIR", str(tmp_path / "work"))
    for v in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    (tmp_path / "work").mkdir(exist_ok=True)
    s = Store(tmp_path / "work" / "sessions.sqlite")
    s.create_session("AINSSB-7", "AINSSB", "niloyray/ains-sandbox", "story", "wait_g3")
    s.update_session("AINSSB-7", pr_number=7, branch="AINSSB-7-thing", gate_state={"summary": "a thing"})
    s.add_event("AINSSB-7", "agent.done", {"stage": "implement", "ok": True, "turns": 3, "result": "did it"})
    s.create_session("AINSSB-8", "AINSSB", "niloyray/ains-sandbox", "story", "blocked")
    s.update_session("AINSSB-8", last_error="implement crashed")
    s.close()
    (tmp_path / "work" / "heartbeat.json").write_text(
        '{"at": "2000-01-01T00:00:00Z", "interval_s": 60}', encoding="utf-8"
    )
    monkeypatch.setattr(Portal, "pr_state", lambda self, s, t: None)
    return TestClient(create_app(config_root))


def test_T_I20_1_dashboard_lists_waiting_stories_and_stale_poller(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AINSSB-7" in r.text and "G3 code" in r.text and "Blocked" in r.text
    assert "stale" in r.text  # heartbeat from 2000
    assert 'href="/projects/AINSSB"' in r.text  # nav


def test_T_I20_2_project_page_links_and_rows(client):
    r = client.get("/projects/AINSSB")
    assert r.status_code == 200
    assert "github.com/niloyray/ains-sandbox/pull/7" in r.text
    assert "specs/AINSSB-7/spec.md" in r.text and "Run verify" in r.text and "Remove AINSSB" in r.text
    assert client.get("/projects/NOPE", follow_redirects=False).status_code == 303


def test_T_I20_3_story_page_highlights_current_state(client):
    r = client.get("/stories/AINSSB-7")
    assert r.status_code == 200
    assert 'class="current"' in r.text and "did it" in r.text and "Event trail" in r.text
    assert client.get("/stories/NOPE-1", follow_redirects=False).status_code == 303


def test_T_I20_4_remove_needs_exact_confirmation(client):
    r = client.post("/projects/AINSSB/remove", data={"confirm": "wrong"}, follow_redirects=False)
    assert r.status_code == 303 and "error=confirm" in r.headers["location"]


def test_T_I20_5_api_status_json(client):
    d = client.get("/api/status").json()
    assert d["projects"] == ["AINSSB"] and {s["key"] for s in d["stories"]} == {"AINSSB-7", "AINSSB-8"}
    assert d["heartbeat"]["ok"] is False


def test_T_I20_6_platform_and_add_pages_render(client):
    assert "story" in client.get("/platform").text and "claude-code" in client.get("/platform").text
    assert "Onboard" in client.get("/add").text


def test_fsm_current_mapping():
    ids = {n["id"] for n in FSM_NODES}
    assert fsm_current("Spec Review", "wait_g1", None) == {"Spec Review", "draft PR"}
    assert fsm_current("Code Review", "wait_g3", {"draft": False, "ci": "FAILURE", "labels": []}) == {
        "Code Review",
        "ci / ci red",
    }
    assert fsm_current("Code Review", "wait_g3", {"draft": False, "ci": "SUCCESS", "ci_green": True, "labels": []}) == {
        "Code Review",
        "ci / ci green",
    }
    assert fsm_current("Done", "released", {"merged": True}) == {"Done", "merged", "production approved"}
    assert fsm_current("Staging", "released", None) == {"Staging", "staging approved"}
    assert all(x in ids for x in fsm_current("Blocked", "blocked", None))


def test_T_I21_2_add_form_greys_out_unsupported_options(client, config_root):
    import shutil
    from pathlib import Path

    shutil.copy(Path(__file__).resolve().parents[1] / "profiles.yaml", config_root / "profiles.yaml")
    html = client.get("/add").text
    assert "Legacy modernization" in html and "L5 Closed-loop" in html and "BMAD" in html
    assert html.count("disabled") >= 6  # unsupported classes, methods, levels and the maintain phase
    data = {
        "repo_name": "x",
        "jira_key": "X1",
        "jira_name": "x",
        "project_class": "legacy-modernization",
        "autonomy": "L3",
        "methodology": "spec-kit",
        "phases": ["intent", "spec", "plan", "develop", "test", "deploy"],
    }
    r = client.post("/add", data=data)
    assert r.status_code == 200 and "not supported yet" in r.text
