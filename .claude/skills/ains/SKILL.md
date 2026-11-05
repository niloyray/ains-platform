---
name: ains
description: Operate and manage the AINS platform; /ains <request> in plain words, or guided onboarding one question at a time (the AI-native SDLC orchestrator, its gates, projects and stories) from Claude Code. Use for any request about platform status, what a person must do next on a story, onboarding, verifying or removing a project, starting, stopping or diagnosing the platform, or reading its logs and event trail. All answers come from the platform's own store (the SQLite database and heartbeat on the container volume), the same source the management portal shows, so the two never disagree.
---

# AINS platform management

The platform is one Docker Compose stack in `ains-orchestrator/`: the **orchestrator** (`ains poll`, claims Jira stories, runs
the stage agents, waits at gates, merges) and the **portal** (`ains portal`, http://localhost:8787). Both read and write the
same state: `/data/sessions.sqlite` (sessions and events), `/data/heartbeat.json` (last poll cycle), `/data/logs/` and the mounted config
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
ains-platform.cmd start|stop|restart|build|down|status|logs|doctor|verify <repo> <KEY>|portal|backup
```
- `build` after any change under `ains-orchestrator/src` (the image bakes the source); config files are mounted and need
  only the next poll cycle.
- A rebuilt orchestrator resumes from the store; nothing is lost. A story that was mid-agent re-runs that stage once.

## Guided onboarding: one question at a time

When the user wants to add a project (any wording: "add", "onboard", "start a project", "new project"), do not ask for
everything at once. Ask one question per turn with the AskUserQuestion tool so the user picks from options, in this order,
and skip a question when the user already gave the answer:

1. **Repository name** (free text; lower case and hyphens; it will live under the GitHub owner from `gh api user`).
2. **Jira project key** (free text; 2 to 10 capital letters; propose one derived from the repository name).
3. **Jira project name** (free text; propose the repository name in words).
4. **Project class**: options are the entries of `project_classes` in `ains-orchestrator/profiles.yaml` with
   `supported: true`, each with its note and ceiling; mention the unsupported ones in one line as "not available yet".
5. **Autonomy level**: options are the supported `autonomy_levels` at or below the class ceiling, each with the gate set it
   implies (from `gates`). Recommend L3 for a first project.
6. **G3 approval mode**: `label` (recommended; single GitHub account) or `review` (needs a separate orchestrator account).
7. **Gate overrides**: offer "keep the level's gates" (recommended) or let the user name gates to switch on or off.
   Lifecycle phases and methodology are not asked while only one supported option exists for each; state them.
8. Show the summary as the exact command, run it with `--dry-run`, show the planned steps, and ask **"run it for real?"**
   Only after a yes: run without `--dry-run`, then `ains target verify`, then remind the user to commit
   `ains-orchestrator/targets.yaml` and add a line to `docs/targets.md`.

Read the catalog file before asking questions 4 and 5 so the options are always the current ones. Use the same
one-question style for removal: confirm the key, offer dry run first, then ask for the real run.

## Projects

- Onboard: `ains target add` with no arguments is itself a guided wizard in the terminal (use it when the user wants to type the answers); the portal "Add project" form; or the one-line form
  `uv run ains target add <repo> <KEY> "<name>" --class small-poc --autonomy L3 --methodology spec-kit [--dry-run]`.
  Supported choices live in `ains-orchestrator/profiles.yaml`; unsupported ones are refused with the reason. Gates default
  from the autonomy level (L3: all on; L4: G1, G2, G4 off). Then commit `targets.yaml` and `docs/targets.md`.
- The orchestrator's own GitHub account (`AINS_BOT_LOGIN`, token `AINS_BOT_GITHUB_TOKEN` in `.env`): onboarding invites it to
  every new repository with write access and accepts the invitation itself (step `bot collaborator`); `target verify`
  checks the permission. With it, `--approval-mode review` works (real PR approvals by a person). Without it only
  `label` mode works. A PR opened before a mode switch keeps the old way of approving.
- Verify: `uv run ains target verify <repo> <KEY>` or the portal's "Run verify".
- Remove: portal (typed confirmation, dry run first) or `uv run ains target remove <repo> [--delete-repo]`.
- The sandbox `niloyray/ains-sandbox` / `AINSSB` stays as the permanent regression target; never remove it.

## Storyboarding: `/ains story <idea>` (or "draft a story for ...", "update story KEY ...")

Stories are the only way work enters the platform, so help write them well, one question at a time:
1. Pick the project (ask with options from `ains targets list` unless the request names it or only one exists).
2. Get a grounded first draft: `ains story draft <KEY> --idea "<the idea>" --json` (the story agent reads the project's
   CLAUDE.md, README and specs). If that fails (no `claude` on this machine), draft yourself using the same shape:
   summary (<= 80 chars, imperative), description (context, change, constraints), 3 to 6 testable acceptance
   criteria, out of scope, and at most 3 questions only the person can answer.
3. Show the draft. Ask the draft's questions one per turn with the question tool, then redraft
   (`--feedback "<answers>"`). Repeat until the person accepts; let them edit any field by hand.
4. Ask whether to create it in To Do or straight in Ready, then run
   `ains story create <KEY> --summary "..." --description-file <tmp.md> --ac "..." --ac "..." --out "..." [--ready]`
   (write the description to a temp file; never pass long text on the command line). Report the key and the Jira link.
5. For an existing story: `ains story show KEY`, ask what should change, then `ains story edit KEY ...` (or
   `ains story update KEY --change "..."` to let the agent rewrite it). Never edit a story that is past Ready
   without saying so: the platform may already be working on it; a Rework comment is the way to steer a running story.

## Stories

A story runs: Jira `Ready` → claim → spec agent → **G1** → plan agent → **G2** → implement agent → CI → review agent →
**G3** → squash-merge → release run → **G4** staging → **G5** production → Jira `Done`. Rework: Jira `Rework` with a
comment (G1, G2), label `g3-rework` plus a PR comment (G3), red CI (automatic, max 2 rounds). `Blocked` resumes when a
person moves the story to `Ready`. Full state map and transition table: `docs/user-guide.html`, section 7.

To check a story the user names: `ains session status <KEY> --json`, then read `stage`, the last `gate.*` and
`agent.done` events, and the PR via `gh pr view <n> --repo <repo> --json statusCheckRollup,labels,reviews`.

## Working from a project folder, and working locally

The skill is also loaded when the current folder is an onboarded project (it has `.claude/settings.json`, `CLAUDE.md`
and `.github/workflows/ci.yml` written by the platform). There, do not assume the platform folder:
- read state through the global CLI and the portal: `ains targets list`, `ains target verify <repo> <KEY>`,
  `curl -s http://localhost:8787/api/status` (same rows as `ains status --json`), `gh pr view`; `docker compose`
  commands need the platform folder, so only use them when `ains-orchestrator/docker-compose.yml` is reachable;
- the project key is the one whose repository matches `git remote get-url origin`; "what is waiting on me" then means
  that project's stories only.

Two ways a person works locally, both inside the flow (user guide section 6, "Working locally alongside the platform"):
- **Human lane, no agents:** branch, commit, push, open a PR; CI and the platform guard run; the person merges after
  the green check; `ai-review` label on the PR asks the review agent for an opinion (it never merges). A Jira key in
  the branch and commit names lets the release callbacks move that story.
- **Joining an agent's story:** check out the story branch `<KEY>-<slug>`, commit and push **only while the story
  waits at a gate** (`wait_g1`, `wait_g2`, `wait_g3` in `ains status`); every agent stage starts from the remote tip,
  so the commits are kept. Never push while a stage runs (`spec`, `plan`, `implement`, `review`). Prefer a Rework
  comment or `g3-rework` to steer an agent; direct commits are for small fixes.
When asked to help with a local change, first run `ains status` (or the portal API) and say which of the two cases
applies and whether the story is at a gate right now. Never touch the platform-owned paths (`.github/`, `.claude/`,
`.specify/`, `CLAUDE.md`) in a project; a PR that changes them fails the CI guard. Those files are refreshed with
`ains target add`.

## Diagnosing

Read `docs/developer-manual.html` section 16 for the known signatures. The usual order: `ains status` → the story's
events (`agent.done.result` says what the agent actually did) → `gh pr view` → `gh run view <id> --log-failed` → container
log. If the store and Jira disagree, the store is the platform's truth and Jira is the human's; report both and do not
edit either.

## When you change the platform

Keep `docs/implementation-log.md` current (what was observed, cause, fix, commit), add a test with the test-plan id in its
name, run `uv run pytest -q` and `uv run ruff check .`, rebuild with `ains-platform.cmd build`, and re-run the relevant
row of the self-test checklist in `docs/user-guide.html` section 11 on the sandbox.

Invoke as `/ains <request>`. See `reference.md` in this skill for the command sheet, the gate table and the state names.
