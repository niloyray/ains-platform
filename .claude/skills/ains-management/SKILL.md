---
name: ains-management
description: Operate and manage the AINS platform (the AI-native SDLC orchestrator, its gates, projects and stories) from Claude Code. Use for any request about platform status, what a person must do next on a story, onboarding, verifying or removing a project, starting, stopping or diagnosing the platform, or reading its logs and event trail. All answers come from the platform's own store (the SQLite database and heartbeat on the container volume), the same source the management portal shows, so the two never disagree.
---

# AINS platform management

The platform is one Docker Compose stack in `ains-orchestrator/`: the **orchestrator** (`ains poll`, claims Jira stories, runs
the stage agents, waits at gates, merges) and the **portal** (`ains portal`, http://localhost:8787). Both read and write the
same state: `/work/sessions.sqlite` (sessions and events), `/work/heartbeat.json` (last poll cycle) and the mounted config
files (`targets.yaml`, `profiles.yaml`, `flows/`, `agents/`, `claude-settings.json`). **Never keep a parallel record of
platform state in notes or memory; ask the store every time.**

## How to read state (always through the store)

| Question | Command (from `ains-platform/`) | Notes |
|---|---|---|
| Is the platform up? | `ains-platform.cmd status` | containers, every story's next human action, portal link |
| What must a person do next? | `docker compose -f ains-orchestrator/docker-compose.yml exec -T orchestrator ains status --json` | one row per story; `next` is the sentence to relay; `released` rows check Jira for Staging/Done |
| One story in detail | `... exec -T orchestrator ains session status <KEY> --json` | stage, branch, PR, gate_state, every event incl. agent replies (`agent.done.result`) |
| Same, over HTTP | `curl -s http://localhost:8787/api/status` | identical data; use when the CLI is awkward |
| Is a project onboarded correctly? | `uv run ains target verify <repo> <KEY>` (in `ains-orchestrator/`) | read-only; exit 1 lists what is missing |
| Health of tokens and tools | `... exec orchestrator ains doctor` | fails on any `ANTHROPIC_*` variable by design |
| Live log | `ains-platform.cmd logs` or `docker compose ... logs -t --since 30m orchestrator` | one `cycle:` line per minute; idle lines repeat `wait_g3` etc. |

When reporting, quote the `next` sentence from `ains status` for each waiting story; do not paraphrase gate instructions
from memory. Stage names: `spec, wait_g1, plan, wait_g2, implement, review, wait_g3, merge, released, blocked`;
`pr_reviewed` rows are human-lane PRs the review agent commented on.

## What the platform never does, and neither does this skill

- Pass a gate. G1/G2 are Jira status moves by a person (Planning, Implementing), G3 is the `g3-approved` label or a PR
  approval, G4/G5 are GitHub environment approvals. Do not transition Jira or add labels on the user's behalf unless the
  user explicitly says "approve G3 on PR #n" in this conversation; then do exactly that one action and report it.
- Merge a PR, approve a PR as the agent, or run an agent with an `ANTHROPIC_*` key.
- Write a token anywhere but `ains-orchestrator/.env` (gitignored) or GitHub repository secrets.

## Operating the platform

```
ains-platform.cmd start|stop|restart|build|down|status|logs|doctor|verify <repo> <KEY>|portal
```
- `build` after any change under `ains-orchestrator/src` (the image bakes the source); config files are mounted and need
  only the next poll cycle.
- A rebuilt orchestrator resumes from the store; nothing is lost. A story that was mid-agent re-runs that stage once.

## Projects

- Onboard: portal "Add project" (dry run first) or
  `uv run ains target add <repo> <KEY> "<name>" --class small-poc --autonomy L3 --methodology spec-kit [--dry-run]`.
  Supported choices live in `ains-orchestrator/profiles.yaml`; unsupported ones are refused with the reason. Gates default
  from the autonomy level (L3: all on; L4: G1, G2, G4 off). Then commit `targets.yaml` and `docs/targets.md`.
- Verify: `uv run ains target verify <repo> <KEY>` or the portal's "Run verify".
- Remove: portal (typed confirmation, dry run first) or `uv run ains target remove <repo> [--delete-repo]`.
- The sandbox `niloyray/ains-sandbox` / `AINSSB` stays as the permanent regression target; never remove it.

## Stories

A story runs: Jira `Ready` → claim → spec agent → **G1** → plan agent → **G2** → implement agent → CI → review agent →
**G3** → squash-merge → release run → **G4** staging → **G5** production → Jira `Done`. Rework: Jira `Rework` with a
comment (G1, G2), label `g3-rework` plus a PR comment (G3), red CI (automatic, max 2 rounds). `Blocked` resumes when a
person moves the story to `Ready`. Full state map and transition table: `docs/user-guide.html`, section 7.

To check a story the user names: `ains session status <KEY> --json`, then read `stage`, the last `gate.*` and
`agent.done` events, and the PR via `gh pr view <n> --repo <repo> --json statusCheckRollup,labels,reviews`.

## Diagnosing

Read `docs/developer-manual.html` section 16 for the known signatures. The usual order: `ains status` → the story's
events (`agent.done.result` says what the agent actually did) → `gh pr view` → `gh run view <id> --log-failed` → container
log. If the store and Jira disagree, the store is the platform's truth and Jira is the human's; report both and do not
edit either.

## When you change the platform

Keep `docs/implementation-log.md` current (what was observed, cause, fix, commit), add a test with the test-plan id in its
name, run `uv run pytest -q` and `uv run ruff check .`, rebuild with `ains-platform.cmd build`, and re-run the relevant
row of the self-test checklist in `docs/user-guide.html` section 11 on the sandbox.

See `reference.md` in this skill for the command sheet, the gate table and the state names.
