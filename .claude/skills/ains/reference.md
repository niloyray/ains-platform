# AINS management reference

## Command sheet

```
# control (repo root)
ains-platform.cmd start | stop | restart | build | down | status | logs | doctor | verify <repo> <KEY> | portal

# inside the running container (from ains-orchestrator/)
docker compose exec -T orchestrator ains status [--json]
docker compose exec -T orchestrator ains session status <KEY> [--json]
docker compose exec -T orchestrator ains session advance <KEY>      # one transition now
docker compose exec -T orchestrator ains session dispatch <KEY>     # claim a Ready story now
docker compose exec -T orchestrator ains doctor
docker compose exec -T orchestrator ains targets list

# on the host; `ains` works from any folder once ains.bat is on PATH (else: cd ains-orchestrator && uv run ains ...)
ains target add                      # guided wizard: one question at a time, dry run, confirm, run, verify
uv run ains target add <repo> <KEY> "<name>" [--class c] [--autonomy L3|L4] [--methodology spec-kit] [--gates ...] [--approval-mode label|review] [--dry-run]
uv run ains target verify <repo> <KEY>
uv run ains target remove <repo> [--delete-repo] [--dry-run]
uv run pytest -q && uv run ruff check .

# portal
http://localhost:8787            dashboard · /projects/<KEY> · /stories/<KEY> · /add · /platform · /api/status
```

## Gates

| Gate | Where | Approve | Send back |
|---|---|---|---|
| G1 spec | Jira | move to Planning | comment, move to Rework |
| G2 plan | Jira | move to Implementing | comment, move to Rework |
| G3 code | PR | label `g3-approved` (label mode) or Approve (review mode); needs check `ci / ci` green | PR comment + label `g3-rework`, or Request changes |
| G4 staging | Actions run | Review deployments → staging → Approve | Reject |
| G5 production | Actions run | Review deployments → production → Approve | Reject |

An `off` gate is auto-passed with the Jira comment "G<n> auto-approved (gate off for this target)".

## State names

| Store stage | Jira status set by | Meaning |
|---|---|---|
| spec | platform: Specifying | spec agent running |
| wait_g1 | platform: Spec Review | waiting for G1 |
| plan | person: Planning | plan agent running |
| wait_g2 | platform: Plan Review | waiting for G2 |
| implement | person: Implementing | implement agent running, PR made ready |
| review | CI: Code Review | review agent running |
| wait_g3 | Code Review | waiting for G3; CI red → automatic rework; new commit → review again |
| merge | Code Review | squash-merge in progress |
| released | release workflow: Staging, Done | G4/G5 live in GitHub Actions |
| blocked | platform: Blocked | agent failed twice or CI red three times; resume by moving to Ready |
| pr_reviewed | (no story) | human-lane PR reviewed once per commit |

## Event kinds

`claimed`, `resumed`, `manual.reset`, `agent.start`, `agent.done` (with `result`), `agent.retry`, `agent.skipped`,
`stage.done`, `pr.opened`, `pr.new_commits`, `review.posted`, `gate.waiting`, `gate.passed`, `gate.rework`, `merged`,
`blocked`.

## Files that hold state or config

| Path (container) | Host | What |
|---|---|---|
| /work/sessions.sqlite | volume `ains-workspaces` | sessions and events |
| /work/heartbeat.json | volume | last poll cycle |
| /work/<KEY>/ | volume | story clone, deleted after merge |
| /app/targets.yaml | ains-orchestrator/targets.yaml | onboarded projects, gates, profile |
| /app/profiles.yaml | ains-orchestrator/profiles.yaml | catalog of classes, phases, methods, autonomy levels |
| /app/flows, /app/agents, /app/claude-settings.json | same names | flow, prompts, agent permissions |
| ains-orchestrator/.env | host only | the three tokens, plus `AINS_BOT_LOGIN` / `AINS_BOT_GITHUB_TOKEN` for the orchestrator's own GitHub account (approval_mode `review`); never committed |
