# ains-platform

The platform half of the AINS research PoC: an AI-native SDLC that takes a Jira story to a merged, released pull request through stage agents and human gates.

- `ains-orchestrator/` — the homegrown orchestrator (`ains` CLI): Jira polling, flow state machine with gates G1–G5, stage agents spawned as headless Claude Code CLI processes, PR watcher with a review agent, project onboarding.
- `.github/workflows/` — reusable workflow definitions (CI, release, Jira callback) that onboarded project repos call; `intake-ack` is the only workflow that runs here.
- `bootstrap/` — the onboarding script and the templates every onboarded repo receives.
- `docs/` — architecture document, the two diagrams, gates, targets, divergence notes, test evidence.
- `plan/` — the implementation plan and the test plan. The plan is the source of truth.

How it is built and run: the platform is developed directly from a workstation with VS Code and the Claude Code CLI and committed straight to `main`. It has no CI and no Jira project of its own. Jira, the orchestrator container and the reusable workflows exist for the projects it onboards, starting with a sandbox and then `ains-poc`.

Start here: `plan/ainativesdlc_plan.md`, then `docs/ains-architecture.html` and `docs/ains-poc-flow.html`.
