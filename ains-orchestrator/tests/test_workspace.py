"""T-I11-1 to T-I11-5."""

from __future__ import annotations

from pathlib import Path

import pytest

from ains_orchestrator.config import Target
from ains_orchestrator.workspace import Workspace, WorkspaceError, slug
from tests.fakes import FakeGitHub

T = Target(repo="niloyray/ains-sandbox")


def test_T_I11_1_clone_and_branch(tmp_path: Path):
    gh = FakeGitHub()
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=0)
    b = ws.branch_name("AINSSB-1", "Repository Scaffold: README + health-check script!!")
    assert b == "AINSSB-1-repository-scaffold-readme-health-check"
    assert len(b) <= len("AINSSB-1-") + 40
    p = ws.ensure("AINSSB-1", T, b)
    assert p == tmp_path / "work" / "AINSSB-1" and (p / ".git").exists()
    assert gh.calls == ["clone niloyray/ains-sandbox", f"branch {b}", f"sync {b}"]  # sync: start from the remote tip
    ws.ensure("AINSSB-1", T, b)  # second call only re-syncs the branch
    assert len(gh.calls) == 4 and gh.calls[-1] == f"sync {b}"


def test_slug_edge_cases():
    assert slug("") == "story" and slug("---") == "story" and slug("Ünïcode ok") == "n-code-ok"


def test_T_I11_2_and_3_commit_message_format(tmp_path: Path):
    gh = FakeGitHub()
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=0)
    ws.commit("AINSSB-1", "spec", "spec output")
    assert gh.calls[-1] == "commit AINSSB-1: spec output"


def test_T_I11_4_cleanup_removes_directory(tmp_path: Path):
    gh = FakeGitHub()
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=0)
    ws.ensure("AINSSB-1", T, "AINSSB-1-x")
    ws.cleanup("AINSSB-1")
    assert not ws.path("AINSSB-1").exists()
    ws.cleanup("AINSSB-1")  # idempotent


def test_T_I11_5_refuses_clone_without_disk_space(tmp_path: Path):
    gh = FakeGitHub()
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=10**18)
    with pytest.raises(WorkspaceError, match="refusing to clone"):
        ws.ensure("AINSSB-1", T, "AINSSB-1-x")
    assert gh.calls == []


def test_has_files(tmp_path: Path):
    gh = FakeGitHub()
    ws = Workspace(tmp_path / "work", gh, min_free_bytes=0)
    ws.ensure("AINSSB-1", T, "b")
    (ws.path("AINSSB-1") / "specs" / "AINSSB-1").mkdir(parents=True)
    (ws.path("AINSSB-1") / "specs" / "AINSSB-1" / "spec.md").write_text("x")
    assert ws.has_files("AINSSB-1", "specs/AINSSB-1/spec.md", "specs/AINSSB-1/plan.md") == ["specs/AINSSB-1/plan.md"]
