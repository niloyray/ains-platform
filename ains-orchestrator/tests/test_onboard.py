"""T-I16-1, T-I16-2 and offline versions of T-I16-3/4/6/7 with a fake runner and a fake Jira."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ains_orchestrator.config import load_targets
from ains_orchestrator.github import GitHubClient, RunResult
from ains_orchestrator.onboard import REQUIRED_STATUSES, Onboarder, render

TEMPLATES = Path(__file__).resolve().parents[2] / "bootstrap" / "templates"


class FakeRunner:
    """Answers gh/git calls; records argv and stdin."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.stdins: list[str | None] = []
        self.existing_repos: set[str] = set()
        self.remote_files: dict[str, str] = {}  # what "origin/main" holds, emulated

    def _snapshot(self, dest: Path) -> dict[str, str]:
        return {
            str(f.relative_to(dest)).replace("\\", "/"): f.read_text(encoding="utf-8")
            for f in dest.rglob("*")
            if f.is_file() and ".git" not in f.parts
        }

    def __call__(self, argv, cwd, stdin=None):
        self.calls.append(argv)
        self.stdins.append(stdin)
        j = " ".join(argv)
        if j.startswith("gh repo view"):
            return RunResult(0, "{}", "") if argv[3] in self.existing_repos else RunResult(1, "", "GraphQL: not found")
        if j.startswith("gh repo create"):
            self.existing_repos.add(argv[3])
            return RunResult(0, f"https://github.com/{argv[3]}", "")
        if j.startswith("gh repo clone"):
            dest = Path(argv[4])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
            for rel, content in self.remote_files.items():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_text(content, encoding="utf-8")
            return RunResult(0, "", "")
        if j.startswith("git status --porcelain") and cwd is not None:
            dirty = self._snapshot(Path(cwd)) != self.remote_files
            return RunResult(0, " M files\n" if dirty else "", "")
        if j.startswith("git push") and cwd is not None:
            self.remote_files = self._snapshot(Path(cwd))
            return RunResult(0, "", "")
        if j.startswith("gh api users/"):
            return RunResult(0, "12345\n", "")
        return RunResult(0, "", "")


class FakeJira:
    def __init__(self, statuses=None, exists=False):
        self.projects = {"X": True} if exists else {}
        self.statuses = statuses if statuses is not None else REQUIRED_STATUSES
        self.created = []
        self.deleted = []
        self.fail_ensure = False

    def project_exists(self, key):
        return key in self.projects

    def create_project(self, key, name, template):
        self.projects[key] = True
        self.created.append((key, name, template))

    def project_statuses(self, key):
        return list(self.statuses)

    def ensure_statuses(self, key, wanted):
        if self.fail_ensure:
            from ains_orchestrator.errors import JiraError

            raise JiraError("workflow update rejected: test")
        added = [n for n, _ in wanted if n not in self.statuses]
        self.statuses = list(self.statuses) + added
        return added

    def delete_project(self, key):
        self.projects.pop(key, None)
        self.deleted.append(key)


@pytest.fixture
def world(tmp_path: Path, monkeypatch):
    root = tmp_path / "ains-orchestrator"
    root.mkdir()
    (root / "targets.yaml").write_text("# comment\ntargets: {}\n", encoding="utf-8")
    runner = FakeRunner()
    gh = GitHubClient(runner=runner, sleep=lambda s: None)
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "ATATT3-fake")
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("PATH", "")  # no `specify` on PATH: the template step must report it, not fail

    def make(dry_run=False, jira=None):
        return Onboarder(
            config_root=root,
            templates_dir=TEMPLATES,
            github=gh,
            jira=jira,
            owner="niloyray",
            dry_run=dry_run,
            reviewer_login="niloyray",
        )

    return {"root": root, "runner": runner, "make": make}


def test_T_I16_1_dry_run_changes_nothing(world):
    rep = world["make"](dry_run=True, jira=FakeJira()).add("ains-sandbox", "AINSSB", "sandbox")
    assert all(s.status == "planned" for s in rep.steps), rep.text()
    writes = [c for c in world["runner"].calls if c[:2] not in (["gh", "repo"],) or c[2] != "view"]
    assert writes == []
    assert (world["root"] / "targets.yaml").read_text() == "# comment\ntargets: {}\n"
    assert "gh repo create niloyray/ains-sandbox --public" in rep.text()


def test_T_I16_2_templates_render_with_secrets_inherit():
    values = {
        "repo": "niloyray/ains-sandbox",
        "repo_name": "ains-sandbox",
        "owner": "niloyray",
        "jira_key": "AINSSB",
        "jira_name": "sandbox",
        "image_name": "ains-sandbox",
        "platform_repo": "niloyray/ains-platform",
        "platform_ref": "main",
        "jira_base_url": "https://x",
    }
    for name in ("ci.yml", "release.yml", "CLAUDE.md", "README.md", "claude-settings.json", "gitignore"):
        out = render((TEMPLATES / name).read_text(encoding="utf-8"), values)
        assert "{{" not in out, name
    ci = render((TEMPLATES / "ci.yml").read_text(encoding="utf-8"), values)
    rel = render((TEMPLATES / "release.yml").read_text(encoding="utf-8"), values)
    assert "niloyray/ains-platform/.github/workflows/reusable-ci-python.yml@main" in ci and "secrets: inherit" in ci
    assert "reusable-release.yml@main" in rel and "image-name: ains-sandbox" in rel and "secrets: inherit" in rel
    settings = json.loads(render((TEMPLATES / "claude-settings.json").read_text(encoding="utf-8"), values))
    assert "Bash(git push*)" in settings["permissions"]["deny"]
    claude_md = render((TEMPLATES / "CLAUDE.md").read_text(encoding="utf-8"), values)
    assert "AINSSB" in claude_md and "Never edit `.github/`" in claude_md


def test_T_I16_3_real_add_runs_every_step(world):
    jira = FakeJira()
    rep = world["make"](jira=jira).add("ains-sandbox", "AINSSB", "sandbox")
    statuses = {s.name: s.status for s in rep.steps}
    assert statuses["github repo"] == "done" and statuses["templates"] == "done"
    assert statuses["label ai-review"] == "done" and statuses["secrets"] == "done"
    assert statuses["environments"] == "done" and statuses["branch protection"] == "done"
    assert statuses["jira project"] == "done" and statuses["jira statuses"] == "exists"  # fake ships all 12
    assert statuses["targets.yaml"] == "done" and not rep.failed
    assert jira.created[0][:2] == ("AINSSB", "sandbox")
    assert any("Automation" in m for m in rep.manual)
    calls = [" ".join(c) for c in world["runner"].calls]
    assert any(c.startswith("gh repo create niloyray/ains-sandbox --public") for c in calls)
    assert any("branches/main/protection" in c for c in calls)
    assert sum("environments/" in c for c in calls) == 2
    assert any(c.startswith("gh secret set JIRA_API_TOKEN") for c in calls)
    assert any(c.startswith("git push -u origin HEAD:main") for c in calls)
    stdin_values = [s for s in world["runner"].stdins if s]
    assert "ATATT3-fake" in stdin_values  # passed via stdin, never on the command line
    assert not any("ATATT3-fake" in c for c in calls)
    t = load_targets(world["root"] / "targets.yaml")
    assert t.targets["AINSSB"].repo == "niloyray/ains-sandbox" and t.targets["AINSSB"].approval_mode == "label"
    assert (world["root"] / "targets.yaml").read_text().startswith("# comment")  # comments preserved


def test_T_I16_4_second_run_is_idempotent(world):
    jira = FakeJira()
    ob = world["make"](jira=jira)
    ob.add("ains-sandbox", "AINSSB", "sandbox")
    rep = ob.add("ains-sandbox", "AINSSB", "sandbox")
    statuses = {s.name: s.status for s in rep.steps}
    assert statuses["github repo"] == "exists" and statuses["jira project"] == "exists"
    assert statuses["targets.yaml"] == "exists" and not rep.failed
    assert (world["root"] / "targets.yaml").read_text().count("AINSSB:") == 1


def test_missing_statuses_are_added_via_api(world):
    jira = FakeJira(statuses=["To Do", "Done"])
    rep = world["make"](jira=jira).add("ains-sandbox", "AINSSB", "sandbox")
    st = next(s for s in rep.steps if s.name == "jira statuses")
    assert st.status == "done" and "Specifying" in st.detail and "Blocked" in st.detail
    assert set(REQUIRED_STATUSES) <= set(jira.statuses)


def test_status_api_failure_falls_back_to_manual_step(world):
    jira = FakeJira(statuses=["To Do", "Done"])
    jira.fail_ensure = True
    rep = world["make"](jira=jira).add("ains-sandbox", "AINSSB", "sandbox")
    st = next(s for s in rep.steps if s.name == "jira statuses")
    assert st.status == "manual" and any("by hand" in m for m in rep.manual)


def test_T_I16_7_second_project_and_T_I16_6_remove(world):
    jira = FakeJira()
    ob = world["make"](jira=jira)
    ob.add("ains-sandbox", "AINSSB", "sandbox")
    ob.add("ains-sandbox-2", "AINSSB2", "sandbox-2")
    t = load_targets(world["root"] / "targets.yaml")
    assert set(t.targets) == {"AINSSB", "AINSSB2"}
    rep = ob.remove("ains-sandbox-2")
    statuses = {s.name: s.status for s in rep.steps}
    assert (
        statuses["github repo"] == "done" and "archived" in next(s for s in rep.steps if s.name == "github repo").detail
    )
    assert statuses["jira project"] == "done" and jira.deleted == ["AINSSB2"]
    t = load_targets(world["root"] / "targets.yaml")
    assert set(t.targets) == {"AINSSB"}
    rep = ob.remove("ains-sandbox")
    assert load_targets(world["root"] / "targets.yaml").targets == {}
    assert (world["root"] / "targets.yaml").read_text().startswith("# comment")


def test_invalid_key_rejected(world):
    with pytest.raises(Exception, match="not a valid Jira project key"):
        world["make"]().add("x", "bad-key", "x")


class _VerifyGh:
    """Canned gh answers keyed by the first words of argv; anything else raises GitHubError."""

    def __init__(self, answers):
        self.answers = answers

    def exec(self, argv, cwd=None, *, write=False, what="", stdin=None):
        from ains_orchestrator.errors import GitHubError

        joined = " ".join(argv)
        for prefix, out in self.answers.items():
            if joined.startswith(prefix):
                if out is None:
                    raise GitHubError(f"404 {what}")
                return out
        raise GitHubError(f"unexpected {joined}")


class _VerifyJira:
    def __init__(self, exists=True, statuses=None):
        self._exists, self._statuses = exists, statuses or []

    def project_exists(self, key):
        return self._exists

    def project_statuses(self, key):
        return self._statuses


def test_T_I16_7_verify_target_reports_each_missing_piece():
    from types import SimpleNamespace

    from ains_orchestrator.onboard import REQUIRED_STATUSES, verify_target

    answers = {
        "gh repo view": '{"name": "ains-sandbox", "defaultBranchRef": {"name": "main"}}',
        "gh api repos/o/ains-sandbox/contents/.github/workflows/ci.yml": ".github/workflows/ci.yml",
        "gh api repos/o/ains-sandbox/contents/": None,  # every other file missing
        "gh label list": "ai-review\ng3-approved\nai-native",
        "gh secret list": "JIRA_EMAIL",
        "gh api repos/o/ains-sandbox/environments": (
            '[{"name": "staging", "protection_rules": [{"type": "required_reviewers"}]},'
            ' {"name": "production", "protection_rules": []}]'
        ),
        "gh api repos/o/ains-sandbox/branches/main/protection": '["ci"]',
    }
    targets = {"AINSSB": SimpleNamespace(repo="o/ains-sandbox")}
    checks = verify_target(
        _VerifyGh(answers),
        _VerifyJira(statuses=REQUIRED_STATUSES[:-1]),
        repo="o/ains-sandbox",
        jira_key="AINSSB",
        targets=targets,
    )
    by = {c.name: c for c in checks}
    assert by["github repo"].ok and by["file .github/workflows/ci.yml"].ok and not by["file CLAUDE.md"].ok
    assert not by["labels"].ok and "g3-rework" in by["labels"].detail
    assert by["secret JIRA_EMAIL"].ok and not by["secret JIRA_API_TOKEN"].ok
    assert by["environment staging"].ok and not by["environment production"].ok
    assert not by["branch protection"].ok and "['ci']" in by["branch protection"].detail
    assert by["jira project"].ok and not by["jira statuses"].ok and "Blocked" in by["jira statuses"].detail
    assert by["targets.yaml"].ok
    assert not verify_target(_VerifyGh(answers), None, repo="o/ains-sandbox", jira_key="AINSSB", targets={})[-1].ok
