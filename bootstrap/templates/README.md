# {{repo_name}}

Onboarded to the AINS AI-native SDLC platform ({{platform_repo}}). Stories: Jira project `{{jira_key}}`.

How a story flows: Jira story with label `ai-native` moved to **Ready** → the orchestrator claims it → spec agent (gate G1 in Jira: move to **Planning**) → plan agent (gate G2: move to **Implementing**) → implement agent → PR ready → CI (`ci` check) → review agent posts a review → a human approves the PR (gate G3) → squash merge → release: image to GHCR, **staging** (G4) and **production** (G5) environment approvals → Jira **Done**.

Human lane: branch `<JIRA-KEY>-<slug>`, open a PR, add the label `ai-review` for an agent review, same CI and gates.

Conventions: see `CLAUDE.md`. Specs per story: `specs/<JIRA-KEY>/`.
