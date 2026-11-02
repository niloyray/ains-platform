"""AINS orchestrator: the Layer 2 loop runner and Layer 4 orchestration service of the AINS platform.

It polls Jira for AI-native stories, spawns one headless Claude Code CLI process per stage
(spec, plan, implement, review), waits at human gates, pushes branches and PRs, and never
calls a model API itself.
"""

__version__ = "0.1.0"
