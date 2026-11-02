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

