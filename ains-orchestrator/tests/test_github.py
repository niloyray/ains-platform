"""T-I4-1 to T-I4-5 with an injected fake runner. T-I4-6 is the live test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ains_orchestrator.errors import GitHubError, MergeRefused
from ains_orchestrator.github import AGENT_REVIEW_MARKER, GitHubClient, RunResult


class FakeRunner:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.responses: dict[str, RunResult] = {}
        self.fail_once: dict[str, str] = {}  # prefix -> stderr for a single network failure

    def __call__(self, argv: list[str], cwd: Path | None) -> RunResult:
        self.calls.append(argv)
        key = " ".join(argv[:4])
        if key in self.fail_once:
            return RunResult(1, "", self.fail_once.pop(key))
        for prefix, r in self.responses.items():
            if " ".join(argv).startswith(prefix):
                return r
        return RunResult(0, "", "")


def pr_json(**over):
    d = {
        "number": 7,
        "url": "https://github.com/niloyray/ains-sandbox/pull/7",
        "title": "AINSSB-1: scaffold",
        "headRefOid": "abc123",
        "headRefName": "AINSSB-1-scaffold",
        "isDraft": False,
        "state": "OPEN",
        "reviewDecision": "REVIEW_REQUIRED",
        "labels": [],
        "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}],
        "reviews": [],
        "mergedAt": None,
    }
    d.update(over)
    return json.dumps(d)


def test_T_I4_1_clone_branch_commit_push_sequence(tmp_path: Path):
    r = FakeRunner()
    r.responses["git status --porcelain"] = RunResult(0, " M file.py\n", "")
    c = GitHubClient(runner=r, sleep=lambda s: None)
    c.clone("niloyray/ains-sandbox", tmp_path / "ws")
    c.create_branch(tmp_path / "ws", "AINSSB-1-scaffold")
    assert c.commit_all(tmp_path / "ws", "AINSSB-1: add scaffold\n\nstage: implement")
    c.push(tmp_path / "ws", "AINSSB-1-scaffold")
    flat = [" ".join(a) for a in r.calls]
    assert flat[0].startswith("gh repo clone niloyray/ains-sandbox")
    assert "git checkout -B AINSSB-1-scaffold" in flat
    assert any(a.startswith("git commit -q -m AINSSB-1: add scaffold") for a in flat)
    assert flat[-1] == "git push -u origin AINSSB-1-scaffold"


def test_commit_all_without_changes_is_noop(tmp_path: Path):
    r = FakeRunner()
    r.responses["git status --porcelain"] = RunResult(0, "", "")
    assert GitHubClient(runner=r).commit_all(tmp_path, "AINSSB-1: nothing") is False
    assert not any(a[:2] == ["git", "commit"] for a in r.calls)


def test_T_I4_2_pr_create_ready_merge_refused_without_approval():
    r = FakeRunner()
    r.responses["gh pr create"] = RunResult(0, "https://github.com/niloyray/ains-sandbox/pull/7\n", "")
    r.responses["gh pr view 7"] = RunResult(0, pr_json(), "")
    c = GitHubClient(runner=r)
    assert c.pr_create("niloyray/ains-sandbox", "AINSSB-1-scaffold", "main", "AINSSB-1: scaffold", "body") == 7
    assert "--draft" in r.calls[-1]
    c.pr_ready("niloyray/ains-sandbox", 7)
    assert r.calls[-1][:3] == ["gh", "pr", "ready"]
    pr = c.pr_view("niloyray/ains-sandbox", 7)
    assert pr.ci_green and not pr.human_approved
    with pytest.raises(MergeRefused, match="no human approval"):
        c.pr_merge("niloyray/ains-sandbox", pr)
    assert not any(a[:3] == ["gh", "pr", "merge"] for a in r.calls)


def test_merge_happens_with_human_approval_and_green_ci():
    r = FakeRunner()
    reviews = [{"author": {"login": "niloyray"}, "state": "APPROVED", "body": "lgtm"}]
    r.responses["gh pr view 7"] = RunResult(0, pr_json(reviewDecision="APPROVED", reviews=reviews), "")
    c = GitHubClient(runner=r, bot_login="ains-bot")
    pr = c.pr_view("niloyray/ains-sandbox", 7)
    c.pr_merge("niloyray/ains-sandbox", pr)
    assert r.calls[-1][:3] == ["gh", "pr", "merge"] and "--squash" in r.calls[-1]


def test_agent_review_or_bot_approval_never_counts_as_human():
    r = FakeRunner()
    reviews = [
        {"author": {"login": "ains-bot"}, "state": "APPROVED", "body": "bot"},
        {"author": {"login": "niloyray"}, "state": "APPROVED", "body": f"{AGENT_REVIEW_MARKER}\nlooks fine"},
    ]
    r.responses["gh pr view 7"] = RunResult(0, pr_json(reviewDecision="APPROVED", reviews=reviews), "")
    pr = GitHubClient(runner=r, bot_login="ains-bot").pr_view("niloyray/ains-sandbox", 7)
    assert pr.human_approved is False


def test_label_approval_mode():
    r = FakeRunner()
    r.responses["gh pr view 7"] = RunResult(0, pr_json(labels=[{"name": "g3-approved"}]), "")
    pr = GitHubClient(runner=r).pr_view("niloyray/ains-sandbox", 7, approval_mode="label")
    assert pr.human_approved is True


def test_merge_refused_when_ci_red():
    r = FakeRunner()
    reviews = [{"author": {"login": "niloyray"}, "state": "APPROVED", "body": ""}]
    r.responses["gh pr view 7"] = RunResult(
        0,
        pr_json(
            reviewDecision="APPROVED", reviews=reviews, statusCheckRollup=[{"name": "ci", "conclusion": "FAILURE"}]
        ),
        "",
    )
    c = GitHubClient(runner=r, bot_login="ains-bot")
    with pytest.raises(MergeRefused, match="not green"):
        c.pr_merge("niloyray/ains-sandbox", c.pr_view("niloyray/ains-sandbox", 7))


def test_T_I4_3_review_events_and_marker():
    r = FakeRunner()
    c = GitHubClient(runner=r)
    c.pr_review("niloyray/ains-sandbox", 7, "found a bug", "REQUEST_CHANGES")
    assert "--request-changes" in r.calls[-1] and r.calls[-1][-1].startswith(AGENT_REVIEW_MARKER)
    c.pr_review("niloyray/ains-sandbox", 7, "fine", "comment")
    assert "--comment" in r.calls[-1]
    with pytest.raises(GitHubError, match="not allowed"):
        c.pr_review("niloyray/ains-sandbox", 7, "x", "APPROVE")


def test_T_I4_4_pr_list_by_label():
    r = FakeRunner()
    item = {
        "number": 9,
        "headRefOid": "def",
        "headRefName": "h",
        "isDraft": False,
        "labels": [{"name": "ai-review"}],
        "title": "t",
        "url": "u",
    }
    r.responses["gh pr list"] = RunResult(0, json.dumps([item]), "")
    prs = GitHubClient(runner=r).pr_list("niloyray/ains-sandbox", label="ai-review")
    assert prs[0]["number"] == 9 and "--label" in r.calls[-1]


def test_T_I4_5_error_wrapping_and_single_network_retry():
    r = FakeRunner()
    r.responses["gh pr ready 7"] = RunResult(1, "", "GraphQL: not found (HTTP 404)")
    c = GitHubClient(runner=r, sleep=lambda s: None)
    with pytest.raises(GitHubError, match="exit 1") as ei:
        c.pr_ready("niloyray/ains-sandbox", 7)
    assert "404" in str(ei.value)
    calls_before = len(r.calls)
    r.fail_once["gh pr ready 7"] = "error: could not resolve host: api.github.com"
    r.responses.pop("gh pr ready 7")
    c.pr_ready("niloyray/ains-sandbox", 7)
    assert len(r.calls) == calls_before + 2  # one failure, one retry


def test_dry_run_records_writes_only():
    r = FakeRunner()
    c = GitHubClient(runner=r, dry_run=True)
    c.pr_ready("niloyray/ains-sandbox", 7)
    assert r.calls == [] and c.dry_run_log and "pr ready" in c.dry_run_log[0]
