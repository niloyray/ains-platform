"""In-memory fakes for Jira, GitHub, the agent runner and the workspace, shared by the flow and poller tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ains_orchestrator.errors import TransitionUnavailable
from ains_orchestrator.flow import AgentResult
from ains_orchestrator.github import PullRequest


class FakeJira:
    def __init__(self) -> None:
        self.issues: dict[str, dict[str, Any]] = {}
        self.comments: dict[str, list[str]] = {}
        self.links: dict[str, list[tuple[str, str]]] = {}
        self.transitions_done: list[tuple[str, str]] = []
        self.search_results: list[dict[str, Any]] = []
        self.fail_search = False

    def add_issue(self, key: str, summary: str, status: str = "Ready", description: str = "desc") -> None:
        self.issues[key] = {
            "key": key,
            "fields": {
                "summary": summary,
                "status": {"name": status},
                "description": description,
                "comment": {"comments": []},
            },
        }

    def set_status(self, key: str, status: str) -> None:
        self.issues[key]["fields"]["status"]["name"] = status

    def human_comment(self, key: str, text: str) -> None:
        self.issues[key]["fields"]["comment"]["comments"].append({"body": text})

    # client API
    def search(self, jql: str, fields=None, max_results=50):
        if self.fail_search:
            from ains_orchestrator.errors import JiraError

            raise JiraError("boom", 503)
        return [i for i in self.issues.values() if i["fields"]["status"]["name"] == "Ready"]

    def get_issue(self, key: str, fields=None):
        return self.issues[key]

    def status_of(self, key: str) -> str:
        return self.issues[key]["fields"]["status"]["name"]

    def transition(self, key: str, name: str) -> None:
        if name == "Nope":
            raise TransitionUnavailable(key, name, ["Specifying"])
        self.transitions_done.append((key, name))
        self.set_status(key, name)

    def add_comment(self, key: str, text: str):
        self.comments.setdefault(key, []).append(text)
        return str(len(self.comments[key]))

    def add_remote_link(self, key: str, url: str, title: str) -> None:
        self.links.setdefault(key, []).append((url, title))


class FakeGitHub:
    """Tracks PRs in memory; the test script flips approval, CI and head SHA."""

    def __init__(self) -> None:
        self.prs: dict[int, PullRequest] = {}
        self.reviews: list[tuple[int, str, str]] = []
        self.merged: list[int] = []
        self.calls: list[str] = []
        self.next_number = 7
        self.sha = "sha-1"
        self.comments: dict[int, list[dict]] = {}

    def clone(self, repo, dest: Path, branch=None):
        self.calls.append(f"clone {repo}")
        (dest / ".git").mkdir(parents=True, exist_ok=True)

    def create_branch(self, cwd, name):
        self.calls.append(f"branch {name}")

    def commit_all(self, cwd, message):
        self.calls.append(f"commit {message.splitlines()[0]}")
        return True

    def push(self, cwd, branch):
        self.calls.append(f"push {branch}")

    def head_sha(self, cwd):
        return self.sha

    def pr_create(self, repo, head, base, title, body, draft=True):
        n = self.next_number
        self.next_number += 1
        self.prs[n] = PullRequest(
            n,
            f"https://github.com/{repo}/pull/{n}",
            title,
            self.sha,
            head,
            draft,
            "OPEN",
            "REVIEW_REQUIRED",
            [],
            {},
            False,
            False,
        )
        self.calls.append(f"pr create #{n} draft={draft}")
        return n

    def pr_ready(self, repo, number):
        self.prs[number].is_draft = False
        self.calls.append(f"pr ready #{number}")

    def pr_view(self, repo, number, *, approval_mode="review", approval_label="g3-approved"):
        pr = self.prs[number]
        pr.head_sha = self.sha
        if approval_mode == "label":
            pr.human_approved = approval_label in pr.labels
        return pr

    def pr_review(self, repo, number, body, event):
        self.reviews.append((number, event, body))
        self.calls.append(f"pr review #{number} {event}")

    def pr_diff(self, repo, number, max_chars=40000):
        return f"diff --git a/impl.txt b/impl.txt (pr #{number})"

    def pr_remove_label(self, repo, number, label):
        if label in self.prs[number].labels:
            self.prs[number].labels.remove(label)
        self.calls.append(f"unlabel #{number} {label}")

    def pr_comments(self, repo, number):
        return list(self.comments.get(number, []))

    def ci_failure_log(self, repo, branch):
        return "CI run https://example/run/1 concluded failure.\n\nFound 1 error.\nPLW1510 subprocess.run without check"

    def pr_merge(self, repo, pr):
        from ains_orchestrator.errors import MergeRefused

        if not pr.human_approved:
            raise MergeRefused("no human approval")
        if not pr.ci_green:
            raise MergeRefused("ci not green")
        pr.merged = True
        self.merged.append(pr.number)
        self.calls.append(f"merge #{pr.number}")

    # test helpers
    def ci(self, number: int, conclusion: str) -> None:
        self.prs[number].checks["ci"] = conclusion

    def approve(self, number: int) -> None:
        self.prs[number].human_approved = True
        self.prs[number].review_decision = "APPROVED"

    def request_changes(self, number: int) -> None:
        self.prs[number].review_decision = "CHANGES_REQUESTED"
        self.prs[number].human_approved = False
        self.prs[number].human_changes_requested = True
        self.prs[number].review_bodies = ["please rename the endpoint"]

    def sync_branch(self, cwd, branch):
        self.calls.append(f"sync {branch}")
        return True

    def checkout(self, cwd, branch):
        self.calls.append(f"checkout {branch}")

    def new_commit(self, sha: str) -> None:
        self.sha = sha


class FakeAgents:
    """Writes the files each stage is expected to produce; can be told to fail N times."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str, dict[str, Any]]] = []
        self.fail_times: dict[str, int] = {}
        self.produce = True
        self.review_verdict = "comment"

    def run(self, stage: str, key: str, workdir: Path, context: dict[str, Any]) -> AgentResult:
        self.runs.append((stage, key, context))
        if self.fail_times.get(stage, 0) > 0:
            self.fail_times[stage] -= 1
            return AgentResult(False, error=f"{stage} crashed")
        if self.produce:
            d = Path(workdir) / "specs" / key
            d.mkdir(parents=True, exist_ok=True)
            for name in {"spec": ["spec.md"], "plan": ["plan.md", "tasks.md"], "implement": ["impl.txt"], "review": []}[
                stage
            ]:
                (d / name).write_text(f"{stage} output for {key}\n", encoding="utf-8")
        if stage == "review":
            return AgentResult(True, text="review body", extra={"verdict": self.review_verdict}, turns=3, cost_usd=0.1)
        return AgentResult(True, text="done", turns=5, cost_usd=0.2, duration_ms=1000)
