# {{repo_name}} — conventions for agents and developers

This repository is onboarded to the AINS platform (`{{platform_repo}}`). Stories live in Jira project `{{jira_key}}`.
The same rules apply to the AINS stage agents (spec, plan, implement, review) and to developers working in VS Code.

## Traceability
- Every branch is `<JIRA-KEY>-<slug>`, every commit message starts with `<JIRA-KEY>:`, every PR title starts with `<JIRA-KEY>:`.
- Specs for a story live in `specs/<JIRA-KEY>/` (`spec.md`, `plan.md`, `tasks.md`), produced with Spec Kit.
- `init/` holds the idea backlog (`init/ideas.md`) and anything that precedes the first story; it is the project's, not the platform's, and is never code.

## Boundaries
- Never edit `.github/`, `.specify/`, `.claude/` or this file from a story; those belong to the platform.
- Never add secrets, tokens or credentials to the repository. Configuration comes from environment variables.
- Keep application code under `app/` (or the directory the plan names) and tests under `tests/`.

## Quality
- Run the test command before opening or updating a PR: `uv run pytest` and `uvx ruff check .` (ruff runs as a tool; do not add it as a dependency).
- A PR is merged only after the `ci` check is green and a human has approved it (gate G3). Agents never approve.
- Add the label `ai-review` to a PR to get a review from the AINS review agent.

## Stage agents, what each may do
- spec: writes `specs/<KEY>/spec.md` only.
- plan: writes `specs/<KEY>/plan.md` and `tasks.md` only.
- implement: writes code and tests for the tasks in `tasks.md`; never commits or pushes (the orchestrator does).
- review: read-only; posts a PR review ending with `VERDICT: comment` or `VERDICT: request_changes`.
