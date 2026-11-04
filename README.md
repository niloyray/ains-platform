# ains-platform

The platform half of the AINS research PoC: an AI-native SDLC that takes a Jira story to a merged, released pull request through stage agents and human gates.

- `ains-orchestrator/` — the homegrown orchestrator (`ains` CLI): Jira polling, flow state machine with gates G1–G5, stage agents spawned as headless Claude Code CLI processes, PR watcher with a review agent, project onboarding.
- `.github/workflows/` — reusable workflow definitions (CI, release, Jira callback) that onboarded project repos call; `intake-ack` is the only workflow that runs here.
- `bootstrap/` — the onboarding script and the templates every onboarded repo receives.
- `docs/` — architecture document, the two diagrams, gates, targets, divergence notes, test evidence.
- `plan/` — the implementation plan and the test plan. The plan is the source of truth.
- `ains-platform.cmd` — control script: `start | stop | restart | build | down | status | logs | doctor | verify | portal | backup`. `start` runs both containers: the orchestrator and the management portal at http://localhost:8787.
- `.claude/skills/ains/` — Claude Code skill for operating the platform; it reads the same store the portal shows.
- `ains target add` with no arguments is a guided wizard (one question at a time, dry run, confirm, run, verify); `ains.bat` in a PATH folder runs the CLI from anywhere.
- `ains-orchestrator/profiles.yaml` — catalog of project classes, lifecycle phases, methodologies and autonomy levels; `supported` flags gate what `ains target add` and the portal offer.
- Data: volume `ains-data` (`/data`: sessions.sqlite, heartbeat.json, logs/) is durable; volume `ains-workspaces` (`/work`) holds throwaway clones.

How it is built and run: the platform is developed directly from a workstation with VS Code and the Claude Code CLI and committed straight to `main`. It has no CI and no Jira project of its own. Jira, the orchestrator container and the reusable workflows exist for the projects it onboards, starting with a sandbox and then `ains-poc`.

Start here: `docs/user-guide.html` (using the platform), `docs/developer-manual.html` (changing it), then `plan/ainativesdlc_plan.md`, `docs/ains-architecture.html` and `docs/ains-poc-flow.html`.
