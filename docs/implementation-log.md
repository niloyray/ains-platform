# AINS platform — implementation and test log

Running log of what was built, what was tested and what the result was. One entry per step or build item, newest at the bottom. Test ids refer to `plan/ains-platform-test-plan.md`; step and item numbers to `plan/ainativesdlc_plan.md`. Commit SHAs are on `main` of `niloyray/ains-platform`. No secret values appear in this file.

Legend: PASS · FAIL · SKIP (with reason) · N/A

---

## 2026-11-02 · Phase 1 started

### Step 2.1 — GitHub account

| What | Result |
|------|--------|
| `gh auth switch --user niloyray`; `gh api user` | PASS: active account niloyray |
| git identity | niloyray / rayniloy@gmail.com (global config) |

### Step 2.2 — Platform repository

| What | Result |
|------|--------|
| `gh repo create niloyray/ains-platform --public --clone` | PASS: https://github.com/niloyray/ains-platform, empty, default branch `main` |
| cloned to `PoC/ains-platform/` beside `init/` and `plan/` | PASS |

### Step 2.3 / 2.4 — No branch protection, no CI (D13); secrets

| What | Result |
|------|--------|
| branch protection | N/A by design (D13): commits straight to `main` |
| Actions secrets in this repo | deferred to I15 (intake-ack is the only workflow here); none set yet |

### I1 — Repo skeleton (step 2.5)

| Test | Result |
|------|--------|
| layout from plan 2a created: `ains-orchestrator/` (pyproject, `src/ains_orchestrator/`, `targets.yaml` empty, `flows/story.yaml`, `agents/claude-code.yaml`, `.env.example`), `.github/workflows/`, `bootstrap/templates/`, `docs/`, `plan/`, README, LICENSE (MIT), `.gitignore` | done |
| docs copied: `docs/architecture.html` (from `init/`), both diagrams with generators, `plan/ainativesdlc_plan.md`, `plan/ains-platform-test-plan.md` | done |
| T-I1-1 `uv sync` | PASS: 19 packages, lock file created |
| T-I1-2 `uv run ains --help` | PASS: groups `agent`, `flow`, `targets`, `session`, `target`, commands `poll`, `doctor` |
| `uv run ains --version` | FAIL first run ("Missing command": root callback did not run without a subcommand); fixed with `invoke_without_command=True` and an eager option; re-run PASS: `ains-orchestrator 0.1.0` |
| T-I1-3 `git check-ignore ains-orchestrator/.env` | PASS: ignored |
| T-I1-4 secret-value scan (`ghp_…`, `ATATT3…` patterns) | PASS: no match |
| `uv run ruff check .` + `ruff format --check` | first run FAIL: three E501 lines; wrapped; re-run PASS |
| commit | `fb6dc39` I1 skeleton pushed to `main`; follow-up commit with the lint fix, `.gitattributes` (LF normalisation) and this log |

### Step 2.6 — Tracking (option T2)

| What | Result |
|------|--------|
| milestone "Phase 1" | created, number 1 |
| issues #1 to #9, one per plan step 2, 3, 4, 5, 6, 6a, 8, 9 plus cross-cutting | created, all on milestone Phase 1 |

Step 2 complete. Next: I2 config schemas, then I3 to I5 (Jira client, GitHub client, session store), all offline.

### I2 — Config schemas · I3 — Jira client · I4 — GitHub client · I5 — Session store

Built together because they are independent (plan 9.1). Modules: `config.py`, `jira.py`, `github.py`, `store.py`, `errors.py`; tests in `tests/`. `ruff check` and `ruff format` clean (line length raised to 120).

| Test | Result |
|------|--------|
| T-I2-1 shipped files validate; `targets.yaml` ships empty; sandbox sample parses | PASS (after fix: YAML reads `on`/`off` as booleans; a before-validator now accepts both) |
| T-I2-2 unknown gate `G9` rejected with file, line and allowed names | PASS (`targets.yaml:4`) |
| T-I2-3 gate `after:` unknown stage rejected | PASS |
| T-I2-4 missing or empty `env_passthrough` rejected | PASS |
| T-I2-5 any `ANTHROPIC_*` variable in `env_passthrough` rejected (D4) | PASS, four variants |
| extra: `--bare` and non-`-p` commands rejected; missing stage prompt file detected; YAML syntax error reports line | PASS |
| T-I2-6 schema export stable; `schemas/{targets,flow,agent}.schema.json` written | PASS |
| T-I3-1 JQL search follows `nextPageToken` pagination (`/rest/api/3/search/jql`) | PASS |
| T-I3-2 transition by name posts the right id | PASS |
| T-I3-3 unavailable transition lists the available names | PASS |
| T-I3-4 429 with `Retry-After: 7` retried once after 7 s (sleep injected) | PASS |
| T-I3-5 dry-run makes no write calls, logs three intents | PASS |
| T-I3-7 token absent from error text and logs | PASS |
| T-I3-6 live smoke | SKIP (deselected: `live` marker; runs in 6a with `AINS_LIVE=1`) |
| T-I4-1 clone, branch, commit, push argv sequence; commit message starts with the key | PASS |
| T-I4-2 draft PR create, ready, merge refused without human approval | PASS |
| extra: merge proceeds with human approval + green `ci`; agent-marked or bot-authored approvals never count; label approval mode; merge refused on red `ci` | PASS |
| T-I4-3 review events `COMMENT`/`REQUEST_CHANGES` carry the agent marker; `APPROVE` refused | PASS |
| T-I4-4 PR list by label | PASS |
| T-I4-5 error wrapping with stderr; one retry on network error only | PASS (after fixing the fake runner's key) |
| T-I5-1 state survives reload | PASS |
| T-I5-2 upsert once; `active_keys` excludes released | PASS |
| T-I5-3 1,000 events ordered, read in < 0.5 s | PASS |
| T-I5-4 unwritable path fails at start-up | PASS |
| suite | `uv run pytest`: 35 passed, 1 deselected (live) |

**Deviation from the test plan.** The GitHub client takes an injected runner instead of a fake `gh` script on `PATH`; the shell-script fake would be awkward on Windows and the injected runner tests the same argv. A real fake `gh` arrives with the L2 dry-run harness (I18).

**Design finding, needs a decision before 6a.** GitHub does not let a PR author approve their own PR. The orchestrator opens PRs with niloyray's PAT, and niloyray is also the human reviewer, so G3 by PR review is impossible on a single account. Two options are implemented in `github.py`: `approval_mode: review` (needs a second account for the orchestrator, e.g. `ains-bot`, so `bot_login` reviews never count) and `approval_mode: label` (the human adds label `g3-approved`; branch protection then requires only the `ci` check). Default for the sandbox is pending the user's choice.

### I6 — Poller · I7 — Flow state machine + gates · I11 — Workspace manager

Modules: `poller.py`, `flow.py`, `workspace.py`; shared in-memory fakes in `tests/fakes.py` (Jira, GitHub PRs, agents that write the expected files). The `flows/story.yaml` status map was aligned so that a human approves G1 by moving the story to **Planning** and G2 by moving it to **Implementing**; Rework is a status; the orchestrator sets Specifying, Spec Review, Plan Review, Code Review, Blocked. The plan's status list gains **Planning** (section 2f) when the plan is next revised.

| Test | Result |
|------|--------|
| T-I7-1 happy path with all gates on: claim → spec → wait_g1 → plan → wait_g2 → implement → review → wait_g3 → merge → released; draft PR, remote link, review posted as COMMENT, three gate.passed events, workspace removed | PASS |
| T-I7-2 gate on: five idle cycles, no re-spawn | PASS |
| T-I7-3 all gates off: runs to released with "G1/G2/G3 auto-approved" comments | PASS |
| T-I7-4 Rework at G2: plan agent re-run with the human comment in its context; rework_count=1 | PASS |
| T-I7-5 changes requested at G3: back to implement with the review attached, then a new review | PASS |
| T-I7-6 agent fails twice: blocked, Jira → Blocked with a comment | PASS |
| T-I7-7 story moved from Blocked back to Ready resumes at the blocked stage (plan), spec not re-run | PASS (test corrected: one transition per cycle) |
| extra: missing output counts as failure; new commits after a review trigger a second review; label approval mode merges on `g3-approved` | PASS |
| T-I6-1 Ready story claimed; status Specifying then Spec Review in the same cycle | PASS |
| T-I6-2 same story claimed once even if dragged back to Ready | PASS |
| T-I6-3 story without a target mapping skipped | PASS |
| T-I6-4 Jira unreachable: error reported, next cycle works | PASS |
| T-I6-5 one story at a time per target | PASS |
| T-I11-1 clone + branch, slug rule, second ensure is a no-op | PASS (expected slug corrected) |
| T-I11-2/3 commit message `<KEY>: …` with stage trailer | PASS |
| T-I11-4 cleanup removes the directory, idempotent | PASS |
| T-I11-5 refuses to clone below the free-space threshold | PASS |
| suite | `uv run pytest`: 57 passed, 1 deselected (live) |

Not yet implemented: the I10 refinement that distinguishes a human "changes requested" from the agent's (flow.py `_human_requested_changes` returns True for now); the Jira comment for T-I6-3 ("no target configured") is a skip only, the comment arrives with I12.

### I8 — Agent runner · I9 — Stage prompts + Claude settings · I10 — PR watcher · I12 — CLI

Modules: `agent.py` (spawns `claude -p` with a minimal environment), `watcher.py`, `cli.py` (all commands wired except `target add/remove`, which is I16); `agents/stages/{spec,plan,implement,review}.md`; `claude-settings.json` (allowlist for git read-only ops, uv, pytest, ruff; denies push, commit, merge, approve, network tools). `github.py` gained human-vs-agent review detection (`human_changes_requested`, `review_bodies`) and `checkout`; the flow uses them for the G3 rework path.

| Test | Result |
|------|--------|
| T-I8-1 argv: `claude -p --output-format json --settings /app/claude-settings.json --permission-mode <stage> --max-turns <n> <prompt>`, never `--bare` | PASS |
| extra: review stage runs with `--permission-mode plan` and `--disallowedTools Edit,Write,…,WebFetch,WebSearch` | PASS |
| T-I8-2 subprocess env contains PATH, HOME and `CLAUDE_CODE_OAUTH_TOKEN` only; `ANTHROPIC_*`, `GH_TOKEN`, `JIRA_*` absent even when set in the parent | PASS |
| extra: `minimal_env` refuses an `ANTHROPIC_*` passthrough (D4) | PASS |
| T-I8-3 cwd is the story workspace | PASS |
| T-I8-4 JSON result parsed: text, turns, cost, duration, session id; array output and non-JSON handled | PASS |
| extra: `is_error` and non-zero exit become failures | PASS |
| T-I8-5 runner reports failure; the engine retries (one retry, then block) | PASS |
| T-I8-6 wall-clock limit passed as the subprocess timeout; timeout reported | PASS |
| extra: missing `CLAUDE_CODE_OAUTH_TOKEN` is reported before spawning | PASS |
| T-I9-1 all four shipped prompts render with the story context; review prompt demands `VERDICT:` and forbids approving | PASS |
| T-I10-4 Human-lane PR with `ai-review` reviewed once, never merged; second cycle skipped | PASS (after fix: the watcher's own PR sessions were mistaken for flow sessions) |
| extra: a new push gets a new review; `request_changes` verdict posted as such | PASS |
| T-I10-5 draft PR ignored | PASS |
| extra: a flow session's PR is left to the flow | PASS |
| T-I12-1 `agent list`, `flow list`, `targets list` | PASS |
| T-I12-2 `session status --json`; unknown key exits 1 | PASS |
| T-I12-3 `session dispatch --dry-run` without Jira credentials exits 2 with a clear message | PASS |
| T-I12-4 `doctor --no-live` with an empty PATH and a planted `ANTHROPIC_API_KEY` reports every failing item and exits 1 | PASS |
| suite | `uv run pytest`: 81 passed, 1 deselected (live) |

Still open from this batch: T-I8-7 (live `claude -p` in the container) and T-I9-5/6/7 wait for I13 and the token; T-I12-5 (`doctor` green in the container) waits for I13.

### I13 — Dockerfile + compose · I14 — Reusable workflows · I15 — intake-ack · I16 — Onboarding (`ains target add/remove`)

Files: `ains-orchestrator/Dockerfile`, `docker-compose.yml`; `.github/workflows/reusable-ci-python.yml`, `reusable-release.yml`, `reusable-jira-callback.yml`, `intake-ack.yml`; `ains-orchestrator/src/ains_orchestrator/onboard.py`; `bootstrap/templates/{ci.yml,release.yml,CLAUDE.md,claude-settings.json,gitignore,README.md}`; CLI `target add` / `target remove` wired. Lane marker changed from a custom field to the label `ai-native` (team-managed Jira projects do not expose custom-field creation via REST; the plan allowed a label). Secrets `JIRA_EMAIL`, `JIRA_API_TOKEN` and variable `JIRA_BASE_URL` set on `ains-platform` for `intake-ack` (values never in files).

| Test | Result |
|------|--------|
| T-I13-1 `docker build` | PASS: image `ains-orchestrator:local`, 1.81 GB |
| T-I13-4 tools in the image | PASS: gh 2.103.3, specify 1.2.6, git 2.47.3, node 22.23.4, ains 0.1.0; claude see next entry |
| T-I13-3 no `ANTHROPIC_*` in the container env | PASS (0 variables) |
| T-I14-1 `actionlint` on the four workflows | PASS: clean |
| T-I16-1 dry-run | see next entry |
| T-I16-2 templates render; thin callers reference `niloyray/ains-platform/...@main` and carry `secrets: inherit`; `.claude/settings.json` denies push/merge/approve; `CLAUDE.md` forbids editing platform files | PASS |
| T-I16-3 (offline, fake gh + fake Jira) every onboarding step runs: repo create, templates pushed, three labels, secrets via stdin (token never on argv), two environments with reviewer, branch protection requiring `ci`, Jira project created, statuses verified, Automation checklist printed, `targets.yaml` block appended with comments preserved | PASS |
| T-I16-4 second run idempotent | PASS |
| extra: missing Jira statuses become manual steps with the exact UI path | PASS |
| T-I16-7 second project onboarded, T-I16-6 `remove` archives the repo, deletes the Jira project, drops the block | PASS (offline) |
| suite | `uv run pytest`: 88 passed, 1 deselected (live) |

### Step 6a.1 — Sandbox project onboarded (first real run of `ains target add`) · T-I15-1

Command: `ains target add ains-sandbox AINSSB sandbox` from the workstation with `.env` (gitignored) holding the GitHub PAT and Jira token. Three runs: the first created everything but crashed while printing the report (Windows cp1252 console; fixed by reconfiguring stdout with `errors="replace"`); the second failed on a `git commit` with nothing to commit (fixed: commit only when `git status --porcelain` is non-empty) and revealed that Spec Kit 1.2.6 has no `--no-git` flag; the third was clean. Spec Kit installed on the workstation (`uv tool install specify-cli`, 1.2.6); it writes `.specify/` and `.claude/skills/speckit-*` (hyphenated skill names), so the four stage prompts now call `/speckit-specify`, `/speckit-plan`, `/speckit-tasks`, `/speckit-implement`.

| Check | Result |
|-------|--------|
| GitHub repo `niloyray/ains-sandbox` | created, public, default branch `main` |
| templates pushed | `.github/workflows/ci.yml`, `release.yml`, `CLAUDE.md`, `README.md`, `.gitignore`, `.claude/settings.json`; second commit adds `.specify/` and `.claude/skills/` (ten speckit skills) |
| labels | `ai-review`, `g3-approved`, `ai-native` |
| secrets and variable | `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_BASE_URL` set on the sandbox repo |
| environments | `staging`, `production`, required reviewer niloyray |
| branch protection | `main` requires status check `ci` |
| Jira project `AINSSB` | created via REST, team-managed ("next-gen") software project, name "sandbox" |
| Jira statuses | **manual**: team-managed projects ship To Do, In Progress, In Review, Done; the ten AINS statuses (Ready, Specifying, Spec Review, Planning, Plan Review, Implementing, Code Review, Staging, Rework, Blocked) must be added in the Jira UI; the command prints the exact path and re-verifies on the next run |
| Jira Automation rule | **manual**: checklist printed by the command |
| `targets.yaml` | `AINSSB -> niloyray/ains-sandbox`, all five gates on, `approval_mode: label` (single-account G3; see the I2–I5 finding) |
| T-I16-1 dry-run | PASS: ten `[planned]` lines, no change |
| T-I16-3 real run | PASS for every automated step; two steps manual by design |
| T-I16-4 idempotent re-run | PASS: `exists` for repo, project, targets block; labels, secrets, environments, protection re-applied without error |
| T-I15-1 `repository_dispatch` → intake-ack | PASS: run 41495999419 succeeded, comment "Accepted into the AI-native lane…" on `AINSSB-1` |
| smoke story | `AINSSB-1` "Sandbox smoke story" created via the new `JiraClient.create_issue` (label `sandbox`, not AI-native); the acceptance story will therefore be `AINSSB-2` |

### Live checks after onboarding (no Claude token needed) · compose config mount · I17 docs

| Test | Result |
|------|--------|
| T-I12-5 `docker compose run --rm orchestrator ains doctor` | all checks green except `env CLAUDE_CODE_OAUTH_TOKEN: missing` (expected until the token is provided): config, GH_TOKEN, JIRA_*, no `ANTHROPIC_*`, git, gh, claude 2.1.316, specify 1.2.6, work dir, `gh auth`, Jira as Niloy Ray |
| finding | the first container run showed 0 targets: `targets.yaml` was baked into the image at build time. Fixed in `docker-compose.yml`: `targets.yaml`, `flows/`, `agents/` and `claude-settings.json` are now mounted read-only, so `ains target add` takes effect on the next poll without a rebuild. Verified: `ains targets list` inside the container shows `AINSSB` |
| T-I3-6 live Jira (`AINS_LIVE=1 pytest -m live`): `/myself`, JQL search, comment on `AINSSB-1` | PASS |
| T-I4-6 / T-I11-6 live GitHub: clone sandbox, branch `AINSSB-1-ci-smoke`, commit `AINSSB-1: …`, push, open PR #1 as niloyray | PASS |
| T-I14-2 thin `ci.yml` → reusable CI on PR #1, first run | FAIL: all steps passed (no pyproject, no tests, no Dockerfile yet, each skipped as designed) but the `setup-uv` post-step errored "Cache path … does not exist" and failed the job. Fixed: `enable-cache: false` in `reusable-ci-python.yml`; empty commit pushed to retrigger; result in the next entry |
| housekeeping | the two `release` runs triggered by the template pushes to `main` were waiting on the staging environment approval (G4 works) and were cancelled, because template pushes are not stories |
| I17 | `docs/gates.md` and `docs/targets.md` written |

### T-I14-2 / T-I14-3 — thin CI caller on the sandbox PR, retriggered

| Test | Result |
|------|--------|
| run 41497227245 on PR #1 (`AINSSB-1: ci smoke`) | PASS: job `ci` success (install, lint, test and image build each skipped cleanly because the sandbox has no code yet); job `jira callback (Code Review)` success |
| callback behaviour | issue key parsed from the PR title (`AINSSB-1`); transition `Code Review` reported as `::warning:: not available` because the status does not exist yet (manual Jira step pending); comment "CI passed on PR #1 …" added; remote link `PR #1` attached |
| PR #1 | left open for the review-agent live test (T-I10-9) once the Claude token is available |

**Where Phase 1 stands.** Every offline build item (I1–I18 except the live parts) is done and green: 88 unit tests, image built, workflows linted and proven live (intake-ack, CI, Jira callback), sandbox onboarded. Blocked on: (1) the ten Jira statuses in `AINSSB` (manual UI), (2) `CLAUDE_CODE_OAUTH_TOKEN` for the first agent run (plan step 4.6, T-I8-7), (3) the Jira Automation rule (manual UI; audit trace only, not required for the acceptance run).

### Jira statuses automated (removes the manual step from onboarding)

The user asked whether the API could add the statuses. The legacy workflow APIs cannot touch team-managed projects, but the newer ones can: `POST /rest/api/3/statuses` with `scope.type = PROJECT` creates project-scoped statuses, `POST /rest/api/3/workflows` (bulk get) returns the project's own workflow, and `POST /rest/api/3/workflows/update` (after `/update/validation`) adds statuses and transitions. The first attempt failed validation: the update payload needs caller-chosen UUIDs as `statusReference` with the numeric ids in a separate `id` field. With that, validation returned no errors and the update succeeded.

| Check | Result |
|-------|--------|
| ten statuses created in project scope for `AINSSB` | PASS (Ready as TODO, Done kept, the rest IN_PROGRESS) |
| workflow "Software workflow for project AINSSB" updated with a GLOBAL transition per new status | PASS: validation `errors: []`, update 200 |
| `GET /project/AINSSB/statuses` | 14 names: the twelve AINS statuses plus the default In Progress and In Review |
| live transitions by name on `AINSSB-1`: To Do → Ready → Spec Review → To Do | PASS (the orchestrator's `transition()` works unchanged) |
| ported into the platform: `JiraClient.ensure_statuses()` called by `ains target add`; manual checklist only as a fallback when the API rejects the update | PASS: 89 unit tests; re-run of `ains target add` reports `jira statuses: exists (all present)` |

Remaining manual Jira item: the Automation rule (audit trace only; Jira Automation has no public create API). Not required for the acceptance run.

## 2026-11-03 · token received, first live agent call, credential checks, acceptance run started

The user ran `claude setup-token` (Max subscription) and pasted the `sk-ant-oat01-…` token; it was written only to the gitignored `ains-orchestrator/.env`.

| Test | Result |
|------|--------|
| T-I8-7 / T-L6-2 `docker compose run orchestrator claude -p "reply with the single word ok" --output-format json` | PASS: result `ok`, 1 turn, `is_error: false`; the JSON carries a `total_cost_usd` estimate (0.023) that Claude Code always reports, it is not a charge: no API key exists |
| T-L6-3 same call with `CLAUDE_CODE_OAUTH_TOKEN` emptied | PASS (fails as designed): "Not logged in · Please run /login", so the OAuth token is the only credential in use |
| T-L6-4 same call with a planted `ANTHROPIC_API_KEY=sk-ant-api03-dummy` | PASS (fails as designed): HTTP 401 "Invalid API key", proving an API key would take precedence if ever present, which is why the agent runner strips `ANTHROPIC_*` and `doctor` fails on it |
| T-I12-5 `ains doctor` in the container | PASS: 14 ok, no FAIL (config with 1 target, all env vars, no `ANTHROPIC_*`, git, gh, claude, specify, work dir, gh auth, Jira) |
| acceptance story | `AINSSB-2` "Repository scaffold: README section and health-check script" created with label `ai-native`, status To Do; moved to Ready when the container is up (next entry) |
| G3 mode | label mode (`g3-approved`) in effect for the sandbox; the user has not asked for a bot account |

### Acceptance run AINSSB-2 — first attempts (step 8)

| Event | Result |
|-------|--------|
| container started, story moved to Ready | no claim for six cycles. Cause: the running image was built before the lane changed from the custom field to the label `ai-native`, so its poller queried `"Execution Lane"`. `docker compose up -d --build` fixed it. Lesson recorded: source is baked into the image; config is mounted. Rebuild after code changes |
| claim (T-I6-1 live) | PASS: session created 18:52:19 UTC, branch `AINSSB-2-repository-scaffold-readme-section-and-h`, Jira → Specifying with the "picked up" comment |
| spec agent (T-I9-5 live) | PASS: `claude -p` ran 10 turns in 40 s, wrote `specs/AINSSB-2/spec.md` in the clone |
| commit after the spec stage | FAIL: `git commit` exit 128 "Author identity unknown" (the container had no git identity). The orchestrator handled it as designed: story → Blocked with the error as a Jira comment (T-I7-6 live) |
| fix | `GitHubClient.commit_all` now passes `-c user.name/-c user.email` (`AINS_GIT_NAME` / `AINS_GIT_EMAIL`, default `ains-orchestrator`); the image also sets a global git identity and `safe.directory *`. Unit test updated; 89 pass. Image rebuilt |
| resume (T-I7-7 live) | story moved from Blocked back to Ready by hand; the poller resumes at the spec stage (the spec agent runs once more, then commit, push, draft PR, Spec Review) |

### Acceptance run AINSSB-2 — reached gate G1 (plan 8.2)

| Event | Result |
|-------|--------|
| second block | push failed: git in the container had no credential helper (`gh` could clone with `GH_TOKEN`, plain `git push` could not). Fixed in the image: `credential.helper = !gh auth git-credential`; verified with `git ls-remote` from inside the container |
| resume improvement | on resume after a failure that happened *after* a successful agent run, the orchestrator now skips the agent when the stage's outputs are present (`agent.skipped` event); unit test added, 90 pass |
| resume 2 | agent skipped, branch pushed, draft PR #2 opened (`AINSSB-2: Repository scaffold: README section and health-check script`), remote link added, Jira → **Spec Review** with the G1 instructions comment, session `wait_g1` at 19:03 UTC |
| spec quality (T-I9-5) | `specs/AINSSB-2/spec.md` from Spec Kit: goal, user-visible behaviour, scenarios with independent tests, written from the story text |
| waiting on | human G1 decision in Jira (Planning = approve, Rework = send back) |

### Acceptance run AINSSB-2 — G1 passed, reached gate G2 (plan 8.3, 8.4)

| Event | Result |
|-------|--------|
| G1 (T-I19-2, approve path) | human moved the story to Planning at 19:10 UTC; `gate.passed G1 mode=human`; the orchestrator continued on its next poll with no other touch |
| plan agent | PASS: 12 turns, 49 s; `plan.md` and `tasks.md` (eight ordered, checkable tasks with files named, tests first) committed to the same branch, draft PR #2 updated |
| Jira | → **Plan Review** with the G2 instructions comment; session `wait_g2` at 19:12 UTC |
| waiting on | human G2 decision in Jira (Implementing = approve, Rework = send back) |

### Acceptance run AINSSB-2 — G2 passed, implement done, review stage findings (plan 8.5, 8.6)

| Event | Result |
|-------|--------|
| G2 | human moved the story to Implementing; `gate.passed G2 mode=human` |
| implement agent (T-I9 live) | PASS: 19 turns, 41 s; code and tests pushed (sha a443016), PR #2 marked ready, Jira → Code Review |
| review agent, first attempt | FAIL in 0.3 s with 0 turns: "Input must be provided either through stdin or as a prompt argument". Cause: `--disallowedTools <list>` is variadic and swallowed the prompt that followed it. Also seen: "Ignoring 7 permissions.allow entries from .claude/settings.json: this workspace has not been trusted", i.e. headless Claude Code ignores the onboarded repo's settings until the workspace is trusted |
| fixes | prompts now go to Claude Code through stdin for every stage (no argv limits, nothing after the tool list); the runner marks each workspace trusted in `~/.claude.json` (`projects[<dir>].hasTrustDialogAccepted`) before spawning; review stage runs in `default` permission mode with Edit/Write/Web tools disallowed so it can run the tests. 91 unit tests pass. Manual probe in the container with the exact review argv and a stdin prompt returned a result |
| second attempt | the old container retried before the rebuild finished → blocked (as designed). Story moved back to Ready by hand; resume restarts at the review stage (implement output already on the PR) |

### Acceptance run AINSSB-2 — review posted, CI fix (plan 8.6)

| Event | Result |
|-------|--------|
| review agent (T-I9-6 live, after the fixes) | PASS: 4 turns, 16 s; posted a PR review marked `[ains review agent]` with verdict `comment`: summary, three minor findings (ruff not declared so `uv run ruff` fails, new `uv.lock`, test import relies on `pythonpath`), tests run (2 passed). It never approved |
| CI on PR #2 | FAIL on the lint step: `Failed to spawn: ruff`, exactly the agent's first finding. Platform fix: the reusable CI runs `uvx ruff check` / `uvx ruff format --check` so onboarded projects need not declare ruff; `CLAUDE.md` template updated to say so |
| Jira status after resume | the resumed review stage had left the story in Ready; `_do_review` now restores **Code Review** after posting (set by hand this time) |
| retrigger | empty commit `253d3df` pushed from the orchestrator's own workspace (keeps its local branch in sync); CI re-runs with the fixed reusable workflow; the orchestrator will see the new head and post a second review (new-commit rule, T-I10-3 live) |
| waiting on | green `ci` on the new head, second review, then the human G3 label |

### Flow change from the acceptance run: CI failure at G3 is a rework, not a dead end

| Event | Result |
|-------|--------|
| CI on the retriggered head `253d3df` | FAIL on lint: ruff flagged `subprocess.run` without `check=` in the agent's test. A real finding, and with CI red G3 could never pass, so the story would have waited forever |
| flow change | at `wait_g3`, a failed `ci` check sends the story back to the implement stage with the failed-step log excerpt as rework input (`gh run view --log-failed`), at most two rounds, then Blocked with a message for a human. Jira gets a comment per round |
| label-mode rework | GitHub also forbids "request changes" on one's own PR, so label mode gained `g3-rework`: the human adds the label and writes the reason as a PR comment; the orchestrator removes the label and re-runs implement with that comment. Label created on the sandbox and by onboarding |
| tests | three new flow tests (CI rework with log, bounded rounds, label rework); 94 pass. Container rebuilt and restarted |
| expected next | review of head `253d3df` (if not finished before the restart), then CI-failure rework → implement agent fixes the lint finding → push → CI → review → `wait_g3` with green CI |

### Defect found by the acceptance run: the required check is named `ci / ci`, not `ci`

| Item | Detail |
|------|--------|
| symptom | with CI red on PR #2 the orchestrator kept reporting `wait_g3` and never started the CI rework; `ci_green` could also never become true |
| cause | the sandbox `ci.yml` calls the platform's reusable workflow from a caller job named `ci`, and GitHub names that check run `ci / ci` (caller job / called job). The GitHub client keyed checks by full name and looked up `ci`; branch protection also required a context `ci` that never reports, so merging would have hung on "expected" forever |
| fix | `GitHubClient._to_pr` keys every check under its full name and under its last segment (exact name wins); onboarding now requires the context `ci / ci` (`REQUIRED_CHECK_CONTEXT`); sandbox protection patched in place via the API. Test T-I4-7 covers the mapping; 95 pass. Commit `f9ee36d`, container rebuilt |
| result | next poll cycle: `stepped={'AINSSB-2': 'implement'}` — CI failure detected, story sent back to implement with the ruff log excerpt (round 1 of 2) |

### CI rework round 1 produced no change: the log excerpt was unusable

| Item | Detail |
|------|--------|
| observed | `gate.rework` (CI) fired, the implement agent ran 8 turns and `stage.done` reported the same head `253d3df`: the agent changed nothing. In the workspace `uvx ruff check .` does reproduce PLW1510, so the finding was fixable |
| cause | `gh run view --log-failed` prints the whole failed job as `job<TAB>step<TAB>timestamp line`; for a reusable workflow the step is `UNKNOWN STEP`, and the job ends with the Postgres service-container teardown. The excerpt took the last 80 lines, so the agent was shown container shutdown noise and a Node.js deprecation warning, with the ruff output 100 lines earlier. The agent's own reply was not persisted, so this had to be reconstructed from the workspace |
| fix | `failed_step_excerpt()` strips prefixes and timestamps and windows 60 lines before and 3 after the last `##[error]` marker (fallback: tail). `agent.done` events now carry the agent's final reply (first 3000 chars). Tests T-I4-8, T-I4-9; 97 pass |
| session surgery | the orchestrator was stopped mid round 2; the AINSSB-2 row was reset to `wait_g3` with `ci_rework_rounds = 0` and a `manual.reset` event explaining why, so the fixed excerpt gets its two rounds. Container rebuilt and restarted |
| lesson for the platform | every agent run must leave its reply in the event log; an agent that reports success without changing the tree should be visible as such (`stage.done` with unchanged sha) |

### CI rework round 1 succeeded (after the excerpt fix)

| Item | Detail |
|------|--------|
| events | `gate.rework` (CI) with the ruff output in the excerpt → implement agent, 8 turns, 16 s, USD 0.05 → `stage.done` sha `dcfb214` → CI running → review |
| agent reply (now persisted) | added `check=False` to the `subprocess.run` call in `tests/test_healthcheck.py`; `uv run pytest` 2 passed; **could not run `uvx ruff check .`: "This command requires approval"** |
| fix | `Bash(uvx ruff*)` added to the platform `claude-settings.json` (mounted, no rebuild) and to the onboarding template; the sandbox's `.claude/settings.json` updated to match. The prompt tells agents to lint with `uvx ruff`, so the allowlist must permit it |

### AINSSB-2 at G3 (plan step 8.6 reached)

| Item | Detail |
|------|--------|
| PR #2 head | `dcfb214`; checks `ci / ci` SUCCESS and `ci / jira callback (Code Review) / callback` SUCCESS |
| review agent | third review posted (COMMENT) on `dcfb214`, 6 turns, 22 s, USD 0.06; its reply also noted it could not run ruff (approval), fixed by the `uvx ruff` allowlist entry for the next spawn |
| session | `wait_g3`, `reviewed_sha` = head; Jira AINSSB-2 in Code Review via the callback |
| waiting on | human G3: read the review and the diff on PR #2, confirm CI green, add label `g3-approved` (or `g3-rework` + a comment to send it back). Then the orchestrator squash-merges, `release.yml` builds the image and waits for the `staging` (G4) and `production` (G5) environment approvals, and the story goes to Done |
| run so far | agent cost for AINSSB-2 across spec, plan, implement x2, review x3: about USD 0.70 (billed to the Max subscription, shown by the CLI for information) |

### G3 passed, PR #2 merged, release waiting at G4; user guide and developer manual written

| Item | Detail |
|------|--------|
| G3 | user added `g3-approved`; `gate.passed` G3 (human) 02:06 UTC; squash-merge of PR #2 at 02:07 UTC (`merged` event, head `dcfb214`); workspace cleaned; session `released` |
| release | run 41564153890 on main: `build` success (no Dockerfile in the sandbox, so no image), `staging (G4)` waiting for the environment approval. A stray release run from the direct settings commit on main (no issue key) was cancelled |
| docs | `docs/user-guide.html` (for people new to Jira, Git and Actions: words, roles, setup, onboarding, one story through all gates, artifacts, troubleshooting, commands) and `docs/developer-manual.html` (principles, repo map, runtime topology, module map, poll cycle, state machine, persistent state, configuration, agent runner, clients, Actions side, onboarding internals, dev loop, extension recipes, invariants, debugging, backlog), both with inline SVG diagrams |
| next | user approves `staging` then `production` on the release run → Jira Staging → Done (plan steps 8.8 to 8.11); then 8.12 human-lane PR, 6a.2, 9 |

### G4 passed; log lines now timestamped; user guide gained "Looking under the hood"

| Item | Detail |
|------|--------|
| G4 | user approved `staging`; job `staging (G4)` success; Jira callback moved AINSSB-2 to **Staging**; `production (G5)` waiting |
| observation | G4/G5 live in the release workflow, so the container log shows nothing for them (the orchestrator's part ended at `released`). Documented in the guide's "Who logs what" table |
| change | `ains poll` prefixes the cycle line with a UTC timestamp and configures module logging with `asctime` (previously no timestamp and module INFO lines were dropped). `docker compose logs -t` documented as the other way to get times |
| docs | user guide section 10 "Looking under the hood": who logs what, Docker Desktop, container log and how to read the cycle line, `ains session status`, a Jira JQL filter for platform stories, GitHub views and `gh` commands, the working copy in the container, health commands |

### Self-test scaffolding: `ains status`, `ains target verify`, checklist in the manuals

| Item | Detail |
|------|--------|
| `ains status` | every session with stage, PR and the one human action it waits for (`next_action()`); `--json`; empty store prints how to start. Tests T-I12-5, T-I12-6 |
| `ains target verify <repo> <KEY>` | read-only mirror of onboarding: repo and starter files, labels, secrets, environments with reviewer, protection context `ci / ci`, Jira project and 12 statuses, targets.yaml block; exit 1 on any failure. Test T-I16-7 |
| poll log | httpx per-request INFO lines silenced (they appeared once module logging was enabled) |
| docs | user guide: "what is waiting for me", verify, section 11 "Checking the platform yourself" (13-row live checklist) and "Where things stand"; developer manual: section 12b acceptance scaffolding (three layers, ids) and status on 2026-11-03 |
| tests | 101 pass. Container rebuilt |
| live state | AINSSB-2: G4 passed, Jira Staging, `production (G5)` waiting; sandbox PR #1 still without `ai-review` label; watch re-armed |

