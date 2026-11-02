# ains-orchestrator

The AINS platform's loop runner. Polls Jira for AI-native stories, spawns one headless Claude Code CLI process per stage (spec, plan, implement, review), waits at human gates G1–G5, pushes branches and pull requests, and writes the trace back to Jira. It never calls a model API itself; every model call happens inside the Claude Code CLI on the engineer's Claude subscription.

Build order, tests and the state machine: `../plan/ainativesdlc_plan.md` section 9 and `../plan/ains-platform-test-plan.md`.

```
uv sync
uv run ains --help
uv run pytest
```
