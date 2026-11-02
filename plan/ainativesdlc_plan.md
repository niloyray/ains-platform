# AINS — AI-native SDLC Platform Setup Plan

**Scope:** Phase 1 only — stand up the six-layer platform described in `init/AI-native delivery stack.html` as a **reusable platform repo** (`ains-platform`), and prove it by running one story end to end into a **separate, first PoC repo** (`ains-poc`). The PoC application itself (Phase 2) has its own plan and is not covered here.

**Status of this document:** plan only. Nothing below has been executed. Date: 2026-11-02 (revision 11: O4 resolved to all five gates; the sandbox project is kept after Phase 1 as the platform's permanent test target; the token is requested only at step 4.6).

**Inputs read:** `init/AI-native delivery stack.html` (architecture), `init/PoC build prompt.md` (build prompt).

---

## 0. Decisions taken

| # | Decision | Value | Source |
|---|----------|-------|--------|
| D1 | GitHub owner of all repos | `github.com/niloyray` | user |
| D2 | Orchestrator runtime | Docker container on this Windows laptop (Docker Desktop, Linux engine) | user |
| D3 | Jira to orchestrator intake | **Polling.** Orchestrator polls Jira; no inbound tunnel to the laptop | user |
| D4 | Model access | Claude Max subscription via `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`. No API key, no per-token billing. **Consequence:** the token may be used only by the Claude Code CLI itself (section 1b), so the orchestrator must *spawn* `claude` as a subprocess, never call a model API | user + Anthropic docs |
| D5 | Method | GitHub Spec Kit (`specify` CLI, integration key `claude`) | architecture doc |
| D6 | Phase separation | Platform work and application work never share a commit, PR or repo | build prompt |
| D7 | **Two GitHub repositories:** `ains-platform` (orchestrator, flows, agents, reusable workflow definitions, bootstrap tooling, docs) and `ains-poc` (application code, its `specs/`, thin caller workflows). GitHub hosts both | user |
| D8 | **One Jira project** `ains-poc`, key `AINSPOC`, holding PoC stories only. The platform has no Jira project | user |
| D9 | **Granular human approval gates**, each individually switchable per target: G1 spec, G2 plan and tasks, G3 code (PR), G4 staging, G5 production. See section 2e | user |
| D10 | **Homegrown orchestrator named `ains-orchestrator`**, harness-agnostic, Python, CLI command `ains`. Lives in `ains-platform/ains-orchestrator/`. Replaces the architecture doc's third-party "Ark" (section 1a) | user |
| D11 | **Phase order:** Phase 1 builds `ains-platform` end to end (steps 2 to 9) before any `ains-poc` work; Phase 2 then develops `ains-poc` on the platform | user |
| D12 | **Human tooling:** VS Code (Cursor-style IDE) with the Claude Code CLI for Phase 1 platform work and for Human-lane stories in Phase 2. No Cursor CLI; the Claude Code CLI is the only agent harness inside the orchestrator | user |
| D13 | **Jira, the orchestrator and GitHub Actions are platform components used only by the PoC.** The platform itself is built and changed directly from the workstation: commits straight to `ains-platform` main, no CI workflow, no story lane, no orchestrator target for the platform. The reusable Actions workflows are *defined* in `ains-platform` and *run* only in `ains-poc` | user |
| D15 | **Project onboarding is a platform capability.** `ains target add <repo> <jira-key> <jira-name> [gates]` creates the Jira project (field, statuses, Automation rule), the GitHub repo (branch protection with `ci` required, environments with reviewer, secrets, label `ai-review`, thin workflows, `CLAUDE.md`, `.claude/`, `.specify/`) and the `targets.yaml` block; `ains target remove` reverses it. Implemented by `bootstrap/new-target.sh` and its inverse, called from the CLI | user |
| D16 | **Phase 1 never touches `ains-poc`.** The platform ships with an empty `targets.yaml` and is proven on a throwaway sandbox project (`niloyray/ains-sandbox`, Jira key `AINSSB`) that is **kept after Phase 1** as the platform's permanent, tiny test target for future regression runs (user decision 2026-11-02: delete only if it stops being small and useful). The second sandbox used to prove removal is deleted. Phase 2 starts with `ains target add ains-poc AINSPOC ains-poc` | user |
| D14 | **Four stage agents, one harness.** The orchestrator spawns a fresh headless Claude Code CLI process per stage: spec, plan, implement and **review**. The review agent is triggered when a PR is marked ready (or when a Human-lane PR carries the label `ai-review`), reads the diff and CI result, runs the tests, posts a PR review, and never merges. The human G3 approval is still required before the orchestrator merges | user |

## 0a. Decisions still open

| # | Open item | Options | Blocks | Recommendation |
|---|-----------|---------|--------|----------------|
| O2 | Jira key for the PoC (Phase 2) | `AINSPOC`, or a shorter key such as `AINS` | Phase 2 day one | `AINSPOC` |
| O3 | Claude OAuth token | user runs `claude setup-token` interactively and hands over the token | Step 4.6 | run via `! claude setup-token` in the Claude Code session |
| O4 | Gate defaults for the acceptance run | resolved 2026-11-02: **all five gates on** for the sandbox acceptance story | — | done |

---

## 1. Research results

### 1a. Open-source orchestrator candidates (checked 2026-11-02)

Two different things get called "model-agnostic":

- **API-agnostic** orchestrators call model APIs themselves through a provider layer. They need an **API key** for every provider, so with Anthropic they bill per token. They cannot use the Max subscription (section 1b).
- **Harness-agnostic** orchestrators spawn coding-agent **CLIs** (`claude`, `codex`, `gemini`, `agy`) as subprocesses. Each CLI brings its own login, so the Claude Code CLI runs on the Max plan. This is the only category compatible with D4.

| Candidate | Category | Tracker in | SCM out | Agents | Licence | Maturity (2026-11-02) | Verdict |
|-----------|----------|-----------|---------|--------|---------|------------------------|---------|
| **`ains-orchestrator` (homegrown)** in `ains-platform/ains-orchestrator/` | harness-agnostic | Jira Cloud (poll) | GitHub | any CLI via YAML command template; Claude Code first | ours | about 400 to 700 lines of Python, fully under our control, gates G1 to G5 and the review stage native | **chosen (D10)** |
| `inkman97/yokai` ([repo](https://github.com/inkman97/yokai)) | harness-agnostic | Jira Cloud and DC (poll, trigger label) | **Bitbucket only**; no GitHub adapter | Claude Code only | MIT | alpha; 2 stars; last push 2026-06-04; "API unstable". Architecture is almost exactly this plan's design | viable fork if we want a third-party core; needs GitHub adapter and gate hooks |
| `chippingway/chipping-orchestrator` ([repo](https://github.com/chippingway/chipping-orchestrator)) | harness-agnostic | **GitHub issues only** | GitHub | `claude`, `codex`, `agy` with per-role choice; built-in reviewer agent and human merge gate | Apache-2.0 | 17 stars; active (pushed today); OpenSSF badges; Linux only | strong design reference for the reviewer role and merge gate; would need Jira-to-issue mirroring |
| `OrchestratorInc/agent-orchestrator` ([repo](https://github.com/OrchestratorInc/agent-orchestrator)) | harness-agnostic | none (chat or app) | GitHub PRs | 35 CLIs incl. Claude Code, Codex, Gemini CLI | Apache-2.0 | 14k stars, very active, desktop-first; daemon exists but headless Jira intake does not | too interactive for a headless lane |
| `mckinsey/agents-at-scale-ark` ([repo](https://github.com/mckinsey/agents-at-scale-ark)) | API-agnostic | none | none | its own agents over OpenAI, Anthropic, Google, Azure, Ollama APIs | Apache-2.0 | 434 stars; needs a Kubernetes cluster | **excluded**: needs API keys, so metered Anthropic billing |
| OpenHands headless | API-agnostic | GitHub resolver | GitHub | its own agent over LLM APIs | MIT | CLI marked no longer maintained | **excluded**: API keys |
| `BloopAI/vibe-kanban` | harness-agnostic | GUI | GitHub | 10 CLIs | Apache-2.0 | 28k stars but **sunsetting** | excluded |

Why homegrown over a fork: yokai is the closest match but dormant since June and lacks GitHub; chipping-orchestrator is active but GitHub-issue-centric. Writing the orchestrator is roughly the same effort as adapting either, keeps the Jira-first design, and keeps the gate model (D9) and the review stage (D14) first-class. The agent layer is a YAML command template, so adding Codex CLI or Gemini CLI later is a config change, each on its own subscription.

Note on the name "Ark": the architecture doc's `ark agent list` / `ark flow list` commands match no public project, and McKinsey's ARK under that name is a Kubernetes, API-key product. The homegrown orchestrator is therefore named `ains-orchestrator` (CLI `ains`) with the same command shape, and `docs/divergence.md` records that it stands in for the doc's "Ark" role at Layers 2 and 4.

### 1b. Does `claude setup-token` cause Claude API charges? No, with one condition

- Claude Code docs, Authentication page: "This token authenticates with your Claude subscription and requires a Pro, Max, Ultra, Team, or Enterprise plan." GitHub Actions page: "If you authenticate with an OAuth token, runs use your Claude subscription instead of API billing."
- Usage counts against Max plan limits (5-hour and weekly windows), not metered pricing.
- **Condition:** since early 2026 Anthropic blocks and contractually forbids using subscription OAuth tokens in third-party harnesses (OpenCode, Cline, etc. received "credential is only for Claude Code" errors). Only the Claude Code CLI, the Agent SDK and the official GitHub Action may use it. Therefore the orchestrator never reads the token itself; it only passes it through to `claude -p` subprocesses. Any API-agnostic orchestrator from section 1a would need `ANTHROPIC_API_KEY` and would be metered.
- Two small gotchas from the docs: bare mode (`claude --bare`) ignores the OAuth token, so flows never pass `--bare`; and if `ANTHROPIC_API_KEY` is present in the environment, non-interactive `claude -p` uses it silently, so the container environment must not contain any `ANTHROPIC_*` variable.

### 1c. Pre-flight state (verified 2026-11-02, read-only checks)

| Item | State | Consequence |
|------|-------|-------------|
| Jira API token | valid; resolves to user Niloy Ray | usable |
| Jira projects | **0** | `AINSPOC` must be created (step 3) |
| Jira "Execution Lane" field | absent | must be created (step 3) |
| GitHub `gh` auth | `niloyray` logged in but **not active**; `ROYNILO_T3DT` active | `gh auth switch` first (step 2) |
| GitHub PAT (niloyray) | valid to 2027-01-31; classic, scopes `repo, workflow, write:packages, project, user, codespace, delete:packages` | one PAT reaches every niloyray repo |
| Existing repo `niloyray/ainativesdlc` | framework docs and diagrams only | leave untouched |
| Docker Desktop | running, Linux engine 28.1.1, Compose v2.35, 22 CPU, 16 GB | ready |
| `claude` CLI on host | 2.1.316 | ready |
| `uv` | 0.12.1 | ready for Spec Kit |
| `specify` (Spec Kit) | not installed | installed inside the orchestrator image |
| `CLAUDE_CODE_OAUTH_TOKEN` | not set | see O3 |
| Host env | contains `ANTHROPIC_BASE_URL`-style variables with a stray prefix | container gets an explicit minimal env (4.5) |

**Secret hygiene.** The Jira token and GitHub PAT were shared in chat. They will be placed only in (a) the gitignored `ains-orchestrator/.env` in the platform working copy and (b) GitHub Actions secrets of `ains-poc` (plus `ains-platform` for `intake-ack` only). Never in committed files or this plan. Rotate both after the PoC.

---

## 2. Target design

### 2a. Platform repo — `niloyray/ains-platform` (code only, no Actions of its own)

```
ains-platform/
├── ains-orchestrator/
│   ├── Dockerfile                 # orchestrator + Claude Code CLI + Spec Kit + git + gh
│   ├── docker-compose.yml
│   ├── .env.example               # names only
│   ├── .env                       # gitignored
│   ├── claude-settings.json       # permission allowlist for headless Claude Code
│   ├── targets.yaml               # AINSPOC -> niloyray/ains-poc (the only target), gate set
│   ├── agents/
│   │   ├── claude-code.yaml       # harness template: claude -p ...; one prompt/skill per stage
│   │   ├── stages/                # spec.md, plan.md, implement.md, review.md prompts
│   │   └── _template.yaml         # how to add codex / gemini later
│   ├── flows/story.yaml           # spec -> G1 -> plan -> G2 -> implement -> review -> G3 -> merge -> G4 -> G5
│   └── src/ains_orchestrator/     # homegrown orchestrator (poll, state machine, gates, spawn)
├── .github/workflows/             # workflow_call DEFINITIONS only; nothing triggers here except intake-ack
│   ├── reusable-ci-python.yml     # ruff, pytest + Postgres, docker build
│   ├── reusable-release.yml       # GHCR push, staging (G4) -> production (G5)
│   ├── reusable-jira-callback.yml # transition + comment + remote link
│   └── intake-ack.yml             # repository_dispatch from Jira Automation (audit trace)
├── bootstrap/
│   ├── new-target.sh              # creates and wires a new PoC repo
│   └── templates/                 # caller workflows, .gitignore, README stub, CLAUDE.md, .specify/
├── docs/
│   ├── architecture.html          # copy of init/AI-native delivery stack.html
│   ├── ains-architecture.html     # diagram 1: phase overview (+ .svg, generator .py)
│   ├── ains-poc-flow.html         # diagram 2: one story phase by phase with the agents (+ .svg, generator .py)
│   ├── gates.md                   # gate model, how to switch gates per target
│   ├── divergence.md              # written in step 9
│   └── targets.md                 # PoC repos served by this platform
├── plan/ainativesdlc_plan.md      # this file
└── README.md
```

Per D13 the platform repo has no branch protection, no CI and no story lane: the engineer commits to `main` from VS Code with the Claude Code CLI. The `intake-ack.yml` workflow is the one exception that runs in this repo, because Jira Automation can only dispatch to one repo and the trace belongs to the platform.

### 2b. PoC repo — `niloyray/ains-poc` (one per PoC; where Actions run)

```
ains-poc/
├── .github/workflows/
│   ├── ci.yml        # on PR: uses niloyray/ains-platform/.github/workflows/reusable-ci-python.yml@main
│   └── release.yml   # on push main: reusable-release.yml then reusable-jira-callback.yml
├── .specify/         # Spec Kit templates
├── specs/<JIRA-ID>/  # spec.md, plan.md, tasks.md per story
├── app/              # Phase 2, separate plan
├── CLAUDE.md         # conventions every agent and developer reads
├── .gitignore
└── README.md
```

### 2c. Routing

```yaml
# ains-orchestrator/targets.yaml  -- ships EMPTY; `ains target add` appends one block per onboarded project
targets:
  AINSSB:                          # permanent sandbox: the platform's own regression target, kept after Phase 1
    repo: niloyray/ains-sandbox
    default_branch: main
    flow: story
    gates: {G1: on, G2: on, G3: on, G4: on, G5: on}
    review_label: ai-review        # Human-lane PRs with this label also get the review agent
  # AINSPOC -> niloyray/ains-poc is added on day one of Phase 2, never during Phase 1 (D16)
```

The poll JQL is `project in (<all target keys>) AND "Execution Lane" = "AI-native" AND status = Ready`, built from `targets.yaml` at each cycle. The orchestrator also polls every target repo for open PRs that are ready and either belong to one of its sessions or carry `review_label`. A new project = one `ains target add` run (D15). The platform repo is never a target (D13).

### 2d. The four stage agents (D14)

All four are the same harness, a fresh `claude -p` process spawned by the orchestrator in `/work/<KEY>/`, differing only in prompt, skill and permissions. Each writes into the clone and exits; the orchestrator commits, pushes and talks to Jira and GitHub.

| Stage agent | Spawned when | Skill / prompt | Reads | Writes | Orchestrator then waits for |
|-------------|--------------|----------------|-------|--------|-----------------------------|
| spec | story claimed | `/speckit-specify` | story text, `CLAUDE.md` | `specs/<KEY>/spec.md`; draft PR opened | G1 in Jira |
| plan | G1 approved | `/speckit-plan`, `/speckit-tasks` | `spec.md` | `plan.md`, `tasks.md` | G2 in Jira |
| implement | G2 approved | `/speckit-implement`, `--permission-mode acceptEdits`, `--max-turns` cap | `tasks.md`, repo | code, tests, commits prefixed `<KEY>:`; PR marked ready | CI and the review agent |
| review | PR ready, or Human-lane PR labelled `ai-review` | `/code-review` on the diff, test run, read-only permissions | diff, CI result, `spec.md`, `plan.md` | a PR review via `gh`: comments, approve or request changes; **never merges** | G3 human approval on the PR |

On Rework (G1, G2) or "changes requested" (G3; in label approval mode the label `g3-rework` plus a PR comment) the orchestrator spawns the same stage's agent again with the human comment appended to its input. A failed CI check at G3 is handled without a human: the implement agent is re-spawned with the failed-step log, at most twice, then the story is Blocked.

### 2e. Architecture diagrams

- **Diagram 1, the platform:** `plan/ains-architecture.html` (+ `.svg`, generated by `plan/ains-architecture-gen.py`). Phase 1 build steps **P1–P7** on the left, the platform runtime with agent usage **A1–A7** in the middle, and only a placeholder for onboarded projects on the right (the sandbox in Phase 1, `ains-poc` in Phase 2). A band on top names the human roles. No project internals are drawn here.
- **Diagram 2, the PoC, one story phase by phase:** `plan/ains-poc-flow.html` (+ `.svg`, generated by `plan/ains-poc-flow-gen.py`). Eight phases from populating Jira to release and Done as columns; humans, Jira, orchestrator, stage agents, LLM, repo/PR and Actions as rows; every spawned agent with its inputs and outputs, and a blue gate band in every phase where the platform waits for a human (G1, G2 in Jira, G3 on the PR, G4 and G5 in GitHub Environments).
- Both: blue arrows are human-to-machine, teal machine-to-machine, rose agent-to-LLM. Both are embedded in `init/AI-native delivery stack.html` (sections 03b and 03c). Step 2.5 copies the six files into `ains-platform/docs/`.

### 2f. Granular approval gates (D9)

| Gate | After stage | Where the human acts | Mechanism | Orchestrator behaviour |
|------|-------------|----------------------|-----------|------------------------|
| **G1 spec** | `spec` wrote `specs/<KEY>/spec.md` | Jira | Draft PR opened with only `spec.md`; Jira status `Spec Review`; comment links the PR. Human transitions to `Spec Approved` or `Rework` with a comment | polls status; on `Rework` re-spawns `spec` with the comment as input |
| **G2 plan** | `plan` wrote `plan.md`, `tasks.md` | Jira | same draft PR updated; status `Plan Review`; human transitions to `Plan Approved` or `Rework` | same |
| *(review stage)* | `implement` pushed code, PR marked ready, CI ran | — | review agent posts its PR review (D14) | spawns the review agent, then waits for G3 |
| **G3 code** | review agent posted | GitHub | PR approval by a human (`review` mode: PR approval; `label` mode: label `g3-approved`) + check `ci / ci` green; status `Code Review` | polls PR; on "changes requested" / `g3-rework` re-spawns `implement` with the human text; on CI failure re-spawns `implement` with the failed log (max 2 rounds) |
| **G4 staging** | PR merged, image built | GitHub | Environment `staging` required reviewer | Actions waits; Jira status `Staging` |
| **G5 production** | staging deployed | GitHub | Environment `production` required reviewer | Actions waits; on completion Jira `Done` |

Jira workflow: `To Do → Ready → Specifying → Spec Review → Planning → Plan Review → Implementing → Code Review → Staging → Done`, plus `Rework` reachable from any review status, plus `Blocked`. A human approves G1 by moving the story to **Planning** and G2 by moving it to **Implementing**. When a gate is `off`, the orchestrator auto-transitions through its review status and leaves a comment "G<n> auto-approved (gate off for this target)" so the trace shows the skip. Gate settings are per target in `targets.yaml`, so different PoCs can test different levels of autonomy, which is itself a research variable.

---

## 3. Enumerated steps

Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked.
Evidence = the command output, URL or run ID that proves the sub-step.

### Step 1 — Confirm open decisions (O2 to O4)

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 1.1 | O1 resolved 2026-11-02: homegrown `ains-orchestrator` (D10) | user | — | `[x]` |
| 1.2 | Confirm O2 Jira key | user | this file updated | `[ ]` |
| 1.3 | User runs `claude setup-token` and hands the token over (O3). **Claude asks for it only when reaching step 4.6, never earlier** | user | token present in `ains-orchestrator/.env` only | `[ ]` |
| 1.4 | O4 resolved 2026-11-02: all five gates on | user | — | `[x]` |

### Step 2 — GitHub account and platform repository (P1, P3)

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 2.1 | `gh auth switch --user niloyray`; `gh api user` returns `niloyray` | Claude | output shows login | `[ ]` |
| 2.2 | Create public repo `niloyray/ains-platform` with `main`, README, `.gitignore` | Claude | repo URL | `[ ]` |
| 2.3 | No branch protection, no CI (D13): commits go straight to `main` from the workstation | Claude | documented in README | `[ ]` |
| 2.4 | Actions secrets in `ains-platform` limited to `JIRA_EMAIL`, `JIRA_API_TOKEN` for `intake-ack`; everything else lives in each onboarded repo and is set by `ains target add` (6a) | Claude | `gh secret list` | `[ ]` |
| 2.5 | Copy `init/AI-native delivery stack.html` to `docs/architecture.html`, the six `plan/ains-*` diagram files to `docs/`, and this plan to `plan/`; first commit | Claude | commit SHA | `[ ]` |
| 2.6 | Open GitHub issues in `ains-platform`, one per step 2 to 9, under milestone "Phase 1" (tracking option T2) | Claude | milestone URL | `[ ]` |

### Step 3 — Project onboarding capability, Jira side (P2, D15)

What `ains target add` must do in Jira for **any** project. Built once, exercised on the sandbox key `AINSSB` in step 6a, reused unchanged for `AINSPOC` in Phase 2.

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 3.1 | Create a team-managed Kanban project from `<jira-key>` and `<jira-name>` via REST; idempotent if it exists | Claude | T-I16-5 | `[ ]` |
| 3.2 | Workflow statuses (section 2f): To Do, Ready, Specifying, Spec Review, Plan Review, Implementing, Code Review, Staging, Done, Rework, Blocked; REST where team-managed projects allow it, otherwise print the exact UI steps and verify afterwards by reading the statuses back | Claude | status list read back | `[ ]` |
| 3.3 | Custom field **Execution Lane**, single select `Human` / `AI-native`, on the Story screen; created once site-wide, attached per project | Claude | field id recorded in `targets.yaml` | `[ ]` |
| 3.4 | Automation rule "AI-native intake ack" per project: trigger = transition to Ready AND Execution Lane = AI-native; action = Send web request `POST https://api.github.com/repos/niloyray/ains-platform/dispatches`, body `{"event_type":"jira-story-ready","client_payload":{"key":"{{issue.key}}"}}`, header `Authorization: Bearer <GitHub PAT>`; Automation has no public create API, so the command prints the rule definition and a checklist, then verifies with a test story | Claude | rule URL and test run | `[ ]` |
| 3.5 | No Jira project, epic or story for the platform itself (D13) | — | — | `[x]` |
| 3.6 | `ains target remove` deletes the project (or archives it if deletion is restricted) and the rule, and reports what it could not undo | Claude | T-I16-6 | `[ ]` |

Note on 3.4. With polling (D3) the Automation rule is the **audit trace**, not the trigger. The orchestrator claims work by polling (4.9). The rule proves the Jira side fires and gives the story an immediate "accepted" comment via the platform repo's `intake-ack.yml`.

### Step 4 — Orchestrator container (Layer 2 and Layer 4), in the platform repo (P4, P5)

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 4.1 | `ains-orchestrator/src/ains_orchestrator/` Python package (build breakdown I1–I12 in section 9), CLI entry point `ains`: `ains agent list`, `ains flow list`, `ains targets list`, `ains session dispatch <KEY>`, `ains session advance <KEY>`, `ains session status <KEY>`, `ains poll`. State per session in SQLite on the `/work` volume so restarts resume | Claude | `ains --help` inside container; unit tests run locally with `uv run pytest` | `[ ]` |
| 4.2 | `ains-orchestrator/Dockerfile`: `python:3.12-slim`; git, curl, gh, Node 22, `uv`, Claude Code CLI, Spec Kit (`uv tool install specify-cli`), the `code-review` plugin from the official Claude plugin marketplace (so the review agent has its skill), orchestrator | Claude | `docker build` succeeds; T-I13-*, T-I9-7 | `[ ]` |
| 4.3 | `ains-orchestrator/docker-compose.yml`: service `orchestrator`, named volume `ains-workspaces:/work`, `env_file: .env`, `restart: unless-stopped`, no published ports | Claude | `docker compose config` | `[ ]` |
| 4.4 | `ains-orchestrator/.env.example` keys: `CLAUDE_CODE_OAUTH_TOKEN`, `GITHUB_TOKEN`, `GH_TOKEN`, `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `AINS_POLL_SECONDS=60`; `.env` gitignored | Claude | `.env` absent from `git status` | `[ ]` |
| 4.5 | `agents/claude-code.yaml`: `claude -p --output-format json --settings /app/claude-settings.json`, working dir `/work/<KEY>`, plus per-stage overrides from `agents/stages/*.md` (implement: `--permission-mode acceptEdits --max-turns N`; review: read-only tools, `/code-review`); the orchestrator passes **only** `CLAUDE_CODE_OAUTH_TOKEN` into the subprocess env; container has **no** `ANTHROPIC_*` variables; never `--bare` | Claude | `env` inside container; a test run shows subscription auth in `claude -p` JSON output | `[ ]` |
| 4.6 | Place the OAuth token from 1.3 into `.env`; `docker compose run orchestrator claude -p "say ok"` returns ok | Claude | output | `[ ]` |
| 4.7 | `targets.yaml` as in 2c; the orchestrator refuses a story whose project has no mapping and comments on it | Claude | unit test | `[ ]` |
| 4.8 | `flows/story.yaml` stages and gates per 2d and 2f: `spec` (clone target, spec agent, draft PR) → G1 → `plan` (plan agent) → G2 → `implement` (implement agent; branch `<KEY>-<slug>`; commits prefixed `<KEY>:`; PR ready, title `<KEY>: ...`) → `review` (review agent posts a PR review) → G3 → `merge` (`gh pr merge --squash`) → G4, G5 handled by Actions. `Rework` and "changes requested" loop back to the stage before the gate with the human comment as extra input | Claude | `ains flow list` shows `story`; state-machine tests | `[ ]` |
| 4.9 | Poll loop: JQL from 2c; on hit transition to Specifying (claim), comment "ains-orchestrator picked up, target <repo>, session <id>, gates <config>", start the flow | Claude | log line on a test story | `[ ]` |
| 4.10 | PR watcher: poll the target repo for PRs that became ready (own sessions) or carry `review_label` (Human lane); spawn the review agent once per head SHA; record the review in the session | Claude | review posted on a test PR | `[ ]` |
| 4.11 | `ains agent list`, `ains flow list`, `ains targets list`, `specify check` all succeed inside the running container | Claude | four outputs | `[ ]` |

### Step 5 — Spec Kit inside the container (Layer 3)

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 5.1 | `specify check` passes with Claude Code detected as the AI tool | Claude | output | `[ ]` |
| 5.2 | `bootstrap/templates/` carries the `.specify/` layout from `specify init --integration claude`, so every new PoC repo starts with the same method templates | Claude | files in platform repo | `[ ]` |
| 5.3 | `spec` and `plan` agents write `specs/<KEY>/spec.md`, then `plan.md` and `tasks.md`, in the **target** repo on the story branch, each followed by its gate | Claude | files exist on first run | `[ ]` |

### Step 6 — Reusable workflow definitions and bootstrap (Layer 5, platform side)

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 6.1 | GitHub PAT into `.env` as `GITHUB_TOKEN` and `GH_TOKEN` | Claude | `gh auth status` inside container | `[ ]` |
| 6.2 | Same PAT pasted into the Jira Automation rule (3.4) | Claude | rule test returns 204 | `[ ]` |
| 6.3 | `intake-ack.yml`: on `repository_dispatch` type `jira-story-ready`, comment on the Jira issue "Accepted into AI-native lane, dispatch run <run-id>". The only workflow that runs in `ains-platform` | Claude | run URL | `[ ]` |
| 6.4 | `reusable-ci-python.yml` (`workflow_call`): ruff, pytest with Postgres service container, `docker build`; job name `ci` | Claude | called successfully from the sandbox repo in 6a | `[ ]` |
| 6.5 | `reusable-release.yml` (`workflow_call`, input `image_name`): build and push `ghcr.io/niloyray/<image>:<sha>`; job `staging` (environment staging, G4); job `production` (environment production, G5); each job calls the Jira callback with status `Staging` then `Done` | Claude | two environment deployments visible | `[ ]` |
| 6.6 | `reusable-jira-callback.yml` (`workflow_call`, inputs `issue_key`, `transition`, `comment`, `link_url`; secrets `JIRA_EMAIL`, `JIRA_API_TOKEN`) | Claude | story updated from a test call | `[ ]` |
| 6.7 | Removed per D13: no platform CI. Orchestrator tests run locally (`uv run pytest`) before each commit | — | — | `[x]` |
| 6.8 | `bootstrap/new-target.sh <repo> <jira-key> <jira-name> [gates]`: creates the GitHub repo, protects `main` (PR + `ci` required), creates Environments with required reviewer, sets Jira secrets and variable in the new repo, creates the `ai-review` label, copies caller workflows, `.specify/`, `CLAUDE.md`, README stub; creates the Jira project, statuses, field and Automation rule (prints manual UI steps where REST cannot); runs `specify init --integration claude` (installs `.specify/` and the `.claude/skills/speckit-*/` skills) and a templated `.claude/settings.json` (per-repo tool allowlist) into the new repo; appends the block to `targets.yaml` and commits it to `ains-platform` main | Claude | dry-run output; T-I16-* | `[ ]` |

### Step 6a — Sandbox target for live tests

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 6a.1 | `ains target add ains-sandbox AINSSB sandbox`: the first real onboarding, creating the throwaway repo `niloyray/ains-sandbox` and Jira project `AINSSB` with everything step 3 and 6.8 describe; used by every L4 live test and by the acceptance run | Claude | T-I16-3, T-I16-5 | `[ ]` |
| 6a.2 | `ains target add ains-sandbox-2 AINSSB2 sandbox-2` a second time proves the capability is reusable, then `ains target remove ains-sandbox-2` proves the inverse | Claude | T-I16-7, T-I16-6 | `[ ]` |
| 6a.3 | At the end of Phase 1 the sandbox **stays** as the permanent regression target (`targets.yaml` keeps its block); only `ains-sandbox-2` is removed. Evidence kept in `docs/test-evidence.md` | Claude | T-I16-6 | `[ ]` |

### Step 7 — Deferred to Phase 2: onboarding `ains-poc` (D16)

Not part of Phase 1. Listed here only so the hand-off is explicit; these rows move to the top of the Phase 2 plan.

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 7.1 | Phase 2 day one: `ains target add ains-poc AINSPOC ains-poc` creates the Jira project and the repo in one run, using the capability proven in 6a | Claude | repo URL, `targets.yaml` commit | deferred |
| 7.2 | `ains-poc` `ci.yml` (from the template): `on: pull_request` calls `reusable-ci-python.yml@main` with `secrets: inherit`, then the Jira callback with transition `Code Review` and the PR link | Claude | green run on a PR | deferred |
| 7.3 | `ains-poc` `release.yml` (from the template): `on: push` to `main` calls `reusable-release.yml` with `secrets: inherit` (which handles G4, G5 and the Jira callbacks). Issue key parsed from the merged PR title | Claude | story `Done` plus link after merge | deferred |
| 7.4 | `ains-poc` `CLAUDE.md` (template plus PoC-specific rules): Jira key in every commit, no secrets, keep `app/` self-contained, run tests before PR, never edit `.github/` or `.specify/` from a story; review conventions the review agent applies | Claude | file present | deferred |
| 7.5 | `ains-poc` contains **no** application code at onboarding; `app/` is created by Phase 2 stories, starting with `AINSPOC-1` as the Phase 2 acceptance story | Claude | tree shows only scaffolding | deferred |

### Step 8 — End-to-end acceptance run on the sandbox project (platform validates itself) (P7, D16)

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 8.1 | Story `AINSSB-1 "Repository scaffold: README section and health-check script"` in the sandbox project, Execution Lane AI-native, small unambiguous description; all five gates on (O4) | Claude | story URL | `[ ]` |
| 8.2 | Transition to Ready. Observe: intake-ack comment, claim (Specifying), spec agent, draft PR with `spec.md`, status `Spec Review` (phases 1 to 3) | Claude (observe) | timestamps | `[ ]` |
| 8.3 | **G1:** user reads `spec.md`, transitions to `Spec Approved` (or `Rework` once, to prove the loop) | user | transition | `[ ]` |
| 8.4 | Observe plan agent, `plan.md`, `tasks.md` committed, status `Plan Review` (phase 4) | Claude (observe) | timestamps | `[ ]` |
| 8.5 | **G2:** user transitions to `Plan Approved` | user | transition | `[ ]` |
| 8.6 | Observe implement agent, PR ready, CI green, status `Code Review`, then the **review agent's PR review posted** (phases 5 and 6) | Claude (observe) | PR URL, review URL, run URL | `[ ]` |
| 8.7 | **G3:** user approves the PR after reading the agent review | user | approval | `[ ]` |
| 8.8 | Observe squash merge, release run, image in GHCR, status `Staging` (phase 8 begins) | Claude (observe) | run URL | `[ ]` |
| 8.9 | **G4:** user approves `staging` environment | user | deployment | `[ ]` |
| 8.10 | **G5:** user approves `production` environment; observe Jira `Done` with PR and run links | user, then Claude (observe) | story `Done` | `[ ]` |
| 8.11 | Acceptance criterion: the only manual touches were the five gate actions (plus one deliberate `Rework`). Anything else means fix the platform directly (D13) and re-run with `AINSSB-2` | Claude | checklist | `[ ]` |
| 8.12 | Human-lane smoke test: a tiny PR opened by hand in `ains-sandbox` with label `ai-review` gets a review from the review agent and still needs G3 | user + Claude (observe) | review URL | `[ ]` |
| 8.13 | Generic-capability check: the whole of 8.1 to 8.12 used only `ains target add`, the shipped templates and the orchestrator; nothing in `ains-platform` names the sandbox except `targets.yaml` and the evidence log | Claude | `grep -r sandbox ains-platform` | `[ ]` |

### Step 9 — Record gaps and divergences

| Sub-step | Action | Owner | Verification | Status |
|----------|--------|-------|--------------|--------|
| 9.1 | `docs/divergence.md`: table against section 05 of the architecture doc. Known entries: (a) intake is pull, not push; (b) orchestrator is the homegrown `ains-orchestrator`, not a third-party Ark; (c) runs in a local container, not a VM; (d) release environments are gates, not hosts; (e) platform and application split across two GitHub repos, one Jira project for the PoC only, platform outside the story lane; (f) five switchable gates instead of the doc's single review gate; (g) four stage roles on one harness instead of eight agents | Claude | file committed | `[ ]` |
| 9.2 | `docs/gates.md` and `docs/targets.md` written | Claude | files committed | `[ ]` |
| 9.3 | Update the status summary to all `[x]` and close the "Phase 1" milestone in `ains-platform` | Claude | milestone closed | `[ ]` |

---

## 4. Secrets and credentials inventory (names only, never values)

| Name | Stored where | Used by | Rotation |
|------|--------------|---------|----------|
| `CLAUDE_CODE_OAUTH_TOKEN` | `ains-orchestrator/.env` | passed only into `claude -p` subprocesses (all four stage agents) | one-year token tied to niloyray's Max account; re-run `claude setup-token` |
| GitHub PAT (niloyray, classic) | `.env` as `GITHUB_TOKEN` and `GH_TOKEN`; the Jira Automation rule | orchestrator (clone, push, PR, review posting, merge in `ains-poc`); Jira `repository_dispatch`; bootstrap script | expires 2027-01-31 |
| `JIRA_API_TOKEN` and `JIRA_EMAIL` | `.env`; Actions secrets in `ains-poc` (bootstrap sets them) and in `ains-platform` only for `intake-ack` | orchestrator poll, claim, transitions, comments; intake-ack; reusable Jira callback | rotate after PoC |
| `GITHUB_TOKEN` (Actions default) | automatic per repo | CI, GHCR push in `ains-poc` | not applicable |

Never in: committed files, this plan, PR bodies, logs.

---

## 5. Tracking options

| Option | What | Pros | Cons |
|--------|------|------|------|
| **T1 — this file** (default) | Status column per sub-step, Evidence filled as each completes; one commit per step in `ains-platform` | zero setup, lives with the platform, reviewable in git | not visible outside the repo |
| **T2 — GitHub issues + milestone in `ains-platform`** (recommended alongside T1) | one issue per step 2 to 9 under milestone "Phase 1" | GitHub serves both repos (D7); keeps Jira for PoC stories only (D13) | small amount of issue admin; created in 2.6 |
| T3 — Jira epic for the platform | epic plus tasks in a platform project | same board as PoC stories | contradicts D13 |

**Chosen:** T1 plus T2. T3 not used.

### Step status summary (update as work proceeds)

| Step | Title | Status | Blocked by | Evidence |
|------|-------|--------|------------|----------|
| 1 | Confirm open decisions | `[ ]` | user | |
| 2 | GitHub account and platform repo | `[ ]` | — | |
| 3 | Project onboarding capability, Jira side | `[ ]` | — | |
| 4 | Orchestrator container, four stage agents, PR watcher | `[ ]` | O3 | |
| 5 | Spec Kit in container and templates | `[ ]` | 4 | |
| 6 | Reusable workflow definitions and `ains target add` / `remove` | `[ ]` | 2, 3 | |
| 6a | Sandbox project onboarded, second onboarding and removal proven | `[ ]` | 6 | |
| 7 | Onboarding `ains-poc` | deferred to Phase 2 | — | |
| 8 | Acceptance run on the sandbox through all five gates and the review agent | `[ ]` | 3 to 6a | |
| 9 | Divergence, gates and targets docs | `[ ]` | 8 | |

---

## 5a. Test plan

`plan/ains-platform-test-plan.md` holds one test-case table per build item I1 to I19, the fakes and fixtures, the six test levels, and the credential and billing checks that prove the Max subscription is used and the Anthropic API never is. The Evidence column of the step tables above cites its test ids.

## 6. Execution order and parallelism

Steps 2, 3 and the workflow authoring in 6.3 to 6.6 are independent and can proceed together. Step 4 depends on O3. Step 7 depends on 6.8. Step 8 needs everything before it. Estimated effort: about 12 focused sessions per the component breakdown in section 9; the gate state machine, the PR watcher with the review agent, and the bootstrap script are the three largest pieces. The acceptance run is an hour or two of wall-clock, mostly waiting on Actions and five human approvals.

---

## 7. Risks and open questions

| Risk | Impact | Mitigation |
|------|--------|------------|
| Anthropic tightens subscription-token rules further | headless `claude -p` on Max stops working | design keeps `claude` as the only consumer of the token, which is the documented CI use; fallback is an API key in `.env`, which is a one-line change but metered |
| Headless Claude Code needs permission decisions | implement stage stalls | `--permission-mode acceptEdits`; allowlist `Bash(git *)`, `Bash(gh *)`, `Bash(uv *)`, `Bash(pytest *)` in `claude-settings.json`; `--max-turns` cap; review agent gets read-only tools |
| Review agent approves its own lane's PR | rubber-stamp | the agent's review is advisory; G3 requires a human approval and branch protection enforces it |
| Max plan rate limits during a long implement stage | stage fails mid-run | small stories; one retry after back-off; limits reset in 5-hour windows |
| Team-managed Jira projects limit workflow and field edits via REST | 3.2, 3.3 fail | UI fallback documented step by step in `bootstrap/new-target.sh` output; or fall back to label `ai-native-lane` |
| Platform has no CI (D13) | a broken orchestrator commit reaches `main` unnoticed | local `uv run pytest` before every commit; the acceptance story is the integration test; re-run `AINSPOC-n` after platform changes |
| Five gates make the loop slow | acceptance run takes long | gates are per target; after `AINSPOC-1` relax to G1, G3, G5 for Phase 2 stories |
| Reusable workflows referenced `@main` change under a PoC | PoC CI breaks on a platform change | PoC callers pin `@v1` once stable; `@main` only during Phase 1 |
| Laptop sleeps or Docker Desktop stops | polling pauses | acceptable for PoC; recorded as VM-vs-laptop divergence |
| Branch protection in `ains-poc` blocks the squash merge | merge stage fails | PAT owner is repo admin; merge waits for G3 then merges via `gh` as niloyray |
| Automation web request to GitHub fails | no intake-ack comment, polling still works | rule audit log; PAT `repo` scope covers `repository_dispatch` |
| Story in a project with no mapping | silent drop | 4.7: comment on the issue, leave in Ready |
| Homegrown orchestrator is itself a research subject | bugs attributed to the pattern rather than our code | keep it small, tested, and documented; `docs/divergence.md` names it as homegrown |

---

## 8. Done criteria for Phase 1

1. `ains agent list`, `ains flow list`, `ains targets list` and `specify check` succeed inside the running container.
2. `ains target add` onboarded the sandbox project (Jira project, repo, protection, environments, secrets, templates, `targets.yaml`) in one run, did it a second time for a second sandbox, and `ains target remove` reversed both.
3. `AINSSB-1` travelled Ready through all five gates to Done with Jira comments and PR link, the review agent posted its review before G3, and manual touches were limited to the gate actions.
4. A Human-lane PR labelled `ai-review` in the sandbox received an agent review.
4a. `ains-poc` does not exist yet; `targets.yaml` holds only the permanent sandbox block; the only other mention of a project in `ains-platform` is in the docs and the evidence log (D16).
5. `docs/divergence.md`, `docs/gates.md` and `docs/targets.md` committed in `ains-platform`.
6. Every row of the status summary is `[x]` with evidence.

---

## 9. Implementation steps for the ains-architecture

This section turns diagram 1 (`plan/ains-architecture.html`) into an engineering work breakdown. Steps 2 to 9 above say *what* gets set up and in which order; the items below say *how each component is built*, which files it produces, how it is tested, and when it is done. Numbering is **I1 to I19**; each item names the P-step and diagram box it realises. Everything is built directly from the workstation per D13, committed to `ains-platform` main, tested locally with `uv run pytest`.

### 9.1 Build order and dependencies

```
I1 repo skeleton
 ├─ I2 config schemas ──┬─ I6 poller ───────┐
 ├─ I3 Jira client ─────┤                   │
 ├─ I4 GitHub client ───┼─ I7 state machine ┼─ I8 agent runner ─ I9 stage prompts ─ I10 PR watcher + review
 └─ I5 session store ───┘        │          │
                                 └─ I11 workspace manager
I12 CLI  (wraps I6–I11)          I13 Dockerfile + compose  (packages I1–I12)
I14 reusable workflows           I15 intake-ack            I16 bootstrap + templates
I17 docs                         I18 test harness (grows with every item)   I19 acceptance (= step 8)
```

I2 to I5 are independent and can be built in any order. I6, I7, I11 need I2 to I5. I8 needs I7 and I11. I9 needs I8. I10 needs I8 and I4. I12 wraps everything. I13 to I16 are independent of the Python code except that I13 copies it into the image.

### 9.2 Component by component

| # | Component (diagram box) | Realises | Produces | Acceptance check | Size |
|---|-------------------------|----------|----------|------------------|------|
| I1 | **Repo skeleton** (Repo ains-platform) | P3 | `ains-platform/` layout from section 2a, `pyproject.toml` (uv, Python 3.12, `ains` entry point), `.gitignore` incl. `.env`, README stub, `LICENSE` | `uv sync` and `uv run ains --help` succeed on the workstation | ½ session |
| I2 | **Config schemas** (targets.yaml, flows, agents) | P3 | Pydantic models + loaders for `targets.yaml`, `flows/story.yaml`, `agents/*.yaml`; JSON schemas exported to `schemas/` for editor validation | invalid YAML is rejected with a line number; the three shipped files validate | ½ session |
| I3 | **Jira client** | A1, A7 | `jira.py`: JQL search, get issue, transition by name, add comment, add remote link; basic-auth with `JIRA_EMAIL` + `JIRA_API_TOKEN`; retries on 429/5xx; dry-run flag | unit tests against recorded JSON; one live smoke test against `AINSPOC` behind an env flag | ½ session |
| I4 | **GitHub client** | A5, A6 | `github.py`: thin wrapper over `gh` CLI (clone, branch, commit, push, `pr create/ready/review/merge`, `pr view --json`, label query); PAT via `GH_TOKEN`; dry-run flag | unit tests with a fake `gh` on `PATH`; live smoke test against `ains-poc` behind an env flag | ½ session |
| I5 | **Session store** (SQLite on /work) | A1–A6 | `store.py`: tables `sessions` (key, target, flow, stage, gate_state, branch, pr_number, head_sha, created, updated), `events` (session, ts, kind, detail); idempotent upserts; `ains session status` reads it | restart in the middle of a stage resumes at the same stage; events show the full trace | ½ session |
| I6 | **Poller** | A1 | `poller.py`: every `AINS_POLL_SECONDS` run the JQL from 2c, skip keys with an open session, claim = transition → Specifying + comment with session id, insert session | a Ready story is claimed exactly once even with two poll cycles overlapping | ½ session |
| I7 | **Flow state machine + gates** | A2, G1–G5 | `flow.py`: stages and gates from `flows/story.yaml`; states `spec → G1 → plan → G2 → implement → review → G3 → merge → released` + `rework(stage)` + `blocked`; gate evaluation per target (`on` waits, `off` auto-passes with a Jira comment); Rework and "changes requested" return to the previous stage with the human comment attached | table-driven tests for every transition, gate on/off, Rework from G1/G2, changes requested at G3 | 1 session |
| I8 | **Agent runner** (Claude Code CLI stage agents) | A2–A4 | `agent.py`: spawn `claude -p` from the agent template with a **minimal env** (`PATH`, `HOME`, `CLAUDE_CODE_OAUTH_TOKEN`, nothing `ANTHROPIC_*`), cwd `/work/<KEY>`, `--output-format json`, per-stage `--permission-mode`, `--max-turns`, `--settings /app/claude-settings.json`; capture JSON result and cost fields into `events`; one retry after back-off; never `--bare` | fake `claude` on `PATH` proves the env is minimal and args are correct; live test `claude -p "say ok"` inside the container shows subscription auth | 1 session |
| I9 | **Stage prompts + Spec Kit** | A3 | `agents/stages/spec.md`, `plan.md`, `implement.md`, `review.md`: each names the Spec Kit skill to run (`/speckit-specify`, `/speckit-plan` + `/speckit-tasks`, `/speckit-implement`, `/code-review`), the inputs, the expected outputs and the stop rule; `claude-settings.json` allowlists `git`, `gh`, `uv`, `pytest` and denies network tools for the review stage | each prompt run against the `ains-poc` scaffold produces the expected files or review; outputs checked by file existence and a schema for the review comment | 1 session |
| I10 | **PR watcher + review stage** | A6, D14 | `watcher.py`: every poll cycle list open PRs of each target; for a PR that is ready and belongs to a session, or carries `review_label`, spawn the review agent once per head SHA; post the result as a PR review via I4; record `reviewed_sha`; then the state machine waits for a human approval (G3) and green `ci` before merge | a PR pushed twice gets exactly two reviews; a Human-lane PR with the label gets one; approval by the agent alone never merges | 1 session |
| I11 | **Workspace manager** | A5 | `workspace.py`: clone target into `/work/<KEY>/`, create branch `<KEY>-<slug>`, commit with `<KEY>:` prefix, push, open draft PR, mark ready, squash merge after G3, delete workspace on Done; disk quota check | end-to-end on a throwaway repo behind an env flag; unit tests with the fake `gh` | ½ session |
| I12 | **CLI `ains`** | diagram "Container" | `cli.py` (typer): `ains agent list`, `ains flow list`, `ains targets list`, `ains session dispatch <KEY>`, `ains session advance <KEY>`, `ains session status <KEY>`, `ains poll [--once]`, `ains doctor` (checks tokens, `gh auth`, `claude -p`, `specify check`) | `ains doctor` green inside the container; every command has a `--dry-run` | ½ session |
| I13 | **Dockerfile + compose** | P4 | `Dockerfile` (python:3.12-slim, git, curl, gh, Node 22, uv, Claude Code CLI, Spec Kit, the package), `docker-compose.yml` (service `orchestrator`, volume `ains-workspaces:/work`, `env_file: .env`, `restart: unless-stopped`, no ports), `.env.example` | `docker compose up -d` then `docker compose exec orchestrator ains doctor` green; image size recorded | ½ session |
| I14 | **Reusable Actions workflows** | A7 | `reusable-ci-python.yml` (ruff, pytest with a Postgres service, `docker build`; job `ci`), `reusable-release.yml` (GHCR push, environments `staging` and `production`, Jira callbacks), `reusable-jira-callback.yml` (transition, comment, remote link) | called from `ains-poc`'s thin workflows: green CI on a PR, two environment deployments, three Jira updates | 1 session |
| I15 | **intake-ack** | A7 (audit trace) | `intake-ack.yml` on `repository_dispatch` type `jira-story-ready` → Jira comment | a test dispatch produces the comment on the story within a minute | ¼ session |
| I16 | **Project onboarding: `ains target add` / `remove` + templates** | P6, D15 | `bootstrap/new-target.sh` and `bootstrap/templates/` (thin `ci.yml`, `release.yml`, `CLAUDE.md`, `.specify/` from `specify init --integration claude`, README stub, `.gitignore`); creates repo, branch protection with `ci` required, environments with reviewer, secrets, label `ai-review`, Jira project pieces via REST where possible, prints the manual UI steps otherwise, appends the `targets.yaml` block | dry-run prints the full plan; real run creates `ains-poc` in one go (step 7.1) | 1 session |
| I17 | **Docs** | diagram "docs/" | `docs/architecture.html`, the six diagram files, `docs/gates.md`, `docs/targets.md`, README for operators (start, stop, rotate tokens, read the trace) | a newcomer can start the container and run `ains doctor` from the README alone | ½ session |
| I18 | **Test harness** | all | `tests/` with fakes for `gh`, `claude`, Jira HTTP; a `--dry-run` mode that executes a whole flow without network; GitHub-free CI is **not** added (D13), tests run with `uv run pytest` before each commit | the whole story flow runs in dry-run in under a minute | grows with I2–I11 |
| I19 | **Acceptance** | P7 | step 8 above: `AINSPOC-1` through every phase and gate, plus the Human-lane review smoke test | done criteria in section 8 | 1 session |

Total: roughly 12 sessions of focused work, consistent with the 4 to 6 working sessions estimate in section 6 only if sessions are long; plan for the upper end.

### 9.3 Orchestrator internals, the parts the diagrams only name

**State machine.** One row per story in `sessions`. Stage transitions are the only writes to `stage`; gates are evaluated on every poll cycle.

| From | Event | To | Side effects |
|------|-------|----|--------------|
| (none) | story found by JQL | `spec` | claim: Jira → Specifying, session comment, clone, branch |
| `spec` | spec agent exit 0, `spec.md` present | `wait_g1` | commit, push, draft PR, Jira → Spec Review |
| `wait_g1` | Jira status Spec Approved, or gate off | `plan` | comment "G1 passed" (or "G1 auto-approved") |
| `wait_g1` | Jira status Rework | `spec` | comment text appended to the spec prompt |
| `plan` | plan agent exit 0, `plan.md` + `tasks.md` present | `wait_g2` | commit, push, Jira → Plan Review |
| `wait_g2` | Plan Approved / gate off | `implement` | Jira → Implementing |
| `wait_g2` | Rework | `plan` | as above |
| `implement` | implement agent exit 0, tests pass locally | `review` | commit, push, PR ready (CI starts, callback sets Code Review) |
| `review` | review agent posted a review for the head SHA | `wait_g3` | event recorded |
| `wait_g3` | human approval + `ci` success, or gate off and `ci` success | `merge` | — |
| `wait_g3` | review "changes requested" by human | `implement` | review comments appended to the implement prompt |
| `merge` | squash merge done | `released` | workspace deleted; release workflow and G4/G5 happen in Actions; Jira callbacks set Staging and Done |
| any | agent exit ≠ 0 twice, or `--max-turns` hit twice | `blocked` | Jira → Blocked with the last error; human unblocks by moving the story back to Ready |

**Agent template** (`agents/claude-code.yaml`), the only harness in this PoC:

```yaml
name: claude-code
command: ["claude", "-p", "--output-format", "json", "--settings", "/app/claude-settings.json"]
env_passthrough: [CLAUDE_CODE_OAUTH_TOKEN]        # nothing else from the container env
stages:
  spec:      {prompt: agents/stages/spec.md,      permission_mode: acceptEdits, max_turns: 30}
  plan:      {prompt: agents/stages/plan.md,      permission_mode: acceptEdits, max_turns: 30}
  implement: {prompt: agents/stages/implement.md, permission_mode: acceptEdits, max_turns: 80}
  review:    {prompt: agents/stages/review.md,    permission_mode: plan,        max_turns: 20, read_only: true}
retry: {attempts: 2, backoff_seconds: 300}
```

**Flow definition** (`flows/story.yaml`):

```yaml
name: story
stages: [spec, plan, implement, review, merge]
gates:
  G1: {after: spec,      where: jira,   approve: "Spec Approved", rework: "Rework"}
  G2: {after: plan,      where: jira,   approve: "Plan Approved", rework: "Rework"}
  G3: {after: review,    where: github, approve: pr_approved_and_ci_green, rework: changes_requested}
  G4: {after: merge,     where: actions_environment, name: staging}
  G5: {after: G4,        where: actions_environment, name: production}
```

**Events and the research log.** Every transition, agent run (with turns, duration and the cost fields the CLI reports) and gate decision is an `events` row. `ains session status <KEY> --json` exports them; the divergence note and the Phase 2 comparison of lanes are built from these exports.

**Operational rules.** One story at a time per target (per-repo lock); poll interval 60 s; agents run with a 30-minute wall-clock limit; the container restarts on failure and resumes from `sessions`; tokens live only in `.env`; `ains doctor` is the first command after any change.

### 9.4 Definition of done for the ains-architecture

1. I1 to I18 merged to `ains-platform` main with passing local tests and a green `ains doctor` inside the container.
2. Diagram 1's every box has a corresponding file or service listed in 9.2.
3. I19 (step 8) passed: `AINSPOC-1` through all gates with the review agent's review before G3, and one Human-lane PR reviewed on label.
4. `docs/divergence.md` lists every place the implementation departs from the architecture document and from this plan.
