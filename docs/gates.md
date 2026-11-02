# Gates

Five human gates, each switchable per target in `ains-orchestrator/targets.yaml`. A gate that is `off` is auto-passed by the orchestrator with a Jira comment ("G<n> auto-approved (gate off for this target)") so the trace never has a silent skip.

| Gate | After stage | Where the human acts | How to approve | How to send it back |
|------|-------------|----------------------|----------------|---------------------|
| G1 spec | `spec` wrote `specs/<KEY>/spec.md` into a draft PR; Jira status **Spec Review** | Jira | move the story to **Planning** | move it to **Rework** with a comment; the spec agent runs again with the comment |
| G2 plan | `plan` wrote `plan.md` and `tasks.md`; status **Plan Review** | Jira | move the story to **Implementing** | move it to **Rework** with a comment; the plan agent runs again |
| G3 code | `implement` pushed code and marked the PR ready; CI ran; the review agent posted its review; status **Code Review** | GitHub PR | `approval_mode: review` → approve the PR; `approval_mode: label` → add the label `g3-approved` | request changes on the PR; the implement agent runs again with the review text |
| G4 staging | PR squash-merged, image pushed to GHCR | GitHub Environments | approve the `staging` deployment | reject it; the release stops |
| G5 production | staging done | GitHub Environments | approve the `production` deployment | reject it |

## Why two approval modes for G3

GitHub does not allow the author of a pull request to approve it. The orchestrator opens PRs with the platform engineer's token, so on a single account the engineer cannot approve them. `approval_mode: label` lets a human express G3 by adding `g3-approved`; branch protection then requires only the `ci` status check. With a second GitHub account for the orchestrator (`bot_login`), `approval_mode: review` uses real PR approvals and the bot's own reviews never count.

## What the orchestrator does at each gate

- Sets the Jira status named in `flows/story.yaml` (`jira_status`), comments with the PR link and the exact approve / rework instructions, and polls every cycle.
- On approval: moves to the next stage and records `gate.passed` with `mode: human` (or `off`).
- On rework: records `gate.rework` with the comment, returns to the previous stage, re-spawns that stage's agent with the comment appended to its prompt, and sets the status back to Specifying or Planning.
- On a second failure of any agent: status **Blocked** with the error; moving the story back to **Ready** resumes at the blocked stage.

## Jira statuses the flow needs

To Do, Ready, Specifying, Spec Review, Planning, Plan Review, Implementing, Code Review, Staging, Done, Rework, Blocked. Team-managed Jira projects do not expose status creation via REST; `ains target add` reports the missing ones and verifies them on re-run. The lane marker is the label `ai-native` on the story.
