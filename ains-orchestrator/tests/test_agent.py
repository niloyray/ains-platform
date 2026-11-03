"""T-I8-1 to T-I8-6 with an injected process runner. T-I8-7 is the live test (container)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ains_orchestrator.agent import (
    ClaudeCodeRunner,
    DryRunAgents,
    ProcResult,
    extract_verdict,
    minimal_env,
    parse_result,
    render_prompt,
)
from ains_orchestrator.config import load_agent

PARENT_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/home/x",
    "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-fake-for-tests",
    "ANTHROPIC_API_KEY": "sk-ant-api-should-never-pass",
    "ANTHROPIC_BASE_URL": "http://evil",
    "GH_TOKEN": "ghp_fake",
    "JIRA_API_TOKEN": "x",
}


class FakeProc:
    def __init__(self, stdout: str = "", rc: int = 0, timed_out: bool = False, fail_first: bool = False):
        self.calls: list[dict] = []
        self.stdout, self.rc, self.timed_out, self.fail_first = stdout, rc, timed_out, fail_first

    def __call__(self, argv, cwd, env, timeout_s, stdin=""):
        self.calls.append({"argv": argv, "cwd": cwd, "env": env, "timeout": timeout_s, "stdin": stdin})
        if self.fail_first and len(self.calls) == 1:
            return ProcResult(1, "", "transient")
        return ProcResult(self.rc, self.stdout, "", self.timed_out)


def result_json(text="done", turns=4, cost=0.12, is_error=False):
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": is_error,
            "result": text,
            "num_turns": turns,
            "duration_ms": 1234,
            "total_cost_usd": cost,
            "session_id": "s1",
        }
    )


@pytest.fixture
def runner_factory(config_root: Path):
    template = load_agent(config_root / "agents" / "claude-code.yaml")

    def make(proc: FakeProc, **kw) -> ClaudeCodeRunner:
        return ClaudeCodeRunner(template, config_root, parent_env=PARENT_ENV, runner=proc, **kw)

    return make


def test_T_I8_1_argv_shape(runner_factory, tmp_path: Path):
    proc = FakeProc(result_json())
    r = runner_factory(proc)
    res = r.run("implement", "AINSSB-1", tmp_path, {"summary": "s", "description": "d", "branch": "b", "repo": "o/r"})
    assert res.ok
    argv = proc.calls[0]["argv"]
    assert argv[:2] == ["claude", "-p"] and "--output-format" in argv and "json" in argv
    assert "--settings" in argv and "/app/claude-settings.json" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert argv[argv.index("--max-turns") + 1] == "80"
    assert "--bare" not in argv
    prompt = proc.calls[0]["stdin"]
    assert "AINSSB-1" in prompt and "implement stage agent" in prompt
    assert not any("stage agent" in a for a in argv)  # the prompt is never on the command line


def test_review_stage_is_read_only(runner_factory, tmp_path: Path):
    proc = FakeProc(result_json("## Summary\nfine\n\nVERDICT: request_changes"))
    res = runner_factory(proc).run("review", "AINSSB-1", tmp_path, {"pr": 7, "head_sha": "abc"})
    argv = proc.calls[0]["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "default" and "--disallowedTools" in argv
    assert argv[-1].startswith("Edit,Write")  # nothing after the tool list that could be swallowed
    assert res.extra["verdict"] == "request_changes"


def test_T_I8_2_env_is_minimal_and_never_anthropic(runner_factory, tmp_path: Path):
    proc = FakeProc(result_json())
    runner_factory(proc).run("spec", "AINSSB-1", tmp_path, {})
    env = proc.calls[0]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == PARENT_ENV["CLAUDE_CODE_OAUTH_TOKEN"]
    assert "PATH" in env and "HOME" in env
    assert not any(k.startswith("ANTHROPIC_") for k in env)
    assert "GH_TOKEN" not in env and "JIRA_API_TOKEN" not in env


def test_minimal_env_refuses_anthropic_passthrough():
    with pytest.raises(ValueError, match="D4"):
        minimal_env(PARENT_ENV, ["ANTHROPIC_API_KEY"])


def test_T_I8_3_cwd_is_workdir(runner_factory, tmp_path: Path):
    proc = FakeProc(result_json())
    runner_factory(proc).run("spec", "AINSSB-1", tmp_path / "AINSSB-1", {})
    assert proc.calls[0]["cwd"] == tmp_path / "AINSSB-1"


def test_T_I8_4_json_result_parsed(runner_factory, tmp_path: Path):
    res = runner_factory(FakeProc(result_json("hello", turns=9, cost=0.5))).run("spec", "K-1", tmp_path, {})
    assert (res.text, res.turns, res.cost_usd, res.duration_ms) == ("hello", 9, 0.5, 1234)
    assert res.extra["session_id"] == "s1"
    assert parse_result("not json at all")["unparsed"] is True
    arr = json.dumps([{"type": "system"}, {"type": "result", "result": "r", "num_turns": 1}])
    assert parse_result(arr)["result"] == "r"


def test_error_result_and_nonzero_exit(runner_factory, tmp_path: Path):
    res = runner_factory(FakeProc(result_json("boom", is_error=True))).run("spec", "K-1", tmp_path, {})
    assert not res.ok and "boom" in res.error
    res = runner_factory(FakeProc("", rc=2)).run("spec", "K-1", tmp_path, {})
    assert not res.ok and "exit 2" in res.error


def test_T_I8_5_retry_is_the_engines_job_runner_reports_failure(runner_factory, tmp_path: Path):
    proc = FakeProc(result_json(), fail_first=True)
    r = runner_factory(proc)
    assert not r.run("spec", "K-1", tmp_path, {}).ok
    assert r.run("spec", "K-1", tmp_path, {}).ok
    assert len(proc.calls) == 2


def test_T_I8_6_wall_clock_limit(runner_factory, tmp_path: Path):
    proc = FakeProc("", timed_out=True)
    r = runner_factory(proc, wall_clock_minutes=1)
    res = r.run("spec", "K-1", tmp_path, {})
    assert proc.calls[0]["timeout"] == 60 and not res.ok and res.extra.get("timeout") is True


def test_missing_token_is_reported_before_spawning(config_root: Path, tmp_path: Path):
    template = load_agent(config_root / "agents" / "claude-code.yaml")
    proc = FakeProc(result_json())
    r = ClaudeCodeRunner(template, config_root, parent_env={"PATH": "/usr/bin"}, runner=proc)
    res = r.run("spec", "K-1", tmp_path, {})
    assert not res.ok and "CLAUDE_CODE_OAUTH_TOKEN" in res.error and proc.calls == []


def test_render_prompt_and_rework_section():
    text = render_prompt("Story {key}: {summary} {unknown}", "K-1", {"summary": "S", "rework_comment": "split it"})
    assert text.startswith("Story K-1: S {unknown}") and "Rework requested" in text and "split it" in text


def test_extract_verdict():
    assert extract_verdict("...\nVERDICT: comment") == "comment"
    assert extract_verdict("VERDICT: Request Changes\n") == "request_changes"
    assert extract_verdict("no verdict line") == "comment"


def test_dry_run_agents_write_placeholders(tmp_path: Path):
    a = DryRunAgents()
    assert a.run("plan", "K-1", tmp_path, {}).ok
    assert (tmp_path / "specs" / "K-1" / "tasks.md").exists()
    assert a.run("review", "K-1", tmp_path, {}).extra["verdict"] == "comment"


def test_T_I9_1_shipped_prompts_render_with_context(shipped_root: Path):
    for stage in ("spec", "plan", "implement", "review"):
        tpl = (shipped_root / "agents" / "stages" / f"{stage}.md").read_text(encoding="utf-8")
        out = render_prompt(
            tpl,
            "AINSSB-1",
            {
                "summary": "Scaffold",
                "description": "Add README",
                "branch": "b",
                "repo": "niloyray/ains-sandbox",
                "pr": 7,
                "head_sha": "abc",
            },
        )
        assert "AINSSB-1" in out and "{key}" not in out and "{summary}" not in out
    review = (shipped_root / "agents" / "stages" / "review.md").read_text(encoding="utf-8")
    assert "VERDICT:" in review and "Do not approve" in review


def test_workspace_is_marked_trusted(runner_factory, tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    env = dict(PARENT_ENV, HOME=str(home))
    proc = FakeProc(result_json())
    template = load_agent(Path(runner_factory(proc).root) / "agents" / "claude-code.yaml")
    r = ClaudeCodeRunner(template, runner_factory(proc).root, parent_env=env, runner=proc)
    work = tmp_path / "AINSSB-1"
    work.mkdir()
    r.run("spec", "AINSSB-1", work, {})
    data = json.loads((home / ".claude.json").read_text())
    assert data["projects"][str(work)]["hasTrustDialogAccepted"] is True
    r.run("spec", "AINSSB-1", work, {})  # idempotent, file still valid JSON
    assert json.loads((home / ".claude.json").read_text())["projects"][str(work)]["hasTrustDialogAccepted"] is True


def test_render_prompt_appends_the_diff_section():
    from ains_orchestrator.agent import render_prompt

    out = render_prompt("Review {story_key}", "AINSSB-PR1", {"story_key": "AINSSB-1", "diff": "diff --git a b\n+x"})
    assert out.startswith("Review AINSSB-1") and "## Diff of the pull request" in out and "+x" in out
    assert "## Diff" not in render_prompt("x", "k", {})
