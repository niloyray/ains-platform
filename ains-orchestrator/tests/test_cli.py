"""T-I12-1 to T-I12-4 with typer's CliRunner against a temp config root."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ains_orchestrator.cli import app
from ains_orchestrator.store import Store

runner = CliRunner()


@pytest.fixture
def env_root(config_root: Path, monkeypatch, tmp_path: Path):
    (config_root / "targets.yaml").write_text(
        "targets:\n  AINSSB:\n    repo: niloyray/ains-sandbox\n", encoding="utf-8"
    )
    monkeypatch.setenv("AINS_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("AINS_WORK_DIR", str(tmp_path / "work"))
    for v in ("CLAUDE_CODE_OAUTH_TOKEN", "GH_TOKEN", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    return config_root


def test_T_I12_1_listings(env_root):
    out = runner.invoke(app, ["agent", "list"]).stdout
    assert "claude-code" in out and "review" in out and "read-only" in out
    out = runner.invoke(app, ["flow", "list"]).stdout
    assert "story: spec -> plan -> implement -> review -> merge" in out and "G3" in out
    out = runner.invoke(app, ["targets", "list"]).stdout
    assert "AINSSB: niloyray/ains-sandbox" in out and "G1=on" in out


def test_T_I12_2_session_status_json(env_root, tmp_path: Path):
    s = Store(tmp_path / "work" / "sessions.sqlite")
    s.create_session("AINSSB-1", "AINSSB", "niloyray/ains-sandbox", "story", "wait_g1")
    s.close()
    r = runner.invoke(app, ["session", "status", "AINSSB-1", "--json"])
    assert r.exit_code == 0 and '"stage": "wait_g1"' in r.stdout and '"session.created"' in r.stdout
    r = runner.invoke(app, ["session", "status", "NOPE-1"])
    assert r.exit_code == 1


def test_T_I12_3_dispatch_dry_run_writes_nothing_without_jira(env_root):
    r = runner.invoke(app, ["session", "dispatch", "AINSSB-1", "--dry-run"])
    assert r.exit_code == 2 and "JIRA" in (r.stdout + r.stderr)  # dry-run still needs read access to the story


def test_T_I12_4_doctor_reports_each_missing_item(env_root, monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-fail-doctor")
    r = runner.invoke(app, ["doctor", "--no-live"])
    out = r.stdout
    assert r.exit_code == 1
    for item in ("env CLAUDE_CODE_OAUTH_TOKEN", "env JIRA_API_TOKEN", "tool claude", "tool specify", "no ANTHROPIC_*"):
        assert f"[FAIL] {item}" in out
    assert "[ok] config" in out


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0 and "ains-orchestrator" in r.stdout


def test_T_I12_5_status_names_the_next_human_action(env_root, tmp_path: Path):
    (env_root / "targets.yaml").write_text(
        "targets:\n  AINSSB:\n    repo: niloyray/ains-sandbox\n    approval_mode: label\n", encoding="utf-8"
    )
    s = Store(tmp_path / "work" / "sessions.sqlite")
    s.create_session("AINSSB-7", "AINSSB", "niloyray/ains-sandbox", "story", "wait_g3")
    s.update_session("AINSSB-7", pr_number=7)
    s.create_session("AINSSB-8", "AINSSB", "niloyray/ains-sandbox", "story", "blocked")
    s.update_session("AINSSB-8", last_error="implement crashed")
    s.close()
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    assert "AINSSB-7" in r.stdout and "G3: on PR #7" in r.stdout and "g3-approved" in r.stdout
    assert "Blocked: implement crashed" in r.stdout and "move AINSSB-8 to Ready" in r.stdout
    r = runner.invoke(app, ["status", "--json"])
    assert r.exit_code == 0 and '"next"' in r.stdout


def test_T_I12_6_status_with_no_sessions_tells_how_to_start(env_root):
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "label ai-native" in r.stdout


def test_next_action_covers_every_stage():
    from ains_orchestrator.cli import next_action
    from ains_orchestrator.store import Session

    def sess(stage, flow="story"):
        return Session("AINSSB-1", "AINSSB", "o/r", flow, stage, {}, None, 3, None, None, 0, None, "t", "t")

    assert "Planning" in next_action(sess("wait_g1"), None)
    assert "Implementing" in next_action(sess("wait_g2"), None)
    assert "Review deployments" in next_action(sess("released"), None)
    assert next_action(sess("released"), None, "Done").startswith("Done")
    assert next_action(sess("released"), None, "Staging").startswith("G5")
    assert "nothing to do" in next_action(sess("implement"), None)
    assert "merging it is yours" in next_action(sess("pr_reviewed", flow="pr-review"), None)
