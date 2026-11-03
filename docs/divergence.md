# Divergence from the architecture document

Plan step 9.1. Each row names a point where the built platform differs from section 05 of
`init/AI-native delivery stack.html` (the layer map against the organisation standard) or from the
original build prompt, why the difference exists, and whether it is a PoC shortcut or a decision to keep.
Status as of 2026-11-03, after the Phase 1 acceptance run (AINSSB-2 Done through all five gates).

| # | Document says | Platform does | Why | Keep or close |
|---|---------------|---------------|-----|---------------|
| a | Intake by push (Jira Automation calls the platform) | Intake by polling: the orchestrator queries Jira every 60 s; the Automation rule is an optional audit trace (`intake-ack`) | No inbound network to a laptop container (D3); polling also makes the platform resumable from its store alone | keep for the laptop runtime; a cloud deployment can add a webhook in front of the same claim code |
| b | Orchestrator is a third-party agent-orchestration service ("Ark", A4c) | Homegrown `ains-orchestrator` (Python, Typer, SQLite) | No public project matched the harness-only, subscription-only constraints; a homegrown loop is small (about 3,000 lines) and fully inspectable | keep; revisit when an orchestration service accepts Claude Code as its only harness |
| c | Runs in a VM or cloud runtime | Docker Compose on the engineer's laptop: orchestrator and portal containers, two named volumes | PoC cost and the personal Claude subscription | PoC shortcut; see `docs/cloud-deployment.md` |
| d | Release environments are hosts | `staging` and `production` are GitHub Environments with required reviewers; the release workflow tags the image in GHCR and waits for the approvals | The gates are what the research measures; hosting adds nothing to that | keep for the PoC |
| e | One repository and one Jira project | Two GitHub repositories (`ains-platform`, one per onboarded project); one Jira project per onboarded project; the platform itself has no CI, no Jira project and no story lane (D13) | The platform must evolve independently of the projects it serves and be re-used across PoCs (D16) | keep |
| f | One review gate | Five switchable gates G1 spec, G2 plan, G3 code, G4 staging, G5 production, set per project from the autonomy level (L3 all on, L4 G3 and G5 only) | Granular gates are a research variable (D9); the profile catalog ties them to the autonomy levels of the framework | keep |
| g | Eight SDLC agents (requirements, architecture, coding, testing, security, release, operations, modernization) | Four stage roles on one harness: spec, plan, implement, review; CI and the release workflow cover test and release mechanically | The story loop needs exactly these four; the others map to classes and phases not yet supported (`profiles.yaml`: maintain, legacy, modernization marked not yet) | PoC scope; add roles as phases become supported |
| h | Layer 1 class router assigns every piece of work a class | The class is a property of the project (`Target.profile`), chosen at onboarding; every story in a project takes the same path | One path per project is enough while each project has one class; routing by size and risk per story is a later feature | PoC shortcut |
| i | Execution Lane custom field | Jira label `ai-native` marks the lane | Team-managed projects cannot create custom fields through REST; a label is visible, filterable and needs no admin step | keep |
| j | Methods arrive as signed skills from a catalog (A3a, AG1) | Spec Kit installed per repository by onboarding; the catalog is `profiles.yaml` with one supported method; nothing is signed | No registry exists in the PoC | PoC gap |
| k | Knowledge graph (A5e), partner agents (A5g) | None; agents see the repository, `CLAUDE.md`, the story text and, for review, the PR diff | Out of scope for Phase 1 | PoC gap |
| l | Models behind a gateway with metering (A6) | Claude Code CLI with a personal Max subscription token; every `ANTHROPIC_*` variable is stripped from agent processes (D4); the CLI's cost field is recorded for information | Cost model of the PoC; also the main blocker for shared use, see `docs/cloud-deployment.md` | PoC shortcut; a shared deployment must change this |
| m | Governance AG1 to AG9 | Present: specs folder as the registry, PAT and OAuth as identity, five human gates, Actions logs plus Jira comments plus the event store as observability. Absent: AG3 data protection, AG4 policy as code, AG7 evaluation, AG8 cost, AG9 lifecycle | Scope | PoC gap, listed for Phase 2 planning |
| n | Build prompt: single acceptance story `AINSPOC-1` on `ains-poc` | Acceptance on a permanent sandbox project (`AINSSB-2`); `ains-poc` is onboarded only on Phase 2 day one | Keeps the platform regression target separate from the research application (D16) | keep |
| o | Not in the document | Management portal, project profiles, data volume with file log, control script, `/ains` skill | Needed to operate and demonstrate the platform; added 2026-11-03 (I20 to I22) | keep |

Things the acceptance run changed inside the platform, which are not divergences from the document but from the
first implementation, are in `implementation-log.md`: the `ci / ci` check name, the CI-failure rework loop, the
`g3-rework` label, the persisted agent replies, the PR diff in the review prompt.
