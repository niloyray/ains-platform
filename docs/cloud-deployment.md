# Running the platform in the cloud for many users

A design note, not a plan. Written 2026-11-03 in answer to "can the container run in the cloud and be used by any
user locally?" The short answer: the code can, with changes in three areas; one of them is a licensing decision.

## What already fits

- The orchestrator and the portal make outbound calls only (Jira REST, GitHub via `gh`, claude.ai via the Claude Code
  CLI). Nothing listens except the portal, and it binds to loopback.
- All state is on two volumes (`/data`, `/work`) and in mounted config files; a container can be rescheduled and
  resumes from the store.
- People already use the platform through Jira, GitHub and a browser. They never run the container themselves.

## What has to change

### 1. Credentials become the platform's own

Today `.env` holds one person's tokens: a GitHub PAT, a Jira API token, a Claude subscription token. In a shared
deployment these become service identities held in the host's secret store (Key Vault, Secrets Manager, Kubernetes
secrets): a GitHub App or machine user with repository and workflow scopes, a Jira service account. Side effect: the
orchestrator is then a different account from the reviewers, so `approval_mode: review` (real PR approvals) works and
the label mode is no longer needed.

### 2. The portal gets a login and an audit column

The portal has no authentication because only the laptop owner can reach it. Behind a company login (identity provider
in front of a reverse proxy, or SSO at the ingress) it is reachable by many; then the portal must record who onboarded,
removed or verified a project, and the add/remove routes should require a role. The `/api/status` route can stay
read-only for the `/ains` skill, which would call it instead of `docker compose exec`.

### 3. Model access is the real blocker

The Claude Max subscription token from `claude setup-token` is personal; Anthropic's terms tie it to that person's own
use. A shared service running agents for many users on one person's subscription is not an allowed setup. A cloud
deployment must authenticate the agents as the organisation: an Anthropic API key with cost metering, or a Claude Code
enterprise arrangement. Decision D4 (strip every `ANTHROPIC_*` variable) was made for the PoC's cost model and would be
reversed deliberately, together with per-story cost reporting, which the store already records from the CLI's cost
field.

## What "used by any user locally" then means

- Product owners and reviewers: Jira and GitHub as today; the portal through the company login.
- Platform engineers: the `/ains` skill from any Claude Code session with the platform repository checked out, talking
  to the portal API and to `gh`; the control script becomes the deployment's own start/stop.
- Nobody installs Docker or holds a token.

## Effort

Items 1 and 2 are one to two days of work plus the infrastructure setup. Item 3 is a licensing and budget decision
before any code.
