"""Exceptions shared by the orchestrator modules."""

from __future__ import annotations


class AinsError(Exception):
    """Base class for orchestrator errors."""


class ConfigError(AinsError):
    """A YAML config file is invalid. Carries file and location."""

    def __init__(self, file: str, message: str, line: int | None = None) -> None:
        where = f"{file}:{line}" if line else file
        super().__init__(f"{where}: {message}")
        self.file, self.line, self.detail = file, line, message


class JiraError(AinsError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class TransitionUnavailable(JiraError):
    def __init__(self, key: str, wanted: str, available: list[str]) -> None:
        super().__init__(f"{key}: transition '{wanted}' not available; available: {', '.join(available) or '-'}")
        self.key, self.wanted, self.available = key, wanted, available


class GitHubError(AinsError):
    def __init__(self, message: str, exit_code: int | None = None, stderr: str = "") -> None:
        super().__init__(message if not stderr else f"{message}: {stderr.strip()[:400]}")
        self.exit_code, self.stderr = exit_code, stderr


class MergeRefused(GitHubError):
    """The PR is not approved or CI is not green."""


class StoreError(AinsError):
    pass
