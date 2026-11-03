# Onboarded targets

One row per project served by this platform. The source of truth is `ains-orchestrator/targets.yaml`; this file is the human-readable log of why each target exists.

| Jira key | GitHub repo | Onboarded | Purpose | Gates | G3 mode | Profile | Status |
|----------|-------------|-----------|---------|-------|---------|---------|--------|
| AINSSB | niloyray/ains-sandbox | 2026-11-02 by `ains target add ains-sandbox AINSSB sandbox` | permanent sandbox: Phase 1 acceptance (plan step 8) and regression target for later platform changes (D16) | all five on | label | small-poc · spec-kit · L3 · intent→deploy | active; Jira statuses added manually, Automation rule manual |

Phase 2 will add `AINSPOC` → `niloyray/ains-poc` with the same command on its first day. The platform repository itself is never a target (D13).

Targets can also be added, verified and removed from the management portal (http://localhost:8787, "+ Add project" and the project page), which runs the same code.

## Adding or removing a target

```
ains target add <repo-name> <JIRA-KEY> "<Jira project name>" [--gates G1=on,G2=on,G3=on,G4=off,G5=off] [--approval-mode label|review]
ains target remove <repo-name> [--delete-repo]
```

`add` is idempotent: it reports `exists` for what is already there and re-applies labels, secrets, environments and branch protection. It prints the manual Jira steps (statuses, Automation rule) and verifies them on the next run. Commit the `targets.yaml` change in this repository afterwards; the running container mounts the file and picks it up on the next poll.
