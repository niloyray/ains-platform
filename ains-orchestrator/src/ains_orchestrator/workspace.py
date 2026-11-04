"""I11: one clone per story under the work directory; branch, commit, push, cleanup."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from ains_orchestrator.config import Target
from ains_orchestrator.errors import AinsError
from ains_orchestrator.github import GitHubClient

log = logging.getLogger("ains.workspace")
MIN_FREE_BYTES = 2 * 1024**3  # refuse to clone with less than 2 GB free


def slug(summary: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
    return s[:max_len].rstrip("-") or "story"


class WorkspaceError(AinsError):
    pass


class Workspace:
    def __init__(self, root: Path, github: GitHubClient, *, min_free_bytes: int = MIN_FREE_BYTES) -> None:
        self.root = Path(root)
        self.github = github
        self.min_free = min_free_bytes

    def path(self, key: str) -> Path:
        return self.root / key

    def branch_name(self, key: str, summary: str) -> str:
        return f"{key}-{slug(summary)}"

    def free_bytes(self) -> int:
        self.root.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.root).free

    def ensure(self, key: str, target: Target, branch: str) -> Path:
        """Clone the target repo for this story if needed and check out the story branch."""
        p = self.path(key)
        if not (p / ".git").exists():
            if self.free_bytes() < self.min_free:
                raise WorkspaceError(
                    f"less than {self.min_free // 1024**3} GB free under {self.root}; refusing to clone"
                )
            self.root.mkdir(parents=True, exist_ok=True)
            self.github.clone(target.repo, p, target.default_branch)
            self.github.create_branch(p, branch)
        # a person may have pushed to the story branch while the story waited at a gate: start from the remote tip
        sync = getattr(self.github, "sync_branch", None)
        if sync is not None:
            sync(p, branch)
        return p

    def commit(self, key: str, stage: str, message: str) -> bool:
        """Commit everything in the workspace with the story key prefix. Returns False if nothing changed."""
        full = f"{key}: {message}\n\nstage: {stage}\nwritten by the ains {stage} agent"
        return self.github.commit_all(self.path(key), full)

    def push(self, key: str, branch: str) -> None:
        self.github.push(self.path(key), branch)

    def head_sha(self, key: str) -> str:
        return self.github.head_sha(self.path(key))

    def cleanup(self, key: str) -> None:
        p = self.path(key)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
            log.info("workspace %s removed", p)

    def has_files(self, key: str, *rel: str) -> list[str]:
        """Names of the expected files that are missing."""
        return [r for r in rel if not (self.path(key) / r).exists()]
